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

What the gate establishes about its eight named tracker items:

* `WI-litil` (cyclomatic_complexity "uniformly 1" for csharp/go/java/rust/swift)
  is **falsified** here: on the branchy `process` fixture every language
  computes complexity 4, so `complexity_nontrivial` is a hard lock for all.
* `WI-rubip`'s ambiguity is **resolved** by the injected-fixture design:
  `edge_calls` / `edge_imports` cells are now falsifiable.
* `WI-fagab` (Python `qualified_name`) and `WI-tosul` (entrypoint concepts
  emitted only by Python) are the documented holes, locked as strict xfails.
* A previously-unfiled parity gap is surfaced and locked: the Java analyzer
  emits no `imports` edge while every other language does.
* `WI-jusus` (emission-parity F5) adds the `emits_variable` / `emits_field`
  kind-emission columns. The JS/TS analyzer emits both (slices 1+2 — class
  fields + module variables); python and go emit the module variable; Java
  emits class fields (its field-emission slice); every remaining (language,
  kind) cell is a measured, strict-xfail hole a per-language Wave-3 emitter fix
  strips. `emits_variable` is not parametrized for Java/C# (no module-level
  variables — see `COLUMN_APPLICABILITY`).
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
}

# Columns not applicable to every language. The injected-fixture design assumes
# a construct is universal, but module-level variables genuinely DO NOT EXIST in
# class-only languages (Java, C#) — every value binding there is a class member
# (measured by `emits_field`). An inapplicable cell is not parametrized at all:
# neither a hard lock nor an xfail hole, so the gate never asserts a construct
# the language cannot express.
COLUMN_APPLICABILITY: dict[str, set[str]] = {
    "emits_variable": {
        "python", "javascript", "typescript", "go", "rust", "swift",
    },
}

# (fixture_language, column) -> xfail reason. Measured 2026-06-11. Each is a
# documented analyzer-emission HOLE the gate LOCKS via strict xfail; a Wave-3
# emitter fix XPASS-trips it and forces a flip to a hard lock.
_WI_TOSUL = (
    "WI-tosul: only the Python analyzer emits entrypoint concepts "
    "(Symbol.meta['concepts'] / usage_contexts) during analysis; this is the "
    "single upstream cause of the dead-code-maybe Python monoculture. "
    "Per-language entrypoint-concept emission is the Wave-3 fix that strips "
    "this xfail."
)
_F5_VAR_HOLE = (
    "WI-jusus (emission-parity F5): the {lang} analyzer emits no kind='variable' "
    "Symbol for the module-level value binding present in the fixture (rust "
    "`const`/`static`, swift top-level `let`/`var`) — measured 2026-06-22. "
    "python / go / javascript / typescript already emit theirs; this is the "
    "documented hole the Wave-3 {lang} variable-emission fix strips."
)
_F5_FIELD_HOLE = (
    "WI-jusus (emission-parity F5): the {lang} analyzer emits no kind='field' "
    "Symbol for the class/struct field present in the fixture — measured "
    "2026-06-22. JS/TS (slices 1+2) and Java emit field symbols; this is the "
    "documented hole the Wave-3 {lang} field-emission fix strips."
)
KNOWN_HOLES: dict[tuple[str, str], str] = {
    # WI-jusus emission-parity F5 variable/field-kind holes (measured 2026-06-22
    # on the augmented fixtures, each carrying a module-level variable [where the
    # language has them] + a class/struct field). JS/TS emit both (slices 1+2);
    # python emits the module variable; go emits the package `var`.
    **{(lang, "emits_variable"): _F5_VAR_HOLE.format(lang=lang)
       for lang in ("rust", "swift")},
    **{(lang, "emits_field"): _F5_FIELD_HOLE.format(lang=lang)
       for lang in ("python", "go", "csharp", "rust", "swift")},
    # ('python','qualified_name') was a strict-xfail hole; WI-fagab populated
    # Symbol.qualified_name on py.py function/method/class symbols, so the cell
    # is now a hard lock ("every emission fix strips an xfail").
    ("java", "edge_imports"): (
        "INV-gojit: Java analyzer emits no `imports` edge for ANY `import` "
        "declaration — verified general, not stdlib-specific: zero imports "
        "edges on both a stdlib `import java.util.List` and a non-stdlib "
        "`import com.example.helper.Formatter` (only `calls` edges produced). "
        "Every other mainstream analyzer emits >=1 imports edge on this "
        "fixture; C# emits 2 for `using System`. Surfaced by this gate."
    ),
    **{
        (lang, "entrypoint_concept"): _WI_TOSUL
        for lang in (
            "javascript", "typescript", "go", "java", "rust", "csharp", "swift",
        )
    },
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
    out = tmp_path / "bm.json"
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
