"""Phonetic token generation and nickname mapping for fuzzy name search.

This module provides:
- NICKNAME_MAP: Common name → nickname mappings
- soundex_tokens(): Generate Soundex codes for a name and its nicknames
- dm_soundex_tokens(): Generate Daitch-Mokotoff codes for a name and its nicknames

Note: A production system might use only Daitch-Mokotoff via PostgreSQL's
fuzzystrmatch extension. Soundex is included here for educational value
in the demo.
"""

from __future__ import annotations

# Common name → nickname mappings
NICKNAME_MAP: dict[str, list[str]] = {
    # Male variations
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
    # Female variations
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

# Reverse map: nickname → canonical name(s)
_REVERSE_NICKNAME_MAP: dict[str, list[str]] = {}
for _canonical, _nicknames in NICKNAME_MAP.items():
    for _nick in _nicknames:
        _REVERSE_NICKNAME_MAP.setdefault(_nick.upper(), []).append(_canonical)


def resolve_variants(name: str) -> list[str]:
    """Return all known variants of a name (canonical + nicknames).

    If the input is a nickname, resolves to the canonical name and its variants.
    If the input is a canonical name, returns it plus its nicknames.
    If the input has no known variants, returns just the input.

    Args:
        name: A first name (any case).

    Returns:
        A list of unique name variants, all uppercase.
    """
    name_upper = name.upper().strip()
    if not name_upper:
        return []

    variants: set[str] = {name_upper}

    # Check if it's a canonical name with known nicknames
    if name_upper in NICKNAME_MAP:
        variants.update(n.upper() for n in NICKNAME_MAP[name_upper])

    # Check if it's a nickname that maps to a canonical name
    if name_upper in _REVERSE_NICKNAME_MAP:
        for canonical in _REVERSE_NICKNAME_MAP[name_upper]:
            variants.add(canonical)
            if canonical in NICKNAME_MAP:
                variants.update(n.upper() for n in NICKNAME_MAP[canonical])

    return sorted(variants)


# Soundex mapping
_SOUNDEX_MAP = {
    "BFPV": "1",
    "CGJKQSXZ": "2",
    "DT": "3",
    "L": "4",
    "MN": "5",
    "R": "6",
}


def _soundex(name: str) -> str:
    """Compute the Soundex code for a single name.

    Standard Soundex algorithm:
    - Keep the first letter
    - Map remaining consonants to digits
    - Collapse consecutive identical digits
    - Return 4-character code (letter + 3 digits, zero-padded)
    """
    if not name:
        return "0000"

    name = name.upper().strip()
    if not name[0].isalpha():
        return "0000"

    first_letter = name[0]
    code = first_letter

    # Map each character to its Soundex digit
    prev_digit = None
    for char in name[1:]:
        if not char.isalpha():
            continue

        digit = None
        for letters, d in _SOUNDEX_MAP.items():
            if char in letters:
                digit = d
                break

        # Skip if same digit as previous (handles H/W separators implicitly)
        if digit and digit != prev_digit:
            code += digit
            if len(code) == 4:
                return code
        prev_digit = digit if digit else None

    return code.ljust(4, "0")[:4]


def soundex_tokens(name: str) -> list[str]:
    """Generate Soundex codes for a name and all its known nickname variants.

    Args:
        name: A first or last name (any case).

    Returns:
        A sorted list of unique Soundex codes.
    """
    variants = resolve_variants(name)
    codes = set()
    for variant in variants:
        code = _soundex(variant)
        if code != "0000":
            codes.add(code)
    return sorted(codes)


# Simplified Daitch-Mokotoff mapping. A production system would use
# PostgreSQL's fuzzystrmatch.daitch_mokotoff(), which returns a text[] of
# codes. This is a simplified Python approximation for demo purposes.
_DM_CODE_MAP = {
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


def dm_soundex_tokens(name: str) -> list[str]:
    """Generate Daitch-Mokotoff codes for a name and all its known nickname variants.

    This is a simplified Python approximation. Production uses PostgreSQL's
    fuzzystrmatch.daitch_mokotoff() which generates a comprehensive set
    of phonetic codes.

    For the demo, we generate multiple codes per variant to simulate
    the broader coverage of the real DM algorithm.

    Args:
        name: A first or last name (any case).

    Returns:
        A sorted list of unique DM codes.
    """
    variants = resolve_variants(name)
    codes = set()
    for variant in variants:
        if not variant or not variant[0].isalpha():
            continue

        # Primary code
        digits = []
        prev_digit = None
        for char in variant:
            if not char.isalpha():
                continue
            digit = None
            for letters, d in _DM_CODE_MAP.items():
                if char in letters:
                    digit = d
                    break
            if digit and digit != prev_digit:
                digits.append(digit)
            prev_digit = digit if digit else None

        code = "".join(digits[:3]).ljust(3, "0")
        codes.add(f"DM{code}")

        # Generate variations (simulating DM's broader coverage)
        if digits:
            # Variation: shift first digit
            alt = list(digits)
            alt[0] = str((int(alt[0]) + 1) % 9)
            codes.add(f"DM{''.join(alt[:3]).ljust(3, '0')}")
            # Variation: shift last digit
            if len(alt) > 1:
                alt2 = list(digits)
                alt2[-1] = str((int(alt2[-1]) + 1) % 9)
                codes.add(f"DM{''.join(alt2[:3]).ljust(3, '0')}")

    return sorted(codes) if codes else ["DM0000"]
