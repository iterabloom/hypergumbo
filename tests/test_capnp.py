"""Tests for Cap'n Proto analysis pass.

Tests verify that the Cap'n Proto analyzer correctly extracts:
- Struct definitions
- Interface definitions (RPC services)
- Method definitions (RPC methods)
- Enum definitions
- Const definitions
- Import statements
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo.analyze import capnp as capnp_module
from hypergumbo.analyze.capnp import (
    analyze_capnp,
    find_capnp_files,
    is_capnp_tree_sitter_available,
)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository for testing."""
    return tmp_path


class TestFindCapnpFiles:
    """Tests for find_capnp_files function."""

    def test_finds_capnp_files(self, temp_repo: Path) -> None:
        """Finds .capnp files in repo."""
        (temp_repo / "user.capnp").write_text("struct User {}")
        (temp_repo / "api.capnp").write_text("interface Api {}")
        (temp_repo / "README.md").write_text("# Docs")

        files = list(find_capnp_files(temp_repo))
        filenames = {f.name for f in files}

        assert "user.capnp" in filenames
        assert "api.capnp" in filenames
        assert "README.md" not in filenames

    def test_finds_nested_capnp_files(self, temp_repo: Path) -> None:
        """Finds .capnp files in subdirectories."""
        idl = temp_repo / "idl"
        idl.mkdir()
        (idl / "user.capnp").write_text("struct User {}")

        files = list(find_capnp_files(temp_repo))

        assert len(files) == 1
        assert files[0].name == "user.capnp"


class TestCapnpTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_availability_check_runs(self) -> None:
        """Availability check returns a boolean."""
        result = is_capnp_tree_sitter_available()
        assert isinstance(result, bool)


class TestCapnpAnalysis:
    """Tests for Cap'n Proto analysis with tree-sitter."""

    def test_analyzes_struct(self, temp_repo: Path) -> None:
        """Detects struct declarations."""
        (temp_repo / "user.capnp").write_text('''
@0xabcdef1234567890;

struct User {
  name @0 :Text;
  email @1 :Text;
}
''')

        result = analyze_capnp(temp_repo)

        assert not result.skipped
        assert any(s.kind == "struct" and s.name == "User" for s in result.symbols)

    def test_analyzes_interface(self, temp_repo: Path) -> None:
        """Detects interface declarations (RPC services)."""
        (temp_repo / "api.capnp").write_text('''
@0xabcdef1234567890;

interface UserService {
  getUser @0 (userId :Text) -> (user :User);
}
''')

        result = analyze_capnp(temp_repo)

        assert any(s.kind == "interface" and s.name == "UserService" for s in result.symbols)

    def test_analyzes_method(self, temp_repo: Path) -> None:
        """Detects method definitions within interfaces."""
        (temp_repo / "api.capnp").write_text('''
@0xabcdef1234567890;

interface UserService {
  getUser @0 (userId :Text) -> (user :User);
  createUser @1 (user :User) -> ();
  listUsers @2 () -> (users :List(User));
}
''')

        result = analyze_capnp(temp_repo)

        method_names = {s.name for s in result.symbols if s.kind == "method"}
        assert "getUser" in method_names
        assert "createUser" in method_names
        assert "listUsers" in method_names

    def test_method_signature(self, temp_repo: Path) -> None:
        """Method signatures include parameters and return type."""
        (temp_repo / "api.capnp").write_text('''
@0xabcdef1234567890;

interface UserService {
  getUser @0 (userId :Text) -> (user :User);
}
''')

        result = analyze_capnp(temp_repo)

        method = next(s for s in result.symbols if s.kind == "method" and s.name == "getUser")
        assert method.signature is not None
        assert "userId" in method.signature
        assert "user" in method.signature

    def test_analyzes_enum(self, temp_repo: Path) -> None:
        """Detects enum declarations."""
        (temp_repo / "status.capnp").write_text('''
@0xabcdef1234567890;

enum UserStatus {
  unknown @0;
  active @1;
  inactive @2;
}
''')

        result = analyze_capnp(temp_repo)

        assert any(s.kind == "enum" and s.name == "UserStatus" for s in result.symbols)

    def test_analyzes_const(self, temp_repo: Path) -> None:
        """Detects const declarations."""
        (temp_repo / "constants.capnp").write_text('''
@0xabcdef1234567890;

const version :Text = "1.0.0";
const maxUsers :UInt32 = 1000;
''')

        result = analyze_capnp(temp_repo)

        const_names = {s.name for s in result.symbols if s.kind == "const"}
        assert "version" in const_names
        assert "maxUsers" in const_names

    def test_analyzes_imports(self, temp_repo: Path) -> None:
        """Detects import statements and creates edges."""
        (temp_repo / "user.capnp").write_text('''
@0xabcdef1234567890;

using import "common.capnp".Common;
using import "/absolute/path.capnp".Path;

struct User {}
''')

        result = analyze_capnp(temp_repo)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 2

    def test_interface_to_method_relationship(self, temp_repo: Path) -> None:
        """Methods should be linked to their parent interface."""
        (temp_repo / "api.capnp").write_text('''
@0xabcdef1234567890;

interface UserService {
  ping @0 () -> ();
}
''')

        result = analyze_capnp(temp_repo)

        interface = next(s for s in result.symbols if s.kind == "interface")
        method = next(s for s in result.symbols if s.kind == "method")

        contains_edges = [e for e in result.edges if e.edge_type == "contains"]
        assert any(e.src == interface.id and e.dst == method.id for e in contains_edges)

    def test_multiple_interfaces_in_file(self, temp_repo: Path) -> None:
        """Handles multiple interfaces in a single file."""
        (temp_repo / "api.capnp").write_text('''
@0xabcdef1234567890;

interface UserService {
  getUser @0 () -> ();
}

interface ProductService {
  getProduct @0 () -> ();
}
''')

        result = analyze_capnp(temp_repo)

        interfaces = {s.name for s in result.symbols if s.kind == "interface"}
        assert "UserService" in interfaces
        assert "ProductService" in interfaces

    def test_nested_struct(self, temp_repo: Path) -> None:
        """Handles nested struct definitions."""
        (temp_repo / "user.capnp").write_text('''
@0xabcdef1234567890;

struct User {
  name @0 :Text;

  struct Address {
    street @0 :Text;
    city @1 :Text;
  }

  address @1 :Address;
}
''')

        result = analyze_capnp(temp_repo)

        struct_names = {s.name for s in result.symbols if s.kind == "struct"}
        assert "User" in struct_names
        assert "Address" in struct_names


class TestCapnpAnalysisUnavailable:
    """Tests for handling unavailable tree-sitter."""

    def test_skipped_when_unavailable(self, temp_repo: Path) -> None:
        """Returns skipped result when tree-sitter unavailable."""
        (temp_repo / "user.capnp").write_text("struct User {}")

        with patch.object(capnp_module, "is_capnp_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Cap'n Proto analysis skipped"):
                result = capnp_module.analyze_capnp(temp_repo)

        assert result.skipped is True


class TestCapnpAnalysisRun:
    """Tests for Cap'n Proto analysis run metadata."""

    def test_analysis_run_created(self, temp_repo: Path) -> None:
        """Analysis run is created with correct metadata."""
        (temp_repo / "user.capnp").write_text('''
@0xabcdef1234567890;

struct User {}
''')

        result = analyze_capnp(temp_repo)

        assert result.run is not None
        assert result.run.pass_id == "capnp-v1"
        assert result.run.files_analyzed >= 1
