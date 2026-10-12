# -*- coding: utf-8 -*-
"""
Прогон автоответчика по всем, кто его включил. Запускается кроном раз в
несколько минут.

Каждый пользователь — отдельный процесс: на сервере 1 CPU и 2 ГБ, Chromium
строго по одному (за этим следит flock внутри autoreply.py). Если включивших
нет, браузер вообще не поднимается — выходим за доли секунды.
"""
import sys, os, json, subprocess, time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tt_core as core
import notify

HERE = os.path.dirname(os.path.abspath(__file__))
USERS = os.path.join(HERE, "users")
PY = sys.executable  # тот же интерпретатор, что запустил процесс (работает и из venv)
TIMEOUT = 20 * 60

log = core.make_logger(os.path.join(HERE, "autoreply.log"))


def enabled(user_dir):
    st = core.load_json(os.path.join(user_dir, "reply_state.json"), {})
    return bool(st.get("enabled"))


def run_one(user_dir):
    proc = subprocess.run([PY, os.path.join(HERE, "autoreply.py"), user_dir, "--live"],
                          capture_output=True, text=True, timeout=TIMEOUT)
    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith("RESULT "):
            return json.loads(line[7:])
    return {"error": "no_result", "replied": [], "fail": 0}


def main():
    if not os.path.isdir(USERS):
        return
    uids = sorted(d for d in os.listdir(USERS) if os.path.isdir(os.path.join(USERS, d)))
    active = [u for u in uids if enabled(os.path.join(USERS, u))]
    if not active:
        return

    for uid in active:
        user_dir = os.path.join(USERS, uid)
        prof = core.load_json(os.path.join(user_dir, "profile.json"), {})
        title = prof.get("name") or uid
        try:
            res = run_one(user_dir)
        except subprocess.TimeoutExpired:
            res = {"error": "timeout", "replied": [], "fail": 0}
        except Exception as e:
            res = {"error": str(e), "replied": [], "fail": 0}

        err = res.get("error")
        if err:
            log("{}: ошибка {}".format(title, err))
            # Про протухшую сессию человеку сказать надо — сам он этого
            # не заметит, автоответчик просто молча перестанет отвечать.
            if err in ("login_required", "no_session") and uid.isdigit():
                st = core.load_json(os.path.join(user_dir, "reply_state.json"), {})
                if st.get("warned") != err:
                    notify.send(uid, "Автоответ на видео встал: сессия TikTok больше не принимается.\n"
                                     "Подключи TikTok заново, и он сам продолжит.")
                    st["warned"] = err
                    core.save_json(os.path.join(user_dir, "reply_state.json"), st)
        elif res.get("replied"):
            st = core.load_json(os.path.join(user_dir, "reply_state.json"), {})
            if st.pop("warned", None):
                core.save_json(os.path.join(user_dir, "reply_state.json"), st)
            log("{}: ответил {}".format(
                title, ", ".join("{} → {}".format(r["nick"], r["phrase"]) for r in res["replied"])))
        time.sleep(3)


if __name__ == "__main__":
    main()
