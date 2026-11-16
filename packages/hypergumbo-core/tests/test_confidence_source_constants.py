# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nothing compares or assigns ``Edge.confidence_source`` with a bare literal (WI-daruz).

The bounded-enum's re-evaluation trigger said to promote it "if a consumer
needs to filter edges by source programmatically across the codebase". Four
modules already did, each with a hand-written string. The answer chosen was
named constants in ``ir.py`` rather than a heavyweight registry (the values
carry no per-value metadata a consumer reads); this test keeps the answer true
by failing on a bare vocabulary literal in a comparison, an assignment or a
``confidence_source=`` keyword anywhere in the package sources outside the
constants' own definitions.
"""

from __future__ import annotations

import ast
from pathlib import Path

from hypergumbo_core.ir import VALID_CONFIDENCE_SOURCES

_PACKAGES = Path(__file__).resolve().parents[2]


def _bare_uses() -> list[str]:
    hits: list[str] = []
    for path in sorted(_PACKAGES.glob("*/src/**/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            candidates: list[ast.expr] = []
            if isinstance(node, ast.Compare):
                candidates = [node.left, *node.comparators]
            elif isinstance(node, ast.keyword) and node.arg == "confidence_source":
                candidates = [node.value]
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(
                    (isinstance(t, ast.Attribute) and t.attr == "confidence_source")
                    or (isinstance(t, ast.Name) and t.id == "confidence_source")
                    for t in targets
                ):
                    candidates = [node.value] if node.value is not None else []
            for expr in candidates:
                for leaf in ast.walk(expr):
                    if isinstance(leaf, ast.Constant) and leaf.value in VALID_CONFIDENCE_SOURCES:
                        hits.append(f"{path.name}:{leaf.lineno}")
    return hits


def test_no_bare_confidence_source_literal() -> None:
    assert _bare_uses() == []
