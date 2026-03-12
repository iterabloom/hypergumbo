# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dockerfile analysis pass using tree-sitter-dockerfile.

This analyzer uses tree-sitter to parse Dockerfiles and extract:
- Build stages (FROM ... AS name)
- Base images
- Exposed ports (EXPOSE)
- Environment variables (ENV)
- Build arguments (ARG)
- Multi-stage build dependencies (COPY --from)

If tree-sitter-dockerfile is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-dockerfile is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract stages and symbols
   - Pass 2: Resolve COPY --from references between stages
4. Create edges for dependencies between stages

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-dockerfile package for grammar
- Two-pass allows cross-file stage resolution
- Container-specific: stages, ports, env vars are first-class symbols
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import AnalysisResult, TreeSitterAnalyzer, find_child_by_type, iter_tree, make_symbol_id, node_text
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("dockerfile")


def find_dockerfiles(repo_root: Path) -> Iterator[Path]:
    """Yield all Dockerfiles in the repository."""
    # Common Dockerfile patterns
    patterns = [
        "Dockerfile",
        "Dockerfile.*",
        "dockerfile",
        "dockerfile.*",
        "*.dockerfile",
    ]
    seen: set[Path] = set()
    for pattern in patterns:
        for f in find_files(repo_root, [pattern]):
            if f not in seen:
                seen.add(f)
                yield f



def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _extract_image_name(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract image name from image_spec node."""
    image_spec = find_child_by_type(node, "image_spec")
    if image_spec:
        # Get the full image spec including tag
        name_node = find_child_by_type(image_spec, "image_name")
        tag_node = find_child_by_type(image_spec, "image_tag")
        if name_node:
            name = node_text(name_node, source)
            if tag_node:
                tag = node_text(tag_node, source)
                return name + tag
            return name
    return ""  # pragma: no cover


def _extract_stage_alias(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract AS alias from FROM instruction."""
    alias_node = find_child_by_type(node, "image_alias")
    if alias_node:
        return node_text(alias_node, source)
    return None


def _extract_copy_from(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract --from=stage from COPY instruction."""
    for child in node.children:
        if child.type == "param":
            param_text = node_text(child, source)
            if param_text.startswith("--from="):
                return param_text[7:]  # Strip "--from="
    return None  # pragma: no cover - COPY without --from


def _extract_env_name(env_pair_node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract variable name from env_pair node."""
    for child in env_pair_node.children:
        if child.type == "unquoted_string":
            return node_text(child, source)
    return None  # pragma: no cover


def _extract_arg_name(arg_instruction: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract ARG name from arg_instruction node.

    The grammar parses 'ARG NAME=value' as:
    - ARG: 'ARG'
    - unquoted_string: 'NAME'
    - =: '='
    - unquoted_string: 'value'

    So the first unquoted_string is the name.
    """
    for child in arg_instruction.children:
        if child.type == "unquoted_string":
            return node_text(child, source)
    return None  # pragma: no cover


def _process_dockerfile_tree(
    root_node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    stage_registry: dict[str, str],
    stage_counter: list[int],
) -> None:
    """Process Dockerfile AST tree to extract symbols and edges.

    Uses iterative traversal to avoid RecursionError on deeply nested code.

    Args:
        root_node: Root tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
        symbols: List to append symbols to
        edges: List to append edges to
        stage_registry: Registry mapping stage names to symbol IDs
        stage_counter: Counter for unnamed stages (wrapped in list for mutability)
    """
    for node in iter_tree(root_node):
        if node.type == "from_instruction":
            # Extract image name and optional alias
            image_name = _extract_image_name(node, source)
            stage_alias = _extract_stage_alias(node, source)

            stage_name = stage_alias if stage_alias else str(stage_counter[0])
            stage_counter[0] += 1

            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            symbol_id = make_symbol_id("dockerfile", rel_path, start_line, end_line, stage_name, "stage")

            sym = Symbol(
                id=symbol_id,
                stable_id=None,
                shape_id=None,
                canonical_name=stage_name,
                fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                kind="stage",
                name=stage_name,
                path=rel_path,
                language="dockerfile",
                span=Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
                meta={"base_image": image_name} if image_name else None,
            )
            symbols.append(sym)
            stage_registry[stage_name.lower()] = symbol_id

            # Create base_image edge if this FROM references another stage
            if image_name and image_name.lower() in stage_registry:
                dst_id = stage_registry[image_name.lower()]
                edge = Edge.create(
                    src=symbol_id,
                    dst=dst_id,
                    edge_type="base_image",
                    line=start_line,
                    confidence=0.95,
                    origin=PASS_ID,
                    evidence_type="dockerfile_from",
                )
                edges.append(edge)

        elif node.type == "expose_instruction":
            # Extract exposed port
            port_node = find_child_by_type(node, "expose_port")
            if port_node:
                port_value = node_text(port_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("dockerfile", rel_path, start_line, end_line, port_value, "exposed_port")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=port_value,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="exposed_port",
                    name=port_value,
                    path=rel_path,
                    language="dockerfile",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)

        elif node.type == "env_instruction":
            # Extract environment variable
            env_pair = find_child_by_type(node, "env_pair")
            if env_pair:
                var_name = _extract_env_name(env_pair, source)
                if var_name:
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    symbol_id = make_symbol_id("dockerfile", rel_path, start_line, end_line, var_name, "env_var")

                    sym = Symbol(
                        id=symbol_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=var_name,
                        fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                        kind="env_var",
                        name=var_name,
                        path=rel_path,
                        language="dockerfile",
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                    )
                    symbols.append(sym)

        elif node.type == "arg_instruction":
            # Extract build argument
            arg_name = _extract_arg_name(node, source)
            if arg_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("dockerfile", rel_path, start_line, end_line, arg_name, "build_arg")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=arg_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="build_arg",
                    name=arg_name,
                    path=rel_path,
                    language="dockerfile",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)

        elif node.type == "copy_instruction":
            # Check for --from=stage dependency
            from_stage = _extract_copy_from(node, source)
            if from_stage:
                # Find current stage (last one added)
                current_stage_id = None
                for sym in reversed(symbols):
                    if sym.kind == "stage" and sym.path == rel_path:
                        current_stage_id = sym.id
                        break

                if current_stage_id and from_stage.lower() in stage_registry:
                    src_stage_id = stage_registry[from_stage.lower()]
                    start_line = node.start_point[0] + 1
                    edge = Edge.create(
                        src=current_stage_id,
                        dst=src_stage_id,
                        edge_type="depends_on",
                        line=start_line,
                        confidence=0.95,
                        origin=PASS_ID,
                        evidence_type="dockerfile_copy_from",
                    )
                    edges.append(edge)


class DockerfileAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based Dockerfile analyzer.

    Uses tree-sitter-dockerfile to parse Dockerfiles and extract build stages,
    base images, exposed ports, environment variables, build arguments, and
    multi-stage build dependencies (COPY --from).

    Overrides ``analyze`` because Dockerfile uses a single-pass approach: both
    symbols and edges (base_image, depends_on) are extracted together since
    stage references need the stage registry built during the same pass.
    """

    lang = "dockerfile"
    file_patterns: ClassVar[list[str]] = ["Dockerfile", "Dockerfile.*", "*.dockerfile"]
    grammar_module = "tree_sitter_dockerfile"

    def analyze(
        self,
        repo_root: Path,
        max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """Run Dockerfile analysis with single-pass symbol+edge extraction."""
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

        files_analyzed = 0
        files_skipped = 0
        warnings_list: list[str] = []

        symbols: list[Symbol] = []
        edges: list[Edge] = []
        stage_registry: dict[str, str] = {}

        dockerfile_files = list(find_dockerfiles(repo_root))

        for dockerfile_path in dockerfile_files:
            if max_files is not None and files_analyzed >= max_files:
                break  # pragma: no cover

            try:
                rel_path = str(dockerfile_path.relative_to(repo_root))
                source = dockerfile_path.read_bytes()
                tree = parser.parse(source)
                files_analyzed += 1

                # Reset stage counter for each file
                stage_counter = [0]

                _process_dockerfile_tree(
                    tree.root_node,
                    source,
                    rel_path,
                    symbols,
                    edges,
                    stage_registry,
                    stage_counter,
                )

            except Exception as e:  # pragma: no cover
                files_skipped += 1  # pragma: no cover
                warnings_list.append(f"Failed to parse {dockerfile_path}: {e}")  # pragma: no cover

        run.files_analyzed = files_analyzed
        run.files_skipped = files_skipped
        run.duration_ms = int((_time.time() - start_time) * 1000)
        run.warnings = warnings_list

        return AnalysisResult(
            symbols=symbols,
            edges=edges,
            run=run,
        )


_analyzer = DockerfileAnalyzer()


def is_dockerfile_tree_sitter_available() -> bool:
    """Check if tree-sitter with Dockerfile grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("dockerfile")
def analyze_dockerfiles(repo_root: Path) -> AnalysisResult:
    """Analyze Dockerfiles in the repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult with symbols and edges
    """
    return _analyzer.analyze(repo_root)
