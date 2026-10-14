# SPDX-License-Identifier: AGPL-3.0-or-later
"""Swift ``Process`` and ObjC ``NSTask``: row the launch, not the lifecycle.

Two defects in the same two rows, fixed together because fixing either alone
leaves the rows worse than they were.

INV-babiz. Both rows put ``terminate`` and ``interrupt`` under ``subprocess``.
Neither spawns anything: each signals a child that is ALREADY running
(swift-corelibs-foundation's ``terminate`` is ``kill(processIdentifier,
SIGTERM)``). python.yaml and rust.yaml row that operation under ``ipc_send``
by the dated WI-dupok ruling ("signals the child"). ``suspend`` / ``resume``
are SIGSTOP / SIGCONT, the same operation. ``waitUntilExit`` returns nothing:
it is python.yaml's ``multiprocessing.Process.join``, which is disclosed, not
rowed (rust's ``Child.wait`` IS rowed ``ipc_recv`` because it returns the exit
status).

INV-vamif. With those members moved out, the Swift row would catalogue no
launch at all, because it never did. It listed ``launchPath`` (a property, so
never a call site) and no ``run`` / ``launch``, so ``try p.run()`` produced no
chain while ``p.waitUntilExit()`` was the one subprocess chain reported. ObjC
had ``launch`` but not the class-method launcher
``launchedTaskWithLaunchPath:arguments:``.

Both classes gain a completeness grant, so an unrowed member (``waitUntilExit``)
is an examined negative rather than an unexamined module.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.io_boundary import load_catalog


def _boundaries(catalog, module: str, name: str) -> set[str]:
    return {p.boundary for p in catalog.primitives if p.module == module and p.name == name}


_ROWS = {
    # language, owner: {member: expected boundaries}
    ("swift", "Process"): {
        "run": {"subprocess"},
        "launch": {"subprocess"},
        "launchedProcess": {"subprocess"},
        "terminate": {"ipc_send"},
        "interrupt": {"ipc_send"},
        "suspend": {"ipc_send"},
        "resume": {"ipc_send"},
        "waitUntilExit": set(),
        "launchPath": set(),
    },
    ("objc", "NSTask"): {
        "launch": {"subprocess"},
        "launchAndReturnError:": {"subprocess"},
        "launchedTaskWithLaunchPath:arguments:": {"subprocess"},
        "launchedTaskWithExecutableURL:arguments:error:terminationHandler:": {"subprocess"},
        "terminate": {"ipc_send"},
        "interrupt": {"ipc_send"},
        "suspend": {"ipc_send"},
        "resume": {"ipc_send"},
        "waitUntilExit": set(),
    },
}


@pytest.mark.parametrize(("language", "owner", "member", "expected"), [
    (lang, owner, member, expected)
    for (lang, owner), members in _ROWS.items()
    for member, expected in members.items()
])
def test_each_member_is_rowed_by_what_it_does(
    language: str, owner: str, member: str, expected: set[str],
) -> None:
    assert _boundaries(load_catalog(language), owner, member) == expected


@pytest.mark.parametrize(("language", "owner"), list(_ROWS))
def test_the_class_is_an_examined_module(language: str, owner: str) -> None:
    assert load_catalog(language).module_io_is_enumerated(owner)


def _chains(repo: Path, cache: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, set[str]]:
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        rc = main(["io-boundaries", str(repo), "--format", "json", "--include-tests"])
    assert rc == 0
    report = json.loads(buf.getvalue())
    return {b: {c["primitive"] for c in v["chains"]} for b, v in report["boundaries"].items()}


_SWIFT = '''import Foundation

func runTool(u: URL) throws {
    let p = Process()
    p.executableURL = u
    try p.run()
    p.waitUntilExit()
}

func legacy(path: String) {
    let t = Process()
    t.launchPath = path
    t.launch()
}

func classForms(u: URL, path: String) throws {
    _ = try Process.run(u, arguments: ["-x"])
    _ = Process.launchedProcess(launchPath: path, arguments: [])
}

func stopTool(p: Process) {
    p.terminate()
    p.interrupt()
}
'''

_OBJC = '''#import <Foundation/Foundation.h>

@interface Runner : NSObject
- (void)go:(NSString *)path;
@end

@implementation Runner
- (void)go:(NSString *)path {
    [NSTask launchedTaskWithLaunchPath:path arguments:@[]];
    NSTask *task = [[NSTask alloc] init];
    [task launch];
    [task waitUntilExit];
    [task terminate];
    [task interrupt];
}
@end
'''


def test_swift_reports_every_launch_and_signals_as_sends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Runner.swift").write_text(_SWIFT)
    chains = _chains(repo, tmp_path / "cache", monkeypatch)
    assert chains["subprocess"] == {
        "Process.run", "Process.launch", "Process.launchedProcess",
    }
    assert {"Process.terminate", "Process.interrupt"} <= chains["ipc_send"]


def test_objc_reports_the_class_method_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Runner.m").write_text(_OBJC)
    chains = _chains(repo, tmp_path / "cache", monkeypatch)
    assert chains["subprocess"] == {
        "NSTask.launchedTaskWithLaunchPath:arguments:", "NSTask.launch",
    }
    assert {"NSTask.terminate", "NSTask.interrupt"} <= chains["ipc_send"]


_LAUNCH_ONLY = '''import Foundation

func main(u: URL) throws {
    _ = try Process.run(u, arguments: [])
}
'''

_CLAIMS = '''claims:
  - id: NO-SUBPROCESS
    text: This program never launches another program.
    constraint:
      boundary: subprocess
      must_not_exist: true
'''


def test_a_swift_program_that_launches_fails_the_no_subprocess_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At the finding level: this returned a clean verdict (INV-vamif)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.swift").write_text(_LAUNCH_ONLY)
    (tmp_path / "claims.yaml").write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(tmp_path / "claims.yaml"),
              "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    assert verdict["verdict"] == "violated", verdict["details"]
