# SPDX-License-Identifier: MPL-2.0
"""Tests for htrac serve auth config schema parsing and validation."""
from __future__ import annotations

import pytest


class TestAuthConfig:
    """Tests for AuthConfig dataclass and parsing."""

    def test_default_values(self) -> None:
        """AuthConfig has sensible defaults."""
        from hypergumbo_tracker.serve_auth_config import AuthConfig

        cfg = AuthConfig()
        assert cfg.session_ttl_minutes == 15
        assert cfg.duress_module is None
        assert cfg.rate_limit_base_delay == 2.0
        assert cfg.rate_limit_max_failures == 10
        assert cfg.webauthn_rp_id == "localhost"
        assert cfg.webauthn_rp_name == "htrac"
        assert cfg.webauthn_origin == "https://localhost"

    def test_from_dict_full(self) -> None:
        """AuthConfig.from_dict parses all fields."""
        from hypergumbo_tracker.serve_auth_config import AuthConfig

        raw = {
            "session_ttl_minutes": 30,
            "duress_module": "/path/to/duress.py",
            "rate_limit": {
                "base_delay": 5.0,
                "max_failures": 3,
            },
            "webauthn": {
                "rp_id": "example.onion",
                "rp_name": "My Tracker",
                "origin": "https://example.onion",
            },
        }
        cfg = AuthConfig.from_dict(raw)
        assert cfg.session_ttl_minutes == 30
        assert cfg.duress_module == "/path/to/duress.py"
        assert cfg.rate_limit_base_delay == 5.0
        assert cfg.rate_limit_max_failures == 3
        assert cfg.webauthn_rp_id == "example.onion"
        assert cfg.webauthn_rp_name == "My Tracker"
        assert cfg.webauthn_origin == "https://example.onion"

    def test_from_dict_empty(self) -> None:
        """AuthConfig.from_dict with empty dict uses defaults."""
        from hypergumbo_tracker.serve_auth_config import AuthConfig

        cfg = AuthConfig.from_dict({})
        assert cfg.session_ttl_minutes == 15
        assert cfg.duress_module is None

    def test_from_dict_partial(self) -> None:
        """AuthConfig.from_dict with partial dict fills missing with defaults."""
        from hypergumbo_tracker.serve_auth_config import AuthConfig

        cfg = AuthConfig.from_dict({"session_ttl_minutes": 60})
        assert cfg.session_ttl_minutes == 60
        assert cfg.rate_limit_base_delay == 2.0

    def test_from_dict_none(self) -> None:
        """AuthConfig.from_dict with None uses defaults."""
        from hypergumbo_tracker.serve_auth_config import AuthConfig

        cfg = AuthConfig.from_dict(None)
        assert cfg.session_ttl_minutes == 15

    def test_from_dict_invalid_rate_limit_type(self) -> None:
        """Non-dict rate_limit is treated as empty."""
        from hypergumbo_tracker.serve_auth_config import AuthConfig

        cfg = AuthConfig.from_dict({"rate_limit": "not-a-dict"})
        assert cfg.rate_limit_base_delay == 2.0

    def test_from_dict_invalid_webauthn_type(self) -> None:
        """Non-dict webauthn is treated as empty."""
        from hypergumbo_tracker.serve_auth_config import AuthConfig

        cfg = AuthConfig.from_dict({"webauthn": 42})
        assert cfg.webauthn_rp_id == "localhost"

    def test_validate_ttl_positive(self) -> None:
        """session_ttl_minutes must be positive."""
        from hypergumbo_tracker.serve_auth_config import AuthConfig

        with pytest.raises(ValueError, match="session_ttl_minutes"):
            AuthConfig(session_ttl_minutes=0)

    def test_validate_base_delay_positive(self) -> None:
        """rate_limit_base_delay must be positive."""
        from hypergumbo_tracker.serve_auth_config import AuthConfig

        with pytest.raises(ValueError, match="rate_limit_base_delay"):
            AuthConfig(rate_limit_base_delay=-1.0)

    def test_validate_max_failures_nonnegative(self) -> None:
        """rate_limit_max_failures must be non-negative."""
        from hypergumbo_tracker.serve_auth_config import AuthConfig

        with pytest.raises(ValueError, match="rate_limit_max_failures"):
            AuthConfig(rate_limit_max_failures=-1)

    def test_to_dict(self) -> None:
        """AuthConfig.to_dict round-trips through from_dict."""
        from hypergumbo_tracker.serve_auth_config import AuthConfig

        cfg = AuthConfig(session_ttl_minutes=30, duress_module="/x.py")
        d = cfg.to_dict()
        cfg2 = AuthConfig.from_dict(d)
        assert cfg2.session_ttl_minutes == 30
        assert cfg2.duress_module == "/x.py"
