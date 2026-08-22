"""Nickname mapping for fuzzy name search.

This module provides:
- NICKNAME_MAP: Common name → nickname mappings

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
