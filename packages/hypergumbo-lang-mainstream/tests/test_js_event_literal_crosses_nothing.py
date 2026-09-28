# SPDX-License-Identifier: AGPL-3.0-or-later
"""An event listener for an event that carries no outside data crosses nothing (WI-punar).

javascript.yaml rows ``process.on`` as ``ipc_recv`` for
``process.on('message', ...)``, the IPC channel from a parent. The row matches
the NAME, so every ``process.on`` was an IPC receive, and ``ipc_recv`` mints
``untrusted_input``. On the ~/ALL_REPOS JS/TS corpus 1,230 ``process.on`` /
``once`` sites name a literal event, and 90 (7.3%) are the IPC channel:
``exit``, ``uncaughtException``, ``SIGINT``, ``unhandledRejection`` and the
rest are lifecycle hooks whose payload never came from outside the process. A
file with only crash handlers failed a must_not_exist ipc_recv claim.

The fix is a stamp, not a new catalogue field. ``io_target_kind: in_memory``
already means "this call site crosses nothing": the tagger skips the chain
while the call still counts as examined (no withheld verdict), and taint
refuses to mint a source there. The analyzer stamps it when the listener's
event is a string literal that carries nothing, and stamps every OTHER site
of the same listener too (the crossing kind, or ``unresolved`` for an event it
cannot read), because the per-site collapse keeps only the values sites have.
The same holds for the browser rows the catalogue notes already reason about:
a WebSocket's ``open`` and ``error`` events carry no data.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main


def _run(argv: list[str], cache: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(argv)
    return buf.getvalue()


def _repo(tmp_path: Path, body: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.js").write_text(body + "\nmodule.exports = { main };\n")
    return repo


def _chains(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> dict[str, set[str]]:
    out = _run(["io-boundaries", str(_repo(tmp_path, body)), "--format", "json"],
               tmp_path / "cache", monkeypatch)
    return {b: {c["primitive"] for c in v["chains"]}
            for b, v in json.loads(out)["boundaries"].items()}


_CRASH = '''function main() {
  process.on("uncaughtException", (err) => { console.error(err); process.exit(1); });
  process.on("SIGTERM", () => process.exit(0));
}
'''


def test_crash_and_signal_handlers_are_no_ipc_receive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    chains = _chains(tmp_path, monkeypatch, _CRASH)
    assert "process.on" not in chains.get("ipc_recv", set())
    assert "console.error" in chains.get("logging", set())  # reach


def test_a_file_with_only_crash_handlers_passes_the_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The finding the item was filed on; it must not be withheld either."""
    repo = _repo(tmp_path, _CRASH)
    claims = tmp_path / "claims.yaml"
    claims.write_text("claims:\n  - id: C\n    text: t\n    constraint:\n"
                      "      boundary: ipc_recv\n      must_not_exist: true\n")
    out = _run(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"],
               tmp_path / "cache", monkeypatch)
    (verdict,) = json.loads(out)["verdicts"]
    assert verdict["verdict"] in ("confirmed", "confirmed_with_caveats"), verdict["details"]


@pytest.mark.parametrize("event", ['"message"', "evt"])
def test_the_ipc_channel_and_an_unreadable_event_still_receive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event: str,
) -> None:
    body = f"function main(evt) {{\n  process.on({event}, (m) => m);\n}}\n"
    assert "process.on" in _chains(tmp_path, monkeypatch, body).get("ipc_recv", set())


_TAINT = '''const child_process = require("child_process");

function main() {{
  process.on("{event}", (payload) => {{
    child_process.execSync(String(payload));
  }});
}}
'''

_TAINT_CLAIMS = '''claims:
  - id: INPUT-NOT-EXEC
    text: t
    constraint:
      taint_flow:
        source_taint: untrusted_input
        prohibited_sink_zone: subprocess
'''


@pytest.mark.parametrize("event,expected", [
    ("message", "violated"), ("uncaughtException", "confirmed_with_caveats"),
])
def test_only_the_ipc_channel_mints_untrusted_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event: str, expected: str,
) -> None:
    repo = _repo(tmp_path, _TAINT.format(event=event))
    claims = tmp_path / "claims.yaml"
    claims.write_text(_TAINT_CLAIMS)
    out = _run(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"],
               tmp_path / "cache", monkeypatch)
    (verdict,) = json.loads(out)["verdicts"]
    assert verdict["verdict"] == expected, verdict["details"]


@pytest.mark.parametrize("event,receives", [('"open"', False), ('"message"', True)])
def test_a_websocket_open_event_receives_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event: str, receives: bool,
) -> None:
    body = ("function main(u) {\n  const ws = new WebSocket(u);\n"
            f"  ws.addEventListener({event}, (e) => e);\n}}\n")
    chains = _chains(tmp_path, monkeypatch, body)
    assert ("WebSocket.addEventListener" in chains.get("net_recv", set())) is receives, chains


def test_a_real_receive_survives_a_silent_sibling_in_the_same_function(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Measured on workadventure: ``open``, ``close``, ``error`` and ``message``
    listeners on one socket collapse into ONE edge. With only the silent sites
    stamped, the collapse read as "every site crosses nothing" and the real
    ``message`` receive vanished. Every site is stamped now."""
    body = ("function main(u) {\n  const ws = new WebSocket(u);\n"
            '  ws.addEventListener("open", (e) => e);\n'
            '  ws.addEventListener("message", (e) => e);\n'
            '  ws.addEventListener("error", (e) => e);\n}\n')
    chains = _chains(tmp_path, monkeypatch, body)
    assert "WebSocket.addEventListener" in chains.get("net_recv", set()), chains


def test_an_unreadable_event_beside_a_silent_one_still_receives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = ('function main(evt) {\n  process.on("exit", () => 0);\n'
            "  process.on(evt, (m) => m);\n}\n")
    assert "process.on" in _chains(tmp_path, monkeypatch, body).get("ipc_recv", set())
