"""Views for fuzzy name search demo.

Provides:
- Unified search page with tabs for the different search mechanisms:
  Legacy (LIKE) → Prefix (startswith) → Trigram (pg_trgm KNN) →
  Soundex + Levenshtein → Daitch-Mokotoff + Levenshtein
- EXPLAIN ANALYZE endpoint for query plan inspection
"""

import time

from django.core.cache import cache
from django.db import connection
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse

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


# Search mechanisms, ordered from naive to robust, with UI labels and
# one-line descriptions. The tab UI lets users experiment with each.
SEARCH_MODES = {
    "legacy": {
        "label": "Legacy · LIKE",
        "description": "Unindexed LIKE '%name%' -- slow and blind to typos or nicknames.",
    },
    "prefix": {
        "label": "Prefix · istartswith",
        "description": "Indexed istartswith prefix match -- fast type-ahead, but exact spelling only.",
    },
    "trigram": {
        "label": "Trigram · KNN",
        "description": "pg_trgm similarity ordered by the <-> distance -- nearest matches, no threshold to tune.",
    },
    "phonetic": {
        "label": "Soundex + Levenshtein",
        "description": "Soundex GIN overlap filter, then Levenshtein precision -- typo- and nickname-tolerant.",
    },
    "dm": {
        "label": "Daitch-Mokotoff + Levenshtein",
        "description": "Daitch-Mokotoff GIN overlap filter, then Levenshtein precision -- stronger coverage for "
        "Slavic/Germanic names.",
    },
}
DEFAULT_MODE = "phonetic"


def _queryset_for(mode: str, first_name: str, last_name: str):
    """Return the QuerySet implementing the given search mechanism."""
    if mode == "legacy":
        return Person.objects.search_legacy(first_name, last_name)
    if mode == "prefix":
        return Person.objects.search_exact(first_name, last_name)
    if mode == "trigram":
        return Person.objects.search_trigram(first_name, last_name)
    if mode == "dm":
        return Person.objects.search_dm(first_name, last_name)
    return Person.objects.search_phonetic(first_name, last_name)


def _run_search(mode: str, first_name: str, last_name: str) -> dict:
    """Execute a search for the given mechanism and return results with timing.

    Args:
        mode: Search mechanism ('legacy', 'prefix', 'trigram', or 'phonetic').
        first_name: First name to search for.
        last_name: Last name to search for.

    Returns:
        Dict with results, elapsed_ms, and count.
    """
    if not first_name and not last_name:
        return {"results": [], "elapsed_ms": 0, "count": 0}

    qs = _queryset_for(mode, first_name, last_name)

    # Time the query execution
    start = time.perf_counter()
    results = list(qs[:100])  # Limit to 100 results
    elapsed_ms = (time.perf_counter() - start) * 1000

    return {
        "results": results,
        "elapsed_ms": round(elapsed_ms, 2),
        "count": len(results),
    }


def _search_response(request: HttpRequest, mode: str) -> HttpResponse:
    """Build the search context for a mechanism and render the shared templates.

    Renders the results partial for HTMX requests and the full page otherwise.
    """
    if mode not in SEARCH_MODES:
        mode = DEFAULT_MODE

    first_name = request.GET.get("first_name", "").strip()
    last_name = request.GET.get("last_name", "").strip()

    context = _run_search(mode, first_name, last_name)
    context.update(
        {
            "mode": mode,
            "modes": SEARCH_MODES,
            "phase_label": SEARCH_MODES[mode]["label"],
            "method_description": SEARCH_MODES[mode]["description"],
            "first_name": first_name,
            "last_name": last_name,
            "total_records": _cached_total_records(),
        }
    )

    if request.headers.get("HX-Request"):
        return TemplateResponse(request, "records/_search_results.html", context)
    return TemplateResponse(request, "records/home.html", context)


def home(request: HttpRequest) -> HttpResponse:
    """Unified search page (defaults to the phonetic tab)."""
    return _search_response(request, request.GET.get("mode", DEFAULT_MODE))


def search(request: HttpRequest) -> HttpResponse:
    """Unified search endpoint; the active tab is selected via the 'mode' param."""
    return _search_response(request, request.GET.get("mode", DEFAULT_MODE))


def search_explain(request: HttpRequest) -> HttpResponse:
    """EXPLAIN ANALYZE endpoint for the single query launched from the search page."""
    mode = request.GET.get("mode", DEFAULT_MODE)
    if mode not in SEARCH_MODES:
        mode = DEFAULT_MODE
    first_name = request.GET.get("first_name", "").strip()
    last_name = request.GET.get("last_name", "").strip()

    context = {
        "plan": None,
        "sql": None,
        "first_name": first_name,
        "last_name": last_name,
        "mode": mode,
        "mode_label": SEARCH_MODES[mode]["label"],
        "mode_description": SEARCH_MODES[mode]["description"],
        "error": None,
    }

    if not first_name and not last_name:
        context["error"] = "Provide a first_name and/or last_name to explain a query."
        return TemplateResponse(request, "records/explain.html", context)

    qs = _queryset_for(mode, first_name, last_name)
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
