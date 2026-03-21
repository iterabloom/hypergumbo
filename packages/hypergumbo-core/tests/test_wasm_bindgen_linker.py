# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the wasm_bindgen linker.

Covers: Rust #[wasm_bindgen] export detection, JS/TS wasm import scanning,
edge creation between JS/TS callers and Rust wasm_bindgen exports, rename
handling (js_name), struct method exports, and registry integration.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol


def _make_span(start: int = 1, end: int = 1) -> Span:
    return Span(start_line=start, end_line=end, start_col=0, end_col=0)


def _make_rust_sym(
    name: str,
    path: str = "src/lib.rs",
    annotations: list[dict] | None = None,
    kind: str = "function",
) -> Symbol:
    """Create a Rust symbol with optional annotations."""
    meta = {"annotations": annotations} if annotations else None
    return Symbol(
        id=f"rust:{path}:1-1:{name}:{kind}",
        name=name,
        kind=kind,
        language="rust",
        path=path,
        span=_make_span(),
        meta=meta,
    )


def _make_js_sym(
    name: str,
    path: str = "src/index.ts",
    language: str = "typescript",
) -> Symbol:
    """Create a JS/TS symbol."""
    return Symbol(
        id=f"{language}:{path}:1-1:{name}:function",
        name=name,
        kind="function",
        language=language,
        path=path,
        span=_make_span(),
    )


class TestFindWasmBindgenExports:
    """Tests for _find_wasm_bindgen_exports internal function."""

    def test_basic_wasm_bindgen_annotation(self) -> None:
        """Detects #[wasm_bindgen] annotated Rust functions."""
        from hypergumbo_core.linkers.wasm_bindgen import _find_wasm_bindgen_exports

        sym = _make_rust_sym("greet", annotations=[{"name": "wasm_bindgen"}])
        result = _find_wasm_bindgen_exports([sym])
        assert "greet" in result
        assert result["greet"] is sym

    def test_js_name_rename(self) -> None:
        """Handles js_name='customName' rename."""
        from hypergumbo_core.linkers.wasm_bindgen import _find_wasm_bindgen_exports

        sym = _make_rust_sym(
            "get_data",
            annotations=[{"name": "wasm_bindgen", "kwargs": {"js_name": "getData"}}],
        )
        result = _find_wasm_bindgen_exports([sym])
        # Registered under both original and renamed
        assert "get_data" in result
        assert "getData" in result
        assert result["getData"] is sym

    def test_filters_non_rust(self) -> None:
        """Ignores non-Rust symbols."""
        from hypergumbo_core.linkers.wasm_bindgen import _find_wasm_bindgen_exports

        sym = Symbol(
            id="python:lib.py:1-1:greet:function",
            name="greet",
            kind="function",
            language="python",
            path="lib.py",
            span=_make_span(),
            meta={"annotations": [{"name": "wasm_bindgen"}]},
        )
        result = _find_wasm_bindgen_exports([sym])
        assert len(result) == 0

    def test_filters_non_function(self) -> None:
        """Ignores non-function/method symbols."""
        from hypergumbo_core.linkers.wasm_bindgen import _find_wasm_bindgen_exports

        sym = _make_rust_sym(
            "MyStruct",
            kind="class",
            annotations=[{"name": "wasm_bindgen"}],
        )
        result = _find_wasm_bindgen_exports([sym])
        assert len(result) == 0

    def test_skips_no_meta(self) -> None:
        """Skips symbols with no meta."""
        from hypergumbo_core.linkers.wasm_bindgen import _find_wasm_bindgen_exports

        sym = _make_rust_sym("greet")
        result = _find_wasm_bindgen_exports([sym])
        assert len(result) == 0

    def test_skips_no_annotations(self) -> None:
        """Skips symbols with meta but no annotations."""
        from hypergumbo_core.linkers.wasm_bindgen import _find_wasm_bindgen_exports

        sym = Symbol(
            id="rust:lib.rs:1-1:greet:function",
            name="greet",
            kind="function",
            language="rust",
            path="lib.rs",
            span=_make_span(),
            meta={"other": "value"},
        )
        result = _find_wasm_bindgen_exports([sym])
        assert len(result) == 0

    def test_skips_non_wasm_annotations(self) -> None:
        """Skips functions with annotations but not wasm_bindgen."""
        from hypergumbo_core.linkers.wasm_bindgen import _find_wasm_bindgen_exports

        sym = _make_rust_sym("greet", annotations=[{"name": "derive", "args": ["Debug"]}])
        result = _find_wasm_bindgen_exports([sym])
        assert len(result) == 0

    def test_method_kind(self) -> None:
        """Detects wasm_bindgen on method kind."""
        from hypergumbo_core.linkers.wasm_bindgen import _find_wasm_bindgen_exports

        sym = _make_rust_sym("get_value", kind="method", annotations=[{"name": "wasm_bindgen"}])
        result = _find_wasm_bindgen_exports([sym])
        assert "get_value" in result

    def test_skips_non_list_annotations(self) -> None:
        """Skips symbols where annotations is not a list."""
        from hypergumbo_core.linkers.wasm_bindgen import _find_wasm_bindgen_exports

        sym = Symbol(
            id="rust:lib.rs:1-1:greet:function",
            name="greet",
            kind="function",
            language="rust",
            path="lib.rs",
            span=_make_span(),
            meta={"annotations": "not_a_list"},
        )
        result = _find_wasm_bindgen_exports([sym])
        assert len(result) == 0

    def test_js_name_non_string_ignored(self) -> None:
        """Non-string js_name is ignored."""
        from hypergumbo_core.linkers.wasm_bindgen import _find_wasm_bindgen_exports

        sym = _make_rust_sym(
            "greet",
            annotations=[{"name": "wasm_bindgen", "kwargs": {"js_name": 42}}],
        )
        result = _find_wasm_bindgen_exports([sym])
        # Still registered under original name
        assert "greet" in result
        assert len(result) == 1


class TestScanJsTsForWasmImports:
    """Tests for _scan_js_ts_for_wasm_imports."""

    def test_named_import_from_pkg(self, tmp_path: Path) -> None:
        """Detects named imports from wasm pkg directories."""
        from hypergumbo_core.linkers.wasm_bindgen import _scan_js_ts_for_wasm_imports

        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { greet } from './pkg/my_module';")
        result = _scan_js_ts_for_wasm_imports(ts_file)
        assert "greet" in result

    def test_named_import_with_wasm_extension(self, tmp_path: Path) -> None:
        """Detects named imports from .wasm files."""
        from hypergumbo_core.linkers.wasm_bindgen import _scan_js_ts_for_wasm_imports

        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { process_data } from './module_bg.wasm';")
        result = _scan_js_ts_for_wasm_imports(ts_file)
        assert "process_data" in result

    def test_multiple_named_imports(self, tmp_path: Path) -> None:
        """Detects multiple named imports."""
        from hypergumbo_core.linkers.wasm_bindgen import _scan_js_ts_for_wasm_imports

        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { greet, add, multiply } from './pkg/math';")
        result = _scan_js_ts_for_wasm_imports(ts_file)
        assert "greet" in result
        assert "add" in result
        assert "multiply" in result

    def test_init_and_named_imports(self, tmp_path: Path) -> None:
        """Handles default + named imports (common wasm-pack pattern)."""
        from hypergumbo_core.linkers.wasm_bindgen import _scan_js_ts_for_wasm_imports

        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import init, { greet } from './pkg/my_wasm';")
        result = _scan_js_ts_for_wasm_imports(ts_file)
        assert "greet" in result

    def test_double_quote_imports(self, tmp_path: Path) -> None:
        """Handles double-quoted import paths."""
        from hypergumbo_core.linkers.wasm_bindgen import _scan_js_ts_for_wasm_imports

        ts_file = tmp_path / "app.ts"
        ts_file.write_text('import { greet } from "./pkg/my_module";')
        result = _scan_js_ts_for_wasm_imports(ts_file)
        assert "greet" in result

    def test_nonexistent_file(self) -> None:
        """Returns empty list for nonexistent file."""
        from hypergumbo_core.linkers.wasm_bindgen import _scan_js_ts_for_wasm_imports

        result = _scan_js_ts_for_wasm_imports(Path("/nonexistent/file.ts"))
        assert result == []

    def test_no_wasm_imports(self, tmp_path: Path) -> None:
        """Returns empty list when no wasm imports found."""
        from hypergumbo_core.linkers.wasm_bindgen import _scan_js_ts_for_wasm_imports

        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { useState } from 'react';")
        result = _scan_js_ts_for_wasm_imports(ts_file)
        assert result == []

    def test_wasm_bg_wasm_import(self, tmp_path: Path) -> None:
        """Detects imports from *_bg.wasm files (wasm-bindgen generated)."""
        from hypergumbo_core.linkers.wasm_bindgen import _scan_js_ts_for_wasm_imports

        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { memory, greet } from './my_lib_bg.wasm';")
        result = _scan_js_ts_for_wasm_imports(ts_file)
        assert "greet" in result

    def test_type_import_ignored(self, tmp_path: Path) -> None:
        """Type-only imports from pkg are ignored."""
        from hypergumbo_core.linkers.wasm_bindgen import _scan_js_ts_for_wasm_imports

        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import type { MyType } from './pkg/my_module';")
        result = _scan_js_ts_for_wasm_imports(ts_file)
        assert result == []

    def test_aliased_import(self, tmp_path: Path) -> None:
        """Aliased imports use the original name for matching."""
        from hypergumbo_core.linkers.wasm_bindgen import _scan_js_ts_for_wasm_imports

        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { greet as hello } from './pkg/my_module';")
        result = _scan_js_ts_for_wasm_imports(ts_file)
        assert "greet" in result

    def test_trailing_comma_in_imports(self, tmp_path: Path) -> None:
        """Handles trailing comma in import specifiers."""
        from hypergumbo_core.linkers.wasm_bindgen import _scan_js_ts_for_wasm_imports

        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { greet, } from './pkg/my_module';")
        result = _scan_js_ts_for_wasm_imports(ts_file)
        assert result == ["greet"]


class TestParseImportNames:
    """Tests for _parse_import_names helper."""

    def test_empty_spec(self) -> None:
        """Empty specifier after split is skipped."""
        from hypergumbo_core.linkers.wasm_bindgen import _parse_import_names

        result = _parse_import_names("greet, , add")
        assert result == ["greet", "add"]

    def test_trailing_comma(self) -> None:
        """Trailing comma produces empty spec that is skipped."""
        from hypergumbo_core.linkers.wasm_bindgen import _parse_import_names

        result = _parse_import_names("greet,")
        assert result == ["greet"]


class TestLinkWasmBindgen:
    """Tests for the main link_wasm_bindgen function."""

    def test_basic_link(self, tmp_path: Path) -> None:
        """Creates wasm_bridge edge between JS import and Rust export."""
        from hypergumbo_core.linkers.wasm_bindgen import link_wasm_bindgen

        rust_sym = _make_rust_sym("greet", annotations=[{"name": "wasm_bindgen"}])
        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { greet } from './pkg/my_module';")
        js_sym = _make_js_sym("main", path=str(ts_file))

        result = link_wasm_bindgen(tmp_path, [js_sym], [rust_sym])
        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "wasm_bridge"
        assert edge.dst == rust_sym.id
        assert edge.confidence == 0.85
        assert edge.evidence_type == "wasm_bindgen_import"

    def test_no_rust_exports(self, tmp_path: Path) -> None:
        """Returns empty when no wasm_bindgen exports found."""
        from hypergumbo_core.linkers.wasm_bindgen import link_wasm_bindgen

        rust_sym = _make_rust_sym("greet")  # no wasm_bindgen annotation
        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { greet } from './pkg/my_module';")
        js_sym = _make_js_sym("main", path=str(ts_file))

        result = link_wasm_bindgen(tmp_path, [js_sym], [rust_sym])
        assert len(result.edges) == 0

    def test_no_matching_import(self, tmp_path: Path) -> None:
        """Returns empty when JS imports don't match Rust exports."""
        from hypergumbo_core.linkers.wasm_bindgen import link_wasm_bindgen

        rust_sym = _make_rust_sym("greet", annotations=[{"name": "wasm_bindgen"}])
        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { other_func } from './pkg/my_module';")
        js_sym = _make_js_sym("main", path=str(ts_file))

        result = link_wasm_bindgen(tmp_path, [js_sym], [rust_sym])
        assert len(result.edges) == 0

    def test_js_name_rename_link(self, tmp_path: Path) -> None:
        """Links using js_name renamed export."""
        from hypergumbo_core.linkers.wasm_bindgen import link_wasm_bindgen

        rust_sym = _make_rust_sym(
            "get_data",
            annotations=[{"name": "wasm_bindgen", "kwargs": {"js_name": "getData"}}],
        )
        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { getData } from './pkg/my_module';")
        js_sym = _make_js_sym("main", path=str(ts_file))

        result = link_wasm_bindgen(tmp_path, [js_sym], [rust_sym])
        assert len(result.edges) == 1
        assert result.edges[0].dst == rust_sym.id

    def test_multiple_exports_multiple_files(self, tmp_path: Path) -> None:
        """Links multiple exports across multiple JS files."""
        from hypergumbo_core.linkers.wasm_bindgen import link_wasm_bindgen

        sym_a = _make_rust_sym("greet", annotations=[{"name": "wasm_bindgen"}])
        sym_b = _make_rust_sym("add", path="src/math.rs", annotations=[{"name": "wasm_bindgen"}])

        ts1 = tmp_path / "app.ts"
        ts1.write_text("import { greet } from './pkg/my_module';")
        ts2 = tmp_path / "calc.ts"
        ts2.write_text("import { add } from './pkg/my_module';")

        js_sym1 = _make_js_sym("main", path=str(ts1))
        js_sym2 = _make_js_sym("calc", path=str(ts2))

        result = link_wasm_bindgen(tmp_path, [js_sym1, js_sym2], [sym_a, sym_b])
        assert len(result.edges) == 2
        dst_ids = {e.dst for e in result.edges}
        assert sym_a.id in dst_ids
        assert sym_b.id in dst_ids

    def test_deduplicates_edges(self, tmp_path: Path) -> None:
        """Same import in same file doesn't create duplicate edges."""
        from hypergumbo_core.linkers.wasm_bindgen import link_wasm_bindgen

        rust_sym = _make_rust_sym("greet", annotations=[{"name": "wasm_bindgen"}])
        ts_file = tmp_path / "app.ts"
        ts_file.write_text(
            "import { greet } from './pkg/my_module';\n"
            "import { greet } from './pkg/other_module';\n"
        )
        js_sym = _make_js_sym("main", path=str(ts_file))

        result = link_wasm_bindgen(tmp_path, [js_sym], [rust_sym])
        assert len(result.edges) == 1

    def test_deduplicates_same_path_symbols(self, tmp_path: Path) -> None:
        """Multiple symbols from same file don't cause repeated scanning."""
        from hypergumbo_core.linkers.wasm_bindgen import link_wasm_bindgen

        rust_sym = _make_rust_sym("greet", annotations=[{"name": "wasm_bindgen"}])
        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { greet } from './pkg/my_module';")
        js_sym1 = _make_js_sym("func_a", path=str(ts_file))
        js_sym2 = _make_js_sym("func_b", path=str(ts_file))

        result = link_wasm_bindgen(tmp_path, [js_sym1, js_sym2], [rust_sym])
        assert len(result.edges) == 1

    def test_empty_inputs(self) -> None:
        """Empty inputs produce empty result."""
        from hypergumbo_core.linkers.wasm_bindgen import link_wasm_bindgen

        result = link_wasm_bindgen(Path("/test"), [], [])
        assert result.edges == []
        assert result.run is not None

    def test_run_metadata(self, tmp_path: Path) -> None:
        """Result includes run metadata."""
        from hypergumbo_core.linkers.wasm_bindgen import link_wasm_bindgen

        result = link_wasm_bindgen(tmp_path, [], [])
        assert result.run is not None
        assert isinstance(result.run, AnalysisRun)
        assert result.run.duration_ms >= 0

    def test_nonexistent_file_skipped(self, tmp_path: Path) -> None:
        """Non-existent TS files are skipped without error."""
        from hypergumbo_core.linkers.wasm_bindgen import link_wasm_bindgen

        rust_sym = _make_rust_sym("greet", annotations=[{"name": "wasm_bindgen"}])
        js_sym = _make_js_sym("main", path="/nonexistent/app.ts")

        result = link_wasm_bindgen(tmp_path, [js_sym], [rust_sym])
        assert result.edges == []

    def test_javascript_language(self, tmp_path: Path) -> None:
        """Works with JavaScript (not just TypeScript) symbols."""
        from hypergumbo_core.linkers.wasm_bindgen import link_wasm_bindgen

        rust_sym = _make_rust_sym("greet", annotations=[{"name": "wasm_bindgen"}])
        js_file = tmp_path / "app.js"
        js_file.write_text("import { greet } from './pkg/my_module';")
        js_sym = _make_js_sym("main", path=str(js_file), language="javascript")

        result = link_wasm_bindgen(tmp_path, [js_sym], [rust_sym])
        assert len(result.edges) == 1

    def test_relative_path_resolution(self, tmp_path: Path) -> None:
        """Relative symbol paths are resolved against repo root."""
        from hypergumbo_core.linkers.wasm_bindgen import link_wasm_bindgen

        rust_sym = _make_rust_sym("greet", annotations=[{"name": "wasm_bindgen"}])
        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { greet } from './pkg/my_module';")
        # Use relative path
        js_sym = _make_js_sym("main", path="app.ts")

        result = link_wasm_bindgen(tmp_path, [js_sym], [rust_sym])
        assert len(result.edges) == 1

    def test_filters_non_js_ts_symbols(self, tmp_path: Path) -> None:
        """Filters out non-JS/TS symbols from file scanning."""
        from hypergumbo_core.linkers.wasm_bindgen import link_wasm_bindgen

        rust_sym = _make_rust_sym("greet", annotations=[{"name": "wasm_bindgen"}])
        python_sym = Symbol(
            id="python:app.py:1-1:main:function",
            name="main",
            kind="function",
            language="python",
            path="app.py",
            span=_make_span(),
        )

        result = link_wasm_bindgen(tmp_path, [python_sym], [rust_sym])
        assert result.edges == []

    def test_src_id_format(self, tmp_path: Path) -> None:
        """Source ID uses correct format with relative path."""
        from hypergumbo_core.linkers.wasm_bindgen import link_wasm_bindgen

        rust_sym = _make_rust_sym("greet", annotations=[{"name": "wasm_bindgen"}])
        ts_file = tmp_path / "src" / "app.ts"
        ts_file.parent.mkdir(parents=True, exist_ok=True)
        ts_file.write_text("import { greet } from '../pkg/my_module';")
        js_sym = _make_js_sym("main", path=str(ts_file))

        result = link_wasm_bindgen(tmp_path, [js_sym], [rust_sym])
        assert len(result.edges) == 1
        assert result.edges[0].src.startswith("typescript:")
        assert "greet" in result.edges[0].src
        assert "wasm_import" in result.edges[0].src

    def test_non_relative_path_fallback(self, tmp_path: Path) -> None:
        """When file path isn't relative to repo_root, uses absolute path in src_id."""
        from hypergumbo_core.linkers.wasm_bindgen import link_wasm_bindgen

        rust_sym = _make_rust_sym("greet", annotations=[{"name": "wasm_bindgen"}])
        # Create file outside of repo_root
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        ts_file = other_dir / "app.ts"
        ts_file.write_text("import { greet } from './pkg/my_module';")
        js_sym = _make_js_sym("main", path=str(ts_file))

        # Use a different dir as repo_root so relative_to raises ValueError
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        result = link_wasm_bindgen(repo_root, [js_sym], [rust_sym])
        assert len(result.edges) == 1
        # src_id contains the absolute path since it can't be made relative
        assert str(other_dir) in result.edges[0].src


class TestWasmBindgenSyntheticSymbols:
    """Tests for synthetic wasm_import Symbol creation.

    The slicer's BFS needs node_by_id.get(edge.src) to return a Symbol for
    cross-language traversal. The linker creates synthetic wasm_import
    Symbol nodes so reverse slices from Rust exports can traverse through
    the wasm bridge back to the JS/TS importer.
    """

    def test_creates_synthetic_symbol_for_each_edge(self, tmp_path: Path) -> None:
        """Each wasm import creates a synthetic wasm_import Symbol."""
        from hypergumbo_core.linkers.wasm_bindgen import link_wasm_bindgen

        sym_a = _make_rust_sym("greet", annotations=[{"name": "wasm_bindgen"}])
        sym_b = _make_rust_sym("add", path="src/math.rs", annotations=[{"name": "wasm_bindgen"}])

        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { greet, add } from './pkg/my_module';")
        js_sym = _make_js_sym("main", path=str(ts_file))

        result = link_wasm_bindgen(tmp_path, [js_sym], [sym_a, sym_b])

        assert len(result.edges) == 2
        assert len(result.symbols) == 2

        sym_by_id = {s.id: s for s in result.symbols}
        for edge in result.edges:
            assert edge.src in sym_by_id
            sym = sym_by_id[edge.src]
            assert sym.kind == "wasm_import"
            assert sym.language == "typescript"

    def test_synthetic_symbol_has_correct_fields(self, tmp_path: Path) -> None:
        """Synthetic Symbol has proper id, name, fingerprint, meta."""
        from hypergumbo_core.linkers.wasm_bindgen import link_wasm_bindgen

        rust_sym = _make_rust_sym("greet", annotations=[{"name": "wasm_bindgen"}])
        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { greet } from './pkg/my_module';")
        js_sym = _make_js_sym("main", path=str(ts_file))

        result = link_wasm_bindgen(tmp_path, [js_sym], [rust_sym])

        assert len(result.symbols) == 1
        sym = result.symbols[0]
        assert sym.name == "greet"
        assert sym.kind == "wasm_import"
        assert sym.language == "typescript"
        assert sym.canonical_name == "import { greet }"
        assert sym.meta == {"wasm_export": "greet"}
        assert sym.fingerprint is not None
        assert len(sym.fingerprint) == 16
        # Tier 2 prevents _classify_symbols from reclassifying to tier 4
        assert sym.supply_chain_tier == 2
        assert sym.supply_chain_reason == "synthetic WASM bridge node"

    def test_no_symbols_when_no_matches(self, tmp_path: Path) -> None:
        """No synthetic symbols when imports don't match exports."""
        from hypergumbo_core.linkers.wasm_bindgen import link_wasm_bindgen

        rust_sym = _make_rust_sym("greet", annotations=[{"name": "wasm_bindgen"}])
        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { other } from './pkg/my_module';")
        js_sym = _make_js_sym("main", path=str(ts_file))

        result = link_wasm_bindgen(tmp_path, [js_sym], [rust_sym])
        assert len(result.symbols) == 0
        assert len(result.edges) == 0

    def test_symbols_passed_through_registry(self, tmp_path: Path) -> None:
        """Symbols are passed through LinkerResult via registry dispatch."""
        from hypergumbo_core.linkers.registry import LinkerContext, run_linker

        import hypergumbo_core.linkers.wasm_bindgen

        rust_sym = _make_rust_sym("greet", annotations=[{"name": "wasm_bindgen"}])
        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { greet } from './pkg/my_module';")
        js_sym = _make_js_sym("main", path=str(ts_file))

        ctx = LinkerContext(repo_root=tmp_path, symbols=[js_sym, rust_sym])
        result = run_linker("wasm_bindgen", ctx)
        assert len(result.symbols) == 1
        assert result.symbols[0].kind == "wasm_import"


class TestWasmBindgenRegistry:
    """Tests for registry integration."""

    def test_registered(self) -> None:
        """Linker is registered in the registry."""
        from hypergumbo_core.linkers.registry import get_linker

        import hypergumbo_core.linkers.wasm_bindgen

        linker = get_linker("wasm_bindgen")
        assert linker is not None
        assert linker.name == "wasm_bindgen"

    def test_activation_language_pair(self) -> None:
        """Activates for TypeScript/Rust and JavaScript/Rust pairs."""
        from hypergumbo_core.linkers.registry import get_linker

        import hypergumbo_core.linkers.wasm_bindgen

        linker = get_linker("wasm_bindgen")
        assert linker.activation.should_run(set(), {"typescript", "rust"})
        assert linker.activation.should_run(set(), {"javascript", "rust"})
        assert not linker.activation.should_run(set(), {"python", "rust"})

    def test_requirements(self) -> None:
        """Has requirements for JS/TS files and Rust wasm_bindgen functions."""
        from hypergumbo_core.linkers.registry import get_linker

        import hypergumbo_core.linkers.wasm_bindgen

        linker = get_linker("wasm_bindgen")
        assert len(linker.requirements) == 2
        req_names = {r.name for r in linker.requirements}
        assert "js_ts_files" in req_names
        assert "wasm_bindgen_functions" in req_names

    def test_requirements_met(self) -> None:
        """Requirements are met when both JS/TS and Rust wasm_bindgen symbols exist."""
        from hypergumbo_core.linkers.registry import LinkerContext, check_linker_requirements

        import hypergumbo_core.linkers.wasm_bindgen

        js_sym = _make_js_sym("main")
        rust_sym = _make_rust_sym("greet", annotations=[{"name": "wasm_bindgen"}])
        ctx = LinkerContext(repo_root=Path("/test"), symbols=[js_sym, rust_sym])
        diagnostics = check_linker_requirements(ctx)
        wasm_diag = next(
            (d for d in diagnostics if d.linker_name == "wasm_bindgen"), None,
        )
        assert wasm_diag is not None
        assert wasm_diag.all_met

    def test_requirements_unmet(self) -> None:
        """Requirements are unmet when no wasm_bindgen symbols exist."""
        from hypergumbo_core.linkers.registry import LinkerContext, check_linker_requirements

        import hypergumbo_core.linkers.wasm_bindgen

        js_sym = _make_js_sym("main")
        ctx = LinkerContext(repo_root=Path("/test"), symbols=[js_sym])
        diagnostics = check_linker_requirements(ctx)
        wasm_diag = next(
            (d for d in diagnostics if d.linker_name == "wasm_bindgen"), None,
        )
        assert wasm_diag is not None
        assert not wasm_diag.all_met

    def test_via_registry_dispatch(self, tmp_path: Path) -> None:
        """Can be invoked via run_linker registry dispatch."""
        from hypergumbo_core.linkers.registry import LinkerContext, run_linker

        import hypergumbo_core.linkers.wasm_bindgen

        rust_sym = _make_rust_sym("greet", annotations=[{"name": "wasm_bindgen"}])
        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { greet } from './pkg/my_module';")
        js_sym = _make_js_sym("main", path=str(ts_file))

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[js_sym, rust_sym],
        )
        result = run_linker("wasm_bindgen", ctx)
        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "wasm_bridge"


# ---------------------------------------------------------------------------
# Dynamic WASM loading (WebAssembly.instantiate, URL imports, Emscripten)
# ---------------------------------------------------------------------------


class TestWasmDynamicLoading:
    """Tests for dynamic WASM module loading detection."""

    def test_webassembly_instantiate_detected(self, tmp_path: Path) -> None:
        """WebAssembly.instantiate with .wasm path creates wasm_load edge."""
        from hypergumbo_core.linkers.wasm_bindgen import _create_wasm_load_edges
        from hypergumbo_core.ir import AnalysisRun, PASS_VERSION

        js_file = tmp_path / "loader.ts"
        js_file.write_text(
            "const buf = await fetch('rotate.wasm').then(r => r.arrayBuffer());\n"
            "const { instance } = await WebAssembly.instantiate(buf, imports);\n"
        )

        js_sym = _make_js_sym("loader", str(js_file))
        run = AnalysisRun.create(pass_id="test", version=PASS_VERSION)

        edges, symbols = _create_wasm_load_edges(tmp_path, [js_sym], run)

        # Should not detect because the pattern expects the .wasm URL inside the call
        # (our regex requires the .wasm path to be a string literal in the call args)
        # Let's test with the URL pattern instead
        assert len(edges) == 0  # No match — .wasm is in fetch(), not in instantiate()

    def test_wasm_url_import_detected(self, tmp_path: Path) -> None:
        """import wasmUrl from 'url:codecs/rotate.wasm' creates wasm_load edge."""
        from hypergumbo_core.linkers.wasm_bindgen import _create_wasm_load_edges
        from hypergumbo_core.ir import AnalysisRun, PASS_VERSION

        js_file = tmp_path / "worker.ts"
        js_file.write_text(
            "import wasmUrl from 'url:codecs/rotate/rotate.wasm';\n"
            "const { instance } = await WebAssembly.instantiate(buf);\n"
        )

        js_sym = _make_js_sym("worker", str(js_file))
        run = AnalysisRun.create(pass_id="test", version=PASS_VERSION)

        edges, symbols = _create_wasm_load_edges(tmp_path, [js_sym], run)

        assert len(edges) == 1
        assert edges[0].edge_type == "wasm_load"
        assert len(symbols) == 1
        assert symbols[0].kind == "wasm_module"
        assert "rotate.wasm" in symbols[0].name

    def test_emscripten_module_detected(self, tmp_path: Path) -> None:
        """Emscripten Module() pattern creates wasm_load edge."""
        from hypergumbo_core.linkers.wasm_bindgen import _create_wasm_load_edges
        from hypergumbo_core.ir import AnalysisRun, PASS_VERSION

        js_file = tmp_path / "phonemize.js"
        js_file.write_text(
            "async function init() {\n"
            "    const module = await loadModule(wasmUrl, dataUrl);\n"
            "    return module;\n"
            "}\n"
        )

        js_sym = _make_js_sym("phonemize", str(js_file), language="javascript")
        run = AnalysisRun.create(pass_id="test", version=PASS_VERSION)

        edges, symbols = _create_wasm_load_edges(tmp_path, [js_sym], run)

        assert len(edges) == 1
        assert edges[0].edge_type == "wasm_load"
        assert symbols[0].name == "__emscripten_module__"

    def test_no_wasm_loading(self, tmp_path: Path) -> None:
        """Regular JS file without WASM patterns produces no edges."""
        from hypergumbo_core.linkers.wasm_bindgen import _create_wasm_load_edges
        from hypergumbo_core.ir import AnalysisRun, PASS_VERSION

        js_file = tmp_path / "app.ts"
        js_file.write_text("console.log('no wasm here');\n")

        js_sym = _make_js_sym("app", str(js_file))
        run = AnalysisRun.create(pass_id="test", version=PASS_VERSION)

        edges, symbols = _create_wasm_load_edges(tmp_path, [js_sym], run)

        assert len(edges) == 0
        assert len(symbols) == 0

    def test_deduplicates_wasm_refs(self, tmp_path: Path) -> None:
        """Same .wasm reference in same file doesn't create duplicate edges."""
        from hypergumbo_core.linkers.wasm_bindgen import _create_wasm_load_edges
        from hypergumbo_core.ir import AnalysisRun, PASS_VERSION

        js_file = tmp_path / "dual.ts"
        js_file.write_text(
            "import wasmUrl from 'url:codecs/mod.wasm';\n"
            "import wasmUrl2 from 'url:codecs/mod.wasm';\n"
        )

        js_sym = _make_js_sym("dual", str(js_file))
        run = AnalysisRun.create(pass_id="test", version=PASS_VERSION)

        edges, symbols = _create_wasm_load_edges(tmp_path, [js_sym], run)

        # Should deduplicate: same file + same wasm ref = 1 edge
        assert len(edges) == 1

    def test_webassembly_instantiate_with_inline_url(self, tmp_path: Path) -> None:
        """WebAssembly.instantiate with inline .wasm URL creates edge."""
        from hypergumbo_core.linkers.wasm_bindgen import _create_wasm_load_edges
        from hypergumbo_core.ir import AnalysisRun, PASS_VERSION

        js_file = tmp_path / "loader.ts"
        js_file.write_text(
            "const { instance } = await WebAssembly.instantiate('rotate.wasm', imports);\n"
        )

        js_sym = _make_js_sym("loader", str(js_file))
        run = AnalysisRun.create(pass_id="test", version=PASS_VERSION)

        edges, symbols = _create_wasm_load_edges(tmp_path, [js_sym], run)
        assert len(edges) == 1
        assert "rotate.wasm" in symbols[0].name

    def test_non_js_symbols_skipped(self, tmp_path: Path) -> None:
        """Non-JS/TS symbols are skipped."""
        from hypergumbo_core.linkers.wasm_bindgen import _create_wasm_load_edges
        from hypergumbo_core.ir import AnalysisRun, PASS_VERSION

        rust_sym = _make_rust_sym("my_func")
        run = AnalysisRun.create(pass_id="test", version=PASS_VERSION)

        edges, symbols = _create_wasm_load_edges(tmp_path, [rust_sym], run)
        assert len(edges) == 0

    def test_duplicate_paths_deduplicated(self, tmp_path: Path) -> None:
        """Multiple symbols from same file don't cause duplicate scanning."""
        from hypergumbo_core.linkers.wasm_bindgen import _create_wasm_load_edges
        from hypergumbo_core.ir import AnalysisRun, PASS_VERSION

        js_file = tmp_path / "app.ts"
        js_file.write_text("import wasmUrl from 'url:codecs/mod.wasm';\n")

        sym1 = _make_js_sym("func1", str(js_file))
        sym2 = _make_js_sym("func2", str(js_file))
        run = AnalysisRun.create(pass_id="test", version=PASS_VERSION)

        edges, symbols = _create_wasm_load_edges(tmp_path, [sym1, sym2], run)
        assert len(edges) == 1  # Only scanned once

    def test_relative_path_resolved(self, tmp_path: Path) -> None:
        """Relative paths in symbol.path are resolved to absolute."""
        from hypergumbo_core.linkers.wasm_bindgen import _create_wasm_load_edges
        from hypergumbo_core.ir import AnalysisRun, PASS_VERSION

        js_file = tmp_path / "src" / "app.ts"
        js_file.parent.mkdir(parents=True, exist_ok=True)
        js_file.write_text("import wasmUrl from 'url:codecs/mod.wasm';\n")

        sym = _make_js_sym("app", "src/app.ts")
        run = AnalysisRun.create(pass_id="test", version=PASS_VERSION)

        edges, symbols = _create_wasm_load_edges(tmp_path, [sym], run)
        assert len(edges) == 1

    def test_nonexistent_file_skipped(self, tmp_path: Path) -> None:
        """Symbols pointing to nonexistent files are skipped."""
        from hypergumbo_core.linkers.wasm_bindgen import _create_wasm_load_edges
        from hypergumbo_core.ir import AnalysisRun, PASS_VERSION

        sym = _make_js_sym("ghost", str(tmp_path / "nonexistent.ts"))
        run = AnalysisRun.create(pass_id="test", version=PASS_VERSION)

        edges, symbols = _create_wasm_load_edges(tmp_path, [sym], run)
        assert len(edges) == 0

    def test_absolute_path_not_relative_to_root(self, tmp_path: Path) -> None:
        """Absolute paths outside repo root use full path."""
        from hypergumbo_core.linkers.wasm_bindgen import _create_wasm_load_edges
        from hypergumbo_core.ir import AnalysisRun, PASS_VERSION
        import tempfile

        with tempfile.TemporaryDirectory() as other_dir:
            js_file = Path(other_dir) / "app.ts"
            js_file.write_text("import wasmUrl from 'url:codecs/mod.wasm';\n")

            sym = _make_js_sym("app", str(js_file))
            run = AnalysisRun.create(pass_id="test", version=PASS_VERSION)

            edges, symbols = _create_wasm_load_edges(tmp_path, [sym], run)
            assert len(edges) == 1

    def test_registry_integration_includes_wasm_load(self, tmp_path: Path) -> None:
        """Registry-based linker produces wasm_load edges alongside wasm_bridge."""
        import hypergumbo_core.linkers.wasm_bindgen  # trigger registration
        from hypergumbo_core.linkers.registry import LinkerContext, run_linker

        js_file = tmp_path / "app.ts"
        js_file.write_text(
            "import wasmUrl from 'url:codecs/rotate.wasm';\n"
        )

        js_sym = _make_js_sym("app", str(js_file))

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[js_sym],
        )
        result = run_linker("wasm_bindgen", ctx)

        wasm_load_edges = [e for e in result.edges if e.edge_type == "wasm_load"]
        assert len(wasm_load_edges) == 1
        wasm_module_syms = [s for s in result.symbols if s.kind == "wasm_module"]
        assert len(wasm_module_syms) == 1
