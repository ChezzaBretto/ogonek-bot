# -*- coding: utf-8 -*-
"""
Добор недоставленных ❤️ в течение дня.

Ночной прогон в 00:00 может дойти не до всех чатов: протухла сессия, TikTok
икнул, сервер был занят, ник в списке написан с опечаткой. Огонёк тогда сгорит,
а заметить это некому — в веб-версии TikTok огоньков не видно вообще, так что
проверить их бот не может. Поэтому вместо проверки — доборы в течение дня.

Браузер поднимается только если реально есть что досылать: если за сегодня всё
разослано, скрипт выходит за доли секунды и Chromium не трогает.

  python retry_run.py           обычный добор
  python retry_run.py --final   последний за день: о недоборе пишем человеку

Восстановить уже сгоревший огонёк бот не может — кнопка восстановления живёт
только в приложении. Поэтому --final шлёт список проблемных чатов человеку,
пока не истекло 48-часовое окно, в которое TikTok позволяет восстановить.
"""
import sys, os, json, datetime, time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tt_core as core
import notify
import daily_run

HERE = os.path.dirname(os.path.abspath(__file__))
USERS = os.path.join(HERE, "users")

log = core.make_logger(os.path.join(HERE, "daily.log"))


def targets_of(user_dir):
    path = os.path.join(user_dir, "targets.txt")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]


def pending(user_dir):
    """Ники, которым сегодня ещё не отправляли. Ключ состояния — 'ГГГГ-ММ-ДД::ник'."""
    state = core.load_json(os.path.join(user_dir, "state.json"), {}) or {}
    sent = state.get("sent", {})
    today = datetime.date.today().isoformat()
    return [t for t in targets_of(user_dir) if "{}::{}".format(today, t) not in sent]


def main():
    final = "--final" in sys.argv[1:]
    if not os.path.isdir(USERS):
        return

    uids = sorted(d for d in os.listdir(USERS) if os.path.isdir(os.path.join(USERS, d)))
    log("~" * 45)
    log("Добор{}. Пользователей: {}".format(" (последний за день)" if final else "", len(uids)))

    report = []
    for uid in uids:
        user_dir = os.path.join(USERS, uid)
        prof = core.load_json(os.path.join(user_dir, "profile.json"), {})
        title = prof.get("name") or uid

        if not os.path.exists(os.path.join(user_dir, "cookies.json")):
            continue
        if not targets_of(user_dir):
            continue

        left = pending(user_dir)
        if not left:
            log("{}: всё разослано, браузер не поднимаю".format(title))
            continue

        log("--- {} ({}): не доставлено {}".format(title, uid, len(left)))
        try:
            res = daily_run.run_one(uid)
        except Exception as e:
            res = {"error": str(e), "ok": 0, "fail": 0, "sent": [], "missing": []}

        err = res.get("error")
        if err:
            log("{}: ошибка {}".format(title, err))
        else:
            log("{}: добрал {}, ошибок {}".format(title, res["ok"], res["fail"]))

        still = pending(user_dir)
        if still:
            log("{}: осталось недоставленным {}".format(title, ", ".join(still)))
            if final and uid.isdigit():
                notify.send(uid, build_warning(still, err))
            report.append("⚠️ {} — не дошло {} из {}".format(
                title, len(still), len(targets_of(user_dir))))
        else:
            report.append("✅ {} — добрал всё".format(title))

        time.sleep(5)

    log("Добор закончен.")
    if final and report:
        notify.send_owner("🌒 Вечерний добор\n\n" + "\n".join(report))


def build_warning(still, err):
    msg = ("⚠️ Сегодня не получилось отправить ❤️ в чаты:\n• " + "\n• ".join(still) +
           "\n\nОгонёк там может сгореть.")
    if err in ("login_required", "no_session"):
        msg += ("\n\nПричина — сессия TikTok протухла. Нажми «🔗 Подключить TikTok» "
                "и подключись заново.")
    else:
        msg += ("\n\nЕсли ник в списке написан не точь-в-точь как в TikTok, чат не находится. "
                "Сверь через «📋 Показать мои чаты» и поправь «✏️ Задать список».")
    msg += ("\n\nСгоревший огонёк я вернуть не могу — кнопка восстановления есть только "
            "в приложении. В течение 48 часов рядом с чатом появляется серый огонёк "
            "и предложение восстановить: успей нажать.")
    return msg


if __name__ == "__main__":
    main()
