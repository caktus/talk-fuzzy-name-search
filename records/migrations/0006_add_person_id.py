"""Add person_id field to link records representing the same real person."""

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("records", "0005_remove_phonetic_columns_make_dob_required"),
    ]

    operations = [
        # Add person_id with a default for existing rows
        migrations.AddField(
            model_name="person",
            name="person_id",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                help_text="Links records representing the same real person (same DOB, name variants, typos)",
            ),
            preserve_default=False,
        ),
        # Backfill: give every existing record its own unique person_id
        # (they're all independent since there was no clustering before)
        migrations.RunPython(
            code=migrations.RunPython.noop,  # Done in management command, not migration
            reverse_code=migrations.RunPython.noop,
        ),
        # Add index for cluster lookups
        migrations.AddIndex(
            model_name="person",
            index=models.Index(fields=["person_id"], name="idx_person_person_id"),
        ),
    ]
