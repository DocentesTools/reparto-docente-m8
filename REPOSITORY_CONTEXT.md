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
- Consume `fa-auth-m8` through its authentication contract. Do not own its
  database or private signing keys, and do not directly depend on other services
  beyond that contract.
- Keep domain logic in `controllers/` and `services/`, separate from FastAPI
  route transport; see `docs/ARCHITECTURE.md`.
- Do not hand-author Alembic revisions; use the repository's existing Compose
  workflow to generate and apply migrations from the models.
- Preserve the public HTTP contract consumed by the optional `astro-reparto-m8`
  plugin, including the `reparto-docente-m8@0.1` compatibility range.

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
