# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Fortran field/variable symbol emission (WI-jusus emission-parity tail).

The Fortran analyzer emitted module/program/function/subroutine/type symbols
but ZERO ``kind="field"`` (derived-type components) and ZERO ``kind="variable"``
(module/program/submodule-level declarations) — so derived-type structure,
module state, and module constants had no anchor in the symbol graph.

This slice classifies each ``variable_declaration`` node by its EFFECTIVE
enclosing scope (the nearest non-preprocessor ancestor — the bundled
``tree_sitter_fortran`` grammar does not flatten ``#ifdef``/``#if`` conditionals,
so a guarded declaration's immediate parent is a ``preproc_*`` node that must be
walked past):

- scope ``derived_type_definition``  -> ``kind="field"``  (owner = the type)
- scope ``module`` / ``program`` / ``submodule`` / ``block_data`` -> ``kind="variable"``
- scope ``subroutine`` / ``function`` / ``block_construct`` / interface body
  -> a local or parameter-type declaration -> SKIPPED

Both data-anchor kinds are routed through the ``register_symbol`` chokepoint so
they never enter the cross-file call-resolution registry (Fortran is imperative
-> skip BOTH ``field`` and ``variable``): a ``field`` (``box.label``) would let
the ``NameResolver`` suffix index mint a wrong ``calls`` edge for a bare
``call label()``, and a module ``variable`` (``compute``) would EXACT-match a
bare ``call compute()`` and clobber a same-named subroutine. They still reach
output/search/centrality because ``base.py`` assembles ``all_symbols`` from
``analysis.symbols`` independently of the resolution registry.

Grammar facts (bundled ``tree_sitter_fortran``): a multi-name declaration
(``real :: y, z``) yields several ``identifier`` children; array / coarray /
pointer-init / initialized declarators nest the name in a
``sized_declarator`` / ``coarray_declarator`` / ``pointer_init_declarator`` /
``init_declarator`` (harvested via ``_declarator_name``).

Error-recovery contract (asymmetric priority): NEVER drop a valid symbol on
compilable code, and NEVER emit a wrong edge; best-effort suppress phantoms on
malformed input via LINE-ADJACENCY signals only. A ``has_error`` gate is NOT
used — the bundled grammar sets ``has_error`` on valid F2003 it cannot parse
(``asynchronous``), so it would drop valid declarations. Constructs the grammar
cannot parse at all (DEC/VAX ``STRUCTURE``/``UNION``/``MAP``) may leak a phantom
EXTRA variable (never a wrong edge — data anchors are excluded from resolution);
that is an accepted grammar limitation, tested via the firm no-wrong-edge/no-crash
contract rather than exact non-emission.
"""

from pathlib import Path

from hypergumbo_lang_common.fortran import analyze_fortran_files


def _write(tmp_path: Path, body: str, name: str = "m.f90") -> None:
    (tmp_path / name).write_text(body)


def _names(result, kind: str) -> set[str]:
    return {s.name for s in result.symbols if s.kind == kind}


def _no_data_anchor_is_call_target(result) -> bool:
    """The firm contract on ANY input: no field/variable is a ``calls`` dst."""
    anchor_ids = {s.id for s in result.symbols if s.kind in ("field", "variable")}
    return not any(e.edge_type == "calls" and e.dst in anchor_ids
                   for e in result.edges)


class TestFortranDerivedTypeFields:
    def test_derived_type_fields_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module geometry
  type :: point
    integer :: x
    real :: y, z
  end type point
end module geometry
""")
        fields = _names(analyze_fortran_files(tmp_path), "field")
        assert "point.x" in fields
        assert "point.y" in fields
        assert "point.z" in fields

    def test_field_owner_bare_type_form(self, tmp_path: Path) -> None:
        # The ``type shape_t`` form (no ``::``) still yields the right owner.
        _write(tmp_path, """
module m
  type shape_t
    real :: area
  end type shape_t
end module m
""")
        assert "shape_t.area" in _names(analyze_fortran_files(tmp_path), "field")

    def test_field_with_default_init(self, tmp_path: Path) -> None:
        # A derived-type component with a default initializer carries its name
        # inside an ``init_declarator``.
        _write(tmp_path, """
module m
  type :: counter_t
    integer :: count = 0
  end type counter_t
end module m
""")
        assert "counter_t.count" in _names(analyze_fortran_files(tmp_path), "field")

    def test_field_has_type_signature(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module m
  type :: node_t
    type(node_t) :: next
    integer :: value
  end type node_t
end module m
""")
        result = analyze_fortran_files(tmp_path)
        by_name = {s.name: s for s in result.symbols if s.kind == "field"}
        assert by_name["node_t.value"].signature == "integer"
        assert by_name["node_t.next"].signature == "type(node_t)"


class TestFortranModuleVariables:
    def test_module_variable_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module state
  implicit none
  integer :: module_counter
end module state
""")
        assert "module_counter" in _names(analyze_fortran_files(tmp_path), "variable")

    def test_module_parameter_emitted(self, tmp_path: Path) -> None:
        # ``parameter`` (a named constant) is a module-level variable per the
        # WI-jusus contract ("module constants"); multi-init handled.
        _write(tmp_path, """
module consts
  real, parameter :: PI = 3.14159, E = 2.71828
end module consts
""")
        variables = _names(analyze_fortran_files(tmp_path), "variable")
        assert "pi" in variables
        assert "e" in variables

    def test_program_variable_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
program main
  integer :: prog_var
  prog_var = 5
end program main
""")
        assert "prog_var" in _names(analyze_fortran_files(tmp_path), "variable")

    def test_submodule_variable_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
submodule (parent_mod) child_mod
  integer :: submod_var
end submodule child_mod
""")
        assert "submod_var" in _names(analyze_fortran_files(tmp_path), "variable")

    def test_procedure_pointer_variable_emitted(self, tmp_path: Path) -> None:
        # A module-level procedure pointer is a named data anchor holding a
        # procedure reference -> kind="variable", with the ``procedure(...)``
        # spec as its signature. It is kept out of call resolution like every
        # other variable (clobber-safe: it has no static definition target).
        _write(tmp_path, """
module m
  procedure(iface), pointer :: fptr
end module m
""")
        result = analyze_fortran_files(tmp_path)
        fptr = next((s for s in result.symbols
                     if s.kind == "variable" and s.name == "fptr"), None)
        assert fptr is not None
        # The bundled grammar's ``procedure`` node bundles the ``pointer``
        # attribute into the type-specifier text (unlike intrinsic/derived
        # types, whose attributes are separate siblings).
        assert fptr.signature == "procedure(iface), pointer"


class TestFortranArrayAndInitDeclarators:
    """Inline-dimensioned arrays / pointer-init / initialized arrays — the
    dominant Fortran data idiom (HPC arrays, allocatable state). These parse as
    ``sized_declarator`` / ``pointer_init_declarator`` / ``init_declarator``
    wrapping a ``sized_declarator``, none of which are a bare ``identifier``
    child (regression: the first cut dropped all of them silently)."""

    def test_array_module_variable_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module grids
  integer :: nx, ny
  real :: grid(nx, ny)
  real :: buffer(1024)
end module grids
""")
        variables = _names(analyze_fortran_files(tmp_path), "variable")
        assert {"nx", "ny", "grid", "buffer"} <= variables

    def test_array_derived_type_component_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module m
  type :: mesh_t
    real :: coords(3)
    integer :: counts(10)
    real :: scalarfield
  end type mesh_t
end module m
""")
        fields = _names(analyze_fortran_files(tmp_path), "field")
        assert {"mesh_t.coords", "mesh_t.counts", "mesh_t.scalarfield"} <= fields

    def test_allocatable_deferred_shape_array_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module m
  real, allocatable :: workspace(:)
end module m
""")
        assert "workspace" in _names(analyze_fortran_files(tmp_path), "variable")

    def test_pointer_init_variable_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module m
  integer, pointer :: p => null()
end module m
""")
        assert "p" in _names(analyze_fortran_files(tmp_path), "variable")

    def test_initialized_array_variable_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module m
  integer :: arr(3) = [1, 2, 3]
end module m
""")
        assert "arr" in _names(analyze_fortran_files(tmp_path), "variable")

    def test_mixed_scalar_and_array_declaration(self, tmp_path: Path) -> None:
        # ``real :: a, b(3), c`` — the array ``b`` in the middle must not be
        # dropped between its scalar siblings.
        _write(tmp_path, """
module m
  real :: a, b(3), c
end module m
""")
        variables = _names(analyze_fortran_files(tmp_path), "variable")
        assert {"a", "b", "c"} <= variables

    def test_field_count_matches_declared_components(self, tmp_path: Path) -> None:
        # Count invariant across every component declarator shape: scalar,
        # inline-array, initialized, allocatable, pointer-init. Guards the
        # recall fix against silent per-shape drops.
        _write(tmp_path, """
module m
  type :: rec_t
    integer :: scalar_c
    real :: array_c(4)
    integer :: init_c = 7
    real, allocatable :: alloc_c(:)
    integer, pointer :: ptr_c => null()
  end type rec_t
end module m
""")
        fields = _names(analyze_fortran_files(tmp_path), "field")
        rec_fields = {f for f in fields if f.startswith("rec_t.")}
        assert rec_fields == {
            "rec_t.scalar_c", "rec_t.array_c", "rec_t.init_c",
            "rec_t.alloc_c", "rec_t.ptr_c",
        }


class TestFortranPointerAndCoarrayDeclarators:
    """Array-shaped pointer declarators must bind the NAME (before ``=>``), never
    the association target; coarrays are a fifth declarator shape."""

    def test_array_pointer_init_binds_name_not_target(self, tmp_path: Path) -> None:
        # `real, pointer :: q(:,:) => target_arr` — the name is q; target_arr is
        # the association RHS and must NOT be harvested (regression: the target
        # was emitted as a phantom variable while q was dropped).
        _write(tmp_path, """
module m
  real, dimension(3,3), target :: target_arr
  real, pointer :: q(:,:) => target_arr
end module m
""")
        variables = _names(analyze_fortran_files(tmp_path), "variable")
        assert "q" in variables
        assert "target_arr" in variables  # emitted once, from its OWN declaration
        # target_arr is not double-counted via q's association RHS:
        result = analyze_fortran_files(tmp_path)
        assert sum(1 for s in result.symbols
                   if s.kind == "variable" and s.name == "target_arr") == 1

    def test_array_pointer_field_binds_name_not_target(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module m
  type :: node_t
    real, pointer :: payload(:) => null()
    integer :: normal
  end type node_t
end module m
""")
        fields = _names(analyze_fortran_files(tmp_path), "field")
        assert "node_t.payload" in fields
        assert "node_t.normal" in fields

    def test_coarray_module_variable_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module m
  real :: field(10)[*]
  integer :: scalar_co[*]
end module m
""")
        variables = _names(analyze_fortran_files(tmp_path), "variable")
        assert {"field", "scalar_co"} <= variables

    def test_coarray_derived_type_component_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module m
  type :: dist_t
    real :: halo(4)[*]
  end type dist_t
end module m
""")
        assert "dist_t.halo" in _names(analyze_fortran_files(tmp_path), "field")

    def test_coarray_in_mixed_statement(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module m
  real :: a, b[*], c
end module m
""")
        assert {"a", "b", "c"} <= _names(analyze_fortran_files(tmp_path), "variable")


class TestFortranPreprocessorGuardedDeclarations:
    """Preprocessor conditionals (``.F90``) interpose ``preproc_*`` nodes between
    the program unit / type and the declaration; the scope-walk sees past them."""

    def test_ifdef_guarded_module_variable_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module m
#ifdef DEBUG
  integer :: dbg_var
#endif
  integer :: always_var
end module m
""", name="m.F90")
        variables = _names(analyze_fortran_files(tmp_path), "variable")
        assert {"dbg_var", "always_var"} <= variables

    def test_ifdef_guarded_derived_type_field_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module m
  type :: t
#ifdef EXTRA
    integer :: extra_field
#endif
    integer :: normal_field
  end type t
end module m
""", name="m.F90")
        fields = _names(analyze_fortran_files(tmp_path), "field")
        assert {"t.extra_field", "t.normal_field"} <= fields

    def test_if_elif_else_branches_all_emit(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module m
#if defined(A)
  integer :: v_a
#elif defined(B)
  integer :: v_b
#else
  integer :: v_else
#endif
end module m
""", name="m.F90")
        variables = _names(analyze_fortran_files(tmp_path), "variable")
        assert {"v_a", "v_b", "v_else"} <= variables

    def test_guarded_subroutine_local_still_excluded(self, tmp_path: Path) -> None:
        # The scope-walk past preproc must still land on the subroutine for a
        # guarded LOCAL -> excluded (no false variable).
        _write(tmp_path, """
module m
contains
  subroutine s()
#ifdef DEBUG
    integer :: guarded_local
#endif
    guarded_local = 0
  end subroutine s
end module m
""", name="m.F90")
        both = (_names(analyze_fortran_files(tmp_path), "variable")
                | _names(analyze_fortran_files(tmp_path), "field"))
        assert "guarded_local" not in both


class TestFortranBlockData:
    def test_block_data_variable_emitted(self, tmp_path: Path) -> None:
        # A BLOCK DATA unit's declarations are COMMON-block / global state.
        _write(tmp_path, """
block data init
  integer :: bd_var
end block data init
""")
        assert "bd_var" in _names(analyze_fortran_files(tmp_path), "variable")


class TestFortranMalformedSiblingDeferral:
    def test_missing_comma_before_attribute_deferred(self, tmp_path: Path) -> None:
        # `real, dimension(:,:) allocatable :: m` (missing comma) leaves an ERROR
        # sibling and would otherwise harvest the keyword `allocatable`.
        _write(tmp_path, """
module m
  real, dimension(:,:) allocatable :: mat
end module m
""")
        variables = _names(analyze_fortran_files(tmp_path), "variable")
        assert "allocatable" not in variables

    def test_trailing_comma_firm_contract(self, tmp_path: Path) -> None:
        # `integer :: a,` (no `&` continuation) is invalid input that swallows the
        # next statement's `real` keyword. The valid intended name `a` is
        # recovered; `real` may leak as a phantom (accepted residual on invalid
        # input). The firm contract holds: no crash, no wrong edge.
        _write(tmp_path, """
module m
  integer :: a,
  real :: b
end module m
""")
        result = analyze_fortran_files(tmp_path)
        assert "a" in _names(result, "variable")
        assert _no_data_anchor_is_call_target(result)

    def test_valid_decl_before_unparseable_statement_preserved(self, tmp_path: Path) -> None:
        # CRITICAL non-over-defer: a VALID declaration that merely PRECEDES an
        # unparseable statement (a Cray pointer, unsupported by the bundled
        # grammar) on a LATER line must still emit — the next-sibling-ERROR
        # deferral is gated on line adjacency, so it fires only for the swallow
        # pattern (ERROR on the vardecl's own line), not a following statement.
        _write(tmp_path, """
module hpc_state
  integer :: iterations
  real :: tolerance
  pointer (grid_ptr, grid)
end module hpc_state
""")
        variables = _names(analyze_fortran_files(tmp_path), "variable")
        assert "iterations" in variables
        assert "tolerance" in variables

    def test_attr_list_without_double_colon_deferred(self, tmp_path: Path) -> None:
        # `real, target dimension(3)` (attribute present, mandatory `::` missing)
        # misparses `dimension(3)` as a sized_declarator and would phantom a
        # variable named `dimension`.
        _write(tmp_path, """
module m
  real, target dimension(3)
end module m
""")
        assert "dimension" not in _names(analyze_fortran_files(tmp_path), "variable")

    def test_valid_attr_decl_still_emits(self, tmp_path: Path) -> None:
        # The attr-no-:: guard must not touch a VALID attributed declaration.
        _write(tmp_path, """
module m
  real, target :: legit_var
end module m
""")
        assert "legit_var" in _names(analyze_fortran_files(tmp_path), "variable")

    def test_dec_structure_firm_contract_co_located_var(self, tmp_path: Path) -> None:
        # A DEC/VAX `structure /point/ ... end structure` (non-standard legacy
        # extension the bundled grammar cannot parse) may leak a phantom
        # component variable (an accepted residual), but the FIRM contract holds:
        # a valid co-located declaration still emits, and no data anchor is a
        # `calls` target.
        _write(tmp_path, """
module legacy
  structure /point/
    integer x
    integer y
  end structure
  integer :: legit_var
end module legacy
""")
        result = analyze_fortran_files(tmp_path)
        assert "legit_var" in _names(result, "variable")
        assert _no_data_anchor_is_call_target(result)

    def test_valid_decl_after_cray_pointer_preserved(self, tmp_path: Path) -> None:
        # A VALID declaration on the line AFTER a Cray pointer (real Fortran the
        # grammar cannot parse) must be preserved — no guard fires on it.
        _write(tmp_path, """
module m
  pointer (p, x)
  integer :: after_cray
end module m
""")
        assert "after_cray" in _names(analyze_fortran_files(tmp_path), "variable")

    def test_nested_type_definition_firm_contract(self, tmp_path: Path) -> None:
        # Nested type DEFINITIONS are invalid Fortran (rejected by every
        # compiler). The line-adjacent inner component is suppressed; a
        # non-adjacent outer component may leak a phantom variable (an accepted
        # grammar-limitation residual — see the module docstring). The FIRM
        # contract holds regardless: no crash, no wrong edge.
        _write(tmp_path, """
module m
  type :: outer_t
    type :: inner_t
      integer :: inner_field
    end type inner_t
    integer :: outer_field
  end type outer_t
end module m
""")
        result = analyze_fortran_files(tmp_path)
        assert "inner_field" not in (_names(result, "variable") | _names(result, "field"))
        assert _no_data_anchor_is_call_target(result)

    def test_missing_end_type_firm_contract(self, tmp_path: Path) -> None:
        # A missing `end type outer` (invalid Fortran) merges scopes; a component
        # may be mis-owned or leak (an accepted residual on invalid input). The
        # FIRM contract holds: no crash, and no data anchor is a `calls` target.
        _write(tmp_path, """
module m
  type :: outer
    integer :: a
    type :: inner
      integer :: b
    end type inner
end module m
""")
        result = analyze_fortran_files(tmp_path)
        assert not result.skipped
        assert _no_data_anchor_is_call_target(result)


class TestFortranParameterizedDerivedType:
    def test_pdt_kind_len_parameters_not_fields(self, tmp_path: Path) -> None:
        # A parameterized derived type's KIND/LEN parameters are type slots, not
        # data components: the real component ``elements`` is a field; ``k``/``n``
        # are NOT (regression: they were emitted as phantom fields while the real
        # component was dropped — an inverted field set).
        _write(tmp_path, """
module m
  type :: matrix(k, n)
    integer, kind :: k
    integer, len :: n
    real :: elements(n, n)
  end type matrix
end module m
""")
        fields = _names(analyze_fortran_files(tmp_path), "field")
        assert "matrix.elements" in fields
        assert "matrix.k" not in fields
        assert "matrix.n" not in fields


class TestFortranTypeSpecNoPhantom:
    """A type specifier's inner identifiers (kind selector, len selector,
    derived-type name) live INSIDE the ``intrinsic_type``/``derived_type`` node,
    never as a declarator, so they must never phantom as separate symbols."""

    def test_kind_and_len_selectors_not_phantom(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module m
  integer :: dp
  integer :: strlen
  real(dp) :: value
  character(len=strlen) :: label
  type(mesh_t) :: origin_mesh
end module m
""")
        result = analyze_fortran_files(tmp_path)
        variables = _names(result, "variable")
        # The real module variables are present...
        assert {"dp", "strlen", "value", "label", "origin_mesh"} <= variables
        # ...but the selector/spec identifiers do not phantom a second time.
        # (dp/strlen appear once each — as their own declarations, not re-harvested
        #  from `real(dp)` / `character(len=strlen)`.)
        assert sum(1 for s in result.symbols
                   if s.kind == "variable" and s.name == "dp") == 1
        assert "mesh_t" not in variables  # the derived-type spec name is not a variable


class TestFortranLocalTypeFields:
    def test_local_derived_type_fields_emitted_with_owner(self, tmp_path: Path) -> None:
        # A derived type defined inside a procedure is a real type (the analyzer
        # already emits a `type` symbol for it); its components are emitted as
        # fields owned by that type — consistent with the existing local-type
        # `type` emission, and the owner is the type (never the procedure).
        _write(tmp_path, """
module m
contains
  subroutine build()
    type :: local_t
      integer :: member
    end type local_t
  end subroutine build
end module m
""")
        result = analyze_fortran_files(tmp_path)
        assert "local_t.member" in _names(result, "field")
        assert "member" not in _names(result, "variable")


class TestFortranLocalExclusion:
    def test_subroutine_locals_and_params_not_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module m
contains
  subroutine translate(dx)
    integer, intent(in) :: dx
    integer :: local_tmp
    local_tmp = dx
  end subroutine translate
end module m
""")
        result = analyze_fortran_files(tmp_path)
        both = _names(result, "field") | _names(result, "variable")
        assert "dx" not in both
        assert "local_tmp" not in both

    def test_function_locals_and_result_not_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
module m
contains
  function area_of(s) result(a)
    real, intent(in) :: s
    real :: a
    a = s * s
  end function area_of
end module m
""")
        result = analyze_fortran_files(tmp_path)
        both = _names(result, "field") | _names(result, "variable")
        assert "s" not in both
        assert "a" not in both

    def test_block_construct_local_not_emitted(self, tmp_path: Path) -> None:
        # An F2008 BLOCK-construct local sits under ``block_construct`` -> skipped.
        _write(tmp_path, """
module m
contains
  subroutine outer()
    block
      integer :: block_scoped
    end block
  end subroutine outer
end module m
""")
        result = analyze_fortran_files(tmp_path)
        both = _names(result, "field") | _names(result, "variable")
        assert "block_scoped" not in both

    def test_interface_body_param_not_emitted(self, tmp_path: Path) -> None:
        # A parameter declaration inside an interface's procedure body has parent
        # ``subroutine`` -> skipped (no field/variable leak).
        _write(tmp_path, """
module m
  interface
    subroutine ext_sub(iface_param)
      integer :: iface_param
    end subroutine ext_sub
  end interface
end module m
""")
        result = analyze_fortran_files(tmp_path)
        both = _names(result, "field") | _names(result, "variable")
        assert "iface_param" not in both


class TestFortranFieldCallGraphIntegrity:
    def test_field_not_a_call_target(self, tmp_path: Path) -> None:
        # A derived-type field must NEVER be a ``calls`` edge target: the
        # NameResolver suffix index would otherwise suffix-match a bare
        # ``call label()`` to the field ``box.label``.
        _write(tmp_path, """
module m
  type :: box
    integer :: label
  end type box
contains
  subroutine caller()
    call label()
  end subroutine caller
end module m
""")
        result = analyze_fortran_files(tmp_path)
        assert "box.label" in _names(result, "field")
        field_ids = {s.id for s in result.symbols if s.kind == "field"}
        bad = [e for e in result.edges
               if e.edge_type == "calls" and e.dst in field_ids]
        assert bad == [], f"a call resolved to a field: {bad}"

    def test_variable_does_not_clobber_call(self, tmp_path: Path) -> None:
        # A module ``variable`` must NEVER be a ``calls`` edge target: a bare
        # ``call compute()`` would EXACT-match a registered variable ``compute``
        # and clobber a same-named subroutine.
        _write(tmp_path, """
module m
  integer :: compute
contains
  subroutine driver()
    call compute()
  end subroutine driver
end module m
""")
        result = analyze_fortran_files(tmp_path)
        assert "compute" in _names(result, "variable")
        var_ids = {s.id for s in result.symbols if s.kind == "variable"}
        bad = [e for e in result.edges
               if e.edge_type == "calls" and e.dst in var_ids]
        assert bad == [], f"a call resolved to a variable: {bad}"

    def test_real_subroutine_call_still_resolves(self, tmp_path: Path) -> None:
        # Sanity: keeping fields/variables out of the registry does not harm
        # ordinary subroutine call resolution.
        _write(tmp_path, """
module m
contains
  subroutine helper()
  end subroutine helper
  subroutine driver()
    call helper()
  end subroutine driver
end module m
""")
        result = analyze_fortran_files(tmp_path)
        helper = next(s for s in result.symbols
                      if s.kind == "subroutine" and s.name == "helper")
        call_dsts = {e.dst for e in result.edges if e.edge_type == "calls"}
        assert helper.id in call_dsts


class TestFortranAsyncGrammarDivergence:
    """The bundled grammar cannot parse the standard F2003 ``asynchronous``
    attribute — it emits an ERROR child and sets ``root.has_error``. A
    ``has_error`` deferral therefore drops VALID declarations that merely sit
    near an ``asynchronous`` construct (a recall loss on compilable code); these
    lock the never-drop-valid-symbol contract. (The ``asynchronous`` declaration
    itself is fragmented by the grammar and unrecoverable — a grammar limit — but
    it must not leak a keyword-fragment phantom either.)"""

    def test_async_declaration_no_keyword_fragment_phantom(self, tmp_path: Path) -> None:
        # `integer, asynchronous :: buf` fragments into ERROR + identifier
        # `synchronous` (a keyword tail) + a same-line ERROR for `:: buf`; the
        # line-adjacency guard suppresses the fragment. Firm contract: no garbage
        # phantom, no wrong edge.
        _write(tmp_path, """
module m
  integer, asynchronous :: buf
end module m
""")
        result = analyze_fortran_files(tmp_path)
        assert "synchronous" not in _names(result, "variable")
        assert _no_data_anchor_is_call_target(result)

    def test_valid_var_after_async_interface_preserved(self, tmp_path: Path) -> None:
        # A valid module var below an interface containing an `asynchronous`
        # dummy: the interface subtree has_error, but `pending_count` is on a
        # different line and must NOT be dropped (the round-4 regression).
        _write(tmp_path, """
module m
  interface
    subroutine s(buf)
      integer, asynchronous :: buf
    end subroutine s
  end interface
  integer :: pending_count
end module m
""")
        assert "pending_count" in _names(analyze_fortran_files(tmp_path), "variable")

    def test_standalone_async_statement_preserves_following_decl(self, tmp_path: Path) -> None:
        # A standalone `asynchronous :: buf` statement (F2003) is an unparseable
        # ERROR node that swallows its trailing newline (ends at col 0 of the next
        # line). The prev-sibling guard must correct for that so the valid
        # declaration on the following line is NOT dropped.
        _write(tmp_path, """
module m
  integer :: buf
  asynchronous :: buf
  integer :: keep_after
end module m
""")
        assert "keep_after" in _names(analyze_fortran_files(tmp_path), "variable")

    def test_standalone_codimension_statement_preserves_preceding_decl(self, tmp_path: Path) -> None:
        # The F2008 CODIMENSION statement parses as a `variable_modification` node
        # that absorbs the FOLLOWING declaration into an ERROR (a grammar recall
        # limit — `after` is unrecoverable, unlike the async case). The valid
        # declaration BEFORE it must still emit, and the firm contract holds.
        _write(tmp_path, """
module m
  real :: x
  codimension :: x[*]
  integer :: after
end module m
""")
        result = analyze_fortran_files(tmp_path)
        assert "x" in _names(result, "variable")
        assert _no_data_anchor_is_call_target(result)

    def test_async_last_statement_unit_vars_preserved(self, tmp_path: Path) -> None:
        # An `asynchronous` statement as the unit's LAST line makes the whole
        # module parse as an ERROR node holding the module_statement + decls; the
        # scope recovery must still emit the valid module variables (was: all
        # dropped, a unit-wide recall loss).
        _write(tmp_path, """
module m
  integer :: alpha
  integer :: beta
  integer :: gamma
  asynchronous :: alpha
end module m
""")
        variables = _names(analyze_fortran_files(tmp_path), "variable")
        assert {"alpha", "beta", "gamma"} <= variables

    def test_error_wrapped_subroutine_local_not_leaked(self, tmp_path: Path) -> None:
        # The unit-scope recovery is UNIT-only: a local in an ERROR-wrapped
        # subroutine (no unit statement) must stay excluded, not leak as a
        # module variable.
        _write(tmp_path, """
module m
contains
  subroutine s()
    integer :: local_v
    asynchronous :: local_v
  end subroutine s
end module m
""")
        assert "local_v" not in _names(analyze_fortran_files(tmp_path), "variable")


class TestFortranFixedFormContinuation:
    """Fixed-form (.f/.for) column-6 continuation — the bundled grammar cannot
    join continuation lines, so the trailing comma forms an ERROR sibling. The
    CLEAN leading name must still be recovered (the clean-prefix harvest); the
    CONTINUED name stays buried in the ERROR (an accepted grammar recall limit).
    Fixed-form was entirely untested before this."""

    def test_continuation_recovers_clean_leading_name(self, tmp_path: Path) -> None:
        (tmp_path / "p.f").write_text(
            "      PROGRAM P\n"
            "      INTEGER COUNT,\n"
            "     *        TOTAL\n"
            "      COUNT = 0\n"
            "      END\n"
        )
        variables = _names(analyze_fortran_files(tmp_path), "variable")
        assert "count" in variables  # clean leading name recovered

    def test_single_line_form_still_emits_both(self, tmp_path: Path) -> None:
        # Control: the same two names on one physical line emit both (isolates the
        # continuation shape, not the names, as the recall limit).
        (tmp_path / "p.f").write_text(
            "      PROGRAM P\n"
            "      INTEGER COUNT, TOTAL\n"
            "      END\n"
        )
        variables = _names(analyze_fortran_files(tmp_path), "variable")
        assert {"count", "total"} <= variables


class TestFortranMalformedInputFirmContract:
    def test_garbage_declaration_no_wrong_edge(self, tmp_path: Path) -> None:
        # On garbage input the analyzer may emit a best-guess name (a spurious
        # extra), but the FIRM contract holds: no crash, and no data anchor is
        # ever a `calls` target.
        _write(tmp_path, """
module mbad
  integer :: @#$ broken
  real :: good_one
end module mbad
""")
        result = analyze_fortran_files(tmp_path)
        assert not result.skipped
        assert _no_data_anchor_is_call_target(result)

    def test_dec_structure_firm_contract(self, tmp_path: Path) -> None:
        # DEC/VAX STRUCTURE with 3+ components / nested UNION-MAP (non-standard
        # legacy the bundled grammar cannot parse): deeper components may leak a
        # phantom variable (accepted residual), but no data anchor is a `calls`
        # target and the analyzer does not crash.
        _write(tmp_path, """
module legacy
  structure /point/
    integer x
    integer y
    integer z
    union
      map
        real rx
      end map
    end union
  end structure
end module legacy
""")
        result = analyze_fortran_files(tmp_path)
        assert not result.skipped
        assert _no_data_anchor_is_call_target(result)
