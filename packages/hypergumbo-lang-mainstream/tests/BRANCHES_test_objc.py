"""Branch coverage tests for objc.py analyzer.

Tests specific branch paths in the Objective-C analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Class and protocol extraction
- Method and property extraction
- Base class extraction
- Signature extraction
- Import and call edges
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_mainstream.objc import (
    _make_symbol_id,
    _make_file_id,
    analyze_objc,
    find_objc_files,
)


def make_objc_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an Objective-C file with given content."""
    (tmp_path / name).write_text(content)


class TestObjCHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = _make_symbol_id("MyClass.m", 1, 10, "MyClass", "class")
        assert symbol_id == "objc:MyClass.m:1-10:MyClass:class"

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = _make_file_id("MyClass.m")
        assert file_id == "objc:MyClass.m:1-1:file:file"


class TestClassExtraction:
    """Branch coverage for class extraction."""

    def test_class_interface(self, tmp_path: Path) -> None:
        """Test @interface extraction."""
        make_objc_file(tmp_path, "Person.h", """
@interface Person : NSObject
@end
""")
        result = analyze_objc(tmp_path)
        assert not result.skipped

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) >= 1
        assert any(c.name == "Person" for c in classes)

    def test_class_implementation(self, tmp_path: Path) -> None:
        """Test @implementation extraction."""
        make_objc_file(tmp_path, "Person.m", """
@implementation Person
@end
""")
        result = analyze_objc(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]

        assert len(classes) >= 1
        assert any(c.name == "Person" for c in classes)

    def test_class_with_superclass(self, tmp_path: Path) -> None:
        """Test class with superclass."""
        make_objc_file(tmp_path, "Employee.h", """
@interface Employee : Person
@end
""")
        result = analyze_objc(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]

        employee = next((c for c in classes if c.name == "Employee"), None)
        assert employee is not None
        base_classes = (employee.meta or {}).get("base_classes", [])
        assert "Person" in base_classes


class TestProtocolExtraction:
    """Branch coverage for protocol extraction."""

    def test_protocol_declaration(self, tmp_path: Path) -> None:
        """Test @protocol extraction."""
        make_objc_file(tmp_path, "Drawable.h", """
@protocol Drawable
- (void)draw;
@end
""")
        result = analyze_objc(tmp_path)
        protocols = [s for s in result.symbols if s.kind == "protocol"]

        assert len(protocols) >= 1
        assert any(p.name == "Drawable" for p in protocols)


class TestMethodExtraction:
    """Branch coverage for method extraction."""

    def test_method_declaration(self, tmp_path: Path) -> None:
        """Test method declaration extraction."""
        make_objc_file(tmp_path, "Calculator.h", """
@interface Calculator : NSObject
- (int)add:(int)a to:(int)b;
@end
""")
        result = analyze_objc(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]

        assert len(methods) >= 1

    def test_method_definition(self, tmp_path: Path) -> None:
        """Test method definition extraction."""
        make_objc_file(tmp_path, "Calculator.m", """
@implementation Calculator
- (int)add:(int)a to:(int)b {
    return a + b;
}
@end
""")
        result = analyze_objc(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]

        assert len(methods) >= 1
        assert any("add" in m.name for m in methods)


class TestPropertyExtraction:
    """Branch coverage for property extraction."""

    def test_property_declaration(self, tmp_path: Path) -> None:
        """Test @property extraction."""
        make_objc_file(tmp_path, "User.h", """
@interface User : NSObject
@property (nonatomic, strong) NSString *name;
@property (nonatomic, assign) int age;
@end
""")
        result = analyze_objc(tmp_path)
        properties = [s for s in result.symbols if s.kind == "property"]

        assert len(properties) >= 2


class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_import_statement(self, tmp_path: Path) -> None:
        """Test #import creates edge."""
        make_objc_file(tmp_path, "App.m", """
#import <Foundation/Foundation.h>
#import "User.h"

@implementation App
@end
""")
        result = analyze_objc(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]

        assert len(import_edges) >= 1


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_message_send_creates_edge(self, tmp_path: Path) -> None:
        """Test message send creates call edge."""
        make_objc_file(tmp_path, "App.m", """
@implementation App
- (void)setup {
    NSLog(@"Setup");
}

- (void)run {
    [self setup];
}
@end
""")
        result = analyze_objc(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]

        assert len(call_edges) >= 1


class TestFindObjCFiles:
    """Branch coverage for file discovery."""

    def test_finds_m_files(self, tmp_path: Path) -> None:
        """Test .m files are discovered."""
        (tmp_path / "App.m").write_text("@implementation App\n@end")

        files = find_objc_files(tmp_path)
        assert len(files) >= 1
        assert any(f.suffix == ".m" for f in files)

    def test_finds_mm_files(self, tmp_path: Path) -> None:
        """Test .mm files are discovered."""
        (tmp_path / "App.mm").write_text("@implementation App\n@end")

        files = find_objc_files(tmp_path)
        assert len(files) >= 1
        assert any(f.suffix == ".mm" for f in files)

    def test_finds_h_files(self, tmp_path: Path) -> None:
        """Test .h files are discovered."""
        (tmp_path / "App.h").write_text("@interface App\n@end")

        files = find_objc_files(tmp_path)
        assert len(files) >= 1
        assert any(f.suffix == ".h" for f in files)


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_objc_files(self, tmp_path: Path) -> None:
        """Test directory with no Objective-C files."""
        result = analyze_objc(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_objc(self, tmp_path: Path) -> None:
        """Test minimal Objective-C file."""
        make_objc_file(tmp_path, "App.m", """
@implementation App
@end
""")
        result = analyze_objc(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_objc_file(tmp_path, "App.m", """
@implementation App
@end
""")
        result = analyze_objc(tmp_path)
        assert result.run is not None


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        import hypergumbo_lang_mainstream.objc as objc_module
        from unittest.mock import patch
        import pytest

        with patch.object(objc_module, "is_objc_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="tree-sitter-objc not available"):
                result = objc_module.analyze_objc(tmp_path)
        assert result.skipped is True
        assert "tree-sitter-objc" in result.skip_reason


class TestClassMethods:
    """Branch coverage for class method extraction."""

    def test_class_method(self, tmp_path: Path) -> None:
        """Test + class method extraction."""
        make_objc_file(tmp_path, "Factory.h", """
@interface Factory : NSObject
+ (instancetype)sharedInstance;
@end
""")
        result = analyze_objc(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]
        # Should extract class method
        assert len(methods) >= 1


class TestProtocolConformance:
    """Branch coverage for protocol conformance."""

    def test_class_with_protocols(self, tmp_path: Path) -> None:
        """Test class conforming to protocols."""
        make_objc_file(tmp_path, "ViewController.h", """
@protocol MyDelegate
@end

@interface ViewController : UIViewController <UITableViewDelegate, MyDelegate>
@end
""")
        result = analyze_objc(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]

        vc = next((c for c in classes if c.name == "ViewController"), None)
        assert vc is not None
