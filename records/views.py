"""Views for fuzzy name search demo.

Provides:
- Unified search page with checkbox-based mode selection:
  Exact (prefix), Phonetic (Soundex, DM), Fuzzy (Levenshtein, Trigram)
- EXPLAIN ANALYZE endpoint for query plan inspection
- Help page with live examples
"""

import logging
import time

from django.core.cache import cache
from django.db import connection, models
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.utils.dateparse import parse_date

from .models import TRIGRAM_SIMILARITY_CUTOFF, CourtRecord, apply_levenshtein_filter, build_unified_filter

_logger = logging.getLogger(__name__)

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

# Bitmask values for match_source annotation
MATCH_BITS = {
    "prefix": 1,
    "legacy": 2,
    "soundex": 4,
    "dm": 16,
    "trigram": 32,
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
    sql = " AND ".join(parts) if parts else ""
    if not sql:
        return ""
    desc = {
        "prefix": "Exact startswith match.",
        "legacy": "Unindexed substring search.",
        "soundex": "Phonetic code equality.",
        "levenshtein": "Edit distance ≤ 2.",
        "dm": "Phonetic codes for Slavic/Germanic names.",
        "trigram": f"Character trigram KNN ranking, cut at similarity() ≥ {TRIGRAM_SIMILARITY_CUTOFF}.",
    }
    return f"{desc.get(mode, '')} {sql}".strip()


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

    first_name = request.GET.get("first_name", "").strip()
    last_name = request.GET.get("last_name", "").strip()
    date_of_birth = parse_date(request.GET.get("date_of_birth", "").strip())
    sort_param = request.GET.get("sort", "")

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
    guided_cases = _get_guided_cases()
    active_key = request.GET.get("guided_case")
    active = next((c for c in guided_cases if c["key"] == active_key), guided_cases[0])
    return TemplateResponse(
        request,
        "records/help.html",
        {
            "examples": _get_help_examples(),
            "guided_cases": guided_cases,
            "guided_active": active,
            "guided_active_key": active["key"],
        },
    )


# =============================================================================
# Guided demo — "click step by step" on the How-it-works page
# =============================================================================

#: Case 1 (JIM LLOYD), verified against the 54M seed on 2026-08: 1 person_id,
#: 20 canonical rows + 15 typo forms (see CURATED_CASES in
#: records/management/commands/find_demo_cases.py).
GUIDED_DEMO_PERSON = {"first_name": "JIM", "last_name": "LLOYD", "date_of_birth": "1981-11-07"}

#: The steps the interactive guided demo walks through. Each step is a REAL
#: search through the same search_unified() code path as the /search/ page,
#: so what the page shows is the app's actual behavior. Step 0 is the
#: no-search intro; steps carry the exact form state they represent.
GUIDED_DEMO_STEPS: list[dict] = [
    {
        "title": "Your client: Jim Lloyd, born 1981-11-07",
        "desc": "An attorney is handed a name as spoken — \"Jim Lloyd\" — plus one fact: a "
        "birth date. Nothing has been searched yet. Walk through the steps and watch each "
        "one re-run the real search; rows that enter the results turn green, rows that "
        "leave turn red.",
        "modes": [],
        "first_name": "",
        "last_name": "",
        "date_of_birth": "",
    },
    {
        "title": "Search the full name the way the client gave it",
        "desc": "A plain substring scan of the whole name: every record containing BOTH Jim "
        "and Lloyd, anywhere in either field — no birth date, no index. This is the shape of "
        "the noise (the table shows the top-100 sample of what a full-dataset scan returns). "
        "Watch the timing pill — it's slow because it's an unindexed scan, and it matches "
        "different people (other Jim Lloyds born on other dates).",
        "modes": ["legacy"],
        "first_name": "JIM",
        "last_name": "LLOYD",
        "date_of_birth": "",
    },
    {
        "title": "Add the date of birth",
        "desc": "The same search, now restricted to people born 1981-11-07. The unrelated "
        "same-named rows collapse away (red = dropped from the previous step) and the client "
        "surfaces. On a small dataset you'd stop here; on the full 54M rows you would still "
        "not trust a raw substring match — that's where the next steps tighten precision.",
        "modes": ["legacy"],
        "first_name": "JIM",
        "last_name": "LLOYD",
        "date_of_birth": "1981-11-07",
    },
    {
        "title": "Type the full name — exact prefix",
        "desc": "Type JIM + LLOYD and switch to the fast default: B-tree prefix matching, "
        "milliseconds (watch the pill). Only exact spellings now survive — precise and fast, "
        "but blind to LOYD and other variant spellings the client's files may be stored under.",
        "modes": ["prefix"],
        "first_name": "JIM",
        "last_name": "LLOYD",
        "date_of_birth": "1981-11-07",
    },
    {
        "title": "Enable Soundex: sounds-like matching",
        "desc": "Soundex turns a name into a code for how it is pronounced — names that sound "
        "the same get the same code even if they're spelled differently: LOYD sounds like LLOYD "
        "(both code as L300), so those rows now match. The green NEW rows are those variant-"
        "spelling records the prefix step could not see. The cost: any unrelated name with the "
        "same pronunciation code sneaks in too (e.g. LADD also codes L300), which is a different "
        "person — more results come in, some of them wrong.",
        "modes": ["prefix", "soundex"],
        "first_name": "JIM",
        "last_name": "LLOYD",
        "date_of_birth": "1981-11-07",
    },
    {
        "title": "Add Levenshtein: keep typos within 2 edits",
        "desc": "Now layer a precision filter on top: only names within 2 edits of the typed "
        "JIM / LLOYD survive. LOYD stays (1 edit); LADD falls out (3 edits). What remains are "
        "records that are genuinely spell-variants of the client — the phonetic false positives "
        "are the red rows below.",
        "modes": ["prefix", "soundex", "levenshtein"],
        "first_name": "JIM",
        "last_name": "LLOYD",
        "date_of_birth": "1981-11-07",
    },
    {
        "title": "Even a mistyped query still finds him",
        "desc": "What if the attorney mistypes the query — LOYD instead of LLOYD? Trigram mode "
        "ranks rows by character distance (similarity() >= 0.3 per name, closest first), so "
        "the canonical JIM LLOYD records surface even though the query itself is misspelled. "
        "The typo can live in the data OR in the typed query — the search handles both.",
        "modes": ["trigram"],
        "first_name": "JIM",
        "last_name": "LOYD",
        "date_of_birth": "1981-11-07",
    },
]

#: Case 2 (WILL VAUGHN), verified the same way: 1 person_id, 19 canonical
#: rows + 12 forms. The headline teaching point is VAUGHN/VAUGHAN — same
#: Soundex (V250) but different character strings, which LIKE and prefix
#: can never bridge — plus typos like WILW and VAUGNH.
GUIDED_DEMO_PERSON_VAUGHN = {"first_name": "WILL", "last_name": "VAUGHN", "date_of_birth": "1973-07-19"}

GUIDED_DEMO_STEPS_VAUGHN: list[dict] = [
    {
        "title": "Your client: Will Vaughn, born 1973-07-19",
        "desc": "Same job, sneakier data: this client's files are stored under both VAUGHN and the "
        "near-identical VAUGHAN spelling (31 records for this one person). VAUGHN and VAUGHAN are "
        "different character-for-character strings — and you'll see exactly what that does to each "
        "search mode. Watch each step re-run the real search; rows that enter turn green, rows "
        "that leave are listed under Dropped this step.",
        "modes": [],
        "first_name": "",
        "last_name": "",
        "date_of_birth": "",
    },
    {
        "title": "Search the full name the way the client gave it",
        "desc": "A plain substring scan of the whole name: every record containing BOTH Will "
        "and Vaughn, anywhere in either field — no birth date, no index. Note the trap right "
        "away: this matches ZERO of the client's VAUGHAN-spelled files, even though they're the "
        "same person. Character-identity matching is spelling-fragile.",
        "modes": ["legacy"],
        "first_name": "WILL",
        "last_name": "VAUGHN",
        "date_of_birth": "",
    },
    {
        "title": "Add the date of birth",
        "desc": "The same substring search, restricted to people born 1973-07-19. The drops (red) "
        "show what the date eliminated. But the VAUGHAN problem is untouched: those files still "
        "aren't on this page at all, because a substring match can never match them.",
        "modes": ["legacy"],
        "first_name": "WILL",
        "last_name": "VAUGHN",
        "date_of_birth": "1973-07-19",
    },
    {
        "title": "Type the full name — exact prefix",
        "desc": "WILL + VAUGHN + the DOB, with the fast B-tree default on. Millisecond-fast, but the "
        "page now holds exactly one spelling: the client's VAUGHAN-spelled files are still missing "
        "and typos like WILW are invisible.",
        "modes": ["prefix"],
        "first_name": "WILL",
        "last_name": "VAUGHN",
        "date_of_birth": "1973-07-19",
    },
    {
        "title": "Enable Soundex: sounds-like matching",
        "desc": "Soundex turns a name into a code for how it is pronounced — names that sound "
        "the same can be spelled completely differently and still match: VAUGHN and VAUGHAN "
        "pronounce the same way, so the client's missing VAUGHAN files now match (and the WILW "
        "typo of WILL does too). The green NEW rows are exactly those records the prefix step "
        "could not see. The cost: any unrelated name that happens to pronounce the same way "
        "also sneaks in — more results come in, some of them wrong.",
        "modes": ["prefix", "soundex"],
        "first_name": "WILL",
        "last_name": "VAUGHN",
        "date_of_birth": "1973-07-19",
    },
    {
        "title": "Add Levenshtein: keep typos within 2 edits",
        "desc": "Now tighten: only names within 2 edits of WILL / VAUGHN survive. VAUGHAN stays "
        "(1 edit), WILW stays (1), VAUGNH stays (2); soundex-only false positives like VAGE fall "
        "out (more than 2 edits away). What remains is genuinely this client.",
        "modes": ["prefix", "soundex", "levenshtein"],
        "first_name": "WILL",
        "last_name": "VAUGHN",
        "date_of_birth": "1973-07-19",
    },
    {
        "title": "Even a truncated query still finds him",
        "desc": "The attorney types only VAUGH for the last name (truncated input). Trigram mode ranks "
        "by character overlap — both VAUGHN and VAUGHAN clear the similarity() >= 0.3 cut against "
        "VAUGH — so the client's files surface in both spellings, closest first. No exact spelling "
        "and no phonetic code needed for a partial word.",
        "modes": ["trigram"],
        "first_name": "WILL",
        "last_name": "VAUGH",
        "date_of_birth": "1973-07-19",
    },
]

#: The two hand-verified talk scenarios, always available in the guided demo.
#: (The steps are rehearsed line-by-line against the live 54M seed, so they
#: keep bespoke descriptions instead of the generic templates below.)
_GUIDED_DEMO_CURATED: list[dict] = [
    {
        "key": "lloyd",
        "person": GUIDED_DEMO_PERSON,
        "steps": GUIDED_DEMO_STEPS,
        "blurb": "a verified person in the dataset with 35 near-duplicate records (20 under the exact "
        "spelling, 15 under typos like LOYD and LMOYD).",
        "verified": True,
    },
    {
        "key": "vaughn",
        "person": GUIDED_DEMO_PERSON_VAUGHN,
        "steps": GUIDED_DEMO_STEPS_VAUGHN,
        "blurb": "a verified person in the dataset with 31 near-duplicate records (19 under the exact "
        "spelling, 12 under the VAUGHAN variant and typos like WILW and VAUGNH).",
        "verified": True,
    },
]

#: How many additionally-discovered ("dynamic") client cases the guided demo
#: offers, on top of the two verified ones above.
GUIDED_DEMO_DYNAMIC_COUNT = 4
_DYNAMIC_CASES_CACHE_KEY = "guided:demo_dynamic_cases"
_DYNAMIC_CASES_CACHE_SECONDS = 86400  # discovery is a multi-minute aggregate at 54M rows

#: One person_id stored under up to three name spellings (the teaching point),
#: canonical count 8..30, short ASCII names. The single aggregate pass mirrors
#: find_demo_cases.py's discovery query. The Soundex gate runs in SQL because
#: the demo's Soundex step uses PostgreSQL's SOUNDEX — the candidate must
#: satisfy PostgreSQL's code, not a Python re-implementation. The typo-distance
#: and trigram-headroom gates run in Python on the short candidate list.
_DYNAMIC_CASE_SQL = """
WITH agg AS (
    SELECT person_id, first_name, last_name, COUNT(*) AS n
    FROM records_courtrecord
    WHERE first_name ~ '^[A-Z]{3,9}$'
      AND last_name ~ '^[A-Z]{4,8}$'
      AND last_name NOT IN ('LLOYD', 'VAUGHN')
    GROUP BY person_id, first_name, last_name
),
ranked AS (
    SELECT agg.*,
           COUNT(*)   OVER (PARTITION BY person_id)                          AS spells,
           ROW_NUMBER() OVER (PARTITION BY person_id ORDER BY n DESC, last_name) AS rn
    FROM agg
)
SELECT r.person_id::text AS person_id,
       r.first_name, r.last_name, r.n,
       v.first_name AS variant_first, v.last_name AS variant_last, v.n AS variant_n,
       SOUNDEX(r.last_name) AS sl_r, SOUNDEX(v.last_name) AS sl_v,
       SOUNDEX(r.first_name) AS sf_r, SOUNDEX(v.first_name) AS sf_v
FROM ranked r
JOIN agg v
  ON v.person_id = r.person_id AND v.last_name <> r.last_name
WHERE r.rn = 1
  AND r.spells <= 3         -- a few spellings is fine; the story uses the top two
  AND r.n BETWEEN 8 AND 30
  AND v.n BETWEEN 1 AND 30
  AND (SOUNDEX(v.last_name) = SOUNDEX(r.last_name)     -- cheap pre-filter in SQL
       OR SOUNDEX(v.first_name) = SOUNDEX(r.first_name))
ORDER BY r.person_id::text                             -- deterministic; scatters first names
LIMIT 800
"""


def _edit_distance(a: str, b: str) -> int:
    """Case-insensitive Levenshtein, capped at 3 ("greater than 2")."""
    a, b = a.upper(), b.upper()
    if len(a) < len(b):
        return _edit_distance(b, a)
    if not b:
        return min(len(a), 3)
    if len(a) - len(b) > 2:
        return 3
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        row_min = cur[0]
        for j, cb in enumerate(b):
            v = min(prev[j + 1] + 1, cur[j] + 1, prev[j] + (ca != cb))
            v = 3 if v > 2 else v
            cur.append(v)
            row_min = min(row_min, v)
        if row_min > 2:
            return 3
        prev = cur
    return prev[-1]


def _pg_trigrams(s: str) -> set[str]:
    """pg_trgm-style trigram set (3-space zero padding on both ends)."""
    padded = "   " + s.upper() + "   "
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


def _pg_similarity(a: str, b: str) -> float:
    ta, tb = _pg_trigrams(a), _pg_trigrams(b)
    return len(ta & tb) / max(len(ta), len(tb))


def _discover_dynamic_cases() -> list[dict]:
    """Find up to GUIDED_DEMO_DYNAMIC_COUNT real people in the dataset whose files
    are stored under exactly two spellings that each demo step can demonstrate:

    - the variant is a genuine typo (1..2 edits from the canonical last name)
    - the variant shares the canonical Soundex code (step 5 finds it)
    - both spellings clear the trigram similarity cut against the 3-letter
      truncated last name (step 7's finale still finds the client)

    Returns [] when the dataset has no qualifying people (e.g. an empty/fresh
    DB). Raises on a genuine database failure — the caller must not cache that,
    or a transient hiccup would hide the demo clients for the full TTL.
    """
    with connection.cursor() as cursor:
        cursor.execute(_DYNAMIC_CASE_SQL)
        rows = cursor.fetchall()
    curated_names = {(c["person"]["first_name"], c["person"]["last_name"]) for c in _GUIDED_DEMO_CURATED}
    seen_names: set[tuple[str, str]] = set()
    valid: list[dict] = []
    for r in rows:
        person_id, fn, ln, n, var_fn, var_ln, var_n, sl_r, sl_v, sf_r, sf_v = r
        if (fn, ln) in curated_names or (fn, ln) in seen_names:
            continue
        # Validate each demo step can actually demonstrate its point. A person
        # may appear twice (two variant rows); try the next row if this
        # variant fails — the person is only claimed on success (below).
        if not (1 <= _edit_distance(ln, var_ln) <= 2):
            continue
        # The Soundex step matches a row only when BOTH name codes match the
        # query's; these codes were computed by PostgreSQL itself, the same
        # way the search computes them.
        if sl_r != sl_v or sf_r != sf_v:
            continue
        prefix = ln[:3]
        if (
            _pg_similarity(ln, prefix) < TRIGRAM_SIMILARITY_CUTOFF
            or _pg_similarity(var_ln, prefix) < TRIGRAM_SIMILARITY_CUTOFF
        ):
            continue
        seen_names.add((fn, ln))
        valid.append(
            {
                "person_id": person_id,
                "first_name": fn,
                "last_name": ln,
                "variant_first": var_fn,
                "variant_last": var_ln,
                "count": int(n),
                "variant_count": int(var_n),
            }
        )
    # Prefer last names >= 6 chars (more trigram headroom in the finale step),
    # and diversify: at most 2 people per first name before widening to 3, etc.
    valid.sort(key=lambda c: (-len(c["last_name"]), c["first_name"], c["last_name"]))
    picked: list[dict] = []
    remaining = list(valid)
    # Round-robin passes: each pass takes one client per distinct first name,
    # so the picked clients are as first-name-diverse as the pool allows.
    # Each pass picks at least one candidate from `remaining` (or the pool is
    # exhausted), so this always terminates.
    while len(picked) < GUIDED_DEMO_DYNAMIC_COUNT and remaining:
        seen_first: set[str] = set()
        for c in list(remaining):
            if len(picked) >= GUIDED_DEMO_DYNAMIC_COUNT:
                break
            if c["first_name"] in seen_first:
                continue
            seen_first.add(c["first_name"])
            picked.append(c)
            remaining.remove(c)
    return picked


def _build_generic_steps(first: str, last: str, dob: str) -> list[dict]:
    """The same 7-step walkthrough for any client: intro, substring, +DOB,
    exact prefix, +Soundex, +Levenshtein, truncated-trigram finale. Wording is
    generic (no case-specific variant names) so it stays true for discovered
    people we only validated structurally.
    """
    fn, ln, prefix = first.upper(), last.upper(), last.upper()[:3]
    pretty = f"{first.title()} {last.title()}"
    return [
        {
            "title": f"Your client: {pretty}, born {dob}",
            "desc": f"An attorney is handed a name as spoken — \"{pretty}\" — plus one fact: a "
            "birth date. Nothing has been searched yet. Walk through the steps and watch each "
            "one re-run the real search; rows that enter the results turn green, rows that "
            "leave turn red.",
            "modes": [], "first_name": "", "last_name": "", "date_of_birth": "",
        },
        {
            "title": "Search the full name the way the client gave it",
            "desc": "A plain substring scan of the whole name: every record containing BOTH "
            f"{fn} and {ln}, anywhere in either field — no birth date, no index. This is the "
            "shape of the noise (the table shows the top-100 sample of the full dataset). "
            "Watch the timing pill — it's slow because it's an unindexed scan, and it matches "
            "other people with the same name born on other dates.",
            "modes": ["legacy"], "first_name": fn, "last_name": ln, "date_of_birth": "",
        },
        {
            "title": "Add the date of birth",
            "desc": f"The same search, now restricted to people born {dob}. The unrelated "
            "same-named rows collapse away (red = dropped from the previous step) and this "
            "client's records surface. On the full 54M-row dataset a raw substring match is "
            "still not something you'd trust — the next steps tighten it.",
            "modes": ["legacy"], "first_name": fn, "last_name": ln, "date_of_birth": dob,
        },
        {
            "title": "Type the full name — exact prefix",
            "desc": f"{fn} + {ln} + the DOB, with the fast B-tree prefix default on — "
            "milliseconds (watch the pill). Only this exact spelling matches now: precise and "
            "fast, but any of this client's records filed under a different spelling are "
            "invisible.",
            "modes": ["prefix"], "first_name": fn, "last_name": ln, "date_of_birth": dob,
        },
        {
            "title": "Enable Soundex: sounds-like matching",
            "desc": "Soundex turns a name into a code for how it is pronounced — names that "
            f"sound the same get the same code even if they're spelled differently. The green "
            f"NEW rows are this client's records filed under a different spelling of {ln} that "
            "the prefix step could not see. The cost: any unrelated name that happens to "
            "pronounce the same way also sneaks in — more results come in, some of them wrong.",
            "modes": ["prefix", "soundex"], "first_name": fn, "last_name": ln, "date_of_birth": dob,
        },
        {
            "title": "Add Levenshtein: keep typos within 2 edits",
            "desc": f"Now layer a precision filter on top: only names within 2 edits of the "
            f"typed {fn} / {ln} survive. One edit covers transpositions and single mistyped "
            "characters — enough for real-world typos, far too tight for names that merely "
            "sound alike, so the phonetic false positives fall out (red). What remains is "
            "genuinely this client.",
            "modes": ["prefix", "soundex", "levenshtein"], "first_name": fn, "last_name": ln, "date_of_birth": dob,
        },
        {
            "title": "Even a truncated query still finds them",
            "desc": f"The attorney types only {fn} {prefix} (the last name cut off). Trigram "
            f"mode ranks rows by character overlap — both spellings of {ln} clear the 30% "
            f"similarity cut against {prefix}, so the client's records surface, closest first. "
            "No exact spelling and no phonetic code needed for a partial word.",
            "modes": ["trigram"], "first_name": fn, "last_name": prefix, "date_of_birth": dob,
        },
    ]


def _get_discovered_cases() -> list[dict]:
    """The dynamic demo cases (discovered from the live data), cached for a day.

    Each entry: {"c": {first_name, last_name, variant_first, variant_last, count,
    variant_count, person_id}} — the DOB is resolved per-case in
    _get_guided_cases() via a person_id index lookup.
    """
    found = cache.get(_DYNAMIC_CASES_CACHE_KEY)
    if found is None:
        try:
            found = _discover_dynamic_cases()
        except Exception:
            # A DB failure is not a "no clients" result: don't cache it, so
            # the next page load retries. Log the real error for diagnosis.
            _logger.exception("Guided demo: dynamic case discovery failed")
            return []
        # cache.add, not cache.set (stampede guard): if a concurrent request
        # populated the key first, serve that winner's result.
        if not cache.add(_DYNAMIC_CASES_CACHE_KEY, found, _DYNAMIC_CASES_CACHE_SECONDS):
            found = cache.get(_DYNAMIC_CASES_CACHE_KEY) or []
    # Cache stores the raw dicts; wrap uniformly here so hits and misses
    # both return the same [{"c": {...}}] shape callers expect.
    return [{"c": c} for c in found]


def _get_guided_cases() -> list[dict]:
    """Every scenario the How-it-works guided demo offers, in display order:
    the two verified cases first, then up to GUIDED_DEMO_DYNAMIC_COUNT people
    discovered in the live dataset. ?case= on the step endpoint (and
    ?guided_case= on the help page) selects which scenario plays back.
    """
    cases = list(_GUIDED_DEMO_CURATED)
    try:
        discovered = _get_discovered_cases()
        dobs = {}
        if discovered:
            # One row per person_id (all rows of a person share the DOB, so a
            # MIN is lossless); indexed lookup, fast even at 54M rows.
            dobs = {
                str(r["person_id"]): r["dob"].isoformat()
                for r in CourtRecord.objects.filter(person_id__in=[d["c"]["person_id"] for d in discovered])
                .values("person_id")
                .annotate(dob=models.Min("date_of_birth"))
            }
        for d in discovered:
            dob = dobs.get(str(d["c"]["person_id"]))
            if dob is None:
                continue
            person = {"first_name": d["c"]["first_name"], "last_name": d["c"]["last_name"], "date_of_birth": dob}
            cases.append(
                {
                    "key": f"{d['c']['first_name'].lower()}-{d['c']['last_name'].lower()}",
                    "person": person,
                    "steps": _build_generic_steps(d["c"]["first_name"], d["c"]["last_name"], dob),
                    "blurb": (
                        f"a real person in the dataset stored under two spellings "
                        f"({d['c']['last_name']} and {d['c']['variant_last']}: "
                        f"{d['c']['count']} + {d['c']['variant_count']} records)."
                    ),
                    "verified": False,
                }
            )
    except Exception:
        # Transient DB hiccup: this request falls back to the curated cases
        # only. Log it (was a silent `pass` that hid a real cache-shape bug).
        _logger.exception("Guided demo: building dynamic cases failed")
    return cases


def guided_demo_step(request: HttpRequest) -> HttpResponse:
    """HTMX fragment: run step N of a guided-demo case, diffing it against the step the visitor just saw.

    ?case= picks the scenario (defaults to the first); ?step= the step. The previous
    step's row ids arrive in ?prev_ids= (the browser carries them from the rendered
    fragment via hx-vals), so the added/removed highlight is always computed against
    what the user actually saw last — even if they click steps out of order.
    """
    guided_cases = _get_guided_cases()
    case_key = request.GET.get("case", guided_cases[0]["key"])
    case = next((c for c in guided_cases if c["key"] == case_key), None)
    if case is None:  # unknown ?case= falls back to the first scenario
        case = guided_cases[0]
    try:
        step = int(request.GET.get("step", "0"))
    except ValueError:
        step = 0
    if step < 0 or step >= len(case["steps"]):
        step = 0
    spec = case["steps"][step]
    prev_ids = [iid for iid in request.GET.get("prev_ids", "").split(",") if iid.isdigit()]

    context = {
        "case_key": case["key"],
        "steps": case["steps"],
        "step": step,
        "step_spec": spec,
        "person": case["person"],
        "results": [],
        "count": 0,
        "elapsed_ms": 0,
        "queries": [],
        "has_prev": bool(prev_ids),
        "added_ids": set(),
        "removed_ids": set(),
        "removed_records": [],
        "current_ids_csv": "",
    }

    if spec["modes"]:
        run = _run_unified_search(
            spec["modes"],
            spec["first_name"],
            spec["last_name"],
            parse_date(spec["date_of_birth"]) or None,
        )
        context.update(
            results=run["results"],
            count=run["count"],
            elapsed_ms=run["elapsed_ms"],
            queries=run["queries"],
        )
        current_ids = {str(r["person"].id) for r in run["results"]}
        context["current_ids_csv"] = ",".join(sorted(current_ids))
        prev_set = set(prev_ids)
        if prev_set:
            context["added_ids"] = current_ids - prev_set
            context["removed_ids"] = prev_set - current_ids
            context["removed_records"] = list(
                CourtRecord.objects.filter(id__in=context["removed_ids"]).order_by("first_name", "last_name")[:100]
            )
        # Per-row flags the template renders:
        # is_canonical = typed in this query with the exact stored spelling
        # (marks "the client's spelling" rows); is_added = new vs previous step.
        added = context["added_ids"]
        qfn, qln = spec["first_name"].upper(), spec["last_name"].upper()
        for r in context["results"]:
            r["is_canonical"] = bool(qfn and qln) and (
                r["person"].first_name.upper() == qfn and r["person"].last_name.upper() == qln
            )
            r["is_added"] = str(r["person"].id) in added

    return TemplateResponse(request, "records/_guided_demo_step.html", context)


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


def _generate_help_examples() -> dict:
    """Generate dynamic examples for the help page."""
    from datetime import date

    # Pick a random DOB that gives us 5-20 results, without sorting the
    # whole table (B5). TABLESAMPLE SYSTEM takes a PERCENT (0-100), so
    # 0.01 = 0.01% of pages: on the 54M-row demo table (~720K pages) that
    # is ~5,400 rows / ~700 distinct DOBs in ~2ms — O(sample), not the
    # old O(n log n) ORDER BY RANDOM() full-table sort. A few attempts
    # top up the candidate list; only when the table is too small for
    # the page sample to yield 100 distinct DOBs (dev/tests) do we fall
    # back to ORDER BY RANDOM(), which is cheap at that size.
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

        # Count every candidate DOB in one aggregate instead of one
        # COUNT(*) per DOB: a single round trip with a <=100-date IN list.
        dob = None
        if sampled_dobs:
            placeholders = ", ".join(["%s"] * len(sampled_dobs))
            c.execute(
                "SELECT date_of_birth, COUNT(*) FROM records_courtrecord "
                f"WHERE date_of_birth IN ({placeholders}) GROUP BY date_of_birth",
                sampled_dobs,
            )
            counts = {row[0]: row[1] for row in c.fetchall()}
            # First sampled DOB (in sample order) with a manageable count
            # — the same selection rule as before.
            for d in sampled_dobs:
                count = counts.get(d)
                if 5 <= count <= 20:
                    dob = d
                    break

    if not dob:
        dob = date(1990, 1, 1)

    # Get a sample of names for this DOB
    with connection.cursor() as c:
        c.execute(
            """
            SELECT first_name, last_name FROM records_courtrecord
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

    return {"dob": dob_str, "groups": groups}


def search_explain(request: HttpRequest) -> HttpResponse:
    """EXPLAIN ANALYZE endpoint for the query search_unified() actually runs."""
    modes = _parse_explain_modes(request)
    first_name = request.GET.get("first_name", "").strip()
    last_name = request.GET.get("last_name", "").strip()
    date_of_birth = parse_date(request.GET.get("date_of_birth", "").strip())
    sort_field = SORT_PARAMS.get(request.GET.get("sort", ""), "")

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
