"""Configuration. If real tokens aren't set, the bot runs in MOCK mode (safe for demos)."""
import os
try:
    from dotenv import load_dotenv; load_dotenv()
except Exception:
    pass

# --- Asana (SOURCE OF TRUTH) ---
ASANA_TOKEN = os.getenv("ASANA_TOKEN", "").strip()
ASANA_PROJECT_GID = os.getenv("ASANA_PROJECT_GID", "").strip()

# --- Telegram (two-way chat) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()   # where notifications go
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "hook").strip()

# --- Microsoft Teams (incoming webhook per channel) ---
TEAMS_WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL", "").strip()

# MOCK = no external calls; prints instead. Auto-on when Asana token missing.
DEST = os.getenv("DEST", "asana").strip().lower()   # asana | planner
MOCK = os.getenv("MOCK", "").strip().lower() in ("1", "true", "yes") or (
    not ASANA_TOKEN and not os.getenv("PLANNER_PLAN_ID", "").strip())

# Status mapping between Asana (completed flag + custom field) and our vocabulary
STATUSES = ["To do", "In progress", "Blocked", "Done"]
