"""Branch coverage tests for proto.py analyzer.

Tests specific branch paths in the Protocol Buffers analyzer that may not be covered
by the main test suite. Focuses on:
- RPC signature extraction edge cases (streaming)
- Package name extraction edge cases
- Parent message name resolution
- Empty file handling
- Multiple imports
"""
from pathlib import Path

from hypergumbo_lang_common.proto import (
    _make_file_id,
    _make_symbol_id,
    _node_text,
    analyze_proto,
    find_proto_files,
)


def make_proto_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Proto file with given content."""
    (tmp_path / name).write_text(content)


class TestProtoHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = _make_symbol_id("api/user.proto", 10, 20, "UserService", "service")
        assert symbol_id == "proto:api/user.proto:10-20:UserService:service"

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = _make_file_id("api/user.proto")
        assert file_id == "proto:api/user.proto:1-1:file:file"


class TestRPCSignatureExtraction:
    """Branch coverage for RPC signature extraction."""

    def test_client_streaming_rpc(self, tmp_path: Path) -> None:
        """Test RPC with client-side streaming."""
        make_proto_file(tmp_path, "stream.proto", '''
syntax = "proto3";

service StreamService {
  rpc ClientUpload(stream UploadRequest) returns (UploadResponse);
}
''')
        result = analyze_proto(tmp_path)
        rpcs = [s for s in result.symbols if s.kind == "rpc"]
        assert len(rpcs) == 1
        assert "stream UploadRequest" in rpcs[0].signature
        assert "UploadResponse" in rpcs[0].signature

    def test_server_streaming_rpc(self, tmp_path: Path) -> None:
        """Test RPC with server-side streaming."""
        make_proto_file(tmp_path, "stream.proto", '''
syntax = "proto3";

service StreamService {
  rpc ServerDownload(DownloadRequest) returns (stream DownloadChunk);
}
''')
        result = analyze_proto(tmp_path)
        rpcs = [s for s in result.symbols if s.kind == "rpc"]
        assert len(rpcs) == 1
        assert "DownloadRequest" in rpcs[0].signature
        assert "stream DownloadChunk" in rpcs[0].signature

    def test_bidirectional_streaming_rpc(self, tmp_path: Path) -> None:
        """Test RPC with bidirectional streaming."""
        make_proto_file(tmp_path, "stream.proto", '''
syntax = "proto3";

service ChatService {
  rpc Chat(stream ChatMessage) returns (stream ChatMessage);
}
''')
        result = analyze_proto(tmp_path)
        rpcs = [s for s in result.symbols if s.kind == "rpc"]
        assert len(rpcs) == 1
        # Both request and response should be streaming
        assert rpcs[0].signature.count("stream") == 2


class TestPackageNameExtraction:
    """Branch coverage for package name extraction."""

    def test_no_package_declaration(self, tmp_path: Path) -> None:
        """Test Proto file without package declaration."""
        make_proto_file(tmp_path, "simple.proto", '''
syntax = "proto3";

message SimpleMessage {
  string value = 1;
}
''')
        result = analyze_proto(tmp_path)
        messages = [s for s in result.symbols if s.kind == "message"]
        assert len(messages) == 1
        # Without package, canonical name should just be message name
        assert messages[0].canonical_name == "SimpleMessage"

    def test_nested_package_name(self, tmp_path: Path) -> None:
        """Test deeply nested package name."""
        make_proto_file(tmp_path, "api.proto", '''
syntax = "proto3";

package com.example.myservice.v1.api;

message DeepMessage {
  string id = 1;
}
''')
        result = analyze_proto(tmp_path)
        messages = [s for s in result.symbols if s.kind == "message"]
        assert len(messages) == 1
        assert "com.example.myservice.v1.api" in messages[0].canonical_name


class TestNestedTypes:
    """Branch coverage for nested message/enum handling."""

    def test_deeply_nested_message(self, tmp_path: Path) -> None:
        """Test deeply nested message structure."""
        make_proto_file(tmp_path, "nested.proto", '''
syntax = "proto3";

message Outer {
  message Middle {
    message Inner {
      string value = 1;
    }
  }
}
''')
        result = analyze_proto(tmp_path)
        messages = [s for s in result.symbols if s.kind == "message"]
        names = [m.name for m in messages]
        assert "Outer" in names
        assert "Middle" in names
        assert "Inner" in names

    def test_enum_inside_nested_message(self, tmp_path: Path) -> None:
        """Test enum nested inside a nested message."""
        make_proto_file(tmp_path, "nested.proto", '''
syntax = "proto3";

message Container {
  message Inner {
    enum Status {
      STATUS_UNKNOWN = 0;
      STATUS_OK = 1;
    }
  }
}
''')
        result = analyze_proto(tmp_path)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert len(enums) == 1
        assert enums[0].name == "Status"


class TestMultipleImports:
    """Branch coverage for import handling."""

    def test_multiple_google_imports(self, tmp_path: Path) -> None:
        """Test multiple Google protobuf imports."""
        make_proto_file(tmp_path, "api.proto", '''
syntax = "proto3";

import "google/protobuf/timestamp.proto";
import "google/protobuf/duration.proto";
import "google/protobuf/empty.proto";

message Event {
  google.protobuf.Timestamp created_at = 1;
  google.protobuf.Duration duration = 2;
}
''')
        result = analyze_proto(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 3

    def test_public_import(self, tmp_path: Path) -> None:
        """Test public import statement."""
        make_proto_file(tmp_path, "api.proto", '''
syntax = "proto3";

import public "common/types.proto";

message MyType {
  string id = 1;
}
''')
        result = analyze_proto(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        # Public import should still create edge
        assert len(import_edges) >= 1


class TestServiceContainsEdges:
    """Branch coverage for service-to-RPC contains edges."""

    def test_multiple_rpcs_in_service(self, tmp_path: Path) -> None:
        """Test service with multiple RPCs creates multiple contains edges."""
        make_proto_file(tmp_path, "api.proto", '''
syntax = "proto3";

service ApiService {
  rpc GetItem(GetItemRequest) returns (GetItemResponse);
  rpc ListItems(ListItemsRequest) returns (ListItemsResponse);
  rpc CreateItem(CreateItemRequest) returns (CreateItemResponse);
  rpc DeleteItem(DeleteItemRequest) returns (DeleteItemResponse);
}
''')
        result = analyze_proto(tmp_path)
        contains_edges = [e for e in result.edges if e.edge_type == "contains"]
        rpcs = [s for s in result.symbols if s.kind == "rpc"]
        # Should have one contains edge per RPC
        assert len(contains_edges) == len(rpcs)
        assert len(rpcs) == 4


class TestMultipleServicesInFile:
    """Branch coverage for multiple services handling."""

    def test_rpcs_linked_to_correct_service(self, tmp_path: Path) -> None:
        """Test RPCs are linked to their correct parent service."""
        make_proto_file(tmp_path, "services.proto", '''
syntax = "proto3";

service ServiceA {
  rpc MethodA(RequestA) returns (ResponseA);
}

service ServiceB {
  rpc MethodB(RequestB) returns (ResponseB);
}
''')
        result = analyze_proto(tmp_path)
        services = {s.name: s for s in result.symbols if s.kind == "service"}
        rpcs = {s.name: s for s in result.symbols if s.kind == "rpc"}
        contains_edges = [e for e in result.edges if e.edge_type == "contains"]

        # Check MethodA is linked to ServiceA
        method_a_edge = next(
            (e for e in contains_edges if e.dst == rpcs["MethodA"].id),
            None
        )
        assert method_a_edge is not None
        assert method_a_edge.src == services["ServiceA"].id

        # Check MethodB is linked to ServiceB
        method_b_edge = next(
            (e for e in contains_edges if e.dst == rpcs["MethodB"].id),
            None
        )
        assert method_b_edge is not None
        assert method_b_edge.src == services["ServiceB"].id


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_empty_proto_file(self, tmp_path: Path) -> None:
        """Test empty Proto file."""
        make_proto_file(tmp_path, "empty.proto", "")
        result = analyze_proto(tmp_path)
        assert result.run is not None
        assert len(result.symbols) == 0

    def test_syntax_only_file(self, tmp_path: Path) -> None:
        """Test Proto file with only syntax declaration."""
        make_proto_file(tmp_path, "syntax_only.proto", '''
syntax = "proto3";
''')
        result = analyze_proto(tmp_path)
        assert result.run is not None
        assert len(result.symbols) == 0

    def test_package_only_file(self, tmp_path: Path) -> None:
        """Test Proto file with only package declaration."""
        make_proto_file(tmp_path, "package_only.proto", '''
syntax = "proto3";
package myservice.v1;
''')
        result = analyze_proto(tmp_path)
        assert result.run is not None
        assert len(result.symbols) == 0


class TestRPCCanonicalNames:
    """Branch coverage for RPC canonical name generation."""

    def test_rpc_canonical_name_includes_service(self, tmp_path: Path) -> None:
        """Test RPC canonical name includes service prefix."""
        make_proto_file(tmp_path, "api.proto", '''
syntax = "proto3";

package myservice.v1;

service UserService {
  rpc GetUser(GetUserRequest) returns (GetUserResponse);
}
''')
        result = analyze_proto(tmp_path)
        rpc = next(s for s in result.symbols if s.kind == "rpc")
        # Canonical name should include package and service
        assert "myservice.v1" in rpc.canonical_name
        assert "UserService" in rpc.canonical_name
        assert "GetUser" in rpc.canonical_name


class TestMessageCanonicalNames:
    """Branch coverage for message canonical name generation."""

    def test_nested_message_canonical_name(self, tmp_path: Path) -> None:
        """Test nested message includes parent in canonical name."""
        make_proto_file(tmp_path, "nested.proto", '''
syntax = "proto3";

package myservice.v1;

message Response {
  message Data {
    string id = 1;
  }
}
''')
        result = analyze_proto(tmp_path)
        data_msg = next(s for s in result.symbols if s.name == "Data")
        # Canonical name should include package and parent message
        assert "myservice.v1" in data_msg.canonical_name
        assert "Response" in data_msg.canonical_name
        assert "Data" in data_msg.canonical_name


class TestFindProtoFilesEdgeCases:
    """Branch coverage for file discovery."""

    def test_excludes_vendor_directory(self, tmp_path: Path) -> None:
        """Test vendor directories are excluded."""
        vendor = tmp_path / "vendor" / "google"
        vendor.mkdir(parents=True)
        (vendor / "timestamp.proto").write_text('syntax = "proto3";')
        (tmp_path / "api.proto").write_text('syntax = "proto3";')

        files = list(find_proto_files(tmp_path))
        filenames = [f.name for f in files]
        assert "api.proto" in filenames
        # Vendor proto should be excluded
        assert "timestamp.proto" not in filenames

    def test_finds_in_nested_directories(self, tmp_path: Path) -> None:
        """Test finding protos in deeply nested directories."""
        deep = tmp_path / "api" / "v1" / "proto" / "internal"
        deep.mkdir(parents=True)
        (deep / "internal.proto").write_text('syntax = "proto3";')

        files = list(find_proto_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "internal.proto"


class TestEnumExtraction:
    """Branch coverage for enum extraction."""

    def test_top_level_enum(self, tmp_path: Path) -> None:
        """Test top-level enum extraction."""
        make_proto_file(tmp_path, "enums.proto", '''
syntax = "proto3";

package myservice.v1;

enum Status {
  STATUS_UNSPECIFIED = 0;
  STATUS_ACTIVE = 1;
  STATUS_INACTIVE = 2;
}
''')
        result = analyze_proto(tmp_path)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert len(enums) == 1
        assert enums[0].name == "Status"
        assert "myservice.v1" in enums[0].canonical_name

    def test_multiple_enums(self, tmp_path: Path) -> None:
        """Test multiple enum definitions."""
        make_proto_file(tmp_path, "enums.proto", '''
syntax = "proto3";

enum Color {
  COLOR_UNSPECIFIED = 0;
  COLOR_RED = 1;
}

enum Size {
  SIZE_UNSPECIFIED = 0;
  SIZE_SMALL = 1;
}
''')
        result = analyze_proto(tmp_path)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert len(enums) == 2
        names = [e.name for e in enums]
        assert "Color" in names
        assert "Size" in names
