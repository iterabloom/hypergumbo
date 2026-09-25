# SPDX-License-Identifier: AGPL-3.0-or-later
"""Node runtime globals reached as ``obj.method()`` without an import (WI-kikar).

``JS_KNOWN_GLOBALS`` is the set of receivers the Case-3b member-call fallback
will type without an import. It is hand-maintained, and it had drifted from the
catalogue: ``process`` and ``performance`` carry rows in
``io_primitives/javascript.yaml`` but were not in the set, so every
``process.<m>()`` and ``performance.now()`` call fell to the ``external``
sentinel -- ADR-0051's own marker for *unreachable to the catalogue*.

THE FILING NAMED TWO ROWS; THE PROBE FOUND EIGHT CALLS ACROSS TWO GLOBALS.
WI-kikar was filed against the two METHOD-kind rows (``process.on`` for the fork
channel, ``process.send``). But the break is in the member-call path, not in the
row kind, so the ~14 FUNCTION-kind rows on ``process`` -- ``hrtime``,
``uptime``, ``getuid``, ``memoryUsage``, ``chdir``, ``openStdin`` -- were
equally unreachable, and so was ``performance.now``, a second global with the
same shape that the filing does not mention at all. Both are node globals
requiring no import, which is exactly what Case 3b exists for.

The shadowing tests are the refutation half and matter more than the recall
half: ``process`` is an ordinary identifier and a local binding of that name
(``const process = spawn(...)``, ``import { process } from ...``) must NOT
acquire the runtime global's module slot. Case 3b's existing guards
(``named_imports``, ``var_types``, and the Case-2 namespace elif) are what make
adding a name to the set safe; these pin that they apply to the new names too.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _callee_dsts(tmp_path: Path) -> list[str]:
    from hypergumbo_lang_mainstream.js_ts import analyze_javascript

    result = analyze_javascript(tmp_path)
    return [
        e.dst for e in (result.edges or [])
        if e.edge_type == "calls" and isinstance(e.dst, str)
    ]


class TestProcessMemberCalls:
    """``process.<m>()`` carries ``process`` in its module slot."""

    def test_method_kind_rows_reach_the_catalogue(self, tmp_path: Path) -> None:
        """``process.on`` / ``process.send`` -- WI-kikar's filed target.

        ipc_recv (the fork channel) and ipc_send. Both were `external`.
        """
        pytest.importorskip("tree_sitter_typescript")
        (tmp_path / "ipc.js").write_text(
            "export function run(h) {\n"
            "  process.on('message', h);\n"
            "  process.send({ a: 1 });\n"
            "}\n"
        )
        dsts = _callee_dsts(tmp_path)
        assert "javascript:process:0-0:on:unresolved" in dsts
        assert "javascript:process:0-0:send:unresolved" in dsts

    def test_function_kind_rows_reach_it_too(self, tmp_path: Path) -> None:
        """The larger half the filing does not mention.

        Same member-call path, different row kind, so these broke identically:
        host_info_read (hrtime, uptime, getuid, memoryUsage), env_write (chdir)
        and ipc_recv (openStdin).
        """
        pytest.importorskip("tree_sitter_typescript")
        (tmp_path / "sys.js").write_text(
            "export function probe() {\n"
            "  const t = process.hrtime();\n"
            "  const u = process.uptime();\n"
            "  const i = process.getuid();\n"
            "  const m = process.memoryUsage();\n"
            "  process.chdir('/tmp');\n"
            "  const s = process.openStdin();\n"
            "  return [t, u, i, m, s];\n"
            "}\n"
        )
        dsts = _callee_dsts(tmp_path)
        for name in ("hrtime", "uptime", "getuid", "memoryUsage", "chdir", "openStdin"):
            assert f"javascript:process:0-0:{name}:unresolved" in dsts, name
        assert not [d for d in dsts if d.startswith("javascript:external:")]


class TestPerformanceGlobal:
    """``performance`` is the second global with this shape (Node 16+, browsers)."""

    def test_performance_now_reaches_the_catalogue(self, tmp_path: Path) -> None:
        pytest.importorskip("tree_sitter_typescript")
        (tmp_path / "perf.js").write_text(
            "export function timed() {\n  return performance.now();\n}\n"
        )
        assert "javascript:performance:0-0:now:unresolved" in _callee_dsts(tmp_path)


class TestShadowingStillRefuses:
    """A local binding of the name must NOT acquire the global's module slot."""

    def test_a_named_import_shadows_process(self, tmp_path: Path) -> None:
        pytest.importorskip("tree_sitter_typescript")
        (tmp_path / "shadow.js").write_text(
            "import { process } from './my-process';\n"
            "export function run() {\n  process.send({ a: 1 });\n}\n"
        )
        assert "javascript:process:0-0:send:unresolved" not in _callee_dsts(tmp_path)

    def test_a_local_binding_shadows_process(self, tmp_path: Path) -> None:
        """``const process = new Worker(...)`` is a variable, not the runtime."""
        pytest.importorskip("tree_sitter_typescript")
        (tmp_path / "local.js").write_text(
            "export function run() {\n"
            "  const process = new Worker('w.js');\n"
            "  process.send({ a: 1 });\n"
            "}\n"
        )
        assert "javascript:process:0-0:send:unresolved" not in _callee_dsts(tmp_path)

    def test_a_namespace_import_shadows_performance(self, tmp_path: Path) -> None:
        pytest.importorskip("tree_sitter_typescript")
        (tmp_path / "ns.js").write_text(
            "import * as performance from 'perf_hooks';\n"
            "export function t() {\n  return performance.now();\n}\n"
        )
        assert "javascript:performance:0-0:now:unresolved" not in _callee_dsts(tmp_path)


class TestTheSetTracksTheCatalogue:
    """Both new names are in the set BECAUSE the catalogue rows them.

    The set's docstring claims it mirrors ``javascript.yaml``; that claim had
    silently stopped being true, which is how WI-kikar happened. This pins the
    linkage for the two names repaired here rather than asserting a general
    derivation the catalogue cannot yet support -- it does not record which of
    its modules are GLOBALS and which need an import, and that residual is
    filed separately.
    """

    @pytest.mark.parametrize("name", ["process", "performance"])
    def test_name_is_admitted_and_rowed(self, name: str) -> None:
        from hypergumbo_core.io_boundary import load_catalog
        from hypergumbo_lang_mainstream.js_ts import JS_KNOWN_GLOBALS

        assert name in JS_KNOWN_GLOBALS
        callable_rows = [
            p for p in load_catalog("javascript").primitives
            if p.module == name and p.kind in ("method", "function")
        ]
        assert callable_rows, f"{name} is admitted but the catalogue rows nothing callable"
