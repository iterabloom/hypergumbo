# SPDX-License-Identifier: MPL-2.0
"""Tests for htrac serve session management.

Sessions are opaque crypto-random tokens stored server-side only.
Configurable TTL (default 15 min, fixed window). Auth class: normal/duress.
All sessions invalidated on process restart (in-memory only).
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest


class TestSessionStore:
    """Tests for SessionStore in-memory session management."""

    def test_create_session(self) -> None:
        """create_session returns a random token string."""
        from hypergumbo_tracker.serve_sessions import SessionStore

        store = SessionStore()
        token = store.create_session(auth_class="normal")
        assert isinstance(token, str)
        assert len(token) >= 32  # crypto-random, at least 128 bits

    def test_tokens_are_unique(self) -> None:
        """Each session gets a unique token."""
        from hypergumbo_tracker.serve_sessions import SessionStore

        store = SessionStore()
        t1 = store.create_session(auth_class="normal")
        t2 = store.create_session(auth_class="normal")
        assert t1 != t2

    def test_validate_session(self) -> None:
        """Valid token returns session data."""
        from hypergumbo_tracker.serve_sessions import SessionStore

        store = SessionStore()
        token = store.create_session(auth_class="normal")
        session = store.validate(token)
        assert session is not None
        assert session.auth_class == "normal"

    def test_validate_invalid_token(self) -> None:
        """Invalid token returns None."""
        from hypergumbo_tracker.serve_sessions import SessionStore

        store = SessionStore()
        assert store.validate("nonexistent-token") is None

    def test_session_has_creation_time(self) -> None:
        """Session records creation time."""
        from hypergumbo_tracker.serve_sessions import SessionStore

        store = SessionStore()
        before = time.time()
        token = store.create_session(auth_class="normal")
        after = time.time()
        session = store.validate(token)
        assert session is not None
        assert before <= session.created_at <= after

    def test_session_has_expiry(self) -> None:
        """Session has an expiry time based on TTL."""
        from hypergumbo_tracker.serve_sessions import SessionStore

        store = SessionStore(ttl_seconds=900)  # 15 min
        token = store.create_session(auth_class="normal")
        session = store.validate(token)
        assert session is not None
        assert session.expires_at == pytest.approx(session.created_at + 900, abs=1)

    def test_expired_session_returns_none(self) -> None:
        """Expired sessions are rejected."""
        from hypergumbo_tracker.serve_sessions import SessionStore

        store = SessionStore(ttl_seconds=1)
        token = store.create_session(auth_class="normal")

        with patch("time.time", return_value=time.time() + 2):
            assert store.validate(token) is None

    def test_duress_auth_class(self) -> None:
        """Duress sessions are tagged appropriately."""
        from hypergumbo_tracker.serve_sessions import SessionStore

        store = SessionStore()
        token = store.create_session(auth_class="duress")
        session = store.validate(token)
        assert session is not None
        assert session.auth_class == "duress"

    def test_client_fingerprint(self) -> None:
        """Sessions can store a client fingerprint."""
        from hypergumbo_tracker.serve_sessions import SessionStore

        store = SessionStore()
        token = store.create_session(auth_class="normal", fingerprint="ua:test/1.0")
        session = store.validate(token)
        assert session is not None
        assert session.fingerprint == "ua:test/1.0"

    def test_revoke_session(self) -> None:
        """Revoking a session makes it invalid."""
        from hypergumbo_tracker.serve_sessions import SessionStore

        store = SessionStore()
        token = store.create_session(auth_class="normal")
        assert store.validate(token) is not None
        store.revoke(token)
        assert store.validate(token) is None

    def test_revoke_nonexistent_is_noop(self) -> None:
        """Revoking a nonexistent token does not raise."""
        from hypergumbo_tracker.serve_sessions import SessionStore

        store = SessionStore()
        store.revoke("nonexistent")  # Should not raise

    def test_clear_all_sessions(self) -> None:
        """clear() invalidates all sessions (restart scenario)."""
        from hypergumbo_tracker.serve_sessions import SessionStore

        store = SessionStore()
        t1 = store.create_session(auth_class="normal")
        t2 = store.create_session(auth_class="duress")
        store.clear()
        assert store.validate(t1) is None
        assert store.validate(t2) is None

    def test_active_count(self) -> None:
        """active_count returns number of non-expired sessions."""
        from hypergumbo_tracker.serve_sessions import SessionStore

        store = SessionStore()
        assert store.active_count() == 0
        store.create_session(auth_class="normal")
        store.create_session(auth_class="normal")
        assert store.active_count() == 2

    def test_default_ttl_is_15_minutes(self) -> None:
        """Default TTL is 900 seconds (15 minutes)."""
        from hypergumbo_tracker.serve_sessions import SessionStore

        store = SessionStore()
        assert store.ttl_seconds == 900

    def test_custom_ttl(self) -> None:
        """TTL can be customized."""
        from hypergumbo_tracker.serve_sessions import SessionStore

        store = SessionStore(ttl_seconds=60)
        assert store.ttl_seconds == 60

    def test_cleanup_expired(self) -> None:
        """cleanup_expired removes expired sessions from memory."""
        from hypergumbo_tracker.serve_sessions import SessionStore

        store = SessionStore(ttl_seconds=1)
        store.create_session(auth_class="normal")
        assert store.active_count() == 1

        with patch("time.time", return_value=time.time() + 2):
            store.cleanup_expired()
            assert store.active_count() == 0
