# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hypergumbo SCIP-backed Rust analyzer.

Slice A surface: :func:`translate_scip_to_hg` converts a serialized SCIP
``Index`` blob into ``(symbols, edges)`` using the in-core shim modules
plus the ``rust.py`` stable-id parity helper. Later slices add a live
``rust-analyzer`` invocation, analyzer-registry wiring, and the opt-in
flag machinery.
"""

from hypergumbo_lang_rust_analyzer.gate import (
    ENV_VAR_NAME,
    should_use_rust_analyzer_backend,
)
from hypergumbo_lang_rust_analyzer.graceful_degrade import (
    try_analyze_with_rust_analyzer,
)
from hypergumbo_lang_rust_analyzer.invoke import (
    RustAnalyzerError,
    RustAnalyzerInvocationFailed,
    RustAnalyzerNoOutput,
    RustAnalyzerNotInstalled,
    run_rust_analyzer_scip,
)
from hypergumbo_lang_rust_analyzer.translate import (
    reassign_rust_stable_ids,
    translate_scip_to_hg,
)

__version__ = "3.0.0"

# Module paths for analyzer discovery via entry-points (ADR-0012 Step 1).
# Importing the listed module triggers the @register_analyzer decorator.
ANALYZER_MODULES = [
    "hypergumbo_lang_rust_analyzer.analyzer",
]

__all__ = [
    "ANALYZER_MODULES",
    "ENV_VAR_NAME",
    "RustAnalyzerError",
    "RustAnalyzerInvocationFailed",
    "RustAnalyzerNoOutput",
    "RustAnalyzerNotInstalled",
    "__version__",
    "reassign_rust_stable_ids",
    "run_rust_analyzer_scip",
    "should_use_rust_analyzer_backend",
    "translate_scip_to_hg",
    "try_analyze_with_rust_analyzer",
]
