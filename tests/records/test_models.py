"""Characterization tests for records.models.

Captures existing behavior of CourtRecord model, QuerySet methods, and phonetic search.
"""

from datetime import date

import pytest

from records.models import CourtRecord, _phonetic_group
from tests.records.factories import CourtRecordFactory


@pytest.mark.django_db
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


@pytest.mark.django_db
class TestLevenshteinOnlySemantics:
    """Levenshtein is a precision filter on top of base modes, and can also run
    standalone (a full scan measured against the typed name)."""

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        self.dob = date(1990, 5, 15)
        # Two people share the queried DOB.
        CourtRecord.objects.create(first_name="John", last_name="Smith", date_of_birth=self.dob)
        CourtRecord.objects.create(first_name="Robert", last_name="Jones", date_of_birth=self.dob)

    def test_levenshtein_standalone_finds_near_name(self):
        """Standalone Levenshtein with a name runs the edit-distance search."""
        # dist("JAHN", "JOHN") = 1 and dist("SMITH", "SMITH") = 0 -> John Smith
        # matches; Robert Jones is far from both query names and is cut.
        results = CourtRecord.objects.search_unified(["levenshtein"], "Jahn", "Smith", self.dob)
        assert [(p.first_name, p.last_name) for p in results] == [("John", "Smith")]

    def test_levenshtein_standalone_no_near_match_returns_nothing(self):
        """Standalone Levenshtein with a name that has no near match returns nothing
        (a name query no longer falls back to the whole DOB set)."""
        results = CourtRecord.objects.search_unified(["levenshtein"], "Qzzz", "Zzzz", self.dob)
        assert list(results) == []
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


@pytest.mark.django_db
class TestDobOnlySearchCap:
    """B7: a DOB-only search is capped at RESULT_LIMIT (200) rows and returns a materialized list."""

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        self.dob = date(1990, 1, 1)

    def test_dob_only_capped_at_200(self):
        """250 people sharing one DOB -> exactly 200 rows returned."""
        for i in range(250):
            CourtRecord.objects.create(first_name=f"First{i}", last_name=f"Last{i}", date_of_birth=self.dob)
        results = CourtRecord.objects.search_unified([], "", "", self.dob)
        assert isinstance(results, list)
        assert len(results) == 200

    def test_dob_only_returns_all_when_under_limit(self):
        """A DOB with fewer than RESULT_LIMIT people returns all of them."""
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


@pytest.mark.django_db
class TestTrigramVisibility:
    """B6: trigram rows must stay visible when base modes would fill the whole page."""

    # Near-spelling variants of "Smith" that legacy (icontains) and prefix
    # (istartswith) never match, but trigram ranks close to "Smith" *and* clear
    # the 0.3 similarity() cutoff (TRIGRAM_SIMILARITY_CUTOFF) (Smit=0.57, Smitz/Smity/Smita/Smits=0.5).
    # (The old fixtures Smyth/Smythe/Smidt/Smyt/Smyths sit around the 0.3 cutoff
    # (0.18-0.33), so they were less reliable pins.)
    NEAR_NAMES = ["Smit", "Smitz", "Smity", "Smita", "Smits"]

    def _seed(self):
        dob = date(1990, 1, 1)
        for _ in range(150):
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
        """Base mode matches more than the reserved-slot base cap; trigram-only rows still make the page."""
        self._seed()
        results = CourtRecord.objects.search_unified(["legacy", "trigram"], "", "Smith")
        assert isinstance(results, list)
        assert len(results) <= 200

        names = [p.last_name for p in results]
        # All 150 base-mode matches are present, plus the trigram-only names.
        assert names.count("Smith") == 150
        for near in self.NEAR_NAMES:
            assert near in names

        main_rows = [p for p in results if p._match_source & 2]  # legacy bit
        tri_rows = [p for p in results if p._match_source == 32]  # trigram bit
        assert len(main_rows) == 120  # base rows capped, reserving trigram slots
        assert len(tri_rows) == 35  # 30 displaced Smiths + 5 near-spelling names

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
        """Without trigram the base list is not capped to the reduced trigram base cap."""
        dob = date(1990, 1, 1)
        expected_smith = [(f"First{i:02d}", "Smith") for i in range(70)]
        expected_smithers = [(f"First{i:02d}", "Smithers") for i in range(40)]
        expected = expected_smith + expected_smithers
        for first, last in reversed(expected):
            CourtRecord.objects.create(first_name=first, last_name=last, date_of_birth=dob)
        results = CourtRecord.objects.search_unified(["legacy"], "", "smith")
        # 110 matches, under RESULT_LIMIT: all of them come back (no cap applied).
        assert len(results) == 110
        # No default ORDER BY: assert the page contents, not a specific order.
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


@pytest.mark.django_db
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


@pytest.mark.django_db
class TestSearchUnifiedCap:
    """B16: search_unified() is annotated -> list[CourtRecord] and documented as limited to
    RESULT_LIMIT (200); no code path may return more than 200 rows."""

    def test_name_search_capped_at_200(self):
        """A name search with >200 matching rows returns at most 200 CourtRecord objects."""
        dob = date(1990, 1, 1)
        for i in range(250):
            CourtRecord.objects.create(first_name=f"First{i}", last_name="Smith", date_of_birth=dob)
        results = CourtRecord.objects.search_unified(["legacy"], "", "Smith")
        assert isinstance(results, list)
        assert len(results) <= 200
        assert all(isinstance(p, CourtRecord) for p in results)

    def test_dob_only_search_capped_at_200(self):
        """A DOB-only search with >200 matching rows returns at most 200."""
        dob = date(1990, 1, 1)
        for i in range(250):
            CourtRecord.objects.create(first_name=f"First{i}", last_name=f"Last{i}", date_of_birth=dob)
        results = CourtRecord.objects.search_unified([], "", "", dob)
        assert isinstance(results, list)
        assert len(results) <= 200

    def test_trigram_plus_base_capped_at_200(self):
        """Trigram + base mode: base rows (capped to reserve trigram slots)
        plus the KNN top-up never push the merged list past 200."""
        dob = date(1990, 1, 1)
        for _ in range(250):
            CourtRecord.objects.create(first_name="John", last_name="Smith", date_of_birth=dob)
        for first, last in [("Mary", "Smyth"), ("Tom", "Smythe"), ("Pat", "Smidt")]:
            CourtRecord.objects.create(first_name=first, last_name=last, date_of_birth=dob)
        results = CourtRecord.objects.search_unified(["legacy", "trigram"], "", "Smith")
        assert isinstance(results, list)
        assert len(results) <= 200
        assert all(isinstance(p, CourtRecord) for p in results)


@pytest.mark.django_db
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
        search does not expand 'Bob' to ROBERT/ROB/BOBBY via nickname variants.
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


@pytest.mark.django_db
class TestSoundexHeroCase:
    """P1-11 hero case: the unified soundex mode finds "John Smith" for "John Smyth".

    Smyth shares Soundex code S530 with Smith, so the soundex pre-filter
    accepts it.
    """

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        CourtRecord.objects.create(first_name="John", last_name="Smith", date_of_birth="1990-05-15")
        # Decoy: same first name, different last-name soundex (J553) — must not match.
        CourtRecord.objects.create(first_name="John", last_name="Jones", date_of_birth="1990-06-15")

    def test_unified_soundex_mode_finds_smith(self):
        """The unified search's OR-ed soundex group finds him as well."""
        results = CourtRecord.objects.search_unified(["soundex"], "John", "Smyth")
        assert [(p.first_name, p.last_name) for p in results] == [("John", "Smith")]


class TestPhoneticGroup:
    """Pure SQL-group builder for the soundex/dm branches of build_unified_filter (no DB)."""

    TEMPLATES = {
        "soundex": (
            "SOUNDEX(UPPER(first_name)) = SOUNDEX(%s)",
            "SOUNDEX(UPPER(last_name)) = SOUNDEX(%s)",
        ),
        "dm": (
            "DAITCH_MOKOTOFF(UPPER(first_name)) && DAITCH_MOKOTOFF(%s)",
            "DAITCH_MOKOTOFF(UPPER(last_name)) && DAITCH_MOKOTOFF(%s)",
        ),
    }

    @pytest.mark.parametrize("mode", ["soundex", "dm"])
    def test_both_names(self, mode):
        fn_tpl, ln_tpl = self.TEMPLATES[mode]
        sql, params = _phonetic_group(fn_tpl, ln_tpl, "John", "Smith", "JOHN", "SMITH")
        assert sql == f"({fn_tpl}) AND ({ln_tpl})"
        assert params == ["JOHN", "SMITH"]

    @pytest.mark.parametrize("mode", ["soundex", "dm"])
    def test_first_name_only(self, mode):
        fn_tpl, _ = self.TEMPLATES[mode]
        sql, params = _phonetic_group(*self.TEMPLATES[mode], "John", "", "JOHN", "")
        assert sql == fn_tpl
        assert params == ["JOHN"]

    @pytest.mark.parametrize("mode", ["soundex", "dm"])
    def test_last_name_only(self, mode):
        _, ln_tpl = self.TEMPLATES[mode]
        sql, params = _phonetic_group(*self.TEMPLATES[mode], "", "Smith", "", "SMITH")
        assert sql == ln_tpl
        assert params == ["SMITH"]

    @pytest.mark.parametrize("mode", ["soundex", "dm"])
    def test_no_names(self, mode):
        sql, params = _phonetic_group(*self.TEMPLATES[mode], "", "", "", "")
        assert sql is None
        assert params == []
