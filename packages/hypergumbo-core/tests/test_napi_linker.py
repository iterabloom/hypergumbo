# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Node.js N-API linker (JavaScript-C/C++ native addon interop).

The N-API linker creates napi_bridge edges between JavaScript/TypeScript code
that imports native addons and the C/C++ functions that register those exports
via the N-API or node-addon-api interfaces.

Two N-API mechanisms are detected by scanning C/C++ source files:

1. **N-API (C)**: ``napi_create_function(env, "funcName", ...)`` registers a
   function name that becomes available when JS does ``require('./addon')``.
2. **node-addon-api (C++)**: ``Napi::Function::New(env, FuncName, "funcName")``
   or ``InstanceMethod("funcName", &Class::Method)`` registers exports.

The linker matches these registered names against JavaScript/TypeScript symbols
that call into or import from native addons.
"""
from pathlib import Path

import pytest

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.linkers.registry import LinkerContext


def _make_js_symbol(
    name: str,
    kind: str = "function",
    path: str = "index.js",
    start_line: int = 1,
    end_line: int = 10,
) -> Symbol:
    """Create a test JavaScript symbol."""
    run = AnalysisRun.create(pass_id="test", version="test")
    return Symbol(
        id=f"javascript:{path}:{start_line}-{end_line}:{name}:{kind}",
        name=name,
        kind=kind,
        language="javascript",
        path=path,
        span=Span(start_line=start_line, end_line=end_line, start_col=0, end_col=0),
        origin="js-v1",
        origin_run_id=run.execution_id,
    )


def _make_c_symbol(
    name: str,
    kind: str = "function",
    path: str = "addon.c",
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


def _make_cpp_symbol(
    name: str,
    kind: str = "function",
    path: str = "addon.cc",
    start_line: int = 1,
    end_line: int = 10,
) -> Symbol:
    """Create a test C++ symbol."""
    run = AnalysisRun.create(pass_id="test", version="test")
    return Symbol(
        id=f"cpp:{path}:{start_line}-{end_line}:{name}:{kind}",
        name=name,
        kind=kind,
        language="cpp",
        path=path,
        span=Span(start_line=start_line, end_line=end_line, start_col=0, end_col=0),
        origin="cpp-v1",
        origin_run_id=run.execution_id,
    )


class TestNAPILinkerCAPI:
    """Tests for N-API C interface pattern detection."""

    def test_links_napi_create_function_to_js(self, tmp_path: Path) -> None:
        """C napi_create_function exports linked to JS caller via registered name."""
        from hypergumbo_core.linkers.napi import link_napi

        c_file = tmp_path / "addon.c"
        c_file.write_text(
            '#include <node_api.h>\n'
            'napi_value Add(napi_env env, napi_callback_info info) {\n'
            '    return NULL;\n'
            '}\n'
            'napi_value Init(napi_env env, napi_value exports) {\n'
            '    napi_value fn;\n'
            '    napi_create_function(env, "add", NAPI_AUTO_LENGTH, Add, NULL, &fn);\n'
            '    napi_set_named_property(env, exports, "add", fn);\n'
            '    return exports;\n'
            '}\n'
        )

        c_func = _make_c_symbol("Add", path=str(c_file), start_line=2, end_line=4)
        js_sym = _make_js_symbol("app", kind="module", path="index.js")

        # JS unresolved call to "add"
        call_edge = Edge.create(
            src=js_sym.id,
            dst="javascript:index.js:0-0:add:unresolved",
            edge_type="calls",
            line=3,
            evidence_type="function_call",
            confidence=0.50,
            origin="js-v1",
        )

        result = link_napi(
            repo_root=tmp_path,
            js_symbols=[js_sym],
            c_cpp_symbols=[c_func],
            edges=[call_edge],
        )

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "napi_bridge"
        assert edge.dst == c_func.id
        assert edge.evidence_type == "napi_create_function"

    def test_napi_set_named_property_export(self, tmp_path: Path) -> None:
        """Detects napi_set_named_property as an export registration."""
        from hypergumbo_core.linkers.napi import link_napi

        c_file = tmp_path / "addon.c"
        c_file.write_text(
            '#include <node_api.h>\n'
            'napi_value Multiply(napi_env env, napi_callback_info info) {\n'
            '    return NULL;\n'
            '}\n'
            'napi_value Init(napi_env env, napi_value exports) {\n'
            '    napi_value fn;\n'
            '    napi_create_function(env, "multiply", NAPI_AUTO_LENGTH, Multiply, NULL, &fn);\n'
            '    napi_set_named_property(env, exports, "multiply", fn);\n'
            '    return exports;\n'
            '}\n'
        )

        c_func = _make_c_symbol("Multiply", path=str(c_file), start_line=2, end_line=4)
        js_sym = _make_js_symbol("caller", kind="module", path="app.js")

        call_edge = Edge.create(
            src=js_sym.id,
            dst="javascript:app.js:0-0:multiply:unresolved",
            edge_type="calls",
            line=5,
            evidence_type="function_call",
            confidence=0.50,
            origin="js-v1",
        )

        result = link_napi(
            repo_root=tmp_path,
            js_symbols=[js_sym],
            c_cpp_symbols=[c_func],
            edges=[call_edge],
        )

        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "napi_bridge"


class TestNAPILinkerNodeAddonAPI:
    """Tests for node-addon-api (C++) pattern detection."""

    def test_links_napi_function_new(self, tmp_path: Path) -> None:
        """C++ Napi::Function::New export linked to JS caller."""
        from hypergumbo_core.linkers.napi import link_napi

        cpp_file = tmp_path / "addon.cc"
        cpp_file.write_text(
            '#include <napi.h>\n'
            'Napi::Value ComputeHash(const Napi::CallbackInfo& info) {\n'
            '    return info.Env().Undefined();\n'
            '}\n'
            'Napi::Object Init(Napi::Env env, Napi::Object exports) {\n'
            '    exports.Set("computeHash", Napi::Function::New(env, ComputeHash));\n'
            '    return exports;\n'
            '}\n'
        )

        cpp_func = _make_cpp_symbol(
            "ComputeHash", path=str(cpp_file), start_line=2, end_line=4
        )
        js_sym = _make_js_symbol("app", kind="module", path="index.js")

        call_edge = Edge.create(
            src=js_sym.id,
            dst="javascript:index.js:0-0:computeHash:unresolved",
            edge_type="calls",
            line=3,
            evidence_type="function_call",
            confidence=0.50,
            origin="js-v1",
        )

        result = link_napi(
            repo_root=tmp_path,
            js_symbols=[js_sym],
            c_cpp_symbols=[cpp_func],
            edges=[call_edge],
        )

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "napi_bridge"
        assert edge.dst == cpp_func.id
        assert edge.evidence_type == "napi_addon_api"

    def test_links_instance_method(self, tmp_path: Path) -> None:
        """C++ InstanceMethod("name", &Class::Method) linked to JS caller."""
        from hypergumbo_core.linkers.napi import link_napi

        cpp_file = tmp_path / "addon.cc"
        cpp_file.write_text(
            '#include <napi.h>\n'
            'class MyWorker : public Napi::ObjectWrap<MyWorker> {\n'
            '  Napi::Value Process(const Napi::CallbackInfo& info) {\n'
            '    return info.Env().Undefined();\n'
            '  }\n'
            '  static Napi::Object Init(Napi::Env env, Napi::Object exports) {\n'
            '    Napi::Function func = DefineClass(env, "MyWorker", {\n'
            '      InstanceMethod("process", &MyWorker::Process),\n'
            '    });\n'
            '    exports.Set("MyWorker", func);\n'
            '    return exports;\n'
            '  }\n'
            '};\n'
        )

        cpp_method = _make_cpp_symbol(
            "MyWorker::Process", kind="method", path=str(cpp_file),
            start_line=3, end_line=5,
        )
        js_sym = _make_js_symbol("app", kind="module", path="index.js")

        call_edge = Edge.create(
            src=js_sym.id,
            dst="javascript:index.js:0-0:process:unresolved",
            edge_type="calls",
            line=4,
            evidence_type="method_call",
            confidence=0.50,
            origin="js-v1",
        )

        result = link_napi(
            repo_root=tmp_path,
            js_symbols=[js_sym],
            c_cpp_symbols=[cpp_method],
            edges=[call_edge],
        )

        assert len(result.edges) == 1
        assert result.edges[0].evidence_type == "napi_addon_api"


    def test_links_static_method(self, tmp_path: Path) -> None:
        """C++ StaticMethod("name", &Class::Method) linked to JS caller."""
        from hypergumbo_core.linkers.napi import link_napi

        cpp_file = tmp_path / "addon.cc"
        cpp_file.write_text(
            '#include <napi.h>\n'
            'class MathLib : public Napi::ObjectWrap<MathLib> {\n'
            '  static Napi::Value Add(const Napi::CallbackInfo& info) {\n'
            '    return info.Env().Undefined();\n'
            '  }\n'
            '  static Napi::Object Init(Napi::Env env, Napi::Object exports) {\n'
            '    Napi::Function func = DefineClass(env, "MathLib", {\n'
            '      StaticMethod("add", &MathLib::Add),\n'
            '    });\n'
            '    exports.Set("MathLib", func);\n'
            '    return exports;\n'
            '  }\n'
            '};\n'
        )

        cpp_method = _make_cpp_symbol(
            "MathLib::Add", kind="method", path=str(cpp_file),
            start_line=3, end_line=5,
        )
        js_sym = _make_js_symbol("app", kind="module", path="index.js")

        call_edge = Edge.create(
            src=js_sym.id,
            dst="javascript:index.js:0-0:add:unresolved",
            edge_type="calls",
            line=4,
            evidence_type="method_call",
            confidence=0.50,
            origin="js-v1",
        )

        result = link_napi(
            repo_root=tmp_path,
            js_symbols=[js_sym],
            c_cpp_symbols=[cpp_method],
            edges=[call_edge],
        )

        assert len(result.edges) == 1
        assert result.edges[0].evidence_type == "napi_addon_api"


class TestNAPILinkerEdgeCases:
    """Edge case tests for N-API linker."""

    def test_empty_inputs(self, tmp_path: Path) -> None:
        """Handles empty inputs gracefully."""
        from hypergumbo_core.linkers.napi import link_napi

        result = link_napi(
            repo_root=tmp_path,
            js_symbols=[],
            c_cpp_symbols=[],
            edges=[],
        )

        assert result.edges == []
        assert result.run is not None

    def test_result_includes_run_metadata(self, tmp_path: Path) -> None:
        """Result includes analysis run metadata."""
        from hypergumbo_core.linkers.napi import link_napi

        result = link_napi(
            repo_root=tmp_path,
            js_symbols=[],
            c_cpp_symbols=[],
            edges=[],
        )

        assert result.run is not None
        assert result.run.pass_id == "napi-linker-v1"

    def test_no_link_when_c_function_not_in_symbols(self, tmp_path: Path) -> None:
        """No edge when N-API exports a name but no matching C symbol exists."""
        from hypergumbo_core.linkers.napi import link_napi

        c_file = tmp_path / "addon.c"
        c_file.write_text(
            '#include <node_api.h>\n'
            'napi_value Init(napi_env env, napi_value exports) {\n'
            '    napi_value fn;\n'
            '    napi_create_function(env, "missing", NAPI_AUTO_LENGTH, Missing, NULL, &fn);\n'
            '    return exports;\n'
            '}\n'
        )

        js_sym = _make_js_symbol("app", kind="module", path="index.js")
        call_edge = Edge.create(
            src=js_sym.id,
            dst="javascript:index.js:0-0:missing:unresolved",
            edge_type="calls",
            line=3,
            evidence_type="function_call",
            confidence=0.50,
            origin="js-v1",
        )

        result = link_napi(
            repo_root=tmp_path,
            js_symbols=[js_sym],
            c_cpp_symbols=[],
            edges=[call_edge],
        )

        assert len(result.edges) == 0

    def test_ignores_resolved_edges(self, tmp_path: Path) -> None:
        """Only processes unresolved edges."""
        from hypergumbo_core.linkers.napi import link_napi

        c_file = tmp_path / "addon.c"
        c_file.write_text(
            '#include <node_api.h>\n'
            'napi_value Add(napi_env env, napi_callback_info info) {\n'
            '    return NULL;\n'
            '}\n'
            'napi_value Init(napi_env env, napi_value exports) {\n'
            '    napi_create_function(env, "add", NAPI_AUTO_LENGTH, Add, NULL, &fn);\n'
            '    return exports;\n'
            '}\n'
        )

        c_func = _make_c_symbol("Add", path=str(c_file))
        js_sym = _make_js_symbol("app", kind="module", path="index.js")

        # Already-resolved edge
        resolved_edge = Edge.create(
            src=js_sym.id,
            dst=c_func.id,
            edge_type="calls",
            line=3,
            evidence_type="function_call",
            confidence=0.85,
            origin="js-v1",
        )

        result = link_napi(
            repo_root=tmp_path,
            js_symbols=[js_sym],
            c_cpp_symbols=[c_func],
            edges=[resolved_edge],
        )

        assert len(result.edges) == 0

    def test_deduplicates_edges(self, tmp_path: Path) -> None:
        """Same export name called twice creates only one edge."""
        from hypergumbo_core.linkers.napi import link_napi

        c_file = tmp_path / "addon.c"
        c_file.write_text(
            '#include <node_api.h>\n'
            'napi_value Add(napi_env env, napi_callback_info info) { return NULL; }\n'
            'napi_value Init(napi_env env, napi_value exports) {\n'
            '    napi_create_function(env, "add", NAPI_AUTO_LENGTH, Add, NULL, &fn);\n'
            '    return exports;\n'
            '}\n'
        )

        c_func = _make_c_symbol("Add", path=str(c_file))
        js_sym = _make_js_symbol("app", kind="module", path="index.js")

        edge1 = Edge.create(
            src=js_sym.id,
            dst="javascript:index.js:0-0:add:unresolved",
            edge_type="calls",
            line=3,
            evidence_type="function_call",
            confidence=0.50,
            origin="js-v1",
        )
        edge2 = Edge.create(
            src=js_sym.id,
            dst="javascript:index.js:0-0:add:unresolved",
            edge_type="calls",
            line=7,
            evidence_type="function_call",
            confidence=0.50,
            origin="js-v1",
        )

        result = link_napi(
            repo_root=tmp_path,
            js_symbols=[js_sym],
            c_cpp_symbols=[c_func],
            edges=[edge1, edge2],
        )

        assert len(result.edges) == 1

    def test_short_dst_ignored(self, tmp_path: Path) -> None:
        """Unresolved edges with too few colon-separated parts are ignored."""
        from hypergumbo_core.linkers.napi import link_napi

        c_file = tmp_path / "addon.c"
        c_file.write_text(
            '#include <node_api.h>\n'
            'napi_value Add(napi_env env, napi_callback_info info) { return NULL; }\n'
            'napi_value Init(napi_env env, napi_value exports) {\n'
            '    napi_create_function(env, "add", NAPI_AUTO_LENGTH, Add, NULL, &fn);\n'
            '    return exports;\n'
            '}\n'
        )

        c_func = _make_c_symbol("Add", path=str(c_file))
        js_sym = _make_js_symbol("app", kind="module", path="index.js")

        bad_edge = Edge.create(
            src=js_sym.id,
            dst="x:y:unresolved",
            edge_type="calls",
            line=3,
            evidence_type="function_call",
            confidence=0.50,
            origin="js-v1",
        )

        result = link_napi(
            repo_root=tmp_path,
            js_symbols=[js_sym],
            c_cpp_symbols=[c_func],
            edges=[bad_edge],
        )

        assert len(result.edges) == 0

    def test_nonexistent_c_file_skipped(self, tmp_path: Path) -> None:
        """C/C++ files that don't exist on disk are silently skipped."""
        from hypergumbo_core.linkers.napi import link_napi

        c_func = _make_c_symbol("Add", path=str(tmp_path / "nonexistent.c"))
        js_sym = _make_js_symbol("app", kind="module", path="index.js")

        call_edge = Edge.create(
            src=js_sym.id,
            dst="javascript:index.js:0-0:add:unresolved",
            edge_type="calls",
            line=3,
            evidence_type="function_call",
            confidence=0.50,
            origin="js-v1",
        )

        result = link_napi(
            repo_root=tmp_path,
            js_symbols=[js_sym],
            c_cpp_symbols=[c_func],
            edges=[call_edge],
        )

        assert len(result.edges) == 0

    def test_non_function_symbols_ignored(self, tmp_path: Path) -> None:
        """C/C++ struct/variable symbols are not matched for N-API linking."""
        from hypergumbo_core.linkers.napi import link_napi

        c_file = tmp_path / "addon.c"
        c_file.write_text(
            '#include <node_api.h>\n'
            'napi_value Init(napi_env env, napi_value exports) {\n'
            '    napi_create_function(env, "data", NAPI_AUTO_LENGTH, data, NULL, &fn);\n'
            '    return exports;\n'
            '}\n'
        )

        c_struct = _make_c_symbol("data", kind="struct", path=str(c_file))
        js_sym = _make_js_symbol("app", kind="module", path="index.js")

        call_edge = Edge.create(
            src=js_sym.id,
            dst="javascript:index.js:0-0:data:unresolved",
            edge_type="calls",
            line=3,
            evidence_type="function_call",
            confidence=0.50,
            origin="js-v1",
        )

        result = link_napi(
            repo_root=tmp_path,
            js_symbols=[js_sym],
            c_cpp_symbols=[c_struct],
            edges=[call_edge],
        )

        assert len(result.edges) == 0

    def test_multiple_exports_in_one_file(self, tmp_path: Path) -> None:
        """Multiple napi_create_function calls create multiple export mappings."""
        from hypergumbo_core.linkers.napi import link_napi

        c_file = tmp_path / "addon.c"
        c_file.write_text(
            '#include <node_api.h>\n'
            'napi_value Add(napi_env env, napi_callback_info info) { return NULL; }\n'
            'napi_value Sub(napi_env env, napi_callback_info info) { return NULL; }\n'
            'napi_value Init(napi_env env, napi_value exports) {\n'
            '    napi_create_function(env, "add", NAPI_AUTO_LENGTH, Add, NULL, &fn1);\n'
            '    napi_create_function(env, "subtract", NAPI_AUTO_LENGTH, Sub, NULL, &fn2);\n'
            '    return exports;\n'
            '}\n'
        )

        c_add = _make_c_symbol("Add", path=str(c_file), start_line=2, end_line=2)
        c_sub = _make_c_symbol("Sub", path=str(c_file), start_line=3, end_line=3)
        js_sym = _make_js_symbol("app", kind="module", path="index.js")

        edge1 = Edge.create(
            src=js_sym.id,
            dst="javascript:index.js:0-0:add:unresolved",
            edge_type="calls",
            line=3,
            evidence_type="function_call",
            confidence=0.50,
            origin="js-v1",
        )
        edge2 = Edge.create(
            src=js_sym.id,
            dst="javascript:index.js:0-0:subtract:unresolved",
            edge_type="calls",
            line=4,
            evidence_type="function_call",
            confidence=0.50,
            origin="js-v1",
        )

        result = link_napi(
            repo_root=tmp_path,
            js_symbols=[js_sym],
            c_cpp_symbols=[c_add, c_sub],
            edges=[edge1, edge2],
        )

        assert len(result.edges) == 2
        dst_ids = {e.dst for e in result.edges}
        assert c_add.id in dst_ids
        assert c_sub.id in dst_ids

    def test_relative_path_resolved_to_repo_root(self, tmp_path: Path) -> None:
        """Relative paths in C/C++ symbols are resolved relative to repo_root."""
        from hypergumbo_core.linkers.napi import link_napi

        c_file = tmp_path / "addon.c"
        c_file.write_text(
            '#include <node_api.h>\n'
            'napi_value Add(napi_env env, napi_callback_info info) { return NULL; }\n'
            'napi_value Init(napi_env env, napi_value exports) {\n'
            '    napi_create_function(env, "add", NAPI_AUTO_LENGTH, Add, NULL, &fn);\n'
            '    return exports;\n'
            '}\n'
        )

        # Symbol with relative path (not absolute)
        c_func = _make_c_symbol("Add", path="addon.c")
        js_sym = _make_js_symbol("app", kind="module", path="index.js")

        call_edge = Edge.create(
            src=js_sym.id,
            dst="javascript:index.js:0-0:add:unresolved",
            edge_type="calls",
            line=3,
            evidence_type="function_call",
            confidence=0.50,
            origin="js-v1",
        )

        result = link_napi(
            repo_root=tmp_path,
            js_symbols=[js_sym],
            c_cpp_symbols=[c_func],
            edges=[call_edge],
        )

        assert len(result.edges) == 1

    def test_confidence_level(self, tmp_path: Path) -> None:
        """N-API bridge edges have appropriate confidence."""
        from hypergumbo_core.linkers.napi import link_napi

        c_file = tmp_path / "addon.c"
        c_file.write_text(
            '#include <node_api.h>\n'
            'napi_value Add(napi_env env, napi_callback_info info) { return NULL; }\n'
            'napi_value Init(napi_env env, napi_value exports) {\n'
            '    napi_create_function(env, "add", NAPI_AUTO_LENGTH, Add, NULL, &fn);\n'
            '    return exports;\n'
            '}\n'
        )

        c_func = _make_c_symbol("Add", path=str(c_file))
        js_sym = _make_js_symbol("app", kind="module", path="index.js")

        call_edge = Edge.create(
            src=js_sym.id,
            dst="javascript:index.js:0-0:add:unresolved",
            edge_type="calls",
            line=3,
            evidence_type="function_call",
            confidence=0.50,
            origin="js-v1",
        )

        result = link_napi(
            repo_root=tmp_path,
            js_symbols=[js_sym],
            c_cpp_symbols=[c_func],
            edges=[call_edge],
        )

        assert len(result.edges) == 1
        assert result.edges[0].confidence >= 0.80

    def test_typescript_symbols_also_linked(self, tmp_path: Path) -> None:
        """TypeScript unresolved calls are also resolved."""
        from hypergumbo_core.linkers.napi import link_napi

        c_file = tmp_path / "addon.c"
        c_file.write_text(
            '#include <node_api.h>\n'
            'napi_value Hash(napi_env env, napi_callback_info info) { return NULL; }\n'
            'napi_value Init(napi_env env, napi_value exports) {\n'
            '    napi_create_function(env, "hash", NAPI_AUTO_LENGTH, Hash, NULL, &fn);\n'
            '    return exports;\n'
            '}\n'
        )

        c_func = _make_c_symbol("Hash", path=str(c_file))
        ts_sym = Symbol(
            id="typescript:app.ts:1-5:app:module",
            name="app",
            kind="module",
            language="typescript",
            path="app.ts",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="ts-v1",
            origin_run_id=AnalysisRun.create(pass_id="test", version="test").execution_id,
        )

        call_edge = Edge.create(
            src=ts_sym.id,
            dst="typescript:app.ts:0-0:hash:unresolved",
            edge_type="calls",
            line=3,
            evidence_type="function_call",
            confidence=0.50,
            origin="ts-v1",
        )

        result = link_napi(
            repo_root=tmp_path,
            js_symbols=[ts_sym],
            c_cpp_symbols=[c_func],
            edges=[call_edge],
        )

        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "napi_bridge"


class TestNAPILinkerRegistry:
    """Tests for N-API linker registry integration."""

    @pytest.fixture(autouse=True)
    def ensure_napi_registered(self) -> None:
        """Ensure N-API linker is registered before each test."""
        import importlib
        import hypergumbo_core.linkers.napi as napi_module
        importlib.reload(napi_module)

    def test_napi_linker_registered(self) -> None:
        """N-API linker is registered in the linker registry."""
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("napi")
        assert linker is not None
        assert linker.name == "napi"

    def test_napi_linker_has_requirements(self) -> None:
        """N-API linker declares its requirements."""
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("napi")
        assert linker is not None
        assert len(linker.requirements) >= 2

        req_names = [r.name for r in linker.requirements]
        assert "js_ts_files" in req_names
        assert "c_cpp_functions" in req_names

    def test_napi_linker_activation(self) -> None:
        """N-API linker activates for js+c, js+cpp, typescript+c, typescript+cpp pairs."""
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("napi")
        assert linker is not None

        assert linker.activation.should_run(set(), {"javascript", "c"})
        assert linker.activation.should_run(set(), {"javascript", "cpp"})
        assert linker.activation.should_run(set(), {"typescript", "c"})
        assert linker.activation.should_run(set(), {"typescript", "cpp"})
        assert not linker.activation.should_run(set(), {"javascript"})
        assert not linker.activation.should_run(set(), {"c"})

    def test_napi_requirements_met(self) -> None:
        """N-API requirements report as met when matching data exists."""
        from hypergumbo_core.linkers.registry import check_linker_requirements

        js_func = _make_js_symbol("app", kind="module", path="index.js")
        c_func = _make_c_symbol("Add", path="addon.c")

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[js_func, c_func],
            edges=[],
        )

        diagnostics = check_linker_requirements(ctx)
        napi_diag = next((d for d in diagnostics if d.linker_name == "napi"), None)
        assert napi_diag is not None
        assert napi_diag.all_met is True

    def test_napi_requirements_unmet_no_native(self) -> None:
        """N-API requirements unmet when no C/C++ functions exist."""
        from hypergumbo_core.linkers.registry import check_linker_requirements

        js_func = _make_js_symbol("app", kind="module", path="index.js")

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[js_func],
            edges=[],
        )

        diagnostics = check_linker_requirements(ctx)
        napi_diag = next((d for d in diagnostics if d.linker_name == "napi"), None)
        assert napi_diag is not None
        assert napi_diag.all_met is False

    def test_napi_linker_via_registry_dispatch(self, tmp_path: Path) -> None:
        """N-API linker works via registry dispatch."""
        from hypergumbo_core.linkers.registry import run_linker

        c_file = tmp_path / "addon.c"
        c_file.write_text(
            '#include <node_api.h>\n'
            'napi_value Add(napi_env env, napi_callback_info info) { return NULL; }\n'
            'napi_value Init(napi_env env, napi_value exports) {\n'
            '    napi_create_function(env, "add", NAPI_AUTO_LENGTH, Add, NULL, &fn);\n'
            '    return exports;\n'
            '}\n'
        )

        c_func = _make_c_symbol("Add", path=str(c_file))
        js_sym = _make_js_symbol("app", kind="module", path="index.js")

        call_edge = Edge.create(
            src=js_sym.id,
            dst="javascript:index.js:0-0:add:unresolved",
            edge_type="calls",
            line=3,
            evidence_type="function_call",
            confidence=0.50,
            origin="js-v1",
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[js_sym, c_func],
            edges=[call_edge],
        )

        result = run_linker("napi", ctx)

        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "napi_bridge"
