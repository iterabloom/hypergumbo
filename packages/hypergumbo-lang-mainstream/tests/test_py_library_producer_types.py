# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-kozaj: a receiver bound to a library FACTORY call is typed from its signature row.

THE GAP. ``_external_constructor_type`` typed a receiver from an external call
only when the call was a construction: a catalogued type name or a PascalCase
import. ``w = csv.writer(f)`` is neither -- ``csv.writer`` is a builtin
function that returns a ``_csv.Writer`` -- so ``w.writerow(row)``, the call
that carries the data into the file, arrived as ``python:external:0-0:writerow``
and no catalogue row could reach it. Measured on the analyzer before this
change: every form below emitted the ``external`` slot.

THE MECHANISM, GENERAL BY CONSTRUCTION. A ``library_signatures`` row keyed by
the callee's IMPORT-RESOLVED qualified name (``csv.writer: _csv.Writer``) types
the call's result, in every place the constructor resolver already answers:
an assignment, a chain root, a ``with`` item, a ``self`` field and a call-site
argument. No call is special-cased: the user-channel test adds a row this file
has never heard of and watches it type a receiver.

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


_FORMS = {
    "dotted": (
        "import csv\n\n\ndef f(fh, row):\n"
        "    w = csv.writer(fh)\n    w.writerow(row)\n"
    ),
    "aliased-module": (
        "import csv as c\n\n\ndef f(fh, row):\n"
        "    w = c.writer(fh)\n    w.writerow(row)\n"
    ),
    "from-import": (
        "from csv import writer\n\n\ndef f(fh, row):\n"
        "    w = writer(fh)\n    w.writerow(row)\n"
    ),
    "chain-root": (
        "import csv\n\n\ndef f(fh, row):\n"
        "    csv.writer(fh).writerow(row)\n"
    ),
    "self-field": (
        "import csv\n\n\nclass Out:\n"
        "    def __init__(self, fh):\n        self.w = csv.writer(fh)\n\n"
        "    def put(self, row):\n        self.w.writerow(row)\n"
    ),
}


class TestTheFactoryResultIsTyped:

    def test_the_shipped_row_exists(self) -> None:
        assert load_library_signatures("python")["csv.writer"] == "_csv.Writer"

    @pytest.mark.parametrize("form", sorted(_FORMS))
    def test_writerow_reaches_the_writer_type(self, tmp_path: Path, form: str) -> None:
        slots = _slots(tmp_path, _FORMS[form], "writerow")
        assert slots, "reach: no writerow call edge at all"
        assert slots == {"_csv.Writer"}, slots


class TestTheBindingIsChecked:
    """The row is keyed by the RESOLVED name, so a namesake matches nothing."""

    def test_a_writer_from_another_module_is_not_a_csv_writer(
        self, tmp_path: Path,
    ) -> None:
        source = (
            "from decoy import writer\n\n\ndef f(fh, row):\n"
            "    w = writer(fh)\n    w.writerow(row)\n"
        )
        slots = _slots(tmp_path, source, "writerow")
        assert slots, "reach"
        assert "_csv.Writer" not in slots, slots

    def test_a_local_writer_function_is_not_a_csv_writer(self, tmp_path: Path) -> None:
        source = (
            "def writer(fh):\n    return fh\n\n\ndef f(fh, row):\n"
            "    w = writer(fh)\n    w.writerow(row)\n"
        )
        slots = _slots(tmp_path, source, "writerow")
        assert slots, "reach"
        assert "_csv.Writer" not in slots, slots

    def test_a_lowercase_function_with_no_row_types_nothing(
        self, tmp_path: Path,
    ) -> None:
        """``json.dumps`` returns a str; with no row it must not type a receiver."""
        source = (
            "import json\n\n\ndef f(x):\n"
            "    s = json.dumps(x)\n    s.writerow(x)\n"
        )
        slots = _slots(tmp_path, source, "writerow")
        assert slots, "reach"
        assert slots.isdisjoint({"json", "json.dumps"}), slots


class TestAnyRowWorksNotOnlyCsv:
    """The mechanism is the row, not the call: a user row for a producer this
    file names nowhere else types its receiver, and removing it untypes it.

    The producer is a name NO shipped row will ever carry. It used to be
    ``urllib.request.build_opener``, until WI-dibit shipped that row built-in
    and the control below went red: a control keyed to a real library name
    tests the catalogue's contents, not the user channel."""

    _SOURCE = (
        "import acme_unrowed.net\n\n\ndef f(url, data):\n"
        "    opener = acme_unrowed.net.make_opener()\n"
        "    opener.open(url, data)\n"
    )

    def _with_user_row(self, tmp_path, monkeypatch, body: str) -> set[str]:
        d = tmp_path / "cfg" / "hypergumbo" / "library_signatures.d"
        d.mkdir(parents=True)
        (d / "python.yaml").write_text(body)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        load_library_signatures.cache_clear()
        try:
            return _slots(tmp_path, self._SOURCE, "open")
        finally:
            load_library_signatures.cache_clear()

    def test_a_user_row_types_the_receiver(self, tmp_path, monkeypatch) -> None:
        slots = self._with_user_row(
            tmp_path, monkeypatch,
            "language: python\nsignatures:\n"
            "  acme_unrowed.net.make_opener: acme_unrowed.net.Opener\n",
        )
        assert slots == {"acme_unrowed.net.Opener"}, slots

    def test_control_without_the_row_the_receiver_is_untyped(
        self, tmp_path, monkeypatch,
    ) -> None:
        slots = self._with_user_row(
            tmp_path, monkeypatch, "language: python\nsignatures:\n  x.y: x.Z\n",
        )
        assert slots, "reach"
        assert "acme_unrowed.net.Opener" not in slots, slots

    def test_a_member_row_types_a_call_written_on_the_class(
        self, tmp_path: Path,
    ) -> None:
        """``sqlite3.Connection.cursor(conn)``: the qualified callee is a member
        row's key, so the same lookup answers it."""
        source = (
            "import sqlite3\n\n\ndef f(conn, q):\n"
            "    cur = sqlite3.Connection.cursor(conn)\n    cur.execute(q)\n"
        )
        assert _slots(tmp_path, source, "execute") == {"sqlite3.Cursor"}


class TestAFromImportedOwnerAdmitsDeclaredTypesOnly:
    """``from defusedcsv import csv`` then ``csv.writer(f)``: the from-imported
    name is the OWNER path (WI-sugom's rule), resolved for the catalogue's types
    and the signature rows, and never by the PascalCase convention."""

    def test_a_declared_factory_under_a_from_imported_module(
        self, tmp_path: Path,
    ) -> None:
        source = (
            "from defusedcsv import csv\n\n\ndef f(fh, row):\n"
            "    w = csv.writer(fh)\n    w.writerow(row)\n"
        )
        assert _slots(tmp_path, source, "writerow") == {"defusedcsv.csv._ProxyWriter"}

    def test_a_catalogued_type_under_a_from_imported_module(
        self, tmp_path: Path,
    ) -> None:
        source = (
            "from defusedcsv import csv\n\n\ndef f(fh, row):\n"
            "    csv.DictWriter(fh, fieldnames=['a']).writerow(row)\n"
        )
        assert _slots(tmp_path, source, "writerow") == {"defusedcsv.csv.DictWriter"}

    def test_the_convention_is_not_applied_to_a_from_imported_owner(
        self, tmp_path: Path,
    ) -> None:
        source = (
            "from mylib import models\n\n\ndef f(x):\n"
            "    fld = models.Field(x)\n    fld.save(x)\n"
        )
        slots = _slots(tmp_path, source, "save")
        assert slots, "reach"
        assert "mylib.models.Field" not in slots, slots
