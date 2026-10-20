# SPDX-License-Identifier: MPL-2.0
"""DuressHandler Protocol for htrac serve (ADR-0019).

Defines the interface for user-implemented duress behavior (the ADR-0019
duress contract). By design the handler is loaded from a Python file named in
config.yaml under ``auth.duress_module``; the file must define a module-level
``handler`` variable that satisfies the ``DuressHandler`` Protocol.

Not yet wired as of 2026-10 (WI-hopip): nothing loads the handler at server
startup or calls these hooks, and config.yaml's ``auth.duress_module`` is not
read by ``load_config``. The "called once" / "called on every API response"
descriptions below are the contract a future caller must honour.

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

Both fail quiet toward the client and loud toward the operator (WI-hopip).
A handler that raises in ``on_duress_login`` is reported as not completed
(``False``) rather than propagated: a raise would fail the duress login where
a normal login succeeds, telling the attacker which password they typed. A
configured handler file that cannot be loaded (missing, not Python, raising on
import, no ``handler``) still falls back to ``NullDuressHandler`` so the server
starts, but with a ``UserWarning`` naming the file and the reason -- silently,
the operator would believe duress was armed when it was a no-op. No path
configured is the normal case and is silent.

Why This Design
---------------
- Deliberately underspecified: the attacker cannot predict duress behavior
  because it's user-defined, not shipped with the software.
- The handler file should be kept out of version control by the operator
  (the repo does not gitignore it; its path is user-chosen) so it exists
  only on the server machine.
- Timeout enforcement caps a slow handler at ``timeout`` seconds (default
  5.0). It bounds, but does not eliminate, the timing difference between
  duress and normal logins.
- Protocol (structural typing) rather than ABC: the handler doesn't need
  to import anything from hypergumbo.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import warnings
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
        The loaded handler, or NullDuressHandler on any error -- with a
        ``UserWarning`` when a path was given.
    """
    if not module_path:
        return NullDuressHandler()

    try:
        spec = importlib.util.spec_from_file_location("_duress_handler", module_path)
        if spec is None or spec.loader is None:
            return _not_loaded(module_path, "not a Python file")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_duress_handler"] = mod
        spec.loader.exec_module(mod)
        handler = getattr(mod, "handler", None)
        if handler is None:
            return _not_loaded(module_path, "no module-level 'handler'")
        return handler
    except Exception as exc:
        return _not_loaded(module_path, f"{type(exc).__name__}: {exc}")
    finally:
        sys.modules.pop("_duress_handler", None)


def _not_loaded(module_path: str, reason: str) -> NullDuressHandler:
    warnings.warn(
        f"duress handler {module_path} not loaded ({reason}); "
        "duress logins will do nothing",
        UserWarning,
        stacklevel=3,
    )
    return NullDuressHandler()


async def call_on_duress_login(
    handler: DuressHandler,
    session: dict[str, Any],
    context: dict[str, Any],
    *,
    timeout: float = 5.0,
) -> bool:
    """Call handler.on_duress_login with timeout enforcement.

    Returns True if the call completed within timeout, False if it timed
    out or raised (a raise must not reach the client; see the module docstring).
    The timeout bounds, but does not eliminate, the timing difference
    between duress and normal logins (normal logins are not padded).
    """
    try:
        await asyncio.wait_for(
            handler.on_duress_login(session, context),
            timeout=timeout,
        )
        return True
    except Exception:  # asyncio.TimeoutError included
        return False
