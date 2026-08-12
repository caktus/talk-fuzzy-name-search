"""Tests for seed_data management command generation logic.

Tests the vectorized name generation, Dirichlet sampling, cluster expansion,
typo injection within clusters, person_id consistency, and batched bulk-insert.
"""

from uuid import UUID

import numpy as np
import polars as pl
import pytest

from records.management.commands.seed_data import Command
from records.models import Person

pytestmark = pytest.mark.django_db


class TestNamePoolGeneration:
    """Test _build_name_pool generates valid name combinations."""

    def test_pool_size(self):
        """Pool size matches requested size."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        rng = np.random.default_rng(42)
        pool = cmd._build_name_pool(500, fake, rng)

        assert len(pool) == 500
        assert "first_name" in pool.columns
        assert "last_name" in pool.columns

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


class TestDirichletSampling:
    """Test _dirichlet_sample produces realistic name frequency distribution."""

    def test_sample_size(self):
        """Sample size matches requested size."""
        cmd = Command()
        pool = pl.DataFrame({
            "first_name": [f"FIRST{i}" for i in range(100)],
            "last_name": [f"LAST{i}" for i in range(100)],
        })
        rng = np.random.default_rng(42)
        sampled = cmd._dirichlet_sample(pool, 500, rng)

        assert len(sampled) == 500

    def test_heavy_tailed_distribution(self):
        """Dirichlet sampling produces heavy-tailed distribution."""
        cmd = Command()
        pool = pl.DataFrame({
            "first_name": [f"FIRST{i}" for i in range(1000)],
            "last_name": [f"LAST{i}" for i in range(1000)],
        })
        rng = np.random.default_rng(42)
        sampled = cmd._dirichlet_sample(pool, 10000, rng)

        name_counts = (
            sampled.group_by(["first_name", "last_name"])
            .agg(pl.len().alias("count"))
            .sort("count", descending=True)
        )
        max_count = name_counts["count"][0]
        mean_count = name_counts["count"].mean()

        assert max_count > mean_count * 2

    def test_sampled_names_from_pool(self):
        """All sampled names exist in the original pool."""
        cmd = Command()
        pool = pl.DataFrame({
            "first_name": [f"FIRST{i}" for i in range(100)],
            "last_name": [f"LAST{i}" for i in range(100)],
        })
        rng = np.random.default_rng(42)
        sampled = cmd._dirichlet_sample(pool, 500, rng)

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
        sampled = cmd._dirichlet_sample(pool, 100, rng)
        identities = cmd._assign_attributes(sampled, 100, rng, fake)
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
        sampled = cmd._dirichlet_sample(pool, 100, rng)
        identities = cmd._assign_attributes(sampled, 100, rng, fake)
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
        sampled = cmd._dirichlet_sample(pool, 100, rng)
        identities = cmd._assign_attributes(sampled, 100, rng, fake)
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
        sampled = cmd._dirichlet_sample(pool, 200, rng)
        identities = cmd._assign_attributes(sampled, 200, rng, fake)
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
        sampled = cmd._dirichlet_sample(pool, 200, rng)
        identities = cmd._assign_attributes(sampled, 200, rng, fake)
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
        sampled = cmd._dirichlet_sample(pool, 200, rng)
        identities = cmd._assign_attributes(sampled, 200, rng, fake)
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
        sampled = cmd._dirichlet_sample(pool, 1000, rng)
        identities = cmd._assign_attributes(sampled, 1000, rng, fake)
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
        sampled = cmd._dirichlet_sample(pool, 1000, rng)
        identities = cmd._assign_attributes(sampled, 1000, rng, fake)
        expanded = cmd._expand_clusters(identities, rng)

        cmd._bulk_insert(expanded)
        with_middle = Person.objects.exclude(middle_name__isnull=True).count()
        assert with_middle > len(expanded) * 0.8  # Allow some variance


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
