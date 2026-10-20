# SPDX-License-Identifier: MPL-2.0
"""An authenticator that accepts everything, for tests of what lies behind the gate.

``create_app`` denies every route but ``/health`` without an authenticator
(WI-hopip); the gate itself is tested in ``test_serve_auth_gate.py``.
"""
from __future__ import annotations

from starlette.requests import HTTPConnection


def allow_all(conn: HTTPConnection) -> bool:
    return True
