"""Offline demo — runs the whole flow in MOCK mode, no tokens needed.
Run: python demo.py"""
import sync, digest

print("="*64)
print("DEMO 1 — Telegram command creates a task in Asana + notifies Teams")
print("="*64)
print(">>> user in Telegram: /newtask Wire telematics gateway | Electrical | 2026-08-15")
print(sync.handle_telegram_command("/newtask Wire telematics gateway | Electrical | 2026-08-15"))

print("\n"+"="*64)
print("DEMO 2 — Telegram marks a task done -> Asana updated -> Teams+Telegram notified")
print("="*64)
print(">>> user in Telegram: /done 1001")
print(sync.handle_telegram_command("/done 1001"))

print("\n"+"="*64)
print("DEMO 3 — Asana webhook fires (someone edited in Asana) -> notify all")
print("="*64)
sync.handle_asana_event({"resource": {"gid": "1002"}, "action": "changed"})

print("\n"+"="*64)
print("DEMO 4 — Weekly digest built from Asana, posted to Teams + Telegram")
print("="*64)
print(digest.build_digest())
