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
    """The constant-external fallback names the constant as written (WI-surar)."""
    assert calls["load"] == {"File:0-0:read:external_symbol"}, calls["load"]


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


def test_a_root_prefixed_receiver_resolves_both_ways(tmp_path: Path) -> None:
    """``::Redis::Alfred.delete`` is keyed ``Redis::Alfred.delete`` (chatwoot, 25
    calls), and a class DECLARED with the prefix keeps it in its names. The
    receiver-blind fallback had been resolving both by accident."""
    from hypergumbo_lang_mainstream.ruby import analyze_ruby

    (tmp_path / "lib.rb").write_text(
        "module Redis\n  module Alfred\n    def self.delete(k)\n      k\n    end\n  end\nend\n\n"
        "class ::Svc::Resolver\n  def self.using(x)\n    x\n  end\nend\n"
    )
    (tmp_path / "app.rb").write_text(
        "class App\n  def a\n    ::Redis::Alfred.delete(1)\n  end\n\n"
        "  def b\n    ::Svc::Resolver.using(2)\n  end\nend\n"
    )
    edges = [e for e in analyze_ruby(tmp_path).edges if e.edge_type == "calls"]
    a = {e.dst for e in edges if e.src.endswith("App#a:method")}
    b = {e.dst for e in edges if e.src.endswith("App#b:method")}
    assert any(d.endswith("Alfred.delete:method") for d in a), a
    assert any(d.endswith("Resolver.using:method") for d in b), b


# --- WI-surar: the slot is the constant's owner path, as ruby writes it -------
#
# ``Net::HTTP.get(u)`` used to emit ``ruby:http:0-0:get``: the namespace cut
# off and the rest lowercased, with or without ``require 'net/http'``. ADR-0051
# says the module slot names the OWNER PATH of the called symbol; ``Net::HTTP``
# is that owner, and ``http`` is neither it nor the require path. The overlay
# row a ruby author writes (``module: Net::HTTP``) could not match, because a
# component-suffix match must agree in case (INV-dijor), and the dropped
# namespace was the only thing telling ``Net::HTTP`` from ``Faraday::HTTP``.

_OWNER_SRC = """require 'net/http'
require 'json'
require 'set'

class Client
  def fetch(u)
    Net::HTTP.get(URI(u))
  end

  def rooted(u)
    ::Net::HTTP.get(URI(u))
  end

  def other(u)
    Faraday::HTTP.get(u)
  end

  def save(p, s)
    File.write(p, s)
  end

  def decode(t)
    JSON.parse(t)
  end

  def bag
    Set.new([1])
  end
end
"""


@pytest.fixture(scope="module")
def owner_slots(tmp_path_factory: pytest.TempPathFactory) -> dict[str, set[tuple[str, str]]]:
    """caller -> {(dst module slot, dst_ref.module_path)} for constant_external calls."""
    from hypergumbo_core.ir import symbol_path_slot
    from hypergumbo_lang_mainstream.ruby import analyze_ruby

    root = tmp_path_factory.mktemp("surar")
    (root / "client.rb").write_text(_OWNER_SRC)
    out: dict[str, set[tuple[str, str]]] = {}
    for e in analyze_ruby(root).edges:
        if e.edge_type != "calls" or (e.meta or {}).get("receiver") != "constant_external":
            continue
        caller = e.src.split(":")[-2].rsplit("#", 1)[-1]
        ref = e.dst_ref.module_path if e.dst_ref is not None else None
        out.setdefault(caller, set()).add((symbol_path_slot(e.dst), ref))
    return out


@pytest.mark.parametrize("caller,slot", [
    ("fetch", "Net::HTTP"),
    ("rooted", "Net::HTTP"),   # the root prefix is not part of the owner
    ("other", "Faraday::HTTP"),
    ("save", "File"),
    ("decode", "JSON"),
    ("bag", "Set"),            # a require hint does not replace the owner
])
def test_the_slot_is_the_constant_as_written(
    owner_slots: dict[str, set[tuple[str, str]]], caller: str, slot: str,
) -> None:
    # REACH FIRST: every caller must have produced a constant_external edge,
    # or the equality below would be about an empty set.
    assert caller in owner_slots, owner_slots
    assert owner_slots[caller] == {(slot, slot)}, owner_slots[caller]


def test_the_slot_does_not_depend_on_a_require(tmp_path: Path) -> None:
    """The filed repro says "with or without a require"; the fixture above has
    one, so the other half is pinned here."""
    from hypergumbo_lang_mainstream.ruby import analyze_ruby

    (tmp_path / "a.rb").write_text("def f(u)\n  Net::HTTP.get(URI(u))\nend\n")
    dsts = {e.dst for e in analyze_ruby(tmp_path).edges
            if (e.meta or {}).get("receiver") == "constant_external"}
    assert dsts == {"ruby:Net::HTTP:0-0:get:unresolved"}, dsts


_NET_CLAIM = ("claims:\n  - id: C\n    text: t\n    constraint:\n"
              "      boundary: net_recv\n      must_not_exist: true\n")


def _net_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                 module: str, call: str) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.rb").write_text(
        f"require 'net/http'\ndef fetch(u)\n  {call}.get(URI(u))\nend\n")
    overlay = tmp_path / "ov.yaml"
    overlay.write_text("language: ruby\nstatus: overlay\nnet_recv:\n"
                       f"  - module: {module}\n    functions: [get]\n")
    claims = tmp_path / "claims.yaml"
    claims.write_text(_NET_CLAIM)
    out = _run(["verify-claims", str(repo), "--claims", str(claims),
                "--io-primitives", str(overlay), "--format", "json"],
               tmp_path / "cache", monkeypatch)
    (verdict,) = json.loads(out)["verdicts"]
    return verdict["verdict"]


@pytest.mark.parametrize("module", ["Net::HTTP", "net/http"])
def test_an_overlay_row_spelled_as_ruby_writes_it_reaches_the_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: str,
) -> None:
    """The behaviour the item states: ``module: Net::HTTP`` was inconclusive
    (no row matched ``http``). The require-path spelling keeps matching."""
    assert _net_verdict(tmp_path, monkeypatch, module, "Net::HTTP") == "violated"


def test_an_unqualified_row_does_not_reach_a_namespaced_constant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WI-mujod: a row spelled ``HTTP`` names the top-level ``HTTP`` constant
    (the http.rb gem's), and ``Net::HTTP`` carries a leading component that row
    does not -- a different owner, the same reason a ``Net::HTTP`` row does not
    reach ``Faraday::HTTP`` (below). It used to match through the hint-longer
    suffix direction, which WI-surar's own filing named as the hazard ("HTTP
    would also match ... Faraday::HTTP, a project Api::HTTP"). FAIL-CLOSED: the
    unclassified call withholds a clean verdict rather than confirming it."""
    assert _net_verdict(tmp_path, monkeypatch, "HTTP", "Net::HTTP") == "inconclusive"


def test_the_namespace_tells_two_http_constants_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control: a ``Net::HTTP`` row must NOT classify ``Faraday::HTTP.get``.
    Before the fix both calls emitted ``http``, so nothing could tell them
    apart. FAIL-CLOSED: the unvouched module withholds a clean verdict."""
    assert _net_verdict(tmp_path, monkeypatch, "Net::HTTP", "Faraday::HTTP") == "inconclusive"
