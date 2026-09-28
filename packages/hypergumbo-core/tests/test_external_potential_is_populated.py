# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ``external_potential`` bucket is produced again (INV-toguh).

It was empty on every repository measured (six, including hypergumbo itself),
while the code hid it by default for "tending to dominate". The cause was two
definitions of one field. F3 Filter 1 (2026-05-13) skipped every edge with
``is_resolved=False``, reading ADR-0028's "the dst could not be resolved" as
"a receiver-unresolved guess". ADR-0037 ruling 1 (2026-06-10) then made
``is_resolved`` mean IN-REPO-NESS, so every edge into an external placeholder
is ``is_resolved=False`` by definition (ir.py), and the bucket's own entry
condition (the dst is an external boundary) admitted only edges Filter 1 then
dropped: 1,851 of 1,851 on full-stack-fastapi-template, 76,015 of 76,015 on
the self-survey.

Filter 1 is re-keyed to what it was written to drop: a RECEIVER-UNRESOLVED
target, whose module slot names nothing (``python:external:0-0:frob``). A call
into a named external module the catalogue does not classify
(``sqlmodel.Session.exec``, ``react.useState``) is the bucket's stated purpose,
"first-party code reaches into territory the catalogue cannot classify", and
now reaches it. The unit test that pinned Filter 1 built an
``is_resolved=True`` edge into an external dst, the cell ADR-0037 says is never
produced, so these tests run the shipped command on a real survey.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_APP = '''import json
import some_vendor_sdk


def upload(payload, client):
    some_vendor_sdk.push(payload)
    client.frobnicate(payload)
    return json.dumps(payload)


def save(payload):
    with open("out.txt", "w") as fh:
        fh.write(payload)
'''


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp("toguh")
    repo = root / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(_APP)
    mp = pytest.MonkeyPatch()
    mp.setenv("XDG_CACHE_HOME", str(root / "cache"))
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = main(["io-boundaries", str(repo), "--format", "json"])
    finally:
        mp.undo()
    assert rc == 0
    return json.loads(buf.getvalue())


def _external_potential(report: dict) -> set[str]:
    entry = report["boundaries"].get("external_potential")
    return {c["primitive"] for c in entry["chains"]} if entry else set()


def test_a_call_into_an_unclassified_named_module_is_disclosed(report: dict) -> None:
    assert "some_vendor_sdk.push" in _external_potential(report)
    assert report["external_potential_edges"] >= 1


def test_a_receiver_unresolved_call_is_still_skipped(report: dict) -> None:
    """``client.frobnicate`` names no module: the noise Filter 1 exists to drop."""
    assert not any("frobnicate" in p for p in _external_potential(report))


def test_a_call_into_an_enumerated_module_is_not_disclosed(report: dict) -> None:
    """Filter 2 (the closed-world skip) was dead while the bucket was empty.
    ``json`` is enumerated in python.yaml, so an unrowed json call is a known
    non-I/O, not a catalogue gap."""
    assert not any(p.startswith("json.") for p in _external_potential(report))


def test_the_bucket_stays_out_of_the_headline(report: dict) -> None:
    """Disclosed-only: ``total_io_edges`` counts the classified chains alone."""
    classified = sum(
        len(v["chains"]) for b, v in report["boundaries"].items()
        if b != "external_potential"
    )
    assert report["total_io_edges"] == classified
    assert report["boundaries"]["fs_write"]["chain_count"] >= 1  # reach
