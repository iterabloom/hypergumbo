# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every walk follows a construction into the initializer (WI-satal).

py / js_ts / dart land an ``instantiates`` edge on the CLASS node, and no edge
leaving a class is traversable, so a walk that arrives at the class stops
there. INV-rolok fixed that for the dead-code walk only. Measured on the
shipped commands before this change, with ``Runner(cmd)`` constructing a class
whose ``__init__`` runs ``subprocess.run(cmd, shell=True)``:

* taint: a host_secret flow into the subprocess zone was ``violated`` through a
  function and ``confirmed_with_caveats``, with no evidence, through the
  constructor, in python and in javascript;
* io-boundaries: the ``subprocess.run`` chain inside ``__init__`` listed no
  entry point, while the ``sys.argv`` read in ``main`` listed ``main``;
* slice: a forward slice from ``main`` reached ``Runner`` and not
  ``Runner.__init__``, and a reverse slice from ``__init__`` found no caller.

The pairs come from one place (``construction.py``) and each walk reads them
in its own terms. No emitted edge changes.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_PY_CTOR = '''import os
import subprocess
import sys


class Runner:
    def __init__(self, cmd):
        subprocess.run(cmd, shell=True)


def go():
    cmd = os.environ["CMD"]
    Runner(cmd)


def main():
    Runner(sys.argv[1])


if __name__ == "__main__":
    main()
'''

_JS_CTOR = '''const child_process = require("child_process");

class Runner {
  constructor(cmd) {
    child_process.execSync(cmd);
  }
}

function go() {
  const cmd = process.env.CMD;
  new Runner(cmd);
}

module.exports = { go };
'''

_CLAIMS = '''claims:
  - id: SECRET-NOT-EXEC
    text: A secret never reaches a launched command.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: subprocess
'''


def _run(argv: list[str], cache: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(argv)
    return buf.getvalue()


def _repo(tmp_path: Path, name: str, text: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / name).write_text(text)
    return repo


@pytest.mark.parametrize("name,text", [("app.py", _PY_CTOR), ("app.js", _JS_CTOR)])
def test_a_flow_through_a_constructor_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, text: str,
) -> None:
    repo = _repo(tmp_path, name, text)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    out = _run(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"],
               tmp_path / "cache", monkeypatch)
    (verdict,) = json.loads(out)["verdicts"]
    assert verdict["verdict"] == "violated", verdict["details"]


def test_an_io_chain_in_an_initializer_names_its_entry_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path, "app.py", _PY_CTOR)
    out = _run(["io-boundaries", str(repo), "--format", "json"], tmp_path / "cache", monkeypatch)
    (chain,) = [c for c in json.loads(out)["boundaries"]["subprocess"]["chains"]
                if c["primitive"] == "subprocess.run"]
    assert chain["io_edge_src"].endswith(":Runner.__init__:method")  # reach
    assert any(ep.endswith(":main:function") for ep in chain["entry_points"])


def _slice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *extra: str) -> list[str]:
    repo = _repo(tmp_path, "app.py", _PY_CTOR)
    out = tmp_path / "slice.json"
    _run(["slice", str(repo), *extra, "--out", str(out)], tmp_path / "cache", monkeypatch)
    return json.loads(out.read_text())["feature"]["node_ids"]


def test_a_forward_slice_enters_the_initializer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = _slice(tmp_path, monkeypatch, "--entry", "main")
    assert any(n.endswith(":Runner:class") for n in nodes)  # reach
    assert any(n.endswith(":Runner.__init__:method") for n in nodes)


def test_a_reverse_slice_finds_who_constructs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = _slice(tmp_path, monkeypatch, "--entry", "Runner.__init__", "--reverse")
    assert any(n.endswith(":main:function") for n in nodes)
    assert any(n.endswith(":go:function") for n in nodes)


def test_a_forward_slice_does_not_enter_other_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The licence is the conjunction: ``contains`` alone confers nothing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "class Runner:\n    def __init__(self):\n        pass\n\n"
        "    def other(self):\n        pass\n\n\ndef main():\n    Runner()\n"
    )
    out = tmp_path / "slice.json"
    _run(["slice", str(repo), "--entry", "main", "--out", str(out)],
         tmp_path / "cache", monkeypatch)
    nodes = json.loads(out.read_text())["feature"]["node_ids"]
    assert any(n.endswith(":Runner.__init__:method") for n in nodes)
    assert not any(n.endswith(":Runner.other:method") for n in nodes)
