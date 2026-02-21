"""Robot Framework analyzer using tree-sitter.

Robot Framework is a generic test automation framework for acceptance testing
and robotic process automation (RPA). It uses a keyword-driven approach where
test cases are written in a tabular format.

How It Works
------------
Uses TreeSitterAnalyzer base class for grammar checking and parser creation.
Overrides analyze() because Robot Framework needs cross-file state for
keyword registry resolution.

1. Pass 1: Extract keywords, test cases, variables, and library/resource imports
2. Pass 2: Extract keyword invocation edges with registry lookup for resolution

Symbols Extracted
-----------------
- **Keywords**: User-defined keywords (reusable test steps)
- **Test Cases**: Individual test case definitions
- **Variables**: Suite-level variable definitions (${VAR})
- **Libraries**: External library imports (Python libraries like SeleniumLibrary)
- **Resources**: Imported .robot files that share keywords

Edges Extracted
---------------
- **calls**: Keyword invocations from test cases and keywords
- **imports**: Library and resource imports

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate grammar checking and parser setup
- Robot Framework's keyword-driven approach makes cross-reference tracking valuable
- Library imports connect Robot tests to Python code
- Resource imports create dependency graphs between .robot files
- Tags are captured for filtering and categorization
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    TreeSitterAnalyzer,
    populate_docstrings_from_tree,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = make_pass_id("robot")


def find_robot_files(repo_root: Path) -> list[Path]:
    """Find all Robot Framework files in the repository."""
    files: list[Path] = []
    for pattern in ["**/*.robot"]:
        files.extend(find_files(repo_root, [pattern]))
    return sorted(files)


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str) -> str:
    """Create a stable symbol ID."""
    return f"robot:{path}:{kind}:{name}"


class _RobotExtractor:
    """Extraction logic for Robot Framework files.

    Maintains cross-file state: symbol registry for keyword resolution,
    and current container tracking for edge source attribution.
    """

    # Built-in Robot Framework keywords that should not create edges
    BUILTIN_KEYWORDS = frozenset({
        # BuiltIn library
        "Call Method", "Catenate", "Comment", "Continue For Loop",
        "Continue For Loop If", "Convert To Binary", "Convert To Boolean",
        "Convert To Bytes", "Convert To Hex", "Convert To Integer",
        "Convert To Number", "Convert To Octal", "Convert To String",
        "Create Dictionary", "Create List", "Evaluate", "Exit For Loop",
        "Exit For Loop If", "Fail", "Fatal Error", "Get Count",
        "Get Length", "Get Library Instance", "Get Time", "Get Variable Value",
        "Get Variables", "Import Library", "Import Resource", "Import Variables",
        "Keyword Should Exist", "Length Should Be", "Log", "Log Many",
        "Log To Console", "Log Variables", "No Operation", "Pass Execution",
        "Pass Execution If", "Regexp Escape", "Reload Library", "Remove Tags",
        "Repeat Keyword", "Replace Variables", "Return From Keyword",
        "Return From Keyword If", "Run Keyword", "Run Keyword And Continue On Failure",
        "Run Keyword And Expect Error", "Run Keyword And Ignore Error",
        "Run Keyword And Return", "Run Keyword And Return If",
        "Run Keyword And Return Status", "Run Keyword And Warn On Failure",
        "Run Keyword If", "Run Keyword If All Critical Tests Passed",
        "Run Keyword If All Tests Passed", "Run Keyword If Any Critical Tests Failed",
        "Run Keyword If Any Tests Failed", "Run Keyword If Test Failed",
        "Run Keyword If Test Passed", "Run Keyword If Timeout Occurred",
        "Run Keyword Unless", "Run Keywords", "Set Global Variable",
        "Set Library Search Order", "Set Local Variable", "Set Log Level",
        "Set Suite Documentation", "Set Suite Metadata", "Set Suite Variable",
        "Set Tags", "Set Task Variable", "Set Test Documentation",
        "Set Test Message", "Set Test Variable", "Set Variable",
        "Set Variable If", "Should Be Empty", "Should Be Equal",
        "Should Be Equal As Integers", "Should Be Equal As Numbers",
        "Should Be Equal As Strings", "Should Be True", "Should Contain",
        "Should Contain Any", "Should Contain X Times", "Should End With",
        "Should Match", "Should Match Regexp", "Should Not Be Empty",
        "Should Not Be Equal", "Should Not Be Equal As Integers",
        "Should Not Be Equal As Numbers", "Should Not Be Equal As Strings",
        "Should Not Be True", "Should Not Contain", "Should Not Contain Any",
        "Should Not End With", "Should Not Match", "Should Not Match Regexp",
        "Should Not Start With", "Should Start With", "Skip", "Skip If",
        "Sleep", "Variable Should Exist", "Variable Should Not Exist",
        "Wait Until Keyword Succeeds",
        # Collections library
        "Append To List", "Combine Lists", "Convert To Dictionary",
        "Copy Dictionary", "Copy List", "Count Values In List",
        "Dictionaries Should Be Equal", "Dictionary Should Contain Item",
        "Dictionary Should Contain Key", "Dictionary Should Contain Sub Dictionary",
        "Dictionary Should Contain Value", "Dictionary Should Not Contain Key",
        "Dictionary Should Not Contain Value", "Get Dictionary Items",
        "Get Dictionary Keys", "Get Dictionary Values", "Get From Dictionary",
        "Get From List", "Get Index From List", "Get Match Count",
        "Get Matches", "Get Slice From List", "Insert Into List",
        "Keep In Dictionary", "List Should Contain Sub List",
        "List Should Contain Value", "List Should Not Contain Duplicates",
        "List Should Not Contain Value", "Lists Should Be Equal",
        "Log Dictionary", "Log List", "Pop From Dictionary",
        "Remove Duplicates", "Remove From Dictionary", "Remove From List",
        "Remove Values From List", "Reverse List", "Set List Value",
        "Set To Dictionary", "Should Contain Match", "Should Not Contain Match",
        "Sort List",
        # String library
        "Convert To Lower Case", "Convert To Title Case", "Convert To Upper Case",
        "Decode Bytes To String", "Encode String To Bytes", "Fetch From Left",
        "Fetch From Right", "Format String", "Generate Random String",
        "Get Line", "Get Line Count", "Get Lines Containing String",
        "Get Lines Matching Pattern", "Get Lines Matching Regexp",
        "Get Regexp Matches", "Get Substring", "Remove String",
        "Remove String Using Regexp", "Replace String", "Replace String Using Regexp",
        "Should Be Byte String", "Should Be Lower Case", "Should Be String",
        "Should Be Title Case", "Should Be Unicode String", "Should Be Upper Case",
        "Should Not Be String", "Split String", "Split String From Right",
        "Split String To Characters", "Split To Lines", "Strip String",
    })

    def __init__(self, repo_root: Path, execution_id: str) -> None:
        self.repo_root = repo_root
        self._symbols: list[Symbol] = []
        self._edges: list[Edge] = []
        self._symbol_registry: dict[str, str] = {}  # name -> symbol_id
        self._current_container: str | None = None
        self._execution_id = execution_id
        self._files_analyzed = 0

    def extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "keyword_definition":
            self._extract_keyword(node, path)
        elif node.type == "test_case_definition":
            self._extract_test_case(node, path)
        elif node.type == "variable_definition":
            self._extract_variable(node, path)
        elif node.type == "setting_statement":
            self._extract_setting(node, path)

        for child in node.children:
            self.extract_symbols(child, path)

    def _extract_keyword(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a keyword definition."""
        name = None
        arguments: list[str] = []
        documentation = ""
        tags: list[str] = []

        for child in node.children:
            if child.type == "name":
                name = _get_node_text(child).strip()
            elif child.type == "body":
                for body_child in child.children:
                    if body_child.type == "keyword_setting":
                        setting_name = None
                        for sc in body_child.children:
                            if sc.type == "keyword_setting_name":
                                setting_name = _get_node_text(sc)
                            elif sc.type == "arguments" and setting_name:
                                if setting_name == "Arguments":
                                    for arg in sc.children:
                                        if arg.type == "argument":
                                            arg_text = _get_node_text(arg).strip()
                                            # Extract variable name from ${var}
                                            if arg_text.startswith("${") and arg_text.endswith("}"):
                                                arg_text = arg_text[2:-1]
                                            arguments.append(arg_text)
                                elif setting_name == "Documentation":
                                    documentation = " ".join(
                                        _get_node_text(arg).strip()
                                        for arg in sc.children
                                        if arg.type == "argument"
                                    )
                                elif setting_name == "Tags":
                                    tags = [
                                        _get_node_text(arg).strip()
                                        for arg in sc.children
                                        if arg.type == "argument"
                                    ]

        if not name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        symbol_id = _make_symbol_id(rel_path, name, "keyword")

        # Build signature
        if arguments:
            signature = f"{name}({', '.join(arguments)})"
        else:
            signature = f"{name}()"

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        meta: dict = {"arguments": arguments}
        if documentation:
            meta["documentation"] = documentation
        if tags:
            meta["tags"] = tags

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=name,
            kind="keyword",
            language="robot",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=signature,
            meta=meta,
        )
        self._symbols.append(symbol)
        self._symbol_registry[name] = symbol_id

    def _extract_test_case(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a test case definition."""
        name = None
        documentation = ""
        tags: list[str] = []

        for child in node.children:
            if child.type == "name":
                name = _get_node_text(child).strip()
            elif child.type == "body":
                for body_child in child.children:
                    if body_child.type == "test_case_setting":
                        setting_name = None
                        for sc in body_child.children:
                            if sc.type == "test_case_setting_name":
                                setting_name = _get_node_text(sc)
                            elif sc.type == "arguments" and setting_name:
                                if setting_name == "Documentation":
                                    documentation = " ".join(
                                        _get_node_text(arg).strip()
                                        for arg in sc.children
                                        if arg.type == "argument"
                                    )
                                elif setting_name == "Tags":
                                    tags = [
                                        _get_node_text(arg).strip()
                                        for arg in sc.children
                                        if arg.type == "argument"
                                    ]

        if not name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        symbol_id = _make_symbol_id(rel_path, name, "test_case")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        meta: dict = {}
        if documentation:
            meta["documentation"] = documentation
        if tags:
            meta["tags"] = tags

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=name,
            kind="test_case",
            language="robot",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=name,
            meta=meta,
        )
        self._symbols.append(symbol)
        self._symbol_registry[name] = symbol_id

    def _extract_variable(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a suite-level variable definition."""
        var_name = None
        value = ""

        for child in node.children:
            if child.type == "variable_name":
                var_name = _get_node_text(child).strip()
            elif child.type == "arguments":
                # Get the first argument as the value
                for arg in child.children:
                    if arg.type == "argument":
                        value = _get_node_text(arg).strip()
                        break

        if not var_name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        symbol_id = _make_symbol_id(rel_path, var_name, "variable")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=f"${{{var_name}}}",  # Include ${} for clarity
            kind="variable",
            language="robot",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f"${{{var_name}}} = {value}" if value else f"${{{var_name}}}",
            meta={"value": value} if value else {},
        )
        self._symbols.append(symbol)

    def _extract_setting(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract Library and Resource imports."""
        setting_name = None
        setting_value = ""

        for child in node.children:
            if child.type == "setting_name":
                setting_name = _get_node_text(child).strip()
            elif child.type == "arguments":
                for arg in child.children:
                    if arg.type == "argument":
                        setting_value = _get_node_text(arg).strip()
                        break

        if not setting_name or not setting_value:
            return  # pragma: no cover - malformed settings are syntax errors

        rel_path = path.relative_to(self.repo_root)

        if setting_name == "Library":
            symbol_id = _make_symbol_id(rel_path, setting_value, "library")
            span = Span(
                start_line=node.start_point[0] + 1,
                start_col=node.start_point[1],
                end_line=node.end_point[0] + 1,
                end_col=node.end_point[1],
            )
            symbol = Symbol(
                id=symbol_id,
                stable_id=symbol_id,
                name=setting_value,
                kind="library",
                language="robot",
                path=str(rel_path),
                span=span,
                origin=PASS_ID,
                signature=f"Library: {setting_value}",
                meta={"import_type": "library"},
            )
            self._symbols.append(symbol)

        elif setting_name == "Resource":
            symbol_id = _make_symbol_id(rel_path, setting_value, "resource")
            span = Span(
                start_line=node.start_point[0] + 1,
                start_col=node.start_point[1],
                end_line=node.end_point[0] + 1,
                end_col=node.end_point[1],
            )
            symbol = Symbol(
                id=symbol_id,
                stable_id=symbol_id,
                name=setting_value,
                kind="resource",
                language="robot",
                path=str(rel_path),
                span=span,
                origin=PASS_ID,
                signature=f"Resource: {setting_value}",
                meta={"import_type": "resource"},
            )
            self._symbols.append(symbol)

            # Add import edge
            edge = Edge.create(
                src=f"robot:{rel_path}",
                dst=f"robot:resource:{setting_value}",
                edge_type="imports",
                line=node.start_point[0] + 1,
                origin=PASS_ID,
                origin_run_id=self._execution_id,
                evidence_type="static",
                confidence=1.0,
                evidence_lang="robot",
            )
            self._edges.append(edge)

    def extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract call edges from the syntax tree."""
        # Track current container for source attribution
        if node.type == "keyword_definition":
            for child in node.children:
                if child.type == "name":
                    name = _get_node_text(child).strip()
                    self._current_container = self._symbol_registry.get(name)
                    break
            self._extract_edges_from_children(node, path)
            self._current_container = None
            return

        elif node.type == "test_case_definition":
            for child in node.children:
                if child.type == "name":
                    name = _get_node_text(child).strip()
                    self._current_container = self._symbol_registry.get(name)
                    break
            self._extract_edges_from_children(node, path)
            self._current_container = None
            return

        elif node.type == "keyword_invocation":
            self._extract_keyword_call(node, path)

        for child in node.children:
            self.extract_edges(child, path)

    def _extract_edges_from_children(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract edges from children nodes."""
        for child in node.children:
            self.extract_edges(child, path)

    def _extract_keyword_call(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a keyword call edge."""
        keyword_name = None
        rel_path = path.relative_to(self.repo_root)

        for child in node.children:
            if child.type == "keyword":
                keyword_name = _get_node_text(child).strip()
                break

        if not keyword_name:
            return  # pragma: no cover

        # Skip built-in keywords
        if keyword_name in self.BUILTIN_KEYWORDS:
            return

        # Determine source
        src = self._current_container or f"robot:{rel_path}"

        # Try to resolve the target
        resolved_id = self._symbol_registry.get(keyword_name)

        if resolved_id:
            dst = resolved_id
            confidence = 1.0
        else:
            # Unresolved - could be from library or imported resource
            dst = f"robot:unresolved:{keyword_name}"
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
            evidence_lang="robot",
        )
        self._edges.append(edge)


class RobotAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based analyzer for Robot Framework files.

    Extracts keywords, test cases, variables, library/resource imports,
    and keyword invocation edges. Uses grammar_module for tree-sitter-robot.
    """

    lang = "robot"
    file_patterns: ClassVar[list[str]] = ["**/*.robot"]
    grammar_module = "tree_sitter_robot"

    def _create_parser(self) -> "tree_sitter.Parser":
        """Create Robot Framework parser from standalone grammar."""
        import tree_sitter
        from tree_sitter_robot import language

        return tree_sitter.Parser(tree_sitter.Language(language()))

    def analyze(self, repo_root: Path, max_files: int | None = None) -> AnalysisResult:
        """Run Robot Framework analysis with cross-file keyword registry."""
        if not self._check_grammar_available():
            import warnings
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

        start_time = time.time()

        files = find_robot_files(repo_root)
        if not files:
            return AnalysisResult(
                symbols=[],
                edges=[],
                run=None,
            )

        parser = self._create_parser()
        execution_id = f"uuid:{uuid.uuid4()}"
        extractor = _RobotExtractor(repo_root, execution_id)

        # Pass 1: Extract symbols
        for path in files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                before = len(extractor._symbols)
                extractor.extract_symbols(tree.root_node, path)
                populate_docstrings_from_tree(tree.root_node, content, extractor._symbols[before:])
                extractor._files_analyzed += 1
            except Exception:  # pragma: no cover  # noqa: S112  # nosec B112
                continue

        # Pass 2: Extract edges
        for path in files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                extractor.extract_edges(tree.root_node, path)
            except Exception:  # pragma: no cover  # noqa: S112  # nosec B112
                continue

        duration_ms = int((time.time() - start_time) * 1000)

        run = AnalysisRun(
            pass_id=PASS_ID,
            execution_id=execution_id,
            version=PASS_VERSION,
            toolchain={"name": "robot", "version": "unknown"},
            duration_ms=duration_ms,
            files_analyzed=extractor._files_analyzed,
        )

        return AnalysisResult(
            symbols=extractor._symbols,
            edges=extractor._edges,
            run=run,
        )


_analyzer = RobotAnalyzer()


def is_robot_tree_sitter_available() -> bool:
    """Check if tree-sitter-robot is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("robot")
def analyze_robot(repo_root: Path) -> AnalysisResult:
    """Analyze Robot Framework files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols and edges
    """
    return _analyzer.analyze(repo_root)
