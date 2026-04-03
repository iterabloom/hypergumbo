# SPDX-License-Identifier: MPL-2.0
"""DuressHandler Protocol for htrac serve (ADR-0019).

Defines the interface for user-implemented duress behavior. The handler is
loaded at server startup from a Python file specified in config.yaml under
``auth.duress_module``. The file must define a module-level ``handler`` variable
that satisfies the ``DuressHandler`` Protocol.

How It Works
------------
``DuressHandler`` is a ``typing.Protocol`` with two methods:

1. ``on_duress_login(session, context)`` — async, called once when a duress
   session is created (after password verification). Can perform actions like
   alerting, logging to an external system, or wiping sensitive data.

2. ``filter_response(session, response)`` — sync, called on every API response
   during a duress session. Can redact, modify, or fabricate data to deceive
   the attacker.

``NullDuressHandler`` is the default (no-op) used when no custom handler is
configured. ``load_duress_handler()`` loads a handler from a file path.
``call_on_duress_login()`` wraps the async call with timeout enforcement.

Why This Design
---------------
- Deliberately underspecified: the attacker cannot predict duress behavior
  because it's user-defined, not shipped with the software.
- The handler file is gitignored and not tracked — it exists only on the
  server machine.
- Timeout enforcement prevents a slow handler from creating a timing
  side-channel (duress login taking longer than normal login).
- Protocol (structural typing) rather than ABC: the handler doesn't need
  to import anything from hypergumbo.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DuressHandler(Protocol):
    """Protocol for user-implemented duress session behavior.

    Implementations MUST define both methods. The handler file is loaded
    from a path in config.yaml and must expose a module-level ``handler``
    variable satisfying this Protocol.
    """

    async def on_duress_login(
        self, session: dict[str, Any], context: dict[str, Any],
    ) -> None:
        """Called once when a duress session is created.

        Args:
            session: Session metadata (token, auth_class, created_at, etc.).
            context: Request context (client IP hash, User-Agent, etc.).
        """
        ...  # pragma: no cover

    def filter_response(
        self, session: dict[str, Any], response: dict[str, Any],
    ) -> dict[str, Any]:
        """Called on every API response during a duress session.

        Args:
            session: Session metadata.
            response: The response dict about to be sent to the client.

        Returns:
            The (potentially modified) response dict.
        """
        ...  # pragma: no cover


class NullDuressHandler:
    """Default no-op duress handler. Used when no custom handler is configured."""

    async def on_duress_login(
        self, session: dict[str, Any], context: dict[str, Any],
    ) -> None:
        """No-op: does nothing on duress login."""

    def filter_response(
        self, session: dict[str, Any], response: dict[str, Any],
    ) -> dict[str, Any]:
        """No-op: returns the response unchanged."""
        return response


def load_duress_handler(module_path: str | None) -> DuressHandler:
    """Load a DuressHandler from a Python file path.

    The file must define a module-level ``handler`` variable that satisfies
    the DuressHandler Protocol.

    Args:
        module_path: Path to the Python file, or None/empty for default.

    Returns:
        The loaded handler, or NullDuressHandler on any error.
    """
    if not module_path:
        return NullDuressHandler()

    try:
        spec = importlib.util.spec_from_file_location("_duress_handler", module_path)
        if spec is None or spec.loader is None:
            return NullDuressHandler()
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_duress_handler"] = mod
        spec.loader.exec_module(mod)
        handler = getattr(mod, "handler", None)
        if handler is None:
            return NullDuressHandler()
        return handler
    except Exception:
        return NullDuressHandler()
    finally:
        sys.modules.pop("_duress_handler", None)


async def call_on_duress_login(
    handler: DuressHandler,
    session: dict[str, Any],
    context: dict[str, Any],
    *,
    timeout: float = 5.0,
) -> bool:
    """Call handler.on_duress_login with timeout enforcement.

    Returns True if the call completed within timeout, False if timed out.
    Timeout prevents timing side-channels (duress login taking noticeably
    longer than normal login).
    """
    try:
        await asyncio.wait_for(
            handler.on_duress_login(session, context),
            timeout=timeout,
        )
        return True
    except asyncio.TimeoutError:
        return False
