"""Characterization tests for records.models.

Captures existing behavior of CourtRecord model, QuerySet methods, and phonetic search.
"""

from datetime import date

import pytest

from records.models import CourtRecord, CourtRecordQuerySet
from tests.records.factories import CourtRecordFactory

pytestmark = pytest.mark.django_db


class TestCourtRecordModel:
    """Characterize CourtRecord model creation and behavior."""

    def test_create_court_record_minimal(self):
        """CourtRecord can be created with just first_name, last_name, and date_of_birth."""
        person = CourtRecordFactory.create(
            first_name="John",
            last_name="Smith",
        )
        assert person.first_name == "John"
        assert person.last_name == "Smith"
        assert person.middle_name is None
        assert person.nicknames == []
        assert person.date_of_birth is not None

    def test_create_court_record_with_middle_name(self):
        """CourtRecord supports middle_name field."""
        person = CourtRecord.objects.create(
            first_name="John",
            last_name="Smith",
            middle_name="Michael",
            date_of_birth="1990-01-15",
        )
        assert person.middle_name == "Michael"

    def test_create_court_record_with_nicknames(self):
        """CourtRecord supports nicknames ArrayField."""
        person = CourtRecord.objects.create(
            first_name="Bill",
            last_name="Smith",
            date_of_birth="1985-06-20",
            nicknames=["William", "Billy"],
        )
        assert "William" in person.nicknames
        assert "Billy" in person.nicknames

    def test_court_record_str_representation(self):
        """CourtRecord __str__ includes first and last name."""
        person = CourtRecord.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth="1990-01-15",
        )
        assert "John" in str(person)
        assert "Smith" in str(person)


class TestCourtRecordQuerySet:
    """Characterize CourtRecordQuerySet search methods."""

    def test_search_phonetic_returns_queryset_empty_table(self):
        """search_phonetic returns a QuerySet without raising on empty table."""
        qs = CourtRecord.objects.search_phonetic("John", "Smith")
        assert isinstance(qs, CourtRecordQuerySet)

    def test_search_trigram_returns_queryset_empty_table(self):
        """search_trigram returns a QuerySet without raising on empty table."""
        qs = CourtRecord.objects.search_trigram("John", "Smith")
        assert isinstance(qs, CourtRecordQuerySet)

    def test_search_dm_returns_queryset_empty_table(self):
        """search_dm returns a QuerySet without raising on empty table."""
        qs = CourtRecord.objects.search_dm("John", "Smith")
        assert isinstance(qs, CourtRecordQuerySet)

    def test_search_legacy_returns_queryset_empty_table(self):
        """search_legacy returns a QuerySet without raising on empty table."""
        qs = CourtRecord.objects.search_legacy("John", "Smith")
        assert isinstance(qs, CourtRecordQuerySet)

    def test_search_legacy_finds_exact_match(self):
        """search_legacy finds records with exact name matches."""
        CourtRecord.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth="1990-01-15",
        )
        results = CourtRecord.objects.search_legacy("John", "Smith")
        assert results.count() >= 1

    def test_search_legacy_case_insensitive(self):
        """search_legacy is case-insensitive (icontains)."""
        CourtRecord.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth="1990-01-15",
        )
        results = CourtRecord.objects.search_legacy("john", "smith")
        assert results.count() >= 1


class TestDateOfBirthFilter:
    """Characterize the required date_of_birth field and filtering across all search modes."""

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        self.matching_dob = "1990-05-15"
        self.other_dob = "1985-01-01"
        CourtRecord.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth=self.matching_dob,
        )
        CourtRecord.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth=self.other_dob,
        )

    def test_search_legacy_filters_by_date_of_birth(self):
        results = CourtRecord.objects.search_legacy("John", "Smith", self.matching_dob)
        assert results.count() == 1
        assert str(results.first().date_of_birth) == self.matching_dob

    def test_search_exact_filters_by_date_of_birth(self):
        results = CourtRecord.objects.search_exact("John", "Smith", self.matching_dob)
        assert results.count() == 1

    def test_search_phonetic_filters_by_date_of_birth(self):
        results = CourtRecord.objects.search_phonetic("John", "Smith", self.matching_dob)
        assert results.count() == 1

    def test_search_dm_filters_by_date_of_birth(self):
        results = CourtRecord.objects.search_dm("John", "Smith", self.matching_dob)
        assert results.count() == 1

    def test_search_trigram_filters_by_date_of_birth(self):
        results = CourtRecord.objects.search_trigram("John", "Smith", self.matching_dob)
        assert results.count() == 1

    def test_search_trigram_date_of_birth_only(self):
        """date_of_birth alone (no names) is enough to trigger a trigram search."""
        results = CourtRecord.objects.search_trigram("", "", self.matching_dob)
        assert results.count() == 1

    def test_search_legacy_no_criteria_returns_empty(self):
        """With no name and no date_of_birth, search_legacy returns nothing."""
        results = CourtRecord.objects.search_legacy("", "")
        assert results.count() == 0


class TestLevenshteinOnlySemantics:
    """B1/B13: Levenshtein is a precision filter on top of base modes, not a standalone search."""

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        self.dob = date(1990, 5, 15)
        # Two people share the queried DOB: the pre-fix DOB fallback returned
        # both of them for any name.
        CourtRecord.objects.create(first_name="John", last_name="Smith", date_of_birth=self.dob)
        CourtRecord.objects.create(first_name="Robert", last_name="Jones", date_of_birth=self.dob)

    def test_levenshtein_only_name_and_dob_returns_nothing(self):
        """B1: name + DOB with only Levenshtein must not return everyone born that day."""
        results = CourtRecord.objects.search_unified(["levenshtein"], "Qzzz", "Zzzz", self.dob)
        assert list(results) == []

    def test_levenshtein_only_name_without_dob_returns_nothing(self):
        """B13: Levenshtein checked alone with a name yields no results."""
        results = CourtRecord.objects.search_unified(["levenshtein"], "Qzzz", "Zzzz")
        assert list(results) == []

    def test_levenshtein_only_dob_without_name_still_returns_dob_set(self):
        """No name given: the DOB-only early return is preserved."""
        results = CourtRecord.objects.search_unified(["levenshtein"], "", "", self.dob)
        assert len(list(results)) == 2

    def test_levenshtein_refines_base_mode(self):
        """Base mode + Levenshtein still narrows results (distance 1 in, distance 3 out)."""
        # "Joh" is a substring of "John" (legacy matches) at distance 1.
        found = CourtRecord.objects.search_unified(["legacy", "levenshtein"], "Joh", "Smith", self.dob)
        assert [(p.first_name, p.last_name) for p in found] == [("John", "Smith")]
        # "J" also matches legacy, but dist("J", "JOHN") = 3 — Levenshtein excludes it.
        assert list(CourtRecord.objects.search_unified(["legacy", "levenshtein"], "J", "Smith", self.dob)) == []
        # Control: legacy alone would have matched "J".
        assert len(CourtRecord.objects.search_unified(["legacy"], "J", "Smith", self.dob)) == 1


class TestDobOnlySearchCap:
    """B7: a DOB-only search is capped at 100 rows and returns a materialized list."""

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        self.dob = date(1990, 1, 1)

    def test_dob_only_capped_at_100(self):
        """150 people sharing one DOB -> exactly 100 rows returned."""
        for i in range(150):
            CourtRecord.objects.create(first_name=f"First{i}", last_name=f"Last{i}", date_of_birth=self.dob)
        results = CourtRecord.objects.search_unified([], "", "", self.dob)
        assert isinstance(results, list)
        assert len(results) == 100

    def test_dob_only_returns_all_when_under_100(self):
        """A DOB with fewer than 100 people returns all of them."""
        for i in range(3):
            CourtRecord.objects.create(first_name=f"First{i}", last_name=f"Last{i}", date_of_birth=self.dob)
        results = CourtRecord.objects.search_unified([], "", "", self.dob)
        assert isinstance(results, list)
        assert len(results) == 3

    def test_dob_only_returns_materialized_list(self):
        """The DOB-only path returns a Python list (fetched), not a lazy QuerySet."""
        CourtRecord.objects.create(first_name="John", last_name="Smith", date_of_birth=self.dob)
        results = CourtRecord.objects.search_unified([], "", "", self.dob)
        assert isinstance(results, list)
        assert len(results) == 1


class TestTrigramVisibility:
    """B6: trigram rows must stay visible when base modes would fill the whole page."""

    # Near-spelling variants of "Smith" that legacy (icontains) and prefix
    # (istartswith) never match, but trigram ranks close to "Smith" *and* clear
    # the 0.4 similarity() cutoff (Smit=0.57, Smitz/Smity/Smita/Smits=0.5).
    # (The old fixtures Smyth/Smythe/Smidt/Smyt/Smyths were all <0.4 and would be
    # cut by the new threshold.)
    NEAR_NAMES = ["Smit", "Smitz", "Smity", "Smita", "Smits"]

    def _seed(self):
        dob = date(1990, 1, 1)
        for _ in range(75):
            CourtRecord.objects.create(first_name="John", last_name="Smith", date_of_birth=dob)
        for first, last in [
            ("Mary", "Smit"),
            ("Tom", "Smitz"),
            ("Pat", "Smity"),
            ("Lee", "Smita"),
            ("Kim", "Smits"),
        ]:
            CourtRecord.objects.create(first_name=first, last_name=last, date_of_birth=dob)

    def test_trigram_rows_visible_with_many_base_matches(self):
        """Base mode matches >60 rows; trigram-only rows still make the page."""
        self._seed()
        results = CourtRecord.objects.search_unified(["legacy", "trigram"], "", "Smith")
        assert isinstance(results, list)
        assert len(results) <= 100

        names = [p.last_name for p in results]
        # All 75 base-mode matches are present, plus the trigram-only names.
        assert names.count("Smith") == 75
        for near in self.NEAR_NAMES:
            assert near in names

        main_rows = [p for p in results if p._match_source & 2]  # legacy bit
        tri_rows = [p for p in results if p._match_source == 32]  # trigram bit
        assert len(main_rows) == 60  # base rows capped, reserving trigram slots
        assert len(tri_rows) == 20  # 15 displaced Smiths + 5 near-spelling names

        # Main rows come first, then the trigram top-up (KNN order, closest first).
        first_tri = next(i for i, p in enumerate(results) if p._match_source == 32)
        assert all(p._match_source & 2 for p in results[:first_tri])
        assert tri_rows[0].last_name == "Smith"  # distance 0 beats the near names

    def test_trigram_only_rows_absent_without_trigram(self):
        """Control: legacy/prefix never surface the near-spelling names."""
        self._seed()
        for modes in (["legacy"], ["prefix"], ["legacy", "prefix"]):
            results = CourtRecord.objects.search_unified(modes, "", "Smith")
            names = {p.last_name for p in results}
            assert not names & set(self.NEAR_NAMES)

    def test_no_trigram_keeps_full_base_page(self):
        """Without trigram the base list keeps the full 100-row page."""
        dob = date(1990, 1, 1)
        expected_smith = [(f"First{i:02d}", "Smith") for i in range(70)]
        expected_smithers = [(f"First{i:02d}", "Smithers") for i in range(40)]
        expected = expected_smith + expected_smithers
        for first, last in reversed(expected):
            CourtRecord.objects.create(first_name=first, last_name=last, date_of_birth=dob)
        results = CourtRecord.objects.search_unified(["legacy"], "", "smith")
        assert len(results) == 100
        # No default ORDER BY: assert the page contents, not a specific order
        # (a tie on LIMIT 100 can land on any subset). Smith has only 70
        # rows, so the page must always include at least 30 Smithers rows.
        assert {p.last_name for p in results} == {"Smith", "Smithers"}
        assert sum(1 for p in results if p.last_name == "Smithers") >= 30

    def test_main_list_has_no_default_ordering(self):
        """search_unified() runs without a default ORDER BY; page sort is the view's job."""
        dob = date(1990, 1, 1)
        for first, last in [("Zed", "Zebra"), ("Ike", "Cherry"), ("Amy", "Apple"), ("Bob", "Berry")]:
            CourtRecord.objects.create(first_name=first, last_name=last, date_of_birth=dob)
        results = CourtRecord.objects.search_unified(["legacy"], "", "e")
        assert {(p.first_name, p.last_name) for p in results} == {
            ("Amy", "Apple"),
            ("Bob", "Berry"),
            ("Ike", "Cherry"),
            ("Zed", "Zebra"),
        }


class TestSearchUnifiedSortField:
    """The page sort is a SQL ORDER BY on the base query (sort_field)."""

    def _seed(self):
        # Shuffled DOBs so order is observable
        for first, dob in [("Zed", "1992-06-15"), ("Ike", "1988-01-01"), ("Amy", "1995-12-31"), ("Bob", "1990-05-15")]:
            CourtRecord.objects.create(first_name=first, last_name="Smith", date_of_birth=dob)

    def test_sort_field_runs_sql_order_by(self):
        """The ORDER BY is in the SQL the base query executes, not Python."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self._seed()
        with CaptureQueriesContext(connection) as ctx:
            results = CourtRecord.objects.search_unified(["prefix"], "", "Smith", sort_field="-date_of_birth")
        main_sql = next(q["sql"] for q in ctx.captured_queries if "records_courtrecord" in q["sql"])
        assert 'ORDER BY "records_courtrecord"."date_of_birth" DESC' in main_sql
        # Results come back already sorted by the DB
        assert [p.first_name for p in results] == ["Amy", "Zed", "Bob", "Ike"]

    def test_sort_field_asc(self):
        self._seed()
        results = CourtRecord.objects.search_unified(["prefix"], "", "Smith", sort_field="date_of_birth")
        assert [p.first_name for p in results] == ["Ike", "Bob", "Zed", "Amy"]

    def test_dob_only_search_sorted(self):
        dob = date(1990, 1, 1)
        for last, other_dob in [("A", dob), ("B", dob), ("C", dob)]:
            CourtRecord.objects.create(first_name="John", last_name=last, date_of_birth=other_dob)
        results = CourtRecord.objects.search_unified([], "", "", dob, sort_field="first_name")
        assert [p.last_name for p in results] == ["A", "B", "C"]


class TestSearchUnifiedCap:
    """B16: search_unified() is annotated -> list[CourtRecord] and documented as limited to 100;
    no code path may return more than 100 rows."""

    def test_name_search_capped_at_100(self):
        """A name search with >100 matching rows returns at most 100 CourtRecord objects."""
        dob = date(1990, 1, 1)
        for i in range(150):
            CourtRecord.objects.create(first_name=f"First{i}", last_name="Smith", date_of_birth=dob)
        results = CourtRecord.objects.search_unified(["legacy"], "", "Smith")
        assert isinstance(results, list)
        assert len(results) <= 100
        assert all(isinstance(p, CourtRecord) for p in results)

    def test_dob_only_search_capped_at_100(self):
        """A DOB-only search with >100 matching rows returns at most 100."""
        dob = date(1990, 1, 1)
        for i in range(150):
            CourtRecord.objects.create(first_name=f"First{i}", last_name=f"Last{i}", date_of_birth=dob)
        results = CourtRecord.objects.search_unified([], "", "", dob)
        assert isinstance(results, list)
        assert len(results) <= 100

    def test_trigram_plus_base_capped_at_100(self):
        """Trigram + base mode: base rows (capped at 60 to reserve trigram slots)
        plus the KNN top-up never push the merged list past 100."""
        dob = date(1990, 1, 1)
        for _ in range(120):
            CourtRecord.objects.create(first_name="John", last_name="Smith", date_of_birth=dob)
        for first, last in [("Mary", "Smyth"), ("Tom", "Smythe"), ("Pat", "Smidt")]:
            CourtRecord.objects.create(first_name=first, last_name=last, date_of_birth=dob)
        results = CourtRecord.objects.search_unified(["legacy", "trigram"], "", "Smith")
        assert isinstance(results, list)
        assert len(results) <= 100
        assert all(isinstance(p, CourtRecord) for p in results)


class TestUnifiedSearchNicknameLimitation:
    """P1-12: unified search does not expand nicknames — pins the current behavior.

    No base filter consults NICKNAME_MAP and the Levenshtein refinement is
    measured against the query as typed, so nickname-to-canonical pairs
    farther than edit distance 2 do not match. If these start passing, the
    change was deliberate — update these tests alongside the docstrings and
    UI copy (see RECS-2026-08-14 P1-12).
    """

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        self.dob = date(1990, 5, 15)
        CourtRecord.objects.create(first_name="William", last_name="Smith", date_of_birth=self.dob)
        CourtRecord.objects.create(first_name="Robert", last_name="Smith", date_of_birth=self.dob)
        CourtRecord.objects.create(first_name="Bill", last_name="Smith", date_of_birth=self.dob)

    def test_unified_bill_does_not_find_william(self):
        """'Bill' does not surface 'William Smith' via legacy + Levenshtein.

        Fails at both stages: 'WILLIAM' has no 'BILL' substring (legacy base
        filter), and the Levenshtein refinement is measured against the query
        as typed — dist('WILLIAM', 'BILL') = 4 > 2 — with no variant
        expansion to WILLIAM/BILLY/WILL. The exact 'Bill Smith' row still
        matches, so the search itself is not vacuous.
        """
        results = CourtRecord.objects.search_unified(["legacy", "levenshtein"], "Bill", "Smith", self.dob)
        assert [(p.first_name, p.last_name) for p in results] == [("Bill", "Smith")]

    def test_unified_dm_bob_does_not_find_robert(self):
        """'Bob' does not find 'Robert Smith' in dm mode.

        Fails at the DM pre-filter: DAITCH_MOKOTOFF('BOB') (['770000']) and
        DAITCH_MOKOTOFF('ROBERT') (['979300']) share no code, and the unified
        search does not expand 'Bob' to ROBERT/ROB/BOBBY via resolve_variants().
        """
        results = CourtRecord.objects.search_unified(["dm"], "Bob", "Smith", self.dob)
        assert [(p.first_name, p.last_name) for p in results] == []

    def test_unified_levenshtein_still_finds_distance_one_typo(self):
        """Positive control: 'Bil' does find 'Bill Smith' (distance 1 ≤ 2).

        Ensures the negative tests pin the nickname limitation rather than a
        broken Levenshtein refinement.
        """
        results = CourtRecord.objects.search_unified(["legacy", "levenshtein"], "Bil", "Smith", self.dob)
        assert [(p.first_name, p.last_name) for p in results] == [("Bill", "Smith")]

    def test_standalone_search_dm_still_requires_distance_two(self):
        """Even standalone search_dm() does not find 'Robert' for 'Bob'.

        The phonetic pre-filter does expand 'Bob' to ROBERT/ROB/BOBBY via
        resolve_variants(), but the Levenshtein precision filter is measured
        against the query as typed: dist('ROBERT', 'BOB') = 4 > 2. Pins the
        search_dm() docstring claim.
        """
        results = CourtRecord.objects.search_dm("Bob", "Smith")
        assert [(p.first_name, p.last_name) for p in results] == []


class TestSearchPhoneticHeroCase:
    """P1-11 hero case: search_phonetic("John", "Smyth") finds "John Smith".

    Smyth/Smythe share Soundex code S530 with Smith and sit within edit
    distance 2, so both the Soundex pre-filter and the Levenshtein
    refinement accept them.
    """

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        CourtRecord.objects.create(first_name="John", last_name="Smith", date_of_birth="1990-05-15")
        # Decoy: same first name, different last-name soundex (J553) — must not match.
        CourtRecord.objects.create(first_name="John", last_name="Jones", date_of_birth="1990-06-15")

    def test_smyth_finds_smith(self):
        """Typo variant 'Smyth' (distance 1) finds 'John Smith'."""
        results = CourtRecord.objects.search_phonetic("John", "Smyth")
        assert [(p.first_name, p.last_name) for p in results] == [("John", "Smith")]

    def test_smythe_finds_smith(self):
        """Variant 'Smythe' (distance 2) still matches within the tolerance."""
        results = CourtRecord.objects.search_phonetic("John", "Smythe")
        assert [(p.first_name, p.last_name) for p in results] == [("John", "Smith")]

    def test_unified_soundex_mode_finds_smith(self):
        """The unified search's OR-ed soundex group finds him as well."""
        results = CourtRecord.objects.search_unified(["soundex"], "John", "Smyth")
        assert [(p.first_name, p.last_name) for p in results] == [("John", "Smith")]
