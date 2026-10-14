# SPDX-License-Identifier: AGPL-3.0-or-later
"""A bare Go call whose name resolves nowhere still emits a calls edge.

INV-guzuj (INV-foluz's Go shape). The bare-identifier arm of go.py's call
handling tried a local symbol, the resolver, the INV-fahub deferral and the
dot-import placeholder, and a name that fell through all of them emitted
NOTHING: ``func g() { unknownThing() }`` produced no calls edge. The resolver
is right to withhold a binding for a name it cannot place; withholding the
EDGE made the call site vanish from the graph. Every other analyzer mints an
unresolved placeholder here, and so does Python since INV-foluz.

WHAT MUST STAY SILENT, per INV-foluz's "STILL REFUSED" rule: a name bound in the
enclosing function (a local closure ``f := func(){}; f()``, a function-typed
parameter ``cb()``) is a call on a VALUE the function produced, not on an
external callee, so ``external`` would name nothing. Builtins and conversions
were already filtered and must stay so.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.go import analyze_go

_SOURCE = """package main

func g() { unknownThing() }

func closure() {
\tf := func() {}
\tf()
}

func param(cb func()) { cb() }

func rangeFn(fs []func()) {
\tfor _, fn := range fs {
\t\tfn()
\t}
}

func builtin(xs []int) int { return len(xs) }

func conv(x int) int64 { return int64(x) }

func main() {}
"""


def _calls(tmp_path: Path) -> dict[str, list]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/t\n\ngo 1.21\n")
    (repo / "main.go").write_text(_SOURCE)
    analysis = analyze_go(repo)
    names = {s.id: s.name for s in analysis.symbols}
    out: dict[str, list] = {}
    for e in analysis.edges:
        if e.edge_type == "calls":
            out.setdefault(names.get(e.src, e.src), []).append(e)
    return out


def test_an_unknown_bare_call_emits_an_unresolved_edge(tmp_path: Path) -> None:
    [edge] = _calls(tmp_path)["g"]
    assert edge.dst == "go:external:0-0:unknownThing:unresolved"
    assert edge.is_resolved is False


def test_a_call_on_a_local_binding_emits_nothing(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    assert "closure" not in calls, calls.get("closure")
    assert "param" not in calls, calls.get("param")
    assert "rangeFn" not in calls, calls.get("rangeFn")


def test_builtins_and_conversions_stay_filtered(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    assert "builtin" not in calls
    assert "conv" not in calls


def test_a_dot_import_does_not_claim_a_local_closure(tmp_path: Path) -> None:
    """The dot-import arm attributed ANY bare name to the first dot-imported
    package, a local closure included. A name the file does not bind still goes
    there; a bound one stays silent, by the same rule as the arm above."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/t\n\ngo 1.21\n")
    (repo / "main.go").write_text(
        'package main\n\nimport . "strings"\n\n'
        "func a() {\n\tf := func() {}\n\tf()\n}\n\n"
        'func b() { Contains("x", "y") }\n\nfunc main() {}\n'
    )
    analysis = analyze_go(repo)
    names = {s.id: s.name for s in analysis.symbols}
    by_fn: dict[str, list[str]] = {}
    for e in analysis.edges:
        if e.edge_type == "calls":
            by_fn.setdefault(names.get(e.src, e.src), []).append(e.dst)
    assert "a" not in by_fn, by_fn.get("a")
    assert by_fn["b"] == ["go:strings:0-0:Contains:unresolved"]
