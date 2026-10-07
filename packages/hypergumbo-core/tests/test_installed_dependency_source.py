# SPDX-License-Identifier: AGPL-3.0-or-later
"""Installed dependency source is off by default (ADR-0004 §"Installed dependency source").

THE POLICY, AS DECIDED. Source a package manager put into the working tree --
Mix ``deps/``, ``node_modules/``, a virtualenv, a Composer / Go ``vendor/``,
Bundler's ``vendor/bundle``, CocoaPods ``Pods/`` -- is not parsed unless the
user opts in with ``--trace-deps <pkg,...|all>`` (or the ``trace_deps`` config
key). It is recognised by CONTENT, per ecosystem (a name only selects which
directories get the test), and a directory whose files are COMMITTED is
vendored code, never installed, whatever markers it carries. A skip is
disclosed: one stderr line, one sketch line, and
``supply_chain_summary.installed_deps_skipped``.

WHY. A dev box and a fresh clone of the same commit must give the same default
output (a pristine clone has no ``deps/``); parsing one developer checkout's
40 MB / 81-package ``deps/`` got a 6 GB VM OOM-killed; and the real use of
dependency source is targeted ("what does ``chain.run`` actually send?"), so
the opt-in is per package.

These tests drive the public entry points (``is_excluded``, ``find_files``,
``FileIndex.build``, ``cli.main``) on fixtures, plus the registry's own
functions for the per-ecosystem marker and package-listing rules.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterator

import pytest

from hypergumbo_core import discovery
from hypergumbo_core.discovery import (
    BUILD_OUTPUT_RULES,
    DEFAULT_EXCLUDES,
    INSTALLED_DEP_RULES,
    FileIndex,
    TraceDeps,
    content_rule_prunes,
    find_files,
    format_installed_deps_disclosure,
    installed_deps_summary,
    installed_packages,
    is_excluded,
    manifest_walk_skip,
    match_content_rule,
    resolve_trace_deps,
    set_active_trace_deps,
    set_trace_deps_override,
    walks_into,
)
from hypergumbo_core.user_config import LayeredConfig


@pytest.fixture(autouse=True)
def _reset_trace_state() -> Iterator[None]:
    """Every test starts and ends with nothing traced and no flag recorded."""
    set_active_trace_deps(TraceDeps(), None)
    set_trace_deps_override(None)
    yield
    set_active_trace_deps(TraceDeps(), None)
    set_trace_deps_override(None)


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


def _mix_project(root: Path, *, lock: bool = True, hex_meta: bool = True) -> Path:
    """mix.exs (+ mix.lock), lib/app.ex calling Foo.greet, deps/foo/lib/foo.ex."""
    _write(root / "mix.exs", (
        "defmodule App.MixProject do\n  use Mix.Project\n"
        "  def project, do: [app: :app, deps: [{:foo, \"~> 1.0\"}]]\nend\n"
    ))
    if lock:
        _write(root / "mix.lock", '%{"foo": {:hex, :foo, "1.0.0"}}\n')
    _write(root / "lib" / "app.ex", (
        "defmodule App do\n  def hi do\n    Foo.greet(\"x\")\n  end\nend\n"
    ))
    _write(root / "deps" / "foo" / "lib" / "foo.ex", (
        "defmodule Foo do\n  def greet(name) do\n    name\n  end\nend\n"
    ))
    if hex_meta:
        _write(root / "deps" / "foo" / "hex_metadata.config", '{<<"name">>,<<"foo">>}.\n')
    return root


# ---------------------------------------------------------------------------
# Detection by content, per ecosystem
# ---------------------------------------------------------------------------


class TestMixDeps:
    def test_sibling_mix_exs_and_lock_mark_deps_installed(self, tmp_path: Path) -> None:
        _mix_project(tmp_path, hex_meta=False)
        rule = match_content_rule(tmp_path / "deps")
        assert rule is not None and rule.ecosystem == "Mix"
        assert is_excluded(tmp_path / "deps" / "foo" / "lib" / "foo.ex", tmp_path)

    def test_hex_metadata_alone_marks_deps_installed(self, tmp_path: Path) -> None:
        """No mix.lock beside it (a lock-less checkout); a Hex-fetched package
        inside still identifies the directory."""
        _mix_project(tmp_path, lock=False, hex_meta=True)
        assert is_excluded(tmp_path / "deps" / "foo" / "lib" / "foo.ex", tmp_path)

    def test_a_first_party_deps_directory_is_analysed(self, tmp_path: Path) -> None:
        """No mix.lock, no hex_metadata.config: a directory merely CALLED deps
        (a monorepo's own libraries) is source."""
        _mix_project(tmp_path, lock=False, hex_meta=False)
        assert match_content_rule(tmp_path / "deps") is None
        assert not is_excluded(tmp_path / "deps" / "foo" / "lib" / "foo.ex", tmp_path)

    def test_packages_are_the_app_directories(self, tmp_path: Path) -> None:
        _mix_project(tmp_path)
        (tmp_path / "deps" / "phoenix").mkdir()
        (tmp_path / "deps" / ".hidden").mkdir()
        rule = match_content_rule(tmp_path / "deps")
        assert rule is not None
        assert sorted(installed_packages(tmp_path / "deps", rule)) == ["foo", "phoenix"]


class TestNodeModules:
    def test_sibling_package_json_marks_installed(self, tmp_path: Path) -> None:
        _write(tmp_path / "web" / "package.json", "{}")
        f = _write(tmp_path / "web" / "node_modules" / "react" / "index.js", "x")
        assert is_excluded(f, tmp_path)

    @pytest.mark.parametrize("state", [
        ".package-lock.json", ".yarn-integrity", ".modules.yaml", ".yarn-state.yml",
    ])
    def test_package_manager_state_file_marks_installed(
        self, tmp_path: Path, state: str,
    ) -> None:
        _write(tmp_path / "node_modules" / state, "{}")
        f = _write(tmp_path / "node_modules" / "react" / "index.js", "x")
        assert is_excluded(f, tmp_path)

    def test_a_bare_node_modules_fixture_is_analysed(self, tmp_path: Path) -> None:
        """Resolver test fixtures commit a bare ``node_modules`` with neither a
        manifest beside it nor a state file; that is not an install."""
        f = _write(tmp_path / "test" / "fixtures" / "node_modules" / "x" / "index.js", "x")
        assert match_content_rule(f.parents[1]) is None
        assert not is_excluded(f, tmp_path)

    def test_packages_include_scoped_and_skip_bin(self, tmp_path: Path) -> None:
        _write(tmp_path / "package.json", "{}")
        nm = tmp_path / "node_modules"
        for d in ("react", "@babel/core", "@babel/parser", ".bin"):
            (nm / d).mkdir(parents=True)
        rule = match_content_rule(nm)
        assert rule is not None
        assert sorted(installed_packages(nm, rule)) == [
            "@babel/core", "@babel/parser", "react",
        ]


class TestVendor:
    def test_composer_vendor(self, tmp_path: Path) -> None:
        _write(tmp_path / "composer.json", "{}")
        _write(tmp_path / "vendor" / "autoload.php", "<?php")
        _write(tmp_path / "vendor" / "guzzlehttp" / "guzzle" / "src" / "Client.php", "<?php")
        (tmp_path / "vendor" / "bin").mkdir()
        (tmp_path / "vendor" / "composer").mkdir()
        rule = match_content_rule(tmp_path / "vendor")
        assert rule is not None and rule.ecosystem == "Composer"
        assert list(installed_packages(tmp_path / "vendor", rule)) == ["guzzlehttp/guzzle"]
        assert is_excluded(tmp_path / "vendor" / "guzzlehttp" / "guzzle" / "src" / "Client.php", tmp_path)

    def test_go_module_vendor(self, tmp_path: Path) -> None:
        _write(tmp_path / "go.mod", "module x\n")
        _write(tmp_path / "vendor" / "modules.txt", (
            "# github.com/pkg/errors v0.9.1\n## explicit\ngithub.com/pkg/errors\n"
            "# golang.org/x/absent v0.1.0\n"
        ))
        _write(tmp_path / "vendor" / "github.com" / "pkg" / "errors" / "errors.go", "package errors")
        rule = match_content_rule(tmp_path / "vendor")
        assert rule is not None and rule.ecosystem == "Go module"
        assert list(installed_packages(tmp_path / "vendor", rule)) == ["github.com/pkg/errors"]

    def test_vendor_without_a_marker_is_analysed(self, tmp_path: Path) -> None:
        """Rails ``vendor/javascript``, a C project's ``vendor/zlib``: no package
        manager filled it, so it is source (tier 3, see TestTiering)."""
        f = _write(tmp_path / "vendor" / "zlib" / "zlib.c", "int x;")
        assert match_content_rule(tmp_path / "vendor") is None
        assert not is_excluded(f, tmp_path)

    def test_bundler_vendor_bundle_but_not_the_rest_of_vendor(self, tmp_path: Path) -> None:
        _write(tmp_path / "Gemfile.lock", "GEM\n")
        gem = _write(
            tmp_path / "vendor" / "bundle" / "ruby" / "3.2.0" / "gems" / "rack-3.0.8"
            / "lib" / "rack.rb", "module Rack; end",
        )
        (tmp_path / "vendor" / "bundle" / "ruby" / "3.2.0" / "gems" / "oddname").mkdir()
        own = _write(tmp_path / "vendor" / "javascript" / "pin.js", "x")
        bundle = tmp_path / "vendor" / "bundle"
        rule = match_content_rule(bundle)
        assert rule is not None and rule.ecosystem == "Bundler"
        assert sorted(installed_packages(bundle, rule)) == ["oddname", "rack"]
        assert is_excluded(gem, tmp_path)
        assert not is_excluded(own, tmp_path)

    def test_a_bundle_directory_elsewhere_is_not_bundler(self, tmp_path: Path) -> None:
        _write(tmp_path / "Gemfile.lock", "GEM\n")
        (tmp_path / "bundle" / "ruby").mkdir(parents=True)
        assert match_content_rule(tmp_path / "bundle") is None


class TestPodsAndVirtualenvs:
    def test_cocoapods(self, tmp_path: Path) -> None:
        _write(tmp_path / "Podfile.lock", "PODS:\n")
        _write(tmp_path / "Pods" / "Manifest.lock", "PODS:\n")
        for d in ("Alamofire", "Target Support Files", "Headers"):
            (tmp_path / "Pods" / d).mkdir()
        rule = match_content_rule(tmp_path / "Pods")
        assert rule is not None
        assert list(installed_packages(tmp_path / "Pods", rule)) == ["Alamofire"]

    def test_virtualenv_packages_are_import_names(self, tmp_path: Path) -> None:
        site = tmp_path / ".venv" / "lib" / "python3.12" / "site-packages"
        _write(tmp_path / ".venv" / "pyvenv.cfg", "home = /usr\n")
        _write(site / "requests" / "__init__.py")
        _write(site / "six.py")
        _write(site / "requests-2.31.0.dist-info" / "METADATA")
        _write(site / "distutils-precedence.pth")
        _write(site / "README.txt")
        (site / "__pycache__").mkdir()
        _write(tmp_path / ".venv" / "Lib" / "site-packages" / "winpkg" / "__init__.py")
        rule = match_content_rule(tmp_path / ".venv")
        assert rule is not None and rule.ecosystem == "Python virtualenv"
        assert sorted(installed_packages(tmp_path / ".venv", rule)) == [
            "requests", "six", "winpkg",
        ]


class TestBuildOutput:
    """ADR-0004 point 7: build output is pruned with no opt-in and no disclosure."""

    @pytest.mark.parametrize("name,sibling", [
        ("cover", "mix.exs"),
        (".dart_tool", "pubspec.yaml"),
        (".stack-work", "stack.yaml"),
        ("elm-stuff", "elm.json"),
    ])
    def test_conditioned_on_the_project_manifest(
        self, tmp_path: Path, name: str, sibling: str,
    ) -> None:
        bare = _write(tmp_path / "plain" / name / "out.js", "x")
        assert not is_excluded(bare, tmp_path)  # no manifest: just a directory
        f = _write(tmp_path / "proj" / name / "out.js", "x")
        _write(tmp_path / "proj" / sibling, "")
        assert is_excluded(f, tmp_path)
        set_active_trace_deps(TraceDeps(everything=True), tmp_path)
        assert is_excluded(f, tmp_path)  # --trace-deps all does not reach it

    def test_phoenix_built_assets_only_at_priv_static_assets(self, tmp_path: Path) -> None:
        _write(tmp_path / "mix.exs", "")
        built = _write(tmp_path / "priv" / "static" / "assets" / "app.js", "x")
        source = _write(tmp_path / "assets" / "js" / "app.js", "x")
        assert is_excluded(built, tmp_path)
        assert not is_excluded(source, tmp_path)

    def test_elixir_ls_is_a_default_exclude(self, tmp_path: Path) -> None:
        assert ".elixir_ls" in DEFAULT_EXCLUDES
        assert is_excluded(_write(tmp_path / ".elixir_ls" / "x.ex"), tmp_path)

    def test_build_output_rules_have_no_packages(self) -> None:
        assert all(not r.is_installed_dep for r in BUILD_OUTPUT_RULES)
        assert all(r.is_installed_dep for r in INSTALLED_DEP_RULES)


class TestNamesLeftTheDefaultList:
    def test_dependency_names_are_not_bare_default_excludes(self) -> None:
        """They moved to content rules. Put back as bare names, a committed
        ``vendor/`` vanishes from the analysis again, silently."""
        for name in ("node_modules", "vendor", "deps", "Pods", "venv", ".venv", "env"):
            assert name not in DEFAULT_EXCLUDES

    def test_a_file_named_like_a_candidate_is_not_a_directory(self, tmp_path: Path) -> None:
        _write(tmp_path / "mix.exs")
        _write(tmp_path / "mix.lock")
        f = _write(tmp_path / "deps", "not a directory")
        assert match_content_rule(f) is None
        assert not is_excluded(f, tmp_path)


# ---------------------------------------------------------------------------
# Committed directories are vendored code, never installed (owner decision)
# ---------------------------------------------------------------------------


class TestCommittedIsNeverInstalled:
    def test_committed_go_vendor_tree_is_analysed(self, tmp_path: Path) -> None:
        """A ``go mod vendor`` tree is often committed and carries
        vendor/modules.txt; committed wins over the marker."""
        _write(tmp_path / "go.mod", "module x\n")
        _write(tmp_path / "vendor" / "modules.txt", "# github.com/a/b v1\n")
        f = _write(tmp_path / "vendor" / "github.com" / "a" / "b" / "b.go", "package b")
        _git_commit_all(tmp_path)
        assert match_content_rule(tmp_path / "vendor") is None
        assert not is_excluded(f, tmp_path)

    def test_untracked_install_in_a_git_repo_is_still_installed(self, tmp_path: Path) -> None:
        _write(tmp_path / "mix.exs")
        _write(tmp_path / "mix.lock")
        _git_commit_all(tmp_path)
        f = _write(tmp_path / "deps" / "foo" / "lib" / "foo.ex", "x")
        assert is_excluded(f, tmp_path)

    def test_a_committed_placeholder_does_not_commit_the_install(self, tmp_path: Path) -> None:
        """``vendor/.gitkeep`` keeps an empty directory in the tree; the
        installed contents beside it are still installed."""
        _write(tmp_path / "composer.json", "{}")
        _write(tmp_path / "vendor" / ".gitkeep")
        _git_commit_all(tmp_path)
        _write(tmp_path / "vendor" / "autoload.php", "<?php")
        f = _write(tmp_path / "vendor" / "a" / "b" / "x.php", "<?php")
        assert is_excluded(f, tmp_path)

    def test_a_repository_with_no_commits_falls_back_to_markers(self, tmp_path: Path) -> None:
        _write(tmp_path / "package.json", "{}")
        f = _write(tmp_path / "node_modules" / "a" / "i.js", "x")
        subprocess.run([_GIT, "init", "-q"], cwd=tmp_path, check=True)
        assert is_excluded(f, tmp_path)

    def test_an_ambient_git_dir_cannot_redirect_the_query(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A hook environment exporting GIT_DIR must not make the check answer
        for a different repository."""
        other = tmp_path / "other"
        _write(other / "vendor" / "x.txt", "x")
        _git_commit_all(other)
        repo = tmp_path / "repo"
        _write(repo / "composer.json", "{}")
        _write(repo / "vendor" / "autoload.php", "<?php")
        monkeypatch.setenv("GIT_DIR", str(other / ".git"))
        assert match_content_rule(repo / "vendor") is not None


# ---------------------------------------------------------------------------
# --trace-deps
# ---------------------------------------------------------------------------


class TestTraceDepsParse:
    @pytest.mark.parametrize("spec", [None, "", "none", "NONE", [], ["none"]])
    def test_nothing(self, spec: "str | list[str] | None") -> None:
        assert TraceDeps.parse(spec).is_empty

    def test_all(self) -> None:
        t = TraceDeps.parse("all")
        assert t.everything and t.traces("anything")
        assert t.cache_token() == "deps-all"

    def test_names_are_folded(self) -> None:
        t = TraceDeps.parse("Phoenix_HTML, typing-extensions,@babel/core")
        assert t.traces("phoenix_html") and t.traces("typing_extensions")
        assert t.traces("@babel/core") and not t.traces("phoenix")
        assert TraceDeps.parse(["phoenix-html"]).cache_token() == TraceDeps.parse(
            "PHOENIX.HTML").cache_token()
        assert t.cache_token().startswith("deps-") and len(t.cache_token()) == 17

    def test_empty_has_no_cache_token(self) -> None:
        assert TraceDeps().cache_token() == ""

    @pytest.mark.parametrize("spec", ["all,phoenix", ["none", "x"]])
    def test_keywords_do_not_mix_with_names(self, spec: "str | list[str]") -> None:
        with pytest.raises(ValueError, match="cannot be combined"):
            TraceDeps.parse(spec)


class TestPerPackageTracing:
    def _npm(self, root: Path) -> Path:
        _write(root / "package.json", "{}")
        nm = root / "node_modules"
        _write(nm / "react" / "index.js", "x")
        _write(nm / "lodash" / "index.js", "x")
        _write(nm / "@babel" / "core" / "index.js", "x")
        _write(nm / "@babel" / "parser" / "index.js", "x")
        _write(nm / ".package-lock.json", "{}")
        return nm

    def test_only_the_traced_packages_survive(self, tmp_path: Path) -> None:
        nm = self._npm(tmp_path)
        set_active_trace_deps(TraceDeps.parse("react,@babel/core"), tmp_path)
        kept = {
            p.relative_to(tmp_path).as_posix()
            for p in FileIndex.build(tmp_path).all_files()
        }
        assert kept == {
            "package.json", "node_modules/react/index.js",
            "node_modules/@babel/core/index.js",
        }
        # Parity: the per-path walker gives the same answers (the stray state
        # file beside the packages is pruned on both).
        assert is_excluded(nm / ".package-lock.json", tmp_path)
        assert is_excluded(nm / "lodash" / "index.js", tmp_path)
        assert is_excluded(nm / "@babel" / "parser" / "index.js", tmp_path)
        assert not is_excluded(nm / "@babel" / "core" / "index.js", tmp_path)
        assert set(find_files(tmp_path, ["*.js"])) == {
            nm / "react" / "index.js", nm / "@babel" / "core" / "index.js",
        }

    def test_untraced_ecosystem_stays_pruned(self, tmp_path: Path) -> None:
        self._npm(tmp_path)
        set_active_trace_deps(TraceDeps.parse("no-such-package"), tmp_path)
        assert is_excluded(tmp_path / "node_modules" / "react" / "index.js", tmp_path)
        assert not is_excluded(tmp_path / "package.json", tmp_path)

    def test_all_keeps_everything_installed(self, tmp_path: Path) -> None:
        nm = self._npm(tmp_path)
        set_active_trace_deps(TraceDeps(everything=True), tmp_path)
        assert not is_excluded(nm / "lodash" / "index.js", tmp_path)

    def test_virtualenv_single_module_and_package(self, tmp_path: Path) -> None:
        site = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
        _write(tmp_path / "venv" / "pyvenv.cfg", "home = /usr\n")
        _write(site / "requests" / "api.py", "x = 1\n")
        _write(site / "six.py", "x = 1\n")
        _write(site / "other.py", "x = 1\n")
        _write(tmp_path / "venv" / "bin" / "tool.py", "x = 1\n")
        set_active_trace_deps(TraceDeps.parse("requests,six"), tmp_path)
        kept = {
            p.relative_to(tmp_path).as_posix()
            for p in FileIndex.build(tmp_path).all_files()
        }
        # pyvenv.cfg and bin/ sit beside the packages, other.py is untraced.
        assert kept == {
            "venv/lib/python3.12/site-packages/six.py",
            "venv/lib/python3.12/site-packages/requests/api.py",
        }

    def test_ancestor_search_stops_at_the_analysed_root(self, tmp_path: Path) -> None:
        """A repository that itself lives inside someone's ``deps/`` is not
        inside installed source: analysing ``deps/foo`` directly with a
        per-package trace must not prune foo's own files."""
        _mix_project(tmp_path)
        repo = tmp_path / "deps" / "foo"
        set_active_trace_deps(TraceDeps.parse("unrelated"), repo)
        assert not content_rule_prunes("foo.ex", repo / "lib" / "foo.ex")
        set_active_trace_deps(TraceDeps.parse("unrelated"), None)
        assert content_rule_prunes("foo.ex", repo / "lib" / "foo.ex")

    def test_a_path_outside_installed_source_is_untouched(self, tmp_path: Path) -> None:
        """Per-package mode with no analysed root set (a bare ``is_excluded``):
        a path with no installed ancestor is kept."""
        set_active_trace_deps(TraceDeps.parse("react"), None)
        assert not content_rule_prunes("a.py", _write(tmp_path / "src" / "a.py"))

    def test_installed_roots_are_recorded_pruned_or_traced(self, tmp_path: Path) -> None:
        self._npm(tmp_path)
        _mix_project(tmp_path)
        roots = [p for p, _ in FileIndex.build(tmp_path).installed_dep_roots]
        assert sorted(r.name for r in roots) == ["deps", "node_modules"]
        set_active_trace_deps(TraceDeps(everything=True), tmp_path)
        roots = [p for p, _ in FileIndex.build(tmp_path).installed_dep_roots]
        assert sorted(r.name for r in roots) == ["deps", "node_modules"]

    def test_an_explicit_exclude_wins_over_tracing(self, tmp_path: Path) -> None:
        self._npm(tmp_path)
        set_active_trace_deps(TraceDeps(everything=True), tmp_path)
        idx = FileIndex.build(tmp_path, excludes=["node_modules"])
        assert idx.installed_dep_roots == []
        assert not any("node_modules" in p.parts for p in idx.all_files())

    def test_a_path_exclude_hides_the_root_from_the_disclosure(self, tmp_path: Path) -> None:
        self._npm(tmp_path / "web")
        idx = FileIndex.build(tmp_path, excludes=["web/node_modules"])
        assert idx.installed_dep_roots == []


class TestResolution:
    def test_flag_outranks_config_and_config_outranks_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        assert resolve_trace_deps(tmp_path).is_empty
        _write(tmp_path / ".hypergumbo.toml", 'trace_deps = ["phoenix"]\n')
        assert resolve_trace_deps(tmp_path).traces("phoenix")
        set_trace_deps_override(TraceDeps(everything=True))
        assert resolve_trace_deps(tmp_path).everything

    def test_a_bad_config_resolves_to_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        _write(tmp_path / ".hypergumbo.toml", "trace_deps = 3\n")
        assert resolve_trace_deps(tmp_path).is_empty

    def test_the_results_cache_key_carries_the_setting(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from hypergumbo_core.sketch_embeddings import _get_results_cache_dir

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        repo = tmp_path / "repo"
        _write(repo / "a.py", "x = 1\n")
        plain = _get_results_cache_dir(repo).parent.name
        set_trace_deps_override(TraceDeps(everything=True))
        assert _get_results_cache_dir(repo).parent.name == f"{plain}-deps-all"
        set_trace_deps_override(TraceDeps.parse("phoenix"))
        named = _get_results_cache_dir(repo).parent.name
        assert named.startswith(f"{plain}-deps-") and named != f"{plain}-deps-all"


class TestUserConfigKey:
    def _load(self, tmp_path: Path, text: str) -> LayeredConfig:
        from hypergumbo_core.user_config import load_layered_config

        _write(tmp_path / ".hypergumbo.toml", text)
        return load_layered_config(repo_root=tmp_path, environ={
            "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        })

    def test_list_and_all(self, tmp_path: Path) -> None:
        assert self._load(tmp_path, 'trace_deps = ["a", "b"]\n').trace_deps.traces("b")
        assert self._load(tmp_path, 'trace_deps = "all"\n').trace_deps.everything

    def test_project_tier_replaces_user_tier(self, tmp_path: Path) -> None:
        _write(tmp_path / "xdg" / "hypergumbo" / "config.toml", 'trace_deps = "all"\n')
        cfg = self._load(tmp_path, 'trace_deps = ["phoenix"]\n')
        assert not cfg.trace_deps.everything and cfg.trace_deps.traces("phoenix")

    @pytest.mark.parametrize("text,needle", [
        ("trace_deps = 3\n", "must be a str or list"),
        ("trace_deps = [1]\n", "package names"),
        ('trace_deps = ["all", "x"]\n', "cannot be combined"),
    ])
    def test_bad_values_raise(self, tmp_path: Path, text: str, needle: str) -> None:
        from hypergumbo_core.user_config import ConfigError

        with pytest.raises(ConfigError, match=needle):
            self._load(tmp_path, text)


# ---------------------------------------------------------------------------
# Disclosure
# ---------------------------------------------------------------------------


class TestDisclosure:
    def test_nothing_skipped_prints_nothing(self) -> None:
        assert format_installed_deps_disclosure(None) is None
        assert format_installed_deps_disclosure(
            {"dirs": 0, "packages": 0, "entries": []},
        ) is None

    def test_the_line(self, tmp_path: Path) -> None:
        _mix_project(tmp_path)
        (tmp_path / "deps" / "phoenix").mkdir()
        roots = FileIndex.build(tmp_path).installed_dep_roots
        summary = installed_deps_summary(roots, tmp_path, TraceDeps())
        assert summary == {
            "dirs": 1, "packages": 2,
            "entries": [{"path": "deps", "ecosystem": "Mix", "packages": 2, "traced": []}],
        }
        assert format_installed_deps_disclosure(summary) == (
            "Installed dependency source not analysed: deps/ (2 Mix packages). "
            "Calls into them appear as external stubs. "
            "To trace into them: --trace-deps <pkg,...|all>"
        )

    def test_partial_trace_names_the_traced_and_counts_the_rest(self, tmp_path: Path) -> None:
        _mix_project(tmp_path)
        (tmp_path / "deps" / "phoenix").mkdir()
        roots = FileIndex.build(tmp_path).installed_dep_roots
        summary = installed_deps_summary(roots, tmp_path, TraceDeps.parse("foo"))
        assert summary["entries"] == [
            {"path": "deps", "ecosystem": "Mix", "packages": 1, "traced": ["foo"]},
        ]
        assert "(1 Mix package)" in str(format_installed_deps_disclosure(summary))

    def test_fully_traced_directories_are_not_entries(self, tmp_path: Path) -> None:
        _mix_project(tmp_path)
        roots = FileIndex.build(tmp_path).installed_dep_roots
        assert installed_deps_summary(roots, tmp_path, TraceDeps.parse("foo"))["dirs"] == 0
        assert installed_deps_summary(roots, tmp_path, TraceDeps(everything=True))["dirs"] == 0

    def test_a_directory_with_no_packages_is_still_disclosed(self, tmp_path: Path) -> None:
        _write(tmp_path / "package.json", "{}")
        _write(tmp_path / "node_modules" / ".bin" / "tool", "x")
        roots = FileIndex.build(tmp_path).installed_dep_roots
        summary = installed_deps_summary(roots, tmp_path, TraceDeps())
        assert summary["entries"][0]["packages"] == 0

    def test_many_directories_are_summarised(self, tmp_path: Path) -> None:
        roots = []
        for i in range(12):
            d = tmp_path / f"app{i:02d}"
            _write(d / "package.json", "{}")
            _write(d / "node_modules" / "x" / "i.js")
            roots.append(d / "node_modules")
        rule = match_content_rule(roots[0])
        assert rule is not None
        summary = installed_deps_summary([(r, rule) for r in roots], tmp_path, TraceDeps())
        assert summary["dirs"] == 12 and summary["packages"] == 12
        assert len(summary["entries"]) == 10
        line = format_installed_deps_disclosure(summary)
        assert line is not None
        assert "app00/node_modules/ (1 npm package), " in line
        assert "9 more directories" in line
        one_more = dict(summary, dirs=4)
        assert "1 more directory." in str(format_installed_deps_disclosure(one_more))


# ---------------------------------------------------------------------------
# Hand-written project-manifest walks
# ---------------------------------------------------------------------------


class TestManifestWalks:
    def test_walks_into_refuses_content_claimed_dirs(self, tmp_path: Path) -> None:
        _mix_project(tmp_path)
        set_active_trace_deps(TraceDeps(everything=True), tmp_path)
        assert not walks_into(tmp_path / "deps")  # tracing does not make it the project's
        assert walks_into(tmp_path / "lib")

    def test_manifest_walks_still_skip_dependency_names(self) -> None:
        skip = manifest_walk_skip()
        assert {"node_modules", "vendor"} <= skip
        assert set(DEFAULT_EXCLUDES) <= skip

    def test_a_dependency_manifest_is_not_a_first_party_name(self, tmp_path: Path) -> None:
        from hypergumbo_core.supply_chain import collect_workspace_package_names

        _write(tmp_path / "pyproject.toml", '[project]\nname = "mine"\n')
        _write(tmp_path / "venv" / "pyvenv.cfg", "home = /usr\n")
        _write(
            tmp_path / "venv" / "lib" / "python3.12" / "site-packages" / "dep"
            / "pyproject.toml", '[project]\nname = "theirs"\n',
        )
        assert collect_workspace_package_names(tmp_path) == {"mine"}

    def test_derived_skipped_scan_does_not_enter_installed_source(self, tmp_path: Path) -> None:
        from hypergumbo_core.cli import _find_derived_skipped

        _mix_project(tmp_path)
        _write(tmp_path / "deps" / "foo" / "dist" / "bundle.js", "x")
        _write(tmp_path / "dist" / "app.js", "x")
        assert _find_derived_skipped(tmp_path) == ["dist/app.js"]

    def test_glob_scans_ask_the_content_rules(self, tmp_path: Path) -> None:
        from hypergumbo_core.discovery import under_content_rule_dir

        _mix_project(tmp_path)
        assert under_content_rule_dir(tmp_path, ("deps", "foo"))
        assert not under_content_rule_dir(tmp_path, ("lib",))

    def test_a_dependency_manifest_declares_no_framework(self, tmp_path: Path) -> None:
        """thc_dev: an installed ``deps/cowboy/mix.exs`` declared the cowboy
        framework for a project that never names it."""
        from hypergumbo_core.profile import _find_manifest_files

        _mix_project(tmp_path)
        _write(tmp_path / "deps" / "cowboy" / "mix.exs", "")
        _write(tmp_path / "apps" / "web" / "mix.exs", "")
        found = {p.relative_to(tmp_path).as_posix() for p in _find_manifest_files(tmp_path, "mix.exs")}
        assert found == {"mix.exs", "apps/web/mix.exs"}

    def test_requirements_scan_skips_a_virtualenv_named_env(self, tmp_path: Path) -> None:
        from hypergumbo_core.profile import _in_non_project_dir

        _write(tmp_path / "env" / "pyvenv.cfg", "home = /usr\n")
        assert _in_non_project_dir(tmp_path, ("env", "requirements"))
        assert _in_non_project_dir(tmp_path, ("node_modules",))
        assert not _in_non_project_dir(tmp_path, ("src", "requirements"))

    def test_sketch_structure_counts_skip_installed_source(self, tmp_path: Path) -> None:
        from hypergumbo_core.sketch import _count_dir_items, _format_structure_tree_fallback

        _mix_project(tmp_path)
        assert _count_dir_items(tmp_path, tmp_path, []) == 3  # lib, mix.exs, mix.lock
        tree = _format_structure_tree_fallback(tmp_path, list(DEFAULT_EXCLUDES))
        assert "lib/" in tree and "deps" not in tree

    def test_sketch_config_scans_skip_dependency_manifests(self, tmp_path: Path) -> None:
        """A depth-2 manifest under installed source (``deps/foo/mix.exs``) is
        not a monorepo sub-project, in either config scan."""
        from hypergumbo_core.sketch import _collect_config_content, _extract_config_heuristic

        _mix_project(tmp_path)
        _write(tmp_path / "deps" / "foo" / "mix.exs", (
            "defmodule Foo.MixProject do\n  def project, do: [app: :foo, version: \"9.9.9\"]\nend\n"
        ))
        _write(tmp_path / "deps" / "foo" / "package.json", '{"name": "foo-dep"}')
        _write(tmp_path / "apps" / "web" / "package.json", '{"name": "web"}')
        heuristic = "\n".join(_extract_config_heuristic(tmp_path))
        assert "deps/foo" not in heuristic and "apps/web" in heuristic
        content, _seen = _collect_config_content(tmp_path)
        names = [name for name, _ in content]
        assert not any(n.startswith("deps/") for n in names)
        assert "apps/web/package.json" in names

    def test_additional_files_still_name_exclude(self, tmp_path: Path) -> None:
        """The name-pattern arm still drops a default-excluded directory that no
        content rule claims (``dist/``)."""
        from hypergumbo_core.sketch import _select_additional_files

        _write(tmp_path / "README.md", "# app\n")
        _write(tmp_path / "dist" / "NOTES.md", "# built\n")
        _selected, candidates, _per_file, _deg = _select_additional_files(
            tmp_path, [], [], {},
        )
        names = {p.relative_to(tmp_path).as_posix() for p in candidates}
        assert "README.md" in names and "dist/NOTES.md" not in names

    def test_additional_files_skip_installed_source(self, tmp_path: Path) -> None:
        from hypergumbo_core.sketch import _select_additional_files

        _mix_project(tmp_path)
        _write(tmp_path / "README.md", "# app\n")
        _write(tmp_path / "deps" / "foo" / "README.md", "# foo\n")
        _selected, candidates, _per_file, _deg = _select_additional_files(
            tmp_path, [], [], {},
        )
        names = {p.relative_to(tmp_path).as_posix() for p in candidates}
        assert "README.md" in names and "deps/foo/README.md" not in names


# ---------------------------------------------------------------------------
# Tiering of what IS analysed
# ---------------------------------------------------------------------------


class TestTiering:
    @pytest.mark.parametrize("rel", [
        "src/runtime/vendor/github.com/a/b/b.go",
        "web/node_modules/react/index.js",
    ])
    def test_committed_vendor_trees_below_the_root_are_tier_3(
        self, tmp_path: Path, rel: str,
    ) -> None:
        from hypergumbo_core.supply_chain import Tier, classify_file

        f = _write(tmp_path / rel, "x")
        assert classify_file(f, tmp_path).tier == Tier.EXTERNAL_DEP

    def test_traced_virtualenv_source_is_tier_3_with_its_package(self, tmp_path: Path) -> None:
        from hypergumbo_core.supply_chain import Tier, classify_file

        _write(tmp_path / "venv" / "pyvenv.cfg", "home = /usr\n")
        site = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
        f = _write(site / "requests" / "api.py", "def get(): pass\n")
        loose = _write(site / "requests-2.0.dist-info" / "x.py", "x = 1\n")
        c = classify_file(f, tmp_path)
        assert c.tier == Tier.EXTERNAL_DEP
        assert c.reason == "in installed dependency source venv/"
        assert c.package_name == "requests"
        assert classify_file(loose, tmp_path).package_name is None


# ---------------------------------------------------------------------------
# End to end: the acceptance fixture through cli.main
# ---------------------------------------------------------------------------


def _survey(repo: Path, out: Path, *extra: str) -> dict[str, Any]:
    from hypergumbo_core.cli import main

    rc = main([
        "survey", str(repo), "--out", str(out),
        "--no-sketch-fan-out", "--no-handler-slices", *extra,
    ])
    assert rc == 0
    data: dict[str, Any] = json.loads(out.read_text())
    return data


@pytest.fixture
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


@pytest.mark.usefixtures("_isolated_cache")
class TestEndToEnd:
    def test_default_skips_discloses_and_stubs(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _mix_project(tmp_path / "app")
        bm = _survey(repo, tmp_path / "out.json")
        err = capsys.readouterr().err
        assert "Installed dependency source not analysed: deps/ (1 Mix package)" in err
        assert not [n for n in bm["nodes"] if n["path"].startswith("deps/")]
        calls = {
            (e["src"].split(":")[-2], e["dst"]) for e in bm["edges"] if e["type"] == "calls"
        }
        assert ("App.hi", "elixir:Foo:0-0:greet:external_symbol") in calls
        assert bm["supply_chain_summary"]["installed_deps_skipped"]["packages"] == 1
        # The flag is cleared when the command returns.
        assert discovery._trace_deps_override is None

    @pytest.mark.parametrize("spec", ["foo", "all"])
    def test_tracing_brings_deps_back_as_tier_3(
        self, tmp_path: Path, spec: str, capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _mix_project(tmp_path / "app")
        bm = _survey(repo, tmp_path / "out.json", "--trace-deps", spec)
        assert "Installed dependency source" not in capsys.readouterr().err
        dep_nodes = [n for n in bm["nodes"] if n["path"].startswith("deps/")]
        assert {n["name"] for n in dep_nodes} >= {"Foo", "Foo.greet"}
        assert all(n["supply_chain"]["tier"] == 3 for n in dep_nodes)
        assert any(
            e["type"] == "calls" and e["dst"].startswith("elixir:deps/foo/lib/foo.ex")
            and "Foo.greet" in e["dst"] for e in bm["edges"]
        )
        assert bm["supply_chain_summary"]["installed_deps_skipped"]["dirs"] == 0

    def test_committed_deps_are_analysed_by_default(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _mix_project(tmp_path / "app")
        _git_commit_all(repo)
        bm = _survey(repo, tmp_path / "out.json")
        assert "Installed dependency source" not in capsys.readouterr().err
        dep_nodes = [n for n in bm["nodes"] if n["path"].startswith("deps/")]
        assert dep_nodes and all(n["supply_chain"]["tier"] == 3 for n in dep_nodes)

    def test_the_sketch_carries_the_line(self, tmp_path: Path) -> None:
        from hypergumbo_core.sketch import generate_sketch

        repo = _mix_project(tmp_path / "app")
        bm = _survey(repo, tmp_path / "out.json")
        sketch = generate_sketch(repo, max_tokens=2000, cached_results=bm)
        assert "Installed dependency source not analysed: deps/ (1 Mix package)" in sketch
        bm["supply_chain_summary"]["installed_deps_skipped"] = {
            "dirs": 0, "packages": 0, "entries": [],
        }
        assert "Installed dependency source" not in generate_sketch(
            repo, max_tokens=2000, cached_results=bm,
        )

    def test_mixed_keywords_are_a_usage_error(self, tmp_path: Path) -> None:
        from hypergumbo_core.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["survey", str(tmp_path), "--trace-deps", "all,foo"])
        assert exc.value.code == 2
