# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hypergumbo SCIP-backed Rust analyzer.

The package is complete as scoped (WI-duzul, done). Its parts:

- :func:`translate_scip_to_hg` (``translate.py``) converts a serialized
  SCIP ``Index`` blob into ``(symbols, edges)`` using the in-core shim
  modules plus the ``rust.py`` stable-id parity helper.
- ``invoke.py`` shells out to ``rust-analyzer scip`` to produce that blob.
- ``gate.py`` holds the opt-in machinery: the backend runs only when
  requested (``--backend rust-analyzer`` or the env flag) AND the binary
  resolves on ``PATH``.
- ``analyzer.py`` registers the backend with the analyzer registry at
  priority 45, above ``rust.py``.
- ``graceful_degrade.py`` returns ``None`` so the caller can fall through
  to ``rust.py`` when the backend cannot run.

SAFETY: ``rust-analyzer`` executes ``build.rs``. Never point this backend
at an untrusted repository.
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

__version__ = "7.0.0"

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
