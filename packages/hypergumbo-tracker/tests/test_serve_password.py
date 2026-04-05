# SPDX-License-Identifier: MPL-2.0
"""Tests for htrac serve bcrypt password verification.

Two passwords: real (normal session) and duress (tagged session).
Rate limiting per credential with exponential backoff.
Max failures before credential lockout.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest


class TestPasswordVerifier:
    """Tests for PasswordVerifier with bcrypt hashing."""

    def test_verify_real_password(self) -> None:
        """Correct real password returns 'normal'."""
        from hypergumbo_tracker.serve_password import PasswordVerifier

        pv = PasswordVerifier.from_plaintext(real="secret123", duress="panic456")
        assert pv.verify("secret123") == "normal"

    def test_verify_duress_password(self) -> None:
        """Correct duress password returns 'duress'."""
        from hypergumbo_tracker.serve_password import PasswordVerifier

        pv = PasswordVerifier.from_plaintext(real="secret123", duress="panic456")
        assert pv.verify("panic456") == "duress"

    def test_verify_wrong_password(self) -> None:
        """Wrong password returns None."""
        from hypergumbo_tracker.serve_password import PasswordVerifier

        pv = PasswordVerifier.from_plaintext(real="secret123", duress="panic456")
        assert pv.verify("wrongpass") is None

    def test_from_hashes(self) -> None:
        """Can initialize from pre-computed bcrypt hashes."""
        import bcrypt

        from hypergumbo_tracker.serve_password import PasswordVerifier

        real_hash = bcrypt.hashpw(b"secret123", bcrypt.gensalt()).decode()
        duress_hash = bcrypt.hashpw(b"panic456", bcrypt.gensalt()).decode()

        pv = PasswordVerifier(real_hash=real_hash, duress_hash=duress_hash)
        assert pv.verify("secret123") == "normal"
        assert pv.verify("panic456") == "duress"

    def test_constant_time_comparison(self) -> None:
        """Both real and duress are always checked (no early return on first match)."""
        from hypergumbo_tracker.serve_password import PasswordVerifier

        pv = PasswordVerifier.from_plaintext(real="secret123", duress="panic456")
        # This just verifies correctness — timing is hard to test directly
        assert pv.verify("secret123") == "normal"
        assert pv.verify("panic456") == "duress"
        assert pv.verify("neither") is None


class TestRateLimiter:
    """Tests for per-credential rate limiting with exponential backoff."""

    def test_no_limit_initially(self) -> None:
        """First attempt is not rate-limited."""
        from hypergumbo_tracker.serve_password import RateLimiter

        rl = RateLimiter()
        assert rl.is_allowed("cred-1") is True

    def test_allowed_after_success(self) -> None:
        """After recording success, attempts are still allowed."""
        from hypergumbo_tracker.serve_password import RateLimiter

        rl = RateLimiter()
        rl.record_success("cred-1")
        assert rl.is_allowed("cred-1") is True

    def test_backoff_after_failure(self) -> None:
        """After a failure, next attempt is rate-limited (backoff)."""
        from hypergumbo_tracker.serve_password import RateLimiter

        rl = RateLimiter(base_delay=1.0)
        rl.record_failure("cred-1")
        # Immediately after failure, should be blocked
        assert rl.is_allowed("cred-1") is False

    def test_allowed_after_backoff_expires(self) -> None:
        """After backoff period expires, attempts are allowed again."""
        from hypergumbo_tracker.serve_password import RateLimiter

        rl = RateLimiter(base_delay=0.5)
        rl.record_failure("cred-1")

        with patch("time.time", return_value=time.time() + 1.0):
            assert rl.is_allowed("cred-1") is True

    def test_exponential_backoff(self) -> None:
        """Consecutive failures increase backoff exponentially."""
        from hypergumbo_tracker.serve_password import RateLimiter

        rl = RateLimiter(base_delay=1.0)
        rl.record_failure("cred-1")  # backoff = 1s
        rl.record_failure("cred-1")  # backoff = 2s
        rl.record_failure("cred-1")  # backoff = 4s

        # After 3s, still blocked (need 4s)
        with patch("time.time", return_value=time.time() + 3.0):
            assert rl.is_allowed("cred-1") is False

        # After 5s, allowed
        with patch("time.time", return_value=time.time() + 5.0):
            assert rl.is_allowed("cred-1") is True

    def test_success_resets_backoff(self) -> None:
        """A successful attempt resets the failure counter."""
        from hypergumbo_tracker.serve_password import RateLimiter

        rl = RateLimiter(base_delay=1.0)
        rl.record_failure("cred-1")
        rl.record_failure("cred-1")
        rl.record_success("cred-1")
        # After success, no backoff
        assert rl.is_allowed("cred-1") is True

    def test_lockout_after_max_failures(self) -> None:
        """After max consecutive failures, credential is locked out."""
        from hypergumbo_tracker.serve_password import RateLimiter

        rl = RateLimiter(max_failures=3)
        rl.record_failure("cred-1")
        rl.record_failure("cred-1")
        rl.record_failure("cred-1")
        assert rl.is_locked("cred-1") is True

    def test_not_locked_below_max(self) -> None:
        """Below max failures, credential is not locked."""
        from hypergumbo_tracker.serve_password import RateLimiter

        rl = RateLimiter(max_failures=3)
        rl.record_failure("cred-1")
        rl.record_failure("cred-1")
        assert rl.is_locked("cred-1") is False

    def test_independent_credentials(self) -> None:
        """Rate limiting is per-credential, not global."""
        from hypergumbo_tracker.serve_password import RateLimiter

        rl = RateLimiter(base_delay=1.0)
        rl.record_failure("cred-1")
        assert rl.is_allowed("cred-1") is False
        assert rl.is_allowed("cred-2") is True

    def test_locked_credential_always_denied(self) -> None:
        """Locked credentials are denied even after backoff expires."""
        from hypergumbo_tracker.serve_password import RateLimiter

        rl = RateLimiter(max_failures=2, base_delay=0.1)
        rl.record_failure("cred-1")
        rl.record_failure("cred-1")
        assert rl.is_locked("cred-1") is True

        # Even far in the future, still locked
        with patch("time.time", return_value=time.time() + 3600):
            assert rl.is_allowed("cred-1") is False
