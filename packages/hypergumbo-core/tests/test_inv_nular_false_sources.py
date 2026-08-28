# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-nular: three families of rows that manufacture taint SOURCES out of
operations that read nothing.

THE MECHANISM, WHICH IS SHARPER THAN THE ITEM'S TITLE. ``AUTO_SOURCE_LABEL_MAP``
collapses ``net_recv``, ``ipc_recv`` and ``db_read`` into the single label
``untrusted_input``. So a row is not merely a mislabel: declaring a
non-reading operation ``db_read`` or ``net_recv`` MINTS A TAINT SOURCE at that
call site. ``readIORef``, ``em.createQuery(...)`` and ``socket()`` each invent
``untrusted_input`` out of nothing, and every flow downstream of that invented
source is a false positive with a real-looking value chain.

That is the measured mechanism behind measurement 0006's headline being an
upper bound on useful precision rather than a measure of it: all five
shellcheck true positives are genuine dataflow filed under
``untrusted-input-no-database`` against haskell in-process refs.

THE THREE FAMILIES, found by the 2026-08-28 catalogue sweep
(``~/hypergumbo_lab_notebook/nular_sweep_08282026/``):

F1  IN-PROCESS CELLS declared db_read/db_write -- haskell ``Data.IORef``,
    ``Data.STRef``, ``Control.Concurrent.MVar``, ``Control.Concurrent.STM``.
    Process memory: no descriptor, no syscall, nothing outside the process can
    observe them. ``newIORef`` additionally CONSTRUCTS -- it neither reads nor
    writes. haskell.yaml already recorded the doubt in its own notes without
    resolving it.

F2  CONNECTION LIFECYCLE declared net_recv -- ``socket``/``bind``/``listen``.
    ``socket()`` creates an endpoint, ``bind()`` names it, ``listen()`` marks it
    passive. None receives anything; the receive is ``recv``/``recvfrom``/
    ``recvmsg``, correctly rowed alongside and pinned here as controls.

F3  QUERY BUILDERS declared db_read -- JPA ``EntityManager.create*``, JDBC
    ``Connection.createStatement``, the nine ``Ecto.Query`` combinators. None
    issues a statement or returns a row; each returns a builder.

THE RULE THAT DECIDES F3, AND IT IS NOT "DOES THIS CALL ISSUE SQL".
Remove a builder ONLY when the executor is SEPARATELY ROWED, so the read is
still represented somewhere. JPA rows ``TypedQuery.getResultList`` and
``Statement.executeQuery``; Ecto rows ``Ecto.Repo.all``. Both pass.

DJANGO FAILS THAT TEST AND IS DELIBERATELY KEPT. A Django ``QuerySet`` is lazy,
so ``filter``/``all``/``order_by`` issue nothing -- but the execution is an
IMPLICIT ``__iter__`` with NO CALL SITE TO CATALOGUE. There is no executor row
to carry the read, so removing the lazy methods would not relocate the read, it
would delete it. Pinned below as a control, because the naive reading of this
item would strip them.

DIRECTION. Every removal here REPORTS LESS, which per the standing discipline
needs more evidence than a change that reports more. The evidence is that each
removed row mints a source for an operation that observes nothing -- the
removal deletes false flows AT THE SOURCE rather than suppressing real ones.
"""

from __future__ import annotations

import pytest

from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.taint import AUTO_SOURCE_LABEL_MAP

#: Modules whose values live in THIS PROCESS'S heap. Nothing they do crosses an
#: I/O boundary, so no row may place them under one. A denylist rather than a
#: per-row assertion because the failure mode is a future re-add: a gate beats
#: a grep (INV-nular's own duplicate-key finding landed exactly that way).
IN_PROCESS_MODULES = (
    "Data.IORef", "Data.STRef",
    "Control.Concurrent.MVar", "Control.Concurrent.STM",
)

#: The lifecycle calls that set a socket up without transferring anything.
NON_TRANSFER_SOCKET_CALLS = ("socket", "bind", "listen")

ALL_LANGS = ("bash", "c", "cpp", "elixir", "erlang", "go", "haskell", "java",
             "javascript", "kotlin", "objc", "python", "rust", "scala", "swift")


def _rows(lang):
    return list(load_catalog(lang, include_defaults=False).primitives)


def _has(lang, boundary, module, name):
    return any(p.boundary == boundary and p.module == module and p.name == name
               for p in _rows(lang))


# --------------------------------------------------------------------------
# The mechanism itself — pinned so the rationale cannot quietly stop applying.
# --------------------------------------------------------------------------

def test_db_read_and_net_recv_really_do_mint_taint_sources() -> None:
    """The premise of this whole file. If these boundaries ever stop being
    auto-derived sources, every removal below needs re-justifying on other
    grounds, and this test is where that argument arrives."""
    assert AUTO_SOURCE_LABEL_MAP.get("db_read") == "untrusted_input"
    assert AUTO_SOURCE_LABEL_MAP.get("net_recv") == "untrusted_input"


# --------------------------------------------------------------------------
# F1 — in-process cells
# --------------------------------------------------------------------------

@pytest.mark.parametrize("lang", ALL_LANGS)
def test_no_in_process_cell_is_declared_an_io_boundary(lang: str) -> None:
    """31 haskell rows declared process memory as db_read/db_write. Asserted
    over EVERY language, not just haskell: the same shape is available in any
    language with mutable cells, and a denylist that only guards where the bug
    was found is a grep with extra steps."""
    offenders = [f"{p.boundary} {p.module}.{p.name}" for p in _rows(lang)
                 if p.module in IN_PROCESS_MODULES]
    assert offenders == [], (
        f"{lang}: in-process cells declared as I/O boundaries: {offenders}"
    )


def test_haskell_keeps_the_boundaries_it_really_does_cross() -> None:
    """CONTROL. Removing the ref family must not touch haskell's real I/O."""
    assert _has("haskell", "fs_read", "Prelude", "readFile")
    assert _has("haskell", "fs_write", "Prelude", "writeFile")
    assert _has("haskell", "fs_read", "System.IO", "openFile")


# --------------------------------------------------------------------------
# F2 — connection lifecycle
# --------------------------------------------------------------------------

#: F2's scope. A setup call is removed ONLY where a genuine transfer call is
#: still rowed for the same channel -- the same "is the read still represented"
#: rule that decides F3. Verified per language before any row was touched.
F2_LANGS = ("c", "cpp", "elixir", "erlang", "haskell", "java", "kotlin",
            "rust", "scala")


@pytest.mark.parametrize("lang", F2_LANGS)
def test_socket_setup_is_not_a_network_receive(lang: str) -> None:
    offenders = [f"{p.module}.{p.name}" for p in _rows(lang)
                 if p.boundary == "net_recv"
                 and p.name in NON_TRANSFER_SOCKET_CALLS]
    assert offenders == [], (
        f"{lang}: socket setup declared net_recv (receives nothing): {offenders}"
    )


@pytest.mark.parametrize("lang", F2_LANGS)
def test_every_f2_language_keeps_a_real_receive(lang: str) -> None:
    """THE LICENCE FOR EVERY REMOVAL ABOVE. Deleting a setup row is only
    defensible while the channel's actual transfer is still catalogued, so this
    asserts the surviving surface is non-empty in each language the fix
    touches. Without it the fix could silently empty a language's network-input
    coverage and the suite would still be green."""
    survivors = [p for p in _rows(lang)
                 if p.boundary == "net_recv"
                 and p.name not in NON_TRANSFER_SOCKET_CALLS]
    assert survivors, f"{lang}: removal emptied the net_recv surface"


def test_javascript_is_deliberately_out_of_scope() -> None:
    """THE SECOND CONTROL THAT COSTS SOMETHING, and the same rule as Django.
    JavaScript's ENTIRE net_recv surface is callback REGISTRATION --
    ``http.createServer``, ``Deno.listen``/``listenTls``/``listenDatagram``,
    ``WebSocket.onmessage``, ``EventSource.addEventListener``. Not one row is a
    transfer call, because in Node and the browser there IS no call site at
    which bytes arrive; arrival is a callback the runtime invokes.

    So ``Deno.listen`` is a setup call by the F2 definition, and removing it
    would (a) be arbitrary beside ``listenTls``, which is identical in shape,
    and (b) relocate the read to nothing. JavaScript keeps all of it."""
    assert _has("javascript", "net_recv", "Deno", "listen")
    assert _has("javascript", "net_recv", "http", "createServer")


@pytest.mark.parametrize("lang", ("erlang", "elixir"))
@pytest.mark.parametrize("mod", ("gen_tcp", "gen_sctp", "ssl"))
def test_erlang_listen_is_not_a_network_receive(lang: str, mod: str) -> None:
    """elixir inherits erlang, so one edit clears both."""
    assert not _has(lang, "net_recv", mod, "listen")
    assert _has("erlang", "net_recv", "gen_tcp", "recv"), "control"


@pytest.mark.parametrize("lang", ("java", "kotlin", "scala"))
@pytest.mark.parametrize(
    "mod", ("java.net.ServerSocket", "java.net.DatagramSocket"))
def test_jvm_socket_bind_is_not_a_network_receive(lang: str, mod: str) -> None:
    assert not _has(lang, "net_recv", mod, "bind")


@pytest.mark.parametrize(
    "mod", ("std::net::TcpListener", "std::net::UdpSocket"))
def test_rust_socket_bind_is_not_a_network_receive(mod: str) -> None:
    """``TcpListener::bind`` returns a listener and ``UdpSocket::bind`` a
    socket; the receives are ``TcpStream::read`` and ``UdpSocket::recv_from``,
    both rowed and asserted as controls elsewhere in this file."""
    assert not _has("rust", "net_recv", mod, "bind")
    assert _has("rust", "net_recv", "std::net::UdpSocket", "recv_from")


def test_the_actual_receives_are_still_net_recv() -> None:
    """CONTROL, and the reason the removal is safe: c keeps the three calls
    that genuinely receive."""
    for name in ("recv", "recvfrom", "recvmsg"):
        assert _has("c", "net_recv", "sys/socket", name)
    assert _has("cpp", "net_recv", "sys/socket", "recvmsg"), "cpp inherits c"


def test_accept_is_deliberately_kept_as_net_recv() -> None:
    """RULED, NOT OVERLOOKED. ``accept`` returns a descriptor -- but it also
    returns the PEER ADDRESS, which is chosen by whoever connected. That is
    genuinely network-controlled data entering the process, so accept is a real
    source and stays. The conservative direction is also the honest one here:
    keeping it reports MORE, and the evidence for removal does not exist."""
    assert _has("c", "net_recv", "sys/socket", "accept")
    assert _has("python", "net_recv", "socket.socket", "accept")


# --------------------------------------------------------------------------
# F3 — query builders, and the rule that decides them
# --------------------------------------------------------------------------

@pytest.mark.parametrize("lang", ("java", "kotlin", "scala"))
@pytest.mark.parametrize("pkg", ("javax.persistence", "jakarta.persistence"))
@pytest.mark.parametrize(
    "meth", ("createQuery", "createNamedQuery", "createNativeQuery"))
def test_jpa_query_construction_is_not_a_database_read(
    lang: str, pkg: str, meth: str,
) -> None:
    """kotlin and scala are asserted too because ``_CATALOG_PARENTS`` gives
    them java's rows: one edit clears three languages, and a test that only
    watched java would not notice if that inheritance changed."""
    assert not _has(lang, "db_read", f"{pkg}.EntityManager", meth)


@pytest.mark.parametrize("lang", ("java", "kotlin", "scala"))
def test_jdbc_statement_creation_is_not_a_database_read(lang: str) -> None:
    assert not _has(lang, "db_read", "java.sql.Connection", "createStatement")


@pytest.mark.parametrize("lang", ("java", "kotlin", "scala"))
def test_the_jpa_and_jdbc_executors_still_carry_the_read(lang: str) -> None:
    """CONTROL, and the licence for the removal above: the read did not
    disappear, it is rowed at the call that performs it."""
    assert _has(lang, "db_read", "javax.persistence.TypedQuery", "getResultList")
    assert _has(lang, "db_read", "java.sql.Statement", "executeQuery")


@pytest.mark.parametrize(
    "comb", ("from", "where", "select", "join", "group_by",
             "order_by", "limit", "offset", "distinct"))
def test_ecto_query_construction_is_not_a_database_read(comb: str) -> None:
    assert not _has("elixir", "db_read", "Ecto.Query", comb)


def test_the_ecto_executor_still_carries_the_read() -> None:
    """CONTROL. ``Ecto.Repo.all`` is where the query runs."""
    assert _has("elixir", "db_read", "Ecto.Repo", "all")


def test_statement_preparation_is_kept_and_not_folded_in() -> None:
    """RULED SEPARATELY. Most drivers send the SQL to the server at prepare
    time, so ``prepareStatement`` / ``Postgrex.prepare`` may genuinely cross.
    Folding them into the builder family would remove rows on an argument that
    does not cover them."""
    assert _has("java", "db_read", "java.sql.Connection", "prepareStatement")
    assert _has("elixir", "db_read", "Postgrex", "prepare")


@pytest.mark.parametrize(
    "meth", ("filter", "all", "exclude", "order_by", "distinct",
             "values", "annotate", "select_related"))
def test_django_lazy_queryset_methods_are_deliberately_kept(meth: str) -> None:
    """THE CONTROL THAT COSTS SOMETHING. These are builders by the same
    definition that removes JPA's and Ecto's -- a Django QuerySet is lazy and
    ``filter()`` issues nothing. They stay because the rule is not "does this
    call issue SQL" but "is the read still represented after the removal", and
    Django executes through an IMPLICIT ``__iter__`` with no call site to
    catalogue. There is no executor row to relocate the read to, so removing
    these would delete it rather than move it."""
    assert _has("python", "db_read", "django.db.models", meth)


# ----------------------------------------------------------------------
# F4 — LIFECYCLE CALLBACKS AND CLOSE CALLS declared as data transfers
#
# The second sweep pass. Same mechanism as F1-F3 — a row minting a taint
# source or accepting a taint sink at a call that moves no application data —
# but reached through a different shape: connection lifecycle rather than
# construction.
#
# THE RULE IS UNCHANGED AND IS WHAT KEEPS THE SET SMALL: remove a row only
# where the real transfer is still catalogued for the same channel. Every
# removal below leaves the module's genuine surface intact, asserted as a
# control in the same test.
# ----------------------------------------------------------------------


class TestLifecycleIsNotTransfer:
    def test_the_websocket_open_and_error_events_are_not_receives(self) -> None:
        """A WebSocket ``open`` event carries no data, and the ``error`` event
        is specified to carry none — deliberately, so a cross-origin failure
        cannot leak why it failed. Both were minting ``untrusted_input``."""
        assert not _has("javascript", "net_recv", "WebSocket", "onopen")
        assert not _has("javascript", "net_recv", "WebSocket", "onerror")

    def test_the_websocket_close_event_IS_kept(self) -> None:
        """THE DISTINCTION THAT MAKES THE OTHER TWO DEFENSIBLE. A CloseEvent
        carries ``code`` and ``reason``, both chosen by the peer, so it
        genuinely delivers peer-controlled data into the program. Removing all
        three "lifecycle" callbacks together would have been the tidy answer
        and the wrong one."""
        assert _has("javascript", "net_recv", "WebSocket", "onclose")

    def test_the_websocket_data_path_is_untouched(self) -> None:
        """CONTROL."""
        assert _has("javascript", "net_recv", "WebSocket", "onmessage")
        assert _has("javascript", "net_recv", "WebSocket", "addEventListener")
        assert _has("javascript", "net_send", "WebSocket", "send")

    def test_the_eventsource_error_event_is_not_a_receive(self) -> None:
        assert not _has("javascript", "net_recv", "EventSource", "onerror")
        assert _has("javascript", "net_recv", "EventSource", "onmessage")

    def test_broadcastchannel_send_is_a_send_and_is_not_network(self) -> None:
        """TWO DEFECTS IN ONE ROW. ``postMessage`` SENDS and was declared
        ``net_recv`` — a direction error, minting ``untrusted_input`` at a call
        that emits — and the channel is not network in either direction: it is
        same-origin messaging between browsing contexts and nothing leaves the
        browser.

        MOVED, NOT DELETED, which is the whole reason this is safe: deleting
        the row would leave the send represented nowhere, and losing a sink is
        the false-all-clear direction. The receive label is unchanged because
        ``ipc_recv`` and ``net_recv`` both derive ``untrusted_input``."""
        assert not _has("javascript", "net_recv", "BroadcastChannel", "postMessage")
        assert _has("javascript", "ipc_send", "BroadcastChannel", "postMessage")
        assert _has("javascript", "ipc_recv", "BroadcastChannel", "addEventListener")

    def test_closing_a_haskell_socket_is_not_an_egress(self) -> None:
        """``close`` emits a FIN, which is not a payload any line of the
        program produced — and net_send is a taint SINK, so the row accepted a
        tainted value as "sent" at a call that sends nothing of the program's.
        """
        assert not _has("haskell", "net_send", "Network.Socket", "close")
        for name in ("send", "sendTo", "sendAll", "sendMany"):
            assert _has("haskell", "net_send", "Network.Socket", name), "control"

    def test_graceful_close_reads_but_delivers_nothing(self) -> None:
        """The subtler one. ``gracefulClose`` genuinely READS — it drains until
        EOF or timeout — so bytes really do cross the boundary. It still is not
        a source: the function returns unit, so not one of those bytes reaches
        the program, and ``net_recv`` under the auto-source map means a source
        of data the program then USES."""
        assert not _has("haskell", "net_recv", "Network.Socket", "gracefulClose")
        for name in ("recv", "recvFrom"):
            assert _has("haskell", "net_recv", "Network.Socket", name), "control"

    def test_no_removal_stranded_its_module(self) -> None:
        """THE REGRESSION CHECK, added because this exact thing merged green
        once. Removing the only function-kind row from a module that also
        carries method rows leaves it method-starved, which withholds every
        verdict on the repo — that is what PR #602's `bind` removals did, and
        CI missed it because a hand-picked manifest did not name the test.
        Asserted here at the point of removal rather than left to a test in
        another file that this change might again fail to select."""
        # (language, module, the kinds the module carried BEFORE this change)
        for lang, module, kinds_before in (
            ("javascript", "WebSocket", {"method"}),
            ("javascript", "EventSource", {"method"}),
            ("javascript", "BroadcastChannel", {"method"}),
            ("haskell", "Network.Socket", {"function"}),
        ):
            rows = [p for p in _rows(lang) if p.module == module]
            assert rows, f"{lang}:{module} lost every row"
            assert {p.kind for p in rows} == kinds_before, (
                f"{lang}:{module} lost a whole call KIND. A module that keeps "
                f"method rows while losing its last function row is reported "
                f"method-starved, which withholds every verdict on the repo"
            )
