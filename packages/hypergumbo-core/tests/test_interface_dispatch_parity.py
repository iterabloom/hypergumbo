# SPDX-License-Identifier: AGPL-3.0-or-later
"""G2 parity column: interface-method → impl-method dispatch, per language.

INV-tihim was filed as "one fact, two homes" — the member-name separator
divergence between ``linkers/containment`` and ``linkers/type_hierarchy``.
That is real, and it is *Rust's* cause. Probing four languages found three
unrelated causes; probing sixteen found **eight**. The filing generalized
from the one cause in hand, which is the failure mode this column exists to
make impossible: an empty cell here means an analyzer/linker gap, not
construct-absence, because every language gets the same injected fixture.

Why end-to-end rather than analyzer-only
----------------------------------------
WI-duguk built a parity column that read green for Swift while the live
reverse slice was unmoved, because the column measured the analyzer in
isolation and the defect was in a *linker* two packages away. This column
therefore asserts on the **emitted artifact** after the full pipeline: the
question is not "did the analyzer emit an interface?" but "does a
``dispatches_to`` edge from the abstract type's method to the implementor's
method exist in the behavior map?" — which is the thing a consumer sees.

Why one shared run
------------------
Sixteen full-pipeline invocations is ~30s; one polyglot run is ~2s and the
per-language assertions stay independently xfail-able. The corpus is
generated into ``tmp_path_factory`` rather than committed under
``tests/fixtures/`` for two measured reasons: the pipeline's own test-path
heuristics suppress linkers under fixture paths (byte-identical input, 4
markers at an ordinary path and 0 under ``tests/fixtures/``), and a gate
that deliberately preserves defects cannot host its corpus inside the tree
the project ratchets against itself.

Holes are ``pytest.mark.xfail(strict=True)``, never imperative
``pytest.xfail()`` — the imperative form can never XPASS, so it would record
each violation while silently disabling the ratchet meant to close it. A
fixed language XPASSes and forces its entry's removal in the same PR.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core.cli import run_behavior_map

# One abstract type declaring one required method, and one concrete
# implementor of it. Uniform by construction: same shape, same names, so an
# empty cell cannot be explained by the fixture.
CASES: dict[str, tuple[str, str]] = {
    "java": ("S.java",
             "interface Shape { int area(); }\n"
             "class Square implements Shape { public int area(){ return 1; } }\n"),
    "csharp": ("S.cs",
               "interface IShape { int Area(); }\n"
               "class Square : IShape { public int Area(){ return 1; } }\n"),
    "go": ("s.go",
           "package main\ntype Shape interface { Area() int }\n"
           "type Square struct{}\nfunc (s Square) Area() int { return 1 }\n"),
    "typescript": ("s.ts",
                   "interface Shape { area(): number; }\n"
                   "class Square implements Shape { area(){ return 1; } }\n"),
    "php": ("s.php",
            "<?php\ninterface Shape { public function area(); }\n"
            "class Square implements Shape { public function area(){ return 1; } }\n"),
    "kotlin": ("S.kt",
               "interface Shape { fun area(): Int }\n"
               "class Square : Shape { override fun area(): Int { return 1 } }\n"),
    "rust": ("lib.rs",
             "pub trait Shape { fn area(&self) -> i32; }\npub struct Square;\n"
             "impl Shape for Square { fn area(&self) -> i32 { 1 } }\n"),
    "scala": ("S.scala",
              "trait Shape { def area(): Int }\n"
              "class Square extends Shape { def area(): Int = 1 }\n"),
    "swift": ("s.swift",
              "protocol Shape { func area() -> Int }\n"
              "class Square: Shape { func area() -> Int { return 1 } }\n"),
    "cpp": ("s.cpp",
            "class Shape { public: virtual int area() = 0; };\n"
            "class Square : public Shape { public: int area() override { return 1; } };\n"),
    "dart": ("s.dart",
             "abstract class Shape { int area(); }\n"
             "class Square implements Shape { int area() => 1; }\n"),
    "groovy": ("S.groovy",
               "interface Shape { int area() }\n"
               "class Square implements Shape { int area(){ 1 } }\n"),
    "ruby": ("s.rb",
             "module Shape\n  def area; end\nend\n"
             "class Square\n  include Shape\n  def area; 1; end\nend\n"),
    "python": ("s.py",
               "from abc import ABC, abstractmethod\n\n\nclass Shape(ABC):\n"
               "    @abstractmethod\n    def area(self) -> int: ...\n\n\n"
               "class Square(Shape):\n    def area(self) -> int:\n        return 1\n"),
    "solidity": ("S.sol",
                 "interface Shape { function area() external returns (uint); }\n"
                 "contract Square is Shape { function area() external returns (uint)"
                 " { return 1; } }\n"),
    "elixir": ("s.ex",
               "defmodule Shape do\n  @callback area() :: integer\nend\n\n"
               "defmodule Square do\n  @behaviour Shape\n  def area, do: 1\nend\n"),
}

# Measured 2026-07-31, one cause per entry. Each is a strict xfail: fixing a
# language XPASSes its cell and forces removal of the entry in the same PR.
#
# Note how little these share. The item that produced this column named ONE
# of them. Three of the eight are producer gaps, three are vocabulary gaps in
# a consumer, one is an unrelated analyzer crash, one is an anchoring choice.
KNOWN_HOLES: dict[str, str] = {
    "rust": (
        "COARSE, not absent. linkers/type_hierarchy splits qualified member "
        "names on '#' and '.' but not '::', while linkers/containment's "
        "_SEPARATORS knows all three — so the shared linker cannot see Rust at "
        "all, and linkers/rust_trait_dispatch covers the gap by anchoring "
        "dispatch at the TRAIT (trait:Shape -> Square::area) rather than at the "
        "trait's method requirement. INV-tihim."
    ),
    "php": (
        "PRODUCER. A same-file `interface` is emitted as kind='external_symbol' "
        "(the analyzer treats it as third-party), its method is emitted BARE "
        "('area', no owner) and rooted under the file, and the relationship is "
        "`extends`->external_symbol rather than `implements`->interface. Three "
        "defects stacked; none of them the separator. INV-tihim."
    ),
    "cpp": (
        "PRODUCER. Abstract methods are not emitted at all — the analyzer keeps "
        "only methods with a body, so `virtual int area() = 0;` vanishes and "
        "there is no parent method to dispatch FROM. `modifiers` is also empty "
        "on every cpp symbol, so the abstract-vs-concrete distinction the "
        "type-family predicate reads is unavailable. INV-tihim."
    ),
    "kotlin": (
        "ANALYZER CRASH, unrelated to dispatch. The Kotlin analyzer emits ZERO "
        "type symbols when a file holds 2+ bodied type declarations, "
        "re-emitting the first method as a top-level function (WI-rufub) — and this "
        "fixture has exactly that shape. Each construct is correct in "
        "isolation. Pre-existing (reproduces at 9f0a163833^). This cell cannot "
        "go green until that is fixed; it is not a dispatch defect."
    ),
    "dart": (
        "PRODUCER (WI-lahub). Both types and both methods are emitted correctly with "
        "dot-qualified names, but the analyzer emits NO inheritance edge at all "
        "for `class Square implements Shape` — so type_hierarchy has nothing to "
        "build its maps from."
    ),
    "solidity": (
        "CONSUMER VOCABULARY (INV-nosoz). Everything is emitted correctly, but the edge is "
        "`inherits`, and linkers/type_hierarchy branches inline on "
        "edge_type == 'extends' / == 'implements' — it never consults the "
        "registry's INHERITANCE_EDGE_TYPES, which does contain `inherits`. "
        "Methods are also kind='function' rather than 'method'."
    ),
    "ruby": (
        "CONSUMER VOCABULARY (INV-nosoz). The mixin edge is `includes`, which is registered "
        "on AXIS_RELATIONSHIP but is absent from the registry's "
        "INHERITANCE_EDGE_TYPES — so no consumer built on that constant sees it "
        "either. Ruby also mixes separators within one language "
        "('Shape.area' vs 'Square#area')."
    ),
    "elixir": (
        "PRODUCER (WI-vitas). `@callback area() :: integer` produces no symbol at all, so "
        "the behaviour's required function does not exist, and `@behaviour "
        "Shape` produces no edge. Elixir behaviours are the language's "
        "interface mechanism and are wholly unmodelled."
    ),
}

# Languages deliberately outside this column, with the reason. Declared as
# DATA so the scope is checkable rather than implied: a gate's "N of N green"
# is scoped to what its fixtures execute, and reporting it unscoped is an
# overstatement even when every number is accurate.
NOT_APPLICABLE: dict[str, str] = {
    "bash": "no type system; no abstract-type construct exists",
    "c": "no interface/abstract-type construct (structs carry no methods)",
    "sql": "declarative; no polymorphic dispatch construct",
    "wgsl": "shader language; no interface construct",
}

_MIN_HARD_PASSES = 6


@pytest.fixture(scope="module")
def polyglot_map(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """One pipeline run over a generated polyglot corpus.

    Module-scoped and generated into a temp dir rather than committed: the
    pipeline suppresses linkers under ``tests/fixtures/`` paths, which would
    make several cells vacuously green.
    """
    root = tmp_path_factory.mktemp("iface-dispatch")
    for filename, source in CASES.values():
        (root / filename).write_text(source)
    out = root / "bm.json"
    run_behavior_map(
        repo_root=root, out_path=out,
        include_sketch_precomputed=False, progress=False,
    )
    return json.loads(out.read_text())


def _has_method_to_method_dispatch(bm: dict[str, Any], language: str) -> bool:
    """Does *language* carry an abstract-method → impl-method dispatch edge?

    Deliberately asserts on the METHOD-to-METHOD shape rather than "any
    dispatches_to edge touching this language". Rust's type-anchored edge
    (trait -> method) would satisfy the looser check while leaving the
    language materially different from every other one — a positive that
    would hide the defect it is meant to surface.
    """
    nodes = {n["id"]: n for n in bm.get("nodes", [])}
    for edge in bm.get("edges", []):
        if edge.get("type") != "dispatches_to":
            continue
        src = nodes.get(edge.get("src"))
        dst = nodes.get(edge.get("dst"))
        if src is None or dst is None:
            continue
        if src.get("language") != language or dst.get("language") != language:
            continue
        if src["kind"] in ("method", "function") and dst["kind"] in ("method", "function"):
            return True
    return False


def _language_params() -> list[Any]:
    """Parametrize with STRICT xfail markers, not an imperative skip.

    ``pytest.xfail(...)`` called inside the test body can never XPASS, so it
    would record each violation while silently disabling the ratchet meant to
    close it. A marker XPASSes the moment a language is fixed, which fails the
    run and forces the entry's removal in the same PR.
    """
    return [
        pytest.param(
            language,
            marks=pytest.mark.xfail(strict=True, reason=KNOWN_HOLES[language]),
        ) if language in KNOWN_HOLES else pytest.param(language)
        for language in sorted(CASES)
    ]


@pytest.mark.parametrize("language", _language_params())
def test_emits_interface_dispatch(polyglot_map: dict[str, Any], language: str) -> None:
    """Each language links its abstract type's method to the implementation."""
    assert _has_method_to_method_dispatch(polyglot_map, language), (
        f"[{language}] no method->method `dispatches_to` edge. Either fix the "
        f"gap or record it in KNOWN_HOLES with its measured cause."
    )


def test_liveness_floor(polyglot_map: dict[str, Any]) -> None:
    """The corpus was actually analyzed — guards a vacuous green.

    A shrink-only or xfail-heavy gate passes trivially if the run produced
    nothing. Pinned low so a reduced-analyzer environment still passes while
    an empty run fails loudly.
    """
    nodes = polyglot_map.get("nodes", [])
    assert len(nodes) >= 20, f"only {len(nodes)} nodes; the corpus was not analyzed"
    langs = {n.get("language") for n in nodes}
    assert len(langs & set(CASES)) >= 10, (
        f"only {sorted(langs & set(CASES))} analyzed; most analyzers are missing"
    )


def test_column_is_non_vacuous(polyglot_map: dict[str, Any]) -> None:
    """Enough languages HARD PASS that the check is proven able to be positive.

    Without this, every cell could be an xfail and the column would be green
    by construction — a passing ratchet that is vacuous for the very property
    it was built to measure.
    """
    passing = [
        lang for lang in CASES
        if lang not in KNOWN_HOLES and _has_method_to_method_dispatch(polyglot_map, lang)
    ]
    assert len(passing) >= _MIN_HARD_PASSES, (
        f"only {len(passing)} hard passes ({sorted(passing)}); the column is "
        f"approaching vacuity"
    )


def test_scope_is_declared_and_disjoint() -> None:
    """Every gated language is classified exactly once, and holes are real.

    Catches the two ways this column decays silently: a KNOWN_HOLES entry
    naming a language the corpus no longer covers (a hole for nothing), and a
    language appearing in both the gated and not-applicable maps.
    """
    assert set(KNOWN_HOLES) <= set(CASES), (
        f"KNOWN_HOLES names languages absent from the corpus: "
        f"{sorted(set(KNOWN_HOLES) - set(CASES))}"
    )
    assert not (set(NOT_APPLICABLE) & set(CASES)), (
        f"languages in both gated and not-applicable: "
        f"{sorted(set(NOT_APPLICABLE) & set(CASES))}"
    )
    for language, reason in KNOWN_HOLES.items():
        assert len(reason) > 40, f"{language}'s hole needs a measured cause, not a label"
