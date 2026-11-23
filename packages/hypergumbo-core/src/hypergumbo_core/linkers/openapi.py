# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: OpenAPI/Swagger for detecting API schema to handler connections.

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
- Summary (for documentation)

Handler Matching
----------------
Operations are linked to route symbols (``routes.is_route``) by:
1. Path + method match - every route whose path pattern and HTTP method
   match (confidence 0.85)
2. OperationId fallback - only when no path match was found, a route
   symbol whose name equals the operationId (confidence 0.9)
Tags are recorded in ``meta["tags"]`` only; no tag-based matching is done.

How It Works
------------
1. Find non-test files named openapi, swagger or api with a .yaml/.yml/.json
   extension (any depth); keep those with an ``openapi: 3.x`` or
   ``swagger: 2.x`` key (a string or a YAML number) or a ``paths`` mapping
2. Parse spec and extract path operations
3. Create a ``kind="function"`` symbol per operation, tagged
   ``meta["framework_role"]="openapi_operation"`` (``language=None``,
   ``protocol_origin="openapi"``, ``discovery_language`` the spec file's
   language, ``yaml`` or ``json``, which is also the id's language slot:
   ADR-0034's rule for a Class B stand-in; the slot read ``openapi``, no
   language, and ``discovery_language`` was unset)
4. Match to route symbols by path pattern and HTTP method, the path under
   the document's prefixes (``basePath`` / ``servers``; see
   :func:`_route_serves` for a route registered relative to the prefix)
5. Create ``references`` edges (spec operation → handler, tagged
   ``meta.ref_construct="openapi_operation"``; audit-findings 0016). The
   spec→handler direction is deliberate: it powers the forward slice from
   a spec, which an impl→contract ``implements`` edge would reverse.

Activation
----------
The linker runs when the ``openapi`` or ``swagger`` framework is detected.
No manifest names an OpenAPI document, so ``profile._detect_openapi`` detects
``openapi`` from the documents themselves, with :func:`has_openapi_document`
(the files this linker reads, as ``.proto`` files are the ``protobuf``
framework for the gRPC linker); before it, only the Java ``io.swagger``
dependency activated the linker and a repository with an ``openapi.yaml`` got
no operation node.

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
from urllib.parse import urlsplit

from ..analyze.base import make_symbol_id
from ..discovery import find_non_test_files
from ..ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from ..routes import is_route, method_matches, method_token, route_of
from ..taxonomy import get_language
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerRequirement,
    LinkerResult,
    register_linker,
)
from ._text_filters import read_masked_source

PASS_ID = make_pass_id("openapi-linker")


@dataclass
class OpenApiOperation:
    """Represents an OpenAPI operation."""

    path: str  # e.g., /users/{id}
    method: str  # GET, POST, PUT, DELETE, etc.
    operation_id: str | None  # e.g., getUserById
    summary: str | None  # Short description
    tags: list[str]  # Grouping tags
    line: int  # First line of the operation (its method key) in the spec file
    file_path: str  # Spec file path
    end_line: int = 0  # Last line of the operation's mapping (0: not known)
    # The document's path prefixes: Swagger 2 ``basePath``, the path part of
    # each OpenAPI 3 ``servers[].url``; ``("",)`` when it declares none.
    prefixes: tuple[str, ...] = ("",)


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


def _is_openapi_spec(data: object) -> bool:
    """Check if the data looks like an OpenAPI/Swagger spec.

    The version is read as text: unquoted, YAML loads ``swagger: 2.0`` and
    ``openapi: 3.1`` as numbers, which have no ``startswith``.
    """
    if not isinstance(data, dict):
        return False
    # OpenAPI 3.x
    if str(data.get("openapi", "")).startswith("3."):
        return True
    # Swagger 2.x
    if str(data.get("swagger", "")).startswith("2."):
        return True
    # Check for paths key (common to both)
    if "paths" in data and isinstance(data["paths"], dict):
        return True
    return False


def _find_openapi_files(root: Path) -> Iterator[Path]:
    """Find OpenAPI spec files in the repository."""
    # First, try specific filenames
    for pattern in OPENAPI_FILE_PATTERNS:
        for file_path in find_non_test_files(root, [pattern]):
            if file_path.suffix.lower() in (".yaml", ".yml", ".json"):
                yield file_path


def _operation_spans(content: str) -> dict[tuple[str, str], tuple[int, int]]:
    """``{(path, method): (first line, last line)}`` of every operation, 1-based.

    Read from the YAML node tree (``yaml.compose``), which keeps each node's
    position; JSON is YAML here, so a ``.json`` spec is read the same way.
    The first line is the method key's, the last the operation mapping's. A
    document the YAML composer rejects (a tab-indented JSON file) gives no
    spans, and its operations get the ``0-0`` span (ADR-0036's "no source
    span" sentinel) rather than a guessed line.
    """
    try:
        import yaml
    except ImportError:  # pragma: no cover - pyyaml is a dependency
        return {}
    try:
        root = yaml.compose(content)
    except yaml.YAMLError:
        return {}
    spans: dict[tuple[str, str], tuple[int, int]] = {}
    paths_node = None
    if isinstance(root, yaml.MappingNode):
        for key_node, value_node in root.value:
            if key_node.value == "paths":
                paths_node = value_node
    if not isinstance(paths_node, yaml.MappingNode):
        return spans
    lines = content.splitlines()
    for path_key, path_item in paths_node.value:
        if not isinstance(path_item, yaml.MappingNode):
            continue
        for method_key, op_node in path_item.value:
            start = method_key.start_mark.line + 1
            spans[(str(path_key.value), str(method_key.value))] = (
                start, _last_line(lines, op_node.end_mark, start),
            )
    return spans


def _last_line(lines: list[str], end_mark: Any, start: int) -> int:
    """The 1-based last line of a node whose ``end_mark`` is given.

    A block node's end mark sits at the NEXT token (the following key, after
    its indentation), so the node ends on the last non-blank line before the
    mark; a flow node's (``{...}``) mark is right after its closing brace, on
    its own last line.
    """
    line = int(end_mark.line)  # 0-based
    if line < len(lines) and lines[line][: int(end_mark.column)].strip():
        return line + 1
    while line > start and not lines[line - 1].strip():
        line -= 1
    return max(start, line)


def _read_spec(file_path: Path) -> str | None:
    """The spec file's text (masked), or None when it cannot be read."""
    try:
        return read_masked_source(file_path, encoding="utf-8", errors="replace")
    except (OSError, IOError):  # pragma: no cover
        return None


def _load_openapi_document(file_path: Path) -> dict[str, Any] | None:
    """The parsed document when ``file_path`` is an OpenAPI/Swagger spec, else None."""
    content = _read_spec(file_path)
    if content is None:  # pragma: no cover
        return None
    return _parse_document(file_path, content)


def _parse_document(file_path: Path, content: str) -> dict[str, Any] | None:
    """``content`` parsed as the spec ``file_path`` is, when it is one, else None."""

    data: dict[str, Any] | None = None
    if file_path.suffix.lower() in (".yaml", ".yml"):
        data = _load_yaml(content)
    else:
        data = _load_json(content)

    if not data or not _is_openapi_spec(data):
        return None
    return data


def has_openapi_document(root: Path) -> bool:
    """Whether ``root`` holds a file this linker reads as an OpenAPI spec.

    ``profile._detect_openapi`` asks it, so the ``openapi`` framework is
    claimed exactly where this linker finds a document: a name the globs
    catch whose content is no spec (an ``api.json`` of something else)
    claims nothing.
    """
    return any(_load_openapi_document(p) is not None for p in _find_openapi_files(root))


def _base_paths(data: dict[str, Any]) -> tuple[str, ...]:
    """The path prefixes a spec's operations are served under.

    Swagger 2 ``basePath``; OpenAPI 3 the path part of every
    ``servers[].url`` (absolute or relative; ``{var}`` placeholders take the
    server variable's ``default``). ``/`` and an absent prefix are ``""``.
    """
    raw: list[str] = []
    base = data.get("basePath")
    if isinstance(base, str):
        raw.append(base)
    servers = data.get("servers")
    if isinstance(servers, list):
        for server in servers:
            if not isinstance(server, dict) or not isinstance(server.get("url"), str):
                continue
            url = server["url"]
            variables = server.get("variables")
            if isinstance(variables, dict):
                for name, var in variables.items():
                    if isinstance(var, dict) and isinstance(var.get("default"), str):
                        url = url.replace("{" + str(name) + "}", var["default"])
            raw.append(urlsplit(url).path)
    prefixes = sorted({p.rstrip("/") for p in raw})
    return tuple(prefixes) or ("",)


def _parse_openapi_spec(file_path: Path) -> list[OpenApiOperation]:
    """Parse an OpenAPI spec file and extract operations."""
    operations: list[OpenApiOperation] = []

    content = _read_spec(file_path)
    if content is None:  # pragma: no cover
        return operations
    data = _parse_document(file_path, content)
    if data is None:
        return operations
    spans = _operation_spans(content)
    prefixes = _base_paths(data)

    # Extract paths
    paths = data.get("paths", {})
    if not isinstance(paths, dict):
        return operations

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

            # The operation's real lines (it was a count of PATHS, so every
            # operation of the third path sat on line 3).
            start, end = spans.get((str(path_key), method), (0, 0))
            operations.append(
                OpenApiOperation(
                    path=path_key,
                    method=method.upper(),
                    operation_id=operation_id,
                    summary=summary,
                    tags=tags,
                    line=start,
                    file_path=str(file_path),
                    end_line=end,
                    prefixes=prefixes,
                )
            )

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


def _route_serves(
    op: OpenApiOperation, route_path: str, route_file: str, spec_dir: str,
) -> bool:
    """Whether a route whose path is ``route_path`` serves ``op``'s path.

    An operation is served at ``<prefix><path>`` for each of the document's
    prefixes (``basePath``, ``servers``). A route registered at the full path
    serves it wherever it is. A route at the bare ``<path>`` serves it only
    when the route's file sits under the spec's own directory: server code
    generated from a spec (go-swagger's ``restapi``, openapi-generator stubs)
    is mounted at the prefix and registers paths relative to it, while a
    same-named path elsewhere is another router's (a React client-side route,
    another API version's router). Before, every route at the bare path was
    linked and the prefix was ignored (alertmanager: 4 of 21 edges ended on
    React routes, 8 on the v1 router). With no prefix the bare path is the
    full path.
    """
    for prefix in op.prefixes:
        if _paths_match(prefix + op.path, route_path):
            return True
    if op.prefixes == ("",) or not _paths_match(op.path, route_path):
        return False
    return spec_dir in ("", ".") or route_file.startswith(spec_dir + "/")


def _get_route_info_from_concept(symbol: Symbol) -> tuple[str | None, str | None]:
    """Extract ``(route_path, http_method)`` for OpenAPI operation matching.

    Delegates to the canonical MARKER-FIRST ``routes.route_of``, so a
    marker-only route (Go net/http, Express — ``framework_role`` +
    ``route_path``/``http_method``, no concept) yields its path and stays in
    OpenAPI matching, as does a concept-only route (WI-tosul). The ``'WS'``
    sentinel is reconstructed from ``route_of``'s normalized ``protocol``.
    """
    info = route_of(symbol)
    if info is None:
        return None, None
    method = method_token(info)
    return info["path"], method


def _get_route_symbols(ctx: LinkerContext) -> list[Symbol]:
    """Extract route symbols (marker OR ``concept == 'route'``) from context.

    Exactly ``routes.is_route``'s disjunction, via the canonical accessor
    (WI-tosul).
    """
    return [s for s in ctx.symbols if is_route(s)]


def _relative_dir(file_path: str, root: Path) -> str:
    """The spec file's directory, repo-relative and ``/``-separated, the way
    the analyzers spell a route symbol's ``path``."""
    path = Path(file_path)
    try:
        path = path.relative_to(root)
    except ValueError:  # pragma: no cover - the spec globs yield paths under root
        pass
    return path.parent.as_posix()


def link_openapi(root: Path, route_symbols: list[Symbol]) -> OpenApiLinkResult:
    """Link OpenAPI operations to route handlers.

    Args:
        root: Repository root path
        route_symbols: Route symbols from language analyzers

    Returns:
        OpenApiLinkResult with symbols and edges
    """
    start_time = time.time()
    # Create the AnalysisRun up front so its execution_id is available to
    # stamp every synthesised Symbol (INV-sopon).
    run = AnalysisRun.create(PASS_ID, PASS_VERSION)
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
        # ADR-0031 / ADR-0034: the spec file's language (``yaml`` or
        # ``json``; the globs admit no other suffix) is the stand-in's
        # discovery language and its id's language slot. The slot read
        # ``openapi``, which is no language.
        spec_language = get_language(Path(op.file_path))
        assert spec_language is not None  # .yaml / .yml / .json only
        # ADR-0036 Ruling 2: the kind slot is the Symbol's kind, not the
        # role word ``operation`` (which names a GraphQL construct in the
        # registry). Ruling 1: the name slot is the Symbol's name, the
        # operationId (else ``METHOD path``); it held ``METHOD path`` for
        # every operation. The factory sanitizes it.
        op_name = op.operation_id or f"{op.method} {op.path}"
        symbol_id = make_symbol_id(
            spec_language, op.file_path, op.line, op.end_line, op_name, "function",
        )

        # Canonical kind="function" + meta["framework_role"] (ADR-0027;
        # audit-findings 0013).
        # ADR-0031 Class B: synthetic stand-in for an OpenAPI operation.
        # "openapi" is not a real language, so language=None;
        # discovery_language records the spec-file language (yaml/json),
        # protocol_origin="openapi".
        symbol = Symbol(
            id=symbol_id,
            name=op_name,
            kind="function",
            language=None,
            discovery_language=spec_language,
            protocol_origin="openapi",
            path=op.file_path,
            span=Span(start_line=op.line, end_line=op.end_line, start_col=0, end_col=0),
            signature=f"{op.method} {op.path}",
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            meta={
                "http_method": op.method,
                "path": op.path,
                "operation_id": op.operation_id,
                "summary": op.summary,
                "tags": op.tags,
                "framework_role": "openapi_operation",
            },
        )
        result_symbols.append(symbol)

        # Try to match to route handlers
        matched = False
        spec_dir = _relative_dir(op.file_path, root)
        for route in route_symbols:
            # Extract route info via the canonical routes.route_of accessor
            route_path, route_method = _get_route_info_from_concept(route)

            if not route_path:
                continue

            # Match by path (under the document's prefixes) and method
            if _route_serves(op, route_path, route.path, spec_dir):
                # Method must match, allowing for the wildcard spellings and
                # the comma-joined multi-method form. Delegated to the routes
                # chokepoint, which covers a node-http `ALL` catch-all and a
                # YAML-enriched multi-method route (`"GET,POST"`, which can
                # never equal a single verb) that a plain equality check or
                # an ANY / * check would fail to link (WI-zunal).
                if not method_matches(route_method, op.method):
                    continue

                # Create edge linking spec to implementation
                # A 'references' edge + meta['ref_construct']='openapi_operation',
                # with the dispatch shape in meta['framework_dispatch'] (ADR-0028;
                # audit-findings 0014, 0016). Spec->handler is deliberate: it
                # powers the forward slice from a spec, and an impl->contract
                # 'implements' edge would flip it.
                edge = Edge.create(
                    src=symbol.id,
                    dst=route.id,
                    edge_type="references",
                    line=op.line,
                    confidence=0.85,
                    evidence_type="ast_call_direct",
                    meta={
                        "openapi_path": op.path,
                        "route_path": route_path,
                        "method": op.method,
                        "framework_dispatch": "openapi_path",
                        "ref_construct": "openapi_operation",
                    },
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    # derived-from endpoints: the operation node is minted here; the route is joined
                    #   by path and method
                    derived_from=[route.id],
                )
                result_edges.append(edge)
                matched = True

        # Also try operationId matching
        if op.operation_id and not matched:
            for route in route_symbols:
                # Check if operationId matches function name
                if route.name == op.operation_id:
                    # Same edge shape and spec->handler direction as the
                    # path-match site above.
                    edge = Edge.create(
                        src=symbol.id,
                        dst=route.id,
                        edge_type="references",
                        line=op.line,
                        confidence=0.9,  # Higher confidence for operationId match
                        evidence_type="ast_call_direct",
                        meta={
                            "operation_id": op.operation_id,
                            "route_name": route.name,
                            "framework_dispatch": "openapi_operation_id",
                            "ref_construct": "openapi_operation",
                        },
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        # derived-from endpoints: the operation node is minted here; the route is
                        #   joined by operationId
                        derived_from=[route.id],
                    )
                    result_edges.append(edge)

    # Finalise analysis run statistics (run itself was created above so
    # its execution_id could be stamped onto each Symbol).
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
    "openapi-linker",
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
    # CNF: OpenAPI specs themselves live in YAML/JSON; route handlers they
    # link to come from any HTTP-server language. Plus the YAML/JSON
    # config-format analyzers.
    depends_on=[["python", "javascript", "ruby", "java", "go", "csharp", "rust", "yaml_ansible", "json"]],
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
