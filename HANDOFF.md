# Handoff: Fuzzy Name Search at Scale

## Status: ✅ Complete — 54M records seeded (stage DB intact), unified search UI implemented, 179 tests passing

All review fixes from `RECS-2026-08-14` are implemented and **committed on this branch** (see `git log`); the working tree is clean.

## What's Done

### 1. Database: 54M person records

`records/management/commands/seed_data.py` generates realistic, deterministic person records with:

- Name pool: distinct (first, last) pairs drawn without replacement from the 690K-pair Faker en_US space (no duplicates)
- Name frequency: Zipf(a=1.1) sampling of pool rows -- the sole source of the heavy tail
- Cluster model: 80% singleton identities, 20% heavy-tailed clusters (Pareto(1.5), clipped to 2–80)
- 20% of non-canonical rows in multi-member clusters get one injected typo (the canonical row is never typo'd; row-level typo rate across the whole table is ~7–9%)
- 90% middle names, 30% nicknames
- DOBs derive from `--as-of`; person_ids come from the seeded RNG, so (seed, count, as-of) reproduces a dataset exactly

**Streaming insert** (fixed OOM): batches are generated, expanded, and inserted immediately instead of accumulating all rows in memory, and each batch is capped at 2M identities (B10), so peak memory is bounded to one batch. The 54M reference run peaked at ~1.4 GB (was 14 GB OOM).

```bash
# fast local dataset
.venv/bin/python manage.py seed_data --count 100000 --flush --seed 42 --as-of 2026-01-01
# full 54M stage dataset: ~98 minutes, no OOM on 40 GB RAM
```

Note: the 54M dataset currently on the stage DB was produced by the **pre-rewrite** generator revision; `54M_status.md` is the provenance of record for that data, and re-seeding with the current generator is a separate, deliberate step.

Verified distributions (`54M_status.md`):
| Metric | Expected | Actual |
|--------|----------|--------|
| Row count | 54,000,000 | 54,000,000 ✅ |
| Max cluster size | ≤ 80 | 80 ✅ |
| Singleton identities | ~80% | 80.00% ✅ |
| Middle name rate | ~0.90 | 0.9001 ✅ |
| Nickname rate | ~0.30 | 0.2999 ✅ |
| Typo rate (non-canonical rows in multi-member clusters) | ~0.14–0.20 | 0.1410 ✅ |

### 2. Unified search UI (`records/views.py`, templates)

Replaced tab-based search with **checkbox-based mode selection**. Each algorithm is independently toggleable:

| Mode            | Description                          | Default    |
| --------------- | ------------------------------------ | ---------- |
| Exact prefix    | B-tree `istartswith`                 | ✅ checked |
| Soundex         | Phonetic code match                  | ❌         |
| Levenshtein     | Edit distance ≤ 2 (precision filter) | ❌         |
| Daitch-Mokotoff | Phonetic codes (Slavic/Germanic)     | ❌         |
| Trigram         | pg_trgm similarity                   | ❌         |
| Legacy LIKE     | Unindexed substring                  | ❌         |

**Key behaviors:**

- Single SQL query with Q objects (OR across modes, AND within mode for both names)
- Trigram via separate query merged in Python (ORDER BY `<->` can't be OR-ed)
- Levenshtein as precision filter (AND) applied on top of other modes, not an independent OR
- Both first_name AND last_name must match for a mode to qualify (when both provided)
- Results annotated with `match_source` bitmask (SQL-side CASE expressions)
- Match badges in UI: Exact Prefix, Soundex, DM, Trigram, LIKE

### 3. Model changes (`records/models.py`)

- `search_unified(modes, first_name, last_name, date_of_birth)` — new unified search
- `search_exact()`, `search_phonetic()`, `search_dm()`, `search_trigram()`, `search_legacy()` -- kept as QuerySet-level reference methods (covered by tests; the web UI and EXPLAIN endpoint both build their queries from the shared `build_unified_filter()` / `_explain_queryset_for()` construction)
- `person_id` (UUID, indexed) links cluster variants
- Functional indexes: SOUNDEX B-tree, DAITCH_MOKOTOFF GIN, pg_trgm GiST, text_pattern_ops

### 4. Tests (`tests/`)

**179 tests passing** across:

- `tests/records/test_views.py` (82): unified search modes, DOB filtering, Levenshtein as filter, match source annotation, badges, EXPLAIN endpoint, tooltips, HTML rendering
- `tests/records/test_seed_data.py` (45): name pool, Zipf sampling, typo injection, cluster expansion, bulk insert, edit distance
- `tests/records/test_models.py` (38): model creation, search methods, DOB filtering, hero typo case
- `tests/records/test_phonetics.py` (8): nickname map, resolve_variants
- `tests/test_settings.py` (6)

```bash
.venv/bin/pytest tests/ -q  # 179 passed
```

### 5. UI Features (committed with the unified search)

- **Checkbox grouping**: Exact (green), Phonetic (blue/purple), Fuzzy (amber/indigo)
- **SQL tooltips**: Each active checkbox shows the SQL snippet it applies
- **Phonetic code tooltips**: Soundex/DM badges in results show computed codes to "prove" matches
- **EXPLAIN button**: Opens query plan in new tab
- **SQL query display**: Collapsible section showing executed queries
- **Levenshtein as filter**: Changed from OR to AND — precision filter on top of other modes
- **DOB clear button**: Client-side toggle when DOB is set/cleared
- **Help page**: `/help/` with descriptions of each filter and recommended combinations

## File Map

| File                                             | Purpose                                                              |
| ------------------------------------------------ | -------------------------------------------------------------------- |
| `records/models.py`                              | Person model, QuerySet search methods, functional indexes            |
| `records/views.py`                               | Unified search view, match_source annotation, mode config, help page |
| `records/expressions.py`                         | ORM wrappers for Levenshtein, Soundex, DM                            |
| `records/phonetics.py`                           | NICKNAME_MAP, resolve_variants()                                     |
| `records/management/commands/seed_data.py`       | Polars-based data generation                                         |
| `records/templates/records/home.html`            | Search form with checkboxes                                          |
| `records/templates/records/_search_results.html` | Results table with match badges                                      |
| `records/templates/records/help.html`            | Help page describing search modes                                    |
| `records/templates/records/explain.html`         | EXPLAIN ANALYZE query plan viewer                                    |
| `test_cases.txt`                                 | Sample search cases from 54M seed                                    |
| `54M_status.md`                                  | Detailed 54M verification results                                    |

## Environment

- Python 3.14, Django 5.2, Polars 1.43, psycopg 3.3
- `uv sync` to install deps (not `pip install`)
- PostgreSQL with `fuzzystrmatch` and `pg_trgm` extensions
- `.env`: `DATABASE_URL=psql://postgres:postgres@localhost:5432/fuzzy_demo`

## Resolved Issues

- **Duplicate clusters bug:** Was stale data from multiple seed runs without clean `--flush`. Fresh 54M seed: zero duplicate large clusters.
- **OOM at 54M:** Fixed by streaming insert (generate → expand → insert per batch, no accumulation).
- **`--count` semantics:** Fixed to mean final row count (not identities).
- **Trigram not working:** Fixed early-return guard that blocked trigram-only searches.
- **Trigram slow:** Restored `<->` KNN index scan for single-name, chained ORDER BY for dual-name.
- **Levenshtein auto-pairing:** Removed — Levenshtein is now a precision filter (AND), not an independent mode.

## Known Limitations

- **Nickname expansion dropped** in unified search. The legacy `search_phonetic()`/`search_dm()` QuerySet methods still expand the query's nickname variants in their phonetic pre-filter (with the documented ≤2-edit-distance limitation); the unified search uses query names directly without `resolve_variants()`.
- **Trigram dual-name limitation:** Can only ORDER BY one column's `<->` distance via index. When both names provided, chains `ORDER BY last_name <-> b, first_name <-> a` — results ranked almost entirely by last-name closeness.
- **Legacy LIKE is intentionally slow** (unindexed). Included for comparison only.

## Running the Demo

```bash
uv sync
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
# Open http://localhost:8000
```

The stage DB already contains the 54M seeded records (migrations 0001–0007 applied). For a fresh local database, seed first: `.venv/bin/python manage.py seed_data --count 100000 --flush --seed 42 --as-of 2026-01-01`. The marimo notebook (`uv run marimo edit demo.py`) drives the same `search_unified()` API.

Search for "John Smith" to see exact prefix matches. Enable Soundex + Levenshtein checkboxes to see fuzzy matches with typo tolerance.

## Next Steps

RECS-2026-08-14 review items implemented and committed on this branch: B1/B13/B14 (Levenshtein-only semantics), B2 (XSS), B3 option b (honest deterministic generator), B4 (onboarding), B5 (help-page sampling), B6/B7 (trigram page slots, DOB-only results), B8 (EXPLAIN honesty), B9 (DOB clear), B10 (seed batch cap, TRUNCATE flush), B11 (real index migration 0007), B17 (EXPLAIN verification, recorded in `54M_status.md`), P1-11 (test gaps closed, 179 tests), P1-12 (honest nickname claims).

Remaining (deliberately open):

1. **CI workflow** (RECS P2-18): run the suite against a small seed (`seed_data --count 10000` in a service container with `fuzzystrmatch`/`pg_trgm`) — never re-seed 54M in CI.
2. **Deliberate re-seed** (B3 follow-up): re-seed the stage DB with the rewritten generator and re-measure distributions; until then `54M_status.md` remains the provenance of record.
3. **Nickname tolerance decision** (P1-12): currently documented as _not_ supported by the unified search; fixing it (variant-aware filtering) is a product decision.
4. **Talk prep**: rehearse against the live UI; use the B17 EXPLAIN plans in `54M_status.md` as plan-walkthrough material; follow the RECS talk-script suggestions (empirical distribution story, honest typo-rate wording).

## Notes

- All changes are **committed on this branch** (unified search UI in `abca622`, then the RECS-2026-08-14 fix commits through `0b06951`); working tree clean.
- 179 tests passing (`uv run pytest tests/ -q`).
- Help page at `/help/` shows real sampled examples (TABLESAMPLE + single GROUP BY, cached, stampede-proof per B5) with a refresh button.
- Migrations 0001–0007 are applied on the live DB: 0005 removed the stored phonetic token columns and made DOB required, 0006 added `person_id`, 0007 is the real phonetic-index migration (reconciling 0005's RunSQL indexes so `makemigrations --check` passes).
