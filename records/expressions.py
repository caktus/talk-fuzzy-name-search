"""Custom Django ORM expressions for phonetic name search.

Provides clean ORM wrappers for PostgreSQL fuzzystrmatch functions,
enabling search logic to be expressed through the Django ORM instead
of RawSQL.
"""

from django.contrib.postgres.fields import ArrayField
from django.db.models.expressions import Func
from django.db.models.fields import CharField, IntegerField, TextField


class LevenshteinLessEqual(Func):
    """PostgreSQL levenshtein_less_equal() as a Django ORM expression.

    Computes the Levenshtein edit distance between two strings with
    early termination when the distance exceeds the threshold.

    Usage:
        LevenshteinLessEqual(F('name'), Value('SMITH'), Value(2))

    Returns the computed distance (int). Filter with __lte to check
    if within threshold.
    """

    function = "levenshtein_less_equal"
    output_field = IntegerField()


class Soundex(Func):
    """PostgreSQL SOUNDEX() as a Django ORM expression.

    Usage:
        Soundex(F('first_name'))

    Returns a 4-character Soundex code (str).
    """

    function = "SOUNDEX"
    output_field = CharField()


class DaitchMokotoff(Func):
    """PostgreSQL DAITCH_MOKOTOFF() as a Django ORM expression.

    Returns a text[] of Daitch-Mokotoff phonetic codes.
    Supports GIN-indexed array overlap (&&) via __overlap.

    Usage:
        DaitchMokotoff(F('first_name'))
    """

    function = "DAITCH_MOKOTOFF"
    output_field = ArrayField(TextField())
