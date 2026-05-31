# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-nigah Tier 2: Perl package-qualified call dst_ref retrofit.

Perl encodes package membership directly in the call site via the
``::`` separator (``Foo::bar``). The retrofit splits the callee name
at the rightmost ``::`` to derive ``module_path`` (the package) and
``name`` (the bare sub), mirroring the tcl namespace-split retrofit.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.ir import ExternalRef


def _check_grammar_or_skip(check_fn, lang):
    if not check_fn():
        pytest.skip(f"tree-sitter-{lang} not available")


def test_perl_package_qualified_call_carries_dst_ref(tmp_path: Path) -> None:
    """``Some::Module::doWork()`` (unresolved) carries dst_ref split as pkg + bare."""
    from hypergumbo_lang_mainstream.perl import (
        analyze_perl,
        is_perl_tree_sitter_available,
    )
    _check_grammar_or_skip(is_perl_tree_sitter_available, "perl")

    (tmp_path / "main.pl").write_text('''use strict;

sub main {
    Some::Module::doWork();
}
''')
    result = analyze_perl(tmp_path)

    unresolved = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "doWork" in e.dst
    ]
    assert unresolved, "expected unresolved call edge for doWork"
    edge = unresolved[0]
    assert edge.dst_ref is not None, (
        f"dst_ref must be populated for package-qualified callee; "
        f"got dst={edge.dst!r}"
    )
    assert isinstance(edge.dst_ref, ExternalRef)
    assert edge.dst_ref.lang == "perl"
    assert edge.dst_ref.module_path == "Some::Module"
    assert edge.dst_ref.name == "doWork"


def test_perl_single_segment_package_call_carries_dst_ref(tmp_path: Path) -> None:
    """``Foo::bar()`` splits as module_path='Foo', name='bar'."""
    from hypergumbo_lang_mainstream.perl import (
        analyze_perl,
        is_perl_tree_sitter_available,
    )
    _check_grammar_or_skip(is_perl_tree_sitter_available, "perl")

    (tmp_path / "main.pl").write_text('''use strict;

sub main {
    Foo::bar();
}
''')
    result = analyze_perl(tmp_path)

    unresolved = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "bar" in e.dst
    ]
    assert unresolved, f"expected unresolved call edge for bar; got {[e.dst for e in result.edges if e.edge_type == 'calls']}"
    edge = unresolved[0]
    assert edge.dst_ref is not None
    assert edge.dst_ref.module_path == "Foo"
    assert edge.dst_ref.name == "bar"


def test_find_perl_files_yields_pl_pm_t_extensions(tmp_path: Path) -> None:
    """``find_perl_files`` discovers ``*.pl`` / ``*.pm`` / ``*.t`` files.

    Pre-existing helper that lacked direct test coverage (an orphaned
    BRANCHES_test_perl.py exists with the right tests but its filename
    isn't matched by pytest discovery, so the function was uncovered).
    """
    from hypergumbo_lang_mainstream.perl import find_perl_files

    (tmp_path / "script.pl").write_text("# pl\n")
    (tmp_path / "module.pm").write_text("# pm\n")
    (tmp_path / "test.t").write_text("# t\n")
    (tmp_path / "ignore.txt").write_text("# ignored\n")

    files = sorted(p.name for p in find_perl_files(tmp_path))
    assert files == ["module.pm", "script.pl", "test.t"]


def test_perl_bare_unresolved_call_leaves_dst_ref_none(tmp_path: Path) -> None:
    """Bare unresolved calls (no ``::``) have no package signal; dst_ref stays None."""
    from hypergumbo_lang_mainstream.perl import (
        analyze_perl,
        is_perl_tree_sitter_available,
    )
    _check_grammar_or_skip(is_perl_tree_sitter_available, "perl")

    (tmp_path / "main.pl").write_text('''use strict;

sub main {
    unknown_sub();
}
''')
    result = analyze_perl(tmp_path)

    unresolved = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "unknown_sub" in e.dst
    ]
    if unresolved:
        assert unresolved[0].dst_ref is None
