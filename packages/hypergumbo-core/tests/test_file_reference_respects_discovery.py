# SPDX-License-Identifier: AGPL-3.0-or-later
"""A reference to a file the run does not analyse must not become a file node.

Two helpers resolve a path written in source to a file id by asking the DISK
whether the file exists (``edge_target.in_tree_file_id`` for ``source`` /
``load`` / ``include`` forms, ``file_records.file_reference_dst`` for a
component's relative import). The disk is not the analysed tree: discovery
leaves out what git ignores and does not track, and the default excludes
(ADR-0004). An edge ending on the file id of such a file made the synthesis
pass mint a file node for it, so the output listed, as a node, a file the
user's VCS says is not part of the project and that no analyzer read.

So both helpers ask discovery, and a file discovery leaves out is "not in the
tree": the caller falls back to its stand-in, as for a file that is absent.
"""
from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from hypergumbo_core import discovery
from hypergumbo_core.analyze.edge_target import in_tree_file_id
from hypergumbo_core.analyze.file_records import file_reference_dst

_GIT = shutil.which("git") or "git"
_AS = ["-c", "user.name=t", "-c", "user.email=t@t"]


@pytest.fixture(autouse=True)
def _no_remembered_answers() -> Iterator[None]:
    """Each test asks git itself."""
    discovery._vcs_ignored_cache.clear()
    yield
    discovery._vcs_ignored_cache.clear()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run([_GIT, *_AS, *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Tracked ``kept.*``, ignored-and-untracked ``ignored.*``, and a file
    under a default-excluded directory (``.cache``)."""
    root = tmp_path / "repo"
    files = {
        ".gitignore": "ignored.fish\nIgnored.svelte\n",
        "a.fish": "source ./ignored.fish\nsource ./kept.fish\nsource ./.cache/dep.fish\n",
        "kept.fish": "function k\n  echo k\nend\n",
        "App.svelte": (
            "<script>\n"
            '  import Ignored from "./Ignored.svelte";\n'
            '  import Kept from "./Kept.svelte";\n'
            "</script>\n<Ignored />\n<Kept />\n"
        ),
        "Kept.svelte": "<p>kept</p>\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    (root / "ignored.fish").write_text("function secret_fn\n  echo s\nend\n", encoding="utf-8")
    (root / "Ignored.svelte").write_text("<p>secret</p>\n", encoding="utf-8")
    (root / ".cache").mkdir()
    (root / ".cache" / "dep.fish").write_text("function d\nend\n", encoding="utf-8")
    return root


def test_the_fixture_has_the_files_on_disk_and_git_ignores_two(repo: Path) -> None:
    """Reach: every target exists, so a miss below is not a missing file."""
    for rel in ("kept.fish", "ignored.fish", ".cache/dep.fish",
                "Kept.svelte", "Ignored.svelte"):
        assert (repo / rel).is_file(), rel
    assert discovery.vcs_ignored(repo / "ignored.fish", repo)
    assert not discovery.vcs_ignored(repo / "kept.fish", repo)


def test_a_path_lookup_finds_the_tracked_file_only(repo: Path) -> None:
    here = repo / "a.fish"
    assert in_tree_file_id(here, "a.fish", ["kept.fish"]) == "fish:kept.fish:1-1:file:file"
    assert in_tree_file_id(here, "a.fish", ["ignored.fish"]) is None
    assert in_tree_file_id(here, "a.fish", [".cache/dep.fish"]) is None
    assert in_tree_file_id(here, "a.fish", ["ignored.fish", "kept.fish"]) == (
        "fish:kept.fish:1-1:file:file"), "an excluded candidate is skipped, not the end"


def test_a_relative_import_finds_the_tracked_file_only(repo: Path) -> None:
    here = repo / "App.svelte"
    assert file_reference_dst("svelte", here, "App.svelte", "./Kept.svelte") == (
        "svelte:Kept.svelte:1-1:file:file")
    assert file_reference_dst("svelte", here, "App.svelte", "./Ignored.svelte") is None


def test_the_run_has_no_file_node_for_a_file_it_did_not_analyse(repo: Path) -> None:
    """The whole pipeline: neither ignored file is a node, the tracked ones
    are, and both references still have an edge (to a stand-in)."""
    from hypergumbo_core.cli import run_behavior_map

    out = repo.parent / "out.json"
    run_behavior_map(repo_root=repo, out_path=out)
    import json

    data = json.loads(out.read_text(encoding="utf-8"))
    file_paths = {n["path"] for n in data["nodes"] if n["kind"] == "file"}
    assert {"kept.fish", "Kept.svelte", "a.fish", "App.svelte"} <= file_paths, file_paths
    assert not {"ignored.fish", "Ignored.svelte", ".cache/dep.fish"} & file_paths
    ids = {n["id"] for n in data["nodes"]}
    sourced = [e["dst"] for e in data["edges"]
               if e["type"] == "sources" and e["src"].startswith("fish:a.fish:")]
    assert len(sourced) == 3, sourced
    assert "fish:kept.fish:1-1:file:file" in sourced
    assert all(dst in ids for dst in sourced), "no dangling edge either"
