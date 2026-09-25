# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Rust instance-method call on a receiver whose type the signature declares
emits its edge even when the method name is on the generic-trait denylist
(INV-pamis).

``pub fn exfiltrate(sock: &UdpSocket, payload: &[u8]) { sock.send(payload) }``
emitted NOTHING: ``send`` is on ``SUPPRESSED_METHOD_NAMES["rust"]`` -- the
denylist that stops ``x.clone()`` / ``v.into()`` on an untypable receiver from
binding to an arbitrary impl or bloating the graph -- and the emission gate
applied it before looking at the receiver. The type is in hand by construction
(``&UdpSocket`` resolves through ``use std::net::UdpSocket``), so the edge now
carries ``std::net::UdpSocket`` in the slot and the catalogued net_send row is
reachable. An untypable receiver keeps the denylist and its disclosure.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.rust import analyze_rust


def _edges(root: Path, src: str) -> list[Edge]:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "Cargo.toml").write_text("[package]\nname = \"fx\"\nversion = \"0.1.0\"\n")
    (root / "src" / "lib.rs").write_text(src)
    return analyze_rust(root).edges


def _calls(edges: list[Edge], fn: str) -> list[Edge]:
    return [e for e in edges if e.edge_type == "calls" and f":{fn}:" in e.src]


FIXTURE = (
    "use std::io::Write;\n"
    "use std::net::UdpSocket;\n"
    "\n"
    "pub fn exfiltrate(sock: &UdpSocket, payload: &[u8]) -> std::io::Result<usize> {\n"
    "    sock.send(payload)\n"
    "}\n"
    "\n"
    "pub fn drain<W: Write>(w: &mut W, payload: &[u8]) -> std::io::Result<()> {\n"
    "    w.write_all(payload)\n"
    "}\n"
    "\n"
    "pub fn blind(x: &dyn std::any::Any) {\n"
    "    let _ = x.type_id().clone();\n"
    "}\n"
)


def test_declared_receiver_emits_the_denylisted_sink(tmp_path: Path) -> None:
    edges = _edges(tmp_path, FIXTURE)
    sends = [e for e in _calls(edges, "exfiltrate") if e.dst.endswith(":send:unresolved")]
    assert len(sends) == 1, [e.dst for e in _calls(edges, "exfiltrate")]
    e = sends[0]
    assert e.dst == "rust:std::net::UdpSocket:0-0:send:unresolved", e.dst
    assert e.dst_ref is not None and e.dst_ref.module_path == "std::net::UdpSocket"
    assert (e.meta or {}).get("call_construct") == "method"
    assert tag_io_boundaries(edges, {"rust": load_catalog("rust")}) >= 1


def test_generic_parameter_receiver_still_emits_untyped(tmp_path: Path) -> None:
    edges = _edges(tmp_path, FIXTURE)
    writes = [e for e in _calls(edges, "drain") if e.dst.endswith(":write_all:unresolved")]
    assert len(writes) == 1 and writes[0].dst == "rust:external:0-0:write_all:unresolved"


def test_untypable_receiver_keeps_the_denylist(tmp_path: Path) -> None:
    """``.clone()`` on a receiver nobody typed stays suppressed (the bloat guard)."""
    edges = _edges(tmp_path, FIXTURE)
    assert not [e for e in _calls(edges, "blind") if e.dst.endswith(":clone:unresolved")]
