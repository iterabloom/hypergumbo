"""Tests for Apache Thrift analysis pass.

Tests verify that the Thrift analyzer correctly extracts:
- Service definitions
- Function definitions (RPC methods)
- Struct definitions
- Enum definitions
- Typedef definitions
- Const definitions
- Include statements
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo.analyze import thrift as thrift_module
from hypergumbo.analyze.thrift import (
    analyze_thrift,
    find_thrift_files,
    is_thrift_tree_sitter_available,
)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository for testing."""
    return tmp_path


class TestFindThriftFiles:
    """Tests for find_thrift_files function."""

    def test_finds_thrift_files(self, temp_repo: Path) -> None:
        """Finds .thrift files in repo."""
        (temp_repo / "user.thrift").write_text("service UserService {}")
        (temp_repo / "api.thrift").write_text("service ApiService {}")
        (temp_repo / "README.md").write_text("# Docs")

        files = list(find_thrift_files(temp_repo))
        filenames = {f.name for f in files}

        assert "user.thrift" in filenames
        assert "api.thrift" in filenames
        assert "README.md" not in filenames

    def test_finds_nested_thrift_files(self, temp_repo: Path) -> None:
        """Finds .thrift files in subdirectories."""
        idl = temp_repo / "idl"
        idl.mkdir()
        (idl / "user.thrift").write_text("service UserService {}")

        files = list(find_thrift_files(temp_repo))

        assert len(files) == 1
        assert files[0].name == "user.thrift"


class TestThriftTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_availability_check_runs(self) -> None:
        """Availability check returns a boolean."""
        result = is_thrift_tree_sitter_available()
        assert isinstance(result, bool)


class TestThriftAnalysis:
    """Tests for Thrift analysis with tree-sitter."""

    def test_analyzes_service(self, temp_repo: Path) -> None:
        """Detects service declarations."""
        (temp_repo / "user.thrift").write_text('''
namespace java com.example

service UserService {
  void ping(),
}
''')

        result = analyze_thrift(temp_repo)

        assert not result.skipped
        assert any(s.kind == "service" and s.name == "UserService" for s in result.symbols)

    def test_analyzes_function(self, temp_repo: Path) -> None:
        """Detects function definitions within services."""
        (temp_repo / "user.thrift").write_text('''
service UserService {
  User getUser(1: string userId),
  void createUser(1: User user),
  list<User> listUsers(1: i32 limit),
}
''')

        result = analyze_thrift(temp_repo)

        func_names = {s.name for s in result.symbols if s.kind == "function"}
        assert "getUser" in func_names
        assert "createUser" in func_names
        assert "listUsers" in func_names

    def test_function_signature(self, temp_repo: Path) -> None:
        """Function signatures include parameters and return type."""
        (temp_repo / "user.thrift").write_text('''
service UserService {
  User getUser(1: string userId),
}
''')

        result = analyze_thrift(temp_repo)

        func = next(s for s in result.symbols if s.kind == "function" and s.name == "getUser")
        assert func.signature is not None
        assert "string" in func.signature
        assert "User" in func.signature

    def test_analyzes_struct(self, temp_repo: Path) -> None:
        """Detects struct declarations."""
        (temp_repo / "user.thrift").write_text('''
struct User {
  1: required string id,
  2: required string name,
  3: optional string email,
}
''')

        result = analyze_thrift(temp_repo)

        assert any(s.kind == "struct" and s.name == "User" for s in result.symbols)

    def test_analyzes_enum(self, temp_repo: Path) -> None:
        """Detects enum declarations."""
        (temp_repo / "status.thrift").write_text('''
enum UserStatus {
  UNKNOWN = 0,
  ACTIVE = 1,
  INACTIVE = 2,
}
''')

        result = analyze_thrift(temp_repo)

        assert any(s.kind == "enum" and s.name == "UserStatus" for s in result.symbols)

    def test_analyzes_typedef(self, temp_repo: Path) -> None:
        """Detects typedef declarations."""
        (temp_repo / "types.thrift").write_text('''
typedef string UserId
typedef list<User> UserList
''')

        result = analyze_thrift(temp_repo)

        typedef_names = {s.name for s in result.symbols if s.kind == "typedef"}
        assert "UserId" in typedef_names
        assert "UserList" in typedef_names

    def test_analyzes_const(self, temp_repo: Path) -> None:
        """Detects const declarations."""
        (temp_repo / "constants.thrift").write_text('''
const string VERSION = "1.0.0"
const i32 MAX_USERS = 1000
''')

        result = analyze_thrift(temp_repo)

        const_names = {s.name for s in result.symbols if s.kind == "const"}
        assert "VERSION" in const_names
        assert "MAX_USERS" in const_names

    def test_analyzes_includes(self, temp_repo: Path) -> None:
        """Detects include statements and creates edges."""
        (temp_repo / "user.thrift").write_text('''
include "common.thrift"
include "types.thrift"

service UserService {}
''')

        result = analyze_thrift(temp_repo)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 2

    def test_service_to_function_relationship(self, temp_repo: Path) -> None:
        """Functions should be linked to their parent service."""
        (temp_repo / "user.thrift").write_text('''
service UserService {
  void ping(),
}
''')

        result = analyze_thrift(temp_repo)

        service = next(s for s in result.symbols if s.kind == "service")
        func = next(s for s in result.symbols if s.kind == "function")

        contains_edges = [e for e in result.edges if e.edge_type == "contains"]
        assert any(e.src == service.id and e.dst == func.id for e in contains_edges)

    def test_multiple_services_in_file(self, temp_repo: Path) -> None:
        """Handles multiple services in a single file."""
        (temp_repo / "api.thrift").write_text('''
service UserService {
  void getUser(),
}

service ProductService {
  void getProduct(),
}
''')

        result = analyze_thrift(temp_repo)

        services = {s.name for s in result.symbols if s.kind == "service"}
        assert "UserService" in services
        assert "ProductService" in services

    def test_namespace_in_canonical_name(self, temp_repo: Path) -> None:
        """Canonical name includes namespace if present."""
        (temp_repo / "user.thrift").write_text('''
namespace java com.example.service

service UserService {
  void ping(),
}
''')

        result = analyze_thrift(temp_repo)

        service = next(s for s in result.symbols if s.kind == "service")
        # Should include namespace in canonical name
        assert "com.example.service" in service.canonical_name


class TestThriftAnalysisUnavailable:
    """Tests for handling unavailable tree-sitter."""

    def test_skipped_when_unavailable(self, temp_repo: Path) -> None:
        """Returns skipped result when tree-sitter unavailable."""
        (temp_repo / "user.thrift").write_text("service UserService {}")

        with patch.object(thrift_module, "is_thrift_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Thrift analysis skipped"):
                result = thrift_module.analyze_thrift(temp_repo)

        assert result.skipped is True


class TestThriftAnalysisRun:
    """Tests for Thrift analysis run metadata."""

    def test_analysis_run_created(self, temp_repo: Path) -> None:
        """Analysis run is created with correct metadata."""
        (temp_repo / "user.thrift").write_text('''
service UserService {}
''')

        result = analyze_thrift(temp_repo)

        assert result.run is not None
        assert result.run.pass_id == "thrift-v1"
        assert result.run.files_analyzed >= 1
