# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Rust analyzer followed by the shared call-resolution pass, for tests (ADR-0064).

Not a test module (the leading underscore keeps pytest from collecting it).
The Rust analyzer binds a call only on its own evidence and reports the rest
with facts; the ``inherited_calls`` linker reads those facts and the files'
import tables and binds what they settle. A test of a call that reaches its
target through an import (``use crate::m::f; f()``, ``m::f()``, a receiver
whose type is imported) has to run both, as the pipeline does. This runs the
one linker, not the whole pipeline: a stub it resolves is dropped from the
returned edges, as ``superseded_by`` drops it from a map's readers.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.linkers.inherited_calls import link_inherited_calls
from hypergumbo_core.linkers.registry import LinkerContext
from hypergumbo_lang_mainstream.rust import analyze_rust


def analyze_and_link(repo: Path) -> AnalysisResult:
    """``analyze_rust(repo)`` with the shared pass's edges in place of the
    stubs they resolve."""
    result = analyze_rust(repo)
    linked = link_inherited_calls(LinkerContext(
        repo_root=repo, symbols=result.symbols, edges=result.edges,
        import_scopes=result.import_scopes,
    )).edges
    consumed = {edge.derived_from[0] for edge in linked}
    result.edges = [e for e in result.edges if e.id not in consumed] + linked
    return result
