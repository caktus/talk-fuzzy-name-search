"""Tests for records/forms.py — the parse-only, always-valid GET-param forms.

The forms must reproduce the pre-form direct ``request.GET`` parsing exactly
(silent fallbacks, no visible validation errors). Form-level tests need no
DB; view-level tests pin the request semantics through the live views.
"""

from datetime import date

import pytest
from django.core.cache import cache

from records.forms import DEFAULT_MODES, ExplainForm, RefreshForm, SearchForm
from records.models import CourtRecord


def _clean(form_cls, data):
    """Bind, assert always-valid, and return cleaned_data."""
    form = form_cls(data=data)
    assert form.is_valid(), f"{form_cls.__name__}({data!r}) unexpectedly invalid: {form.errors}"
    return form.cleaned_data


class TestSearchFormModes:
    """The search views' ?modes= semantics: no token strip, dupes kept."""

    def test_missing_modes_default_to_prefix(self):
        assert _clean(SearchForm, {})["modes"] == ["prefix"]
        assert _clean(SearchForm, {})["modes"] == DEFAULT_MODES

    def test_empty_modes_default_to_prefix(self):
        assert _clean(SearchForm, {"modes": ""})["modes"] == ["prefix"]

    def test_unknown_modes_fall_back_to_prefix(self):
        assert _clean(SearchForm, {"modes": "bogus"})["modes"] == ["prefix"]

    def test_tokens_are_not_stripped(self):
        # Search-view quirk (asymmetric with explain): the " soundex" token
        # is not stripped, is unknown, and drops out — only prefix remains.
        assert _clean(SearchForm, {"modes": "prefix, soundex"})["modes"] == ["prefix"]

    def test_duplicate_modes_are_preserved(self):
        assert _clean(SearchForm, {"modes": "prefix,prefix"})["modes"] == ["prefix", "prefix"]

    def test_multiple_valid_modes_keep_order(self):
        assert _clean(SearchForm, {"modes": "prefix,soundex"})["modes"] == ["prefix", "soundex"]


class TestSearchFormNames:
    def test_names_are_stripped(self):
        assert _clean(SearchForm, {"first_name": " John "})["first_name"] == "John"
        assert _clean(SearchForm, {"last_name": " Smith "})["last_name"] == "Smith"

    def test_missing_names_default_to_empty_string(self):
        assert _clean(SearchForm, {})["first_name"] == ""
        assert _clean(SearchForm, {})["last_name"] == ""

    def test_whitespace_only_names_strip_to_empty(self):
        assert _clean(SearchForm, {"first_name": "   "})["first_name"] == ""
        assert _clean(SearchForm, {"last_name": "   "})["last_name"] == ""


class TestSearchFormDateOfBirth:
    def test_iso_date_parses(self):
        assert _clean(SearchForm, {"date_of_birth": "1990-05-15"})["date_of_birth"] == date(1990, 5, 15)

    def test_unpadded_date_parses(self):
        assert _clean(SearchForm, {"date_of_birth": "1990-5-15"})["date_of_birth"] == date(1990, 5, 15)

    def test_invalid_date_is_silently_ignored(self):
        assert _clean(SearchForm, {"date_of_birth": "invalid"})["date_of_birth"] is None

    def test_datetime_is_silently_ignored(self):
        assert _clean(SearchForm, {"date_of_birth": "1990-05-15 10:00:00"})["date_of_birth"] is None

    def test_empty_date_is_none(self):
        assert _clean(SearchForm, {"date_of_birth": ""})["date_of_birth"] is None


class TestSearchFormSort:
    def test_unknown_sort_preserved_verbatim(self):
        assert _clean(SearchForm, {"sort": "bogus"})["sort"] == "bogus"

    def test_unstripped_sort_preserved_verbatim(self):
        # strip=False pin: " dob_asc" must not match SORT_PARAMS, so the raw
        # value (leading space) is what the view sees.
        assert _clean(SearchForm, {"sort": " dob_asc"})["sort"] == " dob_asc"


class TestExplainFormModes:
    """Explain semantics: legacy ?mode= fallback + token stripping."""

    def test_legacy_mode_param_fallback(self):
        assert _clean(ExplainForm, {"mode": "legacy"})["modes"] == ["legacy"]

    def test_empty_modes_falls_back_to_mode(self):
        # The pre-form `or` chain: present-but-empty ?modes= uses ?mode=.
        assert _clean(ExplainForm, {"modes": "", "mode": "legacy"})["modes"] == ["legacy"]

    def test_modes_param_wins_over_mode(self):
        assert _clean(ExplainForm, {"modes": "legacy", "mode": "soundex"})["modes"] == ["legacy"]

    def test_tokens_are_stripped(self):
        # Asymmetric with SearchForm: the explain view strips each token.
        assert _clean(ExplainForm, {"modes": " legacy , soundex "})["modes"] == ["legacy", "soundex"]

    def test_invalid_mode_falls_back_to_prefix(self):
        assert _clean(ExplainForm, {"mode": "bogus"})["modes"] == ["prefix"]
        assert _clean(ExplainForm, {"modes": "bogus"})["modes"] == ["prefix"]

    def test_missing_modes_fall_back_to_prefix(self):
        assert _clean(ExplainForm, {})["modes"] == ["prefix"]


class TestRefreshForm:
    def test_any_non_empty_value_is_accepted(self):
        # Not a BooleanField: "banana" is accepted and truthy.
        assert _clean(RefreshForm, {"refresh": "banana"})["refresh"] == "banana"
        assert _clean(RefreshForm, {"refresh": "1"})["refresh"] == "1"

    def test_missing_or_empty_refresh_degrades_to_empty_string(self):
        assert _clean(RefreshForm, {})["refresh"] == ""
        assert _clean(RefreshForm, {"refresh": ""})["refresh"] == ""


class TestFormAlwaysValid:
    """Invariant: no input the pre-form views accepted-and-ignored ever
    produces a validation error; cleaned_data holds the degraded default."""

    @pytest.mark.parametrize(
        "data",
        [
            {},
            {"first_name": "   ", "last_name": ""},
            {"date_of_birth": "not-a-date"},
            {"date_of_birth": "1990-05-15 10:00:00"},
            {"sort": "  anything  "},
            {"modes": "bogus,alsobogus"},
            {"modes": ""},
        ],
    )
    def test_search_form_never_invalid(self, data):
        form = SearchForm(data=data)
        assert form.is_valid()
        assert form.errors == {}
        # Degraded defaults land in cleaned_data for every field.
        assert set(form.cleaned_data) == {"first_name", "last_name", "date_of_birth", "sort", "modes"}
        assert form.cleaned_data["date_of_birth"] is None
        assert form.cleaned_data["modes"]


@pytest.mark.django_db
class TestSearchViewParamParsing:
    """View-level pins: the forms reproduce the pre-form request.GET semantics."""

    @pytest.fixture(autouse=True)
    def _seed_data(self):
        CourtRecord.objects.create(first_name="John", last_name="Smith", date_of_birth="1990-01-01")

    def test_search_view_ignores_legacy_mode_param(self, client):
        """Only the explain view honors ?mode=; the search views stay prefix-only."""
        response = client.get("/search/?mode=legacy&first_name=John")
        assert response.status_code == 200
        assert response.context["enabled_modes"] == ["prefix"]
        assert response.context["mode_sql"]["prefix"] != ""
        assert response.context["mode_sql"]["legacy"] == ""

    def test_explain_empty_modes_falls_back_to_mode(self, client):
        """?modes=&mode=legacy explains the legacy LIKE query (the `or` fallback)."""
        response = client.get("/search/explain/?modes=&mode=legacy&first_name=John")
        assert response.status_code == 200
        assert response.context["mode"] == "legacy"
        assert response.context["mode_label"] == "Legacy LIKE"
        assert "LIKE" in response.context["sql"]

    def test_multi_value_param_last_wins(self, client):
        """QueryDict.get semantics: ?last_name=a&last_name=b -> last value wins."""
        response = client.get("/search/?first_name=John&last_name=a&last_name=b")
        assert response.status_code == 200
        assert response.context["last_name"] == "b"

    def test_unstripped_sort_yields_no_sql_order_by(self, client):
        """?sort=" dob_asc" does not match SORT_PARAMS -> no ORDER BY in SQL."""
        response = client.get("/search/explain/?first_name=John&last_name=Smith&sort=%20dob_asc")
        assert response.status_code == 200
        assert response.context["sql"]
        assert "ORDER BY" not in response.context["sql"]


@pytest.mark.django_db
class TestHelpPageRefreshParam:
    """?refresh truthiness pin: any non-empty value busts the cache; empty does not."""

    @pytest.fixture(autouse=True)
    def _clear_examples_cache(self):
        cache.clear()
        yield
        cache.clear()

    def test_non_boolean_refresh_busts_cache(self, client):
        """refresh=banana (not a boolean) still invalidates the cache."""
        sentinel = {"sentinel": True}
        cache.set("help_examples", sentinel)
        response = client.get("/help/?refresh=banana")
        assert response.status_code == 200
        assert cache.get("help_examples") != sentinel

    def test_empty_refresh_keeps_cache(self, client):
        sentinel = {"sentinel": True}
        cache.set("help_examples", sentinel)
        response = client.get("/help/?refresh=")
        assert response.status_code == 200
        assert response.context["examples"] == sentinel
        assert cache.get("help_examples") == sentinel
