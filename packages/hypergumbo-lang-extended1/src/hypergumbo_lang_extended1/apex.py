"""Apex language analyzer.

This module analyzes Salesforce Apex files (.cls, .trigger) using tree-sitter.
Apex is Salesforce's proprietary Java-like language for developing on the
Force.com platform.

How It Works
------------
- Pass 1: Collect symbols (classes, interfaces, enums, methods, fields)
- Pass 2: Extract edges (method calls, static calls)

Symbol Types
------------
- class: Class definitions (including abstract, virtual, with sharing)
- interface: Interface definitions
- enum: Enum definitions
- method: Method definitions
- constructor: Constructor definitions
- field: Field/property declarations

Edge Types
----------
- calls: Method calls from one symbol to another
"""

from __future__ import annotations

import time
import uuid as uuid_module
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "apex.tree_sitter"
PASS_VERSION = "0.1.0"

# Built-in Apex system classes and methods to filter out
APEX_BUILTINS = frozenset({
    "System", "Debug", "String", "Integer", "Boolean", "Decimal", "Double",
    "Long", "Date", "DateTime", "Time", "Blob", "Id", "Object", "List", "Set",
    "Map", "Database", "DML", "Test", "Assert", "Exception", "Math", "JSON",
    "Limits", "Schema", "Type", "UserInfo", "Crypto", "EncodingUtil", "URL",
    "PageReference", "ApexPages", "Messaging", "Email", "Trigger", "Approval",
    "Process", "Flow", "Batch", "Schedulable", "Queueable", "HttpRequest",
    "HttpResponse", "Http", "RestRequest", "RestResponse", "RestContext",
    # Common methods on builtins
    "debug", "assertEquals", "assertNotEquals", "assertTrue", "assertFalse",
    "format", "valueOf", "toString", "hashCode", "equals", "clone", "size",
    "get", "put", "add", "remove", "contains", "isEmpty", "clear", "keySet",
    "values", "entrySet", "addAll", "removeAll", "containsAll", "serialize",
    "deserialize", "deserializeStrict", "insert", "update", "delete", "upsert",
    "query", "countQuery", "getQueryLocator", "executeBatch", "schedule",
    "enqueue", "abortJob", "getErrorMessage",
})


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_stable_id(path: Path, repo_root: Path, name: str, kind: str) -> str:
    """Create a stable identifier for a symbol."""
    rel_path = str(path.relative_to(repo_root))
    return f"apex:{rel_path}:{kind}:{name}"


def _extract_base_classes_apex(node: "tree_sitter.Node") -> list[str]:
    """Extract base classes/interfaces from Apex class declaration.

    Apex uses Java-like syntax:
        class Dog extends Animal implements Comparable { }

    The AST structure:
    - superclass node contains extends + type_identifier
    - interfaces node contains implements + type_list with type_identifiers
    """
    base_classes: list[str] = []

    for child in node.children:
        if child.type == "superclass":
            # Extract the base class
            for subchild in child.children:
                if subchild.type == "type_identifier":
                    base_classes.append(_get_node_text(subchild))
        elif child.type == "interfaces":
            # Extract implemented interfaces
            for subchild in child.children:
                if subchild.type == "type_list":
                    for type_node in subchild.children:
                        if type_node.type == "type_identifier":
                            base_classes.append(_get_node_text(type_node))

    return base_classes


def find_apex_files(repo_root: Path) -> list[Path]:
    """Find all Apex files in the repository."""
    cls_files = list(repo_root.glob("**/*.cls"))
    trigger_files = list(repo_root.glob("**/*.trigger"))
    return sorted(cls_files + trigger_files)


def is_apex_tree_sitter_available() -> bool:
    """Check if tree-sitter-apex is available."""
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("apex")
        return True
    except Exception:  # pragma: no cover
        return False


class ApexAnalysisResult:
    """Result of Apex analysis."""

    def __init__(
        self,
        symbols: list[Symbol] | None = None,
        edges: list[Edge] | None = None,
        run: AnalysisRun | None = None,
        skipped: bool = False,
        skip_reason: str = "",
    ):
        self.symbols = symbols or []
        self.edges = edges or []
        self.run = run
        self.skipped = skipped
        self.skip_reason = skip_reason


class ApexAnalyzer:
    """Analyzer for Salesforce Apex files."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self._symbol_registry: dict[str, str] = {}  # name -> id
        self._run_id: str = ""
        self._current_class: Optional[str] = None

    def analyze(self) -> ApexAnalysisResult:
        """Analyze all Apex files in the repository."""
        from tree_sitter_language_pack import get_parser

        start_time = time.time()
        self._run_id = f"uuid:{uuid_module.uuid4()}"

        parser = get_parser("apex")
        apex_files = find_apex_files(self.repo_root)

        if not apex_files:
            return ApexAnalysisResult()

        # Pass 1: Collect all symbols
        for path in apex_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._current_class = None
                self._extract_symbols(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        # Build symbol registry
        for sym in self.symbols:
            self._symbol_registry[sym.name] = sym.id
            # Also register short name (without class prefix)
            if "." in sym.name:
                short_name = sym.name.split(".")[-1]
                if short_name not in self._symbol_registry:
                    self._symbol_registry[short_name] = sym.id

        # Pass 2: Extract edges
        for path in apex_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._current_class = None
                self._extract_edges(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        elapsed = time.time() - start_time

        run = AnalysisRun(
            pass_id=PASS_ID,
            execution_id=self._run_id,
            version=PASS_VERSION,
            toolchain={"name": "apex", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return ApexAnalysisResult(
            symbols=self.symbols,
            edges=self.edges,
            run=run,
        )

    def _get_modifiers(self, node: "tree_sitter.Node") -> dict[str, bool]:
        """Extract modifiers from a modifiers node."""
        modifiers: dict[str, bool] = {}
        for child in node.children:
            if child.type == "modifiers":
                for mod in child.children:
                    if mod.type == "modifier":
                        for m in mod.children:
                            mod_text = _get_node_text(m).lower()
                            if mod_text in ("public", "private", "protected", "global"):
                                modifiers["visibility"] = True
                                modifiers[mod_text] = True
                            elif mod_text == "static":
                                modifiers["static"] = True
                            elif mod_text == "abstract":
                                modifiers["abstract"] = True
                            elif mod_text == "virtual":
                                modifiers["virtual"] = True
                            elif mod_text == "override":
                                modifiers["override"] = True
                            elif mod_text in ("with", "without", "inherited"):  # pragma: no cover
                                # Sharing mode keywords (e.g., "with sharing")
                                pass
        return modifiers

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "class_declaration":
            self._extract_class(node, path)
            # Don't recurse - _extract_class handles children via _extract_class_body
            return

        elif node.type == "interface_declaration":
            self._extract_interface(node, path)
            # Don't recurse - _extract_interface handles children
            return

        elif node.type == "enum_declaration":
            self._extract_enum(node, path)
            # Don't recurse - _extract_enum handles children
            return

        elif node.type == "trigger_declaration":
            self._extract_trigger(node, path)
            # Don't recurse - _extract_trigger handles children
            return

        # Recurse into children
        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_class(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a class definition and its members."""
        name = None
        modifiers = self._get_modifiers(node)

        for child in node.children:
            if child.type == "identifier":
                name = _get_node_text(child)
                break

        if name:
            rel_path = str(path.relative_to(self.repo_root))
            meta: dict[str, object] = {}

            if modifiers.get("public"):
                meta["visibility"] = "public"
            elif modifiers.get("private"):
                meta["visibility"] = "private"
            elif modifiers.get("protected"):
                meta["visibility"] = "protected"
            elif modifiers.get("global"):
                meta["visibility"] = "global"

            if modifiers.get("abstract"):
                meta["abstract"] = True
            if modifiers.get("virtual"):
                meta["virtual"] = True

            # Extract base classes for inheritance linker
            base_classes = _extract_base_classes_apex(node)
            if base_classes:
                meta["base_classes"] = base_classes

            sym = Symbol(
                id=_make_stable_id(path, self.repo_root, name, "class"),
                stable_id=_make_stable_id(path, self.repo_root, name, "class"),
                name=name,
                kind="class",
                language="apex",
                path=rel_path,
                span=Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
                meta=meta if meta else {},
            )
            self.symbols.append(sym)

            # Save class context for extracting members
            prev_class = self._current_class
            self._current_class = name

            # Extract class members
            for child in node.children:
                if child.type == "class_body":
                    self._extract_class_body(child, path)

            self._current_class = prev_class

    def _extract_class_body(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract members from a class body."""
        for child in node.children:
            if child.type == "method_declaration":
                self._extract_method(child, path)
            elif child.type == "constructor_declaration":
                self._extract_constructor(child, path)
            elif child.type == "field_declaration":
                self._extract_field(child, path)
            elif child.type == "class_declaration":
                # Inner class
                self._extract_class(child, path)
            elif child.type == "interface_declaration":
                # Inner interface
                self._extract_interface(child, path)
            elif child.type == "enum_declaration":
                # Inner enum
                self._extract_enum(child, path)

    def _extract_method(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a method definition."""
        name = None
        return_type = None
        modifiers = self._get_modifiers(node)
        params: list[str] = []

        for child in node.children:
            if child.type == "identifier":
                name = _get_node_text(child)
            elif child.type == "void_type":
                return_type = "void"
            elif child.type == "type_identifier":
                return_type = _get_node_text(child)
            elif child.type == "generic_type":
                return_type = _get_node_text(child)
            elif child.type == "formal_parameters":
                params = self._extract_params(child)

        if name:
            qualified_name = f"{self._current_class}.{name}" if self._current_class else name
            rel_path = str(path.relative_to(self.repo_root))

            meta: dict[str, object] = {}
            if return_type:
                meta["return_type"] = return_type
            if params:
                meta["params"] = params
            if modifiers.get("static"):
                meta["static"] = True
            if modifiers.get("public"):
                meta["visibility"] = "public"
            elif modifiers.get("private"):
                meta["visibility"] = "private"
            elif modifiers.get("protected"):
                meta["visibility"] = "protected"
            elif modifiers.get("global"):
                meta["visibility"] = "global"
            if modifiers.get("override"):
                meta["override"] = True
            if modifiers.get("virtual"):
                meta["virtual"] = True

            signature = f"{name}({', '.join(params)})"

            sym = Symbol(
                id=_make_stable_id(path, self.repo_root, qualified_name, "method"),
                stable_id=_make_stable_id(path, self.repo_root, qualified_name, "method"),
                name=qualified_name,
                kind="method",
                language="apex",
                path=rel_path,
                span=Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
                signature=signature,
                meta=meta if meta else {},
            )
            self.symbols.append(sym)

    def _extract_constructor(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a constructor definition."""
        name = None
        modifiers = self._get_modifiers(node)
        params: list[str] = []

        for child in node.children:
            if child.type == "identifier":
                name = _get_node_text(child)
            elif child.type == "formal_parameters":
                params = self._extract_params(child)

        if name:
            qualified_name = f"{self._current_class}.{name}" if self._current_class else name
            rel_path = str(path.relative_to(self.repo_root))

            meta: dict[str, object] = {}
            if params:
                meta["params"] = params
            if modifiers.get("public"):
                meta["visibility"] = "public"
            elif modifiers.get("private"):
                meta["visibility"] = "private"
            elif modifiers.get("protected"):
                meta["visibility"] = "protected"
            elif modifiers.get("global"):
                meta["visibility"] = "global"

            signature = f"{name}({', '.join(params)})"

            sym = Symbol(
                id=_make_stable_id(path, self.repo_root, qualified_name, "constructor"),
                stable_id=_make_stable_id(path, self.repo_root, qualified_name, "constructor"),
                name=qualified_name,
                kind="constructor",
                language="apex",
                path=rel_path,
                span=Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
                signature=signature,
                meta=meta if meta else {},
            )
            self.symbols.append(sym)

    def _extract_field(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a field declaration."""
        field_type = None
        modifiers = self._get_modifiers(node)

        for child in node.children:
            if child.type == "type_identifier":
                field_type = _get_node_text(child)
            elif child.type == "generic_type":
                field_type = _get_node_text(child)
            elif child.type == "variable_declarator":
                name = None
                for subchild in child.children:
                    if subchild.type == "identifier":
                        name = _get_node_text(subchild)
                        break

                if name:
                    qualified_name = f"{self._current_class}.{name}" if self._current_class else name
                    rel_path = str(path.relative_to(self.repo_root))

                    meta: dict[str, object] = {}
                    if field_type:
                        meta["type"] = field_type
                    if modifiers.get("static"):
                        meta["static"] = True
                    if modifiers.get("public"):
                        meta["visibility"] = "public"
                    elif modifiers.get("private"):
                        meta["visibility"] = "private"
                    elif modifiers.get("protected"):
                        meta["visibility"] = "protected"
                    elif modifiers.get("global"):
                        meta["visibility"] = "global"

                    sym = Symbol(
                        id=_make_stable_id(path, self.repo_root, qualified_name, "field"),
                        stable_id=_make_stable_id(path, self.repo_root, qualified_name, "field"),
                        name=qualified_name,
                        kind="field",
                        language="apex",
                        path=rel_path,
                        span=Span(
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        meta=meta if meta else {},
                    )
                    self.symbols.append(sym)

    def _extract_interface(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract an interface definition."""
        name = None
        modifiers = self._get_modifiers(node)

        for child in node.children:
            if child.type == "identifier":
                name = _get_node_text(child)
                break

        if name:
            rel_path = str(path.relative_to(self.repo_root))
            meta: dict[str, object] = {}

            if modifiers.get("public"):
                meta["visibility"] = "public"
            elif modifiers.get("private"):
                meta["visibility"] = "private"
            elif modifiers.get("global"):
                meta["visibility"] = "global"

            sym = Symbol(
                id=_make_stable_id(path, self.repo_root, name, "interface"),
                stable_id=_make_stable_id(path, self.repo_root, name, "interface"),
                name=name,
                kind="interface",
                language="apex",
                path=rel_path,
                span=Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
                meta=meta if meta else {},
            )
            self.symbols.append(sym)

            # Extract interface methods
            prev_class = self._current_class
            self._current_class = name

            for child in node.children:
                if child.type == "interface_body":
                    for member in child.children:
                        if member.type == "method_declaration":
                            self._extract_method(member, path)

            self._current_class = prev_class

    def _extract_enum(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract an enum definition."""
        name = None
        modifiers = self._get_modifiers(node)
        constants: list[str] = []

        for child in node.children:
            if child.type == "identifier":
                name = _get_node_text(child)
            elif child.type == "enum_body":
                for subchild in child.children:
                    if subchild.type == "enum_constant":
                        for c in subchild.children:
                            if c.type == "identifier":
                                constants.append(_get_node_text(c))

        if name:
            rel_path = str(path.relative_to(self.repo_root))
            meta: dict[str, object] = {"constants": constants} if constants else {}

            if modifiers.get("public"):
                meta["visibility"] = "public"
            elif modifiers.get("private"):
                meta["visibility"] = "private"
            elif modifiers.get("global"):
                meta["visibility"] = "global"

            sym = Symbol(
                id=_make_stable_id(path, self.repo_root, name, "enum"),
                stable_id=_make_stable_id(path, self.repo_root, name, "enum"),
                name=name,
                kind="enum",
                language="apex",
                path=rel_path,
                span=Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
                meta=meta if meta else {},
            )
            self.symbols.append(sym)

    def _extract_trigger(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a trigger definition."""
        name = None
        sobject = None

        for child in node.children:
            if child.type == "identifier":
                if name is None:
                    name = _get_node_text(child)
                else:
                    sobject = _get_node_text(child)

        if name:
            rel_path = str(path.relative_to(self.repo_root))
            meta: dict[str, object] = {}
            if sobject:
                meta["sobject"] = sobject

            sym = Symbol(
                id=_make_stable_id(path, self.repo_root, name, "trigger"),
                stable_id=_make_stable_id(path, self.repo_root, name, "trigger"),
                name=name,
                kind="trigger",
                language="apex",
                path=rel_path,
                span=Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
                meta=meta if meta else {},
            )
            self.symbols.append(sym)

            # Extract trigger body
            prev_class = self._current_class
            self._current_class = name

            for child in node.children:
                if child.type == "trigger_body":
                    self._extract_edges(child, path)

            self._current_class = prev_class

    def _extract_params(self, node: "tree_sitter.Node") -> list[str]:
        """Extract parameter names from a formal_parameters node."""
        params: list[str] = []
        for child in node.children:
            if child.type == "formal_parameter":
                param_type = None
                param_name = None
                for subchild in child.children:
                    if subchild.type == "type_identifier":
                        param_type = _get_node_text(subchild)
                    elif subchild.type == "generic_type":
                        param_type = _get_node_text(subchild)
                    elif subchild.type == "identifier":
                        param_name = _get_node_text(subchild)
                if param_type and param_name:
                    params.append(f"{param_type} {param_name}")
                elif param_name:  # pragma: no cover
                    # Defensive: params usually have types in Apex
                    params.append(param_name)
        return params

    def _extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract edges from a syntax tree node."""
        if node.type == "class_declaration":
            # Update current class context
            for child in node.children:
                if child.type == "identifier":
                    prev_class = self._current_class
                    self._current_class = _get_node_text(child)
                    for c in node.children:
                        self._extract_edges(c, path)
                    self._current_class = prev_class
                    return

        elif node.type == "method_declaration":
            # Extract edges from method body
            method_name = None
            for child in node.children:
                if child.type == "identifier":
                    method_name = _get_node_text(child)
                    break

            if method_name and self._current_class:
                src_name = f"{self._current_class}.{method_name}"
                for child in node.children:
                    if child.type == "block":
                        self._extract_calls_from_block(child, path, src_name)

        elif node.type == "constructor_declaration":
            # Extract edges from constructor body
            constructor_name = None
            for child in node.children:
                if child.type == "identifier":
                    constructor_name = _get_node_text(child)
                    break

            if constructor_name and self._current_class:
                src_name = f"{self._current_class}.{constructor_name}"
                for child in node.children:
                    if child.type == "constructor_body":
                        self._extract_calls_from_block(child, path, src_name)

        # Recurse into children
        for child in node.children:
            self._extract_edges(child, path)

    def _extract_calls_from_block(
        self, node: "tree_sitter.Node", path: Path, src_name: str
    ) -> None:
        """Extract method calls from a code block."""
        if node.type == "method_invocation":
            self._extract_method_call(node, path, src_name)
        elif node.type == "object_creation_expression":
            self._extract_constructor_call(node, path, src_name)

        for child in node.children:
            self._extract_calls_from_block(child, path, src_name)

    def _extract_method_call(
        self, node: "tree_sitter.Node", path: Path, src_name: str
    ) -> None:
        """Extract a method call edge."""
        # Method invocation structure: target.method(args)
        target = None
        method_name = None
        identifiers: list[str] = []

        for child in node.children:
            if child.type == "identifier":
                identifiers.append(_get_node_text(child))
            elif child.type == "this":
                target = "this"

        if len(identifiers) >= 2:
            # Class.method() or object.method()
            target = identifiers[0]
            method_name = identifiers[1]
        elif len(identifiers) == 1:
            if target == "this":
                # this.method()
                method_name = identifiers[0]
            else:
                # Standalone method call (rare in Apex)
                method_name = identifiers[0]

        if method_name:
            # Skip built-in methods
            if method_name in APEX_BUILTINS or (target and target in APEX_BUILTINS):
                return

            # Build qualified call name
            if target == "this" and self._current_class:
                call_name = f"{self._current_class}.{method_name}"
            elif target:
                call_name = f"{target}.{method_name}"
            else:
                # Try current class context
                if self._current_class:
                    call_name = f"{self._current_class}.{method_name}"
                else:  # pragma: no cover
                    # Defensive: method calls outside class context are rare
                    call_name = method_name

            self._add_call_edge(path, src_name, call_name, node.start_point[0] + 1)

    def _extract_constructor_call(
        self, node: "tree_sitter.Node", path: Path, src_name: str
    ) -> None:
        """Extract a constructor call (new ClassName()) edge."""
        type_name = None

        for child in node.children:
            if child.type == "type_identifier":
                type_name = _get_node_text(child)
                break
            elif child.type == "generic_type":
                # Extract the base type from generic
                for subchild in child.children:
                    if subchild.type == "type_identifier":
                        type_name = _get_node_text(subchild)
                        break
                break

        if type_name and type_name not in APEX_BUILTINS:
            # Look for the constructor
            call_name = f"{type_name}.{type_name}"
            self._add_call_edge(path, src_name, call_name, node.start_point[0] + 1)

    def _add_call_edge(
        self, path: Path, src_name: str, call_name: str, line: int
    ) -> None:
        """Add a call edge."""
        # Try to resolve the callee
        dst_id = self._symbol_registry.get(call_name)
        if not dst_id:
            # Try short name
            short_name = call_name.split(".")[-1] if "." in call_name else call_name
            dst_id = self._symbol_registry.get(short_name)

        if dst_id:
            confidence = 1.0
            dst = dst_id
        else:
            confidence = 0.6
            dst = f"unresolved:{call_name}"

        src_id = self._symbol_registry.get(
            src_name, f"apex:{path.relative_to(self.repo_root)}:file"
        )

        edge = Edge.create(
            src=src_id,
            dst=dst,
            edge_type="calls",
            line=line,
            origin=PASS_ID,
            origin_run_id=self._run_id,
            evidence_type="tree_sitter",
            confidence=confidence,
            evidence_lang="apex",
        )
        self.edges.append(edge)


def analyze_apex(repo_root: Path) -> ApexAnalysisResult:
    """Analyze Apex files in the repository.

    Args:
        repo_root: Root path of the repository to analyze

    Returns:
        ApexAnalysisResult containing symbols and edges
    """
    if not is_apex_tree_sitter_available():
        warnings.warn(
            "Apex analysis skipped: tree-sitter-apex not available",
            UserWarning,
            stacklevel=2,
        )
        return ApexAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-apex not available",
        )

    analyzer = ApexAnalyzer(repo_root)
    return analyzer.analyze()
