"""Build a status digest from Asana tasks (the Messer-style weekly report)."""
from datetime import date
import asana_client

def build_digest():
    tasks = asana_client.list_tasks()
    done = [t for t in tasks if t.get("completed")]
    open_ = [t for t in tasks if not t.get("completed")]
    today = date.today().isoformat()
    overdue = [t for t in open_ if t.get("due_on") and t["due_on"] < today]
    lines = [f"📊 INDEFOL project digest — {today}",
             f"Open: {len(open_)} · Done: {len(done)} · Overdue: {len(overdue)}", ""]
    if overdue:
        lines.append("⚠️ Overdue:")
        lines += [f"  • {t['name']} (due {t['due_on']})" for t in overdue]
        lines.append("")
    lines.append("🟢 Open tasks:")
    lines += [f"  • {t['name']}"
              + (f" — {t['assignee']['name']}" if t.get('assignee') else "")
              + (f" (due {t['due_on']})" if t.get('due_on') else "") for t in open_] or ["  (none)"]
    return "\n".join(lines)
