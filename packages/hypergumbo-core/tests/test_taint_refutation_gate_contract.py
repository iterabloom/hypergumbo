# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-lupav L2: a ``False`` may only be CONSUMED where the coverage gate is wired.

THE INVARIANT, as filed: the §3a walk may return ``False`` — "ran to completion
and accounted for the value at every step" — only when it actually followed the
value at every step. It does not. A *partially* recorded definition (the source
def recorded, a later use invisible because the construct is not modelled —
Go's ``if err := do(); err != nil`` initializer is the documented population)
still exhausts to ``False``. Verified live at dev tip: with the use at the sink
line recorded the walk returns ``True``; with it absent it returns ``False``,
and ``forfeit_refutation=True`` flips that to ``None``.

WHY THIS FILE IS A GUARD AND NOT A FIX. WI-joluk built the remedy —
``cfg.uncovered_call_lines`` reports the call sites no recorded CFG statement
covers, and ``forfeit_refutation`` downgrades a would-be ``False`` to ``None``
for such a function. It is wired to the BARRIER arm only. Wiring it to the §3a
arm today changes nothing observable, and that is not laziness: §3a spells its
call ``_ddg_taint_reaches(...) is True``, so ``False`` and ``None`` collapse
into the same ``adjudicated = False``, whose only consequence is the finding's
``confidence``/``analysis_method`` label. A no-op parameter cannot be given a
behavioural test, and this project does not ship untestable wiring.

THE HAZARD IS THE ORDERING, AND IT WAS LIVING IN A COMMENT. The moment §3a is
granted removal authority — the collapse replaced by a consumption of ``False``,
as PR #214 already did on the barrier arm, where a ``False`` earns ``sanitized``
and a sanitized flow is DROPPED from a claim's violation set — every unearned
``False`` becomes a deleted real finding. The instruction "land the §3a forfeit
in the SAME PR as any §3a removal authority" is correct and was recorded only in
a tracker thread and a source comment. This asserts it instead, so the change
that creates the hazard cannot merge without the gate that answers it.

THE RULE, per call site of ``_ddg_taint_reaches`` inside ``propagate_taint_ddg``:

  * a site that CONSUMES the ``False`` (anything other than ``... is True``)
    MUST pass ``forfeit_refutation``;
  * a site that COLLAPSES it (``... is True``) need not, because ``False`` is
    indistinguishable from ``None`` there.

WHY THE GUARD CARRIES ITS OWN POSITIVE CONTROL. A structural lint that cannot
be shown to fire is indistinguishable from one that matches nothing — this
codebase has already been bitten by a vacuous test guarding the exact line that
was wrong (``test_barren_seed_line_is_skipped``, retired on this same item,
whose assertion passed for an unrelated reason). ``_audit`` is therefore run
over synthetic sources that DO violate the rule, so the failing direction is
exercised in CI rather than demonstrated once by hand.
"""
from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "hypergumbo_core"
_TAINT = _SRC / "taint.py"

_WALK = "_ddg_taint_reaches"
_PROPAGATOR = "propagate_taint_ddg"
_GATE = "forfeit_refutation"
_BARRIER = "barrier_lines"


def _collapsed_calls(tree: ast.AST) -> set[int]:
    """``id()`` of every Call whose result is immediately tested ``is True``.

    Keyed on ``id()`` rather than position because the same Call object is
    reached twice by ``ast.walk`` (once as the Compare's ``left``, once on its
    own); identity is what makes the two encounters one site.
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if len(node.ops) != 1 or not isinstance(node.ops[0], ast.Is):
            continue
        rhs = node.comparators[0]
        if isinstance(rhs, ast.Constant) and rhs.value is True:
            if isinstance(node.left, ast.Call):
                out.add(id(node.left))
    return out


def _audit(source: str) -> list[str]:
    """Report every ``_ddg_taint_reaches`` site that consumes False ungated.

    Returns human-readable violations; empty means the contract holds.
    """
    tree = ast.parse(source)
    violations: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name != _PROPAGATOR:
            continue
        collapsed = _collapsed_calls(fn)
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Name) and node.func.id == _WALK):
                continue
            kwargs = {kw.arg for kw in node.keywords}
            if _GATE in kwargs:
                continue
            if id(node) in collapsed:
                continue
            arm = "barrier" if _BARRIER in kwargs else "section-3a"
            violations.append(
                f"{_WALK} at line {node.lineno} ({arm} arm) consumes the "
                f"walk's False without passing {_GATE}. An unearned False "
                f"deletes a real finding (INV-lupav L2); wire WI-joluk's "
                f"coverage gate in this same change."
            )
    return violations


def test_live_tree_passes() -> None:
    """The shipped ``taint.py`` honours the contract."""
    assert _audit(_TAINT.read_text(encoding="utf-8")) == []


def test_both_walk_sites_are_still_found() -> None:
    """The guard is anchored to real call sites, not matching nothing.

    Without this, deleting or renaming the walk would leave ``_audit``
    trivially green — the vacuous-guard failure mode this file exists to avoid.
    """
    tree = ast.parse(_TAINT.read_text(encoding="utf-8"))
    sites = [
        n for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef) and fn.name == _PROPAGATOR
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name) and n.func.id == _WALK
    ]
    assert len(sites) == 2, (
        f"expected the §3a and barrier arms, found {len(sites)} call sites"
    )
    gated = [s for s in sites if _BARRIER in {kw.arg for kw in s.keywords}]
    assert len(gated) == 1, "exactly one arm passes barrier_lines"


def test_guard_fires_when_section_3a_gains_removal_authority() -> None:
    """POSITIVE CONTROL: §3a consuming a False ungated is reported.

    This is the change that makes INV-lupav L2 dangerous — the collapse
    replaced by a consumption, with no gate wired.
    """
    src = f'''
def {_PROPAGATOR}(x):
    refuted = {_WALK}(fn, srcs, sinks, uses) is False
'''
    found = _audit(src)
    assert len(found) == 1
    assert "section-3a arm" in found[0]
    assert _GATE in found[0]


def test_guard_fires_on_ungated_barrier_arm() -> None:
    """POSITIVE CONTROL: the barrier arm losing its gate is reported.

    The arm where a ``False`` already earns ``sanitized`` and drops a flow
    (PR #214), so an ungated site here is a live falsehood rather than a
    latent one.
    """
    src = f'''
def {_PROPAGATOR}(x):
    ok = {_WALK}(fn, srcs, sinks, uses, {_BARRIER}=bars) is False
'''
    found = _audit(src)
    assert len(found) == 1
    assert "barrier arm" in found[0]


def test_collapsed_and_gated_sites_are_both_accepted() -> None:
    """Neither spelling of a safe site is reported.

    Covers the two ``continue`` arms of ``_audit`` independently: a gated site
    that consumes False, and an ungated site that collapses it.
    """
    src = f'''
def {_PROPAGATOR}(x):
    a = {_WALK}(fn, s, k, u) is True
    b = {_WALK}(fn, s, k, u, {_BARRIER}=bars, {_GATE}=g) is False
'''
    assert _audit(src) == []


def test_walk_calls_outside_the_propagator_are_out_of_scope() -> None:
    """Only ``propagate_taint_ddg``'s own sites are governed.

    The walk is also called from tests and probes, where a raw ``False`` is
    the subject under examination rather than a removal decision.
    """
    src = f'''
def some_other_function(x):
    refuted = {_WALK}(fn, s, k, u) is False
'''
    assert _audit(src) == []
