# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for intra-file variable reference edges (WI-jagus).

Module-level constants read inside function bodies should emit
``references`` edges with ``evidence_type='ast_name_read'``.
"""
import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def _edges_of(data: dict, etype: str = "references", evidence: str = "ast_name_read") -> list[dict]:
    """Filter edges by type and evidence_type."""
    return [
        e for e in data["edges"]
        if e["type"] == etype
        and e.get("meta", {}).get("evidence_type") == evidence
    ]


class TestIntraFileVariableRefs:
    """Intra-file bare-name reads of module-level constants."""

    def test_basic_constant_read_in_function(self, tmp_path: Path) -> None:
        """A function reading a module-level constant should emit a references edge."""
        (tmp_path / "app.py").write_text(
            "TIMEOUT = 30\n"
            "\n"
            "def connect():\n"
            "    if TIMEOUT > 0:\n"
            "        pass\n"
        )
        out = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
        data = json.loads(out.read_text())

        refs = [e for e in _edges_of(data) if "connect" in e["src"] and "TIMEOUT" in e["dst"]]
        assert len(refs) == 1, f"Expected 1 variable ref edge, got {refs}"
        assert refs[0]["meta"]["evidence_type"] == "ast_name_read"

    def test_multiple_constants_in_one_function(self, tmp_path: Path) -> None:
        """Multiple module-level constants read in one function → multiple edges."""
        (tmp_path / "app.py").write_text(
            "HOST = 'localhost'\n"
            "PORT = 8080\n"
            "\n"
            "def connect():\n"
            "    addr = HOST + ':' + str(PORT)\n"
        )
        out = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
        data = json.loads(out.read_text())

        refs = [e for e in _edges_of(data) if "connect" in e["src"]]
        dst_names = {e["dst"].split(":")[-2] for e in refs}
        assert "HOST" in dst_names, f"Missing HOST ref in {refs}"
        assert "PORT" in dst_names, f"Missing PORT ref in {refs}"

    def test_local_param_shadow_suppresses_edge(self, tmp_path: Path) -> None:
        """Function parameter with same name as constant → no edge."""
        (tmp_path / "app.py").write_text(
            "TIMEOUT = 30\n"
            "\n"
            "def connect(TIMEOUT):\n"
            "    return TIMEOUT\n"
        )
        out = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
        data = json.loads(out.read_text())

        refs = [e for e in _edges_of(data) if "connect" in e["src"] and "TIMEOUT" in e["dst"]]
        assert len(refs) == 0, f"Shadowed constant should not emit edge: {refs}"

    def test_local_assignment_shadow_suppresses_edge(self, tmp_path: Path) -> None:
        """Local assignment of same name as constant → no edge."""
        (tmp_path / "app.py").write_text(
            "TIMEOUT = 30\n"
            "\n"
            "def connect():\n"
            "    TIMEOUT = 60\n"
            "    return TIMEOUT\n"
        )
        out = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
        data = json.loads(out.read_text())

        refs = [e for e in _edges_of(data) if "connect" in e["src"] and "TIMEOUT" in e["dst"]]
        assert len(refs) == 0, f"Locally-assigned name should not emit edge: {refs}"

    def test_for_loop_shadow_suppresses_edge(self, tmp_path: Path) -> None:
        """For-loop variable with same name as constant → no edge."""
        (tmp_path / "app.py").write_text(
            "ITEM = 'default'\n"
            "\n"
            "def process(items):\n"
            "    for ITEM in items:\n"
            "        print(ITEM)\n"
        )
        out = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
        data = json.loads(out.read_text())

        refs = [e for e in _edges_of(data) if "process" in e["src"] and "ITEM" in e["dst"]]
        assert len(refs) == 0, f"For-loop shadowed name should not emit edge: {refs}"

    def test_module_level_read(self, tmp_path: Path) -> None:
        """Constant read at module level → edge from <module> symbol."""
        (tmp_path / "app.py").write_text(
            "BASE = '/api'\n"
            "ENDPOINT = BASE + '/users'\n"
        )
        out = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
        data = json.loads(out.read_text())

        refs = [e for e in _edges_of(data) if "file:file" in e["src"] and "BASE" in e["dst"]]
        assert len(refs) >= 1, f"Module-level read should emit edge: {_edges_of(data)}"

    def test_various_syntactic_contexts(self, tmp_path: Path) -> None:
        """Constant used in if-condition, return, binop all emit edges."""
        (tmp_path / "app.py").write_text(
            "MAX_RETRIES = 3\n"
            "DELAY = 1.0\n"
            "PREFIX = 'app'\n"
            "\n"
            "def retry():\n"
            "    if MAX_RETRIES > 0:\n"
            "        return DELAY * 2\n"
            "\n"
            "def tag():\n"
            "    name = PREFIX + '_main'\n"
        )
        out = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
        data = json.loads(out.read_text())

        retry_refs = [e for e in _edges_of(data) if "retry" in e["src"]]
        retry_dsts = {e["dst"].split(":")[-2] for e in retry_refs}
        assert "MAX_RETRIES" in retry_dsts, f"if-condition ref missing: {retry_refs}"
        assert "DELAY" in retry_dsts, f"return-value ref missing: {retry_refs}"

        tag_refs = [e for e in _edges_of(data) if "tag" in e["src"]]
        tag_dsts = {e["dst"].split(":")[-2] for e in tag_refs}
        assert "PREFIX" in tag_dsts, f"binop ref missing: {tag_refs}"

    def test_nested_function_own_scope(self, tmp_path: Path) -> None:
        """Inner function reading constant → edge from inner, not outer."""
        (tmp_path / "app.py").write_text(
            "LIMIT = 100\n"
            "\n"
            "def outer():\n"
            "    def inner():\n"
            "        return LIMIT\n"
            "    return inner()\n"
        )
        out = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
        data = json.loads(out.read_text())

        refs = [e for e in _edges_of(data) if "LIMIT" in e["dst"]]
        srcs = [e["src"] for e in refs]
        assert any("inner" in s for s in srcs), f"inner should ref LIMIT: {refs}"

    def test_kwonly_and_vararg_shadow(self, tmp_path: Path) -> None:
        """Keyword-only, *args, and **kwargs shadows suppress edges."""
        (tmp_path / "app.py").write_text(
            "KW = 'default'\n"
            "VA = 'default'\n"
            "KA = 'default'\n"
            "\n"
            "def f(*, KW):\n"
            "    return KW\n"
            "\n"
            "def g(*VA):\n"
            "    return VA\n"
            "\n"
            "def h(**KA):\n"
            "    return KA\n"
        )
        out = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
        data = json.loads(out.read_text())

        for name in ("KW", "VA", "KA"):
            refs = [e for e in _edges_of(data) if name in e["dst"]]
            assert len(refs) == 0, f"{name} shadowed by param should not emit edge: {refs}"

    def test_posonly_arg_shadow(self, tmp_path: Path) -> None:
        """Positional-only parameter shadows suppress edges."""
        (tmp_path / "app.py").write_text(
            "VAL = 42\n"
            "\n"
            "def f(VAL, /):\n"
            "    return VAL\n"
        )
        out = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
        data = json.loads(out.read_text())

        refs = [e for e in _edges_of(data) if "VAL" in e["dst"]]
        assert len(refs) == 0, f"Posonly-shadowed name should not emit edge: {refs}"

    def test_local_import_shadow(self, tmp_path: Path) -> None:
        """Import inside function body shadows module-level constant."""
        (tmp_path / "app.py").write_text(
            "os = 'not_a_module'\n"
            "json = 'not_a_module'\n"
            "\n"
            "def f():\n"
            "    import os\n"
            "    return os.getcwd()\n"
            "\n"
            "def g():\n"
            "    from json import loads\n"
            "    return loads(json)\n"
        )
        out = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
        data = json.loads(out.read_text())

        refs_f = [e for e in _edges_of(data) if ":f:" in e["src"] and ":os:" in e["dst"]]
        assert len(refs_f) == 0, f"import-shadowed 'os' should not emit edge: {refs_f}"

    def test_no_false_positive_for_function_names(self, tmp_path: Path) -> None:
        """Reading a function name should NOT produce an ast_name_read edge.

        Function references are handled by the existing _emit_function_ref path
        with evidence_type='function_reference'.
        """
        (tmp_path / "app.py").write_text(
            "def helper():\n"
            "    pass\n"
            "\n"
            "def main():\n"
            "    callback = helper\n"
        )
        out = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
        data = json.loads(out.read_text())

        name_read_refs = [
            e for e in _edges_of(data)
            if "main" in e["src"] and "helper" in e["dst"]
        ]
        assert len(name_read_refs) == 0, (
            f"Function names should use function_reference, not ast_name_read: {name_read_refs}"
        )
