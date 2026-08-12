# Handoff: Fuzzy Name Search at Scale

## Status: ✅ Complete — 54M records seeded, unified search UI implemented, 68 tests passing

## What's Done

### 1. Database: 54M person records

`records/management/commands/seed_data.py` generates realistic person records with:
- Dirichlet-distributed name frequency (heavy tail: some names appear once, others hundreds of times)
- Cluster model: 80% singleton identities, 20% heavy-tailed clusters (Pareto, clipped to 2–80)
- 20% typo injection within clusters (canonical row never typo'd)
- 90% middle names, 30% nicknames

**Streaming insert** (fixed OOM): batches are generated, expanded, and inserted immediately instead of accumulating all rows in memory. Peak memory ~1.4 GB (was 14 GB OOM).

```bash
.venv/bin/python manage.py seed_data --count 54000000 --flush --seed 42
# ~98 minutes, no OOM on 40 GB RAM
```

Verified distributions (`54M_status.md`):
| Metric | Expected | Actual |
|--------|----------|--------|
| Row count | 54,000,000 | 54,000,000 ✅ |
| Max cluster size | ≤ 80 | 80 ✅ |
| Singleton identities | ~80% | 80.00% ✅ |
| Middle name rate | ~0.90 | 0.9001 ✅ |
| Nickname rate | ~0.30 | 0.2999 ✅ |
| Typo rate (multi-member clusters) | ~0.14–0.20 | 0.1410 ✅ |

### 2. Unified search UI (`records/views.py`, templates)

Replaced tab-based search with **checkbox-based mode selection**. Each algorithm is independently toggleable:

| Mode | Description | Default |
|------|-------------|---------|
| Exact prefix | B-tree `istartswith` | ✅ checked |
| Soundex | Phonetic code match | ❌ |
| Levenshtein | Edit distance ≤ 2 (precision filter) | ❌ |
| Daitch-Mokotoff | Phonetic codes (Slavic/Germanic) | ❌ |
| Trigram | pg_trgm similarity | ❌ |
| Legacy LIKE | Unindexed substring | ❌ |

**Key behaviors:**
- Single SQL query with Q objects (OR across modes, AND within mode for both names)
- Trigram via separate query merged in Python (ORDER BY `<->` can't be OR-ed)
- Levenshtein as precision filter (AND) applied on top of other modes, not an independent OR
- Both first_name AND last_name must match for a mode to qualify (when both provided)
- Results annotated with `match_source` bitmask (SQL-side CASE expressions)
- Match badges in UI: Exact Prefix, Soundex, DM, Trigram, LIKE

### 3. Model changes (`records/models.py`)

- `search_unified(modes, first_name, last_name, date_of_birth)` — new unified search
- `search_exact()`, `search_phonetic()`, `search_dm()`, `search_trigram()`, `search_legacy()` — kept for EXPLAIN endpoint
- `person_id` (UUID, indexed) links cluster variants
- Functional indexes: SOUNDEX B-tree, DAITCH_MOKOTOFF GIN, pg_trgm GiST, text_pattern_ops

### 4. Tests (`tests/records/`)

**68 tests passing** across:
- `test_seed_data.py` (23): name pool, Dirichlet sampling, typo injection, cluster expansion, bulk insert, edit distance
- `test_views.py` (21): unified search modes, DOB filtering, Levenshtein as filter, match source annotation, badge labels
- `test_phonetics.py` (12): nickname map, resolve_variants
- `test_models.py` (12): model creation, search methods, DOB filtering

```bash
.venv/bin/pytest tests/records/ -v  # 68 passed
```

### 5. UI Enhancements (today's session)

- **Checkbox grouping**: Exact (green), Phonetic (blue/purple), Fuzzy (amber/indigo)
- **SQL tooltips**: Each active checkbox shows the SQL snippet it applies
- **Phonetic code tooltips**: Soundex/DM badges in results show computed codes to "prove" matches
- **EXPLAIN button**: Opens query plan in new tab
- **SQL query display**: Collapsible section showing executed queries
- **Levenshtein as filter**: Changed from OR to AND — precision filter on top of other modes
- **DOB clear button**: Client-side toggle when DOB is set/cleared
- **Help page**: `/help/` with descriptions of each filter and recommended combinations

## File Map

| File | Purpose |
|------|---------|
| `records/models.py` | Person model, QuerySet search methods, functional indexes |
| `records/views.py` | Unified search view, match_source annotation, mode config, help page |
| `records/expressions.py` | ORM wrappers for Levenshtein, Soundex, DM |
| `records/phonetics.py` | NICKNAME_MAP, resolve_variants() |
| `records/management/commands/seed_data.py` | Polars-based data generation |
| `records/templates/records/home.html` | Search form with checkboxes |
| `records/templates/records/_search_results.html` | Results table with match badges |
| `records/templates/records/help.html` | Help page describing search modes |
| `records/templates/records/explain.html` | EXPLAIN ANALYZE query plan viewer |
| `test_cases.txt` | Sample search cases from 54M seed |
| `54M_status.md` | Detailed 54M verification results |

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

- **Nickname expansion dropped** in unified search (kept in legacy `search_phonetic()`/`search_dm()` for EXPLAIN endpoint). The unified search uses query names directly without `resolve_variants()`.
- **Trigram dual-name limitation:** Can only ORDER BY one column's `<->` distance via index. When both names provided, chains `ORDER BY last_name <-> b, first_name <-> a` — results ranked almost entirely by last-name closeness.
- **Legacy LIKE is intentionally slow** (unindexed). Included for comparison only.

## Running the Demo

```bash
uv sync
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
# Open http://localhost:8000
```

Search for "John Smith" to see exact prefix matches. Enable Soundex + Levenshtein checkboxes to see fuzzy matches with typo tolerance.

## Next Steps

### 1. Update/add tests for new features (unstaged changes)

The following features were added today and need test coverage:

- **Checkbox grouping**: Verify Exact/Phonetic/Fuzzy groups render correctly
- **SQL tooltips on checkboxes**: Verify tooltips show correct SQL snippets for each mode
- **Phonetic code tooltips on result badges**: Verify Soundex/DM badges show computed codes
- **EXPLAIN button**: Verify link opens correct URL with current search params
- **SQL query display**: Verify collapsible section shows executed queries
- **Levenshtein as filter**: Verify Levenshtein narrows results from other modes (not independent OR)
- **DOB clear button**: Verify client-side toggle works when DOB is set/cleared
- **Help page**: Verify `/help/` renders with correct content
- **Help page dynamic examples**: Verify examples render with random DOB and typos

### 2. Clean up code for commit

- Remove any debug print statements
- Ensure consistent formatting (black/isort)
- Verify no leftover TODO/CHANGEME comments
- Check for unused imports
- Verify all template variables are properly escaped

### 3. Draft commit message

```
feat: unified search UI with checkbox-based mode selection

- Replace tab-based search with checkbox-based mode selection
- Group checkboxes by category: Exact (green), Phonetic (blue/purple), Fuzzy (amber/indigo)
- Add SQL tooltips to active checkboxes showing the SQL snippet each mode applies
- Add phonetic code tooltips to Soundex/DM result badges to "prove" matches
- Add EXPLAIN button to open query plan in new tab
- Add collapsible SQL query display above results
- Change Levenshtein from OR mode to precision filter (AND) applied on top of other modes
- Add DOB clear button with client-side toggle
- Add help page at /help/ with descriptions of each filter
- Fix trigram-only search (early-return guard blocked it)
- Restore trigram KNN index scan for single-name searches
- Remove Levenshtein auto-pairing with Soundex/DM

Breaking changes:
- Levenshtein is now a filter, not an independent mode
- No more auto-pairing Levenshtein with Soundex when selected alone
```

### 4. Update documentation

- Update `HANDOFF.md` with today's changes
- Update `README.md` if it exists
- Verify `54M_status.md` is still accurate
- Add any new environment variables to docs

### 5. Pre-commit checks

- Run `black` on all Python files
- Run `isort` on all Python files
- Run `flake8` or `ruff` on all Python files
- Verify no trailing whitespace
- Verify all files end with newline

## Notes

- All changes are unstaged and uncommitted
- 68 tests passing (down from 69 — removed Levenshtein auto-pairing test)
- Help page at `/help/` is static content (no dynamic examples yet)
- No database migrations needed (all changes are view/template level)