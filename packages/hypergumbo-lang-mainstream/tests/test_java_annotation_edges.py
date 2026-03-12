# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Java annotation edge detection (INV-012 scope expansion).

Verifies that annotation applications create edges in the call graph for Java.
"""
import pytest
from pathlib import Path

from hypergumbo_lang_mainstream.java import analyze_java, is_java_tree_sitter_available


pytestmark = pytest.mark.skipif(
    not is_java_tree_sitter_available(),
    reason="tree-sitter-java not available"
)


class TestJavaAnnotationEdges:
    """Test annotation edge detection for Java."""

    def test_class_annotation_creates_edge(self, tmp_path: Path) -> None:
        """A @annotation on a class creates a decorated_by edge."""
        code = '''
package com.example;

@interface Service {
}

@Service
class UserService {
    public void getUsers() { }
}
'''
        java_file = tmp_path / "UserService.java"
        java_file.write_text(code)

        result = analyze_java(tmp_path)

        # Find the decorated_by edge
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by"
        ]

        assert len(decorated_by_edges) >= 1, "Expected at least one decorated_by edge"

        # The edge should be from UserService to Service
        edge = decorated_by_edges[0]
        assert "UserService" in edge.src
        assert "Service" in edge.dst

    def test_multiple_class_annotations_create_multiple_edges(self, tmp_path: Path) -> None:
        """Multiple annotations create multiple edges."""
        code = '''
package com.example;

@interface Service {
}

@interface Controller {
    String value() default "";
}

@Service
@Controller("/users")
class UserController {
}
'''
        java_file = tmp_path / "UserController.java"
        java_file.write_text(code)

        result = analyze_java(tmp_path)

        # Find decorated_by edges for UserController
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by" and "UserController" in e.src
        ]

        assert len(decorated_by_edges) >= 2, "Expected two decorated_by edges for stacked annotations"

    def test_method_annotation_creates_edge(self, tmp_path: Path) -> None:
        """A @annotation on a method creates a decorated_by edge."""
        code = '''
package com.example;

@interface GetMapping {
    String value() default "";
}

class UserController {
    @GetMapping("/users")
    public void getUsers() { }
}
'''
        java_file = tmp_path / "UserController.java"
        java_file.write_text(code)

        result = analyze_java(tmp_path)

        # Find decorated_by edges
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by"
        ]

        assert len(decorated_by_edges) >= 1, "Expected at least one decorated_by edge for method annotation"

        # The edge should reference getUsers and GetMapping
        edge = decorated_by_edges[0]
        assert "getUsers" in edge.src
        assert "GetMapping" in edge.dst

    def test_standard_annotation_override_suppressed(self, tmp_path: Path) -> None:
        """@Override does not create an unresolved decorated_by edge (WI-divob)."""
        code = '''
package com.example;

class MyService {
    @Override
    public String toString() { return ""; }
}
'''
        java_file = tmp_path / "MyService.java"
        java_file.write_text(code)

        result = analyze_java(tmp_path)

        # @Override is a standard annotation — no unresolved edge should exist
        override_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by" and "Override" in e.dst
        ]
        assert len(override_edges) == 0, (
            "Standard @Override should not create unresolved decorated_by edge"
        )

    def test_standard_annotations_all_suppressed(self, tmp_path: Path) -> None:
        """Standard Java annotations (@Deprecated, @Test, etc.) are suppressed."""
        code = '''
package com.example;

class MyService {
    @Deprecated
    public void oldMethod() { }

    @SuppressWarnings("unchecked")
    public void warnMethod() { }

    @Override
    public String toString() { return ""; }
}
'''
        java_file = tmp_path / "MyService.java"
        java_file.write_text(code)

        result = analyze_java(tmp_path)

        unresolved_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by" and "unresolved" in e.dst
        ]
        assert len(unresolved_edges) == 0, (
            f"Standard annotations should not create unresolved edges, "
            f"got: {[e.dst for e in unresolved_edges]}"
        )

    def test_nonstandard_unresolved_annotation_emits_edge(self, tmp_path: Path) -> None:
        """Non-standard annotations like @Transactional still emit unresolved edges."""
        code = '''
package com.example;

@Transactional
class MyService {
    public void doWork() { }
}
'''
        java_file = tmp_path / "MyService.java"
        java_file.write_text(code)

        result = analyze_java(tmp_path)

        # @Transactional is not in the standard set — should emit unresolved edge
        transactional_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by" and "Transactional" in e.dst
        ]
        assert len(transactional_edges) >= 1, (
            "Non-standard @Transactional should emit an unresolved decorated_by edge"
        )
