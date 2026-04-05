# SPDX-License-Identifier: MPL-2.0
"""Tests for htrac serve WebAuthn/FIDO2 registration and authentication.

WebAuthn provides passwordless authentication via hardware security keys
(YubiKey). The server generates challenges and verifies attestation/assertion
responses. Credential storage is in-memory (invalidated on restart).
"""
from __future__ import annotations

import pytest


class TestWebAuthnManager:
    """Tests for WebAuthnManager challenge generation and credential storage."""

    def test_generate_registration_options(self) -> None:
        """Generates registration options with challenge."""
        from hypergumbo_tracker.serve_webauthn import WebAuthnManager

        mgr = WebAuthnManager(rp_id="localhost", rp_name="htrac", origin="https://localhost")
        options = mgr.generate_registration_options(user_id="user1", user_name="admin")
        assert "challenge" in options
        assert options["rp"]["id"] == "localhost"
        assert options["rp"]["name"] == "htrac"
        assert options["user"]["name"] == "admin"

    def test_generate_authentication_options(self) -> None:
        """Generates authentication options with challenge."""
        from hypergumbo_tracker.serve_webauthn import WebAuthnManager

        mgr = WebAuthnManager(rp_id="localhost", rp_name="htrac", origin="https://localhost")
        options = mgr.generate_authentication_options()
        assert "challenge" in options

    def test_store_and_list_credentials(self) -> None:
        """Can store and list registered credentials."""
        from hypergumbo_tracker.serve_webauthn import WebAuthnManager

        mgr = WebAuthnManager(rp_id="localhost", rp_name="htrac", origin="https://localhost")
        assert mgr.credential_count() == 0

        mgr.store_credential("cred-1", b"pubkey-data", user_id="user1")
        assert mgr.credential_count() == 1

        mgr.store_credential("cred-2", b"pubkey-data-2", user_id="user1")
        assert mgr.credential_count() == 2

    def test_get_credential(self) -> None:
        """Can retrieve a stored credential by ID."""
        from hypergumbo_tracker.serve_webauthn import WebAuthnManager

        mgr = WebAuthnManager(rp_id="localhost", rp_name="htrac", origin="https://localhost")
        mgr.store_credential("cred-1", b"pubkey", user_id="user1")

        cred = mgr.get_credential("cred-1")
        assert cred is not None
        assert cred["public_key"] == b"pubkey"
        assert cred["user_id"] == "user1"

    def test_get_nonexistent_credential(self) -> None:
        """Returns None for unknown credential ID."""
        from hypergumbo_tracker.serve_webauthn import WebAuthnManager

        mgr = WebAuthnManager(rp_id="localhost", rp_name="htrac", origin="https://localhost")
        assert mgr.get_credential("nonexistent") is None

    def test_remove_credential(self) -> None:
        """Can remove a credential."""
        from hypergumbo_tracker.serve_webauthn import WebAuthnManager

        mgr = WebAuthnManager(rp_id="localhost", rp_name="htrac", origin="https://localhost")
        mgr.store_credential("cred-1", b"pubkey", user_id="user1")
        assert mgr.credential_count() == 1

        mgr.remove_credential("cred-1")
        assert mgr.credential_count() == 0

    def test_remove_nonexistent_is_noop(self) -> None:
        """Removing nonexistent credential doesn't raise."""
        from hypergumbo_tracker.serve_webauthn import WebAuthnManager

        mgr = WebAuthnManager(rp_id="localhost", rp_name="htrac", origin="https://localhost")
        mgr.remove_credential("nonexistent")  # Should not raise

    def test_clear_credentials(self) -> None:
        """clear() removes all credentials (restart scenario)."""
        from hypergumbo_tracker.serve_webauthn import WebAuthnManager

        mgr = WebAuthnManager(rp_id="localhost", rp_name="htrac", origin="https://localhost")
        mgr.store_credential("c1", b"pk1", user_id="u1")
        mgr.store_credential("c2", b"pk2", user_id="u2")
        mgr.clear()
        assert mgr.credential_count() == 0

    def test_challenge_is_unique(self) -> None:
        """Each registration generates a unique challenge."""
        from hypergumbo_tracker.serve_webauthn import WebAuthnManager

        mgr = WebAuthnManager(rp_id="localhost", rp_name="htrac", origin="https://localhost")
        opt1 = mgr.generate_registration_options(user_id="u1", user_name="admin")
        opt2 = mgr.generate_registration_options(user_id="u1", user_name="admin")
        assert opt1["challenge"] != opt2["challenge"]

    def test_no_pending_challenge_initially(self) -> None:
        """No pending challenge before any generation."""
        from hypergumbo_tracker.serve_webauthn import WebAuthnManager

        mgr = WebAuthnManager(rp_id="localhost", rp_name="htrac", origin="https://localhost")
        assert mgr.get_pending_challenge() is None

    def test_pending_challenge_stored(self) -> None:
        """Registration challenge is stored for later verification."""
        from hypergumbo_tracker.serve_webauthn import WebAuthnManager

        mgr = WebAuthnManager(rp_id="localhost", rp_name="htrac", origin="https://localhost")
        options = mgr.generate_registration_options(user_id="u1", user_name="admin")
        assert mgr.get_pending_challenge() == options["challenge"]

    def test_auth_challenge_stored(self) -> None:
        """Authentication challenge is stored for later verification."""
        from hypergumbo_tracker.serve_webauthn import WebAuthnManager

        mgr = WebAuthnManager(rp_id="localhost", rp_name="htrac", origin="https://localhost")
        options = mgr.generate_authentication_options()
        assert mgr.get_pending_challenge() == options["challenge"]
