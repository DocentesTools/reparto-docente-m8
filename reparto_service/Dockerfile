# ---------- BUILD STAGE ----------
FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6 AS builder

WORKDIR /install

ARG ENVIRONMENT=production
ENV PIP_ROOT_USER_ACTION=ignore

COPY ./reparto_service/requirements_base.txt .
COPY ./reparto_service/requirements_dev.txt .
COPY ./reparto_service/requirements_prod.txt .

RUN pip install --upgrade "pip==26.1.1" && \
    if [ "$ENVIRONMENT" = "development" ]; then \
        pip install --prefix=/install --no-cache-dir -r requirements_base.txt && \
        pip install --prefix=/install --no-cache-dir -r requirements_dev.txt ; \
    else \
        pip install --prefix=/install --no-cache-dir -r requirements_prod.txt ; \
    fi


# ---------- RUNTIME STAGE ----------
FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/home/appuser/.local/bin:$PATH"
ENV PYTHONPATH=/opt:/opt/reparto_service

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends curl=8.14.1-2+deb13u4 \
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

RUN find /opt/reparto_service/scripts -name "*.sh" -exec sed -i 's/\r$//' {} + && \
    chmod +x /opt/reparto_service/scripts/*.sh

USER appuser

EXPOSE 8000

CMD ["./reparto_service/scripts/docker_start.sh"]
