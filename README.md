# reparto-docente-m8

## Classroom stages and classroom creation

`ClassroomStage` is global reference data. It is not scoped to a school,
academic year, department, or active assignment process. Authenticated users
can read stages; only existing `admin` and `superadmin` roles can create,
update, or delete them. A stage referenced by a teaching group cannot be
deleted.

Teaching groups (shown as classrooms in the UI) keep their existing assignment
process scope and now reference one stage through the required
`classroom_stage_id`. Grades must fall inside that stage's inclusive range.
When no custom label is supplied, the service generates
`{grade}° {stage.label} {group_code}`. Omitted labels remain unchanged on
partial updates; clearing a label regenerates it.

Stage CRUD is exposed at `/reparto/classroom-stages/`. Atomic bulk classroom
creation is exposed at
`/reparto/assignment-processes/{process_id}/groups/bulk` and accepts an
inclusive single-letter A-Z range. Any validation or uniqueness conflict rolls
back the complete batch.

No default stage catalogue is seeded. An administrator must create the initial
global stages. The established Compose startup generates/applies the Alembic
revision from SQLModel metadata; this repository does not keep hand-authored
migration revisions.

Local-first FastAPI backend for the **Docentes** teaching-assignment
tool. The first release is a department-head-only product: no LAN
participation, no turns, no exports — just a working process, teachers,
requirements, assignments, and a live-recalculated balance.

This backend follows the [`fa-auth-m8` `examples/fastapi_full`](https://github.com/DocentesTools/fa-auth-m8/tree/main/examples/fastapi_full)
consumer-service shape (consumer of auth tokens, `fastapi-m8` app
lifecycle, alembic migrations, Postgres, Traefik-friendly local stack).

The plan that drives this repo is
[`.claude/plans/docentes/todo/2026-06-26-docentes-local-first-lan-auth-plan.md`](../.claude/plans/docentes/todo/2026-06-26-docentes-local-first-lan-auth-plan.md).
See [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) for the technical
decisions behind the current slice.

## Quick start (local Python)

```bash
# 1. install the local M8 packages (editable, from /opt/fa_m8)
pip install -e ../auth-sdk-m8 ../fastapi-m8

# 2. install this service's runtime + dev dependencies
pip install -r reparto_service/requirements_dev.txt

# 3. copy the example env and edit (every "changethis" → real value)
cp reparto_service/.example_env reparto_service/.env

# 4. run the unit tests
PYTHONPATH=. pytest tests/

# 5. start the service (Postgres-backed; see docker_compose/dev_reparto_m8)
cd reparto_service
alembic upgrade head
uvicorn reparto_service.main:app --reload --port 8000
```

## Quick start (Docker stack)

```bash
cd docker_compose/dev_reparto_m8
cp api.env.example api.env
cp auth.env.example auth.env
# replace every "changethis" → real value
bash init.sh
docker compose up -d
```

The stack serves:

* `https://localhost:4430/user` — `fa-auth-m8` issuer (auth + tokens)
* `https://localhost:4430/reparto` — this service (domain APIs)
* `http://localhost:8080` — Traefik dashboard (loopback only)

## API prefix and structure

The service mounts every route under the configured `API_PREFIX`
(default `/reparto`). The canonical endpoints are:

```
GET    /reparto/academic-years/                         (CRUD)
POST   /reparto/academic-years/
GET    /reparto/academic-years/{id}
PATCH  /reparto/academic-years/{id}
POST   /reparto/academic-years/{id}/archive

GET    /reparto/schools/                                (CRUD)
POST   /reparto/schools/
GET    /reparto/schools/{id}
PATCH  /reparto/schools/{id}

GET    /reparto/departments/                            (CRUD)
POST   /reparto/departments/
GET    /reparto/departments/{id}
PATCH  /reparto/departments/{id}

GET    /reparto/teacher-profiles/                       (CRUD, cross-process)
POST   /reparto/teacher-profiles/
GET    /reparto/teacher-profiles/{id}
PATCH  /reparto/teacher-profiles/{id}
DELETE /reparto/teacher-profiles/{id}

GET    /reparto/assignment-processes/                   (CRUD, parent)
POST   /reparto/assignment-processes/
GET    /reparto/assignment-processes/{id}
PATCH  /reparto/assignment-processes/{id}
GET    /reparto/assignment-processes/{id}/summary       (lightweight balance)
GET    /reparto/assignment-processes/{id}/dashboard     (full balance + validations)

GET    /reparto/assignment-processes/{pid}/teachers/    (per-process teachers)
POST   /reparto/assignment-processes/{pid}/teachers/
GET    /reparto/assignment-processes/{pid}/teachers/{id}
PATCH  /reparto/assignment-processes/{pid}/teachers/{id}
DELETE /reparto/assignment-processes/{pid}/teachers/{id}

GET    /reparto/assignment-processes/{pid}/subjects/     (CRUD)
GET    /reparto/assignment-processes/{pid}/groups/      (teaching groups, CRUD)
GET    /reparto/assignment-processes/{pid}/requirements/ (hour requirements, CRUD)
GET    /reparto/assignment-processes/{pid}/assignments/ (CRUD, with cap-enforcement)
```

## Quality gates

```bash
ruff format .
ruff check .
mypy . --ignore-missing-imports
pytest --cov=reparto_service --cov-fail-under=90
bandit -r reparto_service --severity-level medium
```

Tests are pure unit + API tests against an in-memory SQLite engine
(see `tests/conftest.py`). The first slice targets ~90 % coverage on
the new code (100 % is the long-term policy per workspace rules; the
remaining gaps are the auth event-stream wiring in `core/events.py`
which only matters once LAN read mode ships in Phase 2).

## License

See [`LICENSE`](LICENSE).
