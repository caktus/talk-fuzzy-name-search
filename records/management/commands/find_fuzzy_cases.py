"""Find demo names where phonetic search alone is noisy but phonetic + Levenshtein is exact.

Scans the database for canonical name spellings that exhibit the full story
the fuzzy-search demo tells:

1. The canonical (first_name, last_name) spelling occurs 10-20 times, and
   every one of those rows belongs to the SAME person_id.
2. That person's cluster (all rows with that person_id) contains 3-5 typo
   variants that still match the canonical name via SOUNDEX and/or
   DAITCH_MOKOTOFF codes.
3. A bare phonetic search (soundex and/or DM, no precision filter) returns
   far more rows than the person's cluster -- i.e. too many false positives.
4. Adding the Levenshtein precision filter (edit distance <= 2 per name, the
   same predicate search_unified() applies) removes every false positive so
   the phonetic + Levenshtein result set is EXACTLY the set of rows you see
   when filtering by person_id (or by the person's date_of_birth).

Why a management command instead of one big SQL script:
Exactness (criterion 4) is a per-name set-equality check: each candidate
needs its own indexed soundex/DM lookup (false positives, false negatives)
that cannot be expressed as a single join over the whole table, and we want
seedable random sampling with an early stop after 3-5 hits. So three
CTE-organized SQL statements do all the actual matching, and Python
orchestrates a staged, parallel scan:

  Stage 1  CANDIDATES_SQL          one pass over the table -> single-person
                                   spellings occurring 10-20 times (~60k).
  Stage 2  CLUSTER_CHECK_SQL       one batched join of candidates to their
                                   clusters -> per-candidate typo counts and
                                   which typos survive the phonetic codes.
                                   Cheap prefilter: most candidates fail here
                                   without ever running a phonetic scan.
  Stage 3  EVALUATE_SQL            per surviving candidate: the bare-phonetic
                                   result set, the phonetic+Levenshtein set,
                                   and both directions of set difference vs
                                   the cluster. Runs in worker threads.

Usage:
    python manage.py find_fuzzy_cases                     # 5 matches, seed 42
    python manage.py find_fuzzy_cases --limit 3 --seed 7  # 3 matches, different shuffle
    python manage.py find_fuzzy_cases --min-false-positives 50
    python manage.py find_fuzzy_cases --allow-missed      # relax exactness (see below)
    python manage.py find_fuzzy_cases --lev-misses 2      # search must miss 2+ real rows
    python manage.py find_fuzzy_cases --print-sql > demo.sql

Exactness modes:
    default (strict):  phonetic + Levenshtein result set == person's cluster,
                       row for row. Every typo must survive the phonetic
                       match (the common case, but the tightest constraint).
    --allow-missed:    a cluster typo whose spelling breaks BOTH soundex and
                       DM is allowed to be missed by the search; the 3-5-typo
                       criterion then applies to the typos that do match
                       phonetically. No false positives are ever allowed.
    --lev-misses N:    the false-negative demo. Instead of demanding an exact
                       result set, the search must MISS at least N of the
                       person's own rows; each missed row is reported with its
                       cause ('levenshtein: edit distance > 2', or 'broken
                       phonetic codes'). No false positives are allowed, and
                       the 3-5 phonetic typos / min-false-positives criteria
                       still apply. Note: the seeded data injects at most one
                       edit per row (a transposition is 2 in Levenshtein
                       terms), so genuinely levenshtein-caused misses only
                       appear in data with multi-edit typos.
"""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand
from django.db import connection

# ---------------------------------------------------------------------------
# Stage 1: candidate canonical spellings.
# One pass over the table groups every distinct (first, last) spelling, keeps
# those occurring 10-20 times, and requires that every occurrence shares one
# person_id so the "ground truth" cluster is unambiguous.
# ---------------------------------------------------------------------------
CANDIDATES_SQL = """
WITH canonical_spellings AS MATERIALIZED (
    SELECT first_name,
           last_name,
           count(*)                    AS canonical_n,
           (array_agg(person_id))[1]   AS person_id
    FROM records_courtrecord
    GROUP BY first_name, last_name
    HAVING count(*) BETWEEN %(min_canonical)s AND %(max_canonical)s
       AND count(DISTINCT person_id) = 1
)
SELECT first_name, last_name, canonical_n, person_id
FROM canonical_spellings
"""

# ---------------------------------------------------------------------------
# Stage 2: batched cluster check. The candidate list is inlined as a VALUES
# CTE and joined to the clusters on person_id (indexed). A per-row CTE flags
# each cluster row once, so for every candidate we count: total cluster rows,
# typo rows (spelling != canonical), the typos that still match via soundex
# and/or daitch-mokotoff, and the rows the final search will MISS (typo rows
# whose codes both broke, or whose edit distance exceeds the Levenshtein
# cutoff). Candidates that cannot satisfy the typo/miss criteria are dropped
# before any phonetic scan runs.
# ---------------------------------------------------------------------------
CLUSTER_CHECK_SQL = """
WITH candidates(first_name, last_name, person_id) AS MATERIALIZED (
    VALUES {values}
),
cluster_rows AS MATERIALIZED (
    SELECT c.person_id,
           (r.first_name <> c.first_name OR r.last_name <> c.last_name)    AS is_typo,
           (SOUNDEX(UPPER(r.first_name)) = SOUNDEX(c.first_name)
              AND SOUNDEX(UPPER(r.last_name)) = SOUNDEX(c.last_name))      AS soundex_match,
           (DAITCH_MOKOTOFF(UPPER(r.first_name)) && DAITCH_MOKOTOFF(c.first_name)
              AND DAITCH_MOKOTOFF(UPPER(r.last_name)) && DAITCH_MOKOTOFF(c.last_name))
                                                                           AS dm_match,
           levenshtein(UPPER(r.first_name), UPPER(c.first_name))          AS fn_distance,
           levenshtein(UPPER(r.last_name), UPPER(c.last_name))            AS ln_distance
    FROM candidates c
    JOIN records_courtrecord r ON r.person_id = c.person_id
)
SELECT person_id,
       count(*)                                                    AS cluster_n,
       count(*) FILTER (WHERE is_typo)                             AS typo_n,
       count(*) FILTER (WHERE is_typo AND (soundex_match OR dm_match))
                                                                    AS phonetic_typo_n,
       count(*) FILTER (
           WHERE is_typo
             AND (NOT (soundex_match OR dm_match) OR fn_distance > 2 OR ln_distance > 2)
       )                                                           AS missed_n
FROM cluster_rows
GROUP BY person_id
"""

# ---------------------------------------------------------------------------
# Stage 3: per-candidate evaluation. CTE mirrors of each search stage:
#   cluster   - ground truth: exactly what a person_id (or DOB) filter shows
#   phonetic  - bare soundex and/or DM search, no precision filter
#   phon_lev  - phonetic + the Levenshtein <= 2 refinement per name
#   false_*   - the two directions of set difference, for the exactness check
# ---------------------------------------------------------------------------
EVALUATE_SQL = """
WITH cluster AS MATERIALIZED (
    SELECT id, first_name, last_name
    FROM records_courtrecord
    WHERE person_id = %(person_id)s
),
phonetic AS MATERIALIZED (
    SELECT id, first_name, last_name
    FROM records_courtrecord
    WHERE (SOUNDEX(UPPER(first_name)) = SOUNDEX(%(first_name)s)
           AND SOUNDEX(UPPER(last_name)) = SOUNDEX(%(last_name)s))
       OR (DAITCH_MOKOTOFF(UPPER(first_name)) && DAITCH_MOKOTOFF(%(first_name)s)
           AND DAITCH_MOKOTOFF(UPPER(last_name)) && DAITCH_MOKOTOFF(%(last_name)s))
),
phon_lev AS MATERIALIZED (
    SELECT id, first_name, last_name
    FROM phonetic
    WHERE levenshtein_less_equal(UPPER(first_name), %(first_name)s, 2) <= 2
      AND levenshtein_less_equal(UPPER(last_name), %(last_name)s, 2) <= 2
),
false_positives AS MATERIALIZED (
    -- rows the search returns that are NOT this person
    SELECT p.id
    FROM phon_lev p
    WHERE NOT EXISTS (SELECT 1 FROM cluster c WHERE c.id = p.id)
),
false_negatives AS MATERIALIZED (
    -- cluster rows the search misses (typos that broke both phonetic codes)
    SELECT c.id
    FROM cluster c
    WHERE NOT EXISTS (SELECT 1 FROM phon_lev p WHERE p.id = c.id)
)
SELECT
    (SELECT count(*) FROM cluster)            AS cluster_n,
    (SELECT count(*) FROM phonetic)           AS phonetic_n,
    (SELECT count(*) FROM phon_lev)           AS phon_lev_n,
    (SELECT count(*) FROM false_positives)    AS false_positive_n,
    (SELECT count(*) FROM false_negatives)    AS false_negative_n
"""

# Detail pass (only for accepted candidates): per spelling in the cluster,
# which phonetic code(s) it matches through and its Levenshtein distance.
DETAIL_SQL = """
WITH cluster AS MATERIALIZED (
    SELECT id, first_name, last_name, date_of_birth
    FROM records_courtrecord
    WHERE person_id = %(person_id)s
)
SELECT first_name,
       last_name,
       count(*)            AS rows,
       min(date_of_birth)  AS date_of_birth,
       bool_or(SOUNDEX(UPPER(first_name)) = SOUNDEX(%(first_name)s)
               AND SOUNDEX(UPPER(last_name)) = SOUNDEX(%(last_name)s))      AS soundex_match,
       bool_or(DAITCH_MOKOTOFF(UPPER(first_name)) && DAITCH_MOKOTOFF(%(first_name)s)
               AND DAITCH_MOKOTOFF(UPPER(last_name)) && DAITCH_MOKOTOFF(%(last_name)s))
                                                                          AS dm_match,
       max(levenshtein(UPPER(first_name), %(first_name)s))                AS fn_distance,
       max(levenshtein(UPPER(last_name), %(last_name)s))                  AS ln_distance
FROM cluster
GROUP BY first_name, last_name
ORDER BY first_name, last_name
"""


class Command(BaseCommand):
    help = (
        "Find 3-5 random names where the canonical spelling occurs 10-20 times, the person's "
        "cluster has 3-5 typos matching soundex and/or daitch-mokotoff, bare phonetic search "
        "returns too many false positives, and phonetic + Levenshtein(<=2) recovers exactly "
        "the rows you'd see filtering by person_id/DOB."
    )

    CLUSTER_CHECK_CHUNK = 5000  # candidates per batched stage-2 query

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=5, help="Matches to return, 1-5 (default 5)")
        parser.add_argument(
            "--seed", type=int, default=42, help="RNG seed for the random candidate shuffle (default 42)"
        )
        parser.add_argument(
            "--min-canonical", type=int, default=10, help="Min occurrences of the canonical spelling (default 10)"
        )
        parser.add_argument(
            "--max-canonical", type=int, default=20, help="Max occurrences of the canonical spelling (default 20)"
        )
        parser.add_argument(
            "--min-typos", type=int, default=3, help="Min phonetically-matching typos in the cluster (default 3)"
        )
        parser.add_argument(
            "--max-typos", type=int, default=5, help="Max phonetically-matching typos in the cluster (default 5)"
        )
        parser.add_argument(
            "--min-false-positives",
            type=int,
            default=20,
            help="Min extra rows bare phonetic search must return beyond the cluster (default 20)",
        )
        parser.add_argument(
            "--max-candidates",
            type=int,
            default=2000,
            help="Cap on stage-3 (phonetic scan) evaluations, even if fewer than --limit match (default 2000)",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=6,
            help="Parallel stage-3 evaluation threads (default 6)",
        )
        parser.add_argument(
            "--allow-missed",
            action="store_true",
            help=(
                "Relaxed exactness: typos breaking both phonetic codes may be missed "
                "(no false positives still required)"
            ),
        )
        parser.add_argument(
            "--lev-misses",
            type=int,
            default=0,
            help=(
                "False-negative demo: require the final phonetic + Levenshtein search to MISS "
                "at least N of the person's own rows (0 = off). Each missed row is labeled "
                "with its cause: 'levenshtein' when the typo is more than 2 edits from the "
                "canonical spelling, 'phonetic codes' when the typo broke both soundex and DM."
            ),
        )
        parser.add_argument(
            "--print-sql",
            action="store_true",
            help="Also print a standalone psql script reproducing each match's result sets",
        )
        parser.add_argument(
            "--progress",
            type=int,
            default=200,
            help="Log a progress line every N stage-3 evaluations (0 = off, default 200)",
        )

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------

    def handle(self, *args, **options):
        limit = min(max(options["limit"], 1), 5)

        # Stage 1: candidate spellings.
        self.stdout.write("Stage 1: finding single-person spellings (one pass over the table)...")
        with connection.cursor() as cursor:
            cursor.execute(
                CANDIDATES_SQL, {"min_canonical": options["min_canonical"], "max_canonical": options["max_canonical"]}
            )
            candidates = cursor.fetchall()
        self.stdout.write(
            f"  {len(candidates):,} single-person spellings occur "
            f"{options['min_canonical']}-{options['max_canonical']} times"
        )
        # Drop non-ASCII spellings (a handful exist in the Census/SSA data);
        # they make for awkward search-UI demo names.
        candidates = [c for c in candidates if c[0].isascii() and c[1].isascii()]

        # Stage 2: batched cluster check (typo counts + phonetic survival).
        self.stdout.write("Stage 2: checking cluster typos in batches...")
        survivors = self._cluster_check(candidates, options)
        if options["lev_misses"]:
            mode_note = f" and the search misses >= {options['lev_misses']} real row(s)"
        else:
            mode_note = "" if options["allow_missed"] else " (all typos phonetic, strict mode)"
        self.stdout.write(
            f"  {len(survivors):,} candidates have {options['min_typos']}-{options['max_typos']} "
            f"phonetically-matching typos{mode_note}"
        )

        # Random, reproducible order; early stop in stage 3 after --limit hits.
        rng = random.Random(options["seed"])
        rng.shuffle(survivors)
        survivors = survivors[: options["max_candidates"]]

        self.stdout.write(f"Stage 3: evaluating {len(survivors):,} candidates ({options['workers']} workers)...")
        matches = self._evaluate(survivors, options, limit)

        if not matches:
            self.stdout.write(
                self.style.WARNING(
                    "No candidates matched. Try --seed <other>, --min-false-positives <lower>, "
                    "--max-candidates <higher>, --allow-missed, or --lev-misses 1."
                )
            )
            return

        self.stdout.write("")
        self._print_matches(matches, options)
        if options["print_sql"]:
            self._print_sql(matches)

    # ------------------------------------------------------------------
    # Stage 2
    # ------------------------------------------------------------------

    def _cluster_check(self, candidates: list[tuple], options) -> list[tuple]:
        """Batched stage-2 join; returns candidates that pass the typo-count screen."""
        survivors = []
        for start in range(0, len(candidates), self.CLUSTER_CHECK_CHUNK):
            chunk = candidates[start : start + self.CLUSTER_CHECK_CHUNK]
            values = ",\n           ".join(
                f"(%(first_{i})s, %(last_{i})s, %(person_id_{i})s::uuid)" for i in range(len(chunk))
            )
            params = {}
            for i, (first_name, last_name, canonical_n, person_id) in enumerate(chunk):
                params[f"first_{i}"] = first_name
                params[f"last_{i}"] = last_name
                params[f"person_id_{i}"] = str(person_id)

            sql = CLUSTER_CHECK_SQL.format(values=values)
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                rows = cursor.fetchall()

            by_pid = {
                str(person_id): (cluster_n, typo_n, phonetic_typo_n, missed_n)
                for person_id, cluster_n, typo_n, phonetic_typo_n, missed_n in rows
            }
            for first_name, last_name, canonical_n, person_id in chunk:
                counts = by_pid.get(str(person_id))
                if counts is None:
                    continue
                cluster_n, typo_n, phonetic_typo_n, missed_n = counts
                in_range = options["min_typos"] <= phonetic_typo_n <= options["max_typos"]
                if not in_range:
                    continue
                if options["lev_misses"]:
                    # False-negative demo: the final search must drop >= N real rows.
                    if missed_n >= options["lev_misses"]:
                        survivors.append((first_name, last_name, canonical_n, person_id, cluster_n))
                # Strict mode: every typo row must be phonetic (then typo_n IS the
                # phonetic count); --allow-missed: only the phonetic count matters.
                elif options["allow_missed"] or typo_n == phonetic_typo_n:
                    survivors.append((first_name, last_name, canonical_n, person_id, cluster_n))
        return survivors

    # ------------------------------------------------------------------
    # Stage 3
    # ------------------------------------------------------------------

    def _evaluate(self, survivors: list[tuple], options, limit: int) -> list[dict]:
        """Run EVALUATE_SQL per candidate in worker threads, early-stop at --limit."""
        matches: list[dict] = []
        evaluated = 0
        progress = options["progress"]

        def check(candidate: tuple) -> dict | None:
            first_name, last_name, canonical_n, person_id, cluster_n = candidate
            params = {"first_name": first_name, "last_name": last_name, "person_id": str(person_id)}
            with connection.cursor() as cursor:
                cursor.execute(EVALUATE_SQL, params)
                row = cursor.fetchone()
            if row is None:
                return None
            phonetic_n, phon_lev_n, fp_n, fn_n = row[1], row[2], row[3], row[4]
            false_positives_bare = phonetic_n - cluster_n  # bare-phonetic noise beyond the person
            if fp_n != 0:
                return None
            if options["lev_misses"]:
                if fn_n < options["lev_misses"]:
                    return None
            elif not options["allow_missed"] and fn_n != 0:
                return None
            if false_positives_bare < options["min_false_positives"]:
                return None
            with connection.cursor() as cursor:
                cursor.execute(DETAIL_SQL, params)
                details = cursor.fetchall()
            return {
                "first_name": first_name,
                "last_name": last_name,
                "canonical_n": canonical_n,
                "person_id": str(person_id),
                "cluster_n": cluster_n,
                "phonetic_n": phonetic_n,
                "phon_lev_n": phon_lev_n,
                "false_positives_bare": false_positives_bare,
                "false_negative_n": fn_n,
                "details": details,
            }

        with ThreadPoolExecutor(max_workers=options["workers"]) as pool:
            futures = {pool.submit(check, c): c for c in survivors}
            try:
                for future in as_completed(futures):
                    evaluated += 1
                    if progress and evaluated % progress == 0 and not matches:
                        self.stdout.write(f"  ... {evaluated} evaluated, no matches yet")
                    result = future.result()
                    if result is not None:
                        matches.append(result)
                        if len(matches) >= limit:
                            for f in futures:
                                f.cancel()
                            break
            finally:
                for f in futures:
                    f.cancel()

        # Restore shuffle order is not required; order by canonical count for tidy output
        matches.sort(key=lambda m: (m["canonical_n"], m["first_name"], m["last_name"]))
        return matches

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def _print_matches(self, matches: list[dict], options) -> None:
        bar = "=" * 78
        self.stdout.write(bar)
        self.stdout.write(f"FUZZY-SEARCH DEMO NAMES ({len(matches)} matches)")
        self.stdout.write(bar)
        for n, m in enumerate(matches, start=1):
            self.stdout.write("")
            self.stdout.write(f"[{n}] {m['first_name']} {m['last_name']}")
            self.stdout.write(f"    person_id:      {m['person_id']}")
            self.stdout.write(f"    date_of_birth:  {m['details'][0][3]}")
            self.stdout.write(f"    canonical rows: {m['canonical_n']}  (criterion: 10-20)")
            self.stdout.write(f"    cluster rows:   {m['cluster_n']}  (person_id / DOB filter)")
            self.stdout.write(
                f"    phonetic only:  {m['phonetic_n']} rows  ({m['false_positives_bare']} false positives)"
            )
            exact = (
                "EXACTLY the cluster"
                if m["false_negative_n"] == 0
                else f"the cluster minus {m['false_negative_n']} missed typo row(s)"
            )
            self.stdout.write(f"    phonetic + lev: {m['phon_lev_n']} rows  = {exact}")
            if m["false_negative_n"]:
                breakdown: dict[str, int] = {}
                for _f, _l, rows, _dob, sx, dm, fn_dist, ln_dist in m["details"]:
                    dist_gt2 = fn_dist > 2 or ln_dist > 2
                    codes_ok = sx or dm
                    if not (dist_gt2 or not codes_ok):
                        continue
                    if dist_gt2 and codes_ok:
                        cause = "levenshtein: edit distance > 2"
                    elif dist_gt2:
                        cause = "levenshtein + broken codes"
                    else:
                        cause = "broken phonetic codes (distance within 2)"
                    breakdown[cause] = breakdown.get(cause, 0) + rows
                parts = ", ".join(f"{k}: {v}" for k, v in sorted(breakdown.items()))
                self.stdout.write(f"    missed rows:    {m['false_negative_n']} total  ({parts})")
            self.stdout.write("    variants:")
            for first, last, rows, dob, sx, dm, fn_dist, ln_dist in m["details"]:
                marker = "" if (first, last) == (m["first_name"], m["last_name"]) else "  (typo)"
                codes = (
                    " + ".join(filter(None, ["soundex" if sx else None, "daitch-mokotoff" if dm else None])) or "none"
                )
                missed = (not sx and not dm) or fn_dist > 2 or ln_dist > 2
                suffix = "  MISSED" if missed else ""
                self.stdout.write(
                    f"      {first} {last}{marker}  x{rows}  [phonetic: {codes}, lev: {fn_dist}/{ln_dist}]{suffix}"
                )

    def _print_sql(self, matches: list[dict]) -> None:
        """Emit a standalone psql script reproducing each match's result sets."""
        out = self.stdout.write
        out("")
        out("-- Standalone psql script generated by find_fuzzy_cases")
        out("-- Run:  psql -f demo.sql")
        for n, m in enumerate(matches, start=1):
            first, last, pid = m["first_name"], m["last_name"], m["person_id"]
            out("")
            out(f"-- [{n}] {first} {last}")
            out(
                f"""
-- ground truth: everything the person_id filter shows
SELECT id, first_name, last_name, date_of_birth
FROM records_courtrecord
WHERE person_id = '{pid}'
ORDER BY first_name, last_name;

-- bare phonetic search (soundex and/or DM): note the false positives
SELECT id, first_name, last_name, date_of_birth
FROM records_courtrecord
WHERE (SOUNDEX(UPPER(first_name)) = SOUNDEX('{first}')
       AND SOUNDEX(UPPER(last_name)) = SOUNDEX('{last}'))
   OR (DAITCH_MOKOTOFF(UPPER(first_name)) && DAITCH_MOKOTOFF('{first}')
       AND DAITCH_MOKOTOFF(UPPER(last_name)) && DAITCH_MOKOTOFF('{last}'))
ORDER BY first_name, last_name;

-- phonetic + Levenshtein(<=2): exactly the person's rows
SELECT id, first_name, last_name, date_of_birth
FROM records_courtrecord
WHERE ((SOUNDEX(UPPER(first_name)) = SOUNDEX('{first}')
        AND SOUNDEX(UPPER(last_name)) = SOUNDEX('{last}'))
   OR (DAITCH_MOKOTOFF(UPPER(first_name)) && DAITCH_MOKOTOFF('{first}')
       AND DAITCH_MOKOTOFF(UPPER(last_name)) && DAITCH_MOKOTOFF('{last}')))
  AND levenshtein_less_equal(UPPER(first_name), '{first}', 2) <= 2
  AND levenshtein_less_equal(UPPER(last_name), '{last}', 2) <= 2
ORDER BY first_name, last_name;
"""
            )
