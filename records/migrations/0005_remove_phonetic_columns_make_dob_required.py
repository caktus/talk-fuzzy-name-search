"""Remove stored phonetic columns; make date_of_birth required."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("records", "0004_person_idx_person_date_of_birth"),
    ]

    operations = [
        # Set a default DOB for any existing NULL rows before making NOT NULL
        migrations.RunSQL(
            sql="UPDATE records_person SET date_of_birth = '1970-01-01' WHERE date_of_birth IS NULL",
            reverse_sql="SELECT 1",
        ),
        # Make date_of_birth NOT NULL
        migrations.AlterField(
            model_name="person",
            name="date_of_birth",
            field=models.DateField(),
        ),
        # Remove stored phonetic token columns
        migrations.RemoveField(model_name="person", name="first_name_phonetic"),
        migrations.RemoveField(model_name="person", name="last_name_phonetic"),
        # Remove old GIN indexes on phonetic arrays
        migrations.RemoveIndex(model_name="person", name="idx_person_first_phonetic"),
        migrations.RemoveIndex(model_name="person", name="idx_person_last_phonetic"),
        # Add functional B-tree indexes for SOUNDEX equality comparisons
        migrations.RunSQL(
            sql="CREATE INDEX idx_person_first_name_soundex ON records_person (SOUNDEX(UPPER(first_name)))",
            reverse_sql="DROP INDEX IF EXISTS idx_person_first_name_soundex",
        ),
        migrations.RunSQL(
            sql="CREATE INDEX idx_person_last_name_soundex ON records_person (SOUNDEX(UPPER(last_name)))",
            reverse_sql="DROP INDEX IF EXISTS idx_person_last_name_soundex",
        ),
        # Add functional GIN indexes for DAITCH_MOKOTOFF array overlap (&&)
        migrations.RunSQL(
            sql="CREATE INDEX idx_person_first_name_dm ON records_person USING GIN (DAITCH_MOKOTOFF(UPPER(first_name)))",
            reverse_sql="DROP INDEX IF EXISTS idx_person_first_name_dm",
        ),
        migrations.RunSQL(
            sql="CREATE INDEX idx_person_last_name_dm ON records_person USING GIN (DAITCH_MOKOTOFF(UPPER(last_name)))",
            reverse_sql="DROP INDEX IF EXISTS idx_person_last_name_dm",
        ),
    ]
