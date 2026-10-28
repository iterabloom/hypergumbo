# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every ``framework_role`` a linker writes is either attached to its enclosing
function or set aside for a stated reason (WI-pokuz).

``SYNTHETIC_FRAMEWORK_ROLES`` decides which linker nodes the enclosure pass
connects to the real function they sit in, and it was a hand-written list that
drifted from what the linkers write. Measured on dev before this change: the
Electron and Phoenix IPC linkers write ``ipc_send`` / ``ipc_receive`` (the list
said ``ipc_publisher`` / ``ipc_subscriber``), so session-desktop's 43 and
livebook's 31 IPC nodes had no edge from any real symbol. The same held for
every other call-site role the list missed (``message_sender`` /
``message_handler``, ``crypto_*``, ``grpc_client``, ``graphql_client``, the two
bridge callers), and for ``http_client``, which the list named but which is
minted as ``kind="call_site"``, a kind the predicate never admitted. Six listed
values had no producer at all.

THE PARTITION IS READ FROM THE LINKER SOURCES, so a role a linker starts to
write fails here until someone decides which side it is on.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers import ipc as ipc_mod
from hypergumbo_core.linkers import registry
from hypergumbo_core.linkers.grpc import _PATTERN_FOLDS
from hypergumbo_core.linkers.registry import (
    NOT_ENCLOSED_FRAMEWORK_ROLES,
    SYNTHETIC_FRAMEWORK_ROLES,
    LinkerContext,
    _connect_synthetic_to_enclosing,
    _is_synthetic_node,
)

_LINKERS = Path(registry.__file__).parent

# The writes whose value is not a string literal, each resolved by a table the
# partition below reads. A new one fails the test until it is listed here.
_NON_LITERAL_WRITES = {
    ("event_sourcing.py", "framework_role"),
    ("grpc.py", "framework_role"),
    ("ipc.py", "IPC_FRAMEWORK_ROLES[pattern.type]"),
    ("message_queue.py", "framework_role"),
    ("phoenix_ipc.py", "IPC_FRAMEWORK_ROLES[pattern.type]"),
}


def _written_roles() -> tuple[set[str], set[tuple[str, str]]]:
    """Every role value the linker sources write, and the non-literal sites."""
    roles: set[str] = set()
    non_literal: set[tuple[str, str]] = set()
    for path in sorted(_LINKERS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if isinstance(key, ast.Constant) and key.value == "framework_role":
                        if isinstance(value, ast.Constant):
                            roles.add(value.value)
                        else:
                            non_literal.add((path.name, ast.unparse(value)))
            # ``framework_role = "a" if ... else "b"`` (the value branches,
            # never the test, whose constants are pattern types).
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "framework_role"
                for t in node.targets
            ):
                value = node.value
                branches = [value.body, value.orelse] if isinstance(value, ast.IfExp) else [value]
                for branch in branches:
                    if isinstance(branch, ast.Constant):
                        roles.add(branch.value)
    roles |= {role for role, _kind in _PATTERN_FOLDS.values()}
    roles |= set(ipc_mod.IPC_FRAMEWORK_ROLES.values())
    return roles, non_literal


def test_every_non_literal_write_is_resolved_by_a_known_table() -> None:
    _roles, non_literal = _written_roles()
    assert non_literal == _NON_LITERAL_WRITES


def test_the_two_sets_partition_what_the_linkers_write() -> None:
    roles, _ = _written_roles()
    assert not SYNTHETIC_FRAMEWORK_ROLES & set(NOT_ENCLOSED_FRAMEWORK_ROLES)
    # No value nobody writes (six of them did), and none left undecided.
    assert SYNTHETIC_FRAMEWORK_ROLES | set(NOT_ENCLOSED_FRAMEWORK_ROLES) == roles


def test_every_set_aside_role_says_why() -> None:
    assert all(reason.strip() for reason in NOT_ENCLOSED_FRAMEWORK_ROLES.values())


def test_phoenix_shares_the_electron_table() -> None:
    from hypergumbo_core.linkers import phoenix_ipc

    assert phoenix_ipc.IPC_FRAMEWORK_ROLES is ipc_mod.IPC_FRAMEWORK_ROLES


def _stand_in(kind: str, role: str | None) -> Symbol:
    return Symbol(
        id=f"x:a.js:3-3:{kind}:{role}", name="s", kind=kind, language=None,
        path="a.js", span=Span(3, 3, 0, 0), origin="some-linker",
        origin_run_id="r", meta={"framework_role": role} if role else {},
    )


@pytest.mark.parametrize("kind, role, expected", [
    ("function", "ipc_send", True),
    ("function", "message_sender", True),
    ("call_site", "http_client", True),
    # A call-site node is a stand-in whatever its call_kind (db, subprocess).
    ("call_site", None, True),
    ("function", "openapi_operation", False),
    ("class", "grpc_server", False),
    ("variable", None, False),
])
def test_the_predicate(kind: str, role: str | None, expected: bool) -> None:
    assert _is_synthetic_node(_stand_in(kind, role)) is expected


# ---------------------------------------------------------------------------
# The production linkers, through the production enclosure function.
# ---------------------------------------------------------------------------

def _function(path: str, start: int, end: int, language: str) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:{start}-{end}:handler:function", name="handler",
        kind="function", language=language, path=path,
        span=Span(start, end, 0, 0), origin=language, origin_run_id="r",
    )


def _enclosed(ctx: LinkerContext, linker_symbols: list[Symbol]) -> set[tuple[str, str]]:
    edges = _connect_synthetic_to_enclosing(
        ctx, linker_symbols, pass_id="enclosure-test", run_id="r",
    )
    by_id = {s.id: s for s in linker_symbols}
    return {(e.src, by_id[e.dst].meta["framework_role"]) for e in edges}


def test_an_electron_send_site_is_attached_to_its_function(tmp_path: Path) -> None:
    (tmp_path / "renderer.js").write_text(
        "const { ipcRenderer } = require('electron');\n"
        "function handler() {\n"
        "  ipcRenderer.send('save', 1);\n"
        "}\n"
    )
    (tmp_path / "main.js").write_text(
        "const { ipcMain } = require('electron');\n"
        "ipcMain.on('save', () => {});\n"
    )
    func = _function("renderer.js", 2, 4, "javascript")
    ctx = LinkerContext(repo_root=tmp_path, symbols=[func])
    result = ipc_mod.ipc_linker(ctx)
    assert {s.meta["framework_role"] for s in result.symbols} == {"ipc_send", "ipc_receive"}
    assert _enclosed(ctx, result.symbols) == {(func.id, "ipc_send")}


def test_a_phoenix_broadcast_is_attached_to_its_function(tmp_path: Path) -> None:
    from hypergumbo_core.linkers.phoenix_ipc import phoenix_ipc_linker

    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "room.ex").write_text(
        "defmodule Room do\n"
        "  def handler(socket) do\n"
        "    broadcast!(socket, \"ping\", %{})\n"
        "  end\n"
        "  def handle_in(\"ping\", payload, socket) do\n"
        "    {:noreply, socket}\n"
        "  end\n"
        "end\n"
    )
    func = _function("lib/room.ex", 2, 4, "elixir")
    ctx = LinkerContext(repo_root=tmp_path, symbols=[func])
    result = phoenix_ipc_linker(ctx)
    assert "ipc_send" in {s.meta["framework_role"] for s in result.symbols}
    assert (func.id, "ipc_send") in _enclosed(ctx, result.symbols)
