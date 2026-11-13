# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file the repository's VCS ignores and does not track is not analysed.

ADR-0004 §"Installed dependency source" point 9 (WI-jolov). A working tree
holds files a clean checkout of the same commit does not: local outputs,
editor state, and secrets files (``.env``, ``credentials.json``,
``terraform.tfvars``). Discovery had no rule for them. Measured before the
rule, on a repository of 5 tracked files and 248 ignored ones: 134 of the
248 had graph nodes, and a sketch printed the content of up to 107.

The rule is the VCS's own answer, never a list of names: ignored by the
work tree's rules (``git check-ignore``) AND absent from HEAD. It is applied
by discovery, for every consumer, and what it left out is disclosed.

The tests drive the public entry points -- ``FileIndex.build``,
``is_excluded``, ``find_files``, ``walks_into``, ``cli.main`` and
``generate_sketch`` -- on repositories built in ``tmp_path``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterator

import pytest

from hypergumbo_core import discovery
from hypergumbo_core.discovery import (
    FileIndex,
    TraceDeps,
    find_files,
    format_vcs_ignored_disclosure,
    is_excluded,
    refresh_vcs_ignored,
    set_active_trace_deps,
    vcs_ignored,
    vcs_ignored_summary,
    walks_into,
)

_GIT = shutil.which("git") or "git"
_AS = ["-c", "user.name=t", "-c", "user.email=t@t"]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run([_GIT, *_AS, *args], cwd=cwd, check=True, capture_output=True)


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _repo(root: Path, committed: dict[str, str], ignore: str,
          left: dict[str, str] | None = None, commit: bool = True) -> Path:
    """A git work tree: ``committed`` and a ``.gitignore`` holding ``ignore``
    are committed (force-added, so a committed file may match an ignore
    rule); ``left`` is written afterwards and never added."""
    root.mkdir(parents=True, exist_ok=True)
    _write(root, {**committed, ".gitignore": ignore})
    _git(root, "init", "-q")
    if commit:
        _git(root, "add", "-f", "--", ".gitignore", *committed)
        _git(root, "commit", "-qm", "c")
    _write(root, left or {})
    return root


def _kept(root: Path) -> set[str]:
    """The files the three walkers keep, as root-relative paths. They must
    agree: a rule one walker applies and another does not gives one input
    two answers."""
    by_index = {f.relative_to(root).as_posix() for f in FileIndex.build(root).all_files()}
    every = [p for p in root.rglob("*")
             if p.is_file() and ".git" not in p.relative_to(root).parts]
    by_is_excluded = {p.relative_to(root).as_posix() for p in every
                      if not is_excluded(p, root)}
    by_find_files = {p.relative_to(root).as_posix() for p in find_files(root, ["*"])}
    assert by_index == by_is_excluded == by_find_files
    return by_index


@pytest.fixture(autouse=True)
def _no_remembered_answers() -> Iterator[None]:
    """Each test asks git itself."""
    discovery._vcs_ignored_cache.clear()
    yield
    discovery._vcs_ignored_cache.clear()
    set_active_trace_deps(TraceDeps(), None)


# ── the rule ────────────────────────────────────────────────────────────────

class TestIgnoredAndUntrackedIsNotAnalysed:
    def test_the_secrets_case(self, tmp_path: Path) -> None:
        """Files of the kinds a sketch used to quote in full. No name here is
        known to hypergumbo; the repository's own ignore rules are."""
        root = _repo(tmp_path / "r", {"src/app.py": "x = 1\n", "config.yaml": "a: 1\n"},
                     "config/secrets.yml\ncredentials.json\n*.tfvars\n*.local.toml\n"
                     "notes.md\n.env\n",
                     {"config/secrets.yml": "k: v\n", "credentials.json": "{}\n",
                      "terraform.tfvars": "k = 1\n", "settings.local.toml": "k = 1\n",
                      "notes.md": "n\n", ".env": "K=V\n", "src/new.py": "y = 2\n"})
        assert _kept(root) == {".gitignore", "config.yaml", "src/app.py", "src/new.py"}

    def test_a_committed_file_is_analysed_whatever_rule_matches_it(self, tmp_path: Path) -> None:
        """Force-added on purpose (ADR-0004 point 3: a tracked file is real).
        Its untracked sibling under the same rule is left out."""
        root = _repo(tmp_path / "r", {"gen/schema.py": "a = 1\n", "app.py": "x = 1\n"},
                     "gen/\n", {"gen/scratch.py": "b = 2\n", "gen/deep/more.py": "c = 3\n"})
        assert _kept(root) == {".gitignore", "app.py", "gen/schema.py"}

    def test_tracked_means_committed_not_staged(self, tmp_path: Path) -> None:
        """HEAD is what a clone holds. The index is never read (ADR-0004
        point 3: reading it can run a program the repository names), so an
        ignored file that is only staged is still left out."""
        root = _repo(tmp_path / "r", {"app.py": "x = 1\n"}, "*.gen.py\n",
                     {"staged.gen.py": "y = 2\n"})
        _git(root, "add", "-f", "staged.gen.py")
        assert _kept(root) == {".gitignore", "app.py"}
        _git(root, "commit", "-qm", "now it is the project's")
        assert _kept(root) == {".gitignore", "app.py", "staged.gen.py"}

    def test_an_untracked_file_no_rule_ignores_is_analysed(self, tmp_path: Path) -> None:
        """New work not yet added. Untracked alone is no evidence."""
        root = _repo(tmp_path / "r", {"app.py": "x = 1\n"}, "*.log\n",
                     {"new_module.py": "y = 2\n", "run.log": "l\n"})
        assert _kept(root) == {".gitignore", "app.py", "new_module.py"}

    def test_the_answer_is_gits_not_a_pattern_match(self, tmp_path: Path) -> None:
        """Negation, an anchored rule, a rule in a nested ``.gitignore`` and
        one in ``.git/info/exclude``: each needs git's own reading."""
        root = _repo(tmp_path / "r", {"app.py": "x = 1\n", "sub/.gitignore": "local_*\n"},
                     "*.log\n!keep.log\n/only_at_root.txt\n",
                     {"a.log": "l\n", "keep.log": "l\n", "only_at_root.txt": "t\n",
                      "sub/only_at_root.txt": "t\n", "sub/local_notes.txt": "t\n",
                      "private.txt": "t\n", "sp ace/ünï.log": "l\n"})
        (root / ".git" / "info").mkdir(exist_ok=True)
        (root / ".git" / "info" / "exclude").write_text("private.txt\n")
        assert _kept(root) == {".gitignore", "app.py", "keep.log", "sub/.gitignore",
                               "sub/only_at_root.txt"}

    def test_an_ignored_directory_is_never_walked(
            self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """One question per directory level. What lies below an ignored
        directory is not listed, let alone asked about."""
        root = _repo(tmp_path / "r", {"app.py": "x = 1\n", "lib/a/b/c.py": "y = 1\n"},
                     "cache/\n", {f"cache/{i}/{j}.bin": "" for i in range(5) for j in range(5)})
        calls: list[list[str]] = []
        real = discovery._repo_git

        def counted(cwd: Path, args: list[str],
                    stdin: bytes | None = None) -> tuple[int, bytes] | None:
            calls.append([args[0], *([os.fsdecode(stdin)] if stdin else [])])
            return real(cwd, args, stdin)

        monkeypatch.setattr(discovery, "_repo_git", counted)
        index = FileIndex.build(root)
        asked = [c for c in calls if c[0] == "check-ignore"]
        assert len(asked) == 4  # root, then lib/, lib/a/, lib/a/b/ -- cache/ never
        assert not any("cache/" in c[1] for c in asked)
        assert index.vcs_ignored_skipped == [(root / "cache", True)]

    def test_a_file_ignored_since_the_last_index_is_left_out_of_the_next(
            self, tmp_path: Path) -> None:
        root = _repo(tmp_path / "r", {"app.py": "x = 1\n"}, "*.secret\n")
        assert _kept(root) == {".gitignore", "app.py"}
        _write(root, {"api.secret": "k\n"})
        assert root / "api.secret" not in FileIndex.build(root).all_files()

    def test_a_symlinked_directory_is_asked_about_not_followed(self, tmp_path: Path) -> None:
        root = _repo(tmp_path / "r", {"app.py": "x = 1\n", "real/m.py": "y = 1\n"}, "link\n")
        (root / "link").symlink_to(root / "real", target_is_directory=True)
        refresh_vcs_ignored(root)
        assert vcs_ignored(root / "link", root)
        assert not vcs_ignored(root / "real" / "m.py", root)


# ── where the answer comes from ─────────────────────────────────────────────

class TestWhichVcsIsAsked:
    def test_no_vcs_excludes_nothing(self, tmp_path: Path) -> None:
        """A tarball: a ``.gitignore`` with no git behind it is a text file.
        Nothing reads it as a rule, and no git process is started."""
        _write(tmp_path, {".gitignore": "*.secret\n", "app.py": "x = 1\n", "api.secret": "k\n"})
        assert _kept(tmp_path) == {".gitignore", "app.py", "api.secret"}
        assert refresh_vcs_ignored(tmp_path) is None

    def test_a_work_tree_git_cannot_read_excludes_nothing(self, tmp_path: Path) -> None:
        _write(tmp_path, {".gitignore": "*.secret\n", "app.py": "x = 1\n", "api.secret": "k\n"})
        (tmp_path / ".git").mkdir()
        assert _kept(tmp_path) == {".gitignore", "app.py", "api.secret"}

    def test_before_the_first_commit_nothing_is_exempt(self, tmp_path: Path) -> None:
        """HEAD holds nothing yet, so the ignore rules alone decide."""
        root = _repo(tmp_path / "r", {"app.py": "x = 1\n"}, "*.secret\n",
                     {"api.secret": "k\n"}, commit=False)
        assert _kept(root) == {".gitignore", "app.py"}

    def test_a_subdirectory_is_ruled_by_its_work_tree(self, tmp_path: Path) -> None:
        """``hypergumbo sketch ./service``: the rules above the analysed
        directory apply, and HEAD's files are found from below."""
        root = _repo(tmp_path / "r",
                     {"service/app.py": "x = 1\n", "service/kept.secret": "k\n"},
                     "*.secret\n", {"service/api.secret": "k\n"})
        assert _kept(root / "service") == {"app.py", "kept.secret"}

    def test_a_directory_the_user_names_is_analysed_though_ignored(
            self, tmp_path: Path) -> None:
        """An explicit request outranks the rule: nothing below a named,
        ignored directory is left out."""
        root = _repo(tmp_path / "r", {"app.py": "x = 1\n"}, "scratch/\n*.secret\n",
                     {"scratch/tool.py": "y = 1\n", "scratch/api.secret": "k\n"})
        assert _kept(root / "scratch") == {"tool.py", "api.secret"}
        assert _kept(root) == {".gitignore", "app.py"}
        # In either order: the answer remembered for the repository must not
        # be given for the directory named inside it.
        assert not is_excluded(root / "scratch" / "tool.py", root / "scratch")
        assert is_excluded(root / "scratch" / "tool.py", root)

    def test_a_nested_work_tree_answers_for_its_own_files(self, tmp_path: Path) -> None:
        """Its rules and its HEAD: a file it committed past its own rule is
        analysed, and the outer rules do not reach in... except through the
        outer ``.gitignore`` files git itself reads on the way down."""
        root = _repo(tmp_path / "r", {"app.py": "x = 1\n"}, "*.outer\n")
        _repo(root / "inner", {"lib.py": "y = 1\n", "kept.tmp": "k\n"}, "*.tmp\n",
              {"scratch.tmp": "s\n", "new.py": "z = 1\n"})
        kept = _kept(root)
        assert {"inner/lib.py", "inner/kept.tmp", "inner/new.py"} <= kept
        assert "inner/scratch.tmp" not in kept

    def test_a_nested_work_tree_git_cannot_read_excludes_nothing_in_it(
            self, tmp_path: Path) -> None:
        """A ``.git`` file naming a git directory that is not there (a
        submodule checkout whose superproject moved): git refuses to answer
        from inside it, and no other tree's rules are applied to it."""
        root = _repo(tmp_path / "r", {"app.py": "x = 1\n"}, "*.tmp\n", {"a.tmp": "t\n"})
        _write(root, {"broken/b.tmp": "t\n", "broken/lib.py": "y = 1\n",
                      "broken/.git": "gitdir: /nowhere/at/all\n"})
        kept = _kept(root)
        assert "broken/b.tmp" in kept and "a.tmp" not in kept

    def test_a_directory_git_does_not_take_for_a_work_tree_belongs_to_the_outer_one(
            self, tmp_path: Path) -> None:
        """An empty ``.git`` directory is not a repository to git, which
        answers for the files beside it from the tree above. Git's answer is
        the rule, so that is what happens."""
        root = _repo(tmp_path / "r", {"app.py": "x = 1\n", "odd/kept.tmp": "k\n"}, "*.tmp\n",
                     {"odd/b.tmp": "t\n", "odd/lib.py": "y = 1\n"})
        (root / "odd" / ".git").mkdir()
        assert _kept(root) == {".gitignore", "app.py", "odd/kept.tmp", "odd/lib.py"}

    def test_a_relative_root_gets_the_same_answer(
            self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _repo(tmp_path / "r", {"src/app.py": "x = 1\n"}, "*.secret\n",
                     {"src/api.secret": "k\n"})
        monkeypatch.chdir(root)
        assert [f.as_posix() for f in FileIndex.build(Path("src")).all_files()] == ["src/app.py"]
        assert is_excluded(Path("src/api.secret"), Path("."))


# ── installed dependency source keeps its own rules ─────────────────────────

class TestInstalledDependencySourceIsNotThisRulesBusiness:
    def _node_project(self, root: Path) -> Path:
        return _repo(root, {"package.json": '{"name": "app"}\n', "index.js": "x = 1\n"},
                     "node_modules/\n",
                     {"node_modules/dep/package.json": '{"name": "dep"}\n',
                      "node_modules/dep/index.js": "module.exports = 1\n"})

    def test_untraced_it_is_disclosed_as_installed_source(self, tmp_path: Path) -> None:
        root = self._node_project(tmp_path / "r")
        index = FileIndex.build(root)
        assert root / "node_modules/dep/index.js" not in index.all_files()
        assert [p for p, _rule in index.installed_dep_roots] == [root / "node_modules"]
        assert index.vcs_ignored_skipped == []

    def test_traced_it_is_analysed_though_git_ignores_it(self, tmp_path: Path) -> None:
        """``--trace-deps`` is the user's explicit request (ADR-0004 point 5)."""
        root = self._node_project(tmp_path / "r")
        set_active_trace_deps(TraceDeps.parse("all"), root)
        assert {"node_modules/dep/index.js", "node_modules/dep/package.json"} <= _kept(root)


# ── the walks that are neither the index nor is_excluded ────────────────────

class TestOtherWalksAskTheSameRule:
    def test_a_manifest_walk_does_not_enter_an_ignored_directory(self, tmp_path: Path) -> None:
        root = _repo(tmp_path / "r", {"app/pyproject.toml": "[project]\n"}, "scratch/\n",
                     {"scratch/pyproject.toml": "[project]\n"})
        assert walks_into(root / "app")
        assert not walks_into(root / "scratch")

    def test_a_walk_that_names_no_root_finds_the_answer_above_it(self, tmp_path: Path) -> None:
        """Top-down, the first question is about the root's children; later
        ones, deeper, reuse that answer instead of asking per directory."""
        root = _repo(tmp_path / "r", {"a/b/app.py": "x = 1\n"}, "*.secret\n",
                     {"a/b/api.secret": "k\n"})
        assert not vcs_ignored(root / "a")
        assert list(discovery._vcs_ignored_cache) == [str(root)]
        assert vcs_ignored(root / "a" / "b" / "api.secret")
        assert list(discovery._vcs_ignored_cache) == [str(root)]

    def test_a_path_outside_the_root_is_not_this_roots_to_answer(self, tmp_path: Path) -> None:
        root = _repo(tmp_path / "r", {"app.py": "x = 1\n"}, "*.secret\n", {"api.secret": "k\n"})
        _write(tmp_path, {"elsewhere/api.secret": "k\n"})
        assert vcs_ignored(root / "api.secret", root)
        assert not vcs_ignored(tmp_path / "elsewhere" / "api.secret", root)

    def test_no_answer_above_says_nothing_about_a_work_tree_below(self, tmp_path: Path) -> None:
        """``tmp_path`` was asked and has no VCS; the repository inside it
        does, and is asked for itself."""
        assert not vcs_ignored(tmp_path / "x")
        assert not vcs_ignored(tmp_path / "x")  # the remembered "no answer"
        root = _repo(tmp_path / "r", {"app.py": "x = 1\n"}, "*.secret\n", {"api.secret": "k\n"})
        assert vcs_ignored(root / "api.secret")
        assert is_excluded(root / "api.secret", root)

    def test_remembered_answers_are_bounded(self, tmp_path: Path) -> None:
        for i in range(256):
            discovery._vcs_ignored_cache[f"/nowhere/{i}"] = None
        refresh_vcs_ignored(tmp_path)
        assert list(discovery._vcs_ignored_cache) == [str(tmp_path)]


# ── a manifest git ignores declares nothing for the project ─────────────────

#: One row per place the profile finds a manifest by name or glob: the
#: manifest, its content, a committed file it needs beside it, and a
#: framework that content declares.
_MANIFESTS = [
    ("package.json", '{"name": "x", "dependencies": {"express": "4"}}', None, "express"),
    ("server/package.json", '{"name": "x", "dependencies": {"express": "4"}}', None, "express"),
    ("requirements.txt", "flask\n", None, "flask"),
    ("requirements/dev.txt", "django\n", None, "django"),
    ("Cargo.toml", '[package]\nname = "x"\n[dependencies]\naxum = "0.7"\n', None, "axum"),
    ("go.mod", "module x\nrequire github.com/gin-gonic/gin v1.9.0\n", None, "gin"),
    ("Gemfile", 'source "https://rubygems.org"\ngem "rails"\n', None, "rails"),
    ("gradle/deps.gradle",
     "dependencies { implementation "
     "'org.springframework.boot:spring-boot-starter-web:3.0.0' }\n",
     "build.gradle", "spring-boot"),
    ("project/Deps.scala", 'object Deps { val a = "com.typesafe.play" %% "play" % "2.8" }\n',
     "build.sbt", "play"),
    ("app.cabal", "name: x\nbuild-depends: base, yesod\n", None, "yesod"),
    ("x-1.0-1.rockspec", 'package = "x"\ndependencies = { "lapis" }\n', None, "lapis"),
    ("app.pro", "QT += core gui widgets\n", None, "qt"),
    ("App.fsproj",
     '<Project><ItemGroup><PackageReference Include="Giraffe" Version="6" />'
     "</ItemGroup></Project>\n", None, "giraffe"),
    ("App.csproj",
     '<Project><ItemGroup><PackageReference Include="Microsoft.AspNetCore.Mvc" '
     'Version="2" /></ItemGroup></Project>\n', None, "aspnetcore"),
    ("x.opam", 'depends: [ "dream" ]\n', None, "dream"),
    ("x.nimble", 'requires "jester >= 0.5"\n', None, "jester"),
]


class TestAnIgnoredManifestDeclaresNothing:
    @pytest.mark.parametrize(("rel", "text", "beside", "framework"), _MANIFESTS,
                             ids=[row[0] for row in _MANIFESTS])
    def test_framework_detection(
            self, tmp_path: Path, rel: str, text: str, beside: str | None,
            framework: str) -> None:
        """Ignored and untracked, the manifest declares no framework.
        Committed past the same ignore rule, it does: the second half is
        what shows the first is the rule's doing and not a manifest the
        profile never read."""
        from hypergumbo_core.profile import detect_profile

        committed = {"src/app.py": "x = 1\n", **({beside: "\n"} if beside else {})}
        ignored = _repo(tmp_path / "ignored", committed, f"/{rel}\n", {rel: text})
        assert detect_profile(ignored).frameworks == []
        tracked = _repo(tmp_path / "tracked", {**committed, rel: text}, f"/{rel}\n")
        assert framework in detect_profile(tracked).frameworks

    def test_package_root_detection(self, tmp_path: Path) -> None:
        """The walk that reads each package's own name (tier classification)
        does not take a name from a manifest git ignores."""
        from hypergumbo_core.supply_chain import _manifest_files

        root = _repo(tmp_path / "r", {"a/pyproject.toml": "[project]\n"}, "/b/pyproject.toml\n",
                     {"b/pyproject.toml": "[project]\n", "b/lib.py": "x = 1\n"})
        assert list(_manifest_files(root, {"pyproject.toml"})) == [root / "a" / "pyproject.toml"]


# ── each sketch reader that finds a file past discovery ─────────────────────

def _both(tmp_path: Path, files: dict[str, str],
          rules: str | None = None) -> tuple[Path, Path]:
    """Two repositories holding ``files``: in the first git ignores them and
    does not track them, in the second they are committed past the same
    rules. A reader must take nothing from the first; that it takes
    something from the second shows it reads these files at all."""
    rules = rules or "".join(f"/{rel}\n" for rel in files)
    base = {"src/app.py": "def run():\n    return 1\n"}
    return (_repo(tmp_path / "ignored", base, rules, files),
            _repo(tmp_path / "tracked", {**base, **files}, rules))


_MIT = "MIT License\n\nPermission is hereby granted, free of charge, to any person\n"


class TestEachSketchReaderAsksTheRule:
    """The sketch sections that open a file by name or glob. An end-to-end
    sketch reaches only some of them (the rest need an embedding model, a
    compiled project, or a run with no file index), so each is driven here."""

    @pytest.fixture(autouse=True)
    def _no_file_index(self) -> Iterator[None]:
        discovery.set_file_index(None)
        yield
        discovery.set_file_index(None)

    def test_configuration_lines(self, tmp_path: Path) -> None:
        from hypergumbo_core.sketch import _extract_config_heuristic

        ignored, tracked = _both(tmp_path, {
            "package.json": '{"name": "tok-root"}', "pkg/a/package.json": '{"name": "tok-deep"}',
            "LICENSE": _MIT, "server/LICENSE": "Apache License\nVersion 2.0, January 2004\n"})
        assert _extract_config_heuristic(ignored) == []
        lines = "\n".join(_extract_config_heuristic(tracked))
        assert "tok-root" in lines and "tok-deep" in lines
        assert "MIT" in lines and "Apache" in lines

    def test_configuration_content(self, tmp_path: Path) -> None:
        """The embedding mode's raw material: every manifest's whole text."""
        from hypergumbo_core.sketch import _collect_config_content

        ignored, tracked = _both(tmp_path, {
            "package.json": '{"name": "tok-root"}', "pkg/a/package.json": '{"name": "tok-deep"}'})
        assert _collect_config_content(ignored) == ([], set())
        text = "".join(content for _name, content in _collect_config_content(tracked)[0])
        assert "tok-root" in text and "tok-deep" in text

    def test_configuration_content_found_by_glob(self, tmp_path: Path) -> None:
        from hypergumbo_core.sketch import _collect_config_content_with_discovery

        ignored, tracked = _both(tmp_path, {"prod.tfvars": 'db_password = "tok-secret"\n'})
        assert _collect_config_content_with_discovery(ignored, use_discovery=False) == []
        found = _collect_config_content_with_discovery(tracked, use_discovery=False)
        assert [name for name, _content in found] == ["prod.tfvars"]

    def test_test_framework_markers(self, tmp_path: Path) -> None:
        from hypergumbo_core.sketch import _detect_test_frameworks_from_markers

        ignored, tracked = _both(tmp_path, {"pytest.ini": "[pytest]\n"})
        assert _detect_test_frameworks_from_markers(ignored) == set()
        assert _detect_test_frameworks_from_markers(tracked) == {"pytest"}

    def test_structure_listing(self, tmp_path: Path) -> None:
        from hypergumbo_core.sketch import _format_structure_tree_fallback, _kept_dir_children

        ignored, tracked = _both(
            tmp_path, {"scratch/tool.py": "x = 1\n", "notes.md": "n\n", "src/local.py": "y = 2\n"},
            rules="/scratch/\n/notes.md\n/src/local.py\n")
        assert _kept_dir_children(ignored / "src", ignored, []) == [("app.py", False)]
        assert ("local.py", False) in _kept_dir_children(tracked / "src", tracked, [])
        tree = _format_structure_tree_fallback(ignored, [])
        assert "scratch" not in tree and "notes.md" not in tree and "src" in tree
        tree = _format_structure_tree_fallback(tracked, [])
        assert "scratch" in tree and "notes.md" in tree

    def test_important_files(self, tmp_path: Path) -> None:
        from hypergumbo_core.sketch import _collect_important_files

        ignored, tracked = _both(tmp_path, {
            "package.json": "{}", "main.py": "x = 1\n", "handbook/guide.md": "# g\n"})

        def important(root: Path) -> list[str]:
            return _collect_important_files(root, [], [], [], [], {})

        assert important(ignored) == []
        assert set(important(tracked)) == {"package.json", "main.py", "handbook/guide.md"}

    def test_a_documentation_site_link(self, tmp_path: Path) -> None:
        from hypergumbo_core.sketch import _resolve_pages_url

        ignored, tracked = _both(tmp_path, {"docs/guide.md": "# g\n"})
        url = "https://example.github.io/repo/guide"
        assert _resolve_pages_url(url, "repo", ignored) is None
        assert _resolve_pages_url(url, "repo", tracked) == tracked / "docs/guide.md"

    def test_additional_files(self, tmp_path: Path) -> None:
        from hypergumbo_core.sketch import _select_additional_files

        ignored, tracked = _both(tmp_path, {"notes.md": "# n\n", "settings.toml": "k = 1\n"})

        def selected(root: Path) -> set[str]:
            files = _select_additional_files(root, [root / "src/app.py"], [], {})[0]
            return {f.relative_to(root).as_posix() for f in files}

        assert selected(ignored) == set()
        assert selected(tracked) == {"notes.md", "settings.toml"}

    @pytest.mark.parametrize(("rel", "text"), [
        ("meson.build", "executable('tokbin', 'main.c')\n"),
        ("Makefile", "TARGET = tokbin\n"),
        ("CMakeLists.txt", "add_executable(tokbin main.c)\n"),
        ("Cargo.toml", '[package]\nname = "tokbin"\n'),
        ("go.mod", "module example.com/tokbin\n"),
        ("configure.ac", "AC_INIT([tokbin], [1.0])\n"),
    ])
    def test_binary_names(self, tmp_path: Path, rel: str, text: str) -> None:
        from hypergumbo_core.sketch import _detect_project_binary_names

        ignored, tracked = _both(tmp_path, {rel: text})
        assert _detect_project_binary_names(ignored) == []
        assert _detect_project_binary_names(tracked) == ["tokbin"]

    def test_binary_name_from_compiled_source_at_the_root(self, tmp_path: Path) -> None:
        from hypergumbo_core.sketch import _detect_project_binary_names

        ignored, tracked = _both(tmp_path, {"main.c": "int main(void) { return 0; }\n"})
        assert _detect_project_binary_names(ignored) == []
        assert _detect_project_binary_names(tracked) == ["tracked"]


# ── disclosure ──────────────────────────────────────────────────────────────

class TestWhatWasLeftOutIsNamed:
    def test_the_walk_records_each_entry_it_met(self, tmp_path: Path) -> None:
        root = _repo(tmp_path / "r", {"app.py": "x = 1\n"}, "out/\n*.secret\n",
                     {"out/a/b.json": "{}\n", "out/c.json": "{}\n", "api.secret": "k\n"})
        summary = vcs_ignored_summary(FileIndex.build(root).vcs_ignored_skipped, root)
        assert summary == {"paths": 2, "entries": ["api.secret", "out/"]}
        assert format_vcs_ignored_disclosure(summary) == (
            "Not analysed, because git ignores them and does not track them: "
            "api.secret, out/ (ADR-0004)."
        )

    def test_nothing_left_out_says_nothing(self) -> None:
        assert format_vcs_ignored_disclosure(None) is None
        assert format_vcs_ignored_disclosure({"paths": 0, "entries": []}) is None

    def test_a_long_list_names_three_and_counts_the_rest(self, tmp_path: Path) -> None:
        root = _repo(tmp_path / "r", {"app.py": "x = 1\n"}, "*.secret\n",
                     {f"k{i:02d}.secret": "k\n" for i in range(14)})
        summary = vcs_ignored_summary(FileIndex.build(root).vcs_ignored_skipped, root)
        assert summary["paths"] == 14 and len(summary["entries"]) == 10
        assert format_vcs_ignored_disclosure(summary) == (
            "Not analysed, because git ignores them and does not track them: "
            "k00.secret, k01.secret, k02.secret, 11 more (ADR-0004)."
        )


# ── end to end: nothing of an ignored file reaches a survey or a sketch ─────

#: Each non-JSON ignored file holds its token in every form a reader of that
#: file looks for, so a section that reads it prints the token.
_FORMS = (
    '[package]\nname = "{t}"\nversion = "9.9"\n[project]\nname = "{t}"\n'
    "TARGET = {t}\nexecutable('{t}', 'main.c')\nadd_executable({t} main.c)\n"
    "module example.com/{t}\nAC_INIT([{t}], [1.0])\nname: {t}\n{t}\n"
)


def _token_text(rel: str, token: str) -> str:
    if rel.endswith(".json"):
        return json.dumps({"name": token, "main": "x.js",
                           "dependencies": {f"{token}dep": "1.0"}})
    if rel.endswith(".py"):
        return f'def {token.lower()}():\n    """{token} doc."""\n    return "{token}"\n'
    if rel.endswith(".md"):
        return f"# {token}\n\n{token} explains `serve` and `load` at length.\n"
    return _FORMS.format(t=token)


def _ignored_names() -> list[str]:
    """Every file name a sketch section looks for by name or glob, read from
    the code's own tables, plus the kinds no table names."""
    from hypergumbo_core import sketch

    by_name = [n.replace("*", "local") for n in sketch.CONFIG_FILES]
    by_name += sketch.LICENSE_FILES
    by_name += [glob.replace("*", "local") for glob, _fw, _req in sketch._TEST_FRAMEWORK_MARKERS]
    by_name += ["makefile", "GNUmakefile", "webpack.config.js", "vite.config.ts",
                ".env.example", "README.local.md"]
    other = ["config/secrets.yml", "credentials.json", "settings.local.toml", "notes.md",
             ".env", "private_module.py", "docs/private.md", "data.csv", "schema.sql",
             "scratch/deep/tool.py", "scratch/README.md", "linked/README.md"]
    return list(dict.fromkeys(by_name + other))


_COMMITTED = {
    "README.md": ("# Demo\n\nA small service. See the [guide](docs/guide.md), the "
                  "[private notes](docs/private.md) and [more](linked/).\n"),
    "docs/guide.md": "How `serve` calls `load`.\n",
    "src/app.py": "def load():\n    return 1\n\n\ndef serve():\n    return load()\n",
    "config.yaml": "port: 8080\n",
    "pyproject.toml": '[project]\nname = "demo"\nversion = "1.0"\n',
}


class _Run:
    def __init__(self, repo: Path, out: Path) -> None:
        from hypergumbo_core.cli import main
        from hypergumbo_core.sketch import generate_sketch

        assert main(["survey", str(repo), "--out", str(out), "--no-handler-slices"]) == 0
        self.survey_text = out.read_text()
        self.survey: dict[str, Any] = json.loads(self.survey_text)
        self.sketches = {
            (budget, source): generate_sketch(
                repo, max_tokens=budget, cached_results=self.survey, with_source=source)
            for budget in (2000, 16000) for source in (False, True)
        }


@pytest.fixture(scope="module")
def trees(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    """One commit, twice: ``dirty`` with 200-odd ignored, untracked files
    beside it, ``clean`` without. Both directories are named ``repo``, so a
    sketch's title is the same."""
    work = tmp_path_factory.mktemp("jolov")
    ignored: dict[str, str] = {}
    for prefix in ("", "server/", "pkg/a/"):
        for name in _ignored_names():
            rel = prefix + name
            if rel not in _COMMITTED:
                ignored[rel] = f"FAKEIGNORED{len(ignored):04d}"
    rules = "".join(f"/{rel}\n" for rel in ignored if "scratch/" not in rel) + "scratch/\n"
    dirty = _repo(work / "dirty" / "repo", _COMMITTED, rules,
                  {rel: _token_text(rel, token) for rel, token in ignored.items()})
    clean = _repo(work / "clean" / "repo", _COMMITTED, rules)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("XDG_CACHE_HOME", str(work / "cache"))
        patch.setenv("XDG_CONFIG_HOME", str(work / "xdg"))
        discovery._vcs_ignored_cache.clear()
        yield {
            "ignored": ignored,
            "dirty": _Run(dirty, work / "dirty.json"),
            "clean": _Run(clean, work / "clean.json"),
        }
        discovery.set_file_index(None)


def _without_disclosure(sketch: str) -> str:
    """The sketch less the line that names what was left out (and the blank
    line it stood on)."""
    kept = [line for line in sketch.split("\n")
            if not line.startswith("Not analysed, because git ignores")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept))


class TestNothingOfAnIgnoredFileReachesTheOutput:
    def test_the_fixture_reaches_every_reader(self, trees: dict[str, Any]) -> None:
        """Without this the assertions below could pass on a repository whose
        ignored files nothing would have read anyway."""
        ignored = trees["ignored"]
        assert len(ignored) > 200
        for name in ("package.json", "terraform.tfvars", "LICENSE", "Makefile",
                     "README.local.md", "server/Cargo.toml", "pkg/a/go.mod",
                     "config/secrets.yml", "private_module.py", "docs/private.md",
                     "scratch/deep/tool.py", "linked/README.md"):
            assert name in ignored
        assert trees["dirty"].survey["supply_chain_summary"]["vcs_ignored_skipped"]["paths"] > 200

    def test_no_content(self, trees: dict[str, Any]) -> None:
        run = trees["dirty"]
        for label, text in [("survey", run.survey_text), *run.sketches.items()]:
            assert not re.findall(r"FAKEIGNORED\d{4}", text, flags=re.I), label

    def test_no_graph_node(self, trees: dict[str, Any]) -> None:
        paths = {node["path"] for node in trees["dirty"].survey["nodes"]}
        assert "src/app.py" in paths
        assert not paths & set(trees["ignored"])
        assert paths == {node["path"] for node in trees["clean"].survey["nodes"]}

    def test_a_name_appears_only_in_the_disclosure(self, trees: dict[str, Any]) -> None:
        """A Structure or Additional Files entry naming an ignored file tells
        the reader it is part of the project."""
        run, ignored = trees["dirty"], trees["ignored"]
        for key, sketch in run.sketches.items():
            # The committed README links to an ignored file, and a section
            # that quotes the README quotes the link: the README's to say.
            body = _without_disclosure(sketch).replace("](docs/private.md)", "]()")
            named = [rel for rel in ignored
                     if re.search(rf"(?<![\w/.-]){re.escape(rel)}(?![\w/-])", body)]
            assert named == [], key
            assert "Not analysed, because git ignores them and does not track them: " in sketch

    def test_the_survey_names_what_it_left_out(self, trees: dict[str, Any]) -> None:
        bucket = trees["dirty"].survey["supply_chain_summary"]["vcs_ignored_skipped"]
        assert set(bucket) == {"paths", "entries"}
        assert len(bucket["entries"]) == 10 and bucket["entries"] == sorted(bucket["entries"])
        assert set(bucket["entries"]) <= set(trees["ignored"]) | {
            "scratch/", "server/scratch/", "pkg/a/scratch/"}
        assert trees["clean"].survey["supply_chain_summary"]["vcs_ignored_skipped"] == {
            "paths": 0, "entries": []}

    def test_a_working_tree_and_a_clean_checkout_give_the_same_sketch(
            self, trees: dict[str, Any]) -> None:
        """ADR-0004's reason for every rule in the section: a developer's
        checkout and a fresh clone of one commit agree."""
        for key, sketch in trees["dirty"].sketches.items():
            assert _without_disclosure(sketch) == _without_disclosure(
                trees["clean"].sketches[key]), key
        assert "Not analysed, because git" not in "".join(trees["clean"].sketches.values())

    def test_and_the_same_graph(self, trees: dict[str, Any]) -> None:
        def graph(run: _Run) -> list[dict[str, Any]]:
            return [{k: v for k, v in node.items() if k != "origin_run_id"}
                    for node in run.survey["nodes"]]

        assert graph(trees["dirty"]) == graph(trees["clean"])
        assert trees["dirty"].survey["profile"] == trees["clean"].survey["profile"]
