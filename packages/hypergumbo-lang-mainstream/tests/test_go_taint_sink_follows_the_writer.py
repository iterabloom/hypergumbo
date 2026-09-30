# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taint and io-boundaries give one Go write one answer (INV-totar).

``io.WriteString`` and ``fmt.Fprint*`` are declared under four write boundaries
and the analyzer stamps ``io_target_kind`` from the first argument (WI-suhug).
io-boundaries selected the row by that stamp; taint matched sinks without it
and always took the abstention fallback. Measured on the shipped CLI before
this change, ``io.WriteString(os.Stderr, os.Getenv("API_KEY"))``:
io-boundaries said ``logging``, and ``host_secret -> logging`` CONFIRMED with
0 flows, a false all-clear.

Each case runs both commands on one file and asks them for the same zone,
counting only flows whose sink IS the writer: ``os.Stderr`` is also a
``logging`` attribute row of its own (WI-runos), which is a different sink.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_GO_MOD = "module example.com/r\n\ngo 1.21\n"

#: (file body, the boundary io-boundaries reports, the zone taint must report).
_CASES = {
    "stderr_write_string": (
        'import (\n\t"io"\n\t"os"\n)\n\nfunc F() {\n'
        '\tio.WriteString(os.Stderr, os.Getenv("API_KEY"))\n}\n',
        "logging", "logging",
    ),
    "file_write_string": (
        'import (\n\t"io"\n\t"os"\n)\n\nfunc F(p string) {\n'
        '\tf, _ := os.Create(p)\n\tio.WriteString(f, os.Getenv("API_KEY"))\n}\n',
        "fs_write", "host_fs",
    ),
    "pipe_fprintln": (
        'import (\n\t"fmt"\n\t"os"\n\t"os/exec"\n)\n\nfunc F() {\n'
        '\tcmd := exec.Command("cat")\n\tw, _ := cmd.StdinPipe()\n'
        '\tfmt.Fprintln(w, os.Getenv("API_KEY"))\n}\n',
        "ipc_send", "ipc",
    ),
}

_ZONES = ("logging", "host_fs", "ipc", "network")


def _run(argv: list[str], cache: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(argv)
    return buf.getvalue()


def _repo(tmp_path: Path, body: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text(_GO_MOD)
    (repo / "f.go").write_text("package r\n\n" + body)
    return repo


def _write_boundaries(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch) -> set[str]:
    out = _run(["io-boundaries", str(repo), "--format", "json"],
               tmp_path / "cache", monkeypatch)
    return {
        b for b, v in json.loads(out)["boundaries"].items()
        if b in ("logging", "fs_write", "ipc_send", "net_send")
        and any(c["primitive"] in ("io.WriteString", "fmt.Fprintln")
                for c in v["chains"])
    }


def _writer_zones(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch) -> set[str]:
    claims = tmp_path / "claims.yaml"
    claims.write_text("claims:\n" + "".join(
        f"  - id: {z}\n    text: t\n    constraint:\n      taint_flow:\n"
        f"        source_taint: host_secret\n        prohibited_sink_zone: {z}\n"
        for z in _ZONES
    ))
    out = _run(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"],
               tmp_path / "cache", monkeypatch)
    return {
        v["claim_id"] for v in json.loads(out)["verdicts"]
        if v["verdict"] == "violated"
        and any(set(e.get("sink_primitives", ())) & {"io.WriteString", "fmt.Fprintln"}
                for e in v["evidence"])
    }


@pytest.mark.parametrize("case", sorted(_CASES))
def test_taint_and_io_boundaries_agree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    body, boundary, zone = _CASES[case]
    repo = _repo(tmp_path, body)
    assert _write_boundaries(tmp_path, repo, monkeypatch) == {boundary}
    assert _writer_zones(tmp_path, repo, monkeypatch) == {zone}
