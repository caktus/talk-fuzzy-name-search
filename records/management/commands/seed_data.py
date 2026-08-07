"""Seed the database with realistic person records for demo/benchmarking.

Usage:
    python manage.py seed_data --count 100000

Variation injection:
    ~30% of records: use a nickname as the stored first_name
    ~20% of records: include a middle name (mix of full names and initials)
    ~1% of records: inject a single random typo into first_name or last_name
"""

import random
import string
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from faker import Faker

from records.models import Person
from records.phonetics import NICKNAME_MAP, dm_soundex_tokens, soundex_tokens


class Command(BaseCommand):
    help = "Seed the database with realistic person records"

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=100000,
            help="Number of records to generate (default: 100000)",
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete existing records before seeding",
        )

    def handle(self, *args, **options):
        count = options["count"]
        flush = options["flush"]

        if flush:
            deleted, _ = Person.objects.all().delete()
            self.stdout.write(f"Flushed {deleted:,} existing records")

        self.stdout.write(f"Generating {count:,} person records...")
        self._seed(count)
        self.stdout.write(self.style.SUCCESS(f"Seeded {count:,} records successfully"))

    def _seed(self, count: int) -> None:
        """Generate and bulk-create person records."""
        fake = Faker("en_US")
        batch_size = 5000
        records: list[Person] = []
        nicknames: list[str] = []  # default for non-nickname records

        # Common last names for realistic distribution
        common_last_names = [
            "SMITH",
            "JOHNSON",
            "WILLIAMS",
            "BROWN",
            "JONES",
            "GARCIA",
            "MILLER",
            "DAVIS",
            "RODRIGUEZ",
            "MARTINEZ",
            "HERNANDEZ",
            "LOPEZ",
            "GONZALEZ",
            "WILSON",
            "ANDERSON",
            "THOMAS",
            "TAYLOR",
            "MOORE",
            "JACKSON",
            "MARTIN",
            "LEE",
            "PEREZ",
            "THOMPSON",
            "WHITE",
            "HARRIS",
            "SANCHEZ",
            "CLARK",
            "RAMIREZ",
            "LEWIS",
            "ROBINSON",
            "WALKER",
            "YOUNG",
            "ALLEN",
            "KING",
            "WRIGHT",
            "SCOTT",
            "TORRES",
            "NGUYEN",
            "HILL",
            "FLORES",
            "GREEN",
            "ADKINS",
            "NASH",
            "MORRISON",
            "MURPHY",
            "RIVERA",
            "COOPER",
            "REED",
            "BAKER",
            "HUGHES",
        ]
        canonical_names = list(NICKNAME_MAP.keys())

        for i in range(1, count + 1):
            # Last name (70% common names)
            if random.random() < 0.7:
                last_name = random.choice(common_last_names)
            else:
                last_name = fake.last_name().upper()

            # First name with optional nickname (~30%)
            nicknames = []
            if random.random() < 0.30:
                canonical = random.choice(canonical_names)
                nicknames = list(NICKNAME_MAP[canonical])
                first_name = random.choice(nicknames)
            else:
                first_name = fake.first_name()

            # Middle name (~20%)
            middle_name = None
            if random.random() < 0.20:
                middle_name = fake.first_name() if random.random() < 0.5 else random.choice(string.ascii_uppercase)

            # Date of birth (~80%)
            dob = None
            if random.random() < 0.80:
                years_ago = random.randint(18, 85)
                dob = date.today() - timedelta(days=years_ago * 365 + random.randint(0, 365))

            # Inject typos (~1%)
            if random.random() < 0.01:
                if random.random() < 0.5:
                    first_name = self._inject_typo(first_name)
                else:
                    last_name = self._inject_typo(last_name)

            # Compute phonetic tokens
            first_tokens = list(dict.fromkeys(soundex_tokens(first_name) + dm_soundex_tokens(first_name)))
            last_tokens = list(dict.fromkeys(soundex_tokens(last_name) + dm_soundex_tokens(last_name)))

            records.append(
                Person(
                    first_name=first_name,
                    last_name=last_name,
                    middle_name=middle_name,
                    date_of_birth=dob,
                    nicknames=nicknames,
                    first_name_phonetic=first_tokens,
                    last_name_phonetic=last_tokens,
                )
            )

            # Bulk create in batches
            if len(records) >= batch_size:
                Person.objects.bulk_create(records)
                self.stdout.write(f"  ... {len(records):,} / {count:,} records inserted")
                records = []

        # Insert remaining records
        if records:
            Person.objects.bulk_create(records)
            self.stdout.write(f"  ... {count:,} / {count:,} records inserted")

    @staticmethod
    def _inject_typo(name: str) -> str:
        """Inject a single random typo into a name."""
        if len(name) < 2:
            return name

        typo_type = random.choice(["swap", "drop", "substitute"])

        if typo_type == "swap":
            idx = random.randint(0, len(name) - 2)
            chars = list(name)
            chars[idx], chars[idx + 1] = chars[idx + 1], chars[idx]
            return "".join(chars)

        elif typo_type == "drop":
            idx = random.randint(0, len(name) - 1)
            return name[:idx] + name[idx + 1 :]

        else:  # substitute
            idx = random.randint(0, len(name) - 1)
            chars = list(name)
            chars[idx] = random.choice(string.ascii_uppercase)
            return "".join(chars)
