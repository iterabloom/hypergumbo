# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-nigah Tier 2: tcl namespace-qualified call dst_ref retrofit.

Tcl has no explicit import statement — namespace membership is encoded
directly in the call site via the ``::`` separator (``ns::proc``). The
analyzer's retrofit splits the callee name at the rightmost ``::`` to
derive ``module_path`` (the namespace) and ``name`` (the bare proc), so
unresolved namespace-qualified calls now carry a structured ``dst_ref``
mirroring the legacy colon-separated ``dst`` shape.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.ir import ExternalRef


def _check_grammar_or_skip(check_fn, lang):
    if not check_fn():
        pytest.skip(f"tree-sitter-{lang} not available")


def test_tcl_namespace_qualified_call_carries_dst_ref(tmp_path: Path) -> None:
    """``helpers::doWork`` (unresolved) carries dst_ref with split namespace+name."""
    from hypergumbo_lang_extended1.tcl import (
        analyze_tcl,
        is_tcl_tree_sitter_available,
    )
    _check_grammar_or_skip(is_tcl_tree_sitter_available, "tcl")

    (tmp_path / "main.tcl").write_text('''proc main {} {
    helpers::doWork
}
''')
    result = analyze_tcl(tmp_path)

    unresolved_calls = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "doWork" in e.dst
    ]
    assert unresolved_calls, "expected unresolved call edge for doWork"
    edge = unresolved_calls[0]
    assert edge.dst_ref is not None, (
        f"dst_ref must be populated for namespace-qualified callee; "
        f"got dst={edge.dst!r}"
    )
    assert isinstance(edge.dst_ref, ExternalRef)
    assert edge.dst_ref.lang == "tcl"
    assert edge.dst_ref.module_path == "helpers"
    assert edge.dst_ref.name == "doWork"


def test_tcl_deep_namespace_call_splits_at_rightmost_separator(
    tmp_path: Path,
) -> None:
    """``foo::bar::baz`` splits as module_path='foo::bar', name='baz'."""
    from hypergumbo_lang_extended1.tcl import (
        analyze_tcl,
        is_tcl_tree_sitter_available,
    )
    _check_grammar_or_skip(is_tcl_tree_sitter_available, "tcl")

    (tmp_path / "main.tcl").write_text('''proc main {} {
    foo::bar::baz
}
''')
    result = analyze_tcl(tmp_path)

    unresolved_calls = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "baz" in e.dst
    ]
    assert unresolved_calls, "expected unresolved call edge for baz"
    edge = unresolved_calls[0]
    assert edge.dst_ref is not None
    assert edge.dst_ref.module_path == "foo::bar"
    assert edge.dst_ref.name == "baz"


def test_tcl_bare_unresolved_call_leaves_dst_ref_none(tmp_path: Path) -> None:
    """Bare unresolved calls (no ``::``) have no namespace signal; dst_ref stays None."""
    from hypergumbo_lang_extended1.tcl import (
        analyze_tcl,
        is_tcl_tree_sitter_available,
    )
    _check_grammar_or_skip(is_tcl_tree_sitter_available, "tcl")

    (tmp_path / "main.tcl").write_text('''proc main {} {
    unknown_proc
}
''')
    result = analyze_tcl(tmp_path)

    unresolved_calls = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "unknown_proc" in e.dst
    ]
    assert unresolved_calls
    assert unresolved_calls[0].dst_ref is None
