"""Person model with phonetic name search.

This model stores person records with pre-computed phonetic tokens
for both first and last names, enabling fast fuzzy name search via
PostgreSQL GIN indexes on array overlap.

Note: A production system might compute phonetic tokens on-the-fly via
PostgreSQL's daitch_mokotoff() function with GIN indexes on the
expression. This demo stores tokens as columns for simplicity and
educational clarity.
"""

from __future__ import annotations

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex, GistIndex, OpClass
from django.db import models
from django.db.models import F, Value
from django.db.models.functions import Upper

from .phonetics import dm_soundex_tokens, soundex_tokens


class PersonQuerySet(models.QuerySet):
    """Custom QuerySet for Person with phonetic name search methods."""

    def search_phonetic(self, first_name: str, last_name: str) -> PersonQuerySet:
        """Soundex + Levenshtein search using custom ORM expressions.

        A broad phonetic pre-filter via GIN-indexed Soundex array overlap,
        followed by a Levenshtein precision filter with early exit. Typo-
        and nickname-tolerant. Either name may be empty; only the provided
        name(s) are used to filter.

        Args:
            first_name: The first name to search for.
            last_name: The last name to search for.

        Returns:
            A QuerySet of matching Person records.
        """
        return self._phonetic_overlap_search(first_name, last_name, soundex_tokens)

    def search_dm(self, first_name: str, last_name: str) -> PersonQuerySet:
        """Daitch-Mokotoff + Levenshtein search.

        Same two-stage approach as :meth:`search_phonetic`, but the broad
        pre-filter uses Daitch-Mokotoff codes instead of classic Soundex.
        DM handles vowels and multi-letter clusters differently, giving
        better coverage for Slavic/Germanic names. Either name may be
        empty; only the provided name(s) are used to filter.

        Args:
            first_name: The first name to search for.
            last_name: The last name to search for.

        Returns:
            A QuerySet of matching Person records.
        """
        return self._phonetic_overlap_search(first_name, last_name, dm_soundex_tokens)

    def _phonetic_overlap_search(self, first_name, last_name, token_fn) -> PersonQuerySet:
        """Shared phonetic-overlap + Levenshtein search for a token generator.

        Args:
            first_name: The first name to search for.
            last_name: The last name to search for.
            token_fn: Callable returning phonetic tokens for a name
                (e.g. ``soundex_tokens`` or ``dm_soundex_tokens``).

        Returns:
            A QuerySet of matching Person records.
        """
        from .expressions import LevenshteinLessEqual

        max_edit_distance = 2
        qs = self
        matched = False

        if first_name:
            first_tokens = token_fn(first_name)
            if not first_tokens:
                return self.none()
            qs = (
                qs.filter(
                    # Phonetic array overlap on first name (uses GIN index)
                    first_name_phonetic__overlap=first_tokens,
                )
                .annotate(
                    # Levenshtein distance on first name (early-exit optimization)
                    _first_dist=LevenshteinLessEqual(
                        Upper(F("first_name")),
                        Value(first_name.upper()),
                        Value(max_edit_distance),
                    ),
                )
                .filter(_first_dist__lte=max_edit_distance)
            )
            matched = True

        if last_name:
            last_tokens = token_fn(last_name)
            if not last_tokens:
                return self.none()
            qs = (
                qs.filter(
                    # Phonetic array overlap on last name (uses GIN index)
                    last_name_phonetic__overlap=last_tokens,
                )
                .annotate(
                    # Levenshtein distance on last name (early-exit optimization)
                    _last_dist=LevenshteinLessEqual(
                        Upper(F("last_name")),
                        Value(last_name.upper()),
                        Value(max_edit_distance),
                    ),
                )
                .filter(_last_dist__lte=max_edit_distance)
            )
            matched = True

        if not matched:
            return self.none()

        return qs

    def search_trigram(self, first_name: str, last_name: str) -> PersonQuerySet:
        """Trigram similarity search ordered by pg_trgm distance (KNN).

        Uses the GiST-indexed ``<->`` distance operator to return the
        nearest names first. Either name may be empty; only the provided
        name(s) contribute to the ordering.

        When only one name is given, this is a genuine index-ordered
        nearest-neighbour scan (``Index Scan ... Order By: col <-> 'x'``)
        with no similarity threshold to tune.

        When both names are given, PostgreSQL's GiST KNN scan can only
        drive the index-ordered scan off a *single* column's distance --
        it cannot use an index to order by a summed expression across two
        columns (``(first_name <-> a) + (last_name <-> b)`` forces a full
        parallel sequential scan + sort over all 50M rows, ~14s on this
        dataset). So instead we chain the ``ORDER BY`` clauses
        (``last_name <-> b, first_name <-> a``): PostgreSQL uses the
        last_name GiST index to drive an incremental-sort scan, and only
        falls back to comparing first_name distance to break near-ties in
        last_name distance. That's ~4x faster here (~3.3s vs ~14s), but it
        means results are ranked almost entirely by last-name closeness --
        first_name only matters when two people have (near-)identical
        last-name trigram distance. This is a real limitation of
        multi-column KNN in PostgreSQL, not a bug in this demo.

        Args:
            first_name: The first name to search for.
            last_name: The last name to search for.

        Returns:
            A QuerySet ordered from closest to farthest trigram match.
        """
        from django.db.models.expressions import RawSQL

        if not first_name and not last_name:
            return self.none()

        if first_name and last_name:
            # Chain KNN ordering: last_name drives the index-ordered scan,
            # first_name only breaks near-ties (see docstring above).
            return self.annotate(
                _last_dist=RawSQL("(last_name <-> %s)", [last_name]),
                _first_dist=RawSQL("(first_name <-> %s)", [first_name]),
            ).order_by("_last_dist", "_first_dist")

        if last_name:
            return self.annotate(_distance=RawSQL("(last_name <-> %s)", [last_name])).order_by("_distance")

        return self.annotate(_distance=RawSQL("(first_name <-> %s)", [first_name])).order_by("_distance")

    def search_legacy(self, first_name: str, last_name: str) -> PersonQuerySet:
        """Legacy LIKE-based search (for comparison/benchmarking).

        Uses unindexed LIKE '%name%' queries -- intentionally slow
        and incapable of fuzzy matching. Either name may be empty; only
        the provided name(s) are used to filter.

        Args:
            first_name: The first name to search for.
            last_name: The last name to search for.

        Returns:
            A QuerySet of matching Person records.
        """
        if not first_name and not last_name:
            return self.none()
        qs = self
        if first_name:
            qs = qs.filter(first_name__icontains=first_name)
        if last_name:
            qs = qs.filter(last_name__icontains=last_name)
        return qs

    def search_exact(self, first_name: str, last_name: str) -> PersonQuerySet:
        """Exact startswith matching (fast, no fuzzy tolerance).

        Uses istartswith for prefix matching with B-tree index support.
        Either name may be empty; only the provided name(s) are used to
        filter.

        Args:
            first_name: The first name to search for.
            last_name: The last name to search for.

        Returns:
            A QuerySet of matching Person records.
        """
        if not first_name and not last_name:
            return self.none()
        qs = self
        if first_name:
            qs = qs.filter(first_name__istartswith=first_name)
        if last_name:
            qs = qs.filter(last_name__istartswith=last_name)
        return qs


class PersonManager(models.Manager):
    """Manager for Person that uses PersonQuerySet."""

    def get_queryset(self) -> PersonQuerySet:
        return PersonQuerySet(self.model, using=self._db)


class Person(models.Model):
    """A person record with phonetic name tokens for fuzzy search.

    Stores Soundex and Daitch-Mokotoff phonetic tokens as array columns
    for fast GIN-indexed phonetic matching.

    Note: A production system might compute phonetic tokens on-the-fly
    via PostgreSQL's daitch_mokotoff() function. This demo stores tokens
    as columns for simplicity.
    """

    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    middle_name = models.CharField(max_length=50, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    nicknames = ArrayField(
        models.CharField(max_length=50),
        default=list,
        blank=True,
        help_text="Known nickname variants (e.g., ['Bill', 'Billy'] for William)",
    )

    # Phonetic token arrays -- pre-computed for fast GIN-indexed overlap
    first_name_phonetic = ArrayField(
        models.CharField(max_length=10),
        default=list,
        help_text="Soundex + DM tokens for first name and nickname variants",
    )
    last_name_phonetic = ArrayField(
        models.CharField(max_length=10),
        default=list,
        help_text="Soundex + DM tokens for last name",
    )

    objects = PersonManager.from_queryset(PersonQuerySet)()

    class Meta:
        indexes = [
            # GIN indexes for fast phonetic array overlap (&& operator)
            GinIndex(fields=["first_name_phonetic"], name="idx_person_first_phonetic"),
            GinIndex(fields=["last_name_phonetic"], name="idx_person_last_phonetic"),
            # GiST trigram indexes for pg_trgm similarity() and KNN (<->) ordering.
            # GiST supports index-ordered `ORDER BY col <-> 'x' LIMIT n` scans,
            # unlike GIN which can only filter (%) and must sort afterwards.
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
            # (e.g. UPPER(last_name) LIKE 'SMYTH%'). Composite on
            # (last_name, first_name) so it also serves combined
            # first+last prefix searches; last_name-only lookups use it
            # as the leading column.
            models.Index(
                OpClass(
                    models.Func(
                        models.F("last_name"),
                        function="UPPER",
                    ),
                    name="text_pattern_ops",
                ),
                OpClass(
                    models.Func(
                        models.F("first_name"),
                        function="UPPER",
                    ),
                    name="text_pattern_ops",
                ),
                name="idx_person_name_prefix",
            ),
        ]
        verbose_name = "Person"
        verbose_name_plural = "People"

    def __str__(self) -> str:
        name_parts = [self.first_name, self.last_name]
        if self.middle_name:
            name_parts.insert(1, self.middle_name)
        return " ".join(name_parts)
