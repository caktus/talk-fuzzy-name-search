"""Enable PostgreSQL extensions for fuzzy name search."""

from django.contrib.postgres.operations import CreateExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("records", "0001_initial"),
    ]

    operations = [
        # fuzzystrmatch: provides daitch_mokotoff(), levenshtein_less_equal(), soundex()
        CreateExtension(name="fuzzystrmatch"),
        # pg_trgm: provides trigram similarity for the "failure of trigrams" demo
        CreateExtension(name="pg_trgm"),
    ]
