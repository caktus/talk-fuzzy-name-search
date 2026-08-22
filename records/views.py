"""Views for fuzzy name search demo.

Provides:
- Unified search page with checkbox-based mode selection:
  Exact (prefix), Phonetic (Soundex, DM), Fuzzy (Levenshtein, Trigram)
- EXPLAIN ANALYZE endpoint for query plan inspection
- Help page with live examples
"""

import time
from datetime import date

from django.core.cache import cache
from django.db import connection
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.utils.dateparse import parse_date

from .models import (
    _MATCH_SOURCE_MODES,
    MATCH_SOURCE_BITS,
    TRIGRAM_SIMILARITY_CUTOFF,
    CourtRecord,
    apply_levenshtein_filter,
    build_unified_filter,
)

# CourtRecord.objects.count() is a full-table COUNT(*) -- on the 54M-row demo
# table that's a ~600ms sequential-ish scan, and it's on every page load.
# The exact number doesn't matter for the UI, so cache it via Django's
# cache framework for a while instead of re-counting on every request.
_TOTAL_RECORDS_CACHE_KEY = "records:total_records"
_TOTAL_RECORDS_CACHE_SECONDS = 300


def _cached_total_records() -> int:
    """Return CourtRecord.objects.count(), cached for a few minutes."""
    count = cache.get(_TOTAL_RECORDS_CACHE_KEY)
    if count is None:
        count = CourtRecord.objects.count()
        cache.set(_TOTAL_RECORDS_CACHE_KEY, count, _TOTAL_RECORDS_CACHE_SECONDS)
    return count


# Search modes, each independently toggleable via checkboxes.
SEARCH_MODES = {
    "prefix": {
        "label": "Exact prefix",
        "description": "B-tree indexed istartswith — fast type-ahead, exact spelling.",
        "default": True,
    },
    "soundex": {
        "label": "Soundex",
        "description": "Phonetic pre-filter using SOUNDEX codes.",
        "default": False,
    },
    "levenshtein": {
        "label": "Levenshtein",
        "description": "Edit distance ≤ 2 — precision filter applied on top of other modes.",
        "default": False,
    },
    "dm": {
        "label": "Daitch-Mokotoff",
        "description": "Phonetic codes with stronger Slavic/Germanic coverage.",
        "default": False,
    },
    "trigram": {
        "label": "Trigram",
        "description": f"pg_trgm KNN ranking, cut at similarity() ≥ {TRIGRAM_SIMILARITY_CUTOFF} per name.",
        "default": False,
    },
    "legacy": {
        "label": "Legacy LIKE",
        "description": "Unindexed LIKE '%name%' — slow, included for comparison.",
        "default": False,
    },
}

DEFAULT_MODES = [k for k, v in SEARCH_MODES.items() if v["default"]]

# Base modes that Levenshtein can refine. Levenshtein is a precision filter
# on top of base modes, not a standalone search: with no base mode enabled
# the UI disables the Levenshtein checkbox and search_unified() returns no
# rows for a name query (see B1/B13).
BASE_MODES = ["prefix", "legacy", "soundex", "dm", "trigram"]


def _parse_explain_modes(request: HttpRequest) -> list[str]:
    """Parse the explain mode list from ?modes=a,b (or legacy ?mode=a).

    Unknown mode names are dropped; if nothing valid remains (or no mode was
    given at all) fall back to ["prefix"] — the previous single-mode behavior.
    """
    modes_param = request.GET.get("modes") or request.GET.get("mode", "")
    tokens = [t.strip() for t in modes_param.split(",") if t.strip()]
    valid = [t for t in tokens if t in SEARCH_MODES]
    return valid if valid else ["prefix"]


def _explain_queryset_for(modes: list[str], first_name: str, last_name: str, date_of_birth=None, sort_field=""):
    """Build the exact queryset search_unified() runs for this mode set.

    Returns (queryset, kind) where kind is "main" (the OR-ed filter query,
    with the Levenshtein precision filter, DOB and page sort applied the
    same way search_unified applies them) or "trigram" (the separate KNN
    ORDER BY ... LIMIT 100 query). Returns (None, None) when
    search_unified() would run no query at all (e.g. Levenshtein checked
    without any base mode and no DOB).
    """
    has_name = bool(first_name or last_name)

    if "trigram" in modes and has_name:
        # search_unified() runs trigram as a separate KNN ORDER BY query,
        # even alongside other base modes — that's the query users care
        # about for trigram, so it is the deterministic primary to explain.
        tri_qs = CourtRecord.objects
        if date_of_birth:
            tri_qs = tri_qs.filter(date_of_birth=date_of_birth)
        return tri_qs.trigram_ordered(first_name, last_name)[:100], "trigram"

    order_clause = (sort_field,) if sort_field else ()

    q = build_unified_filter(modes, first_name, last_name)
    if not q:
        if date_of_birth:
            # DOB-only path: explain the bare DOB filter (+ page sort).
            return CourtRecord.objects.filter(date_of_birth=date_of_birth).order_by(*order_clause)[:100], "main"
        return None, None

    qs = CourtRecord.objects.filter(q)
    if date_of_birth:
        qs = qs.filter(date_of_birth=date_of_birth)
    qs = qs.order_by(*order_clause)
    if "levenshtein" in modes:
        qs = apply_levenshtein_filter(qs, first_name, last_name)
    return qs[:100], "main"


def _get_enabled_modes(request: HttpRequest) -> list[str]:
    """Get enabled search modes from request."""
    modes_param = request.GET.get("modes", "")
    if modes_param:
        enabled = [m for m in modes_param.split(",") if m in SEARCH_MODES]
    else:
        enabled = list(DEFAULT_MODES)
    return enabled if enabled else list(DEFAULT_MODES)


# Values for the ?sort= URL parameter on the results table headers.
SORT_PARAMS = {"dob_asc": "date_of_birth", "dob_desc": "-date_of_birth"}


def _search_params(request: HttpRequest) -> dict:
    """Parse the GET params shared by the search and EXPLAIN views."""
    return {
        "first_name": request.GET.get("first_name", "").strip(),
        "last_name": request.GET.get("last_name", "").strip(),
        "date_of_birth": parse_date(request.GET.get("date_of_birth", "").strip()),
        "sort": request.GET.get("sort", ""),
    }


def _run_unified_search(modes: list[str], first_name: str, last_name: str, date_of_birth=None, sort_param="") -> dict:
    """Execute a unified search with multiple modes and return annotated results."""
    if not first_name and not last_name and not date_of_birth:
        return {"results": [], "elapsed_ms": 0, "count": 0}

    # The base query runs without a default ORDER BY (fast plan); the page
    # sort is a user choice applied as a SQL ORDER BY on that query.
    sort_field = SORT_PARAMS.get(sort_param, "")
    start = time.perf_counter()
    records = CourtRecord.objects.search_unified(modes, first_name, last_name, date_of_birth, sort_field)
    elapsed_ms = (time.perf_counter() - start) * 1000

    # Capture executed SQL (connection.queries works when DEBUG=True)
    queries = [
        {"sql": q["sql"], "time": q.get("time", "?")}
        for q in connection.queries
        if "SELECT" in q["sql"][:20] and "django_content_type" not in q["sql"]
    ]

    # Read match_source from SQL-annotated objects
    results = []
    for record in records:
        source = getattr(record, "_match_source", 0) or 0
        results.append(
            {
                "person": record,
                "match_source": source,
                "has_prefix": bool(source & MATCH_SOURCE_BITS["prefix"]),
                "has_legacy": bool(source & MATCH_SOURCE_BITS["legacy"]),
                "has_soundex": bool(source & MATCH_SOURCE_BITS["soundex"]),
                "has_dm": bool(source & MATCH_SOURCE_BITS["dm"]),
                "has_trigram": bool(source & MATCH_SOURCE_BITS["trigram"]),
            }
        )

    # Compute phonetic codes for all results in one batch query
    if results and ("soundex" in modes or "dm" in modes):
        ids = [r["person"].id for r in results]
        with connection.cursor() as c:
            c.execute(
                """
                SELECT p.id,
                       SOUNDEX(UPPER(p.first_name)),
                       SOUNDEX(UPPER(p.last_name)),
                       DAITCH_MOKOTOFF(UPPER(p.first_name)),
                       DAITCH_MOKOTOFF(UPPER(p.last_name))
                FROM records_courtrecord p
                WHERE p.id = ANY(%s)
            """,
                [ids],
            )
            codes_by_id = {}
            for row in c.fetchall():
                codes_by_id[row[0]] = {
                    "soundex_fn": row[1],
                    "soundex_ln": row[2],
                    # DM codes come back as text[] lists — join for the tooltip
                    "dm_fn": ", ".join(row[3]),
                    "dm_ln": ", ".join(row[4]),
                }
            for r in results:
                r["phonetic_codes"] = codes_by_id.get(r["person"].id, {})

    return {
        "results": results,
        "elapsed_ms": round(elapsed_ms, 2),
        "count": len(results),
        "queries": queries,
    }


MODE_SQL_DESCRIPTIONS: dict[str, str] = {
    "prefix": "Exact startswith match.",
    "legacy": "Unindexed substring search.",
    "soundex": "Phonetic code equality.",
    "levenshtein": "Edit distance ≤ 2.",
    "dm": "Phonetic codes for Slavic/Germanic names.",
    "trigram": f"Character trigram KNN ranking, cut at similarity() ≥ {TRIGRAM_SIMILARITY_CUTOFF}.",
}


def _mode_snippets(mode: str, first_name: str, last_name: str) -> list[str]:
    """Build the per-name display SQL snippets for one search mode."""
    fn = first_name.upper()
    ln = last_name.upper()
    parts: list[str] = []
    if mode == "levenshtein":
        if fn:
            parts.append(f"levenshtein_less_equal(UPPER(first_name), '{fn}', 2) <= 2")
        if ln:
            parts.append(f"levenshtein_less_equal(UPPER(last_name), '{ln}', 2) <= 2")
    elif mode == "trigram":
        if fn:
            parts.append(f"similarity(first_name, '{first_name}') >= {TRIGRAM_SIMILARITY_CUTOFF}")
        if ln:
            parts.append(f"similarity(last_name, '{last_name}') >= {TRIGRAM_SIMILARITY_CUTOFF}")
        if fn and ln:
            parts.append(f"ORDER BY (last_name <-> '{last_name}'), (first_name <-> '{first_name}')")
        elif ln:
            parts.append(f"ORDER BY (last_name <-> '{last_name}')")
        elif fn:
            parts.append(f"ORDER BY (first_name <-> '{first_name}')")
    else:
        # prefix/legacy/soundex/dm: render the actual search templates from
        # records/models.py (same-app import) so the display can't drift
        # from the SQL search_unified() really runs.
        entry = _MATCH_SOURCE_MODES.get(mode)
        if entry:
            _, template, make_param = entry
            for field, value in (("first_name", first_name), ("last_name", last_name)):
                if value:
                    parts.append(template.format(field=field).replace("%s", f"'{make_param(value)}'"))
    return parts


def _mode_sql(mode: str, first_name: str, last_name: str, enabled: bool) -> str:
    """Return the SQL snippet for a given search mode, empty if not enabled."""
    if not enabled:
        return ""
    parts = _mode_snippets(mode, first_name, last_name)
    if not parts:
        return ""
    sql = " AND ".join(parts)
    return f"{MODE_SQL_DESCRIPTIONS.get(mode, '')} {sql}".strip()


def _get_phonetic_codes(first_name: str, last_name: str) -> dict:
    """Compute Soundex and DM codes for the query names.

    DM codes are returned as human-readable strings (", ".join of the
    code list), not Python list reprs.
    """
    codes = {}
    fn = first_name.upper()
    ln = last_name.upper()
    if fn or ln:
        with connection.cursor() as c:
            if fn:
                c.execute("SELECT SOUNDEX(%s), DAITCH_MOKOTOFF(%s)", [fn, fn])
                row = c.fetchone()
                codes["soundex_fn"] = row[0]
                codes["dm_fn"] = ", ".join(row[1])
            if ln:
                c.execute("SELECT SOUNDEX(%s), DAITCH_MOKOTOFF(%s)", [ln, ln])
                row = c.fetchone()
                codes["soundex_ln"] = row[0]
                codes["dm_ln"] = ", ".join(row[1])
    return codes


def _phonetic_tooltips(first_name: str, last_name: str, codes: dict, mode_sql: dict) -> dict:
    """Multi-line title tooltips for the soundex/dm checkboxes.

    Built in Python (not the template) because the tooltip line breaks must
    survive both djlint (a literal newline in a template attribute is
    collapsed by djlint-reformat, and the &#10; entity that preserves it
    trips H023 under --profile=django) and browser tooltip rendering. A real
    newline in the rendered attribute is valid HTML and renders as a line
    break. Values are auto-escaped by the template engine on render.
    """
    tips: dict = {}
    if mode_sql.get("soundex"):
        tips["soundex"] = (
            "Phonetic code equality.\n"
            f"Soundex: {first_name}={codes.get('soundex_fn', '')}, "
            f"{last_name}={codes.get('soundex_ln', '')}\n"
            f"{mode_sql['soundex']}"
        )
    if mode_sql.get("dm"):
        tips["dm"] = (
            "Phonetic codes for Slavic/Germanic names.\n"
            f"DM: {first_name}={codes.get('dm_fn', '')}, "
            f"{last_name}={codes.get('dm_ln', '')}\n"
            f"{mode_sql['dm']}"
        )
    return tips


def _search_response(request: HttpRequest) -> HttpResponse:
    """Build the search context and render templates."""
    enabled_modes = _get_enabled_modes(request)

    params = _search_params(request)
    first_name = params["first_name"]
    last_name = params["last_name"]
    date_of_birth = params["date_of_birth"]
    sort_param = params["sort"]

    context = _run_unified_search(enabled_modes, first_name, last_name, date_of_birth, sort_param)
    context["sort"] = sort_param
    # DOB header click toggles asc/desc (unsorted -> asc).
    context["next_sort"] = "dob_desc" if sort_param == "dob_asc" else "dob_asc"
    mode_sql = {m: _mode_sql(m, first_name, last_name, m in enabled_modes) for m in SEARCH_MODES}
    phonetic_codes = _get_phonetic_codes(first_name, last_name)
    context.update(
        {
            "modes": SEARCH_MODES,
            "enabled_modes": enabled_modes,
            "has_base_mode": any(m in BASE_MODES for m in enabled_modes),
            "first_name": first_name,
            "last_name": last_name,
            "date_of_birth": date_of_birth,
            "total_records": _cached_total_records(),
            "mode_sql": mode_sql,
            "phonetic_codes": phonetic_codes,
            "phonetic_tooltips": _phonetic_tooltips(first_name, last_name, phonetic_codes, mode_sql),
        }
    )

    if request.headers.get("HX-Request"):
        return TemplateResponse(request, "records/_search_results.html", context)
    return TemplateResponse(request, "records/home.html", context)


def home(request: HttpRequest) -> HttpResponse:
    """Unified search page with checkbox-based mode selection."""
    return _search_response(request)


def search(request: HttpRequest) -> HttpResponse:
    """Unified search endpoint with checkbox-based mode selection."""
    return _search_response(request)


def help_page(request: HttpRequest) -> HttpResponse:
    """Help page describing search modes."""
    # ?refresh=1 bypasses the cache for fresh examples. With cache.add
    # (see _get_help_examples), two simultaneous refreshes both compute
    # but only one wins the add — the other's result is discarded, which
    # is fine for a human-paced button.
    if request.GET.get("refresh"):
        cache.delete("help_examples")
    return TemplateResponse(request, "records/help.html", {"examples": _get_help_examples()})


def _get_help_examples() -> dict:
    """Generate cached dynamic examples for the help page."""
    examples = cache.get("help_examples")
    if examples:
        return examples
    examples = _generate_help_examples()
    # cache.add, not cache.set (B5 stampede protection): if a concurrent
    # cold-cache request populated the key first, serve that winner's
    # result instead of our duplicate computation.
    if not cache.add("help_examples", examples, 3600):  # Cache for 1 hour
        winner = cache.get("help_examples")
        if winner:
            return winner
    return examples


def _soundex_variant(last_name: str) -> str:
    """Return a SOUNDEX-equivalent spelling of ``last_name``.

    Dropping one of two adjacent identical letters leaves the SOUNDEX code
    unchanged (the second copy is already ignored as an adjacent duplicate),
    e.g. ``WALLER -> WALER``. When the name has no doubled letter it is
    returned unchanged -- a name always matches its own SOUNDEX code. Either
    way the result is SOUNDEX-equivalent to the input, so a search on it finds
    the original person.
    """
    for i in range(len(last_name) - 1):
        if last_name[i] == last_name[i + 1]:
            return last_name[:i] + last_name[i + 1 :]
    return last_name


def _sample_candidate_dobs() -> list[date]:
    """Sample up to 100 distinct candidate DOBs without a full-table sort (B5).

    TABLESAMPLE SYSTEM takes a PERCENT (0-100), so 0.01 = 0.01% of pages:
    on the 54M-row demo table (~720K pages) that is ~5,400 rows / ~700
    distinct DOBs in ~2ms — O(sample), not the old O(n log n) ORDER BY
    RANDOM() full-table sort. A few attempts top up the candidate list; only
    when the table is too small for the page sample to yield 100 distinct
    DOBs (dev/tests) do we fall back to ORDER BY RANDOM(), which is cheap at
    that size.
    """
    sampled_dobs: list[date] = []
    seen_dobs = set()
    with connection.cursor() as c:
        for _attempt in range(3):
            c.execute("SELECT date_of_birth FROM records_courtrecord TABLESAMPLE SYSTEM (0.01) LIMIT 1000")
            for (d,) in c.fetchall():
                if d not in seen_dobs:
                    seen_dobs.add(d)
                    sampled_dobs.append(d)
                if len(sampled_dobs) >= 100:
                    break
            if len(sampled_dobs) >= 100:
                break
        if len(sampled_dobs) < 100:
            # Tiny table: the page sample yielded too few DOBs, and a
            # full-table RANDOM() sort is cheap when the table is this
            # small (0.01% of its pages is under one page).
            c.execute("SELECT date_of_birth FROM records_courtrecord ORDER BY RANDOM() LIMIT 100")
            for (d,) in c.fetchall():
                if d not in seen_dobs:
                    seen_dobs.add(d)
                    sampled_dobs.append(d)
    return sampled_dobs


def _pick_example_dob(dobs: list[date]) -> date | None:
    """First sampled DOB (in sample order) with a manageable 5-20 result count."""
    if not dobs:
        return None
    # Count every candidate DOB in one aggregate instead of one
    # COUNT(*) per DOB: a single round trip with a <=100-date IN list.
    with connection.cursor() as c:
        placeholders = ", ".join(["%s"] * len(dobs))
        c.execute(
            "SELECT date_of_birth, COUNT(*) FROM records_courtrecord "
            f"WHERE date_of_birth IN ({placeholders}) GROUP BY date_of_birth",
            dobs,
        )
        counts = {row[0]: row[1] for row in c.fetchall()}
    for d in dobs:
        count = counts.get(d)
        if 5 <= count <= 20:
            return d
    return None


def _sample_names_for_dob(dob: date) -> list[tuple[str, str]]:
    """Fetch a random sample of 5 (first_name, last_name) pairs for a DOB."""
    with connection.cursor() as c:
        c.execute(
            """
            SELECT first_name, last_name FROM records_courtrecord
            WHERE date_of_birth = %s
            ORDER BY RANDOM() LIMIT 5
        """,
            [dob],
        )
        return c.fetchall()


def _build_example_groups(base_fn: str, base_ln: str) -> list[dict]:
    """Build the six help-page example groups around one base name."""
    # Generate typos for each group
    groups = []

    # Exact prefix: use partial name
    prefix_fn = base_fn[: max(2, len(base_fn) - 1)]
    prefix_ln = base_ln[: max(2, len(base_ln) - 1)]
    groups.append(
        {
            "label": "Exact",
            "color": "green",
            "mode": "prefix",
            "fn": prefix_fn,
            "ln": prefix_ln,
            "desc": f"Searches for names starting with '{prefix_fn}' and '{prefix_ln}'",
        }
    )

    # Soundex: use a SOUNDEX-equivalent spelling so the base person is found
    # (see _soundex_variant). A naive "x" typo would change the SOUNDEX code
    # and return nothing, which is why the old examples came back empty.
    soundex_fn = base_fn
    soundex_ln = _soundex_variant(base_ln)
    groups.append(
        {
            "label": "Phonetic",
            "color": "blue",
            "mode": "soundex",
            "fn": soundex_fn,
            "ln": soundex_ln,
            "desc": f"Soundex matches names that sound like '{soundex_fn} {soundex_ln}'",
        }
    )

    # DM: use the base name unchanged -- Daitch-Mokotoff of a name always
    # matches itself, so the base person is reliably found.
    dm_fn = base_fn
    dm_ln = base_ln
    groups.append(
        {
            "label": "Phonetic",
            "color": "purple",
            "mode": "dm",
            "fn": dm_fn,
            "ln": dm_ln,
            "desc": f"DM finds names that sound like '{dm_fn} {dm_ln}' (Slavic/Germanic variants)",
        }
    )

    # Levenshtein: use a typo. Levenshtein is a precision filter, not a
    # standalone search (B13), so the link pairs it with a base mode —
    # legacy LIKE always matches the truncated name, and the 1-char
    # truncation is within the edit-distance-2 tolerance.
    lev_fn = base_fn[: max(1, len(base_fn) - 1)]  # Remove last char
    lev_ln = base_ln
    groups.append(
        {
            "label": "Fuzzy",
            "color": "amber",
            "mode": "legacy,levenshtein",
            "fn": lev_fn,
            "ln": lev_ln,
            "desc": f"Levenshtein allows up to 2 edits from '{lev_fn} {lev_ln}'",
        }
    )

    # Trigram: use the base name unchanged -- similarity(x, x) = 1.0 clears
    # the cutoff, so the base person is reliably found and ranked first.
    tri_fn = base_fn
    tri_ln = base_ln
    groups.append(
        {
            "label": "Fuzzy",
            "color": "indigo",
            "mode": "trigram",
            "fn": tri_fn,
            "ln": tri_ln,
            "desc": f"Trigram ranks by character overlap with '{tri_fn} {tri_ln}'",
        }
    )

    # Combos
    groups.append(
        {
            "label": "Combo",
            "color": "blue",
            "mode": "soundex,levenshtein",
            "fn": soundex_fn,
            "ln": soundex_ln,
            "desc": "Soundex finds broad matches, Levenshtein narrows to typos",
        }
    )
    groups.append(
        {
            "label": "Combo",
            "color": "purple",
            "mode": "dm,levenshtein",
            "fn": dm_fn,
            "ln": dm_ln,
            "desc": "DM + Levenshtein: better for Slavic/Germanic names",
        }
    )

    return groups


def _generate_help_examples() -> dict:
    """Generate dynamic examples for the help page."""
    dob = _pick_example_dob(_sample_candidate_dobs())
    if not dob:
        dob = date(1990, 1, 1)

    names = _sample_names_for_dob(dob)
    if not names:
        return {"dob": dob.strftime("%Y-%m-%d"), "groups": []}

    # Pick one name as the base
    base_fn, base_ln = names[0]
    return {"dob": dob.strftime("%Y-%m-%d"), "groups": _build_example_groups(base_fn, base_ln)}


def search_explain(request: HttpRequest) -> HttpResponse:
    """EXPLAIN ANALYZE endpoint for the query search_unified() actually runs."""
    modes = _parse_explain_modes(request)
    params = _search_params(request)
    first_name = params["first_name"]
    last_name = params["last_name"]
    date_of_birth = params["date_of_birth"]
    sort_field = SORT_PARAMS.get(params["sort"], "")

    context = {
        "plan": None,
        "sql": None,
        "first_name": first_name,
        "last_name": last_name,
        "date_of_birth": date_of_birth,
        "mode": ",".join(modes),
        "mode_label": " + ".join(SEARCH_MODES[m]["label"] for m in modes),
        "mode_description": " ".join(SEARCH_MODES[m]["description"] for m in modes),
        "explain_subject": f"Explaining: {' + '.join(modes)}",
        "error": None,
    }

    if not first_name and not last_name and not date_of_birth:
        context["error"] = "Provide a first_name, last_name, and/or date_of_birth to explain a query."
        return TemplateResponse(request, "records/explain.html", context)

    qs, kind = _explain_queryset_for(modes, first_name, last_name, date_of_birth, sort_field)

    if qs is None:
        context["error"] = (
            "No query to explain: Levenshtein is a precision filter applied on top of a base mode, "
            "so with no base mode enabled (and no DOB) search_unified runs no query. "
            "Enable a base mode such as legacy or prefix."
        )
        return TemplateResponse(request, "records/explain.html", context)

    if kind == "trigram":
        context["explain_subject"] = "Explaining: trigram (KNN) query"
        if len(modes) > 1:
            context["explain_subject"] += f" (selected from modes: {', '.join(modes)})"

    context["sql"] = str(qs.query)

    try:
        compiler = qs.query.get_compiler(using="default")
        sql, params = compiler.as_sql()
        with connection.cursor() as cursor:
            cursor.execute(f"EXPLAIN (ANALYZE, BUFFERS) {sql}", params)
            context["plan"] = "\n".join(row[0] for row in cursor.fetchall())
    except Exception as e:
        context["error"] = str(e)

    return TemplateResponse(request, "records/explain.html", context)
