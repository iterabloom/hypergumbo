# SPDX-License-Identifier: AGPL-3.0-or-later
"""G2 parity column: ``meta["constructed_from"]`` per language (WI-nopod).

A whole class of frameworks is configured by *constructing* an object
(``app = FastAPI()``, ``const app = new Koa()``, ``app := gin.Default()``).
Until the binding is recorded, no YAML pattern can key on it — which is the
defect WI-nopod reported, arrived at by a different route than the one it
described.

Uniform fixture per language: one module-level binding whose value is a call
to a first-party name. An empty cell therefore means a producer gap, never
"this language has no constructors".

Two distinct hole shapes, and the column records which:

* **KEY MISSING** — the analyzer emits a variable symbol and simply does not
  stamp the callee. A bounded, mechanical fix.
* **NO SYMBOL** — the analyzer emits no module-level variable at all, so
  there is nothing to stamp. A prerequisite gap of a different size, and
  lumping it with the first would have made "eight analyzers need the key"
  the plan. Three languages are in this bucket and the count was wrong until
  it was measured.

Run end-to-end rather than analyzer-only. The chain a consumer depends on is
producer → ``Symbol.meta`` → ``meta_match`` → concept, and a column that
stops at the analyzer can read green while the artifact is unchanged — the
failure WI-duguk's parity column shipped with. ``frameworks="all"`` is off
here on purpose: this column measures the *producer*, and the concept-tagging
half is pinned separately by ``test_constructed_from_matching``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core.cli import run_behavior_map

# One module-level binding per language, value = a call to a first-party name.
# (filename, source, bound name, EXPECTED callee).
#
# The expected callee is per-language on purpose. Each language spells
# construction differently — Rust's idiom is an associated function
# (``C::new()``), Go's is a constructor function (``NewC()``), JS uses
# ``new C()`` — so the recorded callee legitimately differs. A single shared
# expectation was silently wrong for Rust until this was split out.
CASES: dict[str, tuple[str, str, str, str]] = {
    "python": ("m.py", "from x import C\n\napp = C()\n", "app", "C"),
    "javascript": ("m.js", "import { C } from './c';\n\nexport const app = new C();\n", "app", "C"),
    "typescript": ("m.ts", "import { C } from './c';\n\nexport const app = new C();\n", "app", "C"),
    "go": ("m.go", "package main\n\nvar app = NewC()\n", "app", "NewC"),
    "swift": ("m.swift", "let app = C()\n", "app", "C"),
    "clojure": ("m.clj", "(def app (make-c))\n", "app", "make-c"),
    "rust": ("lib.rs", "pub static APP: C = C::new();\n", "APP", "C::new"),
    "ruby": ("m.rb", "APP = C.new\n", "APP", "C.new"),
    "php": ("m.php", "<?php\n$app = new C();\n", "app", "C"),
    "elixir": ("m.ex", "defmodule M do\n  @app C.new()\nend\n", "app", "C.new"),
}

# Measured 2026-07-31. Each entry names WHICH of the two shapes it is.
KNOWN_HOLES: dict[str, str] = {
    "ruby": (
        "NO SYMBOL (WI-kavam). The analyzer emits no module-level variable/constant "
        "symbol for `APP = C.new`, so there is nothing to stamp. Emitting "
        "the binding is a prerequisite, and a larger change than adding a "
        "meta key."
    ),
    "php": (
        "NO SYMBOL (WI-kavam). `$app = new C();` at file scope produces no variable "
        "symbol. Same prerequisite gap as ruby."
    ),
    "elixir": (
        "NO SYMBOL (WI-kavam). A module attribute (`@app C.new()`) produces no symbol. "
        "Elixir has no module-level mutable bindings, so the idiomatic "
        "construction site is a module attribute or a function body — "
        "deciding which to model is a design question, not a stamping one."
    ),
}

_MIN_HARD_PASSES = 5


@pytest.fixture(scope="module")
def polyglot_map(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """One pipeline run over a generated polyglot corpus.

    Generated into a temp dir rather than committed under
    ``tests/fixtures/``: the pipeline's own test-path heuristics suppress
    passes under fixture paths, which would make cells vacuously empty.
    """
    root = tmp_path_factory.mktemp("constructed-from")
    for filename, source, _, _expected in CASES.values():
        (root / filename).write_text(source)
    out = root / "bm.json"
    run_behavior_map(
        repo_root=root, out_path=out,
        include_sketch_precomputed=False, progress=False,
    )
    return json.loads(out.read_text())


def _binding_symbol(bm: dict[str, Any], language: str, name: str) -> dict[str, Any] | None:
    for node in bm.get("nodes", []):
        if node.get("language") == language and node.get("name") == name:
            if node.get("kind") in ("variable", "field", "constant"):
                return node
    return None


def _language_params() -> list[Any]:
    """Strict xfail markers — never imperative ``pytest.xfail()``.

    The imperative form can never XPASS, so it records a violation while
    disabling the ratchet meant to close it. A marker fails the run the
    moment a language is fixed, forcing the entry out in the same PR.
    """
    return [
        pytest.param(
            language,
            marks=pytest.mark.xfail(strict=True, reason=KNOWN_HOLES[language]),
        ) if language in KNOWN_HOLES else pytest.param(language)
        for language in sorted(CASES)
    ]


@pytest.mark.parametrize("language", _language_params())
def test_records_constructed_from(polyglot_map: dict[str, Any], language: str) -> None:
    """The bound name carries the callee it was constructed from."""
    _, _, binding, expected = CASES[language]
    symbol = _binding_symbol(polyglot_map, language, binding)
    assert symbol is not None, (
        f"[{language}] no variable symbol for {binding!r} — this is the "
        f"NO SYMBOL hole shape, a prerequisite gap rather than a missing key"
    )
    value = (symbol.get("meta") or {}).get("constructed_from")
    assert value == expected, (
        f"[{language}] constructed_from is {value!r}; expected {expected!r}"
    )


def test_column_is_non_vacuous(polyglot_map: dict[str, Any]) -> None:
    """Enough languages HARD PASS that the check can produce a positive.

    Without this the column could go green by becoming all-xfail — a ratchet
    that is vacuous for the property it exists to measure.
    """
    passing = []
    for language, (_, _, binding, _expected) in CASES.items():
        if language in KNOWN_HOLES:
            continue
        symbol = _binding_symbol(polyglot_map, language, binding)
        if symbol and (symbol.get("meta") or {}).get("constructed_from"):
            passing.append(language)
    assert len(passing) >= _MIN_HARD_PASSES, (
        f"only {len(passing)} hard passes ({sorted(passing)}); the column is "
        f"approaching vacuity"
    )


def test_holes_are_classified_and_scoped() -> None:
    """Every hole names one of the two shapes and a real cause."""
    assert set(KNOWN_HOLES) <= set(CASES)
    for language, reason in KNOWN_HOLES.items():
        assert reason.startswith(("NO SYMBOL", "KEY MISSING")), (
            f"{language}'s hole must declare which shape it is"
        )
        assert len(reason) > 60, f"{language}'s hole needs a measured cause"
