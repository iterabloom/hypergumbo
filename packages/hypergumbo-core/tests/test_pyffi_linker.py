# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Python FFI linker (Python-C/C++/Rust interop).

The Python FFI linker creates ffi_bridge edges between Python code that calls
C/C++ functions via ctypes or cffi and the corresponding C/C++ implementations,
as well as Rust functions exposed to Python via PyO3's #[pyfunction].

Three FFI mechanisms are supported:

1. **ctypes**: ``ctypes.CDLL("lib.so")`` → ``lib.funcname()``
2. **cffi**: ``ffi.cdef(...)`` / ``ffi.dlopen(...)`` → ``lib.funcname()``
3. **PyO3**: Rust ``#[pyfunction] fn foo()`` → Python ``import mymod; mymod.foo()``
"""
from pathlib import Path

import pytest

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.linkers.registry import LinkerContext


def _make_python_symbol(
    name: str,
    kind: str = "function",
    path: str = "app.py",
    start_line: int = 1,
    end_line: int = 10,
) -> Symbol:
    """Create a test Python symbol."""
    run = AnalysisRun.create(pass_id="test", version="test")
    return Symbol(
        id=f"python:{path}:{start_line}-{end_line}:{name}:{kind}",
        name=name,
        kind=kind,
        language="python",
        path=path,
        span=Span(start_line=start_line, end_line=end_line, start_col=0, end_col=0),
        origin="python-v1",
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


def _make_rust_symbol(
    name: str,
    kind: str = "function",
    path: str = "src/lib.rs",
    start_line: int = 1,
    end_line: int = 10,
    annotations: list[dict] | None = None,
) -> Symbol:
    """Create a test Rust symbol.

    annotations: List of annotation dicts like [{"name": "pyfunction", "args": [], "kwargs": {}}].
        Stored in meta["annotations"] to match the Rust analyzer's output format.
    """
    run = AnalysisRun.create(pass_id="test", version="test")
    meta = {"annotations": annotations} if annotations else None
    return Symbol(
        id=f"rust:{path}:{start_line}-{end_line}:{name}:{kind}",
        name=name,
        kind=kind,
        language="rust",
        path=path,
        span=Span(start_line=start_line, end_line=end_line, start_col=0, end_col=0),
        origin="rust-v1",
        origin_run_id=run.execution_id,
        meta=meta,
    )


def _make_call_edge(
    src_id: str,
    callee_name: str,
    line: int = 5,
) -> Edge:
    """Create a call edge from a Python function to a named callee."""
    return Edge.create(
        src=src_id,
        dst=f"python:app.py:0-0:{callee_name}:unresolved",
        edge_type="calls",
        line=line,
        evidence_type="function_call",
        confidence=0.50,
        origin="python-v1",
    )


class TestPyFFILinkerCtypes:
    """Tests for ctypes FFI pattern detection."""

    def test_links_ctypes_call_to_c_function(self, tmp_path: Path) -> None:
        """Python ctypes.CDLL + attribute call links to C function."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        # Python file using ctypes
        py_file = tmp_path / "app.py"
        py_file.write_text(
            "import ctypes\n"
            "lib = ctypes.CDLL('./libmath.so')\n"
            "result = lib.fast_add(1, 2)\n"
        )

        py_func = _make_python_symbol(
            "app", kind="module", path=str(py_file), start_line=1, end_line=3
        )
        c_func = _make_c_symbol("fast_add", path="math.c")

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[c_func],
            rust_symbols=[],
            edges=[],
        )

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "ffi_bridge"
        assert edge.dst == c_func.id
        assert edge.evidence_type == "ctypes_call"

    def test_links_cdll_variant_names(self, tmp_path: Path) -> None:
        """Handles CDLL, cdll.LoadLibrary, WinDLL, OleDLL, PyDLL."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        py_file = tmp_path / "app.py"
        py_file.write_text(
            "import ctypes\n"
            "lib = ctypes.cdll.LoadLibrary('libfoo.so')\n"
            "lib.do_work()\n"
        )

        py_func = _make_python_symbol(
            "app", kind="module", path=str(py_file), start_line=1, end_line=3
        )
        c_func = _make_c_symbol("do_work", path="foo.c")

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[c_func],
            rust_symbols=[],
            edges=[],
        )

        assert len(result.edges) == 1
        assert result.edges[0].evidence_type == "ctypes_call"

    def test_multiple_ctypes_calls(self, tmp_path: Path) -> None:
        """Multiple ctypes attribute calls create multiple edges."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        py_file = tmp_path / "app.py"
        py_file.write_text(
            "import ctypes\n"
            "lib = ctypes.CDLL('./libmath.so')\n"
            "lib.add(1, 2)\n"
            "lib.multiply(3, 4)\n"
        )

        py_func = _make_python_symbol(
            "app", kind="module", path=str(py_file), start_line=1, end_line=4
        )
        c_add = _make_c_symbol("add", path="math.c", start_line=1, end_line=5)
        c_mul = _make_c_symbol("multiply", path="math.c", start_line=10, end_line=15)

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[c_add, c_mul],
            rust_symbols=[],
            edges=[],
        )

        assert len(result.edges) == 2
        dst_ids = {e.dst for e in result.edges}
        assert c_add.id in dst_ids
        assert c_mul.id in dst_ids

    def test_no_link_when_c_function_missing(self, tmp_path: Path) -> None:
        """No edge when ctypes calls a function not found in C symbols."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        py_file = tmp_path / "app.py"
        py_file.write_text(
            "import ctypes\n"
            "lib = ctypes.CDLL('./libfoo.so')\n"
            "lib.nonexistent()\n"
        )

        py_func = _make_python_symbol(
            "app", kind="module", path=str(py_file), start_line=1, end_line=3
        )

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[],
            rust_symbols=[],
            edges=[],
        )

        assert len(result.edges) == 0


class TestPyFFILinkerCffi:
    """Tests for cffi FFI pattern detection."""

    def test_links_cffi_call_to_c_function(self, tmp_path: Path) -> None:
        """Python cffi ffi.dlopen + attribute call links to C function."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        py_file = tmp_path / "app.py"
        py_file.write_text(
            "from cffi import FFI\n"
            "ffi = FFI()\n"
            "ffi.cdef('int compute(int x);')\n"
            "lib = ffi.dlopen('./libmath.so')\n"
            "lib.compute(42)\n"
        )

        py_func = _make_python_symbol(
            "app", kind="module", path=str(py_file), start_line=1, end_line=5
        )
        c_func = _make_c_symbol("compute", path="math.c")

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[c_func],
            rust_symbols=[],
            edges=[],
        )

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "ffi_bridge"
        assert edge.dst == c_func.id
        assert edge.evidence_type == "cffi_call"

    def test_cffi_verify_mode(self, tmp_path: Path) -> None:
        """cffi ffi.verify() is also detected."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        py_file = tmp_path / "build_ffi.py"
        py_file.write_text(
            "from cffi import FFI\n"
            "ffi = FFI()\n"
            "ffi.cdef('void process(void);')\n"
            "ffi.verify('#include \"native.h\"')\n"
            "lib = ffi.dlopen(None)\n"
            "lib.process()\n"
        )

        py_func = _make_python_symbol(
            "build_ffi", kind="module", path=str(py_file), start_line=1, end_line=6
        )
        c_func = _make_c_symbol("process", path="native.c")

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[c_func],
            rust_symbols=[],
            edges=[],
        )

        assert len(result.edges) == 1
        assert result.edges[0].evidence_type == "cffi_call"


class TestPyFFILinkerPyO3:
    """Tests for PyO3 (Rust-Python) FFI pattern detection."""

    def test_links_pyo3_function_to_rust(self, tmp_path: Path) -> None:
        """Rust #[pyfunction] with matching Python import creates edge."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        # Rust file with #[pyfunction]
        rs_file = tmp_path / "src" / "lib.rs"
        rs_file.parent.mkdir(parents=True)
        rs_file.write_text(
            '#[pyfunction]\n'
            'fn fast_compute(x: i32) -> i32 {\n'
            '    x * 2\n'
            '}\n'
        )

        # Python file importing the function
        py_file = tmp_path / "app.py"
        py_file.write_text(
            "from mymod import fast_compute\n"
            "result = fast_compute(42)\n"
        )

        py_func = _make_python_symbol(
            "app", kind="module", path=str(py_file), start_line=1, end_line=2
        )
        rust_func = _make_rust_symbol(
            "fast_compute",
            path=str(rs_file),
            annotations=[{"name": "pyfunction", "args": [], "kwargs": {}}],
        )

        # Python call edge to fast_compute
        call_edge = Edge.create(
            src=py_func.id,
            dst=f"python:{py_file}:0-0:fast_compute:unresolved",
            edge_type="calls",
            line=2,
            evidence_type="function_call",
            confidence=0.50,
            origin="python-v1",
        )

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[],
            rust_symbols=[rust_func],
            edges=[call_edge],
        )

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "ffi_bridge"
        assert edge.dst == rust_func.id
        assert edge.evidence_type == "pyo3_bridge"

    def test_pyo3_pyclass_method(self, tmp_path: Path) -> None:
        """Rust #[pymethods] with matching Python call creates edge."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        rs_file = tmp_path / "src" / "lib.rs"
        rs_file.parent.mkdir(parents=True)
        rs_file.write_text(
            '#[pyclass]\n'
            'struct Calculator {}\n'
            '\n'
            '#[pymethods]\n'
            'impl Calculator {\n'
            '    fn add(&self, a: i32, b: i32) -> i32 { a + b }\n'
            '}\n'
        )

        rust_method = _make_rust_symbol(
            "Calculator.add",
            kind="method",
            path=str(rs_file),
            start_line=6,
            end_line=6,
            annotations=[{"name": "pymethods", "args": [], "kwargs": {}}],
        )

        py_file = tmp_path / "app.py"
        py_file.write_text(
            "from mymod import Calculator\n"
            "c = Calculator()\n"
            "c.add(1, 2)\n"
        )
        py_func = _make_python_symbol(
            "app", kind="module", path=str(py_file), start_line=1, end_line=3
        )

        call_edge = Edge.create(
            src=py_func.id,
            dst=f"python:{py_file}:0-0:Calculator.add:unresolved",
            edge_type="calls",
            line=3,
            evidence_type="method_call",
            confidence=0.50,
            origin="python-v1",
        )

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[],
            rust_symbols=[rust_method],
            edges=[call_edge],
        )

        assert len(result.edges) == 1
        assert result.edges[0].evidence_type == "pyo3_bridge"

    def test_no_link_without_pyfunction_decorator(self, tmp_path: Path) -> None:
        """Regular Rust functions without #[pyfunction] don't create edges."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        rust_func = _make_rust_symbol(
            "internal_helper",
            path="src/lib.rs",
            # no pyfunction annotation
        )

        py_file = tmp_path / "app.py"
        py_file.write_text("import mymod\nmymod.internal_helper()\n")
        py_func = _make_python_symbol(
            "app", kind="module", path=str(py_file), start_line=1, end_line=2
        )

        call_edge = Edge.create(
            src=py_func.id,
            dst=f"python:{py_file}:0-0:internal_helper:unresolved",
            edge_type="calls",
            line=2,
            evidence_type="function_call",
            confidence=0.50,
            origin="python-v1",
        )

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[],
            rust_symbols=[rust_func],
            edges=[call_edge],
        )

        assert len(result.edges) == 0


class TestPyFFILinkerEdgeCases:
    """Edge case tests for Python FFI linker."""

    def test_empty_inputs(self, tmp_path: Path) -> None:
        """Handles empty inputs gracefully."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[],
            c_symbols=[],
            rust_symbols=[],
            edges=[],
        )

        assert result.edges == []
        assert result.run is not None

    def test_result_includes_run_metadata(self, tmp_path: Path) -> None:
        """Result includes analysis run metadata."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[],
            c_symbols=[],
            rust_symbols=[],
            edges=[],
        )

        assert result.run is not None
        assert result.run.pass_id == "pyffi-linker-v1"

    def test_cpp_symbols_also_linked(self, tmp_path: Path) -> None:
        """C++ functions are also matched via ctypes."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        py_file = tmp_path / "app.py"
        py_file.write_text(
            "import ctypes\n"
            "lib = ctypes.CDLL('./libfoo.so')\n"
            "lib.compute()\n"
        )

        py_func = _make_python_symbol(
            "app", kind="module", path=str(py_file), start_line=1, end_line=3
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

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[cpp_func],
            rust_symbols=[],
            edges=[],
        )

        assert len(result.edges) == 1
        assert result.edges[0].dst == cpp_func.id

    def test_non_python_files_ignored(self, tmp_path: Path) -> None:
        """Only scans Python files for ctypes/cffi patterns."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        # Non-Python file
        js_file = tmp_path / "app.js"
        js_file.write_text("const lib = require('ffi');\nlib.process();\n")

        js_sym = Symbol(
            id=f"javascript:{js_file}:1-2:app:module",
            name="app",
            kind="module",
            language="javascript",
            path=str(js_file),
            span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
            origin="js-v1",
            origin_run_id=AnalysisRun.create(pass_id="test", version="test").execution_id,
        )
        c_func = _make_c_symbol("process", path="native.c")

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[js_sym],
            c_symbols=[c_func],
            rust_symbols=[],
            edges=[],
        )

        assert len(result.edges) == 0

    def test_edge_confidence_level(self, tmp_path: Path) -> None:
        """Edges have appropriate confidence level."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        py_file = tmp_path / "app.py"
        py_file.write_text(
            "import ctypes\n"
            "lib = ctypes.CDLL('./libfoo.so')\n"
            "lib.compute()\n"
        )

        py_func = _make_python_symbol(
            "app", kind="module", path=str(py_file), start_line=1, end_line=3
        )
        c_func = _make_c_symbol("compute", path="foo.c")

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[c_func],
            rust_symbols=[],
            edges=[],
        )

        assert len(result.edges) == 1
        assert result.edges[0].confidence >= 0.80

    def test_deduplicates_edges(self, tmp_path: Path) -> None:
        """Same ctypes call appearing twice creates only one edge (dedup by src+name)."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        py_file = tmp_path / "app.py"
        py_file.write_text(
            "import ctypes\n"
            "lib = ctypes.CDLL('./libfoo.so')\n"
            "lib.compute()\n"
            "lib.compute()\n"  # duplicate call
        )

        py_func = _make_python_symbol(
            "app", kind="module", path=str(py_file), start_line=1, end_line=4
        )
        c_func = _make_c_symbol("compute", path="foo.c")

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[c_func],
            rust_symbols=[],
            edges=[],
        )

        # Dedup means only one edge per (src_id, func_name) pair
        assert len(result.edges) == 1
        assert result.edges[0].dst == c_func.id

    def test_underscore_prefix_calls_skipped(self, tmp_path: Path) -> None:
        """Calls to _private functions are skipped (not FFI calls)."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        py_file = tmp_path / "app.py"
        py_file.write_text(
            "import ctypes\n"
            "lib = ctypes.CDLL('./libfoo.so')\n"
            "lib._internal()\n"
            "lib.__handle()\n"
        )

        py_func = _make_python_symbol(
            "app", kind="module", path=str(py_file), start_line=1, end_line=4
        )
        c_func = _make_c_symbol("_internal", path="foo.c")

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[c_func],
            rust_symbols=[],
            edges=[],
        )

        assert len(result.edges) == 0

    def test_relative_path_resolved_to_repo_root(self, tmp_path: Path) -> None:
        """Relative paths in symbols are resolved relative to repo_root."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        py_file = tmp_path / "app.py"
        py_file.write_text(
            "import ctypes\n"
            "lib = ctypes.CDLL('./libfoo.so')\n"
            "lib.process()\n"
        )

        # Symbol with relative path (not absolute)
        py_func = _make_python_symbol(
            "app", kind="module", path="app.py", start_line=1, end_line=3
        )
        c_func = _make_c_symbol("process", path="native.c")

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[c_func],
            rust_symbols=[],
            edges=[],
        )

        assert len(result.edges) == 1

    def test_nonexistent_file_skipped(self, tmp_path: Path) -> None:
        """Python files that don't exist on disk are silently skipped."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        py_func = _make_python_symbol(
            "app", kind="module",
            path=str(tmp_path / "nonexistent.py"),
            start_line=1, end_line=3,
        )
        c_func = _make_c_symbol("process", path="native.c")

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[c_func],
            rust_symbols=[],
            edges=[],
        )

        assert len(result.edges) == 0

    def test_enclosing_symbol_refinement(self, tmp_path: Path) -> None:
        """When multiple symbols span the same file, the smallest enclosing one is used."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        py_file = tmp_path / "app.py"
        py_file.write_text(
            "import ctypes\n"
            "lib = ctypes.CDLL('./libfoo.so')\n"
            "\n"
            "def helper():\n"
            "    lib.compute()\n"
        )

        # Module-level symbol (broader)
        mod_sym = _make_python_symbol(
            "app", kind="module", path=str(py_file), start_line=1, end_line=5
        )
        # Function-level symbol (narrower, contains the call on line 5)
        func_sym = _make_python_symbol(
            "helper", kind="function", path=str(py_file), start_line=4, end_line=5
        )
        c_func = _make_c_symbol("compute", path="foo.c")

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[mod_sym, func_sym],
            c_symbols=[c_func],
            rust_symbols=[],
            edges=[],
        )

        assert len(result.edges) == 1
        # Should prefer the narrower enclosing symbol (helper function)
        assert result.edges[0].src == func_sym.id

    def test_c_struct_symbols_ignored(self, tmp_path: Path) -> None:
        """C struct symbols (non-function) are not matched for FFI linking."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        py_file = tmp_path / "app.py"
        py_file.write_text(
            "import ctypes\n"
            "lib = ctypes.CDLL('./libfoo.so')\n"
            "lib.data_struct()\n"
        )

        py_func = _make_python_symbol(
            "app", kind="module", path=str(py_file), start_line=1, end_line=3
        )
        c_struct = _make_c_symbol("data_struct", kind="struct", path="types.h")

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[c_struct],
            rust_symbols=[],
            edges=[],
        )

        assert len(result.edges) == 0

    def test_pyo3_ignores_resolved_edges(self, tmp_path: Path) -> None:
        """PyO3 phase ignores edges that are already resolved."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        rust_func = _make_rust_symbol(
            "fast_compute",
            annotations=[{"name": "pyfunction", "args": [], "kwargs": {}}],
        )
        py_func = _make_python_symbol("app", kind="module", path="app.py")

        # An already-resolved edge (does NOT end with :unresolved)
        resolved_edge = Edge.create(
            src=py_func.id,
            dst=rust_func.id,
            edge_type="calls",
            line=5,
            evidence_type="function_call",
            confidence=0.85,
            origin="python-v1",
        )

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[],
            rust_symbols=[rust_func],
            edges=[resolved_edge],
        )

        assert len(result.edges) == 0

    def test_pyo3_ignores_unresolved_with_no_match(self, tmp_path: Path) -> None:
        """PyO3 phase ignores unresolved edges that don't match any PyO3 function."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        rust_func = _make_rust_symbol(
            "fast_compute",
            annotations=[{"name": "pyfunction", "args": [], "kwargs": {}}],
        )
        py_func = _make_python_symbol("app", kind="module", path="app.py")

        # Unresolved edge to a function that doesn't match any PyO3 symbol
        unresolved_edge = Edge.create(
            src=py_func.id,
            dst="python:app.py:0-0:totally_different:unresolved",
            edge_type="calls",
            line=5,
            evidence_type="function_call",
            confidence=0.50,
            origin="python-v1",
        )

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[],
            rust_symbols=[rust_func],
            edges=[unresolved_edge],
        )

        assert len(result.edges) == 0

    def test_pyo3_short_dst_ignored(self, tmp_path: Path) -> None:
        """Unresolved edges with too few colon-separated parts are ignored."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        rust_func = _make_rust_symbol(
            "fast_compute",
            annotations=[{"name": "pyfunction", "args": [], "kwargs": {}}],
        )
        py_func = _make_python_symbol("app", kind="module", path="app.py")

        # Malformed unresolved edge with too few parts
        bad_edge = Edge.create(
            src=py_func.id,
            dst="x:y:unresolved",
            edge_type="calls",
            line=5,
            evidence_type="function_call",
            confidence=0.50,
            origin="python-v1",
        )

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[],
            rust_symbols=[rust_func],
            edges=[bad_edge],
        )

        assert len(result.edges) == 0

    def test_pyo3_deduplicates_edges(self, tmp_path: Path) -> None:
        """PyO3 phase deduplicates edges by (src, call_name)."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        rust_func = _make_rust_symbol(
            "fast_compute",
            annotations=[{"name": "pyfunction", "args": [], "kwargs": {}}],
        )
        py_func = _make_python_symbol("app", kind="module", path="app.py")

        # Two unresolved edges to the same function from the same source
        edge1 = Edge.create(
            src=py_func.id,
            dst="python:app.py:0-0:fast_compute:unresolved",
            edge_type="calls",
            line=5,
            evidence_type="function_call",
            confidence=0.50,
            origin="python-v1",
        )
        edge2 = Edge.create(
            src=py_func.id,
            dst="python:app.py:0-0:fast_compute:unresolved",
            edge_type="calls",
            line=8,
            evidence_type="function_call",
            confidence=0.50,
            origin="python-v1",
        )

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[],
            rust_symbols=[rust_func],
            edges=[edge1, edge2],
        )

        assert len(result.edges) == 1

    def test_rust_non_function_kind_ignored(self, tmp_path: Path) -> None:
        """Rust symbols that are not function/method are ignored by PyO3 detection."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        # Struct with pyclass annotation — should not be in pyo3_lookup
        rust_struct = _make_rust_symbol(
            "Calculator",
            kind="struct",
            annotations=[{"name": "pyclass", "args": [], "kwargs": {}}],
        )
        py_func = _make_python_symbol("app", kind="module", path="app.py")

        call_edge = Edge.create(
            src=py_func.id,
            dst="python:app.py:0-0:Calculator:unresolved",
            edge_type="calls",
            line=2,
            evidence_type="function_call",
            confidence=0.50,
            origin="python-v1",
        )

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[],
            rust_symbols=[rust_struct],
            edges=[call_edge],
        )

        # Struct is not function/method, so no PyO3 match
        assert len(result.edges) == 0

    def test_rust_no_meta_ignored(self, tmp_path: Path) -> None:
        """Rust symbols without meta are ignored by PyO3 detection."""
        from hypergumbo_core.linkers.pyffi import link_pyffi

        rust_func = _make_rust_symbol("plain_func")
        # plain_func has no annotations -> meta is None
        assert rust_func.meta is None

        py_func = _make_python_symbol("app", kind="module", path="app.py")
        call_edge = Edge.create(
            src=py_func.id,
            dst="python:app.py:0-0:plain_func:unresolved",
            edge_type="calls",
            line=2,
            evidence_type="function_call",
            confidence=0.50,
            origin="python-v1",
        )

        result = link_pyffi(
            repo_root=tmp_path,
            python_symbols=[py_func],
            c_symbols=[],
            rust_symbols=[rust_func],
            edges=[call_edge],
        )

        assert len(result.edges) == 0


class TestPyFFILinkerRegistry:
    """Tests for Python FFI linker registry integration."""

    @pytest.fixture(autouse=True)
    def ensure_pyffi_registered(self) -> None:
        """Ensure pyffi linker is registered before each test."""
        import importlib
        import hypergumbo_core.linkers.pyffi as pyffi_module
        importlib.reload(pyffi_module)

    def test_pyffi_linker_registered(self) -> None:
        """Python FFI linker is registered in the linker registry."""
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("pyffi")
        assert linker is not None
        assert linker.name == "pyffi"

    def test_pyffi_linker_has_requirements(self) -> None:
        """Python FFI linker declares its requirements."""
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("pyffi")
        assert linker is not None
        assert len(linker.requirements) >= 2

        req_names = [r.name for r in linker.requirements]
        assert "python_ffi_files" in req_names
        assert "c_cpp_rust_functions" in req_names

    def test_pyffi_linker_activation(self) -> None:
        """Python FFI linker activates for python+c, python+cpp, python+rust pairs."""
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("pyffi")
        assert linker is not None

        # Should activate when python and c are both present
        assert linker.activation.should_run(set(), {"python", "c"})
        # Should activate when python and cpp are both present
        assert linker.activation.should_run(set(), {"python", "cpp"})
        # Should activate when python and rust are both present
        assert linker.activation.should_run(set(), {"python", "rust"})
        # Should NOT activate when only python is present
        assert not linker.activation.should_run(set(), {"python"})
        # Should NOT activate when only c is present
        assert not linker.activation.should_run(set(), {"c"})

    def test_pyffi_requirements_met(self) -> None:
        """PyFFI requirements report as met when matching data exists."""
        from hypergumbo_core.linkers.registry import check_linker_requirements

        py_func = _make_python_symbol("app", kind="module", path="app.py")
        c_func = _make_c_symbol("process", path="native.c")
        rust_func = _make_rust_symbol(
            "compute", annotations=[{"name": "pyfunction", "args": [], "kwargs": {}}],
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[py_func, c_func, rust_func],
            edges=[],
        )

        diagnostics = check_linker_requirements(ctx)
        pyffi_diag = next((d for d in diagnostics if d.linker_name == "pyffi"), None)
        assert pyffi_diag is not None
        assert pyffi_diag.all_met is True

    def test_pyffi_requirements_unmet_no_native(self) -> None:
        """PyFFI requirements unmet when no C/C++/Rust functions exist."""
        from hypergumbo_core.linkers.registry import check_linker_requirements

        py_func = _make_python_symbol("app", kind="module", path="app.py")

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[py_func],
            edges=[],
        )

        diagnostics = check_linker_requirements(ctx)
        pyffi_diag = next((d for d in diagnostics if d.linker_name == "pyffi"), None)
        assert pyffi_diag is not None
        assert pyffi_diag.all_met is False

        native_req = next(
            (r for r in pyffi_diag.requirements if r.name == "c_cpp_rust_functions"), None
        )
        assert native_req is not None
        assert native_req.met is False

    def test_pyffi_linker_via_registry_dispatch(self, tmp_path: Path) -> None:
        """Python FFI linker works via registry dispatch."""
        from hypergumbo_core.linkers.registry import run_linker

        py_file = tmp_path / "app.py"
        py_file.write_text(
            "import ctypes\n"
            "lib = ctypes.CDLL('./libfoo.so')\n"
            "lib.process()\n"
        )

        py_func = _make_python_symbol(
            "app", kind="module", path=str(py_file), start_line=1, end_line=3
        )
        c_func = _make_c_symbol("process", path="native.c")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[py_func, c_func],
            edges=[],
        )

        result = run_linker("pyffi", ctx)

        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "ffi_bridge"
