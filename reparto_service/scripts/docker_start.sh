#!/bin/sh
set -e

# Run migrations
# Check if alembic/versions has no .py files (ignores .gitkeep)
if [ -z "$(find /opt/shared_migrations/reparto_docentes/versions -maxdepth 1 -name '*.py' -print -quit)" ]; then
    echo "Generating Alembic migration..."
    if ! alembic -c /opt/reparto_service/alembic.ini revision --autogenerate -m "Initial m8 migration"; then
        echo "Failed to generate initial migration"
        exit 1
    else
        echo "Initial migration generated..."
    fi
fi

# Run any pre-start tasks
echo "Initialyse DB and data..."
echo "Checking if pre_start.sh exists at $(pwd)/reparto_service/scripts/pre_start.sh"
ls -l $(pwd)/reparto_service/scripts/pre_start.sh
if ! ./reparto_service/scripts/pre_start.sh; then
    echo "Failed to initialise DB and data"
    exit 1  # Ensure the script exits if needed
fi

# Start the FastAPI server
if [ "$VSCODE_DEBUG" = "true" ]; then
  echo "Starting reparto_service under VS Code debugpy..."
  exec python -m debugpy \
    --listen 0.0.0.0:5678 \
    --wait-for-client \
    -m uvicorn reparto_service.main:app \
      --host 0.0.0.0 --port 8000 --reload
else
  exec uvicorn reparto_service.main:app --host 0.0.0.0 --port 8000
fi