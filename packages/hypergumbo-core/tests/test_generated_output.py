# SPDX-License-Identifier: AGPL-3.0-or-later
"""Generated output is recognised by the tool that writes it, not by its name.

ADR-0004 §"Installed dependency source" point 7. ``dist`` / ``build`` /
``_build`` / ``out`` / ``target`` / ``site`` / ``_site`` and the coverage
report names used to be bare ``DEFAULT_EXCLUDES`` entries matched at ANY
depth, so every source directory that happened to share a name with some
tool's output vanished from the analysis, silently: plausible's
``lib/plausible/site/`` (the filed repro, INV-hagof), qemu's ``target/``,
next.js's ``packages/next/src/build``, gitlab's ``lib/gitlab/ci/build``.
Now a {name, producing tool} rule must find the tool's evidence -- its
manifest beside the directory, a declared output path, or a marker the tool
writes into its output -- and a directory with the name but no evidence is
analysed. What is pruned is disclosed, so a wrong exclusion is visible.

These tests drive the public entry points (``is_excluded``, ``find_files``,
``FileIndex.build``, ``cli.main``, ``classify_file``) on fixtures.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core.discovery import (
    BUILD_OUTPUT_RULES,
    COVERAGE_DIR_NAMES,
    DEFAULT_EXCLUDES,
    GENERATED_OUTPUT_DIR_NAMES,
    FileIndex,
    find_files,
    format_generated_output_disclosure,
    generated_output_summary,
    is_excluded,
    match_content_rule,
    walks_into,
)


def _write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


_GIT = shutil.which("git") or "git"


def _git_commit_all(root: Path) -> None:
    subprocess.run([_GIT, "init", "-q"], cwd=root, check=True)
    subprocess.run([_GIT, "add", "-A"], cwd=root, check=True)
    subprocess.run(
        [_GIT, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c"],
        cwd=root, check=True,
    )


# ---------------------------------------------------------------------------
# The filed repro and its siblings: a source directory with an output name
# ---------------------------------------------------------------------------


class TestSourceDirectoriesWithOutputNames:
    def test_plausible_site_modules_are_analysed(self, tmp_path: Path) -> None:
        """INV-hagof: ``lib/plausible/site/`` is an Elixir context module, not
        MkDocs output. It was never discovered."""
        _write(tmp_path / "mix.exs", "defmodule P.MixProject do\nend\n")
        src = _write(
            tmp_path / "lib" / "plausible" / "site" / "toggle_live.ex",
            "defmodule Plausible.Site.ToggleLive do\nend\n",
        )
        assert not is_excluded(src, tmp_path)
        assert src in list(find_files(tmp_path, ["*.ex"]))
        assert src in FileIndex.build(tmp_path).all_files()

    @pytest.mark.parametrize("rel", [
        "target/arm/translate.c",  # qemu: CPU targets at the root
        "packages/next/src/build/index.ts",  # next.js: the build command
        "lib/gitlab/ci/build/policy.rb",  # gitlab: the CI build model
        "lib/gitlab/ci/parsers/coverage/cobertura.rb",
        "pkg/util/coverage/coverage.go",  # kubernetes
        "src/dotty/tools/dotc/coverage/Coverage.scala",  # scala3
        "detekt-generator/src/main/kotlin/out/Printer.kt",
        "docs/_site/globals/site.js",
        "app/_build/helpers.ex",
        "TestResults/Model.cs",
        "htmlcov/view.py",
    ])
    def test_a_name_without_tool_evidence_is_analysed(self, tmp_path: Path, rel: str) -> None:
        f = _write(tmp_path / rel, "x")
        assert not is_excluded(f, tmp_path)
        assert f in FileIndex.build(tmp_path).all_files()

    def test_output_names_are_not_bare_default_excludes(self) -> None:
        """Put back as bare names, every same-named source directory vanishes
        again, silently."""
        for name in GENERATED_OUTPUT_DIR_NAMES | COVERAGE_DIR_NAMES:
            assert name not in DEFAULT_EXCLUDES

    @pytest.mark.parametrize("name", [
        ".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
        ".tox", ".nox", ".cache", ".eggs", ".terraform", ".elixir_ls",
        ".nyc_output", ".c8_output", ".ipynb_checkpoints",
    ])
    def test_unambiguous_tool_names_stay_name_based(self, tmp_path: Path, name: str) -> None:
        assert name in DEFAULT_EXCLUDES
        assert is_excluded(_write(tmp_path / "a" / name / "x.py"), tmp_path)


# ---------------------------------------------------------------------------
# Per-tool evidence
# ---------------------------------------------------------------------------


def _assert_claimed(root: Path, out: str, tool: str) -> None:
    f = _write(root / out / "gen.js", "x")
    rule = match_content_rule(root / out)
    assert rule is not None and rule.ecosystem == tool, rule
    assert is_excluded(f, root)
    assert f not in FileIndex.build(root).all_files()


class TestToolManifestBeside:
    @pytest.mark.parametrize("out,evidence,tool", [
        ("site", "mkdocs.yml", "MkDocs site"),
        ("site", "mkdocs.yaml", "MkDocs site"),
        ("_site", "_config.yml", "Jekyll site"),
        ("_site", "eleventy.config.js", "Eleventy site"),
        ("_site", "_quarto.yml", "Quarto site"),
        ("_build", "conf.py", "Sphinx build"),
        ("_build", "_toc.yml", "Jupyter Book build"),
        ("build", "source/conf.py", "Sphinx build"),
        ("build", "docusaurus.config.js", "Docusaurus build"),
        ("_build", "mix.exs", "Mix build"),
        ("_build", "rebar.config", "rebar3 build"),
        ("_build", "dune-project", "Dune build"),
        ("target", "Cargo.toml", "Cargo build"),
        ("target", "pom.xml", "Maven build"),
        ("target", "build.sbt", "sbt build"),
        ("project/target", "build.sbt", "sbt build"),
        ("target", "project.clj", "Leiningen build"),
        ("target", "build.clj", "Clojure tools.build"),
        ("build", "build.gradle.kts", "Gradle build"),
        ("build", "settings.gradle", "Gradle build"),
        ("build", "pubspec.yaml", "Flutter build"),
        ("build", "gleam.toml", "Gleam build"),
        ("out", "next.config.js", "Next.js export"),
    ])
    def test_claimed(self, tmp_path: Path, out: str, evidence: str, tool: str) -> None:
        _write(tmp_path / evidence, "")
        _assert_claimed(tmp_path, out, tool)

    def test_at_any_depth_beside_its_manifest(self, tmp_path: Path) -> None:
        """A Gradle module's ``app/build`` or a workspace member's
        ``crates/x/target`` is output wherever the manifest is."""
        _write(tmp_path / "app" / "build.gradle", "")
        _assert_claimed(tmp_path, "app/build", "Gradle build")

    def test_committed_output_is_still_output(self, tmp_path: Path) -> None:
        _write(tmp_path / "Cargo.toml", "[package]\n")
        f = _write(tmp_path / "target" / "debug" / "build.rs", "fn main() {}")
        _git_commit_all(tmp_path)
        assert is_excluded(f, tmp_path)

    def test_a_subproject_named_like_output_is_analysed(self, tmp_path: Path) -> None:
        """dart-lang/build: ``build/`` is a package with its own pubspec
        beside the workspace pubspec; a Maven module may be ``coverage/``."""
        _write(tmp_path / "pubspec.yaml", "name: ws\n")
        f = _write(tmp_path / "build" / "lib" / "build.dart", "x")
        _write(tmp_path / "build" / "pubspec.yaml", "name: build\n")
        assert not is_excluded(f, tmp_path)

    @pytest.mark.parametrize("cfg,claimed", [
        ("site_name: x\n", True),
        ("site_dir: site\n", True),
        ("site_dir: 'site'  # default\n", True),
        ("site_dir: ./site/\n", True),
        ("site_dir: public\n", False),
    ])
    def test_mkdocs_site_dir(self, tmp_path: Path, cfg: str, claimed: bool) -> None:
        _write(tmp_path / "mkdocs.yml", cfg)
        f = _write(tmp_path / "site" / "index.html", "x")
        assert is_excluded(f, tmp_path) is claimed

    def test_mkdocs_source_tree_named_site_is_analysed(self, tmp_path: Path) -> None:
        """iceberg: ``site/`` holds the MkDocs SOURCE (its own mkdocs.yml)."""
        _write(tmp_path / "mkdocs.yml", "")
        _write(tmp_path / "site" / "mkdocs.yml", "")
        f = _write(tmp_path / "site" / "hooks.py", "x")
        assert not is_excluded(f, tmp_path)

    def test_sphinx_conf_beside_a_plain_build_dir_is_not_evidence(self, tmp_path: Path) -> None:
        """u-boot: ``doc/build/`` is documentation about building, beside
        ``doc/conf.py`` -- Sphinx writes ``_build``, not ``build``."""
        _write(tmp_path / "doc" / "conf.py", "")
        f = _write(tmp_path / "doc" / "build" / "gcc.rst", "x")
        assert not is_excluded(f, tmp_path)


class TestMarkersInsideTheOutput:
    @pytest.mark.parametrize("out,marker,tool", [
        ("build", "CMakeCache.txt", "CMake build"),
        ("build", "meson-private/coredata.dat", "Meson build"),
        ("build", "build.ninja", "Ninja build"),
        ("out", "build.ninja", "Ninja build"),
        ("out", "Default/args.gn", "Ninja build"),
        ("out", "_next/static/chunks/a.js", "Next.js export"),
        ("target", "CACHEDIR.TAG", "cache directory (CACHEDIR.TAG)"),
        ("site", "CACHEDIR.TAG", "cache directory (CACHEDIR.TAG)"),
        ("coverage", "lcov.info", "coverage report"),
        ("coverage", "lcov-report/index.html", "coverage report"),
        ("coverage", ".resultset.json", "coverage report"),
        ("htmlcov", "coverage_html.js", "coverage report"),
        ("htmlcov", "coverage_html_cb_6fb7b396.js", "coverage report"),
        ("TestResults", "run.trx", "coverage report"),
        ("TestResults", "0f8fad5b/coverage.cobertura.xml", "coverage report"),
    ])
    def test_claimed(self, tmp_path: Path, out: str, marker: str, tool: str) -> None:
        _write(tmp_path / "pkg" / out / marker, "x")
        _assert_claimed(tmp_path / "pkg", out, tool)

    @pytest.mark.parametrize("ignore,claimed", [
        ("*\n", True),
        ("# generated\n**/*\n!.gitignore\n", True),
        ("*.log\n", False),
        ("*\n!keep.js\n", False),
        ("", False),
    ])
    def test_self_ignoring_gitignore(self, tmp_path: Path, ignore: str, claimed: bool) -> None:
        _write(tmp_path / "dist" / ".gitignore", ignore)
        f = _write(tmp_path / "dist" / "app.js", "x")
        assert is_excluded(f, tmp_path) is claimed

    def test_committed_files_override_a_self_ignoring_gitignore(self, tmp_path: Path) -> None:
        """piper-tts-web force-added ``build/build.sh`` under ``**/*``."""
        _write(tmp_path / "build" / ".gitignore", "**/*\n!.gitignore\n")
        f = _write(tmp_path / "build" / "build.sh", "x")
        _git_commit_all(tmp_path)
        subprocess.run([_GIT, "add", "-f", "build/build.sh"], cwd=tmp_path, check=True)
        subprocess.run(
            [_GIT, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "f"],
            cwd=tmp_path, check=True,
        )
        assert not is_excluded(f, tmp_path)

    def test_setuptools_build(self, tmp_path: Path) -> None:
        _write(tmp_path / "setup.py", "")
        _write(tmp_path / "build" / "lib.linux-x86_64-3.12" / "pkg" / "m.py", "x")
        assert match_content_rule(tmp_path / "build").ecosystem == "setuptools build"  # type: ignore[union-attr]
        scripts = tmp_path / "other"
        _write(scripts / "setup.py", "")
        assert not is_excluded(_write(scripts / "build" / "make.py", "x"), scripts)

    def test_python_dist_holds_only_archives(self, tmp_path: Path) -> None:
        _write(tmp_path / "pyproject.toml", "")
        _write(tmp_path / "dist" / "p-1.0-py3-none-any.whl", "x")
        _write(tmp_path / "dist" / "p-1.0.tar.gz", "x")
        assert match_content_rule(tmp_path / "dist").ecosystem == "Python distributions"  # type: ignore[union-attr]
        # (A fresh directory per case: match_content_rule is cached per path.)
        other = tmp_path / "o"
        _write(other / "pyproject.toml", "")
        _write(other / "dist" / "p-1.0.tar.gz", "x")
        assert not is_excluded(_write(other / "dist" / "release.py", "x"), other)
        empty = tmp_path / "e"
        _write(empty / "pyproject.toml", "")
        (empty / "dist").mkdir()
        assert match_content_rule(empty / "dist") is None


class TestDeclaredJavaScriptOutput:
    @pytest.mark.parametrize("manifest", [
        {"main": "dist/index.js"},
        {"main": "./out/extension.js"},
        {"module": "build/omnitone.esm.js"},
        {"exports": {".": {"import": "./dist/x.mjs", "require": "./dist/x.cjs"}}},
        {"exports": {".": ["./dist/x.mjs", "./fallback.js"]}},
        {"bin": {"tool": "dist/cli.js"}},
        {"types": "dist/index.d.ts"},
    ])
    def test_package_json_entry_point(self, tmp_path: Path, manifest: dict[str, Any]) -> None:
        _write(tmp_path / "package.json", json.dumps(manifest))
        out = next(
            s.lstrip("./").split("/")[0]
            for s in json.dumps(manifest).split('"') if "/" in s
        )
        f = _write(tmp_path / out / "x.js", "x")
        _git_commit_all(tmp_path)  # a committed bundle is still a bundle
        assert match_content_rule(tmp_path / out).ecosystem == "declared JS output"  # type: ignore[union-attr]
        assert is_excluded(f, tmp_path)

    def test_entry_point_into_source_claims_nothing(self, tmp_path: Path) -> None:
        _write(tmp_path / "package.json", json.dumps({
            "main": "index.js", "files": ["dist", "src"], "bin": "lib/cli.js",
        }))
        f = _write(tmp_path / "dist" / "x.js", "x")
        _git_commit_all(tmp_path)
        assert not is_excluded(f, tmp_path)

    def test_unparseable_package_json_is_no_evidence(self, tmp_path: Path) -> None:
        _write(tmp_path / "package.json", "{not json")
        f = _write(tmp_path / "dist" / "x.js", "x")
        _git_commit_all(tmp_path)
        assert not is_excluded(f, tmp_path)

    def test_tsconfig_out_dir(self, tmp_path: Path) -> None:
        _write(tmp_path / "tsconfig.build.json", (
            '{\n  // tsc output\n  "compilerOptions": {"outDir": "./build/js",},\n}\n'
        ))
        f = _write(tmp_path / "build" / "js" / "a.js", "x")
        _git_commit_all(tmp_path)
        assert is_excluded(f, tmp_path)

    @pytest.mark.parametrize("out_dir", ["../build", "/abs/build", ""])
    def test_tsconfig_out_dir_outside_the_project_claims_nothing(
        self, tmp_path: Path, out_dir: str,
    ) -> None:
        _write(tmp_path / "tsconfig.json", json.dumps({"compilerOptions": {"outDir": out_dir}}))
        f = _write(tmp_path / "build" / "a.js", "x")
        _git_commit_all(tmp_path)
        assert not is_excluded(f, tmp_path)

    def test_github_action_runs_its_committed_dist(self, tmp_path: Path) -> None:
        _write(tmp_path / "action.yml", (
            "name: x\nruns:\n  using: node20\n  main: 'dist/index.js'\n"
        ))
        f = _write(tmp_path / "dist" / "index.js", "x")
        _git_commit_all(tmp_path)
        assert is_excluded(f, tmp_path)

    def test_untracked_build_beside_package_json_is_output(self, tmp_path: Path) -> None:
        _write(tmp_path / "package.json", "{}")
        _write(tmp_path / "src" / "a.js", "x")
        _git_commit_all(tmp_path)
        f = _write(tmp_path / "build" / "a.js", "x")
        assert match_content_rule(tmp_path / "build").ecosystem == (  # type: ignore[union-attr]
            "untracked JS build output"
        )
        assert is_excluded(f, tmp_path)

    def test_committed_build_scripts_beside_package_json_are_analysed(
        self, tmp_path: Path,
    ) -> None:
        """VS Code's ``extensions/*/build/update-grammar.mjs``."""
        _write(tmp_path / "package.json", "{}")
        f = _write(tmp_path / "build" / "update-grammar.mjs", "x")
        _git_commit_all(tmp_path)
        assert not is_excluded(f, tmp_path)

    def test_outside_git_untracked_is_no_evidence(self, tmp_path: Path) -> None:
        """A tarball checkout: git cannot say, so nothing is assumed."""
        _write(tmp_path / "package.json", "{}")
        f = _write(tmp_path / "build" / "update-grammar.mjs", "x")
        assert not is_excluded(f, tmp_path)


# ---------------------------------------------------------------------------
# Disclosure, other walkers, tier 4
# ---------------------------------------------------------------------------


class TestDisclosure:
    def test_the_walk_records_what_it_pruned(self, tmp_path: Path) -> None:
        _write(tmp_path / "Cargo.toml", "")
        _write(tmp_path / "target" / "debug" / "a.d", "x")
        _write(tmp_path / "mkdocs.yml", "")
        _write(tmp_path / "site" / "index.html", "x")
        _write(tmp_path / "src" / "target" / "arm.rs", "x")  # analysed
        roots = FileIndex.build(tmp_path).generated_output_roots
        summary = generated_output_summary(roots, tmp_path)
        assert summary == {"dirs": 2, "entries": [
            {"path": "site", "tool": "MkDocs site"},
            {"path": "target", "tool": "Cargo build"},
        ]}
        assert format_generated_output_disclosure(summary) == (
            "Generated output not analysed: site/ (MkDocs site), target/ "
            "(Cargo build). Recognised by the tool that writes each (ADR-0004)."
        )

    def test_nothing_pruned_says_nothing(self) -> None:
        assert format_generated_output_disclosure(None) is None
        assert format_generated_output_disclosure({"dirs": 0, "entries": []}) is None

    def test_long_list_names_three_and_counts_the_rest(self, tmp_path: Path) -> None:
        for i in range(5):
            _write(tmp_path / f"m{i}" / "pom.xml", "")
            _write(tmp_path / f"m{i}" / "target" / "a.class", "x")
        summary = generated_output_summary(
            FileIndex.build(tmp_path).generated_output_roots, tmp_path,
        )
        line = str(format_generated_output_disclosure(summary))
        assert "m2/target/ (Maven build), 2 more directories." in line
        summary["dirs"] = 4
        assert "1 more directory." in str(format_generated_output_disclosure(summary))

    def test_survey_prints_and_records_it(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from hypergumbo_core.cli import main
        from hypergumbo_core.sketch import generate_sketch

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        repo = tmp_path / "app"
        _write(repo / "mix.exs", "defmodule App.MixProject do\nend\n")
        _write(repo / "_build" / "dev" / "lib" / "app.ex", "defmodule Gen do\nend\n")
        _write(repo / "lib" / "app" / "site" / "site.ex", (
            "defmodule App.Site do\n  def hi, do: :ok\nend\n"
        ))
        out = tmp_path / "out.json"
        assert main([
            "survey", str(repo), "--out", str(out),
            "--no-sketch-fan-out", "--no-handler-slices",
        ]) == 0
        err = capsys.readouterr().err
        assert "Generated output not analysed: _build/ (Mix build)." in err
        bm = json.loads(out.read_text())
        paths = {n["path"] for n in bm["nodes"]}
        assert "lib/app/site/site.ex" in paths
        assert not [p for p in paths if p.startswith("_build/")]
        assert bm["supply_chain_summary"]["generated_output_skipped"] == {
            "dirs": 1, "entries": [{"path": "_build", "tool": "Mix build"}],
        }
        sketch = generate_sketch(repo, max_tokens=2000, cached_results=bm)
        assert "Generated output not analysed: _build/ (Mix build)." in sketch


class TestOtherConsumersAskTheSameRules:
    def test_manifest_walks_skip_claimed_output_only(self, tmp_path: Path) -> None:
        _write(tmp_path / "Cargo.toml", "")
        (tmp_path / "target").mkdir()
        (tmp_path / "src" / "build").mkdir(parents=True)
        assert not walks_into(tmp_path / "target")
        assert walks_into(tmp_path / "src" / "build")

    def test_tier_4_follows_the_evidence(self, tmp_path: Path) -> None:
        from hypergumbo_core.supply_chain import Tier, classify_file

        qemu = _write(tmp_path / "q" / "target" / "arm" / "translate.c", "x")
        assert classify_file(qemu, tmp_path / "q").tier != Tier.DERIVED
        _write(tmp_path / "r" / "Cargo.toml", "")
        built = _write(tmp_path / "r" / "target" / "debug" / "gen.rs", "x")
        c = classify_file(built, tmp_path / "r")
        assert c.tier == Tier.DERIVED
        assert c.reason == "in Cargo build output target/"

    def test_derived_skipped_counts_claimed_output(self, tmp_path: Path) -> None:
        from hypergumbo_core.cli import _find_derived_skipped

        _write(tmp_path / "Cargo.toml", "")
        _write(tmp_path / "target" / "debug" / "a.d", "x")
        _write(tmp_path / "build" / "make.py", "x")  # no evidence: source
        assert _find_derived_skipped(tmp_path) == ["target/debug/a.d"]

    def test_ddg_walk_includes_source_named_build(self, tmp_path: Path) -> None:
        from hypergumbo_core.ddg_build import LanguageDdgSpec, _spec_files

        spec = LanguageDdgSpec(
            language="python", function_node_types=frozenset({"function_definition"}),
            file_globs=("*.py",),
        )
        repo = tmp_path / "build" / "repo"  # the checkout itself sits under build/
        src = _write(repo / "pkg" / "build" / "steps.py", "def f():\n    pass\n")
        _write(repo / "setup.py", "")
        gen = _write(repo / "build" / "lib.linux-x86_64-3.12" / "m.py", "x")
        cached = _write(repo / "pkg" / "__pycache__" / "steps.py", "x")
        files = _spec_files(repo, spec)
        assert src in files
        assert gen not in files and cached not in files

    def test_every_output_rule_is_build_output(self) -> None:
        assert all(not r.is_installed_dep for r in BUILD_OUTPUT_RULES)
