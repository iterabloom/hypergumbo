# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every lockstep-versioned package must be in every release script's package list.

Background
----------
The release scripts do not discover packages; each one carries its own
hard-coded list. ``hypergumbo-lang-scip-python`` was added on 2026-09-19 and
reached none of the release-path lists, so preparing 8.1.0 would have:

* left the package at 8.0.0 (``bump-version`` did not list it), pinning
  ``hypergumbo-core==8.0.0`` against a core at 8.1.0;
* left the meta package's ``scip-python`` extra pinning
  ``hypergumbo-lang-scip-python==8.0.0``, a version never published, so
  ``pip install 'hypergumbo[scip-python]'`` could not resolve;
* never built or uploaded it (``release.yml``'s build loop did not list it).

Nothing failed: a list that omits a package produces no error, only a
release that lacks it.

How the test works
------------------
The package set comes from the filesystem (``packages/*/pyproject.toml``),
never from any of the lists under test, so a new package is caught the day it
is added. ``bump-version`` is *executed* against a copy of the real manifests
and ``__init__`` files, and the result is read back: every lockstep package
and every internal pin must reach the new version. The other lists
(``release-check``, its ``cov-paths.sh`` coverage set, ``release.yml``,
``prepare-release``'s summary) cannot be
run in a test, so their membership is checked against the same set.

``hypergumbo-tracker`` is versioned independently (``--tracker``) and is
excluded from the lockstep set; the test checks it moves to its own version.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover - py3.10 path
    import tomli as tomllib

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGES_DIR = REPO_ROOT / "packages"
TRACKER = "hypergumbo-tracker"


def _manifests() -> dict[str, Path]:
    """Distribution name -> pyproject.toml, for every Python package in packages/."""
    found = {}
    for pyproject in sorted(PACKAGES_DIR.glob("*/pyproject.toml")):
        project = tomllib.loads(pyproject.read_text()).get("project", {})
        if "name" in project:
            found[project["name"]] = pyproject
    return found


def _lockstep() -> dict[str, Path]:
    return {name: p for name, p in _manifests().items() if name != TRACKER}


def test_package_set_is_read_from_the_filesystem():
    """Reach: the enumeration sees the packages the lists must name."""
    lockstep = _lockstep()
    assert "hypergumbo-core" in lockstep
    assert "hypergumbo" in lockstep
    assert "hypergumbo-lang-scip-python" in lockstep
    assert TRACKER in _manifests() and TRACKER not in lockstep


def _pins(text: str) -> list[tuple[str, str]]:
    return re.findall(r"(hypergumbo(?:-[a-z0-9]+)*)==([0-9][^\"',\s\]]*)", text)


def test_bump_version_moves_every_package_and_every_internal_pin(tmp_path: Path):
    (tmp_path / "scripts").mkdir()
    shutil.copy2(REPO_ROOT / "scripts" / "bump-version", tmp_path / "scripts")
    for pyproject in PACKAGES_DIR.glob("*/pyproject.toml"):
        rel = pyproject.relative_to(REPO_ROOT)
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pyproject, tmp_path / rel)
    for init in PACKAGES_DIR.glob("*/src/*/__init__.py"):
        rel = init.relative_to(REPO_ROOT)
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(init, tmp_path / rel)

    result = subprocess.run(
        [str(tmp_path / "scripts" / "bump-version"), "97.98.99", "--tracker", "96.0.1"],
        input="n\n", capture_output=True, text=True, cwd=tmp_path, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    lockstep = _lockstep()
    stale = []
    for name, pyproject in _manifests().items():
        want = "96.0.1" if name == TRACKER else "97.98.99"
        text = (tmp_path / pyproject.relative_to(REPO_ROOT)).read_text()
        if tomllib.loads(text)["project"]["version"] != want:
            stale.append(f"{name}: version")
        for pinned, version in _pins(text):
            if pinned in lockstep and version != "97.98.99":
                stale.append(f"{name}: pin {pinned}=={version}")
        init_dir = tmp_path / pyproject.parent.relative_to(REPO_ROOT) / "src"
        for init in init_dir.glob("*/__init__.py"):
            m = re.search(r'__version__ = "([^"]+)"', init.read_text())
            if m and m.group(1) != want:
                stale.append(f"{name}: __version__ {m.group(1)}")
    assert not stale, f"bump-version left these behind: {stale}"


def _bash_array(script: str, name: str) -> list[str]:
    m = re.search(rf"^{name}=\(\n(.*?)\n\)", script, re.M | re.S)
    assert m, f"array {name} not found"
    return re.findall(r'"([^"]+)"', m.group(1))


def _dirs(lockstep: dict[str, Path]) -> set[str]:
    return {p.parent.name for p in lockstep.values()}


@pytest.mark.parametrize("array", ["PACKAGES", "INSTALL_ORDER"])
def test_release_check_lists_every_lockstep_package(array: str):
    script = (REPO_ROOT / "scripts" / "release-check").read_text()
    listed = {Path(p).name for p in _bash_array(script, array)}
    missing = _dirs(_lockstep()) - listed
    assert not missing, f"release-check {array} misses {sorted(missing)}"


def test_release_workflow_builds_every_package():
    workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text()
    m = re.search(r"for pkg in ([^;]+); do\s*\n\s*echo \"Building packages/", workflow)
    assert m, "release.yml build loop not found"
    listed = set(m.group(1).split())
    missing = {p.parent.name for p in _manifests().values()} - listed
    assert not missing, f"release.yml builds none of {sorted(missing)}"


def test_prepare_release_summary_names_every_lockstep_package():
    script = (REPO_ROOT / "scripts" / "prepare-release").read_text()
    listed = set(re.findall(r'echo "  - ([a-z0-9-]+)(?: \(meta-package\))? \$VERSION"', script))
    missing = set(_lockstep()) - listed
    assert not missing, f"prepare-release's summary omits {sorted(missing)}"


def test_release_check_coverage_measures_every_package():
    """release-check's 100% gate measures only the sources cov-paths.sh names.

    The meta package ``hypergumbo`` is exempt: its source is a re-export shim
    (``__init__`` plus a ``__main__`` the coverage config omits), and no CI job
    measures it either.
    """
    fragment = (REPO_ROOT / "scripts" / "lib" / "cov-paths.sh").read_text()
    listed = {Path(p.removeprefix("--cov=")).parent.name for p in _bash_array(fragment, "COV_PATHS_ALL")}
    shipped = {
        p.parent.name for name, p in _manifests().items()
        if (p.parent / "src").is_dir() and name != "hypergumbo"
    }
    missing = shipped - listed
    assert not missing, f"cov-paths.sh measures none of {sorted(missing)}"
