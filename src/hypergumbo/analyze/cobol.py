"""COBOL analyzer using tree-sitter.

This module provides static analysis of COBOL source code to extract
programs, paragraphs, sections, and their call relationships.

How It Works
------------
Uses tree-sitter-language-pack for COBOL parsing. Two-pass analysis:

Pass 1 (Symbol Extraction):
- Programs: PROGRAM-ID declarations
- Paragraphs: Procedure division paragraph headers
- Sections: Procedure division section headers
- Data items: WORKING-STORAGE and FILE SECTION entries

Pass 2 (Edge Extraction):
- PERFORM edges: PERFORM paragraph-name statements
- CALL edges: CALL "program-name" statements

COBOL-Specific Considerations
-----------------------------
COBOL has a very different structure from modern languages:
- Programs are identified by PROGRAM-ID in IDENTIFICATION DIVISION
- Code is organized into paragraphs and sections in PROCEDURE DIVISION
- PERFORM is the primary control flow mechanism
- CALL invokes external programs
- Data is declared in DATA DIVISION with level numbers
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from hypergumbo.ir import AnalysisRun, Edge, Span, Symbol

from .base import iter_tree

PASS_ID = "cobol"


@dataclass
class COBOLAnalysisResult:
    """Result of analyzing COBOL files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: Optional[AnalysisRun] = None
    skipped: bool = False
    skipped_reason: str = ""


def is_cobol_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with COBOL support is available."""
    try:
        from tree_sitter_language_pack import get_language

        get_language("cobol")
        return True
    except (ImportError, KeyError):  # pragma: no cover
        return False


def _get_parser():
    """Get a parser for COBOL."""
    from tree_sitter_language_pack import get_parser

    return get_parser("cobol")


def _make_symbol_id(
    path: str, start_line: int, end_line: int, name: str, kind: str
) -> str:
    """Generate location-based ID for a symbol."""
    return f"cobol:{path}:{start_line}-{end_line}:{name}:{kind}"


def _extract_text(node, source_bytes: bytes) -> str:
    """Extract text from a node."""
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")


def _find_child(node, child_type: str):
    """Find first child of given type."""
    for child in node.children:
        if child.type == child_type:
            return child
    return None  # pragma: no cover


def _find_all_descendants(node, target_types: set):
    """Find all descendant nodes of given types."""
    results = []
    for n in iter_tree(node):
        if n.type in target_types:
            results.append(n)
    return results


def _extract_symbols_from_file(
    rel_path: str, source_bytes: bytes, tree, run: AnalysisRun
) -> list[Symbol]:
    """Extract symbols from a parsed COBOL file."""
    symbols = []

    root = tree.root_node

    # Find program definition
    for program_def in _find_all_descendants(root, {"program_definition"}):
        # Find program name in identification_division
        id_div = _find_child(program_def, "identification_division")
        if id_div:
            program_name_node = _find_child(id_div, "program_name")
            if program_name_node:
                name = _extract_text(program_name_node, source_bytes).strip()
                start_line = program_def.start_point[0] + 1
                end_line = program_def.end_point[0] + 1
                symbols.append(
                    Symbol(
                        id=_make_symbol_id(rel_path, start_line, end_line, name, "program"),
                        name=name,
                        kind="program",
                        language="cobol",
                        path=rel_path,
                        span=Span(
                            start_line=start_line,
                            start_col=program_def.start_point[1],
                            end_line=end_line,
                            end_col=program_def.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    )
                )

    # Find paragraphs and sections in procedure division
    for proc_div in _find_all_descendants(root, {"procedure_division"}):
        for node in proc_div.children:
            if node.type == "paragraph_header":
                # Extract paragraph name (remove trailing period)
                name = _extract_text(node, source_bytes).rstrip(".")
                name = name.strip()
                if name:
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    symbols.append(
                        Symbol(
                            id=_make_symbol_id(rel_path, start_line, end_line, name, "paragraph"),
                            name=name,
                            kind="paragraph",
                            language="cobol",
                            path=rel_path,
                            span=Span(
                                start_line=start_line,
                                start_col=node.start_point[1],
                                end_line=end_line,
                                end_col=node.end_point[1],
                            ),
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        )
                    )
            elif node.type == "section_header":
                # Extract section name (remove trailing " SECTION." part)
                full_text = _extract_text(node, source_bytes).strip()
                # Format is "NAME SECTION." - extract just the name part
                parts = full_text.split()
                name = parts[0] if parts else ""
                if name:
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    symbols.append(
                        Symbol(
                            id=_make_symbol_id(rel_path, start_line, end_line, name, "section"),
                            name=name,
                            kind="section",
                            language="cobol",
                            path=rel_path,
                            span=Span(
                                start_line=start_line,
                                start_col=node.start_point[1],
                                end_line=end_line,
                                end_col=node.end_point[1],
                            ),
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        )
                    )

    # Find data items in DATA DIVISION
    for data_desc in _find_all_descendants(root, {"data_description"}):
        entry_name = _find_child(data_desc, "entry_name")
        if entry_name:
            name = _extract_text(entry_name, source_bytes).strip()
            level_node = _find_child(data_desc, "level_number")
            level = _extract_text(level_node, source_bytes).strip() if level_node else "01"
            if name:
                start_line = data_desc.start_point[0] + 1
                end_line = data_desc.end_point[0] + 1
                symbols.append(
                    Symbol(
                        id=_make_symbol_id(rel_path, start_line, end_line, name, "data"),
                        name=name,
                        kind="data",
                        language="cobol",
                        path=rel_path,
                        span=Span(
                            start_line=start_line,
                            start_col=data_desc.start_point[1],
                            end_line=end_line,
                            end_col=data_desc.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        meta={"level": level},
                    )
                )

    return symbols


def _extract_edges_from_file(
    rel_path: str, source_bytes: bytes, tree, symbol_names: set[str]
) -> list[Edge]:
    """Extract edges from a parsed COBOL file."""
    edges = []

    root = tree.root_node

    # Find current paragraph for each statement
    current_paragraph = None
    for proc_div in _find_all_descendants(root, {"procedure_division"}):
        for node in proc_div.children:
            if node.type == "paragraph_header":
                current_paragraph = _extract_text(node, source_bytes).rstrip(".").strip()

            # Find PERFORM statements
            for perform in _find_all_descendants(node, {"perform_statement_call_proc"}):
                procedure_node = _find_child(perform, "perform_procedure")
                if procedure_node:
                    label_node = _find_child(procedure_node, "label")
                    if label_node:
                        target = _extract_text(label_node, source_bytes).strip()
                        source = current_paragraph or f"{rel_path}:file:"
                        edges.append(
                            Edge.create(
                                src=source,
                                dst=target,
                                edge_type="calls",
                                line=perform.start_point[0] + 1,
                                origin=PASS_ID,
                                evidence_type="ast_perform",
                            )
                        )
                        # Store call_type in meta for the last edge added
                        edges[-1].meta = {
                            "file": rel_path,
                            "call_type": "perform",
                        }

            # Find CALL statements
            for call in _find_all_descendants(node, {"call_statement"}):
                string_node = _find_child(call, "string")
                if string_node:
                    # Remove quotes from program name
                    target = _extract_text(string_node, source_bytes).strip().strip('"\'')
                    source = current_paragraph or f"{rel_path}:file:"
                    edges.append(
                        Edge.create(
                            src=source,
                            dst=target,
                            edge_type="calls",
                            line=call.start_point[0] + 1,
                            origin=PASS_ID,
                            evidence_type="ast_call",
                        )
                    )
                    # Store call_type in meta for the last edge added
                    edges[-1].meta = {
                        "file": rel_path,
                        "call_type": "call",
                    }

    return edges


def analyze_cobol(repo_root: Path) -> COBOLAnalysisResult:
    """Analyze COBOL files in the repository.

    Args:
        repo_root: Root directory of the repository

    Returns:
        COBOLAnalysisResult with symbols and edges from COBOL files
    """
    import warnings

    if not is_cobol_tree_sitter_available():
        warnings.warn(
            "tree-sitter-language-pack with COBOL support not available. "
            "Install with: pip install tree-sitter-language-pack",
            UserWarning,
            stacklevel=2,
        )
        return COBOLAnalysisResult(
            symbols=[],
            edges=[],
            skipped=True,
            skipped_reason="tree-sitter-cobol not available",
        )

    parser = _get_parser()

    # Create analysis run
    run = AnalysisRun.create(pass_id=PASS_ID, version="0.1.0")

    # Find COBOL files
    cobol_patterns = ["**/*.cob", "**/*.cbl", "**/*.cobol", "**/*.cpy"]
    cobol_files: list[Path] = []
    for pattern in cobol_patterns:
        cobol_files.extend(repo_root.glob(pattern))

    # Deduplicate
    cobol_files = list(set(cobol_files))

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    # Pass 1: Extract symbols
    file_trees: dict[Path, tuple[bytes, object]] = {}
    for file_path in cobol_files:
        try:
            source_bytes = file_path.read_bytes()
            tree = parser.parse(source_bytes)
            file_trees[file_path] = (source_bytes, tree)

            rel_path = str(file_path.relative_to(repo_root))
            file_symbols = _extract_symbols_from_file(rel_path, source_bytes, tree, run)
            symbols.extend(file_symbols)
        except (OSError, IOError):  # pragma: no cover
            continue

    # Build symbol name set for edge resolution
    symbol_names = {s.name for s in symbols}

    # Pass 2: Extract edges
    for file_path, (source_bytes, tree) in file_trees.items():
        rel_path = str(file_path.relative_to(repo_root))
        file_edges = _extract_edges_from_file(rel_path, source_bytes, tree, symbol_names)
        edges.extend(file_edges)

    # Update run stats
    run.files_analyzed = len(file_trees)

    return COBOLAnalysisResult(symbols=symbols, edges=edges, run=run)
