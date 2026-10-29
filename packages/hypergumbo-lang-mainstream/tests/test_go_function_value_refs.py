# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Go function VALUE outside a call or a keyed field names its function (META-mino).

go.py records a function reference as a call ARGUMENT (``register(h)``,
``function_reference_arg``), a KEYED composite-literal field
(``Command{Run: run}``, ``struct_field_reference``) and a package-level alias
(``var run = helper``, ``function_reference``). Two value positions had no
edge, so a function referenced only there looked uncalled:

- an UNKEYED composite-literal element -- ``[]func(){a, b}``, the dispatch
  table -- and
- the right side of a LOCAL binding: ``f := handler``, ``h = pkg.Fn``,
  ``var g = run`` inside a function.

Both now resolve as the keyed field does (WI-bivin's own-package scoping, a
local binding shadowing, a selector by its operand) and carry
``function_reference``.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.go import analyze_go

_GO_MOD = "module example.com/m\n\ngo 1.22\n"


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text(_GO_MOD)
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return repo


def _refs_from(repo: Path, caller: str) -> list[tuple[str, str]]:
    """(dst name, dst file) of every ``function_reference`` edge out of ``caller``."""
    result = analyze_go(repo)
    srcs = {s.id for s in result.symbols if s.name == caller}
    assert srcs, f"no symbol named {caller!r}"
    out = []
    for e in result.edges:
        if e.src in srcs and e.edge_type == "calls" and e.evidence_type == "function_reference":
            path = e.dst.split(":")[1]
            out.append((e.dst.split(":")[-2], path.split("/repo/", 1)[-1]))
    return sorted(out)


_TARGETS = "package a\n\nfunc a1() int { return 1 }\nfunc a2() int { return 2 }\n"


def test_an_unkeyed_slice_element_names_its_function(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {
        "a/t.go": _TARGETS,
        "a/use.go": (
            "package a\n\nfunc Use() int {\n"
            "\ttable := []func() int{a1, a2}\n\treturn table[0]()\n}\n"
        ),
    })
    assert _refs_from(repo, "Use") == [("a1", "a/t.go"), ("a2", "a/t.go")]


def test_a_package_level_dispatch_table_names_its_functions(tmp_path: Path) -> None:
    """At package level the reference is anchored on the file, as a
    package-level call is (INV-bamij's Go twin)."""
    repo = _repo(tmp_path, {
        "a/t.go": _TARGETS,
        "a/table.go": "package a\n\nvar handlers = [...]func() int{a1, a2}\n",
    })
    result = analyze_go(repo)
    refs = [
        e for e in result.edges
        if e.evidence_type == "function_reference" and "table.go" in e.src
    ]
    assert sorted(e.dst.split(":")[-2] for e in refs) == ["a1", "a2"]


def test_a_local_short_declaration_names_its_function(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {
        "a/t.go": _TARGETS,
        "a/use.go": "package a\n\nfunc Use() int {\n\tf := a1\n\treturn f()\n}\n",
    })
    assert _refs_from(repo, "Use") == [("a1", "a/t.go")]


def test_an_assignment_and_a_local_var_name_their_functions(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {
        "a/t.go": _TARGETS,
        "a/use.go": (
            "package a\n\nfunc Use() int {\n"
            "\tvar g = a1\n\tvar h func() int\n\th = a2\n\treturn g() + h()\n}\n"
        ),
    })
    assert _refs_from(repo, "Use") == [("a1", "a/t.go"), ("a2", "a/t.go")]


def test_a_selector_value_resolves_by_its_operand(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {
        "b/b.go": "package b\n\nfunc Run() int { return 3 }\n",
        "a/use.go": (
            "package a\n\nimport \"example.com/m/b\"\n\n"
            "func Use() int {\n\tf := b.Run\n\tt := []func() int{b.Run}\n"
            "\treturn f() + t[0]()\n}\n"
        ),
    })
    assert _refs_from(repo, "Use") == [("Run", "b/b.go"), ("Run", "b/b.go")]


def test_a_local_name_is_not_the_package_function(tmp_path: Path) -> None:
    """``a1 := ...; f := a1`` and ``[]func() int{a1}`` with a1 a parameter name
    the local value, not the package function."""
    repo = _repo(tmp_path, {
        "a/t.go": _TARGETS,
        "a/use.go": (
            "package a\n\nfunc Use(a1 func() int) int {\n"
            "\tf := a1\n\tt := []func() int{a1}\n\treturn f() + t[0]()\n}\n"
        ),
    })
    assert _refs_from(repo, "Use") == []


def test_values_that_are_not_functions_name_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {
        "a/t.go": _TARGETS + "\nvar limit = 3\n\ntype P struct{ X, Y int }\n",
        "a/use.go": (
            "package a\n\nfunc Use() int {\n"
            "\tn := limit\n\tp := P{n, limit}\n\txs := []int{limit}\n"
            "\tf := func() int { return 1 }\n\treturn p.X + xs[0] + f()\n}\n"
        ),
    })
    assert _refs_from(repo, "Use") == []


def test_a_call_on_the_right_is_a_call_not_a_reference(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {
        "a/t.go": _TARGETS,
        "a/use.go": "package a\n\nfunc Use() int {\n\tx := a1()\n\treturn x\n}\n",
    })
    assert _refs_from(repo, "Use") == []


def test_a_keyed_field_is_one_struct_field_reference_not_two_edges(
    tmp_path: Path,
) -> None:
    """The value of ``T{Run: a1}`` is a literal_element too, under the
    keyed_element: it keeps its one ``struct_field_reference``."""
    repo = _repo(tmp_path, {
        "a/t.go": _TARGETS,
        "a/use.go": (
            "package a\n\ntype T struct{ Run func() int }\n\n"
            "func Use() T { return T{Run: a1} }\n"
        ),
    })
    result = analyze_go(repo)
    use = {s.id for s in result.symbols if s.name == "Use"}
    hits = sorted(
        e.evidence_type for e in result.edges
        if e.src in use and e.dst.split(":")[-2] == "a1"
    )
    assert hits == ["struct_field_reference"]
