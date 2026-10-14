# SPDX-License-Identifier: AGPL-3.0-or-later
"""COBOL analyzer using tree-sitter.

This module provides static analysis of COBOL source code to extract
programs, paragraphs, sections, and their call relationships.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:

Pass 1 (Symbol Extraction):
- Programs: PROGRAM-ID declarations
- Paragraphs: Procedure division paragraph headers
- Sections: Procedure division section headers
- Data items: every data description entry, in any DATA DIVISION section
  (WORKING-STORAGE, FILE, LINKAGE, ...)

Pass 2 (Edge Extraction):
- PERFORM edges: PERFORM paragraph-name statements
- CALL edges: CALL "program-name" statements
- Both carry the resolver's confidence scaled by 0.85; a target found nowhere
  still gets an edge, via ``make_unresolved_edge``.
- The caller is the last paragraph header seen; paragraph symbols span only
  their header line.

Identity (WI-tusad): every symbol mints the ADR-0035 producer id with
``overload_arity.declared_arity_stable_id``, no arity, over its full scope
chain: the enclosing programs (a contained program is read from the ``END
PROGRAM`` markers, since the grammar emits every program as a sibling), then a
paragraph's section, or a data item's group items by level number (``05
CUST-NAME`` under ``01 ACCOUNT-REC``; an 88 condition under the entry it
follows; a 66 renames under its record). So two programs' ``MAIN-PARA`` or two
records' ``CUST-NAME`` hold distinct ids, and deleting one moves no other id.
Emitted names stay bare; the chain is a hash input only.

Known gaps: a PERFORM target is looked up repo-wide by name, so a paragraph of
the same name in another file can be chosen; and a section header does not
update the current caller, so a statement in a section is credited to the
paragraph before it.

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the COBOL-specific extraction
logic.

COBOL-Specific Considerations
-----------------------------
COBOL has a very different structure from modern languages:
- Programs are identified by PROGRAM-ID in IDENTIFICATION DIVISION
- Code is organized into paragraphs and sections in PROCEDURE DIVISION
- PERFORM is the primary control flow mechanism
- CALL invokes external programs
- Data is declared in DATA DIVISION with level numbers
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    make_unresolved_edge,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.overload_arity import declared_arity_stable_id

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("cobol")


def is_cobol_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with COBOL support is available."""
    return _analyzer._check_grammar_available()


def find_cobol_files(repo_root: Path) -> Iterator[Path]:
    """Find all COBOL files in the repository."""
    yield from find_files(repo_root, ["*.cob", "*.cbl", "*.cobol", "*.cpy"])


def _extract_text(node: "tree_sitter.Node", source_bytes: bytes) -> str:
    """Extract text from a node."""
    return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")


def _find_all_descendants(node: "tree_sitter.Node", target_types: set) -> list:
    """Find all descendant nodes of given types."""
    results = []
    for n in iter_tree(node):
        if n.type in target_types:
            results.append(n)
    return results


def _program_chains(
    root: "tree_sitter.Node", source_bytes: bytes,
) -> dict[int, list[str]]:
    """Each ``program_definition``'s scope chain, outermost program first.

    The grammar does not nest a contained program inside its container: every
    program is a sibling, and the container's ``END PROGRAM`` lands inside the
    last program it contains. So the nesting is read from the ``END PROGRAM``
    markers in document order: a program opens on the stack of programs not yet
    ended, and an ``END PROGRAM X`` closes ``X`` and everything opened after
    it. A program with no ``END PROGRAM`` (a file's only program) stays open
    to the end of the file. Keyed by the node's start byte.
    """
    chains: dict[int, list[str]] = {}
    open_programs: list[str] = []
    for program_def in _find_all_descendants(root, {"program_definition"}):
        id_div = find_child_by_type(program_def, "identification_division")
        name_node = find_child_by_type(id_div, "program_name") if id_div else None
        if name_node is None:
            continue
        open_programs.append(_extract_text(name_node, source_bytes).strip())
        chains[program_def.start_byte] = list(open_programs)
        for end in _find_all_descendants(program_def, {"end_program"}):
            end_name = find_child_by_type(end, "program_name")
            ended = _extract_text(end_name, source_bytes).strip() if end_name else ""
            if ended in open_programs:
                del open_programs[len(open_programs) - 1 - open_programs[::-1].index(ended):]
    return chains


def _program_chain_of(node: "tree_sitter.Node", chains: dict[int, list[str]]) -> list[str]:
    """The scope chain of the program whose definition contains ``node``."""
    current = node.parent
    while current is not None:
        if current.type == "program_definition":
            return chains.get(current.start_byte, [])
        current = current.parent
    return []  # pragma: no cover - every entry sits in a program_definition


def _level(level: str) -> int:
    """A level number as an int; a malformed one starts a new record."""
    return int(level) if level.isdigit() else 1


def _stable_id(kind: str, rel_path: str, name: str, chain: list[str]) -> str:
    """The ADR-0035 producer id (WI-tusad): no arity (a program's USING list
    does not overload it), the full scope chain joined by ``/``."""
    return declared_arity_stable_id(
        kind=kind, lang="cobol", path=rel_path, name=name,
        qualified_name="/".join([*chain, name]), arity=None,
    )


# ---------------------------------------------------------------------------
# CobolAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class CobolAnalyzer(TreeSitterAnalyzer):
    """COBOL language analyzer using tree-sitter-language-pack."""

    lang = "cobol"
    pass_version = "0.1.0"
    file_patterns: ClassVar[list[str]] = ["*.cob", "*.cbl", "*.cobol", "*.cpy"]
    language_pack_name = "cobol"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract programs, paragraphs, sections, and data items from COBOL."""
        analysis = FileAnalysis()
        root = tree.root_node
        chains = _program_chains(root, source)

        # Find program definition
        for program_def in _find_all_descendants(root, {"program_definition"}):
            id_div = find_child_by_type(program_def, "identification_division")
            if id_div:
                program_name_node = find_child_by_type(id_div, "program_name")
                if program_name_node:
                    name = _extract_text(program_name_node, source).strip()
                    start_line = program_def.start_point[0] + 1
                    end_line = program_def.end_point[0] + 1
                    sym = Symbol(
                        id=make_symbol_id("cobol", rel_path, start_line, end_line, name, "program"),
                        stable_id=_stable_id(
                            "program", rel_path, name,
                            chains[program_def.start_byte][:-1],
                        ),
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
                    analysis.symbols.append(sym)
                    analysis.symbol_by_name[name.upper()] = sym

        # Find paragraphs and sections in procedure division
        for proc_div in _find_all_descendants(root, {"procedure_division"}):
            program_chain = _program_chain_of(proc_div, chains)
            section_chain = program_chain
            for node in proc_div.children:
                if node.type == "paragraph_header":
                    name = _extract_text(node, source).rstrip(".").strip()
                    if name:
                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1
                        sym = Symbol(
                            id=make_symbol_id("cobol", rel_path, start_line, end_line, name, "paragraph"),
                            stable_id=_stable_id("paragraph", rel_path, name, section_chain),
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
                        analysis.symbols.append(sym)
                        analysis.symbol_by_name[name.upper()] = sym
                elif node.type == "section_header":
                    full_text = _extract_text(node, source).strip()
                    parts = full_text.split()
                    name = parts[0] if parts else ""
                    if name:
                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1
                        section_chain = [*program_chain, name]
                        sym = Symbol(
                            id=make_symbol_id("cobol", rel_path, start_line, end_line, name, "section"),
                            stable_id=_stable_id("section", rel_path, name, program_chain),
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
                        analysis.symbols.append(sym)
                        analysis.symbol_by_name[name.upper()] = sym

        # Find data items in DATA DIVISION. Entries are siblings; the group
        # an entry belongs to is read from the level numbers above it:
        # 01/77 start a record, 66 renames within the record, 88 is a
        # condition on the entry before it, 02-49 nest under a lower level.
        groups: list[tuple[int, str]] = []
        container = -1
        for data_desc in _find_all_descendants(root, {"data_description"}):
            entry_name = find_child_by_type(data_desc, "entry_name")
            if entry_name:
                name = _extract_text(entry_name, source).strip()
                level_node = find_child_by_type(data_desc, "level_number")
                level = _extract_text(level_node, source).strip() if level_node else "01"
                parent = data_desc.parent
                if parent is not None and parent.start_byte != container:
                    groups, container = [], parent.start_byte
                number = _level(level)
                if number in (1, 77):
                    groups = []
                    owners: list[str] = []
                elif number == 66:
                    owners = [n for _, n in groups[:1]]
                elif number == 88:
                    owners = [n for _, n in groups]
                else:
                    while groups and groups[-1][0] >= number:
                        groups.pop()
                    owners = [n for _, n in groups]
                if number not in (66, 88):
                    groups.append((number, name))
                if name:
                    start_line = data_desc.start_point[0] + 1
                    end_line = data_desc.end_point[0] + 1
                    analysis.symbols.append(
                        Symbol(
                            id=make_symbol_id("cobol", rel_path, start_line, end_line, name, "data"),
                            stable_id=_stable_id(
                                "data", rel_path, name,
                                [*_program_chain_of(data_desc, chains), *owners],
                            ),
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

        return analysis

    def register_symbol(self, symbol: Symbol, global_symbols: dict[str, Symbol]) -> None:
        """Register symbols with uppercase names for case-insensitive matching."""
        if symbol.kind in ("paragraph", "section", "program"):
            global_symbols[symbol.name.upper()] = symbol
        else:
            global_symbols[symbol.name] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract PERFORM and CALL edges from COBOL procedure division."""
        edges: list[Edge] = []
        root = tree.root_node

        current_paragraph_name: str | None = None
        for proc_div in _find_all_descendants(root, {"procedure_division"}):
            for node in proc_div.children:
                if node.type == "paragraph_header":
                    current_paragraph_name = _extract_text(node, source).rstrip(".").strip()

                # Find PERFORM statements
                for perform in _find_all_descendants(node, {"perform_statement_call_proc"}):
                    procedure_node = find_child_by_type(perform, "perform_procedure")
                    if procedure_node:
                        label_node = find_child_by_type(procedure_node, "label")
                        if label_node:
                            target_name = _extract_text(label_node, source).strip()

                            if current_paragraph_name:
                                caller_sym = local_symbols.get(current_paragraph_name.upper())
                                src_id = caller_sym.id if caller_sym else make_file_id("cobol", rel_path)
                            else:  # pragma: no cover - file-level PERFORM is rare
                                src_id = make_file_id("cobol", rel_path)  # pragma: no cover

                            result = resolver.lookup(target_name.upper())
                            if result.symbol is not None:
                                edge = Edge.create(
                                    src=src_id,
                                    dst=result.symbol.id,
                                    edge_type="calls",
                                    line=perform.start_point[0] + 1,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_perform",
                                    confidence=0.85 * result.confidence,
                                )
                            else:  # pragma: no cover - unresolvable external paragraph
                                edge = make_unresolved_edge(  # pragma: no cover
                                    "cobol", src_id, target_name,
                                    perform.start_point[0] + 1,
                                    PASS_ID, run.execution_id,
                                )
                            edge.meta = {
                                "file": rel_path,
                                "call_type": "perform",
                            }
                            edges.append(edge)

                # Find CALL statements
                for call in _find_all_descendants(node, {"call_statement"}):
                    string_node = find_child_by_type(call, "string")
                    if string_node:
                        target_name = _extract_text(string_node, source).strip().strip('"\'')

                        if current_paragraph_name:
                            caller_sym = local_symbols.get(current_paragraph_name.upper())
                            src_id = caller_sym.id if caller_sym else make_file_id("cobol", rel_path)
                        else:  # pragma: no cover - file-level CALL is rare
                            src_id = make_file_id("cobol", rel_path)  # pragma: no cover

                        result = resolver.lookup(target_name.upper())
                        if result.symbol is not None:
                            edge = Edge.create(
                                src=src_id,
                                dst=result.symbol.id,
                                edge_type="calls",
                                line=call.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="ast_call",
                                confidence=0.85 * result.confidence,
                            )
                        else:
                            edge = make_unresolved_edge(
                                "cobol", src_id, target_name,
                                call.start_point[0] + 1,
                                PASS_ID, run.execution_id,
                            )
                        edge.meta = {
                            "file": rel_path,
                            "call_type": "call",
                        }
                        edges.append(edge)

        return edges


_analyzer = CobolAnalyzer()


@register_analyzer("cobol")
def analyze_cobol(repo_root: Path) -> AnalysisResult:
    """Analyze COBOL files in the repository."""
    return _analyzer.analyze(repo_root)
