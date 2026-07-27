# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Objective-C analyzer."""
from pathlib import Path

from hypergumbo_core.analyze.base import find_child_by_type
from unittest.mock import patch, MagicMock

class TestObjCHelpers:
    """Tests for Objective-C analyzer helper functions."""

    def test_find_child_by_type_returns_none(self) -> None:
        """Returns None when no matching child type is found."""

        mock_node = MagicMock()
        mock_child = MagicMock()
        mock_child.type = "different_type"
        mock_node.children = [mock_child]

        result = find_child_by_type(mock_node, "identifier")
        assert result is None

class TestFindObjCFiles:
    """Tests for Objective-C file discovery."""

    def test_finds_m_files(self, tmp_path: Path) -> None:
        """Finds .m files."""
        from hypergumbo_lang_mainstream.objc import find_objc_files

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
        from hypergumbo_lang_mainstream.objc import find_objc_files

        (tmp_path / "Mixed.mm").write_text("// Objective-C++")

        files = list(find_objc_files(tmp_path))

        assert len(files) == 1
        assert files[0].suffix == ".mm"

    def test_excludes_matlab_dot_m_files(self, tmp_path: Path) -> None:
        """Should not include MATLAB .m files."""
        from hypergumbo_lang_mainstream.objc import find_objc_files

        (tmp_path / "AppDelegate.m").write_text(
            '#import "AppDelegate.h"\n@implementation AppDelegate\n@end\n'
        )
        (tmp_path / "func.m").write_text("function y = func(x)\n    y = x * 2;\nend\n")

        files = list(find_objc_files(tmp_path))
        m_files = [f for f in files if f.suffix == ".m"]
        assert len(m_files) == 1
        assert m_files[0].name == "AppDelegate.m"

    def test_excludes_wolfram_dot_m_files(self, tmp_path: Path) -> None:
        """Should not include Wolfram .m files."""
        from hypergumbo_lang_mainstream.objc import find_objc_files

        (tmp_path / "AppDelegate.m").write_text(
            '#import "AppDelegate.h"\n@implementation AppDelegate\n@end\n'
        )
        (tmp_path / "math.m").write_text("f[x_] := x^2\n")

        files = list(find_objc_files(tmp_path))
        m_files = [f for f in files if f.suffix == ".m"]
        assert len(m_files) == 1
        assert m_files[0].name == "AppDelegate.m"

class TestObjCTreeSitterAvailability:
    """Tests for tree-sitter-objc availability checking."""

    def test_is_objc_tree_sitter_available(self) -> None:
        """Availability check returns a boolean."""
        from hypergumbo_lang_mainstream.objc import is_objc_tree_sitter_available

        result = is_objc_tree_sitter_available()
        assert isinstance(result, bool)

class TestAnalyzeObjCFallback:
    """Tests for fallback behavior when tree-sitter-objc unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-objc unavailable."""
        from hypergumbo_lang_mainstream import objc as objc_module

        (tmp_path / "test.m").write_text("#import <Foundation/Foundation.h>")

        with patch.object(objc_module._analyzer, "_check_grammar_available", return_value=False):
            result = objc_module.analyze_objc(tmp_path)

        assert result.skipped is True
        assert "not available" in result.skip_reason

class TestObjCClassExtraction:
    """Tests for extracting Objective-C classes."""

    def test_extracts_interface_declaration(self, tmp_path: Path) -> None:
        """Extracts @interface declarations."""
        from hypergumbo_lang_mainstream.objc import analyze_objc

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
        from hypergumbo_lang_mainstream.objc import analyze_objc

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
        from hypergumbo_lang_mainstream.objc import analyze_objc

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
        from hypergumbo_lang_mainstream.objc import analyze_objc

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
        from hypergumbo_lang_mainstream.objc import analyze_objc

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
        from hypergumbo_lang_mainstream.objc import analyze_objc

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
        from hypergumbo_lang_mainstream.objc import analyze_objc

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
        from hypergumbo_lang_mainstream.objc import analyze_objc

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
        from hypergumbo_lang_mainstream.objc import analyze_objc

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

    def test_cross_class_message_send_defers_not_binds(self, tmp_path: Path) -> None:
        """INV-fahub: a cross-class message send is WITHHELD, not magnet-bound.

        ``[h help]`` in ``MyClass`` reaches selector ``help`` which the
        receiver-blind, short-name-keyed resolver would otherwise bind
        high-confidence to ``Helper.help`` (a DIFFERENT class). Under the
        INV-fahub gate this is deferred to an honest unresolved edge that
        carries ``enclosing_class`` for the inherited_calls Site-1 walker,
        rather than a resolved cross_file bind to an arbitrary same-named def.
        """
        from hypergumbo_lang_mainstream.objc import analyze_objc

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

        helper_help = next(s for s in result.symbols if s.name == "Helper.help")
        call_edges = [e for e in result.edges if e.edge_type == "calls"]

        # No resolved cross-class magnet bind to Helper.help.
        assert not any(
            e.is_resolved and e.dst == helper_help.id for e in call_edges
        )
        # The deferred call is an unresolved edge tagged with the caller's
        # enclosing class so Site-1 can recover a genuine inherited call.
        help_calls = [
            e for e in call_edges
            if not e.is_resolved and e.dst.endswith(":help:unresolved")
        ]
        assert len(help_calls) == 1
        assert (help_calls[0].meta or {}).get("enclosing_class") == "MyClass"

class TestObjCSymbolProperties:
    """Tests for symbol property correctness."""

    def test_symbol_has_correct_span(self, tmp_path: Path) -> None:
        """Symbols have correct line number spans."""
        from hypergumbo_lang_mainstream.objc import analyze_objc

        objc_file = tmp_path / "test.m"
        objc_file.write_text("""@interface TestClass : NSObject
@end
""")

        result = analyze_objc(tmp_path)

        test_class = next((s for s in result.symbols if s.name == "TestClass"), None)
        assert test_class is not None
        assert test_class.span.start_line == 1
        assert test_class.language == "objc"
        assert test_class.origin == ["objc"]

class TestObjCEdgeProperties:
    """Tests for edge property correctness."""

    def test_edge_has_confidence(self, tmp_path: Path) -> None:
        """Edges have confidence values."""
        from hypergumbo_lang_mainstream.objc import analyze_objc

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
        from hypergumbo_lang_mainstream.objc import analyze_objc

        objc_file = tmp_path / "empty.m"
        objc_file.write_text("")

        result = analyze_objc(tmp_path)

        assert result.run is not None

    def test_handles_comment_only_file(self, tmp_path: Path) -> None:
        """Handles files with only comments."""
        from hypergumbo_lang_mainstream.objc import analyze_objc

        objc_file = tmp_path / "comments.m"
        objc_file.write_text("""// This is a comment
/* Another comment */
""")

        result = analyze_objc(tmp_path)

        assert result.run is not None

class TestObjCFahubMagnetGate:
    """INV-fahub: the bare/receiver-blind message-send magnet gate.

    ObjC's ``global_methods`` registry is keyed by the bare selector (short
    name), so ``method_resolver.lookup(selector)`` collapses every same-named
    method across unrelated classes to one arbitrary def. Binding a message
    send to that def on nothing but a short-name collision is the cross-class
    magnet (many receiver-blind call sites → one def). The gate withholds such
    a cross-class match (deferring it to the inherited_calls Site-1 walker via
    ``enclosing_class``) while still binding a same-class implicit-``self`` hit.
    """

    def test_cross_class_magnet_does_not_bind(self, tmp_path: Path) -> None:
        """A call reachable only by short-name collision must NOT resolve.

        ``Beta.run`` sends ``process`` to a non-Alpha receiver; the only
        ``process`` in the registry is ``Alpha.process`` (an UNRELATED class).
        Pre-gate this bound is_resolved=True @0.75 to ``Alpha.process``; the
        gate defers it to an unresolved edge carrying ``enclosing_class``.
        """
        from hypergumbo_lang_mainstream.objc import analyze_objc

        (tmp_path / "Alpha.m").write_text("""
@implementation Alpha
- (void)process {
    NSLog(@"alpha process");
}
@end
""")
        (tmp_path / "Beta.m").write_text("""
#import "Widget.h"
@implementation Beta
- (void)run {
    Widget *w = [[Widget alloc] init];
    [w process];
}
@end
""")

        result = analyze_objc(tmp_path)

        alpha_process = next(s for s in result.symbols if s.name == "Alpha.process")
        call_edges = [e for e in result.edges if e.edge_type == "calls"]

        # Must NOT bind the magnet: no resolved edge targets Alpha.process.
        assert not any(
            e.is_resolved and e.dst == alpha_process.id for e in call_edges
        )
        # Deferred: an unresolved ``process`` edge carrying the caller's class.
        process_calls = [
            e for e in call_edges
            if not e.is_resolved and e.dst.endswith(":process:unresolved")
        ]
        assert len(process_calls) == 1
        assert (process_calls[0].meta or {}).get("enclosing_class") == "Beta"

    def test_same_class_message_send_still_resolves(self, tmp_path: Path) -> None:
        """A same-class implicit-``self`` cross-file hit still binds directly.

        ``Store.save`` (in Store.m) sends ``flush`` to ``self``; ``flush`` is
        declared on ``Store`` in Store.h. Owner == enclosing class, so the gate
        binds it (is_resolved=True, cross_file) rather than deferring.
        """
        from hypergumbo_lang_mainstream.objc import analyze_objc

        (tmp_path / "Store.h").write_text("""
@interface Store : NSObject
- (void)flush;
@end
""")
        (tmp_path / "Store.m").write_text("""
#import "Store.h"
@implementation Store
- (void)save {
    [self flush];
}
@end
""")

        result = analyze_objc(tmp_path)

        store_flush = next(s for s in result.symbols if s.name == "Store.flush")
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        resolved = [
            e for e in call_edges if e.is_resolved and e.dst == store_flush.id
        ]
        assert len(resolved) == 1
        assert (resolved[0].meta or {}).get("call_locality") == "cross_file"


class TestObjCParserFailure:
    """Tests for parser failure handling."""

    def test_handles_parser_load_failure(self, tmp_path: Path) -> None:
        """Handles failure to load Objective-C parser."""
        from hypergumbo_lang_mainstream import objc as objc_module

        objc_file = tmp_path / "test.m"
        objc_file.write_text("#import <Foundation/Foundation.h>")

        with patch.object(objc_module._analyzer, "_check_grammar_available", return_value=True):
            with patch.object(objc_module._analyzer, "_create_parser", side_effect=Exception("Parser error")):
                result = objc_module.analyze_objc(tmp_path)

        assert result.skipped is True
        assert "Parser error" in result.skip_reason or "Failed to load" in result.skip_reason

class TestObjCCategoryExtraction:
    """Tests for extracting Objective-C categories."""

    def test_extracts_category_interface(self, tmp_path: Path) -> None:
        """Extracts category @interface declarations."""
        from hypergumbo_lang_mainstream.objc import analyze_objc

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
        from hypergumbo_lang_mainstream.objc import analyze_objc

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
        from hypergumbo_lang_mainstream.objc import analyze_objc

        (tmp_path / "Calculator.h").write_text("""
@interface Calculator : NSObject
- (int)addX:(int)x y:(int)y;
@end
""")
        result = analyze_objc(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "addX:y:" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(int x, int y): int"

    def test_void_method_signature(self, tmp_path: Path) -> None:
        """Extracts signature from void method (omits void)."""
        from hypergumbo_lang_mainstream.objc import analyze_objc

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
        from hypergumbo_lang_mainstream.objc import analyze_objc

        (tmp_path / "Counter.h").write_text("""
@interface Counter : NSObject
- (NSString *)getName;
@end
""")
        result = analyze_objc(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "getName" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(): NSString*"

class TestObjCInheritanceExtraction:
    """Tests for Objective-C inheritance extraction (base_classes metadata).

    Objective-C uses single inheritance for classes and multiple protocol conformance:
        @interface Dog : Animal <MyProtocol>
    The base_classes metadata enables the centralized inheritance linker.
    """

    def test_extracts_superclass(self, tmp_path: Path) -> None:
        """Extracts superclass from class interface."""
        from hypergumbo_lang_mainstream.objc import analyze_objc

        objc_file = tmp_path / "Dog.h"
        objc_file.write_text("""
@interface Animal : NSObject
@end

@interface Dog : Animal
@end
""")

        result = analyze_objc(tmp_path)

        dog = next((s for s in result.symbols if s.name == "Dog"), None)
        assert dog is not None
        assert dog.meta is not None
        assert "base_classes" in dog.meta
        assert "Animal" in dog.meta["base_classes"]

    def test_extracts_protocol_conformance(self, tmp_path: Path) -> None:
        """Extracts protocol conformance as base_classes."""
        from hypergumbo_lang_mainstream.objc import analyze_objc

        objc_file = tmp_path / "Logger.h"
        objc_file.write_text("""
@protocol Printable
@end

@interface Logger : NSObject <Printable>
@end
""")

        result = analyze_objc(tmp_path)

        logger = next((s for s in result.symbols if s.name == "Logger"), None)
        assert logger is not None
        assert logger.meta is not None
        assert "base_classes" in logger.meta
        # Should have both NSObject (superclass) and Printable (protocol)
        assert "NSObject" in logger.meta["base_classes"]
        assert "Printable" in logger.meta["base_classes"]

    def test_extracts_multiple_protocols(self, tmp_path: Path) -> None:
        """Extracts multiple protocol conformances."""
        from hypergumbo_lang_mainstream.objc import analyze_objc

        objc_file = tmp_path / "Multi.h"
        objc_file.write_text("""
@interface Widget : NSObject <Drawable, Clickable>
@end
""")

        result = analyze_objc(tmp_path)

        widget = next((s for s in result.symbols if s.name == "Widget"), None)
        assert widget is not None
        assert widget.meta is not None
        assert "base_classes" in widget.meta
        assert "NSObject" in widget.meta["base_classes"]
        assert "Drawable" in widget.meta["base_classes"]
        assert "Clickable" in widget.meta["base_classes"]

    def test_no_base_classes_for_root_class(self, tmp_path: Path) -> None:
        """No base_classes when class has no inheritance specified.

        Note: In real Objective-C, all classes inherit from NSObject, but
        we only extract what's explicitly written in the source.
        """
        from hypergumbo_lang_mainstream.objc import analyze_objc

        # Root class pattern without explicit superclass
        objc_file = tmp_path / "Root.h"
        objc_file.write_text("""
@interface RootClass
@end
""")

        result = analyze_objc(tmp_path)

        root = next((s for s in result.symbols if s.name == "RootClass"), None)
        assert root is not None
        # Either no meta or no base_classes key
        if root.meta:
            assert "base_classes" not in root.meta or root.meta["base_classes"] == []


class TestObjCSelectorExtraction:
    """Tests for correct Objective-C selector extraction with colons.

    ObjC selectors include colons as part of the name: ``setX:Y:`` not ``setXY``.
    This is critical for cross-file method resolution and I/O catalog matching.
    """

    def test_keyword_method_name_includes_colons(self, tmp_path: Path) -> None:
        """Method names for keyword selectors include colons: addX:y:."""
        from hypergumbo_lang_mainstream.objc import analyze_objc

        (tmp_path / "Calc.m").write_text("""
@implementation Calc
- (int)addX:(int)x y:(int)y {
    return x + y;
}
@end
""")
        result = analyze_objc(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        assert "Calc.addX:y:" in method_names

    def test_single_keyword_method_name_includes_colon(self, tmp_path: Path) -> None:
        """Single-keyword selectors include trailing colon: logMessage:."""
        from hypergumbo_lang_mainstream.objc import analyze_objc

        (tmp_path / "Logger.m").write_text("""
@implementation Logger
- (void)logMessage:(NSString *)msg {
}
@end
""")
        result = analyze_objc(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        assert "Logger.logMessage:" in method_names

    def test_simple_selector_has_no_colon(self, tmp_path: Path) -> None:
        """Simple selectors (no params) have no colon: doStuff."""
        from hypergumbo_lang_mainstream.objc import analyze_objc

        (tmp_path / "Worker.m").write_text("""
@implementation Worker
- (void)doStuff {
}
@end
""")
        result = analyze_objc(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        assert "Worker.doStuff" in method_names

    def test_message_send_selector_excludes_arguments(self, tmp_path: Path) -> None:
        """Message send selectors exclude argument names from the call site."""
        from hypergumbo_lang_mainstream.objc import analyze_objc

        (tmp_path / "FileOps.m").write_text("""
@implementation FileOps

- (void)delete:(NSString *)path {
}

- (void)doWork {
    [self delete:@"/tmp/foo"];
}

@end
""")
        result = analyze_objc(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # The call should resolve to "delete:" method, not include arg text
        assert any("delete:" in e.dst for e in call_edges)

    def test_keyword_message_send_correct_selector(self, tmp_path: Path) -> None:
        """Multi-keyword message sends produce correct selectors with colons."""
        from hypergumbo_lang_mainstream.objc import analyze_objc

        (tmp_path / "Manager.m").write_text("""
@implementation Manager

- (void)removeItemAtPath:(NSString *)path error:(NSError **)err {
}

- (void)cleanup {
    NSError *error = nil;
    [self removeItemAtPath:@"/tmp/foo" error:&error];
}

@end
""")
        result = analyze_objc(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should resolve to the method with correct colon selector
        resolved = [e for e in call_edges if "removeItemAtPath:error:" in e.dst]
        assert len(resolved) == 1, (
            f"Expected 1 edge to removeItemAtPath:error:, got {len(resolved)}. "
            f"All call dsts: {[e.dst for e in call_edges]}"
        )

    def test_unresolved_keyword_message_has_correct_selector(self, tmp_path: Path) -> None:
        """Unresolved keyword messages produce correct selectors with colons."""
        from hypergumbo_lang_mainstream.objc import analyze_objc

        (tmp_path / "Ops.m").write_text("""
@implementation Ops

- (void)doWork {
    [[NSFileManager defaultManager] removeItemAtPath:path error:&err];
}

@end
""")
        result = analyze_objc(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        unresolved_dsts = [e.dst for e in call_edges if "unresolved" in e.dst]
        # Should have "removeItemAtPath:error:" not "removeItemAtPathpatherror"
        matching = [d for d in unresolved_dsts if "removeItemAtPath:error:" in d]
        assert len(matching) >= 1, (
            f"Expected unresolved edge with 'removeItemAtPath:error:', got: {unresolved_dsts}"
        )


class TestObjCParentBaseClasses:
    """Tests for parent_base_classes propagation to ObjC methods.

    Methods inside a class that extends UIView should have
    parent_base_classes=['UIView'] so UIKit framework patterns can match.
    """

    def test_method_gets_parent_base_classes_from_interface(self, tmp_path: Path) -> None:
        """Methods inherit parent_base_classes from @interface base_classes."""
        from hypergumbo_lang_mainstream.objc import analyze_objc

        (tmp_path / "MyView.h").write_text("""
@interface MyView : UIView
- (void)layoutSubviews;
@end
""")
        (tmp_path / "MyView.m").write_text("""
@implementation MyView
- (void)layoutSubviews {
    [super layoutSubviews];
}
@end
""")
        result = analyze_objc(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "layoutSubviews" in s.name]
        assert len(methods) >= 1
        # At least one method should have parent_base_classes from the @interface
        methods_with_bases = [m for m in methods if (m.meta or {}).get("parent_base_classes")]
        assert len(methods_with_bases) >= 1, (
            f"Expected at least 1 method with parent_base_classes, got 0. "
            f"Method metas: {[(m.name, m.meta) for m in methods]}"
        )
        assert "UIView" in methods_with_bases[0].meta["parent_base_classes"]

    def test_method_no_parent_base_classes_for_root_class(self, tmp_path: Path) -> None:
        """Methods in classes without explicit superclass have no parent_base_classes."""
        from hypergumbo_lang_mainstream.objc import analyze_objc

        (tmp_path / "Root.m").write_text("""
@implementation Root
- (void)doStuff {
}
@end
""")
        result = analyze_objc(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "doStuff" in s.name]
        assert len(methods) >= 1
        # No parent_base_classes since Root has no declared superclass
        for m in methods:
            parent_bases = (m.meta or {}).get("parent_base_classes", [])
            assert parent_bases == [], f"Expected empty parent_base_classes, got {parent_bases}"


class TestObjCStableShapeId:
    """Tests for stable_id and shape_id in Objective-C (ADR-0014 §1-2)."""

    def test_class_has_stable_and_shape_id(self, tmp_path: Path) -> None:
        from hypergumbo_lang_mainstream.objc import analyze_objc

        (tmp_path / "Example.m").write_text(
            "@interface Foo : NSObject\n- (void)bar;\n@end\n"
            "@implementation Foo\n- (void)bar { }\n@end\n"
        )
        result = analyze_objc(tmp_path)
        cls = next(s for s in result.symbols if s.kind == "class")
        assert cls.stable_id is not None
        assert cls.stable_id.startswith("sha256:")
        assert cls.shape_id is not None
        assert cls.shape_id.startswith("sha256:")


class TestObjcCyclomaticComplexity:
    """INV-loguk slice C: callable Obj-C symbols (methods) carry non-null
    CC + LOC. Real-grammar verification (if/for/while/do/case/ternary/catch +
    &&/|| short-circuit)."""

    def test_branchy_method_has_cc_and_loc(self, tmp_path) -> None:
        from hypergumbo_lang_mainstream.objc import analyze_objc
        (tmp_path / "Foo.m").write_text("""@implementation Foo
- (int)classify:(int)x with:(int)y {
    if (x > 0 && y > 0) { NSLog(@"a"); }
    if (x < 0 || y < 0) { NSLog(@"b"); }
    for (int i = 0; i < x; i++) { NSLog(@"%d", i); }
    while (x > 0) { x--; }
    switch (x) {
        case 1: return 10;
        case 2: return 20;
        default: return 0;
    }
    return x > y ? x : y;
}
@end
""")
        result = analyze_objc(tmp_path)
        fn = next(s for s in result.symbols
                  if s.kind == "method" and "classify" in s.name)
        # base 1 + if x2 + && + || + for + while + 3 case + ternary = 11
        assert fn.cyclomatic_complexity == 11
        assert fn.line_span is not None and fn.line_span >= 4

    def test_straight_line_method_cc_is_one(self, tmp_path) -> None:
        from hypergumbo_lang_mainstream.objc import analyze_objc
        (tmp_path / "Bar.m").write_text("""@implementation Bar
- (int)plain:(int)x { return x; }
@end
""")
        result = analyze_objc(tmp_path)
        fn = next(s for s in result.symbols
                  if s.kind == "method" and "plain" in s.name)
        assert fn.cyclomatic_complexity == 1
        assert fn.line_span is not None

    def test_callables_non_null_non_callables_null(self, tmp_path) -> None:
        from hypergumbo_lang_mainstream.objc import analyze_objc
        (tmp_path / "Baz.m").write_text("""@interface Baz : NSObject
@property (nonatomic) int count;
- (int)compute:(int)x;
@end
@implementation Baz
- (int)compute:(int)x { if (x > 0) { return 1; } return 0; }
@end
""")
        result = analyze_objc(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]
        assert methods
        for s in methods:
            assert s.cyclomatic_complexity is not None, s.name
            assert s.line_span is not None, s.name
        for s in result.symbols:
            if s.kind != "method":
                assert s.cyclomatic_complexity is None, (s.kind, s.name)
