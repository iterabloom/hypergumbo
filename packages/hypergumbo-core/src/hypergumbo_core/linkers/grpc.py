# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: gRPC/Protobuf for detecting RPC communication patterns.

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
- XxxServer interface implementation (CSI-style, no Unimplemented embedding)

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
4. Create route-marker symbols for each proto RPC method, using the
   real HTTP/2 wire path /<package>.<ServiceName>/<MethodName>. These
   carry ``kind="function"`` + ``meta['framework_role']='route'`` (the
   ADR-0027 Phase-3 route→function fold); there is no ``route`` kind.
5. Match clients to servers by service name
6. Create canonical 'calls' edges with meta['protocol']='grpc' linking
   client stubs to servicers (post WI-vumum-juvil; pre-fold was grpc_calls)
7. Create ``dispatches_to`` edges (``meta['dispatch_kind']='route'``) from
   RPC route symbols to service symbols

Unresolved Edge Resolution
--------------------------
When the Go analyzer creates unresolved edges to gRPC registration functions
(e.g., RegisterUserServer), this linker attempts to resolve them by:
1. Finding unresolved edges with names matching Register*Server pattern
2. Looking up corresponding symbols created by the linker's file scan
3. Creating replacement ``calls`` edges to the matched servicer. These stay
   ``is_resolved=False`` — the linker supplies a destination, not a proof
   that the callee was resolved by name binding.

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

from ..discovery import find_files, find_non_test_files
from ..analyze.base import make_route_symbol
from ..ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from ._transitive_bases import (
    build_inheritance_index,
    collect_transitive_base_names,
)
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerRequirement,
    LinkerResult,
    register_linker,
)
from ._text_filters import read_masked_source

PASS_ID = make_pass_id("grpc-linker")


@dataclass
class GrpcPattern:
    """Represents a detected gRPC pattern."""

    type: str  # 'service', 'servicer', 'stub', 'client', 'server', 'registration'
    service_name: str  # The gRPC service name
    line: int  # Line number in source
    file_path: str  # Source file path
    language: str  # Source language
    # Proto package (only set on type='service' patterns from .proto files).
    # Used by the routes_to lookup to disambiguate cross-package short-name
    # collisions per WI-patiz (INV-zuhub item 1).
    package: str = ""


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

# Go gRPC/ttrpc patterns
# Matches both RegisterXxxServer (standard gRPC) and RegisterXxxService (ttrpc).
# ttrpc is a lightweight gRPC alternative used by containerd, kata-containers, etc.
GO_REGISTER_SERVER_PATTERN = re.compile(
    r"Register(\w+)(?:Server|Service)\s*\(",
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
    for path in find_non_test_files(root, patterns):
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
                language="proto",
                package=package,
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
    existing_edges: list[Edge] | None = None,
) -> list[Edge]:
    """Create gRPC RPC-implementation edges from Go methods to proto RPC routes.

    When a Go struct embeds ``UnimplementedXxxServer``, methods on that struct
    with names matching proto RPC definitions are implementations of those RPCs.
    This function creates canonical ``implements`` + ``meta['protocol']='grpc'``
    edges (folded from the former ``implements_rpc`` edge type per audit-findings
    0016) connecting the Go method symbols to the proto RPC route symbols. The
    folded form keeps its call-like taint / io / ranking / slice coupling via
    ``edge_types.is_grpc_rpc_implementation``.

    Args:
        all_patterns: Detected gRPC patterns (includes Go "server" patterns).
        all_rpc_defs: Proto RPC definitions from .proto files.
        existing_symbols: Pre-existing symbols from language analyzers.
        route_symbols: Route symbols created by this linker.
        run: Analysis run for provenance.

    Returns:
        List of ``implements`` + ``protocol=grpc`` edges.
    """
    edges: list[Edge] = []

    # Find Go files with UnimplementedXxxServer and extract struct→service mapping.
    # We need to know which struct type embeds which service's Unimplemented server.
    go_server_files: set[str] = set()
    for pattern in all_patterns:
        if pattern.language == "go" and pattern.type == "server":
            go_server_files.add(pattern.file_path)

    # Build (file, struct_type) → service_name mapping by re-scanning Go files.
    # WI-kunoz / BUG-05: keyed by (file_path, struct_name) rather than the
    # short struct name alone, because multiple Go files commonly declare a
    # struct with the same conventional name (e.g. eight containerd plugin
    # packages each define ``type service struct { ... UnimplementedXxxServer
    # ... }``). The previous short-name dict overwrote on registration order
    # so all eight collapsed onto whichever ``go_server_files`` set iteration
    # processed last — every ``service.Create`` edge landed on the wrong
    # service's RPC family. File-scoped keys decouple them.
    #
    # Uses brace-depth tracking to handle structs with nested braces
    # (e.g., chan struct{}) and supports package-prefixed embeddings
    # (e.g., pb.UnimplementedXxxServer).
    struct_to_service: dict[tuple[str, str], str] = {}
    for file_path_str in go_server_files:
        try:
            content = Path(file_path_str).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:  # pragma: no cover
            continue
        for struct_name, service_name in _find_struct_unimplemented_embeddings(
            content,
        ).items():
            struct_to_service[(file_path_str, struct_name)] = service_name

    # Also check Go struct symbols for interface implementations that don't
    # use UnimplementedXxxServer embedding. Covers:
    # - ttrpc: interfaces like AgentServiceService, HealthService
    # - CSI: interfaces like IdentityServer, ControllerServer, NodeServer
    # The Go analyzer records implemented interfaces in base_classes metadata.
    #
    # WI-pogus: walk the transitive base-class chain so a struct that embeds
    # an in-tree intermediate (e.g. `BaseFooImpl` extending
    # `UnimplementedFooServer`) is detected the same as a direct embedder.
    # Go uses struct embedding rather than class inheritance, but the Go
    # analyzer encodes embedded structs / implemented interfaces in the
    # same `meta.base_classes` metadata, so the WI-halat helper applies.
    inheritance_index = build_inheritance_index(existing_edges or [])
    symbol_by_id = {s.id: s for s in existing_symbols}
    for sym in existing_symbols:
        if sym.kind != "struct" or sym.language != "go":
            continue
        struct_key = (sym.path, sym.name)
        if struct_key in struct_to_service:
            continue  # already mapped via Unimplemented embedding
        chain = collect_transitive_base_names(sym, symbol_by_id, inheritance_index)
        for base in chain:
            if base.startswith("Unimplemented"):
                continue
            # Match ttrpc patterns: XxxService or XxxServiceService
            if base.endswith("Service"):
                if base.endswith("ServiceService"):
                    service_name = base[:-len("Service")]
                else:
                    service_name = base[:-len("Service")]
                struct_to_service[struct_key] = service_name
            # Match CSI / external library patterns: XxxServer
            # e.g., IdentityServer → Identity, ControllerServer → Controller
            elif base.endswith("Server"):
                service_name = base[:-len("Server")]
                struct_to_service[struct_key] = service_name

    if not struct_to_service:
        return edges

    # Build RPC name → route symbol ID mapping per service.
    # Key: (service_name, rpc_name) → route symbol ID
    rpc_route_lookup: dict[tuple[str, str], str] = {}
    for sym in route_symbols:
        if (sym.meta or {}).get("framework_role") == "route" and sym.meta:
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

        # WI-kunoz / BUG-05: file-scoped (path, struct_name) key so a method
        # on ``service`` in ``plugins/containers/service.go`` doesn't pick
        # up the Leases mapping from ``plugins/leases/service.go``. The
        # short-name dict that this replaced collapsed all eight containerd
        # plugin services onto whichever file ``go_server_files`` iteration
        # processed last.
        service_name = struct_to_service.get((sym.path, struct_name))
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

        # ADR-0028 Phase 3 / audit-findings 0014: framework-dispatch leak.
        # audit-findings 0016 FOLD: implements_rpc -> implements +
        # meta['protocol']='grpc'. A Go method on a struct embedding
        # UnimplementedXxxServer literally IS a Go interface implementation
        # (impl->contract). Its call-like consumer coupling (taint / io /
        # ranking / slice) is preserved via edge_types.is_grpc_rpc_implementation
        # so gRPC reachability is not silently demoted (finding 3).
        edges.append(Edge.create(
            src=sym.id,
            dst=route_id,
            edge_type="implements",
            line=sym.span.start_line if sym.span else 0,
            confidence=0.90,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="ast_call_direct",
            meta={"framework_dispatch": "grpc_go_server", "protocol": "grpc"},
            derived_from=[sym.id, route_id],
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
    existing_edges: list[Edge] | None = None,
) -> GrpcLinkResult:
    """Link gRPC clients to servers across files.

    Args:
        root: Repository root directory
        existing_symbols: Pre-existing symbols from language analyzers.
            When provided, enables linking Go methods on server structs
            to their corresponding proto RPC route symbols.
        existing_edges: Pre-existing edges (extends/implements) used for
            transitive base-name walks (WI-pogus). When omitted, only
            direct embedding via ``meta.base_classes`` is consulted.

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
            content = read_masked_source(file_path, encoding="utf-8", errors="replace")
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

    # ADR-0027 Phase 3 / audit-findings 0013 (WI-nitil): framework-role
    # leak. Each gRPC kind folds to a canonical Cluster A construct
    # (`interface` for the proto service declaration, `class` for
    # server-side implementation classes, `function` for client-side
    # call sites) plus meta["framework_role"]=<value>. The
    # framework-role string remains the disambiguator in the Symbol ID
    # so cross-PR identity is stable.
    for pattern in all_patterns:
        if pattern.type == "service":
            framework_role = "grpc_service"
            canonical_kind = "interface"
        elif pattern.type in ("servicer", "registration"):
            framework_role = "grpc_servicer"
            canonical_kind = "class"
            servicers.append(pattern)
        elif pattern.type in ("stub", "client"):
            framework_role = "grpc_stub" if pattern.type == "stub" else "grpc_client"
            canonical_kind = "function"
            stubs.append(pattern)
        elif pattern.type == "server":
            framework_role = "grpc_server"
            canonical_kind = "class"
            servicers.append(pattern)
        else:  # pragma: no cover
            continue

        symbol_id = _make_symbol_id(
            pattern.file_path, pattern.line, pattern.service_name, framework_role
        )
        sym_meta: dict[str, object] = {"framework_role": framework_role}
        # WI-patiz: grpc_service symbols carry proto_package so the
        # routes_to lookup can disambiguate cross-package short-name
        # collisions at precision when each RPC's package picks out a
        # unique service candidate. Non-proto patterns (Go/Python/Java
        # impl-side) carry no package — their disambiguation must rely
        # on the bridge's deterministic-by-id fallback.
        if framework_role == "grpc_service" and pattern.package:
            sym_meta["proto_package"] = pattern.package
        symbols.append(Symbol(
            id=symbol_id,
            name=pattern.service_name,
            kind=canonical_kind,
            language=pattern.language,
            path=pattern.file_path,
            span=Span(pattern.line, pattern.line, 0, 0),
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            meta=sym_meta,
        ))

    # Create edges linking clients/stubs to servicers/servers
    edges: list[Edge] = []

    # Build lookup by normalized service name
    servicer_by_name: dict[str, GrpcPattern] = {}
    for servicer in servicers:
        normalized = _normalize_service_name(servicer.service_name)
        servicer_by_name[normalized] = servicer

    # WI-ropoz: track which stubs got an impl-side match. Stubs/clients
    # that match no servicer fall through to the proto-service fallback
    # below — the cross-language case where the impl lives outside the
    # analyzed tree (workadventure-style: TS client + Go server in
    # separate repos) needs the client at least bound to the contract.
    stubs_with_servicer: set[int] = set()

    # Build the proto-side ``service`` symbol lookup once so the
    # no-servicer fallback below can reach it. Indexed by normalized
    # short name; cross-package collisions surface as multi-value.
    proto_service_by_name: dict[str, list[Symbol]] = {}
    for sym in symbols:
        if (sym.meta or {}).get("framework_role") == "grpc_service":
            proto_service_by_name.setdefault(
                _normalize_service_name(sym.name), []
            ).append(sym)

    # Match stubs to servicers
    for stub in stubs:
        normalized = _normalize_service_name(stub.service_name)
        if normalized in servicer_by_name:
            stubs_with_servicer.add(id(stub))
            servicer = servicer_by_name[normalized]

            stub_id = _make_symbol_id(
                stub.file_path, stub.line, stub.service_name,
                "grpc_stub" if stub.type == "stub" else "grpc_client"
            )
            servicer_id = _make_symbol_id(
                servicer.file_path, servicer.line, servicer.service_name,
                "grpc_servicer" if servicer.type in ("servicer", "registration") else "grpc_server"
            )

            # ADR-0023 §6 Phase 3 (WI-vumum-juvil): gRPC is a wire
            # protocol, not a relationship. The fold target is
            # canonical 'calls' + meta['protocol']='grpc'.
            #
            # ADR-0028 Phase 3 / audit-findings 0014: framework-dispatch leak.
            # Fold evidence_type to ast_call_direct + meta key.
            edges.append(Edge.create(
                src=stub_id,
                dst=servicer_id,
                edge_type="calls",
                line=stub.line,
                confidence=0.85,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="ast_call_direct",
                meta={
                    "protocol": "grpc",
                    "framework_dispatch": "grpc_service_match",
                },
                derived_from=[stub_id, servicer_id],
            ))

    # WI-ropoz: fallback — stubs/clients without an in-tree servicer
    # bind directly to the proto service Symbol. The impl lives outside
    # the analyzed tree (e.g., workadventure's TS clients call a Go
    # server in a separate repo; without this fallback the entire
    # client side of the graph is disconnected from the proto contract).
    # Edge shape: canonical ``calls`` + meta[protocol]=grpc + the
    # ADR-0028 ``is_resolved=False`` flag because the actual receiver
    # is unknown.
    for stub in stubs:
        if id(stub) in stubs_with_servicer:
            continue
        normalized = _normalize_service_name(stub.service_name)
        proto_candidates = proto_service_by_name.get(normalized)
        if not proto_candidates:
            continue

        stub_id = _make_symbol_id(
            stub.file_path, stub.line, stub.service_name,
            "grpc_stub" if stub.type == "stub" else "grpc_client",
        )

        # Cross-package short-name collisions (two .proto files declare
        # ``service Foo`` in different packages) drop to disambiguation
        # fallback per INV-zuhub. With one candidate, this is precision.
        is_fallback = len(proto_candidates) > 1
        target = (
            proto_candidates[0]
            if len(proto_candidates) == 1
            else min(proto_candidates, key=lambda s: s.id)
        )

        edge_meta: dict[str, object] = {
            "protocol": "grpc",
            "framework_dispatch": "grpc_service_match",
        }
        if is_fallback:
            edge_meta["disambiguation_fallback"] = True

        edges.append(Edge.create(
            src=stub_id,
            dst=target.id,
            edge_type="calls",
            line=stub.line,
            confidence=0.5 if is_fallback else 0.6,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="ast_call_direct",
            is_resolved=False,
            meta=edge_meta,
            derived_from=[stub_id, target.id],
        ))

    # Create route symbols for proto RPC definitions.
    # gRPC RPCs are accessed via HTTP/2 at /<package>.<Service>/<Method>.
    # Build a lookup for service symbols to create routes_to edges.
    # WI-patiz (INV-zuhub item 1): multi-value index. Two .proto files
    # in different packages can each declare ``service Foo`` — pre-fix
    # single-value dict overwrote silently and pointed every cross-
    # package edge at one surviving service symbol.
    service_sym_by_name: dict[str, list[Symbol]] = {}
    for sym in symbols:
        if (sym.meta or {}).get("framework_role") == "grpc_service":
            service_sym_by_name.setdefault(sym.name, []).append(sym)

    # Bridge servicer/server symbols to their proto service definition.
    # The client-side 'calls' edges (with meta['protocol']='grpc')
    # terminate at grpc_server/grpc_servicer, but route and
    # implements_rpc edges originate from grpc_service symbols. Without
    # this bridge, the call chain is disconnected: the client-side graph
    # (stub → server) and the handler-side graph (route → service → method)
    # are separate components. This dispatches_to edge connects them.
    # Multi-value index keyed by normalized service short name.
    service_by_normalized: dict[str, list[Symbol]] = {}
    for svc_list in service_sym_by_name.values():
        for svc in svc_list:
            service_by_normalized.setdefault(
                _normalize_service_name(svc.name), [],
            ).append(svc)

    for sym in symbols:
        if (sym.meta or {}).get("framework_role") in ("grpc_server", "grpc_servicer"):
            normalized = _normalize_service_name(sym.name)
            candidates = [
                c for c in service_by_normalized.get(normalized, [])
                if c.id != sym.id
            ]
            if not candidates:
                continue
            # Impl-side (Go struct embedding UnimplementedXxxServer,
            # Python servicer class, etc.) carries no proto package
            # metadata, so when multiple proto services share the
            # normalized short name the impl alone cannot disambiguate.
            # Deterministic-by-id pick + fallback flag per INV-zuhub.
            is_fallback = len(candidates) > 1
            target_svc = (
                candidates[0] if not is_fallback
                else min(candidates, key=lambda c: c.id)
            )
            confidence = 0.5 if is_fallback else 0.90
            bridge_meta = (
                {"framework_dispatch": "grpc_server_to_service",
                 "disambiguation_fallback": True}
                if is_fallback
                else {"framework_dispatch": "grpc_server_to_service"}
            )
            # ADR-0028 Phase 3 / audit-findings 0014: framework-dispatch leak.
            edges.append(Edge.create(
                src=sym.id,
                dst=target_svc.id,
                edge_type="dispatches_to",
                line=sym.span.start_line if sym.span else 0,
                confidence=confidence,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="ast_call_direct",
                meta=bridge_meta,
                derived_from=[sym.id, target_svc.id],
            ))

    for rpc in all_rpc_defs:
        prefix = f"{rpc.package}.{rpc.service_name}" if rpc.package else rpc.service_name
        route_path = f"/{prefix}/{rpc.rpc_name}"

        # WI-zugob: minted through the shared chokepoint. Two id-format fixes
        # ride along with the migration: the kind-slot was the literal ``route``
        # fossil (unregistered; Symbol.kind was already ``function``), and the
        # lang-slot was the PROTOCOL ``grpc`` rather than a language — the
        # protocol now lives in the typed ``protocol_origin`` field, and the
        # lang-slot names the host file's language like every other Class-B
        # linker id. ``discovery_language`` stays None deliberately: a gRPC
        # route is fabricated from a service definition, not discovered inside
        # a host file, which is why the factory takes it explicitly.
        route_sym = make_route_symbol(
            language="proto",
            path=rpc.file_path,
            span=Span(rpc.line, rpc.line, 0, 0),
            method="RPC",
            route_path=route_path,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            protocol_origin="grpc",
            extra_meta={
                "rpc_service": rpc.service_name,
                "rpc_method": rpc.rpc_name,
            },
        )
        route_id = route_sym.id
        symbols.append(route_sym)

        # Create routes_to edge from route to the service symbol.
        # WI-patiz: when multiple .proto files declare the same service
        # short name across packages, prefer the candidate whose
        # ``meta["proto_package"]`` matches this RPC's package. A
        # unique package match is precision; a missing or ambiguous
        # match drops to the INV-zuhub fallback shape.
        svc_candidates = service_sym_by_name.get(rpc.service_name, [])
        if svc_candidates:
            same_pkg = [
                c for c in svc_candidates
                if (c.meta or {}).get("proto_package") == rpc.package
            ]
            if len(same_pkg) == 1:
                target_svc = same_pkg[0]
                route_is_fallback = False
            elif len(svc_candidates) == 1:
                target_svc = svc_candidates[0]
                route_is_fallback = False
            else:
                target_svc = min(svc_candidates, key=lambda c: c.id)
                route_is_fallback = True
            # ADR-0023 §6 Phase 3 / audit-findings 0001 (WI-vasik-jofiv):
            # gRPC RPC definition routes a route → service; "route"
            # is the dispatch mechanism. Canonical 'dispatches_to'
            # + meta['dispatch_kind']='route'.
            #
            # ADR-0028 Phase 3 / audit-findings 0014: framework-dispatch leak.
            # Fold evidence_type to ast_call_direct + meta key.
            route_confidence = 0.5 if route_is_fallback else 0.90
            route_meta = (
                {
                    "dispatch_kind": "route",
                    "framework_dispatch": "grpc_rpc_definition",
                    "disambiguation_fallback": True,
                }
                if route_is_fallback
                else {
                    "dispatch_kind": "route",
                    "framework_dispatch": "grpc_rpc_definition",
                }
            )
            edges.append(Edge.create(
                src=route_id,
                dst=target_svc.id,
                edge_type="dispatches_to",
                line=rpc.line,
                confidence=route_confidence,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="ast_call_direct",
                meta=route_meta,
                derived_from=[route_id, target_svc.id],
            ))

    # Link Go implementation methods to proto RPC route symbols.
    # When a Go struct embeds UnimplementedXxxServer, its methods that
    # match proto RPC names are implementations of those RPCs.
    if existing_symbols:
        edges.extend(
            _link_go_methods_to_rpc_routes(
                all_patterns, all_rpc_defs, existing_symbols, symbols, run,
                existing_edges=existing_edges,
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
                evidence_type="grpc_stub_resolution",
                is_resolved=False,
                derived_from=[edge.src, best_candidate.id],
            ))

    return resolved_edges


@register_linker(
    "grpc-linker",
    priority=30,  # Run after analyzers but before dependency linker
    description="gRPC/Protobuf RPC pattern linking across languages",
    requirements=GRPC_REQUIREMENTS,
    activation=LinkerActivation(frameworks=["grpc", "protobuf"]),
    # CNF: gRPC has first-class clients in Go, Python, Java, JS/TS, C++, Rust,
    # Ruby, C#, Kotlin, Swift, Dart. Proto schema itself goes through the
    # proto analyzer.
    depends_on=[["go", "python", "java", "javascript", "cpp", "rust", "ruby", "csharp", "kotlin", "swift", "dart", "proto"]],
)
def grpc_linker(ctx: LinkerContext) -> LinkerResult:
    """gRPC linker for registry-based dispatch.

    This wraps link_grpc() and adds unresolved edge resolution.
    """
    # Run the core linking logic
    result = link_grpc(
        ctx.repo_root,
        existing_symbols=ctx.symbols,
        existing_edges=ctx.edges,
    )

    # Resolve unresolved edges from analyzers
    resolved_edges = _resolve_unresolved_grpc_edges(
        ctx, result.symbols, result.run or AnalysisRun.create(PASS_ID, PASS_VERSION)
    )

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges + resolved_edges,
        run=result.run,
    )
