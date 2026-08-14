# Django Dockerfile for kind deployment
# Uses uv for fast dependency installation

FROM python:3.14-slim-bookworm

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Install system dependencies and uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && apt-get purge -y curl \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Copy dependency files first (for better caching).
# gunicorn and whitenoise are declared in pyproject.toml and resolved via
# uv.lock — there is no separate requirements.txt in the repo.
COPY pyproject.toml uv.lock ./

# Install production dependencies with uv (frozen: exactly what uv.lock pins)
RUN uv export --frozen --no-dev -o requirements.txt \
    && uv pip install --system --no-cache -r requirements.txt \
    && rm requirements.txt

# Copy project code
COPY . .

# Collect static files (use ON_KIND_SETUP for build-time only)
RUN ON_KIND_SETUP=1 python manage.py collectstatic --noinput

# Expose port
EXPOSE 8000

# Health check (curl is not installed in the image; use the stdlib instead)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/', timeout=4)"

# Run with gunicorn
CMD ["gunicorn", "--bind", ":8000", "--workers", "2", "--access-logfile", "-", "fuzzy_demo.wsgi:application"]
