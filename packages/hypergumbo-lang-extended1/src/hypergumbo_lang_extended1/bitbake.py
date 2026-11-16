# SPDX-License-Identifier: AGPL-3.0-or-later
"""BitBake analyzer using tree-sitter.

BitBake is the build tool used by Yocto Project and OpenEmbedded for embedded
Linux development. It processes recipe files (.bb, .bbappend), class files
(.bbclass) and include files (.inc) to build complete Linux distributions;
this analyzer reads all four file types.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract variable assignments, task functions and addtask
   statements as symbols. The names an inherit directive or a
   DEPENDS/RDEPENDS assignment gives are recorded per file.
2. Pass 2: One edge per recorded name, once every file of the run is known,
   since a name is resolved against the run's own files.

Symbols Extracted
-----------------
- **Variables**: Key recipe metadata (SUMMARY, LICENSE, SRC_URI, DEPENDS, etc.)
- **Tasks**: Shell and python task functions (do_fetch, do_configure,
  do_compile, etc.), kind ``task``, plus addtask directives, kind ``addtask``

Edges Extracted
---------------
- **inherits**: Inheritance from .bbclass files
- **depends_on**: Package dependencies from DEPENDS/RDEPENDS assignments

Both start at the file's node (``make_file_id``). The dst is another file's
node when the run read exactly one file BitBake's own naming gives that name:
``<cls>.bbclass`` for ``inherit <cls>``, and a recipe ``<pkg>.bb`` or
``<pkg>_<version>.bb`` for a package (``PN`` is the file name up to the first
underscore). No such file, or more than one (two versions of a recipe: which
one BitBake prefers is configuration this analyzer does not read), is a
stand-in outside the tree, ``bitbake:class:0-0:<cls>:unresolved`` or
``bitbake:package:0-0:<pkg>:unresolved`` (``edge_target.external_target``).
A package another recipe names only through ``PROVIDES`` or
``BBCLASSEXTEND`` (``zlib-native``) is not looked up and is a stand-in too.

Why This Design
---------------
- BitBake recipes define the entire build process for packages
- Variable assignments contain critical metadata and dependencies
- Inherit directives create class hierarchies important for understanding builds
- Task functions define the actual build steps
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Optional

from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.overload_arity import scoped_stable_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    make_file_id,
    make_symbol_id,
)
from hypergumbo_core.analyze.edge_target import external_target
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.base import node_own_text as _get_node_text

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver


PASS_ID = make_pass_id("bitbake")


def find_bitbake_files(repo_root: Path) -> list[Path]:
    """Find all BitBake files in the repository.

    The analyzer's own discovery (``_find_source_files``), so the two cannot drift.
    """
    return sorted(_analyzer._find_source_files(repo_root))



def _make_symbol_id(path: Path, node: "tree_sitter.Node", name: str, kind: str) -> str:
    """The node id of the symbol declared by ``node``.

    ``kind`` is the Symbol's own kind and the span slot is the declaration's
    own lines (ADR-0036 Rulings 2 and 3). The lines are what tell apart two
    assignments to one variable, and a shell task from a python task of the
    same name: an id keyed by name alone gave the first pair one id, and kept
    the second pair apart only by writing ``python_task``, a kind no symbol
    has, into the kind slot.
    """
    return make_symbol_id(
        "bitbake", str(path), node.start_point[0] + 1, node.end_point[0] + 1, name, kind,
    )


# Important BitBake variables to track
IMPORTANT_VARIABLES = frozenset({
    "SUMMARY", "DESCRIPTION", "HOMEPAGE", "LICENSE", "LIC_FILES_CHKSUM",
    "SRC_URI", "S", "B", "D", "WORKDIR",
    "DEPENDS", "RDEPENDS", "RRECOMMENDS", "RCONFLICTS", "RPROVIDES",
    "PROVIDES", "PACKAGE_ARCH", "COMPATIBLE_HOST", "COMPATIBLE_MACHINE",
    "PN", "PV", "PR", "PF", "P", "BPN", "BP",
    "PACKAGES", "FILES", "CONFFILES",
    "EXTRA_OECONF", "EXTRA_OECMAKE", "EXTRA_OEMAKE",
    "CFLAGS", "LDFLAGS", "CXXFLAGS",
    "inherit",
})


#: One reference read in Pass 1: ``(edge_type, name, line)``.
_PendingRef = tuple[str, str, int]


class BitBakeAnalyzer(TreeSitterAnalyzer):
    """Analyzer for BitBake files using TreeSitterAnalyzer base class.

    BitBake is unusual: what its edges (inherits, depends_on) refer to is read
    during symbol extraction (Pass 1), not from cross-file call resolution.
    Pass 1 stores each file's references in the module-level
    ``_pending_ref_store`` dict, keyed by rel_path; Pass 2 turns them into
    edges. The dict's keys are also the run's files, which a class or package
    name is looked up in, so it is emptied around each ``analyze()`` and not
    file by file.
    """

    lang = "bitbake"
    file_patterns: ClassVar[list[str]] = ["**/*.bb", "**/*.bbappend", "**/*.bbclass", "**/*.inc"]
    language_pack_name = "bitbake"

    def analyze(
        self, repo_root: Path, max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """Run the base two-pass analysis over a fresh reference store."""
        _pending_ref_store.clear()
        try:
            return super().analyze(repo_root, max_files)
        finally:
            _pending_ref_store.clear()

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract variable, inherit, task, and addtask symbols from a BitBake file."""
        analysis = FileAnalysis()
        rel_parts = Path(rel_path).parts
        repo_root = file_path
        for _ in rel_parts:
            repo_root = repo_root.parent

        # FileAnalysis has no edges field, so collect the references here and
        # hand them to Pass 2 through the module-level _pending_ref_store
        pending_refs: list[_PendingRef] = []

        self._extract_symbols_recursive(
            tree.root_node, file_path, repo_root, rel_path, run,
            analysis, pending_refs,
        )

        # Module-level dict keyed by rel_path passes them between passes
        _pending_ref_store[rel_path] = pending_refs

        return analysis

    def _extract_symbols_recursive(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        rel_path: str, run: "AnalysisRun",
        analysis: FileAnalysis, pending_refs: list[_PendingRef],
    ) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "variable_assignment":
            self._extract_variable(
                node, path, repo_root, rel_path, run, analysis, pending_refs,
            )
        elif node.type == "inherit_directive":
            self._extract_inherit(
                node, path, repo_root, rel_path, run, analysis, pending_refs,
            )
        elif node.type == "function_definition":
            self._extract_function(node, path, repo_root, rel_path, run, analysis)
        elif node.type == "anonymous_python_function":
            self._extract_python_function(node, path, repo_root, rel_path, run, analysis)
        elif node.type == "addtask_statement":
            self._extract_addtask(node, path, repo_root, rel_path, run, analysis)

        for child in node.children:
            self._extract_symbols_recursive(
                child, path, repo_root, rel_path, run, analysis, pending_refs,
            )

    def _extract_variable(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        rel_path: str, run: "AnalysisRun",
        analysis: FileAnalysis, pending_refs: list[_PendingRef],
    ) -> None:
        """Extract a variable assignment."""
        var_name = None
        var_value = ""

        for child in node.children:
            if child.type == "identifier":
                var_name = _get_node_text(child)
            elif child.type == "literal":
                var_value = _get_node_text(child).strip('"\'')

        if not var_name:
            return  # pragma: no cover

        # Only track important variables to avoid noise
        base_name = var_name.split("_")[0]
        if base_name not in IMPORTANT_VARIABLES and var_name not in IMPORTANT_VARIABLES:
            return

        rel_p = Path(rel_path)
        symbol_id = _make_symbol_id(rel_p, node, var_name, "variable")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        symbol = Symbol(
            id=symbol_id,
            stable_id=scoped_stable_id(
                kind="variable", lang="bitbake", path=str(rel_p), name=var_name,
            ),
            name=var_name,
            kind="variable",
            language="bitbake",
            path=str(rel_p),
            span=span,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            signature=f"{var_name} = {var_value[:50]}..." if len(var_value) > 50 else f"{var_name} = {var_value}",
            meta={"value": var_value[:200]} if var_value else {},
        )
        analysis.symbols.append(symbol)

        # Extract dependency edges from DEPENDS/RDEPENDS
        if var_name in ("DEPENDS", "RDEPENDS"):
            self._extract_dependency_refs(
                var_value, node.start_point[0] + 1, pending_refs,
            )

    def _extract_dependency_refs(
        self, value: str, line: int, pending_refs: list[_PendingRef],
    ) -> None:
        """Record the packages a DEPENDS/RDEPENDS value names."""
        clean_value = re.sub(r"\$\{[^}]+\}", "", value)
        packages = clean_value.split()

        for pkg in packages:
            pkg = pkg.strip()
            if not pkg or pkg.startswith("$"):
                continue  # pragma: no cover - defensive after regex cleanup
            pending_refs.append(("depends_on", pkg, line))

    def _extract_inherit(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        rel_path: str, run: "AnalysisRun",
        analysis: FileAnalysis, pending_refs: list[_PendingRef],
    ) -> None:
        """Extract an inherit directive."""
        classes: list[str] = []

        for child in node.children:
            if child.type == "inherit_path":
                classes.append(_get_node_text(child))

        if not classes:
            return  # pragma: no cover

        # Cluster E sub-case (b) FOLD-clean-drop per audit-findings 0010:
        # the per-class inherit Symbol was redundant with the inherits Edge
        # (neither endpoint depends on a dropped Symbol id). Producer now
        # emits only the Edge per inherited class.
        for cls in classes:
            pending_refs.append(("inherits", cls, node.start_point[0] + 1))

    def _extract_function(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        rel_path: str, run: "AnalysisRun", analysis: FileAnalysis,
    ) -> None:
        """Extract a shell function definition (task)."""
        func_name = None

        for child in node.children:
            if child.type == "identifier":
                func_name = _get_node_text(child)
                break

        if not func_name:
            return  # pragma: no cover

        rel_p = Path(rel_path)
        symbol_id = _make_symbol_id(rel_p, node, func_name, "task")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        task_type = "custom"
        if func_name.startswith("do_"):
            task_type = "standard"

        symbol = Symbol(
            id=symbol_id,
            stable_id=scoped_stable_id(
                kind="task", lang="bitbake", path=str(rel_p), name=func_name,
            ),
            name=func_name,
            kind="task",
            language="bitbake",
            path=str(rel_p),
            span=span,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            signature=f"{func_name}()",
            meta={"task_type": task_type},
        )
        analysis.symbols.append(symbol)

    def _extract_python_function(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        rel_path: str, run: "AnalysisRun", analysis: FileAnalysis,
    ) -> None:
        """Extract a Python function definition."""
        func_name = None

        for child in node.children:
            if child.type == "identifier":
                func_name = _get_node_text(child)
                break

        if not func_name:
            return  # pragma: no cover

        rel_p = Path(rel_path)
        symbol_id = _make_symbol_id(rel_p, node, func_name, "task")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        symbol = Symbol(
            id=symbol_id,
            # The scope keeps a python task apart from a shell task of the
            # same name; their node ids differ by their lines.
            stable_id=scoped_stable_id(
                kind="task", lang="bitbake", path=str(rel_p), name=func_name,
                scope=("python",),
            ),
            name=func_name,
            kind="task",
            language="bitbake",
            path=str(rel_p),
            span=span,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            signature=f"python {func_name}()",
            meta={"language": "python", "task_implementation": "python"},
        )
        analysis.symbols.append(symbol)

    def _extract_addtask(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        rel_path: str, run: "AnalysisRun", analysis: FileAnalysis,
    ) -> None:
        """Extract an addtask directive."""
        task_name = None

        for child in node.children:
            if child.type == "identifier":
                task_name = _get_node_text(child)
                break

        if not task_name:
            return  # pragma: no cover

        rel_p = Path(rel_path)
        symbol_id = _make_symbol_id(rel_p, node, task_name, "addtask")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        symbol = Symbol(
            id=symbol_id,
            stable_id=scoped_stable_id(
                kind="addtask", lang="bitbake", path=str(rel_p), name=task_name,
            ),
            name=task_name,
            kind="addtask",
            language="bitbake",
            path=str(rel_p),
            span=span,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            signature=f"addtask {task_name}",
            meta={},
        )
        analysis.symbols.append(symbol)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """One edge per reference Pass 1 recorded for this file."""
        src = make_file_id("bitbake", rel_path)
        edges: list[Edge] = []
        for edge_type, name, line in _pending_ref_store.get(rel_path, []):
            target = _run_file_named(edge_type, name)
            if target is not None:
                dst, dst_ref = make_file_id("bitbake", target), None
            else:
                dst, dst_ref = external_target(
                    "bitbake", name, _STAND_IN_NAMESPACE[edge_type],
                )
            edges.append(Edge.create(
                src=src,
                dst=dst,
                edge_type=edge_type,
                line=line,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="static",
                evidence_lang="bitbake",
                is_resolved=target is not None,
                dst_ref=dst_ref,
            ))
        return edges


# Module-level store for the references collected during Pass 1, by rel_path.
# Its keys are the run's files; ``BitBakeAnalyzer.analyze`` empties it.
_pending_ref_store: dict[str, list[_PendingRef]] = {}

#: The path slot of a stand-in, per edge type: a class name and a package name
#: are two namespaces (``cmake`` the class is not ``cmake`` the recipe).
_STAND_IN_NAMESPACE = {"inherits": "class", "depends_on": "package"}


def _run_file_named(edge_type: str, name: str) -> Optional[str]:
    """The one file of the run that BitBake's naming gives ``name``, if any.

    ``inherit <cls>`` names ``<cls>.bbclass``; a package names the recipe
    ``<pkg>.bb`` or ``<pkg>_<version>.bb``. None or several is ``None``.
    """
    if edge_type == "inherits":
        found = [
            path for path in _pending_ref_store
            if Path(path).name == f"{name}.bbclass"
        ]
    else:
        found = [
            path for path in _pending_ref_store
            if path.endswith(".bb") and Path(path).stem.split("_")[0] == name
        ]
    return found[0] if len(found) == 1 else None

_analyzer = BitBakeAnalyzer()


def is_bitbake_tree_sitter_available() -> bool:
    """Check if tree-sitter-bitbake is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("bitbake", language_state="no_taxonomy_spec")  # WI-futin
def analyze_bitbake(repo_root: Path) -> AnalysisResult:
    """Analyze BitBake files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols and edges
    """
    return _analyzer.analyze(repo_root)
