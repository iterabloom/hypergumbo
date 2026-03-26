# SPDX-License-Identifier: AGPL-3.0-or-later
"""Language-parameterized CFG builder using fringe-based recursive algorithm (ADR-0017 §1a).

Builds intraprocedural control flow graphs from tree-sitter ASTs using YAML-driven
node mappings that translate language-specific tree-sitter node types into generic
control-flow categories.

How It Works
------------
1. ``load_cfg_mapping()`` reads a per-language YAML file that maps tree-sitter
   node types to control-flow categories: conditional, loop, break, continue,
   return, try/catch, switch/match, and semantic hooks.

2. ``build_function_cfg()`` walks the tree-sitter AST for a function body and
   produces a ``FunctionCfg`` using the fringe-based recursive algorithm (same
   algorithm as Joern's CfgCreator.scala):

   - For each AST subtree, produce a partial CFG with an entry node, finalized
     edges, and a **fringe** (pending outgoing edges whose destination is not
     yet known).
   - Sequential composition: connect A's fringe to B's entry.
   - Conditional (if/else): split fringe into true/false edges; merge branches.
   - Loops (while/for): back-edge from body's fringe to condition entry.
   - break/continue: collect with nesting level; resolve at enclosing loop.
   - return/throw: wire to function exit block; produce empty fringe.
   - try/catch/finally: try body's fringe connects to all catch entries.
   - switch/match: edges from scrutinee to each case entry.

3. **Semantic hooks** handle non-standard control flow that cannot be reduced
   to the standard categories via YAML mapping alone:

   - ``early_return_on_error``: Dual control flow (Rust ``?`` operator) —
     Ok path continues, Err path exits function.
   - ``context_manager``: Entry call before body, exit call after body including
     exceptional paths (Python ``with``).
   - ``deferred_execution``: Statement body executes at function exit, after
     all subsequent statements (Go ``defer``).

4. **Unmapped node types** are treated as sequential statements (safe
   overapproximation). A warning is logged at DEBUG level listing unmapped types.

Architecture
------------
The CFG builder is language-parameterized, not language-specific. All language
differences are handled by YAML node mappings in ``cfg_nodes/``. The builder
itself contains no hardcoded language names — it implements a finite set of
generic control-flow patterns that the YAML mappings select.

The ``CfgStatement.defines`` and ``CfgStatement.uses`` lists are populated by
pluggable def/use extractors (ADR-0017 §1c) in a separate pass. The CFG builder
leaves them empty; it focuses solely on control-flow structure.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model (ADR-0017 §1a)
# ---------------------------------------------------------------------------


@dataclass
class CfgStatement:
    """A single statement or expression within a basic block.

    Represents one AST node that produces an observable effect (assignment,
    call, return, etc.). The ``defines`` and ``uses`` fields are populated
    by def/use extractors in a later pass — the CFG builder leaves them
    empty, focusing only on control-flow structure.
    """

    line: int
    col: int
    node_type: str
    code_snippet: str
    defines: list[str] = field(default_factory=list)
    uses: list[str] = field(default_factory=list)
    call_target: Optional[str] = None


@dataclass
class CfgEdge:
    """A control flow edge between basic blocks.

    ``edge_type`` classifies the edge:
    - ``always``: unconditional fall-through
    - ``true``/``false``: conditional branch
    - ``case``: switch/match arm
    - ``exception``: try → catch handler
    """

    target_block: str
    edge_type: Literal["always", "true", "false", "case", "exception"]


@dataclass
class BasicBlock:
    """A straight-line sequence of statements with no internal branches.

    Each block has a unique ``id`` within its function CFG (e.g., ``bb_0``),
    an ordered list of statements, and outgoing edges to successor blocks.
    """

    id: str
    symbol_id: str
    statements: list[CfgStatement] = field(default_factory=list)
    successors: list[CfgEdge] = field(default_factory=list)


@dataclass
class FunctionCfg:
    """Complete CFG for one function.

    ``entry_block`` is the ID of the first basic block. ``exit_block`` is
    a synthetic block representing function return — all return/throw
    statements wire to it. ``blocks`` maps block IDs to BasicBlock objects.
    """

    symbol_id: str
    entry_block: str
    exit_block: str
    blocks: dict[str, BasicBlock] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# YAML CFG node mapping (ADR-0017 §1d)
# ---------------------------------------------------------------------------


@dataclass
class ConditionalMapping:
    """Maps a tree-sitter node type to an if/else-style conditional."""

    node_type: str
    condition_child: str
    true_child: str
    false_child: Optional[str] = None


@dataclass
class LoopMapping:
    """Maps a tree-sitter node type to a loop construct."""

    node_type: str
    body_child: str
    condition_child: Optional[str] = None
    infinite: bool = False


@dataclass
class TryCatchMapping:
    """Maps a tree-sitter node type to a try/catch/finally construct."""

    node_type: str
    body_child: str
    catch_child: Optional[str] = None
    finally_child: Optional[str] = None
    catch_clause_type: Optional[str] = None


@dataclass
class EarlyReturnMapping:
    """Maps a node type to a dual control-flow construct (e.g., Rust ?)."""

    node_type: str
    semantics: str
    ok_defines: bool = True
    err_edge: str = "exit"


@dataclass
class ContextManagerMapping:
    """Maps a node type to a context manager construct (e.g., Python with)."""

    node_type: str
    value_child: str
    body_child: str
    alias_child: Optional[str] = None


@dataclass
class DeferredMapping:
    """Maps a node type to a deferred execution construct (e.g., Go defer)."""

    node_type: str
    body_child: str


@dataclass
class SwitchMapping:
    """Maps a tree-sitter node type to a switch/match construct."""

    node_type: str
    scrutinee_child: str
    arms_child: str
    arm_type: Optional[str] = None


@dataclass
class CfgNodeMapping:
    """Complete CFG node mapping for a language.

    Loaded from ``cfg_nodes/<language>.yaml``. Maps tree-sitter node types
    to generic control-flow categories that the CFG builder understands.
    """

    language: str
    grammar: str
    conditionals: list[ConditionalMapping] = field(default_factory=list)
    loops: list[LoopMapping] = field(default_factory=list)
    break_statements: list[str] = field(default_factory=list)
    continue_statements: list[str] = field(default_factory=list)
    return_statements: list[str] = field(default_factory=list)
    try_catch: list[TryCatchMapping] = field(default_factory=list)
    early_return: list[EarlyReturnMapping] = field(default_factory=list)
    context_manager: list[ContextManagerMapping] = field(default_factory=list)
    deferred: list[DeferredMapping] = field(default_factory=list)
    switch: list[SwitchMapping] = field(default_factory=list)

    def classify(self, node_type: str) -> Optional[str]:
        """Return the control-flow category for a tree-sitter node type.

        Returns one of: 'conditional', 'loop', 'break', 'continue', 'return',
        'try_catch', 'early_return', 'context_manager', 'deferred', 'switch',
        or None if the node type is unmapped (treated as sequential).
        """
        for c in self.conditionals:
            if c.node_type == node_type:
                return "conditional"
        for lo in self.loops:
            if lo.node_type == node_type:
                return "loop"
        if node_type in self.break_statements:
            return "break"
        if node_type in self.continue_statements:
            return "continue"
        if node_type in self.return_statements:
            return "return"
        for tc in self.try_catch:
            if tc.node_type == node_type:
                return "try_catch"
        for er in self.early_return:
            if er.node_type == node_type:
                return "early_return"
        for cm in self.context_manager:
            if cm.node_type == node_type:
                return "context_manager"
        for d in self.deferred:
            if d.node_type == node_type:
                return "deferred"
        for s in self.switch:
            if s.node_type == node_type:
                return "switch"
        return None

    def get_conditional(self, node_type: str) -> Optional[ConditionalMapping]:
        """Return the conditional mapping for a node type, if any."""
        for c in self.conditionals:
            if c.node_type == node_type:
                return c
        return None

    def get_loop(self, node_type: str) -> Optional[LoopMapping]:
        """Return the loop mapping for a node type, if any."""
        for lo in self.loops:
            if lo.node_type == node_type:
                return lo
        return None

    def get_try_catch(self, node_type: str) -> Optional[TryCatchMapping]:
        """Return the try/catch mapping for a node type, if any."""
        for tc in self.try_catch:
            if tc.node_type == node_type:
                return tc
        return None

    def get_early_return(self, node_type: str) -> Optional[EarlyReturnMapping]:
        """Return the early return mapping for a node type, if any."""
        for er in self.early_return:
            if er.node_type == node_type:
                return er
        return None

    def get_context_manager(self, node_type: str) -> Optional[ContextManagerMapping]:
        """Return the context manager mapping for a node type, if any."""
        for cm in self.context_manager:
            if cm.node_type == node_type:
                return cm
        return None

    def get_deferred(self, node_type: str) -> Optional[DeferredMapping]:
        """Return the deferred mapping for a node type, if any."""
        for d in self.deferred:
            if d.node_type == node_type:
                return d
        return None

    def get_switch(self, node_type: str) -> Optional[SwitchMapping]:
        """Return the switch mapping for a node type, if any."""
        for s in self.switch:
            if s.node_type == node_type:
                return s
        return None


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

_MAPPING_CACHE: dict[str, CfgNodeMapping] = {}


def get_cfg_nodes_dir() -> Path:
    """Return the path to the cfg_nodes/ directory."""
    return Path(__file__).parent / "cfg_nodes"


def load_cfg_mapping(language: str, search_dir: Optional[Path] = None) -> Optional[CfgNodeMapping]:
    """Load a CFG node mapping for the given language.

    Searches ``cfg_nodes/<language>.yaml`` in the package directory or
    ``search_dir`` if provided. Returns None if no mapping file exists.
    Caches results to avoid repeated disk I/O.
    """
    cache_key = f"{search_dir or 'default'}:{language}"
    if cache_key in _MAPPING_CACHE:
        return _MAPPING_CACHE[cache_key]

    search = search_dir or get_cfg_nodes_dir()
    yaml_path = search / f"{language}.yaml"
    if not yaml_path.exists():
        _MAPPING_CACHE[cache_key] = None  # type: ignore[assignment]
        return None

    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    mapping = _parse_cfg_mapping(data)
    _MAPPING_CACHE[cache_key] = mapping
    return mapping


def clear_cfg_mapping_cache() -> None:
    """Clear the mapping cache (useful for tests)."""
    _MAPPING_CACHE.clear()


def _parse_cfg_mapping(data: dict[str, Any]) -> CfgNodeMapping:
    """Parse a raw YAML dict into a CfgNodeMapping."""
    conditionals = [
        ConditionalMapping(
            node_type=c["node_type"],
            condition_child=c["condition_child"],
            true_child=c["true_child"],
            false_child=c.get("false_child"),
        )
        for c in data.get("conditional", [])
    ]

    loops = [
        LoopMapping(
            node_type=lo["node_type"],
            body_child=lo["body_child"],
            condition_child=lo.get("condition_child"),
            infinite=lo.get("infinite", False),
        )
        for lo in data.get("loop", [])
    ]

    try_catch = [
        TryCatchMapping(
            node_type=tc["node_type"],
            body_child=tc["body_child"],
            catch_child=tc.get("catch_child"),
            finally_child=tc.get("finally_child"),
            catch_clause_type=tc.get("catch_clause_type"),
        )
        for tc in data.get("try_catch", [])
    ]

    early_return = [
        EarlyReturnMapping(
            node_type=er["node_type"],
            semantics=er["semantics"],
            ok_defines=er.get("ok_defines", True),
            err_edge=er.get("err_edge", "exit"),
        )
        for er in data.get("early_return", [])
    ]

    context_manager = [
        ContextManagerMapping(
            node_type=cm["node_type"],
            value_child=cm["value_child"],
            body_child=cm["body_child"],
            alias_child=cm.get("alias_child"),
        )
        for cm in data.get("context_manager", [])
    ]

    deferred = [
        DeferredMapping(
            node_type=d["node_type"],
            body_child=d["body_child"],
        )
        for d in data.get("deferred", [])
    ]

    switch = [
        SwitchMapping(
            node_type=s["node_type"],
            scrutinee_child=s["scrutinee_child"],
            arms_child=s["arms_child"],
            arm_type=s.get("arm_type"),
        )
        for s in data.get("switch", [])
    ]

    return CfgNodeMapping(
        language=data["language"],
        grammar=data.get("grammar", ""),
        conditionals=conditionals,
        loops=loops,
        break_statements=data.get("break_statement", []),
        continue_statements=data.get("continue_statement", []),
        return_statements=data.get("return_statement", []),
        try_catch=try_catch,
        early_return=early_return,
        context_manager=context_manager,
        deferred=deferred,
        switch=switch,
    )


# ---------------------------------------------------------------------------
# Fringe-based CFG builder
# ---------------------------------------------------------------------------


@dataclass
class _FringeEdge:
    """A pending outgoing edge whose destination is not yet known.

    ``edge_type`` is the label for when the edge is finalized.
    ``source_block`` is the block this edge originates from.
    """

    source_block: str
    edge_type: Literal["always", "true", "false", "case", "exception"]


@dataclass
class _PartialCfg:
    """Intermediate result from processing an AST subtree.

    ``entry_block`` is the first block to execute.
    ``fringe`` contains pending edges that need to be connected to the next
    block in sequential composition.
    ``blocks`` are the finalized blocks produced by this subtree.
    """

    entry_block: Optional[str]
    fringe: list[_FringeEdge]
    blocks: dict[str, BasicBlock]


class CfgBuilder:
    """Builds a FunctionCfg from a tree-sitter AST using YAML-driven node mappings.

    The builder walks the AST for a function body and produces basic blocks
    connected by control-flow edges. Language differences are handled by the
    ``CfgNodeMapping`` — the builder contains no hardcoded language names.

    Usage::

        mapping = load_cfg_mapping("python")
        builder = CfgBuilder(mapping, symbol_id="python:app.py:10-20:foo:function")
        cfg = builder.build(function_body_node, source_bytes)
    """

    def __init__(self, mapping: CfgNodeMapping, symbol_id: str) -> None:
        self._mapping = mapping
        self._symbol_id = symbol_id
        self._block_counter = 0
        self._blocks: dict[str, BasicBlock] = {}
        self._exit_block_id = ""
        self._unmapped_types: set[str] = set()
        # Break/continue targets, keyed by nesting level
        self._loop_stack: list[_LoopContext] = []
        # Deferred statements collected during traversal
        self._deferred_nodes: list[Any] = []

    def build(self, body_node: Any, source: bytes) -> FunctionCfg:
        """Build a complete FunctionCfg from a function body AST node.

        Args:
            body_node: The tree-sitter node for the function body (e.g.,
                the ``block`` child of a ``function_definition``).
            source: The source code as bytes (for extracting code snippets).

        Returns:
            A FunctionCfg with entry, exit, and all intermediate blocks.
        """
        self._block_counter = 0
        self._blocks = {}
        self._unmapped_types = set()
        self._loop_stack = []
        self._deferred_nodes = []

        exit_block = self._new_block()
        self._exit_block_id = exit_block.id

        partial = self._process_children(body_node, source)

        # Connect any remaining fringe to exit
        if partial is not None:
            self._connect_fringe(partial.fringe, exit_block.id)

        # Process deferred nodes — they execute at function exit
        if self._deferred_nodes:
            self._insert_deferred(exit_block, source)

        # Determine entry block
        entry_id = partial.entry_block if partial and partial.entry_block else exit_block.id

        if self._unmapped_types:
            logger.debug(
                "CFG builder (%s): unmapped tree-sitter node types: %s",
                self._symbol_id,
                sorted(self._unmapped_types),
            )

        return FunctionCfg(
            symbol_id=self._symbol_id,
            entry_block=entry_id,
            exit_block=exit_block.id,
            blocks=self._blocks,
        )

    # -------------------------------------------------------------------
    # Block management
    # -------------------------------------------------------------------

    def _new_block(self) -> BasicBlock:
        """Create a new basic block with a unique ID."""
        block_id = f"bb_{self._block_counter}"
        self._block_counter += 1
        block = BasicBlock(id=block_id, symbol_id=self._symbol_id)
        self._blocks[block_id] = block
        return block

    def _connect_fringe(self, fringe: list[_FringeEdge], target_block_id: str) -> None:
        """Connect all fringe edges to the given target block."""
        for fe in fringe:
            src_block = self._blocks[fe.source_block]
            src_block.successors.append(CfgEdge(
                target_block=target_block_id,
                edge_type=fe.edge_type,
            ))

    def _node_text(self, node: Any, source: bytes) -> str:
        """Extract source text for a node, truncated to 80 chars."""
        text = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        if len(text) > 80:
            text = text[:77] + "..."
        return text

    @staticmethod
    def _find_child(node: Any, ref: str) -> Optional[Any]:
        """Find a child node using a YAML child reference.

        References use a prefix to specify the lookup strategy:
        - ``field:name`` → ``node.child_by_field_name("name")``
        - ``type:name`` → first child with ``.type == "name"``
        - bare ``name`` (no prefix) → field lookup first, then type fallback
        """
        if ref.startswith("field:"):
            field_name = ref[6:]
            return node.child_by_field_name(field_name)
        elif ref.startswith("type:"):
            type_name = ref[5:]
            for child in node.children:
                if child.type == type_name:
                    return child
            return None
        else:
            # Bare name: try field first, then type
            result = node.child_by_field_name(ref)
            if result is not None:
                return result
            for child in node.children:
                if child.type == ref:
                    return child
            return None

    # -------------------------------------------------------------------
    # AST traversal
    # -------------------------------------------------------------------

    def _process_children(self, parent_node: Any, source: bytes) -> Optional[_PartialCfg]:
        """Process a sequence of children as sequential statements.

        This is the sequential composition rule: connect each child's fringe
        to the next child's entry.
        """
        result: Optional[_PartialCfg] = None

        for child in parent_node.children:
            child_partial = self._process_node(child, source)
            if child_partial is None:
                continue

            if result is None:
                result = child_partial
            else:
                # Sequential composition: connect result's fringe to child's entry
                if child_partial.entry_block is not None:
                    self._connect_fringe(result.fringe, child_partial.entry_block)
                    result.fringe = child_partial.fringe
                    result.blocks.update(child_partial.blocks)
                else:
                    # Child produced no blocks (e.g. empty partial)
                    result.blocks.update(child_partial.blocks)  # pragma: no cover

        return result

    def _process_node(self, node: Any, source: bytes) -> Optional[_PartialCfg]:
        """Process a single AST node and return its partial CFG.

        Dispatches to category-specific handlers based on the YAML mapping.
        Unmapped node types are treated as sequential statements.
        """
        category = self._mapping.classify(node.type)

        if category == "conditional":
            return self._process_conditional(node, source)
        elif category == "loop":
            return self._process_loop(node, source)
        elif category == "break":
            return self._process_break(node, source)
        elif category == "continue":
            return self._process_continue(node, source)
        elif category == "return":
            return self._process_return(node, source)
        elif category == "try_catch":
            return self._process_try_catch(node, source)
        elif category == "early_return":
            return self._process_early_return(node, source)
        elif category == "context_manager":
            return self._process_context_manager(node, source)
        elif category == "deferred":
            return self._process_deferred(node, source)
        elif category == "switch":
            return self._process_switch(node, source)
        else:
            return self._process_sequential(node, source)

    def _process_sequential(self, node: Any, source: bytes) -> Optional[_PartialCfg]:
        """Process a node as a sequential statement (no control flow).

        Creates a single basic block containing the statement. If the node
        is a punctuation token, comment, or other non-semantic node, returns
        None to skip it.
        """
        # Skip non-semantic nodes
        if node.type in ("comment", "{", "}", "(", ")", ";", ",", ":", "newline", "indent",
                         "dedent", "NEWLINE", "INDENT", "DEDENT"):
            return None
        # Skip keyword tokens that are part of larger constructs
        if node.is_named is False and node.child_count == 0:
            return None

        # If this node has named children, it may be a compound statement we
        # don't recognize. Recurse into its children to capture any control
        # flow hidden inside.
        if node.named_child_count > 0 and self._mapping.classify(node.type) is None:
            # Track unmapped compound types
            if node.type not in _SKIP_UNMAPPED_TYPES:
                self._unmapped_types.add(node.type)
            child_result = self._process_children(node, source)
            if child_result is not None:
                return child_result
            # Fall through to create a single-statement block if children
            # produced nothing

        block = self._new_block()
        stmt = CfgStatement(
            line=node.start_point[0] + 1,
            col=node.start_point[1],
            node_type=node.type,
            code_snippet=self._node_text(node, source),
        )
        block.statements.append(stmt)
        return _PartialCfg(
            entry_block=block.id,
            fringe=[_FringeEdge(source_block=block.id, edge_type="always")],
            blocks={block.id: block},
        )

    def _process_conditional(self, node: Any, source: bytes) -> Optional[_PartialCfg]:
        """Process an if/else-style conditional.

        Creates a condition block, then true/false branches. The fringe
        is the union of both branches' fringes (merge point).
        """
        mapping = self._mapping.get_conditional(node.type)
        if mapping is None:
            return self._process_sequential(node, source)  # pragma: no cover

        # Create condition block
        cond_block = self._new_block()
        cond_node = self._find_child(node, mapping.condition_child)
        if cond_node:
            cond_block.statements.append(CfgStatement(
                line=cond_node.start_point[0] + 1,
                col=cond_node.start_point[1],
                node_type=cond_node.type,
                code_snippet=self._node_text(cond_node, source),
            ))

        fringe: list[_FringeEdge] = []

        # True branch
        true_node = self._find_child(node, mapping.true_child)
        if true_node:
            true_partial = self._process_children(true_node, source)
            if true_partial and true_partial.entry_block:
                cond_block.successors.append(CfgEdge(
                    target_block=true_partial.entry_block,
                    edge_type="true",
                ))
                fringe.extend(true_partial.fringe)
            else:
                fringe.append(_FringeEdge(source_block=cond_block.id, edge_type="true"))
        else:
            fringe.append(_FringeEdge(source_block=cond_block.id, edge_type="true"))  # pragma: no cover

        # False branch (else)
        false_node = self._find_child(node, mapping.false_child) if mapping.false_child else None
        if false_node:
            false_partial = self._process_node(false_node, source)
            if false_partial and false_partial.entry_block:
                cond_block.successors.append(CfgEdge(
                    target_block=false_partial.entry_block,
                    edge_type="false",
                ))
                fringe.extend(false_partial.fringe)
            else:
                fringe.append(_FringeEdge(source_block=cond_block.id, edge_type="false"))  # pragma: no cover
        else:
            # No else branch — false edge goes to fringe (fall through)
            fringe.append(_FringeEdge(source_block=cond_block.id, edge_type="false"))

        return _PartialCfg(
            entry_block=cond_block.id,
            fringe=fringe,
            blocks={cond_block.id: cond_block},
        )

    def _process_loop(self, node: Any, source: bytes) -> Optional[_PartialCfg]:
        """Process a loop construct (while/for/loop).

        Creates a header block (condition for while, iterator for for,
        empty for infinite loop), a body, and back-edge from body to header.
        Break edges go to fringe; continue edges go to header.
        """
        mapping = self._mapping.get_loop(node.type)
        if mapping is None:
            return self._process_sequential(node, source)  # pragma: no cover

        header_block = self._new_block()

        # Condition (for while loops)
        cond_node = (
            self._find_child(node, mapping.condition_child)
            if mapping.condition_child
            else None
        )
        if cond_node:
            header_block.statements.append(CfgStatement(
                line=cond_node.start_point[0] + 1,
                col=cond_node.start_point[1],
                node_type=cond_node.type,
                code_snippet=self._node_text(cond_node, source),
            ))

        # Push loop context for break/continue resolution
        loop_ctx = _LoopContext(header_block_id=header_block.id)
        self._loop_stack.append(loop_ctx)

        # Process body
        body_node = self._find_child(node, mapping.body_child)
        body_partial: Optional[_PartialCfg] = None
        if body_node:
            body_partial = self._process_children(body_node, source)

        self._loop_stack.pop()

        fringe: list[_FringeEdge] = []

        if body_partial and body_partial.entry_block:
            if mapping.infinite:
                # Infinite loop: header → body unconditionally
                header_block.successors.append(CfgEdge(
                    target_block=body_partial.entry_block,
                    edge_type="always",
                ))
            else:
                # Conditional loop: header → body (true), header → fringe (false)
                header_block.successors.append(CfgEdge(
                    target_block=body_partial.entry_block,
                    edge_type="true",
                ))
                fringe.append(_FringeEdge(source_block=header_block.id, edge_type="false"))

            # Back-edge: body's fringe → header
            self._connect_fringe(body_partial.fringe, header_block.id)
        else:
            if not mapping.infinite:
                fringe.append(_FringeEdge(source_block=header_block.id, edge_type="false"))

        # Continue edges → header (back-edge)
        self._connect_fringe(loop_ctx.continue_edges, header_block.id)

        # Break edges → fringe (exit loop)
        fringe.extend(loop_ctx.break_edges)

        return _PartialCfg(
            entry_block=header_block.id,
            fringe=fringe,
            blocks={header_block.id: header_block},
        )

    def _process_break(self, node: Any, source: bytes) -> Optional[_PartialCfg]:
        """Process a break statement.

        Creates a statement block and adds a break edge to the innermost
        loop context. Returns empty fringe (break is a jump).
        """
        block = self._new_block()
        block.statements.append(CfgStatement(
            line=node.start_point[0] + 1,
            col=node.start_point[1],
            node_type=node.type,
            code_snippet=self._node_text(node, source),
        ))

        if self._loop_stack:
            self._loop_stack[-1].break_edges.append(
                _FringeEdge(source_block=block.id, edge_type="always")
            )

        return _PartialCfg(
            entry_block=block.id,
            fringe=[],  # break terminates normal flow
            blocks={block.id: block},
        )

    def _process_continue(self, node: Any, source: bytes) -> Optional[_PartialCfg]:
        """Process a continue statement.

        Creates a statement block and adds a continue edge to the innermost
        loop context. Returns empty fringe (continue is a jump).
        """
        block = self._new_block()
        block.statements.append(CfgStatement(
            line=node.start_point[0] + 1,
            col=node.start_point[1],
            node_type=node.type,
            code_snippet=self._node_text(node, source),
        ))

        if self._loop_stack:
            self._loop_stack[-1].continue_edges.append(
                _FringeEdge(source_block=block.id, edge_type="always")
            )

        return _PartialCfg(
            entry_block=block.id,
            fringe=[],  # continue terminates normal flow
            blocks={block.id: block},
        )

    def _process_return(self, node: Any, source: bytes) -> Optional[_PartialCfg]:
        """Process a return/throw statement.

        Creates a statement block and wires it directly to the function exit.
        Returns empty fringe (return terminates the path).
        """
        block = self._new_block()
        block.statements.append(CfgStatement(
            line=node.start_point[0] + 1,
            col=node.start_point[1],
            node_type=node.type,
            code_snippet=self._node_text(node, source),
        ))

        block.successors.append(CfgEdge(
            target_block=self._exit_block_id,
            edge_type="always",
        ))

        return _PartialCfg(
            entry_block=block.id,
            fringe=[],  # return terminates normal flow
            blocks={block.id: block},
        )

    def _process_try_catch(self, node: Any, source: bytes) -> Optional[_PartialCfg]:
        """Process a try/catch/finally construct.

        Try body's fringe connects to finally entry (if present) or to the
        merge point. Each catch clause gets an exception edge from the try
        body. Finally block (if present) executes after both try and catch.
        """
        mapping = self._mapping.get_try_catch(node.type)
        if mapping is None:
            return self._process_sequential(node, source)  # pragma: no cover

        fringe: list[_FringeEdge] = []

        # Process try body
        try_node = self._find_child(node, mapping.body_child)
        try_partial: Optional[_PartialCfg] = None
        if try_node:
            try_partial = self._process_children(try_node, source)

        # Create a try-entry block to serve as the source for exception edges
        try_entry = self._new_block()
        try_entry.statements.append(CfgStatement(
            line=node.start_point[0] + 1,
            col=node.start_point[1],
            node_type="try_entry",
            code_snippet="try",
        ))

        # Process catch clauses
        catch_partials: list[_PartialCfg] = []
        if mapping.catch_child:
            catch_node = self._find_child(node, mapping.catch_child)
            if catch_node:
                # Single catch handler (found via field/type reference)
                catch_p = self._process_children(catch_node, source)
                if catch_p:
                    catch_partials.append(catch_p)

        # Find catch clauses by type (multiple handlers or no catch_child field)
        if not catch_partials and mapping.catch_clause_type:
            for child in node.children:
                if child.type == mapping.catch_clause_type:
                    catch_p = self._process_children(child, source)
                    if catch_p:
                        catch_partials.append(catch_p)

        # Wire try-entry → try body
        if try_partial and try_partial.entry_block:
            try_entry.successors.append(CfgEdge(
                target_block=try_partial.entry_block,
                edge_type="always",
            ))

        # Wire exception edges: try-entry → each catch entry
        for cp in catch_partials:
            if cp.entry_block:
                try_entry.successors.append(CfgEdge(
                    target_block=cp.entry_block,
                    edge_type="exception",
                ))

        # Process finally
        finally_partial: Optional[_PartialCfg] = None
        if mapping.finally_child:
            finally_node = self._find_child(node, mapping.finally_child)
            if finally_node:
                finally_partial = self._process_children(finally_node, source)

        if finally_partial and finally_partial.entry_block:
            # try fringe → finally entry
            if try_partial:
                self._connect_fringe(try_partial.fringe, finally_partial.entry_block)
            # catch fringes → finally entry
            for cp in catch_partials:
                self._connect_fringe(cp.fringe, finally_partial.entry_block)
            fringe.extend(finally_partial.fringe)
        else:
            # No finally: merge try and catch fringes
            if try_partial:
                fringe.extend(try_partial.fringe)
            for cp in catch_partials:
                fringe.extend(cp.fringe)

        return _PartialCfg(
            entry_block=try_entry.id,
            fringe=fringe,
            blocks={try_entry.id: try_entry},
        )

    def _process_early_return(self, node: Any, source: bytes) -> Optional[_PartialCfg]:
        """Process a dual control-flow construct (e.g., Rust ? operator).

        Creates two edges: Ok path continues (fringe), Err path goes to exit.
        """
        mapping = self._mapping.get_early_return(node.type)
        if mapping is None:
            return self._process_sequential(node, source)  # pragma: no cover

        block = self._new_block()
        block.statements.append(CfgStatement(
            line=node.start_point[0] + 1,
            col=node.start_point[1],
            node_type=node.type,
            code_snippet=self._node_text(node, source),
        ))

        # Err path → exit
        block.successors.append(CfgEdge(
            target_block=self._exit_block_id,
            edge_type="false",
        ))

        # Ok path → fringe (continues)
        return _PartialCfg(
            entry_block=block.id,
            fringe=[_FringeEdge(source_block=block.id, edge_type="true")],
            blocks={block.id: block},
        )

    def _process_context_manager(self, node: Any, source: bytes) -> Optional[_PartialCfg]:
        """Process a context manager (e.g., Python with statement).

        Creates: enter block → body → exit block. The exit block also gets
        an exception edge from the enter block (context managers guarantee
        __exit__ on exceptions).
        """
        mapping = self._mapping.get_context_manager(node.type)
        if mapping is None:
            return self._process_sequential(node, source)  # pragma: no cover

        # Enter block
        enter_block = self._new_block()
        value_node = self._find_child(node, mapping.value_child)
        if value_node:
            enter_block.statements.append(CfgStatement(
                line=value_node.start_point[0] + 1,
                col=value_node.start_point[1],
                node_type="context_manager_enter",
                code_snippet=self._node_text(value_node, source),
            ))

        # Body
        body_node = self._find_child(node, mapping.body_child)
        body_partial: Optional[_PartialCfg] = None
        if body_node:
            body_partial = self._process_children(body_node, source)

        # Exit block
        exit_cm_block = self._new_block()
        exit_cm_block.statements.append(CfgStatement(
            line=node.end_point[0] + 1,
            col=node.end_point[1],
            node_type="context_manager_exit",
            code_snippet="__exit__",
        ))

        # enter → body
        if body_partial and body_partial.entry_block:
            enter_block.successors.append(CfgEdge(
                target_block=body_partial.entry_block,
                edge_type="always",
            ))
            # body fringe → exit
            self._connect_fringe(body_partial.fringe, exit_cm_block.id)
        else:
            enter_block.successors.append(CfgEdge(
                target_block=exit_cm_block.id,
                edge_type="always",
            ))

        # Exception edge: enter → exit (guaranteed cleanup)
        enter_block.successors.append(CfgEdge(
            target_block=exit_cm_block.id,
            edge_type="exception",
        ))

        return _PartialCfg(
            entry_block=enter_block.id,
            fringe=[_FringeEdge(source_block=exit_cm_block.id, edge_type="always")],
            blocks={enter_block.id: enter_block, exit_cm_block.id: exit_cm_block},
        )

    def _process_deferred(self, node: Any, source: bytes) -> Optional[_PartialCfg]:
        """Process a deferred execution statement (e.g., Go defer).

        Records the deferred node for insertion at function exit.
        Creates a statement block for the defer keyword itself.
        """
        mapping = self._mapping.get_deferred(node.type)
        if mapping is None:
            return self._process_sequential(node, source)  # pragma: no cover

        # Record the body for later insertion at function exit
        body_node = self._find_child(node, mapping.body_child)
        if body_node:
            self._deferred_nodes.append(body_node)

        # Create a marker block for the defer statement
        block = self._new_block()
        block.statements.append(CfgStatement(
            line=node.start_point[0] + 1,
            col=node.start_point[1],
            node_type=node.type,
            code_snippet=self._node_text(node, source),
        ))

        return _PartialCfg(
            entry_block=block.id,
            fringe=[_FringeEdge(source_block=block.id, edge_type="always")],
            blocks={block.id: block},
        )

    def _insert_deferred(self, exit_block: BasicBlock, source: bytes) -> None:
        """Insert deferred nodes at function exit (LIFO order, per Go spec)."""
        for deferred_node in reversed(self._deferred_nodes):
            stmt = CfgStatement(
                line=deferred_node.start_point[0] + 1,
                col=deferred_node.start_point[1],
                node_type="deferred_call",
                code_snippet=self._node_text(deferred_node, source),
            )
            exit_block.statements.append(stmt)

    def _process_switch(self, node: Any, source: bytes) -> Optional[_PartialCfg]:
        """Process a switch/match construct.

        Creates a scrutinee block, then edges to each case/arm. The fringe
        is the union of all arms' fringes.
        """
        mapping = self._mapping.get_switch(node.type)
        if mapping is None:
            return self._process_sequential(node, source)  # pragma: no cover

        # Scrutinee block
        scrutinee_block = self._new_block()
        scrutinee_node = self._find_child(node, mapping.scrutinee_child)
        if scrutinee_node:
            scrutinee_block.statements.append(CfgStatement(
                line=scrutinee_node.start_point[0] + 1,
                col=scrutinee_node.start_point[1],
                node_type=scrutinee_node.type,
                code_snippet=self._node_text(scrutinee_node, source),
            ))

        fringe: list[_FringeEdge] = []

        # Process arms/cases
        arms_node = self._find_child(node, mapping.arms_child)
        if arms_node:
            for child in arms_node.children:
                if mapping.arm_type and child.type != mapping.arm_type:
                    continue
                # Skip non-named children (punctuation)
                if not child.is_named:
                    continue

                arm_partial = self._process_children(child, source)
                if arm_partial and arm_partial.entry_block:
                    scrutinee_block.successors.append(CfgEdge(
                        target_block=arm_partial.entry_block,
                        edge_type="case",
                    ))
                    fringe.extend(arm_partial.fringe)

        return _PartialCfg(
            entry_block=scrutinee_block.id,
            fringe=fringe,
            blocks={scrutinee_block.id: scrutinee_block},
        )


@dataclass
class _LoopContext:
    """Tracks break/continue edges for the current loop nesting level."""

    header_block_id: str
    break_edges: list[_FringeEdge] = field(default_factory=list)
    continue_edges: list[_FringeEdge] = field(default_factory=list)


# Node types that are expected to be compound but don't need unmapped warnings
_SKIP_UNMAPPED_TYPES = frozenset({
    # Python: common compound nodes that are handled by recursing into children
    "expression_statement", "module", "block", "decorated_definition",
    "class_definition", "function_definition",
    # Rust
    "source_file", "impl_item", "function_item",
    # Common
    "program", "translation_unit",
})


def build_function_cfg(
    body_node: Any,
    source: bytes,
    mapping: CfgNodeMapping,
    symbol_id: str,
) -> FunctionCfg:
    """Convenience function to build a CFG for a function body.

    Args:
        body_node: Tree-sitter node for the function body.
        source: Source code bytes.
        mapping: YAML-driven CFG node mapping for the language.
        symbol_id: The Symbol.id of the enclosing function.

    Returns:
        A FunctionCfg with entry, exit, and intermediate blocks.
    """
    builder = CfgBuilder(mapping, symbol_id)
    return builder.build(body_node, source)
