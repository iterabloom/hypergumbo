# SPDX-License-Identifier: AGPL-3.0-or-later
"""emission-parity:F1 / G2 — per-(language, field/edge-type) emission gate.

Guardrail **G2** of the fundamental-correctness strategy: a standing,
*enforced* matrix that locks which declared `Symbol`/`Edge` fields each
language analyzer actually emits, so analyzer-emission parity can no longer
regress silently (the `INV-jahiv` / `INV-loguk` reopen cycles were exactly
"a sweep claimed parity, nothing kept it honest"). It is the analyzer-side
complement of the G1 validator ratchet (`test_validation_report_empty.py`).

Design (see `tests/fixtures/emission-parity/README.md` for the rationale):

* **Injected, uniform fixtures.** Each language fixture deliberately contains
  the *same* construct set, so every matrix cell is applicable to every
  language. An empty cell is therefore an analyzer gap, never a
  construct-absent artifact — this is the `WI-rubip` methodological fix
  (substrate-derived matrices at small N cannot tell the two apart).

* **Live dataclasses, never serialized JSON.** Each analyzer is run in-process
  via `run_analyzer(name, fixture_dir)` and the returned `Symbol`/`Edge`
  *dataclass instances* are inspected directly. This is load-bearing: fields
  like `Symbol.is_exported` are *relocated* on serialization
  (→ `supply_chain.is_exported`), so a JSON-derived probe reports them as
  "100% None" even when the analyzer populates the live field (the
  `WI-bujot` probe artifact). The gate measures analyzer *emission*; the
  serialization relocation is a separate (schema) concern.

* **Measured, then locked — with a strict-xfail ratchet.** The healthy cells
  below were *measured* (2026-06-11), not assumed; each is a hard `assert`
  that locks current good behaviour. Each genuine, tracker-documented gap is
  a `KNOWN_HOLES` entry rendered as `xfail(strict=True)`: a Wave-3 emitter fix
  that closes the gap makes the cell XPASS, which (under `strict`) fails the
  suite and forces the maintainer to flip it to a hard lock — ratcheting the
  matrix monotonically toward full parity. Every emission fix strips an xfail.
  The ratchet has now reached **full parity**: `KNOWN_HOLES` is empty — every
  parametrized cell is a hard lock (see the per-item notes below for the
  emission fixes and the one reclassification that got us here).

What the gate establishes about its eight named tracker items:

* `WI-litil` (cyclomatic_complexity "uniformly 1" for csharp/go/java/rust/swift)
  is **falsified** here: on the branchy `process` fixture every language
  computes complexity 4, so `complexity_nontrivial` is a hard lock for all.
* `WI-rubip`'s ambiguity is **resolved** by the injected-fixture design:
  `edge_calls` / `edge_imports` cells are now falsifiable.
* `WI-tosul`'s `entrypoint_concept` framing was **corrected, not stripped by an
  emitter fix** (2026-06-24). The seven non-Python "holes" were a *measurement
  artifact*: this gate runs analyzers in isolation, but entrypoint detection is
  a PIPELINE concern. Entrypoints are produced downstream by four mechanisms no
  isolated analyzer can show — always-on `main-functions.yaml` (`main_function`
  for go/java/rust/csharp/python/...), graph-structural `script_module`
  detection (js/ts top-level code), the connectivity-based fallback (swift), and
  Python's inline `main_guard`. All eight languages already yield entrypoints in
  production (empirically verified). So `entrypoint_concept` is now applicable
  only to `python` here — its sole analyzer-level entrypoint concept — and the
  real cross-language invariant (every language yields an entrypoint end-to-end)
  is locked by the sibling pipeline gate `test_entrypoint_parity.py`. The
  dead-code-maybe "Python monoculture" WI-tosul tracks is a separate *coverage*
  problem (per-language framework/route detection), not entrypoint-existence.
  (`WI-fagab` — Python `qualified_name` — is closed and now a hard lock.)
* The Java `edge_imports` parity gap this gate first surfaced — Java alone
  emitted no `imports` edge — is **closed** (INV-gojit): the Java analyzer
  now emits one `imports` edge per import declaration, so the cell is a hard
  lock like every other language's.
* `WI-jusus` (emission-parity F5) adds the `emits_variable` / `emits_field`
  kind-emission columns, now FULLY CLOSED (every applicable cell is a hard lock).
  Every analyzer with module-level variables (python/js/ts/go/rust/swift) emits
  them, and js/ts/java/go/csharp/rust/swift/python all emit class/struct fields —
  the eight per-language slices that ratcheted the holes away. `emits_variable`
  is not parametrized for Java/C# (no module-level variables — see
  `COLUMN_APPLICABILITY`).
* `WI-lutob` / `INV-jahiv` adds the `shape_id` column — the sole *identity*-field
  cell (all the columns above are semantic/derived-field parity). It was added
  because the identity fields (shape_id/stable_id/fingerprint) were never
  enrolled in this ratchet, so a real `csharp shape_id=None` regression (an
  `analyze()`-override bypassing the base-class auto-stamp loop) slipped past
  every standing test and surfaced only in a point-in-time DEEP bakeoff — exactly
  the silent-parity-regression this gate exists to catch. The column locks
  callable shape_id for the eight core analyzers (all hard locks; csharp is green
  post-PR #704). SCOPE: shape_id is spec-🟨-optional, coverage deliberately
  scoped to ~41/70 analyzers, so this guards MAINSTREAM emission against
  regression, not full-fleet parity — the niche solidity/wgsl "without"-bucket
  gaps stay `WI-lutob` coverage work and are not cells here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import ensure_discovered, run_analyzer
from hypergumbo_core.cli import run_behavior_map

CORPUS = Path(__file__).resolve().parent / "fixtures" / "emission-parity"

# fixture-language -> analyzer name. There is no separate `typescript`
# analyzer; the `javascript` analyzer handles
# ['javascript', 'typescript', 'vue', 'svelte'], so the `typescript` row
# exercises the same analyzer over TS syntax (the js-vs-ts contrast INV-golap
# is about). All eight are availability="core" (no grammar wheel needed).
FIXTURE_ANALYZER: dict[str, str] = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "javascript",
    "go": "go",
    "java": "java",
    "rust": "rust",
    "csharp": "csharp",
    "swift": "swift",
}

# Liveness floor: the smallest symbol count any fixture produces is 4
# (class + 3 callables). A floor of 3 proves the fixture was analyzed while
# tolerating minor per-analyzer shape differences; it fails loudly on a
# silently-empty (skipped / mis-resolved) run rather than passing vacuously.
_MIN_SYMBOLS = 3


def _callables(res: AnalysisResult) -> list:
    return [s for s in res.symbols if s.kind in ("function", "method")]


def _max_complexity(res: AnalysisResult) -> int:
    vals = [
        s.cyclomatic_complexity
        for s in _callables(res)
        if s.cyclomatic_complexity is not None
    ]
    return max(vals) if vals else 0


# WI-duguk: container kinds whose BODY MEMBERS are themselves symbols. Split
# into two columns because the two construct families fail independently — every
# analyzer that emits an `enum` emits zero variants, while abstract-type members
# are emitted by go/java/csharp and not by typescript/rust/swift. One merged
# column would let a language pass on its abstract members while its enum
# variants stay invisible.
_ENUM_CONTAINER_KINDS = frozenset({"enum"})
_ABSTRACT_CONTAINER_KINDS = frozenset({"interface", "protocol", "trait"})


def _emits_members_of(res: AnalysisResult, container_kinds: frozenset[str]) -> bool:
    """True when an emitted container of `container_kinds` has >=1 emitted
    symbol nested inside its span.

    Span containment (rather than a `contains` edge) is deliberate: this matrix
    runs ONE analyzer in isolation, and `contains` is minted downstream by the
    containment linker. The measured root cause of WI-duguk is that the member
    SYMBOL is never emitted at all — there is no missing edge, there is a
    missing node — so the analyzer-level property is exactly "is the member a
    symbol". The linker then roots it for free (verified: every container whose
    members exist does get its `contains` edges).
    """
    containers = [
        s for s in res.symbols if s.kind in container_kinds and s.span is not None
    ]
    return any(
        s.id != c.id
        and s.span is not None
        and s.span.start_line >= c.span.start_line
        and s.span.end_line <= c.span.end_line
        for c in containers
        for s in res.symbols
    )


# Matrix column -> predicate "did the analyzer emit this?" over live dataclasses.
COLUMNS: dict[str, Callable[[AnalysisResult], bool]] = {
    # A callable carries its source-grammar signature string.
    "signature": lambda res: any(s.signature for s in _callables(res)),
    # A symbol carries its container-qualified name (ADR-0032).
    "qualified_name": lambda res: any(s.qualified_name for s in res.symbols),
    # Public-API reachability is set on the live Symbol (pre-serialization).
    "is_exported": lambda res: any(s.is_exported for s in res.symbols),
    # A callable carries a first-line doc summary.
    "docstring": lambda res: any(s.docstring for s in _callables(res)),
    # The 3-branch `process` fixture yields McCabe complexity > 1 (4 if
    # computed), so a hardcoded/omitted value (1 or None) trips this.
    "complexity_nontrivial": lambda res: _max_complexity(res) > 1,
    "edge_calls": lambda res: any(e.edge_type == "calls" for e in res.edges),
    "edge_imports": lambda res: any(e.edge_type == "imports" for e in res.edges),
    # Per-language entrypoint signal feeding detect_entrypoints: an in-analyzer
    # concept (Symbol.meta['concepts']) or a usage_context record.
    "entrypoint_concept": lambda res: (
        any((s.meta or {}).get("concepts") for s in res.symbols)
        or bool(res.usage_contexts)
    ),
    # WI-jusus (emission-parity F5): the analyzer emits a kind='variable' Symbol
    # for the module/package-level value binding present in every fixture.
    "emits_variable": lambda res: any(s.kind == "variable" for s in res.symbols),
    # WI-jusus (emission-parity F5): the analyzer emits a kind='field' Symbol
    # for the class/struct field present in every fixture.
    "emits_field": lambda res: any(s.kind == "field" for s in res.symbols),
    # WI-duguk: the analyzer emits a Symbol for each named member of the
    # enumerated type present in every applicable fixture. Without member
    # symbols the containment linker has nothing to root, so `slice --reverse`
    # from an enum returns the container alone and a consumer reads "this enum
    # is dead" (measured: `--entry Color --language rust --reverse` -> 1 node,
    # vs 2 for a container whose members exist).
    "emits_enum_members": lambda res: _emits_members_of(res, _ENUM_CONTAINER_KINDS),
    # WI-duguk: the analyzer emits a Symbol for each member signature of the
    # abstract type (interface / protocol / trait) present in every applicable
    # fixture. Same consumer consequence as above, reached from a trait.
    "emits_abstract_members": lambda res: _emits_members_of(
        res, _ABSTRACT_CONTAINER_KINDS
    ),
    # WI-lutob / INV-jahiv: the analyzer emits the `axis: identity` structural
    # shape_id on its callables. This is the one identity-field column (the
    # semantic-field columns above are the analyzer-parity core); it exists
    # because a `csharp shape_id=None` regression (csharp.py overriding
    # `analyze()` and bypassing the base-class auto-stamp loop, WI-lutob) slipped
    # past every standing test and was caught only by a point-in-time DEEP
    # bakeoff — the exact "a sweep claimed parity, nothing kept it honest"
    # failure mode this gate exists to prevent. It locks callable shape_id
    # emission for the eight core (mainstream) analyzers, whose coverage
    # ADR-0014's status line asserts is complete. SCOPE BOUNDARY: shape_id is
    # spec-🟨-optional with coverage deliberately scoped to ~41/70 analyzers
    # (spec §6, line ~365), so this column guards MAINSTREAM analyzers against
    # regression — it is NOT a full-fleet parity claim. The niche extended1/
    # common gaps (solidity, wgsl) are documented "without"-bucket coverage work
    # (WI-lutob), not cells here (they are not in FIXTURE_ANALYZER).
    "shape_id": lambda res: any(s.shape_id for s in _callables(res)),
}

# Columns not applicable to every language. The injected-fixture design assumes
# a construct is universal, but a cell is excluded when the property is not an
# *analyzer-emission* property for that language — so an empty cell never reads
# as a false analyzer gap. Two reasons appear here:
#
#   * Construct genuinely absent: module-level variables DO NOT EXIST in
#     class-only languages (Java, C#) — every value binding is a class member
#     (measured by `emits_field`).
#
#   * Property assigned at a different layer: `entrypoint_concept` is a PIPELINE
#     property, not an analyzer-emission one. Entrypoints are produced
#     downstream by mechanisms no isolated analyzer can show — the always-on
#     `main-functions.yaml` convention (`main_function` for go/java/rust/csharp/
#     python/...), graph-structural `script_module` detection (js/ts top-level
#     code, no inbound imports), and the connectivity-based fallback (swift).
#     Only Python carries an *analyzer-level* entrypoint concept (`main_guard`,
#     stamped inline by py.py on the file Symbol for the `if __name__ ==
#     "__main__"` idiom), so `entrypoint_concept` is applicable only to python
#     in THIS analyzer-emission matrix. The real cross-language property — that
#     the full pipeline yields an entrypoint for every language — is verified
#     and locked end-to-end by the sibling gate `test_entrypoint_parity.py`.
#     (WI-tosul correction, 2026-06-24: the seven non-Python "holes" were a
#     measurement artifact of analyzer isolation, not a real emission gap —
#     every language already yields entrypoints in production.)
#   * WI-duguk, third reason, a variant of "construct genuinely absent": the
#     language HAS the concept but expresses it with a construct another column
#     already locks. Python's enumerated type IS a class (`class Color(Enum)`)
#     and its Protocol IS a class, so both would be measured through
#     class-member emission, which `emits_field` already hard-locks — scoring
#     them here would mint a cell that cannot distinguish the new property from
#     the old one. JavaScript has neither construct at all (both are
#     TypeScript-only), and Go has no enumerated type (its idiom is a `const`
#     block whose members are siblings of the type, not nested in its body).
#     Re-eval trigger: if py.py ever emits `kind="enum"` for an `Enum`
#     subclass, or the go analyzer synthesizes an enum container from a typed
#     `const` block, add that language to the corresponding set below.
COLUMN_APPLICABILITY: dict[str, set[str]] = {
    "emits_variable": {
        "python", "javascript", "typescript", "go", "rust", "swift",
    },
    "entrypoint_concept": {"python"},
    "emits_enum_members": {"typescript", "java", "rust", "csharp", "swift"},
    "emits_abstract_members": {
        "typescript", "go", "java", "rust", "csharp", "swift",
    },
}

# (fixture_language, column) -> xfail reason. Live holes are the WI-duguk
# container-member cells below; every other parametrized cell is a hard lock.
#
# These are strict xfails (never `pytest.xfail()`, which can't XPASS and would
# silently disable the ratchet it records). Fixing an analyzer therefore turns
# its cell XPASS -> failure, forcing the entry's removal in the same PR.
#
# Cells that USED to be holes, for the record:
#   * WI-jusus emission-parity F5 (`emits_variable` / `emits_field`): closed by
#     eight per-language slices; every applicable cell is a hard lock.
#   * ('python','qualified_name'): WI-fagab populated Symbol.qualified_name on
#     py.py function/method/class symbols — now a hard lock.
#   * ('java','edge_imports'): INV-gojit wired java.py `_extract_import_edges`
#     to emit one `imports` edge per import declaration — now a hard lock.
#   * (7x 'entrypoint_concept'): NOT closed by an emitter fix but RECLASSIFIED
#     (WI-tosul correction) — entrypoint detection is a pipeline property, so
#     these cells are no longer parametrized (see COLUMN_APPLICABILITY) and the
#     real invariant moved to `test_entrypoint_parity.py`.
_ENUM_HOLE = (
    "WI-duguk: analyzer emits the `enum` container but no Symbol for any of its "
    "named members, so the containment linker has nothing to root and "
    "`slice --reverse` from the enum returns the container alone. Not by design "
    "— no ADR/spec passage excludes enum members, and the niche D and Nim "
    "analyzers already emit them as kind='field' with dotted names (verified "
    "live: D `enum Color` -> field Color.red, field Color.green)."
)
_ABSTRACT_HOLE = (
    "WI-duguk: analyzer emits the abstract-type container but no Symbol for any "
    "of its member signatures. Sibling analyzers go/java/csharp already emit "
    "these as kind='method' with dotted names, so this is a per-analyzer gap "
    "rather than a property of the construct."
)

KNOWN_HOLES: dict[tuple[str, str], str] = {
    # Was 5 of 5; rust and typescript drained, so 3 of the 5 analyzers that
    # emit an `enum` still emit none of its members.
    ("java", "emits_enum_members"): _ENUM_HOLE,
    ("csharp", "emits_enum_members"): _ENUM_HOLE,
    ("swift", "emits_enum_members"): _ENUM_HOLE,
    # Was 3 of 6; rust and typescript drained, so 1 of the 6 applicable
    # analyzers still misses abstract-type members.
    ("swift", "emits_abstract_members"): _ABSTRACT_HOLE,
}


@pytest.fixture(scope="module")
def analyzed() -> dict[str, AnalysisResult]:
    """Run each fixture-language's analyzer once (in-process) and cache the
    live `AnalysisResult`. One analysis per language, both the liveness check
    and every matrix cell read off it — the orphan-audit anti-redundancy
    lesson (don't re-run analysis per assertion under pytest-xdist)."""
    ensure_discovered()
    return {
        fix_lang: run_analyzer(analyzer_name, CORPUS / fix_lang)
        for fix_lang, analyzer_name in FIXTURE_ANALYZER.items()
    }


@pytest.mark.parametrize("fix_lang", sorted(FIXTURE_ANALYZER))
def test_fixture_is_live(fix_lang: str, analyzed: dict[str, AnalysisResult]) -> None:
    """Liveness floor (NOT xfailable): the fixture was actually analyzed.

    Kept separate from the matrix cells so that a 'this analyzer stopped
    working' regression surfaces as a hard failure even for languages whose
    cells are xfail holes (a strict-xfail cell would otherwise absorb an
    empty-result regression as an expected failure)."""
    res = analyzed[fix_lang]
    assert not res.skipped, f"[{fix_lang}] analyzer skipped: {res.skip_reason!r}"
    assert len(res.symbols) >= _MIN_SYMBOLS, (
        f"[{fix_lang}] only {len(res.symbols)} symbols (< {_MIN_SYMBOLS}); the "
        "fixture was not analyzed — the parity matrix would pass vacuously"
    )
    assert res.edges, f"[{fix_lang}] analyzer produced zero edges"


def _matrix_params() -> list:
    params = []
    for fix_lang in sorted(FIXTURE_ANALYZER):
        for col in COLUMNS:
            applicable = COLUMN_APPLICABILITY.get(col)
            if applicable is not None and fix_lang not in applicable:
                # construct does not exist in this language (e.g. module-level
                # variables in Java/C#); not a cell at all.
                continue
            key = (fix_lang, col)
            marks = (
                (pytest.mark.xfail(reason=KNOWN_HOLES[key], strict=True),)
                if key in KNOWN_HOLES
                else ()
            )
            params.append(
                pytest.param(fix_lang, col, marks=marks, id=f"{fix_lang}-{col}")
            )
    return params


@pytest.mark.parametrize("fix_lang,col", _matrix_params())
def test_emission_parity_cell(
    fix_lang: str, col: str, analyzed: dict[str, AnalysisResult]
) -> None:
    """One matrix cell: the analyzer emits column `col` for `fix_lang`.

    Healthy cells are hard locks; the `KNOWN_HOLES` cells are strict xfails
    (the ratchet). Liveness is asserted separately by `test_fixture_is_live`."""
    res = analyzed[fix_lang]
    assert COLUMNS[col](res), (
        f"[{fix_lang}] analyzer emitted no `{col}` over the injected fixture "
        f"(symbols={len(res.symbols)}, edges={len(res.edges)}); the construct "
        "is present in the fixture, so this is an analyzer-emission gap"
    )


def test_fixture_languages_are_analyzed_with_real_output(tmp_path: Path) -> None:
    """G2 coverage assertion: every fixture language is (1) detected in
    `profile.languages`, (2) covered by an analyzer pass that ran on >=1 file,
    and (3) actually produced >=1 emitted node tagged with that language.

    This is deliberately stronger than "the language's analyzer appears in
    `analysis_runs`". Two facts make the weaker check vacuous: a pass is
    recorded in `analysis_runs` even when it analyzed ZERO files (this corpus
    carries 59 such phantom passes), and the analyzer registry's declared
    `languages` claim coverage of ~38 languages the corpus never contains —
    so unioning registry `.languages` over the runs would be satisfied by what
    the registry *claims to handle*, not by what ran. Checking real per-language
    node output instead catches the failure mode this guards: an analyzer that
    silently regresses to emitting nothing for its language. Runs the full
    pipeline once over the whole corpus."""
    ensure_discovered()
    out = tmp_path / "survey.json"
    run_behavior_map(
        repo_root=CORPUS,
        out_path=out,
        include_sketch_precomputed=False,
        progress=False,
    )
    bm = json.loads(out.read_text())

    profile_langs = set((bm.get("profile", {}).get("languages") or {}).keys())
    assert profile_langs, "profile.languages empty — corpus not profiled"
    runs_by_pass = {r.get("pass"): r for r in bm.get("analysis_runs", [])}
    node_langs = {
        node.get("language") for node in bm.get("nodes", []) if node.get("language")
    }

    failures: list[str] = []
    for fix_lang, analyzer_name in sorted(FIXTURE_ANALYZER.items()):
        if fix_lang not in profile_langs:
            failures.append(f"{fix_lang}: not detected in profile.languages")
        run = runs_by_pass.get(analyzer_name)
        if run is None or run.get("files_analyzed", 0) < 1:
            seen = run.get("files_analyzed") if run else "no-run"
            failures.append(
                f"{fix_lang}: covering analyzer '{analyzer_name}' did not run "
                f"on >=1 file (files_analyzed={seen})"
            )
        if fix_lang not in node_langs:
            failures.append(
                f"{fix_lang}: detected/ran but produced no emitted node tagged "
                f"'{fix_lang}' — a silent zero-output regression"
            )
    assert not failures, (
        "language-coverage breaches (detected-but-not-really-analyzed):\n  "
        + "\n  ".join(failures)
    )
