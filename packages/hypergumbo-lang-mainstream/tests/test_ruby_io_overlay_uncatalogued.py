# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-guhuv on the production path: a ruby I/O overlay applies, and a run over
ruby beside python is not aborted by it.

The core file ``test_io_overlay_uncatalogued_language.py`` pins the loader over
captured edges; this one runs ``verify-claims`` with the real ruby analyzer.
Before the fix, the ruby-only repo came back ``inconclusive`` rc 2 ("ruby made
calls but have no I/O catalog") under a "Loaded 1 ... overlay(s)" banner, and
the ruby + python repo exited 2 with no JSON at all, the ruby overlay refused
as a misspelling.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_RUBY = """require 'net/http'
def fetch(u)
  Net::HTTP.get(URI(u))
end
def save(p, s)
  File.write(p, s)
end
"""

#: ``net/http``, because the ruby analyzer emits ``Net::HTTP.get`` as
#: ``ruby:http:0-0:get`` (see the core file's note).
_OVERLAY = """language: ruby
status: overlay
net_recv:
  - module: net/http
    functions: [get]
"""

_CLAIMS = """claims:
  - id: no-net-recv
    text: Never receives data from the network.
    constraint:
      boundary: net_recv
      must_not_exist: true
  - id: no-fs-write
    text: Never writes to the filesystem.
    constraint:
      boundary: fs_write
      must_not_exist: true
"""


def _run(tmp_path: Path, files: dict[str, str],
         capsys: pytest.CaptureFixture[str],
         monkeypatch: pytest.MonkeyPatch) -> tuple[int, dict, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, text in files.items():
        (repo / name).write_text(text)
    (tmp_path / "rb.yaml").write_text(_OVERLAY)
    (tmp_path / "claims.yaml").write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    rc = main([
        "verify-claims", str(repo), "--claims", str(tmp_path / "claims.yaml"),
        "--io-primitives", str(tmp_path / "rb.yaml"), "--format", "json",
    ])
    out = capsys.readouterr()
    return rc, {v["claim_id"]: v for v in json.loads(out.out)["verdicts"]}, out.err


def test_the_overlay_classifies_ruby_and_the_run_says_it_is_overlay_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rc, verdicts, err = _run(tmp_path, {"app.rb": _RUBY}, capsys, monkeypatch)
    assert verdicts["no-net-recv"]["verdict"] == "violated", verdicts["no-net-recv"]
    assert rc == 1
    assert "'ruby' ships no I/O primitive catalogue" in err
    # FAIL-CLOSED: the overlay vouches for nothing about File.
    assert verdicts["no-fs-write"]["verdict"] == "inconclusive"


def test_a_ruby_overlay_does_not_abort_a_run_that_also_holds_python(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rc, verdicts, err = _run(
        tmp_path,
        {"app.rb": _RUBY, "tool.py": "import os\n\ndef f():\n    return os.getcwd()\n"},
        capsys, monkeypatch,
    )
    assert "check the spelling" not in err
    assert verdicts["no-net-recv"]["verdict"] == "violated"
    assert rc == 1
