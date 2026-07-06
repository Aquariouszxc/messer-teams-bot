"""Telegram Bot API send helper. MOCK prints instead."""
import requests
from config import TELEGRAM_BOT_TOKEN, MOCK

def send(chat_id, text):
    if MOCK or not TELEGRAM_BOT_TOKEN:
        print(f"[TELEGRAM->{chat_id}] {text}")
        return True
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                          json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
        return r.ok
    except Exception:
        return False
