"""Tests for TOML analyzer using tree-sitter-toml.

Tests verify that the analyzer correctly extracts:
- Table definitions (sections)
- Key-value pair bindings
- Dependency declarations (Cargo.toml, pyproject.toml)
- Array of tables (e.g., [[bin]])
"""

from hypergumbo.analyze.toml_config import (
    PASS_ID,
    PASS_VERSION,
    TomlAnalysisResult,
    analyze_toml_files,
    find_toml_files,
)


def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "toml-v1"
    assert PASS_VERSION == "hypergumbo-0.1.0"


def test_analyze_table(tmp_path):
    """Test detection of table definitions."""
    toml_file = tmp_path / "config.toml"
    toml_file.write_text("""
[package]
name = "myproject"
version = "0.1.0"
""")
    result = analyze_toml_files(tmp_path)

    assert not result.skipped
    tables = [s for s in result.symbols if s.kind == "table"]
    assert len(tables) >= 1
    assert any(t.name == "package" for t in tables)


def test_analyze_nested_table(tmp_path):
    """Test detection of nested tables."""
    toml_file = tmp_path / "config.toml"
    toml_file.write_text("""
[package.metadata]
key = "value"

[package.metadata.docs]
enable = true
""")
    result = analyze_toml_files(tmp_path)

    tables = [s for s in result.symbols if s.kind == "table"]
    assert len(tables) >= 2


def test_analyze_cargo_dependencies(tmp_path):
    """Test detection of Rust dependencies from Cargo.toml."""
    toml_file = tmp_path / "Cargo.toml"
    toml_file.write_text("""
[package]
name = "mycrate"

[dependencies]
serde = "1.0"
tokio = { version = "1.0", features = ["full"] }

[dev-dependencies]
criterion = "0.5"
""")
    result = analyze_toml_files(tmp_path)

    deps = [s for s in result.symbols if s.kind == "dependency"]
    assert len(deps) >= 3
    assert any(d.name == "serde" for d in deps)
    assert any(d.name == "tokio" for d in deps)
    assert any(d.name == "criterion" for d in deps)


def test_analyze_pyproject_dependencies(tmp_path):
    """Test detection of Python dependencies from pyproject.toml."""
    toml_file = tmp_path / "pyproject.toml"
    toml_file.write_text("""
[project]
name = "mypackage"
dependencies = [
    "requests>=2.0",
    "click",
]

[project.optional-dependencies]
dev = ["pytest", "black"]
""")
    result = analyze_toml_files(tmp_path)

    # Should detect the project
    projects = [s for s in result.symbols if s.kind == "project"]
    assert len(projects) >= 1

    # Should also detect dependencies
    deps = [s for s in result.symbols if s.kind == "dependency"]
    assert len(deps) >= 2
    dep_names = [d.name for d in deps]
    assert "requests" in dep_names
    assert "click" in dep_names


def test_analyze_table_array(tmp_path):
    """Test detection of array of tables (e.g., [[bin]])."""
    toml_file = tmp_path / "Cargo.toml"
    toml_file.write_text("""
[[bin]]
name = "cli"
path = "src/cli.rs"

[[bin]]
name = "server"
path = "src/server.rs"
""")
    result = analyze_toml_files(tmp_path)

    bins = [s for s in result.symbols if s.kind == "binary"]
    assert len(bins) >= 2
    assert any(b.name == "cli" for b in bins)
    assert any(b.name == "server" for b in bins)


def test_analyze_library(tmp_path):
    """Test detection of library configuration."""
    toml_file = tmp_path / "Cargo.toml"
    toml_file.write_text("""
[lib]
name = "mylib"
crate-type = ["cdylib"]
""")
    result = analyze_toml_files(tmp_path)

    libs = [s for s in result.symbols if s.kind == "library"]
    assert len(libs) >= 1


def test_find_toml_files(tmp_path):
    """Test that TOML files are discovered correctly."""
    (tmp_path / "Cargo.toml").write_text("[package]")
    (tmp_path / "pyproject.toml").write_text("[project]")
    (tmp_path / "config.toml").write_text("[settings]")
    (tmp_path / "not_toml.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "nested.toml").write_text("[nested]")

    files = list(find_toml_files(tmp_path))
    assert len(files) >= 4


def test_analyze_empty_directory(tmp_path):
    """Test analysis of directory with no TOML files."""
    result = analyze_toml_files(tmp_path)

    assert not result.skipped
    assert len(result.symbols) == 0
    assert len(result.edges) == 0


def test_analysis_run_metadata(tmp_path):
    """Test that AnalysisRun metadata is correctly set."""
    toml_file = tmp_path / "test.toml"
    toml_file.write_text("[section]")

    result = analyze_toml_files(tmp_path)

    assert result.run is not None
    assert result.run.pass_id == PASS_ID
    assert result.run.version == PASS_VERSION
    assert result.run.files_analyzed >= 1
    assert result.run.duration_ms >= 0


def test_syntax_error_handling(tmp_path):
    """Test that syntax errors don't crash the analyzer."""
    toml_file = tmp_path / "broken.toml"
    toml_file.write_text("[invalid = = =")

    # Should not raise an exception
    result = analyze_toml_files(tmp_path)

    # Result should still be valid
    assert isinstance(result, TomlAnalysisResult)


def test_span_information(tmp_path):
    """Test that span information is correct."""
    toml_file = tmp_path / "span.toml"
    toml_file.write_text("""[package]
name = "test"
""")
    result = analyze_toml_files(tmp_path)

    tables = [s for s in result.symbols if s.kind == "table"]
    assert len(tables) >= 1

    # Check span
    assert tables[0].span.start_line >= 1
    assert tables[0].span.end_line >= tables[0].span.start_line


def test_tree_sitter_not_available():
    """Test graceful degradation when tree-sitter is not available."""
    from hypergumbo.analyze.toml_config import is_toml_tree_sitter_available

    # The function should return a boolean
    result = is_toml_tree_sitter_available()
    assert isinstance(result, bool)


def test_workspace_detection(tmp_path):
    """Test detection of Cargo workspace."""
    toml_file = tmp_path / "Cargo.toml"
    toml_file.write_text("""
[workspace]
members = ["crates/*"]
resolver = "2"
""")
    result = analyze_toml_files(tmp_path)

    workspaces = [s for s in result.symbols if s.kind == "workspace"]
    assert len(workspaces) >= 1


def test_analyze_test_section(tmp_path):
    """Test detection of [[test]] sections."""
    toml_file = tmp_path / "Cargo.toml"
    toml_file.write_text("""
[[test]]
name = "integration_tests"
path = "tests/integration.rs"
""")
    result = analyze_toml_files(tmp_path)

    tests = [s for s in result.symbols if s.kind == "test"]
    assert len(tests) >= 1
    assert any(t.name == "integration_tests" for t in tests)


def test_analyze_example_section(tmp_path):
    """Test detection of [[example]] sections."""
    toml_file = tmp_path / "Cargo.toml"
    toml_file.write_text("""
[[example]]
name = "demo"
path = "examples/demo.rs"
""")
    result = analyze_toml_files(tmp_path)

    examples = [s for s in result.symbols if s.kind == "example"]
    assert len(examples) >= 1
    assert any(e.name == "demo" for e in examples)


def test_analyze_bench_section(tmp_path):
    """Test detection of [[bench]] sections."""
    toml_file = tmp_path / "Cargo.toml"
    toml_file.write_text("""
[[bench]]
name = "perf_test"
harness = false
""")
    result = analyze_toml_files(tmp_path)

    benchmarks = [s for s in result.symbols if s.kind == "benchmark"]
    assert len(benchmarks) >= 1
    assert any(b.name == "perf_test" for b in benchmarks)


def test_build_target_source_file_edge(tmp_path):
    """Test that build targets create edges to their source files."""
    toml_file = tmp_path / "Cargo.toml"
    toml_file.write_text("""
[[bin]]
name = "cli"
path = "src/cli.rs"

[[test]]
name = "integration"
path = "tests/integration.rs"
""")
    result = analyze_toml_files(tmp_path)

    # Check edges are created linking targets to source files
    edges = [e for e in result.edges if e.edge_type == "defines_target"]
    assert len(edges) >= 2

    # Edges should point from target to source file path
    dst_paths = [e.dst for e in edges]
    assert any("src/cli.rs" in dst for dst in dst_paths)
    assert any("tests/integration.rs" in dst for dst in dst_paths)


def test_build_target_path_in_meta(tmp_path):
    """Test that build targets store source path in meta."""
    toml_file = tmp_path / "Cargo.toml"
    toml_file.write_text("""
[[bin]]
name = "mybin"
path = "src/bin/main.rs"
""")
    result = analyze_toml_files(tmp_path)

    bins = [s for s in result.symbols if s.kind == "binary"]
    assert len(bins) >= 1

    # Check meta contains the path
    mybin = next(b for b in bins if b.name == "mybin")
    assert mybin.meta is not None
    assert mybin.meta.get("path") == "src/bin/main.rs"
