"""Tests for C# analyzer.

Tests for the tree-sitter-based C# analyzer, verifying symbol extraction,
edge detection, and graceful degradation when tree-sitter is unavailable.
"""
import pytest
from pathlib import Path
from unittest.mock import patch

from hypergumbo.analyze.csharp import (
    analyze_csharp,
    find_csharp_files,
    is_csharp_tree_sitter_available,
    PASS_ID,
)


@pytest.fixture
def csharp_repo(tmp_path: Path) -> Path:
    """Create a minimal C# repository for testing."""
    src = tmp_path / "src"
    src.mkdir()

    # Main class file
    (src / "Calculator.cs").write_text(
        """using System;
using System.Linq;

namespace MyApp
{
    public class Calculator
    {
        private int counter = 0;

        public int Add(int a, int b)
        {
            counter++;
            return a + b;
        }

        public int Multiply(int a, int b)
        {
            return Helper.Process(a * b);
        }
    }
}
"""
    )

    # Helper class file
    (src / "Helper.cs").write_text(
        """namespace MyApp
{
    public static class Helper
    {
        public static int Process(int value)
        {
            return value * 2;
        }
    }
}
"""
    )

    # Interface file
    (src / "IShape.cs").write_text(
        """namespace MyApp.Shapes
{
    public interface IShape
    {
        double Area();
        double Perimeter();
    }

    public struct Point
    {
        public int X;
        public int Y;
    }

    public enum Color
    {
        Red,
        Green,
        Blue
    }
}
"""
    )

    # Program entry point
    (src / "Program.cs").write_text(
        """using System;

namespace MyApp
{
    public class Program
    {
        public static void Main(string[] args)
        {
            var calc = new Calculator();
            Console.WriteLine(calc.Add(1, 2));
        }
    }
}
"""
    )

    return tmp_path


class TestCSharpFileDiscovery:
    """Tests for C# file discovery."""

    def test_finds_cs_files(self, csharp_repo: Path) -> None:
        """Should find all .cs files."""
        files = list(find_csharp_files(csharp_repo))
        assert len(files) == 4

    def test_file_names(self, csharp_repo: Path) -> None:
        """Should find expected files."""
        files = [f.name for f in find_csharp_files(csharp_repo)]
        assert set(files) == {"Calculator.cs", "Helper.cs", "IShape.cs", "Program.cs"}


class TestCSharpSymbolExtraction:
    """Tests for symbol extraction from C# files."""

    def test_extracts_classes(self, csharp_repo: Path) -> None:
        """Should extract class declarations."""
        result = analyze_csharp(csharp_repo)


        class_symbols = [s for s in result.symbols if s.kind == "class"]
        class_names = {s.name for s in class_symbols}
        assert "Calculator" in class_names
        assert "Helper" in class_names
        assert "Program" in class_names

    def test_extracts_interfaces(self, csharp_repo: Path) -> None:
        """Should extract interface declarations."""
        result = analyze_csharp(csharp_repo)


        interface_symbols = [s for s in result.symbols if s.kind == "interface"]
        interface_names = {s.name for s in interface_symbols}
        assert "IShape" in interface_names

    def test_extracts_structs(self, csharp_repo: Path) -> None:
        """Should extract struct declarations."""
        result = analyze_csharp(csharp_repo)


        struct_symbols = [s for s in result.symbols if s.kind == "struct"]
        struct_names = {s.name for s in struct_symbols}
        assert "Point" in struct_names

    def test_extracts_enums(self, csharp_repo: Path) -> None:
        """Should extract enum declarations."""
        result = analyze_csharp(csharp_repo)


        enum_symbols = [s for s in result.symbols if s.kind == "enum"]
        enum_names = {s.name for s in enum_symbols}
        assert "Color" in enum_names

    def test_extracts_methods(self, csharp_repo: Path) -> None:
        """Should extract method declarations."""
        result = analyze_csharp(csharp_repo)


        method_symbols = [s for s in result.symbols if s.kind == "method"]
        method_names = {s.name for s in method_symbols}
        # Methods include class prefix
        assert "Calculator.Add" in method_names
        assert "Calculator.Multiply" in method_names
        assert "Helper.Process" in method_names
        assert "Program.Main" in method_names

    def test_symbols_have_correct_language(self, csharp_repo: Path) -> None:
        """All symbols should have language='csharp'."""
        result = analyze_csharp(csharp_repo)


        for symbol in result.symbols:
            assert symbol.language == "csharp"

    def test_symbols_have_spans(self, csharp_repo: Path) -> None:
        """All symbols should have valid span information."""
        result = analyze_csharp(csharp_repo)


        for symbol in result.symbols:
            assert symbol.span is not None
            assert symbol.span.start_line > 0
            assert symbol.span.end_line >= symbol.span.start_line


class TestCSharpEdgeExtraction:
    """Tests for edge extraction from C# files."""

    def test_extracts_import_edges(self, csharp_repo: Path) -> None:
        """Should extract using directive edges."""
        result = analyze_csharp(csharp_repo)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 2  # At least System and System.Linq

    def test_extracts_call_edges(self, csharp_repo: Path) -> None:
        """Should extract method call edges."""
        result = analyze_csharp(csharp_repo)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should have calls from Multiply to Helper.Process
        # and from Main to Add
        assert len(call_edges) >= 1

    def test_extracts_instantiate_edges(self, csharp_repo: Path) -> None:
        """Should extract object creation edges."""
        result = analyze_csharp(csharp_repo)


        instantiate_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        # Main creates new Calculator()
        assert len(instantiate_edges) >= 1

    def test_edges_have_confidence(self, csharp_repo: Path) -> None:
        """All edges should have confidence values."""
        result = analyze_csharp(csharp_repo)


        for edge in result.edges:
            assert 0.0 <= edge.confidence <= 1.0


class TestCSharpCrossFileResolution:
    """Tests for cross-file symbol resolution."""

    def test_cross_file_calls(self, csharp_repo: Path) -> None:
        """Should resolve calls across files."""
        result = analyze_csharp(csharp_repo)


        # Find the Multiply method calling Helper.Process
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        cross_file_calls = [
            e for e in call_edges
            if "Calculator" in e.src and "Helper" in e.dst
        ]
        # Should have at least one cross-file call
        assert len(cross_file_calls) >= 1


class TestCSharpAnalysisRun:
    """Tests for analysis run metadata."""

    def test_creates_analysis_run(self, csharp_repo: Path) -> None:
        """Should create an AnalysisRun with metadata."""
        result = analyze_csharp(csharp_repo)


        assert result.run is not None
        assert result.run.pass_id == PASS_ID
        assert result.run.files_analyzed == 4
        assert result.run.duration_ms >= 0

    def test_symbols_reference_run(self, csharp_repo: Path) -> None:
        """Symbols should reference the analysis run."""
        result = analyze_csharp(csharp_repo)


        for symbol in result.symbols:
            assert symbol.origin == PASS_ID
            assert symbol.origin_run_id == result.run.execution_id


class TestCSharpGracefulDegradation:
    """Tests for graceful degradation when tree-sitter unavailable."""

    def test_returns_skipped_when_unavailable(self) -> None:
        """Should return skipped result when tree-sitter unavailable."""
        with patch(
            "hypergumbo.analyze.csharp.is_csharp_tree_sitter_available",
            return_value=False,
        ):
            import warnings
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result = analyze_csharp(Path("/nonexistent"))
                assert result.skipped
                assert "tree-sitter-c-sharp" in result.skip_reason
                assert len(w) == 1


class TestCSharpTreeSitterAvailability:
    """Tests for tree-sitter availability detection."""

    def test_detects_missing_tree_sitter(self) -> None:
        """Should detect when tree-sitter is not installed."""
        with patch("importlib.util.find_spec", return_value=None):
            assert not is_csharp_tree_sitter_available()

    def test_detects_missing_csharp_grammar(self) -> None:
        """Should detect when tree-sitter-c-sharp is not installed."""
        def find_spec_mock(name: str):
            if name == "tree_sitter":
                return True
            return None

        with patch("importlib.util.find_spec", side_effect=find_spec_mock):
            assert not is_csharp_tree_sitter_available()


class TestCSharpSpecialCases:
    """Tests for special C# syntax cases."""

    def test_handles_constructors(self, tmp_path: Path) -> None:
        """Should handle constructor declarations."""
        (tmp_path / "MyClass.cs").write_text(
            """public class MyClass
{
    public MyClass(int value)
    {
        Value = value;
    }

    public int Value { get; }
}
"""
        )

        result = analyze_csharp(tmp_path)


        constructors = [s for s in result.symbols if s.kind == "constructor"]
        assert len(constructors) >= 1

    def test_handles_properties(self, tmp_path: Path) -> None:
        """Should handle property declarations."""
        (tmp_path / "MyClass.cs").write_text(
            """public class MyClass
{
    public int Value { get; set; }
    public string Name { get; private set; }
}
"""
        )

        result = analyze_csharp(tmp_path)


        properties = [s for s in result.symbols if s.kind == "property"]
        assert len(properties) >= 2

    def test_handles_static_classes(self, tmp_path: Path) -> None:
        """Should handle static class declarations."""
        (tmp_path / "Utils.cs").write_text(
            """public static class Utils
{
    public static int Double(int x) => x * 2;
}
"""
        )

        result = analyze_csharp(tmp_path)


        classes = [s for s in result.symbols if s.kind == "class"]
        assert "Utils" in {s.name for s in classes}

    def test_handles_generic_classes(self, tmp_path: Path) -> None:
        """Should handle generic class declarations."""
        (tmp_path / "Container.cs").write_text(
            """public class Container<T>
{
    private T _value;

    public T Get() => _value;
    public void Set(T value) => _value = value;
}
"""
        )

        result = analyze_csharp(tmp_path)


        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) >= 1

    def test_handles_async_methods(self, tmp_path: Path) -> None:
        """Should handle async method declarations."""
        (tmp_path / "AsyncClass.cs").write_text(
            """using System.Threading.Tasks;

public class AsyncClass
{
    public async Task<int> GetValueAsync()
    {
        return await Task.FromResult(42);
    }
}
"""
        )

        result = analyze_csharp(tmp_path)


        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = {s.name for s in methods}
        assert "AsyncClass.GetValueAsync" in method_names

    def test_handles_empty_files(self, tmp_path: Path) -> None:
        """Should handle empty C# files gracefully."""
        (tmp_path / "Empty.cs").write_text("")

        result = analyze_csharp(tmp_path)


        # Should not crash, just have no symbols from that file
        assert result.run is not None

    def test_handles_io_errors(self, tmp_path: Path) -> None:
        """Should handle IO errors gracefully."""
        result = analyze_csharp(tmp_path)


        # Empty repo should not crash
        assert result.symbols == []
        assert result.edges == []

    def test_same_file_method_calls(self, tmp_path: Path) -> None:
        """Should detect calls between methods in the same file."""
        (tmp_path / "SameFile.cs").write_text(
            """public class Calculator
{
    public int Add(int a, int b)
    {
        Log("Adding");
        return a + b;
    }

    private void Log(string message)
    {
        Console.WriteLine(message);
    }
}
"""
        )

        result = analyze_csharp(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Add should call Log
        same_file_calls = [
            e for e in call_edges
            if "Calculator.Add" in e.src and "Log" in e.dst
        ]
        assert len(same_file_calls) >= 1

    def test_same_file_instantiation(self, tmp_path: Path) -> None:
        """Should detect object creation in the same file."""
        (tmp_path / "Factory.cs").write_text(
            """public class Product
{
    public string Name { get; set; }
}

public class Factory
{
    public Product Create()
    {
        return new Product();
    }
}
"""
        )

        result = analyze_csharp(tmp_path)


        instantiate_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        # Factory.Create should instantiate Product
        same_file_creates = [
            e for e in instantiate_edges
            if "Factory.Create" in e.src and "Product" in e.dst
        ]
        assert len(same_file_creates) >= 1

    def test_direct_function_call(self, tmp_path: Path) -> None:
        """Should handle direct function calls without member access."""
        (tmp_path / "Helpers.cs").write_text(
            """public static class Helpers
{
    public static void DoWork()
    {
        Process();
    }

    public static void Process()
    {
    }
}
"""
        )

        result = analyze_csharp(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # DoWork should call Process
        direct_calls = [
            e for e in call_edges
            if "DoWork" in e.src and "Process" in e.dst
        ]
        assert len(direct_calls) >= 1


class TestAspNetCoreRouteDetection:
    """Tests for ASP.NET Core route detection with [HttpGet], [HttpPost], etc."""

    def test_http_get_attribute(self, tmp_path: Path) -> None:
        """Detects [HttpGet] attribute on controller action."""
        (tmp_path / "UsersController.cs").write_text(
            """using Microsoft.AspNetCore.Mvc;

[ApiController]
[Route("api/[controller]")]
public class UsersController : ControllerBase
{
    [HttpGet]
    public IActionResult GetUsers()
    {
        return Ok();
    }
}
"""
        )

        result = analyze_csharp(tmp_path)


        methods = [s for s in result.symbols if s.kind == "method" and "GetUsers" in s.name]
        assert len(methods) == 1
        method = methods[0]

        assert method.meta is not None
        assert method.meta.get("http_method") == "GET"
        assert method.stable_id == "GET"

    def test_http_post_attribute(self, tmp_path: Path) -> None:
        """Detects [HttpPost] attribute on controller action."""
        (tmp_path / "UsersController.cs").write_text(
            """[ApiController]
public class UsersController : ControllerBase
{
    [HttpPost]
    public IActionResult CreateUser()
    {
        return Ok();
    }
}
"""
        )

        result = analyze_csharp(tmp_path)


        methods = [s for s in result.symbols if s.kind == "method" and "CreateUser" in s.name]
        assert len(methods) == 1
        method = methods[0]

        assert method.meta is not None
        assert method.meta.get("http_method") == "POST"
        assert method.stable_id == "POST"

    def test_http_get_with_route_template(self, tmp_path: Path) -> None:
        """Detects [HttpGet("{id}")] with route template."""
        (tmp_path / "UsersController.cs").write_text(
            """[ApiController]
public class UsersController : ControllerBase
{
    [HttpGet("{id}")]
    public IActionResult GetById(int id)
    {
        return Ok();
    }
}
"""
        )

        result = analyze_csharp(tmp_path)


        methods = [s for s in result.symbols if s.kind == "method" and "GetById" in s.name]
        assert len(methods) == 1
        method = methods[0]

        assert method.meta is not None
        assert method.meta.get("route_path") == "{id}"
        assert method.meta.get("http_method") == "GET"

    def test_all_http_methods(self, tmp_path: Path) -> None:
        """Detects all ASP.NET Core HTTP method attributes."""
        (tmp_path / "ItemsController.cs").write_text(
            """[ApiController]
public class ItemsController : ControllerBase
{
    [HttpGet]
    public IActionResult GetAll() { return Ok(); }

    [HttpPost]
    public IActionResult Create() { return Ok(); }

    [HttpPut("{id}")]
    public IActionResult Update() { return Ok(); }

    [HttpDelete("{id}")]
    public IActionResult Delete() { return Ok(); }

    [HttpPatch("{id}")]
    public IActionResult Patch() { return Ok(); }
}
"""
        )

        result = analyze_csharp(tmp_path)


        methods = [s for s in result.symbols if s.kind == "method" and s.stable_id in ("GET", "POST", "PUT", "DELETE", "PATCH")]

        assert len(methods) == 5
        http_methods = {m.stable_id for m in methods}
        assert http_methods == {"GET", "POST", "PUT", "DELETE", "PATCH"}


class TestCSharpSignatureExtraction:
    """Tests for C# function signature extraction."""

    def test_basic_method_signature(self, tmp_path: Path) -> None:
        """Extracts signature from a basic method."""
        (tmp_path / "Calculator.cs").write_text("""
public class Calculator {
    public int Add(int a, int b) {
        return a + b;
    }
}
""")
        result = analyze_csharp(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "Add" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(int a, int b) int"

    def test_void_method_signature(self, tmp_path: Path) -> None:
        """Extracts signature from void method."""
        (tmp_path / "Logger.cs").write_text("""
public class Logger {
    public void Log(string message) {
        Console.WriteLine(message);
    }
}
""")
        result = analyze_csharp(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "Log" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(string message)"

    def test_no_params_signature(self, tmp_path: Path) -> None:
        """Extracts signature from method with no parameters."""
        (tmp_path / "Counter.cs").write_text("""
public class Counter {
    public int GetCount() {
        return 0;
    }
}
""")
        result = analyze_csharp(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "GetCount" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "() int"

    def test_generic_type_signature(self, tmp_path: Path) -> None:
        """Extracts signature with generic types."""
        (tmp_path / "Container.cs").write_text("""
public class Container {
    public List<string> GetItems(Dictionary<string, int> config) {
        return null;
    }
}
""")
        result = analyze_csharp(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "GetItems" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(Dictionary<string, int> config) List<string>"

    def test_constructor_signature(self, tmp_path: Path) -> None:
        """Extracts signature from constructor."""
        (tmp_path / "Person.cs").write_text("""
public class Person {
    public Person(string name, int age) {
        _name = name;
        _age = age;
    }
    private string _name;
    private int _age;
}
""")
        result = analyze_csharp(tmp_path)
        constructors = [s for s in result.symbols if s.kind == "constructor"]
        assert len(constructors) == 1
        assert constructors[0].signature == "(string name, int age)"

    def test_array_type_signature(self, tmp_path: Path) -> None:
        """Extracts signature with array types."""
        (tmp_path / "Processor.cs").write_text("""
public class Processor {
    public byte[] Process(string[] inputs) {
        return null;
    }
}
""")
        result = analyze_csharp(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "Process" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(string[] inputs) byte[]"
