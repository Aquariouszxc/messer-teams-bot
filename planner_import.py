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

    # existing tasks keyed by (bucket_id, title) so re-runs don't duplicate
    existing = {(t.get("bucket_id"), t["name"]) for t in pc.list_tasks()}

    created = skipped = 0
    for r in rows:
        bid = buckets[r["section"]]
        if (bid, r["name"]) in existing:
            skipped += 1
            continue
        pc.create_task(r["name"], notes=r.get("notes", ""), due_on=r.get("due") or None,
                       assignee=r.get("assignee") or None, bucket_id=bid)
        existing.add((bid, r["name"]))
        created += 1
        print("  + [%s] %s" % (r["section"], r["name"]))

    print("Done. created=%d  skipped(existing)=%d  buckets=%d" % (created, skipped, len(buckets)))


if __name__ == "__main__":
    main(dry=("--dry-run" in sys.argv))
