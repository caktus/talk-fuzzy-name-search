"""Smoke tests that the demo app's data backs the UI.

The "How it works" help page builds one clickable example per search mode
(``records.views._generate_help_examples``). Each link must actually return the
base person it was derived from, otherwise the example "doesn't work." These
tests guard that property end-to-end and run against a tiny in-test dataset
only -- they never touch the 54M stage data and do not re-seed.
"""

import pytest
from django.core.cache import cache
from django.db.models import QuerySet

from records.models import CourtRecord
from records.views import SEARCH_MODES, _generate_help_examples, _run_unified_search

pytestmark = pytest.mark.django_db

# One DOB shared by every seeded person, with 6 rows so the help-page DOB
# selector (first DOB whose count is in [5, 20]) lands on it.
TEST_DOB = "1990-01-01"

# Six realistic people, one per last name, all UPPERCASE (the 54M seed stores
# names uppercase) so case-sensitive modes like trigram stay consistent.
PEOPLE = [
    ("TONY", "WALLER"),
    ("JAMES", "SMITH"),
    ("PATRICIA", "JOHNSON"),
    ("MICHAEL", "BROWN"),
    ("BRENDA", "TUCKER"),
    ("JEFFREY", "WATSON"),
]


def _seed() -> None:
    for first, last in PEOPLE:
        CourtRecord.objects.create(first_name=first, last_name=last, date_of_birth=TEST_DOB)


def _parse_modes(mode_str: str) -> list[str]:
    return [m for m in mode_str.split(",") if m in SEARCH_MODES]


def _result_names(out: dict) -> set[tuple[str, str]]:
    return {(r["person"].first_name, r["person"].last_name) for r in out["results"]}


def _reconstruct_base(groups: list[dict], dob: str) -> list[tuple[str, str]]:
    """Rebuild the base person(s) the examples were derived from.

    The ``legacy,levenshtein`` group keeps the FULL base last name and the
    ``prefix`` group keeps the base first name minus its last character. With
    one person per last name in the seed, that pinpoints the exact base row.
    """
    base_ln = next(g["ln"] for g in groups if g["mode"] == "legacy,levenshtein")
    fn_prefix = next(g["fn"] for g in groups if g["mode"] == "prefix")
    qs: QuerySet = CourtRecord.objects.filter(date_of_birth=dob, last_name=base_ln, first_name__istartswith=fn_prefix)
    return [(p.first_name, p.last_name) for p in qs]


class TestHelpExamplesSmoke:
    """Every generated help example must return rows and must return the base."""

    def test_all_help_examples_return_results(self):
        _seed()
        cache.delete("help_examples")
        examples = _generate_help_examples()

        assert examples["groups"], "expected the help page to generate examples"
        assert examples["dob"] == TEST_DOB, f"unexpected DOB {examples['dob']!r}"

        for group in examples["groups"]:
            out = _run_unified_search(_parse_modes(group["mode"]), group["fn"], group["ln"], examples["dob"])
            assert out["count"] >= 1, f"help example {group['mode']!r} ({group['fn']} {group['ln']}) returned no rows"

    def test_help_examples_find_base_person(self):
        _seed()
        cache.delete("help_examples")
        examples = _generate_help_examples()

        base_candidates = _reconstruct_base(examples["groups"], examples["dob"])
        assert len(base_candidates) == 1, f"ambiguous base reconstruction: {base_candidates}"
        base_fn, base_ln = base_candidates[0]

        for group in examples["groups"]:
            out = _run_unified_search(_parse_modes(group["mode"]), group["fn"], group["ln"], examples["dob"])
            assert (base_fn, base_ln) in _result_names(out), (
                f"base {base_fn} {base_ln} not found by example {group['mode']!r} "
                f"(searched {group['fn']} {group['ln']})"
            )


class TestDemoSearchSmoke:
    """A seeded person is findable through the core search paths."""

    def test_demo_search_smoke(self):
        CourtRecord.objects.create(first_name="BRENDA", last_name="TUCKER", date_of_birth=TEST_DOB)

        # Exact prefix (type-ahead): search a truncated spelling.
        exact = _run_unified_search(["prefix"], "BREN", "TUC", TEST_DOB)
        assert ("BRENDA", "TUCKER") in _result_names(exact), "exact-prefix search missed the seeded person"

        # Soundex: search the full name; the person must match their own code.
        soundex = _run_unified_search(["soundex"], "BRENDA", "TUCKER", TEST_DOB)
        assert ("BRENDA", "TUCKER") in _result_names(soundex), "soundex search missed the seeded person"
