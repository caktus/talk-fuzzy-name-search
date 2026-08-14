"""Characterization tests for records views.

Tests the checkbox-based unified search with multiple modes.
"""

import re
from datetime import date

import pytest

from records.models import Person

pytestmark = pytest.mark.django_db


class TestSearchModes:
    """Test that ?modes=... dispatches to the right search mechanisms."""

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        """Create test data: exact match + typo variant."""
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth="1990-05-15",
        )
        Person.objects.create(
            first_name="Jonh",
            last_name="Smyth",
            date_of_birth="1985-03-20",
        )
        Person.objects.create(
            first_name="Alice",
            last_name="Jones",
            date_of_birth="1992-11-01",
        )

    def _get_names(self, response):
        """Extract (first_name, last_name) tuples from search results."""
        return set((r["person"].first_name, r["person"].last_name) for r in response.context["results"])

    def test_prefix_mode_exact_only(self, client):
        """Prefix mode returns only exact prefix matches, no typo tolerance."""
        response = client.get("/search/?modes=prefix&first_name=John&last_name=Smith")
        assert response.status_code == 200
        assert "prefix" in response.context["enabled_modes"]
        names = self._get_names(response)
        assert ("John", "Smith") in names
        assert ("Jonh", "Smyth") not in names

    def test_soundex_mode_finds_exact_match(self, client):
        """Soundex mode returns phonetic matches."""
        response = client.get("/search/?modes=soundex&first_name=John&last_name=Smith")
        assert response.status_code == 200
        assert "soundex" in response.context["enabled_modes"]
        assert ("John", "Smith") in self._get_names(response)

    def test_default_modes_prefix_only(self, client):
        """Default mode is prefix only."""
        response = client.get("/search/?first_name=John&last_name=Smith")
        assert response.status_code == 200
        enabled = response.context["enabled_modes"]
        assert enabled == ["prefix"]

    def test_unknown_mode_uses_defaults(self, client):
        """Unknown mode falls back to defaults."""
        response = client.get("/search/?modes=invalid&first_name=John&last_name=Smith")
        assert response.status_code == 200


class TestSearchByDateOfBirth:
    """Test that DOB filtering works with unified search."""

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth="1990-05-15",
        )
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth="1985-03-20",
        )
        # Shares the queried DOB with the first John — a DOB-only fallback
        # (B1) would leak this row into name searches.
        Person.objects.create(
            first_name="Robert",
            last_name="Jones",
            date_of_birth="1990-05-15",
        )

    @pytest.mark.parametrize(
        "mode",
        ["prefix", "soundex", "legacy,levenshtein", "dm"],
        ids=["prefix", "soundex", "levenshtein", "dm"],
    )
    def test_date_of_birth_narrows_results(self, client, mode):
        """DOB filter narrows results to only matching records.

        Levenshtein is a precision filter, not a standalone search (B1/B13),
        so it is exercised on top of a base mode (legacy).
        """
        response = client.get(f"/search/?modes={mode}&first_name=John&last_name=Smith&date_of_birth=1990-05-15")
        assert response.status_code == 200
        results = response.context["results"]
        assert len(results) == 1
        assert str(results[0]["person"].date_of_birth) == "1990-05-15"

    def test_date_of_birth_narrows_trigram_results(self, client):
        """Trigram mode returns only rows with the queried DOB, closest match first.

        Unlike the name-matching modes, trigram has no name filter: everyone
        born that day is returned, ranked by trigram distance.
        """
        response = client.get("/search/?modes=trigram&first_name=John&last_name=Smith&date_of_birth=1990-05-15")
        assert response.status_code == 200
        results = response.context["results"]
        assert len(results) == 2
        assert all(str(r["person"].date_of_birth) == "1990-05-15" for r in results)
        top = results[0]["person"]
        assert (top.first_name, top.last_name) == ("John", "Smith")

    def test_date_of_birth_alone_returns_matching_record(self, client):
        """DOB alone returns matching records."""
        response = client.get("/search/?modes=prefix&date_of_birth=1990-05-15")
        assert response.status_code == 200

    def test_invalid_date_of_birth_is_ignored(self, client):
        """Invalid DOB is ignored and search proceeds with names only."""
        response = client.get("/search/?modes=prefix&first_name=John&last_name=Smith&date_of_birth=invalid")
        assert response.status_code == 200


class TestSearchExactQuerySet:
    """Test the search_exact QuerySet method."""

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth="1990-05-15",
        )
        Person.objects.create(
            first_name="Jonh",
            last_name="Smyth",
            date_of_birth="1985-03-20",
        )

    def test_search_exact_finds_prefix_match(self):
        """search_exact finds prefix matches."""
        results = list(Person.objects.search_exact("John", "Smith"))
        assert len(results) == 1
        assert results[0].first_name == "John"

    def test_search_exact_case_insensitive(self):
        """search_exact is case-insensitive."""
        results = list(Person.objects.search_exact("john", "smith"))
        assert len(results) == 1

    def test_search_exact_no_typo_tolerance(self):
        """search_exact does not tolerate typos."""
        results = list(Person.objects.search_exact("Jonn", "Smit"))
        assert len(results) == 0


class TestLevenshteinFilter:
    """Test that Levenshtein acts as a precision filter."""

    def test_levenshtein_filters_soundex_results(self, client):
        """Levenshtein narrows down Soundex results."""
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth=date(1990, 1, 1),
        )
        Person.objects.create(
            first_name="Jon",
            last_name="Schmidt",
            date_of_birth=date(1990, 1, 1),
        )
        # Soundex alone finds both (Smith and Schmidt share Soundex code)
        r1 = client.get("/search/?modes=soundex&first_name=John&last_name=Smith")
        assert r1.context["count"] == 2
        # Soundex + Levenshtein narrows to exact match (Schmidt is dist 3 from Smith)
        r2 = client.get("/search/?modes=soundex,levenshtein&first_name=John&last_name=Smith")
        assert r2.context["count"] == 1


class TestLevenshteinCheckboxUX:
    """B13 UX: Levenshtein is a precision filter, disabled when no base mode is checked."""

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth="1990-01-01",
        )

    @staticmethod
    def _levenshtein_checkbox_html(response):
        match = re.search(r'<input[^>]*value="levenshtein"[^>]*>', response.content.decode(), re.S)
        assert match, "levenshtein checkbox not found in rendered HTML"
        return match.group(0)

    def test_levenshtein_disabled_without_base_mode(self, client):
        """No base mode checked -> Levenshtein checkbox is disabled with a visible hint."""
        response = client.get("/search/?modes=levenshtein&first_name=John&last_name=Smith")
        assert response.status_code == 200
        checkbox = self._levenshtein_checkbox_html(response)
        assert "disabled" in checkbox
        assert "checked" not in checkbox
        html = response.content.decode()
        hint = re.search(r'<p id="levenshtein-hint"[^>]*>', html)
        assert hint, "levenshtein hint not found in rendered HTML"
        assert "hidden" not in hint.group(0)
        assert "enable at least one base mode" in html

    def test_levenshtein_enabled_with_base_mode(self, client):
        """A checked base mode keeps the Levenshtein checkbox enabled (hint hidden)."""
        response = client.get("/search/?modes=prefix,levenshtein&first_name=John&last_name=Smith")
        assert response.status_code == 200
        checkbox = self._levenshtein_checkbox_html(response)
        assert "disabled" not in checkbox
        assert "checked" in checkbox
        hint = re.search(r'<p id="levenshtein-hint"[^>]*>', response.content.decode())
        assert hint
        assert "hidden" in hint.group(0)

    def test_levenshtein_only_search_returns_no_results(self, client):
        """B13 pin: Levenshtein alone + name renders an empty result set via the UI."""
        response = client.get("/search/?modes=levenshtein&first_name=John&last_name=Smith")
        assert response.status_code == 200
        assert response.context["count"] == 0


class TestMatchSourceAnnotation:
    """Test that results are annotated with match_source."""

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth="1990-05-15",
        )

    def test_result_has_match_source(self, client):
        """Each result has a match_source bitmask."""
        response = client.get("/search/?modes=prefix&first_name=John&last_name=Smith")
        assert response.status_code == 200
        results = response.context["results"]
        assert len(results) == 1
        assert "match_source" in results[0]
        assert isinstance(results[0]["match_source"], int)

    def test_result_has_person(self, client):
        """Each result has a person object."""
        response = client.get("/search/?modes=prefix&first_name=John&last_name=Smith")
        assert response.status_code == 200
        results = response.context["results"]
        assert len(results) == 1
        assert isinstance(results[0]["person"], Person)

    def test_prefix_badge_flags(self, client):
        """Prefix mode sets has_prefix flag."""
        response = client.get("/search/?modes=prefix&first_name=John&last_name=Smith")
        assert response.status_code == 200
        r = response.context["results"][0]
        assert r["has_prefix"] is True
        assert r["has_soundex"] is False
        assert r["has_dm"] is False
        assert r["has_trigram"] is False

    def test_match_labels_have_correct_names(self, client):
        """MATCH_LABELS uses correct display names."""
        response = client.get("/search/?modes=prefix&first_name=John&last_name=Smith")
        assert response.status_code == 200
        labels = response.context["match_labels"]
        assert labels["prefix"]["label"] == "Exact Prefix"
        assert labels["dm"]["label"] == "DM"
        assert labels["soundex"]["label"] == "Soundex"
        assert labels["trigram"]["label"] == "Trigram"
        assert labels["legacy"]["label"] == "LIKE"

    def test_multiple_badges_render(self, client):
        """Multiple modes enabled show multiple badge flags."""
        response = client.get("/search/?modes=prefix,soundex&first_name=John&last_name=Smith")
        assert response.status_code == 200
        r = response.context["results"][0]
        assert r["has_prefix"] is True
        assert r["has_soundex"] is True


class TestTrigramMode:
    """Test trigram-only mode (was broken, now fixed)."""

    def test_trigram_only_returns_results(self, client):
        """Trigram alone returns results (regression: early-return guard blocked it)."""
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth=date(1990, 1, 1),
        )
        response = client.get("/search/?modes=trigram&first_name=John&last_name=Smith")
        assert response.status_code == 200
        assert response.context["count"] >= 1
        names = self._get_names(response)
        assert ("John", "Smith") in names

    def test_trigram_finds_similar_names(self, client):
        """Trigram finds names with character overlap."""
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth=date(1990, 1, 1),
        )
        Person.objects.create(
            first_name="Jane",
            last_name="Smyth",
            date_of_birth=date(1990, 1, 1),
        )
        response = client.get("/search/?modes=trigram&first_name=John&last_name=Smith")
        assert response.status_code == 200
        assert response.context["count"] >= 1

    def _get_names(self, response):
        return set((r["person"].first_name, r["person"].last_name) for r in response.context["results"])


class TestLegacyMode:
    """Test legacy LIKE mode in unified search."""

    def test_legacy_mode_finds_substring(self, client):
        """Legacy mode uses icontains and finds substring matches."""
        Person.objects.create(
            first_name="Johnathon",
            last_name="Smithsonian",
            date_of_birth=date(1990, 1, 1),
        )
        response = client.get("/search/?modes=legacy&first_name=John&last_name=Smith")
        assert response.status_code == 200
        names = set((r["person"].first_name, r["person"].last_name) for r in response.context["results"])
        assert ("Johnathon", "Smithsonian") in names

    def test_legacy_badge_flag(self, client):
        """Legacy mode sets has_legacy flag."""
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth=date(1990, 1, 1),
        )
        response = client.get("/search/?modes=legacy&first_name=John&last_name=Smith")
        assert response.status_code == 200
        r = response.context["results"][0]
        assert r["has_legacy"] is True


class TestDMMode:
    """Test Daitch-Mokotoff mode in unified search."""

    def test_dm_mode_finds_exact_match(self, client):
        """DM mode returns matches."""
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth=date(1990, 1, 1),
        )
        response = client.get("/search/?modes=dm&first_name=John&last_name=Smith")
        assert response.status_code == 200
        assert response.context["count"] >= 1

    def test_dm_badge_flag(self, client):
        """DM mode sets has_dm flag."""
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth=date(1990, 1, 1),
        )
        response = client.get("/search/?modes=dm&first_name=John&last_name=Smith")
        assert response.status_code == 200
        r = response.context["results"][0]
        assert r["has_dm"] is True


class TestModeSQL:
    """Test that mode_sql snippets are generated for active modes."""

    def test_mode_sql_present_for_active_modes(self, client):
        """Active modes have non-empty SQL snippets."""
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth=date(1990, 1, 1),
        )
        response = client.get("/search/?modes=prefix,soundex&first_name=John&last_name=Smith")
        assert response.status_code == 200
        mode_sql = response.context["mode_sql"]
        assert mode_sql["prefix"] != ""
        assert mode_sql["soundex"] != ""
        assert mode_sql["legacy"] == ""  # Not enabled

    def test_mode_sql_contains_query_names(self, client):
        """SQL snippets contain the query names."""
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth=date(1990, 1, 1),
        )
        response = client.get("/search/?modes=prefix&first_name=John&last_name=Smith")
        mode_sql = response.context["mode_sql"]
        assert "JOHN" in mode_sql["prefix"]
        assert "SMITH" in mode_sql["prefix"]


class TestPhoneticCodes:
    """Test phonetic codes in search context."""

    def test_phonetic_codes_in_context(self, client):
        """Query phonetic codes are computed and passed to template."""
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth=date(1990, 1, 1),
        )
        response = client.get("/search/?modes=soundex&first_name=John&last_name=Smith")
        codes = response.context["phonetic_codes"]
        assert "soundex_fn" in codes
        assert "soundex_ln" in codes
        assert codes["soundex_fn"] == "J500"  # Standard Soundex for JOHN

    def test_result_phonetic_codes_when_soundex_enabled(self, client):
        """Results have phonetic_codes when soundex mode is active."""
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth=date(1990, 1, 1),
        )
        response = client.get("/search/?modes=soundex&first_name=John&last_name=Smith")
        r = response.context["results"][0]
        assert "phonetic_codes" in r
        assert "soundex_fn" in r["phonetic_codes"]

    def test_result_phonetic_codes_when_dm_enabled(self, client):
        """Results have phonetic_codes when DM mode is active."""
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth=date(1990, 1, 1),
        )
        response = client.get("/search/?modes=dm&first_name=John&last_name=Smith")
        r = response.context["results"][0]
        assert "phonetic_codes" in r
        assert "dm_fn" in r["phonetic_codes"]


class TestHelpPage:
    """Test the help page endpoint."""

    def test_help_page_renders(self, client):
        """Help page returns 200."""
        response = client.get("/help/")
        assert response.status_code == 200

    def test_help_page_has_examples(self, client):
        """Help page has examples context."""
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth=date(1990, 1, 1),
        )
        response = client.get("/help/")
        assert response.status_code == 200
        assert "examples" in response.context
        assert "dob" in response.context["examples"]
        assert "groups" in response.context["examples"]

    def test_help_page_refresh_busts_cache(self, client):
        """?refresh=1 triggers cache invalidation."""
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth=date(1990, 1, 1),
        )
        # First request populates cache
        client.get("/help/")
        # Second request with refresh=1 should still work
        response = client.get("/help/?refresh=1")
        assert response.status_code == 200


class TestModeSQLTooltipEscaping:
    """B2 regression: mode-SQL tooltips must not reflect raw request input.

    _mode_sql() interpolates raw query params into the SQL snippets rendered
    in the checkbox tooltips. They must be auto-escaped in the title
    attributes so a crafted name cannot break out of the attribute
    (reflected XSS).
    """

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth=date(1990, 1, 1),
        )

    @staticmethod
    def _assert_escaped_in_tooltip(response, raw_name):
        assert response.status_code == 200
        html = response.content.decode()
        # The raw attribute-breakout sequence must not be present unescaped.
        assert 'onmouseover="alert(1)' not in html
        # The crafted name must appear escaped inside the tooltip
        # (Django's escaper renders " as &quot;).
        assert f"{raw_name}&quot; onmouseover=&quot;alert(1)" in html

    def test_legacy_tooltip_escapes_first_name(self, client):
        """A quote in first_name (legacy mode) renders escaped in the tooltip."""
        response = client.get(
            "/search/",
            {"modes": "legacy", "first_name": 'x" onmouseover="alert(1)', "last_name": "Smith"},
        )
        self._assert_escaped_in_tooltip(response, "x")

    def test_legacy_tooltip_escapes_last_name(self, client):
        """A quote in last_name (legacy mode) renders escaped in the tooltip."""
        response = client.get(
            "/search/",
            {"modes": "legacy", "first_name": "John", "last_name": 'x" onmouseover="alert(1)'},
        )
        self._assert_escaped_in_tooltip(response, "x")

    def test_trigram_tooltip_escapes_first_name(self, client):
        """A quote in first_name (trigram mode, original-case path) renders escaped."""
        response = client.get(
            "/search/",
            {"modes": "trigram", "first_name": 'x" onmouseover="alert(1)'},
        )
        self._assert_escaped_in_tooltip(response, "x")


class TestMultipleModes:
    """Test that multiple modes combine correctly (OR behavior)."""

    def test_multiple_modes_or_behavior(self, client):
        """Multiple modes return union of results."""
        Person.objects.create(
            first_name="John",
            last_name="Smith",
            date_of_birth=date(1990, 1, 1),
        )
        Person.objects.create(
            first_name="Johnny",
            last_name="Smythe",
            date_of_birth=date(1990, 1, 1),
        )
        # Combined should find at least as many as prefix alone
        r2 = client.get("/search/?modes=prefix&first_name=John&last_name=Smith")
        # Combined should find at least as many as either alone
        r3 = client.get("/search/?modes=prefix,soundex&first_name=John&last_name=Smith")
        assert r3.context["count"] >= r2.context["count"]

    def test_empty_search_returns_no_results(self, client):
        """No names and no DOB returns empty results."""
        response = client.get("/search/?modes=prefix")
        assert response.status_code == 200
        assert response.context["count"] == 0


class TestDobOnlySearchRendering:
    """B7: a DOB-only search renders its result rows, not the 'Enter a name' empty state."""

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        self.dob = "1990-05-15"
        Person.objects.create(first_name="John", last_name="Smith", date_of_birth=self.dob)
        Person.objects.create(first_name="Robert", last_name="Jones", date_of_birth=self.dob)
        # Shares no DOB with the query — must never leak into the results.
        Person.objects.create(first_name="Alice", last_name="Taylor", date_of_birth="1985-03-20")

    def test_dob_only_renders_result_rows(self, client):
        """A DOB-only search (no name) shows the matching rows, not the empty state."""
        response = client.get(f"/search/?date_of_birth={self.dob}")
        assert response.status_code == 200
        assert response.context["count"] == 2
        names = set((r["person"].first_name, r["person"].last_name) for r in response.context["results"])
        assert names == {("John", "Smith"), ("Robert", "Jones")}
        html = response.content.decode()
        assert "John" in html
        assert "Robert" in html
        assert "Enter a name to search" not in html

    def test_dob_only_no_match_renders_empty_state(self, client):
        """A DOB with no people renders the no-results message (no crash, no stale rows)."""
        response = client.get("/search/?date_of_birth=1970-01-01")
        assert response.status_code == 200
        assert response.context["count"] == 0
        html = response.content.decode()
        assert "No results found" in html
        assert "Enter a name to search" not in html
        # No rows from any other DOB leak in.
        assert "Alice" not in html
        assert "Taylor" not in html


class TestDobClearButtonRendering:
    """B9: the DOB Clear button always exists; its initial hidden state matches the rendered DOB."""

    def test_clear_button_hidden_without_dob(self, client):
        """No DOB in the URL -> the Clear button exists but is hidden."""
        response = client.get("/")
        assert response.status_code == 200
        html = response.content.decode()
        assert 'id="dob-clear-btn"' in html
        assert 'class="dob-clear-link text-xs font-medium text-primary-700 hover:underline hidden"' in html

    def test_clear_button_visible_with_dob(self, client):
        """DOB in the URL (htmx hx-push-url refresh) -> Clear button exists and is not hidden."""
        response = client.get("/?date_of_birth=1990-05-15")
        assert response.status_code == 200
        html = response.content.decode()
        assert 'id="dob-clear-btn"' in html
        assert 'class="dob-clear-link text-xs font-medium text-primary-700 hover:underline"' in html
        assert 'class="dob-clear-link text-xs font-medium text-primary-700 hover:underline hidden"' not in html
        # The input is prefilled from the query param, so the client-side toggle keeps it visible.
        assert 'value="1990-05-15"' in html

    def test_clear_button_js_is_single_toggle(self, client):
        """Page JS toggles `hidden` on the always-present button; create/remove branches are gone."""
        html = client.get("/").content.decode()
        assert "classList.toggle('hidden', !dob)" in html
        assert "createElement('button')" not in html
        assert "clearBtn.remove()" not in html


class TestTop100Label:
    """B6: the results badge says 'top N', not an unqualified total."""

    def test_full_page_says_top_100(self, client):
        """A full 100-row page renders 'Showing top 100 matches'."""
        for i in range(105):
            Person.objects.create(first_name=f"First{i:03d}", last_name="Smith", date_of_birth=date(1990, 1, 1))
        response = client.get("/search/?modes=legacy&last_name=Smith")
        assert response.status_code == 200
        assert response.context["count"] == 100
        html = response.content.decode()
        assert "Showing top 100 matches" in html
        assert "100 results" not in html


class TestSearchExplain:
    """B8: the EXPLAIN endpoint explains the query search_unified actually runs."""

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        Person.objects.create(first_name="John", last_name="Smith", date_of_birth="1990-05-15")
        Person.objects.create(first_name="Jonh", last_name="Smyth", date_of_birth="1985-03-20")
        Person.objects.create(first_name="Alice", last_name="Jones", date_of_birth="1992-11-01")

    def test_explain_single_mode_legacy(self, client):
        """mode=legacy explains the legacy LIKE query — no Soundex SQL."""
        response = client.get("/search/explain/?mode=legacy&first_name=John&last_name=Smith")
        assert response.status_code == 200
        sql = response.context["sql"]
        assert sql
        assert "LIKE UPPER(%John%)" in sql
        assert "LIKE UPPER(%Smith%)" in sql
        assert "SOUNDEX" not in sql
        assert response.context["plan"]
        assert response.context["error"] is None

    def test_explain_multi_mode_legacy_levenshtein(self, client):
        """modes=legacy,levenshtein explains the combined query search_unified runs."""
        response = client.get("/search/explain/?modes=legacy,levenshtein&first_name=John&last_name=Smith")
        assert response.status_code == 200
        sql = response.context["sql"]
        assert "LIKE UPPER(%John%)" in sql
        assert "levenshtein_less_equal" in sql
        assert "SOUNDEX" not in sql
        assert response.context["plan"]
        assert response.context["mode"] == "legacy,levenshtein"
        assert response.context["mode_label"] == "Legacy LIKE + Levenshtein"
        assert response.context["explain_subject"] == "Explaining: legacy + levenshtein"

    def test_explain_trigram_mode_explains_knn_query(self, client):
        """mode=trigram explains the KNN ORDER BY query search_unified runs."""
        response = client.get("/search/explain/?mode=trigram&first_name=John&last_name=Smith")
        assert response.status_code == 200
        sql = response.context["sql"]
        assert "<->" in sql
        assert "ORDER BY" in sql
        assert response.context["plan"]
        assert response.context["explain_subject"] == "Explaining: trigram (KNN) query"

    def test_explain_invalid_mode_falls_back_to_prefix(self, client):
        """An invalid mode name falls back to prefix and the page renders."""
        response = client.get("/search/explain/?mode=bogus&first_name=John&last_name=Smith")
        assert response.status_code == 200
        assert response.context["mode"] == "prefix"
        assert response.context["mode_label"] == "Exact prefix"
        assert "LIKE" in response.context["sql"]
        assert response.context["plan"]
        assert response.context["error"] is None

    def test_explain_levenshtein_alone_has_no_query(self, client):
        """Levenshtein without a base mode: no query runs; the page says so (no 500)."""
        response = client.get("/search/explain/?mode=levenshtein&first_name=John&last_name=Smith")
        assert response.status_code == 200
        assert response.context["sql"] is None
        assert "base mode" in response.context["error"]
        assert "base mode" in response.content.decode()

    def test_explain_no_input_renders_error(self, client):
        """No name and no DOB keeps the existing 'Provide a first_name...' error path."""
        response = client.get("/search/explain/?mode=legacy")
        assert response.status_code == 200
        assert "Provide a first_name" in response.context["error"]
        assert response.context["sql"] is None
        assert "Provide a first_name" in response.content.decode()

    def test_results_fragment_explain_link_all_modes_encoded(self, client):
        """The results fragment links explain with ALL enabled modes, URL-encoded."""
        response = client.get("/search/?modes=legacy,levenshtein&first_name=J%26J", HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        html = response.content.decode()
        assert "modes=legacy%2Clevenshtein" in html
        assert "first_name=J%26J" in html
