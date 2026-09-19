# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hypergumbo SCIP-backed Python analyzer (scip-python / pyright), WI-nanom.

The second opt-in fidelity backend and the first that executes nothing from
the analysed repository. Its parts mirror ``hypergumbo_lang_rust_analyzer``:

- ``invoke.py`` shells out to ``scip-python index`` to produce a SCIP blob.
- ``translate.py`` turns that blob into ``(symbols, edges)`` through the
  shared importer, plus the one thing the Rust arm never did: external
  callees keep the receiver's MODULE.
- ``gate.py`` answers "did the user ask?" — flag > environment > config
  tiers (a preference, ADR-0045 ruling 5) — and whether the binary is there.
- ``analyzer.py`` registers ``scip_python`` beside the ``python`` analyzer
  with the merge anchor the ADR-0057 pass pairs on.
"""
from hypergumbo_lang_scip_python.gate import should_use_scip_python_backend
from hypergumbo_lang_scip_python.invoke import (
    ScipPythonError,
    ScipPythonInvocationFailed,
    ScipPythonNoOutput,
    ScipPythonNotInstalled,
    run_scip_python_index,
)
from hypergumbo_lang_scip_python.translate import translate_scip_python_to_hg

__version__ = "8.0.0"

#: Module paths for analyzer discovery via entry-points (ADR-0012 Step 1).
ANALYZER_MODULES = [
    "hypergumbo_lang_scip_python.analyzer",
]

__all__ = [
    "ANALYZER_MODULES",
    "ScipPythonError",
    "ScipPythonInvocationFailed",
    "ScipPythonNoOutput",
    "ScipPythonNotInstalled",
    "run_scip_python_index",
    "should_use_scip_python_backend",
    "translate_scip_python_to_hg",
]
