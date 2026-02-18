"""Tests for Lua FFI linker (LuaJIT ffi.cdef/ffi.load/ffi.C linking to C).

The Lua FFI linker creates ffi_bridge edges between Lua code that uses LuaJIT's
FFI interface and the C function implementations those calls resolve to.

Three FFI mechanisms are detected by scanning Lua source files:

1. **ffi.cdef**: Declares C functions available via ``ffi.C.funcname()`` calls.
   The linker extracts function names from the cdef string literal and maps
   ``ffi.C.name()`` calls in Lua to matching C symbols.

2. **ffi.load**: Loads a shared library (``local lib = ffi.load("name")``),
   then ``lib.funcname()`` calls are resolved against C symbols.

3. **ffi.C**: Direct calls via ``ffi.C.funcname()`` — the default C namespace
   provided by LuaJIT. These are matched against C symbols even without a
   preceding cdef (cdef may be in a different file).
"""
from pathlib import Path

import pytest

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.linkers.registry import LinkerContext


def _make_lua_symbol(
    name: str,
    kind: str = "function",
    path: str = "init.lua",
    start_line: int = 1,
    end_line: int = 10,
) -> Symbol:
    """Create a test Lua symbol."""
    run = AnalysisRun.create(pass_id="test", version="test")
    return Symbol(
        id=f"lua:{path}:{start_line}-{end_line}:{name}:{kind}",
        name=name,
        kind=kind,
        language="lua",
        path=path,
        span=Span(start_line=start_line, end_line=end_line, start_col=0, end_col=0),
        origin="lua-v1",
        origin_run_id=run.execution_id,
    )


def _make_c_symbol(
    name: str,
    kind: str = "function",
    path: str = "native.c",
    start_line: int = 1,
    end_line: int = 10,
) -> Symbol:
    """Create a test C symbol."""
    run = AnalysisRun.create(pass_id="test", version="test")
    return Symbol(
        id=f"c:{path}:{start_line}-{end_line}:{name}:{kind}",
        name=name,
        kind=kind,
        language="c",
        path=path,
        span=Span(start_line=start_line, end_line=end_line, start_col=0, end_col=0),
        origin="c-v1",
        origin_run_id=run.execution_id,
    )


def _make_unresolved_edge(
    src_id: str, call_name: str, line: int = 1
) -> Edge:
    """Create an unresolved call edge from a Lua symbol."""
    run = AnalysisRun.create(pass_id="test", version="test")
    return Edge.create(
        src=src_id,
        dst=f"lua:init.lua:0-0:{call_name}:unresolved",
        edge_type="call",
        line=line,
        confidence=0.5,
        origin="lua-v1",
        origin_run_id=run.execution_id,
    )


class TestLuaFFILinkerFfiC:
    """Tests for ffi.C.funcname() pattern detection."""

    def test_links_ffi_c_call_to_c_function(self, tmp_path: Path) -> None:
        """ffi.C.funcname() should create ffi_bridge to matching C symbol."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_file = tmp_path / "init.lua"
        lua_file.write_text(
            'local ffi = require("ffi")\n'
            'ffi.cdef[[\n'
            'int compute(int x, int y);\n'
            ']]\n'
            'local result = ffi.C.compute(1, 2)\n'
        )

        lua_sym = _make_lua_symbol("main", path=str(lua_file))
        c_sym = _make_c_symbol("compute")

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[c_sym],
            edges=[],
        )

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "ffi_bridge"
        assert edge.dst == c_sym.id
        assert edge.src == lua_sym.id

    def test_ffi_c_with_multiple_functions(self, tmp_path: Path) -> None:
        """Multiple ffi.C calls should each create a bridge edge."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_file = tmp_path / "init.lua"
        lua_file.write_text(
            'local ffi = require("ffi")\n'
            'ffi.cdef[[\n'
            'int add(int a, int b);\n'
            'int subtract(int a, int b);\n'
            ']]\n'
            'local x = ffi.C.add(1, 2)\n'
            'local y = ffi.C.subtract(3, 1)\n'
        )

        lua_sym = _make_lua_symbol("main", path=str(lua_file))
        c_add = _make_c_symbol("add")
        c_sub = _make_c_symbol("subtract", start_line=5)

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[c_add, c_sub],
            edges=[],
        )

        assert len(result.edges) == 2
        dst_ids = {e.dst for e in result.edges}
        assert c_add.id in dst_ids
        assert c_sub.id in dst_ids

    def test_ffi_c_no_matching_c_symbol(self, tmp_path: Path) -> None:
        """ffi.C.funcname() with no matching C symbol should produce no edges."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_file = tmp_path / "init.lua"
        lua_file.write_text(
            'local ffi = require("ffi")\n'
            'local result = ffi.C.nonexistent(1, 2)\n'
        )

        lua_sym = _make_lua_symbol("main", path=str(lua_file))

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[],
            edges=[],
        )

        assert len(result.edges) == 0


class TestLuaFFILinkerFfiLoad:
    """Tests for ffi.load("libname") pattern detection."""

    def test_links_ffi_load_call_to_c_function(self, tmp_path: Path) -> None:
        """lib = ffi.load("name"); lib.func() should create ffi_bridge."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_file = tmp_path / "init.lua"
        lua_file.write_text(
            'local ffi = require("ffi")\n'
            'local mylib = ffi.load("mylib")\n'
            'local val = mylib.process(42)\n'
        )

        lua_sym = _make_lua_symbol("main", path=str(lua_file))
        c_sym = _make_c_symbol("process")

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[c_sym],
            edges=[],
        )

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "ffi_bridge"
        assert edge.dst == c_sym.id

    def test_ffi_load_with_global_assignment(self, tmp_path: Path) -> None:
        """lib = ffi.load("name") without local keyword."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_file = tmp_path / "init.lua"
        lua_file.write_text(
            'local ffi = require("ffi")\n'
            'mylib = ffi.load("mylib")\n'
            'mylib.transform(1)\n'
        )

        lua_sym = _make_lua_symbol("main", path=str(lua_file))
        c_sym = _make_c_symbol("transform")

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[c_sym],
            edges=[],
        )

        assert len(result.edges) == 1

    def test_ffi_load_single_quoted(self, tmp_path: Path) -> None:
        """ffi.load('libname') with single quotes."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_file = tmp_path / "init.lua"
        lua_file.write_text(
            "local ffi = require('ffi')\n"
            "local mylib = ffi.load('mylib')\n"
            "mylib.encode(data)\n"
        )

        lua_sym = _make_lua_symbol("main", path=str(lua_file))
        c_sym = _make_c_symbol("encode")

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[c_sym],
            edges=[],
        )

        assert len(result.edges) == 1


class TestLuaFFILinkerEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_inputs(self, tmp_path: Path) -> None:
        """Empty symbol lists should produce no edges."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[],
            c_symbols=[],
            edges=[],
        )

        assert len(result.edges) == 0
        assert result.run is not None

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Symbols pointing to nonexistent files should be skipped."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_sym = _make_lua_symbol("main", path=str(tmp_path / "missing.lua"))

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[_make_c_symbol("func")],
            edges=[],
        )

        assert len(result.edges) == 0

    def test_dedup_same_call(self, tmp_path: Path) -> None:
        """Duplicate ffi.C.func() calls should produce only one edge."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_file = tmp_path / "init.lua"
        lua_file.write_text(
            'local ffi = require("ffi")\n'
            'ffi.C.compute(1)\n'
            'ffi.C.compute(2)\n'
        )

        lua_sym = _make_lua_symbol("main", path=str(lua_file))
        c_sym = _make_c_symbol("compute")

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[c_sym],
            edges=[],
        )

        assert len(result.edges) == 1

    def test_confidence_is_085(self, tmp_path: Path) -> None:
        """FFI bridge edges should have confidence 0.85."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_file = tmp_path / "init.lua"
        lua_file.write_text(
            'local ffi = require("ffi")\n'
            'ffi.C.compute(1)\n'
        )

        lua_sym = _make_lua_symbol("main", path=str(lua_file))
        c_sym = _make_c_symbol("compute")

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[c_sym],
            edges=[],
        )

        assert result.edges[0].confidence == 0.85

    def test_evidence_type_ffi_c(self, tmp_path: Path) -> None:
        """ffi.C calls should have evidence_type 'luajit_ffi_c'."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_file = tmp_path / "init.lua"
        lua_file.write_text(
            'local ffi = require("ffi")\n'
            'ffi.C.compute(1)\n'
        )

        lua_sym = _make_lua_symbol("main", path=str(lua_file))
        c_sym = _make_c_symbol("compute")

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[c_sym],
            edges=[],
        )

        assert result.edges[0].evidence_type == "luajit_ffi_c"

    def test_evidence_type_ffi_load(self, tmp_path: Path) -> None:
        """ffi.load calls should have evidence_type 'luajit_ffi_load'."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_file = tmp_path / "init.lua"
        lua_file.write_text(
            'local ffi = require("ffi")\n'
            'local mylib = ffi.load("mylib")\n'
            'mylib.compute(1)\n'
        )

        lua_sym = _make_lua_symbol("main", path=str(lua_file))
        c_sym = _make_c_symbol("compute")

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[c_sym],
            edges=[],
        )

        assert result.edges[0].evidence_type == "luajit_ffi_load"

    def test_non_function_c_symbols_ignored(self, tmp_path: Path) -> None:
        """C symbols that aren't functions should not be matched."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_file = tmp_path / "init.lua"
        lua_file.write_text(
            'local ffi = require("ffi")\n'
            'ffi.C.MY_CONST()\n'
        )

        lua_sym = _make_lua_symbol("main", path=str(lua_file))
        c_sym = _make_c_symbol("MY_CONST", kind="variable")

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[c_sym],
            edges=[],
        )

        assert len(result.edges) == 0

    def test_relative_path_resolved_to_repo_root(self, tmp_path: Path) -> None:
        """Relative paths should be resolved against repo_root."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_file = tmp_path / "init.lua"
        lua_file.write_text(
            'local ffi = require("ffi")\n'
            'ffi.C.compute(1)\n'
        )

        # Use relative path for the symbol
        lua_sym = _make_lua_symbol("main", path="init.lua")
        c_sym = _make_c_symbol("compute")

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[c_sym],
            edges=[],
        )

        assert len(result.edges) == 1

    def test_unresolved_edge_matching(self, tmp_path: Path) -> None:
        """Unresolved Lua call edges should match against C symbols when cdef declares them."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_sym = _make_lua_symbol("main", path="init.lua")
        c_sym = _make_c_symbol("compute")

        edge = _make_unresolved_edge(lua_sym.id, "compute", line=5)

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[c_sym],
            edges=[edge],
        )

        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "ffi_bridge"
        assert result.edges[0].dst == c_sym.id

    def test_resolved_edges_ignored(self, tmp_path: Path) -> None:
        """Already-resolved edges should not produce ffi_bridge edges."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_sym = _make_lua_symbol("main", path="init.lua")
        c_sym = _make_c_symbol("compute")

        run = AnalysisRun.create(pass_id="test", version="test")
        resolved_edge = Edge.create(
            src=lua_sym.id,
            dst=c_sym.id,
            edge_type="call",
            line=5,
            confidence=0.9,
            origin="lua-v1",
            origin_run_id=run.execution_id,
        )

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[c_sym],
            edges=[resolved_edge],
        )

        assert len(result.edges) == 0

    def test_short_unresolved_dst_ignored(self, tmp_path: Path) -> None:
        """Unresolved edges with short dst (< 5 parts) should be skipped."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_sym = _make_lua_symbol("main", path="init.lua")
        c_sym = _make_c_symbol("compute")

        run = AnalysisRun.create(pass_id="test", version="test")
        short_edge = Edge.create(
            src=lua_sym.id,
            dst="lua:short:unresolved",
            edge_type="call",
            line=5,
            confidence=0.5,
            origin="lua-v1",
            origin_run_id=run.execution_id,
        )

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[c_sym],
            edges=[short_edge],
        )

        assert len(result.edges) == 0

    def test_ffi_c_underscore_skipped(self, tmp_path: Path) -> None:
        """ffi.C calls to underscore-prefixed names should be skipped (internal/private)."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_file = tmp_path / "init.lua"
        lua_file.write_text(
            'local ffi = require("ffi")\n'
            'ffi.C._internal(1)\n'
        )

        lua_sym = _make_lua_symbol("main", path=str(lua_file))
        c_sym = _make_c_symbol("_internal")

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[c_sym],
            edges=[],
        )

        assert len(result.edges) == 0

    def test_run_has_duration(self, tmp_path: Path) -> None:
        """The run should have a non-negative duration_ms."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[],
            c_symbols=[],
            edges=[],
        )

        assert result.run is not None
        assert result.run.duration_ms >= 0

    def test_multiple_lua_files(self, tmp_path: Path) -> None:
        """FFI calls from multiple Lua files should each produce edges."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_file_a = tmp_path / "a.lua"
        lua_file_a.write_text(
            'local ffi = require("ffi")\n'
            'ffi.C.alpha(1)\n'
        )

        lua_file_b = tmp_path / "b.lua"
        lua_file_b.write_text(
            'local ffi = require("ffi")\n'
            'ffi.C.beta(2)\n'
        )

        lua_sym_a = _make_lua_symbol("main_a", path=str(lua_file_a))
        lua_sym_b = _make_lua_symbol("main_b", path=str(lua_file_b))
        c_alpha = _make_c_symbol("alpha")
        c_beta = _make_c_symbol("beta", start_line=5)

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym_a, lua_sym_b],
            c_symbols=[c_alpha, c_beta],
            edges=[],
        )

        assert len(result.edges) == 2

    def test_ffi_load_underscore_skipped(self, tmp_path: Path) -> None:
        """lib.funcname() with underscore-prefixed name should be skipped."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_file = tmp_path / "init.lua"
        lua_file.write_text(
            'local ffi = require("ffi")\n'
            'local mylib = ffi.load("mylib")\n'
            'mylib._private(1)\n'
        )

        lua_sym = _make_lua_symbol("main", path=str(lua_file))
        c_sym = _make_c_symbol("_private")

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[c_sym],
            edges=[],
        )

        assert len(result.edges) == 0

    def test_enclosing_symbol_refinement(self, tmp_path: Path) -> None:
        """When multiple symbols exist for same file, prefer tightest enclosing."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_file = tmp_path / "init.lua"
        lua_file.write_text(
            'local ffi = require("ffi")\n'
            'function outer()\n'
            '  function inner()\n'
            '    ffi.C.compute(1)\n'
            '  end\n'
            'end\n'
        )

        # Outer symbol spans entire file
        outer_sym = _make_lua_symbol(
            "outer", path=str(lua_file), start_line=2, end_line=6
        )
        # Inner symbol spans lines 3-5 (tighter enclosing)
        inner_sym = _make_lua_symbol(
            "inner", path=str(lua_file), start_line=3, end_line=5
        )
        c_sym = _make_c_symbol("compute")

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[outer_sym, inner_sym],
            c_symbols=[c_sym],
            edges=[],
        )

        assert len(result.edges) == 1
        # The edge source should be the inner (tighter) symbol
        assert result.edges[0].src == inner_sym.id

    def test_unresolved_edge_no_matching_c_symbol(self, tmp_path: Path) -> None:
        """Unresolved edge with call name not in c_lookup should be skipped."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_sym = _make_lua_symbol("main", path="init.lua")

        edge = _make_unresolved_edge(lua_sym.id, "nonexistent_func", line=5)

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[],  # no C symbols at all
            edges=[edge],
        )

        assert len(result.edges) == 0

    def test_unresolved_edge_dedup(self, tmp_path: Path) -> None:
        """Duplicate unresolved edges for same src+name should produce only one bridge."""
        from hypergumbo_core.linkers.lua_ffi import link_lua_ffi

        lua_sym = _make_lua_symbol("main", path="init.lua")
        c_sym = _make_c_symbol("compute")

        edge1 = _make_unresolved_edge(lua_sym.id, "compute", line=5)
        edge2 = _make_unresolved_edge(lua_sym.id, "compute", line=10)

        result = link_lua_ffi(
            repo_root=tmp_path,
            lua_symbols=[lua_sym],
            c_symbols=[c_sym],
            edges=[edge1, edge2],
        )

        assert len(result.edges) == 1


class TestLuaFFILinkerRegistry:
    """Tests for linker registration and dispatch."""

    def test_linker_is_registered(self) -> None:
        """The lua_ffi linker should be in the registry."""
        import hypergumbo_core.linkers.lua_ffi
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("lua_ffi")
        assert linker is not None

    def test_requirements_check(self) -> None:
        """Requirements should count Lua files and C functions."""
        import hypergumbo_core.linkers.lua_ffi
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("lua_ffi")
        assert linker is not None
        assert len(linker.requirements) == 2

        # Check the requirement names
        names = {r.name for r in linker.requirements}
        assert "lua_files" in names
        assert "c_functions" in names

    def test_activation_language_pairs(self) -> None:
        """Activation should match lua+c and lua+cpp language pairs."""
        import hypergumbo_core.linkers.lua_ffi
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("lua_ffi")
        assert linker is not None
        assert linker.activation is not None
        pairs = linker.activation.language_pairs
        assert ("lua", "c") in pairs
        assert ("lua", "cpp") in pairs

    def test_dispatch_produces_edges(self, tmp_path: Path) -> None:
        """Registry dispatch should produce ffi_bridge edges."""
        import hypergumbo_core.linkers.lua_ffi
        from hypergumbo_core.linkers.registry import get_linker

        lua_file = tmp_path / "init.lua"
        lua_file.write_text(
            'local ffi = require("ffi")\n'
            'ffi.C.compute(1)\n'
        )

        lua_sym = _make_lua_symbol("main", path=str(lua_file))
        c_sym = _make_c_symbol("compute")

        linker = get_linker("lua_ffi")
        assert linker is not None

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[lua_sym, c_sym],
            edges=[],
        )

        result = linker.func(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "ffi_bridge"

    def test_requirements_count_lua_files(self) -> None:
        """Lua file requirement should count unique Lua file paths."""
        import hypergumbo_core.linkers.lua_ffi
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("lua_ffi")
        assert linker is not None

        # Find the lua_files requirement
        lua_req = next(r for r in linker.requirements if r.name == "lua_files")

        lua_sym1 = _make_lua_symbol("f1", path="a.lua")
        lua_sym2 = _make_lua_symbol("f2", path="b.lua")
        lua_sym3 = _make_lua_symbol("f3", path="a.lua")  # duplicate path

        ctx = LinkerContext(
            repo_root=Path("/tmp"),
            symbols=[lua_sym1, lua_sym2, lua_sym3],
            edges=[],
        )

        assert lua_req.check(ctx) == 2  # two unique paths

    def test_requirements_count_c_functions(self) -> None:
        """C function requirement should count only function/method symbols."""
        import hypergumbo_core.linkers.lua_ffi
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("lua_ffi")
        assert linker is not None

        c_req = next(r for r in linker.requirements if r.name == "c_functions")

        c_func = _make_c_symbol("compute", kind="function")
        c_var = _make_c_symbol("MY_CONST", kind="variable")
        c_method = _make_c_symbol("init", kind="method")

        ctx = LinkerContext(
            repo_root=Path("/tmp"),
            symbols=[c_func, c_var, c_method],
            edges=[],
        )

        assert c_req.check(ctx) == 2  # function + method, not variable
