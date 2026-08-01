# SPDX-License-Identifier: AGPL-3.0-or-later
"""One home for decoded ``node.text`` (WI-sarag / WI-vokiz).

35 analyzers carried a private ``_get_node_text`` copy of the same
two-liner — some None-guarded, some not (the half-guarded-population tell:
no contract, only local habit). The fact now lives once, in
``analyze.base.node_own_text``, and the recurrence test here fails the
build if a new private copy appears — the same enforcement shape as
``member_names``' separator linter.
"""
from __future__ import annotations

import re
from pathlib import Path

import tree_sitter_language_pack as tlp

from hypergumbo_core.analyze.base import node_own_text

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_decodes_real_node_text() -> None:
    tree = tlp.get_parser("python").parse(b"x = 1\n")
    assert node_own_text(tree.root_node) == "x = 1\n"


def test_none_text_yields_empty_string() -> None:
    """The WI-vokiz guard, baked into the chokepoint: ``Node.text`` is
    ``bytes | None``, and half the historical copies dereferenced it
    unguarded."""

    class _NoText:
        text = None

    assert node_own_text(_NoText()) == ""  # type: ignore[arg-type]


def test_no_private_copies_remain() -> None:
    """Recurrence linter: a new ``def _get_node_text`` anywhere under
    packages/*/src is a second home for a fact that has exactly one.

    The single permitted exception is css.py's SOURCE-SLICE variant
    (``(node, source)`` — a different fact: it slices file bytes, matching
    ``analyze.base.node_text``'s contract, for re-parsed sub-trees whose
    ``.text`` is unavailable).
    """
    offenders: list[str] = []
    for path in REPO_ROOT.glob("packages/*/src/**/*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"def _get_node_text\(([^)]*)\)", text):
            if "source" not in m.group(1):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        "private _get_node_text copies found — import "
        "hypergumbo_core.analyze.base.node_own_text (as _get_node_text if "
        f"you must) instead of re-declaring the fact: {offenders}"
    )
