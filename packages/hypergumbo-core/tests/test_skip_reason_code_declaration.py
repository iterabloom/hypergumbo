# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gate: an analyzer that self-declares a skip must also declare its CODE.

WI-dukoh / ADR-0056 W2. ``skip_reason`` drifted to twenty-eight spellings
across thirty-eight files precisely because nothing ever checked it, and the
drift was invisible on a machine with every grammar installed. A structured
``skip_reason_code`` that the next analyzer is free to omit would drift the
same way and for the same reason — an unconverted producer reads as
``unreported``, which is honest but is a hole, and holes that nothing counts
grow.

AST-based, not line-based: the pattern being matched is a keyword argument in
an ``AnalysisResult(...)`` call, and a line-based grep counts the same words
inside docstrings (``analyzer.py``'s module docstring quotes
``skip_reason="rust-analyzer backend not enabled"`` verbatim, and would be
read as a call site). The linker-activation gate this mirrors learned the same
lesson.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_DIRS = [
    REPO_ROOT / "packages" / pkg / "src"
    for pkg in (
        "hypergumbo-core",
        "hypergumbo-lang-common",
        "hypergumbo-lang-mainstream",
        "hypergumbo-lang-extended1",
        "hypergumbo-lang-rust-analyzer",
    )
]


def _kwarg_names(call: ast.Call) -> set[str]:
    return {kw.arg for kw in call.keywords if kw.arg}


def _offenders_in(source: str, label: str) -> list[str]:
    """Return ``label:line`` for every call passing skip_reason without a code."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        names = _kwarg_names(node)
        if "skip_reason" in names and "skip_reason_code" not in names:
            found.append(f"{label}:{node.lineno}")
    return found


def _iter_source_files():
    for src in SRC_DIRS:
        if src.is_dir():
            yield from sorted(src.rglob("*.py"))


def test_every_self_declared_skip_carries_a_code() -> None:
    offenders: list[str] = []
    for path in _iter_source_files():
        offenders.extend(
            _offenders_in(path.read_text(), str(path.relative_to(REPO_ROOT))),
        )
    assert not offenders, (
        "These calls pass skip_reason= without skip_reason_code=, so the skip "
        "reaches limits.skipped_passes as prose only and a consumer has to "
        "match spellings again:\n  " + "\n  ".join(offenders)
    )


def test_gate_can_fail() -> None:
    """Positive control: a detector that has stopped working must not read clean.

    LIVE.md §1.6 — a control that cannot fail is not a control. The live-tree
    assertion above passes on an empty offender list, which is also what a
    broken walker returns.
    """
    offending = (
        "def f():\n"
        "    return AnalysisResult(skipped=True, skip_reason='grammar missing')\n"
    )
    assert _offenders_in(offending, "synthetic.py") == ["synthetic.py:2"]


def test_gate_accepts_a_declared_code() -> None:
    compliant = (
        "def f():\n"
        "    return AnalysisResult(\n"
        "        skipped=True,\n"
        "        skip_reason='grammar missing',\n"
        "        skip_reason_code=DEPENDENCY_UNAVAILABLE,\n"
        "    )\n"
    )
    assert _offenders_in(compliant, "synthetic.py") == []


@pytest.mark.parametrize("src_dir", SRC_DIRS, ids=lambda p: p.parts[-2])
def test_source_root_exists(src_dir: Path) -> None:
    """A gate that walks a path that has moved reports zero offenders.

    An empty walk is indistinguishable from a clean tree, so pin the roots.
    """
    assert src_dir.is_dir(), f"source root missing: {src_dir}"
