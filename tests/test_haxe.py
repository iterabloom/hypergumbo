"""Tests for the Haxe language analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import haxe as haxe_module
from hypergumbo_lang_extended1.haxe import (
    HaxeAnalysisResult,
    analyze_haxe,
    find_haxe_files,
    is_haxe_tree_sitter_available,
)


def make_haxe_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Haxe file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindHaxeFiles:
    """Tests for find_haxe_files function."""

    def test_finds_haxe_files(self, tmp_path: Path) -> None:
        make_haxe_file(tmp_path, "Main.hx", "class Main {}")
        make_haxe_file(tmp_path, "Helper.hx", "class Helper {}")
        files = list(find_haxe_files(tmp_path))
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"Main.hx", "Helper.hx"}

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = list(find_haxe_files(tmp_path))
        assert files == []


class TestIsHaxeTreeSitterAvailable:
    """Tests for is_haxe_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_haxe_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(haxe_module, "is_haxe_tree_sitter_available", return_value=False):
            assert haxe_module.is_haxe_tree_sitter_available() is False


class TestAnalyzeHaxe:
    """Tests for analyze_haxe function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_haxe_file(tmp_path, "Test.hx", "class Test {}")
        with patch.object(haxe_module, "is_haxe_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Haxe analysis skipped"):
                result = haxe_module.analyze_haxe(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_extracts_classes(self, tmp_path: Path) -> None:
        make_haxe_file(tmp_path, "MyClass.hx", """class MyClass {
    public function new() {}
}
""")
        result = analyze_haxe(tmp_path)
        assert not result.skipped
        cls = next((s for s in result.symbols if s.name == "MyClass"), None)
        assert cls is not None
        assert cls.kind == "class"
        assert cls.language == "haxe"

    def test_extracts_abstract_classes(self, tmp_path: Path) -> None:
        make_haxe_file(tmp_path, "Animal.hx", """abstract class Animal {
    public function new() {}
}
""")
        result = analyze_haxe(tmp_path)
        assert not result.skipped
        cls = next((s for s in result.symbols if s.name == "Animal"), None)
        assert cls is not None
        assert cls.kind == "class"
        assert cls.meta["is_abstract"] is True

    def test_extracts_interfaces(self, tmp_path: Path) -> None:
        make_haxe_file(tmp_path, "IAnimal.hx", """interface IAnimal {
    public function speak(): String;
}
""")
        result = analyze_haxe(tmp_path)
        assert not result.skipped
        iface = next((s for s in result.symbols if s.name == "IAnimal"), None)
        assert iface is not None
        assert iface.kind == "interface"

    def test_extracts_functions(self, tmp_path: Path) -> None:
        make_haxe_file(tmp_path, "Calc.hx", """class Calc {
    public function add(a: Int, b: Int): Int {
        return a + b;
    }
}
""")
        result = analyze_haxe(tmp_path)
        assert not result.skipped
        func = next((s for s in result.symbols if s.name == "Calc.add"), None)
        assert func is not None
        assert func.kind == "function"
        assert "function add" in func.signature
        assert "Int" in func.signature

    def test_extracts_static_functions(self, tmp_path: Path) -> None:
        make_haxe_file(tmp_path, "Utils.hx", """class Utils {
    public static function helper(): Void {
    }
}
""")
        result = analyze_haxe(tmp_path)
        func = next((s for s in result.symbols if s.name == "Utils.helper"), None)
        assert func is not None
        assert func.meta["is_static"] is True

    def test_extracts_constructor(self, tmp_path: Path) -> None:
        make_haxe_file(tmp_path, "Person.hx", """class Person {
    public function new() {}
}
""")
        result = analyze_haxe(tmp_path)
        ctor = next((s for s in result.symbols if "new" in s.name), None)
        assert ctor is not None
        assert ctor.kind == "function"

    def test_function_param_count(self, tmp_path: Path) -> None:
        make_haxe_file(tmp_path, "Math.hx", """class Math {
    public function calculate(x: Int, y: Int, z: Int): Int {
        return x + y + z;
    }
}
""")
        result = analyze_haxe(tmp_path)
        func = next((s for s in result.symbols if s.name == "Math.calculate"), None)
        assert func is not None
        assert func.meta["param_count"] == 3

    def test_extracts_call_edges(self, tmp_path: Path) -> None:
        make_haxe_file(tmp_path, "Test.hx", """class Test {
    function helper(): Void {}

    function main(): Void {
        helper();
    }
}
""")
        result = analyze_haxe(tmp_path)
        assert not result.skipped
        # main calls helper
        edge = next(
            (e for e in result.edges if "main" in e.src and "helper" in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "calls"
        assert edge.confidence == 1.0

    def test_recursive_calls(self, tmp_path: Path) -> None:
        make_haxe_file(tmp_path, "Factorial.hx", """class Factorial {
    function factorial(n: Int): Int {
        if (n <= 1) return 1;
        return n * factorial(n - 1);
    }
}
""")
        result = analyze_haxe(tmp_path)
        # factorial calls itself
        edge = next(
            (e for e in result.edges if "factorial" in e.src and "factorial" in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "calls"

    def test_filters_builtins(self, tmp_path: Path) -> None:
        make_haxe_file(tmp_path, "Test.hx", """class Test {
    function main(): Void {
        trace("hello");
        var x = Math.sqrt(4);
    }
}
""")
        result = analyze_haxe(tmp_path)
        # Should not have edges to trace or sqrt (builtins)
        builtin_edges = [e for e in result.edges if any(
            b in e.dst.lower() for b in ["trace", "sqrt"]
        )]
        assert len(builtin_edges) == 0

    def test_unresolved_call_target(self, tmp_path: Path) -> None:
        make_haxe_file(tmp_path, "Test.hx", """class Test {
    function main(): Void {
        externalFunc();
    }
}
""")
        result = analyze_haxe(tmp_path)
        edge = next(
            (e for e in result.edges if "externalFunc" in e.dst),
            None
        )
        assert edge is not None
        assert "unresolved" in edge.dst
        assert edge.confidence == 0.6

    def test_pass_id(self, tmp_path: Path) -> None:
        make_haxe_file(tmp_path, "Test.hx", """class Test {
    function foo(): Void {}
}
""")
        result = analyze_haxe(tmp_path)
        func = next((s for s in result.symbols if "foo" in s.name), None)
        assert func is not None
        assert func.origin == "haxe.tree_sitter"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_haxe_file(tmp_path, "Test.hx", "class Test {}")
        result = analyze_haxe(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "haxe.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_haxe(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_haxe_file(tmp_path, "Test.hx", """class Test {
    function myFunc(): Void {}
}
""")
        result = analyze_haxe(tmp_path)
        func = next((s for s in result.symbols if "myFunc" in s.name), None)
        assert func is not None
        assert func.id == func.stable_id
        assert "haxe:" in func.id
        assert "Test.hx" in func.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_haxe_file(tmp_path, "Test.hx", """class Test {
    function myFunc(): Void {}
}
""")
        result = analyze_haxe(tmp_path)
        func = next((s for s in result.symbols if "myFunc" in s.name), None)
        assert func is not None
        assert func.span is not None
        assert func.span.start_line >= 1
        assert func.span.end_line >= func.span.start_line

    def test_public_visibility(self, tmp_path: Path) -> None:
        make_haxe_file(tmp_path, "Test.hx", """class Test {
    public function publicFunc(): Void {}
    private function privateFunc(): Void {}
}
""")
        result = analyze_haxe(tmp_path)
        pub = next((s for s in result.symbols if "publicFunc" in s.name), None)
        priv = next((s for s in result.symbols if "privateFunc" in s.name), None)
        assert pub is not None
        assert priv is not None
        assert pub.meta["is_public"] is True
        assert priv.meta["is_public"] is False
