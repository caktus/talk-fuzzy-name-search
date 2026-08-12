"""Nickname mapping for fuzzy name search.

This module provides:
- NICKNAME_MAP: Common name → nickname mappings
- resolve_variants(): Resolve a name to all its known variants

Phonetic token generation (Soundex, Daitch-Mokotoff) is handled by
PostgreSQL's fuzzystrmatch extension directly in queries, not in Python.
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
