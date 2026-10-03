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
