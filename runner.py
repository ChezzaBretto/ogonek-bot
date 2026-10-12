# -*- coding: utf-8 -*-
"""
Прогон для ОДНОГО пользователя.

  python runner.py <user_dir>            тест: показать чаты и цели, ничего не слать
  python runner.py <user_dir> --live     боевой: разослать ❤️ по targets.txt
  python runner.py <user_dir> --list     только собрать список чатов

Последней строкой печатает  RESULT {json}  — это и читает бот.
"""
import sys, os, json, time, random, datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tt_core as core
from playwright.sync_api import sync_playwright


def run(user_dir, live=False, list_only=False):
    cookies_path = os.path.join(user_dir, "cookies.json")
    state_path = os.path.join(user_dir, "state.json")
    targets_path = os.path.join(user_dir, "targets.txt")
    log = core.make_logger(os.path.join(user_dir, "bot.log"))

    res = {"ok": 0, "fail": 0, "sent": [], "missing": [], "chats": [], "error": None}

    if not os.path.exists(cookies_path):
        res["error"] = "no_cookies"
        return res

    cookies = core.load_cookies(cookies_path)
    if not any(c["name"] == "sessionid" for c in cookies):
        res["error"] = "no_session"
        return res

    state = core.load_json(state_path, {})
    state.setdefault("sent", {})
    today = datetime.date.today().isoformat()

    wanted = []
    if os.path.exists(targets_path):
        with open(targets_path, encoding="utf-8") as f:
            wanted = [ln.strip() for ln in f if ln.strip()]

    with sync_playwright() as p:
        browser, ctx = core.new_context(p, cookies)
        page = ctx.new_page()
        try:
            log("Открываю сообщения...")
            if not core.open_messages(page):
                log("TikTok просит вход — сессия не принята.")
                res["error"] = "login_required"
                return res

            chats = core.collect_chats(page)
            if not chats:
                log("Чаты не открылись, пробую ещё раз...")
                time.sleep(5)
                page.reload()
                if not core.open_messages(page):
                    log("TikTok просит вход — сессия не принята.")
                    res["error"] = "login_required"
                    return res
                chats = core.collect_chats(page)

            res["chats"] = [n for _, n in chats]
            log("Найдено чатов: {}".format(len(chats)))

            if list_only:
                return res
            if not chats:
                res["error"] = "no_chats"
                return res

            found = {n for _, n in chats}
            targets = [(i, n) for i, n in chats if n in set(wanted)]
            res["missing"] = [w for w in wanted if w not in found]
            log("Целей: {}, найдено: {}".format(len(wanted), len(targets)))

            if not live:
                return res

            for _idx, name in targets:
                key = "{}::{}".format(today, name)
                if state["sent"].get(key):
                    log("Пропуск, уже сегодня: " + name)
                    continue
                try:
                    # ищем чат по нику в актуальном списке: TikTok поднимает
                    # чаты наверх после каждой отправки, индексы живут минуты
                    core.send_to_chat(page, name)
                    state["sent"][key] = datetime.datetime.now().isoformat()
                    core.save_json(state_path, state)
                    res["ok"] += 1
                    res["sent"].append(name)
                    log("Отправлено: " + name)
                except Exception as e:
                    res["fail"] += 1
                    log("ОШИБКА у {}: {}".format(name, e))
                time.sleep(random.randint(core.MIN_DELAY, core.MAX_DELAY))

            log("Итог. Успешно: {}, ошибок: {}".format(res["ok"], res["fail"]))
        except Exception as e:
            res["error"] = str(e)
            log("СБОЙ: {}".format(e))
        finally:
            try:
                ctx.close(); browser.close()
            except Exception:
                pass

    core.save_json(state_path, core.prune_state(core.load_json(state_path, {"sent": {}})))
    return res


def main():
    if len(sys.argv) < 2:
        print("нужен user_dir")
        return
    user_dir = sys.argv[1]
    args = set(sys.argv[2:])
    # Ждём, пока освободится единственный Chromium на сервере. Держим ссылку:
    # закроется файл — снимется замок.
    _lock = core.hold_browser_lock()  # noqa: F841
    res = run(user_dir, live="--live" in args, list_only="--list" in args)
    print("RESULT " + json.dumps(res, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
