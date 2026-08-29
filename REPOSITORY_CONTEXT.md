# reparto-docente-m8

## Layer

Service (teaching-assignment domain system).

## Purpose

Provide the local-first FastAPI service for the Docentes teaching-assignment
domain: schools, academic years, departments, teacher profiles, assignment
processes, processes' teachers, subjects, groups, requirements, capacity-aware
assignments, meeting sessions, selection turns, versions, exports, and audit.

The API prefix defaults to `/reparto`; OpenAPI documentation is enabled when
`SET_DOCS=true`.

## Responsibilities and boundaries

- Own this service's database schema for reference data, processes, assignments,
  and audit data.
- Enforce assignment capacity, assignment-process lifecycle transitions, and
  selection-turn ordering.
- Serve LAN teacher read access, meeting turns, dashboards, and the SSE stream.
- Consume `fa-auth-m8` through its authentication contract from `fastapi-m8`. Do not own its
  database or private signing keys, and do not directly depend on other services
  beyond that contract.
- `auth-sdk-m8` is never imported directly in service code — only `fastapi-m8`
  and its re-exports are. `ruff.toml` enforces this with a `TID251`
  banned-api rule (`auth_sdk_m8`); `ruff check .` fails on any direct import
  outside `tests/`.
- Keep domain logic in `controllers/` and `services/`, separate from FastAPI
  route transport; see `docs/ARCHITECTURE.md`.
- Do not hand-author Alembic revisions; use the repository's existing Compose
  workflow to generate and apply migrations from the models.
- Preserve the public HTTP contract consumed by the optional `astro-reparto-m8`
  plugin, including the `reparto-docente-m8@2.0.0` compatibility range declared
  in `reparto_service/core/config.py` and published in
  `docs/served-api-surface.json`.

## Authorization model

Roles are issued by `fa-auth-m8` and are fixed: `USER < READER < WRITER <
ADMIN < SUPERADMIN`. This service registers no role of its own, and no code
here inspects `is_superuser` on its own — the issuer's truth table makes that
flag equivalent to `role == SUPERADMIN`, so a separate reading of it could only
ever produce a second, divergent answer.

- **One mounted floor, not 21 remembered ones.** `reparto_service/app/main.py`
  mounts `Depends(require_reader)` on the router that aggregates every domain
  router, so every route — read, export and mutation alike — answers `401`
  unauthenticated and `403` for a `USER` before its handler runs, and a router
  added later inherits the floor by construction. `/health`, `/meta`, `/ping`
  and `/metrics` are framework-owned and mounted outside it.
- **No bare `CurrentUser`.** Neither `reparto_service/core/deps.py` nor
  `reparto_service/app/deps.py` defines or re-exports one, so there is nothing
  to import. A route names the role it needs — `CurrentReader`, `CurrentWriter`
  or `CurrentAdmin` — and cannot silently settle for "authenticated".
  `get_current_user` survives only as the dependency-override seam the test
  suite needs; using it as a route's principal is not a supported shape, and
  `tests/test_authorization_boundaries.py` answers `401` on every route to a
  client that satisfies only that dependency.
- **The gates are the SDK dependencies, by identity.** `require_reader` /
  `require_writer` / `require_admin` in `reparto_service/core/deps.py` *are*
  `auth.get_current_active_reader` / `_writer` / `_admin`, not local
  equivalents: only that path re-validates on the fresh, no-positive-cache
  user, which is what makes a demoted or revoked account lose the role on its
  next request instead of at the end of a cache TTL.
- **Department-head authority is `ADMIN`/`SUPERADMIN`, full stop.**
  `Department.department_head_user_id` is descriptive metadata for attribution
  and UI defaults and grants no capability: a binding is not a credential and
  cannot be revoked by demoting the account.
- **`WRITER` mutates its own records only** — own teacher profile, own
  direct-choice, own selection turn — and ownership is proven against the row,
  never inferred from the role.
- **Read scope is not a confidentiality tier.**
  `services/read_scope.py` answers *which processes* a caller may read;
  `services/sse.py`'s audience projection answers *which payload* they receive.
  A read that carries the department-head tier — per-participant hours, the
  validation findings that name them, `extra_hours_reason`, the feasibility
  witness and diagnostics — declares `CurrentAdmin` however harmless a `GET`
  looks, and `tests/test_authorization_boundaries.py::ADMIN_ONLY_READS` is the
  list. The teacher tier reads `…/lan/me` and the shared screen reads
  `…/summary`; both are at the reader floor because neither carries another
  participant's figures.

### Controller-level role checks are kept on purpose

The `has_minimum_role` calls in `controllers/base.py`,
`controllers/departments.py`, `controllers/teacher_profiles.py` and
`services/read_scope.py` sit *underneath* the route-level role dependencies.
That layering is deliberate and reviewed; do not collapse it into the
dependencies, and do not re-raise it as duplication.

- Most of them are not the same question the dependency answers. A dependency
  decides about the caller alone; these decide about a **row** (is this the
  caller's own participation or profile?), about a **third party** (may the
  account being recorded as department head hold that record?), or about
  **query scope** (which departments may this caller see at all?). None of
  those can be expressed by a dependency without moving row access into the
  dependency layer.
- Where a floor genuinely does repeat one — `DomainController.require_writer`
  under a route's `CurrentWriter` — the repeat costs one comparison and buys
  the controller a guarantee that does not depend on every present and future
  caller having declared the right annotation. A route-level floor also never
  makes a finer check unnecessary: reader does not imply writer, and writer
  does not imply owner.
- The duplication is of the *call*, never of the *rule*. Every one of these
  sites delegates to the issuer SDK's own `has_minimum_role`; none
  re-implements the ordering and none consults `is_superuser`. Should the
  import path for that predicate change, the layering decision here is
  unaffected.

## Portable quality guidance

Choose a Python environment documented by this repository or configured for the
active development environment. Do not assume a user-specific Conda environment,
activation-hook path, global interpreter, or parent-workspace virtual environment.

When the relevant tooling is available and quality validation is requested, run
these repository-root commands in the selected environment:

- `ruff format .`
- `ruff check .`
- `mypy . --ignore-missing-imports`
- `pytest --cov --cov-fail-under=100`
- `bandit -r . --severity-level medium`

## Standalone authority

This file, repository documentation, and existing CI are the authoritative local
context. A verified nearest workspace may optionally add launcher-selected
policies and tasks; its absence is a successful standalone condition and does not
make a parent workspace necessary.
