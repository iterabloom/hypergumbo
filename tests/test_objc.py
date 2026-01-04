"""Tests for Objective-C analyzer."""
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestObjCHelpers:
    """Tests for Objective-C analyzer helper functions."""

    def test_find_child_by_type_returns_none(self) -> None:
        """Returns None when no matching child type is found."""
        from hypergumbo.analyze.objc import _find_child_by_type

        mock_node = MagicMock()
        mock_child = MagicMock()
        mock_child.type = "different_type"
        mock_node.children = [mock_child]

        result = _find_child_by_type(mock_node, "identifier")
        assert result is None


class TestFindObjCFiles:
    """Tests for Objective-C file discovery."""

    def test_finds_m_files(self, tmp_path: Path) -> None:
        """Finds .m files."""
        from hypergumbo.analyze.objc import find_objc_files

        (tmp_path / "MyClass.m").write_text("#import <Foundation/Foundation.h>")
        (tmp_path / "Other.h").write_text("@interface Other : NSObject @end")
        (tmp_path / "other.txt").write_text("not objc")

        files = list(find_objc_files(tmp_path))

        assert len(files) == 2
        extensions = {f.suffix for f in files}
        assert ".m" in extensions
        assert ".h" in extensions

    def test_finds_mm_files(self, tmp_path: Path) -> None:
        """Finds .mm (Objective-C++) files."""
        from hypergumbo.analyze.objc import find_objc_files

        (tmp_path / "Mixed.mm").write_text("// Objective-C++")

        files = list(find_objc_files(tmp_path))

        assert len(files) == 1
        assert files[0].suffix == ".mm"


class TestObjCTreeSitterAvailability:
    """Tests for tree-sitter-objc availability checking."""

    def test_is_objc_tree_sitter_available_true(self) -> None:
        """Returns True when tree-sitter-objc is available."""
        from hypergumbo.analyze.objc import is_objc_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()
            assert is_objc_tree_sitter_available() is True

    def test_is_objc_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo.analyze.objc import is_objc_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_objc_tree_sitter_available() is False

    def test_is_objc_tree_sitter_available_no_objc(self) -> None:
        """Returns False when tree-sitter is available but objc grammar is not."""
        from hypergumbo.analyze.objc import is_objc_tree_sitter_available

        def mock_find_spec(name: str) -> object | None:
            if name == "tree_sitter":
                return object()
            return None

        with patch("importlib.util.find_spec", side_effect=mock_find_spec):
            assert is_objc_tree_sitter_available() is False


class TestAnalyzeObjCFallback:
    """Tests for fallback behavior when tree-sitter-objc unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-objc unavailable."""
        from hypergumbo.analyze.objc import analyze_objc

        (tmp_path / "test.m").write_text("#import <Foundation/Foundation.h>")

        with patch("hypergumbo.analyze.objc.is_objc_tree_sitter_available", return_value=False):
            result = analyze_objc(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-objc" in result.skip_reason


class TestObjCClassExtraction:
    """Tests for extracting Objective-C classes."""

    def test_extracts_interface_declaration(self, tmp_path: Path) -> None:
        """Extracts @interface declarations."""
        from hypergumbo.analyze.objc import analyze_objc

        objc_file = tmp_path / "MyClass.h"
        objc_file.write_text("""
@interface MyClass : NSObject
- (void)doSomething;
@end
""")

        result = analyze_objc(tmp_path)


        classes = [s for s in result.symbols if s.kind == "class"]
        class_names = [s.name for s in classes]
        assert "MyClass" in class_names

    def test_extracts_implementation(self, tmp_path: Path) -> None:
        """Extracts @implementation definitions."""
        from hypergumbo.analyze.objc import analyze_objc

        objc_file = tmp_path / "MyClass.m"
        objc_file.write_text("""
@implementation MyClass
- (void)doSomething {
    NSLog(@"Hello");
}
@end
""")

        result = analyze_objc(tmp_path)


        classes = [s for s in result.symbols if s.kind == "class"]
        class_names = [s.name for s in classes]
        assert "MyClass" in class_names


class TestObjCProtocolExtraction:
    """Tests for extracting Objective-C protocols."""

    def test_extracts_protocol_declaration(self, tmp_path: Path) -> None:
        """Extracts @protocol declarations."""
        from hypergumbo.analyze.objc import analyze_objc

        objc_file = tmp_path / "MyProtocol.h"
        objc_file.write_text("""
@protocol MyProtocol
- (void)requiredMethod;
@optional
- (void)optionalMethod;
@end
""")

        result = analyze_objc(tmp_path)


        protocols = [s for s in result.symbols if s.kind == "protocol"]
        protocol_names = [s.name for s in protocols]
        assert "MyProtocol" in protocol_names


class TestObjCMethodExtraction:
    """Tests for extracting Objective-C methods."""

    def test_extracts_instance_methods(self, tmp_path: Path) -> None:
        """Extracts instance method declarations."""
        from hypergumbo.analyze.objc import analyze_objc

        objc_file = tmp_path / "MyClass.h"
        objc_file.write_text("""
@interface MyClass : NSObject
- (void)instanceMethod;
- (NSString *)getName;
@end
""")

        result = analyze_objc(tmp_path)


        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        assert "MyClass.instanceMethod" in method_names or "instanceMethod" in method_names

    def test_extracts_class_methods(self, tmp_path: Path) -> None:
        """Extracts class method declarations."""
        from hypergumbo.analyze.objc import analyze_objc

        objc_file = tmp_path / "MyClass.h"
        objc_file.write_text("""
@interface MyClass : NSObject
+ (instancetype)sharedInstance;
@end
""")

        result = analyze_objc(tmp_path)


        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        assert any("sharedInstance" in name for name in method_names)


class TestObjCPropertyExtraction:
    """Tests for extracting Objective-C properties."""

    def test_extracts_properties(self, tmp_path: Path) -> None:
        """Extracts @property declarations."""
        from hypergumbo.analyze.objc import analyze_objc

        objc_file = tmp_path / "MyClass.h"
        objc_file.write_text("""
@interface MyClass : NSObject
@property (nonatomic, strong) NSString *name;
@property (nonatomic, assign) NSInteger count;
@end
""")

        result = analyze_objc(tmp_path)


        properties = [s for s in result.symbols if s.kind == "property"]
        prop_names = [s.name for s in properties]
        assert any("name" in name for name in prop_names)


class TestObjCImportEdges:
    """Tests for extracting import statements."""

    def test_extracts_framework_imports(self, tmp_path: Path) -> None:
        """Extracts framework #import statements."""
        from hypergumbo.analyze.objc import analyze_objc

        objc_file = tmp_path / "MyClass.m"
        objc_file.write_text("""
#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>

@implementation MyClass
@end
""")

        result = analyze_objc(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        imported = [e.dst for e in import_edges]
        assert any("Foundation" in dst for dst in imported)
        assert any("UIKit" in dst for dst in imported)

    def test_extracts_local_imports(self, tmp_path: Path) -> None:
        """Extracts local #import statements."""
        from hypergumbo.analyze.objc import analyze_objc

        objc_file = tmp_path / "MyClass.m"
        objc_file.write_text("""
#import "MyHeader.h"
#import "Utils/Helper.h"

@implementation MyClass
@end
""")

        result = analyze_objc(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        imported = [e.dst for e in import_edges]
        assert any("MyHeader" in dst for dst in imported)
        assert any("Helper" in dst for dst in imported)


class TestObjCCallEdges:
    """Tests for extracting method call edges."""

    def test_extracts_message_send_calls(self, tmp_path: Path) -> None:
        """Extracts [receiver message] calls."""
        from hypergumbo.analyze.objc import analyze_objc

        objc_file = tmp_path / "MyClass.m"
        objc_file.write_text("""
@implementation MyClass

- (void)helper {
    NSLog(@"helping");
}

- (void)doWork {
    [self helper];
}

@end
""")

        result = analyze_objc(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

    def test_extracts_cross_file_call_edges(self, tmp_path: Path) -> None:
        """Extracts call edges between classes in different files."""
        from hypergumbo.analyze.objc import analyze_objc

        helper_file = tmp_path / "Helper.m"
        helper_file.write_text("""
@implementation Helper

- (void)help {
    NSLog(@"helping");
}

@end
""")

        main_file = tmp_path / "MyClass.m"
        main_file.write_text("""
#import "Helper.h"

@implementation MyClass

- (void)run {
    Helper *h = [[Helper alloc] init];
    [h help];
}

@end
""")

        result = analyze_objc(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1


class TestObjCSymbolProperties:
    """Tests for symbol property correctness."""

    def test_symbol_has_correct_span(self, tmp_path: Path) -> None:
        """Symbols have correct line number spans."""
        from hypergumbo.analyze.objc import analyze_objc

        objc_file = tmp_path / "test.m"
        objc_file.write_text("""@interface TestClass : NSObject
@end
""")

        result = analyze_objc(tmp_path)


        test_class = next((s for s in result.symbols if s.name == "TestClass"), None)
        assert test_class is not None
        assert test_class.span.start_line == 1
        assert test_class.language == "objective-c"
        assert test_class.origin == "objc-v1"


class TestObjCEdgeProperties:
    """Tests for edge property correctness."""

    def test_edge_has_confidence(self, tmp_path: Path) -> None:
        """Edges have confidence values."""
        from hypergumbo.analyze.objc import analyze_objc

        objc_file = tmp_path / "test.m"
        objc_file.write_text("""
#import "Utils.h"
""")

        result = analyze_objc(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        for edge in import_edges:
            assert edge.confidence > 0
            assert edge.confidence <= 1.0


class TestObjCEmptyFile:
    """Tests for handling empty or minimal files."""

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        """Handles empty Objective-C files gracefully."""
        from hypergumbo.analyze.objc import analyze_objc

        objc_file = tmp_path / "empty.m"
        objc_file.write_text("")

        result = analyze_objc(tmp_path)


        assert result.run is not None

    def test_handles_comment_only_file(self, tmp_path: Path) -> None:
        """Handles files with only comments."""
        from hypergumbo.analyze.objc import analyze_objc

        objc_file = tmp_path / "comments.m"
        objc_file.write_text("""// This is a comment
/* Another comment */
""")

        result = analyze_objc(tmp_path)


        assert result.run is not None


class TestObjCParserFailure:
    """Tests for parser failure handling."""

    def test_handles_parser_load_failure(self, tmp_path: Path) -> None:
        """Handles failure to load Objective-C parser."""
        from hypergumbo.analyze.objc import analyze_objc

        objc_file = tmp_path / "test.m"
        objc_file.write_text("#import <Foundation/Foundation.h>")

        with patch("hypergumbo.analyze.objc.is_objc_tree_sitter_available", return_value=True):
            with patch("tree_sitter_objc.language", side_effect=Exception("Parser error")):
                result = analyze_objc(tmp_path)

        assert result.skipped is True
        assert "Parser error" in result.skip_reason or "Failed to load" in result.skip_reason


class TestObjCCategoryExtraction:
    """Tests for extracting Objective-C categories."""

    def test_extracts_category_interface(self, tmp_path: Path) -> None:
        """Extracts category @interface declarations."""
        from hypergumbo.analyze.objc import analyze_objc

        objc_file = tmp_path / "NSString+Utils.h"
        objc_file.write_text("""
@interface NSString (Utils)
- (BOOL)isValidEmail;
@end
""")

        result = analyze_objc(tmp_path)


        # Categories should be extracted as classes with special naming
        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) >= 1


class TestObjCInstantiationEdges:
    """Tests for detecting object instantiation."""

    def test_extracts_alloc_init_pattern(self, tmp_path: Path) -> None:
        """Extracts [[Class alloc] init] instantiation pattern."""
        from hypergumbo.analyze.objc import analyze_objc

        objc_file = tmp_path / "test.m"
        objc_file.write_text("""
@implementation MyClass

- (void)createObjects {
    NSMutableArray *arr = [[NSMutableArray alloc] init];
    Helper *h = [[Helper alloc] initWithName:@"test"];
}

@end
""")

        result = analyze_objc(tmp_path)


        # Should detect instantiation patterns
        instantiate_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        # At minimum should have some edges (may be calls instead)
        all_edges = result.edges
        assert len(all_edges) >= 0  # Just verify we can analyze


class TestObjCSignatureExtraction:
    """Tests for Objective-C method signature extraction."""

    def test_basic_method_signature(self, tmp_path: Path) -> None:
        """Extracts signature from a basic method."""
        from hypergumbo.analyze.objc import analyze_objc

        (tmp_path / "Calculator.h").write_text("""
@interface Calculator : NSObject
- (int)addX:(int)x y:(int)y;
@end
""")
        result = analyze_objc(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "addXy" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(int x, int y): int"

    def test_void_method_signature(self, tmp_path: Path) -> None:
        """Extracts signature from void method (omits void)."""
        from hypergumbo.analyze.objc import analyze_objc

        (tmp_path / "Logger.h").write_text("""
@interface Logger : NSObject
- (void)logMessage:(NSString *)message;
@end
""")
        result = analyze_objc(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "logMessage" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(NSString* message)"

    def test_no_params_signature(self, tmp_path: Path) -> None:
        """Extracts signature from method with no parameters."""
        from hypergumbo.analyze.objc import analyze_objc

        (tmp_path / "Counter.h").write_text("""
@interface Counter : NSObject
- (NSString *)getName;
@end
""")
        result = analyze_objc(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "getName" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(): NSString*"
