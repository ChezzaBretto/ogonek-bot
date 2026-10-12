# -*- coding: utf-8 -*-
"""
Telegram-бот продления огоньков TikTok для нескольких человек.

Друг заходит в бота → вводит инвайт-код → сканирует QR из TikTok-приложения →
присылает список ников → каждую ночь в 00:00 МСК бот шлёт им ❤️.

Запуск: python bot.py   (под systemd-юнитом tiktok-tgbot)
"""
import sys, os, json, html, asyncio, secrets, shutil, datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tt_core as core

from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (ApplicationBuilder, CommandHandler, MessageHandler,
                          CallbackQueryHandler, ContextTypes, PicklePersistence, filters)

HERE = os.path.dirname(os.path.abspath(__file__))
USERS = os.path.join(HERE, "users")
CONFIG = os.path.join(HERE, "config.json")
PY = sys.executable  # тот же интерпретатор, что запустил процесс (venv-friendly)

log = core.make_logger(os.path.join(HERE, "tgbot.log"))

# На сервере 1 CPU и 2 ГБ — Chromium строго по одному за раз.
browser_lock = asyncio.Lock()

BTN_CONNECT = "🔗 Подключить TikTok"
BTN_CHATS = "📋 Показать мои чаты"
BTN_LIST = "✏️ Задать список"
BTN_CHECK = "🧪 Проверка"
BTN_RUN = "🚀 Отправить сейчас"
BTN_OFF = "❌ Отключиться"
BTN_AUTO = "🎬 Автоответ"

KEYBOARD = ReplyKeyboardMarkup(
    [[BTN_CONNECT, BTN_CHATS], [BTN_LIST, BTN_CHECK], [BTN_RUN, BTN_AUTO], [BTN_OFF]],
    resize_keyboard=True)

BUTTONS = {BTN_CONNECT, BTN_CHATS, BTN_LIST, BTN_CHECK, BTN_RUN, BTN_AUTO, BTN_OFF}

ERRORS = {
    "no_cookies": "TikTok не подключён. Жми «🔗 Подключить TikTok».",
    "no_session": "Сессия битая — подключи TikTok заново.",
    "login_required": "TikTok просит вход — сессия протухла. Подключись заново.",
    "no_chats": "Список чатов не открылся. Попробуй ещё раз через минуту.",
    "no_result": "Прогон сорвался без ответа. Попробуй ещё раз.",
    "timeout": "Прогон завис и был остановлен.",
    "no_reply_targets": "Не задано, кому отвечать. Жми «👥 Кому отвечать».",
    "no_phrases": "Нет ни одной фразы для ответа. Жми «💬 Фразы».",
}


# ---------- конфиг и пользователи ----------

def cfg():
    return core.load_json(CONFIG, {})


def save_cfg(c):
    core.save_json(CONFIG, c)


def user_dir(uid):
    return os.path.join(USERS, str(uid))


def profile(uid):
    return core.load_json(os.path.join(user_dir(uid), "profile.json"), {})


def save_profile(uid, prof):
    os.makedirs(user_dir(uid), exist_ok=True)
    core.save_json(os.path.join(user_dir(uid), "profile.json"), prof)


def is_registered(uid):
    return os.path.exists(os.path.join(user_dir(uid), "profile.json"))


def is_admin(uid):
    return str(uid) == str(cfg().get("admin_id"))


def targets_of(uid):
    path = os.path.join(user_dir(uid), "targets.txt")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]


def esc(s):
    return html.escape(str(s))


# ---------- автоответ на видео ----------

def reply_targets_of(uid):
    return _lines(os.path.join(user_dir(uid), "reply_targets.txt"))


def phrases_of(uid):
    return _lines(os.path.join(user_dir(uid), "phrases.txt"))


def _lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]


def _save_lines(path, items):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(items) + "\n")


def reply_state(uid):
    return core.load_json(os.path.join(user_dir(uid), "reply_state.json"), {})


def save_reply_state(uid, st):
    core.save_json(os.path.join(user_dir(uid), "reply_state.json"), st)


def save_targets(uid, nicks):
    """Пишет targets.txt и возвращает текст ответа пользователю."""
    with open(os.path.join(user_dir(uid), "targets.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(nicks) + "\n")
    log("Список {}: сохранено {} ник(ов)".format(uid, len(nicks)))
    known = core.load_json(os.path.join(user_dir(uid), "chats.json"), [])
    missing = [n for n in nicks if known and n not in known]
    msg = "Список сохранён — {} чат(ов).".format(len(nicks))
    if missing:
        msg += ("\n\n⚠️ Среди твоих чатов не вижу таких:\n• " + "\n• ".join(esc(m) for m in missing) +
                "\n\nНик должен совпадать точь-в-точь, вместе с эмодзи и пробелами. "
                "Жми «📋 Показать мои чаты» и скопируй оттуда.")
    else:
        msg += "\n\nВсё нашлось. Ближайший прогон — сегодня в полночь."
    if not os.path.exists(os.path.join(user_dir(uid), "cookies.json")):
        msg += ("\n\n❗ У этого телеграм-аккаунта не подключён TikTok — слать ❤️ не с чего, "
                "ночной прогон его пропустит. Жми «🔗 Подключить TikTok» или /cookies.")
    return msg


# ---------- запуск браузерных задач ----------

async def run_runner(uid, *flags):
    """Гоняет runner.py в подпроцессе, возвращает разобранный RESULT."""
    proc = await asyncio.create_subprocess_exec(
        PY, os.path.join(HERE, "runner.py"), user_dir(uid), *flags,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=25 * 60)
    except asyncio.TimeoutError:
        proc.kill()
        return {"error": "timeout"}
    for line in reversed((out or b"").decode("utf-8", "replace").splitlines()):
        if line.startswith("RESULT "):
            res = json.loads(line[7:])
            if res.get("chats"):
                core.save_json(os.path.join(user_dir(uid), "chats.json"), res["chats"])
            return res
    return {"error": "no_result"}


async def run_autoreply(uid, *flags):
    """Гоняет autoreply.py в подпроцессе, возвращает разобранный RESULT."""
    proc = await asyncio.create_subprocess_exec(
        PY, os.path.join(HERE, "autoreply.py"), user_dir(uid), *flags,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=20 * 60)
    except asyncio.TimeoutError:
        proc.kill()
        return {"error": "timeout"}
    for line in reversed((out or b"").decode("utf-8", "replace").splitlines()):
        if line.startswith("RESULT "):
            return json.loads(line[7:])
    return {"error": "no_result"}


# ---------- команды ----------

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    uid = u.id
    c = cfg()

    # первый, кто написал боту, становится админом
    if not c.get("admin_id"):
        c["admin_id"] = str(uid)
        save_cfg(c)
        save_profile(uid, {"name": u.full_name, "username": u.username or "",
                           "joined": datetime.date.today().isoformat(), "admin": True})
        await update.message.reply_text(
            "Ты админ этого бота.\n\nИнвайт-код для друзей: <code>{}</code>\n"
            "Меняется командой /code, список подключённых — /users.".format(esc(c.get("invite_code", ""))),
            parse_mode=ParseMode.HTML, reply_markup=KEYBOARD)
        return

    if is_registered(uid):
        await update.message.reply_text("С возвращением. Что делаем?", reply_markup=KEYBOARD)
        return

    ctx.user_data["awaiting"] = "code"
    await update.message.reply_text(
        "Привет. Этот бот держит огоньки 🔥 в TikTok — каждую ночь сам шлёт ❤️ в чаты, "
        "которые ты выберешь.\n\nПришли инвайт-код, чтобы начать.")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    awaiting = ctx.user_data.get("awaiting")
    log("MSG {} awaiting={}: {}".format(uid, awaiting or "-", text.replace("\n", " ⏎ ")[:100]))

    # Кнопка — всегда кнопка, а не ввод. Все кнопки, кроме «Показать мои чаты»,
    # сбрасывают режим ожидания — без этого подпись кнопки уезжала в targets.txt
    # как ник. «Показать мои чаты» режим НЕ трогает: бот сам советует ею
    # копировать ники посреди ввода списка, и сброс молча съедал новый список.
    if text in BUTTONS:
        awaiting = None
        if text != BTN_CHATS:
            ctx.user_data.pop("awaiting", None)

    if awaiting == "code":
        if text == cfg().get("invite_code"):
            u = update.effective_user
            save_profile(uid, {"name": u.full_name, "username": u.username or "",
                               "joined": datetime.date.today().isoformat()})
            ctx.user_data.pop("awaiting", None)
            log("Новый пользователь: {} ({})".format(u.full_name, uid))
            await notify_admin(ctx, "➕ Подключился: {} (@{})".format(u.full_name, u.username or "—"))
            await update.message.reply_text(
                "Код принят.\n\nДальше два шага:\n"
                "1. «🔗 Подключить TikTok» — пришлю QR, отсканируешь его камерой в приложении TikTok\n"
                "2. «✏️ Задать список» — скажешь, в какие чаты слать ❤️",
                reply_markup=KEYBOARD)
        else:
            await update.message.reply_text("Код не подошёл. Попроси актуальный у того, кто дал бота.")
        return

    if not is_registered(uid):
        await update.message.reply_text("Сначала /start и инвайт-код.")
        return

    if awaiting == "targets":
        nicks = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not nicks:
            await update.message.reply_text("Пусто. Пришли ники по одному в строке.")
            return
        ctx.user_data.pop("awaiting", None)
        await update.message.reply_text(save_targets(uid, nicks),
                                        parse_mode=ParseMode.HTML, reply_markup=KEYBOARD)
        return

    if awaiting == "auto_targets":
        nicks = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not nicks:
            await update.message.reply_text("Пусто. Пришли ники по одному в строке.")
            return
        ctx.user_data.pop("awaiting", None)
        _save_lines(os.path.join(user_dir(uid), "reply_targets.txt"), nicks)
        log("Автоответ {}: целей {}".format(uid, len(nicks)))
        known = core.load_json(os.path.join(user_dir(uid), "chats.json"), [])
        missing = [n for n in nicks if known and n not in known]
        msg = "Записал: буду отвечать {} чел.".format(len(nicks))
        if missing:
            msg += ("\n\n⚠️ Среди твоих чатов не вижу таких:\n• " +
                    "\n• ".join(esc(m) for m in missing) +
                    "\n\nНик должен совпадать точь-в-точь. Скопируй его "
                    "кнопкой «📋 Показать мои чаты».")
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=KEYBOARD)
        await auto_menu(update.message.reply_text, uid)
        return

    if awaiting == "auto_phrases":
        items = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not items:
            await update.message.reply_text("Пусто. Пришли фразы по одной в строке.")
            return
        ctx.user_data.pop("awaiting", None)
        _save_lines(os.path.join(user_dir(uid), "phrases.txt"), items)
        log("Автоответ {}: фраз {}".format(uid, len(items)))
        await update.message.reply_text(
            "Запомнил {} фраз(ы) — буду выбирать случайную.".format(len(items)),
            reply_markup=KEYBOARD)
        await auto_menu(update.message.reply_text, uid)
        return

    # кнопки
    if text == BTN_CONNECT:
        await connect(update, ctx)
    elif text == BTN_CHATS:
        await show_chats(update, ctx)
    elif text == BTN_LIST:
        ctx.user_data["awaiting"] = "targets"
        cur = targets_of(uid)
        hint = "\n\nСейчас в списке:\n" + "\n".join("• " + esc(t) for t in cur) if cur else ""
        await update.message.reply_text(
            "Пришли ники одним сообщением, по одному в строке — они заменят текущий список."
            "\nСкопировать точное написание можно кнопкой «📋 Показать мои чаты»." + hint,
            parse_mode=ParseMode.HTML)
    elif text == BTN_CHECK:
        await check(update, ctx)
    elif text == BTN_RUN:
        await run_now(update, ctx)
    elif text == BTN_AUTO:
        await auto_menu(update.message.reply_text, uid)
    elif text == BTN_OFF:
        await update.message.reply_text(
            "Отключить бота и стереть твою сессию TikTok с сервера?",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Да, стереть", callback_data="wipe"),
                InlineKeyboardButton("Отмена", callback_data="cancel")]]))
    else:
        nicks = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(nicks) >= 2:
            # Несколько строк без режима ожидания — почти наверняка список
            # ников, присланный без нажатия «✏️ Задать список». Раньше бот
            # отвечал «Не понял», человек думал, что список принят, а рассылка
            # продолжала идти по старому. Терять такое молча нельзя.
            ctx.user_data["pending_targets"] = nicks
            await update.message.reply_text(
                "Похоже на список чатов ({} строк). Заменить им текущий список?".format(len(nicks)),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Да, заменить", callback_data="set_targets"),
                    InlineKeyboardButton("Отмена", callback_data="cancel")]]))
            return
        await update.message.reply_text("Не понял. Выбери кнопку ниже.", reply_markup=KEYBOARD)


async def connect(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if browser_lock.locked():
        await update.message.reply_text("Сейчас занят другим пользователем — подожди минуту и жми снова.")
        return

    await update.message.reply_text(
        "Открываю TikTok, сейчас будет QR.\n\n"
        "Как придёт: <b>приложение TikTok → Профиль → ☰ → Сканировать QR</b> — наведи на код.\n"
        "⚠️ Именно из приложения TikTok. Системной камерой или сканером из галереи "
        "не сработает: откроется просто сайт, а вход не привяжется.\n"
        "<b>После сканирования в приложении появится кнопка подтверждения входа — её надо нажать</b>, "
        "иначе TikTok вход не завершит и я так и буду молчать.\n\n"
        "Код живёт около минуты, я буду присылать свежий. "
        "Если с QR не выходит — есть вход без него: /cookies",
        parse_mode=ParseMode.HTML)

    async with browser_lock:
        proc = await asyncio.create_subprocess_exec(
            PY, os.path.join(HERE, "qr_login.py"), user_dir(uid),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)

        last_msg = None
        try:
            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=7 * 60)
                if not line:
                    break
                line = line.decode("utf-8", "replace").strip()

                # без этого лога непонятно, на чём встал вход у человека:
                # дошло ли до QR, был ли FAIL, или он просто не подтвердил в приложении
                log("QR-вход {}: {}".format(uid, line[:60]))

                if line.startswith("QR "):
                    path = line[3:]
                    if last_msg:
                        try:
                            await last_msg.delete()
                        except Exception:
                            pass
                    with open(path, "rb") as f:
                        last_msg = await update.message.reply_photo(
                            f, caption="Отсканируй этот код в приложении TikTok")
                elif line == "OK":
                    if last_msg:
                        try:
                            await last_msg.delete()
                        except Exception:
                            pass
                    log("Вошёл: {}".format(uid))
                    await update.message.reply_text(
                        "✅ TikTok подключён.\n\nТеперь «📋 Показать мои чаты», "
                        "потом «✏️ Задать список».", reply_markup=KEYBOARD)
                    break
                elif line.startswith("FAIL"):
                    await update.message.reply_text(
                        "Не получилось войти ({}).\n\nЧастая причина: код отсканирован, но в "
                        "приложении не нажато подтверждение входа. Попробуй ещё раз — "
                        "или заходи без QR: /cookies".format(esc(line[5:] or "?")),
                        reply_markup=KEYBOARD)
                    break
        except asyncio.TimeoutError:
            await update.message.reply_text(
                "Время вышло. Жми «🔗 Подключить TikTok» заново — или заходи без QR: /cookies",
                reply_markup=KEYBOARD)
        finally:
            try:
                proc.kill()
            except Exception:
                pass


async def show_chats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if browser_lock.locked():
        await update.message.reply_text("Сейчас занят другим пользователем — подожди минуту.")
        return
    msg = await update.message.reply_text("Открываю TikTok, собираю список чатов — это до минуты...")
    async with browser_lock:
        res = await run_runner(uid, "--list")
    if res.get("error"):
        await msg.edit_text(ERRORS.get(res["error"], res["error"]))
        return
    chats = res.get("chats", [])
    if not chats:
        await msg.edit_text("Чатов не нашёл.")
        return
    body = "\n".join(esc(c) for c in chats)
    await msg.edit_text(
        "Твои чаты ({}). Скопируй нужные ники и отправь их через «✏️ Задать список»:\n\n"
        "<pre>{}</pre>".format(len(chats), body), parse_mode=ParseMode.HTML)


async def check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not targets_of(uid):
        await update.message.reply_text("Список пуст — сначала «✏️ Задать список».")
        return
    if browser_lock.locked():
        await update.message.reply_text("Сейчас занят другим пользователем — подожди минуту.")
        return
    msg = await update.message.reply_text("Проверяю, ничего не отправляю...")
    async with browser_lock:
        res = await run_runner(uid)
    if res.get("error"):
        await msg.edit_text(ERRORS.get(res["error"], res["error"]))
        return
    wanted = targets_of(uid)
    missing = res.get("missing", [])
    ok = [w for w in wanted if w not in missing]
    text = "Нашёл {} из {} чатов.".format(len(ok), len(wanted))
    if missing:
        text += "\n\n⚠️ Не найдены:\n• " + "\n• ".join(esc(m) for m in missing)
        text += "\n\nСкорее всего опечатка в нике — сверь через «📋 Показать мои чаты»."
    else:
        text += "\nВсё в порядке, ночью разошлю ❤️."
    await msg.edit_text(text, parse_mode=ParseMode.HTML)


async def run_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not targets_of(uid):
        await update.message.reply_text("Список пуст — сначала «✏️ Задать список».")
        return
    if browser_lock.locked():
        await update.message.reply_text("Сейчас занят другим пользователем — подожди минуту.")
        return
    msg = await update.message.reply_text(
        "Рассылаю ❤️. Между чатами держу паузы 6–25 секунд, так что это небыстро.")
    async with browser_lock:
        res = await run_runner(uid, "--live")
    if res.get("error"):
        await msg.edit_text(ERRORS.get(res["error"], res["error"]))
        return
    text = "Готово. Отправлено: {}".format(res.get("ok", 0))
    if res.get("fail"):
        text += ", сбоев: {}".format(res["fail"])
    if res.get("missing"):
        text += "\n\n⚠️ Не найдены: " + ", ".join(esc(m) for m in res["missing"])
    if not res.get("ok") and not res.get("fail"):
        text = "Все чаты из списка уже получили ❤️ сегодня — повторно не шлю."
    await msg.edit_text(text, parse_mode=ParseMode.HTML)


AUTO_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("👥 Кому отвечать", callback_data="ar_targets"),
     InlineKeyboardButton("💬 Фразы", callback_data="ar_phrases")],
    [InlineKeyboardButton("🧪 Проверить", callback_data="ar_check"),
     InlineKeyboardButton("🚀 Ответить сейчас", callback_data="ar_run")],
])


def auto_kb(on):
    rows = [[InlineKeyboardButton("⏸ Выключить" if on else "▶️ Включить",
                                  callback_data="ar_off" if on else "ar_on")]]
    return InlineKeyboardMarkup(rows + list(AUTO_KB.inline_keyboard))


async def auto_menu(send, uid, note=""):
    """Экран автоответа. send — reply_text сообщения или edit_text у callback."""
    st = reply_state(uid)
    on = bool(st.get("enabled"))
    nicks, phrases = reply_targets_of(uid), phrases_of(uid)

    body = ["🎬 <b>Автоответ на видео</b>", ""]
    body.append("Статус: <b>{}</b>".format("▶️ включён" if on else "⏸ выключен"))
    body.append("Кому отвечаю: <b>{}</b>".format(len(nicks)))
    body.append("Фраз в наборе: <b>{}</b>".format(len(phrases)))
    if nicks:
        body.append("\n<b>Кому:</b>\n" + "\n".join("• " + esc(n) for n in nicks[:20]))
    if phrases:
        body.append("\n<b>Чем:</b>\n" + "\n".join("• " + esc(p) for p in phrases[:20]))
    body.append("\nКак работает: раз в {} мин бот заглядывает в эти чаты. Если "
                "тебе прислали видео и ты на него ещё не ответил — пишет случайную "
                "фразу из набора. На своё и на текстовые сообщения не реагирует."
                .format(cfg().get("autoreply_every", 10)))
    if not on and nicks and phrases:
        body.append("\nОсталось нажать «▶️ Включить».")
    if note:
        body.append("\n" + note)
    await send("\n".join(body), parse_mode=ParseMode.HTML, reply_markup=auto_kb(on))


async def auto_run(q, ctx, live):
    """Ручной прогон автоответчика: проверка или боевой ответ."""
    uid = q.from_user.id
    if not reply_targets_of(uid):
        await q.message.reply_text("Сначала «👥 Кому отвечать».")
        return
    if not phrases_of(uid):
        await q.message.reply_text("Сначала «💬 Фразы».")
        return
    if browser_lock.locked():
        await q.message.reply_text("Сейчас занят другим прогоном — подожди минуту.")
        return
    msg = await q.message.reply_text(
        "Смотрю чаты, отвечаю..." if live else "Смотрю чаты, ничего не отправляю...")
    async with browser_lock:
        res = await run_autoreply(uid, *(["--live"] if live else []))

    if res.get("error"):
        await msg.edit_text(ERRORS.get(res["error"], res["error"]))
        return

    lines = []
    if res.get("primed"):
        lines.append("Впервые заглянул в эти чаты, запомнил, что в них уже лежит: " +
                     ", ".join(esc(n) for n in res["primed"]) +
                     ".\nОтвечу на следующее присланное видео — на старое не отвечаю.")
    done = res.get("replied") if live else res.get("would")
    if done:
        lines.append(("Ответил:" if live else "Ответил бы:"))
        lines += ["• {} → «{}»".format(esc(r["nick"]), esc(r["phrase"])) for r in done]
    elif not res.get("primed"):
        lines.append("Новых видео нет — отвечать не на что.")
    if res.get("missing"):
        lines.append("\n⚠️ Не нашёл чаты: " + ", ".join(esc(m) for m in res["missing"]) +
                     "\nСверь написание через «📋 Показать мои чаты».")
    if res.get("fail"):
        lines.append("\nСбоев при отправке: {}".format(res["fail"]))
    await msg.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)


COOKIES_HELP = (
    "<b>Вход без QR</b> — переносом готовой сессии из браузера. Пригодится, когда "
    "QR не срабатывает: TikTok после сканирования ждёт подтверждения в приложении, "
    "и если его не нажать, вход не завершается.\n\n"
    "Суть везде одна: расширением <b>Cookie-Editor</b> на сайте tiktok.com сделать "
    "<b>Export as JSON</b> и прислать файл сюда. Вытащить сессию можно только "
    "расширением — «скопируй строку скриптом» не годится, оно не видит нужную куку.\n\n"
    "💻 <b>Компьютер (проще всего):</b>\n"
    "1. Открой <b>tiktok.com</b>, войди в аккаунт\n"
    "2. Поставь расширение <b>Cookie-Editor</b> (Chrome или Firefox)\n"
    "3. На вкладке TikTok: Cookie-Editor → <b>Export</b> → <b>Export as JSON</b>\n"
    "4. Вставь в блокнот, сохрани файлом <code>cookies.json</code>\n"
    "5. Пришли файл сюда\n\n"
    "🤖 <b>Телефон Android:</b>\n"
    "1. Поставь браузер <b>Firefox</b> (в Chrome на Android расширений нет)\n"
    "2. В Firefox добавь расширение <b>Cookie-Editor</b>\n"
    "3. Открой tiktok.com, войди → Cookie-Editor → <b>Export as JSON</b>\n"
    "4. Сохрани как файл и пришли сюда (если экспорт ушёл в буфер — вставь в "
    "заметку, сохрани файлом)\n\n"
    "🍏 <b>iPhone:</b> Cookie-Editor есть для Safari в App Store. Поставь, включи "
    "в «Настройки → Safari → Расширения», на tiktok.com жми его → Export as JSON, "
    "сохрани файлом, пришли. Путь капризный — если есть компьютер, лучше через него.\n\n"
    "Присылай <b>именно файлом</b> — текстом такой объём не влезет. Сообщение с "
    "файлом удалю сразу после импорта: это доступ к аккаунту, в переписке ему не место."
)


async def cmd_cookies(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(COOKIES_HELP, parse_mode=ParseMode.HTML)


async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Импорт сессии TikTok файлом — обход QR-входа."""
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    if not is_registered(uid):
        await update.message.reply_text("Сначала /start и инвайт-код.")
        return

    doc = update.message.document
    if doc.file_size and doc.file_size > 2 * 1024 * 1024:
        await update.message.reply_text("Файл великоват — экспорт куки весит десятки КБ. /cookies")
        return

    try:
        f = await ctx.bot.get_file(doc.file_id)
        raw = await f.download_as_bytearray()
        cookies = core.parse_cookies_blob(raw)
    except Exception as e:
        await update.message.reply_text(
            "Не разобрал файл ({}). Нужен экспорт куки в JSON — как его сделать: /cookies".format(esc(e)))
        return

    if not any(c["name"] == "sessionid" for c in cookies):
        await update.message.reply_text(
            "В файле нет куки <b>sessionid</b> — это не сессия TikTok. Обычно так выходит, "
            "если экспорт сделан не на вкладке tiktok.com или аккаунт там не залогинен. "
            "Инструкция: /cookies", parse_mode=ParseMode.HTML)
        return

    os.makedirs(user_dir(uid), exist_ok=True)
    path = os.path.join(user_dir(uid), "cookies.json")
    core.save_json(path, cookies)
    os.chmod(path, 0o600)
    log("Сессия импортирована файлом: {}".format(uid))

    msg = await ctx.bot.send_message(chat_id, "Сессия принята, проверяю — пускает ли TikTok...")
    try:
        await update.message.delete()  # в приватном чате бот вправе убрать входящее
    except Exception:
        pass

    if browser_lock.locked():
        await msg.edit_text("Сессия сохранена, но сейчас занят другим прогоном. "
                           "Нажми «📋 Показать мои чаты» через минуту.")
        return
    async with browser_lock:
        res = await run_runner(uid, "--list")

    if res.get("error"):
        await msg.edit_text(
            "Сессия сохранена, но TikTok её не принял: {}\n\n"
            "Так бывает, если куки сняты в браузере на телефоне или уже устарели. "
            "Экспортируй заново из браузера на компьютере, где TikTok открыт и залогинен: "
            "/cookies".format(ERRORS.get(res["error"], esc(res["error"]))))
        return

    chats = res.get("chats") or []
    await msg.edit_text(
        "✅ TikTok подключён, вижу {} чат(ов).\n\nДальше «📋 Показать мои чаты», "
        "потом «✏️ Задать список».".format(len(chats)))
    await ctx.bot.send_message(chat_id, "Что делаем?", reply_markup=KEYBOARD)
    await notify_admin(ctx, "🔑 {} подключил TikTok файлом сессии".format(
        esc(profile(uid).get("name", uid))))


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if q.data == "wipe":
        shutil.rmtree(user_dir(uid), ignore_errors=True)
        log("Отключился: {}".format(uid))
        await q.edit_message_text("Отключено. Сессия TikTok и список чатов стёрты с сервера.\n"
                                  "Вернуться можно через /start.")
    elif q.data == "set_targets":
        nicks = ctx.user_data.pop("pending_targets", None)
        if not nicks:
            await q.edit_message_text("Список потерялся — пришли его ещё раз.")
            return
        await q.edit_message_text(save_targets(uid, nicks), parse_mode=ParseMode.HTML)
    elif q.data in ("ar_on", "ar_off"):
        on = q.data == "ar_on"
        if on and not (reply_targets_of(uid) and phrases_of(uid)):
            await q.answer("Сначала задай, кому отвечать и какими фразами.", show_alert=True)
            return
        st = reply_state(uid)
        st["enabled"] = on
        save_reply_state(uid, st)
        log("Автоответ {}: {}".format(uid, "включён" if on else "выключен"))
        await auto_menu(q.edit_message_text, uid,
                        "✅ Включил." if on else "⏸ Выключил.")
    elif q.data == "ar_targets":
        ctx.user_data["awaiting"] = "auto_targets"
        cur = reply_targets_of(uid)
        hint = ("\n\nСейчас в списке:\n" + "\n".join("• " + esc(t) for t in cur)) if cur else ""
        await q.message.reply_text(
            "Пришли ники тех, кому отвечать на видео — по одному в строке, "
            "одним сообщением. Они заменят текущий список.\nТочное написание "
            "копируется кнопкой «📋 Показать мои чаты»." + hint,
            parse_mode=ParseMode.HTML)
    elif q.data == "ar_phrases":
        ctx.user_data["awaiting"] = "auto_phrases"
        cur = phrases_of(uid)
        hint = ("\n\nСейчас в наборе:\n" + "\n".join("• " + esc(p) for p in cur)) if cur else ""
        await q.message.reply_text(
            "Пришли фразы для ответа — по одной в строке, одним сообщением. "
            "Бот будет брать случайную и старается не повторять подряд одну и ту же."
            + hint, parse_mode=ParseMode.HTML)
    elif q.data == "ar_check":
        await auto_run(q, ctx, live=False)
    elif q.data == "ar_run":
        await auto_run(q, ctx, live=True)
    else:
        ctx.user_data.pop("pending_targets", None)
        await q.edit_message_text("Отменил, всё на месте.")


# ---------- админские команды ----------

async def notify_admin(ctx, text):
    admin = cfg().get("admin_id")
    if admin:
        try:
            await ctx.bot.send_message(admin, text)
        except Exception:
            pass


async def cmd_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    c = cfg()
    arg = " ".join(ctx.args).strip()
    if arg:
        c["invite_code"] = arg
        save_cfg(c)
        await update.message.reply_text("Новый инвайт-код: <code>{}</code>".format(esc(arg)),
                                        parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("Инвайт-код: <code>{}</code>\nСменить: /code новыйкод".format(
            esc(c.get("invite_code", ""))), parse_mode=ParseMode.HTML)


async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    rows = []
    for d in sorted(os.listdir(USERS)) if os.path.isdir(USERS) else []:
        prof = profile(d)
        has_tt = os.path.exists(os.path.join(user_dir(d), "cookies.json"))
        rows.append("• {} — {} чат(ов), TikTok {}".format(
            esc(prof.get("name", d)), len(targets_of(d)), "✅" if has_tt else "—"))
    await update.message.reply_text("Подключены:\n" + ("\n".join(rows) if rows else "никого"),
                                    parse_mode=ParseMode.HTML)


def main():
    c = cfg()
    if not c.get("token"):
        print("Нет токена в config.json")
        return
    if not c.get("invite_code"):
        c["invite_code"] = secrets.token_hex(3).upper()
        save_cfg(c)
    os.makedirs(USERS, exist_ok=True)

    # Сервер в РФ, Bot API режется DPI — ходим к Telegram через локальный
    # WARP-прокси. Playwright при этом остаётся на прямом IP, иначе TikTok
    # увидит смену страны и выбросит сессию.
    proxy = c.get("proxy") or None
    # user_data держит режим ожидания ввода («жду инвайт-код», «жду список ников»).
    # Без persistence рестарт бота посреди диалога терял его, и следующее
    # сообщение человека уходило в «Не понял».
    builder = ApplicationBuilder().token(c["token"]).persistence(
        PicklePersistence(filepath=os.path.join(HERE, "ptb_state.pickle")))
    if proxy:
        builder = builder.proxy(proxy).get_updates_proxy(proxy)
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("code", cmd_code))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("cookies", cmd_cookies))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log("Бот запущен.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
