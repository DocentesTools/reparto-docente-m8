#!/bin/sh
set -e
set -x

# Ensure PYTHONPATH is set correctly
export PYTHONPATH=/opt/reparto_service

echo "Current working directory: $(pwd)"

echo "Waiting for DB..."
fastapi-m8-prestart || { echo "Failed to reach DB"; exit 1; }

# Run migrations
echo "Run Migrations"
alembic -c /opt/reparto_service/alembic.ini upgrade head || { echo "Migration failed"; exit 1; }

# Seed the worked configuration example. The module is a no-op unless
# SEED_EXAMPLE_DATA is on *and* the domain is empty, so this is safe to run on
# every start and the decision stays in one place (reparto_service/initial_data).
echo "Seed example data if enabled"
python -m reparto_service.initial_data || { echo "Failed to create initial data"; exit 1; }
