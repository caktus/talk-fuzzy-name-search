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
        # NOTE: the four functional phonetic indexes (idx_person_{first,last}_name_{soundex,dm})
        # previously created here via RunSQL are now created by real CreateIndex operations in
        # 0007_alter_person_person_id_and_more so the migration state matches the models
        # (RECS-2026-08-14 B11). The RunSQL was stateless, so removing it is safe for already-
        # migrated databases; live databases already have the indexes and apply 0007 --fake.
    ]
