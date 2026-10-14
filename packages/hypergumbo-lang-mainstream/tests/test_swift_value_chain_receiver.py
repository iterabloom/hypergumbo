# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Swift value chain ``v.p.m()`` is not typed by its head ``v``.

WI-sulas. ``_extract_call_target`` returned the chain's FIRST identifier as the
receiver hint, so for ``v.p.m()`` the emit site typed ``v`` although the
receiver is ``v.p``. Measured on vapor, Alamofire, Kingfisher and hummingbird:
1,020 such sites, 20 of 20 sampled carrying the head's type. Two consequences:

* an external head type went into the module slot:
  ``ch.pipeline.addHandler(h)`` emitted ``swift:Channel:0-0:addHandler`` for a
  call on a ``ChannelPipeline``;
* an in-repo head type drove resolution: ``s.db.save()`` with ``s: Store``
  looked up ``Store.save`` and could bind a method the receiver does not have.

A value chain of two or more hops now contributes NO hint, and the receiver
expression is typed by the walker that already types expression receivers
(WI-higob), which answers or stays silent. The ``self.a`` chain (WI-sizas) and a
TYPE-headed chain (``URLSession.shared.dataTask``) are untouched and pinned.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.swift import analyze_swift

_SOURCE = """import NIOCore

class DB { func save() {} }
class Store {
    var db = DB()
    func save() {}
}

func external(ch: Channel, h: ChannelHandler) {
    ch.pipeline.addHandler(h)
}

func singleHop(ch: Channel) {
    ch.close()
}

func inRepo(s: Store) {
    s.db.save()
}

func inRepoSingle(s: Store) {
    s.save()
}
"""


def _calls(tmp_path: Path) -> dict[str, list[Edge]]:
    (tmp_path / "main.swift").write_text(_SOURCE)
    analysis = analyze_swift(tmp_path)
    names = {s.id: s.name for s in analysis.symbols}
    out: dict[str, list[Edge]] = {}
    for e in analysis.edges:
        if e.edge_type == "calls":
            out.setdefault(names.get(e.src, e.src), []).append(e)
    return out


def _slot(e: Edge) -> str:
    return e.dst.split(":", 1)[1].split(":0-0:", 1)[0] if ":0-0:" in e.dst else ""


def test_an_external_value_chain_does_not_carry_the_head_type(tmp_path: Path) -> None:
    [edge] = [e for e in _calls(tmp_path)["external"] if e.dst.split(":")[-2] == "addHandler"]
    assert _slot(edge) != "Channel", edge.dst
    assert (edge.meta or {}).get("receiver_type_hint") != "Channel", edge.meta


def test_a_single_hop_still_types_the_receiver(tmp_path: Path) -> None:
    """The control: without it a blanket 'never type' would pass the test above."""
    [edge] = [e for e in _calls(tmp_path)["singleHop"] if e.dst.split(":")[-2] == "close"]
    assert _slot(edge) == "Channel", edge.dst


def test_an_in_repo_value_chain_does_not_bind_the_head_type_method(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    store_save = {e.dst for e in calls["inRepoSingle"] if "save" in e.dst}
    assert store_save and all("Store.save" in d for d in store_save), store_save
    chained = [e.dst for e in calls["inRepo"] if "save" in e.dst]
    assert chained and not any("Store.save" in d for d in chained), chained


def test_the_chain_is_stamped_field_chain(tmp_path: Path) -> None:
    """What lets the recovery linker refuse a class the caller instantiates."""
    [edge] = [e for e in _calls(tmp_path)["external"] if e.dst.split(":")[-2] == "addHandler"]
    assert (edge.meta or {}).get("receiver") == "field_chain", edge.meta


def test_a_survey_does_not_recover_the_chain_into_the_head_class(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hummingbird shape end to end: ``router.middlewares.add(..)`` must not
    become ``Router.add`` through method-call recovery."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.swift").write_text(
        "class Middlewares { func add(_ x: Int) {} }\n"
        "class Router {\n    var middlewares = Middlewares()\n    func add(_ x: Int) {}\n}\n"
        "func build() {\n    let router = Router()\n    router.middlewares.add(1)\n}\n"
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    main(["survey", str(repo), "--out", str(tmp_path / "s.json")])
    survey = json.loads((tmp_path / "s.json").read_text())
    wrong = [e for e in survey["edges"]
             if e["type"] == "calls" and e["dst"].endswith(":Router.add:method")]
    assert wrong == [], wrong
