"""C analysis pass using tree-sitter-c.

This analyzer uses tree-sitter-c to parse C files and extract:
- Function definitions and declarations (symbols)
- Struct declarations (symbols)
- Typedef declarations (symbols)
- Enum declarations (symbols)
- Function call relationships (edges)
- JNI export patterns (Java_ClassName_methodName)

If tree-sitter-c is not installed, the analyzer gracefully degrades
and returns an empty result.

How It Works
------------
1. Check if tree-sitter and tree-sitter-c are available
2. If not available, return empty result (not an error, just no C analysis)
3. Check for C++ files in the repo; if present, skip .h files (the C++
   analyzer handles them with tree-sitter-cpp to avoid duplicate symbols)
4. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
5. Detect function calls and JNI export patterns

Why This Design
---------------
- Optional dependency keeps base install lightweight
- C support is separate from other languages to keep modules focused
- Two-pass allows cross-file call resolution
- Same pattern as PHP/JS analyzers for consistency
- .h dedup: Both C and C++ analyzers process .h files, creating 2x symbols.
  On Falco (C/C++ repo), 44/50 .h files were duplicated and C orphan rate
  was 92.1%. Fix: skip .h in C analyzer when C++ files exist.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    iter_tree,
    make_symbol_id,
    node_text,
    populate_docstrings_from_tree,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("c")


def _has_cpp_files(repo_root: Path) -> bool:
    """Check if the repository contains C++ files.

    Checks for C++ source extensions (.cpp, .cc, .cxx) and C++-specific
    header extensions (.hpp, .hxx). When any of these exist, .h files
    are presumed to be C++ headers and should be processed by the C++
    analyzer, not the C analyzer.
    """
    return any(find_files(repo_root, ["*.cpp", "*.cc", "*.cxx", "*.hpp", "*.hxx"]))


def find_c_files(
    repo_root: Path, *, include_headers: bool = True
) -> Iterator[Path]:
    """Yield all C files in the repository.

    When include_headers is True (default), headers (.h) are yielded before
    source files (.c) so that definitions can replace declarations when
    building the symbol registry.

    When include_headers is False, only .c files are yielded. This is used
    when C++ files exist in the repo, since the C++ analyzer already
    processes .h files with the C++ grammar.
    """
    if include_headers:
        yield from find_files(repo_root, ["*.h", "*.c"])
    else:
        yield from find_files(repo_root, ["*.c"])



def _find_identifier_in_children(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Find identifier name in node's children."""
    for child in node.children:
        if child.type in ("identifier", "type_identifier"):
            return node_text(child, source)
    return None


def _get_function_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function name from function_definition or declaration."""
    # Look for declarator which contains the function name
    for child in node.children:
        if child.type == "function_declarator":
            # Function declarator contains identifier
            return _find_identifier_in_children(child, source)
        elif child.type == "pointer_declarator":
            # Handle pointer return types: int* func()
            for subchild in child.children:
                if subchild.type == "function_declarator":
                    return _find_identifier_in_children(subchild, source)
    return None


def _find_function_declarator(node: "tree_sitter.Node") -> Optional["tree_sitter.Node"]:
    """Find the function_declarator node within a function definition or declaration."""
    for child in node.children:
        if child.type == "function_declarator":
            return child
        elif child.type == "pointer_declarator":
            # Pointer return type: int* func()
            for subchild in child.children:
                if subchild.type == "function_declarator":
                    return subchild
    return None  # pragma: no cover


def _extract_c_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a C function definition or declaration.

    Returns signature like "(int x, char* name) int" or "(void) void".
    """
    if node.type not in ("function_definition", "declaration"):
        return None  # pragma: no cover

    # Find function_declarator
    func_decl = _find_function_declarator(node)
    if not func_decl:
        return None  # pragma: no cover

    # Find parameter_list
    param_list = None
    for child in func_decl.children:
        if child.type == "parameter_list":
            param_list = child
            break

    if not param_list:
        return None  # pragma: no cover

    # Extract parameters
    param_strs: list[str] = []
    for child in param_list.children:
        if child.type == "parameter_declaration":
            # Get full text of parameter and clean it
            param_text = node_text(child, source).strip()
            param_strs.append(param_text)

    # Build signature with parameters
    sig = "(" + ", ".join(param_strs) + ")"

    # Extract return type (before the function_declarator)
    return_type_parts: list[str] = []
    for child in node.children:
        if child.type in ("function_declarator", "pointer_declarator"):
            break
        if child.type in ("primitive_type", "type_identifier", "sized_type_specifier",
                          "storage_class_specifier", "type_qualifier"):
            return_type_parts.append(node_text(child, source))

    if return_type_parts:
        return_type = " ".join(return_type_parts)
        # Add pointer indicator if function_declarator is wrapped in pointer_declarator
        for child in node.children:
            if child.type == "pointer_declarator":
                return_type += "*"
                break
        if return_type and return_type != "void":
            sig += f" {return_type}"

    return sig


def _get_c_parser() -> Optional["tree_sitter.Parser"]:
    """Get tree-sitter parser for C."""
    try:
        import tree_sitter
        import tree_sitter_c
    except ImportError:
        return None

    parser = tree_sitter.Parser()
    lang_ptr = tree_sitter_c.language()
    parser.language = tree_sitter.Language(lang_ptr)
    return parser



def _extract_symbols(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
) -> list[Symbol]:
    """Extract symbols from a parsed C tree (pass 1).

    Uses iterative traversal to avoid RecursionError on deeply nested code.
    """
    symbols: list[Symbol] = []

    for node in iter_tree(tree.root_node):
        # Function definitions
        if node.type == "function_definition":
            name = _get_function_name(node, source)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                signature = _extract_c_signature(node, source)
                symbol = Symbol(
                    id=make_symbol_id("c", str(file_path), span.start_line, span.end_line, name, "function"),
                    name=name,
                    kind="function",
                    language="c",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    signature=signature,
                )
                symbols.append(symbol)

        # Function declarations (prototypes)
        elif node.type == "declaration":
            # Check if this is a function declaration
            for child in node.children:
                if child.type == "function_declarator":
                    name = _find_identifier_in_children(child, source)
                    if name:
                        span = Span(
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        )
                        signature = _extract_c_signature(node, source)
                        symbol = Symbol(
                            id=make_symbol_id("c", str(file_path), span.start_line, span.end_line, name, "function"),
                            name=name,
                            kind="function",
                            language="c",
                            path=str(file_path),
                            span=span,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            signature=signature,
                            modifiers=["declaration"],
                        )
                        symbols.append(symbol)

        # Struct definitions (with body only — skip references like
        # ``struct stat sb;`` and forward declarations ``struct Foo;``)
        elif node.type == "struct_specifier":
            has_body = any(c.type == "field_declaration_list" for c in node.children)
            name = _find_identifier_in_children(node, source) if has_body else None
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbol = Symbol(
                    id=make_symbol_id("c", str(file_path), span.start_line, span.end_line, name, "struct"),
                    name=name,
                    kind="struct",
                    language="c",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                symbols.append(symbol)

        # Enum definitions (with body only — skip references like
        # ``enum Color c;`` and forward declarations ``enum Color;``)
        elif node.type == "enum_specifier":
            has_body = any(c.type == "enumerator_list" for c in node.children)
            name = _find_identifier_in_children(node, source) if has_body else None
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbol = Symbol(
                    id=make_symbol_id("c", str(file_path), span.start_line, span.end_line, name, "enum"),
                    name=name,
                    kind="enum",
                    language="c",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                symbols.append(symbol)

        # Typedef declarations
        elif node.type == "type_definition":
            # Find the typedef name (last identifier usually)
            name = None
            for child in node.children:
                if child.type == "type_identifier":
                    name = node_text(child, source)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbol = Symbol(
                    id=make_symbol_id("c", str(file_path), span.start_line, span.end_line, name, "typedef"),
                    name=name,
                    kind="typedef",
                    language="c",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                symbols.append(symbol)

    return symbols


def _get_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    global_symbols: dict[str, Symbol],
    local_symbols: dict[str, Symbol] | None = None,
) -> Optional[Symbol]:
    """Walk up to find the enclosing function definition.

    Checks ``local_symbols`` first (file-scoped, always has the current
    file's definition), then falls back to ``global_symbols`` with a path
    check.  This is essential for C repos where multiple files define the
    same function name (e.g. ``cmd_main`` in git's per-binary entry points).
    Without the local lookup, only the single global-registry winner
    would produce outgoing edges.
    """
    current = node.parent
    str_path = str(file_path)
    while current is not None:
        if current.type == "function_definition":
            name = _get_function_name(current, source)
            if name:
                # Prefer file-local symbol (always matches current file)
                if local_symbols and name in local_symbols:
                    return local_symbols[name]
                # Fall back to global symbol with path check
                if name in global_symbols:
                    func_sym = global_symbols[name]
                    if func_sym.path == str_path:
                        return func_sym
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
    global_symbols: dict[str, Symbol],
    resolver: NameResolver | None = None,
    local_symbols: dict[str, Symbol] | None = None,
) -> list[Edge]:
    """Extract edges from a parsed C tree (pass 2).

    Uses global symbol registry to resolve cross-file references.
    Uses ``local_symbols`` (file-scoped) to correctly identify the enclosing
    function even when multiple files define functions with the same name.
    Uses iterative traversal to avoid RecursionError on deeply nested code.
    """
    if resolver is None:  # pragma: no cover - defensive
        resolver = NameResolver(global_symbols)

    edges: list[Edge] = []

    for node in iter_tree(tree.root_node):
        # Function calls: func_name(...)
        if node.type == "call_expression":
            current_function = _get_enclosing_function(
                node, source, file_path, global_symbols, local_symbols,
            )
            if current_function:
                # Get the function being called
                func_node = node.child_by_field_name("function")
                if func_node and func_node.type == "identifier":
                    callee_name = node_text(func_node, source)
                    lookup_result = resolver.lookup(callee_name)
                    if lookup_result.found and lookup_result.symbol is not None:
                        edge = Edge.create(
                            src=current_function.id,
                            dst=lookup_result.symbol.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            confidence=0.95 * lookup_result.confidence,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            evidence_type="ast_call_direct",
                        )
                        edges.append(edge)

        # Explicit function pointer: &process
        elif node.type == "pointer_expression":
            children = node.children
            if children and node_text(children[0], source) == "&":
                ident = next(
                    (c for c in node.children if c.type == "identifier"), None,
                )
                if ident:
                    ref_name = node_text(ident, source)
                    current_function = _get_enclosing_function(
                        node, source, file_path, global_symbols, local_symbols,
                    )
                    if current_function:
                        lookup_result = resolver.lookup(ref_name)
                        if (
                            lookup_result.found
                            and lookup_result.symbol is not None
                            and lookup_result.symbol.kind == "function"
                            and lookup_result.symbol.id != current_function.id
                        ):
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=lookup_result.symbol.id,
                                edge_type="references",
                                line=node.start_point[0] + 1,
                                confidence=0.85 * lookup_result.confidence,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="function_pointer",
                            ))

    # Dispatch table detection: function pointers in static array initializers.
    # Pattern: static struct Foo table[] = { { "name", func_ptr }, ... };
    # Identifiers in initializer lists that resolve to known functions
    # become dispatches_to edges, reducing orphan rate for C codebases.
    #
    # After detecting dispatch tables, we also scan function bodies for
    # references to the dispatch table variable (e.g., get_builtin accessing
    # commands[]), creating uses_dispatch_table edges to complete the chain:
    # cmd_main -> get_builtin -> commands[] -> cmd_add, cmd_commit, ...
    _func_symbols = {
        name: sym for name, sym in global_symbols.items()
        if sym.kind == "function"
    }
    dispatch_tables: dict[str, str] = {}  # variable name -> stable ID
    for node in iter_tree(tree.root_node):
        if node.type != "init_declarator":
            continue
        # Must be inside a declaration (not inside a function body)
        if not node.parent or node.parent.type != "declaration":
            continue  # pragma: no cover - defensive
        # Look for array declarator with initializer list
        array_decl = None
        init_list = None
        for child in node.children:
            if child.type == "array_declarator":
                array_decl = child
            elif child.type == "initializer_list":
                init_list = child
        if array_decl is None or init_list is None:
            continue

        # Get the array variable name
        array_name = None
        for child in array_decl.children:
            if child.type == "identifier":
                array_name = node_text(child, source)
                break
        if not array_name:
            continue  # pragma: no cover - defensive

        # Build a stable src ID for the dispatch table (array variable)
        array_line = node.start_point[0] + 1
        array_src_id = make_symbol_id(
            "c", str(file_path), array_line, array_line, array_name, "variable",
        )

        # Scan nested initializer lists for identifiers that match functions
        seen_funcs: set[str] = set()
        for inner_node in iter_tree(init_list):
            if inner_node.type != "identifier":
                continue
            ident_name = node_text(inner_node, source)
            if ident_name in seen_funcs:
                continue
            # Only link to known function symbols
            lookup_result = resolver.lookup(ident_name)
            if not lookup_result.found or lookup_result.symbol is None:
                continue
            if lookup_result.symbol.kind != "function":
                continue
            seen_funcs.add(ident_name)
            edges.append(Edge.create(
                src=array_src_id,
                dst=lookup_result.symbol.id,
                edge_type="dispatches_to",
                line=inner_node.start_point[0] + 1,
                confidence=0.80 * lookup_result.confidence,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="dispatch_table_initializer",
            ))

        # Only record tables that actually have function pointer entries
        if seen_funcs:
            dispatch_tables[array_name] = array_src_id

    # Scan function bodies for references to discovered dispatch table
    # variables, creating uses_dispatch_table edges.  This connects
    # lookup functions (e.g., get_builtin) to the dispatch table variable,
    # completing the chain from the caller through to the dispatched targets.
    if dispatch_tables:
        str_path = str(file_path)
        for node in iter_tree(tree.root_node):
            if node.type != "function_definition":
                continue
            func_name = _get_function_name(node, source)
            if func_name is None:
                continue  # pragma: no cover - defensive (malformed AST)
            # Resolve the function symbol from local or global tables
            func_sym = None
            if local_symbols and func_name in local_symbols:
                func_sym = local_symbols[func_name]
            elif func_name in global_symbols:  # pragma: no cover - fallback
                gs = global_symbols[func_name]
                if gs.path == str_path:
                    func_sym = gs
            if func_sym is None:
                continue  # pragma: no cover - defensive
            # Scan function body for dispatch table variable references
            body = node.child_by_field_name("body")
            if body is None:
                continue  # pragma: no cover - defensive
            seen_tables: set[str] = set()
            for inner in iter_tree(body):
                if inner.type != "identifier":
                    continue
                name = node_text(inner, source)
                if name in dispatch_tables and name not in seen_tables:
                    seen_tables.add(name)
                    edges.append(Edge.create(
                        src=func_sym.id,
                        dst=dispatch_tables[name],
                        edge_type="uses_dispatch_table",
                        line=inner.start_point[0] + 1,
                        confidence=0.85,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="dispatch_table_reference",
                    ))

    return edges


def _analyze_c_file(
    file_path: Path,
    run: AnalysisRun,
) -> tuple[list[Symbol], list[Edge], bool]:
    """Analyze a single C file (legacy single-pass, used for testing).

    Returns (symbols, edges, success).
    """
    parser = _get_c_parser()
    if parser is None:
        return [], [], False

    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return [], [], False

    symbols = _extract_symbols(tree, source, file_path, run)

    # Build symbol registry for edge extraction
    global_symbols: dict[str, Symbol] = {}

    for sym in symbols:
        global_symbols[sym.name] = sym

    resolver = NameResolver(global_symbols)
    edges = _extract_edges(tree, source, file_path, run, global_symbols, resolver)
    return symbols, edges, True


class CAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based C analyzer.

    Uses tree-sitter-c to parse C files and extract functions, structs, enums,
    typedefs, call edges, and JNI patterns. Overrides ``analyze`` to use
    custom file discovery that skips .h files when C++ files exist (avoids
    duplicates with the C++ analyzer). Overrides ``register_symbol`` to prefer
    definitions in .c files over declarations in .h files.
    """

    lang = "c"
    file_patterns: ClassVar[list[str]] = ["*.h", "*.c"]
    grammar_module = "tree_sitter_c"

    def extract_symbols_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        run: AnalysisRun,
    ) -> FileAnalysis:
        """Extract symbols from a single C file."""
        analysis = FileAnalysis()
        symbols = _extract_symbols(tree, source, file_path, run)
        analysis.symbols = symbols
        for sym in symbols:
            analysis.symbol_by_name[sym.name] = sym
        return analysis

    def register_symbol(
        self,
        symbol: Symbol,
        global_symbols: dict,
    ) -> None:
        """Register symbol, preferring .c definitions over .h declarations."""
        existing = global_symbols.get(symbol.name)
        if existing is None:
            global_symbols[symbol.name] = symbol
        else:
            sym_is_source = symbol.path.endswith('.c')
            existing_is_source = existing.path.endswith('.c')
            if sym_is_source and not existing_is_source:
                global_symbols[symbol.name] = symbol

    def extract_edges_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        local_symbols: dict[str, Symbol],
        global_symbols: dict,
        run: AnalysisRun,
        import_aliases: dict[str, str],
        resolver: NameResolver,
    ) -> list[Edge]:
        """Extract call edges from a single C file."""
        return _extract_edges(
            tree, source, file_path, run, global_symbols, resolver,
            local_symbols=local_symbols,
        )

    def analyze(
        self,
        repo_root: Path,
        max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """Run analysis with custom file discovery for .h dedup.

        Overrides the base ``analyze`` to use ``find_c_files`` which skips .h
        files when C++ files exist in the repo.
        """
        import time as _time
        import warnings as _warnings

        start_time = _time.time()
        run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

        if not self._check_grammar_available():
            _warnings.warn(
                f"{self.lang} analysis skipped: grammar not available. "
                f"Install the required tree-sitter grammar package.",
                UserWarning,
                stacklevel=2,
            )
            run.duration_ms = int((_time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=f"{self.lang} tree-sitter grammar not available",
            )

        parser = self._create_parser()

        # Custom file discovery: skip .h when C++ files exist
        include_headers = not _has_cpp_files(repo_root)
        source_files = find_c_files(repo_root, include_headers=include_headers)

        file_analyses: dict[Path, tuple[FileAnalysis, dict[str, str]]] = {}
        files_analyzed = 0
        files_skipped = 0

        for source_file in source_files:
            if max_files is not None and files_analyzed >= max_files:
                break  # pragma: no cover

            try:
                source = source_file.read_bytes()
            except OSError:
                files_skipped += 1
                continue

            tree = parser.parse(source)
            rel_path = str(source_file.relative_to(repo_root))
            analysis = self.extract_symbols_from_file(
                tree, source, source_file, rel_path, run,
            )
            populate_docstrings_from_tree(tree.root_node, source, analysis.symbols)
            import_aliases = self.get_import_aliases(tree, source)
            file_analyses[source_file] = (analysis, import_aliases)
            files_analyzed += 1

        # Build global symbol registry
        global_symbols: dict = {}
        for analysis, _ in file_analyses.values():
            for symbol in analysis.symbols:
                self.register_symbol(symbol, global_symbols)

        # Pass 2: Extract edges
        all_symbols: list[Symbol] = []
        all_edges: list[Edge] = []
        resolver = self.resolver_class(global_symbols)

        for source_file, (analysis, import_aliases) in file_analyses.items():
            all_symbols.extend(analysis.symbols)
            source = source_file.read_bytes()
            tree = parser.parse(source)
            rel_path = str(source_file.relative_to(repo_root))
            edges = self.extract_edges_from_file(
                tree, source, source_file, rel_path,
                analysis.symbol_by_name, global_symbols, run,
                import_aliases, resolver,
            )
            all_edges.extend(edges)

        # Deduplicate: remove declaration-only symbols when a definition
        # exists for the same function name.  Declarations in headers produce
        # orphan nodes because calls resolve to the definition.
        definition_by_name: dict[str, Symbol] = {}
        for sym in all_symbols:
            if sym.kind == "function" and "declaration" not in sym.modifiers:
                definition_by_name[sym.name] = sym

        # Build a remap table: declaration ID → definition ID.
        # Edges targeting a removed declaration should point to the
        # surviving definition instead.
        id_remap: dict[str, str] = {}
        kept: list[Symbol] = []
        for sym in all_symbols:
            if (
                sym.kind == "function"
                and "declaration" in sym.modifiers
                and sym.name in definition_by_name
            ):
                id_remap[sym.id] = definition_by_name[sym.name].id
            else:
                kept.append(sym)
        all_symbols = kept

        # Rewrite edge src/dst that reference removed declaration IDs
        if id_remap:
            from dataclasses import replace as _dc_replace

            for i, edge in enumerate(all_edges):
                new_src = id_remap.get(edge.src, edge.src)
                new_dst = id_remap.get(edge.dst, edge.dst)
                if new_src != edge.src or new_dst != edge.dst:
                    all_edges[i] = _dc_replace(edge, src=new_src, dst=new_dst)

        run.files_analyzed = files_analyzed
        run.files_skipped = files_skipped
        run.duration_ms = int((_time.time() - start_time) * 1000)

        return AnalysisResult(
            symbols=all_symbols,
            edges=all_edges,
            run=run,
        )


_analyzer = CAnalyzer()


def is_c_tree_sitter_available() -> bool:
    """Check if tree-sitter and C grammar are available."""
    return _analyzer._check_grammar_available()


@register_analyzer("c", capture_symbols_as="c")
def analyze_c(repo_root: Path) -> AnalysisResult:
    """Analyze all C files in a repository.

    Uses a two-pass approach:
    1. Parse all files and extract symbols into global registry
    2. Detect calls and resolve against global symbol registry

    Returns a AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-c is not available, returns empty result (silently skipped).
    """
    return _analyzer.analyze(repo_root)
