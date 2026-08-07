# demo.py -- Fuzzy Name Search at 50M Scale
# Run with: marimo edit demo.py

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
    # Fuzzy Name Search at 50M Scale

    **Django + PostgreSQL strategies for fuzzy name matching across 50 million records.**

    This interactive demo shows how to search for human names in massive datasets -- handling
    typos, nicknames, and spelling variations that break traditional `LIKE` queries.

    **Problem:** A user searches for "Smith, John" but the database contains "Smyth, Jon"
    or "Smithe, Johnny." Standard `LIKE` queries are too strict, full-text search doesn't
    handle name nuances well, and unindexed fuzzy matching won't scale.

    **Solution:** A dual-layer pipeline using:
    1. **Phonetic Matching** (Soundex, Daitch-Mokotoff) -- broad filter via phonetic-array GIN indexes
    2. **Levenshtein Distance** -- precision filter with early-exit optimization

    Navigate the sections below to explore each technique.
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Section 1: Grounding the Problem

    Why does name search fail? Let's see it in action at scale.
    """)
    return


@app.cell
def _(pd):
    import os
    import sys
    import time

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fuzzy_demo.settings")

    import django
    from django.apps import apps

    if not apps.ready:
        django.setup()

    from records.models import Person

    async def qs_to_df(qs, fields=None):
        rows = [r async for r in qs]
        if not rows:
            return pd.DataFrame()
        # .values() / .values_list() return dicts; model instances use __dict__
        if isinstance(rows[0], dict):
            df = pd.DataFrame(rows)
        else:
            df = pd.DataFrame([{k: v for k, v in r.__dict__.items() if k != "_state"} for r in rows])
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

    return MillisecondTimer, Person, qs_to_df


@app.cell
async def _(MillisecondTimer, Person, mo):
    with MillisecondTimer():
        total = await Person.objects.acount()
    mo.md(f"**Database:** {total:,} person records loaded")
    return


@app.cell
async def _(MillisecondTimer, Person, qs_to_df):
    with MillisecondTimer():
        results = await qs_to_df(Person.objects.filter(last_name__icontains="smith")[:20])

    results
    return


@app.cell
async def _(MillisecondTimer, Person, mo, qs_to_df):
    with MillisecondTimer("istartswith"):
        like_results = await qs_to_df(Person.objects.filter(last_name__istartswith="Smyth")[:20])

    with MillisecondTimer("search_phonetic"):
        phonetic_results = await qs_to_df(Person.objects.search_phonetic("John", "Smyth")[:20])

    mo.md(f"""
    ### The Typo Problem

    | Search Method | Query | Results |
    |---|---|---|
    | `LIKE 'Smyth%'` | Exact spelling required | **{len(like_results)}** |
    | Phonetic + Levenshtein | Catches "Smyth" → "Smith" | **{len(phonetic_results)}** |

    A single typo (`Smyth` instead of `Smith`) causes zero results with `LIKE`,
    but the phonetic approach still finds matches.
    """)
    return like_results, phonetic_results


@app.cell
def _(like_results):
    # __icontains ("LIKE") results include only "SMYTH"
    like_results
    return


@app.cell
def _(phonetic_results):
    # Phonetic results include "SMITH" (query was limited to 10 results)
    phonetic_results
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Section 2: Trigram Similarity with GiST KNN

    PostgreSQL's `pg_trgm` extension provides trigram similarity matching.
    A GiST index with `gist_trgm_ops` supports the `<->` distance operator,
    which returns rows already ordered by similarity -- an index-ordered
    "nearest neighbour" (KNN) scan that stops at `LIMIT`.

    - **No threshold to tune** -- `ORDER BY last_name <-> 'Smyth' LIMIT 20`
      always returns the 20 closest names, however loose the matches
    - **Index-ordered** -- no scan-then-sort over a large candidate set

    The trade-offs (measured on 50M rows): GiST indexes are large
    (multiple GB) and slow to build, and cold-cache latency can spike
    because trigram signatures are lossy and prune poorly.
    """)
    return


@app.cell
def _(mo):
    name_query = mo.ui.text(
        placeholder="Last name to match (e.g., Smyth)",
        label="Last name (KNN nearest match)",
        value="Smyth",
    )
    name_query
    return (name_query,)


@app.cell
async def _(MillisecondTimer, Person, name_query, pd):
    from asgiref.sync import sync_to_async
    from django.db.models.expressions import RawSQL

    target = name_query.value.strip() or "Smyth"

    def run_knn_search(name):
        # GiST `<->` distance ordering: an index-ordered KNN scan that
        # returns the closest names first and stops at LIMIT -- no threshold
        # and no scan-then-sort over a large candidate set.
        qs = Person.objects.annotate(
            similarity=RawSQL("similarity(last_name, %s)", [name]),
            distance=RawSQL("last_name <-> %s", [name]),
        ).order_by("distance")[:200]
        rows = list(qs.values("first_name", "last_name", "similarity"))
        return str(qs.query), rows

    with MillisecondTimer():
        # thread_sensitive=True keeps everything on the same DB connection.
        query_sql, rows = await sync_to_async(run_knn_search, thread_sensitive=True)(target)

    tgm_results = pd.DataFrame(rows)

    # Change the name above to watch the nearest matches update.
    # For "Smyth", notice that "Smth" is more similar than "Smith"
    tgm_results
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Section 3: Phonetic + Levenshtein Deep Dive

    The core of our approach: **phonetic algorithms** for broad matching,
    **Levenshtein distance** for precision filtering.
    """)
    return


@app.cell
def _():
    from records.phonetics import (
        dm_soundex_tokens,
        resolve_variants,
        soundex_tokens,
    )

    return dm_soundex_tokens, resolve_variants, soundex_tokens


@app.cell
def _(mo):
    name_input = mo.ui.text(
        placeholder="Type a name (e.g., William, Bob, Smith)",
        label="Name to analyze",
        value="William",
    )
    name_input
    return (name_input,)


@app.cell
def _(dm_soundex_tokens, mo, name_input, resolve_variants, soundex_tokens):
    name = name_input.value.strip()

    mo.stop(not name, mo.md("Enter a name above to see its phonetic analysis."))

    variants = resolve_variants(name)
    soundex = soundex_tokens(name)
    dm_tokens = dm_soundex_tokens(name)

    mo.md(f"""
    ### Phonetic Analysis: "{name}"

    | Property | Value |
    |---|---|
    | **Variants** | {", ".join(variants)} |
    | **Soundex tokens** | {", ".join(soundex)} |
    | **DM tokens** | {", ".join(dm_tokens)} |

    **Soundex** maps names to 4-character codes based on consonant sounds.
    "William" → W450, "Bill" → B400. Different codes, but both are stored
    in the phonetic array for broad matching.

    **Daitch-Mokotoff** generates multiple codes per name, accounting for
    different language transliterations. It's more comprehensive than Soundex
    but also more complex.
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ### Levenshtein Early-Exit Optimization

    The key performance trick: `levenshtein_less_equal(a, b, max_distance)`
    terminates early if the distance exceeds `max_distance`. This prevents
    wasteful CPU cycles on obviously-non-matching names.

    - `levenshtein("Smith", "Smyth")` → computes full distance = 2
    - `levenshtein_less_equal("Smith", "Johnson", 2)` → exits early, returns > 2

    Combined with GIN-indexed phonetic pre-filtering, this keeps searches
    fast even at 50+ million records.
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Section 4: Live Before vs. After Benchmark

    Enter a name below and compare the legacy LIKE search against our
    optimized phonetic + Levenshtein approach -- across **50 million records**.
    """)
    return


@app.cell
def _(mo):
    first_name_input = mo.ui.text(
        placeholder="First name (e.g., John, Jonh, Bill)",
        label="First name",
        value="John",
    )
    last_name_input = mo.ui.text(
        placeholder="Last name (e.g., Smith, Smyth)",
        label="Last name",
        value="Smith",
    )
    mo.hstack([first_name_input, last_name_input])
    return first_name_input, last_name_input


@app.cell
async def _(
    MillisecondTimer,
    Person,
    first_name_input,
    last_name_input,
    pd,
    qs_to_df,
):
    first = first_name_input.value.strip()
    last = last_name_input.value.strip()

    if first and last:
        with MillisecondTimer("search_legacy"):
            legacy_results = await qs_to_df(
                Person.objects.search_legacy(first, last).values(
                    "first_name", "last_name", "middle_name", "date_of_birth"
                )[:50]
            )
    else:
        legacy_results = pd.DataFrame()

    legacy_results
    return


@app.cell
async def _(
    MillisecondTimer,
    Person,
    first_name_input,
    last_name_input,
    pd,
    qs_to_df,
):
    first_optimized = first_name_input.value.strip()
    last_optimized = last_name_input.value.strip()

    if first_optimized and last_optimized:
        with MillisecondTimer("optimized"):
            optimized_results = await qs_to_df(
                Person.objects.search_phonetic(first_optimized, last_optimized).values(
                    "first_name", "last_name", "middle_name", "date_of_birth"
                )[:50]
            )
    else:
        optimized_results = pd.DataFrame()

    optimized_results
    return


@app.cell
def _(mo):
    mo.md(r"""
    ---

    ## Summary

    The dual-layer approach works because:

    1. **Phonetic tokens** (Soundex + Daitch-Mokotoff) stored as PostgreSQL arrays
       enable instant broad filtering via GIN indexes and the `&&` overlap operator

    2. **Levenshtein early-exit** (`levenshtein_less_equal`) provides precision
       filtering without computing full edit distances for non-matching names

    3. **B-tree prefix indexes** (`text_pattern_ops`) support fast type-ahead
       for exact/prefix matching

    This combination keeps searches fast and forgiving at **50+ million records**.

    **Repository:** [talk-fuzzy-name-search](https://github.com/caktus/talk-fuzzy-name-search)
    """)
    return


if __name__ == "__main__":
    app.run()
