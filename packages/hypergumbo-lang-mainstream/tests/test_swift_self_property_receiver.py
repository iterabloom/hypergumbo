# SPDX-License-Identifier: AGPL-3.0-or-later
"""`self.<property>.<method>()` carries the PROPERTY's type (WI-dodop).

Writing ``self.`` in front of a property destroyed the receiver hint for the
identical call. Measured on a minimal production repro:

    db.write(2)        -> name='write' hint='db'   has_recv=True
    self.db.write(1)   -> name='write' hint=None   has_recv=True

``self`` parses as ``self_expression``, not ``simple_identifier``, so
``_walk_nav`` collects nothing into ``receiver_parts`` and the inner
``navigation_suffix`` -- ``db``, the token that IDENTIFIES the receiver -- is
consumed as a ``method_name`` and then overwritten by the outer suffix. The
emit cascade's third stage (WI-higob slice 2) already routed these sites to
``_swift_receiver_expr_type``, which returned ``None`` for a
``navigation_expression``. That is the gap this closes.

Sized on vapor: 393 of 1,913 classified untyped sites, of which an estimated
59.3% carry an EXTERNAL property type. The estimate is not an acceptance
criterion -- it keys declarations by property name repo-wide -- so these tests
pin the MECHANISM and the corpus delta is measured separately.

THE THIRD LANGUAGE WITH THIS SHAPE: objc WI-garar, rust WI-dizag A.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.swift import analyze_swift


def _edges(root: Path, src: str) -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.swift").write_text(src)
    return analyze_swift(root).edges


def _call(edges: list[Edge], method: str) -> Edge:
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.dst.endswith(f":{method}:unresolved")
    ]
    assert len(hits) == 1, [e.dst for e in edges if method in e.dst]
    return hits[0]


class TestTheModuleSlot:
    def test_an_external_property_type_reaches_the_module_slot(self, tmp_path) -> None:
        """The 59.3% case: the property's type is not a project type."""
        edges = _edges(tmp_path, (
            "import Foundation\n"
            "class Client {\n"
            "    let session: URLSession = URLSession.shared\n"
            "    func go() { self.session.invalidateAndCancel() }\n"
            "}\n"
        ))
        e = _call(edges, "invalidateAndCancel")
        assert e.dst.startswith("swift:URLSession:"), e.dst
        assert (e.meta or {}).get("receiver_type_hint") == "URLSession"

    def test_a_PROJECT_property_type_is_typed_but_REFUSED(self, tmp_path) -> None:
        """INV-kotob: a project type is a symbol, not a module.

        The 34.9% case, and it must NOT be counted as a win: the hint is
        earned and correct, and the module slot stays the ``external``
        placeholder. A change that routed this into the slot would be the
        exact defect filed against objc as WI-dason.
        """
        edges = _edges(tmp_path, (
            "class Database { func write(_ x: Int) { } }\n"
            "class Store {\n"
            "    let db: Database = Database()\n"
            "    func go() { self.db.write(1) }\n"
            "}\n"
        ))
        e = _call(edges, "write")
        assert e.dst.startswith("swift:external:"), e.dst
        assert (e.meta or {}).get("receiver_type_hint") == "Database"


class TestItResolvesTheFIELDNotALocal:
    def test_a_local_of_the_same_name_does_not_capture_self_dot(self, tmp_path) -> None:
        """``self.db`` means the FIELD, whatever a local is called.

        This is the test that pins WHICH resolver is used. ``_type_of``
        consults scoped locals and ``var_types`` BEFORE falling through to
        ``_inherited_field_type``, so wiring this to ``type_of`` would resolve
        ``self.db`` to the local ``Decoy`` here and stamp a confidently wrong
        type -- which `method_call_recovery` step 3a then trusts to REFUTE a
        correct class hint. The field resolver is the only correct one.
        """
        edges = _edges(tmp_path, (
            "import Foundation\n"
            "class Decoy { func invalidateAndCancel() { } }\n"
            "class Client {\n"
            "    let session: URLSession = URLSession.shared\n"
            "    func go() {\n"
            "        let session = Decoy()\n"
            "        self.session.invalidateAndCancel()\n"
            "        _ = session\n"
            "    }\n"
            "}\n"
        ))
        e = _call(edges, "invalidateAndCancel")
        assert (e.meta or {}).get("receiver_type_hint") == "URLSession", (
            "self.session resolved to the shadowing LOCAL, not the field"
        )

    def test_an_INHERITED_property_resolves_through_the_base(self, tmp_path) -> None:
        edges = _edges(tmp_path, (
            "import Foundation\n"
            "class Base { let session: URLSession = URLSession.shared }\n"
            "class Child: Base {\n"
            "    func go() { self.session.invalidateAndCancel() }\n"
            "}\n"
        ))
        e = _call(edges, "invalidateAndCancel")
        assert e.dst.startswith("swift:URLSession:"), e.dst


class TestItStaysSilentWhenItDoesNotKnow:
    def test_an_undeclared_property_stays_untyped(self, tmp_path) -> None:
        """No stamp is the honest answer; a guess would refute a real hint."""
        edges = _edges(tmp_path, (
            "class Client {\n"
            "    func go() { self.mystery.doThing() }\n"
            "}\n"
        ))
        e = _call(edges, "doThing")
        assert e.dst.startswith("swift:external:"), e.dst
        assert (e.meta or {}).get("receiver_type_hint") is None

    def test_a_bare_self_method_call_is_left_alone(self, tmp_path) -> None:
        """Deliberately OUT of this slice.

        ``self.doThing()`` has the enclosing type as its receiver, which is a
        PROJECT type and can never fill the module slot, so typing it buys no
        catalogue reach (L5). It also stamps a hint that
        ``method_call_recovery`` step 3a would act on, so it is a separate
        decision with its own measurement rather than a freebie folded in here.
        """
        edges = _edges(tmp_path, (
            "class Client {\n"
            "    func doThing() { }\n"
            "    func go() { self.doThing() }\n"
            "}\n"
        ))
        hits = [e for e in edges if e.dst.endswith(":doThing:unresolved")]
        for e in hits:
            assert (e.meta or {}).get("receiver_type_hint") is None

    def test_a_DEEPER_self_chain_is_not_guessed_at(self, tmp_path) -> None:
        """`self.a.b.method()` — the receiver is `self.a.b`, not `self.<prop>`.

        112 of the 1,913 classified vapor sites are a chain off a
        navigation_expression. Resolving one needs the type of `self.a` first
        and then a member lookup on THAT type, which this slice does not do.
        The honest answer is no stamp: `method_call_recovery` step 3a treats a
        stamped `receiver_type_hint` as grounds to REFUTE a class hint, so a
        guess here would delete a correct recovery somewhere else.
        """
        edges = _edges(tmp_path, (
            "import Foundation\n"
            "class Inner { let session: URLSession = URLSession.shared }\n"
            "class Outer {\n"
            "    let inner: Inner = Inner()\n"
            "    func go() { self.inner.session.invalidateAndCancel() }\n"
            "}\n"
        ))
        e = _call(edges, "invalidateAndCancel")
        assert e.dst.startswith("swift:external:"), e.dst
        assert (e.meta or {}).get("receiver_type_hint") is None
