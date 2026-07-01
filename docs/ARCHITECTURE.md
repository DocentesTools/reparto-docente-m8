# Architecture notes — first implementation slice

This document captures the technical decisions behind the current
state of the repo, the first implementation slice of the
[Docentes local-first LAN auth plan](../../.claude/plans/docentes/todo/2026-06-26-docentes-local-first-lan-auth-plan.md).

## 1. Canonical backend path

The plan resolves the canonical backend to `reparto-docente-m8/reparto_service`
(plan §5.2, §5.3). This is the only place domain code lives — there
are no parallel paths in `backend/`, `docentes_app_service/`, or
`frontend/` placeholder locations.

The frontend (Astro plugin) lives in a separate repo
[`astro-reparto-m8`](https://github.com/DocentesTools/astro-reparto-m8)
and consumes the APIs documented in the plan.

## 2. Consumer-service shape

`reparto_service` is a **consumer** of the `fa-auth-m8` issuer:

* it validates JWTs through `auth_sdk_m8` and never holds a signing
  key (`AUTH_SERVICE_ROLE=consumer`),
* it discovers the auth service at the URL configured by
  `INTROSPECTION_URL`,
* it shares the `ACCESS_SECRET_KEY` (HS256) with the issuer in
  `auth.env`.

The `core/events.py` module is the SSE bridge that consumes
`session.revoked` and `user.deleted` events from fa-auth to evict the
local validation cache. It is wired but not exercised by the first
slice's tests (Phase 2 will turn it on).

## 3. Database

PostgreSQL is the canonical target (plan §13.3, Q2 resolved as
"Postgres-first"). Models use a `CHAR(36)` UUID column
(`reparto_service.core.db_models.UUIDString`) so the same schema is
round-trip-clean on Postgres, MySQL/MariaDB, and the SQLite in-memory
engine used by the test suite.

`TABLES_PREFIX=reparto` is the default (set in `.example_env`) so every
table name becomes `reparto_<entity>`. This isolates the service
inside a shared database and makes it easy to grep for its tables.

## 4. Domain model (first slice)

The first slice implements the plan's "small but end-to-end" cut
(plan §22). The implemented entities are:

* `AcademicYear` — academic year record (CRUD)
* `School` — institute context (CRUD)
* `Department` — teaching department inside a school (CRUD)
* `TeacherProfile` — minimal cross-process teacher record (CRUD)
* `AssignmentProcess` — the annual department assignment process
  (CRUD + summary + dashboard)
* `ProcessTeacher` — teacher-per-process binding (CRUD)
* `Subject` — per-process subject (CRUD)
* `TeachingGroup` — per-process teaching group (CRUD)
* `HourRequirement` — per-process required hours (CRUD)
* `Assignment` — the central decision: who teaches how many hours of
  what (CRUD with cap-enforcement)

Plan §8 enums are centralised in `reparto_service/enums.py`. The full
status vocabulary is declared up-front, even if a particular status is
not transitioned by the first slice — this avoids a schema migration
for every phase.

## 5. Summary service

The heart of the product is `reparto_service.services.summary.SummaryService`.
It is a stateless calculator that takes a `Session` and a
`process_id` and returns:

* `GlobalBalance` — total required / available / assigned / pending
  hours, the aggregate balance state, and counts of uncovered
  requirements and overloaded teachers.
* `TeacherBalance` — per-process-teacher available / assigned /
  remaining / excess hours and state.
* `RequirementBalance` — per-hour-requirement required / assigned /
  pending hours and state.
* `ProcessDashboard` — all of the above plus the validation messages.

### 5.1 Balance states (plan §9)

* GlobalBalanceState: `balanced`, `pending`, `exceeded`, `warning`.
* TeacherBalanceState: `balanced`, `pending`, `overloaded`, `inactive`,
  `not_participating`.
* RequirementBalanceState: `uncovered`, `partial`, `covered`,
  `over_assigned`, `explicitly_shared`.

### 5.2 Validations (plan §9.4)

Every balance is translated into zero or more `ValidationMessage`
records with a severity of `info`, `warning`, or `blocking`:

* **Blocking**: requirement `over_assigned` (no override), requirement
  `uncovered`, teacher `overloaded` (no override), process has
  pending hours or unresolved overage.
* **Warning**: requirement `over_assigned` (with override), teacher
  `overloaded` (with override), requirement `partial`.
* **Info**: requirement `fully_assigned`, teacher `balanced`, process
  `balanced`.

### 5.3 The over-assignment cap (plan §9.3, §8.10)

The rule is: the sum of `assigned_hours` for a requirement must not
exceed `required_hours` unless **at least one assignment on the
requirement** carries a department-head override. The cap is enforced
in `AssignmentController._enforce_requirement_cap`, and the summary
service uses the same `has_override` flag to decide whether an
over-assignment is a blocking or a warning validation.

## 6. API design

### 6.1 Routing

* `api_router` is mounted with the configured `API_PREFIX`
  (default `/reparto`) in `reparto_service/main.py`.
* `app/main.py` aggregates the per-resource routers.
* All routes use typed request/response schemas (Pydantic v2) and
  delegate business logic to `controllers/`.

### 6.2 Error model

* Domain errors (not found, validation) raise `HTTPException` with the
  right status code. The plan's structured error format
  (`auth_sdk_m8.schemas.base.ResponseErrorBase`) is inherited from
  `auth_sdk_m8.controllers.base.BaseController` and used as a
  fallback for unexpected exceptions.

### 6.3 Permissions (first slice)

Plan §7 distinguishes `department_head`, `teacher`, `readonly`, etc.
The first slice does not yet have a department-head role binding —
the consumer service trusts whatever role the auth SDK hands it. For
now, mutations require `is_superuser` or a `writer`-class role
(superadmin, admin, writer); reads are open to any authenticated
user. The full role mapping is part of Phase 1 (auth integration
task) and is the next thing the team will need to pick up.

## 7. Tests

Tests live at `tests/` (per `pytest.ini`). The conftest:

* sets the required env vars BEFORE the first `reparto_service`
  import (Pydantic settings are constructed at import time),
* monkey-patches `auth_sdk_m8.utils.paths.find_dotenv` so the local
  `.example_env` is not loaded,
* uses an in-memory SQLite engine per test (no cross-test pollution).

The test suite has three layers:

1. `test_core_db_models.py` — `UUIDString` TypeDecorator and
   `prefixed_tables` helper.
2. `test_summary_service.py` — the full calculation policy
   (balances, validations, per-state transitions).
3. `test_routes_*.py` and `test_controllers_base.py` — FastAPI
   integration tests for the public routes (happy paths and key
   error paths).
4. `test_main.py` — smoke tests for the wired `create_app` (openapi,
   health, meta, routes count).

The current coverage on the new code is 91 %; the remaining gaps are
mostly the auth event-stream bridge (`core/events.py`, exercised only
once Phase 2 is in) and a few defensive error paths in the
controllers (e.g. integrity-error fallback on duplicate `slug`).

## 8. Migrations

Alembic is wired with `script_location = ./reparto_service/alembic`
and `version_locations = ./shared_migrations/reparto_docentes/versions`
so the dev stack can mount the migrations directory as a volume
shared with `auth_user_service` and other consumers. The first
migration is generated by the existing `docker_start.sh` bootstrap
when the volume is empty (the plan's workspace policy: no
hand-written migration files).

## 9. What is intentionally not in the first slice

* LAN read mode (Phase 2) — `MeetingSession` is not implemented.
* Turn order (Phase 3) — `SelectionTurn` is not implemented.
* Direct teacher selection (Phase 4) — the assignment endpoint is
  department-head-only.
* Exports (Phase 5) — no PDF/CSV/backup generation.
* Audit events — the table is not implemented; the plan defers
  AuditEvent to "after MVP" and the first slice ships without it.

These are explicit trade-offs of the "small but end-to-end" first
slice (plan §22). Each of them has a clear extension point in the
existing routes (`POST /assignments/{id}/override`,
`POST /processes/{id}/meeting-session/open`, etc.) so the next phases
do not require breaking changes.
