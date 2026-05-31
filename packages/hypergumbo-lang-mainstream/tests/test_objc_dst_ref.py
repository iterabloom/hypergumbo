# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-nigah Tier 2: ObjC class-message unresolved-edge dst_ref.

Objective-C ``message_expression`` calls have a receiver and a selector
(``[receiver selectorPart:arg]``). The retrofit attaches a structured
``dst_ref`` when the receiver is a PascalCase identifier — by ObjC
convention this denotes a class name, so the dst_ref's ``module_path``
becomes the class. Lowercase receivers (``self``, ``super``, local
vars) get no ``dst_ref`` — there is no module signal.

The heuristic is convention-based rather than verified against class
definitions; the trade-off accepts occasional false positives (a
local var named ``Width``) in exchange for a cheap retrofit that
doesn't require threading the class registry through the edge-extract
function. The downstream consumer treats ``dst_ref`` as a hint, not
ground truth.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.ir import ExternalRef


def _check_grammar_or_skip(check_fn, lang):
    if not check_fn():
        pytest.skip(f"tree-sitter-{lang} not available")


def test_objc_class_message_carries_dst_ref(tmp_path: Path) -> None:
    """``[NSString stringWithFormat:@"..."]`` carries dst_ref with class module_path."""
    from hypergumbo_lang_mainstream.objc import (
        analyze_objc,
        is_objc_tree_sitter_available,
    )
    _check_grammar_or_skip(is_objc_tree_sitter_available, "objc")

    (tmp_path / "Main.m").write_text('''
@interface Main : NSObject
- (void)run;
@end

@implementation Main
- (void)run {
    [NSString stringWithFormat:@"hi"];
}
@end
''')
    result = analyze_objc(tmp_path)

    unresolved = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "stringWithFormat" in e.dst
    ]
    assert unresolved, "expected unresolved class-message edge"
    edge = unresolved[0]
    assert edge.dst_ref is not None, (
        f"dst_ref must be populated for PascalCase receiver; got dst={edge.dst!r}"
    )
    assert isinstance(edge.dst_ref, ExternalRef)
    assert edge.dst_ref.lang == "objc"
    assert edge.dst_ref.module_path == "NSString"
    assert "stringWithFormat" in edge.dst_ref.name


def test_objc_self_message_leaves_dst_ref_none(tmp_path: Path) -> None:
    """``[self helper]`` is not a class message; dst_ref stays None."""
    from hypergumbo_lang_mainstream.objc import (
        analyze_objc,
        is_objc_tree_sitter_available,
    )
    _check_grammar_or_skip(is_objc_tree_sitter_available, "objc")

    (tmp_path / "Main.m").write_text('''
@interface Main : NSObject
- (void)run;
@end

@implementation Main
- (void)run {
    [self unknownHelperOnSelf];
}
@end
''')
    result = analyze_objc(tmp_path)

    unresolved = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "unknownHelperOnSelf" in e.dst
    ]
    if unresolved:
        assert unresolved[0].dst_ref is None, (
            "dst_ref must be None for lowercase (self/super/local) receivers"
        )


def test_objc_nested_message_receiver_leaves_dst_ref_none(tmp_path: Path) -> None:
    """``[[obj alloc] init]`` has a nested receiver; dst_ref stays None."""
    from hypergumbo_lang_mainstream.objc import (
        analyze_objc,
        is_objc_tree_sitter_available,
    )
    _check_grammar_or_skip(is_objc_tree_sitter_available, "objc")

    (tmp_path / "Main.m").write_text('''
@interface Main : NSObject
- (void)run;
@end

@implementation Main
- (void)run {
    [[Worker alloc] initWithName:@"x"];
}
@end
''')
    result = analyze_objc(tmp_path)

    # The outer ``initWithName:`` call has a nested-message receiver — the
    # ``_extract_message_receiver`` helper returns None for that, so the
    # outer call's dst_ref must be None.
    outer = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "initWithName" in e.dst
    ]
    if outer:
        assert outer[0].dst_ref is None, (
            "dst_ref must be None when receiver is a nested message_expression"
        )
