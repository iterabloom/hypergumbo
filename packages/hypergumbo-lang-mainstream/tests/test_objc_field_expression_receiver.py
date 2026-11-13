# SPDX-License-Identifier: AGPL-3.0-or-later
"""A ``self.<property>`` receiver must not desynchronise the message parse (WI-garar).

``self.<prop>`` parses to a ``field_expression`` receiver::

    message_expression
      [  field_expression(self.session)  identifier(dataTaskWithRequest)
         :  identifier(request)  ]

``_extract_message_selector`` and ``_extract_message_receiver`` must both treat
that node as the receiver, as they treat a nested ``[[obj alloc] init]``
receiver. Taking THE RECEIVER TO BE THE FIRST ``identifier`` CHILD instead
skips past the real receiver and mis-keys on the SELECTOR: the receiver
extractor returns ``"dataTaskWithRequest"``, and the selector extractor then
consumes that same identifier as the receiver it must skip and returns the first
ARGUMENT as the selector::

    [self.session dataTaskWithRequest:r]  ->  objc:external:0-0:r:unresolved
    [self.session writeData:nil]          ->  objc:NSFileHandle:0-0:nil:unresolved
                                              receiver_type_hint='NSFileHandle'

BOTH SLOTS WOULD BE WRONG, AND THE SECOND LINE IS A FALSE POSITIVE. The module
slot would read ``NSFileHandle`` because the enclosing body declares a LOCAL
named ``writeData`` -- which collides with the selector the receiver extractor
mistook for a receiver name -- so ``_objc_receiver_type`` would look THAT name
up. The real receiver is ``self.session``, an ``NSURLSession``. A wrong module in
the slot is the INV-fazim category error arriving from the other direction: it
can match a catalogued row of the same short name and assert a boundary the
program does not cross.

``field_expression`` receivers are common in Objective-C (most of them
``self.<x>``), so this is not a corner.

SCOPE. These tests pin the PARSE -- the selector in the name slot, no module or
hint borrowed from a same-named local -- and, where the module slot is asserted,
the receiver TYPED from the class's ``@property`` declarations
(``NSURLSession``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import classify_call, load_catalog


@pytest.fixture()
def objc_available():
    from hypergumbo_core.analyze.base import is_grammar_available

    if not is_grammar_available("tree_sitter_objc"):
        pytest.skip("objc tree-sitter grammar not installed")


_SOURCE = '''\
@interface Client : NSObject
@property (nonatomic, strong) NSURLSession *session;
@property (nonatomic, strong) NSFileHandle *handle;
@end

@implementation Client

- (void)plain:(NSURLRequest *)req {
    [self.session dataTaskWithRequest:req];
}

- (void)collide:(NSString *)p {
    NSFileHandle *writeData = [NSFileHandle fileHandleForWritingAtPath:p];
    [self.session writeData:nil];
}

- (void)simple {
    [self.handle closeFile];
}

- (void)control:(NSString *)p {
    NSFileHandle *h = [NSFileHandle fileHandleForWritingAtPath:p];
    [h writeData:nil];
}

@end
'''


def _edges(tmp_path: Path):
    from hypergumbo_lang_mainstream.objc import analyze_objc

    (tmp_path / "Client.m").write_text(_SOURCE)
    result = analyze_objc(tmp_path)
    assert not result.skipped
    return [
        e for e in result.edges
        if e.edge_type == "calls" and not e.is_resolved
        and (e.meta or {}).get("call_construct") == "method"
    ]


def _by_line(tmp_path: Path) -> dict[int, list]:
    out: dict[int, list] = {}
    for e in _edges(tmp_path):
        out.setdefault(e.line, []).append(e)
    return out


def _line_of(needle: str) -> int:
    for i, line in enumerate(_SOURCE.split("\n"), start=1):
        if needle in line:
            return i
    raise AssertionError(f"{needle!r} not in the fixture")


LINE_PLAIN = _line_of("[self.session dataTaskWithRequest:req]")
LINE_COLLIDE = _line_of("[self.session writeData:nil]")
LINE_SIMPLE = _line_of("[self.handle closeFile]")
LINE_CONTROL = _line_of("[h writeData:nil]")


class TestTheSelectorReachesTheNameSlot:
    """ADR-0036 ruling 1: the name slot must carry the NAME, never an argument,
    for a ``field_expression`` receiver too."""

    def test_keyword_selector_is_not_the_argument(
        self, tmp_path: Path, objc_available: None,
    ) -> None:
        got = _by_line(tmp_path)[LINE_PLAIN]
        assert len(got) == 1, [e.dst for e in got]
        # READ THE NAME FROM ITS LOSSLESS HOME, NEVER FROM THE ID (ADR-0036
        # ruling 1). An ObjC selector ENDS in a colon, so the id's
        # second-to-last token is the EMPTY STRING -- the exact INV-divuf /
        # WI-nakut failure `meta["callee_name"]` exists for. Re-deriving it
        # from the id reads '' even against a correct dst.
        assert (got[0].meta or {}).get("callee_name") == "dataTaskWithRequest:"
        # The MODULE slot is the property-typing's business (`session` is a
        # declared `NSURLSession` property); this test is about the NAME slot,
        # which must not hold the argument `req`.
        assert got[0].dst.endswith(":dataTaskWithRequest::unresolved"), got[0].dst

    def test_simple_selector_survives(
        self, tmp_path: Path, objc_available: None,
    ) -> None:
        """``[self.handle closeFile]`` has no colon and no argument to steal, so
        it is the shape that would keep working by accident. Pinned so a later
        change cannot fix the keyword form by breaking this one."""
        got = _by_line(tmp_path)[LINE_SIMPLE]
        assert len(got) == 1, [e.dst for e in got]
        assert (got[0].meta or {}).get("callee_name") == "closeFile"
        assert got[0].dst.endswith(":closeFile:unresolved"), got[0].dst


class TestTheCollidingLocalNoLongerTypesTheReceiver:
    """The false positive, and the reason this is a correctness fix rather than
    only a recall one."""

    def test_no_module_is_borrowed_from_a_same_named_local(
        self, tmp_path: Path, objc_available: None,
    ) -> None:
        got = _by_line(tmp_path)[LINE_COLLIDE]
        assert len(got) == 1, [e.dst for e in got]
        edge = got[0]
        assert "NSFileHandle" not in edge.dst, (
            f"{edge.dst} borrowed the class of the local `writeData`; the "
            f"receiver is self.session, an NSURLSession"
        )
        assert (edge.meta or {}).get("receiver_type_hint") != "NSFileHandle"
        assert (edge.meta or {}).get("callee_name") == "writeData:"
        # This site is typed from the DECLARED property rather than left at
        # the sentinel, which makes the point more sharply: the module is the
        # receiver's real class, not the colliding local's.
        assert edge.dst == "objc:NSURLSession:0-0:writeData::unresolved", edge.dst

    def test_the_borrowed_module_did_not_classify_a_boundary(
        self, tmp_path: Path, objc_available: None,
    ) -> None:
        """The end-to-end consequence: a wrong module can match a catalogued row
        of the same short name. Asserted through the production gate."""
        catalogs = {"objc": load_catalog("objc", include_defaults=True)}
        edge = _by_line(tmp_path)[LINE_COLLIDE][0]
        prim = classify_call(
            catalogs, edge.dst, edge.meta, dst_ref=edge.dst_ref,
        )
        assert prim is None, (
            f"self.session writeData: classified as {prim.qualified_name!r}"
        )
        # None because the module names NSURLSession, which has no
        # `writeData:` row -- unlike NSFileHandle, the class of the same-named
        # local, which does.


class TestTheTypedLocalControlIsUnmoved:
    """CONTROL. A plain identifier receiver the body DECLARES is typed from its
    declaration (WI-higob) and must keep reaching its catalogued row."""

    def test_declared_local_receiver_still_reaches_fs_write(
        self, tmp_path: Path, objc_available: None,
    ) -> None:
        catalogs = {"objc": load_catalog("objc", include_defaults=True)}
        got = _by_line(tmp_path)[LINE_CONTROL]
        assert len(got) == 1, [e.dst for e in got]
        prim = classify_call(
            catalogs, got[0].dst, got[0].meta, dst_ref=got[0].dst_ref,
        )
        assert prim is not None, got[0].dst
        assert prim.qualified_name == "NSFileHandle.writeData:"
        assert prim.boundary == "fs_write"
