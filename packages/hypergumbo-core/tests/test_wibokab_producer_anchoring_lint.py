# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-bokab (v7) completeness gate: every tree-sitter producer anchors file identity.

A grammar-free, AST-based structural lint over ``packages/hypergumbo-lang-*/src``: every
call to ``compute_stable_id`` (the untyped tier, called as ``<analyzer>.compute_stable_id``)
or ``make_typed_stable_id`` (the typed tier, an imported free function) MUST pass a
``file_stable_id=`` keyword. That is the WI-bokab fold (ADR-0035 §1/§4) that gives
file-resident symbols a per-file identity so same-``(kind, name, qualified_name)`` symbols
in different files hash distinctly.

This is the one check that does NOT depend on any tree-sitter grammar being installed, so
it guarantees completeness across the long tail of analyzers (fennel, janet, smithy, …)
that the integration tests + the multi-language probe don't individually exercise. A
missed call site here = a language whose corpus collisions persist = a future scheme bump,
which ADR-0035 §6 (one atomic bump) forbids.

Exemptions:
  * ``py.py`` — the AST (non-tree-sitter) analyzer threads file identity through
    ``containing_stable_id`` directly (file id for top-level, enclosing-class id for
    methods), via its own ``_compute_stable_id`` and ``make_typed_stable_id(... containing
    ...)``. It is not a tree-sitter producer and is correct without ``file_stable_id``.
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TARGET_CALLS = {"compute_stable_id", "make_typed_stable_id"}
# py.py uses the containing_stable_id idiom (it is the AST analyzer, not tree-sitter).
_EXEMPT_FILENAMES = {"py.py"}


def _called_name(call: ast.Call) -> str | None:
    """The bare callee name for ``foo(...)`` (ast.Name) or ``x.foo(...)`` (ast.Attribute)."""
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _producer_calls_missing_anchor() -> list[str]:
    """Return ``file:line`` for every producer call lacking a ``file_stable_id=`` kwarg."""
    violations: list[str] = []
    found = 0
    src_files = sorted(_REPO_ROOT.glob("packages/hypergumbo-lang-*/src/**/*.py"))
    assert src_files, f"no analyzer sources found under {_REPO_ROOT}/packages/hypergumbo-lang-*/src"
    for path in src_files:
        if path.name in _EXEMPT_FILENAMES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _called_name(node) not in _TARGET_CALLS:
                continue
            found += 1
            if not any(kw.arg == "file_stable_id" for kw in node.keywords):
                rel = path.relative_to(_REPO_ROOT)
                violations.append(f"{rel}:{node.lineno}")
    # Non-vacuous guard: there are dozens of producer call sites; if the scan finds
    # almost none, the matcher silently broke (e.g. a refactor renamed the entrypoints).
    assert found >= 30, f"producer-call scan found only {found} calls — matcher likely broken"
    return violations


def test_every_tree_sitter_producer_threads_file_stable_id() -> None:
    missing = _producer_calls_missing_anchor()
    assert not missing, (
        "WI-bokab: these tree-sitter stable_id producer calls do not pass "
        "file_stable_id= (cross-file collisions persist for these languages):\n  "
        + "\n  ".join(missing)
    )
