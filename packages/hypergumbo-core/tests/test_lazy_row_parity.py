# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-tunog: ADR-0049's Lazy rows outside Django, each held or moved on its own proof.

ADR-0049 ruling 4 names a "Lazy / unexecuted" shape -- a call that returns a
query or iterator that has run nothing -- and WI-fasap moved its Django members
to ``db_compose`` once the python analyzer emitted the evaluation site (the
``for``, the index, the ``list()``) as its own ``db_read`` call. That licence was
measured on Django and does not transfer: ruling 3 licenses a move only against
a REPRESENTED crossing, tested at the finding level, so every other member
needs its own proof.

WHAT EACH ROW TURNED OUT TO BE, from the library's own source or documentation
(the row's ``notes`` carry the citation):

* ``TypedQuery.getResultStream`` (javax / jakarta) is NOT lazy. The API's own
  default body is ``getResultList().stream()``, and Hibernate's override runs
  the query plan at the call. It is a transfer and was never a member.
* ``EntityManager.getReference`` IS deferred ("whose state may be lazily
  fetched"), but its read happens at the first access of the entity's state,
  through the application's own accessors, which no catalogue can row.
* ``sqlite3.Connection.iterdump`` IS deferred (a generator), but it is iterated
  in the calling scope and no evaluation site is emitted for it.
* ``std::net::TcpListener.incoming`` IS deferred (an iterator whose ``next`` is
  ``accept``), and the reads of the connections it yields are rowed -- but they
  do not fire, because ``stream.unwrap()`` leaves the receiver untyped.

So none of them moves, and the end-to-end tests below are the reason in a form
that fails: on the shipped catalogue each fixture reports a flow whose ONLY
source is the held row, and the same fixture with that row moved to the
disclosure boundary (a counterfactual overlay, never shipped) reports none.
That second arm is the WI-lunav repeat ruling 3 forbids -- a real read deleted
with nothing to relocate it to -- measured rather than argued. If a later
change represents the crossing (an evaluation site, a typed yielded receiver),
the counterfactual arm keeps its flow and the second test goes red. That is
the signal to measure the move under ruling 3, not a breakage to silence and
not a licence by itself.

Every end-to-end test asserts REACH first (the held row is the source of the
shipped arm's flow), because a fixture that never reaches the row would make
the counterfactual arm vacuously clean.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.io_boundary import load_catalog

_CLAIMS = """claims:
  - id: read-back-no-host-fs
    text: Data read back out of a database or off the network is never written to the filesystem.
    constraint:
      taint_flow:
        source_taint: untrusted_input
        prohibited_sink_zone: host_fs
"""


def _rows(language: str, module: str) -> dict[str, str]:
    """``{name: boundary}`` for one module of the shipped catalogue."""
    return {p.name: p.boundary for p in load_catalog(language).primitives
            if p.module == module}


def _verify(
    tmp_path: Path,
    files: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    overlay: str | None = None,
) -> dict:
    """The one verdict of the shipped ``verify-claims`` over ``files``,
    optionally with an I/O overlay that outranks the built-in rows."""
    repo = tmp_path / "repo"
    for rel, src in files.items():
        dest = repo / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    args = ["verify-claims", str(repo), "--claims", str(claims), "--format", "json"]
    if overlay is not None:
        ov = tmp_path / "counterfactual.yaml"
        ov.write_text(overlay)
        args += ["--io-primitives", str(ov)]
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(args)
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict


def _flows(verdict: dict) -> dict[str, set[str]]:
    """``{source function name: its source primitives}`` over the evidence.

    Keyed by the FUNCTION, not the primitive: in the counterfactual arm the
    moved row mints nothing by construction, so "the primitive is gone" is
    vacuously true. What a move must not do is leave the function's read with
    no source at all, and that is a question about the function."""
    out: dict[str, set[str]] = {}
    for ev in verdict["evidence"]:
        fn = ev["source_symbol"].split(":")[-2]
        out.setdefault(fn, set()).update(ev["source_primitives"])
    return out


# ---------------------------------------------------------------------------
# JPA (java; kotlin and scala inherit the rows through _CATALOG_PARENTS)
# ---------------------------------------------------------------------------

_JPA_SERVICE = """\
package app;

import jakarta.persistence.EntityManager;
import jakarta.persistence.TypedQuery;
import java.io.FileWriter;
import java.io.IOException;
import java.util.stream.Stream;

public class OrderService {
    private EntityManager em;

    public void exportByRef(long id, String path) throws IOException {
        Order order = em.getReference(Order.class, id);
        FileWriter w = new FileWriter(path);
        w.write(order.getNote());
        w.close();
    }

    public void exportStream(String path) throws IOException {
        TypedQuery<Order> q = em.createQuery("select o from Order o", Order.class);
        FileWriter w = new FileWriter(path);
        try (Stream<Order> s = q.getResultStream()) {
            s.forEach(o -> { try { w.write(o.getNote()); } catch (IOException e) { } });
        }
        w.close();
    }
}
"""
_JPA_ORDER = """\
package app;

public class Order {
    private String note;
    public String getNote() { return note; }
}
"""
_JPA_FILES = {
    "src/main/java/app/OrderService.java": _JPA_SERVICE,
    "src/main/java/app/Order.java": _JPA_ORDER,
}
#: The would-be move, never shipped: getReference to the database twin.
_GETREFERENCE_MOVED = """\
language: java
status: overlay
db_compose:
  - module: jakarta.persistence.EntityManager
    methods: [getReference]
"""
_GETREFERENCE = "jakarta.persistence.EntityManager.getReference"
_GETRESULTSTREAM = "jakarta.persistence.TypedQuery.getResultStream"


class TestTheJpaRows:

    @pytest.mark.parametrize("pkg", ["javax.persistence", "jakarta.persistence"])
    @pytest.mark.parametrize("language", ["java", "kotlin", "scala"])
    def test_both_rows_stay_db_read(self, language: str, pkg: str) -> None:
        """Written out, not derived: getResultStream because it is a transfer,
        getReference because ruling 3 holds it."""
        assert _rows(language, f"{pkg}.EntityManager").get("getReference") == "db_read"
        assert _rows(language, f"{pkg}.TypedQuery").get("getResultStream") == "db_read"

    def test_the_read_through_a_reference_rests_on_the_row_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``order.getNote()`` on a reference is where the provider fetches the
        row, and an entity accessor is the application's own method, so the
        only thing representing that read is the ``getReference`` mint."""
        shipped = _flows(_verify(tmp_path / "a", _JPA_FILES, monkeypatch))
        assert shipped.get("OrderService.exportByRef") == {_GETREFERENCE}, shipped  # reach
        assert shipped.get("OrderService.exportStream") == {_GETRESULTSTREAM}, shipped

        moved = _flows(_verify(tmp_path / "b", _JPA_FILES, monkeypatch, _GETREFERENCE_MOVED))
        assert "OrderService.exportByRef" not in moved, (
            "the read through the reference kept a source after the move, so "
            "something now represents it -- getReference may have become movable")
        # Control: the counterfactual moved exactly one row, so the transfer's
        # flow is untouched and the arms differ by the reference alone.
        assert moved.get("OrderService.exportStream") == {_GETRESULTSTREAM}, moved


# ---------------------------------------------------------------------------
# sqlite3.Connection.iterdump (python)
# ---------------------------------------------------------------------------

#: The Python documentation's own iterdump idiom, with the connection typed by
#: annotation. The ``con = sqlite3.connect(...)`` spelling does NOT reach the
#: row -- ``sqlite3.connect`` has no library_signatures return type -- and its
#: flow rests on the ``sqlite3.connect`` row instead, so it cannot test this one.
_ITERDUMP = """\
import sqlite3


def export(con: sqlite3.Connection, path):
    with open(path, "w") as f:
        for line in con.iterdump():
            f.write(line)
"""
_ITERDUMP_MOVED = """\
language: python
status: overlay
db_compose:
  - module: sqlite3.Connection
    methods: [iterdump]
"""


class TestTheIterdumpRow:

    def test_it_stays_db_read(self) -> None:
        assert _rows("python", "sqlite3.Connection").get("iterdump") == "db_read"

    def test_the_dump_written_in_scope_rests_on_the_row_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The generator is iterated two lines below the call, in the same
        function: measurement 0010's READ-IN-SCOPE case, where the mint is
        already in the scope of the read and moving it relocates nothing."""
        files = {"dump.py": _ITERDUMP}
        shipped = _verify(tmp_path / "a", files, monkeypatch)
        assert shipped["verdict"] == "violated", shipped["details"]
        assert _flows(shipped).get("export") == {"sqlite3.Connection.iterdump"}  # reach

        moved = _verify(tmp_path / "b", files, monkeypatch, _ITERDUMP_MOVED)
        assert "export" not in _flows(moved), (
            "the dump kept a source after the move, so something now represents "
            "the iteration -- iterdump may have become movable")
        assert moved["verdict"] != "violated", moved["details"]


# ---------------------------------------------------------------------------
# std::net::TcpListener.incoming (rust)
# ---------------------------------------------------------------------------

#: The std documentation's own accept loop. ``s.read_to_end`` IS rowed
#: (``std::net::TcpStream``), but ``stream.unwrap()`` leaves ``s`` untyped, so
#: that row never fires here and the ``incoming`` mint is the only source.
_INCOMING = """\
use std::fs;
use std::io::Read;
use std::net::TcpListener;

fn serve_incoming() {
    let listener = TcpListener::bind("127.0.0.1:7878").unwrap();
    for stream in listener.incoming() {
        let mut s = stream.unwrap();
        let mut buf = Vec::new();
        s.read_to_end(&mut buf).unwrap();
        fs::write("/var/tmp/in.bin", &buf).unwrap();
    }
}

fn main() {
    serve_incoming();
}
"""
_INCOMING_MOVED = """\
language: rust
status: overlay
net_listen:
  - module: std::net::TcpListener
    methods: [incoming]
"""


class TestTheIncomingRow:

    def test_it_stays_net_recv_beside_accept(self) -> None:
        rows = _rows("rust", "std::net::TcpListener")
        assert rows.get("incoming") == "net_recv"
        assert rows.get("accept") == "net_recv"

    def test_the_connection_read_in_scope_rests_on_the_row_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        files = {"Cargo.toml": '[package]\nname = "srv"\nversion = "0.1.0"\n',
                 "src/main.rs": _INCOMING}
        shipped = _verify(tmp_path / "a", files, monkeypatch)
        assert shipped["verdict"] == "violated", shipped["details"]
        assert _flows(shipped).get("serve_incoming") == {"std::net::TcpListener.incoming"}  # reach

        moved = _verify(tmp_path / "b", files, monkeypatch, _INCOMING_MOVED)
        assert "serve_incoming" not in _flows(moved), (
            "the connection read kept a source after the move, so the yielded "
            "TcpStream is now typed -- incoming may have become movable")
        assert moved["verdict"] != "violated", moved["details"]


# ---------------------------------------------------------------------------
# NSURLSession / URLSession task factories (objc, swift)
# ---------------------------------------------------------------------------

#: Held for a reason the end-to-end arms above cannot express: there is no
#: disclosure value to move them TO. ``net_listen`` is the server side (bind,
#: accept); a client task's response arriving in a completion handler is
#: neither, and filing it there gives one value two readings.
_OBJC_TASK_RECV_ROWS: frozenset[str] = frozenset({
    "dataTaskWithURL:completionHandler:", "dataTaskWithURL:",
    "downloadTaskWithURL:completionHandler:", "downloadTaskWithURL:",
    "downloadTaskWithRequest:completionHandler:", "downloadTaskWithRequest:",
    "downloadTaskWithResumeData:completionHandler:",
})


class TestTheClientTaskRows:

    def test_the_objc_task_factories_stay_net_recv(self) -> None:
        rows = _rows("objc", "NSURLSession")
        assert {n: rows.get(n) for n in _OBJC_TASK_RECV_ROWS} == dict.fromkeys(
            _OBJC_TASK_RECV_ROWS, "net_recv")

    def test_the_swift_download_task_stays_net_recv(self) -> None:
        assert _rows("swift", "URLSession").get("downloadTask") == "net_recv"

    def test_no_client_task_was_filed_under_the_server_side_value(self) -> None:
        """The pin that matters if someone reaches for the nearest deferred
        value: ``net_listen`` carries no ``NSURLSession`` / ``URLSession`` row."""
        for language, module in (("objc", "NSURLSession"), ("swift", "URLSession")):
            assert "net_listen" not in set(_rows(language, module).values())
