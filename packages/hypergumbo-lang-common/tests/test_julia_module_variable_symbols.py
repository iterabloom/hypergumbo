# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Julia module-level variable emission (WI-jusus emission-parity tail).

The Julia analyzer already emitted struct-member fields (``kind="field"``) and
module ``const`` (``kind="const"``), but NOT non-const module-level variables —
so module state bound with a plain assignment (``x = 5``, ``g = () -> …``) had no
anchor, invisible to search, centrality, and io-boundaries.

This slice emits ``kind="variable"`` for a module-level ``assignment``. Scope
discrimination is a WALK (``_julia_variable_scope``) that ascends through
scope-transparent wrappers — a module-level ``begin…end``, ``if…elseif…else``, a
``global x = …`` statement, and a chained ``x = y = 5`` link — to the enclosing
``module_definition``/``source_file``, and STOPS (excludes) at the first
scope-introducing ancestor (``function_definition`` incl. short-form ``f(x) = …``,
``let`` / ``for`` / ``while`` / ``try`` / ``do`` / ``local``). A ``const`` assignment's
parent is ``const_statement`` (already handled), so it is not double-emitted. The
target is read from the LHS: ``identifier``; the PRE-``::`` target of a
``typed_expression`` (``y::Int`` -> ``y``, ``(a,b)::Point`` -> ``a``,``b``, never the
type); every element of an ``open_tuple``/``tuple``/``parenthesized`` (incl. a
``splat_expression`` ``rest...`` -> ``rest``); a ``call_expression`` LHS is a
short-form FUNCTION; a ``field_expression``/``index_expression`` is a MUTATION.

Resolution gate (``register_symbol`` skips both ``field`` and ``variable``): a
struct ``field`` and a module ``variable`` are both DATA anchors kept out of call
resolution. A variable's name is bare/unqualified, so registering it would let it
EXACT-match a bare ``foo()`` call and beat a same-named function's
module-qualified suffix-match — a wrong cross-module ``calls`` edge. Excluding
variables is no regression: they were unemitted before this slice, so no existing
edge is lost (unlike the F# value-binding case). Both kinds still reach
``analysis.symbols`` (search / centrality / io-boundaries).
"""

from pathlib import Path

from hypergumbo_lang_common.julia import analyze_julia


def _write(tmp_path: Path, body: str, name: str = "M.jl") -> None:
    (tmp_path / name).write_text(body)


def _names(result, kind: str) -> set[str]:
    return {s.name for s in result.symbols if s.kind == kind}


class TestJuliaModuleVariables:
    def test_module_level_variable_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module M
x = 5
counter = 0
end
""")
        variables = _names(analyze_julia(tmp_path), "variable")
        assert "x" in variables
        assert "counter" in variables

    def test_top_level_variable_no_module(self, tmp_path: Path) -> None:
        # A top-level assignment in a file with no module wrapper (parent =
        # source_file) is still module-level state.
        _write(tmp_path, """
top_x = 1
config = "prod"
""")
        variables = _names(analyze_julia(tmp_path), "variable")
        assert "top_x" in variables
        assert "config" in variables

    def test_typed_module_variable(self, tmp_path: Path) -> None:
        # `y::Int = 10` binds `y`; the type `Int` must NOT become a variable.
        _write(tmp_path, """
module M
y::Int = 10
end
""")
        variables = _names(analyze_julia(tmp_path), "variable")
        assert "y" in variables
        assert "Int" not in variables

    def test_tuple_unpacking_variables(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module M
a, b = 1, 2
end
""")
        variables = _names(analyze_julia(tmp_path), "variable")
        assert "a" in variables
        assert "b" in variables

    def test_lambda_bound_variable_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module M
g = () -> 42
end
""")
        assert "g" in _names(analyze_julia(tmp_path), "variable")

    def test_splat_destructuring_binds_all(self, tmp_path: Path) -> None:
        # `a, rest... = xs` binds a AND rest (the splat-bound name).
        _write(tmp_path, """
module M
a, rest... = xs
end
""")
        variables = _names(analyze_julia(tmp_path), "variable")
        assert "a" in variables
        assert "rest" in variables


class TestJuliaModuleVariableExclusions:
    def test_function_body_local_not_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module M
function compute(n)
    local_var = n + 1
    return local_var
end
end
""")
        assert "local_var" not in _names(analyze_julia(tmp_path), "variable")

    def test_loop_variable_not_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module M
function run()
    for i in 1:3
        loopvar = i
    end
end
end
""")
        assert "loopvar" not in _names(analyze_julia(tmp_path), "variable")

    def test_const_not_double_emitted_as_variable(self, tmp_path: Path) -> None:
        # `const PI = 3.14` stays kind="const"; it must NOT also emit a variable.
        _write(tmp_path, """
module M
const PI = 3.14
end
""")
        result = analyze_julia(tmp_path)
        assert "PI" in _names(result, "const")
        assert "PI" not in _names(result, "variable")

    def test_field_assignment_not_emitted(self, tmp_path: Path) -> None:
        # `obj.field = 2` is a mutation, not a declaration.
        _write(tmp_path, """
module M
function mutate(obj)
    obj.field = 2
end
end
""")
        both = _names(analyze_julia(tmp_path), "variable") | _names(analyze_julia(tmp_path), "field")
        assert "field" not in both

    def test_index_assignment_not_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module M
arr = [0, 0]
function mutate()
    arr[1] = 3
end
end
""")
        # `arr` (the module binding) emits; `arr[1] = 3` does not add a phantom.
        result = analyze_julia(tmp_path)
        variables = _names(result, "variable")
        assert "arr" in variables
        # No spurious extra variable from the index assignment.
        assert sum(1 for s in result.symbols if s.kind == "variable" and s.name == "arr") == 1

    def test_module_level_mutation_not_a_variable(self, tmp_path: Path) -> None:
        # A module-level index/field mutation (`arr[1] = …`, `obj.x = …`) reaches
        # the module-scope gate but its LHS is not a declaration target, so no
        # phantom variable is emitted (only the real `arr` binding).
        _write(tmp_path, """
module M
arr = [0, 0]
arr[1] = 99
ref.field = 5
end
""")
        result = analyze_julia(tmp_path)
        variables = _names(result, "variable")
        assert "arr" in variables
        assert "field" not in variables
        assert sum(1 for s in result.symbols if s.kind == "variable" and s.name == "arr") == 1

    def test_short_form_function_not_variable(self, tmp_path: Path) -> None:
        # `f(x) = x + 1` is a short-form function (LHS is a call_expression),
        # not a variable.
        _write(tmp_path, """
module M
f(x) = x + 1
end
""")
        result = analyze_julia(tmp_path)
        assert "f" in _names(result, "function")
        assert "f" not in _names(result, "variable")


class TestJuliaScopeTransparentWrappers:
    """A module variable behind a scope-TRANSPARENT wrapper (a module-level
    begin/if block, a `global` statement, or a chained assignment) is still
    module-level state and must emit — the scope walk sees through these."""

    def test_module_level_global_statement(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module M
global gx = 5
end
""")
        assert "gx" in _names(analyze_julia(tmp_path), "variable")

    def test_module_level_begin_block(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module M
begin
    bx = 1
end
end
""")
        assert "bx" in _names(analyze_julia(tmp_path), "variable")

    def test_module_level_if_else_block(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module M
if true
    ix = 1
else
    ex = 2
end
end
""")
        variables = _names(analyze_julia(tmp_path), "variable")
        assert "ix" in variables
        assert "ex" in variables

    def test_chained_assignment_binds_all(self, tmp_path: Path) -> None:
        # `x = y = 5` binds BOTH x and y at module scope (the inner assignment's
        # parent is the outer assignment, a transparent chain link).
        _write(tmp_path, """
module M
x = y = 5
end
""")
        variables = _names(analyze_julia(tmp_path), "variable")
        assert "x" in variables
        assert "y" in variables


class TestJuliaScopeIntroducingExclusions:
    """Scope-introducing constructs must NOT leak their locals as module vars."""

    def test_let_block_local_not_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module M
let
    lv = 1
end
end
""")
        assert "lv" not in _names(analyze_julia(tmp_path), "variable")

    def test_while_loop_local_not_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module M
function run()
    while true
        wv = 1
    end
end
end
""")
        assert "wv" not in _names(analyze_julia(tmp_path), "variable")

    def test_try_catch_local_not_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module M
try
    tv = 1
catch
    cv = 2
end
end
""")
        both = _names(analyze_julia(tmp_path), "variable")
        assert "tv" not in both
        assert "cv" not in both

    def test_do_block_local_not_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module M
function run()
    map(1:3) do i
        dv = i
    end
end
end
""")
        assert "dv" not in _names(analyze_julia(tmp_path), "variable")

    def test_short_form_function_body_local_not_leaked(self, tmp_path: Path) -> None:
        # A short-form function with a `begin…end` body is ALSO an `assignment`,
        # but it introduces the function's scope — its body-locals must NOT leak
        # as module variables (the walk stops at the short-form assignment).
        _write(tmp_path, """
module M
short(x) = begin
    sv = x
    sv
end
end
""")
        result = analyze_julia(tmp_path)
        assert "short" in _names(result, "function")
        assert "sv" not in _names(result, "variable")

    def test_short_form_body_local_does_not_clobber_function(self, tmp_path: Path) -> None:
        # A same-named local inside a short-form function body must not clobber a
        # real function of that name (the leak would mint a phantom variable that
        # captures inbound calls edges).
        _write(tmp_path, """
module M
function helper()
    42
end
compute(x) = begin
    helper = x
    helper
end
function caller()
    helper()
end
end
""")
        result = analyze_julia(tmp_path)
        assert "helper" not in _names(result, "variable")
        helper_fn = next((s for s in result.symbols
                          if s.kind == "function" and s.name == "helper"), None)
        assert helper_fn is not None
        call_dsts = {e.dst for e in result.edges if e.edge_type == "calls"}
        assert helper_fn.id in call_dsts

    def test_in_function_global_is_documented_miss(self, tmp_path: Path) -> None:
        # DOCUMENTED fails-safe miss (non-blocking): a `global x = …` INSIDE a
        # function binds module state but is excluded (the walk stops at the
        # enclosing function). It is a miss, never a wrong symbol. The meaningful
        # module-level `global` form still emits (see TestJuliaScopeTransparent).
        _write(tmp_path, """
module M
function setup()
    global gcfg = 5
end
end
""")
        assert "gcfg" not in _names(analyze_julia(tmp_path), "variable")

    def test_macro_wrapped_assignment_is_documented_exclusion(self, tmp_path: Path) -> None:
        # DOCUMENTED fails-safe exclusion: `@show x = 5` nests the assignment
        # under a macro argument list (neither a module scope nor transparent), so
        # nothing is emitted — an arbitrary macro can rewrite/suppress the binding.
        _write(tmp_path, """
module M
@show mx = 5
end
""")
        assert "mx" not in _names(analyze_julia(tmp_path), "variable")


class TestJuliaTypedAndCompoundLHS:
    """A `typed_expression` LHS binds the PRE-`::` target, never the type; a
    call_expression pre-target is a short-form typed FUNCTION."""

    def test_tuple_typed_lhs_binds_values_not_type(self, tmp_path: Path) -> None:
        # `(a, b)::Point = f()` binds a and b; `Point` (the type) must NOT phantom
        # as a variable (it would clobber the struct with a wrong calls edge).
        _write(tmp_path, """
module M
struct Point
    x::Int
    y::Int
end
function build()
    (a, b)::Point = (1, 2)
end
end
""")
        result = analyze_julia(tmp_path)
        variables = _names(result, "variable")
        assert "Point" not in variables
        # No calls edge resolves to a phantom `Point` variable.
        point_var_ids = {s.id for s in result.symbols
                         if s.kind == "variable" and s.name == "Point"}
        assert point_var_ids == set()

    def test_parenthesized_typed_lhs_binds_value_not_type(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module M
(x)::Int = 5
end
""")
        variables = _names(analyze_julia(tmp_path), "variable")
        assert "x" in variables
        assert "Int" not in variables

    def test_typed_short_form_function_is_function(self, tmp_path: Path) -> None:
        # `f(x)::Int = x` is a short-form function with a return type — it must
        # emit a FUNCTION `f`, never a phantom variable named after the type.
        _write(tmp_path, """
module M
f(x)::Int = x
end
""")
        result = analyze_julia(tmp_path)
        assert "f" in _names(result, "function")
        assert "Int" not in _names(result, "variable")
        assert "f" not in _names(result, "variable")


class TestJuliaModuleVariableDedup:
    def test_rebound_variable_emitted_once(self, tmp_path: Path) -> None:
        # `x = 5; x = 10` is one module binding, not two symbols.
        _write(tmp_path, """
module M
x = 5
x = 10
end
""")
        result = analyze_julia(tmp_path)
        assert sum(1 for s in result.symbols
                   if s.kind == "variable" and s.name == "x") == 1


class TestJuliaModuleVariableResolutionGate:
    """A module variable is a DATA anchor kept OUT of call resolution (skipped by
    ``register_symbol`` and not written to ``symbol_by_name``). Resolving it would
    let a bare (unqualified) value name EXACT-match a bare ``foo()`` call and beat
    a same-named function's module-qualified suffix-match — a wrong cross-module
    ``calls`` edge. Excluding variables is no regression (they were unemitted
    before this slice, so no existing edge is lost)."""

    def test_non_callable_variable_not_a_call_target(self, tmp_path: Path) -> None:
        # A `target = 99` in one file must NOT capture a `target()` call from
        # another file (it is not registered for resolution).
        (tmp_path / "a.jl").write_text("module A\ntarget = 99\nend\n")
        (tmp_path / "b.jl").write_text(
            "module B\nfunction go()\n    target()\nend\nend\n")
        result = analyze_julia(tmp_path)
        target_var = next((s for s in result.symbols
                           if s.kind == "variable" and s.name == "target"), None)
        assert target_var is not None  # still emitted (search/centrality)
        call_dsts = {e.dst for e in result.edges if e.edge_type == "calls"}
        assert target_var.id not in call_dsts

    def test_variable_does_not_clobber_cross_module_function(self, tmp_path: Path) -> None:
        # A `handler = () -> …` in module B must NOT clobber a same-named
        # `function handler()` in module A: an unqualified `handler()` call in
        # module C resolves to the FUNCTION (variables are out of resolution).
        (tmp_path / "M.jl").write_text("""
module A
function handler()
    1
end
end
module B
handler = () -> 2
end
module C
function caller()
    handler()
end
end
""")
        result = analyze_julia(tmp_path)
        handler_fn = next(s for s in result.symbols
                          if s.kind == "function" and s.name == "handler")
        handler_var = next(s for s in result.symbols
                           if s.kind == "variable" and s.name == "handler")
        call_dsts = {e.dst for e in result.edges if e.edge_type == "calls"}
        assert handler_fn.id in call_dsts
        assert handler_var.id not in call_dsts

    def test_rebind_wobble_is_fails_safe(self, tmp_path: Path) -> None:
        # DOCUMENTED edge case: rebinding a name across callability (`g = 5;
        # g = () -> 1`) freezes to the FIRST binding via the (scope, name) dedup
        # — a fails-safe under-approximation (miss, never a wrong-owner edge). The
        # firm contract holds: one `g` symbol, no data anchor is a wrong call dst.
        _write(tmp_path, """
module M
g = 5
g = () -> 1
end
""")
        result = analyze_julia(tmp_path)
        assert sum(1 for s in result.symbols
                   if s.kind == "variable" and s.name == "g") == 1

    def test_non_callable_stray_call_no_phantom_edge(self, tmp_path: Path) -> None:
        # `x = 5; x()` (a runtime MethodError) must not mint a caller->variable:x
        # edge.
        (tmp_path / "M.jl").write_text(
            "module M\nx = 5\nfunction f()\n    x()\nend\nend\n")
        result = analyze_julia(tmp_path)
        x_var = next((s for s in result.symbols
                      if s.kind == "variable" and s.name == "x"), None)
        assert x_var is not None
        call_dsts = {e.dst for e in result.edges if e.edge_type == "calls"}
        assert x_var.id not in call_dsts


class TestJuliaModuleVariableCallGraph:
    def test_variable_never_a_call_target(self, tmp_path: Path) -> None:
        # A module variable — even one bound to a lambda — is a data anchor kept
        # OUT of resolution: a bare `adder()` call must not resolve to it (a
        # variable's bare name would otherwise beat a function's qualified name;
        # excluding it is no regression since variables were unemitted before).
        _write(tmp_path, """
module M
adder = () -> 42
function caller()
    adder()
end
end
""")
        result = analyze_julia(tmp_path)
        adder = next(s for s in result.symbols
                     if s.kind == "variable" and s.name == "adder")
        call_dsts = {e.dst for e in result.edges if e.edge_type == "calls"}
        assert adder.id not in call_dsts

    def test_no_calls_edge_ever_terminates_on_a_data_anchor(self, tmp_path: Path) -> None:
        # Standing invariant across every LHS form: no `calls` edge may resolve to
        # a variable or field anchor (they are never call targets).
        _write(tmp_path, """
module M
scalar = 1
typed::Int = 2
tup_a, tup_b = 3, 4
splat_h, splat_rest... = xs
lam = () -> 5
struct Point
    px::Int
end
function driver()
    scalar()
    typed()
    tup_a()
    splat_h()
    lam()
    px()
end
end
""")
        result = analyze_julia(tmp_path)
        anchor_ids = {s.id for s in result.symbols if s.kind in ("field", "variable")}
        bad = [e for e in result.edges
               if e.edge_type == "calls" and e.dst in anchor_ids]
        assert bad == [], f"a call resolved to a data anchor: {bad}"

    def test_const_binding_stays_resolvable_variable_does_not(self, tmp_path: Path) -> None:
        # Deliberate, intentional asymmetry: `const` is a value binding that stays
        # in resolution (pre-existing), while a `variable` (this slice) is kept out.
        _write(tmp_path, """
module M
const cg = () -> 1
vg = () -> 2
function driver()
    cg()
    vg()
end
end
""")
        result = analyze_julia(tmp_path)
        cg = next(s for s in result.symbols if s.kind == "const" and s.name == "cg")
        vg = next(s for s in result.symbols if s.kind == "variable" and s.name == "vg")
        call_dsts = {e.dst for e in result.edges if e.edge_type == "calls"}
        assert cg.id in call_dsts       # const remains a resolvable value binding
        assert vg.id not in call_dsts   # variable is a data anchor, out of resolution

    def test_struct_field_not_a_call_target(self, tmp_path: Path) -> None:
        # Regression guard for the existing field skip: a struct field must never
        # be a calls-edge dst even alongside the new variable emission.
        _write(tmp_path, """
module M
struct Box
    label::String
end
function helper()
    1
end
function caller()
    helper()
end
end
""")
        result = analyze_julia(tmp_path)
        field_ids = {s.id for s in result.symbols if s.kind == "field"}
        bad = [e for e in result.edges
               if e.edge_type == "calls" and e.dst in field_ids]
        assert bad == []
