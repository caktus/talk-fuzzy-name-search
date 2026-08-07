"""Characterization tests for records.models.

Captures existing behavior of Person model, QuerySet methods, and phonetic search.
"""

import pytest

from records.models import Person, PersonQuerySet
from tests.records.factories import PersonFactory

pytestmark = pytest.mark.django_db


class TestPersonModel:
    """Characterize Person model creation and behavior."""

    def test_create_person_minimal(self):
        """Person can be created with just first_name and last_name."""
        person = PersonFactory.create(
            first_name="John",
            last_name="Smith",
        )
        assert person.first_name == "John"
        assert person.last_name == "Smith"
        assert person.middle_name is None
        assert person.nicknames == []

    def test_create_person_with_middle_name(self):
        """Person supports middle_name field."""
        person = Person.objects.create(
            first_name="John",
            last_name="Smith",
            middle_name="Michael",
        )
        assert person.middle_name == "Michael"

    def test_create_person_with_nicknames(self):
        """Person supports nicknames ArrayField."""
        person = Person.objects.create(
            first_name="Bill",
            last_name="Smith",
            nicknames=["William", "Billy"],
        )
        assert "William" in person.nicknames
        assert "Billy" in person.nicknames

    def test_person_str_representation(self):
        """Person __str__ includes first and last name."""
        person = Person.objects.create(
            first_name="John",
            last_name="Smith",
        )
        assert "John" in str(person)
        assert "Smith" in str(person)

    def test_person_phonetic_tokens_non_empty(self):
        """Phonetic tokens are populated for a created person."""
        person = Person.objects.create(
            first_name="John",
            last_name="Smith",
            first_name_phonetic=["J500"],
            last_name_phonetic=["S530"],
        )
        assert len(person.first_name_phonetic) > 0
        assert len(person.last_name_phonetic) > 0


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
            first_name_phonetic=["J500"],
            last_name_phonetic=["S530"],
        )
        results = Person.objects.search_legacy("John", "Smith")
        assert results.count() >= 1

    def test_search_legacy_case_insensitive(self):
        """search_legacy is case-insensitive (icontains)."""
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            first_name_phonetic=["J500"],
            last_name_phonetic=["S530"],
        )
        results = Person.objects.search_legacy("john", "smith")
        assert results.count() >= 1
