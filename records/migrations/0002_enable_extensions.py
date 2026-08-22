from django.contrib.postgres.operations import CreateExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("records", "0001_initial"),
    ]
    operations = [
        # soundex(), daitch_mokotoff(), levenshtein_less_equal()
        CreateExtension(name="fuzzystrmatch"),
        # similarity() and the <-> trigram distance operator
        CreateExtension(name="pg_trgm"),
    ]
