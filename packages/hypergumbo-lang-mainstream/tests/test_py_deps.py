# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Python pyproject.toml dependency manifest parser (WI-nunuj)."""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.supply_chain import DependencyManifest, Tier
from hypergumbo_lang_mainstream.py_deps import (
    _extract_distribution_name,
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

    def test_stdlib_carve_out_follows_catalog_not_sys(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # ADR-0041 §3 single-source (WI-bifih): the stdlib carve-out must follow
        # the python.yaml ``stdlib_modules`` catalog, NOT the live interpreter's
        # ``sys.stdlib_module_names`` (the second source §3 forbids). Prove it by
        # making the catalog declare a name that is NOT in sys.stdlib_module_names
        # ("clicklib") as stdlib: it must be carved out (only possible via the
        # catalog), while a name the catalog omits is kept.
        import sys

        from hypergumbo_core.io_boundary import IoBoundaryCatalog
        from hypergumbo_lang_mainstream import py_deps as _py_deps

        assert "clicklib" not in getattr(sys, "stdlib_module_names", frozenset())
        fake = IoBoundaryCatalog(
            language="python", stdlib_modules=frozenset({"clicklib"})
        )
        monkeypatch.setattr(_py_deps, "load_catalog", lambda lang: fake)
        (tmp_path / "pyproject.toml").write_text(
            "[project]\n"
            'name = "demo"\n'
            'dependencies = ["clicklib", "keepme"]\n'
        )
        manifest = parse_python_dependencies(tmp_path)
        assert "clicklib" not in manifest.entries  # carved by the catalog
        assert "keepme" in manifest.entries  # absent from catalog -> kept

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


class TestPyprojectClassifiesDirectness:
    """End-to-end: a Python boundary node referencing a pyproject-declared dep
    is tier 3 (external — distance only) and carries directness 'direct'.

    ADR-0041 §1/§2 (supply:F5): declared third-party deps no longer get tier 2;
    the declaration relationship moves to the `directness` meta stamp.
    """

    def test_direct_dep_is_tier3_with_directness_direct(self, tmp_path: Path) -> None:
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
        # All third-party boundary nodes are tier 3 (distance only).
        sc_tiers = {(n.get("supply_chain") or {}).get("tier") for n in click_externals}
        assert sc_tiers == {3}, (
            f"Expected click boundary nodes all tier 3; saw tiers={sc_tiers}"
        )
        # The direct-dependency relationship is recorded on `directness`.
        directness = {(n.get("meta") or {}).get("directness") for n in click_externals}
        assert directness == {"direct"}, (
            f"Expected directness 'direct' on declared click dep; saw {directness}"
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


class TestLoadPyprojectOSError:
    """Direct exercise of `_load_pyproject`'s OSError-on-read defensive path.

    Reachable in production: `_find_pyproject_files` discovers a pyproject,
    but between discovery and `_load_pyproject(file)` the file may disappear
    (transient cleanup) or become unreadable (permission change). Both
    realistic in monorepo CI environments where workspace cleanup runs
    concurrently with analysis.
    """

    def test_returns_none_when_read_text_raises(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from hypergumbo_lang_mainstream.py_deps import _load_pyproject

        existing = tmp_path / "pyproject.toml"
        existing.write_text("[project]\nname = 'x'\n")
        with patch.object(Path, "read_text", side_effect=OSError("simulated")):
            assert _load_pyproject(existing) is None


class TestParsePythonDependenciesMonorepo:
    """WI-zujip: walks `packages/<pkg>/pyproject.toml` so monorepo layouts
    (where the root pyproject is shared-tool-config-only and actual deps live
    in per-package files) get tier-2 classification on declared deps. Parallel
    of WI-davan E1's source-root walking."""

    def test_walks_packages_pyproject_files(self, tmp_path: Path) -> None:
        # Root pyproject is shared-config-only (no [project]).
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\n"
            'python_files = ["test_*.py"]\n'
        )
        (tmp_path / "packages" / "pkg1").mkdir(parents=True)
        (tmp_path / "packages" / "pkg1" / "pyproject.toml").write_text(
            "[project]\n"
            'name = "pkg1"\n'
            'dependencies = ["click>=8.0"]\n'
        )
        (tmp_path / "packages" / "pkg2").mkdir(parents=True)
        (tmp_path / "packages" / "pkg2" / "pyproject.toml").write_text(
            "[project]\n"
            'name = "pkg2"\n'
            'dependencies = ["rich"]\n'
        )
        manifest = parse_python_dependencies(tmp_path)
        assert "click" in manifest.entries
        assert "rich" in manifest.entries

    def test_root_only_still_works(self, tmp_path: Path) -> None:
        # Single-package layout: root pyproject with deps. Should remain
        # functional (no regression vs. pre-walk behaviour).
        (tmp_path / "pyproject.toml").write_text(
            "[project]\n"
            'name = "demo"\n'
            'dependencies = ["click"]\n'
        )
        manifest = parse_python_dependencies(tmp_path)
        assert "click" in manifest.entries

    def test_no_pyproject_anywhere_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("def main(): pass\n")
        manifest = parse_python_dependencies(tmp_path)
        assert manifest.entries == {}

    def test_dedup_same_dep_across_packages(self, tmp_path: Path) -> None:
        # Two packages declaring `click` should produce one entry, not two.
        (tmp_path / "packages" / "pkg1").mkdir(parents=True)
        (tmp_path / "packages" / "pkg1" / "pyproject.toml").write_text(
            "[project]\nname = \"pkg1\"\ndependencies = [\"click\"]\n",
        )
        (tmp_path / "packages" / "pkg2").mkdir(parents=True)
        (tmp_path / "packages" / "pkg2" / "pyproject.toml").write_text(
            "[project]\nname = \"pkg2\"\ndependencies = [\"click\"]\n",
        )
        manifest = parse_python_dependencies(tmp_path)
        assert "click" in manifest.entries
        # Single canonical entry — no per-package suffixing or duplication.
        click_entries = [k for k in manifest.entries if k == "click"]
        assert len(click_entries) == 1

    def test_skips_default_excludes(self, tmp_path: Path) -> None:
        # A pyproject.toml inside .venv/ or node_modules/ must NOT be picked up.
        (tmp_path / ".venv" / "lib" / "site-packages" / "rogue").mkdir(parents=True)
        (tmp_path / ".venv" / "lib" / "site-packages" / "rogue" / "pyproject.toml").write_text(
            "[project]\nname = \"rogue\"\ndependencies = [\"smuggled\"]\n",
        )
        (tmp_path / "node_modules" / "rogue2").mkdir(parents=True)
        (tmp_path / "node_modules" / "rogue2" / "pyproject.toml").write_text(
            "[project]\nname = \"rogue2\"\ndependencies = [\"smuggled2\"]\n",
        )
        # A real package whose deps SHOULD be picked up.
        (tmp_path / "packages" / "pkg1").mkdir(parents=True)
        (tmp_path / "packages" / "pkg1" / "pyproject.toml").write_text(
            "[project]\nname = \"pkg1\"\ndependencies = [\"click\"]\n",
        )
        manifest = parse_python_dependencies(tmp_path)
        assert "click" in manifest.entries
        assert "smuggled" not in manifest.entries
        assert "smuggled2" not in manifest.entries

    def test_skips_dotfile_dirs(self, tmp_path: Path) -> None:
        # Generic dotfile dirs (.git, .pytest_cache, …) are skipped.
        (tmp_path / ".pytest_cache" / "lurking").mkdir(parents=True)
        (tmp_path / ".pytest_cache" / "lurking" / "pyproject.toml").write_text(
            "[project]\nname = \"lurking\"\ndependencies = [\"hidden\"]\n",
        )
        (tmp_path / "packages" / "pkg1").mkdir(parents=True)
        (tmp_path / "packages" / "pkg1" / "pyproject.toml").write_text(
            "[project]\nname = \"pkg1\"\ndependencies = [\"click\"]\n",
        )
        manifest = parse_python_dependencies(tmp_path)
        assert "click" in manifest.entries
        assert "hidden" not in manifest.entries


class TestExtractDistributionName:
    """supply-verdict F3: a package's own distribution name is read so it can
    be subtracted from the tier-2 set when a sibling declares it as a dep."""

    def test_pep621_project_name(self) -> None:
        assert _extract_distribution_name({"project": {"name": "demo-pkg"}}) == "demo-pkg"

    def test_pep621_project_name_stripped(self) -> None:
        assert _extract_distribution_name({"project": {"name": "  demo  "}}) == "demo"

    def test_poetry_name_when_no_project(self) -> None:
        data = {"tool": {"poetry": {"name": "poetry-pkg"}}}
        assert _extract_distribution_name(data) == "poetry-pkg"

    def test_project_present_without_name_falls_through_to_poetry(self) -> None:
        # [project] exists but has no name; the poetry name is the fallback.
        data = {"project": {"dependencies": ["click"]}, "tool": {"poetry": {"name": "p"}}}
        assert _extract_distribution_name(data) == "p"

    def test_project_name_non_string_ignored(self) -> None:
        # A non-string name is not a usable distribution name.
        assert _extract_distribution_name({"project": {"name": 123}}) is None

    def test_no_name_anywhere_returns_none(self) -> None:
        # Shared-config-only pyproject (e.g. [tool.pytest]) has no own name.
        assert _extract_distribution_name({"tool": {"pytest": {"x": 1}}}) is None

    def test_poetry_subsection_without_name_returns_none(self) -> None:
        # [tool.poetry] exists but declares no name.
        assert _extract_distribution_name({"tool": {"poetry": {"version": "1.0"}}}) is None


class TestWorkspaceMemberSubtraction:
    """supply-verdict F3 / INV-nuzas (ADR-0041 D8a): a monorepo sibling that
    another workspace package declares as a dependency is first-party source,
    not a third-party direct dependency — it must NOT land in the tier-2
    direct-dependency manifest (the "tier-2 direct dependency lie")."""

    def test_workspace_sibling_dep_subtracted(self, tmp_path: Path) -> None:
        # pkg-a depends on its sibling pkg-b AND on a real third party (click).
        (tmp_path / "packages" / "pkg-a").mkdir(parents=True)
        (tmp_path / "packages" / "pkg-a" / "pyproject.toml").write_text(
            "[project]\nname = \"pkg-a\"\ndependencies = [\"pkg-b\", \"click\"]\n",
        )
        (tmp_path / "packages" / "pkg-b").mkdir(parents=True)
        (tmp_path / "packages" / "pkg-b" / "pyproject.toml").write_text(
            "[project]\nname = \"pkg-b\"\n",
        )
        manifest = parse_python_dependencies(tmp_path)
        # Third-party dep is retained as tier-2 direct.
        assert "click" in manifest.entries
        # Workspace sibling is subtracted (would otherwise be "pkg_b").
        assert "pkg_b" not in manifest.entries
        assert "pkg-b" not in manifest.entries

    def test_poetry_workspace_sibling_subtracted(self, tmp_path: Path) -> None:
        # Poetry-style monorepo: own name lives under [tool.poetry].name.
        (tmp_path / "packages" / "lib-core").mkdir(parents=True)
        (tmp_path / "packages" / "lib-core" / "pyproject.toml").write_text(
            "[tool.poetry]\nname = \"lib-core\"\n"
            "[tool.poetry.dependencies]\nlib-util = \"*\"\nrich = \"*\"\n",
        )
        (tmp_path / "packages" / "lib-util").mkdir(parents=True)
        (tmp_path / "packages" / "lib-util" / "pyproject.toml").write_text(
            "[tool.poetry]\nname = \"lib-util\"\n",
        )
        manifest = parse_python_dependencies(tmp_path)
        assert "rich" in manifest.entries
        assert "lib_util" not in manifest.entries

    def test_third_party_not_subtracted_when_no_sibling_shadows_it(
        self, tmp_path: Path
    ) -> None:
        # Regression guard: a normal third-party dep with no matching workspace
        # package name stays in the manifest (subtraction is workspace-only).
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = \"solo\"\ndependencies = [\"click\", \"rich\"]\n",
        )
        manifest = parse_python_dependencies(tmp_path)
        assert "click" in manifest.entries
        assert "rich" in manifest.entries
