# Planner destination — setup & auto-provision

The Teams bot can write to **Microsoft Planner** instead of Asana by flipping one switch.
Same bot brain, swappable hub (`DEST=asana` | `DEST=planner`).

## Why a "Plan" must exist first
A Planner **task** lives inside a **Plan** (the board), and a Plan hangs off an **M365 Group**.
This is exactly like Asana, where tasks live inside the **project** we already created.
Planner just starts empty, so we provision **one** Plan — once — then every task is automatic.

`planner_client.py` can create that Plan from code (no clicking) via `ensure_plan()`.

## Order of resolution (ensure_plan)
1. If `PLANNER_PLAN_ID` is set → use it (fastest; skips all lookups).
2. Else find the M365 group named `PLANNER_GROUP_NAME` (or use `PLANNER_GROUP_ID`).
3. Else create that group (needs `Group.ReadWrite.All`), optionally owned by `PLANNER_GROUP_OWNER`.
4. Find-or-create the plan titled `PLANNER_PLAN_TITLE` inside the group → cache its id.

It's **idempotent** — re-running never makes duplicates.

## Graph permissions (Application, admin-consented on the bot app)
| Permission | Needed for |
|---|---|
| `Tasks.ReadWrite.All` | create/read Planner plans & tasks — **already granted** |
| `Group.Read.All` | find an existing group by name |
| `Group.ReadWrite.All` | auto-create the group + set its owner |

If you don't want to grant `Group.ReadWrite.All`, create the group once by hand (or reuse an
existing Team), then set `PLANNER_GROUP_ID` — only the plan is auto-created, which needs just
`Tasks.ReadWrite.All`.

## Railway env vars
```
DEST=planner
# Option 1 — you already have the plan id:
PLANNER_PLAN_ID=<paste plan id>
# Option 2 — let the bot provision it:
PLANNER_GROUP_NAME=Messer 1MW
PLANNER_GROUP_MAILNICK=messer1mw
PLANNER_PLAN_TITLE=Messer 1MW — Project Schedule
PLANNER_GROUP_OWNER=admin@<tenant>.onmicrosoft.com   # recommended
# optional:
PLANNER_GROUP_ID=<group id if reusing an existing group/Team>
PLANNER_DEFAULT_ASSIGNEE=lead.test@indefolsolar.onmicrosoft.com
```
(The Graph app credentials `MICROSOFT_APP_ID/PASSWORD/TENANT_ID` are the same ones the Teams
bot already uses — no new secret.)

## Engineer CLI (run locally or on Railway shell)
```
python planner_client.py ensure-plan   # prints: PLANNER_PLAN_ID=<id>  (paste into Railway)
python planner_client.py list           # lists tasks currently in the plan
```

## Demo flow to the team
1. Show `DEST=asana` → send a task in Teams → it lands in Asana.
2. Change `DEST=planner` on Railway → redeploy → send the same task → it lands in Planner,
   same `[Category] (Owner - role) — date (Teams)` title, assigned to the owner.
3. One bot, two hubs — this is the "evaluate Asana vs Planner" comparison for management.
