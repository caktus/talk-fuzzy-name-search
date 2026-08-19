# demo.py -- Fuzzy Name Search at 54M Scale
# Run with: marimo edit demo.py
# The web UI (manage.py runserver -> http://localhost:8000) is the full
# interactive experience; this notebook drives the same search_unified() API.

import marimo

__generated_with = "0.23.13"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import pandas as pd

    return mo, pd


@app.cell
def _(mo):
    mo.md(r"""
    # Fuzzy Name Search at 54M Scale

    **Django + PostgreSQL strategies for fuzzy name matching across 54 million records.**

    This interactive demo searches for human names in a 54-million-row dataset --
    handling the typos and spelling variations that break traditional `LIKE` queries.

    **Problem:** A user searches for "Smith, John" but the database contains
    "Smyth, Jon" or "Smithe, Johnny." Standard `LIKE` queries are too strict,
    full-text search doesn't handle name nuances well, and unindexed fuzzy
    matching won't scale.

    **Solution:** One unified API -- `Person.objects.search_unified(modes, first, last, dob)` --
    with independently toggleable modes: exact prefix, legacy LIKE, Soundex,
    Daitch-Mokotoff, and trigram (pg_trgm KNN). Levenshtein (edit distance <= 2)
    is not a standalone mode; it is a precision filter applied on top of the base
    modes. Every mode is backed by a PostgreSQL index (functional B-tree/GIN for
    phonetics, GiST for trigrams, `text_pattern_ops` for prefix), and each search
    returns one 100-row page annotated with the mode(s) that matched each row.

    **The web UI** (`uv run python manage.py runserver` -> http://localhost:8000)
    is the full experience: mode checkboxes, match badges, SQL tooltips, and an
    EXPLAIN viewer. This notebook drives the same `search_unified()` calls.
    """)
    return


@app.cell
def _():
    import os
    import sys
    import time
    from pathlib import Path

    from asgiref.sync import sync_to_async

    sys.path.insert(0, str(Path(__file__).parent))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fuzzy_demo.settings")

    import django
    from django.apps import apps

    if not apps.ready:
        django.setup()

    from records.models import Person

    # Match-source bitmask -- same values as the web UI (records/views.py MATCH_BITS)
    MATCH_BITS = {"prefix": 1, "legacy": 2, "soundex": 4, "dm": 16, "trigram": 32}

    def match_labels(source):
        if not source:
            return "DOB only"
        return ", ".join(name for name, bit in MATCH_BITS.items() if source & bit)

    def persons_to_df(rows, fields=None):
        """List of Person objects from search_unified() -> DataFrame."""
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([{k: v for k, v in r.__dict__.items() if k != "_state"} for r in rows])
        if "_match_source" in df.columns:
            df["matched"] = df["_match_source"].map(match_labels)
            df = df.drop(columns=["_match_source"])
        if fields:
            df = df[fields]
        return df

    class MillisecondTimer:
        def __init__(self, label=None):
            self.label = label

        def __enter__(self):
            self.start = time.perf_counter()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.end = time.perf_counter()
            # Multiply by 1000 to get milliseconds
            self.elapsed_ms = (self.end - self.start) * 1000
            print(f"Elapsed time{' [' + self.label + ']' if self.label else ''}: {self.elapsed_ms:.2f} ms")

    return MillisecondTimer, Person, MATCH_BITS, persons_to_df, sync_to_async


@app.cell
async def _(MillisecondTimer, Person, mo):
    with MillisecondTimer():
        total = await Person.objects.acount()
    mo.md(f"**Database:** {total:,} person records loaded")
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Section 1: The Typo Problem at Scale

    Why does name search fail? Let's see it in action on the full 54M dataset.
    """)
    return


@app.cell
async def _(MillisecondTimer, Person, mo, persons_to_df, sync_to_async):
    # The user typed "Smyth" -- a typo for "Smith".
    with MillisecondTimer("search_unified(prefix)"):
        prefix_rows = await sync_to_async(
            lambda: Person.objects.search_unified(["prefix"], "John", "Smyth"),
            thread_sensitive=True,
        )()

    with MillisecondTimer("search_unified(legacy)"):
        legacy_rows = await sync_to_async(
            lambda: Person.objects.search_unified(["legacy"], "John", "Smyth"),
            thread_sensitive=True,
        )()

    with MillisecondTimer("search_unified(soundex + levenshtein)"):
        fuzzy_rows = await sync_to_async(
            lambda: Person.objects.search_unified(["soundex", "levenshtein"], "John", "Smyth"),
            thread_sensitive=True,
        )()

    fuzzy_df = persons_to_df(fuzzy_rows)
    n_smith = int((fuzzy_df["last_name"].astype(str).str.upper() == "SMITH").sum()) if len(fuzzy_df) else 0

    mo.md(f"""
    All three results below are single `search_unified()` calls -- the exact
    API the web UI makes -- for the query **John Smyth**:

    | Modes enabled | Rows on the 100-row page |
    |---|---|
    | `prefix` -- B-tree `istartswith` | **{len(prefix_rows)}** |
    | `legacy` -- unindexed `LIKE '%Smyth%'` | **{len(legacy_rows)}** |
    | `soundex` + `levenshtein` | **{len(fuzzy_rows)}** ({n_smith} of them SMITH) |

    `SOUNDEX('SMYTH') == SOUNDEX('SMITH') == S530`, so the phonetic mode finds
    the Smith family even with the typo. But S530 also covers SANTAA, SANTO and
    friends -- which is why Levenshtein (edit distance <= 2 against the names as
    typed) runs as an **AND precision filter** on top of the phonetic pre-filter,
    not as a standalone mode.

    The page is ordered by (last_name, first_name) and capped at 100 rows --
    exactly the page size the UI renders.
    """)
    return fuzzy_df


@app.cell
def _(fuzzy_df):
    # The full 100-row page (ordered last_name, first_name) -- the same page
    # the web UI renders for this query.
    fuzzy_df
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Section 2: Disambiguating with a Date of Birth

    54M rows means thousands of John Smiths. The phonetic page above is full of
    *Smith-family* rows, but the person you want is one of many. The DOB filter
    (applied inside `search_unified`, shared by every mode) narrows the page to
    the actual cluster.
    """)
    return


@app.cell
async def _(MillisecondTimer, Person, mo, persons_to_df, sync_to_async):
    # Earliest JOHN SMITH DOB in the table -- stable anchor for the demo.
    anchor_dob = await sync_to_async(
        lambda: (
            Person.objects.filter(first_name="JOHN", last_name="SMITH")
            .order_by("date_of_birth")
            .values_list("date_of_birth", flat=True)
            .first()
        ),
        thread_sensitive=True,
    )()

    with MillisecondTimer(f"search_unified(soundex + levenshtein, DOB {anchor_dob})"):
        anchor_rows = await sync_to_async(
            lambda: Person.objects.search_unified(["soundex", "levenshtein"], "John", "Smyth", anchor_dob),
            thread_sensitive=True,
        )()

    mo.md(f"""
    `search_unified(["soundex", "levenshtein"], "John", "Smyth", {anchor_dob})` --
    the typo'd query plus the person's real DOB:
    """)
    return persons_to_df(anchor_rows)


@app.cell
def _(mo):
    mo.md(r"""
    ## Section 3: Unified Search -- Your Names

    The same search the web UI runs: pick any combination of base modes
    (OR-ed into one query), optionally add the Levenshtein precision filter
    (AND-ed on top), and optionally pin a DOB. One 100-row page comes back,
    each row labeled with the mode(s) that matched it.
    """)
    return


@app.cell
def _(mo):
    first_name_input = mo.ui.text(
        placeholder="First name (e.g., John, Jonh)",
        label="First name",
        value="John",
    )
    last_name_input = mo.ui.text(
        placeholder="Last name (e.g., Smith, Smyth)",
        label="Last name",
        value="Smyth",
    )
    dob_input = mo.ui.text(
        placeholder="YYYY-MM-DD (optional)",
        label="Date of birth",
        value="",
    )
    mo.hstack([first_name_input, last_name_input, dob_input])
    return first_name_input, last_name_input, dob_input


@app.cell
def _(mo):
    mode_checks = {
        name: mo.ui.checkbox(value, label=label)
        for name, label, value in [
            ("prefix", "Exact prefix", True),
            ("soundex", "Soundex", True),
            ("levenshtein", "Levenshtein (precision filter)", True),
            ("dm", "Daitch-Mokotoff", False),
            ("trigram", "Trigram (KNN)", False),
            ("legacy", "Legacy LIKE (slow, for comparison)", False),
        ]
    }
    mo.vstack(list(mode_checks.values()))
    return mode_checks


@app.cell
async def _(
    MillisecondTimer,
    Person,
    dob_input,
    first_name_input,
    last_name_input,
    mo,
    mode_checks,
    persons_to_df,
    sync_to_async,
):
    from datetime import date

    q_first = first_name_input.value.strip()
    q_last = last_name_input.value.strip()
    q_dob_raw = dob_input.value.strip()

    if not q_first and not q_last and not q_dob_raw:
        mo.stop(mo.md("Enter a name (or a DOB) above to run the search."))

    q_dob = None
    if q_dob_raw:
        try:
            q_dob = date.fromisoformat(q_dob_raw)
        except ValueError:
            mo.stop(mo.md("Date of birth must be YYYY-MM-DD (or empty)."))

    q_modes = [name for name, cb in mode_checks.items() if cb.value]
    if q_modes == ["levenshtein"]:
        mo.stop(
            mo.md(
                "Levenshtein is a precision filter, not a base mode -- enable at "
                "least one of prefix / soundex / dm / trigram / legacy. (The web "
                "UI disables the Levenshtein checkbox in the same situation.)"
            )
        )

    with MillisecondTimer(f"search_unified({q_modes})"):
        unified_rows = await sync_to_async(
            lambda: Person.objects.search_unified(q_modes, q_first, q_last, q_dob),
            thread_sensitive=True,
        )()

    mo.md(f"**{len(unified_rows)}** rows on the 100-row page (modes: {', '.join(q_modes) or 'none'}).")
    return persons_to_df(
        unified_rows,
        fields=["first_name", "last_name", "middle_name", "date_of_birth", "matched"],
    )


@app.cell
def _(mo):
    mo.md(r"""
    ## Section 4: Trigram Similarity with GiST KNN

    PostgreSQL's `pg_trgm` extension provides trigram similarity matching.
    A GiST index with `gist_trgm_ops` supports the `<->` distance operator,
    which returns rows already ordered by similarity -- an index-ordered
    "nearest neighbour" (KNN) scan. This is exactly what the `trigram` mode of
    `search_unified()` runs (via `trigram_ordered()`).

    - **Similarity cutoff** -- `similarity(name, query) >= 0.3` per provided
      name (TRIGRAM_SIMILARITY_CUTOFF, pg_trgm's own default threshold) cuts
      the noise a bare KNN top-100 surfaces for a rare spelling
    - **Index-ordered** -- no scan-then-sort over a large candidate set

    The trade-offs (measured on 54M rows; EXPLAIN plans in `54M_status.md`):
    GiST indexes are large (multiple GB) and slow to build, and the scan only
    stops once 100 neighbours are filled -- milliseconds for a common name
    ("Smith"), but seconds for a rarer target like "Smyth", because its
    100th-closest trigram match is much farther away.
    """)
    return


@app.cell
def _(mo):
    name_query = mo.ui.text(
        placeholder="Last name to match (e.g., Smith, Smyth)",
        label="Last name (KNN nearest match)",
        value="Smith",
    )
    name_query
    return (name_query,)


@app.cell
async def _(MillisecondTimer, Person, name_query, pd, sync_to_async):
    from django.db.models.expressions import RawSQL

    target = name_query.value.strip() or "Smith"

    def run_knn_search(name):
        # GiST `<->` distance ordering: an index-ordered KNN scan that
        # returns the closest names first, cut at similarity() >= 0.3, with
        # no scan-then-sort over a large candidate set. Same SQL shape the
        # trigram mode of search_unified() runs (single-name form).
        from records.models import TRIGRAM_SIMILARITY_CUTOFF

        qs = (
            Person.objects.annotate(
                similarity=RawSQL("similarity(last_name, %s)", [name]),
                distance=RawSQL("last_name <-> %s", [name]),
            )
            .filter(similarity__gte=TRIGRAM_SIMILARITY_CUTOFF)
            .order_by("distance")[:100]
        )
        return list(qs.values("first_name", "last_name", "similarity"))

    with MillisecondTimer():
        # thread_sensitive=True keeps everything on the same DB connection.
        knn_rows = await sync_to_async(run_knn_search, thread_sensitive=True)(target)

    tgm_results = pd.DataFrame(knn_rows)

    # Change the name above and watch the nearest matches update.
    # For "Smyth", notice that "SMTH" ranks closer than "SMYTH" -- trigrams
    # measure character overlap, not phonetics.
    tgm_results
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Section 5: Levenshtein as a Precision Filter

    The key performance trick: `levenshtein_less_equal(a, b, max_distance)`
    terminates early if the distance exceeds `max_distance`. This prevents
    wasteful CPU cycles on obviously-non-matching names.

    - `levenshtein("Smith", "Smyth")` → computes full distance = 2
    - `levenshtein_less_equal("Smith", "Johnson", 2)` → exits early, returns > 2

    In `search_unified()` this runs as an **AND filter on top of the base
    modes** (never standalone): the phonetic base modes (functional `SOUNDEX()`
    B-tree / `DAITCH_MOKOTOFF()` GIN indexes) do the broad filtering, and
    Levenshtein ≤ 2 against the names as typed keeps the 100-row page precise --
    even at 54 million records.

    One honest limit: the filter is measured against the query as typed, so it
    refines, e.g., "Smyth" → "Smith", but it does **not** expand nicknames
    ("Bill" → "William" is distance 4 and is not a match).
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Section 6: Live Before vs. After Benchmark

    Enter a name below and compare the strict `legacy` LIKE mode against the
    fuzzy `soundex` + `levenshtein` modes -- both are plain `search_unified()`
    calls across **54 million records**.
    """)
    return


@app.cell
def _(mo):
    bench_first = mo.ui.text(
        placeholder="First name (e.g., John, Jonh)",
        label="First name",
        value="John",
    )
    bench_last = mo.ui.text(
        placeholder="Last name (e.g., Smith, Smyth)",
        label="Last name (try the typo 'Smyth')",
        value="Smyth",
    )
    mo.hstack([bench_first, bench_last])
    return bench_first, bench_last


@app.cell
async def _(MillisecondTimer, Person, bench_first, bench_last, mo, persons_to_df, sync_to_async):
    b1_first = bench_first.value.strip()
    b1_last = bench_last.value.strip()

    if b1_first and b1_last:
        with MillisecondTimer("search_unified(legacy)"):
            bench_legacy_rows = await sync_to_async(
                lambda: Person.objects.search_unified(["legacy"], b1_first, b1_last),
                thread_sensitive=True,
            )()
    else:
        bench_legacy_rows = []

    mo.md(f"**Before** -- strict `legacy` LIKE: **{len(bench_legacy_rows)}** rows on the page.")
    return persons_to_df(
        bench_legacy_rows,
        fields=["first_name", "last_name", "middle_name", "date_of_birth"],
    )


@app.cell
async def _(MillisecondTimer, Person, bench_first, bench_last, mo, persons_to_df, sync_to_async):
    b2_first = bench_first.value.strip()
    b2_last = bench_last.value.strip()

    if b2_first and b2_last:
        with MillisecondTimer("search_unified(soundex + levenshtein)"):
            bench_fuzzy_rows = await sync_to_async(
                lambda: Person.objects.search_unified(["soundex", "levenshtein"], b2_first, b2_last),
                thread_sensitive=True,
            )()
    else:
        bench_fuzzy_rows = []

    mo.md(f"**After** -- `soundex` + `levenshtein`: **{len(bench_fuzzy_rows)}** rows on the page.")
    return persons_to_df(
        bench_fuzzy_rows,
        fields=["first_name", "last_name", "middle_name", "date_of_birth", "matched"],
    )


@app.cell
def _(mo):
    mo.md(r"""
    ---

    ## Summary

    One API, `search_unified(modes, first_name, last_name, date_of_birth)`,
    drives both this notebook and the web UI:

    1. **Base modes OR-ed into one query** -- exact prefix, legacy LIKE,
       Soundex, Daitch-Mokotoff. Phonetic matching applies PostgreSQL's
       `SOUNDEX()` / `DAITCH_MOKOTOFF()` directly in SQL via functional
       indexes (B-tree for Soundex equality, GIN for DM array overlap) -- no
       stored token columns.
    2. **Levenshtein as a precision filter** -- `levenshtein_less_equal(..., 2)`
       with early exit, AND-ed on top of the base modes (never standalone).
    3. **pg_trgm GiST KNN** -- the trigram mode orders by `<->` distance via
       index scan, cut at `similarity() >= 0.3` per provided name.
    4. **`text_pattern_ops` B-tree** -- fast case-insensitive type-ahead for
       the prefix mode.
    5. **One 100-row page** per search, each row annotated with the mode(s)
       that matched it (rendered as badges in the UI).

    That combination keeps searches fast and forgiving at **54 million records**.

    **Web UI** (mode checkboxes, match badges, SQL tooltips, EXPLAIN plans):
    `uv run python manage.py runserver` → http://localhost:8000

    **Repository:** [talk-fuzzy-name-search](https://github.com/caktus/talk-fuzzy-name-search)
    """)
    return


if __name__ == "__main__":
    app.run()
