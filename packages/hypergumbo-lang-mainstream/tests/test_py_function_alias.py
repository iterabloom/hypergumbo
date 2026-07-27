# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-gulot: a module-level function alias (``f = g``) must resolve to its
target function so calls through the alias reach the real body — not a dead-end
``kind=variable`` node with 0 out-degree (a dispatch:F3 / INV-pohik instance).
"""

from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_lang_mainstream.py import extract_nodes


def _analyze(tmp_path: Path, src: str):
    f = tmp_path / "m.py"
    f.write_text(src)
    res = extract_nodes(f)
    ids = {s.name: s.id for s in res.symbols}
    return res, ids


class TestModuleLevelFunctionAlias:
    _SRC = (
        "def is_test_path(p):\n"
        "    return True\n"
        "_is_test_path = is_test_path\n"  # module-level function alias
        "def caller():\n"
        "    return _is_test_path('x')\n"
    )

    def test_alias_call_resolves_to_target_function(self, tmp_path: Path) -> None:
        res, ids = _analyze(tmp_path, self._SRC)
        caller_id = ids["caller"]
        calls = [e for e in res.edges if e.edge_type == "calls" and e.src == caller_id]
        assert any(e.dst == ids["is_test_path"] for e in calls), (
            "caller's call through the alias did not resolve to is_test_path"
        )
        assert not any(e.dst == ids["_is_test_path"] for e in calls), (
            "caller still dead-ends its call at the kind=variable alias node"
        )

    def test_target_function_is_called(self, tmp_path: Path) -> None:
        # The consequence fixed: is_test_path is no longer wrongly 'uncalled'.
        res, ids = _analyze(tmp_path, self._SRC)
        incoming = [
            e for e in res.edges
            if e.edge_type == "calls" and e.dst == ids["is_test_path"]
        ]
        assert incoming, "is_test_path appears uncalled (alias not resolved)"

    def test_imported_function_alias_resolves_cross_file(self, tmp_path: Path) -> None:
        # The real defect (ranking.py:113): `_f = f` where f is IMPORTED. The
        # full pipeline resolves the import, so the alias call reaches the
        # cross-file target (exercises the alias import-resolution branch).
        (tmp_path / "utils.py").write_text("def is_test_path(p):\n    return True\n")
        (tmp_path / "main.py").write_text(
            "from utils import is_test_path\n"
            "_is_test_path = is_test_path\n"
            "def caller():\n"
            "    return _is_test_path('x')\n"
        )
        out_path = tmp_path / "out.json"
        run_behavior_map(
            repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False
        )
        data = json.loads(out_path.read_text())
        target = next(
            n["id"] for n in data["nodes"]
            if n.get("name") == "is_test_path" and n.get("kind") == "function"
        )
        caller = next(
            n["id"] for n in data["nodes"]
            if n.get("name") == "caller" and n.get("kind") == "function"
        )
        calls = [
            e for e in data["edges"]
            if e["type"] == "calls" and e["src"] == caller
        ]
        assert any(e["dst"] == target for e in calls), (
            "caller's call through the imported alias did not resolve cross-file "
            "to is_test_path"
        )

    def test_non_function_alias_unaffected(self, tmp_path: Path) -> None:
        # `x = y` where y is NOT a function must not spuriously become a call/link.
        src = (
            "y = 5\n"
            "x = y\n"
            "def caller():\n"
            "    return x\n"
        )
        res, ids = _analyze(tmp_path, src)
        # No calls edge should target x or y (they are plain variables).
        assert not any(
            e.edge_type == "calls" and e.dst in (ids.get("x"), ids.get("y"))
            for e in res.edges
        )

    def test_imported_alias_rhs_branch_no_crash(self, tmp_path: Path) -> None:
        # Alias whose RHS is an IMPORTED name exercises the import-resolution
        # branch. Single-file analysis can't resolve the import target, so no
        # alias is recorded — assert no crash and the alias LHS still exists.
        src = (
            "from mod import g\n"
            "f = g\n"
            "def caller():\n"
            "    return f()\n"
        )
        res, ids = _analyze(tmp_path, src)
        assert "f" in ids  # extracted as a variable; import branch ran, no crash

    def test_non_name_assignment_target_ignored(self, tmp_path: Path) -> None:
        # A non-Name assignment target (`obj.x = g`) is skipped by the alias scan.
        src = (
            "def g():\n"
            "    return 1\n"
            "class C:\n"
            "    x = None\n"
            "obj = C()\n"
            "obj.x = g\n"  # Attribute target -> skipped
            "def caller():\n"
            "    return g()\n"
        )
        res, ids = _analyze(tmp_path, src)
        assert any(e.edge_type == "calls" and e.dst == ids["g"] for e in res.edges)

    def test_function_local_alias_does_not_pollute_module_map(self, tmp_path: Path) -> None:
        # Regression: a function-local `p = g` must NOT make a same-named MODULE
        # variable `p` resolve to g. The module-scope scan (tree.body) excludes
        # local assigns, so `caller`'s `p()` (the module p == 5) is not resolved
        # to the function g.
        src = (
            "def g():\n"
            "    return 1\n"
            "p = 5\n"  # module variable p (not a function)
            "def local():\n"
            "    p = g\n"  # function-local alias, same name
            "    return p\n"
            "def caller():\n"
            "    return p()\n"  # calls the MODULE p (== 5), NOT g
        )
        res, ids = _analyze(tmp_path, src)
        assert not any(
            e.edge_type == "calls" and e.src == ids["caller"] and e.dst == ids["g"]
            for e in res.edges
        ), "a function-local alias leaked into the module-level alias map"
