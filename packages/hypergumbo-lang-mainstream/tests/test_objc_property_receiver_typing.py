# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-garar stage 2: a ``self.<property>`` receiver carries its DECLARED class.

Stage 1 fixed the PARSE — ``_message_receiver_node`` identifies a
``field_expression`` receiver, so the selector reaches the name slot and no
module is borrowed from a same-named local. It deliberately moved recall by
ZERO, because ``gate_named_entry`` refuses a method-kind row for an UNTYPED
method call however correct its name is (INV-tapat / INV-maluk). This is the
stage that supplies the type.

THE TYPE WAS ALWAYS IN THE TREE, one node from the name ``_extract_property_name``
already reads::

    property_declaration
      @property  property_attributes_declaration
      struct_declaration
        type_identifier(NSURLSession)          <- discarded until now
        struct_declarator -> pointer_declarator -> identifier(session)

THE MAP MUST BE GLOBAL, NOT PER-FILE. Objective-C declares properties in the
``@interface`` and implements methods in the ``@implementation``, and idiomatically
those are different files: AFNetworking declares ``session`` in
``AFURLSessionManager.h:96`` and sends ``[self.session …]`` from
``AFURLSessionManager.m``. A per-file map resolves neither. So the per-file maps
are aggregated across pass 1 exactly as ``method_return_types`` already is, first
writer wins.

WHY AFNetworking IS THE DIRECT TEST. Its nine catalogued ``net_recv`` rows are
all ``NSURLSession`` and every one is reached through ``self.session``, which is
why WI-robit's census recorded an HTTP client reaching ``net_send`` and ZERO
``net_recv``.

SCOPE, stated so the controls below are not mistaken for oversights: a plain
ivar (``NSURLSession *_session;`` in a ``{ … }`` block) is NOT typed here, and a
property INHERITED from a superclass is NOT resolved through the base chain —
both are keyed on the declaring class only. Each is pinned as a sentinel control
so a later widening is attributable to that later change.
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


_HEADER = '''\
@interface Client : NSObject
@property (readonly, nonatomic, strong) NSURLSession *session;
@property (nonatomic, strong) NSFileHandle *handle;
@property (strong, nonatomic) IBOutlet NSFileHandle *outletHandle;
@property (strong, nonatomic) NSDictionary<NSString *, id> *generic;
@end

@interface Derived : Client
@end
'''

_IMPL = '''\
@implementation Client

- (void)recv:(NSURL *)url {
    [self.session dataTaskWithURL:url];
}

- (void)send:(NSURLRequest *)req {
    [self.session dataTaskWithRequest:req];
}

- (void)writeIt:(NSData *)d {
    [self.handle writeData:d];
}

- (void)viaOutlet:(NSData *)d {
    [self.outletHandle writeData:d];
}

- (void)viaGeneric {
    [self.generic count];
}

@end

@implementation Derived

- (void)inherited:(NSURL *)url {
    [self.session dataTaskWithURL:url];
}

@end

@implementation Bare {
    NSFileHandle *_ivarHandle;
}

- (void)viaIvar:(NSData *)d {
    [self->_ivarHandle writeData:d];
}

- (void)viaOther:(Client *)peer {
    [peer.session dataTaskWithURL:nil];
}

- (void)viaLiteral {
    [@"a string" length];
}

@end
'''


def _analyze(tmp_path: Path):
    from hypergumbo_lang_mainstream.objc import analyze_objc

    (tmp_path / "Client.h").write_text(_HEADER)
    (tmp_path / "Client.m").write_text(_IMPL)
    result = analyze_objc(tmp_path)
    assert not result.skipped
    return result


def _by_line(tmp_path: Path) -> dict[int, list]:
    out: dict[int, list] = {}
    for e in _analyze(tmp_path).edges:
        if e.edge_type != "calls" or e.is_resolved:
            continue
        if (e.meta or {}).get("call_construct") != "method":
            continue
        out.setdefault(e.line, []).append(e)
    return out


def _line_of(needle: str) -> int:
    for i, line in enumerate(_IMPL.split("\n"), start=1):
        if needle in line:
            return i
    raise AssertionError(f"{needle!r} not in the fixture")


LINE_RECV = _line_of("dataTaskWithURL:url]")
LINE_SEND = _line_of("dataTaskWithRequest:req]")
LINE_WRITE = _line_of("[self.handle writeData:d]")
LINE_OUTLET = _line_of("[self.outletHandle writeData:d]")
LINE_GENERIC = _line_of("[self.generic count]")
LINE_INHERITED = [i for i, ln in enumerate(_IMPL.split("\n"), start=1)
                  if "dataTaskWithURL:url]" in ln][1]
LINE_IVAR = _line_of("[self->_ivarHandle writeData:d]")
LINE_OTHER = _line_of("[peer.session dataTaskWithURL:nil]")
LINE_LITERAL = _line_of('[@"a string" length]')


def _boundary(tmp_path: Path, line: int):
    catalogs = {"objc": load_catalog("objc", include_defaults=True)}
    got = _by_line(tmp_path).get(line) or []
    assert len(got) == 1, [e.dst for e in got]
    prim = classify_call(catalogs, got[0].dst, got[0].meta, dst_ref=got[0].dst_ref)
    return got[0], prim


class TestThePropertyTypeReachesTheModuleSlot:
    """L3: the declared class of the property, across the .h/.m boundary."""

    def test_module_slot_names_the_declared_class(
        self, tmp_path: Path, objc_available: None,
    ) -> None:
        edge, _ = _boundary(tmp_path, LINE_SEND)
        assert edge.dst == (
            "objc:NSURLSession:0-0:dataTaskWithRequest::unresolved"
        ), edge.dst
        assert (edge.meta or {}).get("receiver_type_hint") == "NSURLSession"

    def test_structured_dst_ref_follows(
        self, tmp_path: Path, objc_available: None,
    ) -> None:
        edge, _ = _boundary(tmp_path, LINE_SEND)
        assert edge.dst_ref is not None
        assert edge.dst_ref.module_path == "NSURLSession"
        assert edge.dst_ref.name == "dataTaskWithRequest:"

    def test_call_construct_survives(
        self, tmp_path: Path, objc_available: None,
    ) -> None:
        """Filling the slot is what makes ``_register_sanitizer_callers``
        reachable for these sites; the flag is what stops a first-party short
        name from binding a catalogued barrier and DROPPING a flow (#214)."""
        edge, _ = _boundary(tmp_path, LINE_SEND)
        assert (edge.meta or {}).get("call_construct") == "method"


class TestTheCatalogueIsReached:
    """L4, and the behavioural evidence INV-linub's per-language closure needs.

    ``net_recv`` is the row WI-robit's census recorded as ZERO on an HTTP
    client: nine rows, all ``NSURLSession``, all reached via ``self.session``.
    """

    def test_self_property_reaches_net_recv(
        self, tmp_path: Path, objc_available: None,
    ) -> None:
        _, prim = _boundary(tmp_path, LINE_RECV)
        assert prim is not None, "dataTaskWithURL: reached no catalogue row"
        assert prim.qualified_name == "NSURLSession.dataTaskWithURL:"
        assert prim.boundary == "net_recv"

    def test_self_property_reaches_net_send(
        self, tmp_path: Path, objc_available: None,
    ) -> None:
        _, prim = _boundary(tmp_path, LINE_SEND)
        assert prim is not None
        assert prim.boundary == "net_send"

    def test_a_second_property_on_the_same_class_is_independent(
        self, tmp_path: Path, objc_available: None,
    ) -> None:
        """``self.handle`` is ``NSFileHandle``, not ``NSURLSession`` — the map is
        keyed per property, not per class."""
        edge, prim = _boundary(tmp_path, LINE_WRITE)
        assert (edge.meta or {}).get("receiver_type_hint") == "NSFileHandle"
        assert prim is not None
        assert prim.qualified_name == "NSFileHandle.writeData:"
        assert prim.boundary == "fs_write"


class TestAMacroIsNotAType:
    """Caught on corpus, not in a fixture: the first cut took the FIRST
    ``type_identifier`` and wrote ``IBOutlet`` -- a macro -- into the module slot
    as though it named a class (1 edge on AFNetworking, 3 on CocoaLumberjack).
    An unqualifiable name in the slot asserts a type that does not exist
    (INV-fazim), so this is a false positive, not cosmetic noise."""

    def test_a_macro_before_the_type_is_skipped(
        self, tmp_path: Path, objc_available: None,
    ) -> None:
        edge, prim = _boundary(tmp_path, LINE_OUTLET)
        assert "IBOutlet" not in edge.dst, edge.dst
        assert edge.dst == "objc:NSFileHandle:0-0:writeData::unresolved", edge.dst
        assert prim is not None and prim.boundary == "fs_write"

    def test_a_generic_type_abstains_rather_than_guessing(
        self, tmp_path: Path, objc_available: None,
    ) -> None:
        """``NSDictionary<NSString *, id>`` yields NO bare ``type_identifier``.
        Abstaining is correct: there is no honest simple name to write."""
        edge, prim = _boundary(tmp_path, LINE_GENERIC)
        assert edge.dst.split(":")[1] == "external", edge.dst
        assert prim is None


class TestTheDeclaredScopeIsNotExceeded:
    """CONTROLS. Both shapes are real and both stay at the sentinel, so a later
    widening is attributable to that later change rather than to this one."""

    def test_inherited_property_keeps_the_sentinel(
        self, tmp_path: Path, objc_available: None,
    ) -> None:
        """``Derived`` inherits ``session`` from ``Client``. Resolving through
        the base chain is a separate change with its own false-positive
        surface (a subclass may shadow), so it is NOT done here."""
        edge, prim = _boundary(tmp_path, LINE_INHERITED)
        assert edge.dst.split(":")[1] == "external", edge.dst
        assert prim is None

    def test_plain_ivar_keeps_the_sentinel(
        self, tmp_path: Path, objc_available: None,
    ) -> None:
        """An ivar declared in an ``@implementation`` brace block is not a
        ``property_declaration`` and is not indexed."""
        edge, prim = _boundary(tmp_path, LINE_IVAR)
        assert edge.dst.split(":")[1] == "external", edge.dst
        assert prim is None

    def test_a_property_on_ANOTHER_object_keeps_the_sentinel(
        self, tmp_path: Path, objc_available: None,
    ) -> None:
        """``peer.session`` is the same property on the same class, reached
        through a receiver that is not ``self``. Typing it needs ``peer`` typed
        first — a different and more speculative inference — so the chain root
        must be ``self``. This is the boundary, pinned."""
        edge, prim = _boundary(tmp_path, LINE_OTHER)
        assert edge.dst.split(":")[1] == "external", edge.dst
        assert prim is None

    def test_a_non_field_receiver_is_untouched(
        self, tmp_path: Path, objc_available: None,
    ) -> None:
        """A receiver that is neither an identifier, a nested send nor a
        ``field_expression`` (here a string literal) reaches the property
        lookup and declines. Stage 1 made these emit AT ALL; stage 2 must not
        invent a type for them."""
        edge, prim = _boundary(tmp_path, LINE_LITERAL)
        assert edge.dst.split(":")[1] == "external", edge.dst
        assert (edge.meta or {}).get("callee_name") == "length"
        assert prim is None
