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

`reparto_service/alembic/env.py` supplies the settings that make the output
usable: `compare_type=True`, a `render_item` hook that emits the `import` for
project-defined column types (`UUIDString`) so the generated revision is
self-contained, and the `include_object` filter owned by
`reparto_service/core/autogenerate.py`.

That filter withholds two things from the comparison. Reflected tables other
than the version table are not ours to diff — the dev stacks put several
services' schemas in one PostgreSQL instance. And reflected **type-bound enum
`CHECK` constraints** are withheld because Alembic cannot match them: since
1.19 it compares check constraints by name, reading the metadata side through
`all_table_check_constraints`, which excludes exactly the type-bound ones, while
reflecting every named constraint from the database. Each `Enum(...,
create_constraint=True)` constraint therefore compares as *removed*. Unfiltered,
the **second** `docker compose up` generates a revision dropping all 22 of them
and applies it: the schema still reads correctly and the service still starts
healthy, so nothing else reports that the database-level validation is gone.

The exclusion is derived from the current metadata, not a kept list, so
dropping an enum column from the models still drops its stale constraint. Only
PostgreSQL exposes the problem — SQLite reflects the same constraints without
names — which is why `tests/live/test_schema_postgres.py` carries the
discriminating case (§3.3).

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

A reset database is empty, and an empty domain is correct for a deployment and
useless for a walk-through — see §3.2a.

### 3.2a The worked configuration example

`reparto_service/initial_data.py` seeds one department into an empty domain,
behind `SEED_EXAMPLE_DATA` (default `false`). `pre_start.sh` invokes it on every
start; it does nothing unless the flag is on **and** the domain holds no
assignment process, so it can never extend or collide with data somebody else
put there, and a second start is a no-op.

It configures **stage 1 only** — school, department, academic year, process, 17
teaching groups over two classroom stages, 14 subjects, the group-subject matrix,
6 participants and the leadership hour allocation. It creates no teaching plan,
activity, requirement or assignment: stages 2 and 3 are what an operator walks,
so seeding them would consume the demonstration.

The matrix is chosen so that completing those stages lands on the §5 co-teaching
numbers exactly:

```text
116 h  main activities           (materialised, one per active main cell)
+  2 h  two tutoring activities  (1 h each, two positions each)
+  2 h  one co-teaching activity (2 h, two positions)
─────
120 h  group load    = the allocated 120 h        → balanced
124 h  teacher load  = the six participants' 124 h → balanced
```

Both balances are exact at two different numbers, which is the point of the
example. `tests/test_initial_data.py` completes the plan over the seed through
the real materialisation and activity-creation controllers and asserts those
totals against the calculation service, so the arithmetic is gated rather than
asserted in prose.

The cells carry no hour values of their own, only their position count, so they
resolve through the subject defaults — the documented behaviour, exercised.
Nobody carries authorized extra hours: raising a target is the audited
`POST /teachers/{id}/extra-hours` action, never a seeded value. Seeded teacher
profiles are left unlinked to any auth user, and every row's author is a fixed
synthetic UUID5, so the seed claims no real identity.

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
* `FeasibilityWitness` — restricted one-to-one internal cache for the complete
  provisional slot-to-participant mapping and administration-only diagnostics.
  It is never part of common plan, teacher, shared-screen, audit, snapshot or
  export schemas.
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

Unsafe `DELETE` is replaced by explicit `POST .../{id}/retire` actions.
`GroupSubject` retirement is draft-process-only, sets `active=false`, preserves
the source row, and refuses while any live activity is sourced from or linked to
the cell. A general `PATCH active=false` cannot bypass this guard. Teaching
activity retirement sets `retired_at` and preserves its links and history. An
unlocked activity without requirements retires directly; live unassigned slots
move the plan to `STALE` for regeneration, while live assigned slots move to
`RECONCILIATION_REQUIRED` and are marked accordingly. `HourRequirement` has no
manual delete or retire route and remains owned by generation/reconciliation.
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

`services/feasibility.py` owns the pure bounded deterministic solver. Its inputs
are immutable remaining targets and indivisible slots expressed only in integer
hundredths. Stable largest-slot-first DFS uses residual-target and per-activity
matching prunes, deterministic memoization, and configurable instance, step and
time limits. It returns `FEASIBLE` with a complete deterministic witness,
`INFEASIBLE` with an internal diagnostic, or fail-closed `UNKNOWN` when a bound
is reached.

`services/feasibility_controls.py` admits at most one full solve per assignment
process. PostgreSQL uses a non-blocking transaction advisory lock so the guarantee
spans API workers; standalone/test engines use the same fail-fast contract with
a process-local lock. A concurrent request receives `429` plus `Retry-After`
instead of joining an unbounded queue. The solver continues to enforce the
server-owned 30-participant, 100-slot, 1,000,000-step and 2-second budgets, and
no assignment/planning row lock is held during the search.

`services/selection_guards.py` owns the solver-free assignment hot path. It
builds the current remaining state and applies one proposal using only residual
total equality, exact slot fit, oversized-slot detection and per-activity Hall
matching. It also validates an existing deterministic witness and attempts a
strictly bounded, balance-improving local repair; it never calls the full solver.
`services/feasibility_witnesses.py` owns persistence and orchestration. It hashes
the solver version plus every active participant target, live slot identity/hour
and active slot-to-participant assignment with stable JSON ordering and SHA-256.
Lock, generation and reconciliation evaluate the exact intended next generation;
new slot ids are derived deterministically so the persisted witness is the same
witness the applied generation exposes. Meeting open, final export and final
process close only verify a current matching witness and never invoke the solver.
`POST /assignment-processes/{process_id}/teaching-plan/feasibility/evaluate`
runs or reuses the bounded evaluation for that exact fingerprint; the full
witness is available only through the administrator-gated
`GET .../feasibility/witness`, and the latest evaluation's stable findings
(code, message, affected slot/activity identifiers — never the witness itself)
through the equally administrator-gated `GET .../feasibility/diagnostics`
(plan §7.3, §20.24), which fails closed with `409` whenever no current
fingerprint- and generation-matching evaluation exists. Relevant participant,
planning, allocation,
generation, reconciliation and undo mutations immediately reset the plan to
`NOT_EVALUATED` and delete the cached row. A valid assignment hot path loads the
matching row, performs bounded local repair, and persists the repaired mapping
against the post-selection fingerprint in the same transaction. It never runs
the full solver. Reassignment first validates the current witness, builds the
hypothetical post-undo remaining state, and runs that same cheap-guard plus
bounded-repair path before atomically cancelling the old row and occupying the
same slot with the replacement. Pure undo never runs the solver.

Every full-solve attempt emits operational telemetry containing only status,
cache use, participant/slot counts, search/memo counts, budget outcome, configured
bounds and elapsed milliseconds. It deliberately excludes process/plan/user/
teacher/slot identifiers, names, fingerprints, witnesses and diagnostic related
IDs. The explicit evaluate/witness routes remain `ADMIN`/`SUPERADMIN`-only;
lifecycle solver triggers remain behind the process department-head mutation
gate and cannot be teacher-triggered.

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
| Configuration | `…/teachers` (+ `/extra-hours`), `/subjects`, `/groups`, `/group-subjects` (+ `/bulk-preview`, `/bulk-apply`, `/{id}/sync-preview`, `/{id}/sync-apply`) |
| Planning | `…/allocation-revisions` (+ `/current`), `/teaching-plan` (+ `/lock`, `/unlock`, `/summary`, `/validations`, `/materialize-main`), `/teaching-activities` |
| Requirements | `…/requirements` (read) + `/generation-preview`, `/generate`, `/reconciliation-preview`, `/reconcile` |
| Assignment | `…/assignments` (+ `/{id}/undo`, `/{id}/reassign`, `/direct-choice`, `/validations`), `/meeting-sessions` (+ `/close`), `…/turns` (+ `/initialize`, `/start`, `/complete`, `/skip`, `/override`) |
| Read models | `…/summary`, `/dashboard`, `/lan/me`, `/events` |
| History | `…/versions` (+ `/{left}/compare/{right}`), `/compare-previous-year`, `/audit-events`, `/exports`, `/restore-draft` |
| Planning exchange | `…/exports/planning-draft`, `/exports/planning-provisional`, `/exports/planning-final`, `/imports/planning` |

### 9.2 Gated operations

Two guards, both derived from the same status sets the read models report, so a
viewer can never be shown a readiness that disagrees with what the write path
allows:

* **Plan lock, requirement generation and reconciliation** run or reuse the
  bounded solve for the exact intended generation and fail closed on
  `INFEASIBLE` or `UNKNOWN`.
* **Stage entry** (opening a meeting) requires a balanced, locked and generated
  plan (`REQUIREMENTS_GENERATED`) plus a current `FEASIBLE` result and matching
  deterministic witness; an inexact, unlocked, un-generated, infeasible,
  unknown or missing plan is refused with `409`.
* **Final export and final process close** verify that same current feasibility
  provenance without running a solve.
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
row-level detail. This covers group-subject bulk create/update/upsert, explicit
main-activity source sync, requirement generation and reconciliation. A source
sync echoes a SHA-256 preview fingerprint because source values, activity values,
plan generation and assigned-slot impact must all stay unchanged.

Editing a materialized main `GroupSubject` never overwrites its activity. The
activity becomes `OUT_OF_SYNC`, plan feasibility is invalidated, and assignment
readiness is blocked. `sync-preview` returns resolved source values, current
activity values, their deterministic diff, and affected assigned requirement
IDs. `sync-apply` updates only after the fingerprint is confirmed; affected
assigned slots become `RECONCILIATION_REQUIRED`, while an inactive source is
sent to the separate guarded-retirement action. Main materialization locks its
source rows before checking existing activities on PostgreSQL, and the partial
unique `(teaching_plan_id, source_group_subject_id)` index remains the final
concurrency barrier.

### 9.4 Error model

Domain errors raise `HTTPException` with the appropriate status: `400` for
validation and mutability refusals, `403` for permission, `404` for cross-process
or missing rows, `409` for lifecycle/readiness conflicts and staleness guards,
`422` for schema-level rejections. The structured error format
(`auth_sdk_m8.schemas.base.ResponseErrorBase`) is inherited from
`auth_sdk_m8.controllers.base.BaseController` as the fallback for unexpected
exceptions.

### 9.5 Permissions

**The read floor comes first.** `reparto_service.app.main` mounts the SDK's
`get_current_active_reader` on the *domain router aggregator*, so every domain
route — list, get, export and mutation alike — rejects an unauthenticated caller
with `401` and a `USER`-role caller with `403` before its handler runs. It is
mounted once, on the aggregator, rather than repeated per router: a floor that
must be remembered twenty-one times is a floor with twenty-one ways to forget
it, and a router added later inherits it by construction. The framework's
`/health`, `/meta`, `/ping` and `/metrics` endpoints are mounted outside the
aggregator and keep their own visibility.

It is the SDK-built dependency rather than a local role comparison on purpose,
and so is every gate above it: only that path re-validates against the fresh,
no-positive-cache user, which is what makes a role-sensitive check observe a
revocation committed after the last cache fill — a demoted writer stops being a
writer on their next request rather than at the end of the cache TTL. A
hand-rolled comparison over `get_current_user` cannot offer that however
correct its arithmetic, which is why `RBAC-03` asked for the dependency and not
merely for equivalent behaviour. `tests/test_authorization_boundaries.py`
proves it from both sides: the gates are asserted to *be* the SDK objects, and
a client that satisfies only the cacheable `get_current_user` path is answered
`401` on every route in the schema.

**Every route names the role it needs in its own signature.** `CurrentReader`,
`CurrentWriter` and `CurrentAdmin` are the SDK's `get_current_active_reader`/
`_writer`/`_admin` dependencies, so the requirement is visible where the handler
is read and enforced before the handler runs — including before body
validation, so an unauthorized caller never learns what the payload wants.
There is deliberately no bare `CurrentUser` export left: a new route cannot
settle for "authenticated" by omission.

Mapping: `CurrentAdmin` for every process/planning mutation (the department
head, §21.2) and all platform reference data; `CurrentWriter` for the three
own-data actions; `CurrentReader` everywhere else.

`DomainController` keeps only the checks a dependency cannot express, because
they need the row:

* `require_own_process_teacher` / `require_own_teacher_profile` — the ownership
  resolvers that turn "writer" into "writer, on their own record". A department
  head passes them unconditionally; anybody else must be acting on the row
  linked to their own auth id;
* `require_writer` / `require_department_head` — the role predicates those
  resolvers build on, and the one the SSE projection asks in order to pick a
  viewer's tier;
* `ensure_process_mutable` — every child-resource write is refused when the parent
  process is `final` or `archived`, from one place.

`auth_sdk_m8` enforces a canonical role/flag truth table
(`USER < READER < WRITER < ADMIN < SUPERADMIN`, with `is_superuser` valid only
alongside `SUPERADMIN`), and this service adds no roles of its own. Every role
comparison above goes through the SDK's `has_minimum_role`; nothing inspects
`is_superuser` separately, since the truth table already makes the flag
equivalent to `role == SUPERADMIN` and a second check could only ever produce a
second, divergent answer.

**`Department.department_head_user_id` grants nothing.** It records *who* heads
a department, for attribution, notifications and UI defaults. Authorization is
the caller's own role and only that: a binding cannot be revoked by demoting the
account it names, so it was never a safe credential. This is a deliberate
behaviour change — an account bound as a department's head that does not hold
`ADMIN` loses process-mutation rights it previously had, and existing bindings
must be audited before this ships.

Because the field is descriptive, it must still *describe something true*:
`POST`/`PATCH /departments` refuse (400) a target the identity service does not
know or that holds a role below `ADMIN`. This service never reads the auth
service's user table (`ARCH-NO-CROSS-SERVICE-DATA`), so the role comes from the
issuer's own `GET {AUTH_PREFIX}/users/get/{user_id}/` contract, called with the
**caller's own** bearer token (`services/user_directory.py`) — a lookup can
never see more than the caller already may. The check fails closed: an
unconfigured `INTROSPECTION_URL`, a transport failure or an unexpected status is
a `503` and the head is left unchanged, and the raised reason is a bounded,
secret-free code that never carries the token, the target id or the response
body. One consequence is deliberate and worth stating plainly: the issuer gates
that endpoint on superuser, so naming *somebody else* as head is in practice a
`SUPERADMIN` act, because nothing weaker can verify the claim. Clearing the
field is always allowed — a department whose head has left must not be stranded
by the check.

### 9.5.1 Read scoping

`READER` and `WRITER` see only the departments they belong to; `ADMIN` and
`SUPERADMIN` see the whole deployment (`services/read_scope.py`).

The open question was what a *tenant* is here. This service has no tenant column
of its own and the token's `tenant_id` is not populated in this deployment, so
either would have meant inventing data. What the domain already knows is
**participation**: a `TeacherProfile` linked to an auth user, joined to the
`ProcessTeacher` rows placing that teacher in a process, which belongs to
exactly one department of one school. Membership is therefore derived, never
stored — it widens the moment a teacher is added to a process in a second
department and narrows when that participation is removed, with no second place
to keep in sync.

Membership is by **department**, not by process, so last year's process of the
same department stays readable — which is what the previous-year comparison
needs. Three consequences are deliberate:

* a `READER`/`WRITER` with no linked teacher profile sees nothing: empty lists
  and `404` under every process. "Authenticated" has never meant "belongs here";
* an out-of-scope row answers `404`, not `403`. A `403` confirms the row exists,
  which is exactly what a caller outside the tenant must not learn;
* academic years and classroom stages stay unscoped — a calendar and a grade
  vocabulary, deployment-wide reference data every scoped view needs to render.

The gate is mounted as a router dependency on every router nested under
`/assignment-processes/{process_id}/…`, for the same reason the reader floor is
mounted on the aggregator: a resource added later is scoped by construction
rather than by memory. The top-level process, school, department and
teacher-profile lists filter in their controllers; teacher profiles resolve to
"colleagues in my departments, plus my own profile", the last clause so that the
record a teacher may *edit* is always a record they may *read*.

`tests/test_authorization_boundaries.py` sweeps the generated OpenAPI document
rather than a hand-kept path list, so a route added tomorrow is swept the day it
is added; `tests/test_read_scope.py` pins the scoping decision.

The sweep runs the full §21.1 matrix: every operation against `READER`,
`WRITER`, `ADMIN` and `SUPERADMIN`, plus the unauthenticated and `USER` passes.
It asserts "403 or not 403" rather than an exact status, because what is under
test is the boundary — a caller who clears it may still be answered `404` or
`422` by the domain, and demanding `200` would mean building valid state for a
hundred-odd operations and failing for reasons that have nothing to do with
authorization. The expected floor per operation comes from three explicit sets
(own-data, read-only POSTs, admin-only reads) that are themselves asserted to
name operations that still exist, so a renamed route cannot quietly fall through
to the default. The SSE stream is the one exclusion — an authorized caller gets
an open stream that never completes — and its authorization is covered by the
read-floor, read-scope and audience tests instead.

Ownership is proven with a *second* account of the same role: a `WRITER` who is
a legitimate participant in the same process still gets `403` on another
teacher's turn and another teacher's profile. Direct choice is proven
structurally as well — the request schema has no participant field, so there is
no payload that binds a slot to somebody else.

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

* **Decimal hour columns** — `HoursNumeric` exists but no model uses it yet; hour
  columns remain `float` and are lifted to `Decimal` in the services (§8.2).
* **Authorization hardening** — complete (§9.5, §9.5.1): the read floor, the
  `WRITER`-owns-its-own-records rule, the `ADMIN`/`SUPERADMIN` department head,
  the issuer-checked `department_head_user_id`, per-department read scoping, and
  every gate on the SDK's role dependencies.
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
5. **Published surface** — `test_served_api_surface.py` regenerates
   `docs/served-api-surface.json` from `app.openapi()` and fails on any drift.
   That artifact is what a consumer's own gate compares its declared calls
   against; refresh it with `REPARTO_WRITE_API_SURFACE=1`.

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
