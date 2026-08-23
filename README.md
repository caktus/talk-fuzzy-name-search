# Fuzzy Name Search at 54M Scale -- Demo

Django + PostgreSQL strategies for fuzzy name matching across 54 million records.

## Quick Start

Prereqs: Python 3.14+ (see `requires-python` in `pyproject.toml`), `uv`
(fallback: `pip install uv`), and PostgreSQL with the `fuzzystrmatch` and
`pg_trgm` extensions. Both extensions ship with standard PostgreSQL installs
and the app's migrations create them for you — the database user just needs
privilege to create extensions (superuser, or the matching grants).

### Fresh Machine Setup

If you're starting from a clean macOS or Linux machine, install `uv` and
PostgreSQL first:

**macOS**

```bash
# uv (Python package/dependency manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# PostgreSQL
# Install via Postgres.app (https://postgresapp.com/downloads.html)

# Or via Homebrew:
brew install postgresql@18
brew services start postgresql@18
```

**Linux (Debian/Ubuntu)**

```bash
# uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# PostgreSQL
sudo apt update
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable --now postgresql

# Create a superuser role matching your OS username (lets you run `psql`
# and connect without a password locally):
sudo -u postgres createuser --superuser "$(whoami)"
```

After installing, confirm `psql` and `uv` are on your `PATH` (you may need
to restart your shell), then continue below.

```bash
# Setup
git clone git@github.com:caktus/talk-fuzzy-name-search.git
cd talk-fuzzy-name-search
uv sync --locked

# Database (one-time)
psql -c "CREATE DATABASE fuzzy_demo;"

# Optional: .env. You can skip this entirely if your local Postgres uses the
# default credentials (settings.py falls back to
# psql://postgres@localhost:5432/fuzzy_demo).
cat > .env <<EOF
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/fuzzy_demo
DEBUG=True
DJANGO_SECRET_KEY=django-insecure-demo-key-change-in-production
EOF
```

### Seeding with real name data

`seed_data` weights first/last names with real US Census/SSA frequency counts
from `name_dataset/us_forenames.csv` and `name_dataset/us_surnames.csv`. Those
files are gitignored local artifacts (not committed), so download them once
first — run the marimo script from the `name_dataset/` directory so the CSVs
land where `seed_data` expects them:

```bash
cd name_dataset
uv run python download_name_data.py   # one-time; requires kagglehub (already a dependency)
cd ..

# Migrate + seed a fast local dataset (under a minute)
uv run python manage.py migrate

# Reverse index migrations to save time while creating data (potentially hours!)
uv run python manage.py migrate records 0002

# Create fake data
uv run python manage.py seed_data --count 100000 --flush --seed 42 --as-of 2026-01-01

# Full 54M stage dataset. Reference runs:
# ~40-45 without indexes, or 4 hours or more with indexes on a MBP M3 Max
# IMPORTANT: Run large seeds with DEBUG *disabled* to minimize RAM usage / query logging overhead.

# DEBUG=False time uv run python manage.py seed_data --count 54000000 --flush --seed 42 --as-of 2026-01-01

# Add non-gist indexes
# ~5 min on MBP M3 Max; ~2.5 min on Linux VM (PCIe 5.0 X4 NVME)
time uv run python manage.py migrate records 0003
# Add gist index (slow)
# ~20 min on MBP M3 Max; ~14 min on Linux VM (PCIe 5.0 X4 NVME)
time uv run python manage.py migrate records 0004

# Run
uv run python manage.py runserver # Web demo (localhost:8000)
uv run pytest tests/ # Test suite
```

## Sharing the demo data

On the machine that generated the data:

```bash
pg_dump -Ox -Fc fuzzy_demo > fuzzy_demo.tar
```

On the machine restoring the data:

```bash
dropdb fuzzy_demo
createdb fuzzy_demo
pg_restore -d fuzzy_demo fuzzy_demo.tar
```

## Slides

Running the slides locally:

```bash
uv run marimo edit --watch --no-token slides.py
```

With a token (and listen on local network):

```bash
uv run marimo edit --watch --host 0.0.0.0 slides.py
```

## What's Inside

- **`records/`** -- Django app with `CourtRecord` model, the `search_unified()` search API, and web views
- **`slides.py`** -- Marimo slide deck for the conference presentation (drives the same `CourtRecord` model as the web UI)
- **`tests/`** -- Test suite
