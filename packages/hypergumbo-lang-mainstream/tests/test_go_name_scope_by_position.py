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


# --------------------------------------------------------------------------
# WI-kibah: a selector function reference is resolved by its OPERAND.
# --------------------------------------------------------------------------

_KIBAH = {
    "a/a.go": "package a\n\nfunc NotFound() {}\n\nfunc Serve() {}\n",
    "c/c.go": "package c\n\nfunc NotFound() {}\n",
    "h/h.go": (
        "package h\n\n"
        "type Handler struct{}\n\n"
        "func (h *Handler) Serve() {}\n\n"
        "type Other struct{}\n\n"
        "func (o *Other) Serve() {}\n"
    ),
    "b/b.go": (
        "package b\n\n"
        'import (\n\t"net/http"\n\n\t"example.com/fx/a"\n'
        '\t"example.com/fx/c"\n\t"example.com/fx/h"\n)\n\n'
        "type C struct{ H func() }\n\n"
        "func reg(f func()) {}\n\n"
        "func External() { reg(http.NotFound) }\n\n"
        "func ExternalField() C { return C{H: http.NotFound} }\n\n"
        "func InRepo() { reg(a.NotFound) }\n\n"
        "func InRepoSecond() C { return C{H: c.NotFound} }\n\n"
        "func Typed() {\n\tx := &h.Handler{}\n\treg(x.Serve)\n}\n\n"
        "func TypedField(x *h.Other) C { return C{H: x.Serve} }\n\n"
        "func ShadowedAlias(a *h.Handler) { reg(a.Serve) }\n"
    ),
}


def _refs(r, caller: str) -> list[str]:
    return sorted(
        _rel(e.dst) for e in _out(r, caller)
        if e.evidence_type in ("function_reference_arg", "struct_field_reference")
    )


def test_an_external_package_reference_binds_no_repo_function(
    tmp_path: Path,
) -> None:
    r = _analyze(tmp_path, _KIBAH)
    assert _refs(r, "External") == []
    assert _refs(r, "ExternalField") == []
    # The read itself is still recorded, as an attribute of the package.
    reads = [e.dst for e in _out(r, "External", edge_type="module_attr_ref")]
    assert reads == ["go:net/http:0-0:net/http.NotFound:attribute"]


def test_an_in_repo_package_reference_binds_that_package(tmp_path: Path) -> None:
    r = _analyze(tmp_path, _KIBAH)
    # Two packages declare NotFound: each operand picks its own.
    assert _refs(r, "InRepo") == ["a/a.go:NotFound"]
    assert _refs(r, "InRepoSecond") == ["c/c.go:NotFound"]


def test_a_typed_operand_binds_its_types_method(tmp_path: Path) -> None:
    r = _analyze(tmp_path, _KIBAH)
    assert _refs(r, "Typed") == ["h/h.go:Handler.Serve"]
    assert _refs(r, "TypedField") == ["h/h.go:Other.Serve"]


def test_a_parameter_shadowing_an_import_is_the_receiver(tmp_path: Path) -> None:
    """``a`` is the ``*h.Handler`` parameter here, not package ``a`` (whose
    ``Serve`` function the name-only lookup would also accept)."""
    r = _analyze(tmp_path, _KIBAH)
    assert _refs(r, "ShadowedAlias") == ["h/h.go:Handler.Serve"]


def test_a_method_expression_binds_its_types_method(tmp_path: Path) -> None:
    r = _analyze(tmp_path, {
        "x/x.go": (
            "package x\n\n"
            "type Handler struct{}\n\n"
            "func (h Handler) Serve() {}\n\n"
            "func reg(f func(Handler)) {}\n\n"
            "func regP(f func(*Handler)) {}\n\n"
            "func UseExpr() {\n"
            "\tregP((*Handler).Serve)\n\treg(Handler.Serve)\n"
            "\tregP((*Handler).Missing)\n\treg(Handler.Missing)\n}\n"
        ),
    })
    # Both spellings name the type's method: ``(*Handler).Serve`` on the
    # pointer type, ``Handler.Serve`` on the value type.
    assert _refs(r, "UseExpr") == ["x/x.go:Handler.Serve", "x/x.go:Handler.Serve"]


def test_a_literal_or_var_initialized_operand_names_its_type(
    tmp_path: Path,
) -> None:
    """``(&T{}).M`` names ``T``'s method by its own literal, and ``var a =
    &T{}`` types ``a`` as ``a := &T{}`` does; an operand whose type is not
    known binds nothing, whatever the method's name (no repo-wide pick)."""
    r = _analyze(tmp_path, {
        "y/y.go": (
            "package y\n\n"
            "type T struct{}\n\n"
            "func (t *T) Serve() {}\n\n"
            "type U struct{}\n\n"
            "func (u U) Only() {}\n\n"
            "func reg(f func()) {}\n\n"
            "func mk() interface{ Only() } { return U{} }\n\n"
            "func Literal() { reg((&T{}).Serve); reg(U{}.Only); reg(T{}.Nope) }\n\n"
            "func VarInit() {\n\tvar a = &T{}\n\treg(a.Serve)\n}\n\n"
            "func Unknown() {\n\tv := mk()\n\treg(v.Only)\n\treg(mk().Only)\n}\n"
        ),
    })
    assert _refs(r, "Literal") == ["y/y.go:T.Serve", "y/y.go:U.Only"]
    assert _refs(r, "VarInit") == ["y/y.go:T.Serve"]
    assert _refs(r, "Unknown") == []


_TYPED_PACKAGES = {
    "eureka/e.go": "package eureka\n\ntype SDConfig struct{}\n\nfunc (c *SDConfig) Unmarshal() {}\n",
    "consul/c.go": "package consul\n\ntype SDConfig struct{}\n\nfunc (c *SDConfig) Unmarshal() {}\n",
    "consul/use.go": (
        "package consul\n\n"
        "func reg(f func()) {}\n\n"
        "func Use() {\n\tvar config SDConfig\n\tconfig.Unmarshal()\n"
        "\treg(config.Unmarshal)\n}\n"
    ),
    "h/h.go": "package h\n\ntype Ghost struct{}\n",
    "g/g.go": "package g\n\ntype Ghost struct{}\n\nfunc (g *Ghost) Serve() {}\n",
    "q/q.go": (
        "package q\n\ntype Engine struct{}\n\ntype Result struct{}\n\n"
        "func (e *Engine) Query() *Result { return nil }\n\n"
        "func (r *Result) Rows() {}\n"
    ),
    "k/k.go": (
        "package k\n\n"
        'import (\n\t"example.com/fx/h"\n\t"example.com/fx/q"\n)\n\n'
        "func reg(f func()) {}\n\n"
        "type F struct{}\n\n"
        "func Wrong(x *h.Ghost) { reg(x.Serve); x.Serve() }\n\n"
        "func Registry(e *q.Engine) {\n\tr := e.Query()\n\tr.Rows()\n\treg(r.Rows)\n}\n\n"
        "func Unimported(f F) {\n\tt := f.NewGhost()\n\treg(t.Serve)\n}\n"
    ),
    "tu/tu.go": "package tu\n\ntype AT struct{}\n\nfunc (t *AT) Collector() {}\n",
    "d1/d.go": "package d1\n\ntype Dup struct{}\n\nfunc (Dup) Go() {}\n",
    "d2/d.go": "package d2\n\ntype Dup struct{}\n\nfunc (Dup) Go() {}\n",
    "wa/wa.go": (
        "package wa\n\n"
        'import (\n\t"example.com/fx/d1"\n\t"example.com/fx/tu"\n)\n\n'
        "type AT struct{ *tu.AT }\n\n"
        "type W2 struct{ d1.Dup }\n\n"
        "func NewAT() *AT { return &AT{} }\n\n"
        "func NewW2() *W2 { return &W2{} }\n"
    ),
    "cc/cc.go": (
        "package cc\n\n"
        'import "example.com/fx/wa"\n\n'
        "func reg(f func()) {}\n\n"
        "func Promoted() {\n\tat := wa.NewAT()\n\tat.Collector()\n"
        "\treg(at.Collector)\n\tat.Missing()\n"
        "\tw2 := wa.NewW2()\n\tw2.Go()\n\treg(w2.Go)\n}\n"
    ),
}


def test_a_typed_receiver_binds_the_method_of_its_own_package(
    tmp_path: Path,
) -> None:
    """Every package's ``SDConfig.Unmarshal`` shares one symbol key, and the
    first of them was bound whatever the receiver's package. An unqualified
    type is the caller's package's; a qualified one (``h.Ghost``) is its
    import's, and a same-named type elsewhere (``g.Ghost``) is not it."""
    r = _analyze(tmp_path, _TYPED_PACKAGES)
    use = sorted((_rel(e.dst), e.evidence_type) for e in _out(r, "Use"))
    assert use == [
        ("consul/c.go:SDConfig.Unmarshal", "ast_call"),
        ("consul/c.go:SDConfig.Unmarshal", "function_reference_arg"),
        ("consul/use.go:reg", "ast_call"),
    ], use
    wrong = sorted(_rel(e.dst) for e in _out(r, "Wrong"))
    assert wrong == ["go:external:0-0:Serve:unresolved", "k/k.go:reg"], wrong
    # An unqualified type from the return-type registry is the CALLEE's
    # package's, so the first candidate still answers when the caller's
    # package declares none.
    registry = sorted(
        (_rel(e.dst), e.evidence_type) for e in _out(r, "Registry")
        if "Rows" in _rel(e.dst)
    )
    assert registry == [
        ("q/q.go:Result.Rows", "ast_call"),
        ("q/q.go:Result.Rows", "function_reference_arg"),
    ], registry
    # A qualifier that names no import keeps the historical first candidate.
    assert _refs(r, "Unimported") == ["g/g.go:Ghost.Serve"]
    # A method promoted by embedding (``struct{ *tu.AT }``) is found through
    # the embedded type when it is the only one of that name; ``Dup.Go`` is
    # declared on two embedded-able types and names nothing.
    promoted = sorted(
        (_rel(e.dst), e.evidence_type) for e in _out(r, "Promoted")
        if "wa/wa.go" not in _rel(e.dst)
    )
    assert promoted == [
        ("cc/cc.go:reg", "ast_call"),
        ("cc/cc.go:reg", "ast_call"),
        ("go:wa:0-0:Go:unresolved", "ast_call"),
        ("go:wa:0-0:Missing:unresolved", "ast_call"),
        ("tu/tu.go:AT.Collector", "ast_call"),
        ("tu/tu.go:AT.Collector", "function_reference_arg"),
    ], promoted


# --------------------------------------------------------------------------
# WI-labik: a package-level function alias continues to what it holds.
# --------------------------------------------------------------------------

_LABIK = {
    "testutils/t.go": "package testutils\n\nfunc NewWebhook() int { return 1 }\n",
    "w/w.go": (
        "package w\n\n"
        'import (\n\t"strings"\n\n\t"example.com/fx/testutils"\n)\n\n'
        "func helper() {}\n\n"
        "func mk() func() { return helper }\n\n"
        "func mk2() (int, int) { return 1, 2 }\n\n"
        "var (\n"
        "\tNewWebhook = testutils.NewWebhook\n"
        "\tlocalAlias = helper\n"
        "\tupper      = strings.ToUpper\n"
        "\tbuilt      = mk()\n"
        "\tplain      = 3\n"
        "\ttypedOnly  func()\n"
        "\tp1, p2     = mk2()\n"
        "\tq1, q2     = helper, helper\n"
        ")\n\n"
        "func Use() { NewWebhook(); localAlias(); upper(\"x\"); built() }\n"
    ),
}


def test_a_package_level_alias_continues_to_its_target(tmp_path: Path) -> None:
    r = _analyze(tmp_path, _LABIK)
    # Reach: the calls bind the alias variables (INV-nopoh).
    called = sorted(_rel(e.dst) for e in _out(r, "Use"))
    assert called == [
        "w/w.go:NewWebhook", "w/w.go:built", "w/w.go:localAlias", "w/w.go:upper",
    ], called
    alias = _out(r, "NewWebhook")
    assert [(_rel(e.dst), e.evidence_type) for e in alias] == [
        ("testutils/t.go:NewWebhook", "function_reference"),
    ]
    local = _out(r, "localAlias")
    assert [(_rel(e.dst), e.evidence_type) for e in local] == [
        ("w/w.go:helper", "function_reference"),
    ]


def test_an_external_alias_reads_its_target_from_the_variable(
    tmp_path: Path,
) -> None:
    r = _analyze(tmp_path, _LABIK)
    reads = [e.dst for e in _out(r, "upper", edge_type="module_attr_ref")]
    assert reads == ["go:strings:0-0:strings.ToUpper:attribute"]


def test_a_call_initializer_and_a_literal_are_not_aliases(tmp_path: Path) -> None:
    r = _analyze(tmp_path, _LABIK)
    built = [(_rel(e.dst), e.evidence_type) for e in _out(r, "built")]
    assert built == [("w/w.go:mk", "ast_call")], built
    assert _out(r, "plain") == []
    # ``var typedOnly func()`` has no initializer (and, like every package
    # var without one, no symbol): nothing is emitted for it.
    assert not any(":typedOnly:" in e.src for e in r.edges)
    # Two names from one call: the call is INV-nopoh's, no alias edge.
    assert [e.evidence_type for e in _out(r, "p1")] == ["ast_call"]
    # Two aliases in one spec: each name pairs with its own value (only the
    # first name has a symbol to anchor on, as INV-nopoh's anchor does).
    assert [(_rel(e.dst), e.evidence_type) for e in _out(r, "q1")] == [
        ("w/w.go:helper", "function_reference"),
    ]
