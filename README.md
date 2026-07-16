# reparto-docente-m8

![CI](https://github.com/DocentesTools/reparto-docente-m8/actions/workflows/CI.yaml/badge.svg?branch=main)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/15450e364eff4ad09695c4910153f7bb)](https://app.codacy.com/gh/DocentesTools/reparto-docente-m8/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)
[![codecov](https://codecov.io/github/DocentesTools/reparto-docente-m8/graph/badge.svg?token=9SIK5LAQPQ)](https://codecov.io/github/DocentesTools/reparto-docente-m8)
[![Docker Pulls](https://img.shields.io/docker/pulls/tepochtli/reparto-docente-m8)](https://hub.docker.com/r/tepochtli/reparto-docente-m8)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

Local-first FastAPI service for the Docentes teaching-assignment domain. It is
an authenticated consumer of `fa-auth-m8`: it validates access tokens over the
auth contract and owns no auth-service database or private signing keys.

## What it provides

- School, academic-year, department, teacher-profile, classroom-stage, and
  assignment-process administration.
- Per-process teachers, subjects, teaching groups, generated hour-requirement
  slots, and capacity-enforced assignments.
- Process lifecycle transitions, reopening, draft restoration, summaries,
  dashboards, audit events, and a server-sent event stream with role-projected
  payloads.
- LAN teacher read access, meeting sessions, ordered selection turns, and
  direct assignment choices.
- Process versions, previous-year comparison, and export artifact endpoints.

The API prefix defaults to `/reparto`. Interactive OpenAPI documentation is
available when `SET_DOCS=true` in the service environment.

## Quick start (Windows)

Use the repository's required conda environment:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
. C:\Users\mexse\anaconda3\shell\condabin\conda-hook.ps1
conda activate fa_auth_m8

pip install -r reparto_service\requirements_dev.txt
Copy-Item reparto_service\.example_env reparto_service\.env
# Replace every changethis value in reparto_service\.env.

$env:PYTHONPATH = "."
pytest --cov --cov-fail-under=100
uvicorn reparto_service.main:app --reload --port 9000
```

The application validates its configuration at startup. Do not use global
Python or manually author Alembic revisions; the Compose workflow generates
and applies schema migrations from the models.

## Docker development stack

```bash
cd docker_compose/dev_reparto_m8
cp reparto.env.example reparto.env
cp auth.env.example auth.env
# Replace every changethis value before starting the stack.
bash init.sh
docker compose up -d
```

The stack includes PostgreSQL, Redis, the `fa-auth-m8` issuer, this service,
and Traefik. It is localhost-only by default; follow
[the stack guide](docker_compose/dev_reparto_m8/README.md) before exposing it
to a LAN.

## API map

| Area | Base path |
| --- | --- |
| Reference administration | `/reparto/academic-years`, `/schools`, `/classroom-stages`, `/departments`, `/teacher-profiles` |
| Assignment process | `/reparto/assignment-processes` |
| Per-process resources | `/reparto/assignment-processes/{process_id}/teachers`, `/subjects`, `/groups`, `/requirements`, `/assignments` |
| Teaching-load planning | `/reparto/assignment-processes/{process_id}/allocation-revisions`, `/teaching-plan`, `/teaching-plan/validations`, `/teaching-plan/materialize-main`, `/group-subjects`, `/teaching-activities` |
| Planning exchange | `/reparto/assignment-processes/{process_id}/exports/planning-draft`, `/exports/planning-provisional`, `/exports/planning-final`, `/imports/planning` |
| Lifecycle and read models | `/transition`, `/reopen`, `/copy-previous-year`, `/summary`, `/dashboard`, `/lan/me`, `/events` under an assignment process |
| Audit and history | `/audit-events`, `/versions`, `/compare-previous-year`, `/exports`, `/restore-draft` under an assignment process |
| Meeting turns | `/reparto/assignment-processes/{process_id}/meeting-sessions/{meeting_session_id}/turns` |

The `/requirements` `GET` endpoints are read-only: requirement rows are
generated, indivisible teacher-position slots derived from teaching activities
(one slot per required teacher position), never manually created or edited. They
are produced by the generation flow — `POST /requirements/generation-preview`
dry-runs the next generation (create/preserve/retire diff plus any assigned-slot
conflicts) and `POST /requirements/generate` applies it: one slot per teacher
position of every live activity, unchanged slots keep their id and assignment
while removed unassigned slots retire, the plan advances to
`REQUIREMENTS_GENERATED` at the next generation number, and a change that would
touch an already-assigned slot is refused (409) so it routes through
reconciliation rather than silently dropping an assignment. Both require a locked
(or stale, for regeneration) plan. `POST /requirements/reconciliation-preview`
dry-runs that resolution — it reports each conflicting assigned slot (whether its
hours changed or its position was removed, the assignment that would be released
and, for a value change, the replacement slot) — and `POST /requirements/reconcile`
applies it: on a stale or reconciliation-required plan it releases (soft-cancels,
audited) the affected assignments, retires the old slots, links a value-changed
slot to its fresh replacement via `superseded_by_requirement_id`, and returns the
plan to `REQUIREMENTS_GENERATED`. It requires a `reason` and an
`expected_conflict_count` confirmation, so an assignment is never silently
deleted. An assignment
binds one teacher to one complete, indivisible slot: create with just
`{hour_requirement_id, process_teacher_id}` (no hour or share input), and the
requirement's activity is denormalised server-side so the database enforces one
active assignment per slot and a distinct teacher per activity. `DELETE`
soft-cancels an assignment and frees its slot. Assignment endpoints also include
`POST /assignments/direct-choice` for teacher LAN selection. Selection-turn
endpoints support initialization plus start, complete, skip, and override
actions; completing a turn may carry the department head's manual slot choice,
which is recorded through the same complete-slot service (identical
availability, distinct-teacher and exact-target rules — no separate assignment
logic). Group-subject endpoints include `POST /group-subjects/bulk-preview` and
`POST /group-subjects/bulk-apply` for filtered create/update/upsert matrix
operations with a confirmed affected-row count. Teaching-activity endpoints
manage manual secondary planning items and their multi-group links.
`POST /teaching-plan/materialize-main` deterministically generates the
main-subject activities: one single-group `MAIN_GENERATED` activity per active
main group-subject cell (hours inherited from the cell, then the subject
default). It is idempotent — cells already materialised are skipped, never
duplicated — and requires an unlocked plan.
`GET /teaching-plan/validations` returns the plan's blocking and warning
findings (missing allocation, group/teacher-load imbalance, unmaterialised main
subjects, invalid activity/group links, ungenerated or stale requirements); it is
read-only and never triggers a feasibility solve. `GET /assignments/validations`
is its assignment-stage twin: it reports the blocking findings that stop final
closure (unassigned indivisible slots, participants assigned above their exact
target, and active participants still below target) plus the authorized-overload
warning, and is likewise read-only and solver-free. Assignment creation enforces
the exact target directly — an indivisible slot cannot be assigned if it would
push a participant above `target_weekly_hours`; there is no override, so an
overload must first be authorized by raising extra hours. Process
teachers carry `base_weekly_hours` and department-head authorized
`extra_weekly_hours`; their sum is the exposed `target_weekly_hours`, and a
non-zero extra flags `is_overloaded`. Extra hours change only through the audited
`POST /teachers/{process_teacher_id}/extra-hours` action (reason required, blocked
below already-assigned hours), never through the generic teacher `PATCH`.
Meeting and assignment operations are gated on plan readiness: opening a meeting
requires a balanced, locked and generated plan (`REQUIREMENTS_GENERATED`) — an
inexact, unlocked, un-generated or missing plan is refused with `409` — and new
assignment operations (manual, direct selection and meeting-turn choices) are
refused while an allocation change leaves the plan `STALE` or
`RECONCILIATION_REQUIRED`, so an assignment is never taken against a plan pending
reconciliation. Planning artifacts can be exported and imported while the plan is
still invalid: `POST /exports/planning-draft` and `POST /exports/planning-provisional`
render a self-describing artifact — both balance states plus the full blocking and
warning validation report and the live activities — and are **never blocked** by an
inexact, unbalanced or stale plan, whereas `POST /exports/planning-final` retains
the strict gate and is refused (`400`) while any blocking validation remains.
`POST /imports/planning` ingests activities as `IMPORTED` teaching activities:
every referenced subject and group-subject cell is validated against the target
process, every hour must be a canonical decimal string (a binary float or a
value with more than two decimal places is rejected), and an import never creates
or activates an assignment.
`POST /copy-from/{source_process_id}` seeds a fresh draft process from a previous
year: it always copies the configuration structure — subjects and their defaults,
teaching groups, group-subject cells and participants (base hours carried, but the
extra-hour approvals dropped) — and never activates the previous leadership
allocation, nor copies assignments, meetings, turns or extra-hour approvals. Pass
`copy_activities: true` to additionally copy the source plan's live
secondary-activity templates into a fresh draft teaching plan (main-generated and
retired activities are excluded). `POST /versions` captures an immutable three-stage
snapshot of the whole process — the allocation revisions and current allocation,
the teaching-plan status and generation, both independent balances, the
per-participant assignment summary (base/extra/target hours), the group-subject
matrix, the live activities with their linked group cells and the generated
requirement slots — and `GET /versions` lists them. `GET /versions/{left}/compare/{right}`
and `GET /compare-previous-year` diff two snapshots along the plan §10.3
dimensions: whether the allocation, group hours, teacher load, subject category,
activities, group links, teacher-position count, participant targets or
requirement generation changed, plus signed hour and count deltas (hours as
canonical decimal strings). `POST /exports` generates an export artifact (JSON or
CSV); a `backup` artifact carries the complete restorable three-stage domain —
process settings, allocation revisions, teaching plan, subjects, groups,
group-subject matrix, teaching activities and their links, the generated
indivisible requirement slots and the assignments — plus the version list, while a
`final` artifact is refused (`400`) while any blocking validation remains and
archives the process on success. `POST /restore-draft` rebuilds a backup into an
empty draft process: it remaps every id, always restores the configuration,
allocation history, plan and activities, and restores the generated requirement
slots and their assignments only when `restore_assignments` is set (the restore
mode). A restore never re-enables live LAN/direct access, never carries auth-user
attribution and always recomputes feasibility — a stored feasibility result is
never trusted — and it validates the backup's generation and reconciliation
consistency (generations within the plan, supersession links, and one active
assignment per slot / one teacher per activity) before writing anything.
`GET /audit-events` returns the process's mutation trail oldest first. Every
three-stage mutation is recorded with a canonical event type drawn from a single
registry (`AuditEventType`) — allocation revisions (`allocation.revised`), the
group-subject matrix including one row-detailed event per bulk apply
(`group_subject.bulk_applied`), teaching-plan creation and staleness, activity
creation/materialisation/import, requirement generation and reconciliation
(`requirements.generated`, `requirements.reconciled`), audited extra-hour changes
(`process_teacher.extra_hours_updated`) and every assignment and selection-turn
action — each carrying the actor, role, before/after payloads and an optional
reason. The trail can be narrowed with the optional `event_type` (validated
against the registry; an unknown value is rejected with `422`) and `entity_type`
query parameters.
`GET /events` is a Server-Sent Events stream of that same process's changes, for
LAN clients and the shared meeting screen. It opens with a `stream.opened` frame
carrying the current plan readiness (so a client needs no separate fetch) and
then relays `allocation.revised`, `teaching_plan.updated`, `teaching_plan.stale`,
`requirements.generated`, `requirements.reconciled` and
`participant.extra_hours_updated`, plus a keep-alive comment while idle. Every
payload is projected to the viewer's role: a department head or administrator
receives the full payload; a teacher receives the plan readiness, whether
selection is blocked, and hour figures **only for their own participation** —
never another teacher's target; the shared screen receives readiness alone
(`ready` / `not_ready` / `recalculation_required`) with no identifiers. A caller
may request a *less* privileged tier with `?audience=teacher|shared_screen` (a
projection screen should), but requesting a more privileged tier than the role
grants is refused with `403`. The stream is best-effort — the database remains
authoritative — so a subscriber that falls behind receives a `stream.gap` frame
telling it to refetch rather than silently missing a change.
Consult the OpenAPI schema for request and response models.

## Quality gates

Run these commands from the repository root in the `fa_auth_m8` conda
environment:

```bash
ruff format .
ruff check .
mypy . --ignore-missing-imports
pytest --cov --cov-fail-under=100
bandit -r . --severity-level medium
```

## Architecture

The service communicates with `fa-auth-m8` over HTTP and keeps domain logic in
controllers and services, separate from FastAPI route transport. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the detailed design.

## License

Licensed under the [Apache License 2.0](LICENSE).
