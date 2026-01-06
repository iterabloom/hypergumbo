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
1. Scan .proto files for service definitions
2. Scan implementation files for gRPC patterns
3. Create symbols for services, clients, and servers
4. Match clients to servers by service name
5. Create grpc_calls edges linking client stubs to servicers

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
from ..ir import AnalysisRun, Edge, Span, Symbol
from .registry import (
    LinkerContext,
    LinkerRequirement,
    LinkerResult,
    register_linker,
)

PASS_ID = "grpc-linker-v1"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class GrpcPattern:
    """Represents a detected gRPC pattern."""

    type: str  # 'service', 'servicer', 'stub', 'client', 'server', 'registration'
    service_name: str  # The gRPC service name
    line: int  # Line number in source
    file_path: str  # Source file path
    language: str  # Source language


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


def _scan_proto_file(file_path: Path, content: str) -> list[GrpcPattern]:
    """Scan a .proto file for service definitions."""
    patterns: list[GrpcPattern] = []

    for i, line in enumerate(content.split("\n"), 1):
        match = PROTO_SERVICE_PATTERN.match(line)
        if match:
            patterns.append(GrpcPattern(
                type="service",
                service_name=match.group(1),
                line=i,
                file_path=str(file_path),
                language="protobuf",
            ))

    return patterns


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


def _normalize_service_name(name: str) -> str:
    """Normalize service name for matching (remove common suffixes)."""
    # Remove common suffixes for matching
    for suffix in ("Service", "Servicer", "Stub", "Client", "Server"):
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[:-len(suffix)]
    return name


def link_grpc(root: Path) -> GrpcLinkResult:
    """Link gRPC clients to servers across files.

    Args:
        root: Repository root directory

    Returns:
        GrpcLinkResult with symbols and edges.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    all_patterns: list[GrpcPattern] = []

    # Scan all relevant files
    for file_path in _find_grpc_files(root):
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError):  # pragma: no cover
            continue

        if file_path.suffix == ".proto":
            all_patterns.extend(_scan_proto_file(file_path, content))
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
)
def grpc_linker(ctx: LinkerContext) -> LinkerResult:
    """gRPC linker for registry-based dispatch.

    This wraps link_grpc() and adds unresolved edge resolution.
    """
    # Run the core linking logic
    result = link_grpc(ctx.repo_root)

    # Resolve unresolved edges from analyzers
    resolved_edges = _resolve_unresolved_grpc_edges(
        ctx, result.symbols, result.run or AnalysisRun.create(PASS_ID, PASS_VERSION)
    )

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges + resolved_edges,
        run=result.run,
    )
