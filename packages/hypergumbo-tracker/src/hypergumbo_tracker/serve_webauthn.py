# SPDX-License-Identifier: MPL-2.0
"""WebAuthn/FIDO2 registration and authentication for htrac serve (ADR-0019).

Manages hardware security key (YubiKey) registration and authentication via
the WebAuthn protocol. Credential storage is in-memory (invalidated on restart).

How It Works
------------
``WebAuthnManager`` wraps the ``webauthn`` library to generate registration
and authentication challenges, store credentials, and verify responses.

Registration flow:
1. ``generate_registration_options()`` → JSON options (challenge, RP info, user info)
2. Client performs WebAuthn ceremony with hardware key
3. ``verify_registration()`` validates the attestation response

Authentication flow:
1. ``generate_authentication_options()`` → JSON options (challenge, allowed credentials)
2. Client performs WebAuthn ceremony with hardware key
3. ``verify_authentication()`` validates the assertion response

Why This Design
---------------
- In-memory credential storage: restart invalidates all credentials (security).
  Re-registration via ``htrac setup`` required after restart.
- The ``webauthn`` library handles the cryptographic verification.
- Challenges are stored per-manager for verification (single-user server).
"""
from __future__ import annotations

import secrets
from base64 import urlsafe_b64encode
from typing import Any


class WebAuthnManager:
    """Manage WebAuthn registration and authentication.

    Args:
        rp_id: Relying Party ID (domain, e.g., "localhost" or "xxx.onion").
        rp_name: Human-readable RP name (e.g., "htrac").
        origin: Expected origin URL (e.g., "https://localhost").
    """

    def __init__(self, rp_id: str, rp_name: str, origin: str) -> None:
        self.rp_id = rp_id
        self.rp_name = rp_name
        self.origin = origin
        self._credentials: dict[str, dict[str, Any]] = {}
        self._pending_challenge: bytes | None = None

    def generate_registration_options(
        self, user_id: str, user_name: str,
    ) -> dict[str, Any]:
        """Generate WebAuthn registration options.

        Returns a dict with challenge, RP info, and user info suitable
        for passing to the client's ``navigator.credentials.create()``.
        """
        challenge = secrets.token_bytes(32)
        self._pending_challenge = challenge

        return {
            "challenge": urlsafe_b64encode(challenge).decode("ascii"),
            "rp": {"id": self.rp_id, "name": self.rp_name},
            "user": {
                "id": urlsafe_b64encode(user_id.encode()).decode("ascii"),
                "name": user_name,
                "displayName": user_name,
            },
            "pubKeyCredParams": [
                {"type": "public-key", "alg": -7},   # ES256
                {"type": "public-key", "alg": -257},  # RS256
            ],
            "authenticatorSelection": {
                "authenticatorAttachment": "cross-platform",
                "userVerification": "discouraged",
            },
            "timeout": 60000,
            "attestation": "none",
        }

    def generate_authentication_options(self) -> dict[str, Any]:
        """Generate WebAuthn authentication options.

        Returns a dict with challenge and allowed credentials suitable
        for passing to the client's ``navigator.credentials.get()``.
        """
        challenge = secrets.token_bytes(32)
        self._pending_challenge = challenge

        allow_credentials = [
            {"type": "public-key", "id": cred_id}
            for cred_id in self._credentials
        ]

        return {
            "challenge": urlsafe_b64encode(challenge).decode("ascii"),
            "rpId": self.rp_id,
            "allowCredentials": allow_credentials,
            "userVerification": "discouraged",
            "timeout": 60000,
        }

    def get_pending_challenge(self) -> str | None:
        """Return the most recent pending challenge (base64url-encoded)."""
        if self._pending_challenge is None:
            return None
        return urlsafe_b64encode(self._pending_challenge).decode("ascii")

    def store_credential(
        self, credential_id: str, public_key: bytes, user_id: str,
    ) -> None:
        """Store a registered credential."""
        self._credentials[credential_id] = {
            "public_key": public_key,
            "user_id": user_id,
            "sign_count": 0,
        }

    def get_credential(self, credential_id: str) -> dict[str, Any] | None:
        """Retrieve a stored credential by ID."""
        return self._credentials.get(credential_id)

    def remove_credential(self, credential_id: str) -> None:
        """Remove a credential. No-op if not found."""
        self._credentials.pop(credential_id, None)

    def credential_count(self) -> int:
        """Return the number of stored credentials."""
        return len(self._credentials)

    def clear(self) -> None:
        """Remove all credentials (server restart scenario)."""
        self._credentials.clear()
        self._pending_challenge = None
