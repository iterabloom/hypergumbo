# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-nanon: a linker must SAY whether it is gated; silence is not consent.

THE DEFECT. ``register_linker`` ends in::

    activation=activation or LinkerActivation(always=True)

so a linker registered without an ``activation=`` argument runs on every repo,
and in the record that is BYTE-IDENTICAL to a linker whose author considered
gating and chose always-on. One value, two meanings: "we decided" and "nobody
decided". Twenty-five of the production linkers were in the second state, twelve
in the first, and nothing in the tree could tell them apart.

This is the project's absent-versus-empty defect wearing its other face. The
usual form is an empty field read as a measurement (``files_analyzed == 0``
stamped ``no_candidate_files``, WI-finij). This is the same substitution in a
PERMISSIVE default: the absent declaration is read as an affirmative one, and
the reading is invisible because it happens inside a ``or`` expression.

WHAT THIS GATE DOES, AND WHAT IT DELIBERATELY DOES NOT. It requires every
``register_linker`` call in a production linker module to pass ``activation=``
explicitly. It does NOT require the declaration to be CORRECT, and it does not
change which linkers run — every one of the twenty-five kept always-on
behaviour to the byte. Tightening an activation gate is the recall-loss
direction (a missed framework detection silently drops real edges and leaves no
hole where a hole would be visible), so narrowing them is a separate,
per-linker, evidence-backed decision that this change only makes POSSIBLE by
producing an exact worklist.

WHY THE TWENTY-FIVE DECLARE VIA ``always_on_unreviewed()`` AND NOT
``LinkerActivation(always=True)``. Writing the latter on all twenty-five would
manufacture twenty-five rationales nobody verified — the fabricated-disclosure
failure ``pass_silence`` already names ("a reason invented on the producer's
behalf ... is worse than none"). The factory is behaviourally identical and
says the true thing: always-on, gating unassessed. It also makes the cohort
greppable, which is what turns "we should look at those someday" into a list.

SCOPE. Production ``linkers/*.py`` only. Ad-hoc registrations inside test
modules keep the documented default: a test pinning priority ordering or cache
behaviour has no opinion about activation, and forcing it to invent one would
be noise, not rigour.
"""
from __future__ import annotations

import ast
from pathlib import Path

_LINKERS = Path(__file__).resolve().parents[1] / "src" / "hypergumbo_core" / "linkers"


def _undeclared_activation(source: str, label: str) -> list[str]:
    """``register_linker`` call sites that pass no ``activation=``, as ``label:line``.

    AST-based on purpose. The two ``@register_linker(`` occurrences in
    ``registry.py`` live inside docstring examples; a line pattern would count
    them as registrations and this gate would be unfailable-for-the-wrong-reason
    from the day it was written.
    """
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute) else None
        )
        if name != "register_linker":
            continue
        if not any(kw.arg == "activation" for kw in node.keywords):
            hits.append(f"{label}:{node.lineno}")
    return hits


class TestEveryProductionLinkerDeclaresActivation:
    """No production linker inherits its activation silently."""

    def test_no_undeclared_activation(self):
        offenders: list[str] = []
        for module_path in sorted(_LINKERS.glob("*.py")):
            offenders.extend(
                _undeclared_activation(
                    module_path.read_text(encoding="utf-8"), module_path.name
                )
            )
        assert offenders == [], (
            "These linkers inherit always=True silently, which is "
            "indistinguishable from choosing it. Pass activation= explicitly "
            "-- always_on_unreviewed() if you have not assessed gating: "
            f"{offenders}"
        )

    def test_gate_can_fail(self):
        """Control: the detector fires on a registration that omits activation.

        A gate that cannot fail is not a gate. The clean result above means
        nothing unless this returns a hit.
        """
        missing = "@register_linker('x', priority=10)\ndef f(ctx): ...\n"
        present = (
            "@register_linker('x', activation=LinkerActivation(always=True))\n"
            "def f(ctx): ...\n"
        )
        assert _undeclared_activation(missing, "probe") == ["probe:1"]
        assert _undeclared_activation(present, "probe") == []
