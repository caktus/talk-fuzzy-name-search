"""Parse-only Django forms for the ``records`` app's GET query parameters.

Design principle: these forms are **parse-only and designed to be always
valid**. Every field degrades invalid or missing input to the same default
the pre-form view code produced (silent fallback), so ``form.is_valid()``
is effectively always True and the views never render form errors. No
input that is currently accepted-and-ignored is turned into a visible
validation error — the forms exist to centralize parsing, not to
push back on the user.
"""

from django import forms
from django.utils.dateparse import parse_date

from .models import TRIGRAM_SIMILARITY_CUTOFF

# Search modes. ``prefix`` and ``legacy`` are the base match modes (mutually
# exclusive radio buttons in the UI); the rest are independent checkboxes.
SEARCH_MODES = {
    "prefix": {
        "label": "Exact prefix",
        "description": "B-tree indexed istartswith — fast type-ahead, exact spelling.",
        "default": True,
    },
    "soundex": {
        "label": "Soundex",
        "description": "Phonetic pre-filter using SOUNDEX codes.",
        "default": False,
    },
    "levenshtein": {
        "label": "Levenshtein",
        "description": "Edit distance ≤ 2 — precision filter applied on top of other modes.",
        "default": False,
    },
    "dm": {
        "label": "Daitch-Mokotoff",
        "description": "Phonetic codes with stronger Slavic/Germanic coverage.",
        "default": False,
    },
    "trigram": {
        "label": "Trigram",
        "description": f"pg_trgm KNN ranking, cut at similarity() ≥ {TRIGRAM_SIMILARITY_CUTOFF} per name.",
        "default": False,
    },
    "legacy": {
        "label": "LIKE (unindexed)",
        "description": "Unindexed LIKE '%name%' — slow, included for comparison.",
        "default": False,
    },
}

DEFAULT_MODES = [k for k, v in SEARCH_MODES.items() if v["default"]]

# Base modes that Levenshtein can refine. Levenshtein is a precision filter
# on top of base modes, not a standalone search: with no base mode enabled
# the UI disables the Levenshtein checkbox and search_unified() returns no
# rows for a name query (see B1/B13).
BASE_MODES = ["prefix", "legacy", "soundex", "dm", "trigram"]


class LenientDateField(forms.Field):
    """Lenient date field: empty, missing, or unparseable values return ``None``.

    Mirrors the pre-form ``parse_date(request.GET.get(...).strip())``
    behavior: accepts ``YYYY-MM-DD`` and ``YYYY-M-DD``, rejects datetimes,
    and never raises ``ValidationError`` — an invalid DOB is silently
    ignored and the search proceeds without a DOB filter.
    """

    def __init__(self):
        super().__init__(required=False)

    def to_python(self, value):
        if value is None:
            return None
        return parse_date(str(value).strip())


class SearchModesField(forms.Field):
    """Parse the comma-joined ``?modes=`` param for the search views.

    Missing or empty → ``DEFAULT_MODES``. Otherwise split on ``,`` and
    match tokens **as-is (no strip)** — ``prefix, soundex`` keeps only
    ``prefix``. Valid tokens keep their order and duplicates; if nothing
    valid remains → ``DEFAULT_MODES``.
    """

    def __init__(self):
        super().__init__(required=False, widget=forms.HiddenInput)

    def to_python(self, value):
        if not value:
            return list(DEFAULT_MODES)
        enabled = [m for m in value.split(",") if m in SEARCH_MODES]
        return enabled if enabled else list(DEFAULT_MODES)


class ExplainModesField(SearchModesField):
    """Parse ``?modes=`` for the explain view.

    Deliberate asymmetry vs :class:`SearchModesField`: tokens are
    **stripped** before matching (the search views do not strip). The
    asymmetry is pre-existing behavior pinned by tests — preserve it.
    Empty, or nothing valid remaining, → ``["prefix"]``.
    """

    def to_python(self, value):
        if not value:
            return ["prefix"]
        tokens = [t.strip() for t in value.split(",") if t.strip()]
        valid = [t for t in tokens if t in SEARCH_MODES]
        return valid if valid else ["prefix"]


class SearchForm(forms.Form):
    """GET-param parser for the home/search views. Parse-only, always valid."""

    # No max_length: the pre-form code is unbounded; a cap would start
    # rejecting inputs that are currently accepted.
    first_name = forms.CharField(required=False, strip=True)
    last_name = forms.CharField(required=False, strip=True)
    date_of_birth = LenientDateField()
    # strip=False: today ?sort=" dob_asc" is NOT stripped and therefore does
    # not match SORT_PARAMS; the raw value is also kept in the context for
    # the sort-header rendering.
    sort = forms.CharField(required=False, strip=False)
    modes = SearchModesField()


class ExplainForm(SearchForm):
    """GET-param parser for the search_explain view.

    Honors the legacy ``?mode=`` fallback: a missing or present-but-empty
    ``modes`` falls back to ``mode`` (the pre-form ``or`` chain). The
    regular search views deliberately ignore ``?mode=``.
    """

    modes = ExplainModesField()

    def clean(self):
        cleaned_data = super().clean()
        # Keep the pre-form `or` chain: a present-but-empty ``?modes=`` (or
        # a missing one) falls back to the legacy ``?mode=`` param.
        raw = self.data.get("modes") or self.data.get("mode", "")
        cleaned_data["modes"] = self.fields["modes"].to_python(raw)
        return cleaned_data


class RefreshForm(forms.Form):
    """Parse ``?refresh=`` for the help page.

    The view checks truthiness: any non-empty value (even ``refresh=banana``)
    busts the ``help_examples`` cache. Deliberately **not** a BooleanField,
    which would reject ``banana`` — that value DOES bust the cache today.
    """

    refresh = forms.CharField(required=False, strip=True)
