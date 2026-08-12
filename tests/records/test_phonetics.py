"""Characterization tests for records.phonetics module.

Captures existing behavior of nickname resolution.
Phonetic token generation (Soundex, Daitch-Mokotoff) is now handled
by PostgreSQL's fuzzystrmatch extension directly in queries.
"""

from records.phonetics import NICKNAME_MAP, resolve_variants


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


class TestResolveVariants:
    """Characterize resolve_variants() behavior."""

    def test_canonical_name_returns_variants(self):
        """Canonical name returns itself plus nicknames."""
        variants = resolve_variants("William")
        assert "WILLIAM" in variants
        assert "BILL" in variants
        assert "WILL" in variants

    def test_nickname_resolves_to_canonical(self):
        """Nickname resolves to canonical name and variants."""
        variants = resolve_variants("Bob")
        assert "ROBERT" in variants
        assert "BOB" in variants

    def test_unknown_name_returns_self(self):
        """Unknown name returns just itself."""
        variants = resolve_variants("Xyzzy")
        assert variants == ["XYZZY"]

    def test_empty_name_returns_empty(self):
        """Empty name returns empty list."""
        variants = resolve_variants("")
        assert variants == []

    def test_case_insensitive(self):
        """Name resolution is case-insensitive."""
        assert resolve_variants("william") == resolve_variants("WILLIAM")
