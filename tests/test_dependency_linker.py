"""Tests for dependency linker.

Tests that the linker correctly connects:
- Cargo.toml dependencies to Rust `use` imports
- pyproject.toml dependencies to Python imports
"""

from hypergumbo.ir import Edge, Span, Symbol
from hypergumbo.linkers.dependency import (
    PASS_ID,
    DependencyLinkResult,
    link_dependencies,
)


def test_pass_id():
    """Verify pass ID is set correctly."""
    assert PASS_ID == "dependency-linker-v1"


def test_link_rust_dependencies():
    """Test linking Rust imports to Cargo.toml dependencies."""
    # Create a Cargo.toml dependency symbol
    cargo_dep = Symbol(
        id="toml:sha256:abc123",
        stable_id=None,
        shape_id=None,
        canonical_name="serde",
        fingerprint="fp1",
        kind="dependency",
        name="serde",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml-v1",
    )

    # Create a Rust file ID symbol
    rust_file = Symbol(
        id="rust:sha256:file1",
        stable_id=None,
        shape_id=None,
        canonical_name="src/main.rs",
        fingerprint="fp2",
        kind="file",
        name="src/main.rs",
        path="src/main.rs",
        language="rust",
        span=Span(start_line=1, start_col=0, end_line=100, end_col=0),
        origin="rust-v1",
    )

    # Create an import edge from the Rust file to serde
    import_edge = Edge(
        id="edge:sha256:imp1",
        src="rust:sha256:file1",
        dst="rust:serde::Serialize:0-0:module:module",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="rust-v1",
    )

    result = link_dependencies(
        toml_symbols=[cargo_dep],
        code_edges=[import_edge],
        code_symbols=[rust_file],
    )

    assert isinstance(result, DependencyLinkResult)
    assert len(result.edges) >= 1

    dep_edge = result.edges[0]
    assert dep_edge.edge_type == "depends_on_manifest"
    assert dep_edge.src == "rust:sha256:file1"  # The importing file
    assert dep_edge.dst == "toml:sha256:abc123"  # The dependency declaration


def test_link_python_dependencies():
    """Test linking Python imports to pyproject.toml dependencies."""
    # Create a pyproject.toml dependency symbol
    pyproject_dep = Symbol(
        id="toml:sha256:pyreq1",
        stable_id=None,
        shape_id=None,
        canonical_name="requests",
        fingerprint="fp1",
        kind="dependency",
        name="requests",
        path="pyproject.toml",
        language="toml",
        span=Span(start_line=10, start_col=0, end_line=10, end_col=20),
        origin="toml-v1",
    )

    # Create a Python file ID symbol
    python_file = Symbol(
        id="python:sha256:file1",
        stable_id=None,
        shape_id=None,
        canonical_name="src/app.py",
        fingerprint="fp2",
        kind="module",
        name="src/app.py",
        path="src/app.py",
        language="python",
        span=Span(start_line=1, start_col=0, end_line=50, end_col=0),
        origin="python-v1",
    )

    # Create an import edge from the Python file to requests
    import_edge = Edge(
        id="edge:sha256:imp1",
        src="python:sha256:file1",
        dst="python:requests:0-0:module:module",
        edge_type="imports",
        line=2,
        confidence=0.95,
        origin="python-v1",
    )

    result = link_dependencies(
        toml_symbols=[pyproject_dep],
        code_edges=[import_edge],
        code_symbols=[python_file],
    )

    assert len(result.edges) >= 1

    dep_edge = result.edges[0]
    assert dep_edge.edge_type == "depends_on_manifest"
    assert "requests" in pyproject_dep.name


def test_no_match_different_dependencies():
    """Test that unrelated imports and dependencies don't link."""
    cargo_dep = Symbol(
        id="toml:sha256:abc123",
        stable_id=None,
        shape_id=None,
        canonical_name="serde",
        fingerprint="fp1",
        kind="dependency",
        name="serde",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml-v1",
    )

    # Import a completely different crate
    import_edge = Edge(
        id="edge:sha256:imp1",
        src="rust:sha256:file1",
        dst="rust:tokio::sync:0-0:module:module",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="rust-v1",
    )

    result = link_dependencies(
        toml_symbols=[cargo_dep],
        code_edges=[import_edge],
        code_symbols=[],
    )

    assert len(result.edges) == 0


def test_multiple_files_import_same_dependency():
    """Test that multiple files importing same dep each get an edge."""
    cargo_dep = Symbol(
        id="toml:sha256:abc123",
        stable_id=None,
        shape_id=None,
        canonical_name="serde",
        fingerprint="fp1",
        kind="dependency",
        name="serde",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml-v1",
    )

    import_edge1 = Edge(
        id="edge:sha256:imp1",
        src="rust:sha256:file1",
        dst="rust:serde::Serialize:0-0:module:module",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="rust-v1",
    )

    import_edge2 = Edge(
        id="edge:sha256:imp2",
        src="rust:sha256:file2",
        dst="rust:serde::Deserialize:0-0:module:module",
        edge_type="imports",
        line=5,
        confidence=0.95,
        origin="rust-v1",
    )

    result = link_dependencies(
        toml_symbols=[cargo_dep],
        code_edges=[import_edge1, import_edge2],
        code_symbols=[],
    )

    assert len(result.edges) == 2
    sources = {e.src for e in result.edges}
    assert "rust:sha256:file1" in sources
    assert "rust:sha256:file2" in sources


def test_empty_inputs():
    """Test with empty inputs."""
    result = link_dependencies(
        toml_symbols=[],
        code_edges=[],
        code_symbols=[],
    )

    assert isinstance(result, DependencyLinkResult)
    assert len(result.edges) == 0
    assert result.run is not None


def test_run_metadata():
    """Test that run metadata is populated."""
    cargo_dep = Symbol(
        id="toml:sha256:abc123",
        stable_id=None,
        shape_id=None,
        canonical_name="serde",
        fingerprint="fp1",
        kind="dependency",
        name="serde",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml-v1",
    )

    import_edge = Edge(
        id="edge:sha256:imp1",
        src="rust:sha256:file1",
        dst="rust:serde::Serialize:0-0:module:module",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="rust-v1",
    )

    result = link_dependencies(
        toml_symbols=[cargo_dep],
        code_edges=[import_edge],
        code_symbols=[],
    )

    assert result.run is not None
    assert result.run.pass_id == PASS_ID
    assert result.run.duration_ms >= 0


def test_rust_underscore_crate_name():
    """Test that crate names with underscores/hyphens are matched."""
    # In Cargo.toml, it's `my-crate`, but in Rust code it's `my_crate`
    cargo_dep = Symbol(
        id="toml:sha256:abc123",
        stable_id=None,
        shape_id=None,
        canonical_name="my-crate",
        fingerprint="fp1",
        kind="dependency",
        name="my-crate",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml-v1",
    )

    import_edge = Edge(
        id="edge:sha256:imp1",
        src="rust:sha256:file1",
        dst="rust:my_crate::Something:0-0:module:module",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="rust-v1",
    )

    result = link_dependencies(
        toml_symbols=[cargo_dep],
        code_edges=[import_edge],
        code_symbols=[],
    )

    assert len(result.edges) == 1


def test_python_submodule_import():
    """Test that importing a submodule links to the parent package."""
    pyproject_dep = Symbol(
        id="toml:sha256:pyreq1",
        stable_id=None,
        shape_id=None,
        canonical_name="requests",
        fingerprint="fp1",
        kind="dependency",
        name="requests",
        path="pyproject.toml",
        language="toml",
        span=Span(start_line=10, start_col=0, end_line=10, end_col=20),
        origin="toml-v1",
    )

    # Import requests.adapters (submodule)
    import_edge = Edge(
        id="edge:sha256:imp1",
        src="python:sha256:file1",
        dst="python:requests.adapters:0-0:module:module",
        edge_type="imports",
        line=2,
        confidence=0.95,
        origin="python-v1",
    )

    result = link_dependencies(
        toml_symbols=[pyproject_dep],
        code_edges=[import_edge],
        code_symbols=[],
    )

    assert len(result.edges) == 1


def test_ignores_non_import_edges():
    """Test that non-import edges are ignored."""
    cargo_dep = Symbol(
        id="toml:sha256:abc123",
        stable_id=None,
        shape_id=None,
        canonical_name="serde",
        fingerprint="fp1",
        kind="dependency",
        name="serde",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml-v1",
    )

    # A calls edge, not an imports edge
    calls_edge = Edge(
        id="edge:sha256:call1",
        src="rust:sha256:file1",
        dst="rust:serde::to_string:0-0:function:function",
        edge_type="calls",
        line=10,
        confidence=0.9,
        origin="rust-v1",
    )

    result = link_dependencies(
        toml_symbols=[cargo_dep],
        code_edges=[calls_edge],
        code_symbols=[],
    )

    assert len(result.edges) == 0


def test_ignores_non_dependency_toml_symbols():
    """Test that non-dependency TOML symbols are ignored."""
    # A table symbol, not a dependency
    table_sym = Symbol(
        id="toml:sha256:tbl1",
        stable_id=None,
        shape_id=None,
        canonical_name="package",
        fingerprint="fp1",
        kind="table",
        name="package",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=1, start_col=0, end_line=1, end_col=10),
        origin="toml-v1",
    )

    import_edge = Edge(
        id="edge:sha256:imp1",
        src="rust:sha256:file1",
        dst="rust:serde::Serialize:0-0:module:module",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="rust-v1",
    )

    result = link_dependencies(
        toml_symbols=[table_sym],
        code_edges=[import_edge],
        code_symbols=[],
    )

    assert len(result.edges) == 0


def test_ignores_unsupported_language_imports():
    """Test that imports from unsupported languages are skipped."""
    cargo_dep = Symbol(
        id="toml:sha256:abc123",
        stable_id=None,
        shape_id=None,
        canonical_name="serde",
        fingerprint="fp1",
        kind="dependency",
        name="serde",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml-v1",
    )

    # A Go import (not Rust or Python)
    import_edge = Edge(
        id="edge:sha256:imp1",
        src="go:sha256:file1",
        dst="go:github.com/user/pkg:0-0:module:module",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="go-v1",
    )

    result = link_dependencies(
        toml_symbols=[cargo_dep],
        code_edges=[import_edge],
        code_symbols=[],
    )

    assert len(result.edges) == 0


def test_deduplicates_same_file_same_dependency():
    """Test that same file importing same dep multiple times gets one edge."""
    cargo_dep = Symbol(
        id="toml:sha256:abc123",
        stable_id=None,
        shape_id=None,
        canonical_name="serde",
        fingerprint="fp1",
        kind="dependency",
        name="serde",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml-v1",
    )

    # Same file imports serde twice
    import_edge1 = Edge(
        id="edge:sha256:imp1",
        src="rust:sha256:file1",
        dst="rust:serde::Serialize:0-0:module:module",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="rust-v1",
    )

    import_edge2 = Edge(
        id="edge:sha256:imp2",
        src="rust:sha256:file1",  # Same source file
        dst="rust:serde::Deserialize:0-0:module:module",
        edge_type="imports",
        line=4,
        confidence=0.95,
        origin="rust-v1",
    )

    result = link_dependencies(
        toml_symbols=[cargo_dep],
        code_edges=[import_edge1, import_edge2],
        code_symbols=[],
    )

    # Should only produce one edge (deduplicated)
    assert len(result.edges) == 1


def test_rust_import_no_namespace():
    """Test Rust imports with no :: separator."""
    cargo_dep = Symbol(
        id="toml:sha256:abc123",
        stable_id=None,
        shape_id=None,
        canonical_name="log",
        fingerprint="fp1",
        kind="dependency",
        name="log",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml-v1",
    )

    # Import like `use log;` without ::
    import_edge = Edge(
        id="edge:sha256:imp1",
        src="rust:sha256:file1",
        dst="rust:log:0-0:module:module",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="rust-v1",
    )

    result = link_dependencies(
        toml_symbols=[cargo_dep],
        code_edges=[import_edge],
        code_symbols=[],
    )

    assert len(result.edges) == 1


def test_python_simple_import():
    """Test Python import without dots or colons."""
    pyproject_dep = Symbol(
        id="toml:sha256:pyreq1",
        stable_id=None,
        shape_id=None,
        canonical_name="flask",
        fingerprint="fp1",
        kind="dependency",
        name="flask",
        path="pyproject.toml",
        language="toml",
        span=Span(start_line=10, start_col=0, end_line=10, end_col=20),
        origin="toml-v1",
    )

    # Simple import like `import flask`
    import_edge = Edge(
        id="edge:sha256:imp1",
        src="python:sha256:file1",
        dst="python:flask",
        edge_type="imports",
        line=2,
        confidence=0.95,
        origin="python-v1",
    )

    result = link_dependencies(
        toml_symbols=[pyproject_dep],
        code_edges=[import_edge],
        code_symbols=[],
    )

    assert len(result.edges) == 1


def test_rust_bare_crate_import():
    """Test Rust import with just the crate name, no separators."""
    cargo_dep = Symbol(
        id="toml:sha256:abc123",
        stable_id=None,
        shape_id=None,
        canonical_name="log",
        fingerprint="fp1",
        kind="dependency",
        name="log",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml-v1",
    )

    # Bare crate import: just "rust:log" with no :: or :
    import_edge = Edge(
        id="edge:sha256:imp1",
        src="rust:sha256:file1",
        dst="rust:log",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="rust-v1",
    )

    result = link_dependencies(
        toml_symbols=[cargo_dep],
        code_edges=[import_edge],
        code_symbols=[],
    )

    assert len(result.edges) == 1
