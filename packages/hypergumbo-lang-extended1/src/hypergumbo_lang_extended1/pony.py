"""Pony language analyzer using tree-sitter.

Pony is an actor-model programming language with capabilities-secure type system,
reference capabilities for safe concurrency, and memory safety without a garbage
collector pause. It runs on the LLVM backend.

How It Works
------------
1. Uses tree-sitter-pony grammar from tree-sitter-language-pack to parse .pony files
2. Pass 1: Extract actors, classes, interfaces, traits, primitives, methods, constructors
3. Pass 2: Extract call edges with registry lookup for resolution

Symbols Extracted
-----------------
- **Actors**: Pony's concurrent entities (actor definitions)
- **Classes**: Regular class definitions
- **Interfaces**: Interface definitions (structural typing)
- **Traits**: Trait definitions (nominal typing)
- **Primitives**: Singleton value types
- **Constructors**: new constructors within types
- **Methods**: fun methods within types
- **Fields**: var/let field definitions

Edges Extracted
---------------
- **calls**: Method and constructor invocations

Why This Design
---------------
- Pony's actor model makes understanding message passing important
- Reference capabilities (iso, trn, ref, val, box, tag) are captured
- Interface/trait relationships help understand type hierarchies
"""

from __future__ import annotations

import time
import uuid
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = "pony.tree_sitter"
PASS_VERSION = "0.1.0"


class PonyAnalysisResult:
    """Result of Pony analysis."""

    def __init__(
        self,
        symbols: list[Symbol],
        edges: list[Edge],
        run: AnalysisRun | None = None,
        skipped: bool = False,
        skip_reason: str = "",
    ) -> None:
        self.symbols = symbols
        self.edges = edges
        self.run = run
        self.skipped = skipped
        self.skip_reason = skip_reason


def is_pony_tree_sitter_available() -> bool:
    """Check if tree-sitter-pony is available."""
    try:
        from tree_sitter_language_pack import get_language

        get_language("pony")
        return True
    except Exception:  # pragma: no cover
        return False


def find_pony_files(repo_root: Path) -> list[Path]:
    """Find all Pony files in the repository."""
    return sorted(repo_root.glob("**/*.pony"))


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str) -> str:
    """Create a stable symbol ID."""
    return f"pony:{path}:{kind}:{name}"


class PonyAnalyzer:
    """Analyzer for Pony files."""

    # Built-in Pony types and functions to filter
    BUILTINS = frozenset({
        # Primitives
        "None", "Bool", "I8", "I16", "I32", "I64", "I128", "ILong", "ISize",
        "U8", "U16", "U32", "U64", "U128", "ULong", "USize", "F32", "F64",
        # Collections
        "Array", "String", "Map", "Set", "List", "Range",
        # System
        "Env", "StdStream", "FileAuth", "NetAuth", "DNSAuth",
        # Common functions
        "print", "println", "err", "out", "apply", "create", "size", "push",
        "pop", "values", "keys", "pairs", "string", "hash", "eq", "ne",
        "lt", "le", "gt", "ge", "add", "sub", "mul", "div", "rem", "neg",
        "op_and", "op_or", "op_xor", "op_not", "shl", "shr",
    })

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._symbols: list[Symbol] = []
        self._edges: list[Edge] = []
        self._symbol_registry: dict[str, str] = {}  # name -> symbol_id
        self._current_type: str | None = None
        self._current_type_id: str | None = None
        self._current_method: str | None = None
        self._current_method_id: str | None = None
        self._execution_id = f"uuid:{uuid.uuid4()}"
        self._files_analyzed = 0

    def analyze(self) -> PonyAnalysisResult:
        """Run the Pony analysis."""
        start_time = time.time()

        files = find_pony_files(self.repo_root)
        if not files:
            return PonyAnalysisResult(
                symbols=[],
                edges=[],
                run=None,
            )

        from tree_sitter_language_pack import get_parser

        parser = get_parser("pony")

        # Pass 1: Extract symbols
        for path in files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_symbols(tree.root_node, path)
                self._files_analyzed += 1
            except Exception:  # pragma: no cover  # noqa: S112  # nosec B112
                continue

        # Pass 2: Extract edges
        for path in files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_edges(tree.root_node, path)
            except Exception:  # pragma: no cover  # noqa: S112  # nosec B112
                continue

        duration_ms = int((time.time() - start_time) * 1000)

        run = AnalysisRun(
            pass_id=PASS_ID,
            execution_id=self._execution_id,
            version=PASS_VERSION,
            toolchain={"name": "pony", "version": "unknown"},
            duration_ms=duration_ms,
            files_analyzed=self._files_analyzed,
        )

        return PonyAnalysisResult(
            symbols=self._symbols,
            edges=self._edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type in ("actor_definition", "class_definition", "interface_definition",
                         "trait_definition", "primitive_definition"):
            self._extract_type_definition(node, path)
            return  # Don't recurse - _extract_type_definition handles members

        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_type_definition(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a type definition (actor, class, interface, trait, primitive)."""
        type_kind = node.type.replace("_definition", "")
        name = None

        for child in node.children:
            if child.type == "identifier":
                name = _get_node_text(child)
                break

        if not name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        symbol_id = _make_symbol_id(rel_path, name, type_kind)

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        # Count members
        method_count = 0
        constructor_count = 0
        field_count = 0
        for child in node.children:
            if child.type == "members":
                for member in child.children:
                    if member.type == "method":
                        method_count += 1
                    elif member.type == "constructor":
                        constructor_count += 1
                    elif member.type == "field":
                        field_count += 1

        meta = {
            "method_count": method_count,
            "constructor_count": constructor_count,
            "field_count": field_count,
        }

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=name,
            kind=type_kind,
            language="pony",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f"{type_kind} {name}",
            meta=meta,
        )
        self._symbols.append(symbol)
        self._symbol_registry[name] = symbol_id

        # Extract members
        old_type = self._current_type
        old_type_id = self._current_type_id
        self._current_type = name
        self._current_type_id = symbol_id

        for child in node.children:
            if child.type == "members":
                self._extract_members(child, path)

        self._current_type = old_type
        self._current_type_id = old_type_id

    def _extract_members(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract members (constructors, methods, fields) from a type."""
        for child in node.children:
            if child.type == "constructor":
                self._extract_constructor(child, path)
            elif child.type == "method":
                self._extract_method(child, path)
            elif child.type == "field":
                self._extract_field(child, path)

    def _extract_constructor(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a constructor (new)."""
        name = None
        params: list[str] = []

        for child in node.children:
            if child.type == "identifier":
                name = _get_node_text(child)
            elif child.type == "parameters":
                params = self._extract_parameters(child)

        if not name:
            return  # pragma: no cover

        full_name = f"{self._current_type}.{name}" if self._current_type else name
        rel_path = path.relative_to(self.repo_root)
        symbol_id = _make_symbol_id(rel_path, full_name, "constructor")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        signature = f"new {name}({', '.join(params)})"

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=full_name,
            kind="constructor",
            language="pony",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=signature,
            meta={"params": params, "parent_type": self._current_type},
        )
        self._symbols.append(symbol)
        self._symbol_registry[full_name] = symbol_id

    def _extract_method(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a method (fun)."""
        name = None
        params: list[str] = []
        capability = ""

        for child in node.children:
            if child.type == "identifier":
                name = _get_node_text(child)
            elif child.type == "parameters":
                params = self._extract_parameters(child)
            elif child.type == "capability":
                # capability wraps ref/val/box/iso/trn/tag
                for cap_child in child.children:
                    if cap_child.type in ("ref", "val", "box", "iso", "trn", "tag"):
                        capability = cap_child.type
                        break

        if not name:
            return  # pragma: no cover

        full_name = f"{self._current_type}.{name}" if self._current_type else name
        rel_path = path.relative_to(self.repo_root)
        symbol_id = _make_symbol_id(rel_path, full_name, "method")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        cap_str = f" {capability}" if capability else ""
        signature = f"fun{cap_str} {name}({', '.join(params)})"

        meta: dict = {"params": params, "parent_type": self._current_type}
        if capability:
            meta["capability"] = capability

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=full_name,
            kind="method",
            language="pony",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=signature,
            meta=meta,
        )
        self._symbols.append(symbol)
        self._symbol_registry[full_name] = symbol_id

    def _extract_field(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a field (var or let)."""
        name = None
        field_type = "var"

        for child in node.children:
            if child.type == "identifier":
                name = _get_node_text(child)
            elif child.type in ("var", "let"):
                field_type = child.type

        if not name:
            return  # pragma: no cover

        # Skip underscore-prefixed private fields for cleaner output
        if name.startswith("_"):
            return

        full_name = f"{self._current_type}.{name}" if self._current_type else name
        rel_path = path.relative_to(self.repo_root)
        symbol_id = _make_symbol_id(rel_path, full_name, "field")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=full_name,
            kind="field",
            language="pony",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f"{field_type} {name}",
            meta={"field_type": field_type, "parent_type": self._current_type},
        )
        self._symbols.append(symbol)

    def _extract_parameters(self, node: "tree_sitter.Node") -> list[str]:
        """Extract parameter names from a parameters node."""
        params: list[str] = []
        for child in node.children:
            if child.type == "parameter":
                for param_child in child.children:
                    if param_child.type == "identifier":
                        params.append(_get_node_text(param_child))
                        break
        return params

    def _extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract call edges from the syntax tree."""
        # Track current context
        if node.type in ("actor_definition", "class_definition", "interface_definition",
                         "trait_definition", "primitive_definition"):
            for child in node.children:
                if child.type == "identifier":
                    name = _get_node_text(child)
                    self._current_type = name
                    self._current_type_id = self._symbol_registry.get(name)
                    break
            for child in node.children:
                self._extract_edges(child, path)
            self._current_type = None
            self._current_type_id = None
            return

        if node.type in ("constructor", "method"):
            for child in node.children:
                if child.type == "identifier":
                    name = _get_node_text(child)
                    full_name = f"{self._current_type}.{name}" if self._current_type else name
                    self._current_method = full_name
                    self._current_method_id = self._symbol_registry.get(full_name)
                    break
            for child in node.children:
                self._extract_edges(child, path)
            self._current_method = None
            self._current_method_id = None
            return

        if node.type == "call_expression":
            self._extract_call_edge(node, path)

        for child in node.children:
            self._extract_edges(child, path)

    def _extract_call_edge(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a call edge from a call expression."""
        callee_name = None
        rel_path = path.relative_to(self.repo_root)

        for child in node.children:
            if child.type == "member_expression":
                callee_name = self._get_member_expression_name(child)
            elif child.type == "identifier":
                callee_name = _get_node_text(child)

        if not callee_name:
            return  # pragma: no cover

        # Extract the method name for filtering
        parts = callee_name.split(".")
        method_name = parts[-1] if parts else callee_name
        type_name = parts[0] if len(parts) > 1 else ""

        # Skip builtins
        if method_name in self.BUILTINS or type_name in self.BUILTINS:
            return

        # Determine source
        src = self._current_method_id or self._current_type_id or f"pony:{rel_path}"

        # Try to resolve the target
        resolved_id = self._symbol_registry.get(callee_name)

        # Also try type.method format if we have a type
        if not resolved_id and len(parts) == 2:
            # Already in type.method format
            pass
        elif not resolved_id and self._current_type:
            # Try current_type.method_name
            local_name = f"{self._current_type}.{callee_name}"
            resolved_id = self._symbol_registry.get(local_name)

        if resolved_id:
            dst = resolved_id
            confidence = 1.0
        else:
            dst = f"pony:unresolved:{callee_name}"
            confidence = 0.6

        edge = Edge.create(
            src=src,
            dst=dst,
            edge_type="calls",
            line=node.start_point[0] + 1,
            origin=PASS_ID,
            origin_run_id=self._execution_id,
            evidence_type="static",
            confidence=confidence,
            evidence_lang="pony",
        )
        self._edges.append(edge)

    def _get_member_expression_name(self, node: "tree_sitter.Node") -> str:
        """Get the full name from a member expression (e.g., Counter.create)."""
        parts: list[str] = []
        self._collect_member_parts(node, parts)
        return ".".join(parts)

    def _collect_member_parts(self, node: "tree_sitter.Node", parts: list[str]) -> None:
        """Collect parts of a member expression."""
        for child in node.children:
            if child.type == "identifier":
                parts.append(_get_node_text(child))
            elif child.type == "member_expression":
                self._collect_member_parts(child, parts)


def analyze_pony(repo_root: Path) -> PonyAnalysisResult:
    """Analyze Pony files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        PonyAnalysisResult containing extracted symbols and edges
    """
    if not is_pony_tree_sitter_available():
        warnings.warn(
            "Pony analysis skipped: tree-sitter-pony not available",
            UserWarning,
            stacklevel=2,
        )
        return PonyAnalysisResult(
            symbols=[],
            edges=[],
            run=AnalysisRun(
                pass_id=PASS_ID,
                execution_id=f"uuid:{uuid.uuid4()}",
                version=PASS_VERSION,
                toolchain={"name": "pony", "version": "unknown"},
                duration_ms=0,
                files_analyzed=0,
            ),
            skipped=True,
            skip_reason="tree-sitter-pony not available",
        )

    analyzer = PonyAnalyzer(repo_root)
    return analyzer.analyze()
