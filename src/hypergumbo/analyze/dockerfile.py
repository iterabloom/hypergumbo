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
import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .base import iter_tree

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "dockerfile-v1"
PASS_VERSION = "hypergumbo-0.1.0"


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


def is_dockerfile_tree_sitter_available() -> bool:
    """Check if tree-sitter with Dockerfile grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover
    if importlib.util.find_spec("tree_sitter_dockerfile") is None:
        return False  # pragma: no cover
    return True


@dataclass
class DockerfileAnalysisResult:
    """Result of analyzing Dockerfiles."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"dockerfile:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None  # pragma: no cover


def _extract_image_name(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract image name from image_spec node."""
    image_spec = _find_child_by_type(node, "image_spec")
    if image_spec:
        # Get the full image spec including tag
        name_node = _find_child_by_type(image_spec, "image_name")
        tag_node = _find_child_by_type(image_spec, "image_tag")
        if name_node:
            name = _node_text(name_node, source)
            if tag_node:
                tag = _node_text(tag_node, source)
                return name + tag
            return name
    return ""  # pragma: no cover


def _extract_stage_alias(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract AS alias from FROM instruction."""
    alias_node = _find_child_by_type(node, "image_alias")
    if alias_node:
        return _node_text(alias_node, source)
    return None


def _extract_copy_from(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract --from=stage from COPY instruction."""
    for child in node.children:
        if child.type == "param":
            param_text = _node_text(child, source)
            if param_text.startswith("--from="):
                return param_text[7:]  # Strip "--from="
    return None  # pragma: no cover - COPY without --from


def _extract_env_name(env_pair_node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract variable name from env_pair node."""
    for child in env_pair_node.children:
        if child.type == "unquoted_string":
            return _node_text(child, source)
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
            return _node_text(child, source)
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
            symbol_id = _make_symbol_id(rel_path, start_line, end_line, stage_name, "stage")

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
                edge = Edge(
                    id=_make_edge_id(symbol_id, dst_id, "base_image"),
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
            port_node = _find_child_by_type(node, "expose_port")
            if port_node:
                port_value = _node_text(port_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, port_value, "exposed_port")

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
            env_pair = _find_child_by_type(node, "env_pair")
            if env_pair:
                var_name = _extract_env_name(env_pair, source)
                if var_name:
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    symbol_id = _make_symbol_id(rel_path, start_line, end_line, var_name, "env_var")

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
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, arg_name, "build_arg")

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
                    edge = Edge(
                        id=_make_edge_id(current_stage_id, src_stage_id, "depends_on"),
                        src=current_stage_id,
                        dst=src_stage_id,
                        edge_type="depends_on",
                        line=start_line,
                        confidence=0.95,
                        origin=PASS_ID,
                        evidence_type="dockerfile_copy_from",
                    )
                    edges.append(edge)


def analyze_dockerfiles(repo_root: Path) -> DockerfileAnalysisResult:
    """Analyze Dockerfiles in the repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        DockerfileAnalysisResult with symbols and edges
    """
    if not is_dockerfile_tree_sitter_available():  # pragma: no cover
        return DockerfileAnalysisResult(  # pragma: no cover
            skipped=True,  # pragma: no cover
            skip_reason="tree-sitter-dockerfile not installed (pip install tree-sitter-dockerfile)",  # pragma: no cover
        )  # pragma: no cover

    import tree_sitter
    import tree_sitter_dockerfile

    start_time = time.time()
    files_analyzed = 0
    files_skipped = 0
    warnings_list: list[str] = []

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    # Stage registry for cross-file resolution: name -> symbol_id
    stage_registry: dict[str, str] = {}

    # Create parser
    try:
        parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_dockerfile.language()))
    except Exception as e:  # pragma: no cover
        warnings.warn(f"Failed to initialize Dockerfile parser: {e}")
        return DockerfileAnalysisResult(
            skipped=True,
            skip_reason=f"Failed to initialize parser: {e}",
        )

    dockerfile_files = list(find_dockerfiles(repo_root))

    for dockerfile_path in dockerfile_files:
        try:
            rel_path = str(dockerfile_path.relative_to(repo_root))
            source = dockerfile_path.read_bytes()
            tree = parser.parse(source)
            files_analyzed += 1

            # Reset stage counter for each file
            stage_counter = [0]

            # Process this file
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

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = duration_ms
    run.warnings = warnings_list

    return DockerfileAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )
