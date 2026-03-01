"""Tests for Ruby FFI linker (Ruby-C/C++ interop).

The Ruby FFI linker creates ffi_bridge edges between Ruby code that calls
C/C++ functions via the FFI gem (``attach_function``) and the corresponding
C/C++ implementations, as well as C extension registration patterns
(``rb_define_method``) that expose C functions to Ruby.

Two FFI mechanisms are supported:

1. **FFI gem**: ``extend FFI::Library`` + ``attach_function :name, ...``
2. **C extensions**: ``rb_define_method(klass, "name", c_func, argc)``
"""
from pathlib import Path

import pytest

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.linkers.registry import LinkerContext


def _make_ruby_symbol(
    name: str,
    kind: str = "function",
    path: str = "lib/app.rb",
    start_line: int = 1,
    end_line: int = 10,
) -> Symbol:
    """Create a test Ruby symbol."""
    run = AnalysisRun.create(pass_id="test", version="test")
    return Symbol(
        id=f"ruby:{path}:{start_line}-{end_line}:{name}:{kind}",
        name=name,
        kind=kind,
        language="ruby",
        path=path,
        span=Span(start_line=start_line, end_line=end_line, start_col=0, end_col=0),
        origin="ruby-v1",
        origin_run_id=run.execution_id,
    )


def _make_c_symbol(
    name: str,
    kind: str = "function",
    path: str = "ext/native.c",
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


class TestRubyFFIGem:
    """Tests for Ruby FFI gem pattern detection."""

    def test_links_attach_function_to_c_function(self, tmp_path: Path) -> None:
        """Ruby FFI attach_function links to C function."""
        from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

        rb_file = tmp_path / "lib" / "mylib.rb"
        rb_file.parent.mkdir(parents=True)
        rb_file.write_text(
            "require 'ffi'\n"
            "module MyLib\n"
            "  extend FFI::Library\n"
            "  ffi_lib 'libmath'\n"
            "  attach_function :fast_add, [:int, :int], :int\n"
            "end\n"
        )

        rb_sym = _make_ruby_symbol(
            "MyLib", kind="module", path=str(rb_file), start_line=2, end_line=6
        )
        c_func = _make_c_symbol("fast_add", path="src/math.c")

        result = link_ruby_ffi(
            repo_root=tmp_path,
            ruby_symbols=[rb_sym],
            c_symbols=[c_func],
            edges=[],
        )

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "ffi_bridge"
        assert edge.dst == c_func.id
        assert edge.evidence_type == "ruby_ffi_attach"

    def test_attach_function_with_explicit_c_name(self, tmp_path: Path) -> None:
        """attach_function with different Ruby and C names uses the C name."""
        from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

        rb_file = tmp_path / "lib" / "mylib.rb"
        rb_file.parent.mkdir(parents=True)
        rb_file.write_text(
            "require 'ffi'\n"
            "module MyLib\n"
            "  extend FFI::Library\n"
            "  ffi_lib 'libfoo'\n"
            "  attach_function :ruby_name, :c_real_name, [:int], :int\n"
            "end\n"
        )

        rb_sym = _make_ruby_symbol(
            "MyLib", kind="module", path=str(rb_file), start_line=2, end_line=6
        )
        c_func = _make_c_symbol("c_real_name", path="src/foo.c")

        result = link_ruby_ffi(
            repo_root=tmp_path,
            ruby_symbols=[rb_sym],
            c_symbols=[c_func],
            edges=[],
        )

        assert len(result.edges) == 1
        assert result.edges[0].dst == c_func.id

    def test_multiple_attach_functions(self, tmp_path: Path) -> None:
        """Multiple attach_function declarations create multiple edges."""
        from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

        rb_file = tmp_path / "lib" / "mylib.rb"
        rb_file.parent.mkdir(parents=True)
        rb_file.write_text(
            "require 'ffi'\n"
            "module MyLib\n"
            "  extend FFI::Library\n"
            "  ffi_lib 'libmath'\n"
            "  attach_function :add, [:int, :int], :int\n"
            "  attach_function :multiply, [:int, :int], :int\n"
            "end\n"
        )

        rb_sym = _make_ruby_symbol(
            "MyLib", kind="module", path=str(rb_file), start_line=2, end_line=7
        )
        c_add = _make_c_symbol("add", path="src/math.c", start_line=1, end_line=5)
        c_mul = _make_c_symbol("multiply", path="src/math.c", start_line=10, end_line=15)

        result = link_ruby_ffi(
            repo_root=tmp_path,
            ruby_symbols=[rb_sym],
            c_symbols=[c_add, c_mul],
            edges=[],
        )

        assert len(result.edges) == 2
        dst_ids = {e.dst for e in result.edges}
        assert c_add.id in dst_ids
        assert c_mul.id in dst_ids

    def test_no_link_when_c_function_missing(self, tmp_path: Path) -> None:
        """No edge when attach_function references a C function not in symbols."""
        from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

        rb_file = tmp_path / "lib" / "mylib.rb"
        rb_file.parent.mkdir(parents=True)
        rb_file.write_text(
            "require 'ffi'\n"
            "module MyLib\n"
            "  extend FFI::Library\n"
            "  attach_function :nonexistent, [], :void\n"
            "end\n"
        )

        rb_sym = _make_ruby_symbol(
            "MyLib", kind="module", path=str(rb_file), start_line=2, end_line=5
        )

        result = link_ruby_ffi(
            repo_root=tmp_path,
            ruby_symbols=[rb_sym],
            c_symbols=[],
            edges=[],
        )

        assert len(result.edges) == 0

    def test_attach_function_with_string_name(self, tmp_path: Path) -> None:
        """attach_function with string name (instead of symbol)."""
        from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

        rb_file = tmp_path / "lib" / "mylib.rb"
        rb_file.parent.mkdir(parents=True)
        rb_file.write_text(
            "require 'ffi'\n"
            "module MyLib\n"
            "  extend FFI::Library\n"
            "  attach_function 'compute', [:int], :int\n"
            "end\n"
        )

        rb_sym = _make_ruby_symbol(
            "MyLib", kind="module", path=str(rb_file), start_line=2, end_line=5
        )
        c_func = _make_c_symbol("compute", path="src/native.c")

        result = link_ruby_ffi(
            repo_root=tmp_path,
            ruby_symbols=[rb_sym],
            c_symbols=[c_func],
            edges=[],
        )

        assert len(result.edges) == 1
        assert result.edges[0].dst == c_func.id


class TestRubyCExtension:
    """Tests for C extension registration pattern detection."""

    def test_links_rb_define_method(self, tmp_path: Path) -> None:
        """rb_define_method in C links C function to Ruby class."""
        from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

        c_file = tmp_path / "ext" / "myext" / "myext.c"
        c_file.parent.mkdir(parents=True)
        c_file.write_text(
            '#include "ruby.h"\n'
            'static VALUE rb_compute(VALUE self, VALUE x) { return INT2NUM(42); }\n'
            'void Init_myext() {\n'
            '  VALUE cMyExt = rb_define_class("MyExt", rb_cObject);\n'
            '  rb_define_method(cMyExt, "compute", rb_compute, 1);\n'
            '}\n'
        )

        c_func = _make_c_symbol(
            "rb_compute", path=str(c_file), start_line=2, end_line=2
        )
        rb_sym = _make_ruby_symbol("MyExt", kind="class", path="lib/myext.rb")

        result = link_ruby_ffi(
            repo_root=tmp_path,
            ruby_symbols=[rb_sym],
            c_symbols=[c_func],
            edges=[],
        )

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "ffi_bridge"
        assert edge.evidence_type == "ruby_c_extension"
        assert edge.dst == c_func.id

    def test_rb_define_module_function(self, tmp_path: Path) -> None:
        """rb_define_module_function also creates edges."""
        from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

        c_file = tmp_path / "ext" / "mymod.c"
        c_file.parent.mkdir(parents=True)
        c_file.write_text(
            '#include "ruby.h"\n'
            'static VALUE process_data(VALUE self) { return Qnil; }\n'
            'void Init_mymod() {\n'
            '  VALUE mMyMod = rb_define_module("MyMod");\n'
            '  rb_define_module_function(mMyMod, "process", process_data, 0);\n'
            '}\n'
        )

        c_func = _make_c_symbol(
            "process_data", path=str(c_file), start_line=2, end_line=2
        )

        result = link_ruby_ffi(
            repo_root=tmp_path,
            ruby_symbols=[],
            c_symbols=[c_func],
            edges=[],
        )

        assert len(result.edges) == 1
        assert result.edges[0].evidence_type == "ruby_c_extension"

    def test_rb_define_singleton_method(self, tmp_path: Path) -> None:
        """rb_define_singleton_method also creates edges."""
        from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

        c_file = tmp_path / "ext" / "myext.c"
        c_file.parent.mkdir(parents=True)
        c_file.write_text(
            '#include "ruby.h"\n'
            'static VALUE version_info(VALUE self) { return rb_str_new2("1.0"); }\n'
            'void Init_myext() {\n'
            '  VALUE cMyExt = rb_define_class("MyExt", rb_cObject);\n'
            '  rb_define_singleton_method(cMyExt, "version", version_info, 0);\n'
            '}\n'
        )

        c_func = _make_c_symbol(
            "version_info", path=str(c_file), start_line=2, end_line=2
        )

        result = link_ruby_ffi(
            repo_root=tmp_path,
            ruby_symbols=[],
            c_symbols=[c_func],
            edges=[],
        )

        assert len(result.edges) == 1

    def test_c_function_not_found_skipped(self, tmp_path: Path) -> None:
        """rb_define_method referencing unknown C function creates no edge."""
        from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

        c_file = tmp_path / "ext" / "myext.c"
        c_file.parent.mkdir(parents=True)
        c_file.write_text(
            '#include "ruby.h"\n'
            'void Init_myext() {\n'
            '  rb_define_method(cMyExt, "compute", nonexistent_func, 1);\n'
            '}\n'
        )

        result = link_ruby_ffi(
            repo_root=tmp_path,
            ruby_symbols=[],
            c_symbols=[],
            edges=[],
        )

        assert len(result.edges) == 0


class TestRubyFFIEdgeCases:
    """Edge case tests for Ruby FFI linker."""

    def test_empty_inputs(self, tmp_path: Path) -> None:
        """Handles empty inputs gracefully."""
        from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

        result = link_ruby_ffi(
            repo_root=tmp_path,
            ruby_symbols=[],
            c_symbols=[],
            edges=[],
        )

        assert result.edges == []
        assert result.run is not None

    def test_result_includes_run_metadata(self, tmp_path: Path) -> None:
        """Result includes analysis run metadata."""
        from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

        result = link_ruby_ffi(
            repo_root=tmp_path,
            ruby_symbols=[],
            c_symbols=[],
            edges=[],
        )

        assert result.run is not None
        assert result.run.pass_id == "ruby-ffi-linker-v1"

    def test_cpp_symbols_also_linked(self, tmp_path: Path) -> None:
        """C++ functions are also matched via FFI gem."""
        from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

        rb_file = tmp_path / "lib" / "mylib.rb"
        rb_file.parent.mkdir(parents=True)
        rb_file.write_text(
            "require 'ffi'\n"
            "module MyLib\n"
            "  extend FFI::Library\n"
            "  attach_function :compute, [], :void\n"
            "end\n"
        )

        rb_sym = _make_ruby_symbol(
            "MyLib", kind="module", path=str(rb_file), start_line=2, end_line=5
        )
        run = AnalysisRun.create(pass_id="test", version="test")
        cpp_func = Symbol(
            id="cpp:foo.cpp:1-10:compute:function",
            name="compute",
            kind="function",
            language="cpp",
            path="foo.cpp",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="cpp-v1",
            origin_run_id=run.execution_id,
        )

        result = link_ruby_ffi(
            repo_root=tmp_path,
            ruby_symbols=[rb_sym],
            c_symbols=[cpp_func],
            edges=[],
        )

        assert len(result.edges) == 1
        assert result.edges[0].dst == cpp_func.id

    def test_edge_confidence_level(self, tmp_path: Path) -> None:
        """Edges have appropriate confidence level."""
        from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

        rb_file = tmp_path / "lib" / "mylib.rb"
        rb_file.parent.mkdir(parents=True)
        rb_file.write_text(
            "require 'ffi'\n"
            "module MyLib\n"
            "  extend FFI::Library\n"
            "  attach_function :compute, [], :void\n"
            "end\n"
        )

        rb_sym = _make_ruby_symbol(
            "MyLib", kind="module", path=str(rb_file), start_line=2, end_line=5
        )
        c_func = _make_c_symbol("compute", path="src/foo.c")

        result = link_ruby_ffi(
            repo_root=tmp_path,
            ruby_symbols=[rb_sym],
            c_symbols=[c_func],
            edges=[],
        )

        assert len(result.edges) == 1
        assert result.edges[0].confidence >= 0.80

    def test_nonexistent_ruby_file_skipped(self, tmp_path: Path) -> None:
        """Ruby files that don't exist on disk are silently skipped."""
        from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

        rb_sym = _make_ruby_symbol(
            "MyLib", kind="module",
            path=str(tmp_path / "nonexistent.rb"),
        )
        c_func = _make_c_symbol("compute", path="native.c")

        result = link_ruby_ffi(
            repo_root=tmp_path,
            ruby_symbols=[rb_sym],
            c_symbols=[c_func],
            edges=[],
        )

        assert len(result.edges) == 0

    def test_relative_path_resolved(self, tmp_path: Path) -> None:
        """Relative paths are resolved relative to repo_root."""
        from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

        rb_file = tmp_path / "lib" / "mylib.rb"
        rb_file.parent.mkdir(parents=True)
        rb_file.write_text(
            "require 'ffi'\n"
            "module MyLib\n"
            "  extend FFI::Library\n"
            "  attach_function :compute, [], :void\n"
            "end\n"
        )

        rb_sym = _make_ruby_symbol(
            "MyLib", kind="module", path="lib/mylib.rb", start_line=2, end_line=5
        )
        c_func = _make_c_symbol("compute", path="src/foo.c")

        result = link_ruby_ffi(
            repo_root=tmp_path,
            ruby_symbols=[rb_sym],
            c_symbols=[c_func],
            edges=[],
        )

        assert len(result.edges) == 1

    def test_deduplicates_edges(self, tmp_path: Path) -> None:
        """Same attach_function appearing in two files creates separate edges."""
        from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

        rb_file = tmp_path / "lib" / "mylib.rb"
        rb_file.parent.mkdir(parents=True)
        rb_file.write_text(
            "require 'ffi'\n"
            "module MyLib\n"
            "  extend FFI::Library\n"
            "  attach_function :compute, [], :void\n"
            "  attach_function :compute, [], :void\n"
            "end\n"
        )

        rb_sym = _make_ruby_symbol(
            "MyLib", kind="module", path=str(rb_file), start_line=2, end_line=6
        )
        c_func = _make_c_symbol("compute", path="src/foo.c")

        result = link_ruby_ffi(
            repo_root=tmp_path,
            ruby_symbols=[rb_sym],
            c_symbols=[c_func],
            edges=[],
        )

        # Dedup by (src_id, func_name)
        assert len(result.edges) == 1

    def test_enclosing_symbol_refinement(self, tmp_path: Path) -> None:
        """When multiple symbols span the same file, the narrower one is used."""
        from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

        rb_file = tmp_path / "lib" / "mylib.rb"
        rb_file.parent.mkdir(parents=True)
        rb_file.write_text(
            "require 'ffi'\n"
            "module Outer\n"
            "  module Inner\n"
            "    extend FFI::Library\n"
            "    attach_function :compute, [], :void\n"
            "  end\n"
            "end\n"
        )

        outer_sym = _make_ruby_symbol(
            "Outer", kind="module", path=str(rb_file), start_line=2, end_line=7
        )
        inner_sym = _make_ruby_symbol(
            "Inner", kind="module", path=str(rb_file), start_line=3, end_line=6
        )
        c_func = _make_c_symbol("compute", path="src/foo.c")

        result = link_ruby_ffi(
            repo_root=tmp_path,
            ruby_symbols=[outer_sym, inner_sym],
            c_symbols=[c_func],
            edges=[],
        )

        assert len(result.edges) == 1
        # Should prefer the narrower Inner module
        assert result.edges[0].src == inner_sym.id

    def test_non_ruby_files_ignored(self, tmp_path: Path) -> None:
        """Only scans Ruby files (.rb) for FFI patterns."""
        from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

        py_file = tmp_path / "app.py"
        py_file.write_text("attach_function :compute, [], :void\n")

        py_sym = Symbol(
            id=f"python:{py_file}:1-1:app:module",
            name="app",
            kind="module",
            language="python",
            path=str(py_file),
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin="py-v1",
            origin_run_id=AnalysisRun.create(pass_id="test", version="test").execution_id,
        )
        c_func = _make_c_symbol("compute", path="native.c")

        result = link_ruby_ffi(
            repo_root=tmp_path,
            ruby_symbols=[py_sym],
            c_symbols=[c_func],
            edges=[],
        )

        assert len(result.edges) == 0

    def test_nonexistent_c_extension_file_skipped(self, tmp_path: Path) -> None:
        """C extension files that don't exist are skipped."""
        from hypergumbo_core.linkers.ruby_ffi import link_ruby_ffi

        c_func = _make_c_symbol(
            "rb_compute", path=str(tmp_path / "nonexistent.c")
        )

        result = link_ruby_ffi(
            repo_root=tmp_path,
            ruby_symbols=[],
            c_symbols=[c_func],
            edges=[],
        )

        assert len(result.edges) == 0


class TestRubyFFIRegistry:
    """Tests for Ruby FFI linker registry integration."""

    @pytest.fixture(autouse=True)
    def ensure_registered(self) -> None:
        """Ensure ruby_ffi linker is registered before each test."""
        import importlib
        import hypergumbo_core.linkers.ruby_ffi as mod
        importlib.reload(mod)

    def test_registered(self) -> None:
        """Ruby FFI linker is registered in the linker registry."""
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("ruby_ffi")
        assert linker is not None
        assert linker.name == "ruby_ffi"

    def test_has_requirements(self) -> None:
        """Ruby FFI linker declares its requirements."""
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("ruby_ffi")
        assert linker is not None
        assert len(linker.requirements) >= 2

        req_names = [r.name for r in linker.requirements]
        assert "ruby_files" in req_names
        assert "c_cpp_functions" in req_names

    def test_activation(self) -> None:
        """Ruby FFI linker activates for ruby+c and ruby+cpp language pairs."""
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("ruby_ffi")
        assert linker is not None

        assert linker.activation.should_run(set(), {"ruby", "c"})
        assert linker.activation.should_run(set(), {"ruby", "cpp"})
        assert not linker.activation.should_run(set(), {"ruby"})
        assert not linker.activation.should_run(set(), {"c"})

    def test_via_registry_dispatch(self, tmp_path: Path) -> None:
        """Ruby FFI linker works via registry dispatch."""
        from hypergumbo_core.linkers.registry import run_linker

        rb_file = tmp_path / "lib" / "mylib.rb"
        rb_file.parent.mkdir(parents=True)
        rb_file.write_text(
            "require 'ffi'\n"
            "module MyLib\n"
            "  extend FFI::Library\n"
            "  attach_function :process, [], :void\n"
            "end\n"
        )

        rb_sym = _make_ruby_symbol(
            "MyLib", kind="module", path=str(rb_file), start_line=2, end_line=5
        )
        c_func = _make_c_symbol("process", path="native.c")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[rb_sym, c_func],
            edges=[],
        )

        result = run_linker("ruby_ffi", ctx)

        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "ffi_bridge"

    def test_requirements_met(self) -> None:
        """Requirements report as met when matching data exists."""
        from hypergumbo_core.linkers.registry import check_linker_requirements

        rb_sym = _make_ruby_symbol("MyLib", kind="module", path="lib/app.rb")
        c_func = _make_c_symbol("process", path="native.c")

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[rb_sym, c_func],
            edges=[],
        )

        diagnostics = check_linker_requirements(ctx)
        diag = next((d for d in diagnostics if d.linker_name == "ruby_ffi"), None)
        assert diag is not None
        assert diag.all_met is True

    def test_requirements_unmet_no_c(self) -> None:
        """Requirements unmet when no C/C++ functions exist."""
        from hypergumbo_core.linkers.registry import check_linker_requirements

        rb_sym = _make_ruby_symbol("MyLib", kind="module", path="lib/app.rb")

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[rb_sym],
            edges=[],
        )

        diagnostics = check_linker_requirements(ctx)
        diag = next((d for d in diagnostics if d.linker_name == "ruby_ffi"), None)
        assert diag is not None
        assert diag.all_met is False
