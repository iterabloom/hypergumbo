# SPDX-License-Identifier: AGPL-3.0-or-later
"""An own instance field of EXTERNAL type carries its module to the call site.

INV-fibis recall half. ``class_field_types`` is ``dict[str, dict[str, Symbol]]``
-- Symbol-valued, so BY CONSTRUCTION it can only ever hold an IN-REPO class.
Locals have both halves (``var_types`` for in-repo, ``external_var_types`` for
catalogued external types, WI-fuvuj); fields had only the first. So::

    s = socket.socket()          # local  -> python:socket.socket:0-0:sendall:...
    s.sendall(payload)

    self.sock = socket.socket()  # field  -> python:external:0-0:sendall:...
    self.sock.sendall(payload)

Two spellings of one fact, and only the local one reaches the catalogue.

WHY THIS IS THE RECALL FIX AND NOT A WIDENING OF THE MATCHER. INV-nuhun
established that the sink lookup refuses an untyped method receiver TWICE over,
and that BOTH refusals are the deliberate closure of INV-tapat and INV-maluk
(both `satisfied`; INV-maluk was reopened over exactly this consumer). Nothing
here touches that gate. It supplies the module hint the gate has always asked
for -- verified at the unit in INV-nuhun's own probe, where
``_lookup_named_entry(hits, "sendall", "socket.socket")`` returns the network
sink while the same call with ``None`` and with ``"external"`` returns None. The
gate opens on evidence; this produces the evidence.

INV-jujoh (satisfied, PR #480) made these calls EMIT at all -- before it there
was no edge to carry a module. This is the next link in the same chain: the edge
now exists, and here it stops being anonymous.
"""

from pathlib import Path

from hypergumbo_lang_mainstream.py import analyze_python, extract_nodes


def _analyze(tmp_path: Path, src: str):
    f = tmp_path / "m.py"
    f.write_text(src)
    return extract_nodes(f)


def _method_edges(res, attr: str):
    return [
        e for e in res.edges
        if e.edge_type == "calls" and e.dst.endswith(f":{attr}:unresolved")
    ]


def _dst_module(res, attr: str) -> str:
    edges = _method_edges(res, attr)
    assert len(edges) == 1, [e.dst for e in edges]
    return edges[0].dst.split(":")[1]


class TestTheFieldCarriesItsModule:
    """The three shapes an __init__ can establish an external field type from."""

    def test_annotated_init_param_assigned_to_a_field(self, tmp_path: Path) -> None:
        res = _analyze(tmp_path, """
import socket


class Client:
    def __init__(self, sock: socket.socket):
        self.sock = sock

    def send(self, payload):
        return self.sock.sendall(payload)
""")
        assert _dst_module(res, "sendall") == "socket.socket"

    def test_external_constructor_assigned_to_a_field(self, tmp_path: Path) -> None:
        res = _analyze(tmp_path, """
import socket


class Client:
    def __init__(self):
        self.sock = socket.socket()

    def send(self, payload):
        return self.sock.sendall(payload)
""")
        assert _dst_module(res, "sendall") == "socket.socket"

    def test_annotated_field_declaration(self, tmp_path: Path) -> None:
        """``self.sock: socket.socket = sock`` -- the AnnAssign form WI-sajub
        added to the own-field scan but never routed to a TYPE."""
        res = _analyze(tmp_path, """
import socket


class Client:
    def __init__(self, sock):
        self.sock: socket.socket = sock

    def send(self, payload):
        return self.sock.sendall(payload)
""")
        assert _dst_module(res, "sendall") == "socket.socket"

    def test_the_edge_says_the_type_was_INFERRED(self, tmp_path: Path) -> None:
        """Parity with the local-variable branch (WI-fuvuj): a receiver typed by
        inference is labelled, so a consumer can tell it from a declared one.
        WI-javus: the give-up branch must NOT wear this label, which is what
        ``test_an_untyped_own_field_stays_anonymous`` holds."""
        res = _analyze(tmp_path, """
import socket


class Client:
    def __init__(self, sock: socket.socket):
        self.sock = sock

    def send(self, payload):
        return self.sock.sendall(payload)
""")
        e = _method_edges(res, "sendall")[0]
        assert e.meta.get("resolution_quality") == "type_inferred"
        assert e.meta.get("call_construct") == "method"
        assert e.is_resolved is False


class TestItDoesNotDisturbWhatINVjujohEstablished:
    """Every one of these passed before this change and must still pass."""

    def test_an_untyped_own_field_stays_anonymous(self, tmp_path: Path) -> None:
        """INV-jujoh's own case: the EDGE must still exist (that was its whole
        point) and must still be honest that nothing was inferred."""
        res = _analyze(tmp_path, """
class Client:
    def __init__(self, sock):
        self.sock = sock

    def send(self, payload):
        return self.sock.sendall(payload)
""")
        edges = _method_edges(res, "sendall")
        assert len(edges) == 1
        assert edges[0].dst == "python:external:0-0:sendall:unresolved"
        assert edges[0].meta.get("resolution_quality") != "type_inferred"

    def test_an_untyped_own_field_carries_no_inherited_stamp(
        self, tmp_path: Path,
    ) -> None:
        res = _analyze(tmp_path, """
class Client:
    def __init__(self, sock):
        self.sock = sock

    def send(self, payload):
        return self.sock.sendall(payload)
""")
        assert "inherited_field_receiver" not in _method_edges(res, "sendall")[0].meta

    def test_an_inherited_field_is_still_stamped_and_not_typed(
        self, tmp_path: Path,
    ) -> None:
        """``self.conn`` is assigned by no __init__ in this file, so it may be a
        PARENT's field. It must keep the stamp that routes it to the
        inherited_calls walk, and must NOT acquire a module it has no evidence
        for."""
        res = _analyze(tmp_path, """
import socket


class Client(Base):
    def __init__(self, sock: socket.socket):
        self.sock = sock

    def send(self, payload):
        return self.conn.sendall(payload)
""")
        e = _method_edges(res, "sendall")[0]
        assert e.dst == "python:external:0-0:sendall:unresolved"
        assert e.meta.get("inherited_field_receiver") == "conn"

    def test_an_in_repo_class_field_still_resolves(self, tmp_path: Path) -> None:
        """Case 2f, the Symbol-valued path, is untouched: an in-repo field type
        still produces a RESOLVED edge, not an inferred external one.

        USES ``analyze_python``, NOT ``extract_nodes``, and the difference is not
        cosmetic: the single-file helper passes ``global_symbols=None``, so the
        in-repo field resolution never runs there and this control would have
        passed for the wrong reason (no edge at all, rather than a resolved one).
        Measured before being written down."""
        (tmp_path / "m.py").write_text("""
class Engine:
    def run(self):
        return 1


class Client:
    def __init__(self):
        self.engine = Engine()

    def go(self):
        return self.engine.run()
""")
        res = analyze_python(tmp_path)
        resolved = [
            e for e in res.edges
            if e.edge_type == "calls" and e.is_resolved and "run" in e.dst
        ]
        assert resolved, [e.dst for e in res.edges if e.edge_type == "calls"]


class TestTheFieldNamespaceIsSeparateFromTheLocalOne:
    """A field and a local of the same name are different variables, and the
    external-type map is keyed by BARE name for locals. Colliding them would let
    a local's type answer for a field, or the reverse."""

    def test_a_local_of_the_same_name_does_not_take_the_fields_type(
        self, tmp_path: Path,
    ) -> None:
        res = _analyze(tmp_path, """
import socket


class Client:
    def __init__(self, sock: socket.socket):
        self.sock = sock

    def send(self, payload):
        sock = make_something_else()
        return sock.sendall(payload)
""")
        e = _method_edges(res, "sendall")[0]
        assert e.dst == "python:external:0-0:sendall:unresolved", (
            "the LOCAL sock is untyped; the field's type must not answer for it"
        )

    def test_the_field_keeps_its_type_when_a_local_shares_the_name(
        self, tmp_path: Path,
    ) -> None:
        res = _analyze(tmp_path, """
import socket


class Client:
    def __init__(self, sock: socket.socket):
        self.sock = sock

    def send(self, payload):
        sock = make_something_else()
        sock.close()
        return self.sock.sendall(payload)
""")
        assert _dst_module(res, "sendall") == "socket.socket"
