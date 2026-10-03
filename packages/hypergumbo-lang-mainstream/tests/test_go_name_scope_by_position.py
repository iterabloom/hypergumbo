# SPDX-License-Identifier: AGPL-3.0-or-later
"""Go names are resolved by SCOPE and POSITION, not by spelling alone.

One mechanism, four filed shapes. Go's scope rules (spec, "Declarations and
scope") decide what an identifier names at the point it is written: a
parameter is in scope for its function's body; a local ``x := ...`` /
``var x`` / ``const x`` from the END of its statement to the end of the
innermost enclosing block (or ``if`` / ``for`` / ``switch`` / ``case``
clause); an import alias wherever nothing nearer binds the name. go.py
asked a position-blind question at each of these sites:

- WI-bopiv: the bare-name local-binding set counted every name bound ANYWHERE
  in the top-level declaration, so ``nodeName, err := nodeName(o)`` -- whose
  right side runs before the local exists -- lost its call to the package
  function, and so did a call in one ``if`` branch to a name bound in the
  other.
- WI-kugap: an operand that names an import was taken for the package even
  where a parameter or local of that name shadows it (``func f(os T)
  { os.Open(p) }`` named the os package's ``Open``), and the resolved-lookup
  emit sites stamped ``call_construct="function"`` on a call written on a
  receiver.
- WI-kibah: a selector function reference (``reg(http.NotFound)``,
  ``Command{RunE: pkg.Run}``) was resolved by its field name repo-wide, so a
  stdlib reference bound a repo function of that name.
- WI-labik: a package-level ``var NewWebhook = testutils.NewWebhook`` alias
  had no edge to what it holds, so a call through it dead-ended at the
  variable.

Every test pairs the shadowed (or wrong) shape with an unshadowed control in
the same fixture and asserts reach first, so a constant answer in either
direction fails.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.go import analyze_go

_GO_MOD = "module example.com/fx\n\ngo 1.21\n"


def _analyze(tmp_path: Path, files: dict[str, str]):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text(_GO_MOD)
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return analyze_go(repo)


def _ids(result, name: str) -> set[str]:
    ids = {s.id for s in result.symbols if s.name == name}
    assert ids, f"no symbol named {name!r}"  # reach first
    return ids


def _out(result, caller: str, *, edge_type: str = "calls") -> list:
    srcs = _ids(result, caller)
    return [e for e in result.edges if e.edge_type == edge_type and e.src in srcs]


def _rel(dst: str) -> str:
    """``<file>:<name>`` of an in-repo dst, or the dst itself for a placeholder."""
    parts = dst.split(":")
    path = parts[1]
    if "/repo/" not in path:
        return dst
    return f"{path.split('/repo/', 1)[1]}:{parts[-2]}"


# --------------------------------------------------------------------------
# WI-bopiv: a bare name is bound only where its binding is in scope.
# --------------------------------------------------------------------------

_BOPIV = {
    "p/p.go": (
        "package p\n\n"
        "type Obj struct{}\n\n"
        "func nodeName(o Obj) (string, error) { return \"\", nil }\n\n"
        "func open() {}\n\n"
        "func handler() {}\n\n"
        "func wrap(f func()) func() { return f }\n\n"
        "func register(f func()) {}\n"
    ),
    "p/use.go": (
        "package p\n\n"
        # The right side of the statement that declares the shadow runs first.
        "func SameStatement(o Obj) {\n"
        "\tnodeName, err := nodeName(o)\n"
        "\t_, _ = nodeName, err\n"
        "}\n\n"
        # Bound in one branch, called in the other.
        "func SiblingBlock(b bool) {\n"
        "\tif b {\n\t\topen := func() {}\n\t\topen()\n"
        "\t} else {\n\t\topen()\n\t}\n"
        "}\n\n"
        # A closure written BEFORE the local exists names the package function.
        "func BeforeBinding() {\n"
        "\trun := func() { open() }\n"
        "\topen := func() {}\n"
        "\topen()\n"
        "\trun()\n"
        "}\n\n"
        # A parameter name inside a function TYPE binds nothing in the body.
        "func FuncTypeParam(cb func(open string)) {\n\topen()\n}\n\n"
        # The argument of the defining statement is the package function too.
        "func ArgOnRightSide() {\n"
        "\thandler := wrap(handler)\n"
        "\tregister(handler)\n"
        "}\n\n"
        "func VarSpec() {\n\tvar open = wrap(open)\n\topen()\n}\n"
    ),
}


def test_the_right_side_of_a_shadowing_declaration_is_the_package_function(
    tmp_path: Path,
) -> None:
    r = _analyze(tmp_path, _BOPIV)
    dsts = [_rel(e.dst) for e in _out(r, "SameStatement")]
    assert dsts == ["p/p.go:nodeName"], dsts


def test_a_binding_in_one_branch_does_not_shadow_the_other(tmp_path: Path) -> None:
    r = _analyze(tmp_path, _BOPIV)
    edges = _out(r, "SiblingBlock")
    to_pkg = [e for e in edges if _rel(e.dst) == "p/p.go:open"]
    # Exactly one: the else-branch call. The if-branch call is the local value.
    assert len(to_pkg) == 1, [(_rel(e.dst), e.line) for e in edges]
    assert to_pkg[0].line == 13


def test_a_local_is_not_in_scope_before_its_declaration(tmp_path: Path) -> None:
    r = _analyze(tmp_path, _BOPIV)
    edges = _out(r, "BeforeBinding")
    lines = sorted(e.line for e in edges if _rel(e.dst) == "p/p.go:open")
    # Line 18 is the closure's call (package); line 20 calls the local.
    assert lines == [18], [(_rel(e.dst), e.line) for e in edges]


def test_a_function_type_parameter_name_binds_nothing(tmp_path: Path) -> None:
    r = _analyze(tmp_path, _BOPIV)
    assert [_rel(e.dst) for e in _out(r, "FuncTypeParam")] == ["p/p.go:open"]


def test_a_reference_on_the_right_side_binds_the_package_function(
    tmp_path: Path,
) -> None:
    r = _analyze(tmp_path, _BOPIV)
    refs = [
        (_rel(e.dst), e.line) for e in _out(r, "ArgOnRightSide")
        if e.evidence_type == "function_reference_arg"
    ]
    # ``wrap(handler)`` names the package function; ``register(handler)`` the local.
    assert refs == [("p/p.go:handler", 29)], refs


def test_a_var_spec_initializer_is_outside_its_own_scope(tmp_path: Path) -> None:
    r = _analyze(tmp_path, _BOPIV)
    edges = _out(r, "VarSpec")
    refs = [
        (_rel(e.dst), e.evidence_type) for e in edges
        if _rel(e.dst).endswith(":open")
    ]
    assert refs == [("p/p.go:open", "function_reference_arg")], refs


def test_switch_range_and_select_bindings_are_scoped(tmp_path: Path) -> None:
    """A type-switch alias, a ``:=`` range variable and a ``:=`` receive bind
    only inside their statement; the ``=`` forms assign and bind nothing."""
    r = _analyze(tmp_path, {
        "z/z.go": (
            "package z\n\n"
            "func open() {}\n\n"
            "var cb func()\n\n"
            "func TypeSwitch(v interface{}) {\n"
            "\tswitch open := v.(type) {\n\tcase func():\n\t\topen()\n\t}\n"
            "\tswitch v.(type) {\n\tdefault:\n\t\topen()\n\t}\n"
            "}\n\n"
            "func Ranges(fs []func()) {\n"
            "\tfor _, open := range fs {\n\t\topen()\n\t}\n"
            "\tfor _, cb = range fs {\n\t}\n"
            "\tcb()\n\topen()\n"
            "}\n\n"
            "func Selects(ch chan func()) {\n"
            "\tselect {\n\tcase open := <-ch:\n\t\topen()\n"
            "\tcase cb = <-ch:\n\t\topen()\n\t}\n"
            "}\n"
        ),
    })
    assert sorted(_rel(e.dst) for e in _out(r, "TypeSwitch")) == ["z/z.go:open"]
    # ``cb`` is assigned by ``=``, so the call is NOT a local value's: it is
    # emitted (a package var without an initializer has no symbol, hence the
    # placeholder). The ``:=`` range variable ``open`` is local in the loop only.
    assert sorted(_rel(e.dst) for e in _out(r, "Ranges")) == [
        "go:external:0-0:cb:unresolved", "z/z.go:open",
    ]
    assert sorted(_rel(e.dst) for e in _out(r, "Selects")) == ["z/z.go:open"]


def test_a_function_without_a_body_binds_nothing(tmp_path: Path) -> None:
    """An assembly-backed declaration (``func f(open int)`` with no body) has
    no scope for its parameters; the next function is unaffected."""
    r = _analyze(tmp_path, {
        "w/w.go": (
            "package w\n\n"
            "func open() {}\n\n"
            "func asm(open int)\n\n"
            "func After() { open() }\n"
        ),
    })
    assert [_rel(e.dst) for e in _out(r, "After")] == ["w/w.go:open"]


# --------------------------------------------------------------------------
# WI-kugap: an operand that names an import is the package only where no
# parameter or local shadows it; a receiver call is stamped ``method``.
# --------------------------------------------------------------------------

_KUGAP = {
    "k/k.go": (
        "package k\n\n"
        'import (\n\t"net/url"\n\t"os"\n)\n\n'
        "type T struct{}\n\n"
        "func (t T) Open(p string) {}\n\n"
        "func ParamShadow(os T, p string) { os.Open(p) }\n\n"
        "func NoShadow(p string) { os.Open(p) }\n\n"
        "func LocalShadow(raw string) {\n"
        "\turl, _ := url.Parse(raw)\n"
        "\turl.String()\n"
        "}\n"
    ),
}


def test_a_parameter_that_shadows_an_import_is_the_receiver(tmp_path: Path) -> None:
    r = _analyze(tmp_path, _KUGAP)
    shadow = _out(r, "ParamShadow")
    assert [(_rel(e.dst), (e.meta or {}).get("call_construct")) for e in shadow] == [
        ("k/k.go:T.Open", "method"),
    ], [e.dst for e in shadow]
    control = _out(r, "NoShadow")
    assert [(e.dst, (e.meta or {}).get("call_construct")) for e in control] == [
        ("go:os:0-0:Open:unresolved", "function"),
    ]


def test_a_local_that_shadows_its_import_is_not_the_package(tmp_path: Path) -> None:
    r = _analyze(tmp_path, _KUGAP)
    got = {
        e.dst.split(":")[-2]: (e.dst, (e.meta or {}).get("call_construct"))
        for e in _out(r, "LocalShadow")
    }
    # The defining call runs before the local exists: the package function.
    assert got["Parse"] == ("go:net/url:0-0:Parse:unresolved", "function")
    # The later call is on the local: no package slot, a method.
    dst, construct = got["String"]
    assert not dst.startswith("go:net/url:0-0:"), dst
    assert construct == "method"


_RESOLVED = {
    "m/thing.go": (
        "package m\n\n"
        "type Thing struct{}\n\n"
        "func (t Thing) Frob() {}\n\n"
        "func helper() {}\n\n"
        "func SameFile(xs map[string]Thing) { xs[\"k\"].Frob(); helper() }\n"
    ),
    "m/other.go": (
        "package m\n\n"
        "func OtherFile(xs map[string]Thing) { xs[\"k\"].Frob() }\n"
    ),
    "n/n.go": (
        "package n\n\n"
        'import "example.com/fx/q"\n\n'
        "func PkgCall() { q.Run() }\n"
    ),
    "q/q.go": "package q\n\nfunc Run() {}\n",
}


def test_a_resolved_receiver_call_is_stamped_method(tmp_path: Path) -> None:
    r = _analyze(tmp_path, _RESOLVED)
    for caller in ("SameFile", "OtherFile"):
        frob = [e for e in _out(r, caller) if e.dst.endswith(":Thing.Frob:method")]
        assert len(frob) == 1, (caller, [e.dst for e in _out(r, caller)])
        assert (frob[0].meta or {}).get("call_construct") == "method", caller
    # Controls: a bare call and a package-qualified in-repo call are functions.
    helper = [e for e in _out(r, "SameFile") if e.dst.endswith(":helper:function")]
    assert [(e.meta or {}).get("call_construct") for e in helper] == ["function"]
    run = _out(r, "PkgCall")
    assert [(_rel(e.dst), (e.meta or {}).get("call_construct")) for e in run] == [
        ("q/q.go:Run", "function"),
    ]


def test_an_attribute_read_on_a_shadowing_local_is_not_the_package(
    tmp_path: Path,
) -> None:
    """``os.Args`` on a parameter named ``os`` is a field of the parameter;
    reporting it as the package's argv would invent a taint source."""
    r = _analyze(tmp_path, {
        "s/s.go": (
            "package s\n\n"
            'import "os"\n\n'
            "type T struct{ Args []string }\n\n"
            "func Shadowed(os T) []string { return os.Args }\n\n"
            "func Plain() []string { return os.Args }\n"
        ),
    })
    assert _out(r, "Shadowed", edge_type="module_attr_ref") == []
    plain = [e.dst for e in _out(r, "Plain", edge_type="module_attr_ref")]
    assert plain == ["go:os:0-0:os.Args:attribute"]


def test_a_package_variable_receiver_under_a_shadowing_local_is_not_typed(
    tmp_path: Path,
) -> None:
    """``http.DefaultClient.Do`` is ``net/http.Client.Do`` only where ``http``
    is the package; on a parameter named ``http`` it is a field's method."""
    r = _analyze(tmp_path, {
        "v/v.go": (
            "package v\n\n"
            'import "net/http"\n\n'
            "type Cl struct{}\n\n"
            "func (c *Cl) Do(q *http.Request) {}\n\n"
            "type Box struct{ DefaultClient *Cl }\n\n"
            "func Shadowed(http Box, q *http.Request) { http.DefaultClient.Do(q) }\n\n"
            "func Plain(q *http.Request) { http.DefaultClient.Do(q) }\n"
        ),
    })
    plain = [e.dst for e in _out(r, "Plain")]
    assert plain == ["go:net/http.Client:0-0:Do:unresolved"], plain
    shadowed = [_rel(e.dst) for e in _out(r, "Shadowed")]
    assert shadowed == ["v/v.go:Cl.Do"], shadowed
