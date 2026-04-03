# SPDX-License-Identifier: MPL-2.0
"""Tests for htrac serve DuressHandler Protocol.

The DuressHandler Protocol defines two hooks: on_duress_login (called once
when a duress session starts) and filter_response (called on every response).
User implements their own handler. NullDuressHandler is the default (no-op).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


class TestDuressHandlerProtocol:
    """Tests for the DuressHandler Protocol definition."""

    def test_protocol_has_on_duress_login(self) -> None:
        """DuressHandler Protocol requires on_duress_login method."""
        from hypergumbo_tracker.serve_duress import DuressHandler

        assert hasattr(DuressHandler, "on_duress_login")

    def test_protocol_has_filter_response(self) -> None:
        """DuressHandler Protocol requires filter_response method."""
        from hypergumbo_tracker.serve_duress import DuressHandler

        assert hasattr(DuressHandler, "filter_response")


class TestNullDuressHandler:
    """Tests for the default no-op duress handler."""

    @pytest.mark.asyncio
    async def test_on_duress_login_is_noop(self) -> None:
        """NullDuressHandler.on_duress_login does nothing."""
        from hypergumbo_tracker.serve_duress import NullDuressHandler

        handler = NullDuressHandler()
        await handler.on_duress_login(session={}, context={})  # Should not raise

    def test_filter_response_passes_through(self) -> None:
        """NullDuressHandler.filter_response returns response unchanged."""
        from hypergumbo_tracker.serve_duress import NullDuressHandler

        handler = NullDuressHandler()
        response = {"items": [1, 2, 3]}
        result = handler.filter_response(session={}, response=response)
        assert result is response  # Same object, not a copy

    def test_implements_protocol(self) -> None:
        """NullDuressHandler satisfies the DuressHandler Protocol."""
        from hypergumbo_tracker.serve_duress import DuressHandler, NullDuressHandler

        handler = NullDuressHandler()
        assert isinstance(handler, DuressHandler)


class TestLoadDuressHandler:
    """Tests for loading a DuressHandler from a Python module path."""

    def test_load_null_when_no_config(self) -> None:
        """Returns NullDuressHandler when no module path configured."""
        from hypergumbo_tracker.serve_duress import NullDuressHandler, load_duress_handler

        handler = load_duress_handler(None)
        assert isinstance(handler, NullDuressHandler)

    def test_load_null_when_empty_config(self) -> None:
        """Returns NullDuressHandler when module path is empty string."""
        from hypergumbo_tracker.serve_duress import NullDuressHandler, load_duress_handler

        handler = load_duress_handler("")
        assert isinstance(handler, NullDuressHandler)

    def test_load_from_module_path(self, tmp_path: Path) -> None:
        """Loads a custom DuressHandler from a Python file path."""
        from hypergumbo_tracker.serve_duress import load_duress_handler

        # Create a custom handler module
        handler_file = tmp_path / "my_duress.py"
        handler_file.write_text(
            "class Handler:\n"
            "    async def on_duress_login(self, session, context):\n"
            "        pass\n"
            "    def filter_response(self, session, response):\n"
            "        response['filtered'] = True\n"
            "        return response\n"
            "\n"
            "handler = Handler()\n"
        )

        handler = load_duress_handler(str(handler_file))
        assert handler is not None
        # Verify it has the required methods
        assert hasattr(handler, "on_duress_login")
        assert hasattr(handler, "filter_response")

        # Test that filter_response works
        resp = handler.filter_response(session={}, response={"items": []})
        assert resp.get("filtered") is True

    def test_load_returns_null_on_missing_file(self) -> None:
        """Returns NullDuressHandler when file doesn't exist."""
        from hypergumbo_tracker.serve_duress import NullDuressHandler, load_duress_handler

        handler = load_duress_handler("/nonexistent/path/duress.py")
        assert isinstance(handler, NullDuressHandler)

    def test_load_returns_null_on_non_python_file(self, tmp_path: Path) -> None:
        """Returns NullDuressHandler for a non-Python file (spec is None)."""
        from hypergumbo_tracker.serve_duress import NullDuressHandler, load_duress_handler

        bin_file = tmp_path / "duress.bin"
        bin_file.write_bytes(b"\x00\x01\x02\x03")

        handler = load_duress_handler(str(bin_file))
        assert isinstance(handler, NullDuressHandler)

    def test_load_returns_null_on_bad_module(self, tmp_path: Path) -> None:
        """Returns NullDuressHandler when module has no 'handler' attribute."""
        from hypergumbo_tracker.serve_duress import NullDuressHandler, load_duress_handler

        bad_file = tmp_path / "bad_duress.py"
        bad_file.write_text("x = 42\n")

        handler = load_duress_handler(str(bad_file))
        assert isinstance(handler, NullDuressHandler)


class TestWithTimeoutEnforcement:
    """Tests for timeout enforcement on duress handler calls."""

    @pytest.mark.asyncio
    async def test_on_login_timeout(self) -> None:
        """on_duress_login is cancelled if it exceeds timeout."""
        from hypergumbo_tracker.serve_duress import call_on_duress_login

        class SlowHandler:
            async def on_duress_login(self, session, context):
                await asyncio.sleep(10)
            def filter_response(self, session, response):
                return response

        result = await call_on_duress_login(SlowHandler(), {}, {}, timeout=0.1)
        assert result is False  # Timed out

    @pytest.mark.asyncio
    async def test_on_login_success_within_timeout(self) -> None:
        """on_duress_login succeeds within timeout."""
        from hypergumbo_tracker.serve_duress import call_on_duress_login

        class FastHandler:
            async def on_duress_login(self, session, context):
                pass
            def filter_response(self, session, response):
                return response

        result = await call_on_duress_login(FastHandler(), {}, {}, timeout=1.0)
        assert result is True
