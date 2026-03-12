# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for C# attribute edge detection (INV-012 scope expansion).

Verifies that attribute applications create edges in the call graph for C#.
"""
import pytest
from pathlib import Path

from hypergumbo_lang_mainstream.csharp import analyze_csharp, is_csharp_tree_sitter_available


pytestmark = pytest.mark.skipif(
    not is_csharp_tree_sitter_available(),
    reason="tree-sitter-c-sharp not available"
)


class TestCSharpAttributeEdges:
    """Test attribute edge detection for C#."""

    def test_class_attribute_creates_edge(self, tmp_path: Path) -> None:
        """A [Attribute] on a class creates a decorated_by edge."""
        code = '''
using System;

public class ServiceAttribute : Attribute { }

[Service]
public class UserService
{
    public void GetUsers() { }
}
'''
        cs_file = tmp_path / "UserService.cs"
        cs_file.write_text(code)

        result = analyze_csharp(tmp_path)

        # Find the decorated_by edge
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by"
        ]

        assert len(decorated_by_edges) >= 1, "Expected at least one decorated_by edge"

        # The edge should be from UserService to ServiceAttribute
        edge = decorated_by_edges[0]
        assert "UserService" in edge.src
        # Note: C# attribute names may or may not have "Attribute" suffix
        assert "Service" in edge.dst

    def test_multiple_class_attributes_create_multiple_edges(self, tmp_path: Path) -> None:
        """Multiple attributes create multiple edges."""
        code = '''
using System;

public class ServiceAttribute : Attribute { }
public class ControllerAttribute : Attribute
{
    public ControllerAttribute(string path) { }
}

[Service]
[Controller("/users")]
public class UserController { }
'''
        cs_file = tmp_path / "UserController.cs"
        cs_file.write_text(code)

        result = analyze_csharp(tmp_path)

        # Find decorated_by edges for UserController
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by" and "UserController" in e.src
        ]

        assert len(decorated_by_edges) >= 2, "Expected two decorated_by edges for stacked attributes"

    def test_method_attribute_creates_edge(self, tmp_path: Path) -> None:
        """A [Attribute] on a method creates a decorated_by edge."""
        code = '''
using System;

public class HttpGetAttribute : Attribute
{
    public HttpGetAttribute(string path) { }
}

public class UserController
{
    [HttpGet("/users")]
    public void GetUsers() { }
}
'''
        cs_file = tmp_path / "UserController.cs"
        cs_file.write_text(code)

        result = analyze_csharp(tmp_path)

        # Find decorated_by edges
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by"
        ]

        assert len(decorated_by_edges) >= 1, "Expected at least one decorated_by edge for method attribute"

        # The edge should reference GetUsers and HttpGet
        edge = decorated_by_edges[0]
        assert "GetUsers" in edge.src
        assert "HttpGet" in edge.dst
