# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-bivin: a BARE Go identifier is resolved inside the caller's own package.

THE GAP. Go scoping answers a bare identifier exactly: it names a declaration
of the caller's OWN package (any file of that package directory), a builtin, a
dot-imported name, or a local binding. No other package can be meant. The
bare-identifier arm of go.py checked the caller's own FILE and then asked the
repo-wide ``ListNameResolver`` with no path hint, so a same-package callee in
another file was looked up among every package's declarations of that name:

- three or more packages declaring it: the ambiguity guard withheld the bind
  and the call became the ``external`` placeholder (alertmanager +124, beads
  +379 at filing) -- a recall loss;
- exactly two: the path-sorted FIRST candidate was bound at 0.57, a
  WRONG-PACKAGE edge whenever the caller's package sorts later;
- exactly one, in another package: bound at 0.80, though Go makes that
  binding impossible without a dot import.

THE RULE. The caller's package -- its directory AND its ``package`` clause, so
an external test package ``x_test`` is told apart from ``x`` in the same
directory -- is the scope a bare identifier resolves in. A candidate of any
other package is never chosen for a bare identifier, except through a dot
import. The same rule covers the two other places a bare identifier is
resolved: a function passed as an argument (``register(handler)``) and a
struct-literal field value (``cobra.Command{RunE: run}``). A method or field
is never the target of a bare identifier, even in the caller's own file.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.go import analyze_go

_GO_MOD = "module example.com/fx\n\ngo 1.21\n"


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text(_GO_MOD)
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return repo


#: A call site's edge: ``ast_call`` when bound, ``ast_call_direct`` for the
#: ``make_unresolved_edge`` placeholder.
_CALL = ("ast_call", "ast_call_direct")


def _calls_from(repo: Path, caller: str, *, evidence: tuple[str, ...] = _CALL) -> list:
    """Every ``calls`` edge (of the given evidence types) out of ``caller``."""
    result = analyze_go(repo)
    srcs = {s.id for s in result.symbols if s.name == caller}
    assert srcs, f"no symbol named {caller!r}"  # reach first
    return [
        e for e in result.edges
        if e.edge_type == "calls" and e.src in srcs and e.evidence_type in evidence
    ]


def _dst_path(edge) -> str:
    """The repo-relative file of an edge's dst (``external`` for a placeholder)."""
    path = edge.dst.split(":")[1]
    return path.split("/repo/", 1)[1] if "/repo/" in path else path


def _new_in(pkg: str) -> str:
    return f"package {pkg}\n\nfunc New() int {{ return 1 }}\n"


def _use_new(pkg: str, fn: str) -> str:
    return f"package {pkg}\n\nfunc {fn}() int {{ return New() }}\n"


def test_two_candidates_bind_the_callers_package_not_the_first_sorted(
    tmp_path: Path,
) -> None:
    """N=2 was a WRONG-PACKAGE bind: b's bare New() went to a/a.go at 0.57."""
    repo = _repo(tmp_path, {
        "a/a.go": _new_in("a"),
        "b/b.go": _new_in("b"),
        "b/use.go": _use_new("b", "UseB"),
    })
    [edge] = _calls_from(repo, "UseB")
    assert _dst_path(edge) == "b/b.go"
    assert edge.confidence == 0.80


def test_three_candidates_bind_instead_of_the_external_placeholder(
    tmp_path: Path,
) -> None:
    """N>=3 was the ``external`` placeholder for every one of these calls."""
    repo = _repo(tmp_path, {
        "a/a.go": _new_in("a"),
        "a/use.go": _use_new("a", "UseA"),
        "b/b.go": _new_in("b"),
        "b/use.go": _use_new("b", "UseB"),
        "c/c.go": _new_in("c"),
    })
    for caller, home in (("UseA", "a/a.go"), ("UseB", "b/b.go")):
        [edge] = _calls_from(repo, caller)
        assert _dst_path(edge) == home, edge.dst
        assert edge.is_resolved is True


def test_a_sole_candidate_in_another_package_is_not_bound(tmp_path: Path) -> None:
    """A bare name declared only in ANOTHER package cannot be that declaration."""
    repo = _repo(tmp_path, {
        "a/a.go": "package a\n\nfunc Helper() int { return 1 }\n",
        "b/use.go": "package b\n\nfunc UseB() int { return Helper() }\n",
    })
    [edge] = _calls_from(repo, "UseB")
    assert edge.dst == "go:external:0-0:Helper:unresolved"
    assert edge.is_resolved is False


def test_the_external_test_package_is_its_own_scope(tmp_path: Path) -> None:
    """``package a_test`` shares a's directory but not its declarations."""
    repo = _repo(tmp_path, {
        "a/a.go": "package a\n\nfunc helper() int { return 1 }\n\nfunc New() int { return 1 }\n",
        "a/helpers_ext_test.go": "package a_test\n\nfunc helper() int { return 2 }\n",
        "a/a_ext_test.go": (
            "package a_test\n\nimport \"testing\"\n\n"
            "func TestExt(t *testing.T) { _ = helper(); _ = New() }\n"
        ),
        "a/a_int_test.go": (
            "package a\n\nimport \"testing\"\n\n"
            "func TestInt(t *testing.T) { _ = helper() }\n"
        ),
    })
    ext = {e.dst.split(":")[-2]: e for e in _calls_from(repo, "TestExt")}
    assert _dst_path(ext["helper"]) == "a/helpers_ext_test.go"
    # ``New`` lives in package a; the external test package must write ``a.New``.
    assert ext["New"].dst == "go:external:0-0:New:unresolved"
    [internal] = _calls_from(repo, "TestInt")
    assert _dst_path(internal) == "a/a.go"


def test_build_constraint_variants_stay_in_the_package(tmp_path: Path) -> None:
    """Three per-OS variants of one function are all the caller's own package:
    the call binds one of them (deterministically, at reduced confidence)
    rather than falling to ``external`` or leaving the package."""
    variant = "//go:build {os}\n\npackage a\n\nfunc platform() int {{ return 1 }}\n"
    repo = _repo(tmp_path, {
        "a/p_linux.go": variant.format(os="linux"),
        "a/p_darwin.go": variant.format(os="darwin"),
        "a/p_windows.go": variant.format(os="windows"),
        "a/use.go": "package a\n\nfunc UseA() int { return platform() }\n",
        "b/p.go": "package b\n\nfunc platform() int { return 2 }\n",
    })
    [edge] = _calls_from(repo, "UseA")
    assert _dst_path(edge) == "a/p_darwin.go"
    assert edge.is_resolved is True
    assert round(edge.confidence, 3) == round(0.80 / 3 ** 0.5, 3)


def test_a_dot_import_still_reaches_the_imported_package(tmp_path: Path) -> None:
    """A dot import is the one way a bare name names another package."""
    repo = _repo(tmp_path, {
        "a/a.go": "package a\n\nfunc Helper() int { return 1 }\n",
        "c/c.go": "package c\n\nfunc Helper() int { return 3 }\n",
        "b/use.go": (
            'package b\n\nimport . "example.com/fx/a"\n\n'
            "func UseB() int { return Helper() }\n"
        ),
    })
    [edge] = _calls_from(repo, "UseB")
    assert _dst_path(edge) == "a/a.go"


def test_a_method_in_the_callers_file_is_not_a_bare_target(tmp_path: Path) -> None:
    """``func (s *S) Close()`` is registered in the file's symbol map under its
    bare name too; a bare ``Close()`` still means the package function."""
    repo = _repo(tmp_path, {
        "a/close.go": "package a\n\nfunc Close() {}\n",
        "a/s.go": (
            "package a\n\ntype S struct{}\n\nfunc (s *S) Close() {}\n\n"
            "func UseA() { Close() }\n"
        ),
    })
    [edge] = _calls_from(repo, "UseA")
    assert _dst_path(edge) == "a/close.go", edge.dst


def test_a_function_reference_argument_binds_the_callers_package(
    tmp_path: Path,
) -> None:
    """``register(handler)``: the same scoping rule as a bare call."""
    handler = "package {p}\n\nfunc handler() {{}}\n"
    repo = _repo(tmp_path, {
        "a/h.go": handler.format(p="a"),
        "b/h.go": handler.format(p="b"),
        "b/use.go": (
            "package b\n\nfunc register(f func()) {}\n\n"
            "func UseB() { register(handler) }\n"
        ),
    })
    [edge] = _calls_from(repo, "UseB", evidence=("function_reference_arg",))
    assert _dst_path(edge) == "b/h.go"


def test_a_struct_field_reference_binds_the_callers_package(
    tmp_path: Path,
) -> None:
    """``Command{Run: run}``: the same scoping rule as a bare call."""
    run = "package {p}\n\nfunc run() {{}}\n"
    repo = _repo(tmp_path, {
        "a/r.go": run.format(p="a"),
        "b/r.go": run.format(p="b"),
        "b/use.go": (
            "package b\n\ntype Command struct{ Run func() }\n\n"
            "func UseB() Command { return Command{Run: run} }\n"
        ),
    })
    [edge] = _calls_from(repo, "UseB", evidence=("struct_field_reference",))
    assert _dst_path(edge) == "b/r.go"


def test_references_to_another_packages_sole_function_are_not_bound(
    tmp_path: Path,
) -> None:
    """Both reference arms: a bare name declared only elsewhere binds nothing."""
    repo = _repo(tmp_path, {
        "a/h.go": "package a\n\nfunc handler() {}\n",
        "b/use.go": (
            "package b\n\ntype Command struct{ Run func() }\n\n"
            "func register(f func()) {}\n\n"
            "func UseB() Command { register(handler); return Command{Run: handler} }\n"
        ),
    })
    assert _calls_from(repo, "UseB", evidence=("function_reference_arg",)) == []
    assert _calls_from(repo, "UseB", evidence=("struct_field_reference",)) == []


def test_a_non_test_file_cannot_reach_a_test_files_helper(tmp_path: Path) -> None:
    """``go build`` never compiles ``_test.go``: package code cannot call into it,
    while the package's own test files can."""
    repo = _repo(tmp_path, {
        "a/h_test.go": "package a\n\nfunc helper() int { return 1 }\n",
        "a/use.go": "package a\n\nfunc UseA() int { return helper() }\n",
        "a/use_test.go": (
            "package a\n\nimport \"testing\"\n\n"
            "func TestUse(t *testing.T) { _ = helper() }\n"
        ),
        "b/h.go": "package b\n\nfunc helper() int { return 2 }\n",
    })
    [edge] = _calls_from(repo, "UseA")
    assert edge.dst == "go:external:0-0:helper:unresolved"
    [from_test] = _calls_from(repo, "TestUse")
    assert _dst_path(from_test) == "a/h_test.go"


def test_a_local_binding_shadows_the_package_declaration(tmp_path: Path) -> None:
    """A parameter or closure named like a package function is that local value:
    no call, argument or field-value edge reaches the package function."""
    repo = _repo(tmp_path, {
        "a/o.go": "package a\n\nfunc open() {}\n",
        "a/use.go": (
            "package a\n\ntype T struct{ Run func() }\n\n"
            "func register(f func()) {}\n\n"
            "func UseParam(open func()) { open() }\n\n"
            "func UseClosure() T {\n\topen := func() {}\n\topen()\n"
            "\tregister(open)\n\treturn T{Run: open}\n}\n\n"
            "func UsePackage() T { open(); register(open); return T{Run: open} }\n"
        ),
    })
    every = _CALL + ("function_reference_arg", "struct_field_reference")
    for caller in ("UseParam", "UseClosure"):
        hits = [
            e for e in _calls_from(repo, caller, evidence=every)
            if e.dst.split(":")[-2] == "open"
        ]
        assert hits == [], (caller, [e.dst for e in hits])
    # Control: unshadowed, all three forms reach the package function.
    reached = sorted(
        e.evidence_type for e in _calls_from(repo, "UsePackage", evidence=every)
        if _dst_path(e) == "a/o.go"
    )
    assert reached == ["ast_call", "function_reference_arg", "struct_field_reference"]


def test_a_bare_call_in_a_method_is_never_an_embedded_types_method(
    tmp_path: Path,
) -> None:
    """Go has no implicit receiver: inside ``func (c *Caller) Run()`` a bare
    ``Process()`` is the package function even when ``Caller`` embeds a type
    with a ``Process`` method (promotion needs ``c.Process()``). On dev the
    resolver picked the method, INV-fahub withheld it with an
    ``enclosing_class`` stamp, and the inherited_calls Site-1 walker then bound
    ``Caller.Run -> TypeA.Process`` at 0.9."""
    repo = _repo(tmp_path, {
        "a_typea.go": "package main\n\ntype TypeA struct{}\n\nfunc (a *TypeA) Process() {}\n",
        "proc.go": "package main\n\nfunc Process() {}\n",
        "caller.go": (
            "package main\n\ntype Caller struct{ TypeA }\n\n"
            "func (c *Caller) Run() {\n\tProcess()\n}\n\nfunc main() {}\n"
        ),
    })
    [edge] = _calls_from(repo, "Caller.Run")
    assert _dst_path(edge) == "proc.go"
    assert "enclosing_class" not in (edge.meta or {})
