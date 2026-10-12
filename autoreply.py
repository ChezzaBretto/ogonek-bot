import sys, os, json, time, random, datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tt_core as core
from playwright.sync_api import sync_playwright

def read_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]

def pick_phrase(phrases, last):
    if len(phrases) > 1:
        choices = [p for p in phrases if p != last] or phrases
    else:
        choices = phrases
    return random.choice(choices)

def run(user_dir, live=False):
    log = core.make_logger(os.path.join(user_dir, "autoreply.log"))
    state_path = os.path.join(user_dir, "reply_state.json")

    res = {"replied": [], "would": [], "primed": [], "skipped": 0,
           "fail": 0, "missing": [], "error": None}

    cookies_path = os.path.join(user_dir, "cookies.json")
    if not os.path.exists(cookies_path):
        res["error"] = "no_cookies"
        return res
    cookies = core.load_cookies(cookies_path)
    if not any(c["name"] == "sessionid" for c in cookies):
        res["error"] = "no_session"
        return res

    targets = read_lines(os.path.join(user_dir, "reply_targets.txt"))
    phrases = read_lines(os.path.join(user_dir, "phrases.txt"))
    if not targets:
        res["error"] = "no_reply_targets"
        return res
    if not phrases:
        res["error"] = "no_phrases"
        return res

    here = os.path.dirname(os.path.abspath(__file__))
    limit = core.load_json(os.path.join(here, "config.json"), {}).get("autoreply_limit", 0)

    state = core.load_json(state_path, {})
    chats_state = state.setdefault("chats", {})
    today = datetime.date.today().isoformat()

    with sync_playwright() as p:
        browser, ctx = core.new_context(p, cookies)
        page = ctx.new_page()
        try:
            if not core.open_messages(page):
                log("TikTok просит вход — сессия не принята.")
                res["error"] = "login_required"
                return res

            rows = []
            for _ in range(30):
                rows = core.chat_rows(page)
                if rows and any(r["nick"] for r in rows):
                    break
                page.wait_for_timeout(1000)
            if not rows:
                res["error"] = "no_chats"
                return res

            def index(rs):
                out = {}
                for r in rs:
                    if r["nick"]:
                        out.setdefault(r["nick"], r)
                return out

            by_nick = index(rows)

            if any(t not in by_nick for t in targets):
                core.collect_chats(page)
                rows = core.chat_rows(page)
                by_nick = index(rows)
            known = core.load_json(os.path.join(user_dir, "chats.json"), [])
            log("Чатов в списке: {}, целей: {}".format(len(rows), len(targets)))

            for nick in targets:
                row = by_nick.get(nick)
                if row is None:
                    if known and nick in known:
                        res["skipped"] += 1
                    else:
                        res["missing"].append(nick)
                        log("Нет в списке чатов: " + nick)
                    continue

                st = chats_state.get(nick)
                if st is not None and st.get("preview") == row["preview"]:
                    res["skipped"] += 1
                    continue

                if not core.click_chat_by_name(page, nick):
                    res["missing"].append(nick)
                    log("Чат не открылся: " + nick)
                    continue
                page.wait_for_timeout(3000)

                msgs = core.read_messages(page)
                videos = core.pending_videos(msgs)
                fresh = core.chat_rows(page)
                cur_preview = next((r["preview"] for r in fresh if r["nick"] == nick), row["preview"])

                if st is None:
                    chats_state[nick] = {"preview": cur_preview, "day": today,
                                         "count": 0, "last_id": videos[-1]["id"] if videos else ""}
                    core.save_json(state_path, state)
                    res["primed"].append(nick)
                    log("Первый раз вижу чат, запомнил без ответа: " + nick)
                    continue

                if st.get("day") != today:
                    st["day"], st["count"] = today, 0

                if not videos:
                    st["preview"] = cur_preview
                    core.save_json(state_path, state)
                    continue

                target_msg = videos[-1]
                if target_msg["id"] and target_msg["id"] == st.get("last_id"):
                    st["preview"] = cur_preview
                    core.save_json(state_path, state)
                    continue
                if limit and st.get("count", 0) >= limit:
                    log("Дневной лимит ответов исчерпан: " + nick)
                    st["preview"] = cur_preview
                    core.save_json(state_path, state)
                    continue

                phrase = pick_phrase(phrases, state.get("last_phrase"))
                if not live:
                    res["would"].append({"nick": nick, "videos": len(videos), "phrase": phrase})
                    log("Ответил бы {}: {}".format(nick, phrase))
                    continue

                try:
                    core.send_in_open_chat(page, phrase)
                    st["last_id"] = target_msg["id"]
                    st["count"] = st.get("count", 0) + 1
                    state["last_phrase"] = phrase
                    after = core.chat_rows(page)
                    st["preview"] = next((r["preview"] for r in after if r["nick"] == nick), cur_preview)
                    core.save_json(state_path, state)
                    res["replied"].append({"nick": nick, "phrase": phrase, "videos": len(videos)})
                    log("Ответил {}: {}".format(nick, phrase))
                except Exception as e:
                    res["fail"] += 1
                    log("ОШИБКА у {}: {}".format(nick, e))

                time.sleep(random.randint(core.REPLY_MIN_DELAY, core.REPLY_MAX_DELAY))

        except Exception as e:
            res["error"] = str(e)
            log("СБОЙ: {}".format(e))
        finally:
            try:
                ctx.close(); browser.close()
            except Exception:
                pass

    core.save_json(state_path, state)
    return res

def main():
    if len(sys.argv) < 2:
        print("нужен user_dir")
        return
    args = set(sys.argv[2:])
    _lock = core.hold_browser_lock()
    res = run(sys.argv[1], live="--live" in args)
    print("RESULT " + json.dumps(res, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
