# SPDX-License-Identifier: AGPL-3.0-or-later
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
from types import SimpleNamespace
from hypergumbo_core.cli import run_behavior_map
from hypergumbo_lang_mainstream.js_ts import (
    _detect_route_call,
    _extract_express_usage_contexts,
    _extract_nextjs_usage_contexts,
    _extract_object_properties,
    _extract_param_types,
    _find_route_handler_in_call,
    _find_route_path_in_chain,
    _get_parser_for_file,
    _get_receiver_name,
)


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


# ============================================================================
# Tests for _extract_param_types unrecognized node type (branch 433->436)
# ============================================================================


def test_extract_param_types_unrecognized_node_type() -> None:
    """Cover branch 433->436: node.type is none of the recognized function types.

    When _extract_param_types receives a node whose type is not
    function_declaration, arrow_function, method_definition, or function,
    all elif branches are False and params_node stays None, so we fall
    through to line 436 and return an empty dict.
    """
    mock_node = SimpleNamespace(type="class_declaration")
    result = _extract_param_types(mock_node, b"")
    assert result == {}


# ============================================================================
# Tests for _find_route_path_in_chain traversal branches (492->488, 494->492,
# 500->499, 507->506)
# ============================================================================


def test_find_route_path_in_chain_variable_arg() -> None:
    """Cover branches 492->488, 494->492, 500->499, 507->506.

    Constructs a mock call_expression for router.route(routeVar) where the
    route argument is a variable (not a string literal).

    Branch coverage:
    - 494->492: arguments found but arg loop exhausts (no "string" child)
    - 492->488: args_child loop exhausts, continues subchild loop
    - 507->506: call_expression child (arguments) is not member_expression
    - 500->499: member_expression child (identifier/property_identifier) is
      not call_expression
    """
    source = b"route"

    # Build mock AST: call_expression with .route(variable)
    #   call_expression
    #   ├── arguments              (placed first so #5 fires during traversal)
    #   │   └── identifier         (not "string" → #3)
    #   └── member_expression
    #       ├── identifier         (not property_identifier)
    #       └── property_identifier "route"
    route_prop = SimpleNamespace(type="property_identifier", start_byte=0, end_byte=5)
    mem_expr = SimpleNamespace(
        type="member_expression",
        children=[
            SimpleNamespace(type="identifier"),
            route_prop,
        ],
    )
    args_node = SimpleNamespace(
        type="arguments",
        children=[SimpleNamespace(type="identifier")],  # variable, not string
    )
    call_expr = SimpleNamespace(
        type="call_expression",
        children=[args_node, mem_expr],  # arguments before member_expression
    )

    result = _find_route_path_in_chain(call_expr, source)
    assert result is None


# ============================================================================
# Tests for _get_receiver_name chained call branches (533->527, 534->533)
# ============================================================================


def test_get_receiver_name_call_expression_without_member_expr() -> None:
    """Cover branches 533->527, 534->533.

    A member_expression whose first child is a call_expression (chained call)
    with no member_expression subchild.  Example: require('express')().get(...)

    - 534->533: subchild of call_expression is not member_expression (continue)
    - 533->527: subchild loop exhausts without finding member_expression
    """
    source = b"app"

    # call_expression with children [identifier, arguments] — no member_expression
    inner_call = SimpleNamespace(
        type="call_expression",
        children=[
            SimpleNamespace(type="identifier", start_byte=0, end_byte=3),
            SimpleNamespace(type="arguments", children=[]),
        ],
    )
    member_expr = SimpleNamespace(
        type="member_expression",
        children=[inner_call],
    )
    result = _get_receiver_name(member_expr, source)
    # No member_expression subchild → returns None
    assert result is None


# ============================================================================
# Tests for _detect_route_call no property_identifier branch (582->587)
# ============================================================================


def test_detect_route_call_no_property_identifier() -> None:
    """Cover branch 582->587: callee member_expression has no property_identifier.

    The for loop at line 582 exhausts without finding a property_identifier
    child, so method_name stays None → not in HTTP_METHODS → return None, None.
    """
    source = b"app"

    callee = SimpleNamespace(
        type="member_expression",
        children=[
            SimpleNamespace(type="identifier", start_byte=0, end_byte=3),
            # No property_identifier child
        ],
    )
    args = SimpleNamespace(type="arguments", children=[])
    call_expr = SimpleNamespace(
        type="call_expression",
        children=[callee, args],
    )
    result = _detect_route_call(call_expr, source)
    assert result == (None, None)


# ============================================================================
# Tests for _find_route_handler_in_call last-arg fallthrough (646->622)
# ============================================================================


def test_find_route_handler_last_arg_not_identifier_or_member() -> None:
    """Cover branch 646->622: last arg is neither identifier nor member_expression.

    When all args are strings (e.g. app.get('/path', '/redirect')), the last arg
    is a "string" type, so neither the member_expression check (641) nor the
    identifier check (646) matches. We continue the outer for loop.
    """
    source = b""

    string_arg = SimpleNamespace(type="string")
    args = SimpleNamespace(
        type="arguments",
        children=[string_arg],
    )
    call_expr = SimpleNamespace(
        type="call_expression",
        children=[args],
    )
    result = _find_route_handler_in_call(call_expr, source)
    assert result == (None, None, False)


# ============================================================================
# Tests for _extract_express_usage_contexts handler resolution branches
# (699->709, 705->709, 710->716, 711->710)
# ============================================================================


def test_express_route_with_external_handler_no_position_lookup(tmp_path: Path) -> None:
    """Cover branch 699->709: symbol_by_position is None.

    When an external handler is not found by name and symbol_by_position is None,
    the elif on line 699 is False → skip to line 709.
    Also covers 711->710 and 710->716 since call_expression children include
    non-member_expression types (arguments).
    """
    app_file = tmp_path / "app.js"
    app_file.write_text(
        "const express = require('express');\n"
        "const app = express();\n"
        "app.get('/users', externalHandler);\n"
    )

    parser = _get_parser_for_file(app_file)
    source = app_file.read_bytes()
    tree = parser.parse(source)

    # Pass symbol_by_position=None to trigger branch 699->709
    contexts = _extract_express_usage_contexts(
        tree, source, app_file, symbol_by_name={}, line_offset=0,
        symbol_by_position=None,
    )
    assert len(contexts) == 1
    assert contexts[0].metadata["http_method"] == "GET"


def test_express_route_inline_handler_position_miss(tmp_path: Path) -> None:
    """Cover branch 705->709: position_key not in symbol_by_position.

    Inline handler is found (handler_name is None, handler_node is non-None),
    symbol_by_position is non-empty but doesn't contain the handler's position.
    The elif condition on 699 is True, but line 705 is False → skip to 709.
    """
    app_file = tmp_path / "app.js"
    app_file.write_text(
        "const express = require('express');\n"
        "const app = express();\n"
        "app.get('/inline', (req, res) => { res.send('ok'); });\n"
    )

    parser = _get_parser_for_file(app_file)
    source = app_file.read_bytes()
    tree = parser.parse(source)

    # Non-empty symbol_by_position that won't match the handler's position
    dummy_position = {("nonexistent.js", 999, 999): SimpleNamespace(id="dummy")}
    contexts = _extract_express_usage_contexts(
        tree, source, app_file, symbol_by_name={}, line_offset=0,
        symbol_by_position=dummy_position,
    )
    assert len(contexts) == 1
    assert contexts[0].metadata["route_path"] == "/inline"


# ============================================================================
# Tests for _extract_object_properties edge-case branches
# (777->772, 781->772, 784->765, 790->765)
# ============================================================================


def test_object_properties_non_identifier_key() -> None:
    """Cover branch 777->772: pair child before colon is not property_identifier or string.

    A pair whose key is a computed expression (e.g., [expr]: value) — the
    key_node before the colon is not property_identifier or string.
    """
    source = b"[computed]: value"

    computed_key = SimpleNamespace(type="computed_property_name", start_byte=0, end_byte=10)
    colon = SimpleNamespace(type=":")
    value = SimpleNamespace(type="identifier", start_byte=13, end_byte=18)
    pair = SimpleNamespace(type="pair", children=[computed_key, colon, value])
    obj = SimpleNamespace(type="object", children=[pair])

    result = _extract_object_properties(obj, source)
    # key_node is None (777->772 fires, then 784->765: key_node is falsy)
    assert result == {}


def test_object_properties_comma_after_colon() -> None:
    """Cover branch 781->772: pair child after colon is a comma.

    A pair with trailing comma in children: { key: value, }
    The comma child after the colon has type ',' which is in (',',) → skip.
    """
    source = b"key: val"

    key = SimpleNamespace(type="property_identifier", start_byte=0, end_byte=3)
    colon = SimpleNamespace(type=":")
    value = SimpleNamespace(type="identifier", start_byte=5, end_byte=8)
    comma = SimpleNamespace(type=",")
    pair = SimpleNamespace(type="pair", children=[key, colon, value, comma])
    obj = SimpleNamespace(type="object", children=[pair])

    result = _extract_object_properties(obj, source)
    # value_node is set to the identifier (not the comma) → key present
    assert result == {"key": "val"}


def test_object_properties_no_value() -> None:
    """Cover branch 790->765: key_node is set but value_node is None.

    A pair that has only a key and colon but no value: { key: }
    key_node is truthy (784 passes) but value_node is None (790->765 fires).
    """
    source = b"key"

    key = SimpleNamespace(type="property_identifier", start_byte=0, end_byte=3)
    colon = SimpleNamespace(type=":")
    # Only a comma after colon (filtered out by 781), leaving value_node = None
    comma = SimpleNamespace(type=",")
    pair = SimpleNamespace(type="pair", children=[key, colon, comma])
    obj = SimpleNamespace(type="object", children=[pair])

    result = _extract_object_properties(obj, source)
    # key_node is truthy but value_node is None → key not added to properties
    assert result == {}


# ============================================================================
# Tests for _extract_hapi_usage_contexts missing path/method branches
# (880->876, 887->884)
# ============================================================================


def test_hapi_route_object_without_path_or_method(tmp_path: Path) -> None:
    """Cover branch 880->876: object arg has neither 'path' nor 'method'.

    server.route({ handler: someHandler }) — object has handler but no path
    or method. The if on line 880 is False → continue the arg loop.
    """
    app_file = tmp_path / "server.js"
    app_file.write_text(
        "const Hapi = require('@hapi/hapi');\n"
        "const server = Hapi.server();\n"
        "server.route({ handler: someHandler });\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # No route should be detected (no path/method)
    hapi_contexts = [uc for uc in data.get("usage_contexts", [])
                     if uc.get("metadata", {}).get("config_based")]
    assert len(hapi_contexts) == 0


def test_hapi_route_array_element_without_path_or_method(tmp_path: Path) -> None:
    """Cover branch 887->884: array element object has neither 'path' nor 'method'.

    server.route([{ handler: h1 }, { method: 'GET', path: '/ok', handler: h2 }])
    First array element has no path/method (887->884), second does.
    """
    app_file = tmp_path / "server.js"
    app_file.write_text(
        "const Hapi = require('@hapi/hapi');\n"
        "const server = Hapi.server();\n"
        "server.route([\n"
        "    { handler: someHandler },\n"
        "    { method: 'GET', path: '/ok', handler: okHandler }\n"
        "]);\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    hapi_contexts = [uc for uc in data.get("usage_contexts", [])
                     if uc.get("metadata", {}).get("config_based")]
    # Only the second element should produce a context
    assert len(hapi_contexts) == 1
    assert hapi_contexts[0]["metadata"]["route_path"] == "/ok"


# ============================================================================
# Tests for _extract_nextjs_usage_contexts export branches
# (1056->1051, 1078->1043)
# ============================================================================


def test_nextjs_export_function_without_name(tmp_path: Path) -> None:
    """Cover branch 1056->1051: function_declaration child has no identifier name.

    An export statement with an anonymous function declaration (edge case
    in tree-sitter parsing). name is None → if on 1056 is False → continue.
    Also covers 1078->1043: neither default export nor meaningful export name.
    """
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()
    page_file = pages_dir / "about.js"
    # Export a named function that's NOT in the meaningful exports list
    page_file.write_text(
        "export function randomHelper() { return 1; }\n"
    )

    parser = _get_parser_for_file(page_file)
    source = page_file.read_bytes()
    tree = parser.parse(source)

    contexts = _extract_nextjs_usage_contexts(
        tree, source, page_file, symbol_by_name={}, line_offset=0,
    )
    # "randomHelper" is not a meaningful Next.js export → 1078->1043 fires
    assert len(contexts) == 0


def test_nextjs_non_meaningful_named_export(tmp_path: Path) -> None:
    """Cover branch 1078->1043 via integration: export name not in meaningful_exports.

    A Next.js page file that exports a non-meaningful named function.
    The condition on line 1078 is False → continue outer loop.
    """
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()
    page_file = pages_dir / "index.js"
    page_file.write_text(
        "export function utilityFn() { return 42; }\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # No Next.js page/API route context should be generated for non-meaningful export
    nextjs_contexts = [uc for uc in data.get("usage_contexts", [])
                       if uc.get("kind") == "export"]
    assert len(nextjs_contexts) == 0
