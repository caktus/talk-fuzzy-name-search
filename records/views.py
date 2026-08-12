"""Views for fuzzy name search demo.

Provides:
- Unified search page with checkbox-based mode selection:
  Exact (prefix), Phonetic (Soundex, DM), Fuzzy (Levenshtein, Trigram)
- EXPLAIN ANALYZE endpoint for query plan inspection
- Help page with live examples
"""

import time

from django.core.cache import cache
from django.db import connection
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.utils.dateparse import parse_date

from .models import Person

# Person.objects.count() is a full-table COUNT(*) -- on the 50M-row demo
# table that's a ~600ms sequential-ish scan, and it's on every page load.
# The exact number doesn't matter for the UI, so cache it via Django's
# cache framework for a while instead of re-counting on every request.
_TOTAL_RECORDS_CACHE_KEY = "records:total_records"
_TOTAL_RECORDS_CACHE_SECONDS = 300


def _cached_total_records() -> int:
    """Return Person.objects.count(), cached for a few minutes."""
    count = cache.get(_TOTAL_RECORDS_CACHE_KEY)
    if count is None:
        count = Person.objects.count()
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
        "description": "pg_trgm similarity — nearest matches by character overlap.",
        "default": False,
    },
    "legacy": {
        "label": "Legacy LIKE",
        "description": "Unindexed LIKE '%name%' — slow, included for comparison.",
        "default": False,
    },
}

# Bitmask values for match_source annotation
MATCH_BITS = {
    "prefix": 1,
    "legacy": 2,
    "soundex": 4,
    "dm": 16,
    "trigram": 32,
}

# Badge colors for the template
MATCH_LABELS = {
    "prefix": {"label": "Exact Prefix", "color": "bg-green-50 text-green-700 ring-green-600/20"},
    "legacy": {"label": "LIKE", "color": "bg-gray-50 text-gray-700 ring-gray-600/20"},
    "soundex": {"label": "Soundex", "color": "bg-blue-50 text-blue-700 ring-blue-600/20"},
    "dm": {"label": "DM", "color": "bg-purple-50 text-purple-700 ring-purple-600/20"},
    "trigram": {"label": "Trigram", "color": "bg-indigo-50 text-indigo-700 ring-indigo-600/20"},
}

DEFAULT_MODES = [k for k, v in SEARCH_MODES.items() if v["default"]]


def _queryset_for(mode: str, first_name: str, last_name: str, date_of_birth=None):
    """Return the QuerySet implementing the given search mechanism (for EXPLAIN)."""
    if mode == "legacy":
        return Person.objects.search_legacy(first_name, last_name, date_of_birth)
    if mode == "prefix":
        return Person.objects.search_exact(first_name, last_name, date_of_birth)
    if mode == "trigram":
        return Person.objects.search_trigram(first_name, last_name, date_of_birth)
    if mode == "dm":
        return Person.objects.search_dm(first_name, last_name, date_of_birth)
    return Person.objects.search_phonetic(first_name, last_name, date_of_birth)


def _get_enabled_modes(request: HttpRequest) -> list[str]:
    """Get enabled search modes from request."""
    modes_param = request.GET.get("modes", "")
    if modes_param:
        enabled = [m for m in modes_param.split(",") if m in SEARCH_MODES]
    else:
        enabled = list(DEFAULT_MODES)
    return enabled if enabled else list(DEFAULT_MODES)


def _run_unified_search(modes: list[str], first_name: str, last_name: str, date_of_birth=None) -> dict:
    """Execute a unified search with multiple modes and return annotated results."""
    if not first_name and not last_name and not date_of_birth:
        return {"results": [], "elapsed_ms": 0, "count": 0}

    from django.db import connection

    start = time.perf_counter()
    persons = Person.objects.search_unified(modes, first_name, last_name, date_of_birth)
    elapsed_ms = (time.perf_counter() - start) * 1000

    # Capture executed SQL (connection.queries works when DEBUG=True)
    queries = [
        {"sql": q["sql"], "time": q.get("time", "?")}
        for q in connection.queries
        if "SELECT" in q["sql"][:20] and "django_content_type" not in q["sql"]
    ]

    # Read match_source from SQL-annotated objects
    results = []
    for person in persons:
        source = getattr(person, "_match_source", 0) or 0
        results.append(
            {
                "person": person,
                "match_source": source,
                "has_prefix": bool(source & MATCH_BITS["prefix"]),
                "has_legacy": bool(source & MATCH_BITS["legacy"]),
                "has_soundex": bool(source & MATCH_BITS["soundex"]),
                "has_dm": bool(source & MATCH_BITS["dm"]),
                "has_trigram": bool(source & MATCH_BITS["trigram"]),
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
                FROM records_person p
                WHERE p.id = ANY(%s)
            """,
                [ids],
            )
            codes_by_id = {}
            for row in c.fetchall():
                codes_by_id[row[0]] = {
                    "soundex_fn": row[1],
                    "soundex_ln": row[2],
                    "dm_fn": row[3],
                    "dm_ln": row[4],
                }
            for r in results:
                r["phonetic_codes"] = codes_by_id.get(r["person"].id, {})

    return {
        "results": results,
        "elapsed_ms": round(elapsed_ms, 2),
        "count": len(results),
        "queries": queries,
    }


def _mode_sql(mode: str, first_name: str, last_name: str, enabled: bool) -> str:
    """Return the SQL snippet for a given search mode, empty if not enabled."""
    if not enabled:
        return ""
    fn = first_name.upper()
    ln = last_name.upper()
    parts = []
    if mode == "prefix":
        if fn:
            parts.append(f"UPPER(first_name) LIKE '{fn}%'")
        if ln:
            parts.append(f"UPPER(last_name) LIKE '{ln}%'")
    elif mode == "legacy":
        if fn:
            parts.append(f"first_name ILIKE '%{first_name}%'")
        if ln:
            parts.append(f"last_name ILIKE '%{last_name}%'")
    elif mode == "soundex":
        if fn:
            parts.append(f"SOUNDEX(UPPER(first_name)) = SOUNDEX('{fn}')")
        if ln:
            parts.append(f"SOUNDEX(UPPER(last_name)) = SOUNDEX('{ln}')")
    elif mode == "levenshtein":
        if fn:
            parts.append(f"levenshtein_less_equal(UPPER(first_name), '{fn}', 2) <= 2")
        if ln:
            parts.append(f"levenshtein_less_equal(UPPER(last_name), '{ln}', 2) <= 2")
    elif mode == "dm":
        if fn:
            parts.append(f"DAITCH_MOKOTOFF(UPPER(first_name)) && DAITCH_MOKOTOFF('{fn}')")
        if ln:
            parts.append(f"DAITCH_MOKOTOFF(UPPER(last_name)) && DAITCH_MOKOTOFF('{ln}')")
    elif mode == "trigram":
        if fn and ln:
            parts.append(f"ORDER BY (last_name <-> '{last_name}'), (first_name <-> '{first_name}')")
        elif ln:
            parts.append(f"ORDER BY (last_name <-> '{last_name}')")
        elif fn:
            parts.append(f"ORDER BY (first_name <-> '{first_name}')")
    sql = " AND ".join(parts) if parts else ""
    if not sql:
        return ""
    desc = {
        "prefix": "Exact startswith match.",
        "legacy": "Unindexed substring search.",
        "soundex": "Phonetic code equality.",
        "levenshtein": "Edit distance ≤ 2.",
        "dm": "Phonetic codes for Slavic/Germanic names.",
        "trigram": "Character trigram similarity ranking.",
    }
    return f"{desc.get(mode, '')} {sql}".strip()


def _get_phonetic_codes(first_name: str, last_name: str) -> dict:
    """Compute Soundex and DM codes for the query names."""
    from django.db import connection

    codes = {}
    fn = first_name.upper()
    ln = last_name.upper()
    if fn or ln:
        with connection.cursor() as c:
            if fn:
                c.execute("SELECT SOUNDEX(%s), DAITCH_MOKOTOFF(%s)", [fn, fn])
                row = c.fetchone()
                codes["soundex_fn"] = row[0]
                codes["dm_fn"] = row[1]
            if ln:
                c.execute("SELECT SOUNDEX(%s), DAITCH_MOKOTOFF(%s)", [ln, ln])
                row = c.fetchone()
                codes["soundex_ln"] = row[0]
                codes["dm_ln"] = row[1]
    return codes


def _search_response(request: HttpRequest) -> HttpResponse:
    """Build the search context and render templates."""
    enabled_modes = _get_enabled_modes(request)

    first_name = request.GET.get("first_name", "").strip()
    last_name = request.GET.get("last_name", "").strip()
    date_of_birth = parse_date(request.GET.get("date_of_birth", "").strip())

    context = _run_unified_search(enabled_modes, first_name, last_name, date_of_birth)
    context.update(
        {
            "modes": SEARCH_MODES,
            "enabled_modes": enabled_modes,
            "match_labels": MATCH_LABELS,
            "first_name": first_name,
            "last_name": last_name,
            "date_of_birth": date_of_birth,
            "total_records": _cached_total_records(),
            "mode_sql": {m: _mode_sql(m, first_name, last_name, m in enabled_modes) for m in SEARCH_MODES},
            "phonetic_codes": _get_phonetic_codes(first_name, last_name),
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
    # ?refresh=1 bypasses the cache for fresh examples
    if request.GET.get("refresh"):
        from django.core.cache import cache

        cache.delete("help_examples")
    return TemplateResponse(request, "records/help.html", {"examples": _get_help_examples()})


def _get_help_examples() -> dict:
    """Generate cached dynamic examples for the help page."""
    from django.core.cache import cache

    examples = cache.get("help_examples")
    if examples:
        return examples
    examples = _generate_help_examples()
    cache.set("help_examples", examples, 3600)  # Cache for 1 hour
    return examples


def _generate_help_examples() -> dict:
    """Generate dynamic examples for the help page."""
    from datetime import date

    from django.db import connection

    # Pick a random DOB that gives us 5-20 results
    dob = None
    with connection.cursor() as c:
        c.execute("SELECT date_of_birth FROM records_person ORDER BY RANDOM() LIMIT 100")
        for row in c.fetchall():
            d = row[0]
            c.execute("SELECT count(*) FROM records_person WHERE date_of_birth = %s", [d])
            count = c.fetchone()[0]
            if 5 <= count <= 20:
                dob = d
                break

    if not dob:
        dob = date(1990, 1, 1)

    # Get a sample of names for this DOB
    with connection.cursor() as c:
        c.execute(
            """
            SELECT first_name, last_name FROM records_person
            WHERE date_of_birth = %s
            ORDER BY RANDOM() LIMIT 5
        """,
            [dob],
        )
        names = c.fetchall()

    if not names:
        return {"dob": dob.strftime("%Y-%m-%d"), "groups": []}

    # Pick one name as the base
    base_fn, base_ln = names[0]
    dob_str = dob.strftime("%Y-%m-%d")

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

    # Soundex: use a phonetic variant
    soundex_fn = base_fn
    soundex_ln = base_ln
    if len(base_ln) > 4:
        soundex_ln = base_ln[:2] + "x" + base_ln[3:]  # Simple typo
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

    # DM: use a different spelling
    dm_fn = base_fn
    dm_ln = base_ln
    if len(base_ln) > 4:
        dm_ln = base_ln[:3] + "y" + base_ln[4:]  # Another typo
    groups.append(
        {
            "label": "Phonetic",
            "color": "purple",
            "mode": "dm",
            "fn": dm_fn,
            "ln": dm_ln,
            "desc": f"DM matches Slavic/Germanic variants of '{dm_fn} {dm_ln}'",
        }
    )

    # Levenshtein: use a typo
    lev_fn = base_fn[: max(1, len(base_fn) - 1)]  # Remove last char
    lev_ln = base_ln
    groups.append(
        {
            "label": "Fuzzy",
            "color": "amber",
            "mode": "levenshtein",
            "fn": lev_fn,
            "ln": lev_ln,
            "desc": f"Levenshtein allows up to 2 edits from '{lev_fn} {lev_ln}'",
        }
    )

    # Trigram: use a misspelling
    tri_fn = base_fn
    tri_ln = base_ln[: max(2, len(base_ln) - 2)] + "x"
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

    return {"dob": dob_str, "groups": groups}


def search_explain(request: HttpRequest) -> HttpResponse:
    """EXPLAIN ANALYZE endpoint for the single query launched from the search page."""
    mode = request.GET.get("mode", "prefix")
    if mode not in SEARCH_MODES:
        mode = "prefix"
    first_name = request.GET.get("first_name", "").strip()
    last_name = request.GET.get("last_name", "").strip()
    date_of_birth = parse_date(request.GET.get("date_of_birth", "").strip())

    context = {
        "plan": None,
        "sql": None,
        "first_name": first_name,
        "last_name": last_name,
        "date_of_birth": date_of_birth,
        "mode": mode,
        "mode_label": SEARCH_MODES[mode]["label"],
        "mode_description": SEARCH_MODES[mode]["description"],
        "error": None,
    }

    if not first_name and not last_name and not date_of_birth:
        context["error"] = "Provide a first_name, last_name, and/or date_of_birth to explain a query."
        return TemplateResponse(request, "records/explain.html", context)

    qs = _queryset_for(mode, first_name, last_name, date_of_birth)
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
