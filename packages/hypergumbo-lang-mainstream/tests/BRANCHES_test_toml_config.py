"""Branch coverage tests for toml_config.py analyzer.

Tests specific branch paths in the TOML config analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Table and table array extraction
- Cargo.toml processing (package, dependencies, bins, etc.)
- pyproject.toml processing (project, dependencies, scripts)
- Build target edges
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_mainstream.toml_config import (
    _make_toml_symbol_id,
    _make_edge_id,
    analyze_toml_files,
    find_toml_files,
)


def make_toml_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a TOML file with given content."""
    (tmp_path / name).write_text(content)


class TestTomlHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID uses readable location-based format."""
        symbol_id = _make_toml_symbol_id("Cargo.toml", 1, 5, "mypackage", "package")
        assert symbol_id == "toml:Cargo.toml:1-5:mypackage:package"

    def test_make_edge_id_deterministic(self) -> None:
        """Test edge ID is deterministic."""
        edge_id1 = _make_edge_id("src1", "dst1", "defines_target")
        edge_id2 = _make_edge_id("src1", "dst1", "defines_target")
        assert edge_id1 == edge_id2
        assert edge_id1.startswith("edge:sha256:")


class TestCargoTomlExtraction:
    """Branch coverage for Cargo.toml extraction."""

    def test_package_section(self, tmp_path: Path) -> None:
        """Test [package] extraction from Cargo.toml."""
        make_toml_file(tmp_path, "Cargo.toml", """
[package]
name = "myapp"
version = "0.1.0"
edition = "2021"
""")
        result = analyze_toml_files(tmp_path)
        assert not result.skipped

        packages = [s for s in result.symbols if s.kind == "package"]
        assert len(packages) >= 1
        assert any(p.name == "myapp" for p in packages)

    def test_dependencies_section(self, tmp_path: Path) -> None:
        """Test [dependencies] extraction from Cargo.toml."""
        make_toml_file(tmp_path, "Cargo.toml", """
[package]
name = "myapp"

[dependencies]
serde = "1.0"
tokio = { version = "1.0", features = ["full"] }
""")
        result = analyze_toml_files(tmp_path)
        deps = [s for s in result.symbols if s.kind == "dependency"]

        assert len(deps) >= 2
        assert any(d.name == "serde" for d in deps)
        assert any(d.name == "tokio" for d in deps)

    def test_binary_target(self, tmp_path: Path) -> None:
        """Test [[bin]] extraction from Cargo.toml."""
        make_toml_file(tmp_path, "Cargo.toml", """
[package]
name = "myapp"

[[bin]]
name = "mycli"
path = "src/bin/cli.rs"
""")
        result = analyze_toml_files(tmp_path)
        binaries = [s for s in result.symbols if s.kind == "binary"]

        assert len(binaries) >= 1
        binary = binaries[0]
        assert binary.name == "mycli"
        assert binary.meta is not None
        assert binary.meta.get("path") == "src/bin/cli.rs"

    def test_lib_section(self, tmp_path: Path) -> None:
        """Test [lib] extraction from Cargo.toml."""
        make_toml_file(tmp_path, "Cargo.toml", """
[package]
name = "mylib"

[lib]
name = "mylib"
crate-type = ["cdylib"]
""")
        result = analyze_toml_files(tmp_path)
        libs = [s for s in result.symbols if s.kind == "library"]

        assert len(libs) >= 1

    def test_workspace_section(self, tmp_path: Path) -> None:
        """Test [workspace] extraction from Cargo.toml."""
        make_toml_file(tmp_path, "Cargo.toml", """
[workspace]
members = ["crate-a", "crate-b"]
""")
        result = analyze_toml_files(tmp_path)
        workspaces = [s for s in result.symbols if s.kind == "workspace"]

        assert len(workspaces) >= 1

    def test_test_target(self, tmp_path: Path) -> None:
        """Test [[test]] extraction from Cargo.toml."""
        make_toml_file(tmp_path, "Cargo.toml", """
[package]
name = "myapp"

[[test]]
name = "integration"
path = "tests/integration.rs"
""")
        result = analyze_toml_files(tmp_path)
        tests = [s for s in result.symbols if s.kind == "test"]

        assert len(tests) >= 1

    def test_example_target(self, tmp_path: Path) -> None:
        """Test [[example]] extraction from Cargo.toml."""
        make_toml_file(tmp_path, "Cargo.toml", """
[package]
name = "myapp"

[[example]]
name = "demo"
path = "examples/demo.rs"
""")
        result = analyze_toml_files(tmp_path)
        examples = [s for s in result.symbols if s.kind == "example"]

        assert len(examples) >= 1

    def test_bench_target(self, tmp_path: Path) -> None:
        """Test [[bench]] extraction from Cargo.toml."""
        make_toml_file(tmp_path, "Cargo.toml", """
[package]
name = "myapp"

[[bench]]
name = "perf"
path = "benches/perf.rs"
""")
        result = analyze_toml_files(tmp_path)
        benches = [s for s in result.symbols if s.kind == "benchmark"]

        assert len(benches) >= 1


class TestPyprojectTomlExtraction:
    """Branch coverage for pyproject.toml extraction."""

    def test_project_section(self, tmp_path: Path) -> None:
        """Test [project] extraction from pyproject.toml."""
        make_toml_file(tmp_path, "pyproject.toml", """
[project]
name = "mypackage"
version = "1.0.0"
""")
        result = analyze_toml_files(tmp_path)
        projects = [s for s in result.symbols if s.kind == "project"]

        assert len(projects) >= 1
        assert any(p.name == "mypackage" for p in projects)

    def test_project_dependencies(self, tmp_path: Path) -> None:
        """Test dependencies extraction from pyproject.toml."""
        make_toml_file(tmp_path, "pyproject.toml", """
[project]
name = "mypackage"
dependencies = [
    "requests>=2.0",
    "click",
]
""")
        result = analyze_toml_files(tmp_path)
        deps = [s for s in result.symbols if s.kind == "dependency"]

        assert len(deps) >= 2
        assert any(d.name == "requests" for d in deps)
        assert any(d.name == "click" for d in deps)

    def test_project_scripts(self, tmp_path: Path) -> None:
        """Test [project.scripts] extraction from pyproject.toml."""
        make_toml_file(tmp_path, "pyproject.toml", """
[project]
name = "mypackage"

[project.scripts]
mycli = "mypackage.cli:main"
""")
        result = analyze_toml_files(tmp_path)
        scripts = [s for s in result.symbols if s.kind == "script"]

        assert len(scripts) >= 1
        script = scripts[0]
        assert script.name == "mycli"
        assert script.meta is not None
        assert script.meta.get("entry_point") == "mypackage.cli:main"


class TestBuildTargetEdges:
    """Branch coverage for build target edges."""

    def test_binary_creates_edge(self, tmp_path: Path) -> None:
        """Test [[bin]] with path creates defines_target edge."""
        make_toml_file(tmp_path, "Cargo.toml", """
[package]
name = "myapp"

[[bin]]
name = "mycli"
path = "src/bin/cli.rs"
""")
        result = analyze_toml_files(tmp_path)
        target_edges = [e for e in result.edges if e.edge_type == "defines_target"]

        assert len(target_edges) >= 1


class TestDottedKeys:
    """Branch coverage for dotted key extraction."""

    def test_dotted_table_name(self, tmp_path: Path) -> None:
        """Test dotted table names are extracted."""
        make_toml_file(tmp_path, "config.toml", """
[server.http]
port = 8080
""")
        result = analyze_toml_files(tmp_path)
        tables = [s for s in result.symbols if s.kind == "table"]

        assert len(tables) >= 1
        assert any("server.http" in t.name for t in tables)


class TestFindTomlFiles:
    """Branch coverage for file discovery."""

    def test_finds_toml_files(self, tmp_path: Path) -> None:
        """Test .toml files are discovered."""
        (tmp_path / "config.toml").write_text("[section]\nkey = 'value'")

        files = list(find_toml_files(tmp_path))
        assert len(files) >= 1
        assert any(f.suffix == ".toml" for f in files)


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_toml_files(self, tmp_path: Path) -> None:
        """Test directory with no TOML files."""
        result = analyze_toml_files(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_toml(self, tmp_path: Path) -> None:
        """Test minimal TOML file."""
        make_toml_file(tmp_path, "config.toml", """
[section]
key = "value"
""")
        result = analyze_toml_files(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_toml_file(tmp_path, "config.toml", """
[section]
key = "value"
""")
        result = analyze_toml_files(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1
