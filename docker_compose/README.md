# docker_compose

Local development stacks for `reparto-docente-m8`.

The first stack is `dev_reparto_m8/` — a local-first Postgres + Redis +
fa-auth + reparto_service stack fronted by Traefik for LAN HTTPS.

The shared infrastructure (Traefik cert init, db init, security
preflight) lives under `shared/` and is consumed by every example via
`init.sh`.

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
