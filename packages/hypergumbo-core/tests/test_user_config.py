# SPDX-License-Identifier: AGPL-3.0-or-later
"""User and project configuration files (ADR-0045 rulings 1, 2, 3).

The rule these tests exist to enforce is not "config files work" — it is
**which facts are allowed to live in a config file at all**. hypergumbo has
one setting whose value is a grant of arbitrary code execution (the SCIP
backend indexes by running the analysed crate's ``build.rs`` as the invoking
user), and ``~/.config`` is a directory people deliberately sync between
machines and commit to dotfiles repositories. A trust grant that travels that
way is the global-environment-variable footgun with better syntax.

So the deny tests below are the load-bearing ones, and they are written to
fail loudly rather than quietly: a rejected key raises, because a key that is
silently dropped teaches the user it took effect.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.user_config import (
    ConfigError,
    load_layered_config,
    project_config_path,
    user_config_path,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestWhereTheFilesLive:
    def test_user_config_honours_xdg_config_home(self, tmp_path: Path) -> None:
        got = user_config_path(environ={"XDG_CONFIG_HOME": str(tmp_path)})
        assert got == tmp_path / "hypergumbo" / "config.toml"

    def test_user_config_falls_back_to_dot_config(self, tmp_path: Path) -> None:
        got = user_config_path(environ={}, home=tmp_path)
        assert got == tmp_path / ".config" / "hypergumbo" / "config.toml"

    def test_project_config_is_a_dotfile_at_the_repo_root(
        self, tmp_path: Path,
    ) -> None:
        # NOT `.hypergumbo/config.toml`: `.hypergumbo` is already an OUTPUT
        # artifact name carried in discovery.py's ignore list, and putting
        # config inside an ignored output directory invites a cleanup script
        # to delete it.
        assert project_config_path(tmp_path) == tmp_path / ".hypergumbo.toml"


class TestATrustGrantIsNeverAConfigKey:
    """ADR-0045 ruling 2 — rejected in EVERY tier, user config included.

    This is the one that stops the ADR being an elaborate way to re-create
    the problem it was written about.
    """

    @pytest.mark.parametrize("tier", ["user", "project"])
    def test_enabling_an_executing_backend_is_refused(
        self, tmp_path: Path, tier: str,
    ) -> None:
        body = "[backends]\nrust_analyzer = true\n"
        if tier == "user":
            cfg = _write(tmp_path / "xdg" / "hypergumbo" / "config.toml", body)
            environ = {"XDG_CONFIG_HOME": str(cfg.parent.parent)}
            repo = tmp_path / "repo"
            repo.mkdir()
        else:
            repo = tmp_path / "repo"
            _write(repo / ".hypergumbo.toml", body)
            environ = {"XDG_CONFIG_HOME": str(tmp_path / "empty")}

        with pytest.raises(ConfigError) as exc:
            load_layered_config(repo_root=repo, environ=environ)
        message = str(exc.value)
        assert "rust_analyzer" in message
        # The error must say WHERE the setting belongs instead, or the user
        # simply deletes the line and goes back to the global env var.
        assert "trust" in message.lower()

    def test_disabling_an_executing_backend_is_also_refused(
        self, tmp_path: Path,
    ) -> None:
        """Even the safe direction is refused, deliberately.

        Allowing ``rust_analyzer = false`` would make the key look supported,
        and a user who set it to false today would reasonably expect true to
        work tomorrow. One rule is easier to hold than a directional one.
        """
        repo = tmp_path / "repo"
        _write(repo / ".hypergumbo.toml", "[backends]\nrust_analyzer = false\n")
        with pytest.raises(ConfigError):
            load_layered_config(
                repo_root=repo, environ={"XDG_CONFIG_HOME": str(tmp_path / "e")},
            )


class TestAProjectMayNotTrustItself:
    """ADR-0045 ruling 3 — the inversion of ADR-0016's overlay precedence.

    For catalogs, project-local outranks built-in and that is correct. For a
    security gate it is exactly backwards: a repository shipping its own
    "trust me" marker is the attack the gate exists to prevent. The two
    rulings currently forbid the same single key, which is why the test names
    the rule rather than the key — they will diverge the moment a second
    gated setting exists.
    """

    def test_a_security_gated_key_is_refused_from_the_project_tier(
        self, tmp_path: Path,
    ) -> None:
        repo = tmp_path / "repo"
        _write(repo / ".hypergumbo.toml", "[backends]\nrust_analyzer = true\n")
        with pytest.raises(ConfigError) as exc:
            load_layered_config(
                repo_root=repo, environ={"XDG_CONFIG_HOME": str(tmp_path / "e")},
            )
        assert ".hypergumbo.toml" in str(exc.value)


class TestOverlayPathsAreAnOrdinaryPreference:
    """The first real key: stop retyping --io-primitives on every invocation."""

    def test_user_tier_paths_are_read(self, tmp_path: Path) -> None:
        xdg = tmp_path / "xdg"
        _write(
            xdg / "hypergumbo" / "config.toml",
            'io_primitives = ["overlays/boto3.yaml"]\n',
        )
        repo = tmp_path / "repo"
        repo.mkdir()
        cfg = load_layered_config(
            repo_root=repo, environ={"XDG_CONFIG_HOME": str(xdg)},
        )
        assert cfg.io_primitives == [
            xdg / "hypergumbo" / "overlays" / "boto3.yaml",
        ]

    def test_relative_paths_resolve_against_their_own_config_file(
        self, tmp_path: Path,
    ) -> None:
        """A project path must not resolve against the user's home.

        Two tiers with relative paths is exactly where a single "resolve
        against cwd" rule silently picks the wrong file.
        """
        repo = tmp_path / "repo"
        _write(repo / ".hypergumbo.toml", 'io_primitives = ["ov.yaml"]\n')
        cfg = load_layered_config(
            repo_root=repo, environ={"XDG_CONFIG_HOME": str(tmp_path / "e")},
        )
        assert cfg.io_primitives == [repo / "ov.yaml"]

    def test_project_tier_outranks_user_tier(self, tmp_path: Path) -> None:
        """Ascending precedence, matching _resolve_io_overlays' own contract
        that a later path wins on qualified-name collision."""
        xdg = tmp_path / "xdg"
        _write(xdg / "hypergumbo" / "config.toml", 'io_primitives = ["u.yaml"]\n')
        repo = tmp_path / "repo"
        _write(repo / ".hypergumbo.toml", 'io_primitives = ["p.yaml"]\n')
        cfg = load_layered_config(
            repo_root=repo, environ={"XDG_CONFIG_HOME": str(xdg)},
        )
        assert cfg.io_primitives == [
            xdg / "hypergumbo" / "u.yaml",
            repo / "p.yaml",
        ]


class TestFailuresAreLoud:
    def test_missing_files_are_not_an_error(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        cfg = load_layered_config(
            repo_root=repo, environ={"XDG_CONFIG_HOME": str(tmp_path / "nope")},
        )
        assert cfg.io_primitives == []

    def test_malformed_toml_names_the_file(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _write(repo / ".hypergumbo.toml", "io_primitives = [unclosed\n")
        with pytest.raises(ConfigError) as exc:
            load_layered_config(
                repo_root=repo, environ={"XDG_CONFIG_HOME": str(tmp_path / "e")},
            )
        assert ".hypergumbo.toml" in str(exc.value)

    def test_an_unknown_key_is_refused_rather_than_ignored(
        self, tmp_path: Path,
    ) -> None:
        """Deliberate, and it has a cost worth naming.

        Silently ignoring an unrecognised key is how a user spends an
        afternoon believing a setting is in effect. The cost is forward
        compatibility: an older hypergumbo reading a config written for a
        newer one will refuse it rather than degrade. ADR-0045 OQ4 (schema
        versioning) is where that gets resolved; until it does, loud is the
        safer of the two failure modes.
        """
        repo = tmp_path / "repo"
        _write(repo / ".hypergumbo.toml", 'not_a_setting = "x"\n')
        with pytest.raises(ConfigError) as exc:
            load_layered_config(
                repo_root=repo, environ={"XDG_CONFIG_HOME": str(tmp_path / "e")},
            )
        assert "not_a_setting" in str(exc.value)

    def test_a_wrongly_typed_value_names_the_key_and_the_type(
        self, tmp_path: Path,
    ) -> None:
        repo = tmp_path / "repo"
        _write(repo / ".hypergumbo.toml", 'io_primitives = "not-a-list"\n')
        with pytest.raises(ConfigError) as exc:
            load_layered_config(
                repo_root=repo, environ={"XDG_CONFIG_HOME": str(tmp_path / "e")},
            )
        assert "io_primitives" in str(exc.value)


class TestTheConfigTierActuallyReachesTheOverlayResolver:
    """The wiring, tested separately from the loader.

    A loader that parses perfectly and a resolver that never calls it is the
    failure mode this project has paid for before ("a predicate is inert
    until its call sites pass it"), so the two halves get separate tests and
    this one asserts the join.
    """

    def test_config_paths_land_below_claims_and_flag_paths(
        self, tmp_path: Path,
    ) -> None:
        import argparse

        from hypergumbo_core.cli import _resolve_io_overlays

        repo = tmp_path / "repo"
        _write(repo / ".hypergumbo.toml", 'io_primitives = ["from_project.yaml"]\n')
        xdg = tmp_path / "xdg"
        _write(
            xdg / "hypergumbo" / "config.toml",
            'io_primitives = ["from_user.yaml"]\n',
        )

        import os

        prior = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(xdg)
        try:
            got = _resolve_io_overlays(
                argparse.Namespace(io_primitives=["from_flag.yaml"]),
                [Path("from_claims.yaml")],
                repo_root=repo,
            )
        finally:
            if prior is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = prior

        # Ascending precedence: user < project < claims < flag.
        assert [p.name for p in got] == [
            "from_user.yaml",
            "from_project.yaml",
            "from_claims.yaml",
            "from_flag.yaml",
        ]

    def test_no_repo_root_means_no_config_tiers_rather_than_a_crash(
        self,
    ) -> None:
        import argparse

        from hypergumbo_core.cli import _resolve_io_overlays

        got = _resolve_io_overlays(argparse.Namespace(io_primitives=[]))
        assert got == []


class TestARejectedSettingExitsCleanly:
    """A traceback is not an error message.

    The two cases that reach here are a typo and a repository trying to grant
    itself execution rights; both deserve a sentence, and the second deserves
    one that says where the setting does belong.
    """

    def test_a_trust_key_in_project_config_exits_two_with_a_reason(
        self, tmp_path, capsys,
    ) -> None:
        from hypergumbo_core.cli import _load_config_or_exit

        repo = tmp_path / "repo"
        _write(repo / ".hypergumbo.toml", "[backends]\nrust_analyzer = true\n")
        import os

        prior = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(tmp_path / "empty")
        try:
            with pytest.raises(SystemExit) as exc:
                _load_config_or_exit(repo)
        finally:
            if prior is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = prior
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "rust_analyzer" in err
        assert "trust" in err.lower()

    def test_a_clean_repo_returns_a_config_rather_than_exiting(
        self, tmp_path,
    ) -> None:
        from hypergumbo_core.cli import _load_config_or_exit

        repo = tmp_path / "repo"
        repo.mkdir()
        import os

        prior = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(tmp_path / "empty")
        try:
            assert _load_config_or_exit(repo).io_primitives == []
        finally:
            if prior is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = prior
