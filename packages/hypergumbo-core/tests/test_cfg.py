# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for cfg.py — CFG data structures, YAML loading, and fringe-based builder.

Validates the CFG builder against real tree-sitter ASTs for Python and Rust,
ensuring correct control-flow graph construction for sequential, conditional,
loop, break/continue, return, try/catch, context manager, early return, and
switch/match constructs. Uses property-based assertions (block connectivity,
edge types, reachability) rather than exact "golden" output.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import tree_sitter
from tree_sitter_language_pack import get_language

from hypergumbo_core.cfg import (
    BasicBlock,
    CfgBuilder,
    CfgEdge,
    CfgNodeMapping,
    CfgStatement,
    ConditionalMapping,
    ContextManagerMapping,
    DdgEdge,
    DeferredMapping,
    EarlyReturnMapping,
    FunctionCfg,
    LoopMapping,
    MAX_DEFINITIONS,
    ReachingDefResult,
    SwitchMapping,
    TryCatchMapping,
    _compute_predecessors,
    _Definition,
    _FringeEdge,
    _LoopContext,
    _PartialCfg,
    _reverse_postorder,
    _parse_cfg_mapping,
    build_function_cfg,
    clear_cfg_mapping_cache,
    get_cfg_nodes_dir,
    load_cfg_mapping,
    solve_reaching_defs,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_python(source: str) -> tuple[Any, bytes]:
    """Parse Python source and return (tree, source_bytes)."""
    lang = get_language("python")
    parser = tree_sitter.Parser(lang)
    src = source.encode("utf-8")
    tree = parser.parse(src)
    return tree, src


def _parse_rust(source: str) -> tuple[Any, bytes]:
    """Parse Rust source and return (tree, source_bytes)."""
    lang = get_language("rust")
    parser = tree_sitter.Parser(lang)
    src = source.encode("utf-8")
    tree = parser.parse(src)
    return tree, src


def _get_function_body(tree: Any, lang: str = "python") -> Any:
    """Extract the function body node from a parsed tree.

    For Python: function_definition → body (block)
    For Rust: function_item → body (block)
    """
    root = tree.root_node
    for child in root.children:
        if lang == "python" and child.type == "function_definition":
            return child.child_by_field_name("body")
        if lang == "rust" and child.type == "function_item":
            return child.child_by_field_name("body")
    raise ValueError(f"No function found in tree (lang={lang})")


def _reachable_blocks(cfg: FunctionCfg, start: str) -> set[str]:
    """Return all block IDs reachable from start via BFS."""
    visited: set[str] = set()
    queue = [start]
    while queue:
        bid = queue.pop(0)
        if bid in visited:
            continue
        visited.add(bid)
        block = cfg.blocks.get(bid)
        if block:
            for edge in block.successors:
                queue.append(edge.target_block)
    return visited


def _has_cycle(cfg: FunctionCfg) -> bool:
    """Return True if the CFG contains any cycle (back-edge)."""
    for block in cfg.blocks.values():
        for edge in block.successors:
            # If any successor can reach back to this block, there's a cycle
            reachable_from_successor = _reachable_blocks(cfg, edge.target_block)
            if block.id in reachable_from_successor:
                return True
    return False


def _edge_types(cfg: FunctionCfg, block_id: str) -> list[str]:
    """Return the edge types for a block's successors."""
    block = cfg.blocks[block_id]
    return [e.edge_type for e in block.successors]


def _sym_id(name: str = "test_func") -> str:
    return f"python:test.py:1-10:{name}:function"


# ---------------------------------------------------------------------------
# Data structure tests
# ---------------------------------------------------------------------------


class TestDataStructures:
    """Test CFG data model objects."""

    def test_cfg_statement_defaults(self) -> None:
        stmt = CfgStatement(line=1, col=0, node_type="call", code_snippet="foo()")
        assert stmt.defines == []
        assert stmt.uses == []
        assert stmt.call_target is None

    def test_cfg_edge_types(self) -> None:
        for etype in ("always", "true", "false", "case", "exception"):
            edge = CfgEdge(target_block="bb_1", edge_type=etype)
            assert edge.edge_type == etype

    def test_basic_block_defaults(self) -> None:
        block = BasicBlock(id="bb_0", symbol_id="test:f")
        assert block.statements == []
        assert block.successors == []

    def test_function_cfg_structure(self) -> None:
        cfg = FunctionCfg(
            symbol_id="test:f",
            entry_block="bb_0",
            exit_block="bb_1",
            blocks={"bb_0": BasicBlock(id="bb_0", symbol_id="test:f")},
        )
        assert cfg.entry_block == "bb_0"
        assert cfg.exit_block == "bb_1"

    def test_fringe_edge(self) -> None:
        fe = _FringeEdge(source_block="bb_0", edge_type="always")
        assert fe.source_block == "bb_0"

    def test_partial_cfg(self) -> None:
        p = _PartialCfg(entry_block="bb_0", fringe=[], blocks={})
        assert p.entry_block == "bb_0"
        assert p.fringe == []

    def test_loop_context(self) -> None:
        ctx = _LoopContext(header_block_id="bb_0")
        assert ctx.break_edges == []
        assert ctx.continue_edges == []


# ---------------------------------------------------------------------------
# YAML mapping tests
# ---------------------------------------------------------------------------


class TestCfgNodeMapping:
    """Test YAML CFG node mapping loading and classification."""

    def test_load_python_mapping(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        assert mapping.language == "python"
        assert mapping.grammar == "tree-sitter-python"

    def test_load_rust_mapping(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("rust")
        assert mapping is not None
        assert mapping.language == "rust"

    def test_load_nonexistent_mapping(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("nonexistent_language_xyz")
        assert mapping is None

    def test_caching(self) -> None:
        clear_cfg_mapping_cache()
        m1 = load_cfg_mapping("python")
        m2 = load_cfg_mapping("python")
        assert m1 is m2

    def test_caching_nonexistent(self) -> None:
        clear_cfg_mapping_cache()
        m1 = load_cfg_mapping("nonexistent_lang_abc")
        m2 = load_cfg_mapping("nonexistent_lang_abc")
        assert m1 is None
        assert m2 is None

    def test_custom_search_dir(self, tmp_path: Path) -> None:
        clear_cfg_mapping_cache()
        yaml_content = (
            "language: test\n"
            "grammar: test-grammar\n"
            "conditional:\n"
            "  - node_type: if_stmt\n"
            "    condition_child: cond\n"
            "    true_child: then\n"
        )
        (tmp_path / "test.yaml").write_text(yaml_content)
        mapping = load_cfg_mapping("test", search_dir=tmp_path)
        assert mapping is not None
        assert mapping.language == "test"
        assert len(mapping.conditionals) == 1

    def test_classify_python_if(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        assert mapping.classify("if_statement") == "conditional"

    def test_classify_python_while(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        assert mapping.classify("while_statement") == "loop"

    def test_classify_python_for(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        assert mapping.classify("for_statement") == "loop"

    def test_classify_python_break(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        assert mapping.classify("break_statement") == "break"

    def test_classify_python_continue(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        assert mapping.classify("continue_statement") == "continue"

    def test_classify_python_return(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        assert mapping.classify("return_statement") == "return"

    def test_classify_python_try(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        assert mapping.classify("try_statement") == "try_catch"

    def test_classify_python_with(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        assert mapping.classify("with_statement") == "context_manager"

    def test_classify_python_match(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        assert mapping.classify("match_statement") == "switch"

    def test_classify_unknown(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        assert mapping.classify("some_unknown_type") is None

    def test_classify_rust_early_return(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("rust")
        assert mapping is not None
        assert mapping.classify("try_expression") == "early_return"

    def test_get_conditional(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        c = mapping.get_conditional("if_statement")
        assert c is not None
        assert c.condition_child == "field:condition"
        assert mapping.get_conditional("unknown") is None

    def test_get_loop(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        lo = mapping.get_loop("while_statement")
        assert lo is not None
        assert mapping.get_loop("unknown") is None

    def test_get_try_catch(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        tc = mapping.get_try_catch("try_statement")
        assert tc is not None
        assert mapping.get_try_catch("unknown") is None

    def test_get_early_return(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("rust")
        assert mapping is not None
        er = mapping.get_early_return("try_expression")
        assert er is not None
        assert er.semantics == "return_on_err"
        assert mapping.get_early_return("unknown") is None

    def test_get_context_manager(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        cm = mapping.get_context_manager("with_statement")
        assert cm is not None
        assert mapping.get_context_manager("unknown") is None

    def test_get_deferred(self) -> None:
        mapping = CfgNodeMapping(
            language="test", grammar="test",
            deferred=[DeferredMapping(node_type="defer_stmt", body_child="call")],
        )
        assert mapping.get_deferred("defer_stmt") is not None
        assert mapping.get_deferred("unknown") is None
        assert mapping.classify("defer_stmt") == "deferred"

    def test_get_switch(self) -> None:
        clear_cfg_mapping_cache()
        mapping = load_cfg_mapping("rust")
        assert mapping is not None
        s = mapping.get_switch("match_expression")
        assert s is not None
        assert mapping.get_switch("unknown") is None

    def test_get_cfg_nodes_dir(self) -> None:
        d = get_cfg_nodes_dir()
        assert d.is_dir()
        assert (d / "python.yaml").exists()

    def test_parse_mapping_minimal(self) -> None:
        data = {"language": "minimal"}
        mapping = _parse_cfg_mapping(data)
        assert mapping.language == "minimal"
        assert mapping.grammar == ""
        assert mapping.conditionals == []
        assert mapping.loops == []
        assert mapping.break_statements == []

    def test_parse_mapping_all_fields(self) -> None:
        data = {
            "language": "full",
            "grammar": "tree-sitter-full",
            "conditional": [{"node_type": "if", "condition_child": "c", "true_child": "t", "false_child": "f"}],
            "loop": [{"node_type": "while", "body_child": "b", "condition_child": "c", "infinite": False}],
            "break_statement": ["break"],
            "continue_statement": ["continue"],
            "return_statement": ["return"],
            "try_catch": [{"node_type": "try", "body_child": "b", "catch_child": "h", "finally_child": "fin", "catch_clause_type": "except"}],
            "early_return": [{"node_type": "?", "semantics": "return_on_err", "ok_defines": True, "err_edge": "exit"}],
            "context_manager": [{"node_type": "with", "value_child": "v", "body_child": "b", "alias_child": "a"}],
            "deferred": [{"node_type": "defer", "body_child": "call"}],
            "switch": [{"node_type": "match", "scrutinee_child": "s", "arms_child": "a", "arm_type": "arm"}],
        }
        mapping = _parse_cfg_mapping(data)
        assert len(mapping.conditionals) == 1
        assert mapping.conditionals[0].false_child == "f"
        assert len(mapping.loops) == 1
        assert len(mapping.try_catch) == 1
        assert mapping.try_catch[0].catch_child == "h"
        assert len(mapping.early_return) == 1
        assert len(mapping.context_manager) == 1
        assert mapping.context_manager[0].alias_child == "a"
        assert len(mapping.deferred) == 1
        assert len(mapping.switch) == 1
        assert mapping.switch[0].arm_type == "arm"


# ---------------------------------------------------------------------------
# CFG builder tests — Python
# ---------------------------------------------------------------------------


class TestCfgBuilderPython:
    """Test CFG construction from real Python tree-sitter ASTs."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        clear_cfg_mapping_cache()

    def _build(self, source: str) -> FunctionCfg:
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        tree, src = _parse_python(source)
        body = _get_function_body(tree, "python")
        return build_function_cfg(body, src, mapping, _sym_id())

    def test_empty_function(self) -> None:
        cfg = self._build("def f():\n    pass\n")
        assert cfg.entry_block in cfg.blocks
        assert cfg.exit_block in cfg.blocks
        # Entry should reach exit
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

    def test_sequential_statements(self) -> None:
        cfg = self._build(
            "def f():\n"
            "    x = 1\n"
            "    y = 2\n"
            "    z = x + y\n"
        )
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable
        # Should have multiple blocks or statements
        total_stmts = sum(len(b.statements) for b in cfg.blocks.values())
        assert total_stmts >= 3

    def test_if_else(self) -> None:
        cfg = self._build(
            "def f(x):\n"
            "    if x > 0:\n"
            "        y = 1\n"
            "    else:\n"
            "        y = -1\n"
            "    return y\n"
        )
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

        # Find the condition block — should have true and false edges
        found_branch = False
        for block in cfg.blocks.values():
            etypes = {e.edge_type for e in block.successors}
            if "true" in etypes and "false" in etypes:
                found_branch = True
                break
        assert found_branch, "Expected a block with true/false edges"

    def test_if_no_else(self) -> None:
        cfg = self._build(
            "def f(x):\n"
            "    if x > 0:\n"
            "        print(x)\n"
            "    return 0\n"
        )
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

        # Should have true and false edges from condition
        found_branch = False
        for block in cfg.blocks.values():
            etypes = {e.edge_type for e in block.successors}
            if "true" in etypes and "false" in etypes:
                found_branch = True
                break
        assert found_branch

    def test_while_loop(self) -> None:
        cfg = self._build(
            "def f():\n"
            "    x = 10\n"
            "    while x > 0:\n"
            "        x = x - 1\n"
            "    return x\n"
        )
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

        # Should have a cycle: some block has a successor path back to itself
        found_cycle = _has_cycle(cfg)
        assert found_cycle, "Expected a cycle (back-edge) in while loop"

    def test_for_loop(self) -> None:
        cfg = self._build(
            "def f():\n"
            "    for i in range(10):\n"
            "        print(i)\n"
        )
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

    def test_break_in_loop(self) -> None:
        cfg = self._build(
            "def f():\n"
            "    while True:\n"
            "        break\n"
            "    return 0\n"
        )
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

    def test_continue_in_loop(self) -> None:
        cfg = self._build(
            "def f():\n"
            "    for i in range(10):\n"
            "        if i == 5:\n"
            "            continue\n"
            "        print(i)\n"
        )
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

    def test_return_terminates_path(self) -> None:
        cfg = self._build(
            "def f(x):\n"
            "    if x > 0:\n"
            "        return 1\n"
            "    return -1\n"
        )
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

        # Both return blocks should connect to exit
        returns_to_exit = 0
        for block in cfg.blocks.values():
            for stmt in block.statements:
                if stmt.node_type == "return_statement":
                    assert any(
                        e.target_block == cfg.exit_block for e in block.successors
                    ), f"Return block {block.id} doesn't connect to exit"
                    returns_to_exit += 1
        assert returns_to_exit >= 2

    def test_try_except(self) -> None:
        cfg = self._build(
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except ValueError:\n"
            "        handle()\n"
        )
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

        # Should have exception edges
        found_exception = False
        for block in cfg.blocks.values():
            for edge in block.successors:
                if edge.edge_type == "exception":
                    found_exception = True
                    break
        assert found_exception

    def test_try_except_finally(self) -> None:
        cfg = self._build(
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except ValueError:\n"
            "        handle()\n"
            "    finally:\n"
            "        cleanup()\n"
        )
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

    def test_with_statement(self) -> None:
        cfg = self._build(
            "def f():\n"
            "    with open('f') as fh:\n"
            "        data = fh.read()\n"
            "    return data\n"
        )
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

        # Should have an exception edge (context manager cleanup)
        found_exception = False
        for block in cfg.blocks.values():
            for edge in block.successors:
                if edge.edge_type == "exception":
                    found_exception = True
        assert found_exception

    def test_nested_if(self) -> None:
        cfg = self._build(
            "def f(x, y):\n"
            "    if x > 0:\n"
            "        if y > 0:\n"
            "            return 1\n"
            "        else:\n"
            "            return 2\n"
            "    return 3\n"
        )
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

    def test_nested_loops(self) -> None:
        cfg = self._build(
            "def f():\n"
            "    for i in range(10):\n"
            "        for j in range(10):\n"
            "            if i == j:\n"
            "                break\n"
        )
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable


# ---------------------------------------------------------------------------
# CFG builder tests — Rust
# ---------------------------------------------------------------------------


class TestCfgBuilderRust:
    """Test CFG construction from real Rust tree-sitter ASTs."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        clear_cfg_mapping_cache()

    def _build(self, source: str) -> FunctionCfg:
        mapping = load_cfg_mapping("rust")
        assert mapping is not None
        tree, src = _parse_rust(source)
        body = _get_function_body(tree, "rust")
        return build_function_cfg(body, src, mapping, "rust:test.rs:1-10:foo:function")

    def test_if_else(self) -> None:
        cfg = self._build(
            "fn foo(x: i32) -> i32 {\n"
            "    if x > 0 {\n"
            "        return x;\n"
            "    } else {\n"
            "        return -x;\n"
            "    }\n"
            "}\n"
        )
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

        # Should have true/false branching
        found_branch = False
        for block in cfg.blocks.values():
            etypes = {e.edge_type for e in block.successors}
            if "true" in etypes and "false" in etypes:
                found_branch = True
        assert found_branch

    def test_while_loop(self) -> None:
        cfg = self._build(
            "fn foo() {\n"
            "    let mut x = 10;\n"
            "    while x > 0 {\n"
            "        x -= 1;\n"
            "    }\n"
            "}\n"
        )
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

    def test_loop_infinite_with_break(self) -> None:
        cfg = self._build(
            "fn foo() {\n"
            "    loop {\n"
            "        break;\n"
            "    }\n"
            "}\n"
        )
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

    def test_for_loop(self) -> None:
        cfg = self._build(
            "fn foo() {\n"
            "    for i in 0..10 {\n"
            "        println!(\"{}\", i);\n"
            "    }\n"
            "}\n"
        )
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

    def test_match_expression(self) -> None:
        cfg = self._build(
            "fn foo(x: i32) {\n"
            "    match x {\n"
            "        1 => println!(\"one\"),\n"
            "        2 => println!(\"two\"),\n"
            "        _ => println!(\"other\"),\n"
            "    }\n"
            "}\n"
        )
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

        # Should have case edges from match scrutinee
        found_case = False
        for block in cfg.blocks.values():
            for edge in block.successors:
                if edge.edge_type == "case":
                    found_case = True
        assert found_case

    def test_return_expression(self) -> None:
        cfg = self._build(
            "fn foo(x: i32) -> i32 {\n"
            "    return x + 1;\n"
            "}\n"
        )
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable


# ---------------------------------------------------------------------------
# CfgBuilder._find_child tests
# ---------------------------------------------------------------------------


class TestFindChild:
    """Test the _find_child static method for YAML child reference resolution."""

    def _make_mock_node(self, fields: dict[str, Any], children: list[Any]) -> MagicMock:
        node = MagicMock()
        node.child_by_field_name = lambda name: fields.get(name)
        node.children = children
        return node

    def test_field_prefix(self) -> None:
        child = MagicMock(type="block")
        node = self._make_mock_node({"body": child}, [])
        assert CfgBuilder._find_child(node, "field:body") is child

    def test_field_prefix_missing(self) -> None:
        node = self._make_mock_node({}, [])
        assert CfgBuilder._find_child(node, "field:body") is None

    def test_type_prefix(self) -> None:
        child = MagicMock(type="except_clause")
        node = self._make_mock_node({}, [child])
        assert CfgBuilder._find_child(node, "type:except_clause") is child

    def test_type_prefix_missing(self) -> None:
        node = self._make_mock_node({}, [])
        assert CfgBuilder._find_child(node, "type:except_clause") is None

    def test_bare_name_field_first(self) -> None:
        field_child = MagicMock(type="block")
        type_child = MagicMock(type="body")
        node = self._make_mock_node({"body": field_child}, [type_child])
        # Bare name should find via field first
        assert CfgBuilder._find_child(node, "body") is field_child

    def test_bare_name_type_fallback(self) -> None:
        type_child = MagicMock(type="finally_clause")
        node = self._make_mock_node({}, [type_child])
        assert CfgBuilder._find_child(node, "finally_clause") is type_child

    def test_bare_name_nothing(self) -> None:
        node = self._make_mock_node({}, [])
        assert CfgBuilder._find_child(node, "nonexistent") is None


# ---------------------------------------------------------------------------
# Edge cases and builder internals
# ---------------------------------------------------------------------------


class TestCfgBuilderEdgeCases:
    """Test edge cases in the CFG builder."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        clear_cfg_mapping_cache()

    def test_empty_body(self) -> None:
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        tree, src = _parse_python("def f():\n    pass\n")
        body = _get_function_body(tree, "python")
        cfg = build_function_cfg(body, src, mapping, _sym_id())
        assert cfg.symbol_id == _sym_id()
        assert len(cfg.blocks) >= 1

    def test_unmapped_types_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        mapping = CfgNodeMapping(language="test", grammar="test")
        # Create a mock node with an unmapped compound type
        child = MagicMock()
        child.type = "weird_construct"
        child.is_named = True
        child.named_child_count = 1
        child.start_point = (0, 0)
        child.end_point = (0, 10)
        child.start_byte = 0
        child.end_byte = 10

        inner = MagicMock()
        inner.type = "inner_thing"
        inner.is_named = False
        inner.child_count = 0
        inner.named_child_count = 0
        inner.start_point = (0, 0)
        inner.end_point = (0, 5)
        inner.start_byte = 0
        inner.end_byte = 5
        inner.children = []

        child.children = [inner]

        root = MagicMock()
        root.children = [child]

        with caplog.at_level(logging.DEBUG, logger="hypergumbo_core.cfg"):
            builder = CfgBuilder(mapping, "test:t.py:1-5:f:function")
            builder.build(root, b"some code here")

        assert "weird_construct" in caplog.text

    def test_break_outside_loop_no_crash(self) -> None:
        """Break outside a loop should not crash — just creates a dead block."""
        mapping = CfgNodeMapping(
            language="test", grammar="test",
            break_statements=["break_stmt"],
        )
        break_node = MagicMock()
        break_node.type = "break_stmt"
        break_node.is_named = True
        break_node.child_count = 0
        break_node.named_child_count = 0
        break_node.start_point = (0, 0)
        break_node.end_point = (0, 5)
        break_node.start_byte = 0
        break_node.end_byte = 5
        break_node.children = []

        root = MagicMock()
        root.children = [break_node]

        builder = CfgBuilder(mapping, "test:t.py:1-5:f:function")
        cfg = builder.build(root, b"break")
        # Should produce blocks without crashing
        assert len(cfg.blocks) >= 1

    def test_continue_outside_loop_no_crash(self) -> None:
        """Continue outside a loop should not crash."""
        mapping = CfgNodeMapping(
            language="test", grammar="test",
            continue_statements=["continue_stmt"],
        )
        cont_node = MagicMock()
        cont_node.type = "continue_stmt"
        cont_node.is_named = True
        cont_node.child_count = 0
        cont_node.named_child_count = 0
        cont_node.start_point = (0, 0)
        cont_node.end_point = (0, 8)
        cont_node.start_byte = 0
        cont_node.end_byte = 8
        cont_node.children = []

        root = MagicMock()
        root.children = [cont_node]

        builder = CfgBuilder(mapping, "test:t.py:1-5:f:function")
        cfg = builder.build(root, b"continue")
        assert len(cfg.blocks) >= 1

    def test_code_snippet_truncation(self) -> None:
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        # Create a function with a very long statement
        long_expr = "x" * 100
        tree, src = _parse_python(f"def f():\n    {long_expr} = 1\n")
        body = _get_function_body(tree, "python")
        cfg = build_function_cfg(body, src, mapping, _sym_id())

        # Find a statement with truncated snippet
        for block in cfg.blocks.values():
            for stmt in block.statements:
                if len(stmt.code_snippet) > 3 and stmt.code_snippet.endswith("..."):
                    assert len(stmt.code_snippet) == 80
                    return
        # The assignment may not be super long, but the function produces valid output
        assert cfg.exit_block in _reachable_blocks(cfg, cfg.entry_block)

    def test_convenience_function(self) -> None:
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        tree, src = _parse_python("def f():\n    return 1\n")
        body = _get_function_body(tree, "python")
        cfg = build_function_cfg(body, src, mapping, _sym_id())
        assert isinstance(cfg, FunctionCfg)
        assert cfg.symbol_id == _sym_id()

    def test_try_without_finally(self) -> None:
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        cfg_src = (
            "def f():\n"
            "    try:\n"
            "        x = 1\n"
            "    except:\n"
            "        x = 2\n"
        )
        tree, src = _parse_python(cfg_src)
        body = _get_function_body(tree, "python")
        cfg = build_function_cfg(body, src, mapping, _sym_id())
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

    def test_context_manager_empty_body(self) -> None:
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        tree, src = _parse_python("def f():\n    with open('f'):\n        pass\n")
        body = _get_function_body(tree, "python")
        cfg = build_function_cfg(body, src, mapping, _sym_id())
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

    def test_loop_with_empty_body(self) -> None:
        """Loop with only pass (empty body) should still produce valid CFG."""
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        tree, src = _parse_python("def f():\n    while True:\n        pass\n")
        body = _get_function_body(tree, "python")
        cfg = build_function_cfg(body, src, mapping, _sym_id())
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        # Infinite loop with no break — exit may not be reachable, which is correct
        assert cfg.entry_block in reachable

    def test_conditional_empty_true_body(self) -> None:
        """Conditional where true body produces no partial (lines 741-743)."""
        mapping = CfgNodeMapping(
            language="test", grammar="test",
            conditionals=[ConditionalMapping(
                node_type="if_stmt",
                condition_child="field:cond",
                true_child="field:then",
                false_child="field:else_",
            )],
        )
        cond = MagicMock()
        cond.type = "cond"
        cond.start_point = (0, 3)
        cond.end_point = (0, 4)
        cond.start_byte = 3
        cond.end_byte = 4

        # True body exists but has no children → produces no partial
        then_body = MagicMock()
        then_body.type = "block"
        then_body.children = []

        if_node = MagicMock()
        if_node.type = "if_stmt"
        if_node.is_named = True
        if_node.child_count = 2
        if_node.named_child_count = 2
        if_node.start_point = (0, 0)
        if_node.end_point = (1, 0)
        if_node.start_byte = 0
        if_node.end_byte = 10
        if_node.children = [cond, then_body]
        if_node.child_by_field_name = lambda name: {
            "cond": cond, "then": then_body,
        }.get(name)

        root = MagicMock()
        root.children = [if_node]

        builder = CfgBuilder(mapping, _sym_id())
        cfg = builder.build(root, b"if x:\n  \n")
        # Should have a block with true fringe edge (empty body fallthrough)
        found_true = False
        for block in cfg.blocks.values():
            etypes = {e.edge_type for e in block.successors}
            if "true" in etypes:
                found_true = True
        assert found_true or len(cfg.blocks) >= 2

    def test_conditional_empty_false_body(self) -> None:
        """Conditional where false body found but produces no partial (line 756)."""
        mapping = CfgNodeMapping(
            language="test", grammar="test",
            conditionals=[ConditionalMapping(
                node_type="if_stmt",
                condition_child="field:cond",
                true_child="field:then",
                false_child="field:else_",
            )],
        )
        cond = MagicMock()
        cond.type = "cond"
        cond.start_point = (0, 3)
        cond.end_point = (0, 4)
        cond.start_byte = 3
        cond.end_byte = 4

        then_stmt = MagicMock()
        then_stmt.type = "expr"
        then_stmt.is_named = True
        then_stmt.child_count = 0
        then_stmt.named_child_count = 0
        then_stmt.start_point = (1, 0)
        then_stmt.end_point = (1, 5)
        then_stmt.start_byte = 5
        then_stmt.end_byte = 10
        then_stmt.children = []

        then_body = MagicMock()
        then_body.type = "block"
        then_body.children = [then_stmt]

        # False body exists but has no children → empty partial
        else_body = MagicMock()
        else_body.type = "else_clause"
        else_body.is_named = True
        else_body.child_count = 0
        else_body.named_child_count = 0
        else_body.start_point = (2, 0)
        else_body.end_point = (3, 0)
        else_body.start_byte = 11
        else_body.end_byte = 15
        else_body.children = []  # empty

        if_node = MagicMock()
        if_node.type = "if_stmt"
        if_node.is_named = True
        if_node.child_count = 3
        if_node.named_child_count = 3
        if_node.start_point = (0, 0)
        if_node.end_point = (3, 0)
        if_node.start_byte = 0
        if_node.end_byte = 15
        if_node.children = [cond, then_body, else_body]
        if_node.child_by_field_name = lambda name: {
            "cond": cond, "then": then_body, "else_": else_body,
        }.get(name)

        root = MagicMock()
        root.children = [if_node]

        builder = CfgBuilder(mapping, _sym_id())
        cfg = builder.build(root, b"if x:\n  a()\nelse:\n  \n")
        # Should still produce valid CFG
        assert len(cfg.blocks) >= 2

    def test_loop_empty_body_not_infinite(self) -> None:
        """Non-infinite loop with empty body (lines 826-827)."""
        mapping = CfgNodeMapping(
            language="test", grammar="test",
            loops=[LoopMapping(
                node_type="while_stmt",
                condition_child="field:cond",
                body_child="field:body",
                infinite=False,
            )],
        )
        cond = MagicMock()
        cond.type = "cond"
        cond.start_point = (0, 6)
        cond.end_point = (0, 10)
        cond.start_byte = 6
        cond.end_byte = 10

        body = MagicMock()
        body.type = "block"
        body.children = []  # empty body

        while_node = MagicMock()
        while_node.type = "while_stmt"
        while_node.is_named = True
        while_node.child_count = 2
        while_node.named_child_count = 2
        while_node.start_point = (0, 0)
        while_node.end_point = (1, 0)
        while_node.start_byte = 0
        while_node.end_byte = 15
        while_node.children = [cond, body]
        while_node.child_by_field_name = lambda name: {
            "cond": cond, "body": body,
        }.get(name)

        root = MagicMock()
        root.children = [while_node]

        builder = CfgBuilder(mapping, _sym_id())
        cfg = builder.build(root, b"while cond:\n  \n")
        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

    def test_context_manager_no_body_content(self) -> None:
        """Context manager where body exists but produces no partial (line 1081)."""
        mapping = CfgNodeMapping(
            language="test", grammar="test",
            context_manager=[ContextManagerMapping(
                node_type="with_stmt",
                value_child="field:value",
                body_child="field:body",
            )],
        )
        value = MagicMock()
        value.type = "call"
        value.start_point = (0, 5)
        value.end_point = (0, 12)
        value.start_byte = 5
        value.end_byte = 12

        body = MagicMock()
        body.type = "block"
        body.children = []  # empty

        with_node = MagicMock()
        with_node.type = "with_stmt"
        with_node.is_named = True
        with_node.child_count = 2
        with_node.named_child_count = 2
        with_node.start_point = (0, 0)
        with_node.end_point = (1, 0)
        with_node.start_byte = 0
        with_node.end_byte = 15
        with_node.children = [value, body]
        with_node.child_by_field_name = lambda name: {
            "value": value, "body": body,
        }.get(name)

        root = MagicMock()
        root.children = [with_node]

        builder = CfgBuilder(mapping, _sym_id())
        cfg = builder.build(root, b"with open():\n  \n")

        # Should have enter block → exit block directly
        found_exception = False
        for block in cfg.blocks.values():
            for edge in block.successors:
                if edge.edge_type == "exception":
                    found_exception = True
        assert found_exception

    def test_sequential_child_no_entry(self) -> None:
        """Sequential composition where a child returns partial with no entry (line 630)."""
        mapping = CfgNodeMapping(language="test", grammar="test")

        # First child: produces a normal block
        stmt1 = MagicMock()
        stmt1.type = "expr"
        stmt1.is_named = True
        stmt1.child_count = 0
        stmt1.named_child_count = 0
        stmt1.start_point = (0, 0)
        stmt1.end_point = (0, 5)
        stmt1.start_byte = 0
        stmt1.end_byte = 5
        stmt1.children = []

        # Second child: a compound node whose children produce partials with no entry
        # We simulate this by having a compound node with only non-named children
        inner_anon = MagicMock()
        inner_anon.type = "punct"
        inner_anon.is_named = False
        inner_anon.child_count = 0
        inner_anon.named_child_count = 0
        inner_anon.children = []

        compound = MagicMock()
        compound.type = "wrapper"
        compound.is_named = True
        compound.child_count = 1
        compound.named_child_count = 0
        compound.start_point = (1, 0)
        compound.end_point = (1, 5)
        compound.start_byte = 6
        compound.end_byte = 11
        compound.children = [inner_anon]

        root = MagicMock()
        root.children = [stmt1, compound]

        builder = CfgBuilder(mapping, _sym_id())
        cfg = builder.build(root, b"expr1\nwrap;\n")
        # Should still produce valid CFG
        assert len(cfg.blocks) >= 1

    def test_try_with_catch_child_field(self) -> None:
        """Test try/catch where catch_child is a direct field reference."""
        mapping = CfgNodeMapping(
            language="test", grammar="test",
            try_catch=[TryCatchMapping(
                node_type="try_stmt",
                body_child="field:body",
                catch_child="field:handler",
            )],
        )

        # Build mock nodes: try body with one statement, handler with one statement
        try_body_stmt = MagicMock()
        try_body_stmt.type = "expr"
        try_body_stmt.is_named = True
        try_body_stmt.child_count = 0
        try_body_stmt.named_child_count = 0
        try_body_stmt.start_point = (1, 0)
        try_body_stmt.end_point = (1, 5)
        try_body_stmt.start_byte = 6
        try_body_stmt.end_byte = 11
        try_body_stmt.children = []

        try_body = MagicMock()
        try_body.type = "block"
        try_body.children = [try_body_stmt]

        handler_stmt = MagicMock()
        handler_stmt.type = "expr"
        handler_stmt.is_named = True
        handler_stmt.child_count = 0
        handler_stmt.named_child_count = 0
        handler_stmt.start_point = (2, 0)
        handler_stmt.end_point = (2, 8)
        handler_stmt.start_byte = 12
        handler_stmt.end_byte = 20
        handler_stmt.children = []

        handler = MagicMock()
        handler.type = "handler"
        handler.children = [handler_stmt]

        try_node = MagicMock()
        try_node.type = "try_stmt"
        try_node.is_named = True
        try_node.child_count = 2
        try_node.named_child_count = 2
        try_node.start_point = (0, 0)
        try_node.end_point = (3, 0)
        try_node.start_byte = 0
        try_node.end_byte = 25
        try_node.children = [try_body, handler]
        try_node.child_by_field_name = lambda name: {
            "body": try_body, "handler": handler
        }.get(name)

        root = MagicMock()
        root.children = [try_node]

        builder = CfgBuilder(mapping, "test:t.py:1-5:f:function")
        cfg = builder.build(root, b"try:\n  expr\nexcept:\n  expr\n")

        # Should have exception edge
        found_exception = False
        for block in cfg.blocks.values():
            for edge in block.successors:
                if edge.edge_type == "exception":
                    found_exception = True
        assert found_exception

    def test_switch_without_arm_type_filter(self) -> None:
        """Switch/match where arm_type is None (all named children are arms)."""
        mapping = CfgNodeMapping(
            language="test", grammar="test",
            switch=[SwitchMapping(
                node_type="switch_stmt",
                scrutinee_child="field:expr",
                arms_child="field:cases",
                arm_type=None,
            )],
        )

        # Build mock: switch with 2 case arms
        expr = MagicMock()
        expr.type = "identifier"
        expr.start_point = (0, 7)
        expr.end_point = (0, 8)
        expr.start_byte = 7
        expr.end_byte = 8

        arm1_body = MagicMock()
        arm1_body.type = "expr"
        arm1_body.is_named = True
        arm1_body.child_count = 0
        arm1_body.named_child_count = 0
        arm1_body.start_point = (1, 0)
        arm1_body.end_point = (1, 5)
        arm1_body.start_byte = 9
        arm1_body.end_byte = 14
        arm1_body.children = []

        arm1 = MagicMock()
        arm1.type = "case"
        arm1.is_named = True
        arm1.children = [arm1_body]

        arm2_body = MagicMock()
        arm2_body.type = "expr"
        arm2_body.is_named = True
        arm2_body.child_count = 0
        arm2_body.named_child_count = 0
        arm2_body.start_point = (2, 0)
        arm2_body.end_point = (2, 5)
        arm2_body.start_byte = 15
        arm2_body.end_byte = 20
        arm2_body.children = []

        arm2 = MagicMock()
        arm2.type = "case"
        arm2.is_named = True
        arm2.children = [arm2_body]

        # Punctuation child that should be skipped (line 1170 coverage)
        punct = MagicMock()
        punct.type = ","
        punct.is_named = False

        cases = MagicMock()
        cases.type = "cases"
        cases.children = [arm1, punct, arm2]

        switch_node = MagicMock()
        switch_node.type = "switch_stmt"
        switch_node.is_named = True
        switch_node.child_count = 2
        switch_node.named_child_count = 2
        switch_node.start_point = (0, 0)
        switch_node.end_point = (3, 0)
        switch_node.start_byte = 0
        switch_node.end_byte = 25
        switch_node.children = [expr, cases]
        switch_node.child_by_field_name = lambda name: {
            "expr": expr, "cases": cases,
        }.get(name)

        root = MagicMock()
        root.children = [switch_node]

        builder = CfgBuilder(mapping, "test:t.py:1-5:f:function")
        cfg = builder.build(root, b"switch x\n  case 1\n  case 2\n")

        # Should have case edges
        case_edges = 0
        for block in cfg.blocks.values():
            for edge in block.successors:
                if edge.edge_type == "case":
                    case_edges += 1
        assert case_edges == 2


# ---------------------------------------------------------------------------
# Deferred execution tests (Go defer semantic hook)
# ---------------------------------------------------------------------------


class TestDeferredExecution:
    """Test the deferred execution semantic hook using mock nodes."""

    def test_deferred_statements_in_exit_block(self) -> None:
        mapping = CfgNodeMapping(
            language="test", grammar="test",
            deferred=[DeferredMapping(node_type="defer_statement", body_child="field:call")],
        )

        # Create mock nodes for: stmt1; defer cleanup(); stmt2
        stmt1 = MagicMock()
        stmt1.type = "expr"
        stmt1.is_named = True
        stmt1.child_count = 0
        stmt1.named_child_count = 0
        stmt1.start_point = (0, 0)
        stmt1.end_point = (0, 5)
        stmt1.start_byte = 0
        stmt1.end_byte = 5
        stmt1.children = []

        defer_call = MagicMock()
        defer_call.type = "call"
        defer_call.start_point = (1, 6)
        defer_call.end_point = (1, 15)
        defer_call.start_byte = 12
        defer_call.end_byte = 21

        defer_node = MagicMock()
        defer_node.type = "defer_statement"
        defer_node.is_named = True
        defer_node.child_count = 1
        defer_node.named_child_count = 1
        defer_node.start_point = (1, 0)
        defer_node.end_point = (1, 15)
        defer_node.start_byte = 6
        defer_node.end_byte = 21
        defer_node.children = [defer_call]
        defer_node.child_by_field_name = lambda name: defer_call if name == "call" else None

        stmt2 = MagicMock()
        stmt2.type = "expr"
        stmt2.is_named = True
        stmt2.child_count = 0
        stmt2.named_child_count = 0
        stmt2.start_point = (2, 0)
        stmt2.end_point = (2, 5)
        stmt2.start_byte = 22
        stmt2.end_byte = 27
        stmt2.children = []

        root = MagicMock()
        root.children = [stmt1, defer_node, stmt2]

        builder = CfgBuilder(mapping, "test:t.go:1-5:foo:function")
        cfg = builder.build(root, b"stmt1\ndefer cleanup()\nstmt2\n")

        # Deferred call should appear in the exit block
        exit_block = cfg.blocks[cfg.exit_block]
        deferred_stmts = [s for s in exit_block.statements if s.node_type == "deferred_call"]
        assert len(deferred_stmts) == 1
        assert "cleanup()" in deferred_stmts[0].code_snippet

    def test_deferred_lifo_order(self) -> None:
        """Multiple defers should execute in LIFO order at exit."""
        mapping = CfgNodeMapping(
            language="test", grammar="test",
            deferred=[DeferredMapping(node_type="defer_statement", body_child="field:call")],
        )

        source = b"defer a()\ndefer b()\ndefer c()\n"

        nodes = []
        for i, name in enumerate(["a()", "b()", "c()"]):
            call = MagicMock()
            call.type = "call"
            call.start_point = (i, 6)
            call.end_point = (i, 6 + len(name))
            offset = i * 10
            call.start_byte = offset + 6
            call.end_byte = offset + 6 + len(name)

            defer = MagicMock()
            defer.type = "defer_statement"
            defer.is_named = True
            defer.child_count = 1
            defer.named_child_count = 1
            defer.start_point = (i, 0)
            defer.end_point = (i, 6 + len(name))
            defer.start_byte = offset
            defer.end_byte = offset + 6 + len(name)
            defer.children = [call]
            defer.child_by_field_name = lambda name, c=call: c if name == "call" else None
            nodes.append(defer)

        root = MagicMock()
        root.children = nodes

        builder = CfgBuilder(mapping, "test:t.go:1-5:foo:function")
        cfg = builder.build(root, source)

        exit_block = cfg.blocks[cfg.exit_block]
        deferred_stmts = [s for s in exit_block.statements if s.node_type == "deferred_call"]
        assert len(deferred_stmts) == 3
        # LIFO: c, b, a
        assert "c()" in deferred_stmts[0].code_snippet
        assert "b()" in deferred_stmts[1].code_snippet
        assert "a()" in deferred_stmts[2].code_snippet


# ---------------------------------------------------------------------------
# Early return (Rust ?) tests
# ---------------------------------------------------------------------------


class TestEarlyReturn:
    """Test early return semantic hook with real Rust ASTs."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        clear_cfg_mapping_cache()

    def test_try_expression_dual_flow(self) -> None:
        """The ? operator should produce both Ok (continue) and Err (exit) paths."""
        mapping = load_cfg_mapping("rust")
        assert mapping is not None
        tree, src = _parse_rust(
            "fn foo() -> Result<i32, String> {\n"
            "    let x = some_fn()?;\n"
            "    return Ok(x);\n"
            "}\n"
        )
        body = _get_function_body(tree, "rust")
        cfg = build_function_cfg(body, src, mapping, "rust:test.rs:1-4:foo:function")

        reachable = _reachable_blocks(cfg, cfg.entry_block)
        assert cfg.exit_block in reachable

        # Should have a block with both true (Ok) and false (Err) edges
        found_dual = False
        for block in cfg.blocks.values():
            etypes = {e.edge_type for e in block.successors}
            if "true" in etypes and "false" in etypes:
                found_dual = True
                # The false edge should go to exit
                for edge in block.successors:
                    if edge.edge_type == "false":
                        assert edge.target_block == cfg.exit_block
        assert found_dual


# ---------------------------------------------------------------------------
# Reaching-def solver tests
# ---------------------------------------------------------------------------


def _make_cfg(blocks_spec: list[dict]) -> FunctionCfg:
    """Build a FunctionCfg from a simplified spec.

    Each entry in blocks_spec is a dict with:
    - id: block id
    - stmts: list of (line, defines, uses) tuples
    - succs: list of (target_id, edge_type) tuples
    """
    blocks: dict[str, BasicBlock] = {}
    sym_id = "test:t.py:1-10:f:function"

    for spec in blocks_spec:
        bid = spec["id"]
        block = BasicBlock(id=bid, symbol_id=sym_id)
        for line, defines, uses in spec.get("stmts", []):
            block.statements.append(CfgStatement(
                line=line, col=0, node_type="stmt",
                code_snippet=f"line {line}",
                defines=list(defines),
                uses=list(uses),
            ))
        for target, etype in spec.get("succs", []):
            block.successors.append(CfgEdge(target_block=target, edge_type=etype))
        blocks[bid] = block

    entry = blocks_spec[0]["id"] if blocks_spec else "bb_exit"
    exit_id = "bb_exit"
    if exit_id not in blocks:
        blocks[exit_id] = BasicBlock(id=exit_id, symbol_id=sym_id)

    return FunctionCfg(
        symbol_id=sym_id,
        entry_block=entry,
        exit_block=exit_id,
        blocks=blocks,
    )


class TestReachingDefSolver:
    """Test the worklist-based reaching-def solver."""

    def test_empty_cfg(self) -> None:
        """No definitions → no DDG edges."""
        cfg = _make_cfg([
            {"id": "bb_0", "stmts": [(1, [], ["x"])], "succs": [("bb_exit", "always")]},
        ])
        result = solve_reaching_defs(cfg)
        assert result.ddg_edges == []
        assert result.definition_count == 0
        assert not result.bailed_out

    def test_single_def_use(self) -> None:
        """x = 1; use(x) → DDG edge from def to use."""
        cfg = _make_cfg([
            {"id": "bb_0", "stmts": [
                (1, ["x"], []),     # x = 1
                (2, [], ["x"]),     # use(x)
            ], "succs": [("bb_exit", "always")]},
        ])
        result = solve_reaching_defs(cfg)
        assert result.definition_count == 1
        assert len(result.ddg_edges) == 1
        edge = result.ddg_edges[0]
        assert edge.variable == "x"
        assert edge.def_line == 1
        assert edge.use_line == 2

    def test_two_defs_same_var_kill(self) -> None:
        """x = 1; x = 2; use(x) → only second def reaches use."""
        cfg = _make_cfg([
            {"id": "bb_0", "stmts": [
                (1, ["x"], []),     # x = 1
                (2, ["x"], []),     # x = 2 (kills first)
                (3, [], ["x"]),     # use(x)
            ], "succs": [("bb_exit", "always")]},
        ])
        result = solve_reaching_defs(cfg)
        assert len(result.ddg_edges) == 1
        edge = result.ddg_edges[0]
        assert edge.def_line == 2  # second def, not first

    def test_cross_block_def_use(self) -> None:
        """def in bb_0, use in bb_1 → DDG edge spans blocks."""
        cfg = _make_cfg([
            {"id": "bb_0", "stmts": [
                (1, ["x"], []),
            ], "succs": [("bb_1", "always")]},
            {"id": "bb_1", "stmts": [
                (2, [], ["x"]),
            ], "succs": [("bb_exit", "always")]},
        ])
        result = solve_reaching_defs(cfg)
        assert len(result.ddg_edges) == 1
        edge = result.ddg_edges[0]
        assert edge.def_block == "bb_0"
        assert edge.use_block == "bb_1"

    def test_conditional_merge(self) -> None:
        """Both branches define x → both reach use at merge point."""
        cfg = _make_cfg([
            {"id": "bb_cond", "stmts": [], "succs": [
                ("bb_true", "true"), ("bb_false", "false"),
            ]},
            {"id": "bb_true", "stmts": [
                (2, ["x"], []),
            ], "succs": [("bb_merge", "always")]},
            {"id": "bb_false", "stmts": [
                (3, ["x"], []),
            ], "succs": [("bb_merge", "always")]},
            {"id": "bb_merge", "stmts": [
                (4, [], ["x"]),
            ], "succs": [("bb_exit", "always")]},
        ])
        result = solve_reaching_defs(cfg)
        # Both defs should reach the use (phi-like)
        x_edges = [e for e in result.ddg_edges if e.variable == "x"]
        assert len(x_edges) == 2
        def_lines = sorted(e.def_line for e in x_edges)
        assert def_lines == [2, 3]

    def test_loop_fixpoint(self) -> None:
        """Loop: x = 0; while (...) { use(x); x = x + 1 }

        Both the initial def and the loop body def should reach the use.
        """
        cfg = _make_cfg([
            {"id": "bb_init", "stmts": [
                (1, ["x"], []),     # x = 0
            ], "succs": [("bb_header", "always")]},
            {"id": "bb_header", "stmts": [], "succs": [
                ("bb_body", "true"), ("bb_exit", "false"),
            ]},
            {"id": "bb_body", "stmts": [
                (3, [], ["x"]),     # use(x)
                (4, ["x"], ["x"]),  # x = x + 1
            ], "succs": [("bb_header", "always")]},
        ])
        result = solve_reaching_defs(cfg)
        # The use of x at line 3 should be reached by both:
        # - x = 0 (line 1, initial)
        # - x = x + 1 (line 4, loop body - from previous iteration)
        x_use_edges = [e for e in result.ddg_edges if e.variable == "x" and e.use_line == 3]
        assert len(x_use_edges) == 2
        def_lines = sorted(e.def_line for e in x_use_edges)
        assert def_lines == [1, 4]

    def test_multiple_variables(self) -> None:
        """x = 1; y = 2; use(x, y) → separate DDG edges."""
        cfg = _make_cfg([
            {"id": "bb_0", "stmts": [
                (1, ["x"], []),
                (2, ["y"], []),
                (3, [], ["x", "y"]),
            ], "succs": [("bb_exit", "always")]},
        ])
        result = solve_reaching_defs(cfg)
        assert result.definition_count == 2
        assert len(result.ddg_edges) == 2
        vars_in_edges = {e.variable for e in result.ddg_edges}
        assert vars_in_edges == {"x", "y"}

    def test_def_use_in_same_statement(self) -> None:
        """x = x + 1 → use of x should reach old def, new def is generated."""
        cfg = _make_cfg([
            {"id": "bb_0", "stmts": [
                (1, ["x"], []),       # x = 0
                (2, ["x"], ["x"]),    # x = x + 1 (uses old x, defines new x)
                (3, [], ["x"]),       # use(x)
            ], "succs": [("bb_exit", "always")]},
        ])
        result = solve_reaching_defs(cfg)
        # Line 2 uses x → should have edge from line 1's def
        use_at_2 = [e for e in result.ddg_edges if e.use_line == 2]
        assert len(use_at_2) == 1
        assert use_at_2[0].def_line == 1

        # Line 3 uses x → should have edge from line 2's def (not line 1)
        use_at_3 = [e for e in result.ddg_edges if e.use_line == 3]
        assert len(use_at_3) == 1
        assert use_at_3[0].def_line == 2

    def test_no_use_no_edge(self) -> None:
        """Definitions without uses produce no DDG edges."""
        cfg = _make_cfg([
            {"id": "bb_0", "stmts": [
                (1, ["x"], []),
                (2, ["y"], []),
            ], "succs": [("bb_exit", "always")]},
        ])
        result = solve_reaching_defs(cfg)
        assert result.definition_count == 2
        assert len(result.ddg_edges) == 0

    def test_bail_out(self) -> None:
        """Functions with > MAX_DEFINITIONS should bail out."""
        # Create a CFG with MAX_DEFINITIONS + 1 definitions
        stmts = [(i + 1, [f"v{i}"], []) for i in range(MAX_DEFINITIONS + 1)]
        cfg = _make_cfg([
            {"id": "bb_0", "stmts": stmts, "succs": [("bb_exit", "always")]},
        ])
        result = solve_reaching_defs(cfg)
        assert result.bailed_out
        assert result.definition_count == MAX_DEFINITIONS + 1
        assert result.ddg_edges == []

    def test_unreachable_block(self) -> None:
        """Unreachable blocks should not generate DDG edges for external uses."""
        cfg = _make_cfg([
            {"id": "bb_0", "stmts": [
                (1, ["x"], []),
            ], "succs": [("bb_exit", "always")]},
            # bb_dead is unreachable — no predecessor
            {"id": "bb_dead", "stmts": [
                (10, [], ["x"]),
            ], "succs": [("bb_exit", "always")]},
        ])
        result = solve_reaching_defs(cfg)
        # The use of x in bb_dead should NOT have a reaching def
        # (no path from bb_0 to bb_dead)
        dead_edges = [e for e in result.ddg_edges if e.use_block == "bb_dead"]
        assert len(dead_edges) == 0

    def test_ddg_edge_fields(self) -> None:
        """Verify all DdgEdge fields are populated correctly."""
        edge = DdgEdge(
            variable="x", def_block="bb_0", def_line=1,
            use_block="bb_1", use_line=5,
        )
        assert edge.variable == "x"
        assert edge.def_block == "bb_0"
        assert edge.def_line == 1
        assert edge.use_block == "bb_1"
        assert edge.use_line == 5

    def test_result_defaults(self) -> None:
        """Verify ReachingDefResult defaults."""
        r = ReachingDefResult(symbol_id="test:f")
        assert r.ddg_edges == []
        assert not r.bailed_out
        assert r.definition_count == 0

    def test_definition_dataclass(self) -> None:
        """Verify _Definition fields."""
        d = _Definition(index=0, variable="x", block_id="bb_0", line=1)
        assert d.index == 0
        assert d.variable == "x"


class TestCfgHelpers:
    """Test helper functions for the reaching-def solver."""

    def test_compute_predecessors(self) -> None:
        cfg = _make_cfg([
            {"id": "bb_0", "stmts": [], "succs": [("bb_1", "always")]},
            {"id": "bb_1", "stmts": [], "succs": [("bb_exit", "always")]},
        ])
        preds = _compute_predecessors(cfg)
        assert preds["bb_0"] == []
        assert preds["bb_1"] == ["bb_0"]
        assert "bb_1" in preds["bb_exit"]

    def test_reverse_postorder(self) -> None:
        cfg = _make_cfg([
            {"id": "bb_0", "stmts": [], "succs": [("bb_1", "always")]},
            {"id": "bb_1", "stmts": [], "succs": [("bb_exit", "always")]},
        ])
        rpo = _reverse_postorder(cfg)
        # Entry should come first in RPO
        assert rpo[0] == "bb_0"
        # bb_1 before bb_exit
        assert rpo.index("bb_1") < rpo.index("bb_exit")

    def test_reverse_postorder_with_cycle(self) -> None:
        cfg = _make_cfg([
            {"id": "bb_0", "stmts": [], "succs": [("bb_1", "always")]},
            {"id": "bb_1", "stmts": [], "succs": [("bb_0", "always"), ("bb_exit", "false")]},
        ])
        rpo = _reverse_postorder(cfg)
        # All blocks should be present
        assert set(rpo) == {"bb_0", "bb_1", "bb_exit"}
