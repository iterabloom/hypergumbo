"""Tests for C# analyzer.

Tests for the tree-sitter-based C# analyzer, verifying symbol extraction,
edge detection, and graceful degradation when tree-sitter is unavailable.
"""
import pytest
from pathlib import Path
from unittest.mock import patch

from hypergumbo_lang_mainstream.csharp import (
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


class TestCSharpInheritanceEdges:
    """Tests for extracting C# inheritance edges (META-001)."""

    def test_extracts_base_class_metadata(self, tmp_path: Path) -> None:
        """Extracts base_classes metadata for class with base class."""
        from hypergumbo_lang_mainstream.csharp import analyze_csharp

        cs_file = tmp_path / "Models.cs"
        cs_file.write_text("""
public class BaseModel {
    public void Save() {}
}

public class User : BaseModel {
    public void Greet() {}
}
""")

        result = analyze_csharp(tmp_path)

        user = next((s for s in result.symbols if s.name == "User"), None)
        assert user is not None
        assert user.meta is not None
        assert user.meta.get("base_classes") == ["BaseModel"]

    def test_extracts_interface_implementation(self, tmp_path: Path) -> None:
        """Extracts base_classes metadata for class implementing interface."""
        from hypergumbo_lang_mainstream.csharp import analyze_csharp

        cs_file = tmp_path / "Models.cs"
        cs_file.write_text("""
public interface IEntity {
    void Save();
}

public class User : IEntity {
    public void Save() {}
}
""")

        result = analyze_csharp(tmp_path)

        user = next((s for s in result.symbols if s.name == "User"), None)
        assert user is not None
        assert user.meta is not None
        assert user.meta.get("base_classes") == ["IEntity"]

    def test_extracts_multiple_inheritance(self, tmp_path: Path) -> None:
        """Extracts all base types for class extending class and interfaces."""
        from hypergumbo_lang_mainstream.csharp import analyze_csharp

        cs_file = tmp_path / "Models.cs"
        cs_file.write_text("""
public class BaseModel {
    public virtual void Save() {}
}

public interface IEntity {
    void Id();
}

public interface IDisposable {
    void Dispose();
}

public class User : BaseModel, IEntity, IDisposable {
    public void Id() {}
    public void Dispose() {}
}
""")

        result = analyze_csharp(tmp_path)

        user = next((s for s in result.symbols if s.name == "User"), None)
        assert user is not None
        assert user.meta is not None
        base_classes = user.meta.get("base_classes")
        assert base_classes is not None
        assert "BaseModel" in base_classes
        assert "IEntity" in base_classes
        assert "IDisposable" in base_classes

    def test_strips_generic_parameters(self, tmp_path: Path) -> None:
        """Strips generic parameters from base class names."""
        from hypergumbo_lang_mainstream.csharp import analyze_csharp

        cs_file = tmp_path / "Models.cs"
        cs_file.write_text("""
public class UserRepository : Repository<User> {
    public void Find() {}
}
""")

        result = analyze_csharp(tmp_path)

        repo = next((s for s in result.symbols if s.name == "UserRepository"), None)
        assert repo is not None
        assert repo.meta is not None
        # Should be "Repository", not "Repository<User>"
        assert repo.meta.get("base_classes") == ["Repository"]

    def test_creates_extends_edge(self, tmp_path: Path) -> None:
        """Creates extends edge from class to its base class via linker."""
        from hypergumbo_lang_mainstream.csharp import analyze_csharp
        from hypergumbo_core.linkers.inheritance import link_inheritance
        from hypergumbo_core.linkers.registry import LinkerContext

        cs_file = tmp_path / "Models.cs"
        cs_file.write_text("""
public class BaseModel {
    public void Save() {}
}

public class User : BaseModel {
    public void Greet() {}
}
""")

        result = analyze_csharp(tmp_path)
        linker_ctx = LinkerContext(
            repo_root=tmp_path, symbols=result.symbols, edges=result.edges
        )
        linker_result = link_inheritance(linker_ctx)

        user = next((s for s in result.symbols if s.name == "User"), None)
        base = next((s for s in result.symbols if s.name == "BaseModel"), None)
        assert user is not None
        assert base is not None

        extends_edges = [e for e in linker_result.edges if e.edge_type == "extends"]
        assert len(extends_edges) == 1
        assert extends_edges[0].src == user.id
        assert extends_edges[0].dst == base.id

    def test_creates_implements_edge_for_interface(self, tmp_path: Path) -> None:
        """Creates implements edge from class to interface via linker."""
        from hypergumbo_lang_mainstream.csharp import analyze_csharp
        from hypergumbo_core.linkers.inheritance import link_inheritance
        from hypergumbo_core.linkers.registry import LinkerContext

        cs_file = tmp_path / "Models.cs"
        cs_file.write_text("""
public interface IEntity {
    void Save();
}

public class User : IEntity {
    public void Save() {}
}
""")

        result = analyze_csharp(tmp_path)
        linker_ctx = LinkerContext(
            repo_root=tmp_path, symbols=result.symbols, edges=result.edges
        )
        linker_result = link_inheritance(linker_ctx)

        user = next((s for s in result.symbols if s.name == "User"), None)
        entity = next((s for s in result.symbols if s.name == "IEntity"), None)
        assert user is not None
        assert entity is not None

        implements_edges = [e for e in linker_result.edges if e.edge_type == "implements"]
        assert len(implements_edges) == 1
        assert implements_edges[0].src == user.id
        assert implements_edges[0].dst == entity.id

    def test_no_edge_for_external_base_class(self, tmp_path: Path) -> None:
        """No edge created when base class is not in analyzed codebase."""
        from hypergumbo_lang_mainstream.csharp import analyze_csharp
        from hypergumbo_core.linkers.inheritance import link_inheritance
        from hypergumbo_core.linkers.registry import LinkerContext

        cs_file = tmp_path / "Models.cs"
        cs_file.write_text("""
public class UserController : Controller {
    public void Index() {}
}
""")

        result = analyze_csharp(tmp_path)
        linker_ctx = LinkerContext(
            repo_root=tmp_path, symbols=result.symbols, edges=result.edges
        )
        linker_result = link_inheritance(linker_ctx)

        controller = next((s for s in result.symbols if s.name == "UserController"), None)
        assert controller is not None
        assert controller.meta is not None
        assert controller.meta.get("base_classes") == ["Controller"]

        # No edges (Controller is external)
        extends_edges = [e for e in linker_result.edges if e.edge_type in ("extends", "implements")]
        assert len(extends_edges) == 0


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


class TestCSharpTypeInference:
    """Tests for variable type inference in C#."""

    def test_parameter_type_inference(self, tmp_path: Path) -> None:
        """Method parameter types should enable method call resolution."""
        # Service class with methods
        (tmp_path / "Database.cs").write_text("""
public class Database {
    public void Save(object obj) { }
    public void Commit() { }
}
""")
        # Handler receives Database as parameter
        (tmp_path / "Handler.cs").write_text("""
public class Handler {
    public void Process(Database db, string data) {
        db.Save(data);
        db.Commit();
    }
}
""")

        result = analyze_csharp(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 2

        # Find symbols
        process_method = next(
            (s for s in result.symbols if s.name == "Handler.Process"), None
        )
        db_save = next(
            (s for s in result.symbols if s.name == "Database.Save"), None
        )
        db_commit = next(
            (s for s in result.symbols if s.name == "Database.Commit"), None
        )

        assert process_method is not None
        assert db_save is not None
        assert db_commit is not None

        # Should have edges from Handler.Process to Database.Save and Database.Commit
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        save_edge = next(
            (
                e
                for e in call_edges
                if e.src == process_method.id
                and e.dst == db_save.id
            ),
            None,
        )
        commit_edge = next(
            (
                e
                for e in call_edges
                if e.src == process_method.id
                and e.dst == db_commit.id
            ),
            None,
        )

        assert save_edge is not None, "Expected call edge for db.Save() via param type inference"
        assert commit_edge is not None, "Expected call edge for db.Commit() via param type inference"
        assert save_edge.evidence_type == "method_call_type_inferred"
        assert commit_edge.evidence_type == "method_call_type_inferred"

    def test_constructor_type_inference(self, tmp_path: Path) -> None:
        """Constructor assignments should enable method call resolution."""
        (tmp_path / "Service.cs").write_text("""
public class Client {
    public void Send() { }
}

public class Service {
    public void Run() {
        var client = new Client();
        client.Send();
    }
}
""")

        result = analyze_csharp(tmp_path)

        # Find symbols
        run_method = next(
            (s for s in result.symbols if s.name == "Service.Run"), None
        )
        client_send = next(
            (s for s in result.symbols if s.name == "Client.Send"), None
        )

        assert run_method is not None
        assert client_send is not None

        # Should have edge from Service.Run to Client.Send via constructor type inference
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        send_edge = next(
            (
                e
                for e in call_edges
                if e.src == run_method.id
                and e.dst == client_send.id
            ),
            None,
        )

        assert send_edge is not None, "Expected call edge for client.Send() via constructor type inference"


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
            "hypergumbo_lang_mainstream.csharp.is_csharp_tree_sitter_available",
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


class TestAnnotationExtraction:
    """Tests for C# attribute/annotation extraction for FRAMEWORK_PATTERNS phase."""

    def test_method_annotation_with_positional_arg(self, tmp_path: Path) -> None:
        """Extracts method attribute with positional argument."""
        (tmp_path / "Controller.cs").write_text("""
using Microsoft.AspNetCore.Mvc;

[ApiController]
public class UsersController : ControllerBase
{
    [HttpGet("/users")]
    public IActionResult GetUsers()
    {
        return Ok();
    }
}
""")
        result = analyze_csharp(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1

        method = methods[0]
        assert method.meta is not None
        assert "annotations" in method.meta

        annotations = method.meta["annotations"]
        assert len(annotations) == 1
        assert annotations[0]["name"] == "HttpGet"
        assert annotations[0]["args"] == ["/users"]
        assert annotations[0]["kwargs"] == {}

    def test_class_annotation_no_args(self, tmp_path: Path) -> None:
        """Extracts class attribute without arguments."""
        (tmp_path / "Controller.cs").write_text("""
using Microsoft.AspNetCore.Mvc;

[ApiController]
public class UsersController : ControllerBase
{
    public IActionResult Index() => Ok();
}
""")
        result = analyze_csharp(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]
        controller = next(c for c in classes if c.name == "UsersController")

        assert controller.meta is not None
        assert "annotations" in controller.meta

        annotations = controller.meta["annotations"]
        assert len(annotations) == 1
        assert annotations[0]["name"] == "ApiController"
        assert annotations[0]["args"] == []
        assert annotations[0]["kwargs"] == {}

    def test_annotation_with_named_argument(self, tmp_path: Path) -> None:
        """Extracts attribute with named argument."""
        (tmp_path / "Model.cs").write_text("""
using System.ComponentModel.DataAnnotations;

public class User
{
    [StringLength(50, MinimumLength = 3)]
    public string Name { get; set; }
}
""")
        # Properties aren't currently captured with annotations,
        # but let's test with a method that has named args
        (tmp_path / "Controller.cs").write_text("""
using Microsoft.AspNetCore.Mvc;

public class TestController
{
    [Route("api/test", Name = "GetTest")]
    public IActionResult GetTest()
    {
        return Ok();
    }
}
""")
        result = analyze_csharp(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]
        method = next(m for m in methods if "GetTest" in m.name)

        assert method.meta is not None
        assert "annotations" in method.meta

        annotations = method.meta["annotations"]
        route_ann = next(a for a in annotations if a["name"] == "Route")
        assert route_ann["args"] == ["api/test"]
        assert route_ann["kwargs"] == {"Name": "GetTest"}

    def test_multiple_annotations_on_method(self, tmp_path: Path) -> None:
        """Extracts multiple attributes from a single method."""
        (tmp_path / "Controller.cs").write_text("""
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Authorization;

public class SecureController
{
    [Authorize]
    [HttpPost("/data")]
    public IActionResult PostData()
    {
        return Ok();
    }
}
""")
        result = analyze_csharp(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1

        method = methods[0]
        assert method.meta is not None
        assert "annotations" in method.meta

        annotations = method.meta["annotations"]
        assert len(annotations) == 2

        names = {a["name"] for a in annotations}
        assert names == {"Authorize", "HttpPost"}

    def test_http_method_annotation_extraction(self, tmp_path: Path) -> None:
        """HTTP method attributes are extracted as annotations for YAML pattern matching."""
        (tmp_path / "Controller.cs").write_text("""
using Microsoft.AspNetCore.Mvc;

public class ItemsController
{
    [HttpGet("{id}")]
    public IActionResult GetItem(int id)
    {
        return Ok();
    }
}
""")
        result = analyze_csharp(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]
        method = methods[0]

        # Annotations are extracted for YAML framework pattern matching
        assert method.meta is not None
        assert "annotations" in method.meta
        annotations = method.meta["annotations"]
        assert len(annotations) == 1
        assert annotations[0]["name"] == "HttpGet"
        assert annotations[0]["args"] == ["{id}"]

    def test_qualified_attribute_name(self, tmp_path: Path) -> None:
        """Extracts qualified attribute names (e.g., System.Serializable)."""
        (tmp_path / "Model.cs").write_text("""
[System.Serializable]
public class DataModel
{
    [System.ComponentModel.Description("User name")]
    public void Process() {}
}
""")
        result = analyze_csharp(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]
        class_sym = classes[0]

        assert class_sym.meta is not None
        assert "annotations" in class_sym.meta
        assert class_sym.meta["annotations"][0]["name"] == "System.Serializable"

        methods = [s for s in result.symbols if s.kind == "method"]
        method = methods[0]
        assert method.meta is not None
        assert "annotations" in method.meta
        assert method.meta["annotations"][0]["name"] == "System.ComponentModel.Description"

    def test_non_string_positional_args(self, tmp_path: Path) -> None:
        """Extracts non-string positional arguments (numbers, bools)."""
        (tmp_path / "Model.cs").write_text("""
public class UserModel
{
    [Range(1, 100)]
    public void SetAge(int age) {}
}
""")
        result = analyze_csharp(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]
        method = methods[0]

        assert method.meta is not None
        assert "annotations" in method.meta
        # The Range attribute has two numeric arguments
        annotations = method.meta["annotations"]
        range_ann = next(a for a in annotations if a["name"] == "Range")
        assert range_ann["args"] == ["1", "100"]


class TestUsingAliasExtraction:
    """Tests for using directive alias extraction for disambiguation."""

    def test_extracts_aliased_using(self, tmp_path: Path) -> None:
        """Extracts aliased using directives like 'using Svc = MyApp.Services;'."""
        from hypergumbo_lang_mainstream.csharp import (
            _extract_using_aliases,
            is_csharp_tree_sitter_available,
        )

        if not is_csharp_tree_sitter_available():
            pytest.skip("tree-sitter-c-sharp not available")

        import tree_sitter_c_sharp
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_c_sharp.language())
        parser = tree_sitter.Parser(lang)

        cs_file = tmp_path / "Main.cs"
        cs_file.write_text("""
using System;
using Svc = MyApp.Services;
using System.Collections.Generic;

public class Program
{
    public static void Main() {}
}
""")

        source = cs_file.read_bytes()
        tree = parser.parse(source)

        aliases = _extract_using_aliases(tree, source)

        # Check regular using extracts last component
        assert "System" in aliases
        assert "Generic" in aliases

        # Check aliased using
        assert "Svc" in aliases
        assert aliases["Svc"] == "MyApp.Services"

    def test_extracts_simple_namespace_using(self, tmp_path: Path) -> None:
        """Extracts simple using directives as name -> full path."""
        from hypergumbo_lang_mainstream.csharp import (
            _extract_using_aliases,
            is_csharp_tree_sitter_available,
        )

        if not is_csharp_tree_sitter_available():
            pytest.skip("tree-sitter-c-sharp not available")

        import tree_sitter_c_sharp
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_c_sharp.language())
        parser = tree_sitter.Parser(lang)

        cs_file = tmp_path / "Test.cs"
        cs_file.write_text("""
using System.Linq;

public class Test {}
""")

        source = cs_file.read_bytes()
        tree = parser.parse(source)

        aliases = _extract_using_aliases(tree, source)

        # Last component of qualified name
        assert "Linq" in aliases
        assert aliases["Linq"] == "System.Linq"


class TestCSharpLambdaCallAttribution:
    """Tests for call edge attribution inside C# lambda expressions.

    C# uses lambdas extensively in LINQ (.Where, .Select), callbacks, and async
    patterns. Calls inside these lambdas must be attributed to the enclosing method.

    This confirms META-002 (Extraction Completeness) for C# lambdas - the implicit
    walk-up past lambda_expression nodes correctly finds the enclosing method.
    """

    def test_call_inside_linq_lambda_attributed(self, tmp_path: Path) -> None:
        """Calls inside LINQ lambda expressions are attributed to enclosing method.

        When you have:
            void Process() {
                items.Where(x => Filter(x));
            }

        The call to Filter() should be attributed to Process, not lost.
        """
        from hypergumbo_lang_mainstream.csharp import analyze_csharp

        cs_file = tmp_path / "Lambda.cs"
        cs_file.write_text("""
public class Example {
    public void Process() {
        var list = new List<int> { 1, 2, 3 };
        list.Where(x => Filter(x));
    }

    private bool Filter(int x) {
        return x > 0;
    }
}
""")

        result = analyze_csharp(tmp_path)

        process_method = next(
            (s for s in result.symbols if s.name == "Example.Process" and s.kind == "method"),
            None,
        )
        filter_method = next(
            (s for s in result.symbols if s.name == "Example.Filter" and s.kind == "method"),
            None,
        )

        assert process_method is not None, "Should find Process method"
        assert filter_method is not None, "Should find Filter method"

        # The call to Filter() inside the lambda should be attributed to Process
        call_edge = next(
            (
                e for e in result.edges
                if e.src == process_method.id
                and e.dst == filter_method.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, "Call to Filter() inside lambda should be attributed to Process"

    def test_call_inside_foreach_lambda_attributed(self, tmp_path: Path) -> None:
        """Calls inside ForEach lambda are attributed to enclosing method."""
        from hypergumbo_lang_mainstream.csharp import analyze_csharp

        cs_file = tmp_path / "ForEach.cs"
        cs_file.write_text("""
public class Example {
    public void Caller() {
        var list = new List<int> { 1, 2, 3 };
        list.ForEach(x => Helper(x));
    }

    private void Helper(int x) { }
}
""")

        result = analyze_csharp(tmp_path)

        caller = next(
            (s for s in result.symbols if s.name == "Example.Caller" and s.kind == "method"),
            None,
        )
        helper = next(
            (s for s in result.symbols if s.name == "Example.Helper" and s.kind == "method"),
            None,
        )

        assert caller is not None
        assert helper is not None

        call_edge = next(
            (
                e for e in result.edges
                if e.src == caller.id and e.dst == helper.id and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, "Call inside ForEach lambda should be attributed to Caller"

    def test_chained_linq_lambdas_attributed(self, tmp_path: Path) -> None:
        """Calls in chained LINQ lambdas are all attributed to enclosing method."""
        from hypergumbo_lang_mainstream.csharp import analyze_csharp

        cs_file = tmp_path / "LinqChain.cs"
        cs_file.write_text("""
public class Example {
    public void Process() {
        var result = items
            .Where(x => Filter(x))
            .Select(x => Transform(x))
            .ToList();
    }

    private bool Filter(int x) { return true; }
    private int Transform(int x) { return x; }
}
""")

        result = analyze_csharp(tmp_path)

        process = next((s for s in result.symbols if s.name == "Example.Process"), None)
        filter_m = next((s for s in result.symbols if s.name == "Example.Filter"), None)
        transform_m = next((s for s in result.symbols if s.name == "Example.Transform"), None)

        assert process is not None
        assert filter_m is not None
        assert transform_m is not None

        filter_call = next(
            (e for e in result.edges if e.src == process.id and e.dst == filter_m.id),
            None,
        )
        transform_call = next(
            (e for e in result.edges if e.src == process.id and e.dst == transform_m.id),
            None,
        )

        assert filter_call is not None, "Filter call in LINQ chain should be attributed to Process"
        assert transform_call is not None, "Transform call in LINQ chain should be attributed to Process"
