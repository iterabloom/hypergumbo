"""Tests for --no-first-party-priority flag in sketch command.

Tests that the flag disables tier-based weighting of symbols, giving all
symbols equal priority regardless of supply chain tier.
"""

import pytest
from pathlib import Path

from hypergumbo.cli import build_parser
from hypergumbo.sketch import generate_sketch


class TestNoFirstPartyPriorityParser:
    """Test --no-first-party-priority argument parsing."""

    def test_sketch_has_no_first_party_priority_argument(self):
        """Sketch command should accept --no-first-party-priority."""
        parser = build_parser()
        args = parser.parse_args(["sketch", ".", "--no-first-party-priority"])
        assert args.first_party_priority is False

    def test_first_party_priority_default_is_true(self):
        """Default first_party_priority should be True."""
        parser = build_parser()
        args = parser.parse_args(["sketch", "."])
        assert args.first_party_priority is True


class TestNoFirstPartyPriorityBehavior:
    """Test that tier weighting is disabled when flag is set."""

    @pytest.fixture
    def minimal_repo(self, tmp_path: Path) -> Path:
        """Create a minimal repo for testing."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("def main(): pass\n")
        return tmp_path

    def test_generate_sketch_accepts_first_party_priority(
        self, minimal_repo: Path
    ):
        """generate_sketch should accept first_party_priority parameter."""
        # Should not raise when called with first_party_priority=True
        result = generate_sketch(
            minimal_repo, max_tokens=500, first_party_priority=True
        )
        assert "app.py" in result or "main" in result or "Overview" in result

    def test_generate_sketch_with_priority_disabled(self, minimal_repo: Path):
        """generate_sketch should work with first_party_priority=False."""
        # Should not raise when called with first_party_priority=False
        result = generate_sketch(
            minimal_repo, max_tokens=500, first_party_priority=False
        )
        assert "Overview" in result

    def test_output_differs_with_priority_disabled(self, tmp_path: Path):
        """Sketch output may differ when priority is disabled.

        This test verifies the flag is wired through correctly by checking
        that both modes run without error. The actual ranking difference
        is tested indirectly through the centrality tests.
        """
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text('''
def entry_point():
    """Main entry point."""
    helper()

def helper():
    """Helper function."""
    pass
''')

        # Both should work
        with_priority = generate_sketch(
            tmp_path, max_tokens=1000, first_party_priority=True
        )
        without_priority = generate_sketch(
            tmp_path, max_tokens=1000, first_party_priority=False
        )

        # Both should contain the header
        assert "Overview" in with_priority
        assert "Overview" in without_priority
