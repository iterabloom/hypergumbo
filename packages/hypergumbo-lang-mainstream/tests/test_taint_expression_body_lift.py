# SPDX-License-Identifier: AGPL-3.0-or-later
"""A secret a helper returns WITHOUT a return statement is lifted to its caller (WI-sodoj).

INV-komoj's return lift reads a helper's return statements. Rust's tail
expression and a JS arrow's expression body return their value with none,
so ``Command::new(get())`` / ``cp.exec(get())`` read ``confirmed_with_caveats``
while the explicit-``return`` twins read ``violated``. Each now reads
``violated``. The span logic is tested in hypergumbo-core's
``test_ddg_expression_body_returns.py``.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

_CLAIMS = """\
claims:
  - id: SECRET-NO-SUBPROC
    text: An environment value never reaches a launched program.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: subprocess
"""

_SHAPES = {
    "rust_tail": ("src/lib.rs", (
        "use std::env;\nuse std::process::Command;\n\n"
        "fn get() -> String {\n    env::var(\"TOOL\").unwrap()\n}\n\n"
        "pub fn run() {\n    Command::new(get()).spawn().unwrap();\n}\n"
    )),
    "js_arrow": ("a.js", (
        "const cp = require(\"child_process\");\n"
        "const get = () => process.env.TOOL;\n"
        "function run() {\n  cp.exec(get());\n}\n"
        "module.exports = { run };\n"
    )),
}


@pytest.mark.parametrize("shape", list(_SHAPES))
def test_the_expression_bodied_helper_is_lifted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str,
) -> None:
    from hypergumbo_core.cli import main

    name, text = _SHAPES[shape]
    repo = tmp_path / "repo"
    (repo / name).parent.mkdir(parents=True)
    (repo / name).write_text(text)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    assert verdict["verdict"] == "violated", verdict["details"]
