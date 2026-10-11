# SPDX-License-Identifier: MPL-2.0
"""Auth config schema for htrac serve (ADR-0019).

Schema for an ``auth`` section of config.yaml: session TTL, duress module
path, rate limiting parameters, and WebAuthn relying party configuration.

Not yet consumed as of 2026-10: ``load_config``/``_parse_config_dict`` in
models.py ignore ``auth`` and serve.py never constructs an AuthConfig (only
tests call ``from_dict``), so validation runs only when a caller invokes
``from_dict``.

How It Works
------------
``AuthConfig`` is a validated dataclass parsed from a raw ``auth`` dict via
``from_dict()``. Validation runs in ``__post_init__``, so configuration
errors surface at construction time rather than at request time.

Why This Design
---------------
- Dataclass with ``from_dict()`` / ``to_dict()`` round-tripping (unlike
  TrackerConfig, which is built by the module-level
  ``models._parse_config_dict``).
- Validation at construction time prevents runtime surprises.
- Defaults are conservative: 15-minute TTL, 2-second base delay, 10 max
  failures before lockout.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AuthConfig:
    """Authentication configuration for htrac serve.

    Parsed from the ``auth`` section of config.yaml.
    """

    session_ttl_minutes: int = 15
    duress_module: str | None = None
    rate_limit_base_delay: float = 2.0
    rate_limit_max_failures: int = 10
    webauthn_rp_id: str = "localhost"
    webauthn_rp_name: str = "htrac"
    webauthn_origin: str = "https://localhost"

    def __post_init__(self) -> None:
        if self.session_ttl_minutes <= 0:
            raise ValueError("session_ttl_minutes must be positive")
        if self.rate_limit_base_delay < 0:
            raise ValueError("rate_limit_base_delay must be non-negative")
        if self.rate_limit_max_failures < 0:
            raise ValueError("rate_limit_max_failures must be non-negative")

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> AuthConfig:
        """Parse an AuthConfig from a raw dict (the ``auth`` section of config.yaml).

        Missing fields use defaults. None input uses all defaults.
        """
        if not raw:
            return cls()

        rate_limit = raw.get("rate_limit", {})
        if not isinstance(rate_limit, dict):
            rate_limit = {}

        webauthn = raw.get("webauthn", {})
        if not isinstance(webauthn, dict):
            webauthn = {}

        return cls(
            session_ttl_minutes=raw.get("session_ttl_minutes", 15),
            duress_module=raw.get("duress_module"),
            rate_limit_base_delay=rate_limit.get("base_delay", 2.0),
            rate_limit_max_failures=rate_limit.get("max_failures", 10),
            webauthn_rp_id=webauthn.get("rp_id", "localhost"),
            webauthn_rp_name=webauthn.get("rp_name", "htrac"),
            webauthn_origin=webauthn.get("origin", "https://localhost"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for config.yaml."""
        return {
            "session_ttl_minutes": self.session_ttl_minutes,
            "duress_module": self.duress_module,
            "rate_limit": {
                "base_delay": self.rate_limit_base_delay,
                "max_failures": self.rate_limit_max_failures,
            },
            "webauthn": {
                "rp_id": self.webauthn_rp_id,
                "rp_name": self.webauthn_rp_name,
                "origin": self.webauthn_origin,
            },
        }
