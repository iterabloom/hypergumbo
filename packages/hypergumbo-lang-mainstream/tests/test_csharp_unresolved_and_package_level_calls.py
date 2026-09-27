# SPDX-License-Identifier: AGPL-3.0-or-later
"""C# emits a calls edge for an external callee, and for a call in no method.

INV-bamij recorded C# as "a different defect": its sweep fixture emitted ZERO
calls edges, even inside a named method. Re-read at dev ecab74be2e: the call
arm resolves a name locally or through the resolver and, when neither finds
it, emits NOTHING (there is no else), and a name with 3+ repository candidates
hits a bare ``continue``. So ``File.ReadAllText("z")`` inside a method, the
canonical I/O call, vanished, and so did every other call into the framework.
INV-foluz: every parsed call site emits a calls edge.

The placeholder names what the source names and nothing more. A capitalised
receiver that is not a local variable (``File``) is the owner type the slot
should carry; a local variable receiver (``sock.Send``) is NOT a module, and
its name never goes in the slot (INV-kotob), so the call is ``external``.

The second half is INV-bamij's own shape: a call in a static field's lambda is
anchored on the class, as java's field initialisers now are.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.csharp import analyze_csharp

_SOURCE = """using System;
using System.IO;
using System.Net.Sockets;
public class M {
    static readonly Action Handler = () => { Helper(); File.ReadAllText("/etc/x"); };
    static void Helper() { }
    void Named(Socket sock) {
        Helper();
        File.ReadAllText("z");
        sock.Send(new byte[1]);
    }
}
"""


def _calls(tmp_path: Path) -> dict[int, list[tuple[str, str, dict]]]:
    (tmp_path / "M.cs").write_text(_SOURCE)
    analysis = analyze_csharp(tmp_path)
    kinds = {s.id: s.kind for s in analysis.symbols}
    out: dict[int, list[tuple[str, str, dict]]] = {}
    for e in analysis.edges:
        if e.edge_type == "calls" and e.line is not None:
            out.setdefault(e.line, []).append(
                (kinds.get(e.src, e.src.rsplit(":", 1)[-1]), e.dst, e.meta or {}),
            )
    return out


def _slot(dst: str) -> str:
    return dst.split(":", 1)[1].split(":0-0:", 1)[0]


def test_an_external_static_call_names_its_type(tmp_path: Path) -> None:
    [(_kind, dst, _meta)] = _calls(tmp_path)[9]
    assert dst.split(":")[-2] == "ReadAllText", dst
    assert _slot(dst) == "File", dst


def test_a_call_on_a_local_variable_does_not_put_the_variable_in_the_slot(
    tmp_path: Path,
) -> None:
    [(_kind, dst, meta)] = _calls(tmp_path)[10]
    assert dst.split(":")[-2] == "Send", dst
    assert _slot(dst) == "external", dst
    assert meta.get("call_construct") == "method", meta


def test_an_in_repo_call_still_resolves(tmp_path: Path) -> None:
    """The control: the new placeholder must not replace a real binding."""
    [(_kind, dst, _meta)] = _calls(tmp_path)[8]
    assert dst.endswith(":M.Helper:method"), dst


def test_a_static_field_lambda_is_anchored_on_its_class(tmp_path: Path) -> None:
    line5 = _calls(tmp_path).get(5, [])
    assert {kind for kind, _dst, _meta in line5} == {"class"}, line5
    assert {d.split(":")[-2] for _k, d, _m in line5} >= {"M.Helper", "ReadAllText"}, line5
