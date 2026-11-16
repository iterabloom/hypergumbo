# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every import of a module that is stdlib only ABOVE our floor must be guarded.

THE HAZARD. ``tomllib`` entered the standard library in Python **3.11**; every
package in this repo declares ``requires-python = ">=3.10"``. A bare top-level
``import tomllib`` in a broadly imported module (``user_config.py`` is one)
does not fail gracefully: on 3.10 it takes thousands of tests down with
``ModuleNotFoundError: No module named 'tomllib'``.

A CONVENTION NEEDS A GATE. ``profile.py``, ``linkers/subprocess_cli.py``,
``py_deps.py``, ``user_config.py`` and ``scripts/generate-architecture`` carry
``try: import tomllib / except ImportError: import tomli as tomllib``; one
author skipping the pattern is enough, and nothing else checks it.

WHY A STATIC CHECK. Per-PR CI runs a single newer Python, so a 3.10-only
failure shows up only on the nightly matrix's 3.10 leg, whose verdict can be
masked by an aggregate status attached to an older commit (INV-bozid;
``ci-debug cron-status`` shows the individual legs).

THE RULE. An import of a FLOOR-EXCEEDING stdlib module must sit inside a ``try``
whose handler catches ``ImportError`` (``ModuleNotFoundError`` is a subclass, and
either spelling is accepted). Module level or function level both count — an
unguarded function-local import merely fails later and narrower, not never.

The watched set is DERIVED FROM THE DECLARED FLOOR, not hand-listed: it holds
modules whose stdlib debut is above ``requires-python``. Raising the floor to
3.11 makes ``tomllib`` legal and this table shrinks on the commit that raises it.
"""

# covers: packages/*/src/*, scripts/*, packages/*/pyproject.toml

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Module -> the Python minor version that first shipped it in the stdlib.
_STDLIB_DEBUT = {
    "tomllib": (3, 11),
    "graphlib": (3, 9),
    "zoneinfo": (3, 9),
}


def _declared_floor() -> tuple[int, int]:
    """The LOWEST ``requires-python`` across shipped packages.

    Read rather than assumed: the whole point is that the guard requirement is a
    consequence of the declared floor, so a package that lowers it widens this
    gate automatically.
    """
    floors = []
    for pyproject in sorted(REPO_ROOT.glob("packages/*/pyproject.toml")):
        m = re.search(
            r'requires-python\s*=\s*"[><=~^]*\s*(\d+)\.(\d+)',
            pyproject.read_text(encoding="utf-8"),
        )
        if m:
            floors.append((int(m.group(1)), int(m.group(2))))
    assert floors, "no package declares requires-python"
    return min(floors)


def _watched() -> set[str]:
    floor = _declared_floor()
    return {name for name, debut in _STDLIB_DEBUT.items() if debut > floor}


def _python_sources() -> list[Path]:
    files = [
        p for p in REPO_ROOT.glob("packages/*/src/**/*.py")
        if "__pycache__" not in p.parts
    ]
    # scripts/ too: `generate-architecture` is an extensionless PYTHON script
    # and it carries the guard, so the directory is in scope. The shebang is
    # READ to include a file rather than folded into one exclusion expression:
    # `A and B and C or D` binds as `(A and B and C) or D`, and a slip there
    # excludes exactly the python scripts this means to add. A gate that
    # silently covers nothing is the failure this file is about.
    for p in sorted(REPO_ROOT.glob("scripts/*")):
        if not p.is_file():
            continue
        if p.suffix == ".py":
            files.append(p)
            continue
        if p.suffix:
            continue
        try:
            first = p.read_bytes().split(b"\n", 1)[0]
        except OSError:  # pragma: no cover - unreadable file
            continue
        if first.startswith(b"#!") and b"python" in first:
            files.append(p)
    return files


def _unguarded_imports(tree: ast.AST, watched: set[str]) -> list[tuple[str, int]]:
    """Every import of a watched module NOT lexically inside a try/except-ImportError."""
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        catches_import = any(
            h.type is None
            or (isinstance(h.type, ast.Name) and h.type.id in
                ("ImportError", "ModuleNotFoundError"))
            or (isinstance(h.type, ast.Tuple) and any(
                isinstance(e, ast.Name)
                and e.id in ("ImportError", "ModuleNotFoundError")
                for e in h.type.elts))
            for h in node.handlers
        )
        if not catches_import:
            continue
        for child in ast.walk(node):
            guarded.add(id(child))

    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        names = (
            [a.name.split(".")[0] for a in node.names]
            if isinstance(node, ast.Import)
            else [(node.module or "").split(".")[0]]
        )
        hit = [n for n in names if n in watched]
        if hit and id(node) not in guarded:
            bad.append((hit[0], node.lineno))
    return bad


class TestFloorExceedingImportsAreGuarded:
    def test_the_watched_set_is_non_empty_for_the_declared_floor(self) -> None:
        """CONTROL: if the floor rises past every entry this gate checks nothing.

        Without this, raising ``requires-python`` to 3.12 would empty the watched
        set and turn the test below into a green tick over no assertion at all.
        """
        floor = _declared_floor()
        assert floor == (3, 10), (
            f"declared floor moved to {floor}; re-check _STDLIB_DEBUT — this "
            f"gate is only meaningful while modules debut above the floor"
        )
        assert "tomllib" in _watched()

    def test_no_production_source_imports_tomllib_unguarded(self) -> None:
        offenders = []
        watched = _watched()
        for path in _python_sources():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            for mod, line in _unguarded_imports(tree, watched):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line}: {mod}")
        assert not offenders, (
            "stdlib modules newer than the declared requires-python floor must be "
            "imported inside try/except ImportError with a backport fallback:\n  "
            + "\n  ".join(offenders)
        )

    @pytest.mark.parametrize(
        "rel",
        [
            "packages/hypergumbo-core/src/hypergumbo_core/profile.py",
            "packages/hypergumbo-core/src/hypergumbo_core/linkers/subprocess_cli.py",
            "packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/py_deps.py",
            "packages/hypergumbo-core/src/hypergumbo_core/user_config.py",
        ],
    )
    def test_the_known_tomllib_sites_are_all_guarded(self, rel: str) -> None:
        """POSITIVE CONTROL on the detector itself.

        The check above passes trivially if ``_unguarded_imports`` never fires.
        These four files DO import ``tomllib``; naming them means the walker is
        demonstrably reaching real imports rather than finding nothing.
        """
        path = REPO_ROOT / rel
        source = path.read_text(encoding="utf-8")
        assert "tomllib" in source, f"{rel} no longer imports tomllib"
        assert not _unguarded_imports(ast.parse(source), {"tomllib"})


class TestTheBackportIsDeclared:
    """A guard that falls back to an UNDECLARED package still fails on 3.10."""

    @pytest.mark.parametrize(
        "pkg", ["hypergumbo-core", "hypergumbo-lang-mainstream"],
    )
    def test_packages_importing_tomllib_declare_tomli_below_311(self, pkg: str) -> None:
        src = REPO_ROOT / "packages" / pkg / "src"
        imports_it = any(
            "tomllib" in p.read_text(encoding="utf-8", errors="ignore")
            for p in src.glob("**/*.py")
        )
        assert imports_it, f"{pkg} no longer imports tomllib; drop it from this list"
        pyproject = (REPO_ROOT / "packages" / pkg / "pyproject.toml").read_text()
        assert "tomli" in pyproject and 'python_version < "3.11"' in pyproject, (
            f"{pkg} imports tomllib but does not declare the tomli backport for "
            f"<3.11, so its fallback resolves to nothing on the declared floor"
        )
