# SPDX-License-Identifier: MPL-2.0
"""In-memory session management for htrac serve (ADR-0019).

Provides opaque cryptographically random session tokens stored server-side.
Sessions have a fixed-window TTL (default 15 minutes) — interaction does NOT
reset the timer. Each session records creation time, expiry, auth class
(normal or duress), and an optional client fingerprint.

How It Works
------------
``SessionStore`` is an in-memory dict mapping tokens to ``Session`` dataclass
instances. ``create_session()`` generates a 32-byte URL-safe random token via
``secrets.token_urlsafe(32)``. ``validate()`` checks existence and expiry.
``revoke()`` removes a single session. ``clear()`` invalidates all sessions
(called on server restart — no persistent session storage by design).

Why This Design
---------------
- In-memory only: crash/restart invalidates all sessions. This is a security
  feature — if the server is compromised, restarting it revokes all access.
- Fixed-window TTL: prevents session fixation from extending indefinitely.
- Auth class (normal/duress): duress sessions are tagged so the DuressHandler
  can filter responses. The session store itself doesn't know what duress means.
- Client fingerprint: optional metadata for audit logging (User-Agent, IP hash).
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass


@dataclass
class Session:
    """A single authenticated session."""

    token: str
    auth_class: str  # "normal" or "duress"
    created_at: float
    expires_at: float
    fingerprint: str | None = None


class SessionStore:
    """In-memory session store with TTL-based expiry.

    Args:
        ttl_seconds: Session lifetime in seconds (default: 900 = 15 minutes).
            Fixed window — interaction does NOT reset the timer.
    """

    def __init__(self, ttl_seconds: int = 900) -> None:
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, Session] = {}

    def create_session(
        self,
        auth_class: str,
        fingerprint: str | None = None,
    ) -> str:
        """Create a new session and return the opaque token.

        Args:
            auth_class: "normal" or "duress".
            fingerprint: Optional client fingerprint for audit logging.

        Returns:
            A cryptographically random URL-safe token string (43 chars).
        """
        token = secrets.token_urlsafe(32)
        now = time.time()
        self._sessions[token] = Session(
            token=token,
            auth_class=auth_class,
            created_at=now,
            expires_at=now + self.ttl_seconds,
            fingerprint=fingerprint,
        )
        return token

    def validate(self, token: str) -> Session | None:
        """Validate a session token.

        Returns the Session if valid and not expired, else None.
        """
        session = self._sessions.get(token)
        if session is None:
            return None
        if time.time() > session.expires_at:
            del self._sessions[token]
            return None
        return session

    def revoke(self, token: str) -> None:
        """Revoke a single session by token. No-op if not found."""
        self._sessions.pop(token, None)

    def clear(self) -> None:
        """Invalidate all sessions (server restart scenario)."""
        self._sessions.clear()

    def active_count(self) -> int:
        """Return the number of non-expired sessions."""
        now = time.time()
        return sum(1 for s in self._sessions.values() if s.expires_at > now)

    def cleanup_expired(self) -> None:
        """Remove expired sessions from memory."""
        now = time.time()
        expired = [t for t, s in self._sessions.items() if s.expires_at <= now]
        for t in expired:
            del self._sessions[t]
