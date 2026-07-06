"""Sync logic. Asana = source of truth.
 - Telegram command  -> writes Asana -> notifies Teams + Telegram
 - Asana webhook event -> notifies Teams + Telegram
"""
import asana_client, teams_client, telegram_client
from config import TELEGRAM_CHAT_ID

def notify_all(title, text):
    teams_client.post(title, text)
    telegram_client.send(TELEGRAM_CHAT_ID, f"<b>{title}</b>\n{text}")

def handle_telegram_command(text):
    """Parse a Telegram command and act on Asana. Returns a reply string."""
    text = (text or "").strip()
    if text.startswith("/newtask"):
        # /newtask Title | assignee | YYYY-MM-DD
        parts = [p.strip() for p in text[len("/newtask"):].split("|")]
        if not parts or not parts[0]:
            return "Usage: /newtask Title | assignee | YYYY-MM-DD"
        t = asana_client.create_task(parts[0],
                                     assignee=parts[1] if len(parts) > 1 else None,
                                     due_on=parts[2] if len(parts) > 2 else None)
        notify_all("New task created", f"{t['name']} (Asana #{t['gid']})")
        return f"✅ Created in Asana: {t['name']} (#{t['gid']})"
    if text.startswith("/done"):
        gid = text.replace("/done", "").strip()
        t = asana_client.complete_task(gid)
        if not t: return f"⚠️ No Asana task #{gid}"
        notify_all("Task completed", f"{t['name']} (Asana #{gid})")
        return f"✔️ Marked done in Asana: {t['name']}"
    if text.startswith("/tasks"):
        rows = asana_client.list_tasks()
        return "\n".join(f"#{t['gid']} {'✅' if t.get('completed') else '⬜'} {t['name']}" for t in rows)
    return "Commands: /newtask, /done <id>, /tasks, /digest"

def handle_asana_event(event):
    """Called when Asana webhook fires. event: {resource, action}."""
    gid = event.get("resource", {}).get("gid")
    action = event.get("action")
    t = asana_client.get_task(gid) if gid else None
    name = t.get("name") if t else gid
    notify_all("Asana update", f"{action}: {name}")
    return True
