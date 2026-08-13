# dev_reparto_m8

Local-first development stack for **reparto-docente-m8**.

This stack serves the API only. To drive the domain from a browser you need the
Astro host as well — see *Using it from a UI* below.

## Services

* `m8_db` — PostgreSQL (reparto_db + auth_db, provisioned by `init-db.sh`)
* `redis_cache` — Redis for the auth service (revocation blacklist,
  rate limits, sessions)
* `auth_user_service` — `fa-auth-m8` issuer (validates tokens, manages
  users)
* `reparto_service` — this repo's FastAPI app (the domain)
* `traefik` — reverse proxy for LAN HTTPS
* `prometheus` + `grafana` — metrics, on `127.0.0.1:9090` / `127.0.0.1:3000`

## Env files

| File | Consumed by | Tracked template |
| --- | --- | --- |
| `.env` | the postgres container's `init-db.sh` and Compose interpolation — **no service reads it** | `.env.example` |
| `auth.env` | `auth_user_service` | `auth.env.example` |
| `reparto.env` | `reparto_service` | `reparto.env.example` |
| `grafana.env` | `grafana` admin credentials | `grafana.env.example` |
| `test.env` | the `security-tests-m8` live runner | `test.env.example` |

`.env` holds the **prefixed** `AUTH_DB_*` / `REPARTO_DB_*` triplets that create
the per-service PostgreSQL users; each service env file connects with the
**generic** `DB_DATABASE` / `DB_USER` / `DB_PASSWORD` names. The two are not
interchangeable spellings of one variable — they must agree per service, or the
service boots against a user that does not exist.

Every runtime `*.env` is git-ignored; only the `*.example` templates are tracked.
When a setting changes, update its template in the same commit.

## First boot

```bash
bash init.sh           # copies each *.env.example to *.env, generates certs,
                       # chmod 600s every env file, runs the preflight
# Now replace every "changethis" placeholder in .env, auth.env, reparto.env and
# grafana.env with a strong value (see the inline comments in each template).
docker compose up -d   # builds the images and starts the stack
```

Fill `.env` **before** the first `docker compose up`: `init-db.sh` runs once, at
volume creation, so a later edit does not reprovision the users.

The init script will:

1. Copy any missing `*.env.example` to `*.env`.
2. Tighten the permissions on every `*.env` to `chmod 600`.
3. Run the `security-tests-m8` preflight (advisory only).
4. Generate the local self-signed Traefik certificates under
   `traefik/certs/`.

## Useful commands

```bash
docker compose ps              # stack status
docker compose logs -f reparto_service
docker compose exec m8_db psql -U <REPARTO_DB_USER> -d reparto_db
docker compose down            # stop the stack (volumes preserved)
```

## Database reset

The three-stage schema is a destructive change with no backward data migration,
so a development database is reset rather than migrated:

```bash
bash init.sh --reset-db --yes
rm -f shared_migrations/reparto_docentes/versions/*.py
docker compose up -d
```

Clear **both**. `--reset-db` deletes `db_data/`, but `shared_migrations/` is a
separate bind mount that survives it; leaving revisions behind replays an old
schema onto the new database, and dropping the revisions while keeping `db_data/`
leaves tables that no revision records. With both cleared, the bootstrap
autogenerates one revision describing the current models and applies it.

Booting a second time must autogenerate **no** further revision. If it does, the
models and the applied schema have drifted — the enum `CHECK` constraint case is
covered by `tests/live/test_schema_postgres.py`.

> If PostgreSQL owns `db_data/` as the container uid, this stack's `--reset-db`
> cannot remove it and stops with a permission error. Remove it with
> `sudo rm -rf db_data/`, or from a throwaway root container:
> `docker run --rm -v "$(pwd):/work" alpine rm -rf /work/db_data`.

The same PostgreSQL instance backs the `fa-auth-m8` issuer, so a reset also
clears its users — including the bootstrap superuser, which is recreated from
`auth.env` on the next boot.

To check the schema the bootstrap *will* generate without generating it, run
`pytest tests/test_schema_migration_gate.py`; see
[`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) §3.3 for the
PostgreSQL variant.

## Example data

Set `SEED_EXAMPLE_DATA=true` in `reparto.env` before `docker compose up` to have
the bootstrap insert one worked Matemáticas department — classroom stages,
teaching groups, subjects, the group-subject matrix, participants and the
leadership hour allocation — sized so its plan balances at exactly 120 group
hours and 124 teacher hours once materialised. Those are two independent
balances, both exact at once, not a discrepancy: a co-teaching activity of 2 h
for two teachers adds 2 group hours and 4 teacher hours.

It is **stage 1 only** — no plan, activity, requirement or assignment is seeded,
so the planning and assignment stages are still yours to walk. It is skipped
unless the domain holds no assignment process at all, so a restart is a no-op and
it cannot collide with existing data; it links no auth user and claims no real
identity. Leave it `false` anywhere real.

## Using it from a UI

The browser surface lives in the optional `astro-reparto-m8` plugin, mounted by
an Astro host. This stack ships no UI container: point a host at
`http://localhost:9000/reparto` (its reparto base is also the plugin's on/off
gate) and add that host's origin to `BACKEND_CORS_ORIGINS` in `reparto.env`.
`http://localhost:4321` — the Astro dev-server default — is allowed out of the
box. A host stack that runs this service alongside the auth, media and prompt
services is `fa-ui-m8`'s `dev_local_full_ui_m8`.

## Local-only vs LAN exposure

The Traefik routers in `traefik/dynamic_conf.yml` are pinned to
`Host(`localhost`)` by default — the stack is not reachable from the LAN
until you remove the `Host(`localhost`) && ` prefix on the relevant
router (and update `BACKEND_CORS_ORIGINS` / `BACKEND_HOST` /
`FRONTEND_HOST` in `reparto.env` accordingly).

LAN reach is not optional for the meeting itself: the selection meeting, the
teachers' own views and the shared projection screen are all browser clients on
other machines. Set `API_BIND_IP=0.0.0.0` in `.env`, drop the `Host()` pin, and
add every client origin to `BACKEND_CORS_ORIGINS`.

## Path map

| Service               | Public prefix   | Internal (port 9000) |
|-----------------------|-----------------|----------------------|
| `auth_user_service`   | `/user`         | `/user`              |
| `reparto_service`     | `/reparto`      | `/reparto`           |

The internal entryPoint is bound to `127.0.0.1` by default. Override
`API_BIND_IP` to expose it on the LAN (e.g. `API_BIND_IP=0.0.0.0`).
