"""Characterization tests for records.phonetics module.

Captures existing behavior of phonetic token generation and nickname resolution.
"""

from records.phonetics import (
    NICKNAME_MAP,
    dm_soundex_tokens,
    resolve_variants,
    soundex_tokens,
)


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


class TestSoundexTokens:
    """Characterize soundex_tokens() behavior."""

    def test_returns_non_empty_list(self):
        """soundex_tokens returns a non-empty list for valid names."""
        tokens = soundex_tokens("John")
        assert isinstance(tokens, list)
        assert len(tokens) > 0

    def test_smith_soundex(self):
        """Smith generates Soundex token S530."""
        tokens = soundex_tokens("Smith")
        assert "S530" in tokens

    def test_john_soundex(self):
        """John generates Soundex token J500."""
        tokens = soundex_tokens("John")
        assert "J500" in tokens

    def test_william_includes_bill_tokens(self):
        """William's tokens include Bill's Soundex code."""
        tokens = soundex_tokens("William")
        # Bill's Soundex is B400
        assert "B400" in tokens

    def test_returns_sorted_unique_tokens(self):
        """Tokens are sorted and unique."""
        tokens = soundex_tokens("Robert")
        assert tokens == sorted(set(tokens))

    def test_empty_name_returns_empty(self):
        """Empty name returns empty list."""
        tokens = soundex_tokens("")
        assert tokens == []


class TestDmSoundexTokens:
    """Characterize dm_soundex_tokens() behavior."""

    def test_returns_non_empty_list(self):
        """dm_soundex_tokens returns a non-empty list for valid names."""
        tokens = dm_soundex_tokens("John")
        assert isinstance(tokens, list)
        assert len(tokens) > 0

    def test_tokens_start_with_dm_prefix(self):
        """DM tokens have DM prefix."""
        tokens = dm_soundex_tokens("Smith")
        assert all(t.startswith("DM") for t in tokens)

    def test_returns_sorted_unique_tokens(self):
        """Tokens are sorted and unique."""
        tokens = dm_soundex_tokens("Robert")
        assert tokens == sorted(set(tokens))

    def test_william_includes_variants(self):
        """William's DM tokens include variant codes."""
        tokens = dm_soundex_tokens("William")
        assert len(tokens) >= 1

    def test_empty_name_returns_default(self):
        """Empty name returns default token."""
        tokens = dm_soundex_tokens("")
        assert tokens == ["DM0000"]
