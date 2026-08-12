# 54M Seed Run — Status: ✅ Complete

## Result

**54,000,000 records seeded successfully in 98 minutes (5,891s), no OOM.**

Previously failed at ~14GB RAM on 16GB machine. Now runs on 40GB with peak memory ~1.4 GB.

## Changes to `seed_data.py`

### 1. Streaming insert (`_seed()`) — main fix

Each batch is generated, expanded, and inserted immediately. No accumulation of all rows in memory.

- Before: `all_rows = pl.concat([all_rows, batch_rows])` grew to hold all 54M rows
- After: generate → expand → insert → `del sampled, identities, batch_rows` → next batch
- Added per-stage timing output

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
| Name frequency | Heavy-tailed (Dirichlet) | 960/948/930/922/917... | ✅ |
| Typo rate (multi-member clusters) | ~0.14–0.20 | 0.1410 | ✅ |
| Middle name rate | ~0.90 | 0.9001 | ✅ |
| Nickname rate | ~0.30 | 0.2999 | ✅ |
| Singleton identities | ~80% | 80.00% | ✅ |
| Clusters at max size (80) | ~10K | 10,333 | ✅ |

### Typo rate note

The observed typo rate (0.1410) is lower than the configured `TYPO_RATE = 0.20` because:
- Canonical rows (first in each cluster) are never typo'd
- 80% of identities are singletons (cluster_size=1), which have zero typos
- For a cluster of size N, effective rate = `TYPO_RATE × (N-1) / N`

This is correct behavior. The HANDOFF.md expected ~0.20 as a rough check.

## Timing

| Stage | Time |
|-------|------|
| Name pool (10.8M unique names) | 1.6s |
| Generation (36M identities → 53.2M expanded rows) | 725s (12min) |
| Insertion (53.2M rows in 100K batches) | 5,076s (85min) |
| Final batches (trim to exact 54M) | ~4s |
| **Total** | **5,891s (98min)** |

## Memory

- Peak memory during 54M run: ~1.4 GB
- Previous OOM: ~14 GB on 16GB machine
- Streaming insert keeps memory bounded to one batch at a time

## Tests

**64/64 passing** across the full test suite (`tests/records/`).

## Seed

Run used `--seed 42` for reproducibility.

## Remaining HANDOFF Items

- ✅ Generate 54M records and verify distributions
- ✅ Run full test suite
- ✅ Regenerate `test_cases.txt` — done, 6.3KB with 54M data
- ✅ Confirm duplicate cluster bug — hypothesis 1 confirmed (stale data from multiple seed runs)
  - Fresh 54M seed: no duplicate large clusters found
  - Stuart Dorsey: 48 distinct person_ids, all with different DOBs, max cluster size 4