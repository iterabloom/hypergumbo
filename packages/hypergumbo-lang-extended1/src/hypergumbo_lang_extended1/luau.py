"""Luau language analyzer.

This module analyzes Luau files (.luau, .lua) using tree-sitter. Luau is
Roblox's typed Lua variant used for game development on the Roblox platform.
It extends Lua with a gradual type system.

How It Works
------------
- Pass 1: Collect symbols (functions, types, variables)
- Pass 2: Extract edges (function calls)

Symbol Types
------------
- function: Local and module function definitions
- type: Type definitions (type and export type)
- variable: Local variable declarations

Edge Types
----------
- calls: Function calls from one symbol to another
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

PASS_ID = "luau.tree_sitter"
PASS_VERSION = "0.1.0"

# Built-in Luau/Roblox functions and services to filter out
LUAU_BUILTINS = frozenset({
    # Lua builtins
    "print", "error", "warn", "assert", "type", "typeof", "tonumber", "tostring",
    "pairs", "ipairs", "next", "select", "unpack", "rawget", "rawset", "rawequal",
    "setmetatable", "getmetatable", "pcall", "xpcall", "require", "loadstring",
    # Table functions
    "table", "insert", "remove", "sort", "concat", "find", "clear", "clone",
    "move", "freeze", "isfrozen",
    # String functions
    "string", "format", "sub", "match", "gsub", "gmatch", "lower",
    "upper", "len", "rep", "char", "byte", "reverse", "split", "pack",
    # Math functions
    "math", "abs", "acos", "asin", "atan", "atan2", "ceil", "cos", "cosh",
    "deg", "exp", "floor", "fmod", "frexp", "ldexp", "log", "log10", "max",
    "min", "modf", "pow", "rad", "random", "randomseed", "sin", "sinh",
    "sqrt", "tan", "tanh", "clamp", "sign", "noise", "round",
    # Coroutine
    "coroutine", "create", "resume", "yield", "status", "wrap", "running",
    # OS
    "os", "time", "date", "difftime", "clock",
    # Debug
    "debug", "traceback", "info", "profilebegin", "profileend", "setmemorycategory",
    # Task
    "task", "spawn", "delay", "defer", "wait", "cancel", "synchronize", "desynchronize",
    # Roblox globals
    "game", "workspace", "script", "plugin", "Instance", "Enum", "Vector2",
    "Vector3", "CFrame", "Color3", "BrickColor", "UDim", "UDim2", "Ray",
    "Rect", "Region3", "TweenInfo", "NumberSequence", "ColorSequence",
    "NumberRange", "Font", "tick", "elapsedTime",
    "settings", "UserSettings", "Stats", "shared", "_G",
    # Common service getters
    "GetService", "FindFirstChild", "WaitForChild", "GetChildren", "GetDescendants",
    "Clone", "Destroy", "new", "Connect", "Disconnect", "Fire", "Wait",
})


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_stable_id(path: Path, repo_root: Path, name: str, kind: str) -> str:
    """Create a stable identifier for a symbol."""
    rel_path = str(path.relative_to(repo_root))
    return f"luau:{rel_path}:{kind}:{name}"


def find_luau_files(repo_root: Path) -> list[Path]:
    """Find all Luau files in the repository."""
    luau_files = list(repo_root.glob("**/*.luau"))
    lua_files = list(repo_root.glob("**/*.lua"))
    return sorted(luau_files + lua_files)


def is_luau_tree_sitter_available() -> bool:
    """Check if tree-sitter-luau is available."""
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("luau")
        return True
    except Exception:  # pragma: no cover
        return False


class LuauAnalysisResult:
    """Result of Luau analysis."""

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


class LuauAnalyzer:
    """Analyzer for Luau files."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self._symbol_registry: dict[str, str] = {}  # name -> id
        self._run_id: str = ""
        self._current_function: Optional[str] = None

    def analyze(self) -> LuauAnalysisResult:
        """Analyze all Luau files in the repository."""
        from tree_sitter_language_pack import get_parser

        start_time = time.time()
        self._run_id = f"uuid:{uuid_module.uuid4()}"

        parser = get_parser("luau")
        luau_files = find_luau_files(self.repo_root)

        if not luau_files:
            return LuauAnalysisResult()

        # Pass 1: Collect all symbols
        for path in luau_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_symbols(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        # Build symbol registry
        for sym in self.symbols:
            self._symbol_registry[sym.name] = sym.id
            # Also register short name (without module prefix)
            if "." in sym.name:
                short_name = sym.name.split(".")[-1]
                if short_name not in self._symbol_registry:
                    self._symbol_registry[short_name] = sym.id
            if ":" in sym.name:
                short_name = sym.name.split(":")[-1]
                if short_name not in self._symbol_registry:
                    self._symbol_registry[short_name] = sym.id

        # Pass 2: Extract edges
        for path in luau_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._current_function = None
                self._extract_edges(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        elapsed = time.time() - start_time

        run = AnalysisRun(
            pass_id=PASS_ID,
            execution_id=self._run_id,
            version=PASS_VERSION,
            toolchain={"name": "luau", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return LuauAnalysisResult(
            symbols=self.symbols,
            edges=self.edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "function_declaration":
            self._extract_function(node, path)

        elif node.type == "type_definition":
            self._extract_type(node, path)

        elif node.type == "variable_declaration":
            self._extract_variable(node, path)

        # Recurse into children
        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_function(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a function definition."""
        name = None
        is_local = False
        params: list[str] = []
        return_type = None

        for child in node.children:
            if child.type == "local":
                is_local = True
            elif child.type == "identifier" and name is None:
                name = _get_node_text(child)
            elif child.type == "dot_index_expression":
                # Module.method style
                name = _get_node_text(child)
            elif child.type == "method_index_expression":
                # Module:method style
                name = _get_node_text(child)
            elif child.type == "parameters":
                params = self._extract_params(child)
            elif child.type == ":" and return_type is None:
                # Next identifier is return type
                pass
            elif child.type == "identifier" and name is not None:  # pragma: no cover
                # This could be the return type (complex to hit in tests)
                return_type = _get_node_text(child)

        if name:
            rel_path = str(path.relative_to(self.repo_root))

            meta: dict[str, object] = {}
            if is_local:
                meta["local"] = True
            if params:
                meta["params"] = params
            if return_type:  # pragma: no cover
                meta["return_type"] = return_type

            signature = f"{name}({', '.join(params)})"

            sym = Symbol(
                id=_make_stable_id(path, self.repo_root, name, "function"),
                stable_id=_make_stable_id(path, self.repo_root, name, "function"),
                name=name,
                kind="function",
                language="luau",
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

    def _extract_type(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a type definition."""
        name = None
        is_exported = False

        for child in node.children:
            if child.type == "export":
                is_exported = True
            elif child.type == "identifier":
                name = _get_node_text(child)
                break

        if name:
            rel_path = str(path.relative_to(self.repo_root))

            meta: dict[str, object] = {}
            if is_exported:
                meta["exported"] = True

            sym = Symbol(
                id=_make_stable_id(path, self.repo_root, name, "type"),
                stable_id=_make_stable_id(path, self.repo_root, name, "type"),
                name=name,
                kind="type",
                language="luau",
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

    def _extract_variable(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a variable declaration."""
        # Only extract top-level module tables (e.g., local MyModule = {})
        # Skip simple local variables
        for child in node.children:
            if child.type == "assignment_statement":
                for subchild in child.children:
                    if subchild.type == "variable_list":
                        for var in subchild.children:
                            if var.type == "identifier":
                                name = _get_node_text(var)
                                # Only extract if it looks like a module (capital letter)
                                if name and name[0].isupper():
                                    rel_path = str(path.relative_to(self.repo_root))
                                    sym = Symbol(
                                        id=_make_stable_id(path, self.repo_root, name, "variable"),
                                        stable_id=_make_stable_id(path, self.repo_root, name, "variable"),
                                        name=name,
                                        kind="variable",
                                        language="luau",
                                        path=rel_path,
                                        span=Span(
                                            start_line=node.start_point[0] + 1,
                                            end_line=node.end_point[0] + 1,
                                            start_col=node.start_point[1],
                                            end_col=node.end_point[1],
                                        ),
                                        origin=PASS_ID,
                                        meta={"local": True},
                                    )
                                    self.symbols.append(sym)

    def _extract_params(self, node: "tree_sitter.Node") -> list[str]:
        """Extract parameter names from a parameters node."""
        params: list[str] = []
        for child in node.children:
            if child.type == "parameter":
                param_name = None
                param_type = None
                for subchild in child.children:
                    if subchild.type == "identifier":
                        param_name = _get_node_text(subchild)
                    elif subchild.type == ":" and param_type is None:
                        pass
                    elif param_name is not None and subchild.type not in ("(", ")", ",", ":"):
                        # Type annotation
                        type_text = _get_node_text(subchild)
                        if type_text:
                            param_type = type_text
                if param_name:
                    if param_type:
                        params.append(f"{param_name}: {param_type}")
                    else:
                        params.append(param_name)
        return params

    def _extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract edges from a syntax tree node."""
        if node.type == "function_declaration":
            # Track current function context
            name = None
            for child in node.children:
                if child.type == "identifier" and name is None:
                    name = _get_node_text(child)
                    break
                elif child.type == "dot_index_expression":
                    name = _get_node_text(child)
                    break
                elif child.type == "method_index_expression":
                    name = _get_node_text(child)
                    break

            if name:
                prev_function = self._current_function
                self._current_function = name

                # Extract calls from function body
                for child in node.children:
                    if child.type == "block":
                        self._extract_calls(child, path)

                self._current_function = prev_function
                return

        elif node.type == "function_call":  # pragma: no cover
            # Top-level function calls (rare in Luau, usually wrapped)
            self._extract_function_call(node, path)

        # Recurse into children
        for child in node.children:
            self._extract_edges(child, path)

    def _extract_calls(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract function calls from a code block."""
        if node.type == "function_call":
            self._extract_function_call(node, path)

        for child in node.children:
            self._extract_calls(child, path)

    def _extract_function_call(
        self, node: "tree_sitter.Node", path: Path
    ) -> None:
        """Extract a function call edge."""
        call_name = None

        for child in node.children:
            if child.type == "identifier":
                call_name = _get_node_text(child)
                break
            elif child.type == "dot_index_expression":
                # Module.method or object.method
                call_name = _get_node_text(child)
                break
            elif child.type == "method_index_expression":
                # object:method
                call_name = _get_node_text(child)
                break

        if call_name:
            # Skip built-in functions
            base_name = call_name.split(".")[0] if "." in call_name else call_name
            base_name = base_name.split(":")[0] if ":" in base_name else base_name
            method_name = call_name.split(".")[-1] if "." in call_name else call_name
            method_name = method_name.split(":")[-1] if ":" in method_name else method_name

            if base_name in LUAU_BUILTINS or method_name in LUAU_BUILTINS:
                return

            self._add_call_edge(path, call_name, node.start_point[0] + 1)

    def _add_call_edge(self, path: Path, call_name: str, line: int) -> None:
        """Add a call edge."""
        # Determine source
        if self._current_function:
            src_name = self._current_function
        else:  # pragma: no cover
            # Module-level call (rare in Luau)
            src_name = str(path.relative_to(self.repo_root))

        # Try to resolve the callee
        dst_id = self._symbol_registry.get(call_name)
        if not dst_id:
            # Try short name
            short_name = call_name.split(".")[-1] if "." in call_name else call_name
            short_name = short_name.split(":")[-1] if ":" in short_name else short_name
            dst_id = self._symbol_registry.get(short_name)

        if dst_id:
            confidence = 1.0
            dst = dst_id
        else:
            confidence = 0.6
            dst = f"unresolved:{call_name}"

        src_id = self._symbol_registry.get(
            src_name, f"luau:{path.relative_to(self.repo_root)}:file"
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
            evidence_lang="luau",
        )
        self.edges.append(edge)


def analyze_luau(repo_root: Path) -> LuauAnalysisResult:
    """Analyze Luau files in the repository.

    Args:
        repo_root: Root path of the repository to analyze

    Returns:
        LuauAnalysisResult containing symbols and edges
    """
    if not is_luau_tree_sitter_available():
        warnings.warn(
            "Luau analysis skipped: tree-sitter-luau not available",
            UserWarning,
            stacklevel=2,
        )
        return LuauAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-luau not available",
        )

    analyzer = LuauAnalyzer(repo_root)
    return analyzer.analyze()
