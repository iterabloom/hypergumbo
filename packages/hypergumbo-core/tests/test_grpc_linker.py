# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for gRPC/Protobuf linker."""
from pathlib import Path


class TestGrpcLinkerBasics:
    """Tests for basic linker functionality."""

    def test_linker_returns_result(self, tmp_path: Path) -> None:
        """Linker returns a result object."""
        from hypergumbo_core.linkers.grpc import link_grpc

        result = link_grpc(tmp_path)

        assert result is not None
        assert result.run is not None
        assert result.edges == []
        assert result.symbols == []


class TestGrpcPythonPatterns:
    """Tests for detecting gRPC patterns in Python code."""

    def test_detects_python_servicer_implementation(self, tmp_path: Path) -> None:
        """Detects Python gRPC servicer implementations."""
        from hypergumbo_core.linkers.grpc import link_grpc

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
        service_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "grpc_servicer"]
        assert len(service_symbols) >= 1
        assert any("UserService" in s.name for s in service_symbols)

    def test_detects_python_stub_usage(self, tmp_path: Path) -> None:
        """Detects Python gRPC stub (client) usage."""
        from hypergumbo_core.linkers.grpc import link_grpc

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
        client_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "grpc_stub"]
        assert len(client_symbols) >= 1
        assert any("UserService" in s.name for s in client_symbols)

    def test_detects_python_server_registration(self, tmp_path: Path) -> None:
        """Detects Python gRPC server.add_generic_rpc_handlers or add_*_to_server."""
        from hypergumbo_core.linkers.grpc import link_grpc

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
        from hypergumbo_core.linkers.grpc import link_grpc

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
        server_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "grpc_server"]
        assert len(server_symbols) >= 1

    def test_detects_go_client_creation(self, tmp_path: Path) -> None:
        """Detects Go gRPC client creation."""
        from hypergumbo_core.linkers.grpc import link_grpc

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
        client_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "grpc_client"]
        assert len(client_symbols) >= 1

    def test_detects_go_server_registration(self, tmp_path: Path) -> None:
        """Detects Go gRPC RegisterXxxServer calls."""
        from hypergumbo_core.linkers.grpc import link_grpc

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


class TestTtrpcPatterns:
    """Tests for detecting ttrpc (containerd/ttrpc) patterns in Go code.

    ttrpc is a lightweight alternative to gRPC used by kata-containers,
    containerd, and other container runtimes. It generates RegisterXxxService
    functions (not RegisterXxxServer) and uses interface-based implementation
    instead of UnimplementedXxxServer embedding.
    """

    def test_detects_ttrpc_service_registration(self, tmp_path: Path) -> None:
        """Detects Go ttrpc RegisterXxxService calls."""
        from hypergumbo_core.linkers.grpc import link_grpc

        go_file = tmp_path / "agent_ttrpc.pb.go"
        go_file.write_text(
            "package grpc\n\n"
            "func RegisterAgentServiceService(srv *ttrpc.Server, svc AgentServiceService) {\n"
            '    srv.RegisterService("grpc.AgentService", &ttrpc.ServiceDesc{})\n'
            "}\n"
        )

        result = link_grpc(tmp_path)

        server_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "grpc_server"]
        assert any("AgentService" in s.name for s in server_symbols), (
            f"Expected AgentService server symbol, got: {[s.name for s in server_symbols]}"
        )

    def test_ttrpc_implements_rpc_via_interface(self, tmp_path: Path) -> None:
        """ttrpc interface implementation creates implements_rpc edges."""
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "agent.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package grpc;\n"
            "service AgentService {\n"
            "    rpc CreateContainer(CreateContainerRequest) returns (Empty);\n"
            "    rpc StartContainer(StartContainerRequest) returns (Empty);\n"
            "}\n"
        )

        go_file = tmp_path / "agent_ttrpc.pb.go"
        go_file.write_text(
            "package grpc\n\n"
            "func RegisterAgentServiceService(srv *ttrpc.Server, svc AgentServiceService) {\n"
            '    srv.RegisterService("grpc.AgentService", &ttrpc.ServiceDesc{})\n'
            "}\n"
        )

        # Simulate Go analyzer having extracted the implementing struct
        go_method_syms = [
            Symbol(
                id=f"go:{tmp_path}/handler.go:10-12:agentHandler.CreateContainer:method",
                name="agentHandler.CreateContainer",
                kind="method",
                language="go",
                path=str(tmp_path / "handler.go"),
                span=Span(10, 12, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
            Symbol(
                id=f"go:{tmp_path}/handler.go:14-16:agentHandler.StartContainer:method",
                name="agentHandler.StartContainer",
                kind="method",
                language="go",
                path=str(tmp_path / "handler.go"),
                span=Span(14, 16, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
            # Struct symbol with ttrpc service interface in base_classes
            Symbol(
                id=f"go:{tmp_path}/handler.go:5-8:agentHandler:struct",
                name="agentHandler",
                kind="struct",
                language="go",
                path=str(tmp_path / "handler.go"),
                span=Span(5, 8, 0, 0),
                origin="go",
                origin_run_id="test",
                meta={"base_classes": ["AgentServiceService"]},
            ),
        ]

        result = link_grpc(tmp_path, existing_symbols=go_method_syms)

        impl_edges = [e for e in result.edges if e.edge_type == "implements" and (e.meta or {}).get("protocol") == "grpc"]
        assert len(impl_edges) == 2
        src_methods = sorted(e.src.split(":")[-2] for e in impl_edges)
        assert src_methods == [
            "agentHandler.CreateContainer",
            "agentHandler.StartContainer",
        ]

    def test_detects_ttrpc_health_service(self, tmp_path: Path) -> None:
        """Detects ttrpc RegisterHealthService pattern."""
        from hypergumbo_core.linkers.grpc import link_grpc

        go_file = tmp_path / "health_ttrpc.pb.go"
        go_file.write_text(
            "package grpc\n\n"
            "func RegisterHealthService(srv *ttrpc.Server, svc HealthService) {\n"
            '    srv.RegisterService("grpc.Health", &ttrpc.ServiceDesc{})\n'
            "}\n"
        )

        result = link_grpc(tmp_path)

        server_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "grpc_server"]
        assert any("Health" in s.name for s in server_symbols), (
            f"Expected Health server symbol, got: {[s.name for s in server_symbols]}"
        )

    def test_ttrpc_health_interface_implements_rpc(self, tmp_path: Path) -> None:
        """ttrpc HealthService interface (non-ServiceService suffix) creates edges."""
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "health.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package grpc;\n"
            "service Health {\n"
            "    rpc Check(HealthCheckRequest) returns (HealthCheckResponse);\n"
            "}\n"
        )

        go_file = tmp_path / "health_ttrpc.pb.go"
        go_file.write_text(
            "package grpc\n\n"
            "func RegisterHealthService(srv *ttrpc.Server, svc HealthService) {\n"
            '    srv.RegisterService("grpc.Health", &ttrpc.ServiceDesc{})\n'
            "}\n"
        )

        go_syms = [
            Symbol(
                id=f"go:{tmp_path}/mock.go:10-12:mockHealth.Check:method",
                name="mockHealth.Check",
                kind="method",
                language="go",
                path=str(tmp_path / "mock.go"),
                span=Span(10, 12, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
            Symbol(
                id=f"go:{tmp_path}/mock.go:5-8:mockHealth:struct",
                name="mockHealth",
                kind="struct",
                language="go",
                path=str(tmp_path / "mock.go"),
                span=Span(5, 8, 0, 0),
                origin="go",
                origin_run_id="test",
                meta={"base_classes": ["HealthService"]},
            ),
        ]

        result = link_grpc(tmp_path, existing_symbols=go_syms)

        impl_edges = [e for e in result.edges if e.edge_type == "implements" and (e.meta or {}).get("protocol") == "grpc"]
        assert len(impl_edges) == 1
        assert "mockHealth.Check" in impl_edges[0].src

    def test_ttrpc_skips_struct_already_mapped_via_unimplemented(self, tmp_path: Path) -> None:
        """Struct with both Unimplemented embedding and ttrpc interface uses Unimplemented mapping."""
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "cache.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package cache;\n"
            "service CacheService {\n"
            "    rpc Config(Empty) returns (VMConfig);\n"
            "}\n"
        )

        go_file = tmp_path / "server.go"
        go_file.write_text(
            "package main\n\n"
            "type cacheServer struct {\n"
            "    UnimplementedCacheServiceServer\n"
            "}\n"
        )

        go_syms = [
            Symbol(
                id=f"go:{go_file}:7-9:cacheServer.Config:method",
                name="cacheServer.Config",
                kind="method",
                language="go",
                path=str(go_file),
                span=Span(7, 9, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
            # Struct has BOTH Unimplemented embedding (in source) and ttrpc-like interface
            Symbol(
                id=f"go:{go_file}:3-5:cacheServer:struct",
                name="cacheServer",
                kind="struct",
                language="go",
                path=str(go_file),
                span=Span(3, 5, 0, 0),
                origin="go",
                origin_run_id="test",
                meta={"base_classes": ["UnimplementedCacheServiceServer", "CacheServiceClient"]},
            ),
        ]

        result = link_grpc(tmp_path, existing_symbols=go_syms)

        impl_edges = [e for e in result.edges if e.edge_type == "implements" and (e.meta or {}).get("protocol") == "grpc"]
        # Should still create 1 edge via the Unimplemented path
        assert len(impl_edges) == 1
        assert "cacheServer.Config" in impl_edges[0].src


    def test_csi_server_interface_implements_rpc(self, tmp_path: Path) -> None:
        """CSI-style XxxServer interface (no Unimplemented embedding) creates edges.

        CSI (Container Storage Interface) implementations use external library
        interfaces like csi.IdentityServer, csi.ControllerServer, csi.NodeServer.
        These end in 'Server' rather than 'Service', and don't embed an
        UnimplementedXxxServer struct.
        """
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "csi.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package csi;\n"
            "service Identity {\n"
            "    rpc GetPluginInfo(GetPluginInfoRequest) returns (GetPluginInfoResponse);\n"
            "    rpc Probe(ProbeRequest) returns (ProbeResponse);\n"
            "}\n"
        )

        go_file = tmp_path / "server.go"
        go_file.write_text(
            "package main\n\n"
            "import csi \"github.com/container-storage-interface/spec/lib/go/csi\"\n\n"
            "func (s *nonBlockingGRPCServer) serve(ids csi.IdentityServer) {\n"
            "    csi.RegisterIdentityServer(server, ids)\n"
            "}\n"
        )

        go_syms = [
            Symbol(
                id=f"go:{tmp_path}/driver.go:10-15:directVolume.GetPluginInfo:method",
                name="directVolume.GetPluginInfo",
                kind="method",
                language="go",
                path=str(tmp_path / "driver.go"),
                span=Span(10, 15, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
            Symbol(
                id=f"go:{tmp_path}/driver.go:17-22:directVolume.Probe:method",
                name="directVolume.Probe",
                kind="method",
                language="go",
                path=str(tmp_path / "driver.go"),
                span=Span(17, 22, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
            Symbol(
                id=f"go:{tmp_path}/driver.go:5-8:directVolume:struct",
                name="directVolume",
                kind="struct",
                language="go",
                path=str(tmp_path / "driver.go"),
                span=Span(5, 8, 0, 0),
                origin="go",
                origin_run_id="test",
                meta={"base_classes": ["IdentityServer"]},
            ),
        ]

        result = link_grpc(tmp_path, existing_symbols=go_syms)

        impl_edges = [e for e in result.edges if e.edge_type == "implements" and (e.meta or {}).get("protocol") == "grpc"]
        assert len(impl_edges) == 2
        src_methods = sorted(e.src.split(":")[-2] for e in impl_edges)
        assert src_methods == [
            "directVolume.GetPluginInfo",
            "directVolume.Probe",
        ]

    def test_csi_server_skips_unimplemented_base_class(self, tmp_path: Path) -> None:
        """base_classes containing UnimplementedXxx are filtered from Server matching."""
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "svc.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package pkg;\n"
            "service Node {\n"
            "    rpc NodePublish(Req) returns (Resp);\n"
            "}\n"
        )

        go_syms = [
            Symbol(
                id=f"go:{tmp_path}/impl.go:10-12:nodeImpl.NodePublish:method",
                name="nodeImpl.NodePublish",
                kind="method",
                language="go",
                path=str(tmp_path / "impl.go"),
                span=Span(10, 12, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
            Symbol(
                id=f"go:{tmp_path}/impl.go:5-8:nodeImpl:struct",
                name="nodeImpl",
                kind="struct",
                language="go",
                path=str(tmp_path / "impl.go"),
                span=Span(5, 8, 0, 0),
                origin="go",
                origin_run_id="test",
                # Has both Unimplemented and a real interface
                meta={"base_classes": ["UnimplementedNodeServer", "NodeServer"]},
            ),
        ]

        result = link_grpc(tmp_path, existing_symbols=go_syms)

        impl_edges = [e for e in result.edges if e.edge_type == "implements" and (e.meta or {}).get("protocol") == "grpc"]
        # Should still create 1 edge via the NodeServer interface
        assert len(impl_edges) == 1
        assert "nodeImpl.NodePublish" in impl_edges[0].src

    def test_csi_controller_server_registration(self, tmp_path: Path) -> None:
        """CSI RegisterControllerServer creates grpc_server symbol."""
        from hypergumbo_core.linkers.grpc import link_grpc

        go_file = tmp_path / "server.go"
        go_file.write_text(
            "package main\n\n"
            "func (s *server) serve(cs csi.ControllerServer) {\n"
            "    csi.RegisterControllerServer(grpcServer, cs)\n"
            "}\n"
        )

        result = link_grpc(tmp_path)

        server_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "grpc_server"]
        assert any("Controller" in s.name for s in server_symbols), (
            f"Expected Controller server symbol, got: {[s.name for s in server_symbols]}"
        )


class TestGrpcTypescriptClientToProtoBinding:
    """WI-ropoz: TS gRPC client → proto service binding.

    When a TypeScript file uses ``new FooServiceClient(...)`` and the
    repo contains a ``.proto`` declaring ``service FooService { ... }``
    but no impl-side servicer in the analyzed tree (the impl may live
    in a separate language repo, like workadventure where the server is
    Go and lives in a different module), the linker must still emit a
    binding edge from the TS client to the proto service Symbol —
    otherwise the cross-language graph drops the entire client side.

    Edit shape: ``edge_type='calls'`` + ``meta['protocol']='grpc'`` +
    ``Edge.is_resolved=False`` (since no in-tree handler was located).
    """

    def test_ts_client_binds_to_proto_service_when_no_impl(self, tmp_path: Path) -> None:
        """TS ``new FooServiceClient`` → proto ``service FooService`` edge.

        With no impl-side servicer in the tree, the TS client gets an
        edge directly to the proto-service Symbol so the cross-language
        slice can reach the .proto contract.
        """
        from hypergumbo_core.linkers.grpc import link_grpc

        proto = tmp_path / "service.proto"
        proto.write_text(
            'syntax = "proto3";\n'
            'package example;\n'
            'service FooService {\n'
            '  rpc GetFoo (Req) returns (Resp);\n'
            '}\n'
        )

        ts = tmp_path / "client.ts"
        ts.write_text(
            "import { FooServiceClient } from './generated/foo_grpc_web_pb';\n"
            "const client = new FooServiceClient('http://localhost:8080');\n"
        )

        result = link_grpc(tmp_path)

        # The TS client should be linked to the proto service. ``calls``
        # + meta['protocol']='grpc' + is_resolved=False (no impl found).
        binding_edges = [
            e for e in result.edges
            if e.edge_type == "calls"
            and (e.meta or {}).get("protocol") == "grpc"
            and not e.is_resolved
        ]
        assert len(binding_edges) >= 1, (
            "Expected at least one TS→proto binding edge; got "
            f"{[(e.edge_type, dict(e.meta or {})) for e in result.edges]}"
        )

    def test_ts_client_fallback_dedupes_cross_package_collision(self, tmp_path: Path) -> None:
        """Two ``.proto`` files declaring the same short-name service
        force INV-zuhub disambiguation_fallback on the no-impl fallback.

        Edge gets ``meta['disambiguation_fallback']=True`` and lower
        confidence; resolution stays deterministic-by-id.
        """
        from hypergumbo_core.linkers.grpc import link_grpc

        (tmp_path / "pkg_a").mkdir()
        (tmp_path / "pkg_b").mkdir()
        (tmp_path / "pkg_a" / "svc.proto").write_text(
            'syntax = "proto3";\n'
            'package pkg_a;\n'
            'service ShareName {\n'
            '  rpc DoIt (R) returns (R);\n'
            '}\n'
        )
        (tmp_path / "pkg_b" / "svc.proto").write_text(
            'syntax = "proto3";\n'
            'package pkg_b;\n'
            'service ShareName {\n'
            '  rpc DoIt (R) returns (R);\n'
            '}\n'
        )

        ts = tmp_path / "client.ts"
        ts.write_text(
            "const c = new ShareNameClient('http://localhost:8080');\n"
        )

        result = link_grpc(tmp_path)

        fallback_edges = [
            e for e in result.edges
            if e.edge_type == "calls"
            and (e.meta or {}).get("protocol") == "grpc"
            and (e.meta or {}).get("disambiguation_fallback") is True
        ]
        assert len(fallback_edges) >= 1, (
            "Expected disambiguation_fallback edge for cross-package "
            f"service-name collision; got {[dict(e.meta or {}) for e in result.edges]}"
        )

    def test_ts_client_still_prefers_servicer_when_impl_present(self, tmp_path: Path) -> None:
        """When a servicer is also present, the TS client edge should
        prefer the impl (precision over the proto fallback)."""
        from hypergumbo_core.linkers.grpc import link_grpc

        proto = tmp_path / "service.proto"
        proto.write_text(
            'syntax = "proto3";\n'
            'package example;\n'
            'service BarService {\n'
            '  rpc GetBar (Req) returns (Resp);\n'
            '}\n'
        )

        # Python servicer impl
        py = tmp_path / "server.py"
        py.write_text(
            "class BarServiceServicer(bar_pb2_grpc.BarServiceServicer):\n"
            "    pass\n"
        )

        # TS client
        ts = tmp_path / "client.ts"
        ts.write_text(
            "import { BarServiceClient } from './generated/bar_grpc_web_pb';\n"
            "const client = new BarServiceClient('http://localhost:8080');\n"
        )

        result = link_grpc(tmp_path)

        # Precision edge to the servicer should exist.
        precision_edges = [
            e for e in result.edges
            if e.edge_type == "calls"
            and (e.meta or {}).get("protocol") == "grpc"
            and e.is_resolved
        ]
        assert len(precision_edges) >= 1


class TestGrpcEdgeCreation:
    """Tests for edge creation linking clients to servers."""

    def test_creates_edges_between_client_and_server(self, tmp_path: Path) -> None:
        """Creates edges linking clients to servers by service name."""
        from hypergumbo_core.linkers.grpc import link_grpc

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

        # Should create canonical 'calls' edges with meta['protocol']='grpc'
        # (post WI-vumum-juvil; pre-fold edge_type was 'grpc_calls').
        grpc_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and e.meta.get("protocol") == "grpc"
        ]
        assert len(grpc_edges) >= 1


class TestGrpcJavaPatterns:
    """Tests for detecting gRPC patterns in Java code."""

    def test_detects_java_service_implementation(self, tmp_path: Path) -> None:
        """Detects Java gRPC service implementations."""
        from hypergumbo_core.linkers.grpc import link_grpc

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
        service_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "grpc_servicer"]
        assert len(service_symbols) >= 1

    def test_detects_java_stub_usage(self, tmp_path: Path) -> None:
        """Detects Java gRPC stub usage."""
        from hypergumbo_core.linkers.grpc import link_grpc

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
        client_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "grpc_stub"]
        assert len(client_symbols) >= 1


class TestGrpcTypeScriptPatterns:
    """Tests for detecting gRPC patterns in TypeScript/JavaScript."""

    def test_detects_grpc_js_client(self, tmp_path: Path) -> None:
        """Detects gRPC-web or grpc-js client usage."""
        from hypergumbo_core.linkers.grpc import link_grpc

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
        client_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") in ("grpc_client", "grpc_stub")]
        assert len(client_symbols) >= 1


class TestGrpcProtoFileDetection:
    """Tests for detecting Protocol Buffer files."""

    def test_detects_proto_service_definitions(self, tmp_path: Path) -> None:
        """Detects service definitions in .proto files."""
        from hypergumbo_core.linkers.grpc import link_grpc

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
        proto_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "grpc_service"]
        assert len(proto_symbols) >= 1
        assert any("UserService" in s.name for s in proto_symbols)


class TestGrpcSymbolProperties:
    """Tests for symbol property correctness."""

    def test_symbols_have_correct_properties(self, tmp_path: Path) -> None:
        """Symbols have correct origin."""
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "test.proto"
        proto_file.write_text('''
service TestService {
    rpc DoSomething(Request) returns (Response);
}
''')

        result = link_grpc(tmp_path)

        for symbol in result.symbols:
            assert symbol.origin == ["grpc-linker"]


class TestGrpcEdgeProperties:
    """Tests for edge property correctness."""

    def test_edges_have_confidence(self, tmp_path: Path) -> None:
        """Edges have confidence values."""
        from hypergumbo_core.linkers.grpc import link_grpc

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
        from hypergumbo_core.linkers.grpc import link_grpc

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
        from hypergumbo_core.linkers.grpc import link_grpc

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
        from hypergumbo_core.linkers.grpc import link_grpc

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
        client_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") in ("grpc_client", "grpc_stub")]
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
        from hypergumbo_core.linkers.grpc import _normalize_service_name

        # Names without standard suffixes should return unchanged
        assert _normalize_service_name("User") == "User"
        assert _normalize_service_name("API") == "API"
        assert _normalize_service_name("Handler") == "Handler"

    def test_normalizes_names_with_suffix(self, tmp_path: Path) -> None:
        """Removes common gRPC suffixes for matching."""
        from hypergumbo_core.linkers.grpc import _normalize_service_name

        # Names with standard suffixes should have them removed
        assert _normalize_service_name("UserService") == "User"
        assert _normalize_service_name("UserServicer") == "User"
        assert _normalize_service_name("UserStub") == "User"
        assert _normalize_service_name("UserClient") == "User"
        assert _normalize_service_name("UserServer") == "User"


class TestGrpcProtoRouteSymbols:
    """Tests for gRPC proto RPC methods surfaced as route symbols.

    gRPC RPC methods are accessed via HTTP/2 at path
    /<package>.<ServiceName>/<MethodName>.  The linker should create
    kind="function" symbols for proto RPC definitions so they appear in
    ``routes.txt`` and can be linked to handler implementations.
    """

    def test_proto_rpc_creates_route_symbols(self, tmp_path: Path) -> None:
        """Proto RPC methods produce kind='route' symbols."""
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "user.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package example;\n"
            "service UserService {\n"
            "    rpc GetUser(GetUserRequest) returns (User);\n"
            "    rpc CreateUser(CreateUserRequest) returns (User);\n"
            "}\n"
        )

        result = link_grpc(tmp_path)

        route_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "route"]
        assert len(route_symbols) == 2

        route_names = sorted(s.name for s in route_symbols)
        assert route_names == [
            "RPC /example.UserService/CreateUser",
            "RPC /example.UserService/GetUser",
        ]

    def test_proto_rpc_route_has_metadata(self, tmp_path: Path) -> None:
        """Route symbols have route_path and rpc_service metadata."""
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "order.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package shop.v1;\n"
            "service OrderService {\n"
            "    rpc PlaceOrder(PlaceOrderRequest) returns (OrderResponse);\n"
            "}\n"
        )

        result = link_grpc(tmp_path)

        route_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "route"]
        assert len(route_symbols) == 1

        route = route_symbols[0]
        assert route.meta is not None
        assert route.meta["route_path"] == "/shop.v1.OrderService/PlaceOrder"
        assert route.meta["http_method"] is None
        assert route.meta["route_protocol"] == "grpc"
        assert route.meta["rpc_service"] == "OrderService"

    def test_proto_rpc_route_without_package(self, tmp_path: Path) -> None:
        """Proto files without package still produce routes."""
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "echo.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "service EchoService {\n"
            "    rpc Echo(EchoRequest) returns (EchoResponse);\n"
            "}\n"
        )

        result = link_grpc(tmp_path)

        route_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "route"]
        assert len(route_symbols) == 1

        route = route_symbols[0]
        assert route.meta["route_path"] == "/EchoService/Echo"
        assert route.name == "RPC /EchoService/Echo"

    def test_proto_rpc_route_has_stable_id(self, tmp_path: Path) -> None:
        """Route symbols have stable_id for deduplication."""
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "user.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package example;\n"
            "service UserService {\n"
            "    rpc GetUser(GetUserRequest) returns (User);\n"
            "}\n"
        )

        result = link_grpc(tmp_path)

        route_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "route"]
        assert len(route_symbols) == 1
        assert route_symbols[0].stable_id is not None
        assert route_symbols[0].stable_id.startswith("sha256:")

    def test_proto_rpc_route_creates_routes_to_edge(self, tmp_path: Path) -> None:
        """Route symbols get routes_to edges to the service symbol."""
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "user.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package example;\n"
            "service UserService {\n"
            "    rpc GetUser(GetUserRequest) returns (User);\n"
            "}\n"
        )

        result = link_grpc(tmp_path)

        routes_to_edges = [e for e in result.edges if e.edge_type == "dispatches_to"]
        assert len(routes_to_edges) == 1
        # Route should point to the grpc_service symbol
        assert any(
            (s.meta or {}).get("framework_role") == "grpc_service" and s.id == routes_to_edges[0].dst
            for s in result.symbols
        )

    def test_multiple_services_produce_separate_routes(self, tmp_path: Path) -> None:
        """Multiple services in one proto file produce separate route symbols."""
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "multi.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package api;\n"
            "service UserService {\n"
            "    rpc GetUser(GetUserRequest) returns (User);\n"
            "}\n"
            "service OrderService {\n"
            "    rpc PlaceOrder(PlaceOrderRequest) returns (Order);\n"
            "}\n"
        )

        result = link_grpc(tmp_path)

        route_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "route"]
        assert len(route_symbols) == 2
        paths = sorted(s.meta["route_path"] for s in route_symbols)
        assert paths == [
            "/api.OrderService/PlaceOrder",
            "/api.UserService/GetUser",
        ]


class TestGrpcServerToServiceBridge:
    """Tests for dispatches_to edges bridging server/servicer to service symbols.

    Canonical 'calls' edges with meta['protocol']='grpc' (post
    WI-vumum-juvil; pre-fold name was grpc_calls) terminate at
    grpc_server/grpc_servicer symbols (from Go RegisterXxxServer or
    Python XxxServicer). routes_to edges originate from route symbols
    and target grpc_service symbols (from proto files). Without a
    bridge edge, these two graph components are disconnected, breaking forward
    slice traversal from client code to handler implementations.
    """

    def test_go_server_dispatches_to_proto_service(self, tmp_path: Path) -> None:
        """Go grpc_server symbol gets dispatches_to edge to proto grpc_service."""
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "user.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package example;\n"
            "service UserService {\n"
            "    rpc GetUser(GetUserRequest) returns (User);\n"
            "}\n"
        )

        go_file = tmp_path / "server.go"
        go_file.write_text(
            "package main\n\n"
            "type userServer struct {\n"
            "    pb.UnimplementedUserServiceServer\n"
            "}\n"
        )

        result = link_grpc(tmp_path)

        dispatches = [e for e in result.edges if e.edge_type == "dispatches_to"]
        assert len(dispatches) >= 1

        # The edge should go from grpc_server to grpc_service
        server_ids = {s.id for s in result.symbols if (s.meta or {}).get("framework_role") == "grpc_server"}
        service_ids = {s.id for s in result.symbols if (s.meta or {}).get("framework_role") == "grpc_service"}
        bridge = [
            e for e in dispatches
            if e.src in server_ids and e.dst in service_ids
        ]
        assert len(bridge) == 1
        assert bridge[0].evidence_type == "ast_call_direct"
        assert (bridge[0].meta or {}).get("framework_dispatch") == "grpc_server_to_service"

    def test_python_servicer_dispatches_to_proto_service(self, tmp_path: Path) -> None:
        """Python grpc_servicer symbol gets dispatches_to edge to proto grpc_service."""
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "user.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package example;\n"
            "service UserService {\n"
            "    rpc GetUser(GetUserRequest) returns (User);\n"
            "}\n"
        )

        py_file = tmp_path / "server.py"
        py_file.write_text(
            "class UserServiceServicer(user_pb2_grpc.UserServiceServicer):\n"
            "    def GetUser(self, request, context):\n"
            "        return user_pb2.User(name='test')\n"
        )

        result = link_grpc(tmp_path)

        dispatches = [
            e for e in result.edges
            if e.edge_type == "dispatches_to"
            and (e.meta or {}).get("framework_dispatch") == "grpc_server_to_service"
        ]
        assert len(dispatches) >= 1

        # Verify it bridges servicer to service
        servicer_ids = {s.id for s in result.symbols if (s.meta or {}).get("framework_role") == "grpc_servicer"}
        service_ids = {s.id for s in result.symbols if (s.meta or {}).get("framework_role") == "grpc_service"}
        bridge = [e for e in dispatches if e.src in servicer_ids and e.dst in service_ids]
        assert len(bridge) == 1

    def test_end_to_end_client_to_handler_traversal(self, tmp_path: Path) -> None:
        """Full chain: grpc_client → grpc_server → grpc_service, reachable via edges.

        This is the key integration test: a forward slice from a client stub
        should be able to traverse through server to service to route to handler.
        """
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "user.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package example;\n"
            "service UserService {\n"
            "    rpc GetUser(GetUserRequest) returns (User);\n"
            "}\n"
        )

        go_main = tmp_path / "main.go"
        go_main.write_text(
            "package main\n\n"
            "func main() {\n"
            "    pb.RegisterUserServiceServer(s, &server{})\n"
            "}\n"
        )

        go_client = tmp_path / "client.go"
        go_client.write_text(
            "package main\n\n"
            "func call() {\n"
            "    c := pb.NewUserServiceClient(conn)\n"
            "}\n"
        )

        result = link_grpc(tmp_path)

        # Build adjacency list
        adj: dict[str, set[str]] = {}
        for e in result.edges:
            adj.setdefault(e.src, set()).add(e.dst)

        # Find client and service symbols
        clients = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "grpc_client"]
        services = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "grpc_service"]

        assert len(clients) >= 1, "Should have at least one client"
        assert len(services) >= 1, "Should have at least one service"

        # BFS from client to check if service is reachable
        client_id = clients[0].id
        service_id = services[0].id

        visited: set[str] = set()
        queue = [client_id]
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            for neighbor in adj.get(node, set()):
                queue.append(neighbor)

        assert service_id in visited, (
            f"grpc_service {service_id} not reachable from grpc_client {client_id}. "
            f"Visited: {visited}"
        )

    def test_no_bridge_when_no_matching_service(self, tmp_path: Path) -> None:
        """No dispatches_to edge when there's no matching proto service."""
        from hypergumbo_core.linkers.grpc import link_grpc

        go_file = tmp_path / "server.go"
        go_file.write_text(
            "package main\n\n"
            "type server struct {\n"
            "    pb.UnimplementedOrderServiceServer\n"
            "}\n"
        )

        result = link_grpc(tmp_path)

        dispatches = [
            e for e in result.edges
            if e.edge_type == "dispatches_to"
            and (e.meta or {}).get("framework_dispatch") == "grpc_server_to_service"
        ]
        assert len(dispatches) == 0


class TestINVZuhubConformanceForServiceLookup:
    """INV-zuhub item 1: cross-package short-name collisions on grpc_service.

    Two .proto files in different packages can each declare a service with
    the same short name (the containerd-style versioned-monorepo shape:
    ``services/containers/v1/containers.proto`` and
    ``services/containers/v2/containers.proto`` both declare
    ``service Containers``). The pre-fix single-value
    ``service_sym_by_name: dict[str, str]`` lookup collapsed them onto
    whichever file iteration processed last — both routes_to and the
    server-to-service bridge then pointed every cross-package edge at one
    surviving service symbol.

    Per INV-zuhub item 1:

    - The routes_to lookup has ``rpc.package`` available — when exactly one
      candidate service symbol carries ``meta["proto_package"]`` matching
      the RPC's package, the edge is a precision resolution (no flag).
    - When the package match is ambiguous (no match, multiple matches),
      the edge drops to ``confidence=0.5`` + ``meta["disambiguation_fallback"]=True``.
    - The server-to-service bridge consults impl-side symbols (Go struct
      embedding ``UnimplementedXxxServer``, Python servicer class) that
      carry no proto package metadata; multi-candidate normalized matches
      always drop to the fallback flag because the impl alone cannot
      disambiguate.
    """

    def test_cross_package_routes_resolve_via_package_preference(self, tmp_path: Path) -> None:
        """Two protos, different packages, same service name. Each package's RPC routes_to its own service at precision."""
        from hypergumbo_core.linkers.grpc import link_grpc

        (tmp_path / "a.proto").write_text(
            'syntax = "proto3";\n'
            "package pkg_a;\n"
            "service Foo {\n"
            "    rpc GetA(GetARequest) returns (Result);\n"
            "}\n"
        )
        (tmp_path / "b.proto").write_text(
            'syntax = "proto3";\n'
            "package pkg_b;\n"
            "service Foo {\n"
            "    rpc GetB(GetBRequest) returns (Result);\n"
            "}\n"
        )

        result = link_grpc(tmp_path)

        services = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "grpc_service"]
        assert len(services) == 2
        svc_a = next(s for s in services if (s.meta or {}).get("proto_package") == "pkg_a")
        svc_b = next(s for s in services if (s.meta or {}).get("proto_package") == "pkg_b")

        routes_a = [s for s in result.symbols if (s.meta or {}).get("route_path") == "/pkg_a.Foo/GetA"]
        routes_b = [s for s in result.symbols if (s.meta or {}).get("route_path") == "/pkg_b.Foo/GetB"]
        assert len(routes_a) == 1 and len(routes_b) == 1

        routes_to_edges = [
            e for e in result.edges
            if e.edge_type == "dispatches_to"
            and (e.meta or {}).get("framework_dispatch") == "grpc_rpc_definition"
        ]
        a_edge = next(e for e in routes_to_edges if e.src == routes_a[0].id)
        b_edge = next(e for e in routes_to_edges if e.src == routes_b[0].id)

        assert a_edge.dst == svc_a.id
        assert b_edge.dst == svc_b.id
        # Each RPC's package uniquely matched one service candidate — precision.
        assert (a_edge.meta or {}).get("disambiguation_fallback") is None
        assert (b_edge.meta or {}).get("disambiguation_fallback") is None
        assert a_edge.confidence == 0.90
        assert b_edge.confidence == 0.90

    def test_cross_package_bridge_drops_to_fallback(self, tmp_path: Path) -> None:
        """Go impl with no proto package metadata + cross-package service collision = fallback flag."""
        from hypergumbo_core.linkers.grpc import link_grpc

        (tmp_path / "a.proto").write_text(
            'syntax = "proto3";\n'
            "package pkg_a;\n"
            "service UserService {\n"
            "    rpc Get(R) returns (R);\n"
            "}\n"
        )
        (tmp_path / "b.proto").write_text(
            'syntax = "proto3";\n'
            "package pkg_b;\n"
            "service UserService {\n"
            "    rpc Get(R) returns (R);\n"
            "}\n"
        )
        (tmp_path / "server.go").write_text(
            "package main\n\n"
            "type server struct {\n"
            "    pb.UnimplementedUserServiceServer\n"
            "}\n"
        )

        result = link_grpc(tmp_path)

        bridge_edges = [
            e for e in result.edges
            if e.edge_type == "dispatches_to"
            and (e.meta or {}).get("framework_dispatch") == "grpc_server_to_service"
        ]
        assert len(bridge_edges) == 1
        bridge = bridge_edges[0]
        assert bridge.confidence == 0.5
        assert (bridge.meta or {}).get("disambiguation_fallback") is True

    def test_single_service_bridge_precision(self, tmp_path: Path) -> None:
        """Single .proto + single Go impl: no collision, bridge precision (regression guard)."""
        from hypergumbo_core.linkers.grpc import link_grpc

        (tmp_path / "user.proto").write_text(
            'syntax = "proto3";\n'
            "package example;\n"
            "service UserService {\n"
            "    rpc Get(R) returns (R);\n"
            "}\n"
        )
        (tmp_path / "server.go").write_text(
            "package main\n\n"
            "type server struct {\n"
            "    pb.UnimplementedUserServiceServer\n"
            "}\n"
        )

        result = link_grpc(tmp_path)

        bridge_edges = [
            e for e in result.edges
            if e.edge_type == "dispatches_to"
            and (e.meta or {}).get("framework_dispatch") == "grpc_server_to_service"
        ]
        assert len(bridge_edges) == 1
        bridge = bridge_edges[0]
        assert bridge.confidence == 0.90
        assert (bridge.meta or {}).get("disambiguation_fallback") is None

    def test_grpc_service_symbol_carries_proto_package_meta(self, tmp_path: Path) -> None:
        """grpc_service Symbols emit ``meta["proto_package"]`` when the .proto declares a package."""
        from hypergumbo_core.linkers.grpc import link_grpc

        (tmp_path / "user.proto").write_text(
            'syntax = "proto3";\n'
            "package shop.v1;\n"
            "service UserService {\n"
            "    rpc Get(R) returns (R);\n"
            "}\n"
        )

        result = link_grpc(tmp_path)

        services = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "grpc_service"]
        assert len(services) == 1
        assert services[0].meta.get("proto_package") == "shop.v1"

    def test_routes_to_falls_back_when_no_package_disambiguator(self, tmp_path: Path) -> None:
        """Two package-less .proto files with same service name → both routes_to fall back.

        Without a proto ``package`` declaration, the routes_to lookup has no
        precision disambiguator and must drop to the INV-zuhub fallback
        shape. Covers the multi-candidate-no-package-match branch.
        """
        from hypergumbo_core.linkers.grpc import link_grpc

        (tmp_path / "a.proto").write_text(
            'syntax = "proto3";\n'
            "service Foo {\n"
            "    rpc GetA(R) returns (R);\n"
            "}\n"
        )
        (tmp_path / "b.proto").write_text(
            'syntax = "proto3";\n'
            "service Foo {\n"
            "    rpc GetB(R) returns (R);\n"
            "}\n"
        )

        result = link_grpc(tmp_path)

        routes_to_edges = [
            e for e in result.edges
            if e.edge_type == "dispatches_to"
            and (e.meta or {}).get("framework_dispatch") == "grpc_rpc_definition"
        ]
        assert len(routes_to_edges) == 2
        for e in routes_to_edges:
            assert e.confidence == 0.5
            assert (e.meta or {}).get("disambiguation_fallback") is True

    def test_grpc_service_without_package_omits_meta(self, tmp_path: Path) -> None:
        """grpc_service Symbols from .proto files without a package declaration omit proto_package meta."""
        from hypergumbo_core.linkers.grpc import link_grpc

        (tmp_path / "echo.proto").write_text(
            'syntax = "proto3";\n'
            "service EchoService {\n"
            "    rpc Echo(R) returns (R);\n"
            "}\n"
        )

        result = link_grpc(tmp_path)

        services = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "grpc_service"]
        assert len(services) == 1
        # No proto_package key when .proto omits package
        assert "proto_package" not in (services[0].meta or {})


class TestGrpcProtoToGoImplementation:
    """Tests for linking proto RPC definitions to Go implementation methods.

    When a .proto file defines a service with RPC methods, and a Go file has a
    struct embedding UnimplementedXxxServer with methods matching those RPCs,
    the linker should create implements_rpc edges from the Go methods to the
    proto RPC route symbols.
    """

    def test_links_go_method_to_proto_rpc_route(self, tmp_path: Path) -> None:
        """Go method on server struct creates implements_rpc edge to proto RPC."""
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.linkers.grpc import link_grpc

        # Proto defines the service
        proto_file = tmp_path / "user.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package user;\n"
            "service UserService {\n"
            "    rpc GetUser(GetUserRequest) returns (User);\n"
            "    rpc CreateUser(CreateUserRequest) returns (User);\n"
            "}\n"
        )

        # Go file implements the service
        go_file = tmp_path / "server.go"
        go_file.write_text(
            "package main\n\n"
            "type server struct {\n"
            "    UnimplementedUserServiceServer\n"
            "}\n\n"
            "func (s *server) GetUser(ctx context.Context, req *pb.GetUserRequest) (*pb.User, error) {\n"
            '    return &pb.User{Name: "test"}, nil\n'
            "}\n\n"
            "func (s *server) CreateUser(ctx context.Context, req *pb.CreateUserRequest) (*pb.User, error) {\n"
            "    return &pb.User{Name: req.Name}, nil\n"
            "}\n"
        )

        # Pass existing Go method symbols (as if the Go analyzer had run)
        go_method_syms = [
            Symbol(
                id=f"go:{go_file}:7-9:server.GetUser:method",
                name="server.GetUser",
                kind="method",
                language="go",
                path=str(go_file),
                span=Span(7, 9, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
            Symbol(
                id=f"go:{go_file}:11-13:server.CreateUser:method",
                name="server.CreateUser",
                kind="method",
                language="go",
                path=str(go_file),
                span=Span(11, 13, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
        ]

        result = link_grpc(tmp_path, existing_symbols=go_method_syms)

        # Should have implements_rpc edges from Go methods to proto routes
        impl_edges = [e for e in result.edges if e.edge_type == "implements" and (e.meta or {}).get("protocol") == "grpc"]
        assert len(impl_edges) == 2

        # Check edge targets are proto RPC route symbols
        route_ids = {s.id for s in result.symbols if (s.meta or {}).get("framework_role") == "route"}
        for edge in impl_edges:
            assert edge.dst in route_ids

        # Check source names cover both methods
        src_names = sorted(e.src.split(":")[-2] for e in impl_edges)
        assert src_names == ["server.CreateUser", "server.GetUser"]

    def test_skips_non_go_non_method_symbols(self, tmp_path: Path) -> None:
        """Non-Go symbols and non-method symbols are ignored."""
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "user.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package user;\n"
            "service UserService {\n"
            "    rpc GetUser(Req) returns (Resp);\n"
            "}\n"
        )

        go_file = tmp_path / "server.go"
        go_file.write_text(
            "package main\n\n"
            "type server struct {\n"
            "    UnimplementedUserServiceServer\n"
            "}\n\n"
            "func (s *server) GetUser(ctx context.Context, req *Req) (*Resp, error) {\n"
            "    return nil, nil\n"
            "}\n"
        )

        # Pass a Python symbol, a Go function (not method), and a method
        # without dot — all should be skipped
        non_matching = [
            Symbol(
                id="python:handler.py:1-3:GetUser:function",
                name="GetUser",
                kind="function",
                language="python",
                path="handler.py",
                span=Span(1, 3, 0, 0),
                origin="py",
                origin_run_id="test",
            ),
            Symbol(
                id=f"go:{go_file}:1-3:GetUser:function",
                name="GetUser",
                kind="function",
                language="go",
                path=str(go_file),
                span=Span(1, 3, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
            Symbol(
                id=f"go:{go_file}:1-3:NoDotName:method",
                name="NoDotName",
                kind="method",
                language="go",
                path=str(go_file),
                span=Span(1, 3, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
            Symbol(
                id=f"go:{go_file}:1-3:otherStruct.GetUser:method",
                name="otherStruct.GetUser",
                kind="method",
                language="go",
                path=str(go_file),
                span=Span(1, 3, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
            Symbol(
                id=f"go:{go_file}:7-9:server.NonExistentRpc:method",
                name="server.NonExistentRpc",
                kind="method",
                language="go",
                path=str(go_file),
                span=Span(7, 9, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
        ]

        result = link_grpc(tmp_path, existing_symbols=non_matching)

        impl_edges = [e for e in result.edges if e.edge_type == "implements" and (e.meta or {}).get("protocol") == "grpc"]
        assert len(impl_edges) == 0

    def test_no_link_without_unimplemented_embedding(self, tmp_path: Path) -> None:
        """Go methods without UnimplementedXxxServer don't get linked."""
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "user.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package user;\n"
            "service UserService {\n"
            "    rpc GetUser(GetUserRequest) returns (User);\n"
            "}\n"
        )

        # Go file without UnimplementedUserServiceServer
        go_file = tmp_path / "handler.go"
        go_file.write_text(
            "package main\n\n"
            "type handler struct {}\n\n"
            "func (h *handler) GetUser(ctx context.Context, req *pb.GetUserRequest) (*pb.User, error) {\n"
            '    return &pb.User{Name: "test"}, nil\n'
            "}\n"
        )

        go_method_syms = [
            Symbol(
                id=f"go:{go_file}:5-7:handler.GetUser:method",
                name="handler.GetUser",
                kind="method",
                language="go",
                path=str(go_file),
                span=Span(5, 7, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
        ]

        result = link_grpc(tmp_path, existing_symbols=go_method_syms)

        impl_edges = [e for e in result.edges if e.edge_type == "implements" and (e.meta or {}).get("protocol") == "grpc"]
        assert len(impl_edges) == 0

    def test_no_link_when_struct_pattern_not_extractable(self, tmp_path: Path) -> None:
        """No implements_rpc when struct embedding pattern is unusual."""
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "svc.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "service MyService {\n"
            "    rpc DoThing(Req) returns (Resp);\n"
            "}\n"
        )

        # Go file references UnimplementedMyServiceServer but not inside
        # a type ... struct { } block that the regex can parse — it's in a
        # comment, so the regex-based scan finds the Go pattern (triggering
        # file re-read) but _GO_STRUCT_WITH_UNIMPLEMENTED fails to match.
        go_file = tmp_path / "impl.go"
        go_file.write_text(
            "package main\n\n"
            "// We embed UnimplementedMyServiceServer elsewhere\n"
            "var _ = UnimplementedMyServiceServer{}\n"
        )

        go_syms = [
            Symbol(
                id=f"go:{go_file}:4-4:implServer.DoThing:method",
                name="implServer.DoThing",
                kind="method",
                language="go",
                path=str(go_file),
                span=Span(4, 4, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
        ]

        result = link_grpc(tmp_path, existing_symbols=go_syms)
        impl_edges = [e for e in result.edges if e.edge_type == "implements" and (e.meta or {}).get("protocol") == "grpc"]
        assert len(impl_edges) == 0

    def test_same_struct_name_in_multiple_files_resolves_per_file(
        self, tmp_path: Path,
    ) -> None:
        """WI-kunoz / BUG-05: two Go files each defining ``type service struct``
        embedding different ``UnimplementedXxxServer`` types must each resolve
        their methods to their own service's RPCs.

        On containerd, eight plugin packages (containers, content, diff,
        images, leases, mounts, namespaces, snapshots, tasks) each declare a
        struct literally named ``service`` that embeds the corresponding
        ``UnimplementedXxxServer``. Pre-fix, ``struct_to_service`` was keyed
        by short struct name, so all eight collapsed onto whichever file the
        ``go_server_files`` set happened to iterate last — every
        ``service.Create`` ended up routed to the same wrong RPC family
        (Round 08 §8.3 of hg-uat-v4.1.0). Distinctive struct names
        (controllerService, sandboxService, server) escaped the collision
        because they were unique across files.

        Files are written in two subdirectories so the file paths are clearly
        distinct, mirroring the containerd layout.
        """
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "all.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package containerd.services;\n"
            "service Containers {\n"
            "    rpc Create(CreateContainerRequest) returns (Container);\n"
            "}\n"
            "service Leases {\n"
            "    rpc Create(CreateLeaseRequest) returns (Lease);\n"
            "}\n"
        )

        containers_file = tmp_path / "plugins" / "containers" / "service.go"
        containers_file.parent.mkdir(parents=True)
        containers_file.write_text(
            "package containers\n\n"
            "type service struct {\n"
            "    api.UnimplementedContainersServer\n"
            "}\n\n"
            "func (s *service) Create(ctx context.Context, req *CreateContainerRequest) (*Container, error) {\n"
            "    return nil, nil\n"
            "}\n"
        )

        leases_file = tmp_path / "plugins" / "leases" / "service.go"
        leases_file.parent.mkdir(parents=True)
        leases_file.write_text(
            "package leases\n\n"
            "type service struct {\n"
            "    api.UnimplementedLeasesServer\n"
            "}\n\n"
            "func (s *service) Create(ctx context.Context, req *CreateLeaseRequest) (*Lease, error) {\n"
            "    return nil, nil\n"
            "}\n"
        )

        go_method_syms = [
            Symbol(
                id=f"go:{containers_file}:6-8:service.Create:method",
                name="service.Create",
                kind="method",
                language="go",
                path=str(containers_file),
                span=Span(6, 8, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
            Symbol(
                id=f"go:{leases_file}:6-8:service.Create:method",
                name="service.Create",
                kind="method",
                language="go",
                path=str(leases_file),
                span=Span(6, 8, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
        ]

        result = link_grpc(tmp_path, existing_symbols=go_method_syms)

        impl_edges = [
            e for e in result.edges if e.edge_type == "implements" and (e.meta or {}).get("protocol") == "grpc"
        ]
        # Each method should produce exactly one edge to its own service's
        # Create RPC route (not to the unrelated service's Create).
        assert len(impl_edges) == 2, impl_edges

        route_by_id = {s.id: s for s in result.symbols}
        edges_by_src = {e.src: e for e in impl_edges}

        containers_method_id = go_method_syms[0].id
        leases_method_id = go_method_syms[1].id

        containers_route = route_by_id[edges_by_src[containers_method_id].dst]
        leases_route = route_by_id[edges_by_src[leases_method_id].dst]

        assert (containers_route.meta or {}).get("rpc_service") == "Containers"
        assert (leases_route.meta or {}).get("rpc_service") == "Leases"

    def test_no_link_when_proto_has_no_rpc_methods(self, tmp_path: Path) -> None:
        """No implements_rpc when proto service has no RPC methods."""
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.linkers.grpc import link_grpc

        # Proto with a service but no RPCs
        proto_file = tmp_path / "empty.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "service EmptyService {\n"
            "}\n"
        )

        go_file = tmp_path / "server.go"
        go_file.write_text(
            "package main\n\n"
            "type myServer struct {\n"
            "    UnimplementedEmptyServiceServer\n"
            "}\n"
        )

        go_syms = [
            Symbol(
                id=f"go:{go_file}:3-5:myServer.SomeMethod:method",
                name="myServer.SomeMethod",
                kind="method",
                language="go",
                path=str(go_file),
                span=Span(3, 5, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
        ]

        result = link_grpc(tmp_path, existing_symbols=go_syms)
        impl_edges = [e for e in result.edges if e.edge_type == "implements" and (e.meta or {}).get("protocol") == "grpc"]
        assert len(impl_edges) == 0

    def test_multiple_services_link_correctly(self, tmp_path: Path) -> None:
        """Each server struct links to its own service's RPCs."""
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "api.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package api;\n"
            "service UserService {\n"
            "    rpc GetUser(Req) returns (Resp);\n"
            "}\n"
            "service OrderService {\n"
            "    rpc PlaceOrder(Req) returns (Resp);\n"
            "}\n"
        )

        go_file = tmp_path / "servers.go"
        go_file.write_text(
            "package main\n\n"
            "type userServer struct {\n"
            "    UnimplementedUserServiceServer\n"
            "}\n\n"
            "func (s *userServer) GetUser(ctx context.Context, req *Req) (*Resp, error) {\n"
            "    return nil, nil\n"
            "}\n\n"
            "type orderServer struct {\n"
            "    UnimplementedOrderServiceServer\n"
            "}\n\n"
            "func (s *orderServer) PlaceOrder(ctx context.Context, req *Req) (*Resp, error) {\n"
            "    return nil, nil\n"
            "}\n"
        )

        go_method_syms = [
            Symbol(
                id=f"go:{go_file}:7-9:userServer.GetUser:method",
                name="userServer.GetUser",
                kind="method",
                language="go",
                path=str(go_file),
                span=Span(7, 9, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
            Symbol(
                id=f"go:{go_file}:15-17:orderServer.PlaceOrder:method",
                name="orderServer.PlaceOrder",
                kind="method",
                language="go",
                path=str(go_file),
                span=Span(15, 17, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
        ]

        result = link_grpc(tmp_path, existing_symbols=go_method_syms)

        impl_edges = [e for e in result.edges if e.edge_type == "implements" and (e.meta or {}).get("protocol") == "grpc"]
        assert len(impl_edges) == 2

        # userServer.GetUser → GetUser RPC, orderServer.PlaceOrder → PlaceOrder RPC
        for edge in impl_edges:
            src_method = edge.src.split(":")[-2].split(".")[-1]
            dst_route = next(
                s for s in result.symbols
                if (s.meta or {}).get("framework_role") == "route" and s.id == edge.dst
            )
            assert src_method in dst_route.meta["rpc_method"]


class TestGrpcNestedStructBraces:
    """Tests for Go structs with nested braces (e.g., chan struct{}).

    Real-world Go gRPC servers often embed UnimplementedXxxServer in structs
    that also contain fields with nested braces like ``done chan struct{}``.
    The regex must handle this correctly.
    """

    def test_struct_with_nested_struct_field(self, tmp_path: Path) -> None:
        """Detects Unimplemented embedding after chan struct{} field."""
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "cache.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package cache;\n"
            "service CacheService {\n"
            "    rpc Config(Empty) returns (VMConfig);\n"
            "}\n"
        )

        go_file = tmp_path / "server.go"
        go_file.write_text(
            "package main\n\n"
            "type cacheServer struct {\n"
            "    rpc     *grpc.Server\n"
            "    factory Factory\n"
            "    done    chan struct{}\n"
            "    UnimplementedCacheServiceServer\n"
            "}\n\n"
            "func (s *cacheServer) Config(ctx context.Context, empty *emptypb.Empty) (*pb.VMConfig, error) {\n"
            "    return nil, nil\n"
            "}\n"
        )

        go_method_syms = [
            Symbol(
                id=f"go:{go_file}:10-12:cacheServer.Config:method",
                name="cacheServer.Config",
                kind="method",
                language="go",
                path=str(go_file),
                span=Span(10, 12, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
        ]

        result = link_grpc(tmp_path, existing_symbols=go_method_syms)

        impl_edges = [e for e in result.edges if e.edge_type == "implements" and (e.meta or {}).get("protocol") == "grpc"]
        assert len(impl_edges) == 1
        assert "cacheServer.Config" in impl_edges[0].src

    def test_struct_with_package_prefixed_unimplemented(self, tmp_path: Path) -> None:
        """Detects pb.UnimplementedXxxServer (package-prefixed embedding)."""
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "cache.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package cache;\n"
            "service CacheService {\n"
            "    rpc Status(Empty) returns (StatusReply);\n"
            "}\n"
        )

        go_file = tmp_path / "factory.go"
        go_file.write_text(
            "package main\n\n"
            "type cacheServer struct {\n"
            "    rpc     *grpc.Server\n"
            "    factory Factory\n"
            "    done    chan struct{}\n"
            "    pb.UnimplementedCacheServiceServer\n"
            "}\n\n"
            "func (s *cacheServer) Status(ctx context.Context, empty *emptypb.Empty) (*pb.StatusReply, error) {\n"
            "    return nil, nil\n"
            "}\n"
        )

        go_method_syms = [
            Symbol(
                id=f"go:{go_file}:10-12:cacheServer.Status:method",
                name="cacheServer.Status",
                kind="method",
                language="go",
                path=str(go_file),
                span=Span(10, 12, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
        ]

        result = link_grpc(tmp_path, existing_symbols=go_method_syms)

        impl_edges = [e for e in result.edges if e.edge_type == "implements" and (e.meta or {}).get("protocol") == "grpc"]
        assert len(impl_edges) == 1
        assert "cacheServer.Status" in impl_edges[0].src

    def test_struct_with_both_issues(self, tmp_path: Path) -> None:
        """Handles nested struct{} AND package-prefixed embedding together."""
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "agent.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package agent;\n"
            "service AgentService {\n"
            "    rpc Start(StartRequest) returns (StartReply);\n"
            "    rpc Stop(StopRequest) returns (StopReply);\n"
            "}\n"
        )

        go_file = tmp_path / "handler.go"
        go_file.write_text(
            "package main\n\n"
            "type agentHandler struct {\n"
            "    mu      sync.Mutex\n"
            "    notify  chan struct{}\n"
            "    config  map[string]struct{ enabled bool }\n"
            "    pb.UnimplementedAgentServiceServer\n"
            "}\n\n"
            "func (h *agentHandler) Start(ctx context.Context, req *pb.StartRequest) (*pb.StartReply, error) {\n"
            "    return nil, nil\n"
            "}\n\n"
            "func (h *agentHandler) Stop(ctx context.Context, req *pb.StopRequest) (*pb.StopReply, error) {\n"
            "    return nil, nil\n"
            "}\n"
        )

        go_method_syms = [
            Symbol(
                id=f"go:{go_file}:10-12:agentHandler.Start:method",
                name="agentHandler.Start",
                kind="method",
                language="go",
                path=str(go_file),
                span=Span(10, 12, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
            Symbol(
                id=f"go:{go_file}:14-16:agentHandler.Stop:method",
                name="agentHandler.Stop",
                kind="method",
                language="go",
                path=str(go_file),
                span=Span(14, 16, 0, 0),
                origin="go",
                origin_run_id="test",
            ),
        ]

        result = link_grpc(tmp_path, existing_symbols=go_method_syms)

        impl_edges = [e for e in result.edges if e.edge_type == "implements" and (e.meta or {}).get("protocol") == "grpc"]
        assert len(impl_edges) == 2
        src_methods = sorted(e.src.split(":")[-2] for e in impl_edges)
        assert src_methods == ["agentHandler.Start", "agentHandler.Stop"]


class TestGrpcLinkerRequirements:
    """Tests for gRPC linker registry requirements."""

    def test_count_proto_files(self, tmp_path: Path) -> None:
        """Counts .proto files in the repository."""
        from hypergumbo_core.linkers.grpc import _count_proto_files
        from hypergumbo_core.linkers.registry import LinkerContext

        # Create some proto files
        (tmp_path / "user.proto").write_text("service UserService {}")
        (tmp_path / "order.proto").write_text("service OrderService {}")
        (tmp_path / "app.py").write_text("print('hello')")

        ctx = LinkerContext(repo_root=tmp_path)
        count = _count_proto_files(ctx)

        assert count == 2

    def test_count_grpc_patterns_go_registration(self, tmp_path: Path) -> None:
        """Counts Go gRPC registration patterns in symbols."""
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.linkers.grpc import _count_grpc_patterns_in_symbols
        from hypergumbo_core.linkers.registry import LinkerContext

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
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.linkers.grpc import _count_grpc_patterns_in_symbols
        from hypergumbo_core.linkers.registry import LinkerContext

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
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.linkers.grpc import _count_grpc_patterns_in_symbols
        from hypergumbo_core.linkers.registry import LinkerContext

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
        import hypergumbo_core.linkers.grpc
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("grpc-linker")
        assert linker is not None
        assert linker.name == "grpc-linker"
        assert linker.priority == 30

    def test_grpc_linker_returns_result(self, tmp_path: Path) -> None:
        """grpc_linker function returns LinkerResult."""
        from hypergumbo_core.linkers.grpc import grpc_linker
        from hypergumbo_core.linkers.registry import LinkerContext

        ctx = LinkerContext(repo_root=tmp_path)
        result = grpc_linker(ctx)

        assert result is not None
        assert hasattr(result, "symbols")
        assert hasattr(result, "edges")


class TestGrpcUnresolvedEdgeResolution:
    """Tests for resolving unresolved gRPC edges."""

    def test_resolves_unresolved_register_server_edge(self, tmp_path: Path) -> None:
        """Resolves unresolved edges to RegisterXxxServer functions."""
        from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
        from hypergumbo_core.linkers.grpc import _resolve_unresolved_grpc_edges
        from hypergumbo_core.linkers.registry import LinkerContext

        # Create an unresolved edge from Go analyzer
        unresolved_edge = Edge.create(
            src="go:main.go:10-20:main:function",
            dst="go:github.com/pkg/pb:0-0:RegisterUserServer:unresolved",
            edge_type="calls",
            line=15,
            confidence=0.5,
            origin="go-analyzer",
            origin_run_id="test",
            evidence_type="method_call",
            is_resolved=False,
        )

        # Create a symbol that can resolve the edge
        register_sym = Symbol(
            id="grpc:user_grpc.pb.go:100:UserService:grpc_server",
            name="RegisterUserServer",
            kind="class",
            language="go",
            path=str(tmp_path / "pkg/pb/user_grpc.pb.go"),
            span=Span(100, 110, 0, 0),
            origin="grpc-linker",
            origin_run_id="test",
            meta={"framework_role": "grpc_server"},
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            edges=[unresolved_edge],
            symbols=[],
        )
        run = AnalysisRun.create("grpc-linker", "test")

        resolved = _resolve_unresolved_grpc_edges(ctx, [register_sym], run)

        assert len(resolved) == 1
        assert resolved[0].src == unresolved_edge.src
        assert resolved[0].dst == register_sym.id
        assert resolved[0].edge_type == "calls"

    def test_ignores_non_grpc_unresolved_edges(self, tmp_path: Path) -> None:
        """Ignores unresolved edges that don't match gRPC patterns."""
        from hypergumbo_core.ir import AnalysisRun, Edge
        from hypergumbo_core.linkers.grpc import _resolve_unresolved_grpc_edges
        from hypergumbo_core.linkers.registry import LinkerContext

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
        run = AnalysisRun.create("grpc-linker", "test")

        resolved = _resolve_unresolved_grpc_edges(ctx, [], run)

        # Should not resolve non-gRPC patterns
        assert len(resolved) == 0

    def test_prefers_package_matching_candidate(self, tmp_path: Path) -> None:
        """Prefers symbol whose path matches the package hint."""
        from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
        from hypergumbo_core.linkers.grpc import _resolve_unresolved_grpc_edges
        from hypergumbo_core.linkers.registry import LinkerContext

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
            kind="class",
            language="go",
            path=str(tmp_path / "frontend/pb/grpc.pb.go"),
            span=Span(100, 110, 0, 0),
            origin="grpc-linker",
            origin_run_id="test",
            meta={"framework_role": "grpc_server"},
        )
        correct_sym = Symbol(
            id="grpc:checkout/pb/grpc.pb.go:100:Checkout:grpc_server",
            name="RegisterCheckoutServer",
            kind="class",
            language="go",
            path=str(tmp_path / "checkout/pb/grpc.pb.go"),
            span=Span(100, 110, 0, 0),
            origin="grpc-linker",
            origin_run_id="test",
            meta={"framework_role": "grpc_server"},
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            edges=[unresolved_edge],
            symbols=[],
        )
        run = AnalysisRun.create("grpc-linker", "test")

        # Pass wrong_sym first to ensure we're not just picking first match
        resolved = _resolve_unresolved_grpc_edges(ctx, [wrong_sym, correct_sym], run)

        assert len(resolved) == 1
        # Should prefer the one matching package hint (checkout/pb)
        assert resolved[0].dst == correct_sym.id

    def test_resolves_using_ctx_symbols(self, tmp_path: Path) -> None:
        """_resolve_unresolved_grpc_edges also looks up symbols from ctx.symbols."""
        from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
        from hypergumbo_core.linkers.grpc import _resolve_unresolved_grpc_edges
        from hypergumbo_core.linkers.registry import LinkerContext

        # Create an unresolved edge from Go analyzer
        unresolved_edge = Edge.create(
            src="go:main.go:10-20:main:function",
            dst="go:github.com/pkg/pb:0-0:RegisterUserServer:unresolved",
            edge_type="calls",
            line=15,
            confidence=0.5,
            origin="go-analyzer",
            origin_run_id="test",
        )

        # Create a symbol that can resolve the edge - placed in ctx.symbols
        register_sym = Symbol(
            id="grpc:user_grpc.pb.go:100:UserService:grpc_server",
            name="RegisterUserServer",
            kind="class",
            language="go",
            path=str(tmp_path / "pkg/pb/user_grpc.pb.go"),
            span=Span(100, 110, 0, 0),
            origin="grpc-linker",
            origin_run_id="test",
            meta={"framework_role": "grpc_server"},
        )

        # Pass the symbol via ctx.symbols instead of the second argument
        ctx = LinkerContext(
            repo_root=tmp_path,
            edges=[unresolved_edge],
            symbols=[register_sym],  # Symbol is in ctx.symbols
        )
        run = AnalysisRun.create("grpc-linker", "test")

        # Empty linker_symbols, so ctx.symbols is the only source
        resolved = _resolve_unresolved_grpc_edges(ctx, [], run)

        assert len(resolved) == 1
        assert resolved[0].src == unresolved_edge.src
        assert resolved[0].dst == register_sym.id


class TestTransitiveStructEmbedding:
    """WI-pogus: Go gRPC ttrpc detection walks transitive struct embedding.

    The Go analyzer encodes embedded structs in ``meta.base_classes``.
    A struct that embeds an in-tree intermediate (e.g. ``BaseFooImpl``
    extending ``UnimplementedFooServer`` or implementing ``HealthService``)
    must be detected the same as a direct embedder.
    """

    def test_one_intermediate_chain(self, tmp_path: Path) -> None:
        """LeafImpl extends BaseImpl which embeds UnimplementedHealthServer.

        Go's UnimplementedXxxServer detection has its own regex-based
        file scan; this test exercises the base_classes-based fallback
        path used for ttrpc / CSI patterns where the embedded type is
        an interface name (``HealthService``).
        """
        from hypergumbo_core.ir import Edge, Span, Symbol
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "health.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package grpc;\n"
            "service Health {\n"
            "    rpc Check(HealthCheckRequest) returns (HealthCheckResponse);\n"
            "}\n"
        )

        # In-tree intermediate that implements HealthService directly.
        base_struct = Symbol(
            id="go:base.go:1-5:BaseHealthImpl:struct",
            name="BaseHealthImpl", kind="struct", language="go",
            path="base.go", span=Span(1, 5, 0, 0),
            origin="go", origin_run_id="test",
            meta={"base_classes": ["HealthService"]},
        )
        # Leaf struct embeds the intermediate but not the framework type.
        leaf_struct = Symbol(
            id="go:leaf.go:1-5:UserHealth:struct",
            name="UserHealth", kind="struct", language="go",
            path="leaf.go", span=Span(1, 5, 0, 0),
            origin="go", origin_run_id="test",
            meta={"base_classes": ["BaseHealthImpl"]},
        )
        check = Symbol(
            id="go:leaf.go:10-12:UserHealth.Check:method",
            name="UserHealth.Check", kind="method", language="go",
            path="leaf.go", span=Span(10, 12, 0, 0),
            origin="go", origin_run_id="test",
        )
        edge = Edge.create(
            src=leaf_struct.id, dst=base_struct.id,
            edge_type="extends", line=1,

            origin="test", origin_run_id="test",
        )
        result = link_grpc(
            tmp_path,
            existing_symbols=[base_struct, leaf_struct, check],
            existing_edges=[edge],
        )
        impl_edges = [e for e in result.edges if e.edge_type == "implements" and (e.meta or {}).get("protocol") == "grpc"]
        assert any(check.id == e.src for e in impl_edges), (
            f"UserHealth.Check should implement Health.Check transitively; got "
            f"{[(e.src, e.dst) for e in impl_edges]}"
        )

    def test_two_intermediate_chain(self, tmp_path: Path) -> None:
        """Three-level chain: Leaf → Mid → Base implements HealthService."""
        from hypergumbo_core.ir import Edge, Span, Symbol
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "health.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package grpc;\n"
            "service Health {\n"
            "    rpc Check(HealthCheckRequest) returns (HealthCheckResponse);\n"
            "}\n"
        )

        base_struct = Symbol(
            id="go:base.go:1-5:BaseHealth:struct",
            name="BaseHealth", kind="struct", language="go",
            path="base.go", span=Span(1, 5, 0, 0),
            origin="go", origin_run_id="test",
            meta={"base_classes": ["HealthService"]},
        )
        mid_struct = Symbol(
            id="go:mid.go:1-5:MidHealth:struct",
            name="MidHealth", kind="struct", language="go",
            path="mid.go", span=Span(1, 5, 0, 0),
            origin="go", origin_run_id="test",
            meta={"base_classes": ["BaseHealth"]},
        )
        leaf_struct = Symbol(
            id="go:leaf.go:1-5:UserHealth:struct",
            name="UserHealth", kind="struct", language="go",
            path="leaf.go", span=Span(1, 5, 0, 0),
            origin="go", origin_run_id="test",
            meta={"base_classes": ["MidHealth"]},
        )
        check = Symbol(
            id="go:leaf.go:10-12:UserHealth.Check:method",
            name="UserHealth.Check", kind="method", language="go",
            path="leaf.go", span=Span(10, 12, 0, 0),
            origin="go", origin_run_id="test",
        )
        edges = [
            Edge.create(src=leaf_struct.id, dst=mid_struct.id, edge_type="extends", line=1, origin="test", origin_run_id="test"),
            Edge.create(src=mid_struct.id, dst=base_struct.id, edge_type="extends", line=1, origin="test", origin_run_id="test"),
        ]
        result = link_grpc(
            tmp_path,
            existing_symbols=[base_struct, mid_struct, leaf_struct, check],
            existing_edges=edges,
        )
        impl_edges = [e for e in result.edges if e.edge_type == "implements" and (e.meta or {}).get("protocol") == "grpc"]
        assert any(check.id == e.src for e in impl_edges)

    def test_diamond_inheritance_cycle_guarded(self, tmp_path: Path) -> None:
        """Two paths from leaf to a HealthService ancestor — terminates without double-emit."""
        from hypergumbo_core.ir import Edge, Span, Symbol
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "health.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package grpc;\n"
            "service Health {\n"
            "    rpc Check(HealthCheckRequest) returns (HealthCheckResponse);\n"
            "}\n"
        )

        base_struct = Symbol(
            id="go:base.go:1-5:BaseHealth:struct",
            name="BaseHealth", kind="struct", language="go",
            path="base.go", span=Span(1, 5, 0, 0),
            origin="go", origin_run_id="test",
            meta={"base_classes": ["HealthService"]},
        )
        left_struct = Symbol(
            id="go:left.go:1-5:LeftHealth:struct",
            name="LeftHealth", kind="struct", language="go",
            path="left.go", span=Span(1, 5, 0, 0),
            origin="go", origin_run_id="test",
            meta={"base_classes": ["BaseHealth"]},
        )
        right_struct = Symbol(
            id="go:right.go:1-5:RightHealth:struct",
            name="RightHealth", kind="struct", language="go",
            path="right.go", span=Span(1, 5, 0, 0),
            origin="go", origin_run_id="test",
            meta={"base_classes": ["BaseHealth"]},
        )
        leaf_struct = Symbol(
            id="go:leaf.go:1-5:UserHealth:struct",
            name="UserHealth", kind="struct", language="go",
            path="leaf.go", span=Span(1, 5, 0, 0),
            origin="go", origin_run_id="test",
            meta={"base_classes": ["LeftHealth", "RightHealth"]},
        )
        check = Symbol(
            id="go:leaf.go:10-12:UserHealth.Check:method",
            name="UserHealth.Check", kind="method", language="go",
            path="leaf.go", span=Span(10, 12, 0, 0),
            origin="go", origin_run_id="test",
        )
        edges = [
            Edge.create(src=leaf_struct.id, dst=left_struct.id, edge_type="extends", line=1, origin="test", origin_run_id="test"),
            Edge.create(src=leaf_struct.id, dst=right_struct.id, edge_type="extends", line=1, origin="test", origin_run_id="test"),
            Edge.create(src=left_struct.id, dst=base_struct.id, edge_type="extends", line=1, origin="test", origin_run_id="test"),
            Edge.create(src=right_struct.id, dst=base_struct.id, edge_type="extends", line=1, origin="test", origin_run_id="test"),
        ]
        result = link_grpc(
            tmp_path,
            existing_symbols=[base_struct, left_struct, right_struct, leaf_struct, check],
            existing_edges=edges,
        )
        impl_edges = [e for e in result.edges if e.edge_type == "implements" and (e.meta or {}).get("protocol") == "grpc"]
        # Exactly one edge from UserHealth.Check (no double-emit through diamond).
        edges_from_check = [e for e in impl_edges if e.src == check.id]
        assert len(edges_from_check) == 1

    def test_direct_embedding_regression_unaffected(self, tmp_path: Path) -> None:
        """Direct ttrpc HealthService embedding still works without edges."""
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.linkers.grpc import link_grpc

        proto_file = tmp_path / "health.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "package grpc;\n"
            "service Health {\n"
            "    rpc Check(HealthCheckRequest) returns (HealthCheckResponse);\n"
            "}\n"
        )
        leaf_struct = Symbol(
            id="go:leaf.go:1-5:UserHealth:struct",
            name="UserHealth", kind="struct", language="go",
            path="leaf.go", span=Span(1, 5, 0, 0),
            origin="go", origin_run_id="test",
            meta={"base_classes": ["HealthService"]},
        )
        check = Symbol(
            id="go:leaf.go:10-12:UserHealth.Check:method",
            name="UserHealth.Check", kind="method", language="go",
            path="leaf.go", span=Span(10, 12, 0, 0),
            origin="go", origin_run_id="test",
        )
        result = link_grpc(tmp_path, existing_symbols=[leaf_struct, check])
        impl_edges = [e for e in result.edges if e.edge_type == "implements" and (e.meta or {}).get("protocol") == "grpc"]
        assert any(check.id == e.src for e in impl_edges)
