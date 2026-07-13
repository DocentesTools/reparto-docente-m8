# dev_reparto_m8

Local-first development stack for **reparto-docente-m8**.

## Services

* `m8_db` — PostgreSQL (reparto_db + auth_db, provisioned by `init-db.sh`)
* `redis_cache` — Redis for the auth service (revocation blacklist,
  rate limits, sessions)
* `auth_user_service` — `fa-auth-m8` issuer (validates tokens, manages
  users)
* `reparto_service` — this repo's FastAPI app (the domain)
* `traefik` — reverse proxy for LAN HTTPS

## First boot

```bash
cp api.env.example api.env
cp auth.env.example auth.env
# Replace every "changethis" placeholder in api.env + auth.env with a
# strong value (see the inline comments in each .env.example file).
bash init.sh           # copies env files, generates certs, runs preflight
docker compose up -d   # builds the images and starts the stack
```

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
docker compose exec m8_db psql -U <DB_USER> -d reparto_db
docker compose down            # stop the stack (volumes preserved)
bash init.sh --reset-db --yes  # wipe the database and re-init from scratch
```

## Local-only vs LAN exposure

The Traefik routers in `traefik/dynamic_conf.yml` are pinned to
`Host(`localhost`)` by default — the stack is not reachable from the LAN
until you remove the `Host(`localhost`) && ` prefix on the relevant
router (and update `BACKEND_CORS_ORIGINS` / `BACKEND_HOST` /
`FRONTEND_HOST` in `api.env` accordingly).

## Path map

| Service               | Public prefix   | Internal (port 9000) |
|-----------------------|-----------------|----------------------|
| `auth_user_service`   | `/user`         | `/user`              |
| `reparto_service`     | `/reparto`      | `/reparto`           |

The internal entryPoint is bound to `127.0.0.1` by default. Override
`API_BIND_IP` to expose it on the LAN (e.g. `API_BIND_IP=0.0.0.0`).
