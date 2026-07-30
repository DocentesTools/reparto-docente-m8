# Architecture notes — three-stage teaching allocation

This document records the technical decisions behind the current backend. It
describes the **three-stage** workflow the service implements today:

```text
1. Configuration
2. Department teaching-load planning
3. Assignment to teachers
```

Stage 2 is the intermediate planning stage: school leadership communicates a
weekly group-hour allocation, the department head configures the group-subject
matrix, materializes the mandatory main-subject activities, adds optional
secondary activities (tutoring, co-teaching, department-level work), balances two
independent hour totals, and then generates the indivisible teacher-position
slots that stage 3 assigns.

Section references (`§`) point at the workspace adaptation plan; they are kept so
each decision below can be traced back to the rule that motivated it.

## 1. Canonical backend path

All domain code lives in `reparto-docente-m8/reparto_service`. There are no
parallel `backend/` or `frontend/` paths in this repository.

The frontend (Astro plugin) is a separate repository,
[`astro-reparto-m8`](https://github.com/DocentesTools/astro-reparto-m8), and
consumes this service's HTTP API. Business invariants are enforced here, never
only in the UI.

## 2. Consumer-service shape

`reparto_service` is a **consumer** of the `fa-auth-m8` issuer:

* it validates JWTs through `auth_sdk_m8`/`fastapi_m8` and never holds a signing
  key (`AUTH_SERVICE_ROLE=consumer`),
* it discovers the auth service at the URL configured by `INTROSPECTION_URL`,
* it shares the `ACCESS_SECRET_KEY` (HS256) with the issuer in `auth.env`.

`core/events.py` is the SSE bridge that consumes `session.revoked` and
`user.deleted` events from the issuer to evict the local validation cache. It is
wired into the app lifespan.

The service owns no auth-service tables and depends on no other service directly
— only on the authentication contract.

## 3. Database and migrations

PostgreSQL is the canonical target. Models use a `CHAR(36)` UUID column
(`reparto_service.core.db_models.UUIDString`) so the same schema is round-trip
clean on Postgres, MySQL/MariaDB and the in-memory SQLite engine the test suite
uses.

`TABLES_PREFIX=reparto` is the default, so every table is named
`reparto_<entity>`. This isolates the service inside a shared database.

Partial unique indexes are declared with both `sqlite_where` and
`postgresql_where` so the invariants below are enforced by the test engine and by
production alike.

### 3.1 Migration policy

Alembic is wired with `script_location = ./reparto_service/alembic` and
`version_locations = ./shared_migrations/reparto_docentes/versions` so the dev
stack can mount the migrations directory as a volume shared with the auth
service. Migration files are **generated from the declared models by the Compose
bootstrap**, never hand-authored in this repository (workspace policy).

`reparto_service/scripts/docker_start.sh` is that bootstrap. On every container
start it:

1. runs `alembic check` — no drift, no revision, so ordinary restarts do not
   produce empty revisions;
2. on drift, runs `alembic revision --autogenerate -m "Automatic reparto
   migration"` against `SQLModel.metadata`;
3. hands over to `pre_start.sh`, which waits for the database and runs
   `alembic upgrade head`.

`reparto_service/alembic/env.py` supplies the two settings that make the output
usable: `compare_type=True`, and a `render_item` hook that emits the `import` for
project-defined column types (`UUIDString`) so the generated revision is
self-contained.

Because nobody writes the revision, **the declared metadata is the only place a
schema defect can be caught**. Two consequences:

* every enum column must be declared with
  `reparto_service.core.db_models.enum_column_type`, which yields
  `sa.Enum(..., native_enum=False, create_constraint=True)` — a `VARCHAR` sized
  to the longest member name plus a `CHECK` constraint. A native PostgreSQL
  `ENUM` would need an `ALTER TYPE` migration for every new member, and being a
  schema-level object it survives `DROP TABLE` and breaks the reset in §3.2. The
  persisted token is the member *name* (`'ACTIVE'`, not `'active'`), which is
  what the partial-index predicates are written against.
* the gate is asserted by tests rather than by review:
  `tests/test_schema_migration_gate.py` runs on every suite invocation, and
  `tests/live/test_schema_postgres.py` repeats it against a real PostgreSQL
  server (see §3.3).

### 3.2 Development database reset

The three-stage adaptation is a deliberate **destructive** schema change and no
backward data migration exists:

* current development records are test data only;
* development databases are reset through the existing Compose initialization
  flow rather than migrated forward;
* obsolete assignment semantics (`assigned_hours`, shared assignments, partial
  requirement coverage, over-assignment overrides) were removed outright, with no
  compatibility layer.

The reset is a full teardown of the two pieces of generated state — the database
volume and the generated revisions — from `docker_compose/dev_reparto_m8`:

```bash
docker compose down
sudo rm -rf ./db_data                      # bind-mounted PostgreSQL data
rm -f ./shared_migrations/reparto_docentes/versions/*.py
docker compose up -d
```

The bootstrap then autogenerates one revision describing the whole current
schema and applies it to the empty database. Leaving `db_data` in place while
clearing the revisions is the one combination to avoid: the tables would already
exist with no revision recording them.

Deleting `db_data` also resets the `fa-auth-m8` issuer, which shares the same
PostgreSQL instance — expect to recreate the local superuser afterwards.

### 3.3 Verifying the schema without generating a revision

`tests/test_schema_migration_gate.py` checks, on the declared metadata and the
SQLite test engine, that no enum column is native, that each keeps its `CHECK`,
that no dialect emits `CREATE TYPE`, that partial unique indexes carry both
dialect predicates, and that a database created from the metadata leaves an
empty `compare_metadata` diff — which is exactly what makes step 1 of the
bootstrap pass on a second start.

`tests/live/test_schema_postgres.py` repeats those checks against a real
PostgreSQL server and additionally drops every table and recreates the schema,
proving the §3.2 reset is repeatable. It is excluded from the default run; its
module docstring carries the disposable-database command line.

## 4. Domain model

Reference data and process scaffolding:

* `School`, `AcademicYear`, `Department`, `ClassroomStage` — platform reference
  data.
* `TeacherProfile` — cross-process teacher record, optionally linked to an auth
  user.
* `AssignmentProcess` — the annual per-department process; owns the lifecycle
  state machine (§6).
* `ProcessTeacher` — a teacher's participation in one process.
* `Subject`, `TeachingGroup` — per-process configuration.

Stage 2 (planning):

* `DepartmentHourAllocationRevision` — every leadership group-hour allocation,
  immutable and append-only. Exactly one revision per process is non-superseded
  (`superseded_at IS NULL`); a new revision supersedes the previous one inside one
  transaction, requires a reason, and records its `source` /`source_reference` /
  `received_at` provenance.
* `TeachingPlan` — one plan per process. Carries the operational `status`
  (`DRAFT`, `UNBALANCED`, `BALANCED`, `LOCKED`, `REQUIREMENTS_GENERATED`, `STALE`,
  `RECONCILIATION_REQUIRED`), the `current_generation_number` processing counter,
  lock metadata, and the **orthogonal** `feasibility_*` axis (§8.3).
* `GroupSubject` — the group × subject matrix cell, unique per
  `(process, group, subject)`. Its two hour fields are *optional overrides*: NULL
  inherits the subject default at materialization.
* `TeachingActivity` — one concrete planned item, holding the **actual** planning
  values (`group_weekly_hours_per_group`, `teacher_weekly_hours_per_position`,
  `required_teacher_count`). `source` is `MAIN_GENERATED`, `SECONDARY_MANUAL`,
  `COPIED_FROM_PREVIOUS_YEAR` or `IMPORTED`; `sync_state` and `retired_at` carry
  the generic lifecycle rather than a bespoke status enum.
* `TeachingActivityGroup` — links an activity to a `GroupSubject` cell, unique per
  pair. Group hours count once per link.

Stage 3 (assignment):

* `HourRequirement` — one **generated, indivisible teacher position**.
* `Assignment` — one teacher occupying one complete slot.
* `MeetingSession`, `SelectionTurn` — LAN meeting and ordered selection.
* `ProcessVersion`, `ExportArtifact`, `AuditEvent` — history and trail.

Enums are centralised in `reparto_service/enums.py`. Categories that the domain
reasons about are enums, never booleans (§4.1).

### 4.1 Category enums, not flags

`Subject.allocation_category` is `MAIN`/`SECONDARY` — an extensible enum, not an
`is_main` boolean. `activity_type` (`ORDINARY`, `TUTORING`, `CO_TEACHING`,
`SUPPORT`, `DEPARTMENT_LEVEL`, `OTHER`) is **descriptive only**: no behavior
branches on it. Behavior derives from the hour values, the required teacher count,
the linked group count and the subject's group-link policy flags
(`allows_multiple_groups`, `allows_zero_groups`).

### 4.2 Retirement vocabulary

The differing markers are intentional:

* allocation revisions are **superseded** (`superseded_at`);
* requirement slots are **retired** by generation (`retired_generation`) or
  **superseded** by row (`superseded_by_requirement_id`);
* activities are **retired** at a timestamp (`retired_at`).

Live-row filters follow the same split, and every total excludes retired rows.

## 5. The two independent hour balances

The single global balance of the two-stage design is gone. `TeachingPlan` carries
**two** totals that are related but not required to be equal.

Group teaching-hour balance — what the groups receive:

```text
total_group_hours = SUM(activity.group_weekly_hours_per_group × linked_group_count)
target            = current allocation revision's allocated_group_weekly_hours
```

Teacher workload balance — what the teachers carry:

```text
total_teacher_load = SUM(activity.teacher_weekly_hours_per_position
                         × activity.required_teacher_count)
target             = SUM(process_teacher.base_weekly_hours + extra_weekly_hours)
```

A co-teaching plan is correct on both axes at different numbers:

```text
group hours:   116 main + 2 co-teaching activities × 2 h        = 120
teacher load:  116 main + 2 activities × 2 h × 2 teachers       = 124
```

The two totals are therefore reported on separate axes and **never summed**.
`GET /teaching-plan/summary` returns both; the dashboard and the shared screen
show them side by side.

### 5.1 Participant targets and authorized overload

`ProcessTeacher.available_hours` was replaced by `base_weekly_hours` plus
department-head authorized `extra_weekly_hours`. Their sum is the exposed
`target_weekly_hours`, and `extra_weekly_hours > 0` flags `is_overloaded` —
*authorized overload*, not "assigned above target".

The target is **exact**: a participant may finish neither below nor above it, and
no assignment override may bypass that. Raising capacity means raising extra
hours first, which happens only through the audited
`POST /teachers/{id}/extra-hours` action (reason mandatory, blocked when the new
target would fall below already-assigned hours). Extra hours are deliberately
absent from the generic teacher `PATCH` so the audit trail cannot be bypassed.

## 6. Lifecycles

Three transition tables, all declarative, none duplicated in a controller:

* `services/process_lifecycle.py` — `AssignmentProcessStatus`, including the
  closing edge (records `closed_at`/`closed_by_user_id`) and the explicit
  `final → reopened` reopen edge with a mandatory reason.
* `services/planning_lifecycle.py` — a generic `TransitionTable` plus
  `TEACHING_PLAN_LIFECYCLE`, `HOUR_REQUIREMENT_LIFECYCLE` and
  `FEASIBILITY_LIFECYCLE`. Every edge is annotated with the rule it encodes.
* `services/lifecycle_gates.py` — `PlanReadinessGate`, the status sets that gate
  stage-entry and assignment operations (§9.2).

`PATCH /assignment-processes/{id}` does not accept `status`; it returns 400 with a
pointer to the transition endpoint.

The feasibility table is worth noting: results never mutate into one another.
`FEASIBLE`/`INFEASIBLE`/`UNKNOWN` can only pass back through `NOT_EVALUATED`, so a
stale result or witness can never be silently reused.

## 7. Assignment invariants

An `Assignment` means "this teacher takes this whole slot". The invariants are
enforced by the database, not only by the application.

* **Indivisibility.** A slot's `required_teacher_hours` is covered in full or not
  at all. There is no `assigned_hours` to edit, no partial coverage state, no
  shared assignment and no over-assignment override. A teacher with three
  remaining hours cannot take a four-hour slot.
* **One live assignment per slot.** Partial unique index on
  `(hour_requirement_id) WHERE status = 'ACTIVE'`.
* **Distinct teachers per activity.** The rule "a teacher may never occupy two
  positions of the same activity" is absolute, so it is not a configurable flag.
  `teaching_activity_id` is denormalized onto `Assignment` and the denormalization
  is itself guaranteed: `HourRequirement` carries
  `UNIQUE (id, teaching_activity_id)` and `Assignment` a composite foreign key
  `(hour_requirement_id, teaching_activity_id) → HourRequirement(id,
  teaching_activity_id)`. On top of that sits the partial unique
  `(teaching_activity_id, process_teacher_id) WHERE status = 'ACTIVE'`.
* **Server-derived activity.** `AssignmentCreate` accepts only
  `{hour_requirement_id, process_teacher_id, notes?}`; the activity is read from
  the requirement server-side and never trusted from the client.
* **One code path.** Department-head manual creation, teacher LAN direct choice
  and the department head's manual choice while completing a meeting turn all
  route through the same slot-occupancy routine, so availability, distinct-teacher
  and exact-target rules cannot diverge between them.
* **Soft cancellation.** `DELETE` cancels the assignment and frees the slot; rows
  are not hard-deleted.
* **Locked recheck.** Direct selection locks the requirement row and rechecks
  availability, the participant's remaining exact target and the distinct-teacher
  rule *inside* the transaction. The database constraints are the final barrier
  against two concurrent sibling-slot selections picking the same teacher.

### 7.1 Requirement identity and generation

The plan-wide `current_generation_number` is a *processing* revision, not slot
identity. A slot's logical identity is `(teaching_activity_id, position_index)`
and its row identity is a stable `id` carried across generations:

* active uniqueness is `(teaching_activity_id, position_index) WHERE
  retired_generation IS NULL` — a retired row frees the slot;
* an unchanged slot keeps its `id` and its assignment, and only advances
  `last_validated_generation`;
* a removed **unassigned** slot is retired;
* a changed **assigned** slot never gets silently overwritten or deleted: it
  enters reconciliation and may be linked from its replacement through
  `superseded_by_requirement_id`.

Requirements are generated, never manually created: the requirement routes expose
`GET` reads plus the generation and reconciliation actions only.

Generation is a two-call contract — `generation-preview` dry-runs the
create/preserve/retire diff, `generate` applies it — and refuses (409) any change
that would touch an assigned slot, routing it to
`reconciliation-preview`/`reconcile` instead. Reconciliation requires a reason and
an `expected_conflict_count` confirmation, so an assignment is never dropped
without an explicit, audited decision.

### 7.2 Guarded retirement

Unsafe `DELETE` is replaced by guarded retirement: a `GroupSubject` with a
downstream activity, or an activity with generated requirements or assignments,
cannot simply be deleted — it must be synchronized, regenerated or reconciled.
Versioned and final entities are never hard-deleted.

## 8. Calculations, validations and feasibility

### 8.1 Calculation services

`services/calculations.py` is the single home for every hour formula; controllers
and routes never do arithmetic themselves.

* `PlanningCalculationService` — per-activity group and teacher loads, the two
  plan-wide totals, their targets, and the exact/balanced result.
* `AssignmentCalculationService` — per-participant assigned and remaining hours,
  the derived participant state (`PENDING`, `BALANCED`, `OVERLOADED_AUTHORIZED`,
  `INACTIVE`, `NOT_PARTICIPATING`), per-slot coverage state and the process
  assignment summary.

### 8.2 Decimal-hour handling

`core/decimals.py` is the single source of truth for hour precision: two-place
quantization, `ROUND_HALF_UP` for computed values, a strict `normalize_hours`
input validator (rejecting binary floats, negatives and more than two decimal
places), the canonical decimal-string API representation (`"2.50"`), and a
`HoursNumeric` `NUMERIC(8, 2)` column type.

The stored hour **columns are still `float`** today; the column-level migration
onto `HoursNumeric` is a pending task. Until it lands, every value is lifted into
a two-place `Decimal` **via its string form** before any arithmetic or comparison,
so no binary-float representation reaches a domain decision. A balance is "exact"
only when the quantized difference is exactly `Decimal("0.00")`.

### 8.3 Validation services

`services/validations.py` turns the numbers and a few cheap structural checks into
`blocking` / `warning` / `info` messages carrying stable codes
(`plan.group_hours_imbalanced`, `requirement.unassigned`, …).

* `PlanValidationService` — missing allocation, either balance inexact,
  unmaterialized main subjects, activity/group-link problems, ungenerated or stale
  requirements, plan staleness, feasibility not confirmed, plus warnings.
* `AssignmentValidationService` — unassigned indivisible slots, participants above
  or below their exact target at close, and the authorized-overload warning.

Both are **read-only and solver-free**: `GET /teaching-plan/validations` and
`GET /assignments/validations` never trigger a feasibility evaluation.

### 8.4 The feasibility axis

Two equal aggregate totals do not prove that indivisible slots can be distributed
so that every participant hits an exact target — that is an NP-hard exact
partition with per-activity distinct-teacher (matching) constraints. Assignment
readiness therefore needs a third invariant alongside the two balances:

```text
group_balance_exact AND teacher_load_balance_exact AND feasibility_status == FEASIBLE
```

`feasibility_status` is stored as its **own field**, never folded into
`TeachingPlan.status`, together with the generation it was computed against, the
input fingerprint, the solver version and a diagnostics reference. Teacher
eligibility in this design is `HOURS_ONLY_FUNGIBILITY`: any active participant may
take any slot, and there are no subject/group/stage eligibility models.

The bounded deterministic solver, the persisted witness, the cheap in-transaction
guards and the lock/meeting-open gates are **not implemented yet** (§11). The
schema fields, the enums and the orthogonal lifecycle table are already in place
so adding them requires no schema change, and the plan-level validation already
reports `plan.feasibility_not_confirmed`.

## 9. API design

### 9.1 Routing

* `api_router` is mounted under the configured `API_PREFIX` (default `/reparto`)
  in `reparto_service/main.py`.
* `app/main.py` aggregates the per-resource routers; `app/routes/` holds transport
  only and delegates to `controllers/`.
* All routes use typed Pydantic v2 request/response schemas.
* Process-owned resources are nested under
  `/assignment-processes/{process_id}/…`, so every child write can validate that
  the referenced rows belong to the same process.

Endpoint groups, by stage:

| Area | Paths under `/reparto` |
| --- | --- |
| Reference data | `/academic-years`, `/schools`, `/classroom-stages`, `/departments`, `/teacher-profiles` |
| Process | `/assignment-processes` + `/transition`, `/reopen`, `/copy-from/{source_id}` |
| Configuration | `…/teachers` (+ `/extra-hours`), `/subjects`, `/groups`, `/group-subjects` (+ `/bulk-preview`, `/bulk-apply`) |
| Planning | `…/allocation-revisions` (+ `/current`), `/teaching-plan` (+ `/summary`, `/validations`, `/materialize-main`), `/teaching-activities` |
| Requirements | `…/requirements` (read) + `/generation-preview`, `/generate`, `/reconciliation-preview`, `/reconcile` |
| Assignment | `…/assignments` (+ `/direct-choice`, `/validations`), `/meeting-sessions` (+ `/close`), `…/turns` (+ `/initialize`, `/start`, `/complete`, `/skip`, `/override`) |
| Read models | `…/summary`, `/dashboard`, `/lan/me`, `/events` |
| History | `…/versions` (+ `/{left}/compare/{right}`), `/compare-previous-year`, `/audit-events`, `/exports`, `/restore-draft` |
| Planning exchange | `…/exports/planning-draft`, `/exports/planning-provisional`, `/exports/planning-final`, `/imports/planning` |

### 9.2 Gated operations

Two guards, both derived from the same status sets the read models report, so a
viewer can never be shown a readiness that disagrees with what the write path
allows:

* **Stage entry** (opening a meeting) requires a balanced, locked and generated
  plan (`REQUIREMENTS_GENERATED`); an inexact, unlocked, un-generated or missing
  plan is refused with `409`.
* **New assignment operations** (manual, direct selection, meeting-turn choices)
  are refused while an allocation change leaves the plan `STALE` or
  `RECONCILIATION_REQUIRED`, so no assignment is taken against a plan pending
  reconciliation.

Draft and provisional planning exports are deliberately **never** blocked by an
inexact, unbalanced or stale plan; they carry both balance states and the full
validation report and describe themselves as provisional. Only
`exports/planning-final` and final closure keep the strict gate.

### 9.3 Bulk and preview/apply operations

Matrix-scale edits follow one pattern: a pure planner function is dry-run by a
`*-preview` endpoint and re-run by the corresponding `*-apply`/action endpoint, so
the two can never diverge. The apply call carries the count the operator
confirmed and returns `409` when the recomputed count no longer matches
(staleness guard), executes in one transaction, and records one audit event with
row-level detail. This covers group-subject bulk create/update/upsert, requirement
generation and reconciliation.

### 9.4 Error model

Domain errors raise `HTTPException` with the appropriate status: `400` for
validation and mutability refusals, `403` for permission, `404` for cross-process
or missing rows, `409` for lifecycle/readiness conflicts and staleness guards,
`422` for schema-level rejections. The structured error format
(`auth_sdk_m8.schemas.base.ResponseErrorBase`) is inherited from
`auth_sdk_m8.controllers.base.BaseController` as the fallback for unexpected
exceptions.

### 9.5 Permissions

`DomainController` centralises the current checks:

* `require_writer` — mutations require a writer-class role (`writer`, `admin`,
  `superadmin`) or superuser;
* `require_admin` — platform reference data;
* `require_process_writer` — writer-class role, or the user bound as the
  department's `department_head_user_id`;
* `ensure_process_mutable` — every child-resource write is refused when the parent
  process is `final` or `archived`, from one place.

`auth_sdk_m8` enforces a canonical role/flag truth table
(`USER < READER < WRITER < ADMIN < SUPERADMIN`, with `is_superuser` valid only
alongside `SUPERADMIN`), and this service adds no roles of its own.

Two hardening items are known-open and tracked as their own task: read routes do
not yet carry a minimum-role (`>= READER`) floor, and department-head
authorization is still satisfied by the role-independent
`department_head_user_id` binding rather than by `ADMIN`/`SUPERADMIN` alone (§11).

### 9.6 Role-projected read models and SSE

The same process state is exposed at three confidentiality tiers, projected
server-side rather than filtered in the client:

* `GET /dashboard` and `GET /summary` — full planning and assignment sections for
  a department head or administrator.
* `GET /lan/me` — the teacher's own view: aggregate, identifier-free plan
  balances, **only the caller's own** participation row, the available slot count,
  the current turn, and whether selection is blocked. Another teacher's figures
  are never present in the payload.
* `GET /events` — a Server-Sent Events stream of the same process's changes for
  LAN clients and the shared meeting screen. It opens with a `stream.opened` frame
  carrying current readiness, then relays allocation, plan, generation,
  reconciliation and participant-hour events, with a keep-alive comment while
  idle.

Every SSE payload is projected to the viewer's role: a head or administrator
receives the full payload, a teacher receives readiness plus hours **only for
their own participation**, and the shared screen receives readiness alone
(`ready`/`not_ready`/`recalculation_required`) with no identifiers. A caller may
request a *less* privileged tier with `?audience=teacher|shared_screen` — a
projection screen should — but asking for a more privileged tier than the role
grants is refused with `403`.

The stream is best-effort; the database stays authoritative. A subscriber that
falls behind receives a `stream.gap` frame telling it to refetch rather than
silently missing a change.

## 10. History, exchange and audit

* **Versions.** `POST /versions` captures an immutable three-stage snapshot:
  allocation revisions and the current allocation, plan status and generation,
  both balances, per-participant target/assigned figures, the group-subject
  matrix, live activities with their group links, and the generated slots.
  `GET /versions/{left}/compare/{right}` and `/compare-previous-year` reduce two
  snapshots to the plan's comparison dimensions plus signed hour and count deltas
  (hours as canonical decimal strings). Feasibility **status** is recorded; a
  witness is never snapshotted as authoritative.
* **Exports.** `POST /exports` produces a JSON or CSV artifact. A `backup`
  artifact carries the complete restorable three-stage domain; a `final` artifact
  is refused (`400`) while any blocking validation remains and archives the
  process on success.
* **Restore.** `POST /restore-draft` rebuilds a backup into an empty draft
  process, remapping every id. It never re-enables live LAN/direct access, never
  carries auth-user attribution, always recomputes feasibility instead of trusting
  a stored result, and validates the backup's generation and reconciliation
  consistency (generations within the plan, supersession links, one active
  assignment per slot and one teacher per activity) before writing anything.
* **Copy from previous year.** `POST /copy-from/{source_process_id}` seeds a fresh
  draft: configuration structure, groups, group-subject cells and participants
  (base hours carried, extra-hour approvals dropped). It never activates the
  previous leadership allocation and never copies assignments, meetings or turns.
  `copy_activities: true` additionally copies the source plan's live secondary
  activity templates into a fresh draft plan.
* **Planning exchange.** `POST /imports/planning` ingests activities as `IMPORTED`
  teaching activities, validating every referenced subject and cell against the
  target process and requiring canonical decimal-string hours. An import never
  creates or activates an assignment.
* **Audit.** Every three-stage mutation is recorded with a canonical event type
  drawn from one registry (`AuditEventType`) carrying actor, role, before/after
  payloads and an optional reason. `GET /audit-events` returns the trail oldest
  first, narrowable by `event_type` (validated against the registry; an unknown
  value is rejected with `422`) and `entity_type`.

## 11. Intentionally not implemented yet

These are tracked adaptation tasks, not oversights. Each has its schema or
extension point already in place.

* **Feasibility solver and witness** — the bounded deterministic solver, the
  persisted witness with fingerprint/solver-version invalidation, the cheap
  in-transaction selection guards and per-process solve serialization. The
  `feasibility_*` fields, enums and lifecycle table exist; nothing evaluates them
  yet.
* **Plan lock/unlock endpoints** — `BALANCED → LOCKED` is a legal edge in the
  lifecycle table but no route drives it, so `LOCKED` is currently reached only
  via restore. Locking is the operation that will require all three invariants.
* **Decimal hour columns** — `HoursNumeric` exists but no model uses it yet; hour
  columns remain `float` and are lifted to `Decimal` in the services (§8.2).
* **Undo and reassignment** — head/admin-only undo with reason, audit and
  deterministic turn-queue recompute.
* **`GroupSubject → activity` sync flow** — editing a materialized source cell
  should mark the generated activity `OUT_OF_SYNC` and route through an explicit
  sync preview/apply.
* **Authorization hardening** — a minimum-role (`>= READER`) floor on every
  read/list/export route, `WRITER` restricted to its own records,
  department-head authorization narrowed to `ADMIN`/`SUPERADMIN` with
  `department_head_user_id` demoted to attribution metadata, per-tenant read
  scoping, and migration onto the SDK-provided role dependencies (§9.5).
* **Destructive migration generation** — the new schema's migration is produced by
  the Compose bootstrap from these models and verified against a clean database
  (§3.1, §3.2).

## 12. Tests

Tests live in `tests/` (per `pytest.ini`). The conftest:

* sets the required env vars **before** the first `reparto_service` import
  (Pydantic settings are constructed at import time),
* monkey-patches `auth_sdk_m8.utils.paths.find_dotenv` so the local
  `.example_env` is not loaded,
* uses a fresh in-memory SQLite engine per test, and provides role fixtures whose
  privilege claims satisfy the SDK truth table.

The suite is layered:

1. **Unit** — `test_core_db_models.py`, `test_core_decimals.py`,
   `test_process_lifecycle.py`, `test_planning_lifecycle.py`,
   `test_controllers_base.py`.
2. **Services** — `test_services_calculations.py`, `test_services_validations.py`,
   `test_services_assignment_validations.py`, `test_services_lifecycle_gates.py`,
   `test_services_snapshots.py`, `test_services_sse.py`.
3. **Route integration** — `test_routes_*.py`, one module per resource, covering
   happy paths, permission refusals, cross-process 404s, lifecycle conflicts and
   the DB-level uniqueness invariants.
4. **Smoke** — `test_main.py` for the wired app (openapi, health, meta, routes).

Coverage is **100 %, enforced** (`pytest --cov --cov-fail-under=100`);
`# pragma: no cover` is reserved for genuinely unreachable guards. Domain
factories live in `tests/factories.py` so a test never hand-builds a row graph.

Run the gates from the repository root:

```bash
ruff format .
ruff check .
mypy . --ignore-missing-imports
pytest --cov --cov-fail-under=100
bandit -r . --severity-level medium
```
