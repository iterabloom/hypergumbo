"""HTTP client-server linker for detecting cross-language API calls.

This linker detects HTTP client calls (fetch, axios, requests, OpenAPI clients)
and links them to server route handlers detected by language analyzers.

Detected Client Patterns
------------------------
JavaScript/TypeScript:
- fetch("/api/users") - Fetch API
- fetch("/api/users", { method: "POST" }) - with options
- axios.get("/api/users") - Axios library
- axios.post("/api/users", data) - Axios with data
- __request(OpenAPI, { method: 'GET', url: '/api/users' }) - OpenAPI generated clients

Python:
- requests.get("/api/users") - requests library
- requests.post("/api/users", json=data) - with data
- httpx.get("/api/users") - httpx library

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
3. Extract URL and method from each call
4. Match to route symbols by method + path pattern
5. Create http_calls edges linking client to server

Why This Design
---------------
- Cross-language linking enables full-stack code understanding
- Regex-based client detection is fast and portable
- Route matching handles common parameterization patterns
- Symbols for client calls enable slice traversal from either end
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


@dataclass
class HttpLinkResult:
    """Result of HTTP client-server linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


# Python HTTP client patterns
PYTHON_REQUESTS_PATTERN = re.compile(
    r"""(?:requests|httpx)\.
        (get|post|put|patch|delete|head|options)
        \s*\(\s*
        ["']([^"']+)["']""",
    re.VERBOSE | re.IGNORECASE,
)

# JavaScript fetch pattern
JS_FETCH_PATTERN = re.compile(
    r"""fetch\s*\(\s*["']([^"']+)["']""",
    re.VERBOSE,
)

# JavaScript fetch with method option
JS_FETCH_METHOD_PATTERN = re.compile(
    r"""fetch\s*\(\s*["']([^"']+)["']\s*,\s*\{[^}]*method\s*:\s*["'](\w+)["']""",
    re.VERBOSE | re.IGNORECASE,
)

# JavaScript axios pattern
JS_AXIOS_PATTERN = re.compile(
    r"""axios\.(get|post|put|patch|delete|head|options)
        \s*\(\s*["']([^"']+)["']""",
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
        url = match.group(2)
        line_num = content[: match.start()].count("\n") + 1

        calls.append(
            HttpClientCall(
                method=method,
                url=url,
                line=line_num,
                file_path=str(file_path),
                language="python",
            )
        )

    return calls


def _scan_javascript_file(file_path: Path, content: str) -> list[HttpClientCall]:
    """Scan a JavaScript/TypeScript file for HTTP client calls."""
    calls: list[HttpClientCall] = []

    # Check for fetch with method option first (more specific)
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
            )
        )
        fetch_method_matches.add(match.start())

    # Check for simple fetch calls (default to GET)
    for match in JS_FETCH_PATTERN.finditer(content):
        # Skip if we already captured this with method option
        if match.start() in fetch_method_matches:
            continue

        url = match.group(1)
        line_num = content[: match.start()].count("\n") + 1

        calls.append(
            HttpClientCall(
                method="GET",
                url=url,
                line=line_num,
                file_path=str(file_path),
                language="javascript",
            )
        )

    # Check for axios calls
    for match in JS_AXIOS_PATTERN.finditer(content):
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
            )
        )

    # Check for OpenAPI-generated __request() calls
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
            # Must match HTTP method
            route_method = route.meta.get("http_method", route.stable_id)
            if route_method and route_method.upper() != call.method.upper():
                continue

            # Must match route path
            route_path = route.meta.get("route_path", "")
            if not route_path:  # pragma: no cover
                continue

            if _match_route_pattern(call_path, route_path):
                # Create edge from client to server
                is_cross_language = client_symbol.language != route.language

                edge = Edge.create(
                    src=client_symbol.id,
                    dst=route.id,
                    edge_type="http_calls",
                    line=call.line,
                    confidence=0.8 if is_cross_language else 0.9,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="http_url_match",
                )
                edge.meta = {
                    "http_method": call.method,
                    "url_path": call_path,
                    "cross_language": is_cross_language,
                }
                edges.append(edge)
                break  # Only link to first matching route

    run.duration_ms = int((time.time() - start_time) * 1000)
    run.files_analyzed = files_scanned

    return HttpLinkResult(edges=edges, symbols=symbols, run=run)
