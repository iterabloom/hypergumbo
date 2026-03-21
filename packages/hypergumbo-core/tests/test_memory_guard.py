# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for memory pressure guard in analysis pipeline."""
from __future__ import annotations

from unittest.mock import mock_open, patch

import pytest

from hypergumbo_core.analyze.base import (
    MemoryPressureError,
    _check_memory_pressure,
)


class TestCheckMemoryPressure:
    """Tests for _check_memory_pressure."""

    def test_no_error_when_memory_available(self) -> None:
        """No error raised when plenty of memory available."""
        meminfo = "MemAvailable:   20000000 kB\n"
        with patch("builtins.open", mock_open(read_data=meminfo)):
            _check_memory_pressure()  # Should not raise

    def test_raises_when_memory_low(self) -> None:
        """Raises MemoryPressureError when memory below threshold."""
        meminfo = "MemAvailable:   100000 kB\n"  # ~97 MB
        with patch("builtins.open", mock_open(read_data=meminfo)):
            with pytest.raises(MemoryPressureError, match="Available memory"):
                _check_memory_pressure()

    def test_no_error_on_non_linux(self) -> None:
        """No error when /proc/meminfo not available (non-Linux)."""
        with patch("builtins.open", side_effect=OSError("no /proc")):
            _check_memory_pressure()  # Should not raise

    def test_disabled_via_env(self) -> None:
        """No check when HYPERGUMBO_MIN_MEMORY_MB=0."""
        with patch("hypergumbo_core.analyze.base._MIN_AVAILABLE_MB", 0):
            # Should not even try to read /proc/meminfo
            _check_memory_pressure()

    def test_is_memory_error_subclass(self) -> None:
        """MemoryPressureError is a MemoryError subclass."""
        assert issubclass(MemoryPressureError, MemoryError)
