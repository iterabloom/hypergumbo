# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-fafol parity: every caller of the attribute-ref helper must anchor.

The defect was not one analyzer getting it wrong — it was FIVE analyzers
getting it identically wrong, because each passed the file pseudo-symbol and
none had a reason to think otherwise. Fixing five call sites without a test
that enumerates them leaves the sixth free to repeat it, and this repo's
standing rule is that per-language work ships with a parity test over the
registry rather than five hand-checked edits.

Precedent for the shape: ``test_touch_heartbeat.py`` asserts every vendor's
per-turn hook sources the heartbeat helper.

STATIC, deliberately. The alternative — analyze a fixture per language — needs
five grammars and five fixtures and would fail for reasons unrelated to
anchoring (a missing grammar, a parse change). This asserts the one property
that made the bug possible: the call site passes ``enclosing_symbols``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "hypergumbo_lang_mainstream"

HELPER = "emit_module_attribute_refs"


def _modules_calling_the_helper() -> list[Path]:
    return sorted(p for p in SRC.glob("*.py") if HELPER + "(" in p.read_text(encoding="utf-8"))


def test_the_helper_is_actually_used_somewhere() -> None:
    """Non-vacuity floor. If the helper were renamed, every parametrised case
    below would silently vanish and the parity test would pass by having
    nothing to check."""
    mods = _modules_calling_the_helper()
    assert len(mods) >= 5, (
        f"expected the five tree-sitter analyzers to call {HELPER}; "
        f"found {[m.name for m in mods]}"
    )


@pytest.mark.parametrize(
    "module", _modules_calling_the_helper(), ids=lambda p: p.stem,
)
def test_every_call_site_anchors_to_the_enclosing_callable(module: Path) -> None:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == HELPER
    ]
    assert calls, f"{module.name} mentions {HELPER} but never calls it"
    for call in calls:
        kwargs = {kw.arg for kw in call.keywords}
        assert "enclosing_symbols" in kwargs, (
            f"{module.name}:{call.lineno} calls {HELPER} without "
            "enclosing_symbols, so its module_attr_ref edges anchor to the "
            "FILE and can never share a caller with a sink — the whole of "
            "INV-fafol. Pass the file's symbols."
        )
