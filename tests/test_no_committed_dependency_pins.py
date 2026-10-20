# SPDX-License-Identifier: AGPL-3.0-or-later
"""No committed file pins third-party dependency versions.

GitHub's Dependabot scans every dependency manifest on the default branch
and raises an alert for each pinned version with a known advisory. It
cannot tell a fixture from a real dependency: the recorded aardvark-dns
crate's ``Cargo.lock`` drew five alerts (hickory-proto, bytes, time, rand)
for a crate this repository never builds, and they could not be cleared by
bumping pins, because one hickory-proto advisory has no fixed release and
new pins would no longer match the recorded index. The lockfile was
removed and its README now records where to fetch upstream's copy, with a
hash, for re-recording.

This test keeps it that way for every path, not only that fixture. A
tracked file fails if its name is a lockfile (:data:`LOCKFILE_NAMES`) or
if it is a ``requirements*.txt`` with an exact ``==`` pin, unless it is
listed in :data:`ALLOWED` with a reason. A fixture that needs a lockfile
should write one into ``tmp_path`` at test time, or say how to fetch it,
as the aardvark-dns README does.

Not covered: version pins inside ``go.mod``, ``pom.xml``, ``Gemfile`` and
other manifests that also declare dependencies. Today's fixtures of those
kinds pin nothing; add them here if that changes. The repository's own
``pyproject.toml`` ranges are its real dependencies, so they are out of
scope by design.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Callable

REPO_ROOT = Path(__file__).parent.parent

#: File names that exist only to pin resolved versions.
LOCKFILE_NAMES: frozenset[str] = frozenset({
    "Cargo.lock",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "poetry.lock",
    "Pipfile.lock",
    "uv.lock",
    "pdm.lock",
    "Gemfile.lock",
    "composer.lock",
    "go.sum",
    "pubspec.lock",
    "packages.lock.json",
    "mix.lock",
    "Package.resolved",
    "gradle.lockfile",
    "flake.lock",
})

#: Tracked paths allowed to pin versions, each with the reason. Empty today.
ALLOWED: dict[str, str] = {}

_REQUIREMENTS = re.compile(r"requirements[^/]*\.txt")
_EXACT_PIN = re.compile(r"^\s*[A-Za-z0-9][A-Za-z0-9._-]*(\[[^\]]*\])?\s*===?\s*[^\s;#]", re.M)


def pinned_files(paths: list[str], read: Callable[[str], str]) -> list[str]:
    """The paths among ``paths`` that pin dependency versions, minus :data:`ALLOWED`."""
    found = []
    for path in paths:
        name = PurePosixPath(path).name
        if name in LOCKFILE_NAMES:
            found.append(path)
        elif _REQUIREMENTS.fullmatch(name) and _EXACT_PIN.search(read(path)):
            found.append(path)
    return [p for p in found if p not in ALLOWED]


def _tracked_files() -> list[str]:
    return subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.splitlines()


def test_no_tracked_file_pins_dependency_versions() -> None:
    tracked = _tracked_files()
    assert len(tracked) > 1000
    offenders = pinned_files(
        tracked, lambda p: (REPO_ROOT / p).read_text(encoding="utf-8", errors="replace"),
    )
    assert offenders == [], (
        "committed files that pin dependency versions; Dependabot raises an "
        "alert for every pinned version with an advisory, fixture or not. "
        "Write the file into tmp_path at test time, record where to fetch it "
        "(see packages/hypergumbo-lang-rust-analyzer/recorded/aardvark_dns/"
        "README.md), or add it to ALLOWED with a reason:\n  "
        + "\n  ".join(offenders)
    )


def test_allowed_entries_still_exist() -> None:
    tracked = set(_tracked_files())
    assert [p for p in ALLOWED if p not in tracked] == []


def _contents(files: dict[str, str]) -> Callable[[str], str]:
    return files.__getitem__


def test_a_lockfile_anywhere_is_flagged() -> None:
    files = {
        "packages/x/recorded/crate/Cargo.lock": "",
        "packages/x/tests/fixtures/app/package-lock.json": "{}",
        "go.sum": "",
    }
    assert pinned_files(list(files), _contents(files)) == list(files)


def test_requirements_flagged_only_for_exact_pins() -> None:
    files = {
        "a/requirements.txt": "flask\nflask-restful\n",
        "b/requirements-dev.txt": "# pinned\nDjango==4.2.1\n",
        "c/requirements.txt": "requests[socks] == 2.31.0 ; python_version > '3'\n",
        "d/requirements.txt": "starlette>=0.30\nuvicorn~=0.29\n",
        "e/requirements.txt": "  # flask==1.0 in a comment\n",
    }
    assert pinned_files(list(files), _contents(files)) == [
        "b/requirements-dev.txt",
        "c/requirements.txt",
    ]


def test_manifests_that_only_declare_are_not_flagged() -> None:
    files = {
        "pyproject.toml": 'dependencies = ["requests==2.31.0"]\n',
        "fixtures/go.mod": "module m\n\ngo 1.21\n",
        "fixtures/Cargo.toml": '[dependencies]\nserde = "1"\n',
        "docs/requirements.md": "Django==4.2\n",
    }
    assert pinned_files(list(files), _contents(files)) == []
