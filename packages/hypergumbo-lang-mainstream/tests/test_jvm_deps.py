# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for JVM dependency manifest parsing (Gradle and Maven).

WI-duhom: Gradle/Maven projects have near-zero external_dep nodes because
external dependencies are declared in build.gradle/pom.xml but not physically
present on disk. These parsers extract dependency metadata so
create_boundary_nodes can classify unresolved Java/Kotlin imports.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.supply_chain import DependencyManifest, Tier


class TestParseGradleDependencies:
    """Tests for parse_gradle_dependencies."""

    def test_groovy_implementation(self, tmp_path: Path) -> None:
        """Groovy DSL: implementation 'group:artifact:version'."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_gradle_dependencies

        (tmp_path / "build.gradle").write_text(
            "dependencies {\n"
            "    implementation 'com.fasterxml.jackson.core:jackson-databind:2.15.0'\n"
            "}\n"
        )
        manifest = parse_gradle_dependencies(tmp_path)
        assert manifest.classify_import("com.fasterxml.jackson.core") == Tier.INTERNAL_DEP

    def test_groovy_api(self, tmp_path: Path) -> None:
        """Groovy DSL: api 'group:artifact:version' is a direct dep."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_gradle_dependencies

        (tmp_path / "build.gradle").write_text(
            "dependencies {\n"
            "    api 'org.apache.kafka:kafka-clients:3.6.0'\n"
            "}\n"
        )
        manifest = parse_gradle_dependencies(tmp_path)
        assert manifest.classify_import("org.apache.kafka.clients") == Tier.INTERNAL_DEP

    def test_kotlin_dsl_implementation(self, tmp_path: Path) -> None:
        """Kotlin DSL: implementation("group:artifact:version")."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_gradle_dependencies

        (tmp_path / "build.gradle.kts").write_text(
            "dependencies {\n"
            '    implementation("io.ktor:ktor-server-core:2.3.0")\n'
            "}\n"
        )
        manifest = parse_gradle_dependencies(tmp_path)
        assert manifest.classify_import("io.ktor") == Tier.INTERNAL_DEP

    def test_test_dependency_is_direct(self, tmp_path: Path) -> None:
        """testImplementation is still a direct dep (the project declares it)."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_gradle_dependencies

        (tmp_path / "build.gradle").write_text(
            "dependencies {\n"
            "    testImplementation 'junit:junit:4.13.2'\n"
            "}\n"
        )
        manifest = parse_gradle_dependencies(tmp_path)
        assert manifest.classify_import("junit") == Tier.INTERNAL_DEP

    def test_compile_only_is_direct(self, tmp_path: Path) -> None:
        """compileOnly is a direct dep."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_gradle_dependencies

        (tmp_path / "build.gradle").write_text(
            "dependencies {\n"
            "    compileOnly 'javax.servlet:javax.servlet-api:4.0.1'\n"
            "}\n"
        )
        manifest = parse_gradle_dependencies(tmp_path)
        assert manifest.classify_import("javax.servlet") == Tier.INTERNAL_DEP

    def test_multiple_dependencies(self, tmp_path: Path) -> None:
        """Multiple deps in one block."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_gradle_dependencies

        (tmp_path / "build.gradle").write_text(
            "dependencies {\n"
            "    implementation 'com.google.guava:guava:31.1-jre'\n"
            "    implementation 'io.netty:netty-all:4.1.0'\n"
            "    testImplementation 'org.mockito:mockito-core:5.0'\n"
            "}\n"
        )
        manifest = parse_gradle_dependencies(tmp_path)
        assert manifest.classify_import("com.google.guava") == Tier.INTERNAL_DEP
        assert manifest.classify_import("io.netty") == Tier.INTERNAL_DEP
        assert manifest.classify_import("org.mockito") == Tier.INTERNAL_DEP

    def test_no_build_gradle(self, tmp_path: Path) -> None:
        """Missing build.gradle returns empty manifest."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_gradle_dependencies

        manifest = parse_gradle_dependencies(tmp_path)
        assert manifest.entries == {}

    def test_subproject_build_gradle(self, tmp_path: Path) -> None:
        """Discovers deps from subproject build.gradle files."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_gradle_dependencies

        sub = tmp_path / "clients"
        sub.mkdir()
        (sub / "build.gradle").write_text(
            "dependencies {\n"
            "    implementation 'org.slf4j:slf4j-api:2.0.0'\n"
            "}\n"
        )
        manifest = parse_gradle_dependencies(tmp_path)
        assert manifest.classify_import("org.slf4j") == Tier.INTERNAL_DEP

    def test_version_catalog_libs(self, tmp_path: Path) -> None:
        """Version catalog refs (libs.foo.bar) are ignored (no group info)."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_gradle_dependencies

        (tmp_path / "build.gradle.kts").write_text(
            "dependencies {\n"
            "    implementation(libs.jackson.databind)\n"
            "}\n"
        )
        manifest = parse_gradle_dependencies(tmp_path)
        assert manifest.entries == {}

    def test_groovy_parens_variant(self, tmp_path: Path) -> None:
        """Groovy DSL: implementation('group:artifact:version')."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_gradle_dependencies

        (tmp_path / "build.gradle").write_text(
            "dependencies {\n"
            "    implementation('org.apache.commons:commons-lang3:3.12.0')\n"
            "}\n"
        )
        manifest = parse_gradle_dependencies(tmp_path)
        assert manifest.classify_import("org.apache.commons") == Tier.INTERNAL_DEP

    def test_dependency_without_version(self, tmp_path: Path) -> None:
        """Dependencies with platform BOM (no version) still parsed."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_gradle_dependencies

        (tmp_path / "build.gradle.kts").write_text(
            "dependencies {\n"
            '    implementation("org.springframework.boot:spring-boot-starter-web")\n'
            "}\n"
        )
        manifest = parse_gradle_dependencies(tmp_path)
        assert manifest.classify_import("org.springframework.boot") == Tier.INTERNAL_DEP

    def test_unreadable_build_gradle(self, tmp_path: Path) -> None:
        """OSError reading build.gradle returns empty manifest."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_gradle_dependencies

        gradle = tmp_path / "build.gradle"
        gradle.mkdir()  # directory, not file → OSError on read_text
        manifest = parse_gradle_dependencies(tmp_path)
        assert manifest.entries == {}


class TestExtractGroupId:
    """Tests for _extract_group_id edge cases."""

    def test_no_colon_returns_none(self) -> None:
        """Coordinate without colon returns None."""
        from hypergumbo_lang_mainstream.jvm_deps import _extract_group_id

        assert _extract_group_id("no-colon-here") is None


class TestParseMavenDependencies:
    """Tests for parse_maven_dependencies."""

    def test_basic_dependency(self, tmp_path: Path) -> None:
        """Basic Maven dependency."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_maven_dependencies

        (tmp_path / "pom.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<project>\n"
            "  <dependencies>\n"
            "    <dependency>\n"
            "      <groupId>com.fasterxml.jackson.core</groupId>\n"
            "      <artifactId>jackson-databind</artifactId>\n"
            "      <version>2.15.0</version>\n"
            "    </dependency>\n"
            "  </dependencies>\n"
            "</project>\n"
        )
        manifest = parse_maven_dependencies(tmp_path)
        assert manifest.classify_import("com.fasterxml.jackson.core") == Tier.INTERNAL_DEP

    def test_test_scope_is_direct(self, tmp_path: Path) -> None:
        """Test-scoped Maven deps are still direct (project declares them)."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_maven_dependencies

        (tmp_path / "pom.xml").write_text(
            "<project>\n"
            "  <dependencies>\n"
            "    <dependency>\n"
            "      <groupId>junit</groupId>\n"
            "      <artifactId>junit</artifactId>\n"
            "      <version>4.13.2</version>\n"
            "      <scope>test</scope>\n"
            "    </dependency>\n"
            "  </dependencies>\n"
            "</project>\n"
        )
        manifest = parse_maven_dependencies(tmp_path)
        assert manifest.classify_import("junit") == Tier.INTERNAL_DEP

    def test_with_maven_namespace(self, tmp_path: Path) -> None:
        """pom.xml with Maven namespace (common in real projects)."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_maven_dependencies

        (tmp_path / "pom.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
            "  <dependencies>\n"
            "    <dependency>\n"
            "      <groupId>org.apache.kafka</groupId>\n"
            "      <artifactId>kafka-clients</artifactId>\n"
            "      <version>3.6.0</version>\n"
            "    </dependency>\n"
            "  </dependencies>\n"
            "</project>\n"
        )
        manifest = parse_maven_dependencies(tmp_path)
        assert manifest.classify_import("org.apache.kafka") == Tier.INTERNAL_DEP

    def test_multiple_dependencies(self, tmp_path: Path) -> None:
        """Multiple Maven deps."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_maven_dependencies

        (tmp_path / "pom.xml").write_text(
            "<project>\n"
            "  <dependencies>\n"
            "    <dependency>\n"
            "      <groupId>org.slf4j</groupId>\n"
            "      <artifactId>slf4j-api</artifactId>\n"
            "    </dependency>\n"
            "    <dependency>\n"
            "      <groupId>com.google.guava</groupId>\n"
            "      <artifactId>guava</artifactId>\n"
            "    </dependency>\n"
            "  </dependencies>\n"
            "</project>\n"
        )
        manifest = parse_maven_dependencies(tmp_path)
        assert manifest.classify_import("org.slf4j") == Tier.INTERNAL_DEP
        assert manifest.classify_import("com.google.guava") == Tier.INTERNAL_DEP

    def test_no_pom_xml(self, tmp_path: Path) -> None:
        """Missing pom.xml returns empty manifest."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_maven_dependencies

        manifest = parse_maven_dependencies(tmp_path)
        assert manifest.entries == {}

    def test_dependency_management_section(self, tmp_path: Path) -> None:
        """dependencyManagement section deps are also captured."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_maven_dependencies

        (tmp_path / "pom.xml").write_text(
            "<project>\n"
            "  <dependencyManagement>\n"
            "    <dependencies>\n"
            "      <dependency>\n"
            "        <groupId>org.springframework</groupId>\n"
            "        <artifactId>spring-framework-bom</artifactId>\n"
            "      </dependency>\n"
            "    </dependencies>\n"
            "  </dependencyManagement>\n"
            "</project>\n"
        )
        manifest = parse_maven_dependencies(tmp_path)
        assert manifest.classify_import("org.springframework") == Tier.INTERNAL_DEP

    def test_malformed_pom_returns_empty(self, tmp_path: Path) -> None:
        """Malformed pom.xml returns empty manifest."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_maven_dependencies

        (tmp_path / "pom.xml").write_text("not xml at all")
        manifest = parse_maven_dependencies(tmp_path)
        assert manifest.entries == {}

    def test_submodule_pom(self, tmp_path: Path) -> None:
        """Discovers deps from submodule pom.xml files."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_maven_dependencies

        sub = tmp_path / "core"
        sub.mkdir()
        (sub / "pom.xml").write_text(
            "<project>\n"
            "  <dependencies>\n"
            "    <dependency>\n"
            "      <groupId>io.netty</groupId>\n"
            "      <artifactId>netty-all</artifactId>\n"
            "    </dependency>\n"
            "  </dependencies>\n"
            "</project>\n"
        )
        manifest = parse_maven_dependencies(tmp_path)
        assert manifest.classify_import("io.netty") == Tier.INTERNAL_DEP


class TestParseJvmDependencies:
    """Tests for the combined parse_jvm_dependencies orchestrator."""

    def test_gradle_only(self, tmp_path: Path) -> None:
        """Gradle project without Maven."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_jvm_dependencies

        (tmp_path / "build.gradle").write_text(
            "dependencies {\n"
            "    implementation 'com.squareup.okhttp3:okhttp:4.12.0'\n"
            "}\n"
        )
        manifest = parse_jvm_dependencies(tmp_path)
        assert manifest.classify_import("com.squareup.okhttp3") == Tier.INTERNAL_DEP

    def test_maven_only(self, tmp_path: Path) -> None:
        """Maven project without Gradle."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_jvm_dependencies

        (tmp_path / "pom.xml").write_text(
            "<project>\n"
            "  <dependencies>\n"
            "    <dependency>\n"
            "      <groupId>org.apache.commons</groupId>\n"
            "      <artifactId>commons-lang3</artifactId>\n"
            "    </dependency>\n"
            "  </dependencies>\n"
            "</project>\n"
        )
        manifest = parse_jvm_dependencies(tmp_path)
        assert manifest.classify_import("org.apache.commons") == Tier.INTERNAL_DEP

    def test_both_merged(self, tmp_path: Path) -> None:
        """Project with both Gradle and Maven (Gradle + parent pom)."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_jvm_dependencies

        (tmp_path / "build.gradle").write_text(
            "dependencies {\n"
            "    implementation 'io.ktor:ktor-core:2.3.0'\n"
            "}\n"
        )
        (tmp_path / "pom.xml").write_text(
            "<project>\n"
            "  <dependencies>\n"
            "    <dependency>\n"
            "      <groupId>org.slf4j</groupId>\n"
            "      <artifactId>slf4j-api</artifactId>\n"
            "    </dependency>\n"
            "  </dependencies>\n"
            "</project>\n"
        )
        manifest = parse_jvm_dependencies(tmp_path)
        assert manifest.classify_import("io.ktor") == Tier.INTERNAL_DEP
        assert manifest.classify_import("org.slf4j") == Tier.INTERNAL_DEP

    def test_empty_project(self, tmp_path: Path) -> None:
        """No build files returns empty manifest."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_jvm_dependencies

        manifest = parse_jvm_dependencies(tmp_path)
        assert manifest.entries == {}

    def test_unknown_import_is_external(self, tmp_path: Path) -> None:
        """Import not matching any declared dep → EXTERNAL_DEP."""
        from hypergumbo_lang_mainstream.jvm_deps import parse_jvm_dependencies

        (tmp_path / "build.gradle").write_text(
            "dependencies {\n"
            "    implementation 'com.google.guava:guava:31.1-jre'\n"
            "}\n"
        )
        manifest = parse_jvm_dependencies(tmp_path)
        assert manifest.classify_import("org.unknown.pkg") == Tier.EXTERNAL_DEP
