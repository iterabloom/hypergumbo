# SPDX-License-Identifier: AGPL-3.0-or-later
"""The adjacent membership defects audit-findings 0021 filed and did not move.

``docs/audits/0021-logging-family.md`` Step 5 found, while inventorying the
``logging`` / ``ipc_send`` seam, rows whose boundary was chosen by what the
call is ABOUT rather than what it DOES at the call site. The axiom they are
held to is ADR-0050 §1 (ADR-0049 ruling 1): *a boundary value names what data
crosses the process boundary at this call site, in which direction -- not
what the program is thereby arranged to do later.*

Every boundary here auto-derives taint (``taint.AUTO_SOURCE_LABEL_MAP`` /
``AUTO_SINK_ZONE_MAP``), so each class pins three things, not one:

1. the ROW: what the shipped catalogue declares for the name;
2. the DERIVED taint half: which source label or sink zone the row mints, so
   "a pure relabel" and "a sink disappears" are tested claims rather than
   prose in the commit message;
3. REACH: the shipped ``io-boundaries`` command run on a one-file program
   that calls the name, with a CONTROL in the same file that must still be
   reported. A row nothing reaches can be argued about at length while
   minting nothing (the ``flask.Flask.run`` lesson), and a fixture that
   reaches nothing makes every "it is gone" assertion vacuously true -- so
   each fixture asserts its control first.

A DELETED row is not silent. In a module whose I/O surface the catalogue has
ENUMERATED (``module_completeness``), an unrowed call is an examined
negative; in any other named external module it is disclosed as
``external_potential``. Each deletion below says which, because "the chain
went away" and "the call is now declared pure" are different claims.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

import hypergumbo_core.io_boundary as iob
from hypergumbo_core.cli import main
from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.taint import _derive_auto_imports_from_io_primitives

_CATALOG_DIR = Path(iob.__file__).parent / "io_primitives"


def _rows(language: str, module: str, name: str) -> set[str]:
    """Every boundary the shipped catalogue (overlays included) declares."""
    return {
        p.boundary for p in load_catalog(language).primitives
        if p.module == module and p.name == name
    }


@pytest.fixture(scope="module")
def derived() -> tuple[dict, dict]:
    """``(sources, sinks)`` keyed ``language -> (module, name) -> {label|zone}``."""
    sources, sinks, _amb = _derive_auto_imports_from_io_primitives(_CATALOG_DIR)
    src: dict = {}
    snk: dict = {}
    for lang, entries in sources.items():
        for s in entries:
            src.setdefault(lang, {}).setdefault((s.module, s.name), set()).add(s.taint_label)
    for lang, entries in sinks.items():
        for s in entries:
            snk.setdefault(lang, {}).setdefault((s.module, s.name), set()).add(s.zone)
    return src, snk


def _chains(root: Path, name: str, text: str) -> dict[str, set[str]]:
    """Run the shipped ``io-boundaries`` on a one-file repository."""
    repo = root / "repo"
    repo.mkdir()
    (repo / name).write_text(text)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("XDG_CACHE_HOME", str(root / "cache"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = main(["io-boundaries", str(repo), "--format", "json", "--include-tests"])
    assert rc == 0
    report = json.loads(buf.getvalue())
    return {b: {c["primitive"] for c in v["chains"]} for b, v in report["boundaries"].items()}


def _every_primitive(chains: dict[str, set[str]]) -> set[str]:
    return set().union(*chains.values()) if chains else set()


# ---------------------------------------------------------------------------
# WI-ragaz: logging rows that write nothing at the call.
# ---------------------------------------------------------------------------

_PY_LOGGING = '''import logging


def setup() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.StreamHandler()
    logging.FileHandler("app.log")
    logging.info("ready")
'''

_GO_SLOG = '''package main

import (
	"log/slog"
	"os"
)

func main() {
	h := slog.NewTextHandler(os.Stderr, nil)
	j := slog.NewJSONHandler(os.Stdout, nil)
	_, _ = h, j
	slog.Info("ready")
}
'''

_EX_LOGGER = '''defmodule App do
  require Logger

  def setup(user) do
    Logger.configure(level: :info)
    Logger.metadata(user: user)
    Logger.info("ready")
  end
end
'''


class TestWiRagazConfiguringAFacilityWritesNothing:
    """Constructing or configuring a logging facility writes nothing at the
    call; the write happens at a later call, which is rowed (the erlang
    ``io_lib`` precedent: adjacency is not a crossing, and the represented
    later call is what licenses the removal under ADR-0049 ruling 3)."""

    @pytest.mark.parametrize(("language", "module", "name"), [
        ("python", "logging", "basicConfig"),
        ("python", "logging", "StreamHandler"),
        ("go", "log/slog", "NewTextHandler"),
        ("go", "log/slog", "NewJSONHandler"),
        ("elixir", "Logger", "metadata"),
        ("elixir", "Logger", "configure"),
    ])
    def test_the_row_is_gone(self, language: str, module: str, name: str) -> None:
        assert _rows(language, module, name) == set()

    @pytest.mark.parametrize(("language", "module", "name", "boundary"), [
        ("python", "logging", "info", "logging"),
        ("python", "logging", "FileHandler", "fs_write"),
        ("go", "log/slog", "Info", "logging"),
        ("elixir", "Logger", "info", "logging"),
    ])
    def test_control_the_writing_siblings_stay(
        self, language: str, module: str, name: str, boundary: str,
    ) -> None:
        assert _rows(language, module, name) == {boundary}

    @pytest.mark.parametrize(("language", "module", "name"), [
        ("python", "logging", "basicConfig"),
        ("python", "logging", "StreamHandler"),
        ("go", "log/slog", "NewTextHandler"),
        ("go", "log/slog", "NewJSONHandler"),
        ("elixir", "Logger", "metadata"),
        ("elixir", "Logger", "configure"),
    ])
    def test_no_logging_sink_is_derived(
        self, derived, language: str, module: str, name: str,
    ) -> None:
        _src, sinks = derived
        assert (module, name) not in sinks[language]

    def test_python_logging_stays_an_enumerated_module(self) -> None:
        """So an unrowed ``basicConfig`` is an EXAMINED negative, not a hole."""
        assert load_catalog("python").module_io_is_enumerated("logging")

    def test_python_reach(self, tmp_path: Path) -> None:
        chains = _chains(tmp_path, "setup.py", _PY_LOGGING)
        assert chains["logging"] == {"logging.info"}  # control: reached
        assert chains["fs_write"] == {"logging.FileHandler"}  # control: reached
        assert not {"logging.basicConfig", "logging.StreamHandler"} & _every_primitive(chains)

    def test_go_reach(self, tmp_path: Path) -> None:
        chains = _chains(tmp_path, "main.go", _GO_SLOG)
        assert "log/slog.Info" in chains["logging"]  # control: reached
        assert not {"log/slog.NewTextHandler", "log/slog.NewJSONHandler"} & chains["logging"]
        # log/slog is not an enumerated module: the call is disclosed, not dropped.
        assert {"log/slog.NewTextHandler", "log/slog.NewJSONHandler"} <= chains[
            "external_potential"]

    def test_elixir_reach(self, tmp_path: Path) -> None:
        chains = _chains(tmp_path, "app.ex", _EX_LOGGER)
        assert chains["logging"] == {"Logger.info"}  # control: reached
        assert {"Logger.metadata", "Logger.configure"} <= chains["external_potential"]


# ---------------------------------------------------------------------------
# WI-povom: in-process pub/sub is not inter-process communication.
# ---------------------------------------------------------------------------

_OBJC_NOTIFY = '''#import <Foundation/Foundation.h>

@interface Poster : NSObject
- (void)go:(NSString *)s;
@end

@implementation Poster
- (void)go:(NSString *)s {
    NSNotificationCenter *c = [NSNotificationCenter defaultCenter];
    [c postNotificationName:s object:nil];
    [c postNotificationName:s object:nil userInfo:nil];
    [c postNotification:nil];
    NSDistributedNotificationCenter *d = [NSDistributedNotificationCenter defaultCenter];
    [d postNotificationName:s object:nil userInfo:nil deliverImmediately:YES];
}
@end
'''

_SWIFT_NOTIFY = '''import Foundation

func go(s: String, c: NotificationCenter, d: DistributedNotificationCenter) {
    c.post(name: Notification.Name(s), object: nil)
    d.postNotificationName(NSNotification.Name(s), object: nil)
}
'''

_IN_PROCESS_POSTS = [
    ("objc", "NSNotificationCenter", "postNotificationName:object:"),
    ("objc", "NSNotificationCenter", "postNotificationName:object:userInfo:"),
    ("objc", "NSNotificationCenter", "postNotification:"),
    ("swift", "NotificationCenter", "post"),
]


class TestWiPovomInProcessPubSubIsNotIpc:
    """``ipc_send`` is data sent to ANOTHER process over a channel the program
    set up (audit-findings 0021). A notification posted to the default
    ``NSNotificationCenter`` / ``NotificationCenter`` is delivered to observers
    in the SAME process; no process boundary is crossed, so there is no
    data-crossing value it could take and the row is deleted rather than
    moved. The cross-process siblings stay ``ipc_send``."""

    @pytest.mark.parametrize(("language", "module", "name"), _IN_PROCESS_POSTS)
    def test_the_row_is_gone(self, language: str, module: str, name: str) -> None:
        assert _rows(language, module, name) == set()

    @pytest.mark.parametrize(("language", "module", "name"), _IN_PROCESS_POSTS)
    def test_no_ipc_sink_is_derived(
        self, derived, language: str, module: str, name: str,
    ) -> None:
        _src, sinks = derived
        assert (module, name) not in sinks[language]

    @pytest.mark.parametrize(("language", "module", "name"), [
        ("objc", "NSDistributedNotificationCenter",
         "postNotificationName:object:userInfo:deliverImmediately:"),
        ("swift", "DistributedNotificationCenter", "postNotificationName"),
    ])
    def test_control_the_cross_process_center_stays_ipc_send(
        self, derived, language: str, module: str, name: str,
    ) -> None:
        assert _rows(language, module, name) == {"ipc_send"}
        _src, sinks = derived
        assert sinks[language][(module, name)] == {"ipc"}

    def test_objc_reach(self, tmp_path: Path) -> None:
        chains = _chains(tmp_path, "Poster.m", _OBJC_NOTIFY)
        assert chains["ipc_send"] == {  # control: reached
            "NSDistributedNotificationCenter.postNotificationName:object:userInfo:deliverImmediately:",
        }
        assert {
            "NSNotificationCenter.postNotificationName:object:",
            "NSNotificationCenter.postNotificationName:object:userInfo:",
            "NSNotificationCenter.postNotification:",
        } <= chains["external_potential"]

    def test_swift_reach(self, tmp_path: Path) -> None:
        chains = _chains(tmp_path, "Poster.swift", _SWIFT_NOTIFY)
        assert chains["ipc_send"] == {"DistributedNotificationCenter.postNotificationName"}
        assert "NotificationCenter.post" in chains["external_potential"]


# ---------------------------------------------------------------------------
# WI-nuhor: an elixir BEAM message send is erlang's process_send.
# ---------------------------------------------------------------------------

_EX_SENDS = '''defmodule Sender do
  def go(pid, msg) do
    GenServer.call(pid, msg)
    GenServer.cast(pid, msg)
    GenServer.abcast(:srv, msg)
    GenServer.multi_call(:srv, msg)
    Process.send(pid, msg, [])
    Process.send_after(pid, msg, 100)
    :gen_server.call(pid, msg)
  end
end
'''

_BEAM_SENDS = [
    ("GenServer", "call"), ("GenServer", "cast"), ("GenServer", "abcast"),
    ("GenServer", "multi_call"), ("Process", "send"), ("Process", "send_after"),
]


class TestWiNuhorBeamSendsAreProcessSend:
    """erlang.yaml rows ``erlang:send`` / ``!`` and ``gen_server:call`` /
    ``cast`` / ``abcast`` / ``multi_call`` ``process_send``, whose registry
    description is exactly this case ("the far side is a peer inside the same
    runtime, not an OS pipe"). elixir.yaml rowed the elixir spellings of the
    same sends ``ipc_send``. A PURE RELABEL: both boundaries map to the ``ipc``
    taint zone, so the derived sink is pinned unchanged."""

    @pytest.mark.parametrize(("module", "name"), _BEAM_SENDS)
    def test_the_row_is_process_send(self, module: str, name: str) -> None:
        assert _rows("elixir", module, name) == {"process_send"}

    @pytest.mark.parametrize(("module", "name"), _BEAM_SENDS)
    def test_the_taint_sink_is_unchanged(self, derived, module: str, name: str) -> None:
        _src, sinks = derived
        assert sinks["elixir"][(module, name)] == {"ipc"}

    @pytest.mark.parametrize("name", ["call", "cast", "abcast", "multi_call"])
    def test_control_erlang_gen_server_is_the_precedent(self, name: str) -> None:
        assert _rows("erlang", "gen_server", name) == {"process_send"}

    def test_reach(self, tmp_path: Path) -> None:
        chains = _chains(tmp_path, "sender.ex", _EX_SENDS)
        assert "gen_server.call" in chains["process_send"]  # control: reached
        assert {f"{m}.{n}" for m, n in _BEAM_SENDS} <= chains["process_send"]
        assert "ipc_send" not in chains


# ---------------------------------------------------------------------------
# WI-busam: an elixir Task spawn is erlang's spawn, process_send.
# ---------------------------------------------------------------------------

_EX_TASKS = '''defmodule Spawner do
  def go(sup, xs, v) do
    Task.async(fn -> v end)
    Task.async_stream(xs, fn x -> x end)
    Task.start(fn -> v end)
    Task.start_link(fn -> v end)
    Task.Supervisor.start_child(sup, fn -> v end)
    :erlang.spawn(fn -> v end)
  end
end
'''

_TASK_SPAWNS = [
    ("Task", "async"), ("Task", "async_stream"), ("Task", "start"),
    ("Task", "start_link"), ("Task.Supervisor", "start_child"),
]


class TestWiBusamTaskSpawnsAreProcessSend:
    """A Task spawn starts a BEAM process and copies the closure -- with every
    value it captured -- into it. No OS process boundary is crossed, so it is
    not ``ipc_send``; the precedent is erlang.yaml, which rows ``spawn`` /
    ``spawn_link`` / ``proc_lib:spawn`` / ``supervisor:start_child``
    ``process_send``. A pure relabel (same ``ipc`` taint zone).

    ``Task.Supervisor.start_child`` was spelled as a FUNCTION named
    ``Supervisor.start_child`` on module ``Task``, which no call site can
    produce: the call reported ``external_potential``. Re-homed to its module.
    ``Task.async_stream`` is lazy -- the tasks start when the stream is
    enumerated -- and is kept with its siblings: the spawn it arranges has no
    other row, so deleting it would drop the crossing rather than relocate it
    (ADR-0049 ruling 3)."""

    @pytest.mark.parametrize(("module", "name"), _TASK_SPAWNS)
    def test_the_row_is_process_send(self, module: str, name: str) -> None:
        assert _rows("elixir", module, name) == {"process_send"}

    @pytest.mark.parametrize(("module", "name"), _TASK_SPAWNS)
    def test_the_taint_sink_is_unchanged(self, derived, module: str, name: str) -> None:
        _src, sinks = derived
        assert sinks["elixir"][(module, name)] == {"ipc"}

    def test_the_unmatchable_spelling_is_gone(self) -> None:
        assert _rows("elixir", "Task", "Supervisor.start_child") == set()

    @pytest.mark.parametrize("name", ["spawn", "spawn_link"])
    def test_control_erlang_spawn_is_the_precedent(self, name: str) -> None:
        assert _rows("erlang", "erlang", name) == {"process_send"}

    def test_reach(self, tmp_path: Path) -> None:
        chains = _chains(tmp_path, "spawner.ex", _EX_TASKS)
        assert "erlang.spawn" in chains["process_send"]  # control: reached
        assert {f"{m}.{n}" for m, n in _TASK_SPAWNS} <= chains["process_send"]
        assert "ipc_send" not in chains


# ---------------------------------------------------------------------------
# WI-logoz: an Oban job enqueue is a database insert (community tier).
# ---------------------------------------------------------------------------

_EX_OBAN = '''defmodule Jobs do
  def go(changeset, changesets) do
    Oban.insert(changeset)
    Oban.insert!(changeset)
    Oban.insert_all(changesets)
    Ecto.Repo.insert(changeset)
  end
end
'''

_OBAN = [("Oban", "insert"), ("Oban", "insert!"), ("Oban", "insert_all")]


class TestWiLogozObanInsertIsADatabaseWrite:
    """Oban persists a job as a row in its ``oban_jobs`` table through Ecto;
    the crossing at ``Oban.insert*`` is that insert, which the axis spells
    ``db_write`` -- the boundary the same overlay gives ``Ecto.Repo.insert``.
    The rows are COMMUNITY tier (the file's ``provenance:`` line, ADR-0061):
    they may add findings and never make a verdict cleaner, and moving the
    zone keeps them on the adding side -- an ``ipc`` sink disappears and a
    ``database`` sink appears at the same calls."""

    @pytest.mark.parametrize(("module", "name"), _OBAN)
    def test_the_row_is_db_write(self, module: str, name: str) -> None:
        assert _rows("elixir", module, name) == {"db_write"}

    @pytest.mark.parametrize(("module", "name"), _OBAN)
    def test_the_sink_moves_from_ipc_to_database(
        self, derived, module: str, name: str,
    ) -> None:
        _src, sinks = derived
        assert sinks["elixir"][(module, name)] == {"database"}

    @pytest.mark.parametrize(("module", "name"), _OBAN)
    def test_the_row_stays_community_tier_and_unvouched(self, module: str, name: str) -> None:
        (prim,) = [p for p in load_catalog("elixir").primitives
                   if p.module == module and p.name == name]
        assert prim.unvouched is True

    def test_control_ecto_repo_insert_is_the_same_shape(self) -> None:
        assert _rows("elixir", "Ecto.Repo", "insert") == {"db_write"}

    def test_reach(self, tmp_path: Path) -> None:
        chains = _chains(tmp_path, "jobs.ex", _EX_OBAN)
        assert "Ecto.Repo.insert" in chains["db_write"]  # control: reached
        assert {f"{m}.{n}" for m, n in _OBAN} <= chains["db_write"]
        assert "ipc_send" not in chains


# ---------------------------------------------------------------------------
# WI-rivur: rows chosen by what the call is ABOUT, not what it DOES.
# (Its python os.wait* row is NOT here: it waits on WI-lanos.)
# ---------------------------------------------------------------------------

_OBJC_OBSERVE = '''#import <Foundation/Foundation.h>

@interface Watcher : NSObject
- (void)go:(NSString *)s;
@end

@implementation Watcher
- (void)go:(NSString *)s {
    NSNotificationCenter *c = [NSNotificationCenter defaultCenter];
    [c addObserver:self selector:@selector(go:) name:s object:nil];
    [c removeObserver:self];
    [c removeObserver:self name:s object:nil];
}
@end
'''

_SWIFT_OBSERVE = '''import Foundation

func go(c: NotificationCenter, o: Any) {
    c.addObserver(o, selector: #selector(NSObject.description), name: nil, object: nil)
    c.removeObserver(o)
}
'''

_PY_BACKUP = '''import sqlite3


def copy(src: sqlite3.Connection, dst: sqlite3.Connection) -> None:
    src.backup(dst)
    src.commit()
'''

_ERL_INFO = '''-module(tabs).
-export([go/1]).

go(K) ->
    ets:info(tab),
    ets:info(tab, size),
    mnesia:table_info(tab, size),
    ets:lookup(tab, K).
'''

_UNREGISTER = [
    ("objc", "NSNotificationCenter", "removeObserver:"),
    ("objc", "NSNotificationCenter", "removeObserver:name:object:"),
    ("swift", "NotificationCenter", "removeObserver"),
]

_TABLE_METADATA = [
    ("erlang", "ets", "info"), ("erlang", "mnesia", "table_info"),
    ("elixir", "ets", "info"), ("elixir", "mnesia", "table_info"),
]


class TestWiRivurRemoveObserverReceivesNothing:
    """Un-registering an observer receives nothing and crosses nothing: not
    even a deferred crossing, since an arrival is being CANCELLED, not
    arranged. It was ``ipc_recv``, an auto-derived ``untrusted_input`` source.
    Deleted, so nothing is relocated and ADR-0049 ruling 3 is not engaged.
    swift's ``NotificationCenter.removeObserver`` is the same API and the same
    row, swept with it; ``addObserver`` (route-registration shape, ADR-0049)
    is NOT touched here."""

    @pytest.mark.parametrize(("language", "module", "name"), _UNREGISTER)
    def test_the_row_is_gone(self, language: str, module: str, name: str) -> None:
        assert _rows(language, module, name) == set()

    @pytest.mark.parametrize(("language", "module", "name"), _UNREGISTER)
    def test_no_source_is_derived(
        self, derived, language: str, module: str, name: str,
    ) -> None:
        sources, _snk = derived
        assert (module, name) not in sources[language]

    @pytest.mark.parametrize(("language", "module", "name"), [
        ("objc", "NSNotificationCenter", "addObserver:selector:name:object:"),
        ("swift", "NotificationCenter", "addObserver"),
    ])
    def test_control_add_observer_is_untouched(
        self, language: str, module: str, name: str,
    ) -> None:
        assert _rows(language, module, name) == {"ipc_recv"}

    def test_objc_reach(self, tmp_path: Path) -> None:
        chains = _chains(tmp_path, "Watcher.m", _OBJC_OBSERVE)
        assert chains["ipc_recv"] == {  # control: reached
            "NSNotificationCenter.addObserver:selector:name:object:",
        }
        assert {
            "NSNotificationCenter.removeObserver:",
            "NSNotificationCenter.removeObserver:name:object:",
        } <= chains["external_potential"]

    def test_swift_reach(self, tmp_path: Path) -> None:
        chains = _chains(tmp_path, "Watcher.swift", _SWIFT_OBSERVE)
        assert chains["ipc_recv"] == {"NotificationCenter.addObserver"}
        assert "NotificationCenter.removeObserver" in chains["external_potential"]


class TestWiRivurSqliteBackupWritesTheTarget:
    """``Connection.backup(target)`` reads this database and WRITES it into
    ``target``, returning ``None``. It was ``db_read``: an ``untrusted_input``
    source minted at a call that hands its caller nothing (ADR-0049 ruling 1).
    The crossing it performs is the write, ``db_write``; the sink that derives
    sees the call's arguments (the target connection, ``pages``, ``name``)."""

    def test_the_row_is_db_write(self) -> None:
        assert _rows("python", "sqlite3.Connection", "backup") == {"db_write"}

    def test_the_source_is_gone_and_a_sink_appears(self, derived) -> None:
        sources, sinks = derived
        assert ("sqlite3.Connection", "backup") not in sources["python"]
        assert sinks["python"][("sqlite3.Connection", "backup")] == {"database"}

    def test_the_class_stays_an_enumerated_module(self) -> None:
        assert load_catalog("python").module_io_is_enumerated("sqlite3.Connection")

    def test_reach(self, tmp_path: Path) -> None:
        chains = _chains(tmp_path, "copy.py", _PY_BACKUP)
        assert chains["db_write"] == {  # control (commit) reached
            "sqlite3.Connection.backup", "sqlite3.Connection.commit",
        }
        assert "db_read" not in chains


class TestWiRivurTableMetadataIsNotStoredRows:
    """``ets:info`` and ``mnesia:table_info`` return a table's SHAPE (size,
    memory, type, owner), computed by the runtime -- not rows anybody stored.
    As ``db_read`` they minted ``untrusted_input`` from a number the runtime
    computed. Deleted: there is no crossing to relocate. elixir inherits
    erlang's rows, so ``:ets.info`` there goes with them."""

    @pytest.mark.parametrize(("language", "module", "name"), _TABLE_METADATA)
    def test_the_row_is_gone(self, language: str, module: str, name: str) -> None:
        assert _rows(language, module, name) == set()

    @pytest.mark.parametrize(("language", "module", "name"), _TABLE_METADATA)
    def test_no_source_is_derived(
        self, derived, language: str, module: str, name: str,
    ) -> None:
        sources, _snk = derived
        assert (module, name) not in sources[language]

    @pytest.mark.parametrize(("module", "name"), [
        ("ets", "lookup"), ("ets", "foldl"), ("mnesia", "read"),
    ])
    def test_control_row_reads_stay_db_read(self, module: str, name: str) -> None:
        assert _rows("erlang", module, name) == {"db_read"}

    def test_reach(self, tmp_path: Path) -> None:
        chains = _chains(tmp_path, "tabs.erl", _ERL_INFO)
        assert chains["db_read"] == {"ets.lookup"}  # control: reached
        assert {"ets.info", "mnesia.table_info"} <= chains["external_potential"]
