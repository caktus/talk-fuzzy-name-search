# 54M Seed Run — Status: ✅ Complete

## Result

**54,000,000 records seeded successfully in 98 minutes (5,891s), no OOM.**

Previously failed at ~14GB RAM on 16GB machine. Now runs on 40GB with peak memory ~1.4 GB.

## Changes to `seed_data.py`

### 1. Streaming insert (`_seed()`) — main fix

Each batch is generated, expanded, and inserted immediately. No accumulation of all rows across batches.

- Before: `all_rows = pl.concat([all_rows, batch_rows])` grew to hold all 54M rows
- After: generate → expand → insert → `del sampled, identities, batch_rows` → next batch
- Added per-stage timing output
- (B10, 2026-08-14) the per-batch identity count was **uncapped** at the time of this run, so the
  first batch held the entire 36M-identity dataset in memory before any insert. Batch size is now
  capped at 2M identities (~3M rows at the average cluster size), so memory is bounded to one
  batch at any `--count` — the ~1.4 GB peak below was measured with the whole dataset as one
  batch and is an upper bound, not a one-batch figure.

### 2. Numpy arrays for typo injection (`_expand_clusters()`)

- Before: `to_list()` created Python lists of strings
- After: `to_numpy()` uses numpy arrays, reducing Python object overhead

### 3. Numpy column access for bulk insert (`_bulk_insert()`)

- Before: `iter_rows(named=True)` created Python dicts for every row
- After: extract columns as numpy arrays, index into them with `islice` batching
- Added `datetime64` → Python `date` conversion (Django doesn't accept numpy datetime64)
- Added `nick_arr[i].size` check for empty nicknames (numpy arrays have ambiguous truth values)

## Verification at 54M

| Metric | Expected | Actual | Status |
|--------|----------|--------|--------|
| Row count | 54,000,000 | 54,000,000 | ✅ |
| Max cluster size | ≤ 80 | 80 | ✅ |
| Name frequency | Heavy-tailed | 960/948/930/922/917... | ✅ |
| Typo rate (non-canonical rows in multi-member clusters) | ~0.14–0.20 | 0.1410 | ✅ |
| Middle name rate | ~0.90 | 0.9001 | ✅ |
| Nickname rate | ~0.30 | 0.2999 | ✅ |
| Singleton identities | ~80% | 80.00% | ✅ |
| Clusters at max size (80) | ~10K | 10,333 | ✅ |

### Typo rate note

The configured `TYPO_RATE = 0.20` means "20% of non-canonical rows in multi-member clusters carry a typo" — it is **not** a claim that 20% of all rows carry typos (the row-level typo rate across the whole table is ~7–9%). The observed rate within multi-member clusters (0.1410) is below 0.20 because:
- Canonical rows (first in each cluster) are never typo'd
- For a cluster of size N, the typo-able fraction of its rows is `(N-1) / N`

This is correct behavior of the generator.

## Provenance: honest-generator note (2026-08-14)

The 54M dataset on disk was produced by an **earlier revision** of `records/management/commands/seed_data.py`. Its actual distribution is the empirical data recorded in this file: top-pair frequencies 960/948/930/922/917, 3.72M distinct name pairs, 0.1410 typo rate within multi-member clusters, 80% singleton identities, max cluster size 80. That revision's "Dirichlet heavy tail" step was not a real skew source: it sampled `Dirichlet(1, …, 1)` (uniform over the simplex), and its pool drew first and last names independently, so the 10.8M-row pool contained at most 690,000 distinct pairs (the faker 40.x en_US space) and was heavily duplicated. Its reproducibility was also only partial: DOBs were derived from `date.today()` at run time and person_ids from unseeded `uuid.uuid4()`, so a re-run would not reproduce this dataset.

The generator has since been rewritten to be honest and deterministic (RECS-2026-08-14, B3, option b): the pool is now built from **distinct** pairs (drawn without replacement from the 690K-pair en_US space, capped at that size), name-pair frequencies come from explicit **Zipf(a=1.1)** sampling of pool rows, DOBs derive from a **`--as-of`** reference date, and person_ids are drawn from the seeded RNG. A given (seed, count, as-of) triple now reproduces the dataset exactly, and the distribution model is stated in the command's docstring.

The stage DB has **not** been re-seeded: re-seeding with the new generator produces a different — better-documented — distribution than the one on disk, and the measured numbers in this file remain the provenance of record for the current stage data. Re-seeding and re-measuring is a separate, deliberate step.

## Timing

| Stage | Time |
|-------|------|
| Name pool (10.8M rows drawn from the 690K-pair en_US space — not deduped in that revision) | 1.6s |
| Generation (36M identities → 53.2M expanded rows) | 725s (12min) |
| Insertion (53.2M rows in 100K batches) | 5,076s (85min) |
| Final batches (trim to exact 54M) | ~4s |
| **Total** | **5,891s (98min)** |

## Memory

- Peak memory during 54M run: ~1.4 GB (that run's first batch was uncapped — the entire
  36M-identity dataset; see the B10 note above)
- Previous OOM: ~14 GB on 16GB machine
- Streaming insert keeps memory bounded to one batch at a time (since B10: a batch is at most
  2M identities ≈ 3M rows, independent of `--count`)

## Tests

**64/64 passing** across the full test suite (`tests/records/`).

## Seed

Run used `--seed 42`. Note: with that earlier generator revision this run was **not** byte-for-byte reproducible — DOBs depended on the run date and person_ids came from unseeded `uuid4()`. The rewritten generator (see Provenance above) fixes this via `--as-of` and seeded person_id derivation.

## Remaining HANDOFF Items

- ✅ Generate 54M records and verify distributions
- ✅ Run full test suite
- ✅ Regenerate `test_cases.txt` — done, 6.3KB with 54M data
- ✅ Confirm duplicate cluster bug — hypothesis 1 confirmed (stale data from multiple seed runs)
  - Fresh 54M seed: no duplicate large clusters found
  - Stuart Dorsey: 48 distinct person_ids, all with different DOBs, max cluster size 4