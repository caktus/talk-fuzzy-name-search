# Legacy data pipeline (pre-rewrite)

These are the original CSV-based data pipeline: `generate_data.py` generated
a 50M-row CSV and `load_csv.py` loaded it into PostgreSQL. They are
superseded by `manage.py seed_data` and kept for reference only — they are
NOT part of the current onboarding path (see the README Quick Start).

Do not run them against the current schema: `generate_data.py` emits empty
`dob` values for ~20% of rows, which `load_csv.py` loads as `NULL` and the
`NOT NULL` constraint (migration 0005) rejects with an `IntegrityError`. They
also produce no `person_id` clusters.
