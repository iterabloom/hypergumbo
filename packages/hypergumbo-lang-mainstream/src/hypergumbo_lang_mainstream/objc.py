# SPDX-License-Identifier: AGPL-3.0-or-later
"""Objective-C analyzer using tree-sitter.

This analyzer extracts classes, protocols, methods, and properties from
Objective-C source files (.m, .mm, .h). It uses tree-sitter-objc for parsing
when available, falling back gracefully when the grammar is not installed.

Node types handled:
- class_interface: @interface declarations
- class_implementation: @implementation definitions
- protocol_declaration: @protocol definitions
- method_declaration: Method declarations in interfaces
- method_definition: Method implementations
- property_declaration: @property declarations
- preproc_include: #import statements
- message_expression: [receiver message] method calls

Two-pass analysis:
- Pass 1: Extract all symbols from all files
- Pass 2: Resolve method calls using global symbol registry
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Optional

from hypergumbo_core.discovery import classify_dot_m_file, find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("objective-c")


def _is_objc_tree_sitter_available_legacy() -> bool:  # pragma: no cover - replaced by TreeSitterAnalyzer
    """Legacy availability check -- replaced by TreeSitterAnalyzer._check_grammar_available."""
    pass


def find_objc_files(root: Path) -> list[Path]:
    """Find Objective-C files, disambiguating .m files via content heuristics.

    Extensions:
    - .m: Shared with MATLAB and Wolfram — classified by content
    - .mm: Objective-C++ (unambiguous)
    - .h: Header files (unambiguous for Objective-C analysis)
    """
    # Unambiguous extensions
    result = list(find_files(root, ["*.mm", "*.h"]))
    # Ambiguous .m files — only include if classified as Objective-C
    result.extend(p for p in find_files(root, ["*.m"]) if classify_dot_m_file(p) == "objc")
    return result


def _extract_type_name(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract type name from a type_name node, handling pointers."""
    parts: list[str] = []
    for child in node.children:
        if child.type in ("primitive_type", "type_identifier"):
            parts.append(node_text(child, source))
        elif child.type == "abstract_pointer_declarator":
            parts.append("*")
    return "".join(parts)


def _extract_objc_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract method signature from an Objective-C method declaration/definition.

    Returns signature like:
    - "(int x, int y): int" for methods with return type
    - "(NSString* message)" for void methods (void omitted)
    - "(): NSString*" for no-params methods with return type

    Args:
        node: The method_declaration or method_definition node.
        source: The source code bytes.

    Returns:
        The signature string, or None if extraction fails.
    """
    params: list[str] = []
    return_type: Optional[str] = None

    for child in node.children:
        if child.type == "method_type":
            # This is the return type
            type_name_node = find_child_by_type(child, "type_name")
            if type_name_node:
                return_type = _extract_type_name(type_name_node, source)
        elif child.type == "method_parameter":
            # Extract parameter type and name
            param_type = None
            param_name = None
            for subchild in child.children:
                if subchild.type == "method_type":
                    type_name_node = find_child_by_type(subchild, "type_name")
                    if type_name_node:
                        param_type = _extract_type_name(type_name_node, source)
                elif subchild.type == "identifier":
                    param_name = node_text(subchild, source)
            if param_type and param_name:
                params.append(f"{param_type} {param_name}")

    params_str = ", ".join(params)
    signature = f"({params_str})"

    if return_type and return_type != "void":
        signature += f": {return_type}"

    return signature


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file."""

    symbols: list[Symbol] = field(default_factory=list)
    symbol_by_name: dict[str, Symbol] = field(default_factory=dict)
    methods_by_name: dict[str, Symbol] = field(default_factory=dict)
    current_class: str | None = None


def _extract_class_name(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract class name from class_interface or class_implementation node."""
    # Find the identifier that follows @interface or @implementation
    for child in node.children:
        if child.type == "identifier":
            return node_text(child, source)
    return None  # pragma: no cover


def _extract_base_classes_objc(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract base classes/protocols from Objective-C class interface.

    Objective-C uses single inheritance and multiple protocol conformance:
        @interface Dog : Animal <MyProtocol>      -> ["Animal", "MyProtocol"]
        @interface Cat : Animal <A, B>            -> ["Animal", "A", "B"]

    The AST structure:
    - First identifier = class name
    - Second identifier (after `:`) = superclass
    - parameterized_arguments contains protocol conformance
    """
    base_classes: list[str] = []
    seen_class_name = False
    seen_colon = False

    for child in node.children:
        if child.type == "identifier":
            if not seen_class_name:
                # First identifier is the class name, skip it
                seen_class_name = True
            elif seen_colon:
                # Identifier after `:` is the superclass
                base_classes.append(node_text(child, source))
        elif child.type == ":":
            seen_colon = True
        elif child.type == "parameterized_arguments":
            # Protocol conformance: <ProtocolA, ProtocolB>
            for param_child in child.children:
                if param_child.type == "type_name":
                    type_id = find_child_by_type(param_child, "type_identifier")
                    if type_id:
                        base_classes.append(node_text(type_id, source))

    return base_classes


def _extract_protocol_name(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract protocol name from protocol_declaration node."""
    for child in node.children:
        if child.type == "identifier":
            return node_text(child, source)
    return None  # pragma: no cover


def _extract_method_name(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract method selector from method_declaration or method_definition.

    Objective-C methods can have multiple parts: -(void)setX:(int)x Y:(int)y
    becomes "setX:Y:" as the selector.
    """
    parts: list[str] = []
    for child in node.children:
        if child.type == "identifier":
            parts.append(node_text(child, source))
        elif child.type == "keyword_selector":  # pragma: no cover - complex selectors
            # Complex selector like doSomething:withParam:
            for kw_child in child.children:
                if kw_child.type == "keyword_argument_selector":
                    for kwa_child in kw_child.children:
                        if kwa_child.type == "identifier":
                            parts.append(node_text(kwa_child, source) + ":")
                            break

    if parts:
        return "".join(parts)
    return None  # pragma: no cover


def _extract_property_name(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract property name from property_declaration node."""
    # Property declaration has a struct_declaration child with the var name
    struct_decl = find_child_by_type(node, "struct_declaration")
    if struct_decl:
        for child in struct_decl.children:
            if child.type == "struct_declarator":
                # May be pointer_declarator or identifier
                for decl_child in child.children:
                    if decl_child.type == "pointer_declarator":
                        for ptr_child in decl_child.children:
                            if ptr_child.type == "identifier":
                                return node_text(ptr_child, source)
                    elif decl_child.type == "identifier":
                        return node_text(decl_child, source)
    return None  # pragma: no cover


def _is_class_method(node: "tree_sitter.Node") -> bool:  # pragma: no cover - unused
    """Check if a method is a class method (starts with +)."""
    for child in node.children:
        if child.type == "+":
            return True
        if child.type == "-":
            return False
    return False  # default to instance


def _get_enclosing_class_objc(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Walk up the tree to find enclosing class/implementation name."""
    current = node.parent
    while current is not None:
        if current.type in ("class_interface", "class_implementation"):
            return _extract_class_name(current, source)
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
) -> FileAnalysis:
    """Extract symbols from a single Objective-C file.

    Uses iterative traversal to avoid RecursionError on deeply nested code.
    """
    analysis = FileAnalysis()
    rel_path = str(file_path)

    try:
        source = file_path.read_bytes()
    except (OSError, IOError):  # pragma: no cover
        return analysis

    tree = parser.parse(source)

    for node in iter_tree(tree.root_node):
        if node.type in ("class_interface", "class_implementation"):
            class_name = _extract_class_name(node, source)
            if class_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("objc", rel_path, start_line, end_line, class_name, "class")

                # Extract base classes/protocols for inheritance linker
                base_classes = _extract_base_classes_objc(node, source)
                meta = {"base_classes": base_classes} if base_classes else None

                symbol = Symbol(
                    id=symbol_id,
                    name=class_name,
                    kind="class",
                    language="objective-c",
                    path=rel_path,
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta=meta,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[class_name] = symbol

        elif node.type == "protocol_declaration":
            protocol_name = _extract_protocol_name(node, source)
            if protocol_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("objc",
                    rel_path, start_line, end_line, protocol_name, "protocol"
                )

                symbol = Symbol(
                    id=symbol_id,
                    name=protocol_name,
                    kind="protocol",
                    language="objective-c",
                    path=rel_path,
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[protocol_name] = symbol

        elif node.type in ("method_declaration", "method_definition"):
            method_name = _extract_method_name(node, source)
            if method_name:
                # Prefix with class name if inside a class
                current_class = _get_enclosing_class_objc(node, source)
                full_name = f"{current_class}.{method_name}" if current_class else method_name
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("objc", rel_path, start_line, end_line, full_name, "method")

                # Extract signature
                signature = _extract_objc_signature(node, source)

                symbol = Symbol(
                    id=symbol_id,
                    name=full_name,
                    kind="method",
                    language="objective-c",
                    path=rel_path,
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    signature=signature,
                )
                analysis.symbols.append(symbol)
                analysis.methods_by_name[method_name] = symbol

        elif node.type == "property_declaration":
            prop_name = _extract_property_name(node, source)
            if prop_name:
                current_class = _get_enclosing_class_objc(node, source)
                full_name = f"{current_class}.{prop_name}" if current_class else prop_name
                start_line = node.start_point[0] + 1
                symbol_id = make_symbol_id("objc", rel_path, start_line, start_line, full_name, "property")

                symbol = Symbol(
                    id=symbol_id,
                    name=full_name,
                    kind="property",
                    language="objective-c",
                    path=rel_path,
                    span=Span(
                        start_line=start_line,
                        end_line=start_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)

    return analysis


def _extract_import_path(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract import path from preproc_include node."""
    # Check for system_lib_string (<...>)
    for child in node.children:
        if child.type == "system_lib_string":
            text = node_text(child, source)
            # Remove angle brackets
            return text.strip("<>")
        elif child.type == "string_literal":
            # Local import "..."
            for str_child in child.children:
                if str_child.type == "string_content":
                    return node_text(str_child, source)
    return None  # pragma: no cover


def _extract_message_selector(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract the selector from a message_expression.

    [receiver selectorPart1:arg selectorPart2:arg2] extracts "selectorPart1:selectorPart2:"

    Structure: [ receiver selector ]
    - First identifier after '[' is the receiver (skip it)
    - Second identifier is the selector for simple messages
    - For keyword messages, keyword_argument_list contains selector parts
    """
    parts: list[str] = []
    seen_receiver = False

    for child in node.children:
        if child.type == "identifier":
            if not seen_receiver:
                # First identifier is the receiver, skip it
                seen_receiver = True
            else:
                # Simple message like [obj doSomething]
                parts.append(node_text(child, source))
        elif child.type == "message_expression":
            # Nested message like [[obj alloc] init] - receiver is another message
            seen_receiver = True
        elif child.type == "keyword_argument_list":  # pragma: no cover - complex msgs
            for kw_child in child.children:
                if kw_child.type == "keyword_argument":
                    for kwa_child in kw_child.children:
                        if kwa_child.type == "identifier":
                            parts.append(node_text(kwa_child, source) + ":")
                            break

    if parts:
        return "".join(parts)
    return None  # pragma: no cover


def _get_enclosing_method_objc(
    node: "tree_sitter.Node",
    source: bytes,
    local_methods: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find enclosing method definition."""
    current = node.parent
    while current is not None:
        if current.type == "method_definition":
            method_name = _extract_method_name(current, source)
            if method_name and method_name in local_methods:
                return local_methods[method_name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_methods: dict[str, Symbol],
    method_resolver: NameResolver,
    run: AnalysisRun,
) -> list[Edge]:
    """Extract edges from a file using global symbol knowledge.

    Uses iterative traversal to avoid RecursionError on deeply nested code.
    """
    edges: list[Edge] = []
    _caller_path = str(file_path)
    rel_path = str(file_path)
    file_id = make_file_id("objc", rel_path)

    try:
        source = file_path.read_bytes()
    except (OSError, IOError):  # pragma: no cover
        return edges

    tree = parser.parse(source)

    for node in iter_tree(tree.root_node):
        # Handle imports
        if node.type == "preproc_include":
            # Check if it's #import (not #include)
            has_import = any(c.type == "#import" for c in node.children)
            if has_import:
                import_path = _extract_import_path(node, source)
                if import_path:
                    line = node.start_point[0] + 1
                    edges.append(Edge.create(
                        src=file_id,
                        dst=import_path,
                        edge_type="imports",
                        line=line,
                        evidence_type="import_statement",
                        confidence=0.95,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))

        # Handle message expressions (method calls)
        elif node.type == "message_expression":
            selector = _extract_message_selector(node, source)
            current_method = _get_enclosing_method_objc(node, source, local_methods)
            if selector and current_method is not None:
                line = node.start_point[0] + 1

                # Try local match first (same-file, higher confidence)
                if selector in local_methods:
                    callee = local_methods[selector]
                    edges.append(Edge.create(
                        src=current_method.id,
                        dst=callee.id,
                        edge_type="calls",
                        line=line,
                        evidence_type="message_send",
                        confidence=0.90,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))
                else:
                    # Try cross-file resolution via resolver
                    lookup_result = method_resolver.lookup(selector, caller_path=_caller_path)
                    if lookup_result.found and lookup_result.symbol is not None:
                        edges.append(Edge.create(
                            src=current_method.id,
                            dst=lookup_result.symbol.id,
                            edge_type="calls",
                            line=line,
                            evidence_type="cross_file_message_send",
                            confidence=0.75 * lookup_result.confidence,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))

    return edges


class ObjCAnalyzer(TreeSitterAnalyzer):
    """Objective-C analyzer using tree-sitter-objc.

    Overrides ``analyze()`` because Objective-C uses a custom two-pass
    structure with per-file FileAnalysis, global method registry, and
    cross-file message-send resolution.
    """

    lang = "objective-c"
    file_patterns: ClassVar[list[str]] = ["*.m", "*.mm", "*.h"]
    grammar_module = "tree_sitter_objc"

    def analyze(
        self,
        repo_root: Path,
        max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """Run Objective-C analysis with two-pass symbol/edge extraction."""
        if not self._check_grammar_available():
            warnings.warn(
                f"{self.lang} analysis skipped: grammar not available. "
                f"Install the required tree-sitter grammar package.",
                UserWarning,
                stacklevel=2,
            )
            return AnalysisResult(
                skipped=True,
                skip_reason=f"{self.lang} tree-sitter grammar not available",
            )

        try:
            parser = self._create_parser()
        except Exception as e:
            return AnalysisResult(
                skipped=True,
                skip_reason=f"Failed to load Objective-C parser: {e}",
            )

        run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

        all_files = find_objc_files(repo_root)
        if not all_files:  # pragma: no cover - no ObjC files in test
            return AnalysisResult(run=run)

        # Pass 1: Extract symbols from all files
        all_symbols: list[Symbol] = []
        file_analyses: dict[Path, FileAnalysis] = {}
        global_methods: dict[str, Symbol] = {}

        for objc_file in all_files:
            analysis = _extract_symbols_from_file(objc_file, parser, run)
            file_analyses[objc_file] = analysis
            all_symbols.extend(analysis.symbols)

            # Collect methods globally for cross-file resolution
            for selector, sym in analysis.methods_by_name.items():
                global_methods[selector] = sym

        # Pass 2: Extract edges using global symbol knowledge
        method_resolver = NameResolver(global_methods)
        all_edges: list[Edge] = []

        for objc_file, analysis in file_analyses.items():
            edges = _extract_edges_from_file(
                objc_file, parser, analysis.methods_by_name, method_resolver, run
            )
            all_edges.extend(edges)

        return AnalysisResult(
            symbols=all_symbols,
            edges=all_edges,
            run=run,
        )


_analyzer = ObjCAnalyzer()


def is_objc_tree_sitter_available() -> bool:
    """Check if tree-sitter with Objective-C grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("objc")
def analyze_objc(root: Path) -> AnalysisResult:
    """Analyze Objective-C files in a directory."""
    return _analyzer.analyze(root)
