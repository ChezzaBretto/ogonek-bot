# -*- coding: utf-8 -*-
"""
Отправка сообщений в Telegram из скриптов вне бота (ночной крон-прогон).
Ходит через WARP-прокси: сервер в РФ, Bot API напрямую не отвечает.
"""
import os, json

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")


def _cfg():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def send(chat_id, text):
    cfg = _cfg()
    token = cfg.get("token")
    if not token or not chat_id:
        return False
    proxy = cfg.get("proxy") or None
    url = "https://api.telegram.org/bot{}/sendMessage".format(token)
    try:
        import httpx
        with httpx.Client(proxy=proxy, timeout=25) as cl:
            r = cl.post(url, data={"chat_id": chat_id, "text": text})
            return r.json().get("ok", False)
    except Exception:
        return False


def send_admin(text):
    return send(_cfg().get("admin_id"), text)
