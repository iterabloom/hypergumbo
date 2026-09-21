# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-kazuk: ``ssl.SSLObject`` does no network I/O, so it carries no rows.

WHAT WAS WRONG. Three python catalogue rows asserted a network crossing at a
class that performs none: ``ssl.SSLObject.read`` (``net_recv``),
``ssl.SSLObject.write`` and ``ssl.SSLObject.do_handshake`` (``net_send``).
CPython's own docstring is explicit -- the object "does not provide any network
IO itself. IO needs to be performed through separate "BIO" objects", and when
compared to ``SSLSocket`` it "lacks ... **Any form of network IO, including
methods such as ``recv`` and ``send``**". The rows were kind-asserted by NAME:
``SSLObject``'s methods are spelled like ``SSLSocket``'s, and ``SSLSocket``
really does cross.

WHY THIS IS NOT A JUDGEMENT CALL. The project already ruled this class of
question, twice, and the ruling is older than the rows. The **File-object rule**
(``docs/surveys/stdlib-module-completeness-scope.md`` §3): *"Taking a
caller-opened object is not I/O (``json.load(fp)``, ``tomllib.load(fp)``);
opening a path is."* ``SSLContext.wrap_bio(incoming, outgoing)`` is handed two
``MemoryBIO``s the CALLER made and the CALLER feeds; ``SSLObject.read`` takes a
caller-opened object and is ``pickle.load`` for a memory buffer.
:class:`TestTheRuleAlreadyDecidedThis` pins the siblings so the rule cannot
quietly stop applying to them while this file still passes.

The defect's provenance is recorded in the tree: ``python.yaml``'s own
completeness note says ssl is *"rowed above but ... the MemoryBIO surface were
not read one by one; PARTIAL, not granted"*. They have now been read one by one.

ADR-0049 RULING 3, PAID RATHER THAN ARGUED AWAY. Removal is licensed only once
the arrival scope is represented as a source, and the ruling insists the test be
run **at the finding level, not the row level** -- WI-lunav's precedent is a row
that "transmits nothing" whose deletion still collapsed a real ``violated(3)``
to ``inconclusive(0)``. So :class:`TestTheCrossingStaysRepresented` runs the
REAL python analyzer over a ``wrap_bio`` fixture and asserts the network read is
still reported, anchored where it actually happens -- the caller's own
``socket.socket.recv``.

THE FIXTURE IS PRODUCER-SHAPED, NOT HAND-WRITTEN (ADR-0057 §12). The edges come
from :func:`analyze_python` over real source, because the sibling file
``test_verify_claims_examined_negative.py`` records that a hand-written
catalogue fixture is how a false all-clear survived once already.

WHY THE RECEIVER IS ANNOTATED IN THE FIXTURE, which is the subtlest thing here.
Written idiomatically -- ``sslobj = ctx.wrap_bio(...)`` -- the analyzer cannot
type the receiver, the calls land in the unresolved slot, and these rows never
fire at all. That makes them INERT on the common form and would make any
assertion here vacuously true. Annotating ``sslobj: ssl.SSLObject`` is what
makes the rows REACHABLE, and :class:`TestTheControlCanFail` proves it by
reinstating them and watching three false boundaries come back. A row that is
inert today is still a latent false-positive generator the moment a codebase
annotates its receivers, which is why this is a fix and not a shrug.
"""

from __future__ import annotations

import pathlib
from dataclasses import replace

import pytest

from hypergumbo_core.io_boundary import (
    classify_call,
    load_catalog,
    tag_io_boundaries,
)
from hypergumbo_lang_mainstream.py import analyze_python

CATALOGS = {"python": load_catalog("python")}

#: The three method entries INV-kazuk retires, with the boundary each used to
#: assert. Kept as data so the reinstating control below cannot drift from the
#: removal it is controlling for.
RETIRED_ROWS = (
    ("read", "net_recv"),
    ("write", "net_send"),
    ("do_handshake", "net_send"),
)


def _boundary(module: str, name: str) -> str | None:
    """The boundary the SHIPPED matcher gives a call, or None."""
    dst = f"python:{module}:0-0:{name}:external_symbol"
    primitive = classify_call(CATALOGS, dst, None)
    return primitive.boundary if primitive else None


def _write_bio_fixture(tmp_path: pathlib.Path) -> pathlib.Path:
    """A wrap_bio pump with the receiver ANNOTATED, so the rows are reachable.

    The caller owns the socket and both BIOs; the only real crossing in the
    function is ``sock.recv``.
    """
    (tmp_path / "tls_bio.py").write_text(
        "import socket\n"
        "import ssl\n"
        "\n"
        "\n"
        "def pump(sock: socket.socket, sslobj: ssl.SSLObject,\n"
        "         incoming: ssl.MemoryBIO) -> bytes:\n"
        "    sslobj.do_handshake()\n"
        "    incoming.write(sock.recv(4096))\n"
        "    plaintext = sslobj.read(4096)\n"
        "    sslobj.write(plaintext)\n"
        "    return plaintext\n"
    )
    return tmp_path


def _boundaries_tagged(tmp_path: pathlib.Path, catalogs) -> dict[str, str]:
    """dst -> io_boundary for every edge the tagger marked."""
    result = analyze_python(_write_bio_fixture(tmp_path))
    tag_io_boundaries(result.edges, catalogs)
    return {
        e.dst: (e.meta or {})["io_boundary"]
        for e in result.edges
        if (e.meta or {}).get("io_boundary")
    }


def _catalog_with_the_rows_reinstated():
    """The python catalogue as it stood BEFORE this fix.

    Built by cloning the shipped ``ssl.SSLSocket`` rows onto ``ssl.SSLObject``
    rather than by writing an ``IoPrimitive`` literal, so the control cannot
    drift from the real row shape when a field is added to the dataclass.
    """
    cat = load_catalog("python")
    assert not any(p.module == "ssl.SSLObject" for p in cat.primitives), (
        "the shipped catalogue still rows ssl.SSLObject, so this control is "
        "controlling for nothing -- INV-kazuk's removal has been reverted"
    )
    donors = {
        boundary: next(
            p for p in cat.primitives
            if p.module == "ssl.SSLSocket" and p.boundary == boundary
        )
        for _, boundary in RETIRED_ROWS
    }
    revived = [
        replace(donors[boundary], module="ssl.SSLObject", name=name)
        for name, boundary in RETIRED_ROWS
    ]
    return replace(cat, primitives=list(cat.primitives) + revived)


class TestTheRuleAlreadyDecidedThis:
    """The File-object rule is live, and it is what retires these rows."""

    @pytest.mark.parametrize("module,name", [
        ("pickle", "load"),
        ("marshal", "load"),
    ])
    def test_a_read_from_a_caller_opened_object_is_not_io(
        self, module: str, name: str,
    ) -> None:
        assert _boundary(module, name) is None

    @pytest.mark.parametrize("module,name", [
        ("io.BytesIO", "read"),
        ("io.BytesIO", "write"),
        ("io.StringIO", "write"),
    ])
    def test_an_in_memory_buffer_is_not_a_boundary(
        self, module: str, name: str,
    ) -> None:
        """A ``MemoryBIO`` is one of these wearing a socket's method names."""
        assert _boundary(module, name) is None


class TestTheThreeRowsAreRetired:
    """INV-kazuk proper: the class that does no network I/O rows none."""

    @pytest.mark.parametrize("name", [name for name, _ in RETIRED_ROWS])
    def test_sslobject_method_asserts_no_crossing(self, name: str) -> None:
        assert _boundary("ssl.SSLObject", name) is None

    def test_no_sslobject_row_survives_anywhere_in_the_catalogue(self) -> None:
        """Not just these three names -- the whole module carries no row."""
        assert [
            (p.module, p.name, p.boundary)
            for p in CATALOGS["python"].primitives
            if p.module == "ssl.SSLObject"
        ] == []


class TestTheCrossingStaysRepresented:
    """ADR-0049 ruling 3: removal is licensed against a represented crossing."""

    @pytest.mark.parametrize("module,name,boundary", [
        ("socket.socket", "recv", "net_recv"),
        ("socket.socket", "send", "net_send"),
        ("socket.socket", "sendall", "net_send"),
        ("ssl.SSLSocket", "read", "net_recv"),
        ("ssl.SSLSocket", "recv", "net_recv"),
        ("ssl.SSLSocket", "sendall", "net_send"),
    ])
    def test_the_socket_backed_rows_stand(
        self, module: str, name: str, boundary: str,
    ) -> None:
        """The socket-backed twin really does cross, and keeps its rows.

        This is the difference arm for the removal: it is scoped to one class,
        not a sweep of the ssl surface.
        """
        assert _boundary(module, name) == boundary

    def test_the_bio_idiom_still_reports_its_network_read(
        self, tmp_path: pathlib.Path,
    ) -> None:
        """The finding-level licence, run rather than argued.

        One boundary on the whole function, and it is the caller's own socket
        read -- where the far-side bytes actually enter.
        """
        tagged = _boundaries_tagged(tmp_path, CATALOGS)
        assert tagged == {
            "python:socket.socket:0-0:recv:unresolved": "net_recv",
        }

    def test_no_sslobject_call_carries_a_boundary_in_the_fixture(
        self, tmp_path: pathlib.Path,
    ) -> None:
        tagged = _boundaries_tagged(tmp_path, CATALOGS)
        assert [dst for dst in tagged if "SSLObject" in dst] == []


class TestTheControlCanFail:
    """Mutate what the tests pin and watch them go red (LIVE rule 6)."""

    def test_reinstating_the_rows_brings_back_three_false_boundaries(
        self, tmp_path: pathlib.Path,
    ) -> None:
        """Proves the fixture REACHES the rows, so the assertions above are
        not vacuously true on an untyped receiver."""
        tagged = _boundaries_tagged(
            tmp_path, {"python": _catalog_with_the_rows_reinstated()},
        )
        assert tagged == {
            "python:socket.socket:0-0:recv:unresolved": "net_recv",
            "python:ssl.SSLObject:0-0:read:unresolved": "net_recv",
            "python:ssl.SSLObject:0-0:write:unresolved": "net_send",
            "python:ssl.SSLObject:0-0:do_handshake:unresolved": "net_send",
        }

    def test_the_removal_is_worth_three_of_four_boundaries_here(
        self, tmp_path: pathlib.Path,
    ) -> None:
        before = _boundaries_tagged(
            tmp_path, {"python": _catalog_with_the_rows_reinstated()},
        )
        after = _boundaries_tagged(tmp_path, CATALOGS)
        assert (len(before), len(after)) == (4, 1)
