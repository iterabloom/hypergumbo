# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-hazov: static enforcement of ``MetaKeySpec.write_discipline``.

Why this exists
---------------

Seven times between 2026-04 and 2026-08, a meta slot several writers could
reach held a single value and the last writer silently erased the earlier
ones (INV-forim, the BUG-04/05 family, INV-zumin, INV-pojib, INV-virat, the
``verify_claims`` caveat list, and ``framework_patterns`` overwriting
producer-set ``concepts``). Every one was fixed correctly, locally, and in a
way that did nothing to prevent the next — which is the definition of a
missing mechanism rather than a run of bad luck.

:mod:`hypergumbo_core.axis_meta_keys` already governed *which* keys may
exist and *what vocabulary* their values draw from. It was blind to
**arity**: the offending write in INV-virat was a correctly-spelled
assignment of a registered key, so no existing check could have objected.
``write_discipline`` is the arity declaration; this module is what makes it
binding instead of advisory.

The three rules
---------------

1. **No bypass.** A key declared ``merge_union`` or ``producer_primary``
   may not be assigned directly — it must route through
   ``axis_meta_keys.write_meta_key``, where the fold lives exactly once.

2. **Collision-capable keys must declare.** A key written BOTH at
   construction time (a ``meta={...}`` kwarg, or a dict assigned wholesale
   to ``.meta``) AND by post-hoc mutation (``x.meta["k"] = ...``, a pass
   walking records it did not create) is reachable by two writers. It may
   not sit at the ``unaudited`` default. This is the rule that fails
   CLOSED: silence from an author is not evidence of single-writer.

   The discriminator is narrow on purpose, and was validated before it was
   trusted: 80 registered keys are written by more than one *module*, but
   only four are collision-capable, and two of those four are the io keys
   that produced INV-virat and INV-zumin. A measurement that independently
   rediscovers both known defects is a control; a module count would have
   reported 80 mostly-benign hits and taught nobody anything (each of 26
   linkers writing ``framework_dispatch`` on edges it constructs itself has
   26 writers and zero collisions).

3. **No meta-bearing construction followed by wholesale replacement.**
   ``edge = Edge.create(..., access_mode="write"); edge.meta = {...}`` is
   INV-forim exactly. Nine such sites survive on the live tree and all are
   currently harmless — none of them passes a kwarg that lands in ``meta``,
   so there is nothing to destroy. That is safety by coincidence of which
   kwargs those call sites happen to use, and adding one ``access_mode=``
   to any of them reinstates the bug with no test failing. Scoping the rule
   to meta-bearing constructions converts the coincidence into a
   guarantee while requiring zero migration today.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .axis_meta_keys import (
    DISCIPLINE_MERGE_UNION,
    DISCIPLINE_PRODUCER_PRIMARY,
    DISCIPLINE_UNAUDITED,
    find_meta_key,
)

#: Constructor kwargs that land in ``meta`` (see ``ir.Edge.create``). A
#: wholesale ``.meta = {...}`` after a construction passing any of these
#: destroys them.
META_BEARING_KWARGS: frozenset[str] = frozenset({
    "meta", "access_mode", "data_direction", "channel",
})

#: Modules exempt from rule 1 — the chokepoint itself has to assign.
_CHOKEPOINT_MODULES: frozenset[str] = frozenset({"axis_meta_keys.py"})

_DISCIPLINES_NEEDING_THE_CHOKEPOINT = frozenset({
    DISCIPLINE_MERGE_UNION, DISCIPLINE_PRODUCER_PRIMARY,
})


def _source_files(root: Path) -> list[Path]:
    """Every shipped module. Tests are deliberately out of scope — a test
    may construct a deliberately-malformed record to prove a guard fires."""
    return sorted(
        p for src in root.glob("packages/*/src")
        for p in src.rglob("*.py")
    )


def _mutation_key(node: ast.AST) -> str | None:
    """``<expr>.meta["k"] = ...`` -> ``k``. Bypass shape only (rule 1)."""
    if not isinstance(node, ast.Assign):
        return None
    for target in node.targets:
        if (
            isinstance(target, ast.Subscript)
            and isinstance(target.slice, ast.Constant)
            and isinstance(target.slice.value, str)
            and isinstance(target.value, ast.Attribute)
            and target.value.attr == "meta"
        ):
            return target.slice.value
    return None


def _chokepoint_key(node: ast.AST) -> str | None:
    """``write_meta_key(<meta>, "k", ...)`` -> ``k``.

    A post-hoc write routed through the chokepoint is STILL a post-hoc
    write. Counting only the bypass shape would make a key vanish from the
    collision-capable set the moment it was fixed — and rule 2 would then
    stop objecting if someone later down-declared it to ``unaudited``. The
    census must describe the writers that exist, not the ones still doing
    it wrongly.
    """
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "write_meta_key"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        return node.args[1].value
    return None


def _construction_keys(node: ast.AST) -> list[str]:
    """Keys written at construction: a ``meta={...}`` kwarg, or a dict
    literal assigned wholesale to ``.meta``."""
    keys: list[str] = []
    if isinstance(node, ast.Call):
        for kw in node.keywords:
            if kw.arg == "meta" and isinstance(kw.value, ast.Dict):
                keys.extend(
                    k.value for k in kw.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                )
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
        for target in node.targets:
            if isinstance(target, ast.Attribute) and target.attr == "meta":
                keys.extend(
                    k.value for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                )
    return keys


def _scan(root: Path) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Return (mutated, constructed): key -> set of module paths."""
    mutated: dict[str, set[str]] = {}
    constructed: dict[str, set[str]] = {}
    for path in _source_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        rel = str(path.relative_to(root))
        for node in ast.walk(tree):
            key = _mutation_key(node) or _chokepoint_key(node)
            if key is not None:
                mutated.setdefault(key, set()).add(rel)
            for ckey in _construction_keys(node):
                constructed.setdefault(ckey, set()).add(rel)
    return mutated, constructed


def collision_capable_keys(root: Path) -> list[str]:
    """Keys a second writer can demonstrably reach.

    Written both at construction AND by post-hoc mutation. See rule 2 in
    the module docstring for why this discriminator and not a module count.
    """
    mutated, constructed = _scan(root)
    return sorted(set(mutated) & set(constructed))


def _scopes(tree: ast.AST) -> list[ast.AST]:
    """Every function body, plus the module itself.

    Rule 3 must be evaluated PER SCOPE. A module-wide name scan reports
    that ``sym`` was bound to a meta-bearing construction somewhere in the
    file and then flags an unrelated ``for sym in analysis.symbols`` loop
    in another function — measured, not hypothetical: that false positive
    is what a module-wide first draft of this check produced on ``go.py``.
    """
    return [tree] + [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _meta_bearing_constructions(scope: ast.AST) -> set[str]:
    """Names bound, WITHIN this scope, to a constructor call passing a
    kwarg that lands in ``meta``."""
    names: set[str] = set()
    for node in ast.walk(scope):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if not any(kw.arg in META_BEARING_KWARGS for kw in node.value.keywords):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def find_discipline_drift(root: Path) -> list[str]:
    """Every violation of the three rules, as human-readable offender lines."""
    offenders: list[str] = []

    for path in _source_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        rel = str(path.relative_to(root))
        exempt = path.name in _CHOKEPOINT_MODULES

        # Rule 1 — direct assignment bypassing the chokepoint.
        if not exempt:
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                key = _mutation_key(node)
                if key is None:
                    continue
                spec = find_meta_key(key)
                if (
                    spec is not None
                    and spec.write_discipline in _DISCIPLINES_NEEDING_THE_CHOKEPOINT
                ):
                    offenders.append(
                        f"{rel}:{node.lineno}: meta key '{key}' is declared "
                        f"{spec.write_discipline} — assign it through "
                        f"axis_meta_keys.write_meta_key() so the fold stays "
                        f"in one place (INV-hazov rule 1)"
                    )

        # Rule 3 — wholesale replacement of a POPULATED meta dict, per scope.
        seen_lines: set[int] = set()
        for scope in _scopes(tree):
            bearing = _meta_bearing_constructions(scope)
            if not bearing:
                continue
            for node in ast.walk(scope):
                if not isinstance(node, ast.Assign):
                    continue
                # An empty literal is an INITIALISATION, not a replacement:
                # `if sym.meta is None: sym.meta = {}` destroys nothing.
                if not isinstance(node.value, ast.Dict) or not node.value.keys:
                    continue
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and target.attr == "meta"
                        and isinstance(target.value, ast.Name)
                        and target.value.id in bearing
                        and node.lineno not in seen_lines
                    ):
                        seen_lines.add(node.lineno)
                        offenders.append(
                            f"{rel}:{node.lineno}: '{target.value.id}.meta = "
                            f"{{...}}' destroys the meta-bearing kwargs "
                            f"(access_mode / channel / meta) passed at "
                            f"construction — pass them via meta= instead "
                            f"(INV-hazov rule 3, INV-forim)"
                        )

    # Rule 2 — a key two writers can reach may not sit at the default.
    for key in collision_capable_keys(root):
        spec = find_meta_key(key)
        if spec is None:
            offenders.append(
                f"meta key '{key}' is written both at construction and by "
                f"post-hoc mutation but is not registered in "
                f"axis_meta_keys.META_KEYS (INV-hazov rule 2)"
            )
        elif spec.write_discipline == DISCIPLINE_UNAUDITED:
            offenders.append(
                f"meta key '{key}' is reachable by two writers "
                f"(construction + mutation) but declares no write_discipline "
                f"(INV-hazov rule 2) — silence is not evidence of "
                f"single-writer"
            )

    return sorted(offenders)


def unaudited_key_count(root: Path) -> int:
    """Visible debt: registered keys still at the default.

    Reported rather than hidden, per the No Weasel Words rule — 'explicit
    gaps over implicit completeness'. Landing the mechanism does not audit
    all 84 keys and this number says so out loud.
    """
    from .axis_meta_keys import META_KEYS

    del root
    return sum(
        1 for spec in META_KEYS
        if spec.write_discipline == DISCIPLINE_UNAUDITED
    )
