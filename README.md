# Fuzzy Name Search at 50M Scale -- Demo

Django + PostgreSQL strategies for fuzzy name matching across 50 million records.

## Quick Start

Prereqs: Python 3.14+ (see `requires-python` in `pyproject.toml`), `uv`
(fallback: `pip install uv`), and PostgreSQL with the `fuzzystrmatch` and
`pg_trgm` extensions. Both extensions ship with standard PostgreSQL installs
and the app's migrations create them for you — the database user just needs
privilege to create extensions (superuser, or the matching grants).

```bash
# Setup
cd talk-fuzzy-name-search
uv sync

# Database (one-time)
sudo -u postgres psql -c "CREATE DATABASE fuzzy_demo;"

# Optional: .env. You can skip this entirely if your local Postgres uses the
# default credentials (settings.py falls back to
# psql://postgres@localhost:5432/fuzzy_demo).
cat > .env <<EOF
DATABASE_URL=psql://postgres:postgres@localhost:5432/fuzzy_demo
DEBUG=True
DJANGO_SECRET_KEY=django-insecure-demo-key-change-in-production
EOF

# Migrate + seed a fast local dataset (under a minute)
uv run python manage.py migrate
uv run python manage.py seed_data --count 100000 --flush --seed 42 --as-of 2026-01-01
# Full 54M stage dataset (reference run: ~100 min on a 40 GB machine,
# see 54M_status.md):
# uv run python manage.py seed_data --count 54000000 --flush --seed 42 --as-of 2026-01-01

# Run
uv run python manage.py runserver    # Web demo (localhost:8000)
uv run marimo edit demo.py           # Interactive notebook
uv run pytest tests/                 # Test suite
```

The pre-rewrite CSV pipeline lives in `scripts/legacy/` for reference; it is
not part of this flow (see `scripts/legacy/README.md`).

## What's Inside

- **`records/`** -- Django app with `Person` model, phonetic search QuerySets, and web views
- **`demo.py`** -- Marimo notebook for the 45-minute conference presentation
- **`scripts/legacy/`** -- Pre-rewrite CSV data pipeline (superseded by `manage.py seed_data`)
- **`data/people_50m.csv`** -- 50M fake person records (2.6GB)
- **`tests/`** -- Characterization tests (29 passing)

## Architecture

Dual-layer indexing and filtering pipeline:

1. **Phonetic Matching** (Soundex + Daitch-Mokotoff) -- broad filter via GIN indexes on stored phonetic token arrays
2. **Levenshtein Filtering** -- precision filter with early-exit optimization (`levenshtein_less_equal`)

## Performance at 50M Scale

| Search        | Query               | Results | Time   |
| ------------- | ------------------- | ------- | ------ |
| Phonetic      | "John Smith"        | 10      | 24ms   |
| Phonetic      | "Jonh Smyth" (typo) | 10      | 5ms    |
| Legacy (LIKE) | "Jonh Smyth" (typo) | 0       | 2800ms |
| LIKE          | "Smith" (exact)     | 10      | 1ms    |

## Production Differences

| Aspect           | Demo                           | Production system                          |
| ---------------- | ------------------------------ | ------------------------------------------ |
| Phonetic storage | Stored ArrayField columns      | On-the-fly `daitch_mokotoff()` expressions |
| Algorithms       | Soundex + DM                   | DM only                                    |
| Search methods   | Trigram (KNN) + Phonetic (ORM) | Single RawSQL query                        |
| Scale            | 50M rows                       | 54M+ rows                                  |
