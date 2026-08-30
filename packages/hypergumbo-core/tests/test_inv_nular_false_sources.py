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

import pathlib
import re

import pytest

from hypergumbo_core import io_boundary as io_boundary_module
from hypergumbo_core.io_boundary import (
    MULTI_BOUNDARY_REASON_SIMULTANEOUS,
    IoBoundaryCatalog,
    load_catalog,
    multi_boundary_reason,
)
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


#: THE DIRECTION SWEEP'S VOCABULARY, hoisted to module scope so it is a named
#: thing a test can interrogate rather than a literal buried in one assertion.
#:
#: `response`/`resp`/`render` were ADDED by WI-joruz. The shipped vocabulary
#: carried the VERB ``respond`` and not the NOUN ``response``, so haskell's
#: ``Network.Wai.responseLBS`` -- a send declared as a receive, the same defect
#: F5 fixed for Phoenix -- matched nothing and the sweep reported clean. A
#: name-based gate with an incomplete vocabulary does not fail; it exits 0.
#:
#: The widening is deliberately TIGHT. A generous one (serve/output/request/
#: accept/...) was measured first and returned 43 extra hits, every one of them
#: a false alarm: an HTTP client's ``request()`` sends, ``wait_with_output``
#: reads, and ``ServerBootstrap`` merely contains "Serve". Only the three
#: response-noun forms survive, and they find exactly the four Wai rows.
DIRECTION_SENDY = re.compile(
    r'(?i)(^|[._])(send|write|put|post|publish|emit|broadcast|reply|respond|'
    r'response|resp|render|upload|push)')
DIRECTION_RECVY = re.compile(
    r'(?i)(^|[._])(recv|receive|read|fetch|download|poll|subscribe|consume)')


def _rows(lang):
    """Every row the TOOL loads, including the shipped community overlays.

    ``include_defaults=False`` here until 2026-08-28, which was wrong for this
    file's own question and became visibly wrong when ADR-0047 ruling 1
    (WI-surun) relocated 293 third-party rows into overlays that load BY
    DEFAULT. What this file asserts is whether a row MINTS A TAINT SOURCE at a
    call site -- and ``AUTO_SOURCE_LABEL_MAP`` does not consult which file a row
    came from. A ``net_recv`` row in a default overlay mints ``untrusted_input``
    exactly as one in the catalogue does.

    So the merged view is both the correct view and the STRICTER one, in both
    directions: a removal asserted here can no longer be undone by re-adding
    the row to an overlay, and a "deliberately kept" control now says what it
    means -- that the crossing is still REPRESENTED, not that it sits in one
    particular file. Django's lazy QuerySet is the case that makes the
    difference concrete: its rows moved to an overlay, and the reason they are
    kept (no executor row exists to carry the read) is untouched by the move.
    """
    return list(load_catalog(lang).primitives)


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
#:
#: WAS A HARDCODED NINE-NAME LIST UNTIL INV-kanuk, AND IT WAS BLIND TWICE OVER.
#: The tree ships fifteen catalogues; this named nine, so go, objc, python,
#: swift and bash were never asked. Worse, adding go would STILL have reported
#: clean, because the predicate matched exact-case names and go capitalises
#: every exported identifier -- ``syscall.Socket`` is not ``socket``. A
#: name-based gate with an incomplete matcher does not fail, it exits 0
#: (LIVE.md rule 6). Both halves are now closed: the scope is derived from the
#: tree by :func:`_f2_languages` and the predicate is case-folded.
F2_LANGS = ("c", "cpp", "elixir", "erlang", "haskell", "java", "kotlin",
            "rust", "scala")


def _f2_languages() -> tuple[str, ...]:
    """The tree's OWN catalogue languages, not a list maintained beside it.

    A hardcoded inventory decays (LIVE.md rule 10), and this one had: nine
    names against fifteen shipped catalogues. Deriving it means a new
    ``io_primitives/<lang>.yaml`` is asked the F2 question on the commit that
    adds it, with no second edit to remember.

    Deliberately NOT ``CATALOG_LANGUAGES``: that constant names fourteen while
    the package ships fifteen (``bash.yaml`` sits outside it, which is how it
    also sat outside the WI-sugav subprocess guard). Inheriting that list would
    reproduce the very defect this function exists to close.
    """
    catalog_dir = (pathlib.Path(io_boundary_module.__file__).parent
                   / "io_primitives")
    return tuple(sorted(p.stem for p in catalog_dir.glob("*.yaml")))


#: Languages whose setup rows are KEPT, mapped to why. An exemption here is a
#: DOCUMENTED DEBT, not a pass -- and it is pinned in BOTH directions by
#: :func:`test_f2_exemptions_still_have_something_to_exempt`, which fails when
#: an exempt language stops having offenders. So an exemption cannot quietly
#: outlive the condition that justified it: whoever fixes the underlying gap is
#: forced to delete the entry in the same change.
F2_EXEMPT: dict[str, str] = {
    "javascript": (
        "JS's ENTIRE net_recv surface is callback registration -- there is no "
        "call site at which bytes arrive. Removing Deno.listen would relocate "
        "the read to nothing. Asserted positively in "
        "test_javascript_is_deliberately_out_of_scope."
    ),
}

#: ROW-level F2 exemptions, which are a DIFFERENT and much narrower thing than
#: the language-level table above: they say "this one row is a FALSE POSITIVE OF
#: THE PREDICATE", not "this language has debt".
#:
#: The predicate is a lowercase name test over ``socket``/``bind``/``listen``,
#: and the family it polices is cut by MECHANISM, not by name. Those two
#: disagreed on exactly one shipped row: fiber's ``App.Listen`` is a server
#: LAUNCH -- it blocks and serves -- and is named ``Listen``.
#:
#: **IT IS EMPTY NOW, AND THAT IS THE MECHANISM WORKING RATHER THAN A REASON TO
#: DELETE IT.** The fiber entry said in terms that the row "moves with the
#: launch family, not with INV-kanuk's setup rows", and
#: :func:`test_f2_row_exemptions_still_describe_a_live_row` was written to fail
#: the moment it did -- so the retag was forced to delete the entry in the same
#: change. That is a self-retiring exemption completing its life cycle. The
#: dict stays because the NEXT predicate false positive should land here rather
#: than in a language-wide skip, which is the strictly wider thing it replaced.
F2_EXEMPT_ROWS: dict[str, dict[tuple[str, str], str]] = {}


@pytest.mark.parametrize("lang", _f2_languages())
def test_socket_setup_is_not_a_network_receive(lang: str) -> None:
    """CASE-FOLDED, and that is not a detail -- it is half of INV-kanuk.

    The predicate used to be exact-case membership, which cannot see a single
    go row: `syscall.Socket`, `unix.Bind`, `net.Listen`. Widening the language
    list alone would have left this gate reporting clean over eight live false
    sources.
    """
    if lang in F2_EXEMPT:
        pytest.skip(f"{lang}: documented F2 exemption -- {F2_EXEMPT[lang]}")
    exempt = F2_EXEMPT_ROWS.get(lang, {})
    offenders = [f"{p.module}.{p.name}" for p in _rows(lang)
                 if p.boundary == "net_recv"
                 and p.name.lower() in NON_TRANSFER_SOCKET_CALLS
                 and (p.module, p.name) not in exempt]
    assert offenders == [], (
        f"{lang}: socket setup declared net_recv (receives nothing): {offenders}"
    )


@pytest.mark.parametrize("lang", sorted(F2_EXEMPT))
def test_f2_exemptions_still_have_something_to_exempt(lang: str) -> None:
    """THE GATE ON THE EXEMPTION LIST, so a debt cannot outlive its reason.

    An exemption that stays after its offenders are gone is indistinguishable
    from a language that was never in scope, and the next reader has no way to
    tell which. Asserting the offenders are still THERE makes the entry
    self-retiring: clear the underlying gap, this fails, and the same change
    that fixes it must delete the entry.
    """
    offenders = [f"{p.module}.{p.name}" for p in _rows(lang)
                 if p.boundary == "net_recv"
                 and p.name.lower() in NON_TRANSFER_SOCKET_CALLS]
    assert offenders, (
        f"{lang}: F2 exemption has no offenders left -- delete the F2_EXEMPT "
        f"entry, the reason it records no longer applies"
    )


def test_the_f2_scope_is_derived_from_the_tree_not_maintained_beside_it() -> None:
    """A GATE ON THE INSTRUMENT (the WI-vafit shape).

    INV-kanuk's root cause was an inventory that decayed silently: F2_LANGS
    held nine names while the tree shipped fifteen, and nothing said so. This
    asserts the derivation covers the tree and that the historical list is a
    strict subset -- so a catalogue added tomorrow is asked the F2 question
    without anyone remembering to widen a literal.
    """
    derived = set(_f2_languages())
    catalog_dir = (pathlib.Path(io_boundary_module.__file__).parent
                   / "io_primitives")
    assert derived == {p.stem for p in catalog_dir.glob("*.yaml")}
    assert set(F2_LANGS) < derived, (
        "F2_LANGS should be a strict subset of the tree's catalogues; if it is "
        "not, the historical record is wrong rather than the derivation"
    )
    assert set(F2_EXEMPT) <= derived, "an exemption names a non-existent catalogue"


def test_the_f2_predicate_is_case_insensitive() -> None:
    """THE OTHER HALF OF INV-kanuk, asserted on the MATCHER not the catalogue.

    Go capitalises every exported identifier, so an exact-case membership test
    against ("socket", "bind", "listen") flags none of its eight rows. This
    pins the fold itself, independently of whether any language currently has
    an offender -- the same reason the direction sweep's vocabulary is
    asserted rather than assumed (WI-joruz).
    """
    for name in NON_TRANSFER_SOCKET_CALLS:
        assert name == name.lower(), "the vocabulary must be the folded form"
        assert name.capitalize().lower() in NON_TRANSFER_SOCKET_CALLS
        assert name.upper().lower() in NON_TRANSFER_SOCKET_CALLS


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


# ----------------------------------------------------------------------
# F6 — an INPUT declared as an OUTPUT, found by a systematic direction sweep
#
# F5 was found by luck, while attributing an unrelated inert result. This one
# was found on purpose: a sweep for primitives whose NAME is unambiguous about
# direction (`send`/`write`/`publish` vs `recv`/`read`/`fetch`) sitting under a
# boundary of the opposite direction. Across all fifteen catalogues that sweep
# returned twelve hits and eleven were correct — Phoenix's `post`/`put` name the
# HTTP METHOD of a route they receive, `send_download` sends, haskell's
# `readProcess` crosses at the process launch, `fetch` sends a request.
#
# The twelfth was real: erlang `io:read`, `io:get_line` and `io:get_chars`
# declared `logging`. They read STANDARD INPUT, and `logging` is an outbound
# SINK, so the row was wrong in both directions at once — a tainted value
# "reaching logging" fired at a call that emits nothing, AND the data actually
# read from stdin was not a source, so the most ordinary untrusted input a
# program has was invisible to the analysis.
#
# elixir inherits erlang, so one edit clears both.
# ----------------------------------------------------------------------


class TestStandardInputIsNotLogging:
    @pytest.mark.parametrize("lang", ("erlang", "elixir"))
    @pytest.mark.parametrize("name", ("read", "get_line", "get_chars"))
    def test_stdin_reads_are_receives_not_logging(
        self, lang: str, name: str,
    ) -> None:
        assert _has(lang, "ipc_recv", "io", name)
        assert not _has(lang, "logging", "io", name)

    @pytest.mark.parametrize("lang", ("erlang", "elixir"))
    @pytest.mark.parametrize("name", ("read", "get_line", "get_chars"))
    def test_which_inbound_boundary_is_declared_undecidable(
        self, lang: str, name: str,
    ) -> None:
        """THE DIRECTION IS UNAMBIGUOUS; THE BOUNDARY IS NOT, and the catalogue
        already has a word for that. ``io:read/1`` reads standard input
        (ipc_recv); ``io:read/2`` reads a given IoDevice, which may be a FILE
        (fs_read). The catalogue matches on NAME, not arity — the same reason
        ``time.localtime`` is refused elsewhere in INV-nular — so exactly one is
        true per call site and the call site cannot tell which.

        Declaring one and hiding the other would be the mistake this whole item
        is about: a boundary asserted by name with nothing checking it. Ruled
        ``call_site_undecidable``, INV-vaduk's vocabulary, the same shape as
        C's ``unistd.write``."""
        catalog = load_catalog(lang)
        boundaries = {p.boundary for p in catalog.primitives
                      if p.module == "io" and p.name == name}
        assert boundaries == {"ipc_recv", "fs_read"}
        assert (multi_boundary_reason(catalog, f"io.{name}")
                == "call_site_undecidable")

    def test_the_ruling_keeps_the_declared_debt_register_empty(self) -> None:
        """``call_site_undecidable`` is a RULING, not an open question, so this
        does not re-open the ``unruled`` register INV-nular emptied. Asserted
        because adding a multi-boundary primitive is exactly when that could
        slip back."""
        from hypergumbo_core.io_boundary import unruled_multi_boundary_primitives
        catalogs = {l: load_catalog(l) for l in ("erlang", "elixir")}
        assert list(unruled_multi_boundary_primitives(catalogs)) == []

    @pytest.mark.parametrize("lang", ("erlang", "elixir"))
    @pytest.mark.parametrize("name", ("format", "fwrite", "put_chars", "nl", "write"))
    def test_the_real_writers_stay_logging(self, lang: str, name: str) -> None:
        """CONTROL. This is a direction split, not a reclassification of the
        module: `io:format` really does write."""
        assert _has(lang, "logging", "io", name)
        assert not _has(lang, "ipc_recv", "io", name)

    def test_the_two_arms_differ_in_whether_they_are_a_taint_source(self) -> None:
        """WHY THE RULING HAS A CONSEQUENCE, and why this test does NOT claim
        that stdin became a taint source.

        `ipc_recv` is in ``AUTO_SOURCE_LABEL_MAP`` and `fs_read` is deliberately
        not — reading a file does not by itself make its contents sensitive. So
        the two arms of this undecidable primitive disagree about sourcehood,
        and which one a call site reports decides it.

        MEASURED, and it is the fs_read arm that wins: on ejabberd and emqx the
        four affected chains moved `logging` -> `fs_read`, and `ipc_recv` stayed
        at zero. ``classify_call`` yields ONE primitive, so an undecidable pair
        reports whichever row the lookup finds first (the INV-zumin row-order
        hazard, here benign). The false OUTBOUND sink is gone, which is the
        precision fix; making a stdin read an actual taint source is a separate,
        recall-side question and is NOT achieved here."""
        assert AUTO_SOURCE_LABEL_MAP.get("ipc_recv") == "untrusted_input"
        assert "fs_read" not in AUTO_SOURCE_LABEL_MAP
        assert "logging" not in AUTO_SOURCE_LABEL_MAP

    def test_the_direction_sweep_is_otherwise_clean(self) -> None:
        """A REGRESSION GUARD ON THE SWEEP ITSELF, not just on its one finding.

        Re-runs the direction check over every shipped catalogue and pins the
        surviving hits to the ones adjudicated CORRECT above. A new row whose
        name says one direction and whose boundary says the other fails here
        and has to be adjudicated rather than absorbed."""
        sendy, recvy = DIRECTION_SENDY, DIRECTION_RECVY
        inbound = {"net_recv", "ipc_recv", "db_read", "fs_read", "env_read",
                   "host_info_read", "browser_storage_read"}
        outbound = {"net_send", "ipc_send", "db_write", "fs_write", "env_write",
                    "process_send", "browser_storage_write"}
        #: A primitive that genuinely crosses BOTH ways is not a direction
        #: error by construction, and the catalogue already has a word for it:
        #: ``multi_boundary_reason`` returns "simultaneous". Exempting on that
        #: PROPERTY rather than by listing the pair means the next honestly
        #: simultaneous primitive is exempt automatically, while a new
        #: single-direction mistake still fails. objc's
        #: ``NSURLConnection.sendSynchronousRequest:`` is the live example: it
        #: sends the request and returns the response.
        #: Adjudicated and CORRECT despite the name/boundary mismatch.
        allowed = {
            # Route macros name the HTTP method of a request they RECEIVE.
            ("elixir", "net_recv", "Phoenix.Router", "post"),
            ("elixir", "net_recv", "Phoenix.Router", "put"),
            # send_download sends; the regex catches "download".
            ("elixir", "net_send", "Phoenix.Controller", "send_download"),
            # fetch() sends the request.
            ("javascript", "net_send", "fetch", "fetch"),
            ("javascript", "net_send", "window", "fetch"),
        }
        hits = set()
        for lang in ALL_LANGS:
            catalog = load_catalog(lang)
            for p in _rows(lang):
                if multi_boundary_reason(
                        catalog, p.qualified_name) == MULTI_BOUNDARY_REASON_SIMULTANEOUS:
                    continue
                key = (lang, p.boundary, p.module, p.name)
                if sendy.search(p.name) and p.boundary in inbound:
                    hits.add(key)
                if recvy.search(p.name) and p.boundary in outbound:
                    hits.add(key)
        assert hits <= allowed, (
            "new direction mismatch(es) — a primitive whose NAME says one "
            f"direction under a boundary that says the other: {sorted(hits - allowed)}"
        )


# ----------------------------------------------------------------------
# F7 — F5's HASKELL TWIN: response CONSTRUCTORS declared as receives
#
# Network.Wai shipped its response constructors under net_recv:
#
#     Network.Wai.{responseLBS, responseFile, responseBuilder, responseStream}
#
# Each BUILDS the payload a server is about to send. Building an outbound
# response receives nothing, and net_recv is an auto-derived taint SOURCE, so
# every one of them MINTED untrusted_input at a call that observes nothing.
# That is the F5 mechanism exactly, one language over, and it carries F5's
# second half too: with no Wai row under net_send, a Warp application had NO
# EGRESS SURFACE for its own response path, so a secret written into a response
# body could not be reported as leaving the process at all. The failure
# direction is REPORTS SAFE.
#
# WHY IT SURVIVED THE F5 SWEEP, WHICH IS THE PART WORTH KEEPING. That sweep
# walked CATALOGUES; this is a MECHANISM that crosses them, and the sweep's
# traversal order could not see across. The mechanical net -- the F6 direction
# sweep -- was blind for a SECOND and independent reason: its vocabulary
# carried the verb ``respond`` and not the noun ``response``, so ``responseLBS``
# matched nothing and the sweep reported clean. Both holes are closed here: the
# vocabulary gains the noun form (see DIRECTION_SENDY), and the launch family
# below is pinned across every language rather than per-catalogue.
# ----------------------------------------------------------------------


WAI_RESPONSE_CONSTRUCTORS = (
    "responseLBS", "responseFile", "responseBuilder", "responseStream")


class TestWaiResponsesAreEgress:
    @pytest.mark.parametrize("name", WAI_RESPONSE_CONSTRUCTORS)
    def test_a_response_constructor_is_a_sink_not_a_source(
        self, name: str,
    ) -> None:
        assert _has("haskell", "net_send", "Network.Wai", name)
        assert not _has("haskell", "net_recv", "Network.Wai", name)

    def test_haskell_now_has_a_web_response_egress_surface_at_all(self) -> None:
        """THE POINT OF THE CHANGE, as a property rather than a list — the same
        assertion F5 makes for Phoenix. Before this, zero Wai rows were sinks,
        so no Warp application could produce a "data left the process" finding
        through its own response path."""
        senders = [p for p in _rows("haskell")
                   if p.boundary == "net_send" and p.module.startswith("Network.Wai")]
        assert senders, (
            "Network.Wai has no egress rows; a leak into an HTTP response body "
            "reads as safe"
        )

    @pytest.mark.parametrize("mod,name", [
        ("Network.Socket", "recv"), ("Network.Socket", "recvFrom"),
        ("Network.Socket.ByteString", "recv"),
    ])
    def test_haskells_genuine_receives_are_untouched(
        self, mod: str, name: str,
    ) -> None:
        """CONTROL. This is a direction split on one module, not a
        reclassification of haskell's network surface."""
        assert _has("haskell", "net_recv", mod, name)

    def test_the_row_moved_without_stranding_its_module(self) -> None:
        """A row that MOVES cannot strand its module the way a row that is
        DELETED can (the method-starved hazard), but the two are easy to
        confuse, so the property is asserted rather than assumed: Network.Wai
        is still catalogued, and it is now catalogued as egress."""
        boundaries = {p.boundary for p in _rows("haskell")
                      if p.module == "Network.Wai"}
        assert boundaries == {"net_send"}


class TestServerLaunchIsADeferredCrossing:
    """THE FAMILY WI-joruz REFUSED TO SPLIT, RULED BY ADR-0049 AND NOW MOVED.

    This class was ``TestServerLaunchStaysAReceive``, and holding the line was
    its whole job: stop the family being split before ADR-0049's evidence bar
    was met. It did that job once, visibly -- an earlier attempt moved 21 rows,
    this class went red, and the change narrowed to the eleven SETUP rows
    INV-kanuk's statement actually named. A red ratified pin is an answer.

    THE BAR IS NOW MET AND THE ROWS HAVE MOVED. Corrected census `WI-hazop`;
    reachability measurement 0009; adjudicated findings per shape measurement
    0010; a represented-crossing proof per language, run rather than reasoned.

    WHAT THIS CLASS PINS NOW is the new state AND the two things that did NOT
    move -- because they did not move for DIFFERENT reasons, and collapsing
    them into one assertion is how the next retag takes the wrong one with it.
    """

    #: The rows measurement 0010 licensed. One per moved language, chosen as
    #: the row that language's users would name first.
    @pytest.mark.parametrize("lang,mod,name", [
        ("go", "net/http", "ListenAndServe"),
        ("go", "net/http", "Serve"),
        ("go", "github.com/gin-gonic/gin.Engine", "Run"),
        ("go", "github.com/gofiber/fiber/v2.App", "Listen"),
        ("go", "google.golang.org/grpc.Server", "Serve"),
        ("python", "http.server.HTTPServer", "serve_forever"),
        ("python", "socketserver.TCPServer", "serve_forever"),
        ("python", "asyncio", "start_server"),
        ("python", "flask.Flask", "run"),
        ("python", "uvicorn", "run"),
        ("haskell", "Network.Wai.Handler.Warp", "run"),
        ("erlang", "httpd", "start"),
        ("erlang", "ssl", "handshake"),
    ])
    def test_the_launch_family_is_disclosed_not_minted(
        self, lang: str, mod: str, name: str,
    ) -> None:
        """A launch call blocks and serves; the bytes reach the handler the
        runtime invokes, never this caller. `net_listen` mints no taint and
        SHADOWS `net_recv`, so the crossing is disclosed rather than deleted."""
        assert _has(lang, "net_listen", mod, name)
        assert not _has(lang, "net_recv", mod, name)

    @pytest.mark.parametrize("lang,mod,name", [
        ("go", "net.Listener", "Accept"),
        ("go", "net.Conn", "Read"),
        ("python", "socket.socket", "recv"),
        ("haskell", "Network.Socket", "recv"),
        ("erlang", "gen_tcp", "recv"),
    ])
    def test_the_real_receive_is_still_rowed_in_every_moved_language(
        self, lang: str, mod: str, name: str,
    ) -> None:
        """ADR-0049 ruling 3's CATALOGUE half. The run half is the
        represented-crossing probe recorded in measurement 0010: an idiomatic
        server in each of these four languages still reports a `net_recv`
        chain after the move. This asserts the rows those chains land on, so a
        later cull of the transfer surface fails HERE rather than silently
        turning the launch retag into a deletion (the WI-lunav failure)."""
        assert _has(lang, "net_recv", mod, name)

    @pytest.mark.parametrize("lang,mod,name", [
        ("c", "sys/socket", "accept"),
        ("rust", "std::net::TcpListener", "accept"),
        ("erlang", "gen_tcp", "accept"),
        ("haskell", "Network.Socket", "accept"),
        ("java", "java.net.ServerSocket", "accept"),
        ("go", "net.Listener", "Accept"),
        ("python", "socket.socket", "accept"),
    ])
    def test_accept_is_a_transfer_and_did_not_move_with_the_family(
        self, lang: str, mod: str, name: str,
    ) -> None:
        """``accept`` STAYS net_recv ON ITS OWN MERITS (ADR-0049 ruling 1).

        It returns a peer-chosen address or a connected socket TO ITS CALLER,
        so it is a transfer, not a deferred crossing -- WI-dosov drew this line
        in Haskell first, removing socket/bind/listen while keeping accept.
        Asserted separately from the rows above because the two survive for
        OPPOSITE reasons, and collapsing them is exactly how a family-wide
        retag would have taken ``accept`` with it.

        Language-facing view, so inherited rows appear under the child:
        ``_CATALOG_PARENTS`` maps cpp->c, kotlin/scala->java, elixir->erlang,
        and those four are omitted here rather than asserted twice."""
        assert _has(lang, "net_recv", mod, name)

    @pytest.mark.parametrize("mod,name", [
        ("http", "createServer"),
        ("https", "createServer"),
        ("net", "createServer"),
        ("Deno", "listen"),
    ])
    def test_javascript_did_not_move_and_the_reason_was_measured(
        self, mod: str, name: str,
    ) -> None:
        """JAVASCRIPT IS HELD BACK, and not by omission.

        Its entire REACHABLE `net_recv` surface is these rows. What would
        survive the move -- `WebSocket.onmessage`, `EventSource.onmessage` and
        their `addEventListener` peers -- is INV-misup-unreachable: a
        constructor-bound receiver (`ws = new WebSocket(url)`) never resolves,
        so the call becomes a `uses` edge and matches no row. Moving these four
        would relocate JavaScript's inbound-network representation to NOTHING,
        which ADR-0049 ruling 3 forbids.

        That is `F2_EXEMPT`'s documented reasoning, and it is not taken on
        authority here: an idiomatic JS server probe run for measurement 0010
        reported exactly two `net_recv` chains, both `createServer`, and zero
        from the WebSocket handler. When INV-misup closes, this test is what
        asks whether the exemption still has a reason."""
        assert _has("javascript", "net_recv", mod, name)

    @pytest.mark.parametrize("name", ("get", "post", "resources", "scope"))
    def test_the_register_shape_did_not_move_either(self, name: str) -> None:
        """PHOENIX IS HELD BACK FOR A DIFFERENT REASON, WHICH IS WHY IT IS A
        SEPARATE TEST: not unreachability, but an unmet evidence bar.

        ADR-0049 step 3's bar is ADJUDICATED FINDINGS PER SHAPE, and
        measurement 0010 adjudicated **none** for the REGISTER shape --
        `Phoenix.Router` appears in one repository of seventeen and produced no
        violating situation. Absence of findings is not evidence of
        harmlessness. These rows move when a cohort supplies the findings, not
        because they resemble the rows that did."""
        assert _has("elixir", "net_recv", "Phoenix.Router", name)


def test_the_sweep_vocabulary_covers_the_noun_form() -> None:
    """A GATE ON THE INSTRUMENT, not on the catalogue.

    WI-joruz's row was missed by a sweep that RAN and reported clean, because
    its vocabulary was incomplete. Widening it fixes today's blind spot; this
    pins the widening so it cannot be narrowed back into blindness by someone
    tidying the regex. The verb was present and the noun was not — assert both.
    """
    for spelling in ("respond", "responseLBS", "resp", "render"):
        assert DIRECTION_SENDY.search(spelling), spelling
    assert not DIRECTION_SENDY.search("readFile")


def test_f2_row_exemptions_still_describe_a_live_row() -> None:
    """THE GATE ON THE ROW-LEVEL EXEMPTION LIST (INV-kanuk).

    A row exemption is a claim about a SPECIFIC shipped row: "this one is a
    false positive of the predicate". If the row is retagged, renamed or
    dropped, the claim is about nothing and the entry is indistinguishable from
    a forgotten line. Asserting the row is still there AND still ``net_recv``
    makes the entry self-retiring -- and it retired: the launch retag moved
    fiber's ``App.Listen`` to ``net_listen`` and this gate is what forced the
    entry out in the same change.

    A PLAIN LOOP, NOT A PARAMETRIZE, because the dict is empty today and an
    empty parametrize is collected as a skip -- a green tick over a gate that
    ran nothing, which is the shape this file exists to refuse.
    """
    for lang, rows in sorted(F2_EXEMPT_ROWS.items()):
        for module, name in sorted(rows):
            matches = [p for p in _rows(lang)
                       if (p.module, p.name) == (module, name)]
            assert matches, (
                f"{lang}: F2_EXEMPT_ROWS excuses {module}.{name}, which no "
                f"longer exists in the catalogue -- delete the entry"
            )
            assert all(p.boundary == "net_recv" for p in matches), (
                f"{lang}: {module}.{name} is no longer net_recv, so the "
                f"exemption excuses nothing -- delete the entry"
            )


def test_the_retired_row_exemption_really_did_retire() -> None:
    """The other half of the life cycle, asserted POSITIVELY.

    An emptied dict is indistinguishable from a dict someone emptied to make a
    red test green. This says what actually happened: fiber's ``App.Listen``
    is ``net_listen`` now, so the F2 predicate no longer flags it and there is
    nothing left to excuse. If the retag is ever reverted without restoring the
    exemption, the F2 gate goes red on go and this test tells the next reader
    why."""
    assert _has("go", "net_listen", "github.com/gofiber/fiber/v2.App", "Listen")
    assert not _has("go", "net_recv", "github.com/gofiber/fiber/v2.App", "Listen")
