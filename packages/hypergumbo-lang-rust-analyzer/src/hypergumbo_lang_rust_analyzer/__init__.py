# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hypergumbo SCIP-backed Rust analyzer.

Slice A surface: :func:`translate_scip_to_hg` converts a serialized SCIP
``Index`` blob into ``(symbols, edges)`` using the in-core shim modules
plus the ``rust.py`` stable-id parity helper. Later slices add a live
``rust-analyzer`` invocation, analyzer-registry wiring, and the opt-in
flag machinery.
"""

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

__version__ = "2.6.0"

__all__ = [
    "RustAnalyzerError",
    "RustAnalyzerInvocationFailed",
    "RustAnalyzerNoOutput",
    "RustAnalyzerNotInstalled",
    "__version__",
    "reassign_rust_stable_ids",
    "run_rust_analyzer_scip",
    "translate_scip_to_hg",
]
