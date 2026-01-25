"""OpenAPI/Swagger linker for detecting API schema to handler connections.

This linker parses OpenAPI specification files and links operations to
route handlers in the application code.

Detected OpenAPI Files
----------------------
- openapi.yaml, openapi.json (OpenAPI 3.x)
- swagger.yaml, swagger.json (Swagger 2.x)
- api.yaml, api.json (common naming convention)

Extracted Information
---------------------
For each path/operation:
- Path: /users/{id}
- Method: GET, POST, PUT, DELETE, etc.
- OperationId: getUserById (if specified)
- Tags: ["users"] (for grouping)
- Summary/Description (for documentation)

Handler Matching
----------------
Operations are linked to route handlers by:
1. OperationId match (if specified) - matches function/method names
2. Path + Method match - matches route symbols
3. Tag-based grouping - for controller organization

How It Works
------------
1. Scan for OpenAPI spec files (*.yaml, *.json with openapi/swagger keys)
2. Parse spec and extract path operations
3. Create openapi_operation symbols for each operation
4. Match to route symbols by path pattern and HTTP method
5. Create openapi_implements edges linking handlers to specs

Why This Design
---------------
- OpenAPI specs are the contract - linking to handlers ensures consistency
- Regex-based matching handles parameterized paths
- OperationId matching enables precise function-level linking
- Symbols for operations enable slice traversal from spec to implementation
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerRequirement,
    LinkerResult,
    register_linker,
)

PASS_ID = "openapi-linker-v1"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class OpenApiOperation:
    """Represents an OpenAPI operation."""

    path: str  # e.g., /users/{id}
    method: str  # GET, POST, PUT, DELETE, etc.
    operation_id: str | None  # e.g., getUserById
    summary: str | None  # Short description
    tags: list[str]  # Grouping tags
    line: int  # Line number in spec file
    file_path: str  # Spec file path


@dataclass
class OpenApiLinkResult:
    """Result of OpenAPI linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


# OpenAPI spec file patterns
OPENAPI_FILE_PATTERNS = [
    "openapi.yaml",
    "openapi.yml",
    "openapi.json",
    "swagger.yaml",
    "swagger.yml",
    "swagger.json",
    "api.yaml",
    "api.yml",
    "api.json",
    "**/openapi.yaml",
    "**/openapi.yml",
    "**/openapi.json",
    "**/swagger.yaml",
    "**/swagger.yml",
    "**/swagger.json",
]

# HTTP methods in OpenAPI
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def _load_yaml(content: str) -> dict[str, Any] | None:
    """Load YAML content, falling back to JSON if needed."""
    try:
        import yaml

        return yaml.safe_load(content)  # type: ignore[no-any-return]
    except ImportError:
        # Try JSON if YAML not available
        try:
            return json.loads(content)  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            return None
    except Exception:  # pragma: no cover
        return None


def _load_json(content: str) -> dict[str, Any] | None:
    """Load JSON content."""
    try:
        return json.loads(content)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        return None


def _is_openapi_spec(data: dict[str, Any]) -> bool:
    """Check if the data looks like an OpenAPI/Swagger spec."""
    # OpenAPI 3.x
    if "openapi" in data and data.get("openapi", "").startswith("3."):
        return True
    # Swagger 2.x
    if "swagger" in data and data.get("swagger", "").startswith("2."):
        return True
    # Check for paths key (common to both)
    if "paths" in data and isinstance(data["paths"], dict):
        return True
    return False


def _find_openapi_files(root: Path) -> Iterator[Path]:
    """Find OpenAPI spec files in the repository."""
    # First, try specific filenames
    for pattern in OPENAPI_FILE_PATTERNS:
        for file_path in find_files(root, [pattern]):
            if file_path.suffix.lower() in (".yaml", ".yml", ".json"):
                yield file_path


def _parse_openapi_spec(file_path: Path) -> list[OpenApiOperation]:
    """Parse an OpenAPI spec file and extract operations."""
    operations: list[OpenApiOperation] = []

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, IOError):  # pragma: no cover
        return operations

    # Parse the file
    data: dict[str, Any] | None = None
    if file_path.suffix.lower() in (".yaml", ".yml"):
        data = _load_yaml(content)
    else:
        data = _load_json(content)

    if not data or not _is_openapi_spec(data):
        return operations

    # Extract paths
    paths = data.get("paths", {})
    if not isinstance(paths, dict):
        return operations

    # Track line numbers approximately
    line_num = 1
    for path_key, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue

        for method in HTTP_METHODS:
            if method not in path_item:
                continue

            operation = path_item[method]
            if not isinstance(operation, dict):
                continue

            # Extract operation details
            operation_id = operation.get("operationId")
            summary = operation.get("summary")
            tags = operation.get("tags", [])
            if not isinstance(tags, list):
                tags = []

            operations.append(
                OpenApiOperation(
                    path=path_key,
                    method=method.upper(),
                    operation_id=operation_id,
                    summary=summary,
                    tags=tags,
                    line=line_num,
                    file_path=str(file_path),
                )
            )

        line_num += 1  # Approximate line tracking

    return operations


def _normalize_path(path: str) -> str:
    """Normalize path parameters for matching.

    Converts:
    - /users/{id} -> /users/:id
    - /users/<id> -> /users/:id
    - /users/:id -> /users/:id (unchanged)
    """
    # Convert {param} to :param
    path = re.sub(r"\{([^}]+)\}", r":\1", path)
    # Convert <param> to :param
    path = re.sub(r"<([^>]+)>", r":\1", path)
    return path


def _paths_match(openapi_path: str, route_path: str) -> bool:
    """Check if an OpenAPI path matches a route path."""
    # Normalize both paths
    norm_openapi = _normalize_path(openapi_path)
    norm_route = _normalize_path(route_path)

    # Exact match after normalization
    if norm_openapi == norm_route:
        return True

    # Pattern match - convert :param to regex
    pattern = re.sub(r":[\w]+", r"[^/]+", norm_openapi)
    pattern = f"^{pattern}$"
    return bool(re.match(pattern, norm_route))


def _has_route_concept(symbol: Symbol) -> bool:
    """Check if symbol has a route concept in meta.concepts."""
    if not symbol.meta:  # pragma: no cover
        return False
    concepts = symbol.meta.get("concepts", [])
    return any(c.get("concept") == "route" for c in concepts if isinstance(c, dict))


def _get_route_info_from_concept(symbol: Symbol) -> tuple[str | None, str | None]:
    """Extract route path and method from concept metadata.

    Returns:
        Tuple of (route_path, http_method) from the first route concept,
        or (None, None) if no route concept exists.
    """
    if not symbol.meta:
        return None, None

    concepts = symbol.meta.get("concepts", [])
    for concept in concepts:
        if isinstance(concept, dict) and concept.get("concept") == "route":
            return concept.get("path"), concept.get("method")
    return None, None


def _get_route_symbols(ctx: LinkerContext) -> list[Symbol]:
    """Extract route symbols from context.

    Route symbols are either:
    - kind="route" (Ruby, Go, Rust, Express analyzers)
    - have route concept in meta.concepts (FRAMEWORK_PATTERNS enrichment)
    """
    return [
        s for s in ctx.symbols
        if s.kind == "route" or _has_route_concept(s)
    ]


def link_openapi(root: Path, route_symbols: list[Symbol]) -> OpenApiLinkResult:
    """Link OpenAPI operations to route handlers.

    Args:
        root: Repository root path
        route_symbols: Route symbols from language analyzers

    Returns:
        OpenApiLinkResult with symbols and edges
    """
    start_time = time.time()
    result_symbols: list[Symbol] = []
    result_edges: list[Edge] = []
    files_analyzed = 0

    # Find and parse OpenAPI specs
    all_operations: list[OpenApiOperation] = []
    seen_files: set[Path] = set()

    for file_path in _find_openapi_files(root):
        if file_path in seen_files:
            continue
        seen_files.add(file_path)

        operations = _parse_openapi_spec(file_path)
        all_operations.extend(operations)
        if operations:
            files_analyzed += 1

    # Create symbols for each operation
    for op in all_operations:
        symbol_id = f"openapi:{op.file_path}:{op.line}:{op.method}:{op.path}"

        symbol = Symbol(
            id=symbol_id,
            name=op.operation_id or f"{op.method} {op.path}",
            kind="openapi_operation",
            language="openapi",
            path=op.file_path,
            span=Span(start_line=op.line, end_line=op.line, start_col=0, end_col=0),
            signature=f"{op.method} {op.path}",
            meta={
                "http_method": op.method,
                "path": op.path,
                "operation_id": op.operation_id,
                "summary": op.summary,
                "tags": op.tags,
            },
        )
        result_symbols.append(symbol)

        # Try to match to route handlers
        matched = False
        for route in route_symbols:
            # Extract route info from concept metadata (single source of truth)
            route_path, route_method = _get_route_info_from_concept(route)

            if not route_path:
                continue

            # Match by path and method
            if _paths_match(op.path, route_path):
                # Method must match (or be wildcard)
                if route_method and route_method.upper() not in (op.method, "ANY", "*"):
                    continue

                # Create edge linking spec to implementation
                edge_id = f"edge:openapi:{symbol.id}:{route.id}"
                edge = Edge(
                    id=edge_id,
                    src=symbol.id,
                    dst=route.id,
                    edge_type="openapi_implements",
                    line=op.line,
                    confidence=0.85,
                    evidence_type="openapi_path_match",
                    meta={
                        "openapi_path": op.path,
                        "route_path": route_path,
                        "method": op.method,
                    },
                )
                result_edges.append(edge)
                matched = True

        # Also try operationId matching
        if op.operation_id and not matched:
            for route in route_symbols:
                # Check if operationId matches function name
                if route.name == op.operation_id:
                    edge_id = f"edge:openapi:opid:{symbol.id}:{route.id}"
                    edge = Edge(
                        id=edge_id,
                        src=symbol.id,
                        dst=route.id,
                        edge_type="openapi_implements",
                        line=op.line,
                        confidence=0.9,  # Higher confidence for operationId match
                        evidence_type="openapi_operation_id_match",
                        meta={
                            "operation_id": op.operation_id,
                            "route_name": route.name,
                        },
                    )
                    result_edges.append(edge)

    # Create analysis run
    run = AnalysisRun.create(PASS_ID, PASS_VERSION)
    run.files_analyzed = files_analyzed
    run.duration_ms = int((time.time() - start_time) * 1000)

    return OpenApiLinkResult(
        edges=result_edges,
        symbols=result_symbols,
        run=run,
    )


# Linker requirements
OPENAPI_REQUIREMENTS: list[LinkerRequirement] = [
    LinkerRequirement(
        name="openapi_spec_files",
        description="OpenAPI/Swagger specification files present",
        check=lambda ctx: any(True for _ in _find_openapi_files(ctx.repo_root)),
    ),
]


def _count_openapi_files(ctx: LinkerContext) -> bool:
    """Check if any OpenAPI files exist."""
    for _ in _find_openapi_files(ctx.repo_root):
        return True
    return False


@register_linker(
    "openapi",
    priority=65,  # Run after route detection
    description="OpenAPI/Swagger spec to route handler linking",
    requirements=[
        LinkerRequirement(
            name="openapi_spec_files",
            description="OpenAPI/Swagger specification files present",
            check=_count_openapi_files,
        ),
    ],
    activation=LinkerActivation(frameworks=["openapi", "swagger"]),
)
def openapi_linker(ctx: LinkerContext) -> LinkerResult:
    """OpenAPI linker for registry-based dispatch.

    This wraps link_openapi() to use the LinkerContext/LinkerResult interface.
    """
    route_symbols = _get_route_symbols(ctx)
    result = link_openapi(ctx.repo_root, route_symbols)

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges,
        run=result.run,
    )
