# -*- coding: utf-8 -*-
"""
Общее ядро работы с TikTok-вебом: куки, браузер, список чатов, отправка.
Используется и раннером рассылки, и QR-логином, и ботом.
"""
import os, json, time, datetime

# UA обязан совпадать с браузером, где заводилась сессия, иначе TikTok
# считает её угнанной и выкидывает на /login.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/150.0.0.0 Safari/537.36")

MESSAGE = "❤️"
MIN_DELAY, MAX_DELAY = 6, 25
MAX_CHATS = 200

CHAT_ITEM = ['div[class*="DivItemWrapper"]',
             '[data-e2e="chat-list-item"]',
             '[data-e2e="chat-list"] [role="listitem"]']
CHAT_LIST = ['[data-e2e="chat-list"]',
             'div[class*="DivConversationListContainer"]',
             'div[class*="DivChatListContainer"]']
COMPOSER = ['[data-e2e="message-input-area"] div[contenteditable="true"]',
            'div[contenteditable="true"][role="textbox"]',
            'div[contenteditable="plaintext-only"]',
            'div[contenteditable="true"]']
NICK_SEL = '[data-e2e="dm-new-conversation-nickname"]'

LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled",
               "--no-sandbox", "--disable-dev-shm-usage",
               "--no-first-run", "--no-default-browser-check",
               "--disable-gpu", "--js-flags=--max-old-space-size=512"]

STEALTH = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def hold_browser_lock():
    """Межпроцессный замок: Chromium строго по одному на весь сервер.

    Внутри бота хватало asyncio.Lock, но крон-прогоны — отдельные процессы,
    и с четырьмя запусками в сутки они рано или поздно налезут на нажатие
    кнопки в боте. На 1 CPU и 2 ГБ два Chromium сразу — это OOM.

    Замок держится до конца процесса: возвращённый файл нужно сохранить,
    иначе сборщик мусора закроет его и снимет блокировку.
    """
    import fcntl
    f = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".browser.lock"), "w")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def make_logger(path):
    def log(msg):
        line = "[{}] {}".format(now(), msg)
        print(line, flush=True)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
    return log


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {} if default is None else default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def normalize_cookies(data):
    """Приводит куки из любого формата к тому, что ест Playwright."""
    if isinstance(data, dict):
        data = data.get("cookies", [])
    out = []
    for c in data:
        if "tiktok" not in c.get("domain", ""):
            continue
        item = {k: c[k] for k in ("name", "value", "domain", "path") if k in c}
        item["secure"] = bool(c.get("secure", True))
        item["httpOnly"] = bool(c.get("httpOnly", False))
        # Cookie-Editor пишет sameSite словами вроде no_restriction/unspecified
        ss = str(c.get("sameSite") or "Lax").lower()
        ss = {"no_restriction": "None", "none": "None", "unspecified": "Lax",
              "lax": "Lax", "strict": "Strict"}.get(ss, "Lax")
        item["sameSite"] = ss
        # ...и срок жизни под своим именем; без него Playwright сделает куку
        # сеансовой, и TikTok будет считать сессию свежей при каждом запуске
        exp = c.get("expires") or c.get("expirationDate") or c.get("expiry")
        if exp:
            try:
                item["expires"] = int(float(exp))
            except Exception:
                pass
        out.append(item)
    return out


def parse_cookies_blob(raw):
    """Куки из файла, который человек прислал боту.

    Понимает JSON от Cookie-Editor (список или {"cookies": [...]}) и
    Netscape-формат cookies.txt, который отдают расширения вида
    «Get cookies.txt». Возвращает то же, что load_cookies.
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", "replace")
    raw = raw.strip().lstrip("﻿")
    if raw[:1] in ("{", "["):
        return normalize_cookies(json.loads(raw))

    out = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, path, secure, expires, name, value = parts[:7]
        out.append({"domain": domain, "path": path, "name": name, "value": value,
                    "secure": secure.strip().upper() == "TRUE", "expires": expires})
    return normalize_cookies(out)


def load_cookies(path):
    return normalize_cookies(load_json(path, []))


def new_context(p, cookies=None, headless=True, scale=1):
    """scale > 1 нужен только QR-логину: тогда страница рисуется как на retina
    и QR-библиотека TikTok отдаёт canvas в этом же разрешении. Рассылке это
    ни к чему — лишние пиксели на 1 CPU и 2 ГБ."""
    browser = p.chromium.launch(headless=headless, args=LAUNCH_ARGS)
    _ctx_kw = {"viewport": {"width": 1280, "height": 900}, "locale": "ru-RU",
               "user_agent": UA, "device_scale_factor": scale}
    _px = os.environ.get("TT_PROXY", "").strip()
    if _px:
        # TikTok привязывает сессию к стране: бот в NL, а куки из РФ,
        # поэтому TikTok-трафик идём через московский прокси.
        import re as _re
        m = _re.match(r"socks5://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)", _px)
        if m:
            u, pw, host, port = m.groups()
            _ctx_kw["proxy"] = {"server": f"socks5://{host}:{port}"}
            if u:
                _ctx_kw["proxy"]["username"] = u
                _ctx_kw["proxy"]["password"] = pw
    ctx = browser.new_context(**_ctx_kw)
    ctx.add_init_script(STEALTH)
    if cookies:
        ctx.add_cookies(cookies)
    return browser, ctx


def find_all(page, selectors):
    for sel in selectors:
        loc = page.locator(sel)
        try:
            n = loc.count()
        except Exception:
            n = 0
        if n:
            return loc, n, sel
    return None, 0, None


def find_one(page, selectors, timeout=10000):
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        for sel in selectors:
            loc = page.locator(sel).first
            try:
                if loc.count() and loc.is_visible():
                    return loc
            except Exception:
                pass
        page.wait_for_timeout(300)
    return None


def open_messages(page):
    """Открывает список сообщений. Возвращает False, если сессия не принята."""
    page.goto("https://www.tiktok.com/messages",
              wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(8000)
    return "/login" not in page.url


def collect_chats(page):
    """Ждёт прогрузки виртуального списка, скроллит и возвращает [(idx, ник)]."""
    for _ in range(25):
        _, count, _ = find_all(page, CHAT_ITEM)
        if count:
            break
        page.wait_for_timeout(1000)

    box = find_one(page, CHAT_LIST, timeout=5000)
    for _ in range(25):
        _, before, _ = find_all(page, CHAT_ITEM)
        try:
            if box:
                box.evaluate("el => el.scrollTop = el.scrollHeight")
            else:
                page.mouse.wheel(0, 1200)
        except Exception:
            pass
        page.wait_for_timeout(700)
        _, after, _ = find_all(page, CHAT_ITEM)
        if after == before:
            break

    items, count, _ = find_all(page, CHAT_ITEM)
    chats = []
    for i in range(min(count, MAX_CHATS)):
        chats.append((i, item_nick(items, i)))
    return chats


def item_nick(items, i):
    try:
        nick = items.nth(i).locator(NICK_SEL).first.inner_text().strip()
    except Exception:
        nick = ""
    if not nick:
        try:
            nick = (items.nth(i).inner_text().split("\n")[0] or "").strip()
        except Exception:
            nick = "chat#{}".format(i)
    return nick


def click_chat_by_name(page, name):
    """Находит чат по нику в ТЕКУЩЕМ состоянии списка и кликает по нему.

    Клик по запомненному индексу не годится: TikTok пересортировывает список
    на лету (каждая отправка и каждое входящее поднимают чат наверх), а сам
    список виртуальный — в DOM может быть отрисована только часть элементов.
    Из-за этого сохранённые индексы съезжали и ❤️ улетали не тем людям —
    первым чатам подряд, а не тем, кто в targets.txt.
    """
    box = find_one(page, CHAT_LIST, timeout=5000)
    try:
        if box:
            box.evaluate("el => el.scrollTop = 0")
            page.wait_for_timeout(600)
    except Exception:
        pass

    last_top = -1
    for _ in range(40):
        items, count, _ = find_all(page, CHAT_ITEM)
        for i in range(min(count, MAX_CHATS)):
            if item_nick(items, i) == name:
                items.nth(i).click()
                return True
        cur_top = None
        try:
            if box:
                cur_top = box.evaluate(
                    "el => { el.scrollTop = el.scrollTop + el.clientHeight; return el.scrollTop; }")
            else:
                page.mouse.wheel(0, 1000)
        except Exception:
            pass
        page.wait_for_timeout(600)
        # scrollTop упёрся в максимум — список кончился, ника в нём нет
        if cur_top is not None and cur_top == last_top:
            break
        last_top = cur_top
    return False


def send_to_chat(page, name, message=MESSAGE):
    """Открывает чат по нику и отправляет сообщение."""
    if not click_chat_by_name(page, name):
        raise RuntimeError("чат «{}» не найден в списке".format(name))
    page.wait_for_timeout(2500)
    send_in_open_chat(page, message)


def prune_state(state, days=7):
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    state["sent"] = {k: v for k, v in state.get("sent", {}).items() if k[:10] >= cutoff}
    return state


# ---------- автоответ на присланные видео ----------

REPLY_MIN_DELAY, REPLY_MAX_DELAY = 6, 25
# Сколько ответов в сутки на один чат — предохранитель от петли, если детект
# вдруг начнёт срабатывать на собственное сообщение.
MAX_REPLIES_PER_DAY = 25

# Видео-шер отличается от текстового сообщения контейнером превью. Он есть даже
# когда само видео в headless не подгрузилось и на его месте «Это видео
# недоступно» — то есть детект не зависит от того, открылось видео или нет.
VIDEO_PART = '[class*="DivVideoContainer"]'
MSG_ITEM = '[data-e2e="dm-new-chat-item"]'

# Направление сообщения берётся из вычисленного flex-direction его строки:
# row — прислали нам, row-reverse — писали мы. Классы и разметка у обеих сторон
# одинаковые, по DOM их не различить, только по этому свойству.
READ_MSGS_JS = r"""
(limit) => {
  const items = [...document.querySelectorAll('[data-e2e="dm-new-chat-item"]')];
  return items.slice(-limit).map(el => {
    const holder = el.closest('[data-index]');
    const row = el.querySelector('[data-area="Actions"]') || el.firstElementChild;
    const dir = row ? getComputedStyle(row).flexDirection : '';
    return {
      id: holder ? holder.getAttribute('data-index') : '',
      incoming: dir === 'row',
      video: !!el.querySelector('[class*="DivVideoContainer"]'),
      text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 120)
    };
  });
}
"""

CHAT_ROWS_JS = r"""
() => [...document.querySelectorAll('div[class*="DivItemWrapper"]')].map(el => {
  const nick = el.querySelector('[data-e2e="dm-new-conversation-nickname"]');
  return {
    conv: el.getAttribute('data-conv-id') || '',
    nick: nick ? nick.innerText.trim() : ((el.innerText || '').split('\n')[0] || '').trim(),
    preview: (el.innerText || '').replace(/\n/g, ' | ').slice(0, 200)
  };
})
"""


def read_messages(page, limit=20):
    """Хвост ОТКРЫТОГО чата: id сообщения, входящее ли, видео ли, текст."""
    try:
        return page.evaluate(READ_MSGS_JS, limit)
    except Exception:
        return []


def chat_rows(page):
    """Строки списка чатов вместе с превью последнего сообщения.

    Превью — дешёвый способ понять, что в чате ничего не изменилось: в него
    входит и текст последнего сообщения, и его время. Совпало с прошлым
    прогоном — чат можно не открывать, а значит и не помечать прочитанным
    у собеседника лишний раз.
    """
    try:
        return page.evaluate(CHAT_ROWS_JS)
    except Exception:
        return []


def send_in_open_chat(page, message):
    """Отправляет текст в УЖЕ открытый чат."""
    box = find_one(page, COMPOSER, timeout=12000)
    if box is None:
        raise RuntimeError("поле ввода не найдено")
    box.click()
    page.wait_for_timeout(400)
    page.keyboard.insert_text(message)
    page.wait_for_timeout(500)
    page.keyboard.press("Enter")
    page.wait_for_timeout(2000)


def pending_videos(msgs):
    """Видео из хвоста переписки после последнего НАШЕГО сообщения.

    Как только мы ответили, хвост становится пустым — поэтому дважды на одно
    и то же видео бот не отвечает. Заодно правильно отрабатывает случай
    «видео + подпись текстом» и несколько видео подряд: ответ будет один.
    """
    tail = []
    for m in reversed(msgs):
        if not m.get("incoming"):
            break
        tail.append(m)
    tail.reverse()
    return [m for m in tail if m.get("video")]
