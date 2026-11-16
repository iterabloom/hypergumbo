# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every ``UsageContext.kind`` a producer emits is declared (WI-daruz).

``UsageContext.kind`` is a ``Literal``, and nothing enforces a ``Literal`` at
runtime. Its docstring said "don't add a 5th value without splitting", and a
fifth value -- ``library_export`` -- had been emitted by js_ts.py since
2026-01-30 and consumed by frameworks/library-exports.yaml, while the Literal
still listed four, one of which (``data_value``) nothing produced. A
keep-with-trigger nothing watches is a trigger that cannot fire; this test is
the watcher. It reads every ``UsageContext(...)`` / ``UsageContext.create(...)``
call in the analyzer and core sources.
"""

from __future__ import annotations

import ast
import typing
from pathlib import Path

from hypergumbo_core.ir import UsageContext

_PACKAGES = Path(__file__).resolve().parents[2]


def _emitted_kinds() -> tuple[set[str], list[str]]:
    kinds: set[str] = set()
    non_literal: list[str] = []
    for path in sorted(_PACKAGES.glob("*/src/**/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_ctor = isinstance(func, ast.Name) and func.id == "UsageContext"
            is_create = (
                isinstance(func, ast.Attribute) and func.attr == "create"
                and isinstance(func.value, ast.Name) and func.value.id == "UsageContext"
            )
            if not (is_ctor or is_create):
                continue
            for kw in node.keywords:
                if kw.arg == "kind":
                    if isinstance(kw.value, ast.Constant):
                        kinds.add(kw.value.value)
                    else:
                        non_literal.append(f"{path.name}:{node.lineno}")
    return kinds, non_literal


def _declared() -> set[str]:
    hints = typing.get_type_hints(UsageContext)
    return set(typing.get_args(hints["kind"]))


def test_every_emitted_kind_is_declared() -> None:
    kinds, _ = _emitted_kinds()
    assert kinds, "the scan found no producer: the test is reading nothing"
    assert kinds <= _declared()


def test_no_producer_computes_its_kind() -> None:
    """A computed kind would escape the scan above."""
    _, non_literal = _emitted_kinds()
    assert non_literal == []


def test_the_declared_values_are_the_produced_ones_plus_macro() -> None:
    """``macro`` is declared with no producer because qt.yaml's two rules wait
    on one; any OTHER declared-but-unproduced value is dead vocabulary."""
    kinds, _ = _emitted_kinds()
    assert _declared() - kinds == {"macro"}


#: Usage rules that name a kind no producer emits, so they match nothing --
#: WI-dozav. SHRINK-ONLY: fixing a rule removes it here; a new dead rule fails.
_KNOWN_DEAD_USAGE_RULES = {
    ("cowboy.yaml", "^function$", "^init$"),
    ("cowboy.yaml", "^function$", "^(handle|info|terminate)$"),
    ("cowboy.yaml", "^function$",
     "^(allowed_methods|content_types_accepted|content_types_provided)$"),
    ("cowboy.yaml", "^function$", "^websocket_(init|handle|info)$"),
    ("qt.yaml", "^macro$", "^Q_OBJECT$"),
    ("qt.yaml", "^macro$", "^Q_PROPERTY$"),
    ("qt.yaml", "^method$", "^(event|.*Event)$"),
    ("servant.yaml", "^type$",
     "^(Get|Post|Put|Delete|Patch|ReqBody|Capture|QueryParam|Header)$"),
}


def test_framework_usage_rules_name_a_produced_kind() -> None:
    import re

    import yaml

    produced, _ = _emitted_kinds()
    frameworks = _PACKAGES / "hypergumbo-core/src/hypergumbo_core/frameworks"
    dead = set()
    for path in sorted(frameworks.glob("*.yaml")):
        for pattern in yaml.safe_load(path.read_text()).get("patterns") or []:
            usage = pattern.get("usage") or {}
            kind = usage.get("kind")
            if kind and not any(re.search(kind, k) for k in produced):
                dead.add((path.name, kind, usage.get("name")))
    assert dead == _KNOWN_DEAD_USAGE_RULES
