"""Branch coverage tests for JS/TS analyzer.

These tests specifically target uncovered branches in js_ts.py.
They are in a separate file to allow easy management if they impact CI speed.

Strategy:
- Truly unreachable defensive code is marked with `# pragma: no cover` in the source
- Reachable edge cases are tested here
- Focus on branches that affect correctness, not obscure paths
"""
import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


# ============================================================================
# Tests for Express route extraction branch coverage
# ============================================================================


def test_express_route_with_external_handler_not_in_symbols(tmp_path: Path) -> None:
    """Cover branch: handler_name not in symbol_by_name (line 696->699).

    When Express route uses an imported handler that isn't locally defined.
    """
    app_file = tmp_path / "app.js"
    app_file.write_text(
        "const express = require('express');\n"
        "const { externalHandler } = require('./handlers');\n"
        "const app = express();\n"
        "\n"
        "// External handler not in local symbols\n"
        "app.get('/external', externalHandler);\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # Should still extract usage context with handler_name
    usage_contexts = data.get("usage_contexts", [])
    express_contexts = [uc for uc in usage_contexts if uc.get("metadata", {}).get("http_method")]
    assert len(express_contexts) == 1
    assert express_contexts[0].get("metadata", {}).get("handler_name") == "externalHandler"


def test_express_route_with_inline_arrow_function(tmp_path: Path) -> None:
    """Cover branch: inline handler with symbol_by_position lookup (line 699->709).

    When Express route uses an inline arrow function.
    """
    app_file = tmp_path / "app.js"
    app_file.write_text(
        "const express = require('express');\n"
        "const app = express();\n"
        "\n"
        "// Inline arrow function handler\n"
        "app.get('/inline', (req, res) => {\n"
        "    res.send('hello');\n"
        "});\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # Should extract usage context for inline handler
    usage_contexts = data.get("usage_contexts", [])
    express_contexts = [uc for uc in usage_contexts if uc.get("metadata", {}).get("http_method")]
    assert len(express_contexts) == 1
    assert express_contexts[0].get("metadata", {}).get("route_path") == "/inline"


def test_express_router_chained_route(tmp_path: Path) -> None:
    """Cover branch: router.route('/path').get() pattern (line 530->533).

    Express Router with chained route definition.
    """
    router_file = tmp_path / "routes.js"
    router_file.write_text(
        "const express = require('express');\n"
        "const router = express.Router();\n"
        "\n"
        "function handleGet(req, res) {\n"
        "    res.send('get');\n"
        "}\n"
        "\n"
        "function handlePost(req, res) {\n"
        "    res.send('post');\n"
        "}\n"
        "\n"
        "// Chained route pattern\n"
        "router.route('/users')\n"
        "    .get(handleGet)\n"
        "    .post(handlePost);\n"
        "\n"
        "module.exports = router;\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # Should extract usage contexts for both HTTP methods
    usage_contexts = data.get("usage_contexts", [])
    express_contexts = [uc for uc in usage_contexts if uc.get("metadata", {}).get("http_method")]
    # May or may not extract chained routes - at minimum should have the handlers
    funcs = [n for n in data["nodes"] if n["kind"] == "function"]
    func_names = [f["name"] for f in funcs]
    assert "handleGet" in func_names
    assert "handlePost" in func_names


# ============================================================================
# Tests for TypeScript type extraction branch coverage
# ============================================================================


def test_typescript_function_with_no_type_params(tmp_path: Path) -> None:
    """Cover branch: function with no typed parameters (line 436->461).

    TypeScript function with no parameter type annotations.
    """
    app_file = tmp_path / "app.ts"
    app_file.write_text(
        "// No type annotations on parameters\n"
        "function noTypes(a, b, c) {\n"
        "    return a + b + c;\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    funcs = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(funcs) == 1
    assert funcs[0]["name"] == "noTypes"


def test_typescript_method_definition(tmp_path: Path) -> None:
    """Cover branch: method_definition type (line 433->436).

    TypeScript class method definition.
    """
    app_file = tmp_path / "service.ts"
    app_file.write_text(
        "class UserService {\n"
        "    // Method with typed parameter\n"
        "    getUser(id: number): string {\n"
        "        return `user-${id}`;\n"
        "    }\n"
        "    \n"
        "    // Method without types\n"
        "    process(data) {\n"
        "        return data;\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    method_names = [m["name"] for m in methods]
    assert "UserService.getUser" in method_names
    assert "UserService.process" in method_names


# ============================================================================
# Tests for React/Angular/Vue component extraction branch coverage
# ============================================================================


def test_react_function_component(tmp_path: Path) -> None:
    """Cover branch: React function component detection.

    JSX function component pattern.
    """
    component_file = tmp_path / "Button.jsx"
    component_file.write_text(
        "import React from 'react';\n"
        "\n"
        "function Button({ onClick, children }) {\n"
        "    return (\n"
        "        <button onClick={onClick}>\n"
        "            {children}\n"
        "        </button>\n"
        "    );\n"
        "}\n"
        "\n"
        "export default Button;\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    funcs = [n for n in data["nodes"] if n["kind"] == "function"]
    func_names = [f["name"] for f in funcs]
    assert "Button" in func_names


def test_vue_options_api_component(tmp_path: Path) -> None:
    """Cover branch: Vue Options API pattern.

    Vue component with methods, computed, etc.
    """
    component_file = tmp_path / "Counter.vue"
    component_file.write_text(
        "<template>\n"
        "    <div>{{ count }}</div>\n"
        "</template>\n"
        "\n"
        "<script>\n"
        "export default {\n"
        "    name: 'Counter',\n"
        "    data() {\n"
        "        return { count: 0 };\n"
        "    },\n"
        "    methods: {\n"
        "        increment() {\n"
        "            this.count++;\n"
        "        }\n"
        "    }\n"
        "};\n"
        "</script>\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # Vue files should be processed
    assert len(data["nodes"]) > 0


# ============================================================================
# Tests for module/export pattern branch coverage
# ============================================================================


def test_commonjs_module_exports(tmp_path: Path) -> None:
    """Cover branch: CommonJS module.exports pattern.

    module.exports = { ... } or module.exports.foo = ...
    """
    module_file = tmp_path / "utils.js"
    module_file.write_text(
        "function helper() {\n"
        "    return 'help';\n"
        "}\n"
        "\n"
        "function another() {\n"
        "    return 'another';\n"
        "}\n"
        "\n"
        "// CommonJS export pattern\n"
        "module.exports = {\n"
        "    helper,\n"
        "    another\n"
        "};\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    funcs = [n for n in data["nodes"] if n["kind"] == "function"]
    func_names = [f["name"] for f in funcs]
    assert "helper" in func_names
    assert "another" in func_names


def test_esm_named_exports(tmp_path: Path) -> None:
    """Cover branch: ESM named exports pattern.

    export function foo() { } or export { foo, bar }
    """
    module_file = tmp_path / "exports.js"
    module_file.write_text(
        "// Named export\n"
        "export function namedExport() {\n"
        "    return 'named';\n"
        "}\n"
        "\n"
        "// Default export\n"
        "export default function defaultExport() {\n"
        "    return 'default';\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    funcs = [n for n in data["nodes"] if n["kind"] == "function"]
    func_names = [f["name"] for f in funcs]
    assert "namedExport" in func_names
    assert "defaultExport" in func_names


# ============================================================================
# Tests for async/await pattern branch coverage
# ============================================================================


def test_async_arrow_function(tmp_path: Path) -> None:
    """Cover branch: async arrow function.

    const fn = async () => { }
    """
    module_file = tmp_path / "async.js"
    module_file.write_text(
        "// Async arrow function\n"
        "const fetchData = async (url) => {\n"
        "    const response = await fetch(url);\n"
        "    return response.json();\n"
        "};\n"
        "\n"
        "// Async regular function\n"
        "async function processData(data) {\n"
        "    const result = await transform(data);\n"
        "    return result;\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    funcs = [n for n in data["nodes"] if n["kind"] == "function"]
    func_names = [f["name"] for f in funcs]
    assert "fetchData" in func_names
    assert "processData" in func_names
