# Fuzzy Name Search at 50M Scale -- Demo

Django + PostgreSQL strategies for fuzzy name matching across 50 million records.

## Quick Start

```bash
# Setup
cd talk-fuzzy-name-search
uv sync

# Database (one-time)
sudo -u postgres psql -c "CREATE DATABASE fuzzy_demo;"

# Migrate + seed 50M records
uv run python manage.py migrate
uv run python scripts/generate_data.py --count 50000000 --output data/people_50m.csv
uv run python scripts/load_csv.py data/people_50m.csv

# Run
uv run python manage.py runserver    # Web demo (localhost:8000)
uv run marimo edit demo.py           # Interactive notebook
uv run pytest tests/                 # Test suite (29 passing)
```

## What's Inside

- **`records/`** -- Django app with `Person` model, phonetic search QuerySets, and web views
- **`demo.py`** -- Marimo notebook for the 45-minute conference presentation
- **`scripts/`** -- Data generation and loading scripts
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
