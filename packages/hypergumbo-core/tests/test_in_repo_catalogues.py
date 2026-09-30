# SPDX-License-Identifier: AGPL-3.0-or-later
"""In-repo catalogue data loads only on opt-in, and every loaded file is named by tier.

ADR-0061 rulings 4 and 5; INV-hamin and INV-gumom.

INV-hamin: a repository's own ``.hypergumbo.toml`` loaded its ``io_primitives``
overlays by default, so a repository could grant ``module_completeness`` to its
own egress module and turn a real secret-to-network flow into a clean verdict,
named by nothing in the verdict. Now those paths load only with
``--in-repo-catalogues`` or a per-repository grant recorded by
``hypergumbo trust-catalogues``; with neither, the run says what it did not
load, and a recorded refusal silences it.

INV-gumom: ``catalog_provenance`` named only command-line and claims-file
paths. ``tiers`` now lists every file the run read under ``builtin`` /
``community`` / ``yours`` / ``in_repo``, with git state for a file inside the
repository. The end-to-end verdicts live in the mainstream package's
``test_in_repo_catalogue_opt_in.py``.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

import pytest

import hypergumbo_core.io_boundary as iob
from hypergumbo_core.cli import (
    _loaded_catalogue_files,
    _opted_in_repo_overlays,
    cmd_trust_catalogues,
)
from hypergumbo_core.in_repo_catalogues import (
    GIT_COMMITTED,
    GIT_MODIFIED,
    GIT_UNTRACKED,
    git_state,
    grant_store_root,
    is_inside,
    read_in_repo_decision,
    record_in_repo_decision,
)
from hypergumbo_core.verify_claims import (
    CATALOGUE_TIERS,
    LoadedCatalogueFile,
    catalog_provenance,
    catalogue_tiers,
    render_catalog_provenance_text,
)

_SHIPPED_HTTP = Path(iob.__file__).parent / "io_primitives_overlays" / \
    "python-http-clients.yaml"

_GRANT = """\
language: python
status: overlay
{provenance}retrieved: 2026-01-01
module_completeness:
  - module: vendorlib
    completeness: complete
    retrieved: "2026-01-01"
"""


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return home


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [shutil.which("git") or "git", "-c", "user.email=t@t", "-c", "user.name=t",
         *args],
        cwd=repo, check=True, capture_output=True,
    )


class TestTheGrantStore:
    def test_a_decision_is_three_valued(self, state: Path, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        assert read_in_repo_decision(repo) is None
        record_in_repo_decision(repo, False)
        assert read_in_repo_decision(repo) is False
        path = record_in_repo_decision(repo, True)
        assert read_in_repo_decision(repo) is True
        assert path.stat().st_mode & 0o777 == 0o600

    def test_it_is_keyed_by_the_resolved_path(self, state: Path, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        link = tmp_path / "link"
        link.symlink_to(repo)
        record_in_repo_decision(link, True)
        assert read_in_repo_decision(repo) is True

    def test_it_lives_in_state_not_in_the_synced_config_home(
        self, state: Path,
    ) -> None:
        assert grant_store_root() == state / "hypergumbo" / "catalogue-grants.d"
        assert grant_store_root(environ={}, home=Path("/h")) == Path(
            "/h/.local/state/hypergumbo/catalogue-grants.d",
        )


class TestGitState:
    def test_committed_modified_and_untracked(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-q")
        (repo / "a.yaml").write_text("x: 1\n")
        (repo / "b.yaml").write_text("x: 1\n")
        _git(repo, "add", "a.yaml", "b.yaml")
        _git(repo, "commit", "-q", "-m", "c")
        (repo / "b.yaml").write_text("x: 2\n")
        (repo / "c.yaml").write_text("x: 1\n")
        assert git_state(repo, repo / "a.yaml") == GIT_COMMITTED
        assert git_state(repo, repo / "b.yaml") == GIT_MODIFIED
        assert git_state(repo, repo / "c.yaml") == GIT_UNTRACKED

    def test_a_directory_git_does_not_track_reads_untracked(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "a.yaml").write_text("x: 1\n")
        assert git_state(tmp_path, tmp_path / "a.yaml") == GIT_UNTRACKED

    def test_is_inside(self, tmp_path: Path) -> None:
        assert is_inside(tmp_path, tmp_path / "x" / "y.yaml")
        assert not is_inside(tmp_path / "x", tmp_path / "y.yaml")


class TestTheOptIn:
    def _args(self, flag: bool = False) -> argparse.Namespace:
        return argparse.Namespace(in_repo_catalogues=flag)

    def test_nothing_named_means_nothing_to_gate(
        self, state: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert _opted_in_repo_overlays(self._args(), tmp_path, []) == []
        assert capsys.readouterr().err == ""

    def test_without_an_opt_in_nothing_loads_and_the_run_says_so(
        self, state: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        named = [tmp_path / "deps.yaml"]
        assert _opted_in_repo_overlays(self._args(), tmp_path, named) == []
        err = capsys.readouterr().err
        assert "deps.yaml" in err and "NOT loaded" in err
        assert "--in-repo-catalogues" in err and "trust-catalogues" in err

    def test_the_flag_opts_in_for_one_run(
        self, state: Path, tmp_path: Path,
    ) -> None:
        named = [tmp_path / "deps.yaml"]
        assert _opted_in_repo_overlays(self._args(True), tmp_path, named) == named

    def test_a_grant_opts_in_for_the_repository(
        self, state: Path, tmp_path: Path,
    ) -> None:
        named = [tmp_path / "deps.yaml"]
        record_in_repo_decision(tmp_path, True)
        assert _opted_in_repo_overlays(self._args(), tmp_path, named) == named

    def test_a_decline_is_a_decision_and_the_note_goes_quiet(
        self, state: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        record_in_repo_decision(tmp_path, False)
        assert _opted_in_repo_overlays(
            self._args(), tmp_path, [tmp_path / "deps.yaml"],
        ) == []
        assert capsys.readouterr().err == ""


class TestTheTrustCataloguesCommand:
    def _run(self, path: Path, *, revoke: bool = False, show: bool = False) -> int:
        return cmd_trust_catalogues(
            argparse.Namespace(path=str(path), revoke=revoke, show=show),
        )

    def test_grant_show_revoke(
        self, state: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert self._run(tmp_path, show=True) == 0
        assert "no decision recorded" in capsys.readouterr().out
        assert self._run(tmp_path) == 0
        out = capsys.readouterr().out
        assert "Granted" in out and str(grant_store_root()) in out
        assert self._run(tmp_path, show=True) == 0
        assert "GRANTED" in capsys.readouterr().out
        assert self._run(tmp_path, revoke=True) == 0
        assert "Declined" in capsys.readouterr().out
        assert read_in_repo_decision(tmp_path) is False


class TestTheTiers:
    def test_each_file_lands_in_its_tier(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        mine = tmp_path / "mine.yaml"
        mine.write_text("x: 1\n")
        theirs = repo / "deps.yaml"
        theirs.write_text("x: 1\n")
        seeded = repo / "seeded.yaml"
        seeded.write_text(_SHIPPED_HTTP.read_text())
        builtin = Path(iob.__file__).parent / "io_primitives" / "python.yaml"
        channel = tmp_path / "fw.yaml"
        channel.write_text("x: 1\n")
        tiers = catalogue_tiers([
            LoadedCatalogueFile("io_primitives", builtin, True),
            LoadedCatalogueFile("io_primitives", builtin, True),  # deduplicated
            LoadedCatalogueFile("io_primitives_overlays", _SHIPPED_HTTP, True),
            LoadedCatalogueFile("io_primitives", mine, False),
            LoadedCatalogueFile("io_primitives", theirs, False),
            LoadedCatalogueFile("io_primitives", seeded, False),
            LoadedCatalogueFile("frameworks", channel, False, "analysis"),
        ], repo)
        assert tuple(tiers) == CATALOGUE_TIERS
        assert [e["path"] for e in tiers["builtin"]] == [str(builtin)]
        community = {e["path"]: e for e in tiers["community"]}
        assert community[str(_SHIPPED_HTTP)]["retrieved"]
        assert "git_state" not in community[str(_SHIPPED_HTTP)]
        # The line decides the tier; a community file in the repo still
        # carries what git says about it.
        assert community[str(seeded)]["git_state"] == GIT_UNTRACKED
        assert tiers["in_repo"] == [{
            "family": "io_primitives", "path": str(theirs),
            "git_state": GIT_UNTRACKED,
        }]
        assert {e["path"] for e in tiers["yours"]} == {str(mine), str(channel)}
        assert next(e for e in tiers["yours"]
                    if e["path"] == str(channel))["stage"] == "analysis"

    def test_no_repo_root_means_nothing_is_in_repo(self, tmp_path: Path) -> None:
        mine = tmp_path / "mine.yaml"
        mine.write_text("x: 1\n")
        tiers = catalogue_tiers(
            [LoadedCatalogueFile("io_primitives", mine, False)], None,
        )
        assert [e["path"] for e in tiers["yours"]] == [str(mine)]


class TestCatalogProvenance:
    def test_config_grants_are_named_and_community_grants_withheld(
        self, tmp_path: Path,
    ) -> None:
        yours = tmp_path / "y.yaml"
        yours.write_text(_GRANT.format(provenance=""))
        community = tmp_path / "c.yaml"
        community.write_text(_GRANT.format(provenance="provenance: community\n"))
        flag = tmp_path / "f.yaml"
        flag.write_text(_GRANT.format(provenance=""))
        prov = catalog_provenance(
            {"io_primitives": ([flag], [])},
            tier_files=[LoadedCatalogueFile("io_primitives", yours, False)],
            io_overlay_origins=[
                (yours, "config"), (community, "user_channel"), (flag, "cli"),
            ],
        )
        assert {(g["path"], g["origin"]) for g in prov["completeness_grants"]} == {
            (str(flag), "cli"), (str(yours), "config"),
        }
        assert [(g["path"], g["modules"]) for g in
                prov["withheld_completeness_grants"]] == [
            (str(community), ["vendorlib"]),
        ]
        assert prov["user_supplied"] is True

    def test_a_yours_file_alone_is_user_supplied_input(self, tmp_path: Path) -> None:
        """config.toml or a channel file is the operator's input exactly as a
        flag is; a run resting on one used to report ``user_supplied: false``."""
        yours = tmp_path / "y.yaml"
        yours.write_text("x: 1\n")
        prov = catalog_provenance(
            {}, tier_files=[LoadedCatalogueFile("frameworks", yours, False)],
        )
        assert prov["user_supplied"] is True
        assert prov["withheld_completeness_grants"] == []

    def test_the_shipped_catalogue_alone_is_not(self) -> None:
        builtin = Path(iob.__file__).parent / "io_primitives" / "python.yaml"
        prov = catalog_provenance(
            {}, tier_files=[LoadedCatalogueFile("io_primitives", builtin, True)],
        )
        assert prov["user_supplied"] is False
        assert [e["path"] for e in prov["tiers"]["builtin"]] == [str(builtin)]


class TestTheTextRendering:
    def test_in_repo_yours_and_withheld_are_all_rendered(self) -> None:
        text = "\n".join(render_catalog_provenance_text({
            "user_supplied": True,
            "layers": {"io_primitives": {"cli": ["f.yaml"], "claims_file": []}},
            "tiers": {
                "builtin": [], "community": [],
                "yours": [{"family": "io_primitives", "path": "f.yaml"},
                          {"family": "frameworks", "path": "fw.yaml"}],
                "in_repo": [{"family": "io_primitives", "path": "deps.yaml",
                             "git_state": "modified"}],
            },
            "completeness_grants": [{
                "path": "deps.yaml", "origin": "in_repo_config",
                "language": "python", "modules": ["telnetlib"],
            }],
            "withheld_completeness_grants": [{
                "path": "c.yaml", "origin": "user_channel",
                "language": "python", "modules": ["vendorlib"],
            }],
        }))
        assert "io_primitives: f.yaml  [CLI flag]" in text
        assert text.count("f.yaml") == 1, "a flag path is not listed twice"
        assert "frameworks: fw.yaml  [yours]" in text
        assert "io_primitives: deps.yaml  [in-repo, modified]" in text
        assert "the repository's .hypergumbo.toml, opted in" in text
        assert "WITHHELD GRANTS" in text and "vendorlib" in text


class TestWhatTheRunLoaded:
    def test_io_taint_and_channels(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = tmp_path / "config"
        (config / "hypergumbo" / "frameworks.d").mkdir(parents=True)
        (config / "hypergumbo" / "frameworks.d" / "mine.yaml").write_text("x: 1\n")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
        overlay = tmp_path / "o.yaml"
        overlay.write_text("x: 1\n")
        taint_dir = tmp_path / "taint"
        taint_dir.mkdir()
        (taint_dir / "s.yaml").write_text("x: 1\n")
        files = _loaded_catalogue_files(
            ["scala"], [overlay, tmp_path / "missing.yaml"],
            include_default_overlays=True,
            taint_paths={"taint_sources": [taint_dir], "taint_sinks": [],
                         "taint_sanitizers": []},
        )
        by = {(f.family, f.path.name, f.shipped, f.stage) for f in files}
        # scala inherits java: both shipped files were read.
        assert ("io_primitives", "scala.yaml", True, "verify") in by
        assert ("io_primitives", "java.yaml", True, "verify") in by
        assert ("io_primitives", "o.yaml", False, "verify") in by
        assert not any(name == "missing.yaml" for _, name, _, _ in by)
        assert ("taint_sources", "s.yaml", False, "verify") in by
        assert ("taint_sanitizers", "encryption.yaml", True, "verify") in by
        assert ("function_summaries", "go_stdlib.yaml", True, "verify") in by
        assert ("frameworks", "mine.yaml", False, "analysis") in by

    def test_no_taint_means_no_taint_files(self, tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "none"))
        files = _loaded_catalogue_files(
            ["python"], [], include_default_overlays=False, taint_paths=None,
        )
        families = {f.family for f in files}
        assert families == {"io_primitives"}
