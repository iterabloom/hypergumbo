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


# ----------------------------------------------------------------------
# F5 — a SEND declared as a RECEIVE, and the more serious half of INV-nular
#
# Every Phoenix/Plug row shipped under net_recv, and the rows' own notes
# admitted both directions in one slot: "read request / write response",
# "receive and publish events". Two failures at once, and they are not
# symmetric:
#
#   * net_recv is an auto-derived taint SOURCE, so each sender MINTED
#     untrusted_input at a call that emits. That is the same false-positive
#     mechanism as F1-F4.
#   * nothing here was a SINK, so a Phoenix application had NO egress rows for
#     its own response path. A secret written into a response body or header
#     could not be reported as leaving the process at all. That is a
#     FALSE ALL-CLEAR, and it is why this family matters more than the others:
#     the failure direction is "reports safe", not "reports noise".
#
# Unlike F4's rows these MATCH LIVE — `Phoenix.Controller.render`, `send_resp`,
# `Plug.Conn.put_resp_header` and others were observed classifying on
# phoenix-framework during the F4 A/B, which is how the family was found.
# ----------------------------------------------------------------------


class TestPhoenixResponsesAreEgress:
    @pytest.mark.parametrize("name", [
        "render", "json", "text", "html", "redirect", "send_resp",
        "send_download", "put_status"])
    def test_controller_response_helpers_are_sends(self, name: str) -> None:
        assert _has("elixir", "net_send", "Phoenix.Controller", name)
        assert not _has("elixir", "net_recv", "Phoenix.Controller", name)

    @pytest.mark.parametrize("name", [
        "send_resp", "send_file", "send_chunked", "chunk", "resp"])
    def test_plug_conn_response_writers_are_sends(self, name: str) -> None:
        assert _has("elixir", "net_send", "Plug.Conn", name)
        assert not _has("elixir", "net_recv", "Plug.Conn", name)

    @pytest.mark.parametrize("name", [
        "put_resp_header", "put_resp_content_type", "put_resp_cookie"])
    def test_response_header_builders_are_sinks_not_sources(self, name: str) -> None:
        """Included deliberately, on the WI-lunav asymmetry.
        ``put_resp_header(conn, "x", secret)`` is the point at which the secret
        enters the response that will be transmitted, and nothing carries taint
        from the ``conn`` struct to the eventual send — so a sink at the point
        the value ENTERS is what catches it."""
        assert _has("elixir", "net_send", "Plug.Conn", name)
        assert not _has("elixir", "net_recv", "Plug.Conn", name)

    @pytest.mark.parametrize("mod,name", [
        ("Phoenix.Channel", "push"), ("Phoenix.Channel", "broadcast"),
        ("Phoenix.Channel", "reply"), ("Phoenix.Endpoint", "broadcast"),
        ("Phoenix.Endpoint", "broadcast_from")])
    def test_channel_and_endpoint_broadcasts_are_sends(
        self, mod: str, name: str,
    ) -> None:
        assert _has("elixir", "net_send", mod, name)
        assert not _has("elixir", "net_recv", mod, name)

    @pytest.mark.parametrize("name", [
        "read_body", "fetch_query_params", "fetch_cookies"])
    def test_the_genuine_request_reads_stay_receives(self, name: str) -> None:
        """CONTROL. The request side is real and must not move with the rest —
        this is a direction split, not a wholesale reclassification."""
        assert _has("elixir", "net_recv", "Plug.Conn", name)
        assert not _has("elixir", "net_send", "Plug.Conn", name)

    @pytest.mark.parametrize("name", [
        "get", "post", "put", "patch", "delete", "resources", "scope"])
    def test_the_router_dsl_stays_a_receive(self, name: str) -> None:
        """CONTROL, and the Django rule again. A route macro declares where a
        request arrives; the arrival itself has no call site to catalogue, so
        moving or deleting these would relocate the receive to nothing."""
        assert _has("elixir", "net_recv", "Phoenix.Router", name)

    def test_halt_transfers_nothing_and_is_gone(self) -> None:
        """``Plug.Conn.halt`` marks the connection halted. It moves no bytes in
        either direction, so it is neither a source nor a sink."""
        assert not _has("elixir", "net_recv", "Plug.Conn", "halt")
        assert not _has("elixir", "net_send", "Plug.Conn", "halt")

    def test_phoenix_now_has_an_egress_surface_at_all(self) -> None:
        """THE POINT OF THE WHOLE CHANGE, asserted as a property rather than as
        a list. Before this, zero Phoenix/Plug rows were sinks, so no Phoenix
        application could produce a "data left the process" finding through its
        own response path — the analysis could only ever report safe."""
        senders = [p for p in _rows("elixir")
                   if p.boundary == "net_send"
                   and p.module.startswith(("Phoenix", "Plug"))]
        assert len(senders) >= 20, (
            f"Phoenix/Plug egress surface is {len(senders)} rows; the response "
            f"path must be catalogued as a sink or a leak into it reads as safe"
        )

    def test_elixir_inherits_erlang_without_disturbing_it(self) -> None:
        """CONTROL on the parent link: erlang's own network rows are untouched
        by an elixir-only edit (``_CATALOG_PARENTS``: elixir -> erlang)."""
        assert _has("erlang", "net_recv", "gen_tcp", "recv")
        assert _has("elixir", "net_recv", "gen_tcp", "recv")
