"""Tests for Protocol Buffers (Proto) analysis pass.

Tests verify that the Proto analyzer correctly extracts:
- Service definitions (for gRPC)
- RPC method definitions
- Message definitions
- Enum definitions
- Import relationships
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo.analyze import proto as proto_module
from hypergumbo.analyze.proto import (
    analyze_proto,
    find_proto_files,
    is_proto_tree_sitter_available,
)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository for testing."""
    return tmp_path


class TestFindProtoFiles:
    """Tests for find_proto_files function."""

    def test_finds_proto_files(self, temp_repo: Path) -> None:
        """Finds .proto files in repo."""
        (temp_repo / "user.proto").write_text('syntax = "proto3";')
        (temp_repo / "api.proto").write_text('syntax = "proto3";')
        (temp_repo / "README.md").write_text("# Docs")

        files = list(find_proto_files(temp_repo))
        filenames = {f.name for f in files}

        assert "user.proto" in filenames
        assert "api.proto" in filenames
        assert "README.md" not in filenames

    def test_finds_nested_proto_files(self, temp_repo: Path) -> None:
        """Finds .proto files in subdirectories."""
        protos = temp_repo / "proto"
        protos.mkdir()
        (protos / "user.proto").write_text('syntax = "proto3";')

        files = list(find_proto_files(temp_repo))

        assert len(files) == 1
        assert files[0].name == "user.proto"


class TestProtoTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_availability_check_runs(self) -> None:
        """Availability check returns a boolean."""
        result = is_proto_tree_sitter_available()
        assert isinstance(result, bool)


class TestProtoAnalysis:
    """Tests for Proto analysis with tree-sitter."""

    def test_analyzes_service(self, temp_repo: Path) -> None:
        """Detects service declarations."""
        (temp_repo / "user.proto").write_text('''
syntax = "proto3";

package myservice.v1;

service UserService {
  rpc GetUser(GetUserRequest) returns (GetUserResponse);
}
''')

        result = analyze_proto(temp_repo)

        assert not result.skipped
        assert any(s.kind == "service" and s.name == "UserService" for s in result.symbols)

    def test_analyzes_rpc_methods(self, temp_repo: Path) -> None:
        """Detects RPC method definitions within services."""
        (temp_repo / "user.proto").write_text('''
syntax = "proto3";

service UserService {
  rpc GetUser(GetUserRequest) returns (GetUserResponse);
  rpc CreateUser(CreateUserRequest) returns (User);
  rpc ListUsers(ListUsersRequest) returns (stream User);
}
''')

        result = analyze_proto(temp_repo)

        rpc_names = {s.name for s in result.symbols if s.kind == "rpc"}
        assert "GetUser" in rpc_names
        assert "CreateUser" in rpc_names
        assert "ListUsers" in rpc_names

    def test_analyzes_message(self, temp_repo: Path) -> None:
        """Detects message declarations."""
        (temp_repo / "user.proto").write_text('''
syntax = "proto3";

message User {
  string id = 1;
  string name = 2;
  string email = 3;
}
''')

        result = analyze_proto(temp_repo)

        assert any(s.kind == "message" and s.name == "User" for s in result.symbols)

    def test_analyzes_nested_message(self, temp_repo: Path) -> None:
        """Detects nested message declarations."""
        (temp_repo / "user.proto").write_text('''
syntax = "proto3";

message UserResponse {
  message Data {
    string id = 1;
  }
  Data data = 1;
}
''')

        result = analyze_proto(temp_repo)

        # Both outer and nested messages should be detected
        message_names = {s.name for s in result.symbols if s.kind == "message"}
        assert "UserResponse" in message_names
        assert "Data" in message_names

    def test_analyzes_nested_enum(self, temp_repo: Path) -> None:
        """Detects nested enum declarations inside messages."""
        (temp_repo / "user.proto").write_text('''
syntax = "proto3";

message User {
  enum Status {
    STATUS_UNSPECIFIED = 0;
    STATUS_ACTIVE = 1;
  }
  string id = 1;
  Status status = 2;
}
''')

        result = analyze_proto(temp_repo)

        # Both message and nested enum should be detected
        message_names = {s.name for s in result.symbols if s.kind == "message"}
        enum_names = {s.name for s in result.symbols if s.kind == "enum"}
        assert "User" in message_names
        assert "Status" in enum_names

    def test_analyzes_enum(self, temp_repo: Path) -> None:
        """Detects enum declarations."""
        (temp_repo / "status.proto").write_text('''
syntax = "proto3";

enum UserStatus {
  USER_STATUS_UNSPECIFIED = 0;
  USER_STATUS_ACTIVE = 1;
  USER_STATUS_INACTIVE = 2;
}
''')

        result = analyze_proto(temp_repo)

        assert any(s.kind == "enum" and s.name == "UserStatus" for s in result.symbols)

    def test_analyzes_imports(self, temp_repo: Path) -> None:
        """Detects import statements and creates edges."""
        (temp_repo / "user.proto").write_text('''
syntax = "proto3";

import "google/protobuf/timestamp.proto";
import "common/types.proto";

message User {
  google.protobuf.Timestamp created_at = 1;
}
''')

        result = analyze_proto(temp_repo)

        # Check import edges exist
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 2

    def test_rpc_contains_request_response_types(self, temp_repo: Path) -> None:
        """RPC methods should have request/response types in meta or signature."""
        (temp_repo / "user.proto").write_text('''
syntax = "proto3";

service UserService {
  rpc GetUser(GetUserRequest) returns (GetUserResponse);
}
''')

        result = analyze_proto(temp_repo)

        rpc_sym = next(s for s in result.symbols if s.kind == "rpc" and s.name == "GetUser")
        # The signature should include the request and response types
        assert rpc_sym.signature is not None
        assert "GetUserRequest" in rpc_sym.signature
        assert "GetUserResponse" in rpc_sym.signature

    def test_streaming_rpc(self, temp_repo: Path) -> None:
        """Detects streaming RPC methods."""
        (temp_repo / "user.proto").write_text('''
syntax = "proto3";

service StreamService {
  rpc ServerStream(Request) returns (stream Response);
  rpc ClientStream(stream Request) returns (Response);
  rpc BiDirectional(stream Request) returns (stream Response);
}
''')

        result = analyze_proto(temp_repo)

        rpcs = {s.name: s for s in result.symbols if s.kind == "rpc"}
        assert "ServerStream" in rpcs
        assert "ClientStream" in rpcs
        assert "BiDirectional" in rpcs

        # Streaming info should be captured in signature
        assert "stream" in rpcs["ServerStream"].signature.lower()

    def test_service_has_package_in_canonical_name(self, temp_repo: Path) -> None:
        """Service canonical_name should include package."""
        (temp_repo / "user.proto").write_text('''
syntax = "proto3";

package myservice.v1;

service UserService {
  rpc GetUser(GetUserRequest) returns (GetUserResponse);
}
''')

        result = analyze_proto(temp_repo)

        service = next(s for s in result.symbols if s.kind == "service")
        assert "myservice.v1" in service.canonical_name

    def test_multiple_services_in_file(self, temp_repo: Path) -> None:
        """Handles multiple services in a single file."""
        (temp_repo / "api.proto").write_text('''
syntax = "proto3";

service UserService {
  rpc GetUser(GetUserRequest) returns (GetUserResponse);
}

service ProductService {
  rpc GetProduct(GetProductRequest) returns (GetProductResponse);
}
''')

        result = analyze_proto(temp_repo)

        services = {s.name for s in result.symbols if s.kind == "service"}
        assert "UserService" in services
        assert "ProductService" in services


class TestProtoAnalysisUnavailable:
    """Tests for handling unavailable tree-sitter."""

    def test_skipped_when_unavailable(self, temp_repo: Path) -> None:
        """Returns skipped result when tree-sitter unavailable."""
        (temp_repo / "user.proto").write_text('syntax = "proto3";')

        with patch.object(proto_module, "is_proto_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Proto analysis skipped"):
                result = proto_module.analyze_proto(temp_repo)

        assert result.skipped is True


class TestProtoEdges:
    """Tests for Proto edge detection."""

    def test_service_to_rpc_relationship(self, temp_repo: Path) -> None:
        """RPCs should be linked to their parent service."""
        (temp_repo / "user.proto").write_text('''
syntax = "proto3";

service UserService {
  rpc GetUser(GetUserRequest) returns (GetUserResponse);
}
''')

        result = analyze_proto(temp_repo)

        # Find service and RPC
        service = next(s for s in result.symbols if s.kind == "service")
        rpc = next(s for s in result.symbols if s.kind == "rpc")

        # There should be a contains edge from service to rpc
        contains_edges = [e for e in result.edges if e.edge_type == "contains"]
        assert any(e.src == service.id and e.dst == rpc.id for e in contains_edges)


class TestProtoAnalysisRun:
    """Tests for Proto analysis run metadata."""

    def test_analysis_run_created(self, temp_repo: Path) -> None:
        """Analysis run is created with correct metadata."""
        (temp_repo / "user.proto").write_text('''
syntax = "proto3";
message User {}
''')

        result = analyze_proto(temp_repo)

        assert result.run is not None
        assert result.run.pass_id == "proto-v1"
        assert result.run.files_analyzed >= 1
