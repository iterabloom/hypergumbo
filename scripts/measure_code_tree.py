# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which tree's hypergumbo does a measurement script measure? Chosen, never inherited.

THE DEFECT THIS CLOSES (WI-tumog). A measurement script that lives in a tree
used to put that tree's ``packages/*/src`` at ``sys.path[0]``. ``sys.path``
insertion beats ``PYTHONPATH``, so an A/B whose baseline arm was selected by a
baseline PYTHONPATH -- measurement 0003's documented shape -- ran the SCRIPT'S
tree in both arms whenever the copy invoked was not the editable install's
tree: a worktree copy, a clone, an interpreter without the editable install.
WI-valav's pretix run reported a taint delta of 0 that way, exit 0, plausible
numbers. ``measure-narrowing-headroom.py`` and ``measure-catalogue-exposure.py``
inserted unconditionally, so for them PYTHONPATH never decided anything, and
since they pinned only core, any PYTHONPATH arm was a MIXED tree.

TWO SIGNALS, AND WHY NEITHER SIMPLY WINS. Where the script lives is a signal
("measure the code beside me"), and so is an explicit PYTHONPATH ("measure this
code"). Each precedence order is right for one documented workflow and wrong
for the other: the board (``decontend/slot``) pins PYTHONPATH to the worktree it
runs in, so "PYTHONPATH wins" would make a baseline copy of the script, run
from that worktree, measure the SUBJECT -- the same silent wrong arm, reversed.
So when the two disagree this module REFUSES (exit 2) and names both, and the
caller chooses with ``HG_MEASURE_TREE=script`` or ``HG_MEASURE_TREE=pythonpath``.
When they agree, or PYTHONPATH supplies no package in scope, nothing is asked.

A CONFLICT IS DECIDED PER PACKAGE, BEFORE ANY IMPORT. For each package the
script's tree ships (within the script's scope), the first PYTHONPATH entry
holding that package is compared with the tree's own ``src`` directory. A
PYTHONPATH entry with no hypergumbo package in it -- a test helper directory,
say -- is not a conflict. ``python -E`` / ``-I`` ignore PYTHONPATH, so under
them it cannot conflict either.

THE PROVENANCE LINE IS THE CONTROL, AND IT REFUSES A MIXED TREE. ``announce``
prints, before any number, the tree the code under test resolves to and each
package's directory, and says when that is NOT the script's tree and who chose
it. A run is the measurement of ONE tree, so if the packages in scope resolve
to more than one tree (PYTHONPATH chosen but providing only core; a lone copy
of a script under an interpreter that mixes installs) it refuses rather than
report a number belonging to neither arm. Resolution uses ``PathFinder`` over
the same ``sys.path`` the imports use, without importing, so the refusal
happens before a mixed tree's code can run; a package ALREADY imported is
reported by its own ``__spec__``, i.e. what actually ran.

WHAT THIS DOES NOT DO. It does not record the tree's git commit (a ``git
archive`` export has none; the path is what distinguishes two arms on one box).
It does not cover scripts that never pin a tree -- those import whatever the
interpreter resolves and are listed with their reasons on WI-tumog. It is
standard library only, because it runs before any hypergumbo import is safe.
"""
from __future__ import annotations

import importlib.machinery
import os
import pkgutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, MutableSequence, NoReturn, Sequence, TextIO

#: The selector read when the script's tree and PYTHONPATH disagree.
CHOICE_ENV = "HG_MEASURE_TREE"
_CHOICES = ("script", "pythonpath")

#: Which packages a script's run imports, by import name. ``analysis`` is the
#: analyzer under measurement (core plus every language package); ``core`` is
#: for scripts that import nothing else.
SCOPES: Mapping[str, Callable[[str], bool]] = {
    "analysis": lambda name: (
        name == "hypergumbo_core" or name.startswith("hypergumbo_lang_")
    ),
    "core": lambda name: name == "hypergumbo_core",
}


@dataclass(frozen=True)
class Pin:
    """What ``pin`` decided; ``announce`` reports it."""

    script_tree: Path
    scope: str  # bounded: a key of SCOPES
    choice: str  # "" (no conflict, nothing asked), "script" or "pythonpath"
    pinned: tuple[Path, ...]


def tree_of(package_dir: Path) -> Path:
    """The tree a resolved package directory belongs to.

    ``<tree>/packages/<dist>/src/<pkg>`` resolves to ``<tree>``; anything else
    (a bare PYTHONPATH directory, a site-packages install) to the directory
    the package was found in.
    """
    entry = package_dir.parent
    if entry.name == "src" and entry.parent.parent.name == "packages":
        return entry.parent.parent.parent
    return entry


def _own_sources(tree: Path, wanted: Callable[[str], bool]) -> dict[str, Path]:
    """Import name -> ``src`` directory, for each in-scope package the tree ships."""
    out: dict[str, Path] = {}
    for init in sorted((tree / "packages").glob("*/src/*/__init__.py")):
        if wanted(init.parent.name):
            out[init.parent.name] = init.parent.parent
    return out


def _provider(entries: Sequence[str], name: str) -> "Path | None":
    """The first PYTHONPATH entry that would supply ``name``."""
    for entry in entries:
        base = Path(entry)
        if (base / name / "__init__.py").is_file() or (base / f"{name}.py").is_file():
            return base
    return None


def _refuse(lines: Sequence[str]) -> NoReturn:
    print("\n".join(lines), file=sys.stderr)
    raise SystemExit(2)


def pin(
    script_file: "str | Path",
    scope: str,
    *,
    environ: "Mapping[str, str] | None" = None,
    path: "MutableSequence[str] | None" = None,
    ignore_environment: "bool | None" = None,
) -> Pin:
    """Put the script's tree first on ``path``, unless the caller chose otherwise.

    Refuses (``SystemExit(2)``) when PYTHONPATH supplies an in-scope package
    from a directory other than the script's tree and ``HG_MEASURE_TREE`` does
    not say which to use, or when ``HG_MEASURE_TREE`` holds an unknown value.
    Nothing is inserted before the decision, so a refusal leaves ``path`` as
    it was.
    """
    environ = os.environ if environ is None else environ
    path = sys.path if path is None else path
    if ignore_environment is None:
        ignore_environment = bool(sys.flags.ignore_environment)
    script = Path(script_file).resolve()
    tree = script.parents[1]
    own = _own_sources(tree, SCOPES[scope])

    choice = environ.get(CHOICE_ENV, "")
    if choice and choice not in _CHOICES:
        _refuse([
            f"{script.name}: {CHOICE_ENV}={choice!r} is not one of "
            f"{', '.join(_CHOICES)} (WI-tumog).",
        ])

    entries = (
        [] if ignore_environment
        else [e for e in environ.get("PYTHONPATH", "").split(os.pathsep) if e]
    )
    conflicts: dict[str, Path] = {}
    for name, src in own.items():
        provider = _provider(entries, name)
        if provider is not None and provider.resolve() != src.resolve():
            conflicts[name] = provider
    if conflicts and not choice:
        _refuse([
            f"{script.name}: WHICH CODE IS MEASURED IS AMBIGUOUS (WI-tumog).",
            f"  this script's tree:  {tree}",
            "  PYTHONPATH supplies a different copy of:",
            *(f"    {name:<32} {prov}" for name, prov in sorted(conflicts.items())),
            "  Choose one explicitly:",
            f"    {CHOICE_ENV}=script      measure the code beside this script",
            f"    {CHOICE_ENV}=pythonpath  measure the code PYTHONPATH selects",
            "  or run the copy of this script that lives in the tree you mean"
            " to measure, with no PYTHONPATH naming another tree.",
        ])

    if choice == "pythonpath":
        return Pin(script_tree=tree, scope=scope, choice=choice, pinned=())
    pinned = tuple(dict.fromkeys(own.values()))
    path[:0] = [str(p) for p in pinned]
    return Pin(script_tree=tree, scope=scope, choice=choice, pinned=pinned)


def _locate(name: str, path: Sequence[str]) -> "Path | None":
    """The directory ``name`` imports from (or did), without importing it."""
    module = sys.modules.get(name) if path is sys.path else None
    spec = getattr(module, "__spec__", None) if module is not None else None
    if spec is None:
        spec = importlib.machinery.PathFinder.find_spec(name, list(path))
    if spec is None:
        return None
    if spec.origin and spec.origin not in ("namespace", "built-in", "frozen"):
        origin = Path(spec.origin)
        return origin.parent if origin.name == "__init__.py" else origin
    return Path(next(iter(spec.submodule_search_locations or [""])))


def announce(
    pinned: Pin,
    prefix: str,
    *,
    stream: "TextIO | None" = None,
    path: "Sequence[str] | None" = None,
) -> Path:
    """Print which tree the code under test resolves to; refuse a mixed tree.

    Returns that tree. Refuses (``SystemExit(2)``) when ``hypergumbo_core``
    does not resolve at all, or when the in-scope packages resolve to more than
    one tree -- before printing anything, so no provenance line ever heads a
    run that does not happen.
    """
    stream = sys.stdout if stream is None else stream
    path = sys.path if path is None else path
    wanted = SCOPES[pinned.scope]
    names = sorted(
        {"hypergumbo_core"}
        | {m.name for m in pkgutil.iter_modules(list(path)) if wanted(m.name)}
    )
    where: dict[str, Path] = {}
    for name in names:
        found = _locate(name, path)
        if found is None:
            _refuse([
                f"{prefix} {name} does not resolve on sys.path: nothing to"
                " measure (WI-tumog).",
            ])
        where[name] = found.resolve()
    trees = {name: tree_of(d) for name, d in where.items()}
    if len(set(trees.values())) > 1:
        _refuse([
            f"{prefix} MIXED TREE: the code under test resolves to more than"
            " one tree, so a number from this run belongs to neither"
            " (WI-tumog). Refusing.",
            *(f"{prefix}   {n:<32} {where[n]}" for n in names),
            f"{prefix}   Put every in-scope package of ONE tree on PYTHONPATH,"
            f" or set {CHOICE_ENV}=script.",
        ])
    tree = next(iter(trees.values()))
    script_tree = pinned.script_tree.resolve()
    relation = (
        "this script's tree" if tree == script_tree
        else f"NOT this script's tree ({script_tree})"
    )
    if pinned.choice:
        relation += f"; chosen by {CHOICE_ENV}={pinned.choice}"
    print(f"{prefix} code under test: {tree}  ({relation})", file=stream)
    for name in names:
        print(f"{prefix}   {name:<32} {where[name]}", file=stream)
    return tree
