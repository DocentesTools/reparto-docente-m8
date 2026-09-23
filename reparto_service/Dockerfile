# ---------- BUILD STAGE ----------
FROM python:3.14-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2 AS builder

WORKDIR /install

ARG ENVIRONMENT=production
ENV PIP_ROOT_USER_ACTION=ignore

COPY ./reparto_service/requirements_base.txt .
COPY ./reparto_service/requirements_dev.txt .
COPY ./reparto_service/requirements_prod.txt .
COPY ./reparto_service/requirements_prod.lock .

# Release images install from a hash-locked, fully pinned dependency set so a
# rebuild of the same source cannot silently resolve a different graph.
# `--require-hashes` is enforced for every non-development build. Regenerate
# the lock with:
#   pip-compile --generate-hashes --no-emit-index-url \
#     --output-file=reparto_service/requirements_prod.lock \
#     reparto_service/requirements_prod.txt
RUN pip install --upgrade "pip==26.1.1" && \
    if [ "$ENVIRONMENT" = "development" ]; then \
        pip install --prefix=/install --no-cache-dir -r requirements_base.txt && \
        pip install --prefix=/install --no-cache-dir -r requirements_dev.txt ; \
    else \
        pip install --prefix=/install --no-cache-dir \
            --require-hashes -r requirements_prod.lock ; \
    fi


# ---------- RUNTIME STAGE ----------
FROM python:3.14-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/home/appuser/.local/bin:$PATH"
ENV PYTHONPATH=/opt:/opt/reparto_service

# Debian patch layer — the one fleet form; owner: the workspace's
# `.workspace/context/debian-patch-layer.md`. Every Debian package self-heals
# on each build through `apt-get upgrade -y`; nothing is exact-pinned and
# nothing is installed that the base does not already ship. The base digest
# above moves in lock-step with the other service images, never alone. If a
# package must ever lead the pinned base, the recorded form is an unpinned
# install plus a `dpkg --compare-versions … ge <floor>` assertion in this same
# RUN — never an exact `=` pin, which freezes the image on the next advisory.
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --create-home appuser

WORKDIR /opt

COPY --from=builder /install /usr/local

# Strip installer tooling from the runtime image. This image starts the
# service with Uvicorn (and runs Alembic during startup); it never installs
# packages at runtime, so pip, setuptools and wheel are unnecessary attack
# surface. Keep this after the builder copy so that layer cannot reintroduce
# them, and uninstall pip last because it performs the earlier removals.
# The import probe makes the build fail if the runtime graph actually relies
# on setuptools/pkg_resources.
RUN python -m pip uninstall -y setuptools wheel && \
    python -m pip uninstall -y pip && \
    python -c "import alembic, asyncpg, bcrypt, email_validator, fastapi, fastapi_m8, google.auth, gunicorn, itsdangerous, jinja2, multipart, prometheus_client, psycopg, pydantic, pydantic_settings, redis, slugify, sqlalchemy, sqlmodel, tenacity, uvicorn"

COPY --chown=appuser:appuser ./reparto_service ./reparto_service

# Catalogs are compiled and committed by the build/dev toolchain. The runtime
# deliberately uses only stdlib gettext; fail the image build if either shipped
# locale is absent or unreadable.
RUN python -c "import gettext; from pathlib import Path; root=Path('/opt/reparto_service/locales'); [gettext.translation('reparto', localedir=root, languages=[locale]) for locale in ('es', 'fr')]"

RUN find /opt/reparto_service/scripts -name "*.sh" -exec sed -i 's/\r$//' {} + && \
    chmod +x /opt/reparto_service/scripts/*.sh

USER appuser

EXPOSE 8000

CMD ["./reparto_service/scripts/docker_start.sh"]
