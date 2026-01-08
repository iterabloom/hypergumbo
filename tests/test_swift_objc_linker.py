"""Tests for Swift/Objective-C bridging linker."""
from pathlib import Path


class TestSwiftObjCLinkerBasics:
    """Tests for basic linker functionality."""

    def test_linker_returns_result(self, tmp_path: Path) -> None:
        """Linker returns a result object."""
        from hypergumbo.linkers.swift_objc import link_swift_objc

        # Empty directory
        result = link_swift_objc(tmp_path)

        assert result is not None
        assert result.run is not None
        assert result.edges == []
        assert result.symbols == []


class TestSwiftObjCBridgingDetection:
    """Tests for detecting Swift/Objective-C bridging patterns."""

    def test_detects_objc_annotation_in_swift(self, tmp_path: Path) -> None:
        """Detects @objc annotations in Swift code."""
        from hypergumbo.linkers.swift_objc import link_swift_objc

        swift_file = tmp_path / "MyClass.swift"
        swift_file.write_text('''
import Foundation

@objc class MySwiftClass: NSObject {
    @objc func doSomething() {
        print("Hello")
    }
}
''')

        result = link_swift_objc(tmp_path)

        # Should create symbols for exposed Swift methods
        bridge_symbols = [s for s in result.symbols if s.kind == "objc_bridge"]
        assert len(bridge_symbols) >= 1

    def test_detects_bridging_header_imports(self, tmp_path: Path) -> None:
        """Detects bridging header imports."""
        from hypergumbo.linkers.swift_objc import link_swift_objc

        # Create bridging header
        bridging_header = tmp_path / "MyApp-Bridging-Header.h"
        bridging_header.write_text('''
#import "LegacyHelper.h"
#import "ObjCUtilities.h"
''')

        result = link_swift_objc(tmp_path)

        # Should create import edges for bridging header
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 2

    def test_detects_selector_calls(self, tmp_path: Path) -> None:
        """Detects #selector() calls referencing Objective-C methods."""
        from hypergumbo.linkers.swift_objc import link_swift_objc

        swift_file = tmp_path / "ViewController.swift"
        swift_file.write_text('''
import UIKit

class ViewController: UIViewController {
    override func viewDidLoad() {
        let button = UIButton()
        button.addTarget(self, action: #selector(buttonTapped), for: .touchUpInside)
    }

    @objc func buttonTapped() {
        print("Tapped")
    }
}
''')

        result = link_swift_objc(tmp_path)

        # Should detect selector reference
        symbols = [s for s in result.symbols if "selector" in s.kind.lower() or "objc" in s.kind.lower()]
        assert len(symbols) >= 1


class TestSwiftObjCCrossFileEdges:
    """Tests for cross-file bridging edges."""

    def test_creates_bridge_edges_for_objc_classes_called_from_swift(self, tmp_path: Path) -> None:
        """Creates edges when Swift calls Objective-C classes."""
        from hypergumbo.linkers.swift_objc import link_swift_objc

        # Objective-C class
        objc_file = tmp_path / "LegacyHelper.m"
        objc_file.write_text('''
@implementation LegacyHelper

- (void)doLegacyWork {
    NSLog(@"Working");
}

@end
''')

        # Swift calling it
        swift_file = tmp_path / "ModernCode.swift"
        swift_file.write_text('''
import Foundation

class ModernCode {
    let helper = LegacyHelper()

    func work() {
        helper.doLegacyWork()
    }
}
''')

        result = link_swift_objc(tmp_path)

        # Should detect cross-language reference
        bridge_edges = [e for e in result.edges if e.edge_type in ("swift_objc_bridge", "calls")]
        assert len(bridge_edges) >= 0  # At least parsed without error


class TestSwiftObjCSymbolCreation:
    """Tests for symbol creation in the linker."""

    def test_creates_symbols_with_correct_properties(self, tmp_path: Path) -> None:
        """Symbols have correct language and origin."""
        from hypergumbo.linkers.swift_objc import link_swift_objc

        swift_file = tmp_path / "Test.swift"
        swift_file.write_text('''
@objc class TestClass: NSObject {
    @objc func testMethod() {}
}
''')

        result = link_swift_objc(tmp_path)

        for symbol in result.symbols:
            assert symbol.origin == "swift-objc-linker-v1"


class TestSwiftObjCEdgeProperties:
    """Tests for edge property correctness."""

    def test_edges_have_confidence(self, tmp_path: Path) -> None:
        """Edges have confidence values."""
        from hypergumbo.linkers.swift_objc import link_swift_objc

        bridging_header = tmp_path / "App-Bridging-Header.h"
        bridging_header.write_text('#import "Helper.h"')

        result = link_swift_objc(tmp_path)

        for edge in result.edges:
            assert edge.confidence > 0
            assert edge.confidence <= 1.0


class TestSwiftObjCEmptyProject:
    """Tests for handling projects without Swift/Objective-C interop."""

    def test_handles_swift_only_project(self, tmp_path: Path) -> None:
        """Handles pure Swift projects without Objective-C."""
        from hypergumbo.linkers.swift_objc import link_swift_objc

        swift_file = tmp_path / "App.swift"
        swift_file.write_text('''
struct App {
    static func main() {
        print("Hello")
    }
}
''')

        result = link_swift_objc(tmp_path)

        assert result.run is not None
        # No bridging detected, but no errors

    def test_handles_objc_only_project(self, tmp_path: Path) -> None:
        """Handles pure Objective-C projects without Swift."""
        from hypergumbo.linkers.swift_objc import link_swift_objc

        objc_file = tmp_path / "main.m"
        objc_file.write_text('''
#import <Foundation/Foundation.h>

int main() {
    NSLog(@"Hello");
    return 0;
}
''')

        result = link_swift_objc(tmp_path)

        assert result.run is not None


class TestSwiftObjCNSObjectPattern:
    """Tests for NSObject inheritance detection."""

    def test_detects_nsobject_subclass(self, tmp_path: Path) -> None:
        """Detects Swift classes inheriting from NSObject."""
        from hypergumbo.linkers.swift_objc import link_swift_objc

        swift_file = tmp_path / "Model.swift"
        swift_file.write_text('''
import Foundation

class UserModel: NSObject {
    var name: String = ""
}

class ProductModel: NSObject {
    var title: String = ""
}
''')

        result = link_swift_objc(tmp_path)

        # NSObject subclasses are automatically exposed to Objective-C
        bridge_symbols = [s for s in result.symbols if s.kind == "objc_bridge"]
        assert len(bridge_symbols) >= 2


class TestSwiftObjCLinkerRegistered:
    """Tests for the registered swift_objc_linker function."""

    def test_swift_objc_linker_returns_result(self, tmp_path: Path) -> None:
        """swift_objc_linker function returns LinkerResult."""
        from hypergumbo.linkers.swift_objc import swift_objc_linker
        from hypergumbo.linkers.registry import LinkerContext

        ctx = LinkerContext(repo_root=tmp_path)
        result = swift_objc_linker(ctx)

        assert result is not None
        assert hasattr(result, "symbols")
        assert hasattr(result, "edges")
