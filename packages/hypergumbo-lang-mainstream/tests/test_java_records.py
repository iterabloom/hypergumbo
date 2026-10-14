# SPDX-License-Identifier: AGPL-3.0-or-later
"""A java record is a type with members, like the class it abbreviates.

WI-pidos. ``record_declaration`` (Java 16) was not an enclosing type to
``_get_class_ancestors`` and had no symbol arm, so a file holding only a record
emitted NOTHING: not the record, not its methods or constructors, and so none of
the calls inside them. A record nested in a class was worse than absent: its
methods were named on the OUTER class (``Launcher.call`` for
``Launcher.RemoteLaunchCallable.call``), which gave them the outer class's
callers and overrides (measured on jenkins: 10 wrong call/dispatch edges).

The tests compare a record with the class a developer would otherwise write, so
"like a class" is asserted rather than assumed.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.java import analyze_java

_RECORD = """package p;

import java.nio.file.Files;
import java.nio.file.Path;

@Deprecated
public record R(int a, Path p) implements Comparable<R> {
    static final String K = System.getenv("K");
    public R {
        if (a < 0) throw new IllegalArgumentException();
        Files.exists(p);
    }
    public R(int a) { this(a, Path.of("x")); }
    int twice() { return a * 2; }
    static R of(int a) { return new R(a); }
    public int compareTo(R o) { return Integer.compare(a, o.a); }
    record Inner(int z) { int g() { return z; } }
}
"""


def _analyze(tmp_path: Path, source: str = _RECORD):
    (tmp_path / "R.java").write_text(source)
    return analyze_java(tmp_path)


def _by_name(analysis) -> dict[str, object]:
    return {s.name: s for s in analysis.symbols}


def test_the_record_is_a_symbol(tmp_path: Path) -> None:
    rec = _by_name(_analyze(tmp_path))["R"]
    assert rec.kind == "record"
    assert rec.qualified_name == "p.R"
    assert rec.meta["base_classes"] == ["Comparable<R>"]
    assert rec.meta["decorators"][0]["name"] == "Deprecated"


def test_members_are_named_on_the_record(tmp_path: Path) -> None:
    kinds = {name: s.kind for name, s in _by_name(_analyze(tmp_path)).items()}
    assert kinds["R.twice"] == kinds["R.of"] == kinds["R.compareTo"] == "method"
    assert kinds["R.K"] == "field"
    assert kinds["R.Inner"] == "record"
    assert kinds["R.Inner.g"] == "method"


def test_components_are_private_final_fields(tmp_path: Path) -> None:
    syms = _by_name(_analyze(tmp_path))
    assert (syms["R.a"].kind, syms["R.a"].signature) == ("field", "int")
    assert syms["R.p"].signature == "Path"
    assert syms["R.a"].modifiers == ["private", "final"]
    assert syms["R.a"].is_exported is False
    assert syms["R.Inner.z"].kind == "field"


def test_both_constructor_forms_are_constructors(tmp_path: Path) -> None:
    """The compact form ``public R { ... }`` has no parameter list; its
    signature is the record's components."""
    ctors = sorted(
        s.signature for s in _analyze(tmp_path).symbols if s.kind == "constructor"
    )
    assert ctors == ["(int a)", "(int a, Path p)"]


def test_calls_inside_a_record_are_anchored_on_its_members(tmp_path: Path) -> None:
    analysis = _analyze(tmp_path)
    names = {s.id: s.name for s in analysis.symbols}
    calls = {
        (names.get(e.src), e.dst.split(":")[-2])
        for e in analysis.edges if e.edge_type == "calls"
    }
    assert ("R", "getenv") in calls  # a field initialiser, on the record
    assert ("R.R", "exists") in calls  # inside the compact constructor
    assert ("R.compareTo", "compare") in calls
    inst = [
        (names.get(e.src), names.get(e.dst))
        for e in analysis.edges if e.edge_type == "instantiates"
    ]
    assert ("R.of", "R") in inst


def test_a_nested_records_methods_are_not_the_outer_classs(tmp_path: Path) -> None:
    """The jenkins shape: before, ``Outer.kill`` named BOTH methods."""
    analysis = _analyze(tmp_path, (
        "class Outer {\n"
        "  void kill() {}\n"
        "  record Job(int id) { void kill() {} }\n"
        "}\n"
    ))
    kills = sorted(s.name for s in analysis.symbols if s.name.endswith("kill"))
    assert kills == ["Outer.Job.kill", "Outer.kill"]


def test_the_ddg_covers_a_compact_constructor(tmp_path: Path) -> None:
    from hypergumbo_core.ddg_build import build_repo_ddg

    (tmp_path / "R.java").write_text(
        "record R(int a) {\n  R { int b = a; int c = b; }\n}\n"
    )
    ddg = build_repo_ddg(tmp_path, ("java",))
    assert ddg.ddg_symbols == {"java:R.java:2-2:R.R:constructor"}
