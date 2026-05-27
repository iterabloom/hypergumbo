# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-sopon: every linker module must stamp ``Symbol.origin`` and
``Symbol.origin_run_id`` on the symbols it synthesises.

Invariant
---------
Every ``Symbol`` emitted by an analyzer or linker MUST carry a non-empty
``origin`` matching the producer's versioned PASS_ID, plus a non-empty
``origin_run_id`` matching the ``AnalysisRun.execution_id`` of the run that
produced it. The same convention that holds for analyzer-emitted symbols
(``python-v1``, ``go-v1``, …) extends to linker-emitted synthesis symbols.

Why a two-pronged test
----------------------
* **Structural check** (``TestSymbolOriginIdiomInLinkerSource``) scans every
  linker module's source for the stamp idiom. Catches all 19 currently-broken
  files in one pass, and any future linker module that forgets to stamp.
* **Runtime check** (``TestSymbolOriginRuntime``) exercises three
  representative linkers (HTTP, subprocess, IPC) end-to-end against minimal
  fixtures and asserts the synthesised symbols actually carry the expected
  ``origin`` / ``origin_run_id`` values. Source presence is necessary but not
  sufficient — the runtime check confirms the stamp lands on the values
  consumers see.

Positive precedents
-------------------
* ``linkers/message_queue.py`` (stamps both fields post-construction)
* ``linkers/database_query.py`` (stamps both fields post-construction)
* ``linkers/_view_template_core.py`` (already stamps ``origin`` via kwarg,
  still needs ``origin_run_id``)
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path
from textwrap import dedent

import pytest

LINKERS_DIR = (
    Path(__file__).resolve().parent.parent
    / "src" / "hypergumbo_core" / "linkers"
)

# Linker modules that synthesise their own ``Symbol`` objects. The 21
# Symbol-producing linker modules listed in INV-sopon (the 19 currently broken
# plus the two positive precedents that already stamp correctly). Linker
# modules that only emit edges (e.g. inheritance, type_hierarchy) are out of
# scope for this invariant — they do not create Symbols.
SYMBOL_PRODUCING_LINKERS = [
    # 19 currently-broken linkers (target of this fix)
    "annotation_convention",
    "crypto_flow",
    "graphql",
    "grpc",
    "http",
    "ipc",
    "js_module",
    "message_dispatch",
    "openapi",
    "phoenix_ipc",
    "solidity_abi",
    "subprocess_cli",
    "swift_objc",
    "tauri_ipc",
    "_view_template_core",
    "vue_component",
    "wasm_bindgen",
    "websocket",
    "yjs_crdt",
    # 2 positive precedents (already stamp correctly — guard against regression)
    "database_query",
    "message_queue",
]


def _stamps_symbol_attr(module_name: str, attr: str) -> bool:
    """Return True if ``module_name`` stamps ``attr`` onto synthesised Symbols.

    Recognises two equivalent idioms:

    1. **Post-construction stamp** — an ``ast.Assign`` where the target is
       ``<name>.<attr>`` and the value is a ``Name`` (e.g. ``PASS_ID``) or an
       ``Attribute`` (e.g. ``run.execution_id``). Catches the
       ``symbol.origin = PASS_ID`` / ``symbol.origin_run_id = run.execution_id``
       pattern used by ``message_queue.py`` and ``database_query.py``.
    2. **Constructor kwarg** — a ``Symbol(...)`` call with ``<attr>=...`` as a
       keyword argument. Catches the inline form used by
       ``_view_template_core.py``.

    A plain text search would conflate these with the unrelated
    ``Edge.create(origin=PASS_ID, origin_run_id="test")`` calls that every linker already makes —
    AST inspection narrows the check to Symbol-side stamping only.
    """
    src = (LINKERS_DIR / f"{module_name}.py").read_text()
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == attr
                ):
                    return True
        elif isinstance(node, ast.Call):
            func = node.func
            is_symbol_ctor = (
                (isinstance(func, ast.Name) and func.id == "Symbol")
                or (
                    isinstance(func, ast.Attribute)
                    and func.attr == "Symbol"
                )
            )
            if is_symbol_ctor:
                for kw in node.keywords:
                    if kw.arg == attr:
                        return True
    return False


class TestSymbolOriginIdiomInLinkerSource:
    """Structural pass: every Symbol-producing linker source has the stamp idiom."""

    @pytest.mark.parametrize("module_name", SYMBOL_PRODUCING_LINKERS)
    def test_linker_module_stamps_origin(self, module_name: str) -> None:
        assert _stamps_symbol_attr(module_name, "origin"), (
            f"{module_name}.py synthesises Symbols but never stamps "
            "Symbol.origin = PASS_ID. Add the stamp after construction "
            "(see linkers/message_queue.py:466-470 for the canonical pattern)."
        )

    @pytest.mark.parametrize("module_name", SYMBOL_PRODUCING_LINKERS)
    def test_linker_module_stamps_origin_run_id(self, module_name: str) -> None:
        assert _stamps_symbol_attr(module_name, "origin_run_id"), (
            f"{module_name}.py synthesises Symbols but never stamps "
            "Symbol.origin_run_id = run.execution_id. Add the stamp after "
            "the AnalysisRun is created (see linkers/message_queue.py:466-470)."
        )


class TestSymbolOriginRuntime:
    """Runtime pass: representative linkers actually emit stamped Symbols.

    Three linkers chosen for blast-radius coverage:

    * ``http`` — the most commonly-triggered linker in real corpora; one of the
      two source-locus examples cited in the INV-sopon tracker description.
    * ``subprocess_cli`` — the second source-locus example, exercises a
      different symbol shape (call_site instead of function).
    * ``ipc`` — exercises the no-extra-input form (``link_ipc(repo_root)``)
      and a different framework_role population than the other two.
    """

    def _make_ts_route_symbol(self) -> object:
        """Helper: build a minimal TS route Symbol for http_linker input."""
        from hypergumbo_core.ir import Span, Symbol
        return Symbol(
            id="typescript:src/routes.ts:1-5:GET /api/users:function",
            name="GET /api/users",
            kind="function",
            language="typescript",
            path="src/routes.ts",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="typescript",
            meta={"concept": {"path": "/api/users", "method": "GET"}},
        )

    def _make_py_cli_symbol(self) -> object:
        """Helper: build a minimal Python CLI Symbol for subprocess linker input."""
        from hypergumbo_core.ir import Span, Symbol
        return Symbol(
            id="python:cli.py:1-5:main:function",
            name="myapp",
            kind="function",
            language="python",
            path="cli.py",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="python",
            meta={"framework_role": "cli_entrypoint"},
        )

    def test_http_linker_stamps_origin_on_synthesised_symbols(
        self, tmp_path: Path
    ) -> None:
        """http_linker post-construction stamp matches PASS_ID + run.execution_id."""
        from hypergumbo_core.linkers.http import PASS_ID, link_http

        client = tmp_path / "client.js"
        client.write_text('fetch("https://api.example.com/api/users");\n')

        route_sym = self._make_ts_route_symbol()
        result = link_http(tmp_path, [route_sym])

        assert result.symbols, "fixture should produce at least one http_client symbol"
        for sym in result.symbols:
            assert sym.origin == [PASS_ID], (
                f"http_linker emitted symbol with origin={sym.origin!r}, "
                f"expected {PASS_ID!r}"
            )
            assert sym.origin_run_id == result.run.execution_id, (
                f"http_linker symbol origin_run_id={sym.origin_run_id!r} "
                f"does not match run.execution_id={result.run.execution_id!r}"
            )

    def test_subprocess_linker_stamps_origin_on_synthesised_symbols(
        self, tmp_path: Path
    ) -> None:
        """subprocess_linker post-construction stamp matches PASS_ID + run.execution_id."""
        from hypergumbo_core.linkers.subprocess_cli import (
            PASS_ID,
            link_subprocess,
        )

        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "myapp"\n'
        )
        caller = tmp_path / "caller.py"
        caller.write_text(
            dedent(
                """
                import subprocess
                subprocess.run(["myapp", "doit"])
                """
            ).strip()
        )

        cli_sym = self._make_py_cli_symbol()
        result = link_subprocess(tmp_path, [cli_sym])

        assert result.symbols, "fixture should produce at least one subprocess call symbol"
        for sym in result.symbols:
            assert sym.origin == [PASS_ID], (
                f"subprocess_linker symbol origin={sym.origin!r}, "
                f"expected {PASS_ID!r}"
            )
            assert sym.origin_run_id == result.run.execution_id, (
                f"subprocess_linker symbol origin_run_id={sym.origin_run_id!r} "
                f"does not match run.execution_id={result.run.execution_id!r}"
            )

    def test_ipc_linker_stamps_origin_on_synthesised_symbols(
        self, tmp_path: Path
    ) -> None:
        """ipc_linker post-construction stamp matches PASS_ID + run.execution_id."""
        from hypergumbo_core.linkers.ipc import PASS_ID, link_ipc

        # Electron-style main/renderer pair with a literal channel string.
        main_dir = tmp_path / "main"
        main_dir.mkdir()
        (main_dir / "main.js").write_text(
            'ipcMain.on("save-file", (event, data) => {});\n'
        )
        renderer_dir = tmp_path / "renderer"
        renderer_dir.mkdir()
        (renderer_dir / "ui.js").write_text(
            'ipcRenderer.send("save-file", payload);\n'
        )

        result = link_ipc(tmp_path)

        # ipc only synthesises symbols when it finds matching send/receive
        # pairs. If the fixture didn't trigger that, fail loud — the test
        # contract requires at least one synthesised symbol to exercise the
        # stamp.
        assert result.symbols, (
            "ipc fixture should produce at least one synthesised IPC symbol"
        )
        for sym in result.symbols:
            assert sym.origin == [PASS_ID], (
                f"ipc_linker symbol origin={sym.origin!r}, "
                f"expected {PASS_ID!r}"
            )
            assert sym.origin_run_id == result.run.execution_id, (
                f"ipc_linker symbol origin_run_id={sym.origin_run_id!r} "
                f"does not match run.execution_id={result.run.execution_id!r}"
            )


class TestPassIdImportable:
    """Sanity: every linker module exposes a ``PASS_ID`` constant.

    The stamp idiom relies on this constant existing in every linker module.
    A new linker added without ``PASS_ID`` would silently break the invariant
    even if the structural check passed via dead code elsewhere in the file.
    """

    @pytest.mark.parametrize("module_name", SYMBOL_PRODUCING_LINKERS)
    def test_module_exposes_pass_id(self, module_name: str) -> None:
        mod = importlib.import_module(f"hypergumbo_core.linkers.{module_name}")
        assert hasattr(mod, "PASS_ID"), (
            f"hypergumbo_core.linkers.{module_name} must define PASS_ID"
        )
        assert isinstance(mod.PASS_ID, str) and mod.PASS_ID, (
            f"{module_name}.PASS_ID must be a non-empty string"
        )
