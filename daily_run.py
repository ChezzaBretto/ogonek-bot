import sys, os, json, subprocess, time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tt_core as core
import notify

HERE = os.path.dirname(os.path.abspath(__file__))
USERS = os.path.join(HERE, "users")
PY = sys.executable
TIMEOUT = 25 * 60

log = core.make_logger(os.path.join(HERE, "daily.log"))

ERRORS = {
    "no_cookies": "TikTok не подключён",
    "no_session": "сессия битая",
    "login_required": "сессия протухла",
    "no_chats": "список чатов не открылся",
}

def run_one(uid):
    user_dir = os.path.join(USERS, uid)
    proc = subprocess.run([PY, os.path.join(HERE, "runner.py"), user_dir, "--live"],
                          capture_output=True, text=True, timeout=TIMEOUT)
    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith("RESULT "):
            return json.loads(line[7:])
    return {"error": "no_result", "ok": 0, "fail": 0, "sent": [], "missing": []}

def main():
    if not os.path.isdir(USERS):
        log("Нет папки users")
        return

    uids = sorted(d for d in os.listdir(USERS)
                  if os.path.isdir(os.path.join(USERS, d)))
    log("=" * 45)
    log("Старт. Пользователей: {}".format(len(uids)))

    report = []
    for uid in uids:
        user_dir = os.path.join(USERS, uid)
        prof = core.load_json(os.path.join(user_dir, "profile.json"), {})
        title = prof.get("name") or uid
        targets_path = os.path.join(user_dir, "targets.txt")

        if not os.path.exists(os.path.join(user_dir, "cookies.json")):
            log("{}: TikTok не подключён, пропуск".format(title))
            continue
        if not os.path.exists(targets_path) or not open(targets_path, encoding="utf-8").read().strip():
            log("{}: пустой список чатов, пропуск".format(title))
            continue

        log("--- {} ({})".format(title, uid))
        try:
            res = run_one(uid)
        except subprocess.TimeoutExpired:
            res = {"error": "timeout", "ok": 0, "fail": 0, "sent": [], "missing": []}
        except Exception as e:
            res = {"error": str(e), "ok": 0, "fail": 0, "sent": [], "missing": []}

        err = res.get("error")
        if err:
            log("{}: ошибка {}".format(title, err))
            report.append("⚠️ {} — {}".format(title, ERRORS.get(err, err)))
            if err in ("login_required", "no_session") and uid.isdigit():
                notify.send(uid, "Сессия TikTok протухла — огоньки сегодня не продлились.\n"
                                 "Нажми «Подключить TikTok» и отсканируй QR заново.")
        else:
            log("{}: успешно {}, ошибок {}".format(title, res["ok"], res["fail"]))
            line = "✅ {} — {} ❤️".format(title, res["ok"])
            if res["fail"]:
                line += ", сбоев {}".format(res["fail"])
            if res.get("missing"):
                line += "\n   не найдены: " + ", ".join(res["missing"])
            report.append(line)
            if uid.isdigit() and (res["fail"] or res.get("missing")):
                msg = "Ночной прогон: отправлено {} ❤️".format(res["ok"])
                if res.get("missing"):
                    msg += "\n\nНе нашёл эти чаты: " + ", ".join(res["missing"]) + \
                           "\nПроверь написание ника — оно должно совпадать точь-в-точь."
                notify.send(uid, msg)

        time.sleep(5)

    log("Готово.")
    if report:
        notify.send_owner("🌙 Ночной прогон\n\n" + "\n".join(report))

if __name__ == "__main__":
    main()
