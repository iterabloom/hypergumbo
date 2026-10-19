# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-tumol: a member call on a TYPED receiver is typed from its signature row.

THE GAP. ``library_signatures/python.yaml`` keys a member row by the member's
qualified name (``sqlite3.Connection.cursor: sqlite3.Cursor``), and WI-kozaj's
producer lookup answers it for a call written on the class
(``sqlite3.Connection.cursor(conn)``). The receiver form, ``conn.cursor()``,
went through :func:`_preserved_receiver_type`, which admitted only rows whose
value equals their owner (``TYPE_PRESERVING_MEMBERS``). So a member that
returns ANOTHER type lost the type at that hop: ``opener.open(url)`` returns an
``http.client.HTTPResponse``, and ``resp.read()`` -- the call where the far
side's bytes reach the program -- arrived as ``python:external:0-0:read``.

THE MECHANISM. The receiver hop asks the same signature row the producer
lookup asks: ``<receiver type>.<member>``'s declared return type. A
type-preserving row is the special case whose value is its owner, so pathlib
and Django chains are unchanged. No member is named in code: the user-channel
tests add rows this file names nowhere else and watch them type a receiver,
and remove them and watch the receiver go untyped.

Every negative is paired with its positive twin, and reach is asserted first.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.library_signatures import load_library_signatures
from hypergumbo_lang_mainstream.py import analyze_python


def _slots(tmp_path: Path, source: str, method: str) -> set[str]:
    """The module slot of every call edge to ``method`` in ``source``."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "mod.py").write_text(source)
    return {
        e.dst.split(":")[1] for e in analyze_python(repo).edges
        if e.edge_type == "calls" and e.dst.split(":")[-2] == method
    }


#: A producer, a member of what it produces, and a method of what THAT returns.
#: The names are fictitious so no shipped row can ever carry them.
_ROWS = (
    "language: python\nsignatures:\n"
    "  acme_unrowed.net.make_opener: acme_unrowed.net.Opener\n"
    "  acme_unrowed.net.Opener.fetch: acme_unrowed.net.Response\n"
)
_PRODUCER_ONLY = (
    "language: python\nsignatures:\n"
    "  acme_unrowed.net.make_opener: acme_unrowed.net.Opener\n"
)

_FORMS = {
    "assigned": (
        "import acme_unrowed.net\n\n\ndef f(url):\n"
        "    opener = acme_unrowed.net.make_opener()\n"
        "    resp = opener.fetch(url)\n"
        "    return resp.body()\n"
    ),
    "chain-on-a-typed-name": (
        "import acme_unrowed.net\n\n\ndef f(url):\n"
        "    opener = acme_unrowed.net.make_opener()\n"
        "    return opener.fetch(url).body()\n"
    ),
    "chain-from-the-producer": (
        "import acme_unrowed.net\n\n\ndef f(url):\n"
        "    return acme_unrowed.net.make_opener().fetch(url).body()\n"
    ),
    "with-item": (
        "import acme_unrowed.net\n\n\ndef f(url):\n"
        "    opener = acme_unrowed.net.make_opener()\n"
        "    with opener.fetch(url) as resp:\n"
        "        return resp.body()\n"
    ),
    "self-field": (
        "import acme_unrowed.net\n\n\nclass C:\n"
        "    def __init__(self):\n"
        "        self.opener = acme_unrowed.net.make_opener()\n\n"
        "    def get(self, url):\n"
        "        resp = self.opener.fetch(url)\n"
        "        return resp.body()\n"
    ),
}


def _with_rows(tmp_path, monkeypatch, rows: str, source: str, method: str) -> set[str]:
    d = tmp_path / "cfg" / "hypergumbo" / "library_signatures.d"
    d.mkdir(parents=True)
    (d / "python.yaml").write_text(rows)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    load_library_signatures.cache_clear()
    try:
        return _slots(tmp_path, source, method)
    finally:
        load_library_signatures.cache_clear()


class TestAMemberRowTypesItsResultOnATypedReceiver:

    @pytest.mark.parametrize("form", sorted(_FORMS))
    def test_the_members_result_is_typed(self, tmp_path, monkeypatch, form) -> None:
        slots = _with_rows(tmp_path, monkeypatch, _ROWS, _FORMS[form], "body")
        assert slots == {"acme_unrowed.net.Response"}, slots

    @pytest.mark.parametrize("form", sorted(_FORMS))
    def test_control_without_the_member_row_the_result_is_untyped(
        self, tmp_path, monkeypatch, form,
    ) -> None:
        """The producer row alone still types ``opener`` (reach), and nothing
        types what ``fetch`` returns."""
        assert _with_rows(
            tmp_path, monkeypatch, _PRODUCER_ONLY, _FORMS[form], "fetch",
        ) == {"acme_unrowed.net.Opener"}
        slots = _with_rows(
            tmp_path / "b", monkeypatch, _PRODUCER_ONLY, _FORMS[form], "body",
        )
        assert slots, "reach: no body call edge at all"
        assert "acme_unrowed.net.Response" not in slots, slots

    def test_a_member_with_no_row_types_nothing(self, tmp_path, monkeypatch) -> None:
        source = (
            "import acme_unrowed.net\n\n\ndef f(url):\n"
            "    opener = acme_unrowed.net.make_opener()\n"
            "    resp = opener.other(url)\n"
            "    return resp.body()\n"
        )
        slots = _with_rows(tmp_path, monkeypatch, _ROWS, source, "body")
        assert slots, "reach"
        assert slots.isdisjoint(
            {"acme_unrowed.net.Response", "acme_unrowed.net.Opener"}), slots


class TestTheShippedRowsReachTheirTypes:
    """Two shipped member rows whose value is not their owner."""

    def test_a_cursor_from_a_connection(self, tmp_path: Path) -> None:
        """The connection is typed by its ANNOTATION: ``sqlite3.connect(p)``
        has no producer row, so an assigned connection stays untyped."""
        source = (
            "import sqlite3\n\n\ndef f(conn: sqlite3.Connection, q):\n"
            "    cur = conn.cursor()\n"
            "    cur.execute(q)\n"
        )
        assert _slots(tmp_path, source, "execute") == {"sqlite3.Cursor"}

    def test_an_http_response_from_an_opener(self, tmp_path: Path) -> None:
        source = (
            "import urllib.request\n\n\ndef f(url):\n"
            "    return urllib.request.build_opener().open(url).read()\n"
        )
        assert _slots(tmp_path, source, "read") == {"http.client.HTTPResponse"}


class TestATypePreservingRowStillPreserves:
    """The special case: a row whose value is its owner keeps the type."""

    def test_a_path_derivation(self, tmp_path: Path) -> None:
        source = (
            "import pathlib\n\n\ndef f(raw, x):\n"
            "    d = pathlib.Path(raw)\n"
            "    d.joinpath('a').resolve().write_text(x)\n"
        )
        assert _slots(tmp_path, source, "write_text") == {"pathlib.Path"}
