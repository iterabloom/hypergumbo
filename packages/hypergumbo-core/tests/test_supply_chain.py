# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for supply chain classification.

Tests the classification of files into supply chain tiers:
- Tier 1 (first_party): Project's own source code
- Tier 2 (internal_dep): Monorepo packages, local forks
- Tier 3 (external_dep): Third-party dependencies
- Tier 4 (derived): Build artifacts, minified/bundled output
"""

from pathlib import Path

import pytest

from hypergumbo_core.supply_chain import (
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
        assert fc.is_test is False  # WI-rigun: default

    def test_classification_with_package(self):
        fc = FileClassification(
            tier=Tier.EXTERNAL_DEP,
            reason="in node_modules/",
            package_name="lodash",
        )
        assert fc.package_name == "lodash"

    def test_classification_with_is_test(self):
        """WI-rigun: is_test flag is independent of tier."""
        fc = FileClassification(
            tier=Tier.INTERNAL_DEP,
            reason="test file matches _test.go$",
            is_test=True,
        )
        assert fc.is_test is True
        assert fc.tier == Tier.INTERNAL_DEP


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
        # Rust prost-build generated serde implementations
        "crates/proto/src/gen/penumbra.core.keys.v1.serde.rs",
        "src/gen/mypackage.serde.rs",
        # Go protobuf generated code
        "pkg/api/v1/service.pb.go",
        "internal/proto/message.pb.go",
        # Python protobuf generated code
        "src/proto/service_pb2.py",
        "proto/message_pb2_grpc.py",
        # Swift Package Manager build artifacts
        ".build/release/MyApp",
        ".build/debug/MyApp.build/module.o",
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
        ("third-party/gtest-1.8.1/src/gtest.cc", None),
        ("thirdparty/imgui/imgui.cpp", None),
        ("external/protobuf/src/google/protobuf/message.cc", None),
        ("deps/sqlite3/sqlite3.c", None),
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


class TestVendoredSdkDetection:
    """Test tier 3 detection for vendored SDKs nested deep in path (INV-mogud).

    Go monorepos often embed cloud provider SDKs in subdirectories rather
    than the repo root. These are not in a standard ``vendor/`` directory
    so the root-anchored EXTERNAL_DEP_PATTERNS miss them. The deep
    patterns use ``re.search`` to match SDK directory names anywhere.
    """

    @pytest.mark.parametrize("path", [
        # Go cloud SDKs: *-sdk-go pattern
        "cloudprovider/ionoscloud/ionos-cloud-sdk-go/api_.go",
        "cloudprovider/tencentcloud/tencentcloud-sdk-go/common/http/request.go",
        "cloudprovider/huaweicloud/huaweicloud-sdk-go-v3/core/auth/basic.go",
        "cloudprovider/alibaba/alibaba-cloud-sdk-go/services/ecs/client.go",
        "cloudprovider/baidu/baiducloud-sdk-go/bce/core.go",
        "cloudprovider/civo/civo-cloud-sdk-go/client.go",
        # Go cloud SDKs: *-go-sdk pattern
        "cloudprovider/volcengine/volcengine-go-sdk/service/ecs/api.go",
        # Go cloud SDKs: *-sdk-golang pattern
        "cloudprovider/volcengine/volc-sdk-golang/base/client.go",
        # vendor-internal pattern
        "cloudprovider/oci/vendor-internal/github.com/oracle/oci-go-sdk/core.go",
    ])
    def test_vendored_sdk_classified_as_external(self, path, tmp_path):
        """Vendored SDK files nested in subdirectories are tier 3."""
        file_path = tmp_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("package main")

        result = classify_file(file_path, tmp_path)
        assert result.tier == Tier.EXTERNAL_DEP, (
            f"{path} should be EXTERNAL_DEP, got {result.tier.name}: {result.reason}"
        )

    def test_regular_sdk_file_not_misclassified(self, tmp_path):
        """Files with 'sdk' in name but not in SDK directory stay tier 1."""
        path = "pkg/provider/aws_sdk_provider.go"
        file_path = tmp_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("package provider")

        result = classify_file(file_path, tmp_path)
        assert result.tier != Tier.EXTERNAL_DEP, (
            f"{path} should NOT be EXTERNAL_DEP (it's a regular file)"
        )

    def test_root_level_sdk_go_directory(self, tmp_path):
        """SDK directory at repo root is also detected."""
        path = "my-sdk-go/client.go"
        file_path = tmp_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("package sdk")

        result = classify_file(file_path, tmp_path)
        assert result.tier == Tier.EXTERNAL_DEP, (
            f"{path} should be EXTERNAL_DEP"
        )


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

        # File in workspace should be tier 1 (workspace IS the project)
        result = classify_file(pkg_dir / "index.js", tmp_path, roots)
        assert result.tier == Tier.FIRST_PARTY

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

    def test_cargo_workspaces_dot_member(self, tmp_path):
        """Skip '.' Cargo workspace member (would cause glob error).

        This mirrors test_npm_workspaces_dot_pattern. The bellman repo uses
        members = ["."] to indicate the root package is a workspace member.
        Without the guard, pathlib.glob(".") raises or returns unexpected results.
        """
        cargo_toml = tmp_path / "Cargo.toml"
        cargo_toml.write_text('''
[workspace]
members = [".", "crates/*"]
''')

        # Create a crates subdirectory
        crate_dir = tmp_path / "crates" / "mylib"
        crate_dir.mkdir(parents=True)

        # Should not crash, should find crates/mylib, should NOT add repo root
        roots = detect_package_roots(tmp_path)
        assert crate_dir in roots
        assert tmp_path not in roots  # "." member should be skipped

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
        # Workspace files are first-party (reason no longer requires "source")

    def test_maven_workspaces(self, tmp_path):
        """Detect internal modules from Maven parent pom.xml."""
        pom_xml = tmp_path / "pom.xml"
        pom_xml.write_text('''\
<project>
  <packaging>pom</packaging>
  <modules>
    <module>guacamole-common</module>
    <module>guacamole-ext</module>
    <module>guacamole</module>
  </modules>
</project>
''')

        # Create module directories with src/main/java structure
        for mod in ["guacamole-common", "guacamole-ext", "guacamole"]:
            mod_dir = tmp_path / mod / "src" / "main" / "java" / "org"
            mod_dir.mkdir(parents=True)
            (mod_dir / "App.java").write_text("class App {}")

        roots = detect_package_roots(tmp_path)
        assert tmp_path / "guacamole-common" in roots
        assert tmp_path / "guacamole-ext" in roots
        assert tmp_path / "guacamole" in roots

        # Files in module src/ are tier 1 (source code)
        result = classify_file(
            tmp_path / "guacamole-common" / "src" / "main" / "java" / "org" / "App.java",
            tmp_path, roots,
        )
        assert result.tier == Tier.FIRST_PARTY
        # Workspace files are first-party (reason no longer requires "source")

    def test_maven_workspaces_nonexistent_module(self, tmp_path):
        """Maven modules that don't exist on disk are ignored."""
        pom_xml = tmp_path / "pom.xml"
        pom_xml.write_text('''\
<project>
  <modules>
    <module>exists</module>
    <module>does-not-exist</module>
  </modules>
</project>
''')

        (tmp_path / "exists").mkdir()
        roots = detect_package_roots(tmp_path)
        assert tmp_path / "exists" in roots
        assert tmp_path / "does-not-exist" not in roots

    def test_maven_pom_without_modules(self, tmp_path):
        """pom.xml without modules section doesn't add package roots."""
        pom_xml = tmp_path / "pom.xml"
        pom_xml.write_text('''\
<project>
  <groupId>com.example</groupId>
  <artifactId>single-module</artifactId>
</project>
''')
        roots = detect_package_roots(tmp_path)
        assert roots == set()

    def test_maven_workspaces_with_namespace(self, tmp_path):
        """Maven pom.xml with xmlns namespace is parsed correctly."""
        pom_xml = tmp_path / "pom.xml"
        pom_xml.write_text('''\
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modules>
    <module>core</module>
    <module>api</module>
  </modules>
</project>
''')
        for mod in ["core", "api"]:
            (tmp_path / mod).mkdir()
        roots = detect_package_roots(tmp_path)
        assert tmp_path / "core" in roots
        assert tmp_path / "api" in roots

    def test_malformed_pom_xml(self, tmp_path):
        """Malformed pom.xml doesn't crash."""
        pom_xml = tmp_path / "pom.xml"
        pom_xml.write_text("<project><modules><module>")
        roots = detect_package_roots(tmp_path)
        assert roots == set()

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
        # Workspace files are first-party (reason no longer requires "source")

    def test_workspace_non_source_is_first_party(self, tmp_path):
        """Non-test files in workspace are first-party (workspace IS the project)."""
        pkg_json = tmp_path / "package.json"
        pkg_json.write_text('{"workspaces": ["packages/*"]}')

        pkg_dir = tmp_path / "packages" / "core"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "package.json").write_text("{}")
        (pkg_dir / "index.js").write_text("// root index")

        roots = detect_package_roots(tmp_path)

        # Root-level files in workspace are first-party
        result = classify_file(pkg_dir / "index.js", tmp_path, roots)
        assert result.tier == Tier.FIRST_PARTY

    def test_workspace_test_dir_is_internal_dep(self, tmp_path):
        """Test directories within workspace packages are tier 2."""
        pkg_json = tmp_path / "package.json"
        pkg_json.write_text('{"workspaces": ["packages/*"]}')

        pkg_dir = tmp_path / "packages" / "core"
        test_dir = pkg_dir / "tests"
        test_dir.mkdir(parents=True)
        (pkg_dir / "package.json").write_text("{}")
        (test_dir / "test_core.py").write_text("# test")

        roots = detect_package_roots(tmp_path)

        result = classify_file(test_dir / "test_core.py", tmp_path, roots)
        assert result.tier == Tier.INTERNAL_DEP

    def test_workspace_test_file_pattern_is_internal_dep(self, tmp_path):
        """Test files matching naming conventions are tier 2 in workspaces."""
        cargo_toml = tmp_path / "Cargo.toml"
        cargo_toml.write_text('[workspace]\nmembers = ["crates/core"]')

        pkg_dir = tmp_path / "crates" / "core"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "Cargo.toml").write_text("[package]\nname = 'core'")
        (pkg_dir / "handler_test.go").write_text("// test")

        roots = detect_package_roots(tmp_path)

        result = classify_file(pkg_dir / "handler_test.go", tmp_path, roots)
        assert result.tier == Tier.INTERNAL_DEP
        assert "test" in result.reason

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


class TestDocCClassification:
    """DocC documentation directories should be tier 2 (not first-party)."""

    def test_docc_tutorial_swift_file_is_tier2(self, tmp_path):
        """Swift tutorial fragments in Documentation.docc are tier 2."""
        docc_dir = tmp_path / "Sources" / "MyLib" / "Documentation.docc" / "Tutorials"
        docc_dir.mkdir(parents=True)
        (docc_dir / "code-0001.swift").write_text("import MyLib\nlet store = Store()")

        result = classify_file(docc_dir / "code-0001.swift", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP, (
            f"DocC tutorial should be tier 2, got {result.tier}: {result.reason}"
        )

    def test_docc_nested_under_sources_not_tier1(self, tmp_path):
        """DocC under Sources/ should NOT be promoted to tier 1."""
        src_dir = tmp_path / "Sources" / "ComposableArchitecture"
        docc_dir = src_dir / "Documentation.docc" / "Tutorials" / "MeetTCA"
        docc_dir.mkdir(parents=True)
        (docc_dir / "code-0003.swift").write_text("struct ContactsFeature {}")

        # Even though it's under Sources/, the .docc path should demote it
        result = classify_file(docc_dir / "code-0003.swift", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP, (
            f"DocC tutorial under Sources/ should be tier 2, got {result.tier}: {result.reason}"
        )

    def test_non_docc_source_stays_tier1(self, tmp_path):
        """Regular Source code next to a .docc dir is still tier 1."""
        src_dir = tmp_path / "Sources" / "MyLib"
        src_dir.mkdir(parents=True)
        (src_dir / "Store.swift").write_text("public class Store {}")
        # Create .docc sibling
        docc_dir = src_dir / "Documentation.docc"
        docc_dir.mkdir()

        result = classify_file(src_dir / "Store.swift", tmp_path, set())
        # Should remain tier 1 (first-party production code)
        assert result.tier == Tier.FIRST_PARTY


class TestNotebookClassification:
    """Jupyter notebooks are always tier 2 (internal_dep).

    Notebooks are exploratory code that lives outside the project's import
    namespace. They should be analyzed but deprioritized vs. first-party source.
    """

    def test_notebook_in_root_is_internal_dep(self, tmp_path):
        """Notebook at repo root is tier 2."""
        nb = tmp_path / "analysis.ipynb"
        nb.write_text("{}")
        result = classify_file(nb, tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert "notebook" in result.reason.lower()

    def test_notebook_in_src_is_internal_dep(self, tmp_path):
        """Notebook in src/ is still tier 2 (not tier 1)."""
        src = tmp_path / "src"
        src.mkdir()
        nb = src / "explore.ipynb"
        nb.write_text("{}")
        result = classify_file(nb, tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP

    def test_notebook_in_notebooks_dir_is_internal_dep(self, tmp_path):
        """Notebook in notebooks/ subdirectory is tier 2."""
        nb_dir = tmp_path / "notebooks"
        nb_dir.mkdir()
        nb = nb_dir / "experiment.ipynb"
        nb.write_text("{}")
        result = classify_file(nb, tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP


class TestTestFileClassification:
    """Test that test directories and files are classified as tier 2 (internal_dep).

    Test code is not production code — it should not be tier 1 (first_party).
    Common conventions across languages:
    - Directory-based: tests/, test/, __tests__/, spec/, src/test/
    - File suffix-based: _test.go, .test.js, .spec.ts, _spec.rb

    Per WI-rigun, every test file detected here must ALSO have is_test=True
    on the FileClassification result, so consumers can compose the tier and
    test-ness axes independently.
    """

    def test_tests_dir_is_internal_dep(self, tmp_path):
        """Top-level tests/ directory is tier 2."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_main.py").write_text("def test_main(): pass")

        result = classify_file(tests_dir / "test_main.py", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert "test" in result.reason.lower()
        assert result.is_test is True

    def test_test_dir_singular_is_internal_dep(self, tmp_path):
        """Top-level test/ directory is tier 2."""
        test_dir = tmp_path / "test"
        test_dir.mkdir()
        (test_dir / "test_app.rb").write_text("require 'test'")

        result = classify_file(test_dir / "test_app.rb", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert result.is_test is True

    def test_jest_tests_dir_is_internal_dep(self, tmp_path):
        """Jest __tests__/ directory is tier 2."""
        tests_dir = tmp_path / "src" / "__tests__"
        tests_dir.mkdir(parents=True)
        (tests_dir / "app.test.js").write_text("test('ok', () => {})")

        result = classify_file(tests_dir / "app.test.js", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert result.is_test is True

    def test_spec_dir_is_internal_dep(self, tmp_path):
        """Ruby spec/ directory is tier 2."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "user_spec.rb").write_text("describe User do; end")

        result = classify_file(spec_dir / "user_spec.rb", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert result.is_test is True

    def test_java_src_test_is_internal_dep(self, tmp_path):
        """Maven/Gradle src/test/ directory is tier 2."""
        test_dir = tmp_path / "src" / "test" / "java" / "com" / "example"
        test_dir.mkdir(parents=True)
        (test_dir / "AppTest.java").write_text("class AppTest {}")

        result = classify_file(test_dir / "AppTest.java", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert result.is_test is True

    def test_go_test_file_suffix_is_internal_dep(self, tmp_path):
        """Go _test.go files are tier 2 even in src/."""
        src_dir = tmp_path / "pkg" / "handler"
        src_dir.mkdir(parents=True)
        (src_dir / "handler_test.go").write_text("package handler")

        result = classify_file(src_dir / "handler_test.go", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert result.is_test is True

    def test_js_test_file_suffix_is_internal_dep(self, tmp_path):
        """JavaScript .test.js files are tier 2."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "app.test.js").write_text("test('ok', () => {})")

        result = classify_file(src_dir / "app.test.js", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert result.is_test is True

    def test_ts_spec_file_suffix_is_internal_dep(self, tmp_path):
        """TypeScript .spec.ts files are tier 2."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "service.spec.ts").write_text("describe('Service', () => {})")

        result = classify_file(src_dir / "service.spec.ts", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert result.is_test is True

    def test_ruby_spec_file_suffix_is_internal_dep(self, tmp_path):
        """Ruby _spec.rb files are tier 2."""
        src_dir = tmp_path / "lib"
        src_dir.mkdir()
        (src_dir / "user_spec.rb").write_text("RSpec.describe User do; end")

        result = classify_file(src_dir / "user_spec.rb", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert result.is_test is True

    def test_rust_colocated_tests_rs_is_internal_dep(self, tmp_path):
        """Rust co-located tests.rs modules are tier 2."""
        src_dir = tmp_path / "core" / "lib" / "dal" / "src" / "consensus"
        src_dir.mkdir(parents=True)
        (src_dir / "tests.rs").write_text("#[cfg(test)]\nmod tests;")

        result = classify_file(src_dir / "tests.rs", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert result.is_test is True

    def test_rust_testonly_rs_is_internal_dep(self, tmp_path):
        """Rust testonly.rs files are tier 2."""
        src_dir = tmp_path / "core" / "lib" / "vm_executor" / "src"
        src_dir.mkdir(parents=True)
        (src_dir / "testonly.rs").write_text("pub fn test_helper() {}")

        result = classify_file(src_dir / "testonly.rs", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert result.is_test is True

    def test_unit_tests_dir_is_internal_dep(self, tmp_path):
        """C++ unit_tests/ directory is tier 2."""
        test_dir = tmp_path / "unit_tests" / "engine"
        test_dir.mkdir(parents=True)
        (test_dir / "test_utils.cpp").write_text("TEST(Utils, Parse) {}")

        result = classify_file(test_dir / "test_utils.cpp", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert result.is_test is True

    def test_nested_unit_tests_dir_is_internal_dep(self, tmp_path):
        """Nested unit_tests/ directory (e.g., src/unit_tests/) is tier 2."""
        test_dir = tmp_path / "src" / "unit_tests"
        test_dir.mkdir(parents=True)
        (test_dir / "test_main.cpp").write_text("TEST(Main, Run) {}")

        result = classify_file(test_dir / "test_main.cpp", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert result.is_test is True

    def test_cpp_test_prefix_is_internal_dep(self, tmp_path):
        """C++ test_*.cpp files are tier 2 (GTest convention)."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "test_parser.cpp").write_text("TEST(Parser, Basic) {}")

        result = classify_file(src_dir / "test_parser.cpp", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert result.is_test is True

    def test_cpp_test_prefix_cc_is_internal_dep(self, tmp_path):
        """C++ test_*.cc files are tier 2."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "test_server.cc").write_text("TEST(Server, Start) {}")

        result = classify_file(src_dir / "test_server.cc", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert result.is_test is True

    def test_cpp_production_code_stays_tier1(self, tmp_path):
        """C++ production code with 'test' in the name stays tier 1."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "attestation.cpp").write_text("void attest() {}")

        result = classify_file(src_dir / "attestation.cpp", tmp_path, set())
        assert result.tier == Tier.FIRST_PARTY
        assert result.is_test is False

    def test_production_code_in_src_stays_tier1(self, tmp_path):
        """Production files next to test files stay tier 1."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "app.ts").write_text("export class App {}")

        result = classify_file(src_dir / "app.ts", tmp_path, set())
        assert result.tier == Tier.FIRST_PARTY
        assert result.is_test is False

    def test_test_dir_lower_priority_than_derived(self, tmp_path):
        """Derived artifacts take priority over test classification.

        WI-rigun: when a path matches BOTH derived and test patterns, the
        derived branch fires first and is_test stays False — the file is
        not exposed as a test file because it isn't read as source.
        """
        # dist/tests/ should still be tier 4 (derived), not tier 2 (test)
        dist_tests = tmp_path / "dist" / "tests"
        dist_tests.mkdir(parents=True)
        (dist_tests / "test_main.js").write_text("// compiled test")

        result = classify_file(dist_tests / "test_main.js", tmp_path, set())
        assert result.tier == Tier.DERIVED
        assert result.is_test is False


class TestFuzzBenchClassification:
    """Test that fuzz and benchmark directories are classified as tier 2.

    Fuzz targets and benchmarks are non-production code — they exercise the
    library but are not part of its API surface.  Common across Rust, Go, C/C++.

    - Rust: fuzz/, fuzz_targets/, benches/, criterion/
    - Go: fuzz (in _test.go), benchmarks/ (less common)
    - C/C++: fuzz/, fuzzing/, benchmarks/, benchmark/
    """

    def test_fuzz_dir_is_internal_dep(self, tmp_path):
        """Top-level fuzz/ directory is tier 2."""
        fuzz_dir = tmp_path / "fuzz" / "fuzz_targets"
        fuzz_dir.mkdir(parents=True)
        (fuzz_dir / "fuzz_diff.rs").write_text("fuzz_target!(|data| {})")

        result = classify_file(fuzz_dir / "fuzz_diff.rs", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert "fuzz" in result.reason.lower()

    def test_fuzz_dir_with_cargo_toml(self, tmp_path):
        """Fuzz Cargo.toml in fuzz/ is tier 2."""
        fuzz_dir = tmp_path / "fuzz"
        fuzz_dir.mkdir()
        (fuzz_dir / "Cargo.toml").write_text('[package]\nname = "fuzz"')

        result = classify_file(fuzz_dir / "Cargo.toml", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP

    def test_nested_fuzz_dir_is_internal_dep(self, tmp_path):
        """Nested fuzz/ in a crate workspace is tier 2."""
        fuzz_dir = tmp_path / "crates" / "core" / "fuzz"
        fuzz_dir.mkdir(parents=True)
        (fuzz_dir / "fuzz_parse.rs").write_text("fuzz_target!(|data| {})")

        result = classify_file(fuzz_dir / "fuzz_parse.rs", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP

    def test_benches_dir_is_internal_dep(self, tmp_path):
        """Rust benches/ directory is tier 2."""
        bench_dir = tmp_path / "benches"
        bench_dir.mkdir()
        (bench_dir / "pipeline.rs").write_text("fn bench_pipeline() {}")

        result = classify_file(bench_dir / "pipeline.rs", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert "bench" in result.reason.lower()

    def test_benchmarks_dir_is_internal_dep(self, tmp_path):
        """benchmarks/ directory is tier 2."""
        bench_dir = tmp_path / "benchmarks"
        bench_dir.mkdir()
        (bench_dir / "bench_main.cpp").write_text("BENCHMARK(main)")

        result = classify_file(bench_dir / "bench_main.cpp", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP

    def test_benchmark_singular_dir_is_internal_dep(self, tmp_path):
        """benchmark/ (singular) directory is tier 2."""
        bench_dir = tmp_path / "benchmark"
        bench_dir.mkdir()
        (bench_dir / "bench.go").write_text("func BenchmarkParse(b *testing.B)")

        result = classify_file(bench_dir / "bench.go", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP

    def test_fuzzing_dir_is_internal_dep(self, tmp_path):
        """fuzzing/ directory is tier 2 (OSS-Fuzz convention)."""
        fuzz_dir = tmp_path / "fuzzing"
        fuzz_dir.mkdir()
        (fuzz_dir / "fuzz_harness.c").write_text("int LLVMFuzzerTestOneInput()")

        result = classify_file(fuzz_dir / "fuzz_harness.c", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP

    def test_production_code_unaffected(self, tmp_path):
        """Production code in src/ is not affected by fuzz/bench patterns."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "lib.rs").write_text("pub fn compute() {}")

        result = classify_file(src_dir / "lib.rs", tmp_path, set())
        assert result.tier == Tier.FIRST_PARTY

    def test_fuzz_lower_priority_than_derived(self, tmp_path):
        """Derived artifacts take priority over fuzz classification."""
        dist_fuzz = tmp_path / "dist" / "fuzz"
        dist_fuzz.mkdir(parents=True)
        (dist_fuzz / "fuzz.js").write_text("// compiled fuzz")

        result = classify_file(dist_fuzz / "fuzz.js", tmp_path, set())
        assert result.tier == Tier.DERIVED


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
        from hypergumbo_core.supply_chain import _extract_package_name

        # Edge case: only @scope in path
        result = _extract_package_name("node_modules/@types", "node_modules/")
        assert result == "@types"

    def test_node_modules_empty_path(self, tmp_path):
        """Handle edge case where path ends at node_modules/."""
        # Test the _extract_package_name function edge case
        from hypergumbo_core.supply_chain import _extract_package_name

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
        from hypergumbo_core.supply_chain import SupplyChainConfig

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
        from hypergumbo_core.supply_chain import SupplyChainConfig

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
        from hypergumbo_core.supply_chain import SupplyChainConfig

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
        from hypergumbo_core.supply_chain import SupplyChainConfig

        config = SupplyChainConfig()
        assert config.first_party_patterns == []
        assert config.derived_patterns == []
        assert config.internal_package_roots == []
        assert config.analysis_tiers == [1, 2, 3]

    def test_config_to_dict(self) -> None:
        """SupplyChainConfig serializes correctly."""
        from hypergumbo_core.supply_chain import SupplyChainConfig

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
        from hypergumbo_core.supply_chain import SupplyChainConfig

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
        from hypergumbo_core.limits import Limits

        limits = Limits()
        result = limits.to_dict()
        assert "supply_chain" in result

    def test_add_classification_failure(self) -> None:
        """Can add classification failure."""
        from hypergumbo_core.limits import Limits

        limits = Limits()
        limits.add_classification_failure("weird/path.py", "unable to classify")

        result = limits.to_dict()
        assert len(result["supply_chain"]["classification_failures"]) == 1
        assert result["supply_chain"]["classification_failures"][0]["path"] == "weird/path.py"

    def test_add_ambiguous_path(self) -> None:
        """Can add ambiguous path."""
        from hypergumbo_core.limits import Limits

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
        from hypergumbo_core.limits import Limits

        limits = Limits()
        result = limits.to_dict()
        assert result["supply_chain"]["classification_failures"] == []
        assert result["supply_chain"]["ambiguous_paths"] == []

    def test_merge_preserves_supply_chain(self) -> None:
        """Merging limits preserves supply_chain data."""
        from hypergumbo_core.limits import Limits

        limits1 = Limits()
        limits1.add_classification_failure("file1.py", "reason1")

        limits2 = Limits()
        limits2.add_ambiguous_path("file2.py", 2, "note")

        merged = limits1.merge(limits2)
        result = merged.to_dict()
        assert len(result["supply_chain"]["classification_failures"]) == 1
        assert len(result["supply_chain"]["ambiguous_paths"]) == 1


class TestClassifySymbolsPreservesTier:
    """_classify_symbols skips symbols that already have a linker-set tier."""

    def test_preset_tier_preserved(self, tmp_path: Path) -> None:
        """Symbols with tier != 1 or supply_chain_reason are not reclassified."""
        from hypergumbo_core.cli import _classify_symbols
        from hypergumbo_core.ir import Symbol, Span

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("x = 1\n")

        # Symbol with pre-set tier 3 (like npm_package from linker)
        preset = Symbol(
            id="npm:lodash",
            name="lodash",
            kind="npm_package",
            language="javascript",
            path="",
            span=Span(0, 0, 0, 0),
            supply_chain_tier=3,
            supply_chain_reason="npm_package (third-party dependency)",
        )
        # Normal symbol (tier 1, no reason) should be classified
        normal = Symbol(
            id="py:app:x",
            name="x",
            kind="variable",
            language="python",
            path="src/app.py",
            span=Span(1, 1, 0, 5),
        )

        package_roots = detect_package_roots(tmp_path)
        _classify_symbols([preset, normal], tmp_path, package_roots)

        # Pre-set tier preserved
        assert preset.supply_chain_tier == 3
        assert preset.supply_chain_reason == "npm_package (third-party dependency)"
        # Normal symbol got classified
        assert normal.supply_chain_reason is not None
        assert normal.supply_chain_reason != ""

    def test_preset_reason_only_preserved(self, tmp_path: Path) -> None:
        """Symbol with tier=1 but non-empty reason is not reclassified."""
        from hypergumbo_core.cli import _classify_symbols
        from hypergumbo_core.ir import Symbol, Span

        sym = Symbol(
            id="test:sym",
            name="sym",
            kind="function",
            language="python",
            path="",
            span=Span(0, 0, 0, 0),
            supply_chain_tier=1,
            supply_chain_reason="manually classified",
        )

        package_roots = detect_package_roots(tmp_path)
        _classify_symbols([sym], tmp_path, package_roots)

        assert sym.supply_chain_reason == "manually classified"


class TestClassifySymbolsDependencyTier:
    """_classify_symbols assigns tier 3 to dependency-kind symbols."""

    def test_cargo_dependency_gets_tier3(self, tmp_path: Path) -> None:
        """Cargo.toml dependency declarations should be tier 3 (EXTERNAL_DEP)."""
        from hypergumbo_core.cli import _classify_symbols
        from hypergumbo_core.ir import Symbol, Span

        (tmp_path / "Cargo.toml").write_text('[dependencies]\nff = "0.13"\n')

        sym = Symbol(
            id="toml:dep:ff",
            name="ff",
            kind="dependency",
            language="toml",
            path="Cargo.toml",
            span=Span(2, 0, 2, 12),
        )

        _classify_symbols([sym], tmp_path, set())
        assert sym.supply_chain_tier == 3
        assert "dependency declaration" in sym.supply_chain_reason

    def test_workspace_cargo_dependency_gets_tier3(self, tmp_path: Path) -> None:
        """Dependency in workspace sub-crate Cargo.toml → tier 3."""
        from hypergumbo_core.cli import _classify_symbols
        from hypergumbo_core.ir import Symbol, Span

        subcrate = tmp_path / "groth16"
        subcrate.mkdir()
        (subcrate / "Cargo.toml").write_text('[dependencies]\nff = "0.13"\n')

        sym = Symbol(
            id="toml:dep:ff:groth16",
            name="ff",
            kind="dependency",
            language="toml",
            path="groth16/Cargo.toml",
            span=Span(2, 0, 2, 12),
        )

        package_roots = {subcrate}
        _classify_symbols([sym], tmp_path, package_roots)
        assert sym.supply_chain_tier == 3
        assert "dependency declaration" in sym.supply_chain_reason

    def test_npm_dependency_gets_tier3(self, tmp_path: Path) -> None:
        """package.json dependency declarations should be tier 3."""
        from hypergumbo_core.cli import _classify_symbols
        from hypergumbo_core.ir import Symbol, Span

        (tmp_path / "package.json").write_text('{"dependencies": {"lodash": "^4.0"}}')

        sym = Symbol(
            id="json:dep:lodash",
            name="lodash",
            kind="dependency",
            language="json",
            path="package.json",
            span=Span(1, 0, 1, 20),
        )

        _classify_symbols([sym], tmp_path, set())
        assert sym.supply_chain_tier == 3
        assert "dependency declaration" in sym.supply_chain_reason

    def test_dev_dependency_gets_tier3(self, tmp_path: Path) -> None:
        """devDependency kind symbols should be tier 3."""
        from hypergumbo_core.cli import _classify_symbols
        from hypergumbo_core.ir import Symbol, Span

        (tmp_path / "package.json").write_text('{"devDependencies": {"jest": "^29"}}')

        sym = Symbol(
            id="json:dep:jest",
            name="jest",
            kind="devDependency",
            language="json",
            path="package.json",
            span=Span(1, 0, 1, 20),
        )

        _classify_symbols([sym], tmp_path, set())
        assert sym.supply_chain_tier == 3
        assert "dependency declaration" in sym.supply_chain_reason

    def test_non_dependency_kind_unaffected(self, tmp_path: Path) -> None:
        """Regular symbols (kind=function etc.) are not affected by dependency rule."""
        from hypergumbo_core.cli import _classify_symbols
        from hypergumbo_core.ir import Symbol, Span

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "lib.rs").write_text("fn main() {}\n")

        sym = Symbol(
            id="rs:main",
            name="main",
            kind="function",
            language="rust",
            path="src/lib.rs",
            span=Span(1, 0, 1, 12),
        )

        _classify_symbols([sym], tmp_path, set())
        assert sym.supply_chain_tier == 1  # First-party, not tier 3


class TestIsTestFileAxis:
    """WI-rigun: is_test_file is independent of supply_chain_tier.

    These tests pin the new behavior end-to-end: classify_file sets
    is_test, _classify_symbols propagates it to Symbol.is_test_file,
    and Symbol.to_dict / Symbol.from_dict round-trip the field through
    the supply_chain block. The tier classification stays unchanged
    (test files remain tier 2 per 89154fa20) — this WI only adds the
    independent flag.
    """

    def test_workspace_test_file_is_test_true(self, tmp_path: Path) -> None:
        """Test file inside a workspace package is tier 2 AND is_test=True."""
        pkg = tmp_path / "pkgs" / "core"
        (pkg / "src").mkdir(parents=True)
        (pkg / "src" / "lib.go").write_text("package core")
        (pkg / "src" / "lib_test.go").write_text("package core")

        result = classify_file(
            pkg / "src" / "lib_test.go", tmp_path, {pkg}
        )
        assert result.tier == Tier.INTERNAL_DEP
        assert result.is_test is True

    def test_workspace_production_file_is_test_false(self, tmp_path: Path) -> None:
        """Non-test file inside a workspace is tier 1 AND is_test=False."""
        pkg = tmp_path / "pkgs" / "core"
        (pkg / "src").mkdir(parents=True)
        (pkg / "src" / "lib.go").write_text("package core")

        result = classify_file(pkg / "src" / "lib.go", tmp_path, {pkg})
        assert result.tier == Tier.FIRST_PARTY
        assert result.is_test is False

    def test_workspace_test_dir_is_test_true(self, tmp_path: Path) -> None:
        """Workspace tests/ directory is tier 2 AND is_test=True."""
        pkg = tmp_path / "pkgs" / "core"
        (pkg / "tests").mkdir(parents=True)
        (pkg / "tests" / "test_lib.py").write_text("def test(): pass")

        result = classify_file(
            pkg / "tests" / "test_lib.py", tmp_path, {pkg}
        )
        assert result.tier == Tier.INTERNAL_DEP
        assert result.is_test is True

    def test_external_dep_is_test_false(self, tmp_path: Path) -> None:
        """A test file inside node_modules is tier 3, NOT marked is_test.

        Test files vendored inside an external package are not the
        consumer's tests — the EXTERNAL_DEP branch fires before the
        test branch and is_test stays False.
        """
        node_modules = tmp_path / "node_modules" / "lodash" / "test"
        node_modules.mkdir(parents=True)
        (node_modules / "compact.test.js").write_text("test('ok', () => {})")

        result = classify_file(
            node_modules / "compact.test.js", tmp_path, set()
        )
        assert result.tier == Tier.EXTERNAL_DEP
        assert result.is_test is False

    def test_fuzz_file_is_not_marked_is_test(self, tmp_path: Path) -> None:
        """Fuzz/bench files are tier 2 but is_test=False (separate axis)."""
        fuzz = tmp_path / "fuzz" / "fuzz_targets"
        fuzz.mkdir(parents=True)
        (fuzz / "fuzz_diff.rs").write_text("fuzz_target!(|data| {})")

        result = classify_file(fuzz / "fuzz_diff.rs", tmp_path, set())
        assert result.tier == Tier.INTERNAL_DEP
        assert result.is_test is False

    def test_classify_symbols_propagates_is_test_flag(
        self, tmp_path: Path
    ) -> None:
        """_classify_symbols copies classification.is_test to Symbol.is_test_file."""
        from hypergumbo_core.cli import _classify_symbols
        from hypergumbo_core.ir import Span, Symbol

        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "handler.go").write_text("package pkg")
        (tmp_path / "pkg" / "handler_test.go").write_text("package pkg")

        prod = Symbol(
            id="go:pkg:Handler",
            name="Handler",
            kind="function",
            language="go",
            path="pkg/handler.go",
            span=Span(1, 0, 1, 0),
        )
        test = Symbol(
            id="go:pkg:TestHandler",
            name="TestHandler",
            kind="function",
            language="go",
            path="pkg/handler_test.go",
            span=Span(1, 0, 1, 0),
        )

        _classify_symbols([prod, test], tmp_path, set())

        assert prod.is_test_file is False
        assert test.is_test_file is True
        # Tier semantics unchanged.
        assert prod.supply_chain_tier == 1
        assert test.supply_chain_tier == 2
