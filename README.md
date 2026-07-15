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
- Per-process teachers, subjects, teaching groups, hour requirements, and
  capacity-enforced assignments.
- Process lifecycle transitions, reopening, draft restoration, summaries,
  dashboards, audit events, and a server-sent event stream.
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
| Teaching-load planning | `/reparto/assignment-processes/{process_id}/allocation-revisions`, `/teaching-plan`, `/group-subjects`, `/teaching-activities` |
| Lifecycle and read models | `/transition`, `/reopen`, `/copy-previous-year`, `/summary`, `/dashboard`, `/lan/me`, `/events` under an assignment process |
| Audit and history | `/audit-events`, `/versions`, `/compare-previous-year`, `/exports`, `/restore-draft` under an assignment process |
| Meeting turns | `/reparto/assignment-processes/{process_id}/meeting-sessions/{meeting_session_id}/turns` |

Assignment endpoints include `POST /assignments/direct-choice`. Selection-turn
endpoints support initialization plus start, complete, skip, and override
actions. Group-subject endpoints include `POST /group-subjects/bulk-preview` and
`POST /group-subjects/bulk-apply` for filtered create/update/upsert matrix
operations with a confirmed affected-row count. Teaching-activity endpoints
manage manual secondary planning items and their multi-group links (main-subject
activities are materialised from group subjects in a later flow). Process
teachers carry `base_weekly_hours` and department-head authorized
`extra_weekly_hours`; their sum is the exposed `target_weekly_hours`, and a
non-zero extra flags `is_overloaded`. Extra hours change only through the audited
`POST /teachers/{process_teacher_id}/extra-hours` action (reason required, blocked
below already-assigned hours), never through the generic teacher `PATCH`. Consult
the OpenAPI schema for request and response models.

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
