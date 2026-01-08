"""Tests for Zig analyzer.

Tests for the tree-sitter-based Zig analyzer, verifying symbol extraction,
edge detection, and graceful degradation when tree-sitter is unavailable.
"""
import pytest
from pathlib import Path
from unittest.mock import patch

from hypergumbo.analyze.zig import (
    analyze_zig,
    find_zig_files,
    is_zig_tree_sitter_available,
    PASS_ID,
)


@pytest.fixture
def zig_repo(tmp_path: Path) -> Path:
    """Create a minimal Zig repository for testing."""
    src = tmp_path / "src"
    src.mkdir()

    # Main library file
    (src / "lib.zig").write_text(
        """const std = @import("std");
const math = @import("math.zig");

pub fn main() !void {
    const stdout = std.io.getStdOut().writer();
    const result = math.calculate(5, 3);
    try stdout.print("Result: {}\\n", .{result});
}

fn helperFunction(x: i32) i32 {
    return x * 2;
}

pub const Calculator = struct {
    value: i32,

    pub fn init(val: i32) Calculator {
        return .{ .value = val };
    }

    pub fn add(self: Calculator, other: i32) i32 {
        return self.value + other;
    }

    pub fn compute(self: Calculator) i32 {
        return helperFunction(self.value);
    }
};

test "calculator" {
    const calc = Calculator.init(10);
    try std.testing.expect(calc.add(5) == 15);
}
"""
    )

    # Math module
    (src / "math.zig").write_text(
        """const std = @import("std");

pub fn calculate(a: i32, b: i32) i32 {
    return add(a, b);
}

fn add(a: i32, b: i32) i32 {
    return a + b;
}

fn multiply(a: i32, b: i32) i32 {
    return a * b;
}

pub const MathError = error {
    Overflow,
    DivisionByZero,
};

pub fn safeDivide(a: i32, b: i32) !i32 {
    if (b == 0) return MathError.DivisionByZero;
    return @divTrunc(a, b);
}
"""
    )

    # Types module
    (src / "types.zig").write_text(
        """pub const Point = struct {
    x: i32,
    y: i32,

    pub fn distance(self: Point) f64 {
        const dx = @intToFloat(f64, self.x);
        const dy = @intToFloat(f64, self.y);
        return @sqrt(dx * dx + dy * dy);
    }
};

pub const Color = enum {
    red,
    green,
    blue,

    pub fn toRgb(self: Color) u32 {
        return switch (self) {
            .red => 0xFF0000,
            .green => 0x00FF00,
            .blue => 0x0000FF,
        };
    }
};

pub const Value = union(enum) {
    int: i32,
    float: f64,
    string: []const u8,
};
"""
    )

    return tmp_path


class TestZigFileDiscovery:
    """Tests for Zig file discovery."""

    def test_finds_zig_files(self, zig_repo: Path) -> None:
        """Should find all .zig files."""
        files = list(find_zig_files(zig_repo))
        assert len(files) == 3

    def test_file_names(self, zig_repo: Path) -> None:
        """Should find expected files."""
        files = [f.name for f in find_zig_files(zig_repo)]
        assert "lib.zig" in files
        assert "math.zig" in files
        assert "types.zig" in files


class TestZigSymbolExtraction:
    """Tests for symbol extraction from Zig files."""

    def test_extracts_functions(self, zig_repo: Path) -> None:
        """Should extract function declarations."""
        result = analyze_zig(zig_repo)


        func_symbols = [s for s in result.symbols if s.kind == "function"]
        func_names = {s.name for s in func_symbols}
        assert "main" in func_names
        assert "helperFunction" in func_names
        assert "calculate" in func_names
        assert "add" in func_names

    def test_extracts_structs(self, zig_repo: Path) -> None:
        """Should extract struct declarations."""
        result = analyze_zig(zig_repo)


        struct_symbols = [s for s in result.symbols if s.kind == "struct"]
        struct_names = {s.name for s in struct_symbols}
        assert "Calculator" in struct_names
        assert "Point" in struct_names

    def test_extracts_enums(self, zig_repo: Path) -> None:
        """Should extract enum declarations."""
        result = analyze_zig(zig_repo)


        enum_symbols = [s for s in result.symbols if s.kind == "enum"]
        enum_names = {s.name for s in enum_symbols}
        assert "Color" in enum_names

    def test_extracts_unions(self, zig_repo: Path) -> None:
        """Should extract union declarations."""
        result = analyze_zig(zig_repo)


        union_symbols = [s for s in result.symbols if s.kind == "union"]
        union_names = {s.name for s in union_symbols}
        assert "Value" in union_names

    def test_extracts_error_sets(self, zig_repo: Path) -> None:
        """Should extract error set declarations."""
        result = analyze_zig(zig_repo)


        error_symbols = [s for s in result.symbols if s.kind == "error_set"]
        error_names = {s.name for s in error_symbols}
        assert "MathError" in error_names

    def test_extracts_methods(self, zig_repo: Path) -> None:
        """Should extract struct methods (functions with self parameter)."""
        result = analyze_zig(zig_repo)


        method_symbols = [s for s in result.symbols if s.kind == "method"]
        method_names = {s.name for s in method_symbols}
        # In Zig, methods are functions with a `self` parameter
        # init() doesn't take self, so it's a function (static factory method)
        # add() and compute() take self, so they're methods
        assert "Calculator.add" in method_names or "add" in method_names
        assert "Calculator.compute" in method_names or "compute" in method_names

    def test_symbols_have_correct_language(self, zig_repo: Path) -> None:
        """All symbols should have language='zig'."""
        result = analyze_zig(zig_repo)


        for symbol in result.symbols:
            assert symbol.language == "zig"

    def test_symbols_have_spans(self, zig_repo: Path) -> None:
        """All symbols should have valid span information."""
        result = analyze_zig(zig_repo)


        for symbol in result.symbols:
            assert symbol.span is not None
            assert symbol.span.start_line > 0
            assert symbol.span.end_line >= symbol.span.start_line


class TestZigEdgeExtraction:
    """Tests for edge extraction from Zig files."""

    def test_extracts_import_edges(self, zig_repo: Path) -> None:
        """Should extract @import edges."""
        result = analyze_zig(zig_repo)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        # Should have imports for std and math.zig
        assert len(import_edges) >= 2

    def test_extracts_call_edges(self, zig_repo: Path) -> None:
        """Should extract function call edges."""
        result = analyze_zig(zig_repo)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should have calls to calculate, helperFunction, add, etc.
        assert len(call_edges) >= 1

    def test_edges_have_confidence(self, zig_repo: Path) -> None:
        """All edges should have confidence values."""
        result = analyze_zig(zig_repo)


        for edge in result.edges:
            assert 0.0 <= edge.confidence <= 1.0


class TestZigAnalysisRun:
    """Tests for analysis run metadata."""

    def test_creates_analysis_run(self, zig_repo: Path) -> None:
        """Should create an AnalysisRun with metadata."""
        result = analyze_zig(zig_repo)


        assert result.run is not None
        assert result.run.pass_id == PASS_ID
        assert result.run.files_analyzed == 3
        assert result.run.duration_ms >= 0

    def test_symbols_reference_run(self, zig_repo: Path) -> None:
        """Symbols should reference the analysis run."""
        result = analyze_zig(zig_repo)


        for symbol in result.symbols:
            assert symbol.origin == PASS_ID
            assert symbol.origin_run_id == result.run.execution_id


class TestZigGracefulDegradation:
    """Tests for graceful degradation when tree-sitter unavailable."""

    def test_returns_skipped_when_unavailable(self) -> None:
        """Should return skipped result when tree-sitter unavailable."""
        with patch(
            "hypergumbo.analyze.zig.is_zig_tree_sitter_available",
            return_value=False,
        ):
            import warnings
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result = analyze_zig(Path("/nonexistent"))
                assert result.skipped
                assert "tree-sitter-zig" in result.skip_reason
                assert len(w) == 1


class TestZigTreeSitterAvailability:
    """Tests for tree-sitter availability detection."""

    def test_detects_missing_tree_sitter(self) -> None:
        """Should detect when tree-sitter is not installed."""
        with patch("importlib.util.find_spec", return_value=None):
            assert not is_zig_tree_sitter_available()

    def test_detects_missing_zig_grammar(self) -> None:
        """Should detect when tree-sitter-zig is not installed."""
        def find_spec_mock(name: str):
            if name == "tree_sitter":
                return True
            return None

        with patch("importlib.util.find_spec", side_effect=find_spec_mock):
            assert not is_zig_tree_sitter_available()


class TestZigSpecialCases:
    """Tests for special Zig syntax cases."""

    def test_handles_empty_files(self, tmp_path: Path) -> None:
        """Should handle empty Zig files gracefully."""
        (tmp_path / "empty.zig").write_text("")

        result = analyze_zig(tmp_path)


        # Should not crash
        assert result.run is not None

    def test_handles_io_errors(self, tmp_path: Path) -> None:
        """Should handle IO errors gracefully."""
        result = analyze_zig(tmp_path)


        # Empty repo should not crash
        assert result.symbols == []
        assert result.edges == []

    def test_handles_test_declarations(self, tmp_path: Path) -> None:
        """Should extract test declarations."""
        (tmp_path / "tests.zig").write_text(
            """const std = @import("std");

test "simple test" {
    try std.testing.expect(true);
}

test "another test" {
    const x: i32 = 42;
    try std.testing.expect(x == 42);
}
"""
        )

        result = analyze_zig(tmp_path)


        test_symbols = [s for s in result.symbols if s.kind == "test"]
        assert len(test_symbols) >= 2

    def test_handles_comptime_functions(self, tmp_path: Path) -> None:
        """Should handle comptime functions."""
        (tmp_path / "comptime.zig").write_text(
            """fn comptimeAdd(comptime a: i32, comptime b: i32) i32 {
    return a + b;
}

pub fn regularFunc() void {
    const x = comptimeAdd(1, 2);
    _ = x;
}
"""
        )

        result = analyze_zig(tmp_path)


        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = {s.name for s in funcs}
        assert "comptimeAdd" in func_names
        assert "regularFunc" in func_names

    def test_handles_nested_structs(self, tmp_path: Path) -> None:
        """Should handle nested struct definitions."""
        (tmp_path / "nested.zig").write_text(
            """pub const Outer = struct {
    inner: Inner,

    pub const Inner = struct {
        value: i32,
    };

    pub fn create() Outer {
        return .{ .inner = .{ .value = 0 } };
    }
};
"""
        )

        result = analyze_zig(tmp_path)


        structs = [s for s in result.symbols if s.kind == "struct"]
        # Should find both Outer and Inner
        assert len(structs) >= 1  # At minimum Outer

    def test_handles_extern_functions(self, tmp_path: Path) -> None:
        """Should handle extern function declarations."""
        (tmp_path / "extern.zig").write_text(
            """pub extern "c" fn puts(s: [*:0]const u8) c_int;

pub fn wrapper() void {
    _ = puts("Hello");
}
"""
        )

        result = analyze_zig(tmp_path)


        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = {s.name for s in funcs}
        assert "puts" in func_names or "wrapper" in func_names

    def test_handles_method_calls(self, tmp_path: Path) -> None:
        """Should detect method calls on structs."""
        (tmp_path / "methods.zig").write_text(
            """pub const Counter = struct {
    count: i32,

    pub fn increment(self: *Counter) void {
        self.count += 1;
    }

    pub fn getCount(self: Counter) i32 {
        return self.count;
    }
};

pub fn useCounter() void {
    var c = Counter{ .count = 0 };
    c.increment();
    _ = c.getCount();
}
"""
        )

        result = analyze_zig(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # useCounter should call increment and getCount
        assert len(call_edges) >= 1

    def test_handles_direct_function_calls(self, tmp_path: Path) -> None:
        """Should detect direct function calls."""
        (tmp_path / "direct.zig").write_text(
            """fn helper() i32 {
    return 42;
}

fn caller() i32 {
    return helper();
}
"""
        )

        result = analyze_zig(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # caller should call helper
        assert len(call_edges) >= 1


class TestZigSignatureExtraction:
    """Tests for Zig function signature extraction."""

    def test_typed_params_with_return_type(self, tmp_path: Path) -> None:
        """Extracts signature from function with typed params and return type."""
        (tmp_path / "Calculator.zig").write_text(
            """fn add(x: i32, y: i32) i32 {
    return x + y;
}
"""
        )
        result = analyze_zig(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "add"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(x: i32, y: i32) i32"

    def test_void_return_type_omitted(self, tmp_path: Path) -> None:
        """Omits void return type from signature."""
        (tmp_path / "Logger.zig").write_text(
            """fn log(message: []const u8) void {
    _ = message;
}
"""
        )
        result = analyze_zig(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "log"]
        assert len(funcs) == 1
        # void is omitted
        assert funcs[0].signature == "(message: []const u8)"

    def test_no_params_function(self, tmp_path: Path) -> None:
        """Extracts signature from function with no parameters."""
        (tmp_path / "Simple.zig").write_text(
            """fn getAnswer() i32 {
    return 42;
}
"""
        )
        result = analyze_zig(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "getAnswer"]
        assert len(funcs) == 1
        assert funcs[0].signature == "() i32"
