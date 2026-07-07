"""One-command importer: build the full Messer 1MW schedule into the Planner plan.

Reads messer_schedule.json (58 tasks / 11 phase-sections, parsed from MS Project Rev06),
creates one Planner *bucket* per section and one *task* per row (with due date, notes, and
assignee). Idempotent: re-running never duplicates — buckets/tasks already present are skipped.

Run in the Railway Console (same env as the bot):
    python planner_import.py            # import into the plan
    python planner_import.py --dry-run  # print what it WOULD do, no writes

Uses the same Graph app credentials + PLANNER_PLAN_ID as the Teams bot.
"""
import os, sys, json
import planner_client as pc

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "messer_schedule.json")

# Each section/bucket has ONE fixed owner (set by PM/lead up front). EVERY task in the
# section is pre-assigned to that person. Bucket names must match the schedule exactly.
_DOMAIN = "@indefolsolar.onmicrosoft.com"
SECTION_OWNER = {
    "DELIVERY":                   "pm.test" + _DOMAIN,    # A. John
    "ELECTROLYZER":               "lead.test" + _DOMAIN,  # Quoc Dao
    "WATER TREATMENT":            "mem1.test" + _DOMAIN,  # A. Phuc Phillip
    "ELECTRIC POWER":             "mem2.test" + _DOMAIN,  # A. Hieu
    "SEPARRATOR + GAS SCRUBBER":  "mem3.test" + _DOMAIN,  # A. Nhu
    "PURIFICATION":               "mem4.test" + _DOMAIN,  # Tung
    "CONTAINER":                  "mem5.test" + _DOMAIN,  # A. Linh
    "HOÀN THIỆN HỆ THỐNG":        "mem6.test" + _DOMAIN,  # C. Tuong
    "FAT":                        "mem7.test" + _DOMAIN,  # Phuc K
    # Not yet assigned (no owner given): "BOP", "THI CÔNG CÁC ĐIỂM KẾT NÔI"
}


def main(dry=False):
    rows = json.load(open(DATA, encoding="utf-8"))
    sections = []
    for r in rows:
        if r["section"] not in sections:
            sections.append(r["section"])
    print("Loaded %d tasks across %d sections" % (len(rows), len(sections)))

    pid = pc.ensure_plan()
    print("Plan:", pid)

    if dry:
        for s in sections:
            n = sum(1 for r in rows if r["section"] == s)
            print("  bucket '%s'  (%d tasks)" % (s, n))
        print("[dry-run] no changes made")
        return

    # find-or-create buckets (by name)
    buckets = {b["name"]: b["id"] for b in pc.list_buckets()}
    for s in sections:
        if s not in buckets:
            buckets[s] = pc.create_bucket(s)
            print("+ bucket:", s)
        else:
            print("= bucket exists:", s)

    # existing tasks keyed by (bucket_id, title) -> gid  (so re-runs don't duplicate,
    # and we can back-fill the owner onto tasks that were created before assignment worked)
    existing = {}
    for t in pc.list_tasks():
        existing[(t.get("bucket_id"), t["name"])] = t["gid"]

    created = found = assigned = 0
    for r in rows:
        section = r["section"]
        bid = buckets[section]
        owner = SECTION_OWNER.get(section)              # every task -> section owner
        key = (bid, r["name"])
        if key in existing:
            gid = existing[key]
            found += 1
        else:
            t = pc.create_task(r["name"], notes=r.get("notes", ""), due_on=r.get("due") or None,
                               assignee=owner, bucket_id=bid)
            gid = t["gid"]
            existing[key] = gid
            created += 1
            print("  + [%s] %s" % (section, r["name"]))
        # ensure the assignee is set (idempotent). Needs User.Read.All; skips silently if not.
        if owner and pc.assign_task(gid, owner):
            assigned += 1

    print("Done. created=%d  existing=%d  assigned=%d  buckets=%d"
          % (created, found, assigned, len(buckets)))


if __name__ == "__main__":
    main(dry=("--dry-run" in sys.argv))
