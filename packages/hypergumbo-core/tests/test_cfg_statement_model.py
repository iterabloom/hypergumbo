# SPDX-License-Identifier: AGPL-3.0-or-later
"""The CfgBuilder's statement model: headers, initializers, nested functions.

Two defects of one model (WI-losod, WI-faful), fixed once in the shared builder
rather than per language:

- A loop header or an ``if``/``switch`` initializer is a statement that DEFINES
  its bindings before the body reads them. The conditional and loop hooks used
  to record only the condition, so ``for v in os.environ:``, Go's ``for _, e :=
  range os.Environ()`` and ``if err := cmd.Run(); err != nil`` defined nothing.
- An atomic statement holding a nested function or a control-flow expression
  stays ONE statement that defines its target. It used to be decomposed into
  leaves, so ``int r = switch (x) {...};`` read ``r`` as a use, never a
  definition, and a lambda's body was inlined as though it ran in sequence.

These tests check the CFG's SHAPE against real tree-sitter ASTs. No def/use
extractor is involved (they live in another package); where a test needs
definitions it sets them on the statements by hand. The extractor side, and the
end-to-end DDG and verify-claims behaviour, are tested in hypergumbo-lang-
mainstream's ``test_def_use_statement_model.py``.
"""
from __future__ import annotations

from typing import Any

import pytest
import tree_sitter
from tree_sitter_language_pack import get_language

from hypergumbo_core.cfg import (
    CfgBuilder,
    CfgNodeMapping,
    CfgStatement,
    FunctionCfg,
    NestedFunctionMapping,
    SwitchMapping,
    _parse_cfg_mapping,
    build_function_cfg,
    clear_cfg_mapping_cache,
    load_cfg_mapping,
    solve_reaching_defs,
    unaccounted_names,
    uncovered_semantic_lines,
)

_FUNCTION_TYPES = {
    "python": "function_definition",
    "go": "function_declaration",
    "java": "method_declaration",
    "typescript": "function_declaration",
    "rust": "function_item",
    "c": "function_definition",
}


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_cfg_mapping_cache()


def _outer_body(lang: str, source: str) -> tuple[Any, bytes]:
    """The body of the FIRST (outermost) function in ``source``."""
    parser = tree_sitter.Parser(get_language(lang))
    src = source.encode("utf-8")
    stack = [parser.parse(src).root_node]
    while stack:
        node = stack.pop()
        if node.type == _FUNCTION_TYPES[lang]:
            return node.child_by_field_name("body"), src
        stack.extend(reversed(node.children))
    raise AssertionError("no function in fixture")  # pragma: no cover


def _build(lang: str, source: str) -> tuple[FunctionCfg, Any, bytes]:
    body, src = _outer_body(lang, source)
    mapping = load_cfg_mapping(lang)
    assert mapping is not None
    return build_function_cfg(body, src, mapping, f"{lang}:t:1-99:f:function"), body, src


def _stmts(cfg: FunctionCfg) -> list[CfgStatement]:
    return [s for b in cfg.blocks.values() for s in b.statements]


def _block_of(cfg: FunctionCfg, snippet: str) -> Any:
    """The block holding the one statement whose snippet starts with ``snippet``."""
    [block] = [
        b for b in cfg.blocks.values()
        if any(s.code_snippet.startswith(snippet) for s in b.statements)
    ]
    return block


def _succ(cfg: FunctionCfg, block_id: str) -> set[str]:
    return {e.target_block for e in cfg.blocks[block_id].successors}


def _reachable(cfg: FunctionCfg, start: str) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        bid = stack.pop()
        if bid not in seen:
            seen.add(bid)
            stack.extend(_succ(cfg, bid))
    return seen


def _set(cfg: FunctionCfg, snippet: str, defines: list[str], uses: list[str]) -> None:
    for s in _stmts(cfg):
        if s.code_snippet.startswith(snippet):
            s.defines, s.uses = defines, uses


def _edges(cfg: FunctionCfg) -> set[tuple[str, int, int]]:
    return {(e.variable, e.def_line, e.use_line) for e in solve_reaching_defs(cfg).ddg_edges}


# ---------------------------------------------------------------------------
# WI-losod: headers and initializers are statements
# ---------------------------------------------------------------------------


class TestGoHeaders:
    def test_if_initializer_is_recorded_before_the_condition(self) -> None:
        cfg, _, _ = _build("go", (
            "package main\n"
            "func f(cmd *Cmd) {\n"
            "\tif err := cmd.Run(); err != nil {\n"
            "\t\tlog(err)\n"
            "\t}\n"
            "}\n"
        ))
        block = _block_of(cfg, "err := cmd.Run()")
        assert [s.node_type for s in block.statements] == [
            "short_var_declaration", "binary_expression",
        ]

    def test_for_clause_initializer_runs_once_and_update_runs_after_the_body(self) -> None:
        cfg, _, _ = _build("go", (
            "package main\n"
            "func f(n int) {\n"
            "\tfor j := 0; j < n; j++ {\n"
            "\t\tif j > 2 {\n"
            "\t\t\tcontinue\n"
            "\t\t}\n"
            "\t\tuse(j)\n"
            "\t}\n"
            "}\n"
        ))
        init = _block_of(cfg, "j := 0")
        header = _block_of(cfg, "j < n")
        update = _block_of(cfg, "j++")
        body_use = _block_of(cfg, "use(j)")
        cont = _block_of(cfg, "continue")
        assert init.id == cfg.entry_block
        assert _succ(cfg, init.id) == {header.id}
        assert _succ(cfg, update.id) == {header.id}
        # The body's end and a `continue` both run the update, never skip it.
        assert update.id in _succ(cfg, body_use.id)
        assert _succ(cfg, cont.id) == {update.id}
        # The initializer is not re-run by the back-edge.
        assert init.id not in _reachable(cfg, header.id)

    def test_range_clause_binds_in_the_header_each_iteration(self) -> None:
        cfg, _, _ = _build("go", (
            "package main\n"
            "func f() {\n"
            "\tfor _, e := range os.Environ() {\n"
            "\t\tfmt.Println(e)\n"
            "\t}\n"
            "}\n"
        ))
        header = _block_of(cfg, "_, e := range")
        assert [s.node_type for s in header.statements] == ["range_clause"]
        assert header.id == cfg.entry_block
        assert header.id in _succ(cfg, _block_of(cfg, "fmt.Println(e)").id)

    def test_bare_condition_has_no_field_and_is_still_recorded(self) -> None:
        cfg, _, _ = _build("go", (
            "package main\n"
            "func f(n int) {\n"
            "\tfor n > 0 {\n"
            "\t\tn--\n"
            "\t}\n"
            "}\n"
        ))
        header = _block_of(cfg, "n > 0")
        assert [s.node_type for s in header.statements] == ["binary_expression"]

    def test_infinite_for_has_an_empty_header(self) -> None:
        cfg, _, _ = _build("go", (
            "package main\n"
            "func f() {\n"
            "\tfor {\n"
            "\t\tstep()\n"
            "\t}\n"
            "}\n"
        ))
        header = cfg.blocks[cfg.entry_block]
        assert header.statements == []
        assert [s.code_snippet for s in _stmts(cfg)] == ["step()"]

    def test_switch_initializer_is_recorded_and_not_mistaken_for_an_arm(self) -> None:
        cfg, _, _ = _build("go", (
            "package main\n"
            "func f(a int) {\n"
            "\tswitch x := g(a); h(x) {\n"
            "\tcase 1:\n"
            "\t\tuse(x)\n"
            "\t}\n"
            "}\n"
        ))
        scrutinee = cfg.blocks[cfg.entry_block]
        assert [s.code_snippet for s in scrutinee.statements] == ["x := g(a)", "h(x)"]
        # Only the real case is an arm: neither header part became one.
        assert [e.edge_type for e in scrutinee.successors] == ["case"]
        snippets = [s.code_snippet for s in _stmts(cfg)]
        assert snippets.count("h(x)") == 1 and "g" not in snippets

    def test_if_initializer_definition_reaches_the_condition_and_the_branch(self) -> None:
        cfg, _, _ = _build("go", (
            "package main\n"
            "func f(cmd *Cmd) {\n"
            "\tif err := cmd.Run(); err != nil {\n"
            "\t\tlog(err)\n"
            "\t}\n"
            "}\n"
        ))
        _set(cfg, "err := cmd.Run()", ["err"], ["cmd"])
        _set(cfg, "err != nil", [], ["err"])
        _set(cfg, "log(err)", [], ["err"])
        assert _edges(cfg) == {("err", 3, 3), ("err", 3, 4)}


class TestClassicForInOtherLanguages:
    def test_c_initializer_is_one_statement_even_when_it_is_a_bare_expression(self) -> None:
        cfg, _, _ = _build("c", (
            "void f(int n) {\n"
            "  int i;\n"
            "  for (i = 0; i < n; i++) {\n"
            "    use(i);\n"
            "  }\n"
            "}\n"
        ))
        types = [s.node_type for s in _stmts(cfg)]
        # `i = 0` is an assignment_expression, which c.yaml's atomic list does
        # not name; routed through the ordinary path it would be decomposed.
        assert "assignment_expression" in types
        assert "update_expression" in types
        assert "identifier" not in types and "number_literal" not in types

    def test_java_records_every_init_and_update_field(self) -> None:
        cfg, _, _ = _build("java", (
            "class A {\n"
            "  void f(int n) {\n"
            "    for (i = 0, j = 0; i < n; i++, j++) {\n"
            "      use(i, j);\n"
            "    }\n"
            "  }\n"
            "}\n"
        ))
        init = _block_of(cfg, "i = 0")
        update = _block_of(cfg, "i++")
        assert [s.code_snippet for s in init.statements] == ["i = 0", "j = 0"]
        assert [s.code_snippet for s in update.statements] == ["i++", "j++"]

    def test_typescript_names_its_update_increment(self) -> None:
        cfg, _, _ = _build("typescript", (
            "function f(n) {\n"
            "  for (let k = 0; k < n; k++) {\n"
            "    use(k);\n"
            "  }\n"
            "}\n"
        ))
        assert _block_of(cfg, "k++").statements[0].node_type == "update_expression"
        assert _block_of(cfg, "let k = 0").id == cfg.entry_block

    def test_empty_body_still_runs_the_update(self) -> None:
        cfg, _, _ = _build("c", (
            "void f(int n) {\n"
            "  for (int i = 0; i < n; i++) {}\n"
            "}\n"
        ))
        header = _block_of(cfg, "i < n")
        update = _block_of(cfg, "i++")
        assert update.id in _succ(cfg, header.id)
        assert header.id in _succ(cfg, update.id)


class TestHeaderRecordedAtTheLoopNode:
    """Loops whose binding has no node of its own (WI-losod)."""

    @pytest.mark.parametrize("lang, source, loop_type", [
        ("python", "def f(xs):\n    for v in xs:\n        use(v)\n", "for_statement"),
        ("rust", "fn f() {\n    for v in xs {\n        use_(v);\n    }\n}\n", "for_expression"),
        ("typescript", "function f(xs) {\n  for (const v of xs) {\n    use(v);\n  }\n}\n",
         "for_in_statement"),
        ("java", "class A {\n  void f() {\n    for (String v : xs) {\n      use(v);\n    }\n  }\n}\n",
         "enhanced_for_statement"),
    ])
    def test_header_statement_stands_for_the_header_only(
        self, lang: str, source: str, loop_type: str,
    ) -> None:
        cfg, body, _ = _build(lang, source)
        [header] = [s for s in _stmts(cfg) if s.node_type == loop_type]
        loop = body.named_children[0]
        if loop.type != loop_type:  # rust wraps it in an expression_statement
            loop = loop.named_children[0]
        assert loop.type == loop_type
        assert header.extent_end_byte == loop.child_by_field_name("body").start_byte
        assert cfg.blocks[cfg.entry_block].statements == [header]

    def test_the_cut_keeps_unrecorded_code_after_the_header_uncovered(self) -> None:
        """Python's ``for ... else:`` is not processed by the loop hook. With the
        header covering its whole node the else clause would read as covered,
        and the gate that forfeits refutation would be silent."""
        cfg, body, src = _build("python", (
            "def f(xs, t):\n"
            "    for v in xs:\n"
            "        use(v)\n"
            "    else:\n"
            "        sink(t)\n"
        ))
        mapping = load_cfg_mapping("python")
        assert mapping is not None
        # Line 4 (``else:``) holds no named leaf; line 5 is the clause's body.
        assert uncovered_semantic_lines(cfg, body, src, mapping) == frozenset({5})

    def test_the_cut_keeps_the_header_from_accounting_for_the_body(self) -> None:
        """A body statement that mentions ``v`` without accounting for it is
        reported, even though the enclosing loop node's header statement
        defines ``v``: the header covers the header, not the body."""
        cfg, body, src = _build("python", (
            "def f(xs):\n"
            "    for v in xs:\n"
            "        use(v)\n"
        ))
        _set(cfg, "for v in xs", ["v"], ["xs"])
        _set(cfg, "use(v)", [], [])
        assert unaccounted_names(cfg, body, src) == frozenset({"v"})
        _set(cfg, "use(v)", [], ["v"])
        assert unaccounted_names(cfg, body, src) == frozenset()

    def test_header_definition_reaches_the_body(self) -> None:
        cfg, _, _ = _build("python", "def f(xs):\n    for v in xs:\n        use(v)\n")
        _set(cfg, "for v in xs", ["v"], ["xs"])
        _set(cfg, "use(v)", [], ["v"])
        assert _edges(cfg) == {("v", 2, 3)}


# ---------------------------------------------------------------------------
# WI-faful: an atomic statement stays one statement
# ---------------------------------------------------------------------------


class TestControlFlowInsideAValue:
    def test_java_switch_expression_runs_first_then_the_declaration(self) -> None:
        cfg, _, _ = _build("java", (
            "class A {\n"
            "  void f(int x) {\n"
            "    int r = switch (x) { case 1 -> 2; default -> 3; };\n"
            "    use(r);\n"
            "  }\n"
            "}\n"
        ))
        types = [s.node_type for s in _stmts(cfg)]
        assert "identifier" not in types and "integral_type" not in types
        decl = _block_of(cfg, "int r = switch")
        scrutinee = cfg.blocks[cfg.entry_block]
        assert [e.edge_type for e in scrutinee.successors] == ["case", "case"]
        # Both arms flow into the declaration, which flows on to the use.
        for arm_edge in scrutinee.successors:
            assert decl.id in _reachable(cfg, arm_edge.target_block)
        assert _block_of(cfg, "use(r)").id in _succ(cfg, decl.id)

    def test_rust_let_if_defines_after_both_branches(self) -> None:
        cfg, _, _ = _build("rust", (
            "fn f(c: bool, a: i32) {\n"
            "    let x = if c { a } else { 0 };\n"
            "    use_(x);\n"
            "}\n"
        ))
        decl = _block_of(cfg, "let x = if c")
        cond = cfg.blocks[cfg.entry_block]
        assert {e.edge_type for e in cond.successors} == {"true", "false"}
        for edge in cond.successors:
            assert decl.id in _succ(cfg, edge.target_block)

    def test_rust_let_try_defines_on_the_ok_path_only(self) -> None:
        cfg, _, _ = _build("rust", (
            "fn f() -> Result<(), E> {\n"
            "    let k = std::env::var(\"K\")?;\n"
            "    use_(k);\n"
            "    Ok(())\n"
            "}\n"
        ))
        try_block = cfg.blocks[cfg.entry_block]
        assert [s.node_type for s in try_block.statements] == ["try_expression"]
        decl = _block_of(cfg, "let k =")
        assert {(e.target_block, e.edge_type) for e in try_block.successors} == {
            (cfg.exit_block, "false"), (decl.id, "true"),
        }

    def test_a_statement_that_is_nothing_but_the_branch_adds_no_statement(self) -> None:
        cfg, _, _ = _build("rust", (
            "fn f(c: bool) {\n"
            "    if c { go1(); } else { go2(); }\n"
            "}\n"
        ))
        assert "expression_statement" not in {
            s.node_type for s in _stmts(cfg) if s.code_snippet.startswith("if")
        }
        assert [s.code_snippet for s in _stmts(cfg)] == ["c", "go1();", "go2();"]


class TestNestedFunctions:
    def test_java_lambda_declaration_is_one_statement(self) -> None:
        cfg, _, _ = _build("java", (
            "class A {\n"
            "  void f(int x) {\n"
            "    Runnable q = () -> { if (x > 0) { go(); } };\n"
            "    q.run();\n"
            "  }\n"
            "}\n"
        ))
        types = [s.node_type for s in _stmts(cfg)]
        assert "identifier" not in types and "type_identifier" not in types
        assert types.count("local_variable_declaration") == 1

    def test_body_is_a_region_that_may_run_and_rejoins(self) -> None:
        cfg, _, _ = _build("go", (
            "package main\n"
            "func f(c bool) {\n"
            "\tt := source()\n"
            "\tfunc() {\n"
            "\t\tt := \"constant\"\n"
            "\t\tif c {\n"
            "\t\t\t_ = t\n"
            "\t\t}\n"
            "\t}()\n"
            "\tsink(t)\n"
            "}\n"
        ))
        holder = _block_of(cfg, "func() {")
        inner = _block_of(cfg, 't := "constant"')
        after = _block_of(cfg, "sink(t)")
        # Skip edge: the statement after is reachable WITHOUT entering the body.
        assert after.id in _succ(cfg, holder.id)
        assert inner.id in _succ(cfg, holder.id)
        # The body rejoins, and may repeat.
        region_exit = [b for b in cfg.blocks.values() if after.id in _succ(cfg, b.id)
                       and b.id != holder.id]
        assert len(region_exit) == 1 and inner.id in _succ(cfg, region_exit[0].id)

    def test_a_closure_binding_can_no_longer_kill_the_outer_one(self) -> None:
        """The defect the inlined body caused: ``t := "constant"`` inside the
        literal KILLED the outer ``t``, so the tainted value never reached the
        later use. Now both definitions reach it."""
        cfg, _, _ = _build("go", (
            "package main\n"
            "func f() {\n"
            "\tt := source()\n"
            "\tgo func() {\n"
            "\t\tt := \"constant\"\n"
            "\t\tif t != \"\" {\n"
            "\t\t\treturn\n"
            "\t\t}\n"
            "\t}()\n"
            "\tsink(t)\n"
            "}\n"
        ))
        _set(cfg, "t := source()", ["t"], [])
        _set(cfg, 't := "constant"', ["t"], [])
        _set(cfg, "sink(t)", [], ["t"])
        assert {(v, d) for v, d, u in _edges(cfg) if u == 10} == {("t", 3), ("t", 5)}

    def test_return_break_and_defer_inside_the_body_stay_inside_it(self) -> None:
        cfg, _, _ = _build("go", (
            "package main\n"
            "func f(xs []int) {\n"
            "\tfor _, x := range xs {\n"
            "\t\th := func() {\n"
            "\t\t\tdefer cleanup()\n"
            "\t\t\tfor {\n"
            "\t\t\t\tbreak\n"
            "\t\t\t}\n"
            "\t\t\treturn\n"
            "\t\t}\n"
            "\t\th()\n"
            "\t}\n"
            "}\n"
        ))
        # The literal's `defer` runs at the literal's exit, not the function's.
        assert cfg.blocks[cfg.exit_block].statements == []
        [deferred] = [s for s in _stmts(cfg) if s.node_type == "deferred_call"]
        ret = _block_of(cfg, "return")
        assert cfg.exit_block not in _succ(cfg, ret.id)
        [region_exit] = list(_succ(cfg, ret.id))
        assert deferred in cfg.blocks[region_exit].statements
        # The `break` binds to the literal's own loop: the range loop's header
        # is not where it lands.
        brk = _block_of(cfg, "break")
        header = _block_of(cfg, "_, x := range xs")
        assert header.id not in _succ(cfg, brk.id)

    def test_nested_def_met_directly_is_a_statement_with_a_region(self) -> None:
        cfg, _, _ = _build("python", (
            "def f(c):\n"
            "    def helper():\n"
            "        if c:\n"
            "            return 1\n"
            "    helper()\n"
        ))
        holder = _block_of(cfg, "def helper")
        assert holder.statements[0].node_type == "function_definition"
        assert len(_succ(cfg, holder.id)) == 2  # skip, and into the body

    def test_an_empty_body_attaches_nothing_and_leaves_no_block(self) -> None:
        cfg, _, _ = _build("typescript", (
            "function f(xs) {\n"
            "  xs.forEach(() => {});\n"
            "  done();\n"
            "}\n"
        ))
        holder = _block_of(cfg, "xs.forEach")
        assert _succ(cfg, holder.id) == {_block_of(cfg, "done()").id}
        assert all(b.statements or b.id == cfg.exit_block for b in cfg.blocks.values())

    def test_a_branch_inside_a_lambda_does_not_decompose_the_statement(self) -> None:
        """Before WI-faful, ``xs.forEach(x -> { if (x) ... })`` became leaves:
        ``xs``, ``forEach``, ``x`` -- the call itself was never a statement."""
        cfg, _, _ = _build("java", (
            "class A {\n"
            "  void f(java.util.List<String> xs) {\n"
            "    xs.forEach(y -> { if (y != null) { sink(y); } });\n"
            "  }\n"
            "}\n"
        ))
        first = cfg.blocks[cfg.entry_block].statements[0]
        assert first.node_type == "expression_statement"
        assert first.code_snippet.startswith("xs.forEach(")


class TestSwitchArms:
    def test_java_arrow_rules_are_arms_and_code_after_the_switch_is_reachable(self) -> None:
        cfg, _, _ = _build("java", (
            "class A {\n"
            "  void f(int x) {\n"
            "    switch (x) { case 1 -> a(); default -> b(); }\n"
            "    after();\n"
            "  }\n"
            "}\n"
        ))
        assert _block_of(cfg, "after()").id in _reachable(cfg, cfg.entry_block)
        assert [e.edge_type for e in cfg.blocks[cfg.entry_block].successors] == [
            "case", "case",
        ]

    def test_a_switch_with_no_recognised_arm_still_falls_through(self) -> None:
        mapping = load_cfg_mapping("java")
        assert mapping is not None
        narrowed = CfgNodeMapping(
            language="java", grammar="",
            switch=[SwitchMapping(
                node_type="switch_expression", scrutinee_child="field:condition",
                arms_child="field:body", arm_type="no_such_arm",
            )],
            atomic_statements=mapping.atomic_statements,
            call_node_types=mapping.call_node_types,
        )
        body, src = _outer_body("java", (
            "class A {\n"
            "  void f(int x) {\n"
            "    switch (x) { case 1 -> a(); }\n"
            "    after();\n"
            "  }\n"
            "}\n"
        ))
        cfg = CfgBuilder(narrowed, "java:t:1-9:f:method").build(body, src)
        assert _block_of(cfg, "after()").id in _reachable(cfg, cfg.entry_block)


# ---------------------------------------------------------------------------
# Mapping plumbing
# ---------------------------------------------------------------------------


class TestMappingPlumbing:
    def test_new_keys_parse(self) -> None:
        mapping = _parse_cfg_mapping({
            "language": "x",
            "conditional": [{
                "node_type": "if", "condition_child": "c", "true_child": "t",
                "initializer_child": "field:initializer",
            }],
            "loop": [{
                "node_type": "for", "body_child": "b", "initializer_child": "i",
                "update_child": "u", "header_child": "h", "header_is_node": True,
            }],
            "switch": [{
                "node_type": "sw", "scrutinee_child": "s", "arm_type": ["a", "b"],
                "initializer_child": "i",
            }],
            "nested_function": [{"node_type": "lambda", "body_child": "field:body"}],
        })
        assert mapping.conditionals[0].initializer_child == "field:initializer"
        loop = mapping.loops[0]
        assert (loop.initializer_child, loop.update_child, loop.header_child,
                loop.header_is_node) == ("i", "u", "h", True)
        assert mapping.switch[0].arm_types() == frozenset({"a", "b"})
        assert mapping.switch[0].initializer_child == "i"
        assert mapping.nested_functions == [NestedFunctionMapping("lambda", "field:body")]
        assert mapping.get_nested_function("lambda") is mapping.nested_functions[0]
        assert mapping.get_nested_function("if") is None

    def test_arm_types_shapes(self) -> None:
        assert SwitchMapping("s", "c").arm_types() is None
        assert SwitchMapping("s", "c", arm_type="a").arm_types() == frozenset({"a"})

    def test_nested_function_is_not_a_control_flow_category(self) -> None:
        mapping = load_cfg_mapping("go")
        assert mapping is not None
        assert mapping.get_nested_function("func_literal") is not None
        assert mapping.classify("func_literal") is None


class TestChildReferences:
    def _for(self, source: str) -> Any:
        body, _ = _outer_body("go", "package main\nfunc f() {\n" + source + "\n}\n")
        return body.named_children[0].named_children[0]

    def test_path_descends(self) -> None:
        loop = self._for("for i := 0; i < n; i++ {}")
        found = CfgBuilder._find_child(loop, "type:for_clause/field:initializer")
        assert found is not None and found.type == "short_var_declaration"

    def test_path_stops_when_a_step_misses(self) -> None:
        loop = self._for("for x < n {}")
        assert CfgBuilder._find_child(loop, "type:for_clause/field:initializer") is None

    def test_alternatives_take_the_first_that_resolves(self) -> None:
        ref = "type:for_clause/field:condition|unfielded:except=for_clause,range_clause"
        bare = CfgBuilder._find_child(self._for("for x < n {}"), ref)
        clause = CfgBuilder._find_child(self._for("for i := 0; i < n; i++ {}"), ref)
        assert bare is not None and bare.type == "binary_expression"
        assert clause is not None and clause.type == "binary_expression"

    def test_unfielded_respects_its_exclusions(self) -> None:
        ref = "unfielded:except=for_clause,range_clause"
        assert CfgBuilder._find_child(self._for("for i := 0; ; i++ {}"), ref) is None
        assert CfgBuilder._find_child(self._for("for range ch {}"), ref) is None
        assert CfgBuilder._find_child(
            self._for("for range ch {}"), "unfielded",
        ).type == "range_clause"

    def test_a_field_reference_collects_every_repeat(self) -> None:
        body, _ = _outer_body("java", (
            "class A { void f() { for (i = 0, j = 0; ; ) {} } }"
        ))
        loop = body.named_children[0]
        assert len(CfgBuilder._find_children(loop, "field:init")) == 2
        assert CfgBuilder._find_child(loop, "field:init").type == "assignment_expression"
