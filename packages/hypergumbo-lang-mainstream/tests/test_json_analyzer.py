"""Tests for JSON configuration analyzer using tree-sitter-json.

Tests verify that the analyzer correctly extracts:
- package.json: dependencies, devDependencies, scripts
- tsconfig.json: project references
- composer.json: PHP dependencies
"""

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.ir import PASS_VERSION
from hypergumbo_lang_mainstream.json_config import (
    PASS_ID,
    analyze_json_files,
    find_json_files,
)

def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "json-v1"
    assert PASS_VERSION == "2.1.0"

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

def test_analyze_package_json_bin_entries(tmp_path):
    """Test parsing package.json bin entries (CLI executables).

    The "bin" field in package.json defines CLI entry points:
    - String form: "bin": "./cli.js" (uses package name)
    - Object form: "bin": {"my-cli": "./bin/cli.js", "other": "./bin/other.js"}

    These are important entrypoints because they're what users invoke from the command line.
    """
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text("""{
  "name": "my-cli-tool",
  "bin": {
    "my-cli": "./bin/cli.js",
    "my-tool": "./bin/tool.js"
  }
}
""")
    result = analyze_json_files(tmp_path)

    bins = [s for s in result.symbols if s.kind == "bin"]
    assert len(bins) >= 2

    my_cli = next((b for b in bins if b.name == "my-cli"), None)
    assert my_cli is not None
    assert my_cli.meta is not None
    assert my_cli.meta.get("path") == "./bin/cli.js"
    assert my_cli.canonical_name == "my-cli"  # CLI command name

def test_analyze_package_json_bin_string_form(tmp_path):
    """Test parsing package.json bin as a string (single binary).

    When bin is a string, the command name is the package name.
    """
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text("""{
  "name": "my-tool",
  "bin": "./bin/main.js"
}
""")
    result = analyze_json_files(tmp_path)

    bins = [s for s in result.symbols if s.kind == "bin"]
    assert len(bins) >= 1

    my_tool = bins[0]
    assert my_tool.name == "my-tool"  # Uses package name when bin is string
    assert my_tool.meta is not None
    assert my_tool.meta.get("path") == "./bin/main.js"

def test_bin_creates_defines_target_edge(tmp_path):
    """npm bin entries should create defines_target edges.

    This mirrors Cargo [[bin]] behavior: the bin symbol should have a
    defines_target edge pointing to the target file path, enabling the
    build-target linker to connect it to main().
    """
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text("""{
  "name": "my-cli",
  "bin": {
    "my-cli": "./bin/cli.js"
  }
}
""")
    result = analyze_json_files(tmp_path)

    target_edges = [e for e in result.edges if e.edge_type == "defines_target"]
    assert len(target_edges) >= 1
    assert target_edges[0].dst == "bin/cli.js"


def test_bin_string_creates_defines_target_edge(tmp_path):
    """npm bin string form should also create defines_target edge."""
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text("""{
  "name": "my-tool",
  "bin": "./src/main.js"
}
""")
    result = analyze_json_files(tmp_path)

    target_edges = [e for e in result.edges if e.edge_type == "defines_target"]
    assert len(target_edges) >= 1
    assert target_edges[0].dst == "src/main.js"


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
    assert isinstance(result, AnalysisResult)

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


def test_package_json_main_creates_defines_target_edge(tmp_path):
    """package.json "main" field should create a defines_target edge.

    The "main" field specifies the entry point for Node.js/Electron apps.
    It should produce a defines_target edge pointing to the target file,
    enabling the build-target linker to connect it to the module symbol.
    """
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text("""{
  "name": "my-electron-app",
  "main": "./src/main.js"
}
""")
    result = analyze_json_files(tmp_path)

    target_edges = [e for e in result.edges if e.edge_type == "defines_target"]
    assert len(target_edges) >= 1
    main_edge = next((e for e in target_edges if e.dst == "src/main.js"), None)
    assert main_edge is not None

    # Should also create a main_entry symbol
    main_syms = [s for s in result.symbols if s.kind == "main_entry"]
    assert len(main_syms) >= 1
    assert main_syms[0].name == "my-electron-app"
    assert main_syms[0].meta["path"] == "./src/main.js"


def test_package_json_main_no_name_uses_default(tmp_path):
    """package.json "main" without "name" uses fallback name."""
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text("""{
  "main": "index.js"
}
""")
    result = analyze_json_files(tmp_path)

    target_edges = [e for e in result.edges if e.edge_type == "defines_target"]
    assert len(target_edges) >= 1
    assert target_edges[0].dst == "index.js"


def test_package_json_main_missing_no_edge(tmp_path):
    """package.json without "main" should not create main_entry edges."""
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text("""{
  "name": "my-lib",
  "version": "1.0.0"
}
""")
    result = analyze_json_files(tmp_path)

    main_syms = [s for s in result.symbols if s.kind == "main_entry"]
    assert len(main_syms) == 0


def test_package_json_exports_object_creates_defines_target_edges(tmp_path):
    """package.json "exports" object form should create defines_target edges.

    The "exports" field maps subpath patterns to source files:
    - "." → main entry point
    - "./sync" → sync subpath
    - "./awareness" → awareness subpath
    """
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text("""{
  "name": "y-websocket",
  "exports": {
    ".": "./src/y-websocket.js",
    "./sync": "./src/sync.js"
  }
}
""")
    result = analyze_json_files(tmp_path)

    export_syms = [s for s in result.symbols if s.kind == "export_entry"]
    assert len(export_syms) >= 2

    main_export = next((s for s in export_syms if s.name == "."), None)
    assert main_export is not None
    assert main_export.meta["path"] == "./src/y-websocket.js"
    assert main_export.meta["subpath"] == "."

    sync_export = next((s for s in export_syms if s.name == "./sync"), None)
    assert sync_export is not None
    assert sync_export.meta["path"] == "./src/sync.js"

    target_edges = [e for e in result.edges if e.edge_type == "defines_target"]
    # At least 2 from exports
    export_targets = [e for e in target_edges if "src/y-websocket.js" in e.dst or "src/sync.js" in e.dst]
    assert len(export_targets) >= 2


def test_package_json_exports_string_creates_defines_target_edge(tmp_path):
    """package.json "exports" as a plain string should create one edge."""
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text("""{
  "name": "my-lib",
  "exports": "./src/index.js"
}
""")
    result = analyze_json_files(tmp_path)

    export_syms = [s for s in result.symbols if s.kind == "export_entry"]
    assert len(export_syms) >= 1
    assert export_syms[0].meta["path"] == "./src/index.js"

    target_edges = [e for e in result.edges if e.edge_type == "defines_target"]
    assert any(e.dst == "src/index.js" for e in target_edges)


def test_package_json_exports_conditional_resolves_best_path(tmp_path):
    """Conditional exports should pick the source/import/default path.

    When a subpath maps to an object with condition keys (import, require,
    types, default), we pick the first available source-like path.
    """
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text("""{
  "name": "my-lib",
  "exports": {
    ".": {
      "types": "./dist/types/index.d.ts",
      "import": "./dist/esm/index.js",
      "require": "./dist/cjs/index.js"
    }
  }
}
""")
    result = analyze_json_files(tmp_path)

    export_syms = [s for s in result.symbols if s.kind == "export_entry"]
    assert len(export_syms) >= 1
    # Should pick the "import" or first source-like path
    assert export_syms[0].meta["path"] in (
        "./dist/esm/index.js", "./dist/cjs/index.js",
    )

    target_edges = [e for e in result.edges if e.edge_type == "defines_target"]
    assert len(target_edges) >= 1


def test_package_json_exports_conditional_unknown_conditions(tmp_path):
    """Conditional exports with non-standard conditions use first non-types path."""
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text("""{
  "name": "my-lib",
  "exports": {
    ".": {
      "types": "./dist/index.d.ts",
      "browser": "./dist/browser.js",
      "node": "./dist/node.js"
    }
  }
}
""")
    result = analyze_json_files(tmp_path)

    export_syms = [s for s in result.symbols if s.kind == "export_entry"]
    assert len(export_syms) >= 1
    # Should pick first non-types path (browser)
    assert export_syms[0].meta["path"] == "./dist/browser.js"


def test_package_json_exports_missing_no_export_syms(tmp_path):
    """package.json without "exports" should not create export_entry symbols."""
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text("""{
  "name": "my-app",
  "version": "1.0.0"
}
""")
    result = analyze_json_files(tmp_path)

    export_syms = [s for s in result.symbols if s.kind == "export_entry"]
    assert len(export_syms) == 0


def test_is_json_tree_sitter_available():
    """Check that JSON tree-sitter availability returns True."""
    from hypergumbo_lang_mainstream.json_config import is_json_tree_sitter_available

    assert is_json_tree_sitter_available() is True
