"""Tests for JSON configuration analyzer using tree-sitter-json.

Tests verify that the analyzer correctly extracts:
- package.json: dependencies, devDependencies, scripts
- tsconfig.json: project references
- composer.json: PHP dependencies
"""

from hypergumbo.analyze.json_config import (
    PASS_ID,
    PASS_VERSION,
    JSONAnalysisResult,
    analyze_json_files,
    find_json_files,
)


def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "json-v1"
    assert PASS_VERSION == "hypergumbo-0.1.0"


def test_analyze_package_json(tmp_path):
    """Test parsing package.json with dependencies."""
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text("""{
  "name": "my-app",
  "version": "1.0.0",
  "dependencies": {
    "express": "^4.17.1",
    "lodash": "~4.17.21"
  },
  "devDependencies": {
    "jest": "^27.0.0",
    "typescript": "^4.5.0"
  }
}
""")
    result = analyze_json_files(tmp_path)

    assert not result.skipped

    # Find package symbol
    packages = [s for s in result.symbols if s.kind == "package"]
    assert len(packages) >= 1
    pkg = packages[0]
    assert pkg.name == "my-app"
    assert pkg.meta is not None
    assert pkg.meta.get("version") == "1.0.0"

    # Find dependencies
    deps = [s for s in result.symbols if s.kind == "dependency"]
    assert len(deps) >= 2

    express_dep = next((d for d in deps if d.name == "express"), None)
    assert express_dep is not None
    assert express_dep.meta.get("version") == "^4.17.1"

    # Find devDependencies
    dev_deps = [s for s in result.symbols if s.kind == "devDependency"]
    assert len(dev_deps) >= 2

    jest_dep = next((d for d in dev_deps if d.name == "jest"), None)
    assert jest_dep is not None


def test_analyze_package_json_scripts(tmp_path):
    """Test parsing package.json scripts."""
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text("""{
  "name": "my-app",
  "scripts": {
    "start": "node index.js",
    "test": "jest",
    "build": "tsc"
  }
}
""")
    result = analyze_json_files(tmp_path)

    scripts = [s for s in result.symbols if s.kind == "script"]
    assert len(scripts) >= 3

    start_script = next((s for s in scripts if s.name == "start"), None)
    assert start_script is not None
    assert start_script.meta.get("command") == "node index.js"
    assert start_script.canonical_name == "npm run start"


def test_analyze_package_json_dependency_edges(tmp_path):
    """Test that dependency edges are created."""
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text("""{
  "name": "my-app",
  "dependencies": {
    "express": "^4.17.1"
  }
}
""")
    result = analyze_json_files(tmp_path)

    edges = [e for e in result.edges if e.edge_type == "depends_on"]
    assert len(edges) >= 1


def test_analyze_tsconfig(tmp_path):
    """Test parsing tsconfig.json."""
    tsconfig = tmp_path / "tsconfig.json"
    tsconfig.write_text("""{
  "compilerOptions": {
    "target": "ES2020",
    "module": "commonjs"
  },
  "references": [
    { "path": "./packages/core" },
    { "path": "./packages/utils" }
  ]
}
""")
    result = analyze_json_files(tmp_path)

    # Find tsconfig symbol
    configs = [s for s in result.symbols if s.kind == "tsconfig"]
    assert len(configs) >= 1

    # Find references
    refs = [s for s in result.symbols if s.kind == "reference"]
    assert len(refs) >= 2

    core_ref = next((r for r in refs if r.name == "./packages/core"), None)
    assert core_ref is not None


def test_analyze_tsconfig_reference_edges(tmp_path):
    """Test that reference edges are created."""
    tsconfig = tmp_path / "tsconfig.json"
    tsconfig.write_text("""{
  "references": [
    { "path": "./lib" }
  ]
}
""")
    result = analyze_json_files(tmp_path)

    edges = [e for e in result.edges if e.edge_type == "references"]
    assert len(edges) >= 1


def test_analyze_tsconfig_variants(tmp_path):
    """Test detection of tsconfig variants."""
    (tmp_path / "tsconfig.base.json").write_text('{"compilerOptions": {}}')
    (tmp_path / "tsconfig.build.json").write_text('{"compilerOptions": {}}')
    (tmp_path / "tsconfig.test.json").write_text('{"compilerOptions": {}}')

    result = analyze_json_files(tmp_path)

    configs = [s for s in result.symbols if s.kind == "tsconfig"]
    assert len(configs) >= 3


def test_analyze_composer_json(tmp_path):
    """Test parsing composer.json."""
    composer = tmp_path / "composer.json"
    composer.write_text("""{
  "name": "vendor/my-package",
  "require": {
    "php": "^8.0",
    "laravel/framework": "^9.0"
  },
  "require-dev": {
    "phpunit/phpunit": "^9.5"
  }
}
""")
    result = analyze_json_files(tmp_path)

    # Find composer package
    packages = [s for s in result.symbols if s.kind == "composer_package"]
    assert len(packages) >= 1
    assert packages[0].name == "vendor/my-package"

    # Find dependencies
    deps = [s for s in result.symbols if s.kind == "dependency"]
    assert len(deps) >= 2

    laravel_dep = next((d for d in deps if d.name == "laravel/framework"), None)
    assert laravel_dep is not None

    # Find dev dependencies
    dev_deps = [s for s in result.symbols if s.kind == "devDependency"]
    assert len(dev_deps) >= 1


def test_find_json_files(tmp_path):
    """Test that JSON files are discovered correctly."""
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "tsconfig.json").write_text("{}")
    (tmp_path / "not_json.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "config.json").write_text("{}")

    files = list(find_json_files(tmp_path))
    assert len(files) == 3


def test_analyze_empty_directory(tmp_path):
    """Test analysis of directory with no JSON files."""
    result = analyze_json_files(tmp_path)

    assert not result.skipped
    assert len(result.symbols) == 0
    assert len(result.edges) == 0


def test_analysis_run_metadata(tmp_path):
    """Test that AnalysisRun metadata is correctly set."""
    json_file = tmp_path / "package.json"
    json_file.write_text('{"name": "test"}')

    result = analyze_json_files(tmp_path)

    assert result.run is not None
    assert result.run.pass_id == PASS_ID
    assert result.run.version == PASS_VERSION
    assert result.run.files_analyzed >= 1
    assert result.run.duration_ms >= 0


def test_generic_json_not_extracted(tmp_path):
    """Test that generic JSON files don't produce symbols."""
    json_file = tmp_path / "config.json"
    json_file.write_text('{"debug": true, "port": 3000}')

    result = analyze_json_files(tmp_path)

    # Generic JSON should be analyzed but not produce symbols
    assert result.run is not None
    assert result.run.files_analyzed >= 1
    assert len(result.symbols) == 0


def test_span_information(tmp_path):
    """Test that span information is correct."""
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text('{\n  "name": "test"\n}')

    result = analyze_json_files(tmp_path)

    packages = [s for s in result.symbols if s.kind == "package"]
    assert len(packages) >= 1
    assert packages[0].span is not None
    assert packages[0].span.start_line >= 1


def test_syntax_error_handling(tmp_path):
    """Test that syntax errors don't crash the analyzer."""
    json_file = tmp_path / "broken.json"
    json_file.write_text("{{{invalid json}}}")

    # Should not raise an exception
    result = analyze_json_files(tmp_path)

    # Result should still be valid
    assert isinstance(result, JSONAnalysisResult)


def test_package_json_without_name(tmp_path):
    """Test package.json without name field."""
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text("""{
  "dependencies": {
    "express": "^4.17.1"
  }
}
""")
    result = analyze_json_files(tmp_path)

    # Should still extract dependencies even without package name
    deps = [s for s in result.symbols if s.kind == "dependency"]
    assert len(deps) >= 1


def test_package_json_without_version(tmp_path):
    """Test package.json without version field."""
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text("""{
  "name": "test-pkg"
}
""")
    result = analyze_json_files(tmp_path)

    packages = [s for s in result.symbols if s.kind == "package"]
    assert len(packages) >= 1
    # Version should be None or not present in meta
    assert packages[0].meta.get("version") is None
