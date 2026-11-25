# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Kotlin analyzer followed by the shared linkers it hands its calls to.

Not a test module (the leading underscore keeps pytest from collecting it).
Since ADR-0064's batch 5a the Kotlin analyzer binds a call only where the
language's own evidence is in hand (the enclosing type's own member, found
by position; a member of an object or companion the file imports by name);
every other call is reported with facts, and the linkers decide through each
file's import table. A test that pins WHERE a call lands therefore runs the
three linkers that read those facts, in their registered order:
``inherited_calls`` (a type's member, a bare name through the table),
``receiver_type_dispatch`` (an extension function on the receiver's type) and
``single_holder_name`` (the one method of a name, for a receiver nobody
typed). Nothing else of the pipeline runs: a test that needs it uses
``run_behavior_map``.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.linkers.inherited_calls import link_inherited_calls
from hypergumbo_core.linkers.receiver_type_dispatch import link_receiver_type_dispatch
from hypergumbo_core.linkers.registry import LinkerContext
from hypergumbo_core.linkers.single_holder_name import link_single_holder_name
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin


def analyze_and_link(repo: Path) -> AnalysisResult:
    """``analyze_kotlin(repo)`` with the edges of the three call linkers added."""
    result = analyze_kotlin(repo)
    edges = list(result.edges)
    for link in (link_inherited_calls, link_receiver_type_dispatch, link_single_holder_name):
        edges += link(LinkerContext(
            repo_root=repo, symbols=result.symbols, edges=edges,
            import_scopes=result.import_scopes,
        )).edges
    result.edges = edges
    return result
