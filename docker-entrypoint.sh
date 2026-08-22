#!/bin/sh
set -e

# Run Django migrations before starting the server, if requested by the
# deployment (DJANGO_MANAGEPY_MIGRATE=on).
if [ "$DJANGO_MANAGEPY_MIGRATE" = "on" ]; then
    uv run --no-sync python manage.py migrate --noinput
fi

exec uv run --no-sync gunicorn fuzzy_demo.wsgi:application --bind 0.0.0.0:8000 --workers 3 --access-logfile -
