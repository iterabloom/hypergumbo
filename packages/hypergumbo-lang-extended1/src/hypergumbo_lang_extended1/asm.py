"""Assembly language analysis pass using tree-sitter.

Extracts labels (functions), data symbols, and call edges from assembly source
files (.s, .asm, .S). Assembly is used in operating system kernels, bootloaders,
embedded systems, and performance-critical routines within larger C/C++ projects.

How It Works
------------
1. Check if tree-sitter with asm grammar is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract labels as symbols
   - Pass 2: Detect call instructions and resolve targets against label registry
4. Labels in .text sections become functions; labels in .data/.bss become variables

Why This Design
---------------
- The tree-sitter-asm grammar produces label nodes and instruction nodes
- Labels serve as both function entry points and data labels
- Call instructions reference labels by name — simple string matching resolves them
- Two-pass allows cross-file resolution (common in multi-file assembly projects)

Assembly-Specific Considerations
-------------------------------
- Labels are the primary symbols (no function keyword in assembly)
- .global directives indicate exported symbols
- Call targets may be external (libc functions, syscalls)
- Section directives (.text, .data, .bss) hint at symbol purpose
"""
from __future__ import annotations

import hashlib
import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.analyze.base import iter_tree
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "asm-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_asm_files(repo_root: Path) -> Iterator[Path]:
    """Yield all assembly files in the repository."""
    yield from find_files(repo_root, ["*.s", "*.asm", "*.S"])


def is_asm_tree_sitter_available() -> bool:
    """Check if tree-sitter with asm grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False  # pragma: no cover - language pack not installed
    try:
        from tree_sitter_language_pack import get_language

        get_language("asm")
        return True
    except Exception:  # pragma: no cover - asm grammar not available
        return False


@dataclass
class AsmAnalysisResult:
    """Result of analyzing assembly files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text from a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(
    node: "tree_sitter.Node", child_type: str
) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == child_type:
            return child
    return None


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID for a symbol."""
    return f"asm:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _determine_label_kind(current_section: str) -> str:
    """Determine label kind based on current section.

    Labels in .text are functions; labels in .data/.bss/.rodata are variables.
    Default to function for unknown sections.
    """
    if current_section in (".data", ".bss", ".rodata"):
        return "variable"
    return "function"


@register_analyzer("asm")
def analyze_asm(repo_root: Path) -> AsmAnalysisResult:
    """Analyze assembly language files in a repository.

    Uses two-pass analysis:
    - Pass 1: Extract all labels as symbols
    - Pass 2: Detect call instructions and resolve targets

    Returns an AsmAnalysisResult with symbols for labels and edges for calls.
    """
    if not is_asm_tree_sitter_available():
        warnings.warn("Assembly analysis skipped: tree-sitter-asm unavailable")
        return AsmAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-asm unavailable",
        )

    from tree_sitter_language_pack import get_parser

    parser = get_parser("asm")

    symbols: list[Symbol] = []
    edges: list[Edge] = []
    files_analyzed = 0
    run_id = hashlib.sha256(str(time.time()).encode()).hexdigest()[:16]
    start_time = time.time()

    # Global label registry for cross-file resolution
    label_registry: dict[str, Symbol] = {}

    # Store parsed files for pass 2
    parsed_files: list[tuple[str, bytes, object]] = []

    # Pass 1: Extract labels from all files
    for file_path in find_asm_files(repo_root):
        try:
            source = file_path.read_bytes()
        except (OSError, IOError):  # pragma: no cover
            continue

        tree = parser.parse(source)
        files_analyzed += 1

        rel_path = str(file_path.relative_to(repo_root))

        # Track current section for label kind inference
        current_section = ".text"

        for node in iter_tree(tree.root_node):
            # Track section changes
            if node.type == "meta":
                meta_ident = _find_child_by_type(node, "meta_ident")
                if meta_ident:
                    directive = _node_text(meta_ident, source)
                    if directive == ".section":
                        ident = _find_child_by_type(node, "ident")
                        if ident:
                            section_name = _node_text(ident, source)
                            current_section = section_name

            # Extract labels
            elif node.type == "label":
                ident = _find_child_by_type(node, "ident")
                if ident:
                    label_name = _node_text(ident, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    kind = _determine_label_kind(current_section)

                    sym = Symbol(
                        id=_make_symbol_id(rel_path, start_line, end_line, label_name, kind),
                        name=label_name,
                        canonical_name=label_name,
                        kind=kind,
                        language="asm",
                        path=rel_path,
                        span=Span(
                            start_line=start_line,
                            start_col=node.start_point[1],
                            end_line=end_line,
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        stable_id=f"asm:{rel_path}:{label_name}",
                    )
                    symbols.append(sym)
                    label_registry[label_name] = sym

        parsed_files.append((rel_path, source, tree))

    # Pass 2: Extract call edges
    for rel_path, source, tree in parsed_files:
        # Build map of labels in this file to find enclosing function
        file_labels: list[tuple[int, Symbol]] = sorted(
            [(s.span.start_line, s) for s in symbols
             if s.path == rel_path and s.kind == "function"],
            key=lambda x: x[0],
        )

        for node in iter_tree(tree.root_node):
            if node.type != "instruction":
                continue

            # Check if this is a call instruction
            word_node = _find_child_by_type(node, "word")
            if not word_node:
                continue  # pragma: no cover - defensive against unusual AST shape
            opcode = _node_text(word_node, source).lower()
            if opcode != "call":
                continue

            # Get call target
            target_node = _find_child_by_type(node, "ident")
            if not target_node:
                continue  # pragma: no cover - defensive
            target_name = _node_text(target_node, source)

            # Find enclosing label (function) for this call instruction
            call_line = node.start_point[0] + 1
            caller = _find_enclosing_label(call_line, file_labels)
            if not caller:
                continue  # pragma: no cover - defensive

            # Resolve call target
            target_sym = label_registry.get(target_name)
            if target_sym:
                dst_id = target_sym.id
                confidence = 0.85
            else:
                dst_id = f"asm:external:{target_name}:function"
                confidence = 0.70

            edges.append(Edge(
                id=_make_edge_id(caller.id, dst_id, "calls"),
                src=caller.id,
                dst=dst_id,
                edge_type="calls",
                line=call_line,
                confidence=confidence,
                origin=PASS_ID,
                origin_run_id=run_id,
            ))

    duration_ms = int((time.time() - start_time) * 1000)
    return AsmAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=AnalysisRun(
            execution_id=run_id,
            pass_id=PASS_ID,
            version=PASS_VERSION,
            files_analyzed=files_analyzed,
            duration_ms=duration_ms,
        ),
    )


def _find_enclosing_label(
    line: int, file_labels: list[tuple[int, Symbol]]
) -> Optional[Symbol]:
    """Find the most recent label before the given line.

    Assembly doesn't have explicit function boundaries — a label's scope
    extends until the next label. So the enclosing function for a call
    instruction at line N is the last label defined before line N.
    """
    result = None
    for label_line, sym in file_labels:
        if label_line <= line:
            result = sym
        else:
            break
    return result
