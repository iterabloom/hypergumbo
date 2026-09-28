# SPDX-License-Identifier: AGPL-3.0-or-later
"""A call on a constant receiver is not bound to another class's method (WI-johib).

``_try_receiver_call`` tried ``Const#m`` and ``Const.m``, and when neither was
in the repository it asked the method resolver for ``m`` BY SHORT NAME,
receiver-blind. So:

* ``Rails.root.join`` bound ``HealthServer#root`` (postal),
* ``Postal::Config.dns`` bound ``DNSResolver#dns`` (postal),
* ``File.read(p)`` bound a project's own ``#read``, which also defeated a
  user's overlay row for ``File.read``: a must_not_exist fs_read claim went
  from violated to inconclusive, blaming a project class.

On postal, 54 of the 160 constant-receiver calls that landed on an in-repo
method landed on a DIFFERENT class. The receiver names its owner; a short-name
match on some other class is the receiver-blind binding INV-maluk and
INV-fahub forbid elsewhere. A constant the project does not declare now takes
the constant-external fallback (``ruby:file:0-0:read``), and a project
constant that does not declare the method keeps an unresolved edge whose
receiver type names it, so the inherited-calls linker can walk its ancestors.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_SRC = '''class HealthServer
  def root
    1
  end

  def read
    2
  end
end

module Postal
  class Config
    def self.other
      3
    end
  end
end

class Base
  def self.make
    4
  end
end

class Kid < Base
end

class App
  def boot
    Rails.root.join("lib")
  end

  def cfg
    Postal::Config.root
  end

  def load(p)
    File.read(p)
  end

  def own
    HealthServer.new.root
  end

  def inherited
    Kid.make
  end
end
'''


def _run(argv: list[str], cache: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(argv)
    return buf.getvalue()


@pytest.fixture(scope="module")
def calls(tmp_path_factory: pytest.TempPathFactory) -> dict[str, set[str]]:
    root = tmp_path_factory.mktemp("johib")
    repo = root / "repo"
    repo.mkdir()
    (repo / "m.rb").write_text(_SRC)
    out = root / "survey.json"
    mp = pytest.MonkeyPatch()
    try:
        _run(["survey", str(repo), "--out", str(out)], root / "cache", mp)
    finally:
        mp.undo()
    by_caller: dict[str, set[str]] = {}
    for edge in json.loads(out.read_text())["edges"]:
        if edge["type"] == "calls":
            caller = edge["src"].split(":")[-2].rsplit("#", 1)[-1]
            by_caller.setdefault(caller, set()).add(edge["dst"].split(":", 1)[1])
    return by_caller


@pytest.mark.parametrize("caller,wrong", [
    ("boot", "HealthServer#root"), ("cfg", "HealthServer#root"), ("load", "HealthServer#read"),
])
def test_a_constant_receiver_is_not_bound_to_another_class(
    calls: dict[str, set[str]], caller: str, wrong: str,
) -> None:
    assert not any(d.endswith(f":{wrong}:method") for d in calls[caller]), calls[caller]


def test_an_undeclared_constant_names_its_module(calls: dict[str, set[str]]) -> None:
    """The constant-external fallback's spelling (WI-rijij); WI-surar owns
    whether ``file`` is the right one."""
    assert calls["load"] == {"file:0-0:read:external_symbol"}, calls["load"]


def test_the_owner_that_declares_the_method_still_resolves(calls: dict[str, set[str]]) -> None:
    assert any(d.endswith(":HealthServer#root:method") for d in calls["own"]), calls["own"]


def test_an_inherited_class_method_still_resolves(calls: dict[str, set[str]]) -> None:
    """``Kid.make`` is ``Base.make``: the unresolved edge names ``Kid``, and the
    inherited-calls linker walks to the ancestor that declares it."""
    assert any(d.endswith(":Base.make:method") for d in calls["inherited"]), calls["inherited"]


def test_a_user_overlay_row_reaches_the_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The overlay consequence the item measured: this was inconclusive."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.rb").write_text(_SRC)
    overlay = tmp_path / "ov.yaml"
    overlay.write_text("language: ruby\nstatus: overlay\nfs_read:\n"
                       "  - module: file\n    functions: [read]\n")
    claims = tmp_path / "claims.yaml"
    claims.write_text("claims:\n  - id: C\n    text: t\n    constraint:\n"
                      "      boundary: fs_read\n      must_not_exist: true\n")
    out = _run(["verify-claims", str(repo), "--claims", str(claims),
                "--io-primitives", str(overlay), "--format", "json"],
               tmp_path / "cache", monkeypatch)
    (verdict,) = json.loads(out)["verdicts"]
    assert verdict["verdict"] == "violated", verdict["details"]
