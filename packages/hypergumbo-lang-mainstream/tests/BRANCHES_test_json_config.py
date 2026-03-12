# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for json_config.py analyzer.

Tests specific branch paths in the JSON config analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- package.json processing (dependencies, scripts, bin)
- tsconfig.json processing (references)
- composer.json processing
- File type detection
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_mainstream.json_config import (
    _detect_json_type,
    _make_symbol_id,
    _make_edge_id,
    analyze_json_files,
    find_json_files,
)


def make_json_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a JSON file with given content."""
    (tmp_path / name).write_text(content)


class TestJsonConfigHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = _make_symbol_id("package.json", 1, 10, "lodash", "dependency")
        assert symbol_id == "json:package.json:1-10:lodash:dependency"

    def test_make_edge_id_deterministic(self) -> None:
        """Test edge ID is deterministic."""
        edge_id1 = _make_edge_id("src1", "dst1", "depends_on")
        edge_id2 = _make_edge_id("src1", "dst1", "depends_on")
        assert edge_id1 == edge_id2
        assert edge_id1.startswith("edge:sha256:")


class TestJsonTypeDetection:
    """Branch coverage for JSON type detection."""

    def test_detect_package_json(self) -> None:
        """Test package.json detection."""
        assert _detect_json_type(Path("package.json")) == "package_json"

    def test_detect_tsconfig(self) -> None:
        """Test tsconfig.json detection."""
        assert _detect_json_type(Path("tsconfig.json")) == "tsconfig"
        assert _detect_json_type(Path("tsconfig.base.json")) == "tsconfig"
        assert _detect_json_type(Path("tsconfig.build.json")) == "tsconfig"
        assert _detect_json_type(Path("tsconfig.app.json")) == "tsconfig"

    def test_detect_composer(self) -> None:
        """Test composer.json detection."""
        assert _detect_json_type(Path("composer.json")) == "composer"

    def test_detect_generic(self) -> None:
        """Test generic JSON detection."""
        assert _detect_json_type(Path("config.json")) == "generic"
        assert _detect_json_type(Path(".eslintrc.json")) == "generic"


class TestPackageJsonProcessing:
    """Branch coverage for package.json processing."""

    def test_package_name_and_version(self, tmp_path: Path) -> None:
        """Test package name and version extraction."""
        make_json_file(tmp_path, "package.json", """{
  "name": "my-project",
  "version": "1.0.0"
}
""")
        result = analyze_json_files(tmp_path)
        assert not result.skipped

        packages = [s for s in result.symbols if s.kind == "package"]
        assert len(packages) >= 1
        assert any(p.name == "my-project" for p in packages)

    def test_dependencies_extraction(self, tmp_path: Path) -> None:
        """Test dependencies extraction."""
        make_json_file(tmp_path, "package.json", """{
  "name": "my-project",
  "dependencies": {
    "lodash": "^4.17.0",
    "express": "^4.18.0"
  }
}
""")
        result = analyze_json_files(tmp_path)
        deps = [s for s in result.symbols if s.kind == "dependency"]

        assert len(deps) >= 2
        assert any(d.name == "lodash" for d in deps)
        assert any(d.name == "express" for d in deps)

    def test_dev_dependencies_extraction(self, tmp_path: Path) -> None:
        """Test devDependencies extraction."""
        make_json_file(tmp_path, "package.json", """{
  "name": "my-project",
  "devDependencies": {
    "jest": "^29.0.0",
    "typescript": "^5.0.0"
  }
}
""")
        result = analyze_json_files(tmp_path)
        dev_deps = [s for s in result.symbols if s.kind == "devDependency"]

        assert len(dev_deps) >= 2
        assert any(d.name == "jest" for d in dev_deps)
        assert any(d.name == "typescript" for d in dev_deps)

    def test_scripts_extraction(self, tmp_path: Path) -> None:
        """Test scripts extraction."""
        make_json_file(tmp_path, "package.json", """{
  "name": "my-project",
  "scripts": {
    "build": "tsc",
    "test": "jest",
    "lint": "eslint ."
  }
}
""")
        result = analyze_json_files(tmp_path)
        scripts = [s for s in result.symbols if s.kind == "script"]

        assert len(scripts) >= 3
        assert any(s.name == "build" for s in scripts)
        assert any(s.name == "test" for s in scripts)
        assert any(s.name == "lint" for s in scripts)

    def test_bin_string_form(self, tmp_path: Path) -> None:
        """Test bin field with string form."""
        make_json_file(tmp_path, "package.json", """{
  "name": "my-cli",
  "bin": "./cli.js"
}
""")
        result = analyze_json_files(tmp_path)
        bins = [s for s in result.symbols if s.kind == "bin"]

        assert len(bins) >= 1
        assert any(b.name == "my-cli" for b in bins)

    def test_bin_object_form(self, tmp_path: Path) -> None:
        """Test bin field with object form."""
        make_json_file(tmp_path, "package.json", """{
  "name": "my-tools",
  "bin": {
    "tool1": "./bin/tool1.js",
    "tool2": "./bin/tool2.js"
  }
}
""")
        result = analyze_json_files(tmp_path)
        bins = [s for s in result.symbols if s.kind == "bin"]

        assert len(bins) >= 2
        assert any(b.name == "tool1" for b in bins)
        assert any(b.name == "tool2" for b in bins)

    def test_dependency_edges(self, tmp_path: Path) -> None:
        """Test dependency edges are created."""
        make_json_file(tmp_path, "package.json", """{
  "name": "my-project",
  "dependencies": {
    "lodash": "^4.17.0"
  }
}
""")
        result = analyze_json_files(tmp_path)
        dep_edges = [e for e in result.edges if e.edge_type == "depends_on"]

        assert len(dep_edges) >= 1


class TestTsconfigProcessing:
    """Branch coverage for tsconfig.json processing."""

    def test_tsconfig_symbol(self, tmp_path: Path) -> None:
        """Test tsconfig symbol extraction."""
        make_json_file(tmp_path, "tsconfig.json", """{
  "compilerOptions": {
    "target": "ES2020"
  }
}
""")
        result = analyze_json_files(tmp_path)
        tsconfigs = [s for s in result.symbols if s.kind == "tsconfig"]

        assert len(tsconfigs) >= 1

    def test_tsconfig_references(self, tmp_path: Path) -> None:
        """Test tsconfig project references extraction."""
        make_json_file(tmp_path, "tsconfig.json", """{
  "compilerOptions": {
    "composite": true
  },
  "references": [
    { "path": "./packages/core" },
    { "path": "./packages/cli" }
  ]
}
""")
        result = analyze_json_files(tmp_path)
        refs = [s for s in result.symbols if s.kind == "reference"]

        assert len(refs) >= 2
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(ref_edges) >= 2


class TestComposerJsonProcessing:
    """Branch coverage for composer.json processing."""

    def test_composer_package(self, tmp_path: Path) -> None:
        """Test composer package extraction."""
        make_json_file(tmp_path, "composer.json", """{
  "name": "vendor/my-package",
  "require": {
    "php": ">=8.0"
  }
}
""")
        result = analyze_json_files(tmp_path)
        packages = [s for s in result.symbols if s.kind == "composer_package"]

        assert len(packages) >= 1
        assert any(p.name == "vendor/my-package" for p in packages)

    def test_composer_require(self, tmp_path: Path) -> None:
        """Test composer require dependencies."""
        make_json_file(tmp_path, "composer.json", """{
  "name": "vendor/package",
  "require": {
    "guzzlehttp/guzzle": "^7.0",
    "monolog/monolog": "^2.0"
  }
}
""")
        result = analyze_json_files(tmp_path)
        deps = [s for s in result.symbols if s.kind == "dependency"]

        assert len(deps) >= 2

    def test_composer_require_dev(self, tmp_path: Path) -> None:
        """Test composer require-dev dependencies."""
        make_json_file(tmp_path, "composer.json", """{
  "name": "vendor/package",
  "require-dev": {
    "phpunit/phpunit": "^9.0"
  }
}
""")
        result = analyze_json_files(tmp_path)
        dev_deps = [s for s in result.symbols if s.kind == "devDependency"]

        assert len(dev_deps) >= 1


class TestFindJsonFiles:
    """Branch coverage for file discovery."""

    def test_finds_json_files(self, tmp_path: Path) -> None:
        """Test .json files are discovered."""
        (tmp_path / "config.json").write_text("{}")

        files = list(find_json_files(tmp_path))
        assert len(files) >= 1
        assert any(f.suffix == ".json" for f in files)


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_json_files(self, tmp_path: Path) -> None:
        """Test directory with no JSON files."""
        result = analyze_json_files(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_package_json(self, tmp_path: Path) -> None:
        """Test minimal package.json."""
        make_json_file(tmp_path, "package.json", """{
  "name": "min"
}
""")
        result = analyze_json_files(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_json_file(tmp_path, "package.json", """{
  "name": "my-project"
}
""")
        result = analyze_json_files(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1
