# SPDX-License-Identifier: MPL-2.0
"""Tests for the host-protected tracker config layer.

The security property under test is narrow and worth stating: an agent that can
write anywhere inside the repository checkout must not be able to change, or to
cause the tracker to ignore, the governance config. The interesting cases are
therefore the REFUSALS — a missing protected config must be fatal rather than
falling back to the in-repo file the agent controls.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from hypergumbo_tracker.models import load_config
from hypergumbo_tracker.protected_config import (
    ProtectedConfigError,
    find_protected_config,
    legible_repo_id,
    protection_enabled,
)

MINIMAL = {
    "statuses": ["todo_hard", "done", "deleted"],
    "kinds": {"work_item": {"prefix": "WI", "description": "w"}},
    "stop_hook": {"blocking_statuses": ["todo_hard"], "resolved_statuses": ["done"]},
}


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".agent" / "tracker").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)  # noqa: S607
    return repo


def _write_protected(root: Path, repo: Path, data: dict) -> Path:
    d = root / legible_repo_id(repo)
    d.mkdir(parents=True)
    (d / "config.yaml").write_text(yaml.safe_dump(data))
    return d / "config.yaml"


class TestLegibleRepoId:
    def test_hash_first_then_legible_tail(self) -> None:
        name = legible_repo_id(Path("/home/jgstern/hypergumbo"))
        digest, _, tail = name.partition("_")
        assert len(digest) == 12
        assert all(c in "0123456789abcdef" for c in digest)
        assert tail == "home_jgstern_hypergumbo"

    def test_distinct_clones_of_one_repo_get_distinct_ids(self) -> None:
        """Per-clone keying is the whole point of hashing the toplevel path."""
        a = legible_repo_id(Path("/home/a/hypergumbo"))
        b = legible_repo_id(Path("/home/b/hypergumbo"))
        assert a.split("_")[0] != b.split("_")[0]

    def test_long_path_truncates_from_the_left_keeping_the_tail(self) -> None:
        name = legible_repo_id(Path("/" + "/".join(["segment"] * 40) + "/final"))
        assert len(name) <= 96
        assert name.endswith("final")

    def test_unsafe_characters_collapse(self) -> None:
        name = legible_repo_id(Path("/home/a b/c:d//e"))
        assert " " not in name and ":" not in name
        assert "__" not in name

    def test_root_path_yields_bare_digest(self) -> None:
        """A path with no usable tail must still produce a valid directory name."""
        assert legible_repo_id(Path("/")) == legible_repo_id(Path("/")).split("_")[0]


class TestResolution:
    def test_disabled_when_root_absent(self, tmp_path: Path) -> None:
        assert protection_enabled(tmp_path / "nope") is False

    def test_finds_config_by_exact_name(self, tmp_path: Path) -> None:
        root = tmp_path / "etc"
        root.mkdir()
        repo = _make_repo(tmp_path)
        expected = _write_protected(root, repo, MINIMAL)
        assert find_protected_config(repo, root) == expected

    def test_tail_is_a_renameable_comment(self, tmp_path: Path) -> None:
        """An administrator may rename the legible half; the hash is the key."""
        root = tmp_path / "etc"
        root.mkdir()
        repo = _make_repo(tmp_path)
        d = _write_protected(root, repo, MINIMAL)
        digest = legible_repo_id(repo).split("_")[0]
        d.parent.rename(root / f"{digest}_renamed_by_hand")
        found = find_protected_config(repo, root)
        assert found is not None and found.parent.name.endswith("renamed_by_hand")

    def test_two_directories_one_hash_refuses(self, tmp_path: Path) -> None:
        root = tmp_path / "etc"
        root.mkdir()
        repo = _make_repo(tmp_path)
        _write_protected(root, repo, MINIMAL)
        digest = legible_repo_id(repo).split("_")[0]
        (root / f"{digest}_impostor").mkdir()
        with pytest.raises(ProtectedConfigError, match="Refusing to choose"):
            find_protected_config(repo, root)

    def test_directory_without_config_is_not_found(self, tmp_path: Path) -> None:
        root = tmp_path / "etc"
        root.mkdir()
        repo = _make_repo(tmp_path)
        (root / legible_repo_id(repo)).mkdir(parents=True)
        assert find_protected_config(repo, root) is None


class TestLoadConfigIntegration:
    def test_inert_when_host_has_not_opted_in(self, tmp_path: Path) -> None:
        """Hosts without the root directory keep today's behaviour exactly."""
        repo = _make_repo(tmp_path)
        cfg_dir = repo / ".agent" / "tracker"
        (cfg_dir / "config.yaml").write_text(yaml.safe_dump(MINIMAL))
        loaded = load_config(cfg_dir, protected_root=tmp_path / "absent")
        assert "todo_hard" in loaded.statuses

    def test_protected_config_wins_over_in_repo(self, tmp_path: Path) -> None:
        """The in-repo file is IGNORED, not merged — it is agent-writable."""
        root = tmp_path / "etc"
        root.mkdir()
        repo = _make_repo(tmp_path)
        cfg_dir = repo / ".agent" / "tracker"
        agent_owned = dict(MINIMAL)
        agent_owned["statuses"] = ["todo_hard", "done", "deleted", "agent_smuggled"]
        (cfg_dir / "config.yaml").write_text(yaml.safe_dump(agent_owned))
        _write_protected(root, repo, MINIMAL)
        loaded = load_config(cfg_dir, protected_root=root)
        assert "agent_smuggled" not in loaded.statuses

    def test_missing_protected_config_REFUSES_rather_than_falling_back(
        self, tmp_path: Path
    ) -> None:
        """THE downgrade attack. An agent need not write the protected config —
        only cause it not to be found. Falling back would hand it a config it
        fully controls, so absence must be fatal."""
        root = tmp_path / "etc"
        root.mkdir()
        repo = _make_repo(tmp_path)
        cfg_dir = repo / ".agent" / "tracker"
        (cfg_dir / "config.yaml").write_text(yaml.safe_dump(MINIMAL))
        with pytest.raises(ProtectedConfigError) as exc:
            load_config(cfg_dir, protected_root=root)
        assert "Refusing to fall back" in str(exc.value)
        assert str(root) in str(exc.value)

    def test_refusal_names_the_exact_expected_path(self, tmp_path: Path) -> None:
        """A refusal the human cannot act on is a worse failure than the attack."""
        root = tmp_path / "etc"
        root.mkdir()
        repo = _make_repo(tmp_path)
        with pytest.raises(ProtectedConfigError) as exc:
            load_config(repo / ".agent" / "tracker", protected_root=root)
        assert str(root / legible_repo_id(repo) / "config.yaml") in str(exc.value)

    def test_non_git_directory_falls_through(self, tmp_path: Path) -> None:
        """Protection keys on a git toplevel; outside a repo there is nothing to key on."""
        root = tmp_path / "etc"
        root.mkdir()
        loose = tmp_path / "loose"
        loose.mkdir()
        (loose / "config.yaml").write_text(yaml.safe_dump(MINIMAL))
        assert "todo_hard" in load_config(loose, protected_root=root).statuses


class TestSetupCheck:
    """The setup check is the only thing that tells a human how to opt in."""

    def _check(self, tmp_path: Path, root: Path, monkeypatch):
        from hypergumbo_tracker import protected_config as pc
        from hypergumbo_tracker.setup import _check_protected_config

        monkeypatch.setattr(pc, "PROTECTED_ROOT", root)
        repo = _make_repo(tmp_path)
        return _check_protected_config(repo / ".agent", repo), repo

    def test_warns_and_prints_commands_when_host_has_not_opted_in(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        result, repo = self._check(tmp_path, tmp_path / "absent", monkeypatch)
        assert result.status == "warn"
        body = "\n".join(result.details)
        assert "sudo mkdir -p" in body and legible_repo_id(repo) in body

    def test_errors_when_host_opted_in_but_repo_has_no_config(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        root = tmp_path / "etc"
        root.mkdir()
        result, repo = self._check(tmp_path, root, monkeypatch)
        assert result.status == "error"
        assert "REFUSE" in result.message

    def test_errors_on_ambiguous_directories(self, tmp_path: Path, monkeypatch) -> None:
        from hypergumbo_tracker import protected_config as pc
        from hypergumbo_tracker.setup import _check_protected_config

        root = tmp_path / "etc"
        root.mkdir()
        monkeypatch.setattr(pc, "PROTECTED_ROOT", root)
        repo = _make_repo(tmp_path)
        _write_protected(root, repo, MINIMAL)
        (root / f"{legible_repo_id(repo).split('_')[0]}_two").mkdir()
        result = _check_protected_config(repo / ".agent", repo)
        assert result.status == "error" and "Ambiguous" in result.message

    def test_warns_when_protected_config_is_writable_by_this_user(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A protected config this user owns is not protected from this user."""
        from hypergumbo_tracker import protected_config as pc
        from hypergumbo_tracker.setup import _check_protected_config

        root = tmp_path / "etc"
        root.mkdir()
        monkeypatch.setattr(pc, "PROTECTED_ROOT", root)
        repo = _make_repo(tmp_path)
        _write_protected(root, repo, MINIMAL)
        result = _check_protected_config(repo / ".agent", repo)
        assert result.status == "warn"
        assert "not in force" in result.message

    def test_skips_outside_a_git_repo(self, tmp_path: Path, monkeypatch) -> None:
        from hypergumbo_tracker import protected_config as pc
        from hypergumbo_tracker.setup import _check_protected_config

        monkeypatch.setattr(pc, "PROTECTED_ROOT", tmp_path / "absent")
        loose = tmp_path / "loose" / ".agent"
        loose.mkdir(parents=True)
        result = _check_protected_config(loose)
        assert result.status == "ok" and "not a git repo" in result.message

    def test_reports_in_force_when_config_is_out_of_this_user_s_reach(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The success path: root-owned, not group/other-writable.

        A test cannot chown to root, so the uid comparison is driven instead —
        the property under test is "the checker recognises a config this user
        neither owns nor can write", not "os.stat works".
        """
        import os as _os

        from hypergumbo_tracker import protected_config as pc
        from hypergumbo_tracker.setup import _check_protected_config

        root = tmp_path / "etc"
        root.mkdir()
        monkeypatch.setattr(pc, "PROTECTED_ROOT", root)
        repo = _make_repo(tmp_path)
        cfg = _write_protected(root, repo, MINIMAL)
        root.chmod(0o755)
        cfg.parent.chmod(0o755)
        cfg.chmod(0o644)
        monkeypatch.setattr(_os, "getuid", lambda: 999999)
        result = _check_protected_config(repo / ".agent", repo)
        assert result.status == "ok"
        assert "in force" in result.message


class TestOwnershipTransferGuard:
    """The chmod fallback must never run in the agent's direction (INV-mizid).

    The fallback exists so the HUMAN can reclaim a config the agent owns, and it
    works by writing a new file — which the caller then owns. Run in reverse it
    hands the governance config to the agent, silently. That is the most likely
    way this deployment's config became agent-owned.
    """

    def test_fallback_refuses_for_an_agent(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from hypergumbo_tracker.setup import (
            OwnershipTransferRefused,
            _config_chmod_fallback,
        )

        cfg = tmp_path / "config.yaml"
        cfg.write_text("statuses: []\n")
        monkeypatch.setattr(
            "hypergumbo_tracker.setup.resolve_actor",
            lambda *a, **k: ("agent", "someone_agent"),
        )
        with pytest.raises(OwnershipTransferRefused, match="transfer ownership"):
            _config_chmod_fallback(cfg, 0o444)

    def test_fallback_still_works_for_a_human(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The legitimate direction must keep working, or humans cannot recover."""
        from hypergumbo_tracker.setup import _config_chmod_fallback

        cfg = tmp_path / "config.yaml"
        cfg.write_text("statuses: []\n")
        monkeypatch.setattr(
            "hypergumbo_tracker.setup.resolve_actor",
            lambda *a, **k: ("human", "alice"),
        )
        _config_chmod_fallback(cfg, 0o444)
        assert cfg.stat().st_mode & 0o777 == 0o444
        assert cfg.read_text() == "statuses: []\n", "content must survive the rewrite"

    def test_config_lock_reports_and_continues_when_refused(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """An agent unable to lock is the CORRECT outcome, not a crash.

        The file keeps whatever permissions it had, and the refusal is visible
        on stderr rather than swallowed.
        """
        from hypergumbo_tracker.setup import config_lock

        cfg = tmp_path / "config.yaml"
        cfg.write_text("statuses: []\n")
        cfg.chmod(0o644)
        monkeypatch.setattr(
            "hypergumbo_tracker.setup.resolve_actor",
            lambda *a, **k: ("agent", "someone_agent"),
        )
        monkeypatch.setattr(
            Path, "chmod", lambda self, mode: (_ for _ in ()).throw(PermissionError())
        )
        config_lock(cfg)
        assert "refusing to rewrite" in capsys.readouterr().err

    def test_config_unlock_reports_and_continues_when_refused(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        from hypergumbo_tracker.setup import config_unlock

        cfg = tmp_path / "config.yaml"
        cfg.write_text("statuses: []\n")
        monkeypatch.setattr(
            "hypergumbo_tracker.setup.resolve_actor",
            lambda *a, **k: ("agent", "someone_agent"),
        )
        monkeypatch.setattr(
            Path, "chmod", lambda self, mode: (_ for _ in ()).throw(PermissionError())
        )
        config_unlock(cfg)
        assert "refusing to rewrite" in capsys.readouterr().err
