# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for dependency linker.

Tests that the linker correctly connects:
- Cargo.toml dependencies to Rust `use` imports
- pyproject.toml dependencies to Python imports
"""

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.dependency import (
    PASS_ID,
    DependencyLinkResult,
    link_dependencies,
)


def test_pass_id():
    """Verify pass ID is set correctly."""
    assert PASS_ID == "dependency-linker"


def test_link_rust_dependencies():
    """Test linking Rust imports to Cargo.toml dependencies."""
    # Create a Cargo.toml dependency symbol
    cargo_dep = Symbol(
        id="toml:sha256:abc123",
        stable_id=None,
        shape_id=None,
        qualified_name="serde",
        fingerprint="fp1",
        kind="dependency",
        name="serde",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml",
    )

    # Create a Rust file ID symbol
    rust_file = Symbol(
        id="rust:sha256:file1",
        stable_id=None,
        shape_id=None,
        qualified_name="src/main.rs",
        fingerprint="fp2",
        kind="file",
        name="src/main.rs",
        path="src/main.rs",
        language="rust",
        span=Span(start_line=1, start_col=0, end_line=100, end_col=0),
        origin="rust",
    )

    # Create an import edge from the Rust file to serde
    import_edge = Edge(
        id="edge:sha256:imp1",
        src="rust:sha256:file1",
        dst="rust:serde::Serialize:0-0:module:module",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="rust",

        origin_run_id="test",
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
        qualified_name="requests",
        fingerprint="fp1",
        kind="dependency",
        name="requests",
        path="pyproject.toml",
        language="toml",
        span=Span(start_line=10, start_col=0, end_line=10, end_col=20),
        origin="toml",
    )

    # Create a Python file ID symbol
    python_file = Symbol(
        id="python:sha256:file1",
        stable_id=None,
        shape_id=None,
        qualified_name="src/app.py",
        fingerprint="fp2",
        kind="module",
        name="src/app.py",
        path="src/app.py",
        language="python",
        span=Span(start_line=1, start_col=0, end_line=50, end_col=0),
        origin="python",
    )

    # Create an import edge from the Python file to requests
    import_edge = Edge(
        id="edge:sha256:imp1",
        src="python:sha256:file1",
        dst="python:requests:0-0:module:module",
        edge_type="imports",
        line=2,
        confidence=0.95,
        origin="python",

        origin_run_id="test",
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
        qualified_name="serde",
        fingerprint="fp1",
        kind="dependency",
        name="serde",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml",
    )

    # Import a completely different crate
    import_edge = Edge(
        id="edge:sha256:imp1",
        src="rust:sha256:file1",
        dst="rust:tokio::sync:0-0:module:module",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="rust",

        origin_run_id="test",
    )

    result = link_dependencies(
        toml_symbols=[cargo_dep],
        code_edges=[import_edge],
        code_symbols=[],
    )

    assert len(result.edges) == 0


def _dep_sym(sym_id: str, name: str, manifest_path: str) -> Symbol:
    return Symbol(
        id=sym_id, stable_id=None, shape_id=None, qualified_name=name,
        fingerprint=sym_id, kind="dependency", name=name, path=manifest_path,
        language="toml",
        span=Span(start_line=1, start_col=0, end_line=1, end_col=10),
        origin="toml",
    )


def _file_sym(sym_id: str, file_path: str) -> Symbol:
    return Symbol(
        id=sym_id, stable_id=None, shape_id=None, qualified_name=file_path,
        fingerprint=sym_id, kind="file", name=file_path, path=file_path,
        language="python",
        span=Span(start_line=1, start_col=0, end_line=1, end_col=0),
        origin="python",
    )


def _import_edge(edge_id: str, src_id: str, dep_name: str) -> Edge:
    return Edge(
        id=edge_id, src=src_id,
        dst=f"python:{dep_name}:0-0:module:module",
        edge_type="imports", line=1, confidence=0.95,
        origin="python", origin_run_id="test",
    )


def test_depends_on_manifest_attributes_to_nearest_package_manifest():
    """WI-timon: when the same dependency name is declared in MULTIPLE monorepo
    manifests, a file's depends_on_manifest edge must point to its OWN package's
    manifest (nearest enclosing directory), not whichever manifest happened to be
    processed last into a global flat lookup."""
    dep_a = _dep_sym("toml:a:rich", "rich", "packages/a/pyproject.toml")
    dep_b = _dep_sym("toml:b:rich", "rich", "packages/b/pyproject.toml")
    file_a = _file_sym("python:a-mod", "packages/a/src/a/mod.py")
    edge = _import_edge("e1", "python:a-mod", "rich")

    result = link_dependencies(
        toml_symbols=[dep_a, dep_b], code_edges=[edge], code_symbols=[file_a]
    )
    dep_edges = [e for e in result.edges if e.edge_type == "depends_on_manifest"]
    assert len(dep_edges) == 1, dep_edges
    # Must attribute to package A's manifest, NOT dep_b (the last-processed).
    assert dep_edges[0].dst == "toml:a:rich", dep_edges[0].dst


def test_depends_on_manifest_single_manifest_unchanged():
    """A dependency declared in only ONE manifest links to it regardless of the
    importing file's location (no regression for non-monorepo repos)."""
    dep = _dep_sym("toml:root:rich", "rich", "pyproject.toml")
    file_s = _file_sym("python:mod", "src/app.py")
    edge = _import_edge("e1", "python:mod", "rich")

    result = link_dependencies(
        toml_symbols=[dep], code_edges=[edge], code_symbols=[file_s]
    )
    dep_edges = [e for e in result.edges if e.edge_type == "depends_on_manifest"]
    assert len(dep_edges) == 1
    assert dep_edges[0].dst == "toml:root:rich"


def test_depends_on_manifest_root_manifest_is_nearest_when_file_at_root():
    """A file NOT under any nested package dir attributes to a shared/root
    manifest (the shallowest enclosing dir) when the dep is multiply-declared."""
    dep_root = _dep_sym("toml:root:rich", "rich", "pyproject.toml")
    dep_pkg = _dep_sym("toml:pkg:rich", "rich", "packages/a/pyproject.toml")
    file_root = _file_sym("python:root-mod", "tools/script.py")
    edge = _import_edge("e1", "python:root-mod", "rich")

    result = link_dependencies(
        toml_symbols=[dep_pkg, dep_root], code_edges=[edge],
        code_symbols=[file_root],
    )
    dep_edges = [e for e in result.edges if e.edge_type == "depends_on_manifest"]
    assert len(dep_edges) == 1
    # tools/script.py is under the root manifest's dir ("") but not packages/a.
    assert dep_edges[0].dst == "toml:root:rich", dep_edges[0].dst


def test_depends_on_manifest_ambiguous_fallback_is_deterministic():
    """When the importing file is not resolvable to a path (src not in
    code_symbols) and the dep is multiply-declared, fall back deterministically
    (first-by-id) and flag the uncertainty — not last-writer-wins."""
    dep_b = _dep_sym("toml:b:rich", "rich", "packages/b/pyproject.toml")
    dep_a = _dep_sym("toml:a:rich", "rich", "packages/a/pyproject.toml")
    edge = _import_edge("e1", "python:unknown-file", "rich")

    result = link_dependencies(
        toml_symbols=[dep_b, dep_a], code_edges=[edge], code_symbols=[]
    )
    dep_edges = [e for e in result.edges if e.edge_type == "depends_on_manifest"]
    assert len(dep_edges) == 1
    # Deterministic: sorted-by-id first is toml:a:rich regardless of input order.
    assert dep_edges[0].dst == "toml:a:rich", dep_edges[0].dst
    # Ambiguous attribution carries the registered disambiguation_fallback flag
    # and the INV-zuhub contract (confidence <= 0.5), not a confident 0.9.
    assert dep_edges[0].meta.get("disambiguation_fallback") is True
    assert dep_edges[0].confidence <= 0.5


def test_depends_on_manifest_file_outside_all_package_dirs_falls_back():
    """An importer whose path is under NO candidate manifest dir (and there is no
    root manifest) falls back deterministically with the ambiguity flag — the
    importing_path-set-but-no-enclosure branch."""
    dep_a = _dep_sym("toml:a:rich", "rich", "packages/a/pyproject.toml")
    dep_b = _dep_sym("toml:b:rich", "rich", "packages/b/pyproject.toml")
    file_out = _file_sym("python:out", "scripts/tool.py")
    edge = _import_edge("e1", "python:out", "rich")

    result = link_dependencies(
        toml_symbols=[dep_a, dep_b], code_edges=[edge], code_symbols=[file_out]
    )
    dep_edges = [e for e in result.edges if e.edge_type == "depends_on_manifest"]
    assert len(dep_edges) == 1
    assert dep_edges[0].dst == "toml:a:rich"
    assert dep_edges[0].meta.get("disambiguation_fallback") is True


def test_multiple_files_import_same_dependency():
    """Test that multiple files importing same dep each get an edge."""
    cargo_dep = Symbol(
        id="toml:sha256:abc123",
        stable_id=None,
        shape_id=None,
        qualified_name="serde",
        fingerprint="fp1",
        kind="dependency",
        name="serde",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml",
    )

    import_edge1 = Edge(
        id="edge:sha256:imp1",
        src="rust:sha256:file1",
        dst="rust:serde::Serialize:0-0:module:module",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="rust",

        origin_run_id="test",
    )

    import_edge2 = Edge(
        id="edge:sha256:imp2",
        src="rust:sha256:file2",
        dst="rust:serde::Deserialize:0-0:module:module",
        edge_type="imports",
        line=5,
        confidence=0.95,
        origin="rust",

        origin_run_id="test",
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
        qualified_name="serde",
        fingerprint="fp1",
        kind="dependency",
        name="serde",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml",
    )

    import_edge = Edge(
        id="edge:sha256:imp1",
        src="rust:sha256:file1",
        dst="rust:serde::Serialize:0-0:module:module",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="rust",

        origin_run_id="test",
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
        qualified_name="my-crate",
        fingerprint="fp1",
        kind="dependency",
        name="my-crate",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml",
    )

    import_edge = Edge(
        id="edge:sha256:imp1",
        src="rust:sha256:file1",
        dst="rust:my_crate::Something:0-0:module:module",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="rust",

        origin_run_id="test",
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
        qualified_name="requests",
        fingerprint="fp1",
        kind="dependency",
        name="requests",
        path="pyproject.toml",
        language="toml",
        span=Span(start_line=10, start_col=0, end_line=10, end_col=20),
        origin="toml",
    )

    # Import requests.adapters (submodule)
    import_edge = Edge(
        id="edge:sha256:imp1",
        src="python:sha256:file1",
        dst="python:requests.adapters:0-0:module:module",
        edge_type="imports",
        line=2,
        confidence=0.95,
        origin="python",

        origin_run_id="test",
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
        qualified_name="serde",
        fingerprint="fp1",
        kind="dependency",
        name="serde",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml",
    )

    # A calls edge, not an imports edge
    calls_edge = Edge(
        id="edge:sha256:call1",
        src="rust:sha256:file1",
        dst="rust:serde::to_string:0-0:function:function",
        edge_type="calls",
        line=10,
        confidence=0.9,
        origin="rust",

        origin_run_id="test",
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
        qualified_name="package",
        fingerprint="fp1",
        kind="table",
        name="package",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=1, start_col=0, end_line=1, end_col=10),
        origin="toml",
    )

    import_edge = Edge(
        id="edge:sha256:imp1",
        src="rust:sha256:file1",
        dst="rust:serde::Serialize:0-0:module:module",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="rust",

        origin_run_id="test",
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
        qualified_name="serde",
        fingerprint="fp1",
        kind="dependency",
        name="serde",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml",
    )

    # A Go import (not Rust or Python)
    import_edge = Edge(
        id="edge:sha256:imp1",
        src="go:sha256:file1",
        dst="go:github.com/user/pkg:0-0:module:module",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="go",

        origin_run_id="test",
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
        qualified_name="serde",
        fingerprint="fp1",
        kind="dependency",
        name="serde",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml",
    )

    # Same file imports serde twice
    import_edge1 = Edge(
        id="edge:sha256:imp1",
        src="rust:sha256:file1",
        dst="rust:serde::Serialize:0-0:module:module",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="rust",

        origin_run_id="test",
    )

    import_edge2 = Edge(
        id="edge:sha256:imp2",
        src="rust:sha256:file1",  # Same source file
        dst="rust:serde::Deserialize:0-0:module:module",
        edge_type="imports",
        line=4,
        confidence=0.95,
        origin="rust",

        origin_run_id="test",
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
        qualified_name="log",
        fingerprint="fp1",
        kind="dependency",
        name="log",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml",
    )

    # Import like `use log;` without ::
    import_edge = Edge(
        id="edge:sha256:imp1",
        src="rust:sha256:file1",
        dst="rust:log:0-0:module:module",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="rust",

        origin_run_id="test",
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
        qualified_name="flask",
        fingerprint="fp1",
        kind="dependency",
        name="flask",
        path="pyproject.toml",
        language="toml",
        span=Span(start_line=10, start_col=0, end_line=10, end_col=20),
        origin="toml",
    )

    # Simple import like `import flask`
    import_edge = Edge(
        id="edge:sha256:imp1",
        src="python:sha256:file1",
        dst="python:flask",
        edge_type="imports",
        line=2,
        confidence=0.95,
        origin="python",

        origin_run_id="test",
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
        qualified_name="log",
        fingerprint="fp1",
        kind="dependency",
        name="log",
        path="Cargo.toml",
        language="toml",
        span=Span(start_line=5, start_col=0, end_line=5, end_col=15),
        origin="toml",
    )

    # Bare crate import: just "rust:log" with no :: or :
    import_edge = Edge(
        id="edge:sha256:imp1",
        src="rust:sha256:file1",
        dst="rust:log",
        edge_type="imports",
        line=3,
        confidence=0.95,
        origin="rust",

        origin_run_id="test",
    )

    result = link_dependencies(
        toml_symbols=[cargo_dep],
        code_edges=[import_edge],
        code_symbols=[],
    )

    assert len(result.edges) == 1
