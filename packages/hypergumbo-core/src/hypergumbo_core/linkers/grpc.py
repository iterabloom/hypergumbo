"""gRPC/Protobuf linker for detecting RPC communication patterns.

This linker detects gRPC patterns across multiple languages and creates
edges linking clients to their corresponding server implementations.

Detected Patterns
-----------------
Protocol Buffers (.proto):
- service ServiceName { rpc MethodName(...) returns (...); }
- Creates grpc_service symbols

Python gRPC:
- class XxxServicer(xxx_pb2_grpc.XxxServicer) - server implementation
- xxx_pb2_grpc.XxxStub(channel) - client stub
- add_XxxServicer_to_server(...) - service registration

Go gRPC:
- pb.RegisterXxxServer(s, &handler{}) - service registration
- pb.NewXxxClient(conn) - client creation
- pb.UnimplementedXxxServer - server base embedding

Java gRPC:
- extends XxxGrpc.XxxImplBase - service implementation
- XxxGrpc.newBlockingStub(...) / XxxGrpc.newStub(...) - client creation

TypeScript/JavaScript gRPC:
- new XxxClient(...) - grpc-web/grpc-js client

How It Works
------------
1. Scan .proto files for service and RPC method definitions
2. Scan implementation files for gRPC patterns
3. Create symbols for services, clients, and servers
4. Create kind="route" symbols for each proto RPC method, using the
   real HTTP/2 wire path /<package>.<ServiceName>/<MethodName>
5. Match clients to servers by service name
6. Create grpc_calls edges linking client stubs to servicers
7. Create routes_to edges from RPC route symbols to service symbols

Unresolved Edge Resolution
--------------------------
When the Go analyzer creates unresolved edges to gRPC registration functions
(e.g., RegisterUserServer), this linker attempts to resolve them by:
1. Finding unresolved edges with names matching Register*Server pattern
2. Looking up corresponding symbols created by the linker's file scan
3. Creating proper resolved edges

Why This Design
---------------
- Regex-based detection is fast and language-agnostic
- Service name matching enables cross-file RPC graph construction
- Separate linker keeps language analyzers focused on their language
- Unresolved edge protocol enables integration with analyzer-generated edges
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerRequirement,
    LinkerResult,
    register_linker,
)

PASS_ID = make_pass_id("grpc-linker")


@dataclass
class GrpcPattern:
    """Represents a detected gRPC pattern."""

    type: str  # 'service', 'servicer', 'stub', 'client', 'server', 'registration'
    service_name: str  # The gRPC service name
    line: int  # Line number in source
    file_path: str  # Source file path
    language: str  # Source language


@dataclass
class ProtoRpcDef:
    """An RPC method definition within a proto service."""

    service_name: str
    rpc_name: str
    package: str  # May be empty
    line: int
    file_path: str


@dataclass
class GrpcLinkResult:
    """Result of gRPC linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


# Regex patterns for gRPC detection

# Proto file patterns
PROTO_SERVICE_PATTERN = re.compile(
    r"^\s*service\s+(\w+)\s*\{",
    re.MULTILINE,
)
PROTO_RPC_PATTERN = re.compile(
    r"^\s*rpc\s+(\w+)\s*\(",
    re.MULTILINE,
)
PROTO_PACKAGE_PATTERN = re.compile(
    r"^\s*package\s+([\w.]+)\s*;",
    re.MULTILINE,
)

# Python gRPC patterns
PYTHON_SERVICER_PATTERN = re.compile(
    r"class\s+(\w+)Servicer\s*\(\s*\w+_pb2_grpc\.(\w+)Servicer\s*\)",
    re.MULTILINE,
)
PYTHON_STUB_PATTERN = re.compile(
    r"(\w+_pb2_grpc)\.(\w+)Stub\s*\(",
    re.MULTILINE,
)
PYTHON_REGISTRATION_PATTERN = re.compile(
    r"add_(\w+)Servicer_to_server\s*\(",
    re.MULTILINE,
)
PYTHON_GENERATED_SERVICER_PATTERN = re.compile(
    r"class\s+(\w+)Servicer\s*\(\s*object\s*\)\s*:",
    re.MULTILINE,
)
PYTHON_GENERATED_STUB_PATTERN = re.compile(
    r"class\s+(\w+)Stub\s*\(\s*object\s*\)\s*:",
    re.MULTILINE,
)

# Go gRPC patterns
GO_REGISTER_SERVER_PATTERN = re.compile(
    r"Register(\w+)Server\s*\(",
    re.MULTILINE,
)
GO_NEW_CLIENT_PATTERN = re.compile(
    r"New(\w+)Client\s*\(",
    re.MULTILINE,
)
GO_UNIMPLEMENTED_PATTERN = re.compile(
    r"Unimplemented(\w+)Server\b",
    re.MULTILINE,
)

# Java gRPC patterns
JAVA_IMPL_BASE_PATTERN = re.compile(
    r"extends\s+(\w+)Grpc\.(\w+)ImplBase\b",
    re.MULTILINE,
)
JAVA_STUB_PATTERN = re.compile(
    r"(\w+)Grpc\.new(Blocking)?Stub\s*\(",
    re.MULTILINE,
)

# TypeScript/JavaScript gRPC patterns
TS_CLIENT_PATTERN = re.compile(
    r"new\s+(\w+)Client\s*\(",
    re.MULTILINE,
)


def _find_grpc_files(root: Path) -> Iterator[Path]:
    """Find files that might contain gRPC patterns."""
    patterns = ["**/*.proto", "**/*.py", "**/*.go", "**/*.java", "**/*.ts", "**/*.js"]
    for path in find_files(root, patterns):
        yield path


def _scan_proto_file(
    file_path: Path, content: str,
) -> tuple[list[GrpcPattern], list[ProtoRpcDef]]:
    """Scan a .proto file for service and RPC definitions.

    Returns a tuple of (patterns, rpc_defs).  The *patterns* list contains
    ``GrpcPattern`` entries for each ``service`` block.  The *rpc_defs* list
    contains one ``ProtoRpcDef`` per ``rpc`` method, including the enclosing
    service name and the proto package.  These are used downstream to
    materialise ``kind="route"`` symbols whose path mirrors the real
    HTTP/2 wire path ``/<package>.<ServiceName>/<MethodName>``.
    """
    patterns: list[GrpcPattern] = []
    rpc_defs: list[ProtoRpcDef] = []

    # Extract package name (first match wins — proto allows at most one).
    pkg_match = PROTO_PACKAGE_PATTERN.search(content)
    package = pkg_match.group(1) if pkg_match else ""

    # Track which service block we are inside.
    current_service: str | None = None
    brace_depth = 0

    for i, line in enumerate(content.split("\n"), 1):
        svc_match = PROTO_SERVICE_PATTERN.match(line)
        if svc_match:
            current_service = svc_match.group(1)
            brace_depth = 0
            patterns.append(GrpcPattern(
                type="service",
                service_name=current_service,
                line=i,
                file_path=str(file_path),
                language="protobuf",
            ))

        # Rough brace tracking to know when we leave a service block.
        brace_depth += line.count("{") - line.count("}")
        if brace_depth <= 0 and current_service is not None:
            current_service = None

        if current_service is not None:
            rpc_match = PROTO_RPC_PATTERN.match(line)
            if rpc_match:
                rpc_defs.append(ProtoRpcDef(
                    service_name=current_service,
                    rpc_name=rpc_match.group(1),
                    package=package,
                    line=i,
                    file_path=str(file_path),
                ))

    return patterns, rpc_defs


def _scan_python_file(file_path: Path, content: str) -> list[GrpcPattern]:
    """Scan a Python file for gRPC patterns."""
    patterns: list[GrpcPattern] = []

    # Servicer implementations
    for match in PYTHON_SERVICER_PATTERN.finditer(content):
        service_name = match.group(2)
        line_num = content[:match.start()].count("\n") + 1
        patterns.append(GrpcPattern(
            type="servicer",
            service_name=service_name,
            line=line_num,
            file_path=str(file_path),
            language="python",
        ))

    # Stub usage
    for match in PYTHON_STUB_PATTERN.finditer(content):
        service_name = match.group(2)
        line_num = content[:match.start()].count("\n") + 1
        patterns.append(GrpcPattern(
            type="stub",
            service_name=service_name,
            line=line_num,
            file_path=str(file_path),
            language="python",
        ))

    # Service registration
    for match in PYTHON_REGISTRATION_PATTERN.finditer(content):
        service_name = match.group(1)
        line_num = content[:match.start()].count("\n") + 1
        patterns.append(GrpcPattern(
            type="registration",
            service_name=service_name,
            line=line_num,
            file_path=str(file_path),
            language="python",
        ))

    # Generated servicer classes
    for match in PYTHON_GENERATED_SERVICER_PATTERN.finditer(content):
        service_name = match.group(1)
        line_num = content[:match.start()].count("\n") + 1
        patterns.append(GrpcPattern(
            type="servicer",
            service_name=service_name,
            line=line_num,
            file_path=str(file_path),
            language="python",
        ))

    # Generated stub classes
    for match in PYTHON_GENERATED_STUB_PATTERN.finditer(content):
        service_name = match.group(1)
        line_num = content[:match.start()].count("\n") + 1
        patterns.append(GrpcPattern(
            type="stub",
            service_name=service_name,
            line=line_num,
            file_path=str(file_path),
            language="python",
        ))

    return patterns


def _scan_go_file(file_path: Path, content: str) -> list[GrpcPattern]:
    """Scan a Go file for gRPC patterns."""
    patterns: list[GrpcPattern] = []

    # Server registration
    for match in GO_REGISTER_SERVER_PATTERN.finditer(content):
        service_name = match.group(1)
        line_num = content[:match.start()].count("\n") + 1
        patterns.append(GrpcPattern(
            type="server",
            service_name=service_name,
            line=line_num,
            file_path=str(file_path),
            language="go",
        ))

    # Client creation
    for match in GO_NEW_CLIENT_PATTERN.finditer(content):
        service_name = match.group(1)
        line_num = content[:match.start()].count("\n") + 1
        patterns.append(GrpcPattern(
            type="client",
            service_name=service_name,
            line=line_num,
            file_path=str(file_path),
            language="go",
        ))

    # Unimplemented server embedding
    for match in GO_UNIMPLEMENTED_PATTERN.finditer(content):
        service_name = match.group(1)
        line_num = content[:match.start()].count("\n") + 1
        patterns.append(GrpcPattern(
            type="server",
            service_name=service_name,
            line=line_num,
            file_path=str(file_path),
            language="go",
        ))

    return patterns


def _scan_java_file(file_path: Path, content: str) -> list[GrpcPattern]:
    """Scan a Java file for gRPC patterns."""
    patterns: list[GrpcPattern] = []

    # Service implementation (extends XxxGrpc.XxxImplBase)
    for match in JAVA_IMPL_BASE_PATTERN.finditer(content):
        service_name = match.group(1)
        line_num = content[:match.start()].count("\n") + 1
        patterns.append(GrpcPattern(
            type="servicer",
            service_name=service_name,
            line=line_num,
            file_path=str(file_path),
            language="java",
        ))

    # Stub creation
    for match in JAVA_STUB_PATTERN.finditer(content):
        service_name = match.group(1)
        line_num = content[:match.start()].count("\n") + 1
        patterns.append(GrpcPattern(
            type="stub",
            service_name=service_name,
            line=line_num,
            file_path=str(file_path),
            language="java",
        ))

    return patterns


def _scan_ts_file(file_path: Path, content: str) -> list[GrpcPattern]:
    """Scan a TypeScript/JavaScript file for gRPC patterns."""
    patterns: list[GrpcPattern] = []

    # Client creation (new XxxClient)
    for match in TS_CLIENT_PATTERN.finditer(content):
        service_name = match.group(1)
        # Filter out common false positives
        if service_name.lower() in ("grpc", "http", "web", "socket"):
            continue
        line_num = content[:match.start()].count("\n") + 1
        patterns.append(GrpcPattern(
            type="client",
            service_name=service_name,
            line=line_num,
            file_path=str(file_path),
            language="typescript",
        ))

    return patterns


def _make_symbol_id(file_path: str, line: int, name: str, kind: str) -> str:
    """Generate unique symbol ID."""
    return f"grpc:{file_path}:{line}:{name}:{kind}"


def _make_route_stable_id(method: str, path: str) -> str:
    """Compute a collision-free stable_id for gRPC route symbols.

    Mirrors ``make_route_stable_id`` from ``analyze.base`` but avoids a
    cross-package import.  Uses sha256("route:{method}:{path}").
    """
    import hashlib
    digest = hashlib.sha256(f"route:{method}:{path}".encode()).hexdigest()[:16]
    return f"sha256:{digest}"


# Regex to find "type <Name> struct {" declarations.
_GO_STRUCT_DECL = re.compile(r"type\s+(\w+)\s+struct\s*\{")

# Regex to find UnimplementedXxxServer embedding inside a struct body.
# Allows optional package prefix (e.g., "pb.UnimplementedCacheServiceServer").
_UNIMPLEMENTED_EMBEDDING = re.compile(
    r"(?:\w+\.)?Unimplemented(\w+)Server\b"
)


def _find_struct_unimplemented_embeddings(content: str) -> dict[str, str]:
    """Find Go structs embedding UnimplementedXxxServer, returning struct→service.

    Uses brace-depth tracking to correctly handle structs with nested braces
    (e.g., ``done chan struct{}``, ``map[string]struct{ enabled bool }``).
    Supports package-prefixed embeddings (e.g., ``pb.UnimplementedXxxServer``).
    """
    result: dict[str, str] = {}
    for m in _GO_STRUCT_DECL.finditer(content):
        struct_name = m.group(1)
        body_start = m.end()  # position right after the opening {

        # Find the matching closing brace using depth tracking
        depth = 1
        pos = body_start
        while pos < len(content) and depth > 0:
            ch = content[pos]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            pos += 1

        if depth != 0:
            continue  # pragma: no cover - unbalanced braces

        struct_body = content[body_start:pos - 1]
        embed_match = _UNIMPLEMENTED_EMBEDDING.search(struct_body)
        if embed_match:
            service_name = embed_match.group(1)
            result[struct_name] = service_name
    return result


def _link_go_methods_to_rpc_routes(
    all_patterns: list[GrpcPattern],
    all_rpc_defs: list[ProtoRpcDef],
    existing_symbols: list[Symbol],
    route_symbols: list[Symbol],
    run: AnalysisRun,
) -> list[Edge]:
    """Create implements_rpc edges from Go methods to proto RPC routes.

    When a Go struct embeds ``UnimplementedXxxServer``, methods on that struct
    with names matching proto RPC definitions are implementations of those RPCs.
    This function creates ``implements_rpc`` edges connecting the Go method
    symbols to the proto RPC route symbols.

    Args:
        all_patterns: Detected gRPC patterns (includes Go "server" patterns).
        all_rpc_defs: Proto RPC definitions from .proto files.
        existing_symbols: Pre-existing symbols from language analyzers.
        route_symbols: Route symbols created by this linker.
        run: Analysis run for provenance.

    Returns:
        List of implements_rpc edges.
    """
    edges: list[Edge] = []

    # Find Go files with UnimplementedXxxServer and extract struct→service mapping.
    # We need to know which struct type embeds which service's Unimplemented server.
    go_server_files: set[str] = set()
    for pattern in all_patterns:
        if pattern.language == "go" and pattern.type == "server":
            go_server_files.add(pattern.file_path)

    if not go_server_files:
        return edges

    # Build struct_type → service_name mapping by re-scanning Go files.
    # Uses brace-depth tracking to handle structs with nested braces
    # (e.g., chan struct{}) and supports package-prefixed embeddings
    # (e.g., pb.UnimplementedXxxServer).
    struct_to_service: dict[str, str] = {}
    for file_path_str in go_server_files:
        try:
            content = Path(file_path_str).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:  # pragma: no cover
            continue
        struct_to_service.update(_find_struct_unimplemented_embeddings(content))

    if not struct_to_service:
        return edges

    # Build RPC name → route symbol ID mapping per service.
    # Key: (service_name, rpc_name) → route symbol ID
    rpc_route_lookup: dict[tuple[str, str], str] = {}
    for sym in route_symbols:
        if sym.kind == "route" and sym.meta:
            svc = sym.meta.get("rpc_service", "")
            method = sym.meta.get("rpc_method", "")
            if svc and method:
                rpc_route_lookup[(svc, method)] = sym.id

    if not rpc_route_lookup:
        return edges

    # Match existing Go method symbols to proto RPCs.
    # Go methods are named "StructType.MethodName".
    for sym in existing_symbols:
        if sym.kind != "method" or sym.language != "go":
            continue
        # Parse "StructType.MethodName" format
        parts = sym.name.rsplit(".", 1)
        if len(parts) != 2:
            continue
        struct_name, method_name = parts

        service_name = struct_to_service.get(struct_name)
        if not service_name:
            continue

        # Build the service name as it appears in proto (e.g., "UserService")
        proto_service = service_name + "Service"
        route_id = rpc_route_lookup.get((proto_service, method_name))
        if not route_id:
            # Try without "Service" suffix (some protos name services without it)
            route_id = rpc_route_lookup.get((service_name, method_name))
        if not route_id:
            continue

        edges.append(Edge.create(
            src=sym.id,
            dst=route_id,
            edge_type="implements_rpc",
            line=sym.span.start_line,
            confidence=0.90,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="grpc_go_server_method",
        ))

    return edges


def _normalize_service_name(name: str) -> str:
    """Normalize service name for matching (remove common suffixes)."""
    # Remove common suffixes for matching
    for suffix in ("Service", "Servicer", "Stub", "Client", "Server"):
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[:-len(suffix)]
    return name


def link_grpc(
    root: Path,
    existing_symbols: list[Symbol] | None = None,
) -> GrpcLinkResult:
    """Link gRPC clients to servers across files.

    Args:
        root: Repository root directory
        existing_symbols: Pre-existing symbols from language analyzers.
            When provided, enables linking Go methods on server structs
            to their corresponding proto RPC route symbols.

    Returns:
        GrpcLinkResult with symbols and edges.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    all_patterns: list[GrpcPattern] = []
    all_rpc_defs: list[ProtoRpcDef] = []

    # Scan all relevant files
    for file_path in _find_grpc_files(root):
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError):  # pragma: no cover
            continue

        if file_path.suffix == ".proto":
            patterns, rpc_defs = _scan_proto_file(file_path, content)
            all_patterns.extend(patterns)
            all_rpc_defs.extend(rpc_defs)
        elif file_path.suffix == ".py":
            all_patterns.extend(_scan_python_file(file_path, content))
        elif file_path.suffix == ".go":
            all_patterns.extend(_scan_go_file(file_path, content))
        elif file_path.suffix == ".java":
            all_patterns.extend(_scan_java_file(file_path, content))
        elif file_path.suffix in (".ts", ".js"):
            all_patterns.extend(_scan_ts_file(file_path, content))

    # Create symbols from patterns
    symbols: list[Symbol] = []
    stubs: list[GrpcPattern] = []
    servicers: list[GrpcPattern] = []

    for pattern in all_patterns:
        if pattern.type == "service":
            kind = "grpc_service"
        elif pattern.type in ("servicer", "registration"):
            kind = "grpc_servicer"
            servicers.append(pattern)
        elif pattern.type in ("stub", "client"):
            kind = "grpc_stub" if pattern.type == "stub" else "grpc_client"
            stubs.append(pattern)
        elif pattern.type == "server":
            kind = "grpc_server"
            servicers.append(pattern)
        else:  # pragma: no cover
            continue

        symbol_id = _make_symbol_id(
            pattern.file_path, pattern.line, pattern.service_name, kind
        )
        symbols.append(Symbol(
            id=symbol_id,
            name=pattern.service_name,
            kind=kind,
            language=pattern.language,
            path=pattern.file_path,
            span=Span(pattern.line, pattern.line, 0, 0),
            origin=PASS_ID,
            origin_run_id=run.execution_id,
        ))

    # Create edges linking clients/stubs to servicers/servers
    edges: list[Edge] = []

    # Build lookup by normalized service name
    servicer_by_name: dict[str, GrpcPattern] = {}
    for servicer in servicers:
        normalized = _normalize_service_name(servicer.service_name)
        servicer_by_name[normalized] = servicer

    # Match stubs to servicers
    for stub in stubs:
        normalized = _normalize_service_name(stub.service_name)
        if normalized in servicer_by_name:
            servicer = servicer_by_name[normalized]

            stub_id = _make_symbol_id(
                stub.file_path, stub.line, stub.service_name,
                "grpc_stub" if stub.type == "stub" else "grpc_client"
            )
            servicer_id = _make_symbol_id(
                servicer.file_path, servicer.line, servicer.service_name,
                "grpc_servicer" if servicer.type in ("servicer", "registration") else "grpc_server"
            )

            edges.append(Edge.create(
                src=stub_id,
                dst=servicer_id,
                edge_type="grpc_calls",
                line=stub.line,
                confidence=0.85,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="grpc_service_match",
            ))

    # Create route symbols for proto RPC definitions.
    # gRPC RPCs are accessed via HTTP/2 at /<package>.<Service>/<Method>.
    # Build a lookup for service symbols to create routes_to edges.
    service_sym_by_name: dict[str, str] = {}
    for sym in symbols:
        if sym.kind == "grpc_service":
            service_sym_by_name[sym.name] = sym.id

    # Bridge servicer/server symbols to their proto service definition.
    # grpc_calls edges terminate at grpc_server/grpc_servicer, but route
    # and implements_rpc edges originate from grpc_service symbols. Without
    # this bridge, the call chain is disconnected: the client-side graph
    # (stub → server) and the handler-side graph (route → service → method)
    # are separate components. This dispatches_to edge connects them.
    service_by_normalized: dict[str, str] = {}
    for svc_name, svc_id in service_sym_by_name.items():
        service_by_normalized[_normalize_service_name(svc_name)] = svc_id

    for sym in symbols:
        if sym.kind in ("grpc_server", "grpc_servicer"):
            normalized = _normalize_service_name(sym.name)
            svc_id = service_by_normalized.get(normalized)
            if svc_id and svc_id != sym.id:
                edges.append(Edge.create(
                    src=sym.id,
                    dst=svc_id,
                    edge_type="dispatches_to",
                    line=sym.span.start_line,
                    confidence=0.90,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="grpc_server_to_service",
                ))

    for rpc in all_rpc_defs:
        prefix = f"{rpc.package}.{rpc.service_name}" if rpc.package else rpc.service_name
        route_path = f"/{prefix}/{rpc.rpc_name}"
        route_name = f"RPC {route_path}"
        stable_id = _make_route_stable_id("RPC", route_path)

        route_id = _make_symbol_id(
            rpc.file_path, rpc.line, route_name, "route"
        )
        symbols.append(Symbol(
            id=route_id,
            name=route_name,
            kind="route",
            language="protobuf",
            path=rpc.file_path,
            span=Span(rpc.line, rpc.line, 0, 0),
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            stable_id=stable_id,
            meta={
                "route_path": route_path,
                "http_method": "RPC",
                "rpc_service": rpc.service_name,
                "rpc_method": rpc.rpc_name,
            },
        ))

        # Create routes_to edge from route to the service symbol.
        svc_id = service_sym_by_name.get(rpc.service_name)
        if svc_id:
            edges.append(Edge.create(
                src=route_id,
                dst=svc_id,
                edge_type="routes_to",
                line=rpc.line,
                confidence=0.90,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="grpc_rpc_definition",
            ))

    # Link Go implementation methods to proto RPC route symbols.
    # When a Go struct embeds UnimplementedXxxServer, its methods that
    # match proto RPC names are implementations of those RPCs.
    if existing_symbols:
        edges.extend(
            _link_go_methods_to_rpc_routes(
                all_patterns, all_rpc_defs, existing_symbols, symbols, run,
            )
        )

    run.duration_ms = int((time.time() - start_time) * 1000)

    return GrpcLinkResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )


# ---------------------------------------------------------------------------
# Linker Registry Integration
# ---------------------------------------------------------------------------


def _count_proto_files(ctx: LinkerContext) -> int:
    """Count .proto files in the repository."""
    count = 0
    for _ in find_files(ctx.repo_root, ["**/*.proto"]):
        count += 1
    return count


def _count_grpc_patterns_in_symbols(ctx: LinkerContext) -> int:
    """Count symbols that look like gRPC patterns from analyzers."""
    count = 0
    for sym in ctx.symbols:
        # Count Go registration patterns
        if sym.language == "go" and sym.name.startswith("Register") and "Server" in sym.name:
            count += 1
        # Count Python servicer classes
        if sym.language == "python" and sym.name.endswith("Servicer"):
            count += 1
        # Count Java ImplBase extensions
        if sym.language == "java" and "ImplBase" in (sym.meta or {}).get("extends", ""):
            count += 1
    return count


GRPC_REQUIREMENTS = [
    LinkerRequirement(
        name="proto_files",
        description=".proto service definition files",
        check=_count_proto_files,
    ),
    LinkerRequirement(
        name="grpc_symbols",
        description="gRPC patterns in analyzer symbols",
        check=_count_grpc_patterns_in_symbols,
    ),
]


def _resolve_unresolved_grpc_edges(
    ctx: LinkerContext,
    symbols: list[Symbol],
    run: AnalysisRun,
) -> list[Edge]:
    """Resolve unresolved edges pointing to gRPC registration functions.

    When the Go analyzer can't resolve a RegisterXxxServer call, it creates
    an unresolved edge. This function attempts to resolve these by matching
    the function name to symbols created by the linker's file scan.

    Args:
        ctx: LinkerContext with edges and symbols
        symbols: Symbols created by the linker
        run: AnalysisRun for attribution

    Returns:
        List of resolved edges (replacing unresolved ones).
    """
    resolved_edges: list[Edge] = []

    # Build lookup for linker-created symbols by name
    linker_symbols_by_name: dict[str, list[Symbol]] = {}
    for sym in symbols:
        name = sym.name
        if name not in linker_symbols_by_name:
            linker_symbols_by_name[name] = []
        linker_symbols_by_name[name].append(sym)

    # Also build from ctx.symbols for analyzer symbols
    for sym in ctx.symbols:
        name = sym.name
        if name not in linker_symbols_by_name:
            linker_symbols_by_name[name] = []
        linker_symbols_by_name[name].append(sym)

    # Find unresolved Go edges
    for edge in ctx.get_unresolved_edges(lang="go"):
        parsed = ctx.parse_unresolved_dst(edge.dst)
        if not parsed:  # pragma: no cover - defensive check
            continue

        callee_name = parsed["name"]

        # Check if this is a gRPC registration pattern
        if not (callee_name.startswith("Register") and "Server" in callee_name):
            continue

        # Try to find a matching symbol
        candidates = linker_symbols_by_name.get(callee_name, [])

        # Prefer symbols from the same package hint if available
        package_hint = parsed.get("package", "")
        best_candidate = None

        for candidate in candidates:
            # First match wins for now; could add package matching later
            if best_candidate is None:
                best_candidate = candidate
            # If package hint matches the candidate's path, prefer it
            if package_hint and package_hint in candidate.path:
                best_candidate = candidate
                break

        if best_candidate:
            resolved_edges.append(Edge.create(
                src=edge.src,
                dst=best_candidate.id,
                edge_type="calls",
                line=edge.line,
                confidence=0.75,  # Lower confidence for linker-resolved
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="grpc_unresolved_resolution",
            ))

    return resolved_edges


@register_linker(
    "grpc",
    priority=30,  # Run after analyzers but before dependency linker
    description="gRPC/Protobuf RPC pattern linking across languages",
    requirements=GRPC_REQUIREMENTS,
    activation=LinkerActivation(frameworks=["grpc", "protobuf"]),
)
def grpc_linker(ctx: LinkerContext) -> LinkerResult:
    """gRPC linker for registry-based dispatch.

    This wraps link_grpc() and adds unresolved edge resolution.
    """
    # Run the core linking logic
    result = link_grpc(ctx.repo_root, existing_symbols=ctx.symbols)

    # Resolve unresolved edges from analyzers
    resolved_edges = _resolve_unresolved_grpc_edges(
        ctx, result.symbols, result.run or AnalysisRun.create(PASS_ID, PASS_VERSION)
    )

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges + resolved_edges,
        run=result.run,
    )
