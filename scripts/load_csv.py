#!/usr/bin/env python3
"""Load CSV into PostgreSQL using Django bulk_create.

Usage:
    uv run python scripts/load_csv.py data/people_50m.csv
"""

import csv
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fuzzy_demo.settings")

import django

django.setup()

from records.models import Person  # noqa: E402


def load_csv(csv_path: str, batch_size: int = 50000):
    print(f"Loading {csv_path} into database (bulk_create)...", flush=True)
    start_time = time.time()

    deleted, _ = Person.objects.all().delete()
    print(f"  Cleared {deleted:,} existing records", flush=True)

    loaded = 0
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        batch = []

        for row in reader:
            nicknames = row.get("nicknames", "").split("|") if row.get("nicknames") else []
            dob = row.get("date_of_birth") or None
            middle = row.get("middle_name") or None

            batch.append(
                Person(
                    first_name=row["first_name"],
                    last_name=row["last_name"],
                    middle_name=middle,
                    date_of_birth=dob,
                    nicknames=nicknames,
                )
            )

            if len(batch) >= batch_size:
                Person.objects.bulk_create(batch)
                loaded += len(batch)
                batch = []
                elapsed = time.time() - start_time
                rate = loaded / elapsed if elapsed > 0 else 0
                print(f"  {loaded:,} loaded ({rate:,.0f} rows/s, {elapsed:.0f}s)", flush=True)

        if batch:
            Person.objects.bulk_create(batch)
            loaded += len(batch)

    total_time = time.time() - start_time
    count = Person.objects.count()
    print(f"Loaded {count:,} records in {total_time:.1f}s", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run python scripts/load_csv.py <csv_path>")
        sys.exit(1)
    load_csv(sys.argv[1])