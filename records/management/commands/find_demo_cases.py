"""Find attorney-friendly fuzzy-name-search demo cases in the existing data.

Read-only: scans the 54M-row records table for a person (one person_id, one
DOB) whose dominant exact spelling appears --min..--max times, with at least
--min-typos additional differently-spelled records for the same person within
Levenshtein distance 1..2 of the dominant spelling. Ranks by typo-row count,
typo-form distinctness, then shortest names (cleanest to present), prints the
full attorney narrative with live counts for every search mode.

Discovery is two aggregate passes (fast: ~1 min at 54M rows):

    pass 1  GROUP BY (person_id, first_name, last_name) — keeps the dominant
            spelling of every person_id whose dominant count is in range and
            who has enough distinct spellings to hold the minimum typos
    pass 2  fetches all rows for the surviving person_ids

Unindexed per-name scans (Levenshtein over all 54M, trigram similarity) are
expensive, so they only run with --full.

Usage:
    python manage.py find_demo_cases                # discover + print top 3
    python manage.py find_demo_cases --top 5        # more cases
    python manage.py find_demo_cases --full         # include lev2/trigram sets (~10 min at 54M)
    python manage.py find_demo_cases --case "JIM LLOYD:1981-11-07" --full
    python manage.py find_demo_cases --curated --full   # the 3 verified talk cases
"""

from __future__ import annotations

import time
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

#: Names the talk has been rehearsed against (verified against the live 54M
#: seed on 2026-08-21). --curated prints exactly these, with the verified
#: "bad input" variants an attorney might actually type.
CURATED_CASES: list[tuple[str, str, str, list[str]]] = [
    # (first, last, dob, suggested bad-input first last variants)
    ("JIM", "LLOYD", "1981-11-07", ["JI LLOYD", "JIM LOYD", "JIM LMOYD", "IJM LLOYD", "JIM LLOYB"]),
    ("WILL", "VAUGHN", "1973-07-19", ["WILL VAUGH", "WILL VAUGNH", "WILW VAUGHN", "ILL VAUGHN", "WILL VAUGHAN"]),
    ("JIM", "WALTERS", "2003-06-02", ["JI WALTERS", "JMI WALTERS", "JIM WPLTERS", "JIM WALTER", "IM WALTERS"]),
]

T = "records_courtrecord"


def python_soundex(name: str) -> str:
    """4-char Soundex code for ASCII names (ranking heuristic only).

    Mirrors the standard Soundex rules closely enough to rank discovery
    candidates: keep the first letter, collapse adjacent same-coded
    consonants (H/W are ignored, vowels reset the previous code). Not used
    for any filtering — only to prefer "sounds the same" typos when sorting.
    """
    if not name:
        return "0000"
    code = {
        "b": "1", "f": "1", "p": "1", "v": "1",
        "c": "2", "g": "2", "j": "2", "k": "2", "q": "2", "s": "2", "x": "2", "z": "2",
        "d": "3", "t": "3",
        "l": "4",
        "m": "5", "n": "5",
        "r": "6",
    }
    up = name.upper()
    out = [up[0]]
    prev_code = code.get(up[0].lower(), None)
    for ch in up[1:]:
        low = ch.lower()
        c = code.get(low)
        if c is None:
            # Vowels (a e i o u y) reset; H/W are transparent (not reset).
            if low not in "hw":
                prev_code = None
            continue
        if c != prev_code:
            out.append(c)
            if len(out) == 4:
                break
        prev_code = c
    return "".join(out).ljust(4, "0")


def levenshtein(a: str, b: str) -> int:
    """Edit distance between two (case-insensitive) strings, capped at 3.

    Returns 3 ("> 2") instead of the true distance once it exceeds 2 — all
    callers only test ``1 <= d <= 2``. Bounding the DP by a constant keeps it
    O(len) and the early-bail is safe because row minima are monotone
    non-decreasing.
    """
    a, b = a.upper(), b.upper()
    if len(a) < len(b):
        return levenshtein(b, a)
    if len(b) == 0:
        return min(len(a), 3)
    if len(a) - len(b) > 2:
        return 3
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        row_min = cur[0]
        for j, cb in enumerate(b):
            v = min(prev[j + 1] + 1, cur[j] + 1, prev[j] + (ca != cb))
            if v > 2:
                v = 3
            cur.append(v)
            if v < row_min:
                row_min = v
        if row_min > 2:
            return 3
        prev = cur
    return prev[-1]


class Command(BaseCommand):
    help = "Find (read-only) attorney-friendly fuzzy name search demo cases in the existing data."

    def add_arguments(self, parser):
        parser.add_argument("--min", type=int, default=10, dest="min_n", help="min canonical-spelling rows (default 10)")
        parser.add_argument("--max", type=int, default=20, dest="max_n", help="max canonical-spelling rows (default 20)")
        parser.add_argument("--min-typos", type=int, default=3, help="min distinct differently-spelled forms within edit dist 1..2 (default 3)")
        parser.add_argument("--top", type=int, default=3, help="how many discovered cases to print (default 3)")
        parser.add_argument("--full", action="store_true", help="also run the unindexed Levenshtein/trigram counts per name (slow at 54M)")
        parser.add_argument(
            "--case",
            action="append",
            default=[],
            metavar="FN LN:YYYY-MM-DD",
            help='print a specific known person, e.g. "JIM LLOYD:1981-11-07" (repeatable)',
        )
        parser.add_argument("--curated", action="store_true", help="print the verified talk cases with their curated bad-input variants")

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    PASS1_SQL = """
        WITH agg AS (
            SELECT person_id, first_name, last_name, count(*) AS n
            FROM records_courtrecord
            GROUP BY person_id, first_name, last_name
        ),
        person_agg AS (
            SELECT person_id, count(*) AS distinct_forms, max(n) AS max_n
            FROM agg
            GROUP BY person_id
        )
        SELECT a.person_id, a.first_name AS fn0, a.last_name AS ln0, a.n AS canonical_n,
               (SELECT count(DISTINCT r.date_of_birth)
                FROM records_courtrecord r WHERE r.person_id = a.person_id) AS dob_count
        FROM agg a
        JOIN person_agg p ON p.person_id = a.person_id
        WHERE a.n = p.max_n
          AND p.max_n BETWEEN %s AND %s
          AND p.distinct_forms >= %s
    """
    PASS2_CHUNK = 20_000

    def _find_clusters(self, min_n: int, max_n: int) -> list[dict]:
        """Two aggregate passes; returns all qualifying clusters (unranked)."""
        out = self.stdout.write
        t0 = time.perf_counter()
        with connection.cursor() as cur:
            cur.execute(self.PASS1_SQL, [min_n, max_n, self._min_typos + 1])
            candidates = cur.fetchall()
        out(f"  pass 1: {len(candidates):,} candidate dominant spellings in {time.perf_counter() - t0:.1f}s")

        out(f"  pass 2: fetching all rows for {len(candidates):,} person_ids...")
        t0 = time.perf_counter()
        results = []
        pids = [r[0] for r in candidates]
        for i in range(0, len(pids), self.PASS2_CHUNK):
            chunk = pids[i : i + self.PASS2_CHUNK]
            with connection.cursor() as cur:
                cur.execute(
                    f"SELECT person_id, first_name, last_name, date_of_birth FROM {T} WHERE person_id = ANY(%s)",
                    [chunk],
                )
                rows = cur.fetchall()
            by_pid: dict = {}
            for pid, fn, ln, dob in rows:
                by_pid.setdefault(pid, []).append((fn, ln, dob))
            for pid, fn0, ln0, cn, dobs in candidates[i : i + self.PASS2_CHUNK]:
                rs = by_pid.get(pid)
                if not rs or dobs != 1:
                    continue  # multi-DOB person: not a clean "one client" story
                forms = Counter((fn, ln) for fn, ln, _ in rs)
                typos = {
                    (fn, ln): n
                    for (fn, ln), n in forms.items()
                    if (fn, ln) != (fn0, ln0) and 1 <= max(levenshtein(fn0, fn), levenshtein(ln0, ln)) <= 2
                }
                if not (len(typos) >= self._min_typos or sum(typos.values()) >= self._min_typos):
                    continue
                # Quality: how many typo rows "sound the same" as the canonical
                # spelling (typo'd field shares its Soundex code). "JIM LOYD"
                # scores; "JOE DY" does not — pure Python, no extra SQL.
                fn_sx, ln_sx = python_soundex(fn0), python_soundex(ln0)
                sounding = sum(
                    n
                    for (f, l), n in typos.items()
                    if (f == fn0 and python_soundex(l) == ln_sx) or (l == ln0 and python_soundex(f) == fn_sx)
                )
                results.append(
                    {
                        "person_id": str(pid),
                        "first": fn0,
                        "last": ln0,
                        "dob": rs[0][2],
                        "canonical_n": cn,
                        "total_rows": len(rs),
                        "forms": dict(forms),
                        "typos": typos,
                        "typo_rows": sum(typos.values()),
                        "sounding_rows": sounding,
                    }
                )
        out(f"  pass 2: {time.perf_counter() - t0:.1f}s -> {len(results):,} qualifying clusters")
        return results

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _landscape(self, fn: str, ln: str) -> dict:
        """Indexed-only result-set sizes for the canonical spelling."""
        def one(sql, p=None):
            with connection.cursor() as cur:
                cur.execute(sql, p or [])
                return cur.fetchone()

        d = {
            "like": one(f"SELECT count(*) FROM {T} WHERE last_name ILIKE %s", [f"%{ln}%"])[0],
            "prefix": one(
                f"SELECT count(*) FROM {T} WHERE UPPER(first_name) LIKE %s AND UPPER(last_name) LIKE %s",
                [fn.upper() + "%", ln.upper() + "%"],
            )[0],
            "exact": one(f"SELECT count(*) FROM {T} WHERE UPPER(first_name)=%s AND UPPER(last_name)=%s", [fn.upper(), ln.upper()])[0],
            "soundex": one(
                f"SELECT count(*) FROM {T} WHERE SOUNDEX(UPPER(first_name))=SOUNDEX(UPPER(%s)) AND SOUNDEX(UPPER(last_name))=SOUNDEX(UPPER(%s))",
                [fn.upper(), ln.upper()],
            )[0],
            "soundex_dob": one(
                f"SELECT count(*) FROM {T} WHERE date_of_birth=%s AND SOUNDEX(UPPER(first_name))=SOUNDEX(UPPER(%s)) AND SOUNDEX(UPPER(last_name))=SOUNDEX(UPPER(%s))",
                [self._current_dob, fn.upper(), ln.upper()],
            )[0],
            "dm": one(
                f"SELECT count(*) FROM {T} WHERE DAITCH_MOKOTOFF(UPPER(first_name)) && DAITCH_MOKOTOFF(UPPER(%s)) AND DAITCH_MOKOTOFF(UPPER(last_name)) && DAITCH_MOKOTOFF(UPPER(%s))",
                [fn.upper(), ln.upper()],
            )[0],
            "dm_dob": one(
                f"SELECT count(*) FROM {T} WHERE date_of_birth=%s AND DAITCH_MOKOTOFF(UPPER(first_name)) && DAITCH_MOKOTOFF(UPPER(%s)) AND DAITCH_MOKOTOFF(UPPER(last_name)) && DAITCH_MOKOTOFF(UPPER(%s))",
                [self._current_dob, fn.upper(), ln.upper()],
            )[0],
        }
        return d

    def _full_sets(self, fn: str, ln: str) -> dict:
        """Unindexed per-name scans — one ~30-60s pass each over all 54M."""
        out = self.stdout.write
        t1 = time.perf_counter()
        with connection.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM {T} WHERE LEVENSHTEIN(UPPER(first_name), UPPER(%s))<=2 AND LEVENSHTEIN(UPPER(last_name), UPPER(%s))<=2",
                [fn.upper(), ln.upper()],
            )
            lev2, lev2_ms = cur.fetchone()[0], (time.perf_counter() - t1) * 1000
        t1 = time.perf_counter()
        with connection.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM {T} WHERE similarity(first_name,%s)>=0.3 AND similarity(last_name,%s)>=0.3",
                [fn.upper(), ln.upper()],
            )
            tri, tri_ms = cur.fetchone()[0], (time.perf_counter() - t1) * 1000
        return {"lev2": lev2, "lev2_ms": lev2_ms, "trigram": tri, "trigram_ms": tri_ms}

    def _variant_line(self, cfn: str, cln: str, vfn: str, vln: str) -> str:
        """One probe: does each phonetic/trigram key hit, and how similar?"""
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT SOUNDEX(UPPER(%s))=SOUNDEX(UPPER(%s)),
                       SOUNDEX(UPPER(%s))=SOUNDEX(UPPER(%s)),
                       DAITCH_MOKOTOFF(UPPER(%s)) && DAITCH_MOKOTOFF(UPPER(%s)),
                       DAITCH_MOKOTOFF(UPPER(%s)) && DAITCH_MOKOTOFF(UPPER(%s)),
                       similarity(%s, %s),
                       similarity(%s, %s)
                """,
                [cfn, vfn.upper(), cln, vln.upper(), cfn, vfn.upper(), cln, vln.upper(),
                 cfn.upper(), vfn.upper(), cln.upper(), vln.upper()],
            )
            sf, sl, df, dl, simf, siml = cur.fetchone()
        return (
            f"      '{vfn} {vln}': soundex fn*{int(sf)}/ln*{int(sl)}  dm fn*{int(df)}/ln*{int(dl)}  "
            f"trigram sim fn={simf:.3f} ({'hit' if simf >= 0.3 else 'MISS'}), ln={siml:.3f} ({'hit' if siml >= 0.3 else 'MISS'})"
        )

    def _print_case(self, fn: str, ln: str, dob_str: str, variants: list[str], cluster: dict | None, full: bool) -> None:
        s = self.stdout.write
        self._current_dob = dob_str
        t0 = time.perf_counter()
        L = self._landscape(fn, ln)
        F = self._full_sets(fn, ln) if full else {}

        s("")
        s("")
        s("=" * 78)
        pid = cluster["person_id"] if cluster else "?"
        s(f"CASE: {fn} {ln}   (DOB {dob_str}, person_id {pid[:13]}...)")
        s("=" * 78)

        if cluster:
            s(f"\n  The client's records for one person_id ({cluster['total_rows']} rows):")
            items = sorted(cluster["forms"].items(), key=lambda kv: -kv[1])
            for (f, l), n in items[:18]:
                tag = "typo" if (f, l) in cluster["typos"] else "    "
                s(f"    x{n:<3} {f:<12} {l}   [{tag}]")
            if len(items) > 18:
                s(f"    ... {len(items)-18} more")
        s(f"\n  0 Client named {fn} {ln}. Let's find their records.")
        s(f"  1 Legacy LIKE '%{ln}%'           -> {L['like']:>11,} rows  (many different people!)")
        if cluster:
            s(f"  2 Add DOB {dob_str} to the exact '{fn} {ln}' record -> person_id {cluster['person_id']} ({cluster['total_rows']} rows, one person)")
        s(f"  3 STARTSWITH prefix (both)       -> {L['prefix']:>11,} rows")
        s(f"  4 Exact (both names)             -> {L['exact']:>11,} rows, {cluster['canonical_n'] if cluster else '?'} belong to THIS person")
        s(f"  5 Soundex (+DOB)                 -> {L['soundex']:>11,} all-DB / {L['soundex_dob']:>4} with DOB   codes: fn {self._sx(fn)}, ln {self._sx(ln)}")
        s(f"  6 Daitch-Mokotoff (+DOB)         -> {L['dm']:>11,} all-DB / {L['dm_dob']:>4} with DOB")
        if F:
            s(f"  7 Trigram similarity >= 0.30     -> {F['trigram']:>11,} rows   (unindexed scan {F['trigram_ms']/1000:.1f}s)")
            s(f"  8 Levenshtein <= 2 on both names -> {F['lev2']:>11,} rows   (unindexed scan {F['lev2_ms']/1000:.1f}s — slow alone, use as a precision AND filter)")
        else:
            s("  7 Trigram /  Levenshtein        -> run with --full (unindexed scans)")

        s("")
        s("  'Bad input' variants and how each mode sees them  (* = code set hit):")
        for v in variants:
            vfn, vln = v.split()
            s(self._variant_line(fn, ln, vfn, vln))
        s(f"\n  [counts took {time.perf_counter()-t0:.1f}s]")

    @staticmethod
    def _sx(name: str) -> str:
        with connection.cursor() as cur:
            cur.execute("SELECT SOUNDEX(UPPER(%s))", [name])
            return cur.fetchone()[0]

    # ------------------------------------------------------------------

    def handle(self, *args, **options):
        self._min_typos = options["min_typos"]
        self._current_dob = None
        full = options["full"]

        if options["curated"] or options["case"]:
            for entry in (
                [(fn, ln, dob, variants) for fn, ln, dob, variants in CURATED_CASES]
                if options["curated"]
                else [self._parse_case(c) for c in options["case"]]
            ):
                fn, ln, dob, variants = entry
                cluster = self._known_cluster(fn, ln, dob)
                if not variants and cluster:
                    # Ad-hoc cases: use the cluster's real typos as bad inputs.
                    variants = [
                        f"{f} {l}"
                        for (f, l), n in sorted(
                            cluster["typos"].items(), key=lambda kv: (-kv[1], kv[0])
                        )
                    ]
                self._print_case(fn, ln, dob, variants, cluster, full)
            return

        out = self.stdout.write
        out("Scanning for demo-worthy person clusters (read-only)...")
        clusters = self._find_clusters(options["min_n"], options["max_n"])
        if not clusters:
            raise CommandError("no qualifying clusters found — try lowering --min-typos or widening --min/--max")

        # Rank: most typo rows that "sound the same" as the canonical spelling,
        # then most typo rows overall, then most distinct typo forms, then
        # shortest names first (cleanest to say out loud).
        clusters.sort(
            key=lambda c: (
                -c["sounding_rows"],
                -c["typo_rows"],
                -len(c["typos"]),
                len(c["first"]) + len(c["last"]),
                c["first"] + c["last"],
            )
        )

        out(f"\nTop {options['top']} of {len(clusters):,} qualifying clusters:")
        for c in clusters[: options["top"]]:
            fn_sx, ln_sx = python_soundex(c["first"]), python_soundex(c["last"])
            sound_like = {
                (f, l): n
                for (f, l), n in c["typos"].items()
                if (f == c["first"] and python_soundex(l) == ln_sx)
                or (l == c["last"] and python_soundex(f) == fn_sx)
            }
            ordered: list[str] = []
            for d in (sound_like, c["typos"]):
                for (f, l), _ in sorted(d.items(), key=lambda kv: -kv[1]):
                    v = f"{f} {l}"
                    if v not in ordered:
                        ordered.append(v)
            self._print_case(c["first"], c["last"], str(c["dob"]), ordered[:5], c, full)

    def _parse_case(self, spec: str) -> tuple[str, str, str, list[str]]:
        try:
            name_part, dob_part = spec.split(":")
            fn, *rest = name_part.split()
            ln = rest[0] if rest else ""
        except ValueError:
            raise CommandError(f"bad --case {spec!r}; expected 'FN LN:YYYY-MM-DD'") from None
        if not fn or not ln:
            raise CommandError(f"bad --case {spec!r}; expected 'FN LN:YYYY-MM-DD'")
        return fn, ln, dob_part, []

    def _known_cluster(self, fn: str, ln: str, dob_str: str) -> dict | None:
        """Locate the client's full cluster for FN LN @ DOB.

        The date_of_birth B-tree index keeps the anchor fetch cheap (~1,500
        rows per day in the 54M seed). From every canonical exact-spelling
        row for that DOB, walk the person_id and collect all its rows; rank
        the candidate person_ids by how many rows they carry and take the
        dominant one.
        """
        with connection.cursor() as cur:
            cur.execute(
                f"SELECT person_id FROM {T} WHERE date_of_birth=%s AND UPPER(first_name)=%s AND UPPER(last_name)=%s LIMIT 50",
                [dob_str, fn.upper(), ln.upper()],
            )
            anchor_pids = [r[0] for r in cur.fetchall()]
        if not anchor_pids:
            return None

        clusters: dict[str, dict] = {}
        for pid in anchor_pids:
            with connection.cursor() as cur:
                cur.execute(
                    f"SELECT first_name, last_name, count(*) FROM {T} WHERE person_id=%s GROUP BY 1, 2",
                    [pid],
                )
                forms = {(f, l): n for f, l, n in cur.fetchall()}
            canonical = (fn.upper(), ln.upper())
            typos = {}
            for (f, l), n in forms.items():
                if (f, l) == canonical:
                    continue
                d = max(levenshtein(fn, f), levenshtein(ln, l))
                if 1 <= d <= 2:
                    typos[(f, l)] = n
            c = {
                "person_id": str(pid),
                "first": fn.upper(),
                "last": ln.upper(),
                "dob": dob_str,
                "canonical_n": forms.get(canonical, 0),
                "total_rows": sum(forms.values()),
                "forms": forms,
                "typos": typos,
                "typo_rows": sum(typos.values()),
            }
            clusters[pid] = c
        # Dominant cluster = most rows; tiebreak on most typo rows.
        return max(clusters.values(), key=lambda c: (c["total_rows"], c["typo_rows"]))
