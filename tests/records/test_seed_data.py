"""Tests for seed_data management command generation logic.

Tests the vectorized name generation, Zipf sampling, cluster expansion,
typo injection within clusters, person_id consistency, batched bulk-insert,
and (seed, count, as-of) reproducibility.
"""

from datetime import date
from uuid import UUID

import numpy as np
import polars as pl
import pytest

from records.management.commands.seed_data import Command
from records.models import Person

pytestmark = pytest.mark.django_db

# Fixed reference date so tests never depend on the wall clock.
AS_OF = date(2026, 1, 1)


class TestNamePoolGeneration:
    """Test _build_name_pool generates distinct name combinations."""

    def test_pool_size(self):
        """Pool size matches requested size."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        rng = np.random.default_rng(42)
        pool = cmd._build_name_pool(500, fake, rng)

        assert len(pool) == 500
        assert "first_name" in pool.columns
        assert "last_name" in pool.columns

    def test_pool_pairs_are_distinct(self):
        """Every pair in the pool is unique (deduped pool)."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        rng = np.random.default_rng(42)
        pool = cmd._build_name_pool(500, fake, rng)

        assert len(pool.unique()) == len(pool)

    def test_names_are_uppercase(self):
        """All names in the pool are uppercase."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        rng = np.random.default_rng(42)
        pool = cmd._build_name_pool(200, fake, rng)

        for name in pool["first_name"].to_list():
            assert name == name.upper()
        for name in pool["last_name"].to_list():
            assert name == name.upper()

    def test_no_empty_names(self):
        """No empty names in the pool."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        rng = np.random.default_rng(42)
        pool = cmd._build_name_pool(500, fake, rng)

        assert all(len(name) > 0 for name in pool["first_name"].to_list())
        assert all(len(name) > 0 for name in pool["last_name"].to_list())

    def test_pool_capped_at_name_space(self):
        """Pool cannot exceed the en_US cartesian space and stays distinct."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        rng = np.random.default_rng(42)
        pool = cmd._build_name_pool(2_000_000, fake, rng)

        assert len(pool) <= 690_000
        assert len(pool.unique()) == len(pool)


class TestZipfSampling:
    """Test _zipf_sample produces a heavy-tailed name frequency distribution."""

    def test_sample_size(self):
        """Sample size matches requested size."""
        cmd = Command()
        pool = pl.DataFrame(
            {
                "first_name": [f"FIRST{i}" for i in range(100)],
                "last_name": [f"LAST{i}" for i in range(100)],
            }
        )
        rng = np.random.default_rng(42)
        sampled = cmd._zipf_sample(pool, 500, rng)

        assert len(sampled) == 500

    def test_heavy_tailed_distribution(self):
        """Zipf sampling produces a heavy tail: top pair far more frequent than the median pair."""
        cmd = Command()
        pool = pl.DataFrame(
            {
                "first_name": [f"FIRST{i}" for i in range(100)],
                "last_name": [f"LAST{i}" for i in range(100)],
            }
        )
        rng = np.random.default_rng(42)
        sampled = cmd._zipf_sample(pool, 10_000, rng)

        # Frequency of every pool pair (including pairs that drew zero samples)
        pos = {pair: i for i, pair in enumerate(zip(pool["first_name"], pool["last_name"]))}
        freq = np.zeros(len(pool), dtype=int)
        for fn, ln, c in sampled.group_by(["first_name", "last_name"]).agg(pl.len().alias("c")).iter_rows():
            freq[pos[(fn, ln)]] = c

        top = freq.max()
        median = np.median(freq)
        tenth = np.sort(freq)[::-1][9]

        # Measured across 30 seeds: top/median is >= 12x, top/10th is >= 8x — 5x/2x are robust margins.
        assert top >= 5 * median
        assert top >= 2 * tenth

    def test_sampled_names_from_pool(self):
        """All sampled names exist in the original pool."""
        cmd = Command()
        pool = pl.DataFrame(
            {
                "first_name": [f"FIRST{i}" for i in range(100)],
                "last_name": [f"LAST{i}" for i in range(100)],
            }
        )
        rng = np.random.default_rng(42)
        sampled = cmd._zipf_sample(pool, 500, rng)

        pool_set = set(zip(pool["first_name"].to_list(), pool["last_name"].to_list()))
        for fn, ln in zip(sampled["first_name"].to_list(), sampled["last_name"].to_list()):
            assert (fn, ln) in pool_set


class TestTypoInjection:
    """Test _inject_typo produces valid single-character typos."""

    def test_swap_typo(self):
        """Swap typo exchanges two adjacent characters."""
        cmd = Command()
        rng = np.random.default_rng(0)
        result = cmd._inject_typo("SMITH", rng)
        assert len(result) == len("SMITH")
        assert result != "SMITH"

    def test_drop_typo(self):
        """Drop typo removes one character."""
        cmd = Command()
        rng = np.random.default_rng(1)
        result = cmd._inject_typo("SMITH", rng)
        assert len(result) == len("SMITH") - 1

    def test_substitute_typo(self):
        """Substitute typo replaces one character."""
        cmd = Command()
        rng = np.random.default_rng(2)
        result = cmd._inject_typo("SMITH", rng)
        assert len(result) == len("SMITH")
        assert result != "SMITH"

    def test_short_name_unchanged(self):
        """Names shorter than 2 characters are unchanged."""
        cmd = Command()
        rng = np.random.default_rng(42)
        assert cmd._inject_typo("A", rng) == "A"


class TestClusterExpansion:
    """Test _expand_clusters produces correct cluster structures."""

    def test_expansion_respects_cluster_sizes(self):
        """Expanded rows count matches sum of cluster sizes."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        fake.seed_instance(42)
        rng = np.random.default_rng(42)

        pool = cmd._build_name_pool(50, fake, rng)
        sampled = cmd._zipf_sample(pool, 100, rng)
        identities = cmd._assign_attributes(sampled, 100, rng, fake, AS_OF)
        expanded = cmd._expand_clusters(identities, rng)

        expected_total = int(identities["cluster_size"].sum())
        assert len(expanded) == expected_total

    def test_cluster_records_share_person_id_and_dob(self):
        """All records from the same identity share person_id and DOB."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        fake.seed_instance(42)
        rng = np.random.default_rng(42)

        pool = cmd._build_name_pool(50, fake, rng)
        sampled = cmd._zipf_sample(pool, 100, rng)
        identities = cmd._assign_attributes(sampled, 100, rng, fake, AS_OF)
        expanded = cmd._expand_clusters(identities, rng)

        # Group by person_id string and verify all share the same DOB
        pid_groups = {}
        for row in expanded.iter_rows(named=True):
            pid_str = str(row["person_id"])
            pid_groups.setdefault(pid_str, set()).add(row["date_of_birth"])
        for pid_str, dobs in pid_groups.items():
            assert len(dobs) == 1, f"Cluster {pid_str} has multiple DOBs: {dobs}"

    def test_expanded_records_have_person_id(self):
        """All expanded records have a valid person_id."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        fake.seed_instance(42)
        rng = np.random.default_rng(42)

        pool = cmd._build_name_pool(50, fake, rng)
        sampled = cmd._zipf_sample(pool, 100, rng)
        identities = cmd._assign_attributes(sampled, 100, rng, fake, AS_OF)
        expanded = cmd._expand_clusters(identities, rng)

        for row in expanded.iter_rows(named=True):
            assert isinstance(row["person_id"], UUID)


class TestBulkInsert:
    """Test _bulk_insert creates records correctly."""

    def test_bulk_insert_creates_records(self):
        """Bulk insert creates the expected number of records."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        fake.seed_instance(42)
        rng = np.random.default_rng(42)

        pool = cmd._build_name_pool(50, fake, rng)
        sampled = cmd._zipf_sample(pool, 200, rng)
        identities = cmd._assign_attributes(sampled, 200, rng, fake, AS_OF)
        expanded = cmd._expand_clusters(identities, rng)

        assert Person.objects.count() == 0
        cmd._bulk_insert(expanded)
        assert Person.objects.count() == len(expanded)

    def test_bulk_insert_dob_not_null(self):
        """All inserted records have a non-NULL date_of_birth."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        fake.seed_instance(42)
        rng = np.random.default_rng(42)

        pool = cmd._build_name_pool(50, fake, rng)
        sampled = cmd._zipf_sample(pool, 200, rng)
        identities = cmd._assign_attributes(sampled, 200, rng, fake, AS_OF)
        expanded = cmd._expand_clusters(identities, rng)

        cmd._bulk_insert(expanded)
        null_count = Person.objects.filter(date_of_birth__isnull=True).count()
        assert null_count == 0

    def test_bulk_insert_person_id_not_null(self):
        """All inserted records have a non-NULL person_id."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        fake.seed_instance(42)
        rng = np.random.default_rng(42)

        pool = cmd._build_name_pool(50, fake, rng)
        sampled = cmd._zipf_sample(pool, 200, rng)
        identities = cmd._assign_attributes(sampled, 200, rng, fake, AS_OF)
        expanded = cmd._expand_clusters(identities, rng)

        cmd._bulk_insert(expanded)
        null_count = Person.objects.filter(person_id__isnull=True).count()
        assert null_count == 0

    def test_bulk_insert_nickname_records(self):
        """Some records have nicknames populated."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        fake.seed_instance(42)
        rng = np.random.default_rng(42)

        pool = cmd._build_name_pool(500, fake, rng)
        sampled = cmd._zipf_sample(pool, 1000, rng)
        identities = cmd._assign_attributes(sampled, 1000, rng, fake, AS_OF)
        expanded = cmd._expand_clusters(identities, rng)

        cmd._bulk_insert(expanded)
        with_nicknames = Person.objects.filter(nicknames__len__gt=0).count()
        assert with_nicknames > 0

    def test_bulk_insert_middle_name_records(self):
        """Most records have middle names (90% rate)."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        fake.seed_instance(42)
        rng = np.random.default_rng(42)

        pool = cmd._build_name_pool(500, fake, rng)
        sampled = cmd._zipf_sample(pool, 1000, rng)
        identities = cmd._assign_attributes(sampled, 1000, rng, fake, AS_OF)
        expanded = cmd._expand_clusters(identities, rng)

        cmd._bulk_insert(expanded)
        with_middle = Person.objects.exclude(middle_name__isnull=True).count()
        assert with_middle > len(expanded) * 0.8  # Allow some variance


class TestReproducibility:
    """(seed, count, as-of) must fully determine the generated rows.

    Runs _seed() in-memory with a stubbed _bulk_insert that captures the
    DataFrames, so no database is involved.
    """

    @staticmethod
    def _run_seed(count: int, seed: int, as_of: date) -> pl.DataFrame:
        cmd = Command()
        captured: list[pl.DataFrame] = []
        cmd._bulk_insert = lambda df: captured.append(df)
        cmd._seed(count, seed, as_of)
        return pl.concat(captured)

    def test_same_seed_count_asof_reproduces_rows(self):
        """Two runs with the same (seed, count, as-of) produce identical rows."""
        a = self._run_seed(300, 42, AS_OF)
        b = self._run_seed(300, 42, AS_OF)

        # person_id is an Object (UUID) column; compare it by value, the rest by frame equality
        assert a.drop("person_id").equals(b.drop("person_id"))
        assert a["person_id"].to_list() == b["person_id"].to_list()

    def test_person_ids_deterministic_and_seed_dependent(self):
        """Same seed → identical person_id set; different seed → different set."""
        a = self._run_seed(300, 42, AS_OF)
        b = self._run_seed(300, 42, AS_OF)
        c = self._run_seed(300, 7, AS_OF)

        pids_a = set(a["person_id"].to_list())
        pids_b = set(b["person_id"].to_list())
        pids_c = set(c["person_id"].to_list())

        assert pids_a == pids_b
        assert pids_a != pids_c

    def test_as_of_derives_dobs(self):
        """DOBs derive from the --as-of date: all DOBs <= as-of, and the same
        seed with a shifted as-of shifts every DOB by exactly the delta."""
        a = self._run_seed(200, 42, date(2026, 1, 1))
        b = self._run_seed(200, 42, date(2025, 1, 1))

        dobs_a = a["date_of_birth"].to_list()
        dobs_b = b["date_of_birth"].to_list()

        assert all(d <= date(2026, 1, 1) for d in dobs_a)
        assert all(d <= date(2025, 1, 1) for d in dobs_b)
        assert all((da - db).days == 365 for da, db in zip(dobs_a, dobs_b))


class TestEditDistance:
    """Test the _levenshtein helper used in sample case generation."""

    def test_identical_strings(self):
        assert Command._levenshtein("SMITH", "SMITH") == 0

    def test_single_substitution(self):
        assert Command._levenshtein("SMITH", "SMYTH") == 1

    def test_single_deletion(self):
        assert Command._levenshtein("SMITH", "SMIT") == 1

    def test_single_insertion(self):
        assert Command._levenshtein("SMIT", "SMITH") == 1

    def test_case_insensitive(self):
        assert Command._levenshtein("Smith", "SMITH") == 0
