"""Branch coverage tests for thrift.py analyzer.

Tests specific branch paths in the Thrift analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Service extraction
- Function extraction (RPC methods)
- Struct extraction
- Enum extraction
- Typedef extraction
- Const extraction
- Include edge extraction
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_common.thrift import (
    _make_symbol_id,
    _make_file_id,
    analyze_thrift,
    find_thrift_files,
)


def make_thrift_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Thrift file with given content."""
    (tmp_path / name).write_text(content)


class TestThriftHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = _make_symbol_id("services/user.thrift", 1, 10, "UserService", "service")
        assert symbol_id == "thrift:services/user.thrift:1-10:UserService:service"

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = _make_file_id("common/types.thrift")
        assert file_id == "thrift:common/types.thrift:1-1:file:file"


class TestServiceExtraction:
    """Branch coverage for service definition extraction."""

    def test_simple_service(self, tmp_path: Path) -> None:
        """Test simple service extraction."""
        make_thrift_file(tmp_path, "user.thrift", """
service UserService {
    void createUser(1: string name)
}
""")
        result = analyze_thrift(tmp_path)
        assert not result.skipped

        services = [s for s in result.symbols if s.kind == "service"]
        assert len(services) >= 1
        assert any(s.name == "UserService" for s in services)

    def test_service_with_namespace(self, tmp_path: Path) -> None:
        """Test service in namespace."""
        make_thrift_file(tmp_path, "user.thrift", """
namespace java com.example.users

service UserService {
    void ping()
}
""")
        result = analyze_thrift(tmp_path)
        services = [s for s in result.symbols if s.kind == "service"]
        assert len(services) >= 1
        # Canonical name should include namespace
        assert any("UserService" in s.canonical_name for s in services)


class TestFunctionExtraction:
    """Branch coverage for function (RPC method) extraction."""

    def test_simple_function(self, tmp_path: Path) -> None:
        """Test simple function extraction."""
        make_thrift_file(tmp_path, "api.thrift", """
service ApiService {
    void ping()
}
""")
        result = analyze_thrift(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]
        assert len(functions) >= 1
        assert any(f.name == "ping" for f in functions)

    def test_function_with_params(self, tmp_path: Path) -> None:
        """Test function with parameters."""
        make_thrift_file(tmp_path, "api.thrift", """
service UserService {
    User getUser(1: string userId, 2: bool includeProfile)
}
""")
        result = analyze_thrift(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function" and s.name == "getUser"]
        assert len(functions) >= 1
        assert functions[0].signature is not None

    def test_function_creates_contains_edge(self, tmp_path: Path) -> None:
        """Test function creates contains edge from service."""
        make_thrift_file(tmp_path, "api.thrift", """
service DataService {
    i32 getData(1: string key)
}
""")
        result = analyze_thrift(tmp_path)
        contains_edges = [e for e in result.edges if e.edge_type == "contains"]
        assert len(contains_edges) >= 1


class TestStructExtraction:
    """Branch coverage for struct extraction."""

    def test_simple_struct(self, tmp_path: Path) -> None:
        """Test simple struct extraction."""
        make_thrift_file(tmp_path, "types.thrift", """
struct User {
    1: required string id,
    2: required string name,
    3: optional string email
}
""")
        result = analyze_thrift(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert len(structs) >= 1
        assert any(s.name == "User" for s in structs)

    def test_nested_struct(self, tmp_path: Path) -> None:
        """Test struct with nested struct field."""
        make_thrift_file(tmp_path, "types.thrift", """
struct Address {
    1: string street,
    2: string city
}

struct Person {
    1: string name,
    2: Address address
}
""")
        result = analyze_thrift(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert len(structs) >= 2


class TestEnumExtraction:
    """Branch coverage for enum extraction."""

    def test_simple_enum(self, tmp_path: Path) -> None:
        """Test simple enum extraction."""
        make_thrift_file(tmp_path, "types.thrift", """
enum Status {
    UNKNOWN = 0,
    ACTIVE = 1,
    INACTIVE = 2
}
""")
        result = analyze_thrift(tmp_path)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert len(enums) >= 1
        assert any(e.name == "Status" for e in enums)


class TestTypedefExtraction:
    """Branch coverage for typedef extraction."""

    def test_simple_typedef(self, tmp_path: Path) -> None:
        """Test simple typedef extraction."""
        make_thrift_file(tmp_path, "types.thrift", """
typedef string UserId
typedef list<string> StringList
""")
        result = analyze_thrift(tmp_path)
        typedefs = [s for s in result.symbols if s.kind == "typedef"]
        assert len(typedefs) >= 2
        names = [t.name for t in typedefs]
        assert "UserId" in names
        assert "StringList" in names


class TestConstExtraction:
    """Branch coverage for const extraction."""

    def test_simple_const(self, tmp_path: Path) -> None:
        """Test simple const extraction."""
        make_thrift_file(tmp_path, "consts.thrift", """
const i32 MAX_RETRIES = 3
const string DEFAULT_HOST = "localhost"
""")
        result = analyze_thrift(tmp_path)
        consts = [s for s in result.symbols if s.kind == "const"]
        assert len(consts) >= 2
        names = [c.name for c in consts]
        assert "MAX_RETRIES" in names
        assert "DEFAULT_HOST" in names


class TestIncludeEdges:
    """Branch coverage for include statement extraction."""

    def test_include_creates_edge(self, tmp_path: Path) -> None:
        """Test include statement creates import edge."""
        make_thrift_file(tmp_path, "service.thrift", """
include "common/types.thrift"

service ApiService {
    void ping()
}
""")
        result = analyze_thrift(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1

    def test_multiple_includes(self, tmp_path: Path) -> None:
        """Test multiple include statements."""
        make_thrift_file(tmp_path, "service.thrift", """
include "types.thrift"
include "exceptions.thrift"

service DataService {
    void getData()
}
""")
        result = analyze_thrift(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 2


class TestFindThriftFiles:
    """Branch coverage for file discovery."""

    def test_finds_thrift_files(self, tmp_path: Path) -> None:
        """Test .thrift files are discovered."""
        (tmp_path / "service.thrift").write_text("service Test {}")

        files = list(find_thrift_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".thrift"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        idl = tmp_path / "idl" / "v1"
        idl.mkdir(parents=True)
        (idl / "api.thrift").write_text("service ApiService {}")

        files = list(find_thrift_files(tmp_path))
        assert len(files) == 1


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_thrift_files(self, tmp_path: Path) -> None:
        """Test directory with no Thrift files."""
        result = analyze_thrift(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_thrift(self, tmp_path: Path) -> None:
        """Test minimal Thrift file."""
        make_thrift_file(tmp_path, "min.thrift", """
service MinimalService {
    void ping()
}
""")
        result = analyze_thrift(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_thrift_file(tmp_path, "api.thrift", """
service ApiService {
    void ping()
}
""")
        result = analyze_thrift(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1


class TestNamespaceExtraction:
    """Branch coverage for namespace extraction."""

    def test_multiple_namespace_declarations(self, tmp_path: Path) -> None:
        """Test file with multiple namespace declarations."""
        make_thrift_file(tmp_path, "multi_ns.thrift", """
namespace java com.example
namespace py example
namespace cpp example

struct User {
    1: string name
}
""")
        result = analyze_thrift(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert len(structs) >= 1
        # Should use first namespace
        assert any("User" in s.canonical_name for s in structs)
