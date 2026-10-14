# SPDX-License-Identifier: AGPL-3.0-or-later
"""A java call on a TYPE is a FUNCTION call, not a method call.

WI-fuvaj (java half; the go half was INV-tanom). ``java.py`` stamped
``call_construct="method"`` on every call with a receiver token, so
``Files.readAllBytes(p)`` and ``System.getenv(k)`` read as receiver calls. The
``unknown_receiver_scope`` caveat counts ``method`` edges as its denominator
("N of M method call site(s)"), so every static call inflated M and the untyped
share read low.

Each test pairs a shape that must flip with one that must not, so a constant
stamp in either direction fails.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.java import analyze_java

_SOURCE = """import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

class A extends Base {
    void imported(Path p) throws Exception { Files.readAllBytes(p); }
    void javaLang() { System.getenv("K"); }
    void qualified(Path p) throws Exception { java.nio.file.Files.readString(p); }
    void typedLocal(String s) throws Exception { File f = new File(s); f.createNewFile(); }
    void param(File f) { f.delete(); }
    void self() { this.close(); }
    void inherited(String x) { LOG.info(x); }
    void shadowed() { List<String> List = null; List.size(); }
    void bare() { helper(); }
}
"""


def _constructs(tmp_path: Path) -> dict[tuple[str, str], object]:
    (tmp_path / "A.java").write_text(_SOURCE)
    analysis = analyze_java(tmp_path)
    names = {s.id: s.name for s in analysis.symbols}
    out: dict[tuple[str, str], object] = {}
    for e in analysis.edges:
        if e.edge_type != "calls":
            continue
        caller = names.get(e.src, e.src).rsplit(".", 1)[-1]
        callee = e.dst.split(":")[-2]
        out[(caller, callee)] = (e.meta or {}).get("call_construct")
    return out


def test_a_call_on_a_type_is_a_function(tmp_path: Path) -> None:
    got = _constructs(tmp_path)
    assert got[("imported", "readAllBytes")] == "function"
    assert got[("javaLang", "getenv")] == "function"
    assert got[("qualified", "readString")] == "function"


def test_a_call_on_a_value_stays_a_method(tmp_path: Path) -> None:
    """The control. ``LOG`` is capitalised but neither imported nor java.lang
    (an inherited field), and a local named ``List`` shadows the import: both
    are values, so both stay receiver calls."""
    got = _constructs(tmp_path)
    assert got[("typedLocal", "createNewFile")] == "method"
    assert got[("param", "delete")] == "method"
    assert got[("self", "close")] == "method"
    assert got[("inherited", "info")] == "method"
    assert got[("shadowed", "size")] == "method"


def test_a_bare_call_carries_no_construct(tmp_path: Path) -> None:
    assert _constructs(tmp_path)[("bare", "helper")] is None


def test_the_module_slot_is_unchanged(tmp_path: Path) -> None:
    """The fix changes the construct only; the slot the catalogue reads still
    names the owner."""
    (tmp_path / "A.java").write_text(_SOURCE)
    dsts = {e.dst for e in analyze_java(tmp_path).edges if e.edge_type == "calls"}
    assert "java:java.nio.file.Files:0-0:readAllBytes:unresolved" in dsts
    assert "java:java.lang.System:0-0:getenv:unresolved" in dsts


def test_the_unknown_receiver_denominator_counts_only_receiver_calls(
    tmp_path: Path,
) -> None:
    """The caveat's "N of M method call site(s)", fed the real analyzer's
    edges. The file holds 3 calls on a type and 5 on a value; before WI-fuvaj
    all 8 were counted."""
    from hypergumbo_core.io_boundary import load_catalog
    from hypergumbo_core.verify_claims import unknown_receiver_scope

    (tmp_path / "A.java").write_text(_SOURCE)
    raw = [e.to_dict() for e in analyze_java(tmp_path).edges]
    _sites, total, _names = unknown_receiver_scope(raw, {"java": load_catalog("java")})
    assert total == 5
