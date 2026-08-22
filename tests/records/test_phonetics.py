"""Characterization tests for records.phonetics module.

Captures existing behavior of the NICKNAME_MAP structure.
Phonetic token generation (Soundex, Daitch-Mokotoff) is now handled
by PostgreSQL's fuzzystrmatch extension directly in queries.
"""

from records.phonetics import NICKNAME_MAP


class TestNicknameMap:
    """Characterize NICKNAME_MAP structure and content."""

    def test_nickname_map_has_entries(self):
        """NICKNAME_MAP contains name→nickname mappings."""
        assert len(NICKNAME_MAP) >= 20

    def test_nickname_map_william_variants(self):
        """William maps to common nicknames."""
        assert "BILL" in NICKNAME_MAP["WILLIAM"]
        assert "WILL" in NICKNAME_MAP["WILLIAM"]

    def test_nickname_map_robert_variants(self):
        """Robert maps to common nicknames."""
        assert "BOB" in NICKNAME_MAP["ROBERT"]
        assert "ROB" in NICKNAME_MAP["ROBERT"]
