# docker_compose

Local development stacks for `reparto-docente-m8`.

The first stack is [`dev_reparto_m8/`](dev_reparto_m8/) — a local-first stack of
Postgres, Redis, fa-auth and reparto_service, fronted by Traefik for LAN HTTPS.
Its README is the reference for the env-file layout, the **database reset** (both
`db_data/` and the generated `shared_migrations/` revisions), the
`SEED_EXAMPLE_DATA` worked example and LAN exposure.

It serves the API only. The browser surface is the optional `astro-reparto-m8`
plugin mounted by an Astro host; `fa-ui-m8`'s `dev_local_full_ui_m8` stack runs
this service alongside the auth, media and prompt services for that purpose.

The shared infrastructure (Traefik cert init, db init, security
preflight) lives under `shared/` and is consumed by every example via
`init.sh`.

Every runtime `*.env` is git-ignored; only the `*.example` templates are tracked,
and `bash init.sh` copies each missing one into place on first run.

## Adapting this for production

This stack is for local development and LAN meetings (plan 13.1). For
a real deployment:

* swap the self-signed Traefik certs for ones from your CA (e.g. via
  `mkcert -install` then `bash init.sh --rotate-certs`),
* set every `changethis` placeholder to a strong secret (and use the
  `_FILE` mounts the consumer settings support),
* put the database and Redis on a private network with backup policies
  in place,
* turn HSTS on **only** when the cert is stable (the dynamic_conf
  block inlines the HSTS warning).
