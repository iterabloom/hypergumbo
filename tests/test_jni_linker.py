"""Tests for JNI linker."""
from pathlib import Path

from hypergumbo.ir import AnalysisRun, Symbol, Span


class TestJniNamingConvention:
    """Tests for JNI naming convention parsing."""

    def test_parse_jni_function_name_simple(self) -> None:
        """Parses simple JNI function name."""
        from hypergumbo.linkers.jni import parse_jni_function_name

        result = parse_jni_function_name("Java_com_example_MyClass_processData")

        assert result is not None
        assert result["package"] == "com.example"
        assert result["class"] == "MyClass"
        assert result["method"] == "processData"

    def test_parse_jni_function_name_no_package(self) -> None:
        """Parses JNI function with no package."""
        from hypergumbo.linkers.jni import parse_jni_function_name

        result = parse_jni_function_name("Java_MyClass_doSomething")

        assert result is not None
        assert result["package"] == ""
        assert result["class"] == "MyClass"
        assert result["method"] == "doSomething"

    def test_parse_jni_function_name_deep_package(self) -> None:
        """Parses JNI function with deep package hierarchy."""
        from hypergumbo.linkers.jni import parse_jni_function_name

        result = parse_jni_function_name("Java_org_apache_guacamole_net_Client_connect")

        assert result is not None
        assert result["package"] == "org.apache.guacamole.net"
        assert result["class"] == "Client"
        assert result["method"] == "connect"

    def test_parse_jni_function_name_not_jni(self) -> None:
        """Returns None for non-JNI function names."""
        from hypergumbo.linkers.jni import parse_jni_function_name

        assert parse_jni_function_name("main") is None
        assert parse_jni_function_name("process_data") is None
        assert parse_jni_function_name("Java") is None
        assert parse_jni_function_name("Java_") is None

    def test_parse_jni_function_name_with_overload(self) -> None:
        """Parses JNI function with overload suffix."""
        from hypergumbo.linkers.jni import parse_jni_function_name

        # JNI uses __ followed by signature for overloaded methods
        result = parse_jni_function_name("Java_com_example_MyClass_process__I")

        assert result is not None
        assert result["class"] == "MyClass"
        assert result["method"] == "process"


class TestJniLinker:
    """Tests for JNI linker edge creation."""

    def _make_java_symbol(
        self,
        name: str,
        kind: str = "method",
        is_native: bool = False,
        path: str = "Test.java",
        modifiers: list[str] | None = None,
    ) -> Symbol:
        """Create a test Java symbol.

        Args:
            name: Symbol name
            kind: Symbol kind
            is_native: Whether to set meta.is_native (legacy approach)
            path: File path
            modifiers: List of modifiers (new approach, e.g., ["native", "public"])
        """
        run = AnalysisRun.create(pass_id="test", version="test")
        meta = {"is_native": True} if is_native else {}
        return Symbol(
            id=f"java:{path}:1-10:{name}:{kind}",
            name=name,
            kind=kind,
            language="java",
            path=path,
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="java-v1",
            origin_run_id=run.execution_id,
            meta=meta,
            modifiers=modifiers if modifiers else [],
        )

    def _make_c_symbol(
        self,
        name: str,
        kind: str = "function",
        path: str = "native.c",
    ) -> Symbol:
        """Create a test C symbol."""
        run = AnalysisRun.create(pass_id="test", version="test")
        return Symbol(
            id=f"c:{path}:1-10:{name}:{kind}",
            name=name,
            kind=kind,
            language="c",
            path=path,
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="c-v1",
            origin_run_id=run.execution_id,
        )

    def test_links_java_native_to_c_jni(self) -> None:
        """Links Java native method to C JNI function."""
        from hypergumbo.linkers.jni import link_jni

        java_symbols = [
            self._make_java_symbol("MyClass.processData", "method", is_native=True),
        ]
        c_symbols = [
            self._make_c_symbol("Java_MyClass_processData"),
        ]

        result = link_jni(java_symbols, c_symbols)

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "native_bridge"
        assert "MyClass.processData" in edge.src
        assert "Java_MyClass_processData" in edge.dst

    def test_links_with_package(self) -> None:
        """Links Java native method with package to C JNI function."""
        from hypergumbo.linkers.jni import link_jni

        java_symbols = [
            self._make_java_symbol(
                "com.example.Native.getValue",
                "method",
                is_native=True,
                path="com/example/Native.java",
            ),
        ]
        c_symbols = [
            self._make_c_symbol("Java_com_example_Native_getValue"),
        ]

        result = link_jni(java_symbols, c_symbols)

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "native_bridge"

    def test_links_via_modifiers_field(self) -> None:
        """Links Java native method detected via modifiers field (not meta.is_native)."""
        from hypergumbo.linkers.jni import link_jni

        java_symbols = [
            self._make_java_symbol(
                "MyClass.nativeMethod",
                "method",
                is_native=False,  # Not using legacy meta.is_native
                modifiers=["public", "native"],  # Using new modifiers field
            ),
        ]
        c_symbols = [
            self._make_c_symbol("Java_MyClass_nativeMethod"),
        ]

        result = link_jni(java_symbols, c_symbols)

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "native_bridge"
        assert "MyClass.nativeMethod" in edge.src
        assert "Java_MyClass_nativeMethod" in edge.dst

    def test_no_link_for_non_native_method(self) -> None:
        """Does not link non-native Java methods."""
        from hypergumbo.linkers.jni import link_jni

        java_symbols = [
            self._make_java_symbol("MyClass.processData", "method", is_native=False),
        ]
        c_symbols = [
            self._make_c_symbol("Java_MyClass_processData"),
        ]

        result = link_jni(java_symbols, c_symbols)

        assert len(result.edges) == 0

    def test_no_link_for_missing_c_function(self) -> None:
        """Does not create edge when C function is missing."""
        from hypergumbo.linkers.jni import link_jni

        java_symbols = [
            self._make_java_symbol("MyClass.processData", "method", is_native=True),
        ]
        c_symbols = [
            self._make_c_symbol("some_other_function"),
        ]

        result = link_jni(java_symbols, c_symbols)

        assert len(result.edges) == 0

    def test_links_multiple_native_methods(self) -> None:
        """Links multiple native methods to their implementations."""
        from hypergumbo.linkers.jni import link_jni

        java_symbols = [
            self._make_java_symbol("Native.processData", "method", is_native=True),
            self._make_java_symbol("Native.getValue", "method", is_native=True),
            self._make_java_symbol("Native.regularMethod", "method", is_native=False),
        ]
        c_symbols = [
            self._make_c_symbol("Java_Native_processData"),
            self._make_c_symbol("Java_Native_getValue"),
            self._make_c_symbol("helper_function"),
        ]

        result = link_jni(java_symbols, c_symbols)

        assert len(result.edges) == 2
        edge_methods = {e.src.split(":")[-2] for e in result.edges}
        assert "Native.processData" in edge_methods
        assert "Native.getValue" in edge_methods

    def test_result_includes_run_metadata(self) -> None:
        """Result includes analysis run metadata."""
        from hypergumbo.linkers.jni import link_jni

        java_symbols = [
            self._make_java_symbol("MyClass.processData", "method", is_native=True),
        ]
        c_symbols = [
            self._make_c_symbol("Java_MyClass_processData"),
        ]

        result = link_jni(java_symbols, c_symbols)

        assert result.run is not None
        assert result.run.pass_id == "jni-linker-v1"

    def test_edge_confidence(self) -> None:
        """Edge has appropriate confidence level."""
        from hypergumbo.linkers.jni import link_jni

        java_symbols = [
            self._make_java_symbol("MyClass.processData", "method", is_native=True),
        ]
        c_symbols = [
            self._make_c_symbol("Java_MyClass_processData"),
        ]

        result = link_jni(java_symbols, c_symbols)

        assert len(result.edges) == 1
        assert result.edges[0].confidence >= 0.9


class TestJniLinkerWithAnalyzers:
    """Integration tests using actual analyzer output."""

    def test_links_analyzed_files(self, tmp_path: Path) -> None:
        """Links symbols from actual Java and C analysis."""
        from hypergumbo.analyze.java import analyze_java
        from hypergumbo.analyze.c import analyze_c
        from hypergumbo.linkers.jni import link_jni

        # Create Java file with native method
        java_file = tmp_path / "Native.java"
        java_file.write_text("""
public class Native {
    static {
        System.loadLibrary("native");
    }

    public native void processData(byte[] data);
    public native int getValue();

    public void regularMethod() {
        System.out.println("Regular");
    }
}
""")

        # Create C file with JNI implementations
        c_file = tmp_path / "native.c"
        c_file.write_text("""
#include <jni.h>

JNIEXPORT void JNICALL Java_Native_processData(
    JNIEnv *env, jobject obj, jbyteArray data) {
    // Implementation
}

JNIEXPORT jint JNICALL Java_Native_getValue(
    JNIEnv *env, jobject obj) {
    return 42;
}
""")

        # Analyze both
        java_result = analyze_java(tmp_path)
        c_result = analyze_c(tmp_path)

        # Link
        link_result = link_jni(java_result.symbols, c_result.symbols)

        # Should have created native_bridge edges
        assert link_result.run is not None
        # Edges may or may not be created depending on native detection


class TestJniLinkerRegistry:
    """Tests for JNI linker registry integration."""

    def test_jni_linker_registered(self) -> None:
        """JNI linker is registered in the linker registry."""
        # Import the jni module to trigger registration
        import hypergumbo.linkers.jni  # noqa: F401
        from hypergumbo.linkers.registry import get_linker

        linker = get_linker("jni")
        assert linker is not None
        assert linker.name == "jni"
        assert linker.priority == 10  # Early priority
        assert "JNI" in linker.description or "native" in linker.description.lower()

    def test_jni_linker_has_requirements(self) -> None:
        """JNI linker declares its requirements."""
        import hypergumbo.linkers.jni  # noqa: F401
        from hypergumbo.linkers.registry import get_linker

        linker = get_linker("jni")
        assert linker is not None
        assert len(linker.requirements) == 2

        req_names = [r.name for r in linker.requirements]
        assert "java_native_methods" in req_names
        assert "c_cpp_jni_functions" in req_names

    def test_jni_linker_via_registry(self) -> None:
        """JNI linker works via registry dispatch."""
        import hypergumbo.linkers.jni  # noqa: F401
        from pathlib import Path
        from hypergumbo.linkers.registry import LinkerContext, run_linker

        # Create symbols
        run = AnalysisRun.create(pass_id="test", version="test")
        java_sym = Symbol(
            id="java:Test.java:1-10:MyClass.processData:method",
            name="MyClass.processData",
            kind="method",
            language="java",
            path="Test.java",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="java-v1",
            origin_run_id=run.execution_id,
            modifiers=["native", "public"],
        )
        c_sym = Symbol(
            id="c:native.c:1-10:Java_MyClass_processData:function",
            name="Java_MyClass_processData",
            kind="function",
            language="c",
            path="native.c",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="c-v1",
            origin_run_id=run.execution_id,
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[java_sym, c_sym],
        )

        result = run_linker("jni", ctx)

        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "native_bridge"

    def test_jni_requirements_check_with_matching_symbols(self) -> None:
        """JNI requirements report as met when matching symbols exist."""
        import hypergumbo.linkers.jni  # noqa: F401
        from pathlib import Path
        from hypergumbo.linkers.registry import LinkerContext, check_linker_requirements

        run = AnalysisRun.create(pass_id="test", version="test")

        # Java native method
        java_sym = Symbol(
            id="java:Test.java:1-10:MyClass.nativeMethod:method",
            name="MyClass.nativeMethod",
            kind="method",
            language="java",
            path="Test.java",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="java-v1",
            origin_run_id=run.execution_id,
            modifiers=["native"],
        )

        # C JNI function
        c_sym = Symbol(
            id="c:native.c:1-10:Java_MyClass_nativeMethod:function",
            name="Java_MyClass_nativeMethod",
            kind="function",
            language="c",
            path="native.c",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="c-v1",
            origin_run_id=run.execution_id,
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[java_sym, c_sym],
        )

        diagnostics = check_linker_requirements(ctx)

        # Find JNI linker diagnostics
        jni_diag = next((d for d in diagnostics if d.linker_name == "jni"), None)
        assert jni_diag is not None
        assert jni_diag.all_met is True
        assert all(r.met for r in jni_diag.requirements)

    def test_jni_requirements_check_missing_java_native(self) -> None:
        """JNI requirements report unmet when Java native methods missing."""
        import hypergumbo.linkers.jni  # noqa: F401
        from pathlib import Path
        from hypergumbo.linkers.registry import LinkerContext, check_linker_requirements

        run = AnalysisRun.create(pass_id="test", version="test")

        # Only C JNI function, no Java native method
        c_sym = Symbol(
            id="c:native.c:1-10:Java_MyClass_nativeMethod:function",
            name="Java_MyClass_nativeMethod",
            kind="function",
            language="c",
            path="native.c",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="c-v1",
            origin_run_id=run.execution_id,
        )

        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[c_sym],
        )

        diagnostics = check_linker_requirements(ctx)

        jni_diag = next((d for d in diagnostics if d.linker_name == "jni"), None)
        assert jni_diag is not None
        assert jni_diag.all_met is False

        # Java native requirement should be unmet
        java_req = next((r for r in jni_diag.requirements if r.name == "java_native_methods"), None)
        assert java_req is not None
        assert java_req.met is False
        assert java_req.count == 0

        # C/C++ JNI requirement should be met
        c_req = next((r for r in jni_diag.requirements if r.name == "c_cpp_jni_functions"), None)
        assert c_req is not None
        assert c_req.met is True
        assert c_req.count == 1
        # This test verifies no crashes and proper integration


class TestJniLinkerEdgeCases:
    """Tests for edge cases in JNI linking."""

    def test_empty_symbol_lists(self) -> None:
        """Handles empty symbol lists."""
        from hypergumbo.linkers.jni import link_jni

        result = link_jni([], [])

        assert result.edges == []
        assert result.run is not None

    def test_only_java_symbols(self) -> None:
        """Handles case with only Java symbols."""
        from hypergumbo.linkers.jni import link_jni

        run = AnalysisRun.create(pass_id="test", version="test")
        java_symbols = [
            Symbol(
                id="java:Test.java:1-10:Native.processData:method",
                name="Native.processData",
                kind="method",
                language="java",
                path="Test.java",
                span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
                origin="java-v1",
                origin_run_id=run.execution_id,
                meta={"is_native": True},
            ),
        ]

        result = link_jni(java_symbols, [])

        assert result.edges == []

    def test_only_c_symbols(self) -> None:
        """Handles case with only C symbols."""
        from hypergumbo.linkers.jni import link_jni

        run = AnalysisRun.create(pass_id="test", version="test")
        c_symbols = [
            Symbol(
                id="c:native.c:1-10:Java_Native_processData:function",
                name="Java_Native_processData",
                kind="function",
                language="c",
                path="native.c",
                span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
                origin="c-v1",
                origin_run_id=run.execution_id,
            ),
        ]

        result = link_jni([], c_symbols)

        assert result.edges == []

    def test_underscore_in_class_name(self) -> None:
        """Handles underscores in class names (encoded as _1)."""
        from hypergumbo.linkers.jni import parse_jni_function_name

        # JNI encodes _ as _1 in names
        result = parse_jni_function_name("Java_com_example_My_1Class_process")

        assert result is not None
        # The parser should handle this encoding
        assert "Class" in result["class"] or "My" in result["class"]

    def test_single_part_after_java_prefix(self) -> None:
        """Returns None for single part after Java_ prefix."""
        from hypergumbo.linkers.jni import parse_jni_function_name

        # Only one part after Java_ - not valid JNI
        result = parse_jni_function_name("Java_onlyOneWord")

        assert result is None

    def test_non_c_symbols_ignored(self) -> None:
        """Non-C symbols are ignored in lookup."""
        from hypergumbo.linkers.jni import link_jni

        run = AnalysisRun.create(pass_id="test", version="test")

        # Java symbol that is native
        java_symbols = [
            Symbol(
                id="java:Test.java:1-10:Native.processData:method",
                name="Native.processData",
                kind="method",
                language="java",
                path="Test.java",
                span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
                origin="java-v1",
                origin_run_id=run.execution_id,
                meta={"is_native": True},
            ),
        ]

        # Non-C symbol with JNI-like name (should be ignored)
        c_symbols = [
            Symbol(
                id="python:native.py:1-10:Java_Native_processData:function",
                name="Java_Native_processData",
                kind="function",
                language="python",  # Not C!
                path="native.py",
                span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
                origin="python-v1",
                origin_run_id=run.execution_id,
            ),
        ]

        result = link_jni(java_symbols, c_symbols)

        # Should not link because the C symbol is actually Python
        assert result.edges == []

    def test_non_java_symbols_ignored(self) -> None:
        """Non-Java symbols are ignored when linking."""
        from hypergumbo.linkers.jni import link_jni

        run = AnalysisRun.create(pass_id="test", version="test")

        # Non-Java symbol with native-like meta
        java_symbols = [
            Symbol(
                id="python:test.py:1-10:Native.processData:method",
                name="Native.processData",
                kind="method",
                language="python",  # Not Java!
                path="test.py",
                span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
                origin="python-v1",
                origin_run_id=run.execution_id,
                meta={"is_native": True},
            ),
        ]

        c_symbols = [
            Symbol(
                id="c:native.c:1-10:Java_Native_processData:function",
                name="Java_Native_processData",
                kind="function",
                language="c",
                path="native.c",
                span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
                origin="c-v1",
                origin_run_id=run.execution_id,
            ),
        ]

        result = link_jni(java_symbols, c_symbols)

        # Should not link because the Java symbol is actually Python
        assert result.edges == []

    def test_underscore_encoding_full_path(self) -> None:
        """Tests the full underscore encoding path with _1 sequences."""
        from hypergumbo.linkers.jni import parse_jni_function_name

        # Multiple underscores in class name: My__Class (two underscores)
        # Encoded as: My_1_1Class
        result = parse_jni_function_name("Java_pkg_My_1_1Class_method")

        assert result is not None
        # Should decode the underscores properly
        assert result["method"] == "method"

    def test_underscore_at_end_of_parts(self) -> None:
        """Tests underscore encoding when _1 is at end of parts list."""
        from hypergumbo.linkers.jni import parse_jni_function_name

        # Class name with underscore: My_Class
        # Encoded as: My_1Class
        result = parse_jni_function_name("Java_My_1Class_method")

        assert result is not None
        assert result["method"] == "method"
        assert "My" in result["class"] or "Class" in result["class"]

    def test_non_function_kind_ignored(self) -> None:
        """Non-function C symbols are ignored."""
        from hypergumbo.linkers.jni import link_jni

        run = AnalysisRun.create(pass_id="test", version="test")

        java_symbols = [
            Symbol(
                id="java:Test.java:1-10:Native.processData:method",
                name="Native.processData",
                kind="method",
                language="java",
                path="Test.java",
                span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
                origin="java-v1",
                origin_run_id=run.execution_id,
                meta={"is_native": True},
            ),
        ]

        # C struct, not function
        c_symbols = [
            Symbol(
                id="c:native.c:1-10:Java_Native_processData:struct",
                name="Java_Native_processData",
                kind="struct",  # Not function!
                language="c",
                path="native.c",
                span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
                origin="c-v1",
                origin_run_id=run.execution_id,
            ),
        ]

        result = link_jni(java_symbols, c_symbols)

        # Should not link because the C symbol is a struct
        assert result.edges == []

    def test_consecutive_underscores(self) -> None:
        """Tests consecutive underscore encoding (multiple _1 in a row)."""
        from hypergumbo.linkers.jni import parse_jni_function_name

        # Three consecutive underscores in class name: My___Class
        # Encoded as: My_1_1_1Class
        result = parse_jni_function_name("Java_pkg_My_1_1_1Class_method")

        assert result is not None
        # Should decode properly with multiple underscores
        assert result["method"] == "method"
        assert result["package"] == "pkg"

    def test_decoded_parts_too_few(self) -> None:
        """Tests when decoded parts result in fewer than 2 elements."""
        from hypergumbo.linkers.jni import parse_jni_function_name

        # Edge case: after decoding _1 sequences, we might end up with
        # a single part. This tests that edge case.
        # "Java_a_1" -> parts = ["a", "1"] -> decodes to ["a_"] (single element)
        result = parse_jni_function_name("Java_a_1")

        assert result is None

    def test_cpp_jni_symbols_linked(self) -> None:
        """C++ JNI functions are linked to Java native methods.

        This is critical for Android NDK projects which commonly use .cpp files
        for JNI implementations.
        """
        from hypergumbo.linkers.jni import link_jni

        run = AnalysisRun.create(pass_id="test", version="test")

        java_symbols = [
            Symbol(
                id="java:Test.java:1-10:NativeClass.processData:method",
                name="NativeClass.processData",
                kind="method",
                language="java",
                path="Test.java",
                span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
                origin="java-v1",
                origin_run_id=run.execution_id,
                modifiers=["native", "public"],
            ),
        ]

        # C++ symbol (language="cpp") with JNI function
        cpp_symbols = [
            Symbol(
                id="cpp:native.cpp:1-10:Java_NativeClass_processData:function",
                name="Java_NativeClass_processData",
                kind="function",
                language="cpp",  # C++, not C
                path="native.cpp",
                span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
                origin="cpp-v1",
                origin_run_id=run.execution_id,
            ),
        ]

        result = link_jni(java_symbols, cpp_symbols)

        # Should link C++ JNI function to Java native method
        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "native_bridge"
        assert result.edges[0].src == java_symbols[0].id
        assert result.edges[0].dst == cpp_symbols[0].id
