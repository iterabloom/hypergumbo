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
