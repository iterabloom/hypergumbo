"""HTTP client-server linker for detecting cross-language API calls.

This linker detects HTTP client calls (fetch, axios, requests, OpenAPI clients)
and links them to server route handlers detected by language analyzers.

Detected Client Patterns
------------------------
JavaScript/TypeScript:
- fetch("/api/users") - Fetch API with literal URL
- fetch(API_URL) - Fetch API with variable URL
- fetch("/api/users", { method: "POST" }) - with options
- axios.get("/api/users") - Axios library with literal URL
- axios.get(config.apiUrl) - Axios with variable URL
- __request(OpenAPI, { method: 'GET', url: '/api/users' }) - OpenAPI generated clients

Python:
- requests.get("/api/users") - requests library with literal URL
- requests.get(API_URL) - requests library with variable URL
- httpx.get("/api/users") - httpx library

Variable URL Detection
----------------------
URLs stored in variables are detected with lower confidence (0.65 vs 0.9):
- const API_URL = '/api/users'; fetch(API_URL) -> detected with url_type="variable"
- Direct literal URLs have url_type="literal" and higher confidence

Server Route Matching
---------------------
Routes are matched by:
1. HTTP method (GET, POST, PUT, DELETE, etc.)
2. URL path pattern (exact match or parameterized)

Parameterized routes are supported:
- /users/:id (Express/Flask style)
- /users/{id} (FastAPI style)
- /users/<id> (Flask style)

How It Works
------------
1. Collect route symbols from language analyzers (kind="route")
2. Scan source files for HTTP client calls
3. Extract URL and method from each call (literal or variable)
4. Match to route symbols by method + path pattern
5. Create http_calls edges linking client to server

Why This Design
---------------
- Cross-language linking enables full-stack code understanding
- Regex-based client detection is fast and portable
- Route matching handles common parameterization patterns
- Symbols for client calls enable slice traversal from either end
- Variable URL detection catches patterns where URLs are stored in constants
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .registry import LinkerContext, LinkerResult, LinkerRequirement, register_linker

PASS_ID = "http-linker-v1"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class HttpClientCall:
    """Represents a detected HTTP client call."""

    method: str  # GET, POST, PUT, DELETE, etc.
    url: str  # The URL string from the call
    line: int  # Line number in source
    file_path: str  # Source file path
    language: str  # Source language
    url_type: str = "literal"  # "literal" or "variable"


@dataclass
class HttpLinkResult:
    """Result of HTTP client-server linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


# Pattern for matching variable identifiers (e.g., API_URL, config.apiUrl)
_IDENTIFIER = r"[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*"

# Combined pattern: matches either quoted string literal or variable identifier
# Group 1: literal URL, Group 2: variable identifier
_URL_ARG = rf"(?:['\"]([^'\"]+)['\"]|({_IDENTIFIER}))"

# Python HTTP client patterns - supports both literal URLs and variables
PYTHON_REQUESTS_PATTERN = re.compile(
    rf"""(?:requests|httpx)\.
        (get|post|put|patch|delete|head|options)
        \s*\(\s*
        {_URL_ARG}""",
    re.VERBOSE | re.IGNORECASE,
)

# JavaScript fetch pattern - supports both literal URLs and variables
JS_FETCH_PATTERN = re.compile(
    rf"""fetch\s*\(\s*{_URL_ARG}""",
    re.VERBOSE,
)

# JavaScript fetch with method option - literal URL only (complex pattern)
JS_FETCH_METHOD_PATTERN = re.compile(
    r"""fetch\s*\(\s*["']([^"']+)["']\s*,\s*\{[^}]*method\s*:\s*["'](\w+)["']""",
    re.VERBOSE | re.IGNORECASE,
)

# JavaScript axios pattern - supports both literal URLs and variables
JS_AXIOS_PATTERN = re.compile(
    rf"""axios\.(get|post|put|patch|delete|head|options)
        \s*\(\s*{_URL_ARG}""",
    re.VERBOSE | re.IGNORECASE,
)

# OpenAPI-generated client pattern (__request from @hey-api/openapi-ts, etc.)
# Matches: __request(OpenAPI, { method: 'GET', url: '/api/v1/items/' })
JS_OPENAPI_REQUEST_PATTERN = re.compile(
    r"""__request\s*\(\s*\w+\s*,\s*\{[^}]*
        method\s*:\s*["'](\w+)["'][^}]*
        url\s*:\s*["']([^"']+)["']""",
    re.VERBOSE | re.IGNORECASE | re.DOTALL,
)

# Alternative OpenAPI pattern where url comes before method
JS_OPENAPI_REQUEST_ALT_PATTERN = re.compile(
    r"""__request\s*\(\s*\w+\s*,\s*\{[^}]*
        url\s*:\s*["']([^"']+)["'][^}]*
        method\s*:\s*["'](\w+)["']""",
    re.VERBOSE | re.IGNORECASE | re.DOTALL,
)


def _extract_url_from_match(match: re.Match, literal_group: int = 1, var_group: int = 2) -> tuple[str, str]:
    """Extract URL and url_type from a regex match.

    The _URL_ARG pattern captures:
    - Group literal_group: string literal (e.g., '/api/users')
    - Group var_group: variable identifier (e.g., API_URL, config.apiUrl)

    Returns:
        Tuple of (url, url_type) where url_type is "literal" or "variable".
    """
    literal = match.group(literal_group)
    if literal:
        return literal, "literal"
    variable = match.group(var_group)
    return variable, "variable"


def _extract_path_from_url(url: str) -> str | None:
    """Extract the path component from a URL.

    Args:
        url: A URL string, either full (http://...) or path-only (/api/...)

    Returns:
        The path component, or None if invalid.
    """
    if not url:
        return None

    # Strip query parameters
    url = url.split("?")[0]

    # If it's a full URL, parse it
    if url.startswith(("http://", "https://")):
        parsed = urlparse(url)
        return parsed.path or "/"

    # Otherwise it's already a path
    return url


def _match_route_pattern(request_path: str, route_pattern: str) -> bool:
    """Check if a request path matches a route pattern.

    Handles parameterized routes:
    - :param (Express/Flask)
    - {param} (FastAPI)
    - <param> (Flask)

    Args:
        request_path: The actual path from the HTTP call (e.g., /users/123)
        route_pattern: The route pattern (e.g., /users/:id)

    Returns:
        True if the path matches the pattern.
    """
    # Normalize trailing slashes
    request_path = request_path.rstrip("/") or "/"
    route_pattern = route_pattern.rstrip("/") or "/"

    # Exact match
    if request_path == route_pattern:
        return True

    # Convert route pattern to regex
    # Escape special regex chars except our param patterns
    pattern = route_pattern

    # Replace :param with regex group
    pattern = re.sub(r":(\w+)", r"[^/]+", pattern)

    # Replace {param} with regex group
    pattern = re.sub(r"\{(\w+)\}", r"[^/]+", pattern)

    # Replace <param> with regex group
    pattern = re.sub(r"<(\w+)>", r"[^/]+", pattern)

    # Escape remaining special chars
    pattern = pattern.replace(".", r"\.")

    # Match full path
    pattern = f"^{pattern}$"

    try:
        return bool(re.match(pattern, request_path))
    except re.error:  # pragma: no cover
        return False


def _find_source_files(root: Path) -> Iterator[Path]:
    """Find files that might contain HTTP client calls."""
    patterns = ["**/*.py", "**/*.js", "**/*.ts", "**/*.jsx", "**/*.tsx"]
    for path in find_files(root, patterns):
        yield path


def _scan_python_file(file_path: Path, content: str) -> list[HttpClientCall]:
    """Scan a Python file for HTTP client calls."""
    calls: list[HttpClientCall] = []

    for match in PYTHON_REQUESTS_PATTERN.finditer(content):
        method = match.group(1).upper()
        # Groups: 1=method, 2=literal URL, 3=variable URL
        url, url_type = _extract_url_from_match(match, literal_group=2, var_group=3)
        line_num = content[: match.start()].count("\n") + 1

        calls.append(
            HttpClientCall(
                method=method,
                url=url,
                line=line_num,
                file_path=str(file_path),
                language="python",
                url_type=url_type,
            )
        )

    return calls


def _scan_javascript_file(file_path: Path, content: str) -> list[HttpClientCall]:
    """Scan a JavaScript/TypeScript file for HTTP client calls."""
    calls: list[HttpClientCall] = []

    # Check for fetch with method option first (more specific, literal URLs only)
    fetch_method_matches = set()
    for match in JS_FETCH_METHOD_PATTERN.finditer(content):
        url = match.group(1)
        method = match.group(2).upper()
        line_num = content[: match.start()].count("\n") + 1

        calls.append(
            HttpClientCall(
                method=method,
                url=url,
                line=line_num,
                file_path=str(file_path),
                language="javascript",
                url_type="literal",
            )
        )
        fetch_method_matches.add(match.start())

    # Check for simple fetch calls (default to GET) - supports variables
    for match in JS_FETCH_PATTERN.finditer(content):
        # Skip if we already captured this with method option
        if match.start() in fetch_method_matches:
            continue

        # Groups: 1=literal URL, 2=variable URL
        url, url_type = _extract_url_from_match(match, literal_group=1, var_group=2)
        line_num = content[: match.start()].count("\n") + 1

        calls.append(
            HttpClientCall(
                method="GET",
                url=url,
                line=line_num,
                file_path=str(file_path),
                language="javascript",
                url_type=url_type,
            )
        )

    # Check for axios calls - supports variables
    for match in JS_AXIOS_PATTERN.finditer(content):
        method = match.group(1).upper()
        # Groups: 1=method, 2=literal URL, 3=variable URL
        url, url_type = _extract_url_from_match(match, literal_group=2, var_group=3)
        line_num = content[: match.start()].count("\n") + 1

        calls.append(
            HttpClientCall(
                method=method,
                url=url,
                line=line_num,
                file_path=str(file_path),
                language="javascript",
                url_type=url_type,
            )
        )

    # Check for OpenAPI-generated __request() calls (literal URLs only)
    openapi_matches = set()
    for match in JS_OPENAPI_REQUEST_PATTERN.finditer(content):
        method = match.group(1).upper()
        url = match.group(2)
        line_num = content[: match.start()].count("\n") + 1

        calls.append(
            HttpClientCall(
                method=method,
                url=url,
                line=line_num,
                file_path=str(file_path),
                language="javascript",
                url_type="literal",
            )
        )
        openapi_matches.add(match.start())

    # Check alternative pattern (url before method)
    for match in JS_OPENAPI_REQUEST_ALT_PATTERN.finditer(content):
        if match.start() in openapi_matches:  # pragma: no cover
            continue  # Already captured
        url = match.group(1)
        method = match.group(2).upper()
        line_num = content[: match.start()].count("\n") + 1

        calls.append(
            HttpClientCall(
                method=method,
                url=url,
                line=line_num,
                file_path=str(file_path),
                language="javascript",
                url_type="literal",
            )
        )

    return calls


def _create_client_symbol(call: HttpClientCall, root: Path) -> Symbol:
    """Create a symbol for an HTTP client call."""
    rel_path = Path(call.file_path).relative_to(root) if root else Path(call.file_path)

    return Symbol(
        id=f"{rel_path}::http_client::{call.line}",
        name=f"{call.method} {call.url}",
        kind="http_client",
        path=call.file_path,
        span=Span(
            start_line=call.line,
            start_col=0,
            end_line=call.line,
            end_col=0,
        ),
        language=call.language,
        stable_id=call.method,
        meta={
            "http_method": call.method,
            "url_path": _extract_path_from_url(call.url) or call.url,
            "raw_url": call.url,
            "url_type": call.url_type,
        },
    )


def link_http(root: Path, route_symbols: list[Symbol]) -> HttpLinkResult:
    """Link HTTP client calls to server route handlers.

    Args:
        root: Repository root path.
        route_symbols: Route symbols from language analyzers (kind="route").

    Returns:
        HttpLinkResult with edges linking clients to servers.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    edges: list[Edge] = []
    symbols: list[Symbol] = []
    files_scanned = 0

    # Collect all HTTP client calls
    all_calls: list[HttpClientCall] = []

    for file_path in _find_source_files(root):
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            files_scanned += 1

            if file_path.suffix == ".py":
                calls = _scan_python_file(file_path, content)
            else:
                calls = _scan_javascript_file(file_path, content)

            all_calls.extend(calls)
        except (OSError, IOError):  # pragma: no cover
            pass

    # Create symbols for each client call
    for call in all_calls:
        client_symbol = _create_client_symbol(call, root)
        symbols.append(client_symbol)

        # Try to match to a route symbol
        call_path = _extract_path_from_url(call.url)
        if not call_path:  # pragma: no cover
            continue

        for route in route_symbols:
            # Try concept metadata first (FRAMEWORK_PATTERNS phase)
            concept_path, concept_method = _get_route_info_from_concept(route)

            # Fall back to legacy meta if no concept
            if concept_path:
                route_path = concept_path
                route_method = concept_method
            else:
                route_path = route.meta.get("route_path", "") if route.meta else ""
                route_method = (
                    route.meta.get("http_method", route.stable_id)
                    if route.meta
                    else route.stable_id
                )

            # Must match HTTP method
            if route_method and route_method.upper() != call.method.upper():
                continue

            # Must match route path
            if not route_path:  # pragma: no cover
                continue

            if _match_route_pattern(call_path, route_path):
                # Create edge from client to server
                is_cross_language = client_symbol.language != route.language
                is_variable_url = call.url_type == "variable"

                # Lower confidence for variable URLs (can't verify at static analysis)
                if is_variable_url:
                    base_confidence = 0.65
                elif is_cross_language:
                    base_confidence = 0.8
                else:
                    base_confidence = 0.9

                edge = Edge.create(
                    src=client_symbol.id,
                    dst=route.id,
                    edge_type="http_calls",
                    line=call.line,
                    confidence=base_confidence,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="http_url_match",
                )
                edge.meta = {
                    "http_method": call.method,
                    "url_path": call_path,
                    "cross_language": is_cross_language,
                    "url_type": call.url_type,
                }
                edges.append(edge)
                break  # Only link to first matching route

    run.duration_ms = int((time.time() - start_time) * 1000)
    run.files_analyzed = files_scanned

    return HttpLinkResult(edges=edges, symbols=symbols, run=run)


# =============================================================================
# Linker Registry Integration
# =============================================================================


def _has_route_concept(symbol: Symbol) -> bool:
    """Check if symbol has a route concept in meta.concepts."""
    if not symbol.meta:
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
    - kind="route" (Ruby, Go, Rust analyzers)
    - have meta.route_path (Python, JS framework analyzers - legacy)
    - have route concept in meta.concepts (FRAMEWORK_PATTERNS phase)
    """
    return [
        s for s in ctx.symbols
        if s.kind == "route"
        or (s.meta and s.meta.get("route_path"))
        or _has_route_concept(s)
    ]


def _count_route_symbols(ctx: LinkerContext) -> int:
    """Count available route symbols for requirement check."""
    return len(_get_route_symbols(ctx))


HTTP_REQUIREMENTS = [
    LinkerRequirement(
        name="route_symbols",
        description="Route handler symbols (kind=route or meta.route_path)",
        check=_count_route_symbols,
    ),
]


@register_linker(
    "http",
    priority=60,  # Run after analyzers have produced route symbols
    description="HTTP client-server linking (fetch, axios, requests to routes)",
    requirements=HTTP_REQUIREMENTS,
)
def http_linker(ctx: LinkerContext) -> LinkerResult:
    """HTTP linker for registry-based dispatch.

    This wraps link_http() to use the LinkerContext/LinkerResult interface.
    Extracts route symbols from ctx and delegates to the core linking logic.
    """
    route_symbols = _get_route_symbols(ctx)
    result = link_http(ctx.repo_root, route_symbols)

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges,
        run=result.run,
    )
