# Refactored from https://github.com/astral-sh/uv-docker-example
#
# Structure follows the example: uv preinstalled base image, non-root user,
# dependency install split from source copy for layer caching. BuildKit
# `--mount=type=cache` directives are omitted for podman build compatibility.
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

# Setup a non-root user
RUN groupadd --system --gid 999 nonroot \
 && useradd --system --gid 999 --uid 999 --create-home nonroot

# Install the project into `/app`
WORKDIR /app

ENV PYTHONUNBUFFERED=1
# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1
# Copy from the cache instead of linking (read-only build layers)
ENV UV_LINK_MODE=copy
# Omit development dependencies (debug toolbar etc. stay out of the image)
ENV UV_NO_DEV=1

# Passed by the deployment so the app can report its deployed version.
ARG PODMAN_COMMIT_SHA=unknown
ENV PODMAN_COMMIT_SHA=${PODMAN_COMMIT_SHA}

# Copy the lockfile and manifest, then install only the project's
# dependencies. Splitting this from the source copy allows optimal layer
# caching: dependencies are reinstalled only when the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project

# Then, add the rest of the project source code and finish the sync.
COPY . .
RUN uv sync --locked

# Collect static files at build time so WhiteNoise can serve them.
# DEBUG is off at build time so the dev-only debug toolbar (not installed,
# see UV_NO_DEV=1) is not part of the app registry.
ENV DEBUG=False
RUN uv run --no-sync python manage.py collectstatic --noinput \
 && chown -R nonroot:nonroot /app

# Use the non-root user to run the application
USER nonroot

COPY --chown=nonroot:nonroot docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Runs migrations (if DJANGO_MANAGEPY_MIGRATE=on), then gunicorn.
ENTRYPOINT ["/docker-entrypoint.sh"]
