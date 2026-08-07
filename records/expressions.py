"""Custom Django ORM expressions for phonetic name search.

Provides clean ORM wrappers for PostgreSQL fuzzy matching functions,
enabling search logic to be expressed through the Django ORM instead
of RawSQL.
"""

from django.db.models.expressions import Func
from django.db.models.fields import IntegerField


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
