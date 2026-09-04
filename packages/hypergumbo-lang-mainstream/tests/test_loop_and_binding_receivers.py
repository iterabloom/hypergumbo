# SPDX-License-Identifier: AGPL-3.0-or-later
"""A receiver bound by a LOOP or an optional-binding carries its type (WI-higob residual).

Two shapes, one per language, both of which the analyzer had the information for and was
not reading:

* **objc** — `for (NSString *p in paths)` DECLARES the binding's class in exactly the
  layout a local declaration uses (a direct `type_identifier` plus a `pointer_declarator`),
  and `_objc_declared_receiver_types` simply never looked inside a `for_statement`. The
  very next line's `NSFileManager *fm = ...` receiver was already typed, so this was a gap
  in coverage of one node type, not a missing capability.
* **swift** — `if let x = <expr>` / `guard let x = <expr>` bind a name to an expression
  whose type WI-higob slice 2's walker already computes. PR #760's read-back attributed
  part of VernissageServer's residual losses to "guard-let-from-library bindings the
  file-wide leak matched by name": the OLD leaky scope map typed these by accident, and
  scoping them correctly took the type away. This gives it back on a sound basis.

NOT in scope, and filed rather than guessed: `for s in xs` and `xs.forEach { s in }` in
swift, which need the ELEMENT type of a collection annotation. `_swift_bare_type`
deliberately REFUSES `[T]` because a collection is not a receiver type, so element
extraction is a new rule and must not be a loosening of that one.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.objc import analyze_objc
from hypergumbo_lang_mainstream.swift import analyze_swift


def _one(edges: list[Edge], needle: str) -> Edge:
    hits = [e for e in edges if e.edge_type == "calls" and needle in e.dst]
    assert len(hits) == 1, [e.dst for e in edges if needle in e.dst]
    return hits[0]


class TestObjcFastEnumerationBinding:
    def test_a_for_in_binding_carries_its_declared_class(self, tmp_path: Path) -> None:
        (tmp_path / "a.m").write_text(
            "#import <Foundation/Foundation.h>\n"
            "@implementation Thing\n"
            "- (void)go:(NSArray *)paths {\n"
            "    for (NSString *p in paths) {\n"
            '        [p writeToFile:@"/x" atomically:YES];\n'
            "    }\n"
            "}\n"
            "@end\n"
        )
        edge = _one(analyze_objc(tmp_path).edges, "writeToFile")
        assert edge.dst == "objc:NSString:0-0:writeToFile:atomically::unresolved", edge.dst

    def test_the_declared_local_beside_it_still_works(self, tmp_path: Path) -> None:
        """The control: the shape that already worked, in the same fixture."""
        (tmp_path / "a.m").write_text(
            "#import <Foundation/Foundation.h>\n"
            "@implementation Thing\n"
            "- (void)go {\n"
            "    NSFileManager *fm = [NSFileManager defaultManager];\n"
            '    [fm removeItemAtPath:@"/y" error:nil];\n'
            "}\n"
            "@end\n"
        )
        edge = _one(analyze_objc(tmp_path).edges, "removeItemAtPath")
        assert edge.dst == "objc:NSFileManager:0-0:removeItemAtPath:error::unresolved"

    def test_a_c_style_for_loop_declares_nothing_here(self, tmp_path: Path) -> None:
        """`for (int i = 0; ...)` wraps its own `declaration` child, which the walker
        reaches on its own; the `for_statement` contributes no direct type_identifier.

        The receiver is an `id`, deliberately: a typed parameter would be typed by
        the parameter rule and the fixture would prove nothing about the loop. (It
        was written that way first — `[paths description]` on an `(NSArray *)paths`
        parameter — and correctly returned `NSArray`.)
        """
        (tmp_path / "a.m").write_text(
            "#import <Foundation/Foundation.h>\n"
            "@implementation Thing\n"
            "- (void)go:(id)thing {\n"
            "    for (int i = 0; i < 3; i++) {\n"
            "        [thing description];\n"
            "    }\n"
            "}\n"
            "@end\n"
        )
        edge = _one(analyze_objc(tmp_path).edges, "description")
        assert edge.dst == "objc:external:0-0:description:unresolved", edge.dst


STORE = (
    "import Foundation\n"
    "class Store {\n"
    "    func session() -> URLSession { return URLSession.shared }\n"
    "    func untyped() { }\n"
    "}\n"
)


class TestSwiftOptionalBinding:
    def test_if_let_takes_the_rhs_type(self, tmp_path: Path) -> None:
        (tmp_path / "a.swift").write_text(STORE + (
            "func go(store: Store) {\n"
            "    if let s = store.session() {\n"
            "        s.invalidateAndCancel()\n"
            "    }\n"
            "}\n"
        ))
        edge = _one(analyze_swift(tmp_path).edges, "invalidateAndCancel")
        assert edge.dst == "swift:URLSession:0-0:invalidateAndCancel:unresolved", edge.dst

    def test_guard_let_takes_the_rhs_type(self, tmp_path: Path) -> None:
        (tmp_path / "a.swift").write_text(STORE + (
            "func go(store: Store) {\n"
            "    guard let s = store.session() else { return }\n"
            "    s.invalidateAndCancel()\n"
            "}\n"
        ))
        edge = _one(analyze_swift(tmp_path).edges, "invalidateAndCancel")
        assert edge.dst == "swift:URLSession:0-0:invalidateAndCancel:unresolved", edge.dst

    def test_a_binding_whose_rhs_names_no_type_stays_untyped(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "a.swift").write_text(STORE + (
            "func go(store: Store) {\n"
            "    if let s = store.untyped() {\n"
            "        s.invalidateAndCancel()\n"
            "    }\n"
            "}\n"
        ))
        edge = _one(analyze_swift(tmp_path).edges, "invalidateAndCancel")
        assert edge.dst == "swift:external:0-0:invalidateAndCancel:unresolved", edge.dst

    def test_a_constructor_rhs_is_typed_too(self, tmp_path: Path) -> None:
        (tmp_path / "a.swift").write_text(STORE + (
            "func go() {\n"
            "    if let s = Store() {\n"
            "        s.nosuchmethod()\n"
            "    }\n"
            "}\n"
        ))
        edge = _one(analyze_swift(tmp_path).edges, "nosuchmethod")
        assert (edge.meta or {}).get("receiver_type_hint") == "Store", edge.meta

    def test_the_ITERATED_COLLECTION_keeps_its_own_type(self, tmp_path: Path) -> None:
        """The collection is ALSO a direct `identifier` child of the `for_statement`.
        Accepting it as a binder typed `paths` itself as `NSString` — measured on
        AFNetworking, where `[paths count]` re-keyed from NSArray to NSString. Only
        the declarator binds."""
        (tmp_path / "a.m").write_text(
            "#import <Foundation/Foundation.h>\n"
            "@implementation Thing\n"
            "- (void)go:(NSBundle *)bundle {\n"
            '    NSArray *paths = [bundle pathsForResourcesOfType:@"cer" inDirectory:@"."];\n'
            "    [paths count];\n"
            "    for (NSString *path in paths) {\n"
            '        [path writeToFile:@"/x" atomically:YES];\n'
            "    }\n"
            "}\n"
            "@end\n"
        )
        edges = analyze_objc(tmp_path).edges
        assert _one(edges, ":count:").dst == "objc:NSArray:0-0:count:unresolved"
        assert _one(edges, "writeToFile").dst == (
            "objc:NSString:0-0:writeToFile:atomically::unresolved"
        )
