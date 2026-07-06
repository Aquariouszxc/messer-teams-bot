"""Microsoft Teams — post via Incoming Webhook (MessageCard). MOCK prints instead."""
import requests
from config import TEAMS_WEBHOOK_URL, MOCK

def post(title, text):
    if MOCK or not TEAMS_WEBHOOK_URL:
        print(f"[TEAMS] {title}\n        {text}")
        return True
    card = {"@type": "MessageCard", "@context": "http://schema.org/extensions",
            "summary": title, "themeColor": "1F3864",
            "sections": [{"activityTitle": title, "text": text}]}
    try:
        r = requests.post(TEAMS_WEBHOOK_URL, json=card, timeout=10)
        return r.ok
    except Exception:
        return False
