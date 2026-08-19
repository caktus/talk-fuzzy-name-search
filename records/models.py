"""Person model with phonetic name search.

Uses PostgreSQL's fuzzystrmatch extension (SOUNDEX, DAITCH_MOKOTOFF)
directly in queries via functional GIN/B-tree indexes, rather than
storing pre-computed phonetic tokens as columns.
"""

from __future__ import annotations

import datetime
from uuid import uuid4

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex, GistIndex, OpClass
from django.db import models
from django.db.models import F, Q, Value
from django.db.models.expressions import ExpressionWrapper, RawSQL
from django.db.models.fields import BooleanField, UUIDField
from django.db.models.functions import Upper

from .expressions import LevenshteinLessEqual
from .phonetics import resolve_variants

# Minimum pg_trgm similarity() a result must reach, applied per provided name
# by every trigram search path (standalone search_trigram, trigram_ordered,
# and therefore search_unified's trigram mode and the EXPLAIN endpoint).
# 0.3 (pg_trgm's own similarity_threshold default) keeps close variants (a
# deleted/inserted letter: similarity("Smit", "Smith") = 0.57,
# similarity("BEJNAMIN", "BENJAMIN") = 0.385) while still cutting most of the
# noise a bare KNN top-100 would surface for a rare spelling.
TRIGRAM_SIMILARITY_CUTOFF = 0.3


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


def _apply_trigram_similarity_filter(qs: PersonQuerySet, first_name: str, last_name: str) -> PersonQuerySet:
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
    query (see PersonQuerySet.trigram_ordered).

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

    if "soundex" in modes:
        fn_part = "SOUNDEX(UPPER(first_name)) = SOUNDEX(%s)" if first_name else None
        ln_part = "SOUNDEX(UPPER(last_name)) = SOUNDEX(%s)" if last_name else None
        if fn_part and ln_part:
            sql, params = f"({fn_part}) AND ({ln_part})", [fn_upper, ln_upper]
        elif fn_part:
            sql, params = fn_part, [fn_upper]
        elif ln_part:
            sql, params = ln_part, [ln_upper]
        else:
            sql, params = None, []
        if sql:
            q |= ExpressionWrapper(RawSQL(sql, params), output_field=BooleanField())

    if "dm" in modes:
        fn_part = "DAITCH_MOKOTOFF(UPPER(first_name)) && DAITCH_MOKOTOFF(%s)" if first_name else None
        ln_part = "DAITCH_MOKOTOFF(UPPER(last_name)) && DAITCH_MOKOTOFF(%s)" if last_name else None
        if fn_part and ln_part:
            sql, params = f"({fn_part}) AND ({ln_part})", [fn_upper, ln_upper]
        elif fn_part:
            sql, params = fn_part, [fn_upper]
        elif ln_part:
            sql, params = ln_part, [ln_upper]
        else:
            sql, params = None, []
        if sql:
            q |= ExpressionWrapper(RawSQL(sql, params), output_field=BooleanField())

    return q


def apply_levenshtein_filter(qs: PersonQuerySet, first_name: str, last_name: str) -> PersonQuerySet:
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


class PersonQuerySet(models.QuerySet):
    """Custom QuerySet for Person with phonetic name search methods."""

    def search_phonetic(
        self, first_name: str, last_name: str, date_of_birth: datetime.date | None = None
    ) -> PersonQuerySet:
        """Soundex + Levenshtein search using PostgreSQL fuzzystrmatch.

        Uses SOUNDEX() from the fuzzystrmatch extension for broad phonetic
        pre-filtering (with functional B-tree index support), followed by
        Levenshtein distance for precision filtering. The phonetic pre-filter
        is expanded across the query name's nickname variants
        (resolve_variants()), but the Levenshtein filter is measured against
        the query as typed — so only nicknames within edit distance 2 of the
        stored name match (e.g. 'Bil' -> 'Bill'); 'Bill' -> 'William'
        (distance 4) does not.

        Either name may be empty; only the provided name(s) are used to filter.
        """
        return self._phonetic_search(first_name, last_name, date_of_birth, is_array=False)

    def search_dm(self, first_name: str, last_name: str, date_of_birth: datetime.date | None = None) -> PersonQuerySet:
        """Daitch-Mokotoff + Levenshtein search using PostgreSQL fuzzystrmatch.

        Uses DAITCH_MOKOTOFF() from the fuzzystrmatch extension for broad
        phonetic pre-filtering (with functional GIN index support), followed
        by Levenshtein distance for precision filtering. DM handles vowels and
        multi-letter clusters differently, giving better coverage for
        Slavic/Germanic names. Like search_phonetic(), the phonetic
        pre-filter is expanded across the query's nickname variants
        (resolve_variants()) but the Levenshtein filter is measured against
        the query as typed, so nickname pairs farther than edit distance 2 do
        not match (e.g. 'Bob' -> 'Robert', distance 4).

        Either name may be empty; only the provided name(s) are used to filter.
        """
        return self._phonetic_search(first_name, last_name, date_of_birth, is_array=True)

    def _phonetic_search(
        self,
        first_name: str,
        last_name: str,
        date_of_birth: datetime.date | None,
        is_array: bool,
    ) -> PersonQuerySet:
        """Shared phonetic + Levenshtein search."""
        if not first_name and not last_name and not date_of_birth:
            return self.none()

        qs = self
        matched = False

        if first_name:
            qs, matched_first = self._apply_phonetic_filter(qs, "first_name", first_name, is_array)
            matched = matched or matched_first

        if last_name:
            qs, matched_last = self._apply_phonetic_filter(qs, "last_name", last_name, is_array)
            matched = matched or matched_last

        if date_of_birth:
            qs = qs.filter(date_of_birth=date_of_birth)
            matched = True

        if not matched:
            return self.none()

        return qs

    def _apply_phonetic_filter(
        self,
        qs: PersonQuerySet,
        field_name: str,
        query_name: str,
        is_array: bool,
    ) -> tuple[PersonQuerySet, bool]:
        """Apply phonetic pre-filter + Levenshtein precision filter on one name field.

        Returns (filtered_queryset, whether_any_filter_was_applied).
        """
        variants = resolve_variants(query_name)
        if not variants:
            return qs, False

        if is_array:
            # DM: text[] overlap — OR across all variants
            or_parts = " OR ".join(f"DAITCH_MOKOTOFF(UPPER({field_name})) && DAITCH_MOKOTOFF(%s)" for _ in variants)
            qs = qs.filter(
                ExpressionWrapper(
                    RawSQL(or_parts, [v.upper() for v in variants]),
                    output_field=BooleanField(),
                )
            )
        else:
            # Soundex: scalar equality — OR across all variants
            or_parts = " OR ".join(f"SOUNDEX(UPPER({field_name})) = SOUNDEX(%s)" for _ in variants)
            qs = qs.filter(
                ExpressionWrapper(
                    RawSQL(or_parts, [v.upper() for v in variants]),
                    output_field=BooleanField(),
                )
            )

        # Levenshtein precision filter
        qs = qs.annotate(
            **{
                f"_{field_name}_dist": LevenshteinLessEqual(
                    Upper(F(field_name)),
                    Value(query_name.upper()),
                    Value(2),
                )
            }
        ).filter(**{f"_{field_name}_dist__lte": 2})

        return qs, True

    def search_trigram(
        self, first_name: str, last_name: str, date_of_birth: datetime.date | None = None
    ) -> PersonQuerySet:
        """Trigram similarity search via pg_trgm KNN.

        Every provided name must reach ``TRIGRAM_SIMILARITY_CUTOFF`` (0.3) via
        ``similarity()`` — that cuts the noise a bare KNN top-100 would surface
        for a rare spelling.

        Single name: GiST index-ordered scan via ``<->`` after the cutoff.

        Both names: chained ``ORDER BY`` (``last_name <-> b, first_name <-> a``)
        uses the last_name GiST index for incremental-sort. Results ranked
        almost entirely by last-name closeness — first_name breaks ties only.
        """
        if not first_name and not last_name and not date_of_birth:
            return self.none()

        qs = self
        if date_of_birth:
            qs = qs.filter(date_of_birth=date_of_birth)
        qs = _apply_trigram_similarity_filter(qs, first_name, last_name)

        if first_name and last_name:
            return qs.annotate(
                _last_dist=RawSQL("(last_name <-> %s)", [last_name]),
                _first_dist=RawSQL("(first_name <-> %s)", [first_name]),
            ).order_by("_last_dist", "_first_dist")

        if last_name:
            return qs.annotate(_distance=RawSQL("(last_name <-> %s)", [last_name])).order_by("_distance")

        if first_name:
            return qs.annotate(_distance=RawSQL("(first_name <-> %s)", [first_name])).order_by("_distance")

        return qs

    def trigram_ordered(self, first_name: str, last_name: str) -> PersonQuerySet:
        """Apply the pg_trgm KNN ORDER BY used by search_unified()'s trigram mode.

        Each provided name is also cut at ``TRIGRAM_SIMILARITY_CUTOFF`` via
        ``similarity()`` — the EXPLAIN endpoint and search_unified() both build
        their queryset through here, so the explained SQL always matches the
        search SQL. Callers must ensure at least one name is provided.
        """
        self = _apply_trigram_similarity_filter(self, first_name, last_name)
        if first_name and last_name:
            return self.annotate(
                _last_dist=RawSQL("(last_name <-> %s)", [last_name]),
                _first_dist=RawSQL("(first_name <-> %s)", [first_name]),
            ).order_by("_last_dist", "_first_dist")
        if last_name:
            return self.annotate(_dist=RawSQL("(last_name <-> %s)", [last_name])).order_by("_dist")
        return self.annotate(_dist=RawSQL("(first_name <-> %s)", [first_name])).order_by("_dist")

    def search_legacy(
        self, first_name: str, last_name: str, date_of_birth: datetime.date | None = None
    ) -> PersonQuerySet:
        """Legacy LIKE-based search (for comparison/benchmarking).

        Uses unindexed LIKE '%name%' queries -- intentionally slow
        and incapable of fuzzy matching. Either name may be empty; only the
        provided name(s) are used to filter.
        """
        if not first_name and not last_name and not date_of_birth:
            return self.none()
        qs = self
        if first_name:
            qs = qs.filter(first_name__icontains=first_name)
        if last_name:
            qs = qs.filter(last_name__icontains=last_name)
        if date_of_birth:
            qs = qs.filter(date_of_birth=date_of_birth)
        return qs

    def search_exact(
        self, first_name: str, last_name: str, date_of_birth: datetime.date | None = None
    ) -> PersonQuerySet:
        """Exact startswith matching (fast, no fuzzy tolerance).

        Uses istartswith for prefix matching with B-tree index support.
        Either name may be empty; only the provided name(s) are used to filter.
        """
        if not first_name and not last_name and not date_of_birth:
            return self.none()
        qs = self
        if first_name:
            qs = qs.filter(first_name__istartswith=first_name)
        if last_name:
            qs = qs.filter(last_name__istartswith=last_name)
        if date_of_birth:
            qs = qs.filter(date_of_birth=date_of_birth)
        return qs

    def search_unified(
        self,
        modes: list[str],
        first_name: str,
        last_name: str,
        date_of_birth: datetime.date | None = None,
    ) -> list[Person]:
        """Unified search combining multiple algorithms (OR-ed Q object; trigram rows merged in Python from a separate KNN query).

        Each enabled base mode adds its condition to an OR-ed Q object.
        Trigram runs as a separate ORDER BY KNN query whose rows are merged
        in (not a SQL filter).

        Nickname expansion is deliberately dropped for the unified search:
        no base filter consults NICKNAME_MAP and the Levenshtein refinement
        is measured against the query as typed, so 'Bill' does not find
        'William' (distance 4). See RECS-2026-08-14 P1-12 — nickname support
        would require variant-aware filtering.

        Args:
            modes: List of enabled mode names (e.g., ['prefix', 'soundex']).
            first_name: First name to search for.
            last_name: Last name to search for.
            date_of_birth: Optional DOB filter applied to all modes.

        Returns:
            A list of Person objects (deduplicated, limited to 100). Every path
            materializes and caps at 100, so callers get a bounded, already-
            fetched list and timing around the call covers the DB fetch.
        """
        from django.db.models import IntegerField

        if not first_name and not last_name and not date_of_birth:
            return []

        fn_upper = first_name.upper() if first_name else ""
        ln_upper = last_name.upper() if last_name else ""

        # Base-mode conditions, shared with the EXPLAIN endpoint so both
        # use the exact same query construction as the search that ran.
        q = build_unified_filter(modes, first_name, last_name)

        if not q and "trigram" not in modes:
            # No base mode could build a condition (e.g. only Levenshtein is
            # checked). Levenshtein is a precision filter on top of base
            # modes, not a standalone search, so a name query must not fall
            # back to the bare DOB set — that would silently ignore the name.
            # Only a nameless DOB search returns the DOB-filtered set.
            if date_of_birth and not first_name and not last_name:
                return list(self.filter(date_of_birth=date_of_birth)[:100])
            return []

        # Build main queryset
        qs = self.filter(q)
        if date_of_birth:
            qs = qs.filter(date_of_birth=date_of_birth)
        # Explicit, stable ordering for the main list (B6): the page must not
        # render in arbitrary DB order. Rides the (last_name, first_name)
        # composite index, so the ORDER BY + LIMIT stays cheap.
        qs = qs.order_by("last_name", "first_name")

        # Levenshtein as a precision filter (AND) on top of the other modes
        if "levenshtein" in modes:
            qs = apply_levenshtein_filter(qs, first_name, last_name)

        # Annotate match_source bitmask using SQL CASE expressions
        # prefix=1, legacy=2, soundex=4, dm=16 (trigram=32 is set in Python
        # for the KNN top-up rows, not in SQL)
        annotation_parts = []
        annotation_params = []

        if "prefix" in modes and first_name and last_name:
            annotation_parts.append(
                "CASE WHEN UPPER(first_name) LIKE %s AND UPPER(last_name) LIKE %s THEN 1 ELSE 0 END"
            )
            annotation_params.extend([fn_upper + "%", ln_upper + "%"])
        elif "prefix" in modes and first_name:
            annotation_parts.append("CASE WHEN UPPER(first_name) LIKE %s THEN 1 ELSE 0 END")
            annotation_params.append(fn_upper + "%")
        elif "prefix" in modes and last_name:
            annotation_parts.append("CASE WHEN UPPER(last_name) LIKE %s THEN 1 ELSE 0 END")
            annotation_params.append(ln_upper + "%")

        if "legacy" in modes and first_name and last_name:
            annotation_parts.append("CASE WHEN first_name ILIKE %s AND last_name ILIKE %s THEN 2 ELSE 0 END")
            annotation_params.extend(["%" + first_name + "%", "%" + last_name + "%"])
        elif "legacy" in modes and first_name:
            annotation_parts.append("CASE WHEN first_name ILIKE %s THEN 2 ELSE 0 END")
            annotation_params.append("%" + first_name + "%")
        elif "legacy" in modes and last_name:
            annotation_parts.append("CASE WHEN last_name ILIKE %s THEN 2 ELSE 0 END")
            annotation_params.append("%" + last_name + "%")

        if "soundex" in modes and first_name and last_name:
            annotation_parts.append(
                "CASE WHEN SOUNDEX(UPPER(first_name)) = SOUNDEX(%s) AND SOUNDEX(UPPER(last_name)) = SOUNDEX(%s) THEN 4 ELSE 0 END"
            )
            annotation_params.extend([fn_upper, ln_upper])
        elif "soundex" in modes and first_name:
            annotation_parts.append("CASE WHEN SOUNDEX(UPPER(first_name)) = SOUNDEX(%s) THEN 4 ELSE 0 END")
            annotation_params.append(fn_upper)
        elif "soundex" in modes and last_name:
            annotation_parts.append("CASE WHEN SOUNDEX(UPPER(last_name)) = SOUNDEX(%s) THEN 4 ELSE 0 END")
            annotation_params.append(ln_upper)

        if "dm" in modes and first_name and last_name:
            annotation_parts.append(
                "CASE WHEN DAITCH_MOKOTOFF(UPPER(first_name)) && DAITCH_MOKOTOFF(%s) AND DAITCH_MOKOTOFF(UPPER(last_name)) && DAITCH_MOKOTOFF(%s) THEN 16 ELSE 0 END"
            )
            annotation_params.extend([fn_upper, ln_upper])
        elif "dm" in modes and first_name:
            annotation_parts.append(
                "CASE WHEN DAITCH_MOKOTOFF(UPPER(first_name)) && DAITCH_MOKOTOFF(%s) THEN 16 ELSE 0 END"
            )
            annotation_params.append(fn_upper)
        elif "dm" in modes and last_name:
            annotation_parts.append(
                "CASE WHEN DAITCH_MOKOTOFF(UPPER(last_name)) && DAITCH_MOKOTOFF(%s) THEN 16 ELSE 0 END"
            )
            annotation_params.append(ln_upper)

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

        return main_list


class PersonManager(models.Manager):
    """Manager for Person that uses PersonQuerySet."""

    def get_queryset(self) -> PersonQuerySet:
        return PersonQuerySet(self.model, using=self._db)


class Person(models.Model):
    """A person record for fuzzy name search.

    Phonetic matching uses PostgreSQL's fuzzystrmatch extension
    (SOUNDEX, DAITCH_MOKOTOFF) directly in queries via functional indexes,
    rather than storing pre-computed tokens as columns.
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

    objects = PersonManager.from_queryset(PersonQuerySet)()

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
        verbose_name = "Person"
        verbose_name_plural = "People"

    def __str__(self) -> str:
        name_parts = [self.first_name, self.last_name]
        if self.middle_name:
            name_parts.insert(1, self.middle_name)
        return " ".join(name_parts)
