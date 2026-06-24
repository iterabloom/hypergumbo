# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pipeline-level entrypoint-detection parity gate (the WI-tosul correction).

Companion to ``test_emission_parity_matrix.py``. That matrix locks which fields
each analyzer emits *in isolation*; this gate locks a property the analyzer
matrix structurally **cannot** measure: that hypergumbo's *full* entrypoint-
detection pipeline produces at least one entrypoint for every language, via
whatever mechanism is correct for that language.

Why a separate gate exists (the 2026-06-24 WI-tosul investigation)
------------------------------------------------------------------
Entrypoint detection is a PIPELINE concern, not an analyzer-emission one. The
emission-parity matrix's old ``entrypoint_concept`` column ran analyzers in
isolation and so saw only Python's inline ``main_guard`` concept, leaving the
other seven languages as strict-xfail "holes". That was a *measurement
artifact*, not a real gap: entrypoints are produced downstream by four distinct
mechanisms, none visible to an analyzer run alone —

  * always-on language-convention YAML (``main-functions.yaml``) →
    ``main_function`` for go / java / rust / csharp / python / kotlin / ...;
  * graph-structural ``script_module`` detection (a TS/JS file with top-level
    executable code and no inbound imports) → ``script_module``;
  * the connectivity-based fallback when no structural/convention seed exists →
    ``connectivity_based`` (swift today — see the per-language note below);
  * Python's analyzer-level inline ``main_guard`` concept (the one entrypoint
    signal an isolated analyzer emits).

All eight emission-parity fixtures already yield entrypoints in production; this
gate verifies and *locks* that, so a regression in any language's entrypoint
detection (e.g. a convention-YAML break, or the ``script_module`` pass
silently stopping) fails the suite. The dead-code-maybe "Python monoculture"
that WI-tosul also tracks is a separate *coverage* problem (per-language
framework/route detection breadth), distinct from this entrypoint-existence
invariant.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from hypergumbo_core.cli import run_behavior_map

CORPUS = Path(__file__).resolve().parent / "fixtures" / "emission-parity"

# fixture language -> fixture subdirectory under CORPUS. Same eight fixtures the
# emission-parity matrix uses; each carries a top-level entrypoint idiom.
FIXTURE_DIR: dict[str, str] = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "go": "go",
    "java": "java",
    "rust": "rust",
    "csharp": "csharp",
    "swift": "swift",
}

# Expected entrypoint kind(s) per language. A set means "the pipeline yields at
# least one entrypoint whose kind is in this set" (a precise, mechanism-locking
# assertion). ``None`` means "at least one entrypoint of ANY kind" — used only
# where the seed is the graph-shape-dependent ``connectivity_based`` fallback,
# which an exact-kind assertion would brittly pin (and which a future structural
# detector should be free to replace without tripping this gate).
EXPECTED_ENTRYPOINT_KINDS: dict[str, "set[str] | None"] = {
    "python": {"main_function"},          # __main__ guard -> main_function
    "javascript": {"script_module"},      # top-level executable, no inbound import
    "typescript": {"script_module"},
    "go": {"main_function"},               # func main in package main
    "java": {"main_function"},             # public static void main(String[])
    "rust": {"main_function"},             # fn main()
    "csharp": {"main_function"},           # static void Main(string[])
    # main.swift top-level code is detected only via the connectivity_based
    # fallback today (no structural main.swift / @main entrypoint detection yet
    # — a tracked right-reason follow-up). Lock the weaker, still-meaningful
    # invariant: the pipeline yields >=1 entrypoint for swift.
    "swift": None,
}


@pytest.mark.parametrize("language", sorted(FIXTURE_DIR))
def test_entrypoint_detection_parity(language: str, tmp_path: Path) -> None:
    """The full pipeline yields >=1 (correctly-kinded) entrypoint per language.

    The fixture is copied into a scratch dir so ``run_behavior_map`` (which may
    write cache artifacts alongside the repo) never touches the committed
    corpus."""
    repo = tmp_path / language
    shutil.copytree(CORPUS / FIXTURE_DIR[language], repo)
    out = tmp_path / f"{language}.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())

    entrypoints = data.get("entrypoints", [])
    kinds = {e.get("kind") for e in entrypoints}
    assert entrypoints, (
        f"[{language}] full-pipeline entrypoint detection produced ZERO "
        f"entrypoints — the cross-language entrypoint-parity invariant is "
        f"violated (the fixture carries a top-level entrypoint idiom)"
    )

    expected = EXPECTED_ENTRYPOINT_KINDS[language]
    if expected is not None:
        assert kinds & expected, (
            f"[{language}] expected an entrypoint of kind in {sorted(expected)}, "
            f"got {sorted(k for k in kinds if k)}"
        )
