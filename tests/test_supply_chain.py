"""Tests for supply chain classification.

Tests the classification of files into supply chain tiers:
- Tier 1 (first_party): Project's own source code
- Tier 2 (internal_dep): Monorepo packages, local forks
- Tier 3 (external_dep): Third-party dependencies
- Tier 4 (derived): Build artifacts, minified/bundled output
"""

from pathlib import Path

import pytest

from hypergumbo.supply_chain import (
    Tier,
    FileClassification,
    classify_file,
    detect_package_roots,
    is_likely_minified,
)


class TestTierEnum:
    """Test Tier enum values and ordering."""

    def test_tier_values(self):
        assert Tier.FIRST_PARTY == 1
        assert Tier.INTERNAL_DEP == 2
        assert Tier.EXTERNAL_DEP == 3
        assert Tier.DERIVED == 4

    def test_tier_ordering(self):
        """Higher priority tiers have lower numeric values."""
        assert Tier.FIRST_PARTY < Tier.INTERNAL_DEP
        assert Tier.INTERNAL_DEP < Tier.EXTERNAL_DEP
        assert Tier.EXTERNAL_DEP < Tier.DERIVED


class TestFileClassification:
    """Test FileClassification dataclass."""

    def test_classification_fields(self):
        fc = FileClassification(
            tier=Tier.FIRST_PARTY,
            reason="matches ^src/",
            package_name=None,
        )
        assert fc.tier == Tier.FIRST_PARTY
        assert fc.reason == "matches ^src/"
        assert fc.package_name is None

    def test_classification_with_package(self):
        fc = FileClassification(
            tier=Tier.EXTERNAL_DEP,
            reason="in node_modules/",
            package_name="lodash",
        )
        assert fc.package_name == "lodash"


class TestDerivedArtifactDetection:
    """Test tier 4 (derived) detection via path patterns."""

    @pytest.mark.parametrize("path", [
        "dist/bundle.js",
        "build/app.js",
        "out/index.js",
        "target/release/main",
        ".next/static/chunks/main.js",
        ".nuxt/dist/client.js",
        ".output/server/index.mjs",
        ".svelte-kit/output/client.js",
        "src/app.min.js",
        "styles/main.min.css",
        "lib/vendor.bundle.js",
        "compiled/output.compiled.js",
        "__pycache__/module.cpython-311.pyc",
        "src/__pycache__/test.pyc",
    ])
    def test_derived_path_patterns(self, path, tmp_path):
        """Files matching derived patterns are tier 4."""
        file_path = tmp_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("// content")

        result = classify_file(file_path, tmp_path)
        assert result.tier == Tier.DERIVED, f"{path} should be DERIVED"


class TestExternalDepDetection:
    """Test tier 3 (external_dep) detection."""

    @pytest.mark.parametrize("path,expected_pkg", [
        ("node_modules/lodash/index.js", "lodash"),
        ("node_modules/@babel/core/lib/index.js", "@babel/core"),
        ("node_modules/@types/node/index.d.ts", "@types/node"),
        ("vendor/autoload.php", None),
        ("third_party/lib/util.py", None),
        ("Pods/AFNetworking/Source/AFHTTPClient.m", None),
        ("Carthage/Build/iOS/Alamofire.framework/Alamofire", None),
        (".yarn/cache/lodash-npm-4.17.21.zip", None),
        ("_vendor/hugo/tpl/template.go", None),
    ])
    def test_external_dep_patterns(self, path, expected_pkg, tmp_path):
        """Files in dependency directories are tier 3."""
        file_path = tmp_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("// content")

        result = classify_file(file_path, tmp_path)
        assert result.tier == Tier.EXTERNAL_DEP, f"{path} should be EXTERNAL_DEP"
        if expected_pkg:
            assert result.package_name == expected_pkg


class TestFirstPartyDetection:
    """Test tier 1 (first_party) detection."""

    @pytest.mark.parametrize("path", [
        "src/main.py",
        "src/utils/helpers.py",
        "lib/core.js",
        "app/models/user.rb",
        "pkg/server/main.go",
        "cmd/cli/main.go",
        "internal/auth/handler.go",
        "crates/mylib/src/lib.rs",
        "packages/core/src/index.ts",
    ])
    def test_first_party_patterns(self, path, tmp_path):
        """Files matching first-party patterns are tier 1."""
        file_path = tmp_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("// content")

        result = classify_file(file_path, tmp_path)
        assert result.tier == Tier.FIRST_PARTY, f"{path} should be FIRST_PARTY"

    def test_default_to_first_party(self, tmp_path):
        """Unknown paths default to first-party."""
        file_path = tmp_path / "unknown_dir" / "code.py"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("# code")

        result = classify_file(file_path, tmp_path)
        assert result.tier == Tier.FIRST_PARTY
        assert "default" in result.reason.lower()


class TestInternalDepDetection:
    """Test tier 2 (internal_dep) detection via workspace configs."""

    def test_npm_workspaces(self, tmp_path):
        """Detect internal packages from package.json workspaces."""
        # Create package.json with workspaces
        pkg_json = tmp_path / "package.json"
        pkg_json.write_text('{"workspaces": ["packages/*"]}')

        # Create a package in the workspace
        pkg_dir = tmp_path / "packages" / "core"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "index.js").write_text("// core")

        roots = detect_package_roots(tmp_path)
        assert pkg_dir in roots

        # File in workspace should be tier 2
        result = classify_file(pkg_dir / "index.js", tmp_path, roots)
        assert result.tier == Tier.INTERNAL_DEP

    def test_npm_workspaces_object_format(self, tmp_path):
        """Handle workspaces as object with packages array."""
        pkg_json = tmp_path / "package.json"
        pkg_json.write_text('{"workspaces": {"packages": ["apps/*", "libs/*"]}}')

        apps_dir = tmp_path / "apps" / "web"
        apps_dir.mkdir(parents=True)

        roots = detect_package_roots(tmp_path)
        assert apps_dir in roots

    def test_npm_workspaces_dot_pattern(self, tmp_path):
        """Skip '.' workspace pattern (would cause glob error)."""
        pkg_json = tmp_path / "package.json"
        pkg_json.write_text('{"workspaces": [".", "packages/*"]}')

        # Create a packages subdirectory
        pkg_dir = tmp_path / "packages" / "core"
        pkg_dir.mkdir(parents=True)

        # Should not crash, should find packages/core, should NOT add repo root
        roots = detect_package_roots(tmp_path)
        assert pkg_dir in roots
        assert tmp_path not in roots  # "." pattern should be skipped

    def test_cargo_workspaces(self, tmp_path):
        """Detect internal crates from Cargo.toml workspace."""
        cargo_toml = tmp_path / "Cargo.toml"
        cargo_toml.write_text('''
[workspace]
members = ["crates/*"]
''')

        crate_dir = tmp_path / "crates" / "mylib"
        crate_dir.mkdir(parents=True)
        (crate_dir / "src" / "lib.rs").parent.mkdir()
        (crate_dir / "src" / "lib.rs").write_text("// lib")

        roots = detect_package_roots(tmp_path)
        assert crate_dir in roots

        # Files in workspace src/ are tier 1 (the workspace IS the library)
        result = classify_file(crate_dir / "src" / "lib.rs", tmp_path, roots)
        assert result.tier == Tier.FIRST_PARTY
        assert "source" in result.reason

    def test_workspace_source_is_first_party(self, tmp_path):
        """Files in workspace src/lib/app are tier 1 (library monorepos)."""
        pkg_json = tmp_path / "package.json"
        pkg_json.write_text('{"workspaces": ["packages/*"]}')

        # Create workspace with lib/ directory
        pkg_dir = tmp_path / "packages" / "socket.io"
        (pkg_dir / "lib").mkdir(parents=True)
        (pkg_dir / "lib" / "index.ts").write_text("export class Server {}")

        roots = detect_package_roots(tmp_path)

        # lib/index.ts in workspace should be tier 1
        result = classify_file(pkg_dir / "lib" / "index.ts", tmp_path, roots)
        assert result.tier == Tier.FIRST_PARTY
        assert "socket.io" in result.reason
        assert "source" in result.reason

    def test_workspace_non_source_is_internal_dep(self, tmp_path):
        """Files in workspace outside src/lib/app are tier 2."""
        pkg_json = tmp_path / "package.json"
        pkg_json.write_text('{"workspaces": ["packages/*"]}')

        pkg_dir = tmp_path / "packages" / "core"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "package.json").write_text("{}")
        (pkg_dir / "index.js").write_text("// root index")

        roots = detect_package_roots(tmp_path)

        # Root-level files in workspace are tier 2
        result = classify_file(pkg_dir / "index.js", tmp_path, roots)
        assert result.tier == Tier.INTERNAL_DEP

    def test_examples_dir_is_internal_dep(self, tmp_path):
        """Examples directory is tier 2 (lower priority than workspace source)."""
        # Create examples directory
        examples_dir = tmp_path / "examples" / "basic"
        examples_dir.mkdir(parents=True)
        (examples_dir / "app.js").write_text("// example")

        result = classify_file(examples_dir / "app.js", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert "examples" in result.reason

    def test_demos_dir_is_internal_dep(self, tmp_path):
        """Demos directory is tier 2."""
        demos_dir = tmp_path / "demos"
        demos_dir.mkdir()
        (demos_dir / "demo.py").write_text("# demo")

        result = classify_file(demos_dir / "demo.py", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP

    def test_samples_dir_is_internal_dep(self, tmp_path):
        """Samples directory is tier 2."""
        samples_dir = tmp_path / "samples"
        samples_dir.mkdir()
        (samples_dir / "sample.rs").write_text("// sample")

        result = classify_file(samples_dir / "sample.rs", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP

    def test_examples_lower_priority_than_workspace_source(self, tmp_path):
        """Examples (tier 2) have lower priority than workspace source (tier 1)."""
        # Set up a library monorepo like socket.io
        pkg_json = tmp_path / "package.json"
        pkg_json.write_text('{"workspaces": ["packages/*"]}')

        # Workspace package with lib/
        pkg_dir = tmp_path / "packages" / "mylib"
        (pkg_dir / "lib").mkdir(parents=True)
        (pkg_dir / "lib" / "index.ts").write_text("export class MyLib {}")

        # Examples outside workspaces
        examples_dir = tmp_path / "examples" / "basic"
        examples_dir.mkdir(parents=True)
        (examples_dir / "app.ts").write_text("import { MyLib } from 'mylib'")

        roots = detect_package_roots(tmp_path)

        # Workspace lib/ should be tier 1
        lib_result = classify_file(pkg_dir / "lib" / "index.ts", tmp_path, roots)
        assert lib_result.tier == Tier.FIRST_PARTY

        # Examples should be tier 2
        example_result = classify_file(examples_dir / "app.ts", tmp_path, roots)
        assert example_result.tier == Tier.INTERNAL_DEP

        # Tier 1 < Tier 2, so workspace source has higher priority
        assert lib_result.tier < example_result.tier


class TestMinificationDetection:
    """Test content-based minification heuristics."""

    def test_long_lines_detected(self, tmp_path):
        """Files with avg line length > 150 are minified."""
        minified = tmp_path / "bundle.js"
        # Create a file with very long lines (simulating minification)
        long_line = "var a=1;" * 100  # 800 chars
        minified.write_text(long_line + "\n" + long_line)

        assert is_likely_minified(minified) is True

    def test_normal_code_not_minified(self, tmp_path):
        """Normal code files are not detected as minified."""
        normal = tmp_path / "app.js"
        normal.write_text("""
function hello() {
    console.log("Hello, world!");
}

function goodbye() {
    console.log("Goodbye!");
}
""")
        assert is_likely_minified(normal) is False

    def test_source_map_reference_detected(self, tmp_path):
        """Files with sourceMappingURL are detected as derived."""
        transpiled = tmp_path / "app.js"
        transpiled.write_text("""
function hello() {
    console.log("Hello");
}
//# sourceMappingURL=app.js.map
""")
        assert is_likely_minified(transpiled) is True

    def test_generated_header_detected(self, tmp_path):
        """Files with 'Generated by' headers are detected as derived."""
        generated = tmp_path / "proto.py"
        generated.write_text("""# Generated by the protocol buffer compiler. DO NOT EDIT!
# source: myproto.proto

class MyMessage:
    pass
""")
        assert is_likely_minified(generated) is True

    def test_at_generated_annotation(self, tmp_path):
        """Files with @generated annotation are detected."""
        generated = tmp_path / "Schema.java"
        generated.write_text("""// @generated by some-tool v1.2.3

public class Schema {
    // ...
}
""")
        assert is_likely_minified(generated) is True

    def test_webpack_bootstrap_detected(self, tmp_path):
        """Files with webpack bootstrap pattern are detected as bundled."""
        bundled = tmp_path / "bundle.js"
        bundled.write_text("""#!/usr/bin/env node
module.exports =
/******/ (function(modules) { // webpackBootstrap
/******/    // The module cache
/******/    var installedModules = {};
/******/
/******/    // The require function
/******/    function __webpack_require__(moduleId) {
/******/        // Check if module is in cache
/******/    }
/******/ })([]);
""")
        assert is_likely_minified(bundled) is True

    def test_webpack_require_detected(self, tmp_path):
        """Files with __webpack_require__ in early lines are detected."""
        bundled = tmp_path / "app.bundle.js"
        bundled.write_text("""
var modules = {};
function __webpack_require__(id) {
    return modules[id];
}
module.exports = __webpack_require__(0);
""")
        assert is_likely_minified(bundled) is True

    def test_unreadable_file_returns_false(self, tmp_path):
        """Unreadable files return False gracefully."""
        from unittest.mock import patch, MagicMock

        file_path = tmp_path / "unreadable.js"
        file_path.write_text("some content")

        # Mock read_text to raise OSError (simulates permission denied, etc.)
        mock_path = MagicMock(spec=Path)
        mock_path.read_text.side_effect = OSError("Permission denied")

        with patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
            # Create a real path but mock its read_text method
            assert is_likely_minified(file_path) is False


class TestClassificationPriority:
    """Test that classification checks are applied in correct order."""

    def test_derived_takes_priority_over_first_party(self, tmp_path):
        """dist/src/app.js is DERIVED despite 'src' in path."""
        file_path = tmp_path / "dist" / "src" / "app.js"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("// built")

        result = classify_file(file_path, tmp_path)
        assert result.tier == Tier.DERIVED

    def test_minified_in_src_is_derived(self, tmp_path):
        """Minified file in src/ is DERIVED due to content heuristics."""
        file_path = tmp_path / "src" / "vendor.min.js"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("// minified")

        result = classify_file(file_path, tmp_path)
        assert result.tier == Tier.DERIVED

    def test_external_dep_in_readable_form(self, tmp_path):
        """node_modules with normal code is EXTERNAL_DEP not DERIVED."""
        file_path = tmp_path / "node_modules" / "lodash" / "lodash.js"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("""
/**
 * Lodash library
 */
function chunk(array, size) {
    // implementation
}
""")

        result = classify_file(file_path, tmp_path)
        assert result.tier == Tier.EXTERNAL_DEP


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_file(self, tmp_path):
        """Empty files don't crash minification detection."""
        empty = tmp_path / "empty.js"
        empty.write_text("")

        assert is_likely_minified(empty) is False

    def test_nonexistent_file(self, tmp_path):
        """Nonexistent files return sensible default."""
        missing = tmp_path / "missing.js"

        # Should not crash, return first_party as default
        result = classify_file(missing, tmp_path)
        assert result.tier == Tier.FIRST_PARTY

    def test_binary_file(self, tmp_path):
        """Binary files don't crash."""
        binary = tmp_path / "image.png"
        binary.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 100)

        # Should handle gracefully
        result = classify_file(binary, tmp_path)
        # Binary files in unknown location default to first_party
        assert result.tier == Tier.FIRST_PARTY

    def test_path_outside_repo(self, tmp_path):
        """File outside repo_root defaults to first-party."""
        other_dir = tmp_path / "other_project"
        other_dir.mkdir()
        file_path = other_dir / "code.py"
        file_path.write_text("# code")

        repo_root = tmp_path / "my_project"
        repo_root.mkdir()

        result = classify_file(file_path, repo_root)
        assert result.tier == Tier.FIRST_PARTY
        assert "outside repo" in result.reason

    def test_file_detected_as_minified_by_content(self, tmp_path):
        """File in normal location detected as derived via content heuristics."""
        # File in unknown dir (not src/, not node_modules/, etc.)
        # but with minified content
        file_path = tmp_path / "assets" / "bundle.js"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        long_line = "var a=1,b=2,c=3;" * 50  # 800 chars
        file_path.write_text(long_line)

        result = classify_file(file_path, tmp_path)
        assert result.tier == Tier.DERIVED
        assert "minified" in result.reason

    def test_unreadable_file_for_minification(self, tmp_path):
        """Files that can't be read don't crash minification detection."""
        # Create a file then make it unreadable
        file_path = tmp_path / "secret.js"
        file_path.write_text("// content")

        # Mock by using a path that will fail to read
        import os

        # On Unix, remove read permission
        os.chmod(file_path, 0o000)
        try:
            assert is_likely_minified(file_path) is False
        finally:
            # Restore permissions for cleanup
            os.chmod(file_path, 0o644)

    def test_malformed_package_json(self, tmp_path):
        """Malformed package.json doesn't crash."""
        pkg_json = tmp_path / "package.json"
        pkg_json.write_text("{ invalid json }")

        roots = detect_package_roots(tmp_path)
        assert roots == set()

    def test_malformed_cargo_toml(self, tmp_path):
        """Cargo.toml that can't be read doesn't crash."""
        cargo_toml = tmp_path / "Cargo.toml"
        cargo_toml.write_text("[workspace]\nmembers = [")

        # Should not crash even with malformed content
        roots = detect_package_roots(tmp_path)
        assert roots == set()

    def test_scoped_package_incomplete(self, tmp_path):
        """Scoped package with only @scope (no package name) is handled."""
        # Edge case: file directly under @scope (unusual but possible)
        file_path = tmp_path / "node_modules" / "@types" / "index.d.ts"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("// types")

        result = classify_file(file_path, tmp_path)
        assert result.tier == Tier.EXTERNAL_DEP
        # Best effort extraction - takes first two path parts
        # (In real usage, files are like @types/node/index.d.ts)
        assert result.package_name == "@types/index.d.ts"

    def test_scoped_package_only_scope(self, tmp_path):
        """Scoped package with just @scope returns just @scope."""
        from hypergumbo.supply_chain import _extract_package_name

        # Edge case: only @scope in path
        result = _extract_package_name("node_modules/@types", "node_modules/")
        assert result == "@types"

    def test_node_modules_empty_path(self, tmp_path):
        """Handle edge case where path ends at node_modules/."""
        # Test the _extract_package_name function edge case
        from hypergumbo.supply_chain import _extract_package_name

        # Empty after split: "node_modules/" -> parts = [""]
        result = _extract_package_name("node_modules/", "node_modules/")
        assert result is None

    def test_unreadable_cargo_toml(self, tmp_path):
        """Unreadable Cargo.toml doesn't crash."""
        import os

        cargo_toml = tmp_path / "Cargo.toml"
        cargo_toml.write_text("[workspace]\nmembers = [\"crates/*\"]")

        # Make unreadable
        os.chmod(cargo_toml, 0o000)
        try:
            roots = detect_package_roots(tmp_path)
            assert roots == set()
        finally:
            os.chmod(cargo_toml, 0o644)

    def test_package_roots_with_invalid_path(self, tmp_path):
        """Invalid package root in set doesn't crash classification."""
        file_path = tmp_path / "src" / "app.py"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("# app")

        # Create an invalid package root (not a real path relationship)
        invalid_root = Path("/nonexistent/path")
        package_roots = {invalid_root}

        result = classify_file(file_path, tmp_path, package_roots)
        # Should still classify correctly, ignoring invalid root
        assert result.tier == Tier.FIRST_PARTY

    def test_package_roots_is_relative_to_error(self, tmp_path):
        """is_relative_to errors are handled gracefully."""
        file_path = tmp_path / "code" / "app.py"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("# app")

        # Create an object that will cause TypeError when used with is_relative_to
        # is_relative_to expects a path-like object; passing something weird can trigger errors
        class BadPath:
            """A fake path that causes is_relative_to to fail."""
            name = "bad_pkg"

            def __fspath__(self):
                # Return something that will cause issues
                raise TypeError("Cannot convert to path")

        bad_root = BadPath()
        package_roots = {bad_root}  # type: ignore

        result = classify_file(file_path, tmp_path, package_roots)
        # Should still classify correctly, ignoring the bad root
        assert result.tier == Tier.FIRST_PARTY


class TestSupplyChainConfig:
    """Tests for capsule plan supply_chain configuration."""

    def test_custom_first_party_pattern(self, tmp_path: Path) -> None:
        """Custom first_party_patterns override default classification."""
        from hypergumbo.supply_chain import SupplyChainConfig

        # File in custom_code/ would normally be tier 1 by default
        # but we can explicitly configure it
        file_path = tmp_path / "custom_code" / "app.py"
        file_path.parent.mkdir(parents=True)
        file_path.write_text("# code")

        config = SupplyChainConfig(
            first_party_patterns=["custom_code/"],
        )
        result = classify_file(file_path, tmp_path, config=config)
        assert result.tier == Tier.FIRST_PARTY
        assert "custom_code/" in result.reason

    def test_custom_derived_pattern(self, tmp_path: Path) -> None:
        """Custom derived_patterns classify as tier 4."""
        from hypergumbo.supply_chain import SupplyChainConfig

        # File in generated/ would normally be tier 1 by default
        file_path = tmp_path / "generated" / "types.py"
        file_path.parent.mkdir(parents=True)
        file_path.write_text("# generated code")

        config = SupplyChainConfig(
            derived_patterns=["generated/"],
        )
        result = classify_file(file_path, tmp_path, config=config)
        assert result.tier == Tier.DERIVED
        assert "generated/" in result.reason

    def test_custom_internal_package_roots(self, tmp_path: Path) -> None:
        """Custom internal_package_roots override detection."""
        from hypergumbo.supply_chain import SupplyChainConfig

        # File in custom_packages/shared would be internal dep
        file_path = tmp_path / "custom_packages" / "shared" / "utils.py"
        file_path.parent.mkdir(parents=True)
        file_path.write_text("# shared code")

        config = SupplyChainConfig(
            internal_package_roots=["custom_packages/shared"],
        )
        result = classify_file(file_path, tmp_path, config=config)
        assert result.tier == Tier.INTERNAL_DEP
        assert "custom_packages/shared" in result.reason

    def test_config_defaults(self) -> None:
        """SupplyChainConfig has sensible defaults."""
        from hypergumbo.supply_chain import SupplyChainConfig

        config = SupplyChainConfig()
        assert config.first_party_patterns == []
        assert config.derived_patterns == []
        assert config.internal_package_roots == []
        assert config.analysis_tiers == [1, 2, 3]

    def test_config_to_dict(self) -> None:
        """SupplyChainConfig serializes correctly."""
        from hypergumbo.supply_chain import SupplyChainConfig

        config = SupplyChainConfig(
            analysis_tiers=[1, 2],
            first_party_patterns=["src/", "lib/"],
            derived_patterns=["build/"],
            internal_package_roots=["packages/core"],
        )
        d = config.to_dict()
        assert d["analysis_tiers"] == [1, 2]
        assert d["first_party_patterns"] == ["src/", "lib/"]
        assert d["derived_patterns"] == ["build/"]
        assert d["internal_package_roots"] == ["packages/core"]

    def test_config_from_dict(self) -> None:
        """SupplyChainConfig parses from dict."""
        from hypergumbo.supply_chain import SupplyChainConfig

        data = {
            "analysis_tiers": [1],
            "first_party_patterns": ["custom/"],
            "derived_patterns": ["out/"],
            "internal_package_roots": ["libs/common"],
        }
        config = SupplyChainConfig.from_dict(data)
        assert config.analysis_tiers == [1]
        assert config.first_party_patterns == ["custom/"]
        assert config.derived_patterns == ["out/"]
        assert config.internal_package_roots == ["libs/common"]


class TestSupplyChainLimits:
    """Tests for limits.supply_chain logging."""

    def test_limits_has_supply_chain_section(self) -> None:
        """Limits includes supply_chain section."""
        from hypergumbo.limits import Limits

        limits = Limits()
        result = limits.to_dict()
        assert "supply_chain" in result

    def test_add_classification_failure(self) -> None:
        """Can add classification failure."""
        from hypergumbo.limits import Limits

        limits = Limits()
        limits.add_classification_failure("weird/path.py", "unable to classify")

        result = limits.to_dict()
        assert len(result["supply_chain"]["classification_failures"]) == 1
        assert result["supply_chain"]["classification_failures"][0]["path"] == "weird/path.py"

    def test_add_ambiguous_path(self) -> None:
        """Can add ambiguous path."""
        from hypergumbo.limits import Limits

        limits = Limits()
        limits.add_ambiguous_path(
            "lib/vendor/custom.py",
            assigned_tier=1,
            note="could be tier 2 or 3",
        )

        result = limits.to_dict()
        assert len(result["supply_chain"]["ambiguous_paths"]) == 1
        entry = result["supply_chain"]["ambiguous_paths"][0]
        assert entry["path"] == "lib/vendor/custom.py"
        assert entry["assigned"] == 1
        assert entry["note"] == "could be tier 2 or 3"

    def test_empty_supply_chain_section(self) -> None:
        """Empty limits has empty supply_chain section."""
        from hypergumbo.limits import Limits

        limits = Limits()
        result = limits.to_dict()
        assert result["supply_chain"]["classification_failures"] == []
        assert result["supply_chain"]["ambiguous_paths"] == []

    def test_merge_preserves_supply_chain(self) -> None:
        """Merging limits preserves supply_chain data."""
        from hypergumbo.limits import Limits

        limits1 = Limits()
        limits1.add_classification_failure("file1.py", "reason1")

        limits2 = Limits()
        limits2.add_ambiguous_path("file2.py", 2, "note")

        merged = limits1.merge(limits2)
        result = merged.to_dict()
        assert len(result["supply_chain"]["classification_failures"]) == 1
        assert len(result["supply_chain"]["ambiguous_paths"]) == 1
