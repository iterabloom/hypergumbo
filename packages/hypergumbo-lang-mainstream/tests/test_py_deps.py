# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Python pyproject.toml dependency manifest parser (WI-nunuj)."""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.supply_chain import DependencyManifest, Tier
from hypergumbo_lang_mainstream.py_deps import (
    _extract_pep621_distribution_names,
    _extract_poetry_distribution_names,
    _normalize_dist_name,
    _strip_specifier,
    parse_python_dependencies,
)


class TestStripSpecifier:
    def test_pep440_version_specifier(self) -> None:
        assert _strip_specifier("click>=8.0") == "click"
        assert _strip_specifier("rich==13.0") == "rich"
        assert _strip_specifier("pydantic~=2.0") == "pydantic"
        assert _strip_specifier("requests<3.0") == "requests"
        assert _strip_specifier("hypothesis>4.0") == "hypothesis"
        assert _strip_specifier("pytest!=7.4.0") == "pytest"

    def test_environment_marker(self) -> None:
        assert _strip_specifier("tomli; python_version < '3.11'") == "tomli"

    def test_extras(self) -> None:
        assert _strip_specifier("uvicorn[standard]>=0.20") == "uvicorn"

    def test_bare_name(self) -> None:
        assert _strip_specifier("ruamel.yaml") == "ruamel.yaml"
        assert _strip_specifier("  click  ") == "click"


class TestNormalizeDistName:
    def test_pep503_lowercase(self) -> None:
        assert _normalize_dist_name("PyYAML") == "pyyaml"

    def test_pep503_collapses_separators(self) -> None:
        assert _normalize_dist_name("Foo__Bar.baz--qux") == "foo-bar-baz-qux"


class TestExtractPep621:
    def test_dependencies_section(self) -> None:
        data = {"project": {"dependencies": ["click>=8.0", "rich"]}}
        assert _extract_pep621_distribution_names(data) == {"click", "rich"}

    def test_optional_dependencies_flattened(self) -> None:
        data = {
            "project": {
                "optional-dependencies": {
                    "dev": ["pytest", "ruff>=0.1"],
                    "docs": ["sphinx"],
                },
            },
        }
        assert _extract_pep621_distribution_names(data) == {"pytest", "ruff", "sphinx"}

    def test_no_project_key(self) -> None:
        assert _extract_pep621_distribution_names({}) == set()

    def test_malformed_project(self) -> None:
        assert _extract_pep621_distribution_names({"project": "not a dict"}) == set()

    def test_malformed_dependencies(self) -> None:
        # Non-list dependencies, non-dict optional-dependencies are ignored
        assert _extract_pep621_distribution_names(
            {"project": {"dependencies": "not a list",
                         "optional-dependencies": "not a dict"}},
        ) == set()

    def test_non_string_dep_entries_skipped(self) -> None:
        data = {"project": {"dependencies": ["click", 42, None]}}
        assert _extract_pep621_distribution_names(data) == {"click"}

    def test_non_list_optional_group_skipped(self) -> None:
        data = {
            "project": {
                "optional-dependencies": {"dev": "not a list", "real": ["pytest"]},
            },
        }
        assert _extract_pep621_distribution_names(data) == {"pytest"}

    def test_non_string_inside_optional_group_skipped(self) -> None:
        data = {"project": {"optional-dependencies": {"dev": ["pytest", 99]}}}
        assert _extract_pep621_distribution_names(data) == {"pytest"}


class TestExtractPoetry:
    def test_poetry_dependencies_section(self) -> None:
        data = {
            "tool": {
                "poetry": {
                    "dependencies": {
                        "python": "^3.10",
                        "click": "^8.0",
                        "rich": "*",
                    },
                },
            },
        }
        # python key is the interpreter constraint, must be excluded
        assert _extract_poetry_distribution_names(data) == {"click", "rich"}

    def test_no_tool_key(self) -> None:
        assert _extract_poetry_distribution_names({}) == set()

    def test_malformed_tool(self) -> None:
        assert _extract_poetry_distribution_names({"tool": "not a dict"}) == set()

    def test_no_poetry_subsection(self) -> None:
        assert _extract_poetry_distribution_names({"tool": {}}) == set()

    def test_malformed_poetry(self) -> None:
        assert _extract_poetry_distribution_names({"tool": {"poetry": []}}) == set()

    def test_malformed_poetry_dependencies(self) -> None:
        # Non-dict dependencies are ignored
        assert _extract_poetry_distribution_names(
            {"tool": {"poetry": {"dependencies": ["not", "a", "dict"]}}},
        ) == set()

    def test_non_string_keys_skipped(self) -> None:
        # ints / None as keys are skipped
        data = {"tool": {"poetry": {"dependencies": {123: "x", "click": "*"}}}}
        assert _extract_poetry_distribution_names(data) == {"click"}


class TestParsePythonDependencies:
    def test_no_pyproject(self, tmp_path: Path) -> None:
        manifest = parse_python_dependencies(tmp_path)
        assert isinstance(manifest, DependencyManifest)
        assert manifest.entries == {}

    def test_pep621_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            "[project]\n"
            'name = "demo"\n'
            'dependencies = ["click>=8.0", "rich"]\n'
        )
        manifest = parse_python_dependencies(tmp_path)
        # click and rich are installed in the dev env → real import-name mapping
        assert "click" in manifest.entries
        assert "rich" in manifest.entries
        assert manifest.entries["click"]["direct"] is True

    def test_poetry_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            "[tool.poetry]\n"
            'name = "demo"\n'
            "[tool.poetry.dependencies]\n"
            'python = "^3.10"\n'
            'click = "^8.0"\n'
        )
        manifest = parse_python_dependencies(tmp_path)
        assert "click" in manifest.entries

    def test_optional_dependencies_become_direct(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            "[project]\n"
            'name = "demo"\n'
            "[project.optional-dependencies]\n"
            'dev = ["pytest"]\n'
        )
        manifest = parse_python_dependencies(tmp_path)
        assert "pytest" in manifest.entries

    def test_stdlib_carve_out(self, tmp_path: Path) -> None:
        # A user who erroneously declares a stdlib name as a dependency
        # should NOT see it promoted to tier 2.
        (tmp_path / "pyproject.toml").write_text(
            "[project]\n"
            'name = "demo"\n'
            'dependencies = ["os", "sys", "click"]\n'
        )
        manifest = parse_python_dependencies(tmp_path)
        assert "click" in manifest.entries
        assert "os" not in manifest.entries
        assert "sys" not in manifest.entries

    def test_unknown_dist_falls_through_to_dist_name(self, tmp_path: Path) -> None:
        # An unknown PyPI dist name (not installed in dev env) falls
        # through to the dist name verbatim with hyphens → underscores.
        (tmp_path / "pyproject.toml").write_text(
            "[project]\n"
            'name = "demo"\n'
            'dependencies = ["some-unknown-package-not-installed-anywhere"]\n'
        )
        manifest = parse_python_dependencies(tmp_path)
        assert "some_unknown_package_not_installed_anywhere" in manifest.entries

    def test_malformed_toml_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            "this is = not [valid] toml ===\n"
        )
        manifest = parse_python_dependencies(tmp_path)
        assert manifest.entries == {}

    def test_empty_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("")
        manifest = parse_python_dependencies(tmp_path)
        assert manifest.entries == {}

    def test_pyproject_without_dependency_sections(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            "[build-system]\n"
            'requires = ["setuptools"]\n'
        )
        manifest = parse_python_dependencies(tmp_path)
        assert manifest.entries == {}


class TestPyprojectClassifiesAsTier2:
    """End-to-end: a Python boundary node referencing a pyproject-declared
    dep is classified as tier 2 (direct dependency)."""

    def test_direct_dep_classified_tier2(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            "[project]\n"
            'name = "demo"\n'
            'dependencies = ["click>=8.0"]\n'
        )
        (tmp_path / "main.py").write_text(
            "import click\n"
            "\n"
            "def cli():\n"
            "    click.echo('hi')\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(
            repo_root=tmp_path, out_path=out_path,
            include_sketch_precomputed=False,
        )
        data = json.loads(out_path.read_text())

        click_externals = [
            n for n in data["nodes"]
            if n.get("language") == "python"
            and (n.get("meta") or {}).get("external_boundary") is True
            and "click" in (n.get("id") or "")
        ]
        assert len(click_externals) >= 1
        sc_tiers = {(n.get("supply_chain") or {}).get("tier") for n in click_externals}
        assert 2 in sc_tiers, (
            f"Expected at least one tier-2 click boundary node; saw tiers={sc_tiers}"
        )

    def test_unknown_import_stays_tier3(self, tmp_path: Path) -> None:
        # No pyproject → no manifest → all externals stay tier 3.
        (tmp_path / "main.py").write_text(
            "import some_unknown_pkg\n"
            "\n"
            "def fn():\n"
            "    some_unknown_pkg.do()\n"
        )
        out_path = tmp_path / "out.json"
        run_behavior_map(
            repo_root=tmp_path, out_path=out_path,
            include_sketch_precomputed=False,
        )
        data = json.loads(out_path.read_text())
        externals = [
            n for n in data["nodes"]
            if n.get("language") == "python"
            and (n.get("meta") or {}).get("external_boundary") is True
            and "some_unknown_pkg" in (n.get("id") or "")
        ]
        if externals:  # may be 0 depending on edge production
            tiers = {(n.get("supply_chain") or {}).get("tier") for n in externals}
            assert tiers == {3}
