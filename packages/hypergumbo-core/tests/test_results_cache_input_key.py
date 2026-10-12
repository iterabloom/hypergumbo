# SPDX-License-Identifier: AGPL-3.0-or-later
"""The results-cache key carries the user inputs the analysis reads.

A cached survey is reused by ``sketch``, ``routes``, ``verify-claims`` and every
other command that reads a behavior map. Before the key carried these inputs, a
``frameworks.d`` file added, edited or broken after a survey was cached was
never seen by any of those commands: the cache answered, and the file was
never read. These tests pin the key against each input and pin the set of
inputs against what a survey actually reads.
"""

from __future__ import annotations

import collections
from pathlib import Path

import pytest

from hypergumbo_core import catalogue_home
from hypergumbo_core.catalogue_home import analysis_channel_token
from hypergumbo_core.user_config import config_cache_token
from hypergumbo_core.yaml_catalogs import (
    CHANNEL_STAGE_ANALYSIS,
    CHANNEL_STAGE_COMMAND,
    YAML_CATALOGS,
    CatalogSpec,
    validate_registry,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> "dict[str, Path]":
    """An isolated cache home, config home and repository.

    The other key components are pinned off, so an opt-in or override another
    test in this worker left behind cannot suffix the segment.
    """
    from hypergumbo_core import backend_selection, discovery

    monkeypatch.setattr(backend_selection, "resolved_backend_set", lambda **_kw: ())
    monkeypatch.setattr(discovery, "_trace_deps_override", None)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    repo = tmp_path / "repo"
    _write(repo / "app.py", "def f():\n    return 1\n")
    return {"repo": repo, "home": tmp_path / "xdg" / "hypergumbo"}


def _state_segment(repo: Path) -> str:
    from hypergumbo_core.sketch_embeddings import _get_results_cache_dir

    return _get_results_cache_dir(repo).parent.name


class TestNoUserInputsKeepsTheKey:
    def test_bare_state_hash_when_nothing_is_supplied(self, env) -> None:
        """A user with no channel files and no config keeps the old key."""
        segment = _state_segment(env["repo"])
        assert len(segment) == 16 and int(segment, 16) >= 0
        assert analysis_channel_token() == ""
        assert config_cache_token(env["repo"]) == ""

    def test_an_empty_channel_directory_keeps_the_key(self, env) -> None:
        plain = _state_segment(env["repo"])
        (env["home"] / "frameworks.d").mkdir(parents=True)
        assert _state_segment(env["repo"]) == plain

    def test_two_runs_share_one_key(self, env) -> None:
        _write(env["home"] / "frameworks.d" / "flask.yaml", "id: flask\n")
        assert _state_segment(env["repo"]) == _state_segment(env["repo"])


class TestAnalysisChannelsAreInTheKey:
    @pytest.mark.parametrize("family", sorted(
        s.directory for s in YAML_CATALOGS
        if s.channel_stage == CHANNEL_STAGE_ANALYSIS
    ))
    def test_adding_editing_and_breaking_a_file_moves_the_key(
        self, env, family: str,
    ) -> None:
        plain = _state_segment(env["repo"])
        path = _write(env["home"] / f"{family}.d" / "python.yaml", "a: 1\n")
        added = _state_segment(env["repo"])
        assert added.startswith(f"{plain}-cat-")
        path.write_text("a: 2\n")
        edited = _state_segment(env["repo"])
        path.write_text("key: [unclosed\n")
        broken = _state_segment(env["repo"])
        assert len({plain, added, edited, broken}) == 4

    def test_the_file_name_is_part_of_the_digest(self, env) -> None:
        """The loaders select a channel file by its stem, so a rename matters."""
        path = _write(env["home"] / "frameworks.d" / "flask.yaml", "id: x\n")
        before = analysis_channel_token()
        path.rename(path.with_name("django.yaml"))
        assert analysis_channel_token() != before

    def test_the_token_does_not_depend_on_where_the_home_is(
        self, tmp_path: Path,
    ) -> None:
        """Content and channel-relative name, not the absolute path."""
        tokens = []
        for name in ("one", "two"):
            home = tmp_path / name
            _write(home / "hypergumbo" / "frameworks.d" / "f.yaml", "id: f\n")
            tokens.append(analysis_channel_token(
                environ={"XDG_CONFIG_HOME": str(home)},
            ))
        assert tokens[0] == tokens[1] != ""

    def test_an_unreadable_file_still_gives_a_key(
        self, env, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = _write(env["home"] / "frameworks.d" / "f.yaml", "id: f\n")
        readable = analysis_channel_token()
        real = Path.read_bytes

        def deny(self: Path) -> bytes:
            if self == path:
                raise PermissionError(str(self))
            return real(self)

        monkeypatch.setattr(Path, "read_bytes", deny)
        unreadable = analysis_channel_token()
        assert unreadable not in ("", readable)


class TestCommandChannelsAreNotInTheKey:
    @pytest.mark.parametrize("family", sorted(
        s.directory for s in YAML_CATALOGS
        if s.channel_stage == CHANNEL_STAGE_COMMAND
    ))
    def test_a_command_stage_file_leaves_the_key(self, env, family: str) -> None:
        """Read over the cached graph each run, so it must not evict it."""
        plain = _state_segment(env["repo"])
        _write(env["home"] / f"{family}.d" / "python.yaml", "a: 1\n")
        assert _state_segment(env["repo"]) == plain


class TestConfigTiersAreInTheKey:
    def test_user_config_moves_the_key(self, env) -> None:
        plain = _state_segment(env["repo"])
        cfg = _write(env["home"] / "config.toml", "merge.prefer = [\"a\"]\n")
        first = _state_segment(env["repo"])
        assert first.startswith(f"{plain}-cfg-")
        cfg.write_text("merge.prefer = [\"b\"]\n")
        assert _state_segment(env["repo"]) not in (plain, first)

    def test_project_config_moves_the_key(self, env) -> None:
        """``.hypergumbo.toml`` is not a source file the state hash reads."""
        plain = _state_segment(env["repo"])
        _write(env["repo"] / ".hypergumbo.toml", "merge.corroborated_confidence = 0.9\n")
        assert _state_segment(env["repo"]).startswith(f"{plain}-cfg-")

    def test_a_broken_config_moves_the_key(self, env) -> None:
        """So the run that reports it is not skipped by a cache hit."""
        plain = _state_segment(env["repo"])
        _write(env["home"] / "config.toml", "not = [valid\n")
        assert _state_segment(env["repo"]) != plain

    def test_the_tiers_are_distinguished(self, env) -> None:
        user = _write(env["home"] / "config.toml", "x = 1\n")
        as_user = config_cache_token(env["repo"])
        user.unlink()
        _write(env["repo"] / ".hypergumbo.toml", "x = 1\n")
        assert config_cache_token(env["repo"]) not in ("", as_user)


class TestRegistryDeclaresTheStage:
    def test_every_channel_declares_a_stage(self) -> None:
        for spec in YAML_CATALOGS:
            if spec.user_channel is None:
                assert spec.channel_stage is None, spec.directory
            else:
                assert spec.channel_stage in (
                    CHANNEL_STAGE_ANALYSIS, CHANNEL_STAGE_COMMAND,
                ), spec.directory

    @pytest.mark.parametrize("stage,needle", [
        (None, "declares no channel_stage"),
        ("sometimes", "channel_stage 'sometimes'"),
    ])
    def test_validator_refuses_a_channel_without_a_stage(
        self, tmp_path: Path, stage: "str | None", needle: str,
    ) -> None:
        findings = validate_registry(tmp_path, catalogs=(CatalogSpec(
            directory="x", purpose="p", loader="hypergumbo_core.cli", adr=None,
            user_channel="x.d", no_channel_reason=None, channel_stage=stage,
        ),))
        assert any(needle in f for f in findings), findings

    def test_validator_refuses_a_stage_without_a_channel(
        self, tmp_path: Path,
    ) -> None:
        findings = validate_registry(tmp_path, catalogs=(CatalogSpec(
            directory="x", purpose="p", loader="hypergumbo_core.cli", adr=None,
            user_channel=None, no_channel_reason="internal",
            channel_stage=CHANNEL_STAGE_ANALYSIS,
        ),))
        assert any("channel_stage" in f for f in findings), findings

    def test_a_survey_reads_exactly_the_analysis_stage_channels(
        self, env, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Behavioural pin: what a survey READS is what the key DIGESTS.

        If a loader that runs during analysis starts reading a channel the
        registry declares command-stage, this fails, because that channel's
        edits would otherwise be masked by the cache again.
        """
        from hypergumbo_core.cli import main

        repo = env["repo"]
        _write(repo / "app.py", (
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "@app.route('/x')\n"
            "def x():\n"
            "    items = []\n"
            "    items.append(1)\n"
            "    return str(items)\n"
        ))
        _write(repo / "requirements.txt", "flask\n")
        _write(repo / "Main.java", (
            "public class Main { String a() { java.util.List<String> l = "
            "new java.util.ArrayList<>(); l.add(\"x\"); return l.get(0); } }\n"
        ))
        read: "collections.Counter[str]" = collections.Counter()
        real = catalogue_home.user_channel_files

        def spy(family: str, *a, **kw):
            read[family] += 1
            return real(family, *a, **kw)

        monkeypatch.setattr(catalogue_home, "user_channel_files", spy)
        # The loaders memoise per process; an earlier test in this worker
        # must not hide a read from the spy.
        from hypergumbo_core import dataflow, framework_patterns, library_signatures

        framework_patterns.clear_pattern_cache()
        monkeypatch.setattr(dataflow, "_config_cache", {})
        library_signatures.load_library_signatures.cache_clear()
        library_signatures.load_library_package_variables.cache_clear()
        assert main(["survey", str(repo), "--out", str(repo.parent / "s.json")]) == 0
        declared = {s.directory for s in YAML_CATALOGS
                    if s.channel_stage == CHANNEL_STAGE_ANALYSIS}
        assert set(read) == declared


class TestCachedSurveyIsNotReusedAcrossAChannelChange:
    def test_breaking_a_channel_file_after_a_survey_misses_the_cache(
        self, env,
    ) -> None:
        """The filed repro, at the cache layer: survey, then break the file."""
        from hypergumbo_core.cli import _discover_input_file, main

        repo = env["repo"]
        assert main(["survey", str(repo)]) == 0
        assert _discover_input_file(repo) is not None
        _write(env["home"] / "frameworks.d" / "flask.yaml", "key: [unclosed\n")
        assert _discover_input_file(repo) is None
