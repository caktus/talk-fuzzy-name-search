#!/usr/bin/env python3
"""Generate 50M fake person records as CSV and load into PostgreSQL.

Usage:
    uv run python scripts/generate_data.py --count 50000000 --load-db
"""

import argparse
import csv
import os
import random
import string
import sys
import time
from datetime import date, timedelta
from pathlib import Path

COMMON_FIRST_NAMES_MALE = [
    "JAMES",
    "JOHN",
    "ROBERT",
    "MICHAEL",
    "WILLIAM",
    "DAVID",
    "RICHARD",
    "JOSEPH",
    "THOMAS",
    "CHARLES",
    "CHRISTOPHER",
    "DANIEL",
    "MATTHEW",
    "ANTHONY",
    "MARK",
    "DONALD",
    "STEVEN",
    "PAUL",
    "ANDREW",
    "KENNETH",
    "JOSHUA",
    "KEVIN",
    "BRYAN",
    "GEORGE",
    "EDWARD",
    "RONALD",
    "TIMOTHY",
    "JASON",
    "JEFFREY",
    "RYAN",
    "JACKSON",
    "DILLON",
    "LANDON",
    "NATHAN",
    "ETHAN",
    "AIDEN",
    "LUKE",
    "GABRIEL",
    "ISAAC",
    "OWEN",
]

COMMON_FIRST_NAMES_FEMALE = [
    "MARY",
    "PATRICIA",
    "JENNIFER",
    "LINDA",
    "BARBARA",
    "ELIZABETH",
    "SUSAN",
    "JESSICA",
    "SARAH",
    "KAREN",
    "NANCY",
    "LISA",
    "BETTY",
    "MARGARET",
    "SANDRA",
    "ASHLEY",
    "KIMBERLY",
    "EMILY",
    "DONNA",
    "MICHELLE",
    "AMANDA",
    "MELISSA",
    "DEBRA",
    "STEPHANIE",
    "REBECCA",
    "SHARON",
    "LAURA",
    "CYNTHIA",
    "KATHLEEN",
    "AMY",
    "ANGELA",
    "SHIRLEY",
    "ANN",
    "BRENDA",
    "PAMELA",
    "EMMA",
    "OLIVIA",
    "AVA",
    "ISABELLA",
    "MIA",
]

COMMON_LAST_NAMES = [
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
    "WATSON",
    "BROOKS",
    "KELLY",
    "SANDERS",
    "PRICE",
    "BENNETT",
    "WOOD",
    "BARNES",
    "ROSS",
    "HENDRICKS",
    "COLEMAN",
    "JENKINS",
    "PERRY",
    "POWELL",
    "LONG",
    "PATTERSON",
    "HUGGINS",
    "WASHINGTON",
    "BUTLER",
    "SIMMONS",
    "FOSTER",
    "GONZALES",
    "BRYANT",
    "ALEXANDER",
    "RUSSELL",
    "GRIFFIN",
    "DIAZ",
    "HAYES",
    "MYERS",
    "FORD",
    "HAMILTON",
    "GRAHAM",
    "SULLIVAN",
    "WALLACE",
    "WOODS",
    "COLE",
    "WEST",
    "JORDAN",
    "OWENS",
    "REYNOLDS",
    "FISHER",
    "ELLIS",
    "HARRISON",
    "GIBSON",
    "MCDONALD",
    "CRUZ",
    "MARSHALL",
    "ORTIZ",
    "GOMEZ",
    "MURRAY",
    "FREEMAN",
    "WELLS",
    "WEBB",
    "SIMPSON",
    "STEVENS",
    "TUCKER",
    "PORTER",
    "GEORGE",
    "HERRERA",
    "MCCOY",
    "HUNTER",
    "GLOVER",
    "BOYD",
    "CRAWFORD",
    "MASON",
    "MORALES",
    "KENNEDY",
    "WARREN",
    "RICE",
    "ROBERTS",
    "HOPKINS",
    "OLIVER",
    "BRADLEY",
    "ANDREWS",
    "CASTILLO",
    "WAGNER",
]

EXTRA_LAST_NAMES = [
    "ADAMS",
    "BAILEY",
    "BANKS",
    "BELL",
    "BOWMAN",
    "BRADY",
    "BURKE",
    "CAMPBELL",
    "CARTER",
    "CHEN",
    "CONNORS",
    "CORTES",
    "CURTIS",
    "DEAN",
    "DELGADO",
    "DIXON",
    "DOYLE",
    "DUKE",
    "ELLIOTT",
    "EVANS",
    "FLETCHER",
    "FRANKLIN",
    "FULLER",
    "GRAY",
    "HALL",
    "HOWARD",
    "JOYCE",
    "KELLEY",
    "KIM",
    "KNIGHT",
    "LAWRENCE",
    "LINDSEY",
    "LUCAS",
    "MITCHELL",
    "NELSON",
    "O'BRIEN",
    "O'CONNOR",
    "O'NEILL",
    "PARK",
    "PARKER",
    "PATTON",
    "PETERSON",
    "PHAM",
    "QUINN",
    "RAMOS",
    "REYES",
    "RICHARDS",
    "RICHARDSON",
    "ROGERS",
    "ROSE",
    "RUIZ",
    "SEYMOUR",
    "SHAW",
    "SNYDER",
    "STEWART",
    "TANNER",
    "TURNER",
    "VARGAS",
    "WALSH",
    "WALTERS",
    "WARD",
    "WILLIAMSON",
    "YANG",
    "ZHANG",
]

NICKNAME_MAP = {
    "DANIEL": ["DAN", "DANNY"],
    "MICHAEL": ["MIKE", "MICKEY"],
    "ROBERT": ["BOB", "BOBBY", "ROB"],
    "WILLIAM": ["BILL", "BILLY", "WILL"],
    "JAMES": ["JIM", "JIMMY"],
    "RICHARD": ["RICK", "RICKY", "DICK"],
    "THOMAS": ["TOM", "TOMMY"],
    "CHRISTOPHER": ["CHRIS"],
    "ANTHONY": ["TONY"],
    "MATTHEW": ["MATT"],
    "JOSEPH": ["JOE", "JOEY"],
    "DAVID": ["DAVE"],
    "ANDREW": ["ANDY"],
    "JOSHUA": ["JOSH"],
    "NICHOLAS": ["NICK"],
    "JONATHAN": ["JON"],
    "BENJAMIN": ["BEN"],
    "CHARLES": ["CHARLIE", "CHUCK"],
    "ALEXANDER": ["ALEX"],
    "STEPHEN": ["STEVE"],
    "KENNETH": ["KEN", "KENNY"],
    "DOUGLAS": ["DOUG"],
    "GREGORY": ["GREG"],
    "TIMOTHY": ["TIM", "TIMMY"],
    "RONALD": ["RON", "RONNIE"],
    "SAMUEL": ["SAM"],
    "ELIZABETH": ["LIZ", "BETH", "BETTY"],
    "JENNIFER": ["JEN", "JENNY"],
    "PATRICIA": ["PAT", "PATTY"],
    "MARGARET": ["MAGGIE", "PEG"],
    "SUSAN": ["SUE", "SUSIE"],
    "SANDRA": ["SANDY"],
    "KIMBERLY": ["KIM"],
    "DEBORAH": ["DEB", "DEBBIE"],
    "ANGELA": ["ANGIE"],
    "CYNTHIA": ["CINDY"],
    "CATHERINE": ["CATHY", "KATE"],
    "SAMANTHA": ["SAM"],
    "VICTORIA": ["VICKY"],
    "CHRISTINA": ["CHRIS"],
    "JUDITH": ["JUDY"],
    "KATHRYN": ["KATHY", "KATE"],
    "JACQUELINE": ["JACKIE"],
    "TERESA": ["TERRY"],
    "SARAH": ["SARA"],
}

_SOUNDEX_MAP = {
    "BFPV": "1",
    "CGJKQSXZ": "2",
    "DT": "3",
    "L": "4",
    "MN": "5",
    "R": "6",
}
_DM_MAP = {
    "BPFVWH": "0",
    "CSZ": "1",
    "JQX": "2",
    "KGC": "3",
    "LT": "4",
    "DN": "5",
    "R": "6",
    "M": "7",
    "Y": "8",
}


def soundex(name: str) -> str:
    if not name or not name[0].isalpha():
        return "0000"
    name = name.upper().strip()
    code = name[0]
    prev_digit = None
    for char in name[1:]:
        if not char.isalpha():
            continue
        digit = None
        for letters, d in _SOUNDEX_MAP.items():
            if char in letters:
                digit = d
                break
        if digit and digit != prev_digit:
            code += digit
            if len(code) == 4:
                return code
        prev_digit = digit if digit else None
    return code.ljust(4, "0")[:4]


def dm_token(name: str) -> str:
    if not name or not name[0].isalpha():
        return "DM0000"
    name = name.upper().strip()
    digits = []
    prev = None
    for char in name:
        if not char.isalpha():
            continue
        d = None
        for letters, digit in _DM_MAP.items():
            if char in letters:
                d = digit
                break
        if d and d != prev:
            digits.append(d)
        prev = d if d else None
    return f"DM{''.join(digits[:3]).ljust(3, '0')}"


def phonetic_tokens(name: str) -> list[str]:
    s = soundex(name)
    d = dm_token(name)
    tokens = [s, d]
    upper = name.upper()
    if upper in NICKNAME_MAP:
        for nick in NICKNAME_MAP[upper]:
            tokens.extend([soundex(nick), dm_token(nick)])
    return list(dict.fromkeys(tokens))


def phonetic_tokens_csv(name: str) -> str:
    """Return pipe-separated tokens for CSV (readable format)."""
    return "|".join(phonetic_tokens(name))


def inject_typo(name: str) -> str:
    if len(name) < 2:
        return name
    typo = random.choice(["swap", "drop", "substitute"])
    if typo == "swap":
        idx = random.randint(0, len(name) - 2)
        chars = list(name)
        chars[idx], chars[idx + 1] = chars[idx + 1], chars[idx]
        return "".join(chars)
    elif typo == "drop":
        idx = random.randint(0, len(name) - 1)
        return name[:idx] + name[idx + 1 :]
    else:
        idx = random.randint(0, len(name) - 1)
        chars = list(name)
        chars[idx] = random.choice(string.ascii_uppercase)
        return "".join(chars)


def generate_records(count: int, output_csv: str, report_every: int = 5_000_000):
    """Generate records and write to CSV."""
    print(f"Generating {count:,} records -> {output_csv}", flush=True)
    start_time = time.time()

    canonical_list = list(NICKNAME_MAP.keys())
    all_last = COMMON_LAST_NAMES + EXTRA_LAST_NAMES

    with open(output_csv, "w", newline="", buffering=8192 * 16) as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "first_name",
                "last_name",
                "middle_name",
                "date_of_birth",
                "nicknames",
            ]
        )

        for i in range(1, count + 1):
            last_name = random.choice(COMMON_LAST_NAMES) if random.random() < 0.8 else random.choice(all_last)

            is_male = random.random() < 0.65
            nicknames = []

            if random.random() < 0.30:
                canonical = random.choice(canonical_list)
                nicknames = NICKNAME_MAP[canonical]
                first_name = random.choice(nicknames)
            else:
                pool = COMMON_FIRST_NAMES_MALE if is_male else COMMON_FIRST_NAMES_FEMALE
                first_name = random.choice(pool)

            middle_name = ""
            if random.random() < 0.20:
                middle_name = (
                    random.choice(COMMON_FIRST_NAMES_MALE + COMMON_FIRST_NAMES_FEMALE)
                    if random.random() < 0.5
                    else random.choice(string.ascii_uppercase)
                )

            dob = ""
            if random.random() < 0.80:
                years_ago = random.randint(18, 85)
                d = date.today() - timedelta(days=years_ago * 365 + random.randint(0, 365))
                dob = d.isoformat()

            if random.random() < 0.01:
                if random.random() < 0.5:
                    first_name = inject_typo(first_name)
                else:
                    last_name = inject_typo(last_name)

            nicknames_str = "|".join(nicknames) if nicknames else ""

            # CSV format: pipe-separated arrays
            writer.writerow(
                [
                    first_name,
                    last_name,
                    middle_name,
                    dob,
                    nicknames_str,
                ]
            )

            if i % report_every == 0:
                elapsed = time.time() - start_time
                rate = i / elapsed
                eta = (count - i) / rate if rate > 0 else 0
                print(
                    f"  {i:,} / {count:,} ({i / count * 100:.1f}%) {rate:,.0f} rows/s, ETA {eta / 60:.1f}min",
                    flush=True,
                )

    total_time = time.time() - start_time
    print(f"Done! {count:,} records in {total_time:.1f}s ({count / total_time:,.0f} rows/s)", flush=True)


def load_csv_to_db(csv_path: str, batch_size: int = 50000):
    """Load CSV into PostgreSQL using Django bulk_create."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fuzzy_demo.settings")
    import django

    django.setup()

    from records.models import Person

    print(f"Loading {csv_path} into database (bulk_create)...")
    start_time = time.time()

    deleted, _ = Person.objects.all().delete()
    print(f"  Cleared {deleted:,} existing records", flush=True)

    loaded = 0
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        batch = []
        for row in reader:
            nicknames = row.get("nicknames", "").split("|") if row.get("nicknames") else []

            dob = row.get("date_of_birth") or None
            middle = row.get("middle_name") or None

            batch.append(
                Person(
                    first_name=row["first_name"],
                    last_name=row["last_name"],
                    middle_name=middle,
                    date_of_birth=dob,
                    nicknames=nicknames,
                )
            )

            if len(batch) >= batch_size:
                Person.objects.bulk_create(batch)
                loaded += len(batch)
                elapsed = time.time() - start_time
                rate = loaded / elapsed
                print(f"  {loaded:,} loaded ({rate:,.0f} rows/s)", flush=True)
                batch = []

        if batch:
            Person.objects.bulk_create(batch)
            loaded += len(batch)

    total_time = time.time() - start_time
    count = Person.objects.count()
    print(f"Loaded {count:,} records in {total_time:.1f}s", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Generate fake person records")
    parser.add_argument("--count", type=int, default=50_000_000)
    parser.add_argument("--output", type=str, default="data/people_50m.csv")
    parser.add_argument("--load-db", action="store_true")
    parser.add_argument("--report-every", type=int, default=5_000_000)
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    generate_records(args.count, str(output_path), args.report_every)

    if args.load_db:
        load_csv_to_db(str(output_path))


if __name__ == "__main__":
    main()
