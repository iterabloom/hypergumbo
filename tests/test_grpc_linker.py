"""Tests for gRPC/Protobuf linker."""
from pathlib import Path


class TestGrpcLinkerBasics:
    """Tests for basic linker functionality."""

    def test_linker_returns_result(self, tmp_path: Path) -> None:
        """Linker returns a result object."""
        from hypergumbo.linkers.grpc import link_grpc

        result = link_grpc(tmp_path)

        assert result is not None
        assert result.run is not None
        assert result.edges == []
        assert result.symbols == []


class TestGrpcPythonPatterns:
    """Tests for detecting gRPC patterns in Python code."""

    def test_detects_python_servicer_implementation(self, tmp_path: Path) -> None:
        """Detects Python gRPC servicer implementations."""
        from hypergumbo.linkers.grpc import link_grpc

        python_file = tmp_path / "server.py"
        python_file.write_text('''
import grpc
from generated import user_pb2_grpc

class UserServiceServicer(user_pb2_grpc.UserServiceServicer):
    def GetUser(self, request, context):
        return user_pb2.User(name="test")

    def CreateUser(self, request, context):
        return user_pb2.User(name=request.name)
''')

        result = link_grpc(tmp_path)

        # Should create symbols for the servicer
        service_symbols = [s for s in result.symbols if s.kind == "grpc_servicer"]
        assert len(service_symbols) >= 1
        assert any("UserService" in s.name for s in service_symbols)

    def test_detects_python_stub_usage(self, tmp_path: Path) -> None:
        """Detects Python gRPC stub (client) usage."""
        from hypergumbo.linkers.grpc import link_grpc

        python_file = tmp_path / "client.py"
        python_file.write_text('''
import grpc
from generated import user_pb2_grpc

channel = grpc.insecure_channel('localhost:50051')
stub = user_pb2_grpc.UserServiceStub(channel)
response = stub.GetUser(user_pb2.GetUserRequest(id=1))
''')

        result = link_grpc(tmp_path)

        # Should create symbols for the stub
        client_symbols = [s for s in result.symbols if s.kind == "grpc_stub"]
        assert len(client_symbols) >= 1
        assert any("UserService" in s.name for s in client_symbols)

    def test_detects_python_server_registration(self, tmp_path: Path) -> None:
        """Detects Python gRPC server.add_generic_rpc_handlers or add_*_to_server."""
        from hypergumbo.linkers.grpc import link_grpc

        python_file = tmp_path / "main.py"
        python_file.write_text('''
import grpc
from concurrent import futures
from generated import user_pb2_grpc
from server import UserServiceServicer

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    user_pb2_grpc.add_UserServiceServicer_to_server(UserServiceServicer(), server)
    server.add_insecure_port('[::]:50051')
    server.start()
    server.wait_for_termination()
''')

        result = link_grpc(tmp_path)

        # Should detect service registration
        symbols = [s for s in result.symbols if "UserService" in s.name]
        assert len(symbols) >= 1


class TestGrpcGoPatterns:
    """Tests for detecting gRPC patterns in Go code."""

    def test_detects_go_server_implementation(self, tmp_path: Path) -> None:
        """Detects Go gRPC server implementations."""
        from hypergumbo.linkers.grpc import link_grpc

        go_file = tmp_path / "server.go"
        go_file.write_text('''
package main

import pb "example.com/user"

type userServer struct {
    pb.UnimplementedUserServiceServer
}

func (s *userServer) GetUser(ctx context.Context, req *pb.GetUserRequest) (*pb.User, error) {
    return &pb.User{Name: "test"}, nil
}
''')

        result = link_grpc(tmp_path)

        # Should create symbols for the server
        server_symbols = [s for s in result.symbols if s.kind == "grpc_server"]
        assert len(server_symbols) >= 1

    def test_detects_go_client_creation(self, tmp_path: Path) -> None:
        """Detects Go gRPC client creation."""
        from hypergumbo.linkers.grpc import link_grpc

        go_file = tmp_path / "client.go"
        go_file.write_text('''
package main

import pb "example.com/user"

func main() {
    conn, err := grpc.Dial("localhost:50051", grpc.WithInsecure())
    client := pb.NewUserServiceClient(conn)
    resp, err := client.GetUser(ctx, &pb.GetUserRequest{Id: 1})
}
''')

        result = link_grpc(tmp_path)

        # Should create symbols for the client
        client_symbols = [s for s in result.symbols if s.kind == "grpc_client"]
        assert len(client_symbols) >= 1

    def test_detects_go_server_registration(self, tmp_path: Path) -> None:
        """Detects Go gRPC RegisterXxxServer calls."""
        from hypergumbo.linkers.grpc import link_grpc

        go_file = tmp_path / "main.go"
        go_file.write_text('''
package main

import (
    "google.golang.org/grpc"
    pb "example.com/user"
)

func main() {
    s := grpc.NewServer()
    pb.RegisterUserServiceServer(s, &userServer{})
    s.Serve(lis)
}
''')

        result = link_grpc(tmp_path)

        # Should detect service registration
        symbols = [s for s in result.symbols if "UserService" in s.name]
        assert len(symbols) >= 1


class TestGrpcEdgeCreation:
    """Tests for edge creation linking clients to servers."""

    def test_creates_edges_between_client_and_server(self, tmp_path: Path) -> None:
        """Creates edges linking clients to servers by service name."""
        from hypergumbo.linkers.grpc import link_grpc

        # Server file
        server_file = tmp_path / "server.py"
        server_file.write_text('''
class UserServiceServicer(user_pb2_grpc.UserServiceServicer):
    pass
''')

        # Client file
        client_file = tmp_path / "client.py"
        client_file.write_text('''
stub = user_pb2_grpc.UserServiceStub(channel)
''')

        result = link_grpc(tmp_path)

        # Should create edges between client and server
        grpc_edges = [e for e in result.edges if e.edge_type == "grpc_calls"]
        assert len(grpc_edges) >= 1


class TestGrpcJavaPatterns:
    """Tests for detecting gRPC patterns in Java code."""

    def test_detects_java_service_implementation(self, tmp_path: Path) -> None:
        """Detects Java gRPC service implementations."""
        from hypergumbo.linkers.grpc import link_grpc

        java_file = tmp_path / "UserServiceImpl.java"
        java_file.write_text('''
package com.example;

public class UserServiceImpl extends UserServiceGrpc.UserServiceImplBase {
    @Override
    public void getUser(GetUserRequest request, StreamObserver<User> responseObserver) {
        responseObserver.onNext(User.newBuilder().setName("test").build());
        responseObserver.onCompleted();
    }
}
''')

        result = link_grpc(tmp_path)

        # Should create symbols for the service implementation
        service_symbols = [s for s in result.symbols if s.kind == "grpc_servicer"]
        assert len(service_symbols) >= 1

    def test_detects_java_stub_usage(self, tmp_path: Path) -> None:
        """Detects Java gRPC stub usage."""
        from hypergumbo.linkers.grpc import link_grpc

        java_file = tmp_path / "Client.java"
        java_file.write_text('''
package com.example;

public class Client {
    public void call() {
        ManagedChannel channel = ManagedChannelBuilder.forAddress("localhost", 50051).build();
        UserServiceGrpc.UserServiceBlockingStub stub = UserServiceGrpc.newBlockingStub(channel);
        User response = stub.getUser(GetUserRequest.newBuilder().setId(1).build());
    }
}
''')

        result = link_grpc(tmp_path)

        # Should create symbols for the stub
        client_symbols = [s for s in result.symbols if s.kind == "grpc_stub"]
        assert len(client_symbols) >= 1


class TestGrpcTypeScriptPatterns:
    """Tests for detecting gRPC patterns in TypeScript/JavaScript."""

    def test_detects_grpc_js_client(self, tmp_path: Path) -> None:
        """Detects gRPC-web or grpc-js client usage."""
        from hypergumbo.linkers.grpc import link_grpc

        ts_file = tmp_path / "client.ts"
        ts_file.write_text('''
import { UserServiceClient } from './generated/user_grpc_pb';
import { GetUserRequest } from './generated/user_pb';

const client = new UserServiceClient('http://localhost:50051');
const request = new GetUserRequest();
request.setId(1);
client.getUser(request, (err, response) => {
    console.log(response.getName());
});
''')

        result = link_grpc(tmp_path)

        # Should create symbols for the client
        client_symbols = [s for s in result.symbols if s.kind in ("grpc_client", "grpc_stub")]
        assert len(client_symbols) >= 1


class TestGrpcProtoFileDetection:
    """Tests for detecting Protocol Buffer files."""

    def test_detects_proto_service_definitions(self, tmp_path: Path) -> None:
        """Detects service definitions in .proto files."""
        from hypergumbo.linkers.grpc import link_grpc

        proto_file = tmp_path / "user.proto"
        proto_file.write_text('''
syntax = "proto3";

package example;

service UserService {
    rpc GetUser(GetUserRequest) returns (User);
    rpc CreateUser(CreateUserRequest) returns (User);
}

message User {
    string name = 1;
    int32 id = 2;
}

message GetUserRequest {
    int32 id = 1;
}
''')

        result = link_grpc(tmp_path)

        # Should create symbols for the proto service
        proto_symbols = [s for s in result.symbols if s.kind == "grpc_service"]
        assert len(proto_symbols) >= 1
        assert any("UserService" in s.name for s in proto_symbols)


class TestGrpcSymbolProperties:
    """Tests for symbol property correctness."""

    def test_symbols_have_correct_properties(self, tmp_path: Path) -> None:
        """Symbols have correct origin."""
        from hypergumbo.linkers.grpc import link_grpc

        proto_file = tmp_path / "test.proto"
        proto_file.write_text('''
service TestService {
    rpc DoSomething(Request) returns (Response);
}
''')

        result = link_grpc(tmp_path)

        for symbol in result.symbols:
            assert symbol.origin == "grpc-linker-v1"


class TestGrpcEdgeProperties:
    """Tests for edge property correctness."""

    def test_edges_have_confidence(self, tmp_path: Path) -> None:
        """Edges have confidence values."""
        from hypergumbo.linkers.grpc import link_grpc

        server_file = tmp_path / "server.py"
        server_file.write_text('class FooServiceServicer(foo_pb2_grpc.FooServiceServicer): pass')

        client_file = tmp_path / "client.py"
        client_file.write_text('stub = foo_pb2_grpc.FooServiceStub(channel)')

        result = link_grpc(tmp_path)

        for edge in result.edges:
            assert edge.confidence > 0
            assert edge.confidence <= 1.0


class TestGrpcEmptyProject:
    """Tests for handling projects without gRPC."""

    def test_handles_project_without_grpc(self, tmp_path: Path) -> None:
        """Handles projects without any gRPC code."""
        from hypergumbo.linkers.grpc import link_grpc

        python_file = tmp_path / "app.py"
        python_file.write_text('print("Hello, world!")')

        result = link_grpc(tmp_path)

        assert result.run is not None
        assert result.symbols == []
        assert result.edges == []


class TestGrpcGeneratedFileDetection:
    """Tests for detecting generated gRPC files."""

    def test_detects_python_pb2_grpc_files(self, tmp_path: Path) -> None:
        """Detects Python gRPC generated files."""
        from hypergumbo.linkers.grpc import link_grpc

        # Create a generated file
        pb2_grpc_file = tmp_path / "user_pb2_grpc.py"
        pb2_grpc_file.write_text('''
# Generated by the gRPC Python protocol compiler plugin
class UserServiceStub(object):
    def __init__(self, channel):
        self.GetUser = channel.unary_unary('/example.UserService/GetUser')

class UserServiceServicer(object):
    def GetUser(self, request, context):
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
''')

        result = link_grpc(tmp_path)

        # Should detect the generated service definitions
        symbols = [s for s in result.symbols if "UserService" in s.name]
        assert len(symbols) >= 1


class TestGrpcTypeScriptFalsePositives:
    """Tests for filtering TypeScript false positives."""

    def test_filters_common_false_positives(self, tmp_path: Path) -> None:
        """Filters out common false positive client names."""
        from hypergumbo.linkers.grpc import link_grpc

        ts_file = tmp_path / "client.ts"
        ts_file.write_text('''
// These should be filtered out as false positives
const http = new HttpClient('http://localhost');
const grpc = new GrpcClient('localhost:50051');
const web = new WebClient('ws://localhost');
const socket = new SocketClient('localhost');

// This should be detected as a real gRPC client
const user = new UserServiceClient('localhost:50051');
''')

        result = link_grpc(tmp_path)

        # Should only detect UserServiceClient, not the false positives
        client_symbols = [s for s in result.symbols if s.kind in ("grpc_client", "grpc_stub")]
        client_names = [s.name for s in client_symbols]
        assert "UserService" in client_names
        assert "Http" not in client_names
        assert "Grpc" not in client_names
        assert "Web" not in client_names
        assert "Socket" not in client_names


class TestGrpcNormalizeServiceName:
    """Tests for service name normalization."""

    def test_normalizes_names_without_suffix(self, tmp_path: Path) -> None:
        """Handles names without common suffixes."""
        from hypergumbo.linkers.grpc import _normalize_service_name

        # Names without standard suffixes should return unchanged
        assert _normalize_service_name("User") == "User"
        assert _normalize_service_name("API") == "API"
        assert _normalize_service_name("Handler") == "Handler"

    def test_normalizes_names_with_suffix(self, tmp_path: Path) -> None:
        """Removes common gRPC suffixes for matching."""
        from hypergumbo.linkers.grpc import _normalize_service_name

        # Names with standard suffixes should have them removed
        assert _normalize_service_name("UserService") == "User"
        assert _normalize_service_name("UserServicer") == "User"
        assert _normalize_service_name("UserStub") == "User"
        assert _normalize_service_name("UserClient") == "User"
        assert _normalize_service_name("UserServer") == "User"


class TestGrpcLinkerRequirements:
    """Tests for gRPC linker registry requirements."""

    def test_count_proto_files(self, tmp_path: Path) -> None:
        """Counts .proto files in the repository."""
        from hypergumbo.linkers.grpc import _count_proto_files
        from hypergumbo.linkers.registry import LinkerContext

        # Create some proto files
        (tmp_path / "user.proto").write_text("service UserService {}")
        (tmp_path / "order.proto").write_text("service OrderService {}")
        (tmp_path / "app.py").write_text("print('hello')")

        ctx = LinkerContext(repo_root=tmp_path)
        count = _count_proto_files(ctx)

        assert count == 2

    def test_count_grpc_patterns_go_registration(self, tmp_path: Path) -> None:
        """Counts Go gRPC registration patterns in symbols."""
        from hypergumbo.ir import Span, Symbol
        from hypergumbo.linkers.grpc import _count_grpc_patterns_in_symbols
        from hypergumbo.linkers.registry import LinkerContext

        go_sym = Symbol(
            id="go:test.go:1-10:RegisterUserServer:function",
            name="RegisterUserServer",
            kind="function",
            language="go",
            path="test.go",
            span=Span(1, 10, 0, 0),
            origin="test",
            origin_run_id="test",
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[go_sym])

        count = _count_grpc_patterns_in_symbols(ctx)
        assert count == 1

    def test_count_grpc_patterns_python_servicer(self, tmp_path: Path) -> None:
        """Counts Python gRPC servicer patterns in symbols."""
        from hypergumbo.ir import Span, Symbol
        from hypergumbo.linkers.grpc import _count_grpc_patterns_in_symbols
        from hypergumbo.linkers.registry import LinkerContext

        py_sym = Symbol(
            id="python:test.py:1-10:UserServiceServicer:class",
            name="UserServiceServicer",
            kind="class",
            language="python",
            path="test.py",
            span=Span(1, 10, 0, 0),
            origin="test",
            origin_run_id="test",
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[py_sym])

        count = _count_grpc_patterns_in_symbols(ctx)
        assert count == 1

    def test_count_grpc_patterns_java_impl_base(self, tmp_path: Path) -> None:
        """Counts Java gRPC ImplBase patterns in symbols."""
        from hypergumbo.ir import Span, Symbol
        from hypergumbo.linkers.grpc import _count_grpc_patterns_in_symbols
        from hypergumbo.linkers.registry import LinkerContext

        java_sym = Symbol(
            id="java:Test.java:1-10:UserServiceImpl:class",
            name="UserServiceImpl",
            kind="class",
            language="java",
            path="Test.java",
            span=Span(1, 10, 0, 0),
            origin="test",
            origin_run_id="test",
            meta={"extends": "UserServiceGrpc.UserServiceImplBase"},
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[java_sym])

        count = _count_grpc_patterns_in_symbols(ctx)
        assert count == 1


class TestGrpcLinkerRegistration:
    """Tests for gRPC linker registry integration."""

    def test_linker_is_registered(self) -> None:
        """gRPC linker is registered with the registry."""
        # Import the module to trigger registration
        import hypergumbo.linkers.grpc  # noqa: F401
        from hypergumbo.linkers.registry import get_linker

        linker = get_linker("grpc")
        assert linker is not None
        assert linker.name == "grpc"
        assert linker.priority == 30

    def test_grpc_linker_returns_result(self, tmp_path: Path) -> None:
        """grpc_linker function returns LinkerResult."""
        from hypergumbo.linkers.grpc import grpc_linker
        from hypergumbo.linkers.registry import LinkerContext

        ctx = LinkerContext(repo_root=tmp_path)
        result = grpc_linker(ctx)

        assert result is not None
        assert hasattr(result, "symbols")
        assert hasattr(result, "edges")


class TestGrpcUnresolvedEdgeResolution:
    """Tests for resolving unresolved gRPC edges."""

    def test_resolves_unresolved_register_server_edge(self, tmp_path: Path) -> None:
        """Resolves unresolved edges to RegisterXxxServer functions."""
        from hypergumbo.ir import AnalysisRun, Edge, Span, Symbol
        from hypergumbo.linkers.grpc import _resolve_unresolved_grpc_edges
        from hypergumbo.linkers.registry import LinkerContext

        # Create an unresolved edge from Go analyzer
        unresolved_edge = Edge.create(
            src="go:main.go:10-20:main:function",
            dst="go:github.com/pkg/pb:0-0:RegisterUserServer:unresolved",
            edge_type="calls",
            line=15,
            confidence=0.5,
            origin="go-analyzer",
            origin_run_id="test",
            evidence_type="unresolved_method_call",
        )

        # Create a symbol that can resolve the edge
        register_sym = Symbol(
            id="grpc:user_grpc.pb.go:100:UserService:grpc_server",
            name="RegisterUserServer",
            kind="grpc_server",
            language="go",
            path=str(tmp_path / "pkg/pb/user_grpc.pb.go"),
            span=Span(100, 110, 0, 0),
            origin="grpc-linker-v1",
            origin_run_id="test",
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            edges=[unresolved_edge],
            symbols=[],
        )
        run = AnalysisRun.create("grpc-linker-v1", "test")

        resolved = _resolve_unresolved_grpc_edges(ctx, [register_sym], run)

        assert len(resolved) == 1
        assert resolved[0].src == unresolved_edge.src
        assert resolved[0].dst == register_sym.id
        assert resolved[0].edge_type == "calls"

    def test_ignores_non_grpc_unresolved_edges(self, tmp_path: Path) -> None:
        """Ignores unresolved edges that don't match gRPC patterns."""
        from hypergumbo.ir import AnalysisRun, Edge
        from hypergumbo.linkers.grpc import _resolve_unresolved_grpc_edges
        from hypergumbo.linkers.registry import LinkerContext

        # Create an unresolved edge that's NOT a gRPC pattern
        unresolved_edge = Edge.create(
            src="go:main.go:10-20:main:function",
            dst="go:github.com/pkg:0-0:SomeOtherFunc:unresolved",
            edge_type="calls",
            line=15,
            confidence=0.5,
            origin="go-analyzer",
            origin_run_id="test",
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            edges=[unresolved_edge],
            symbols=[],
        )
        run = AnalysisRun.create("grpc-linker-v1", "test")

        resolved = _resolve_unresolved_grpc_edges(ctx, [], run)

        # Should not resolve non-gRPC patterns
        assert len(resolved) == 0

    def test_prefers_package_matching_candidate(self, tmp_path: Path) -> None:
        """Prefers symbol whose path matches the package hint."""
        from hypergumbo.ir import AnalysisRun, Edge, Span, Symbol
        from hypergumbo.linkers.grpc import _resolve_unresolved_grpc_edges
        from hypergumbo.linkers.registry import LinkerContext

        # The package hint contains 'checkout/pb' which should match path
        unresolved_edge = Edge.create(
            src="go:main.go:10-20:main:function",
            dst="go:checkout/pb:0-0:RegisterCheckoutServer:unresolved",
            edge_type="calls",
            line=15,
            confidence=0.5,
            origin="go-analyzer",
            origin_run_id="test",
        )

        # Two candidates with same name but different paths
        wrong_sym = Symbol(
            id="grpc:frontend/pb/grpc.pb.go:100:Checkout:grpc_server",
            name="RegisterCheckoutServer",
            kind="grpc_server",
            language="go",
            path=str(tmp_path / "frontend/pb/grpc.pb.go"),
            span=Span(100, 110, 0, 0),
            origin="grpc-linker-v1",
            origin_run_id="test",
        )
        correct_sym = Symbol(
            id="grpc:checkout/pb/grpc.pb.go:100:Checkout:grpc_server",
            name="RegisterCheckoutServer",
            kind="grpc_server",
            language="go",
            path=str(tmp_path / "checkout/pb/grpc.pb.go"),
            span=Span(100, 110, 0, 0),
            origin="grpc-linker-v1",
            origin_run_id="test",
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            edges=[unresolved_edge],
            symbols=[],
        )
        run = AnalysisRun.create("grpc-linker-v1", "test")

        # Pass wrong_sym first to ensure we're not just picking first match
        resolved = _resolve_unresolved_grpc_edges(ctx, [wrong_sym, correct_sym], run)

        assert len(resolved) == 1
        # Should prefer the one matching package hint (checkout/pb)
        assert resolved[0].dst == correct_sym.id
