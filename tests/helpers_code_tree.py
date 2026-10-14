# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fake code trees for the measurement scripts' tree-choice tests (WI-tumog).

WHAT THE TESTS HAVE TO OBSERVE. A measurement script that runs the wrong tree's
analyzer exits 0 and prints plausible numbers, so "it ran" proves nothing. The
only reliable observation is WHICH COPY OF THE CODE WAS IMPORTED. A fake package
that announces itself on import and then exits with a distinctive code makes
that observable from outside the process: the marker on stderr plus exit code
``FAKE_EXIT`` means the fake was imported; neither means it was not.

WHY A BARE DIRECTORY AND NOT A ``packages/*/src`` LAYOUT. The fakes stand for
"some other tree that PYTHONPATH names" -- the A/B baseline arm of WI-valav's
pretix run. A bare directory exercises the general case (``tree_of`` falls back
to the PYTHONPATH entry itself); ``fake_tree(..., layout=True)`` builds the
``packages/<dist>/src/<pkg>`` shape a real ``git archive`` export has.

WHY THE CHILD'S ENVIRONMENT IS BUILT FROM SCRATCH FOR THE TWO KEYS. The board
(``decontend/slot``) exports PYTHONPATH pinned to the worktree, and a developer
may have ``HG_MEASURE_TREE`` set. Either inherited value would decide the
outcome the test is trying to pin, so ``run_script`` always sets or removes
both explicitly.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"

#: Printed on stderr by every fake package's ``__init__``.
MARKER = "FAKE-TREE-IMPORTED"
#: The exit code a fake package's import raises.
FAKE_EXIT = 97
#: The selector the scripts read (``scripts/measure_code_tree.py``).
CHOICE_ENV = "HG_MEASURE_TREE"


def analysis_package_names(tree: Path = REPO_ROOT) -> list[str]:
    """``hypergumbo_core`` plus every ``hypergumbo_lang_*`` the tree ships."""
    names = {
        init.parent.name
        for init in (tree / "packages").glob("*/src/*/__init__.py")
    }
    return sorted(
        n for n in names
        if n == "hypergumbo_core" or n.startswith("hypergumbo_lang_")
    )


def fake_tree(root: Path, names: Iterable[str], *, layout: bool = False) -> str:
    """Build fake packages that announce themselves and exit on import.

    Returns the value to put on PYTHONPATH: ``root`` for a bare directory, or
    the ``os.pathsep``-joined ``packages/*/src`` entries for ``layout=True``.
    """
    entries: list[str] = []
    for name in names:
        if layout:
            src = root / "packages" / name.replace("_", "-") / "src"
            entries.append(str(src))
        else:
            src = root
        pkg = src / name
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text(
            "import sys\n"
            f"print('{MARKER} {name}', file=sys.stderr)\n"
            f"raise SystemExit({FAKE_EXIT})\n",
            encoding="utf-8",
        )
    if not layout:
        return str(root)
    return os.pathsep.join(entries)


def own_pythonpath(tree: Path = REPO_ROOT) -> str:
    """PYTHONPATH naming the tree's own sources -- what the board exports."""
    return os.pathsep.join(
        str(p) for p in sorted((tree / "packages").glob("*/src"))
    )


def run_script(
    script: Path,
    args: Sequence[str] = (),
    *,
    pythonpath: "str | Path | None",
    choice: "str | None" = None,
    cwd: "Path | None" = None,
    extra_env: "dict[str, str] | None" = None,
) -> subprocess.CompletedProcess[str]:
    """Run a script in a child with exactly the given tree-selecting env."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop(CHOICE_ENV, None)
    if pythonpath is not None:
        env["PYTHONPATH"] = str(pythonpath)
    if choice is not None:
        env[CHOICE_ENV] = choice
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, str(script), *args],
        env=env, cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, timeout=300, check=False,
    )
