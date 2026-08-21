"""Tests for seed_data management command generation logic.

Tests the vectorized name generation, Census/SSA-weighted independent name
sampling, cluster expansion, typo injection within clusters, person_id
consistency, batched bulk-insert, and (seed, count, as-of) reproducibility.
"""

from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np
import polars as pl
import pytest
from django.core.management.base import CommandError
from django.db import connection

from records.management.commands import seed_data
from records.management.commands.seed_data import Command
from records.models import CourtRecord

pytestmark = pytest.mark.django_db

# Fixed reference date so tests never depend on the wall clock.
AS_OF = date(2026, 1, 1)


@pytest.fixture()
def name_csv(tmp_path) -> Path:
    """A small synthetic name-frequency CSV in the Census/SSA format
    (forename,gender,count): two gender rows for one name, a lowercase name,
    a null name, and a name longer than the model's max_length=50."""
    path = tmp_path / "names.csv"
    path.write_text(f"forename,gender,count\nalice,M,1000\nalice,F,500\nBob,M,100\nCarol,F,50\n,M,7\n{'X' * 51},M,3\n")
    return path


def _sampled_frame(n: int, rng: np.random.Generator, csv_path: Path) -> pl.DataFrame:
    """Names sampled independently from a small synthetic CSV fixture —
    the same call sequence _seed uses per batch."""
    names, cdf = Command._load_name_weights(csv_path, "forename")
    firsts = Command._sample_names(names, cdf, n, rng)
    lasts = Command._sample_names(names, cdf, n, rng)
    return pl.DataFrame({"first_name": firsts, "last_name": lasts})


class ScriptedRng:
    """A stand-in for np.random.Generator with scripted choice()/integers() replies.

    Lets _inject_typo's branch selection (swap/drop/substitute) and its index
    draws be exercised deterministically instead of by chance. Exhausting a
    script raises StopIteration, so a test that asks for one more draw than the
    code path makes will fail loudly.
    """

    def __init__(self, choices, integers):
        self._choices = iter(choices)
        self._integers = iter(integers)
        self.integers_calls = []  # (low, high, value) per draw, in call order

    def choice(self, _options):
        return next(self._choices)

    def integers(self, low, high):
        value = next(self._integers)
        self.integers_calls.append((low, high, value))
        return value


class TestLoadNameWeights:
    """Test _load_name_weights normalizes a Census/SSA frequency CSV into
    (names, cdf) for weighted sampling."""

    def test_genders_are_aggregated(self, name_csv):
        """Gender rows for the same name are summed into one weight."""
        names, cdf = Command._load_name_weights(name_csv, "forename")

        # ALICE = 1000 (M) + 500 (F); the null and 51-char names are dropped
        assert set(names) == {"ALICE", "BOB", "CAROL"}
        assert cdf[0] == pytest.approx(1500 / 1650)
        assert cdf[1] == pytest.approx(1600 / 1650)
        assert cdf[-1] == 1.0

    def test_names_are_uppercased(self, name_csv):
        """Lowercase dataset entries are uppercased."""
        names, _ = Command._load_name_weights(name_csv, "forename")
        assert all(name == name.upper() for name in names)

    def test_sorted_by_count_descending(self, name_csv):
        """Names are ordered by count descending (ties by name) so the
        vocabulary ordering — and the sampled output for a given seed — is
        stable for a given data file."""
        names, _ = Command._load_name_weights(name_csv, "forename")
        assert names.tolist() == ["ALICE", "BOB", "CAROL"]

    def test_cdf_is_monotone_and_normalized(self, name_csv):
        """cdf is non-decreasing and ends at exactly 1.0."""
        _, cdf = Command._load_name_weights(name_csv, "forename")
        assert np.all(np.diff(cdf) >= 0)
        assert cdf[-1] == 1.0

    def test_null_and_long_names_dropped(self, name_csv):
        """Null names and names longer than CharField(max_length=50) are dropped."""
        names, _ = Command._load_name_weights(name_csv, "forename")
        assert all(0 < len(name) <= 50 for name in names)

    def test_missing_file_raises_command_error(self, tmp_path):
        """A missing data file raises a CommandError pointing at the download script."""
        with pytest.raises(CommandError, match="download_name_data"):
            Command._load_name_weights(tmp_path / "nope.csv", "forename")

    @pytest.mark.skipif(
        not (seed_data.FORENAMES_CSV.exists() and seed_data.SURNAMES_CSV.exists()),
        reason="local name data not downloaded; run name_dataset/download_name_data.py",
    )
    def test_local_dataset_files_load(self):
        """The locally downloaded (gitignored) CSVs load via pl.read_csv and
        are valid weight tables. Skipped when the data has not been downloaded."""
        for path, column in [
            (seed_data.FORENAMES_CSV, "forename"),
            (seed_data.SURNAMES_CSV, "surname"),
        ]:
            names, cdf = Command._load_name_weights(path, column)
            assert len(names) >= 100_000
            assert len(np.unique(names)) == len(names)
            assert cdf[-1] == 1.0
            assert all(0 < len(n) <= 50 for n in names[:1000])


class TestIndependentNameSampling:
    """Test _sample_names draws each name with probability proportional to
    its Census/SSA count."""

    @pytest.fixture()
    def vocab(self, tmp_path):
        path = tmp_path / "vocab.csv"
        path.write_text("forename,gender,count\nAlpha,M,9000\nBeta,M,1000\n")
        return Command._load_name_weights(path, "forename")

    def test_sample_size(self, vocab):
        """Sample size matches requested size."""
        names, cdf = vocab
        rng = np.random.default_rng(42)
        assert len(Command._sample_names(names, cdf, 500, rng)) == 500

    def test_samples_only_from_vocabulary(self, vocab):
        """Every sampled name exists in the source vocabulary."""
        names, cdf = vocab
        rng = np.random.default_rng(42)
        sampled = Command._sample_names(names, cdf, 5000, rng)
        assert set(sampled) <= set(names)

    def test_weighting_follows_counts(self, vocab):
        """A name with 9x the count of another is drawn ~9x more often.

        P(ALPHA) = 0.9, P(BETA) = 0.1: over 40k draws E[ALPHA] = 36000
        (sigma ~ 60), E[BETA] = 4000 (sigma ~ 60) — the margins below are
        ~50 sigma wide, so this only fails if the weighting is broken.
        """
        names, cdf = vocab
        rng = np.random.default_rng(42)
        sampled = Command._sample_names(names, cdf, 40_000, rng)
        freq = {name: int((sampled == name).sum()) for name in names}

        assert freq["ALPHA"] > 9 * freq["BETA"]
        assert 33_000 <= freq["ALPHA"] <= 39_000
        assert 3_400 <= freq["BETA"] <= 4_600

    def test_first_and_last_draws_are_independent(self, vocab):
        """The (first, last) joint frequency matches the product of the
        marginals — the two draws do not share a rank, so no pair
        concentrates at a Zipf top rank."""
        names, cdf = vocab
        rng = np.random.default_rng(42)
        firsts = Command._sample_names(names, cdf, 40_000, rng)
        lasts = Command._sample_names(names, cdf, 40_000, rng)

        observed = int(((firsts == "ALPHA") & (lasts == "BETA")).sum())
        expected = 40_000 * 0.9 * 0.1
        sigma = np.sqrt(40_000 * 0.09 * 0.91)
        assert abs(observed - expected) < 5 * sigma

    def test_reproducible_with_fixed_seed(self, vocab):
        """The same seed reproduces the same draw sequence; another seed does not."""
        names, cdf = vocab
        a = Command._sample_names(names, cdf, 10_000, np.random.default_rng(42))
        b = Command._sample_names(names, cdf, 10_000, np.random.default_rng(42))
        c = Command._sample_names(names, cdf, 10_000, np.random.default_rng(7))
        assert (a == b).all()
        assert not (a == c).all()


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


class TestTypoInjectionBranches:
    """P1-11: deterministic branch coverage for _inject_typo.

    A scripted fake RNG (ScriptedRng) pins the swap/drop/substitute branch
    selection and the index draws, so each branch's output shape is asserted
    exactly rather than by chance.
    """

    def test_swap_branch_exchanges_adjacent_pair(self):
        """swap at index 0 exchanges the first two characters; length and letters preserved."""
        rng = ScriptedRng(choices=["swap"], integers=[0])
        result = Command()._inject_typo("SMITH", rng)
        assert result == "MSITH"
        assert len(result) == len("SMITH")
        assert set(result) == set("SMITH")
        # The position draw is over [0, len(name) - 1).
        assert rng.integers_calls == [(0, 4, 0)]

    def test_swap_branch_two_char_name(self):
        """The shortest swappable name: 'AB' -> 'BA' (single possible swap position)."""
        rng = ScriptedRng(choices=["swap"], integers=[0])
        result = Command()._inject_typo("AB", rng)
        assert result == "BA"
        assert rng.integers_calls == [(0, 1, 0)]

    def test_drop_branch_removes_one_char(self):
        """drop at index 2 shortens the name by exactly one character."""
        rng = ScriptedRng(choices=["drop"], integers=[2])
        result = Command()._inject_typo("SMITH", rng)
        assert result == "SMTH"
        assert len(result) == len("SMITH") - 1
        assert rng.integers_calls == [(0, 5, 2)]  # position over [0, len(name))

    def test_drop_branch_two_char_name(self):
        """Dropping from a two-char name leaves one character."""
        rng = ScriptedRng(choices=["drop"], integers=[1])
        assert Command()._inject_typo("AB", rng) == "A"

    def test_substitute_branch_replaces_one_char(self):
        """substitute replaces exactly one character with the drawn uppercase letter."""
        rng = ScriptedRng(choices=["substitute"], integers=[1, 3])
        result = Command()._inject_typo("SMITH", rng)
        assert result == "SDITH"  # index 1 replaced by ascii_uppercase[3] == 'D'
        assert len(result) == len("SMITH")
        # Two draws: the position in [0, len), then the letter in [0, 26).
        assert rng.integers_calls == [(0, 5, 1), (0, 26, 3)]

    def test_no_op_attempt_retries_until_changed(self):
        """A substitute that draws the same character leaves the name unchanged and
        the retry loop picks a fresh branch; the drop then succeeds."""
        rng = ScriptedRng(
            choices=["substitute", "drop"],
            integers=[1, 12, 0],  # substitute 'M' at index 1 is a no-op; drop index 0
        )
        result = Command()._inject_typo("SMITH", rng)
        assert result == "MITH"
        assert len(result) == 4

    def test_single_char_name_unchanged(self):
        """len(name) < 2 returns early without drawing anything from the RNG."""
        rng = ScriptedRng(choices=[], integers=[])  # would raise StopIteration if called
        assert Command()._inject_typo("A", rng) == "A"

    def test_all_same_chars_swap_falls_back(self):
        """Swapping identical characters can never change the name: after all 10
        retry attempts fail, the deterministic fallback changes the last character."""
        rng = ScriptedRng(choices=["swap"] * 10, integers=[0] * 10)
        result = Command()._inject_typo("AAA", rng)
        assert result == "AAB"  # 'AA' + next letter after 'A'
        assert len(result) == 3

    def test_all_same_chars_lowercase_drop(self):
        """All-same-chars with drop on lowercase input: drop always changes the name."""
        rng = ScriptedRng(choices=["drop"], integers=[1])
        assert Command()._inject_typo("aaa", rng) == "aa"


class TestClusterExpansion:
    """Test _expand_clusters produces correct cluster structures."""

    def test_expansion_respects_cluster_sizes(self, name_csv):
        """Expanded rows count matches sum of cluster sizes."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        fake.seed_instance(42)
        rng = np.random.default_rng(42)

        sampled = _sampled_frame(100, rng, name_csv)
        identities = cmd._assign_attributes(sampled, 100, rng, fake, AS_OF)
        expanded = cmd._expand_clusters(identities, rng)

        expected_total = int(identities["cluster_size"].sum())
        assert len(expanded) == expected_total

    def test_cluster_records_share_person_id_and_dob(self, name_csv):
        """All records from the same identity share person_id and DOB."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        fake.seed_instance(42)
        rng = np.random.default_rng(42)

        sampled = _sampled_frame(100, rng, name_csv)
        identities = cmd._assign_attributes(sampled, 100, rng, fake, AS_OF)
        expanded = cmd._expand_clusters(identities, rng)

        # Group by person_id string and verify all share the same DOB
        pid_groups = {}
        for row in expanded.iter_rows(named=True):
            pid_str = str(row["person_id"])
            pid_groups.setdefault(pid_str, set()).add(row["date_of_birth"])
        for pid_str, dobs in pid_groups.items():
            assert len(dobs) == 1, f"Cluster {pid_str} has multiple DOBs: {dobs}"

    def test_expanded_records_have_person_id(self, name_csv):
        """All expanded records have a valid person_id."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        fake.seed_instance(42)
        rng = np.random.default_rng(42)

        sampled = _sampled_frame(100, rng, name_csv)
        identities = cmd._assign_attributes(sampled, 100, rng, fake, AS_OF)
        expanded = cmd._expand_clusters(identities, rng)

        for row in expanded.iter_rows(named=True):
            assert isinstance(row["person_id"], UUID)


class TestBulkInsert:
    """Test _bulk_insert creates records correctly."""

    def test_bulk_insert_creates_records(self, name_csv):
        """Bulk insert creates the expected number of records."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        fake.seed_instance(42)
        rng = np.random.default_rng(42)

        sampled = _sampled_frame(200, rng, name_csv)
        identities = cmd._assign_attributes(sampled, 200, rng, fake, AS_OF)
        expanded = cmd._expand_clusters(identities, rng)

        assert CourtRecord.objects.count() == 0
        cmd._bulk_insert(expanded)
        assert CourtRecord.objects.count() == len(expanded)

    def test_bulk_insert_dob_not_null(self, name_csv):
        """All inserted records have a non-NULL date_of_birth."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        fake.seed_instance(42)
        rng = np.random.default_rng(42)

        sampled = _sampled_frame(200, rng, name_csv)
        identities = cmd._assign_attributes(sampled, 200, rng, fake, AS_OF)
        expanded = cmd._expand_clusters(identities, rng)

        cmd._bulk_insert(expanded)
        null_count = CourtRecord.objects.filter(date_of_birth__isnull=True).count()
        assert null_count == 0

    def test_bulk_insert_person_id_not_null(self, name_csv):
        """All inserted records have a non-NULL person_id."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        fake.seed_instance(42)
        rng = np.random.default_rng(42)

        sampled = _sampled_frame(200, rng, name_csv)
        identities = cmd._assign_attributes(sampled, 200, rng, fake, AS_OF)
        expanded = cmd._expand_clusters(identities, rng)

        cmd._bulk_insert(expanded)
        null_count = CourtRecord.objects.filter(person_id__isnull=True).count()
        assert null_count == 0

    def test_bulk_insert_nickname_records(self, name_csv):
        """Some records have nicknames populated."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        fake.seed_instance(42)
        rng = np.random.default_rng(42)

        sampled = _sampled_frame(1000, rng, name_csv)
        identities = cmd._assign_attributes(sampled, 1000, rng, fake, AS_OF)
        expanded = cmd._expand_clusters(identities, rng)

        cmd._bulk_insert(expanded)
        with_nicknames = CourtRecord.objects.filter(nicknames__len__gt=0).count()
        assert with_nicknames > 0

    def test_bulk_insert_middle_name_records(self, name_csv):
        """Most records have middle names (90% rate)."""
        cmd = Command()
        fake = pytest.importorskip("faker").Faker("en_US")
        fake.seed_instance(42)
        rng = np.random.default_rng(42)

        sampled = _sampled_frame(1000, rng, name_csv)
        identities = cmd._assign_attributes(sampled, 1000, rng, fake, AS_OF)
        expanded = cmd._expand_clusters(identities, rng)

        cmd._bulk_insert(expanded)
        with_middle = CourtRecord.objects.exclude(middle_name__isnull=True).count()
        assert with_middle > len(expanded) * 0.8  # Allow some variance

    @staticmethod
    def _generated_frame(n: int) -> pl.DataFrame:
        """A generation-shaped DataFrame: the same columns _expand_clusters outputs
        (datetime64 DOBs, object UUIDs, list-of-str nicknames, nullable middles)."""
        return pl.DataFrame(
            {
                "first_name": [f"First{i}" for i in range(n)],
                "last_name": [f"Last{i % 500}" for i in range(n)],
                "middle_name": [None if i % 2 else "M" for i in range(n)],
                "date_of_birth": np.full(n, np.datetime64("1990-01-01"), dtype="datetime64[D]"),
                "person_id": [uuid4() for _ in range(n)],
                "nicknames": [["Nick"] if i % 1000 == 0 else [] for i in range(n)],
            }
        )

    @staticmethod
    def _spy_bulk_create(monkeypatch) -> list:
        """Record the row count of every bulk_create call the insert performs."""
        calls: list[int] = []
        original = CourtRecord.objects.bulk_create

        def spy(objs, *args, **kwargs):
            calls.append(len(objs))
            return original(objs, *args, **kwargs)

        monkeypatch.setattr(CourtRecord.objects, "bulk_create", spy)
        return calls

    def test_bulk_insert_20k_rows_single_chunk(self, monkeypatch):
        """P1-11: 20,000 rows — below the 100K BATCH_SIZE, so one chunk — all land
        in the table via a single bulk_create call, with the data intact."""
        cmd = Command()
        df = self._generated_frame(20_000)
        calls = self._spy_bulk_create(monkeypatch)

        cmd._bulk_insert(df)

        assert calls == [20_000]
        assert CourtRecord.objects.count() == 20_000
        # Spot-check the round trip (including the datetime64 -> date conversion).
        row = CourtRecord.objects.get(first_name="First1235")
        assert row.last_name == "Last235"  # 1235 % 500
        assert row.date_of_birth == date(1990, 1, 1)
        assert row.middle_name is None  # odd index
        assert CourtRecord.objects.get(first_name="First0").middle_name == "M"  # even index
        assert CourtRecord.objects.get(first_name="First0").nicknames == ["Nick"]
        assert row.person_id is not None

    def test_bulk_insert_multi_chunk_with_small_batch_size(self, monkeypatch):
        """P1-11: with BATCH_SIZE shrunk to 5000, 12,000 rows exercise the
        chunk loop: three bulk_create calls of 5000 + 5000 + 2000."""
        cmd = Command()
        df = self._generated_frame(12_000)
        monkeypatch.setattr("records.management.commands.seed_data.BATCH_SIZE", 5000)
        calls = self._spy_bulk_create(monkeypatch)

        cmd._bulk_insert(df)

        assert calls == [5000, 5000, 2000]
        assert CourtRecord.objects.count() == 12_000


class TestBatchSizing:
    """B10: the per-batch identity count is floored at 1000 and capped at 2M,
    so memory is bounded to one batch at any --count."""

    def test_first_batch_at_54m_is_capped_at_two_million(self):
        """remaining=54M would sample 36M identities uncapped — the cap binds."""
        assert Command._batch_identities(54_000_000) == 2_000_000

    def test_cap_is_exact_boundary(self):
        """Exactly 2M identities (3M rows at avg cluster 1.5) passes the cap."""
        assert Command._batch_identities(3_000_000) == 2_000_000

    def test_uncapped_size_when_below_cap(self):
        """Small remaining counts keep the old (remaining / avg cluster size) sizing."""
        assert Command._batch_identities(15_000) == 10_000

    def test_floor_of_one_thousand(self):
        """Tiny remaining counts floor the batch at 1000 identities."""
        assert Command._batch_identities(1) == 1000
        assert Command._batch_identities(1_500) == 1000


class TestFlush:
    """B16: --flush uses TRUNCATE ... RESTART IDENTITY, not DELETE."""

    def test_flush_empties_table_and_restarts_sequence(self):
        """After a flush the table is empty and the id sequence restarts at 1."""
        first = CourtRecord.objects.create(first_name="John", last_name="Smith", date_of_birth="1990-01-01")
        second = CourtRecord.objects.create(first_name="Jane", last_name="Doe", date_of_birth="1985-06-20")
        assert second.id == first.id + 1

        Command._flush()

        assert CourtRecord.objects.count() == 0
        with connection.cursor() as cursor:
            # The id sequence keeps its pre-rename name (Django's RenameModel
            # renames the table, not the owned sequence), so it is still
            # records_person_id_seq even though the table is records_courtrecord.
            cursor.execute("SELECT last_value FROM records_person_id_seq")
            assert cursor.fetchone()[0] == 1
        # A fresh insert gets id 1 again (with DELETE the sequence would keep
        # advancing past first.id + 1).
        new = CourtRecord.objects.create(first_name="Ann", last_name="Alfa", date_of_birth="1990-02-02")
        assert new.id == 1

    def test_flush_on_empty_table_is_a_noop(self):
        """Flushing an empty table does not error."""
        assert CourtRecord.objects.count() == 0
        Command._flush()
        assert CourtRecord.objects.count() == 0


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
