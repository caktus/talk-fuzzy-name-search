"""Seed the database with realistic person records for demo/benchmarking.

Usage:
    python manage.py seed_data --count 100000 --flush
    python manage.py seed_data --count 100000 --flush --seed 42 --as-of 2026-01-01

--count is the target number of final rows (after cluster expansion).

Distribution model (rewritten 2026-08-14 after RECS-2026-08-14 B3 — honest + deterministic):
    1. Name pool: exactly pool_size DISTINCT (first_name, last_name) pairs, drawn
       uniformly without replacement from Faker's en_US cartesian space
       (690 first x 1,000 last = 690,000 possible pairs; the pool is capped at
       the space size when --count is large).
    2. Name frequency: pool rows are sampled with Zipf(a = ZIPF_ALPHA = 1.1), a
       genuine heavy tail. Zipf index i (1-based) maps to pool row
       (i - 1) % len(pool); indices past the pool size wrap modulo, which keeps
       every pair reachable. This step is the sole source of name-frequency skew.
    3. Identities generated in batches until expanded row total reaches --count.
       Each identity gets one DOB; cluster variants share that DOB and person_id.
       Cluster size: 80% singleton, 20% Pareto(1.5), clipped to [2, 80].
       Each batch holds at most MAX_BATCH_IDENTITIES identities (B10), so
       peak memory is bounded to one batch regardless of --count.
    4. Vectorized generation via Polars DataFrames.
    5. Bulk-insert in batches of 100,000.

Reproducibility:
    The (seed, count, as-of) triple fully determines the generated rows. DOBs
    derive from --as-of (default: date.today() at run time), and person_ids are
    drawn from the seeded RNG (two 64-bit draws per identity, combined into one
    128-bit UUID). Re-running with the same triple reproduces the same names,
    DOBs, and person_ids.

Variation injection:
    ~30% of identities: use a nickname as the stored first_name
    ~90% of identities: include a middle name (mix of full names and initials)
    ~20% of non-canonical rows in multi-member clusters: inject a single random
    typo into first_name or last_name (the first row of each cluster is the
    canonical row and is never typo'd)
"""

import string
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

import numpy as np
import polars as pl
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from faker import Faker

from records.models import Person
from records.phonetics import NICKNAME_MAP

# Generation rates
NAME_POOL_FRACTION = 0.2  # pool size = 20% of --count
ZIPF_ALPHA = 1.1  # Zipf skew for name-pair frequencies (1 < a < 2: heavy tail)
NICKNAME_RATE = 0.30
MIDDLE_NAME_RATE = 0.90
TYPO_RATE = 0.20  # of non-canonical rows in multi-member clusters
BATCH_SIZE = 100_000
MAX_BATCH_IDENTITIES = 2_000_000  # memory cap per generation batch (B10: ~3M rows at avg cluster size 1.5)
SAMPLE_CASES_COUNT_LIMIT = 1_000_000  # skip post-seed sample cases above this --count


class Command(BaseCommand):
    help = (
        "Seed the database with realistic person records. Names: deduped pool sampled "
        "with a Zipf(a=1.1) heavy tail. Fully reproducible from the (seed, count, as-of) triple."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=100000,
            help="Target number of final rows after cluster expansion (default: 100000)",
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Truncate existing records (and reset the id sequence) before seeding",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=42,
            help="Random seed for reproducibility (default: 42)",
        )
        parser.add_argument(
            "--as-of",
            type=str,
            default=None,
            help=(
                "Reference date (YYYY-MM-DD) that DOB generation treats as 'today' "
                "(default: the actual current date). Pin this together with --seed "
                "for exact reproducibility."
            ),
        )
        parser.add_argument(
            "--no-cases",
            action="store_true",
            help="Skip the post-seed sample-case generation (full-table soundex sort + unindexed scans)",
        )
        parser.add_argument(
            "--print-cases",
            action="store_true",
            help="Force post-seed sample-case generation even for large --count (default: skipped above 1,000,000)",
        )

    def handle(self, *args, **options):
        count = options["count"]
        flush = options["flush"]
        rng_seed = options["seed"]

        if options["as_of"] is None:
            as_of = date.today()
        else:
            try:
                as_of = datetime.strptime(options["as_of"], "%Y-%m-%d").date()
            except ValueError:
                raise CommandError(f"Invalid --as-of date {options['as_of']!r}; expected YYYY-MM-DD") from None

        if flush:
            deleted = Person.objects.count()
            self._flush()
            self.stdout.write(f"Flushed {deleted:,} existing records (TRUNCATE records_person RESTART IDENTITY)")

        self.stdout.write(f"Generating {count:,} person records (seed={rng_seed}, as-of={as_of})...")
        start = time.perf_counter()
        self._seed(count, rng_seed, as_of)
        elapsed = time.perf_counter() - start
        total = Person.objects.count()
        self.stdout.write(self.style.SUCCESS(f"Seeded {total:,} records in {elapsed:.1f}s"))

        # Post-seed sample cases are a full-table ORDER BY SOUNDEX sort plus
        # unindexed scans: fine for small seeds, minutes at 54M. Run them by
        # default only up to SAMPLE_CASES_COUNT_LIMIT; --no-cases always skips,
        # --print-cases always forces.
        if options["print_cases"] or (count <= SAMPLE_CASES_COUNT_LIMIT and not options["no_cases"]):
            self._print_sample_cases()
        elif not options["no_cases"]:
            self.stdout.write("Sample-case generation skipped (count > 1,000,000); re-run with --print-cases to force.")

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def _seed(self, count: int, rng_seed: int, as_of: date) -> None:
        """Generate and bulk-create person records using Polars.

        --count is the target number of *final rows* (after cluster expansion).
        Identities are generated in batches; each batch is expanded and inserted
        immediately to bound memory usage (no accumulation of all rows).

        All date logic derives from as_of (the --as-of reference date), so a
        given (seed, count, as_of) triple reproduces the same rows.
        """
        rng = np.random.default_rng(rng_seed)
        fake = Faker("en_US")
        fake.seed_instance(rng_seed)

        pool_size = max(int(count * NAME_POOL_FRACTION), 100)

        # --- 1. Build name pool (distinct pairs) ---
        self.stdout.write(f"  Building name pool (target {pool_size:,} unique pairs)...")
        t0 = time.perf_counter()
        name_pool = self._build_name_pool(pool_size, fake, rng)
        self.stdout.write(f"    done in {time.perf_counter() - t0:.1f}s ({len(name_pool):,} unique pairs)")

        # --- 2-4. Generate + insert in streaming batches ---
        AVG_CLUSTER_SIZE = 1.5  # rough estimate (80% size=1, 20% Pareto)
        total_inserted = 0
        batch_num = 0

        self.stdout.write(f"  Bulk inserting (batch size {BATCH_SIZE:,})...")
        while total_inserted < count:
            remaining = count - total_inserted
            batch_identities = self._batch_identities(remaining, AVG_CLUSTER_SIZE)
            batch_num += 1

            self.stdout.write(
                f"  Batch {batch_num}: sampling {batch_identities:,} identities (need ~{remaining:,} more rows)"
            )
            t0 = time.perf_counter()
            sampled = self._zipf_sample(name_pool, batch_identities, rng)
            identities = self._assign_attributes(sampled, batch_identities, rng, fake, as_of)
            batch_rows = self._expand_clusters(identities, rng)
            self.stdout.write(f"    generate: {time.perf_counter() - t0:.1f}s ({len(batch_rows):,} rows)")

            # Trim final batch to exact target
            if total_inserted + len(batch_rows) > count:
                batch_rows = batch_rows.head(count - total_inserted)

            t0 = time.perf_counter()
            self._bulk_insert(batch_rows)
            total_inserted += len(batch_rows)
            self.stdout.write(f"    insert: {time.perf_counter() - t0:.1f}s → {total_inserted:,} total")

            # Free memory before next batch
            del sampled, identities, batch_rows

            if total_inserted >= count:
                self.stdout.write(f"  Trimmed to exact target: {count:,} rows")

    @staticmethod
    def _batch_identities(remaining: int, avg_cluster_size: float = 1.5) -> int:
        """Number of identities to sample for the next batch (B10).

        One batch targets ~`remaining` expanded rows (identities x avg cluster
        size), floored at 1000 and capped at MAX_BATCH_IDENTITIES so memory is
        bounded to a single batch (2M identities ~ 3M rows) at any --count.
        """
        return min(max(int(remaining / avg_cluster_size), 1000), MAX_BATCH_IDENTITIES)

    @staticmethod
    def _flush() -> None:
        """Empty the person table and reset the id sequence.

        TRUNCATE ... RESTART IDENTITY instead of DELETE: no per-row delete
        overhead, no 54M dead tuples left for autovacuum, and new seeds
        start at id 1 instead of continuing the sequence.
        """
        with connection.cursor() as cursor:
            cursor.execute("TRUNCATE records_person RESTART IDENTITY")

    def _build_name_pool(self, pool_size: int, fake: Faker, rng: np.random.Generator) -> pl.DataFrame:
        """Build a pool of DISTINCT first/last name pairs.

        Pairs are drawn uniformly *without replacement* from Faker's en_US
        cartesian space (every first-name x last-name combination), so every
        row of the pool is a unique pair. If pool_size exceeds the space size
        (690 x 1,000 = 690,000 for faker 40.x), the pool is capped at the
        space size and a note is printed.
        """
        from faker.providers.person.en_US import Provider as en_US

        all_first_names = [n.upper() for n in en_US.first_names.keys()]
        all_last_names = [n.upper() for n in en_US.last_names.keys()]
        space_size = len(all_first_names) * len(all_last_names)

        if pool_size > space_size:
            self.stdout.write(
                f"  Note: requested pool of {pool_size:,} exceeds the {space_size:,} distinct pairs in "
                f"the en_US name space; capping the pool at {space_size:,}."
            )
            pool_size = space_size

        flat = rng.choice(space_size, size=pool_size, replace=False)
        first_names = np.array(all_first_names)[flat // len(all_last_names)]
        last_names = np.array(all_last_names)[flat % len(all_last_names)]

        return pl.DataFrame(
            {
                "first_name": first_names,
                "last_name": last_names,
            }
        )

    @staticmethod
    def _zipf_sample(pool: pl.DataFrame, sample_size: int, rng: np.random.Generator) -> pl.DataFrame:
        """Sample pool rows with Zipf(a=ZIPF_ALPHA) indices for a heavy-tailed name frequency.

        Zipf index i (1-based) maps to pool row (i - 1) % len(pool). Indices
        past the pool size wrap modulo, so every pool row stays reachable
        while draws concentrate on the first pool rows.
        """
        indices = (rng.zipf(ZIPF_ALPHA, size=sample_size) - 1) % len(pool)
        return pool[indices]

    def _assign_attributes(
        self, df: pl.DataFrame, count: int, rng: np.random.Generator, fake: Faker, as_of: date
    ) -> pl.DataFrame:
        """Assign DOB, person_id, cluster_size, middle_name, nicknames per identity.

        DOBs are drawn relative to as_of (the --as-of reference date), so the
        same (seed, count, as_of) triple reproduces the same DOBs. person_ids
        are drawn from the seeded RNG — two 64-bit draws per identity combined
        into one 128-bit UUID — and are shared by every row of the identity's
        cluster.
        """
        import uuid

        n = len(df)

        # DOB: random date between 18-85 years before as_of (100% of records)
        base_days = rng.integers(18 * 365, 85 * 365, size=n)
        dobs = [as_of - timedelta(days=int(d)) for d in base_days]

        # person_id: one deterministic UUID per identity, drawn from the seeded RNG
        hi = rng.integers(0, 2**64, size=n, dtype=np.uint64)
        lo = rng.integers(0, 2**64, size=n, dtype=np.uint64)
        person_ids = [uuid.UUID(int=int(hi_val) << 64 | int(lo_val)) for hi_val, lo_val in zip(hi, lo)]

        # Cluster size: heavy-tailed (most 1, some 20-50+)
        # Use a Pareto-like distribution: 80% get 1, 15% get 2-10, 5% get 11-50+
        cluster_sizes = np.ones(n, dtype=np.int64)
        # 20% of identities get a larger cluster
        large_cluster_mask = rng.random(n) < 0.20
        n_large = int(large_cluster_mask.sum())
        if n_large > 0:
            # Heavy-tailed: use power law to get 20-50+ for some
            large_sizes = rng.pareto(1.5, size=n_large).astype(int) + 2
            large_sizes = np.clip(large_sizes, 2, 80)
            cluster_sizes[large_cluster_mask] = large_sizes

        # Middle name: 90% chance
        has_middle = rng.random(n) < MIDDLE_NAME_RATE
        middle_names = np.full(n, None, dtype=object)
        n_middle = int(has_middle.sum())

        if n_middle > 0:
            # Pre-generate middle name pool
            middle_pool_full = [fake.first_name().upper() for _ in range(max(n_middle, 100))]
            middle_pool_initial = list(string.ascii_uppercase)

            is_full = rng.random(n_middle) < 0.5
            n_full = int(is_full.sum())
            n_initial = n_middle - n_full

            middle_arr = np.empty(n_middle, dtype=object)
            if n_full > 0:
                middle_arr[is_full] = rng.choice(middle_pool_full, size=n_full)
            if n_initial > 0:
                middle_arr[~is_full] = rng.choice(middle_pool_initial, size=n_initial)

            middle_names[has_middle] = middle_arr

        # Nicknames: 30% chance — use a canonical name from NICKNAME_MAP
        canonical_names = list(NICKNAME_MAP.keys())
        has_nickname = rng.random(n) < NICKNAME_RATE
        n_nick = int(has_nickname.sum())

        first_names_arr = df["first_name"].to_numpy()
        nicknames_arr = [[] for _ in range(n)]  # Python list, not numpy array

        if n_nick > 0:
            chosen_canonicals = rng.choice(canonical_names, size=n_nick)
            for i, idx in enumerate(np.where(has_nickname)[0]):
                canonical = chosen_canonicals[i]
                nick_list = list(NICKNAME_MAP[canonical])
                chosen_nick = rng.choice(nick_list)
                first_names_arr[idx] = chosen_nick.upper()
                nicknames_arr[idx] = nick_list

        # Convert to Python list so Polars infers String (not Object) for nullable columns
        middle_list = [None if m is None else m for m in middle_names]

        df = df.with_columns(
            pl.Series("date_of_birth", dobs),
            pl.Series("person_id", person_ids),
            pl.Series("cluster_size", cluster_sizes),
            pl.Series("middle_name", middle_list),
            pl.Series("nicknames", nicknames_arr),
        )
        # Update first_name with nickname choices
        df = df.with_columns(pl.Series("first_name", first_names_arr))

        return df

    def _expand_clusters(self, df: pl.DataFrame, rng: np.random.Generator) -> pl.DataFrame:
        """Expand each identity into a cluster of records, injecting typos.

        Vectorized expansion: repeat each identity cluster_size times,
        then inject typos into ~20% of expanded records.
        """
        df = df.with_row_index("identity_idx")
        n = len(df)

        # Build flat index array: [0,0,0, 1,1, 2,2,2,2, ...] where each idx repeats cluster_size times
        cluster_sizes = df["cluster_size"].to_numpy()
        flat_indices = np.repeat(np.arange(n), cluster_sizes)
        expanded_df = pl.DataFrame({"identity_idx": flat_indices})

        # Join to get all attributes from the original identities
        expanded_df = expanded_df.join(
            df.select(
                ["identity_idx", "first_name", "last_name", "middle_name", "date_of_birth", "person_id", "nicknames"]
            ),
            on="identity_idx",
            how="left",
        ).drop("identity_idx")

        # Inject typos into ~TYPO_RATE of records (skip first in each cluster)
        total = len(expanded_df)
        typo_flags = rng.random(total) < TYPO_RATE
        # Find cluster start positions and protect them from typos
        cluster_starts = np.concatenate([[0], np.cumsum(cluster_sizes[:-1])])
        typo_flags[cluster_starts] = False

        # Apply typos using numpy arrays (avoids Python list overhead)
        typo_indices = np.where(typo_flags)[0]
        if len(typo_indices) > 0:
            first_names = expanded_df["first_name"].to_numpy()  # numpy, not Python list
            last_names = expanded_df["last_name"].to_numpy()
            typo_first = rng.random(len(typo_indices)) < 0.5
            for i, idx in enumerate(typo_indices):
                if typo_first[i]:
                    first_names[idx] = self._inject_typo(first_names[idx], rng)
                else:
                    last_names[idx] = self._inject_typo(last_names[idx], rng)
            expanded_df = expanded_df.with_columns(
                pl.Series("first_name", first_names),
                pl.Series("last_name", last_names),
            )

        return expanded_df

    @staticmethod
    def _inject_typo(name: str, rng: np.random.Generator) -> str:
        """Inject a single random typo into a name (guaranteed to change it)."""
        if len(name) < 2:
            return name

        for _ in range(10):  # Retry up to 10 times
            typo_type = rng.choice(["swap", "drop", "substitute"])

            if typo_type == "swap":
                idx = rng.integers(0, len(name) - 1)
                chars = list(name)
                chars[idx], chars[idx + 1] = chars[idx + 1], chars[idx]
                result = "".join(chars)
            elif typo_type == "drop":
                idx = rng.integers(0, len(name))
                result = name[:idx] + name[idx + 1 :]
            else:  # substitute
                idx = rng.integers(0, len(name))
                chars = list(name)
                chars[idx] = string.ascii_uppercase[rng.integers(0, 26)]
                result = "".join(chars)

            if result != name:
                return result

        # Fallback: always produces a change
        return name[:-1] + string.ascii_uppercase[(ord(name[-1]) - ord("A") + 1) % 26]

    def _bulk_insert(self, df: pl.DataFrame) -> None:
        """Bulk-insert records from a Polars DataFrame in batches.

        Uses numpy column access for faster iteration than iter_rows.
        """
        from itertools import islice

        total = len(df)

        # Extract columns as numpy arrays (faster than iter_rows)
        fn_arr = df["first_name"].to_numpy()
        ln_arr = df["last_name"].to_numpy()
        mn_arr = df["middle_name"].to_numpy()
        dob_arr = df["date_of_birth"].to_numpy()
        pid_arr = df["person_id"].to_numpy()
        nick_arr = df["nicknames"].to_numpy()

        # Convert numpy datetime64 to Python dates (Django doesn't accept datetime64)
        if dob_arr.dtype.kind == "M":
            dob_arr = dob_arr.astype("datetime64[D]").astype(object)

        inserted = 0
        row_iter = iter(range(total))

        while True:
            batch_indices = list(islice(row_iter, BATCH_SIZE))
            if not batch_indices:
                break

            records = [
                Person(
                    first_name=fn_arr[i],
                    last_name=ln_arr[i],
                    middle_name=None if mn_arr[i] is None else mn_arr[i],
                    date_of_birth=dob_arr[i],
                    nicknames=list(nick_arr[i]) if nick_arr[i].size else [],
                    person_id=pid_arr[i],
                )
                for i in batch_indices
            ]
            Person.objects.bulk_create(records)
            inserted += len(records)
            self.stdout.write(f"    ... {inserted:,} / {total:,} records inserted")

    # ------------------------------------------------------------------
    # Sample search cases
    # ------------------------------------------------------------------

    def _print_sample_cases(self) -> None:
        """Print 5-10 interesting example search cases to stdout."""
        out = self.stdout.write
        out("")
        out("=" * 70)
        out("SAMPLE SEARCH CASES — try these in the search UI:")
        out("=" * 70)

        case_num = 0

        # 1. Large clusters (same person_id, many records)
        out("")
        out("--- Large person clusters (same person_id, many records) ---")
        large_clusters = self._find_large_clusters()
        for cluster in large_clusters[:3]:
            case_num += 1
            pid = str(cluster["person_id"])
            out(f"  [{case_num}] person_id={pid[:12]}... ({cluster['count']} records, DOB: {cluster['dob']})")
            for rec in cluster["sample"][:5]:
                out(f"       {rec['first_name']} {rec['last_name']}")
            if cluster["count"] > 5:
                out(f"       ... and {cluster['count'] - 5} more")

        # 2. Phonetic matches with different DOBs
        out("")
        out("--- Phonetic matches (Soundex collision, different DOBs) ---")
        phonetic_cases = self._find_phonetic_collisions()
        for group in phonetic_cases[:2]:
            case_num += 1
            out(f"  [{case_num}] Soundex group:")
            for rec in group:
                out(f"       {rec['first_name']} {rec['last_name']}, DOB: {rec['date_of_birth']}")

        # 3. Nickname pairs
        out("")
        out("--- Nickname pairs ---")
        nickname_cases = self._find_nickname_pairs()
        for pair in nickname_cases[:2]:
            case_num += 1
            out(
                f"  [{case_num}] Canonical: {pair['canonical_first']} {pair['last_name']}, DOB: {pair['canonical_dob']}"
            )
            out(f"       Nickname:  {pair['nickname_first']} {pair['last_name']}, DOB: {pair['nickname_dob']}")

        out("")
        out("-" * 70)

    def _find_large_clusters(self) -> list[dict]:
        """Find person_id clusters with 20+ records."""
        from django.db.models import Count

        large_clusters = (
            Person.objects.values("person_id", "date_of_birth")
            .annotate(cnt=Count("id"))
            .filter(cnt__gte=20)
            .order_by("-cnt")[:10]
        )

        results = []
        for cluster in large_clusters:
            pid = cluster["person_id"]
            records = list(Person.objects.filter(person_id=pid).values("first_name", "last_name", "date_of_birth")[:5])
            results.append(
                {
                    "person_id": str(pid),
                    "dob": cluster["date_of_birth"],
                    "count": cluster["cnt"],
                    "sample": records,
                }
            )
        return results

    @staticmethod
    def _levenshtein(a: str, b: str) -> int:
        """Compute Levenshtein distance between two strings (case-insensitive)."""
        a, b = a.upper(), b.upper()
        if len(a) < len(b):
            return Command._levenshtein(b, a)
        if len(b) == 0:
            return len(a)
        prev_row = list(range(len(b) + 1))
        for i, ca in enumerate(a):
            curr_row = [i + 1]
            for j, cb in enumerate(b):
                insertions = prev_row[j + 1] + 1
                deletions = curr_row[j] + 1
                substitutions = prev_row[j] + (ca != cb)
                curr_row.append(min(insertions, deletions, substitutions))
            prev_row = curr_row
        return prev_row[-1]

    def _find_phonetic_collisions(self) -> list[list[dict]]:
        """Find groups of records with the same Soundex code but different DOBs."""
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT p.first_name, p.last_name, p.date_of_birth,
                       SOUNDEX(UPPER(p.first_name)) as fn_soundex,
                       SOUNDEX(UPPER(p.last_name)) as ln_soundex
                FROM records_person p
                ORDER BY fn_soundex, ln_soundex
                LIMIT 10000
            """)
            rows = [
                {
                    "first_name": r[0],
                    "last_name": r[1],
                    "date_of_birth": r[2],
                    "fn_soundex": r[3],
                    "ln_soundex": r[4],
                }
                for r in cursor.fetchall()
            ]

        # Group by Soundex pair
        by_soundex = defaultdict(list)
        for row in rows:
            key = (row["fn_soundex"], row["ln_soundex"])
            by_soundex[key].append(row)

        # Find groups with different DOBs
        results = []
        for key, group in by_soundex.items():
            dobs = set(r["date_of_birth"] for r in group)
            if len(dobs) > 1 and len(group) >= 2:
                results.append(group[:5])  # Limit to 5 per group
                if len(results) >= 3:
                    break
        return results

    def _find_nickname_pairs(self) -> list[dict]:
        """Find records where a canonical name and its nickname both exist."""
        results = []
        canonical_names = list(NICKNAME_MAP.keys())

        for canonical in canonical_names[:20]:  # Check first 20 canonical names
            nicknames = NICKNAME_MAP[canonical]
            for nick in nicknames:
                # Check if both canonical and nickname exist
                canonical_recs = list(
                    Person.objects.filter(first_name__iexact=canonical).values(
                        "first_name", "last_name", "date_of_birth"
                    )[:2]
                )
                nick_recs = list(
                    Person.objects.filter(first_name__iexact=nick).values("first_name", "last_name", "date_of_birth")[
                        :2
                    ]
                )

                if canonical_recs and nick_recs:
                    # Find matching last names
                    canonical_lnames = {r["last_name"] for r in canonical_recs}
                    nick_lnames = {r["last_name"] for r in nick_recs}
                    common_lnames = canonical_lnames & nick_lnames
                    for ln in list(common_lnames)[:1]:
                        c_rec = next(r for r in canonical_recs if r["last_name"] == ln)
                        n_rec = next(r for r in nick_recs if r["last_name"] == ln)
                        results.append(
                            {
                                "canonical_first": c_rec["first_name"],
                                "nickname_first": n_rec["first_name"],
                                "last_name": ln,
                                "canonical_dob": c_rec["date_of_birth"],
                                "nickname_dob": n_rec["date_of_birth"],
                            }
                        )
                        if len(results) >= 3:
                            return results
        return results
