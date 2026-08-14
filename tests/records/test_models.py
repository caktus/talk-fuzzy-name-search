"""Characterization tests for records.models.

Captures existing behavior of Person model, QuerySet methods, and phonetic search.
"""

from datetime import date

import pytest

from records.models import Person, PersonQuerySet
from tests.records.factories import PersonFactory

pytestmark = pytest.mark.django_db


class TestPersonModel:
    """Characterize Person model creation and behavior."""

    def test_create_person_minimal(self):
        """Person can be created with just first_name, last_name, and date_of_birth."""
        person = PersonFactory.create(
            first_name="John",
            last_name="Smith",
        )
        assert person.first_name == "John"
        assert person.last_name == "Smith"
        assert person.middle_name is None
        assert person.nicknames == []
        assert person.date_of_birth is not None

    def test_create_person_with_middle_name(self):
        """Person supports middle_name field."""
        person = Person.objects.create(
            first_name="John",
            last_name="Smith",
            middle_name="Michael",
            date_of_birth="1990-01-15",
        )
        assert person.middle_name == "Michael"

    def test_create_person_with_nicknames(self):
        """Person supports nicknames ArrayField."""
        person = Person.objects.create(
            first_name="Bill",
            last_name="Smith",
            date_of_birth="1985-06-20",
            nicknames=["William", "Billy"],
        )
        assert "William" in person.nicknames
        assert "Billy" in person.nicknames

    def test_person_str_representation(self):
        """Person __str__ includes first and last name."""
        person = Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth="1990-01-15",
        )
        assert "John" in str(person)
        assert "Smith" in str(person)


class TestPersonQuerySet:
    """Characterize PersonQuerySet search methods."""

    def test_search_phonetic_returns_queryset_empty_table(self):
        """search_phonetic returns a QuerySet without raising on empty table."""
        qs = Person.objects.search_phonetic("John", "Smith")
        assert isinstance(qs, PersonQuerySet)

    def test_search_trigram_returns_queryset_empty_table(self):
        """search_trigram returns a QuerySet without raising on empty table."""
        qs = Person.objects.search_trigram("John", "Smith")
        assert isinstance(qs, PersonQuerySet)

    def test_search_dm_returns_queryset_empty_table(self):
        """search_dm returns a QuerySet without raising on empty table."""
        qs = Person.objects.search_dm("John", "Smith")
        assert isinstance(qs, PersonQuerySet)

    def test_search_legacy_returns_queryset_empty_table(self):
        """search_legacy returns a QuerySet without raising on empty table."""
        qs = Person.objects.search_legacy("John", "Smith")
        assert isinstance(qs, PersonQuerySet)

    def test_search_legacy_finds_exact_match(self):
        """search_legacy finds records with exact name matches."""
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth="1990-01-15",
        )
        results = Person.objects.search_legacy("John", "Smith")
        assert results.count() >= 1

    def test_search_legacy_case_insensitive(self):
        """search_legacy is case-insensitive (icontains)."""
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth="1990-01-15",
        )
        results = Person.objects.search_legacy("john", "smith")
        assert results.count() >= 1


class TestDateOfBirthFilter:
    """Characterize the required date_of_birth field and filtering across all search modes."""

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        self.matching_dob = "1990-05-15"
        self.other_dob = "1985-01-01"
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth=self.matching_dob,
        )
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth=self.other_dob,
        )

    def test_search_legacy_filters_by_date_of_birth(self):
        results = Person.objects.search_legacy("John", "Smith", self.matching_dob)
        assert results.count() == 1
        assert str(results.first().date_of_birth) == self.matching_dob

    def test_search_exact_filters_by_date_of_birth(self):
        results = Person.objects.search_exact("John", "Smith", self.matching_dob)
        assert results.count() == 1

    def test_search_phonetic_filters_by_date_of_birth(self):
        results = Person.objects.search_phonetic("John", "Smith", self.matching_dob)
        assert results.count() == 1

    def test_search_dm_filters_by_date_of_birth(self):
        results = Person.objects.search_dm("John", "Smith", self.matching_dob)
        assert results.count() == 1

    def test_search_trigram_filters_by_date_of_birth(self):
        results = Person.objects.search_trigram("John", "Smith", self.matching_dob)
        assert results.count() == 1

    def test_search_trigram_date_of_birth_only(self):
        """date_of_birth alone (no names) is enough to trigger a trigram search."""
        results = Person.objects.search_trigram("", "", self.matching_dob)
        assert results.count() == 1

    def test_search_legacy_no_criteria_returns_empty(self):
        """With no name and no date_of_birth, search_legacy returns nothing."""
        results = Person.objects.search_legacy("", "")
        assert results.count() == 0


class TestLevenshteinOnlySemantics:
    """B1/B13: Levenshtein is a precision filter on top of base modes, not a standalone search."""

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        self.dob = date(1990, 5, 15)
        # Two people share the queried DOB: the pre-fix DOB fallback returned
        # both of them for any name.
        Person.objects.create(first_name="John", last_name="Smith", date_of_birth=self.dob)
        Person.objects.create(first_name="Robert", last_name="Jones", date_of_birth=self.dob)

    def test_levenshtein_only_name_and_dob_returns_nothing(self):
        """B1: name + DOB with only Levenshtein must not return everyone born that day."""
        results = Person.objects.search_unified(["levenshtein"], "Qzzz", "Zzzz", self.dob)
        assert list(results) == []

    def test_levenshtein_only_name_without_dob_returns_nothing(self):
        """B13: Levenshtein checked alone with a name yields no results."""
        results = Person.objects.search_unified(["levenshtein"], "Qzzz", "Zzzz")
        assert list(results) == []

    def test_levenshtein_only_dob_without_name_still_returns_dob_set(self):
        """No name given: the DOB-only early return is preserved."""
        results = Person.objects.search_unified(["levenshtein"], "", "", self.dob)
        assert len(list(results)) == 2

    def test_levenshtein_refines_base_mode(self):
        """Base mode + Levenshtein still narrows results (distance 1 in, distance 3 out)."""
        # "Joh" is a substring of "John" (legacy matches) at distance 1.
        found = Person.objects.search_unified(["legacy", "levenshtein"], "Joh", "Smith", self.dob)
        assert [(p.first_name, p.last_name) for p in found] == [("John", "Smith")]
        # "J" also matches legacy, but dist("J", "JOHN") = 3 — Levenshtein excludes it.
        assert list(Person.objects.search_unified(["legacy", "levenshtein"], "J", "Smith", self.dob)) == []
        # Control: legacy alone would have matched "J".
        assert len(Person.objects.search_unified(["legacy"], "J", "Smith", self.dob)) == 1


class TestDobOnlySearchCap:
    """B7: a DOB-only search is capped at 100 rows and returns a materialized list."""

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        self.dob = date(1990, 1, 1)

    def test_dob_only_capped_at_100(self):
        """150 people sharing one DOB -> exactly 100 rows returned."""
        for i in range(150):
            Person.objects.create(first_name=f"First{i}", last_name=f"Last{i}", date_of_birth=self.dob)
        results = Person.objects.search_unified([], "", "", self.dob)
        assert isinstance(results, list)
        assert len(results) == 100

    def test_dob_only_returns_all_when_under_100(self):
        """A DOB with fewer than 100 people returns all of them."""
        for i in range(3):
            Person.objects.create(first_name=f"First{i}", last_name=f"Last{i}", date_of_birth=self.dob)
        results = Person.objects.search_unified([], "", "", self.dob)
        assert isinstance(results, list)
        assert len(results) == 3

    def test_dob_only_returns_materialized_list(self):
        """The DOB-only path returns a Python list (fetched), not a lazy QuerySet."""
        Person.objects.create(first_name="John", last_name="Smith", date_of_birth=self.dob)
        results = Person.objects.search_unified([], "", "", self.dob)
        assert isinstance(results, list)
        assert len(results) == 1
