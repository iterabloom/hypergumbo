# SPDX-License-Identifier: MPL-2.0
"""Bcrypt password verification for htrac serve (ADR-0019).

Two passwords: real (creates normal session) and duress (creates session tagged
as duress, indistinguishable token). Rate limiting per WebAuthn credential with
exponential backoff. Configurable max failures before credential lockout.

How It Works
------------
``PasswordVerifier`` stores bcrypt hashes of the real and duress passwords.
``verify()`` checks the input against both hashes and returns ``"normal"``,
``"duress"``, or ``None``. Both hashes are always checked to prevent timing
side-channels that would reveal which password is real vs duress.

``RateLimiter`` tracks per-credential failure counts and timestamps. After each
failure, the next attempt is delayed by ``base_delay * 2^(failures-1)`` seconds
(exponential backoff). After ``max_failures`` consecutive failures, the credential
is locked and permanently denied until re-registration via ``htrac setup``.

Why This Design
---------------
- bcrypt is intentionally slow (cost factor 12 by default), making brute force
  infeasible even with fast hardware.
- Both passwords checked every time prevents timing attacks that distinguish
  real from duress.
- Exponential backoff slows automated attacks without permanent lockout for
  occasional typos.
- Credential lockout after max failures is the final defense — requires physical
  YubiKey re-registration to unlock.
"""
from __future__ import annotations

import time

import bcrypt


class PasswordVerifier:
    """Verify passwords against stored bcrypt hashes.

    Stores hashes of both real and duress passwords. Always checks both
    to prevent timing side-channels.
    """

    def __init__(self, real_hash: str, duress_hash: str) -> None:
        self._real_hash = real_hash.encode("utf-8")
        self._duress_hash = duress_hash.encode("utf-8")

    @classmethod
    def from_plaintext(cls, real: str, duress: str) -> PasswordVerifier:
        """Create a verifier by hashing plaintext passwords.

        Convenience method for initial setup. In production, hashes are
        stored in config and loaded directly via __init__.
        """
        real_hash = bcrypt.hashpw(real.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        duress_hash = bcrypt.hashpw(duress.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        return cls(real_hash=real_hash, duress_hash=duress_hash)

    def verify(self, password: str) -> str | None:
        """Verify a password against both real and duress hashes.

        Returns:
            ``"normal"`` if real password matches,
            ``"duress"`` if duress password matches,
            ``None`` if neither matches.

        Both hashes are always checked to prevent timing side-channels.
        """
        pw = password.encode("utf-8")
        real_ok = bcrypt.checkpw(pw, self._real_hash)
        duress_ok = bcrypt.checkpw(pw, self._duress_hash)

        if real_ok:
            return "normal"
        if duress_ok:
            return "duress"
        return None


class RateLimiter:
    """Per-credential rate limiter with exponential backoff and lockout.

    Args:
        base_delay: Initial backoff delay in seconds after first failure.
        max_failures: Lock credential after this many consecutive failures.
            0 means no lockout (backoff only).
    """

    def __init__(
        self,
        base_delay: float = 2.0,
        max_failures: int = 10,
    ) -> None:
        self.base_delay = base_delay
        self.max_failures = max_failures
        self._state: dict[str, _CredentialState] = {}

    def is_allowed(self, credential_id: str) -> bool:
        """Check if an attempt is allowed for this credential."""
        state = self._state.get(credential_id)
        if state is None:
            return True
        if state.locked:
            return False
        if state.failures == 0:
            return True
        # Check if backoff has expired
        delay = self.base_delay * (2 ** (state.failures - 1))
        return time.time() >= state.last_failure_at + delay

    def is_locked(self, credential_id: str) -> bool:
        """Check if a credential is permanently locked."""
        state = self._state.get(credential_id)
        return state is not None and state.locked

    def record_failure(self, credential_id: str) -> None:
        """Record a failed attempt for this credential."""
        state = self._state.get(credential_id)
        if state is None:
            state = _CredentialState()
            self._state[credential_id] = state
        state.failures += 1
        state.last_failure_at = time.time()
        if self.max_failures > 0 and state.failures >= self.max_failures:
            state.locked = True

    def record_success(self, credential_id: str) -> None:
        """Record a successful attempt — resets failure counter."""
        state = self._state.get(credential_id)
        if state is not None:
            state.failures = 0
            state.locked = False


class _CredentialState:
    """Internal state for a single credential."""

    __slots__ = ("failures", "last_failure_at", "locked")

    def __init__(self) -> None:
        self.failures: int = 0
        self.last_failure_at: float = 0.0
        self.locked: bool = False
