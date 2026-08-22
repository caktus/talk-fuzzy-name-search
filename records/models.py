"""CourtRecord model with phonetic name search.

Uses PostgreSQL's fuzzystrmatch extension (SOUNDEX, DAITCH_MOKOTOFF)
directly in queries via functional GIN/B-tree indexes, rather than
storing pre-computed phonetic tokens as columns.
"""

from __future__ import annotations

import datetime
from uuid import uuid4

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex, GistIndex, OpClass
from django.contrib.postgres.search import TrigramDistance
from django.db import models
from django.db.models import F, IntegerField, Q, Value
from django.db.models.expressions import ExpressionWrapper, RawSQL
from django.db.models.fields import BooleanField, UUIDField
from django.db.models.functions import Upper

from .expressions import LevenshteinLessEqual

# Minimum pg_trgm similarity() a result must reach, applied per provided name
# by every trigram search path (trigram_ordered, and therefore search_unified's
# trigram mode and the EXPLAIN endpoint).
# 0.3 (pg_trgm's own similarity_threshold default) keeps close variants (a
# deleted/inserted letter: similarity("Smit", "Smith") = 0.57,
# similarity("BEJNAMIN", "BENJAMIN") = 0.385) while still cutting most of the
# noise a bare KNN top-100 would surface for a rare spelling.
TRIGRAM_SIMILARITY_CUTOFF = 0.3

# mode -> (bit, per-name SQL template with {field}, param transform)
_MATCH_SOURCE_MODES = {
    "prefix": (1, "UPPER({field}) LIKE %s", lambda v: v.upper() + "%"),
    "legacy": (2, "{field} ILIKE %s", lambda v: "%" + v + "%"),
    "soundex": (4, "SOUNDEX(UPPER({field})) = SOUNDEX(%s)", lambda v: v.upper()),
    "dm": (16, "DAITCH_MOKOTOFF(UPPER({field})) && DAITCH_MOKOTOFF(%s)", lambda v: v.upper()),
}


def _match_source_case(modes: list[str], first_name: str, last_name: str) -> tuple[list[str], list]:
    """Build the per-mode CASE snippets and params for the _match_source bitmask."""
    parts: list[str] = []
    params: list = []
    for mode, (bit, template, make_param) in _MATCH_SOURCE_MODES.items():
        if mode not in modes:
            continue
        preds = []
        if first_name:
            preds.append(template.format(field="first_name"))
            params.append(make_param(first_name))
        if last_name:
            preds.append(template.format(field="last_name"))
            params.append(make_param(last_name))
        if preds:
            parts.append(f"CASE WHEN {' AND '.join(preds)} THEN {bit} ELSE 0 END")
    return parts, params


def _trigram_similarity_filters(first_name: str, last_name: str) -> dict:
    """Return the similarity() >= cutoff filters for the provided name(s).

    Both names are cut when both are provided; a nameless query (DOB-only)
    yields no filter.
    """
    filters = {}
    if first_name:
        filters["_first_sim__gte"] = TRIGRAM_SIMILARITY_CUTOFF
    if last_name:
        filters["_last_sim__gte"] = TRIGRAM_SIMILARITY_CUTOFF
    return filters


def _apply_trigram_similarity_filter(qs: CourtRecordQuerySet, first_name: str, last_name: str) -> CourtRecordQuerySet:
    """Annotate similarity() on each provided name and cut below the threshold."""
    filters = _trigram_similarity_filters(first_name, last_name)
    if not filters:
        return qs
    annotations = {}
    if first_name:
        annotations["_first_sim"] = RawSQL("similarity(first_name, %s)", [first_name])
    if last_name:
        annotations["_last_sim"] = RawSQL("similarity(last_name, %s)", [last_name])
    return qs.annotate(**annotations).filter(**filters)


def build_unified_filter(modes: list[str], first_name: str, last_name: str) -> Q:
    """Build the OR-ed WHERE condition that search_unified() runs for base modes.

    Each of prefix/legacy/soundex/dm contributes one AND-ed group of
    conditions (both names must match when both are given); the groups are
    OR-ed together. No nickname expansion is applied (it is dropped for the
    unified search). Levenshtein and trigram are intentionally excluded:
    Levenshtein is a precision filter applied on top (see
    apply_levenshtein_filter) and trigram runs as a separate KNN ORDER BY
    query (see CourtRecordQuerySet.trigram_ordered).

    Returns an empty Q() when no base mode can build a condition.
    """
    fn_upper = first_name.upper() if first_name else ""
    ln_upper = last_name.upper() if last_name else ""

    q = Q()

    if "prefix" in modes:
        mode_q = Q()
        if first_name:
            mode_q &= Q(first_name__istartswith=first_name)
        if last_name:
            mode_q &= Q(last_name__istartswith=last_name)
        if mode_q:
            q |= mode_q

    if "legacy" in modes:
        mode_q = Q()
        if first_name:
            mode_q &= Q(first_name__icontains=first_name)
        if last_name:
            mode_q &= Q(last_name__icontains=last_name)
        if mode_q:
            q |= mode_q

    def _phonetic_or(fn_template: str, ln_template: str):
        fn_part = fn_template if first_name else None
        ln_part = ln_template if last_name else None
        if fn_part and ln_part:
            return f"({fn_part}) AND ({ln_part})", [fn_upper, ln_upper]
        if fn_part:
            return fn_part, [fn_upper]
        if ln_part:
            return ln_part, [ln_upper]
        return None, []

    if "soundex" in modes:
        sql, params = _phonetic_or(
            "SOUNDEX(UPPER(first_name)) = SOUNDEX(%s)", "SOUNDEX(UPPER(last_name)) = SOUNDEX(%s)"
        )
        if sql:
            q |= ExpressionWrapper(RawSQL(sql, params), output_field=BooleanField())

    if "dm" in modes:
        sql, params = _phonetic_or(
            "DAITCH_MOKOTOFF(UPPER(first_name)) && DAITCH_MOKOTOFF(%s)",
            "DAITCH_MOKOTOFF(UPPER(last_name)) && DAITCH_MOKOTOFF(%s)",
        )
        if sql:
            q |= ExpressionWrapper(RawSQL(sql, params), output_field=BooleanField())

    return q


def apply_levenshtein_filter(qs: CourtRecordQuerySet, first_name: str, last_name: str) -> CourtRecordQuerySet:
    """Apply the Levenshtein precision filter (edit distance ≤ 2) as an AND.

    Exactly what search_unified() applies on top of the base modes: only the
    provided name(s) are filtered.
    """
    if first_name:
        qs = qs.annotate(
            _fn_dist=LevenshteinLessEqual(Upper(F("first_name")), Value(first_name.upper()), Value(2))
        ).filter(_fn_dist__lte=2)
    if last_name:
        qs = qs.annotate(
            _ln_dist=LevenshteinLessEqual(Upper(F("last_name")), Value(last_name.upper()), Value(2))
        ).filter(_ln_dist__lte=2)
    return qs


class CourtRecordQuerySet(models.QuerySet):
    """Custom QuerySet for CourtRecord with phonetic name search methods."""

    def trigram_ordered(self, first_name: str, last_name: str) -> CourtRecordQuerySet:
        """Apply the pg_trgm KNN ORDER BY used by search_unified()'s trigram mode.

        Uses Django's built-in ``TrigramDistance`` (the ``<->`` operator).
        Callers must ensure at least one name is provided.
        """
        self = _apply_trigram_similarity_filter(self, first_name, last_name)
        if first_name and last_name:
            return self.annotate(
                _last_dist=TrigramDistance(F("last_name"), Value(last_name)),
                _first_dist=TrigramDistance(F("first_name"), Value(first_name)),
            ).order_by("_last_dist", "_first_dist")
        if last_name:
            return self.annotate(_dist=TrigramDistance(F("last_name"), Value(last_name))).order_by("_dist")
        return self.annotate(_dist=TrigramDistance(F("first_name"), Value(first_name))).order_by("_dist")

    def search_unified(
        self,
        modes: list[str],
        first_name: str,
        last_name: str,
        date_of_birth: datetime.date | None = None,
        sort_field: str = "",
    ) -> list[CourtRecord]:
        """Unified search combining multiple algorithms (OR-ed Q object; trigram rows merged in Python from a separate KNN query).

        Each enabled base mode adds its condition to an OR-ed Q object.
        Trigram runs as a separate ORDER BY KNN query whose rows are merged
        in (not a SQL filter).

        Nickname expansion is deliberately dropped for the unified search:
        no base filter consults NICKNAME_MAP and the Levenshtein refinement
        is measured against the query as typed, so 'Bill' does not find
        'William' (distance 4). See RECS-2026-08-14 P1-12 — nickname support
        would require variant-aware filtering.

        There is no default ordering: with sort_field empty the rows come
        back in DB order and the page sort (e.g. ?sort=dob_asc) is passed in
        as sort_field and applied as a SQL ORDER BY on the base query.

        Args:
            modes: List of enabled mode names (e.g., ['prefix', 'soundex']).
            first_name: First name to search for.
            last_name: Last name to search for.
            date_of_birth: Optional DOB filter applied to all modes.
            sort_field: Optional order_by field for the base query (e.g.
                'date_of_birth' or '-date_of_birth'); empty = no ORDER BY.

        Returns:
            A list of CourtRecord objects (deduplicated, limited to 100). Every path
            materializes and caps at 100, so callers get a bounded, already-
            fetched list and timing around the call covers the DB fetch.
        """
        if not first_name and not last_name and not date_of_birth:
            return []

        # Base-mode conditions, shared with the EXPLAIN endpoint so both
        # use the exact same query construction as the search that ran.
        q = build_unified_filter(modes, first_name, last_name)

        # Page sort (e.g. ?sort=dob_asc) applied as a SQL ORDER BY on the
        # base query; empty = no ORDER BY (fastest plan, DB order).
        order_clause = (sort_field,) if sort_field else ()

        if not q and "trigram" not in modes:
            # No base mode could build a condition (e.g. only Levenshtein is
            # checked). Levenshtein is a precision filter on top of base
            # modes, not a standalone search, so a name query must not fall
            # back to the bare DOB set — that would silently ignore the name.
            # Only a nameless DOB search returns the DOB-filtered set.
            if date_of_birth and not first_name and not last_name:
                return list(self.filter(date_of_birth=date_of_birth).order_by(*order_clause)[:100])
            return []

        # Build main queryset
        qs = self.filter(q)
        if date_of_birth:
            qs = qs.filter(date_of_birth=date_of_birth)
        if order_clause:
            qs = qs.order_by(*order_clause)

        # Levenshtein as a precision filter (AND) on top of the other modes
        if "levenshtein" in modes:
            qs = apply_levenshtein_filter(qs, first_name, last_name)

        # Annotate match_source bitmask using SQL CASE expressions
        # prefix=1, legacy=2, soundex=4, dm=16 (trigram=32 is set in Python
        # for the KNN top-up rows, not in SQL)
        annotation_parts, annotation_params = _match_source_case(modes, first_name, last_name)

        if annotation_parts:
            sql = " | ".join(annotation_parts)
            qs = qs.annotate(_match_source=RawSQL(sql, annotation_params, output_field=IntegerField()))
        else:
            qs = qs.annotate(_match_source=Value(0, output_field=IntegerField()))

        # Build main list from non-trigram modes.
        # When trigram is the ONLY mode, q is empty — skip the unfiltered scan.
        # When trigram runs alongside base modes with a name, cap the base rows
        # at 60 so trigram rows get reserved slots on the page and are actually
        # visible (B6); without trigram the full 100-row page is base rows.
        main_list = []
        if bool(q):
            main_limit = 60 if "trigram" in modes and (first_name or last_name) else 100
            main_list = list(qs[:main_limit])
        main_ids = {obj.id for obj in main_list}

        # Trigram via separate query, merged in Python (trigram=32).
        # Uses <-> KNN index scan — fast for single names, chained ORDER BY for dual.
        if "trigram" in modes and (first_name or last_name):
            tri_qs = self
            if date_of_birth:
                tri_qs = tri_qs.filter(date_of_birth=date_of_birth)
            tri_qs = tri_qs.trigram_ordered(first_name, last_name)
            for obj in tri_qs[:100]:
                if obj.id not in main_ids:
                    obj._match_source = 32
                    main_list.append(obj)
                    main_ids.add(obj.id)
                    if len(main_list) >= 100:
                        break

        # Without a page sort, trigram rows (KNN distance order) are appended
        # after the base rows. With a page sort (e.g. DOB), the top-up rows
        # would break the page order, so the merged list is re-sorted — a
        # cheap Python sort over at most 100 already-fetched rows; the base
        # query's SQL ORDER BY still does the heavy lifting (cheap sort
        # input before LIMIT).
        if order_clause:
            descending = order_clause[0].startswith("-")
            field = order_clause[0].lstrip("-")
            main_list.sort(key=lambda r: getattr(r, field), reverse=descending)

        return main_list


class CourtRecordManager(models.Manager):
    """Manager for CourtRecord that uses CourtRecordQuerySet."""

    def get_queryset(self) -> CourtRecordQuerySet:
        return CourtRecordQuerySet(self.model, using=self._db)


class CourtRecord(models.Model):
    """A single raw court record for fuzzy name search.

    This is NOT a unified person entity: a real person appears across many
    duplicated court records (name variants, typos, aliases), so one person
    maps to many rows. Phonetic matching uses PostgreSQL's fuzzystrmatch
    extension (SOUNDEX, DAITCH_MOKOTOFF) directly in queries via functional
    indexes, rather than storing pre-computed tokens as columns.
    """

    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    middle_name = models.CharField(max_length=50, blank=True, null=True)
    date_of_birth = models.DateField()
    # Both fields below are populated by seed_data but currently UNUSED by
    # any search path (RECS-2026-08-14 P1-12, B16): cluster variants of one
    # person surface as separate result rows, and nickname expansion is
    # deliberately dropped from search_unified().
    nicknames = ArrayField(
        models.CharField(max_length=50),
        default=list,
        blank=True,
        help_text="Known nickname variants (e.g., ['Bill', 'Billy'] for William)",
    )
    person_id = UUIDField(
        default=uuid4,
        editable=False,
        help_text="Links records representing the same real person (same DOB, name variants, typos)",
    )

    objects = CourtRecordManager.from_queryset(CourtRecordQuerySet)()

    class Meta:
        indexes = [
            # Functional B-tree indexes for SOUNDEX equality comparisons
            models.Index(
                models.Func(models.F("first_name"), function="SOUNDEX", template="SOUNDEX(UPPER(%(expressions)s))"),
                name="idx_person_first_name_soundex",
            ),
            models.Index(
                models.Func(models.F("last_name"), function="SOUNDEX", template="SOUNDEX(UPPER(%(expressions)s))"),
                name="idx_person_last_name_soundex",
            ),
            # Functional GIN indexes for DAITCH_MOKOTOFF array overlap (&&)
            GinIndex(
                models.Func(
                    models.F("first_name"),
                    function="DAITCH_MOKOTOFF",
                    template="DAITCH_MOKOTOFF(UPPER(%(expressions)s))",
                ),
                name="idx_person_first_name_dm",
            ),
            GinIndex(
                models.Func(
                    models.F("last_name"),
                    function="DAITCH_MOKOTOFF",
                    template="DAITCH_MOKOTOFF(UPPER(%(expressions)s))",
                ),
                name="idx_person_last_name_dm",
            ),
            # GiST trigram indexes for pg_trgm similarity() and KNN (<->) ordering
            GistIndex(
                fields=["last_name"],
                name="idx_person_last_name_trgm",
                opclasses=["gist_trgm_ops"],
            ),
            GistIndex(
                fields=["first_name"],
                name="idx_person_first_name_trgm",
                opclasses=["gist_trgm_ops"],
            ),
            # Functional B-tree index for case-insensitive prefix matching
            models.Index(
                OpClass(
                    models.Func(models.F("last_name"), function="UPPER"),
                    name="text_pattern_ops",
                ),
                OpClass(
                    models.Func(models.F("first_name"), function="UPPER"),
                    name="text_pattern_ops",
                ),
                name="idx_person_name_prefix",
            ),
            # B-tree index for exact date_of_birth filtering
            models.Index(fields=["date_of_birth"], name="idx_person_date_of_birth"),
            # B-tree index for person_id cluster lookups
            models.Index(fields=["person_id"], name="idx_person_person_id"),
        ]
        verbose_name = "Court Record"
        verbose_name_plural = "Court Records"

    def __str__(self) -> str:
        name_parts = [self.first_name, self.last_name]
        if self.middle_name:
            name_parts.insert(1, self.middle_name)
        return " ".join(name_parts)
