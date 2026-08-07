"""Characterization tests for records views.

Tests the mechanism-based search modes exposed by the search view.
"""

import pytest

from records.models import Person
from records.phonetics import dm_soundex_tokens, soundex_tokens

pytestmark = pytest.mark.django_db


def _phonetic(name):
    """Combined Soundex + Daitch-Mokotoff tokens, as stored in production data."""
    return list(dict.fromkeys(soundex_tokens(name) + dm_soundex_tokens(name)))


class TestSearchModes:
    """Test that ?mode=... dispatches to the right search mechanism."""

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        """Create test data: exact match + typo variant."""
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            first_name_phonetic=_phonetic("John"),
            last_name_phonetic=_phonetic("Smith"),
        )
        Person.objects.create(
            first_name="Jonh",
            last_name="Smyth",
            first_name_phonetic=_phonetic("Jonh"),
            last_name_phonetic=_phonetic("Smyth"),
        )
        Person.objects.create(
            first_name="Alice",
            last_name="Jones",
            first_name_phonetic=_phonetic("Alice"),
            last_name_phonetic=_phonetic("Jones"),
        )

    def _get_names(self, response):
        """Extract (first_name, last_name) tuples from search results in the response."""
        return set((p.first_name, p.last_name) for p in response.context["results"])

    def test_legacy_mode_finds_substring_match(self, client):
        """Legacy mode uses icontains and finds exact spelling."""
        response = client.get("/search/?mode=legacy&first_name=John&last_name=Smith")
        assert response.status_code == 200
        assert response.context["mode"] == "legacy"
        assert ("John", "Smith") in self._get_names(response)

    def test_prefix_mode_exact_only(self, client):
        """Prefix mode returns only exact prefix matches, no typo tolerance."""
        response = client.get("/search/?mode=prefix&first_name=John&last_name=Smith")
        assert response.status_code == 200
        assert response.context["mode"] == "prefix"
        names = self._get_names(response)
        assert ("John", "Smith") in names
        assert ("Jonh", "Smyth") not in names

    def test_phonetic_mode_finds_exact_match(self, client):
        """Phonetic mode returns phonetic matches."""
        response = client.get("/search/?mode=phonetic&first_name=John&last_name=Smith")
        assert response.status_code == 200
        assert response.context["mode"] == "phonetic"
        assert ("John", "Smith") in self._get_names(response)

    def test_dm_mode_finds_exact_match(self, client):
        """Daitch-Mokotoff mode returns phonetic matches."""
        response = client.get("/search/?mode=dm&first_name=John&last_name=Smith")
        assert response.status_code == 200
        assert response.context["mode"] == "dm"
        assert ("John", "Smith") in self._get_names(response)

    def test_default_mode_is_phonetic(self, client):
        """When mode is absent, the view defaults to phonetic."""
        response = client.get("/search/?first_name=John&last_name=Smith")
        assert response.status_code == 200
        assert response.context["mode"] == "phonetic"

    def test_unknown_mode_falls_back_to_phonetic(self, client):
        """An unrecognised mode falls back to phonetic rather than erroring."""
        response = client.get("/search/?mode=bogus&first_name=John&last_name=Smith")
        assert response.status_code == 200
        assert ("John", "Smith") in self._get_names(response)


class TestSearchExactQuerySet:
    """Test the search_exact QuerySet method."""

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            first_name_phonetic=soundex_tokens("John"),
            last_name_phonetic=soundex_tokens("Smith"),
        )
        Person.objects.create(
            first_name="Jonh",
            last_name="Smyth",
            first_name_phonetic=soundex_tokens("Jonh"),
            last_name_phonetic=soundex_tokens("Smyth"),
        )

    def test_search_exact_finds_prefix_match(self):
        """search_exact finds records matching the prefix."""
        results = Person.objects.search_exact("John", "Smith")
        assert results.count() == 1
        assert results.first().first_name == "John"

    def test_search_exact_case_insensitive(self):
        """search_exact is case-insensitive."""
        results = Person.objects.search_exact("john", "smith")
        assert results.count() == 1

    def test_search_exact_no_typo_tolerance(self):
        """search_exact does NOT find typo variants."""
        results = Person.objects.search_exact("John", "Smith")
        names = set(results.values_list("first_name", "last_name"))
        assert ("Jonh", "Smyth") not in names
