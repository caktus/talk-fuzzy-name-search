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

# PostgreSQL -- either Postgres.app (https://postgresapp.com, no separate
# service management needed) or Homebrew:
brew install postgresql@17
brew services start postgresql@17
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
cd talk-fuzzy-name-search
uv sync

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

# Reverse migrations to save time while creating data
uv run python manage.py migrate records 0002

# Create fake data
uv run python manage.py seed_data --count 100000 --flush --seed 42 --as-of 2026-01-01

# Full 54M stage dataset. Rreference runs:
# ~100 min on a 40 GB Linux VM w/ PCIe 5.0 X4 NVME
# ~4 hours on a 36 GB MacBook Pro M3 Max (2023)
# uv run python manage.py seed_data --count 54000000 --flush --seed 42 --as-of 2026-01-01

# Run
uv run python manage.py runserver # Web demo (localhost:8000)
uv run marimo edit demo.py # Interactive notebook
uv run pytest tests/ # Test suite
```

The pre-rewrite CSV pipeline lives in `scripts/legacy/` for reference; it is
not part of this flow (see `scripts/legacy/README.md`).

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

- **`records/`** -- Django app with `Person` model, the `search_unified()` search API, and web views
- **`demo.py`** -- Marimo notebook for the 45-minute conference presentation (drives the same `search_unified()` API as the web UI)
- **`scripts/legacy/`** -- Pre-rewrite CSV data pipeline (superseded by `manage.py seed_data`)
- **`54M_status.md`** -- Verification results and EXPLAIN plans for the 54M stage dataset
- **`tests/`** -- Test suite (179 passing)

## Architecture

All search runs through one API, `Person.objects.search_unified(modes, first_name, last_name, date_of_birth)`, with independently toggleable modes -- exact prefix, legacy LIKE, Soundex, Daitch-Mokotoff, and trigram -- OR-ed into a single query, plus Levenshtein (edit distance <= 2) applied as a precision filter (AND) on top of the base modes. Phonetic matching stores no pre-computed tokens: PostgreSQL's `SOUNDEX()` and `DAITCH_MOKOTOFF()` are applied directly in SQL via functional indexes (B-tree for Soundex equality, GIN for Daitch-Mokotoff array overlap), prefix mode rides a `text_pattern_ops` B-tree, and trigram mode uses a pg_trgm GiST KNN scan (`<->` distance ordering) with a per-name `similarity() >= 0.3` cutoff (`TRIGRAM_SIMILARITY_CUTOFF`) that keeps the top-100 from surfacing noise. Each search returns one 100-row page, and every row is annotated with the mode(s) that matched it, which the UI renders as badges.

## Performance at 54M Scale

EXPLAIN (ANALYZE)-verified on the live 54M stage DB (full plans in `54M_status.md`):

| Query                                                                                | Rows | Time    |
| ------------------------------------------------------------------------------------ | ---- | ------- |
| Trigram KNN, single common name (`ORDER BY last_name <-> 'Smith' LIMIT 100`)         | 100  | ~7 ms   |
| Prefix, first name only (bare `LIKE 'JOHN%'` filter)                                 | 100  | ~4 ms   |
| Prefix, first name only (UI query, incl. `ORDER BY` + match annotation)              | 100  | ~379 ms |
| Trigram KNN, dual name (`ORDER BY (last <-> 'Smith'), (first <-> 'John') LIMIT 100`) | 100  | ~14 s   |

## Production Differences

| Aspect           | Demo                                                                           | Production system                          |
| ---------------- | ------------------------------------------------------------------------------ | ------------------------------------------ |
| Phonetic storage | On-the-fly `SOUNDEX()`/`DAITCH_MOKOTOFF()` expressions with functional indexes | On-the-fly `daitch_mokotoff()` expressions |
| Algorithms       | Prefix, legacy LIKE, Soundex + DM, trigram KNN, Levenshtein filter             | DM only                                    |
| Search methods   | Unified ORM search (`search_unified`, 100-row page)                            | Single RawSQL query                        |
| Scale            | 54M rows (stage DB)                                                            | 54M+ rows                                  |
