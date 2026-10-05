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
    # WI-potog: a Unix-domain connection is process-local (WI-baran's rule).
    # Its TCP control is at the bottom of this file (WI-gajir).
    "unix_dial_fprintln": (
        'import (\n\t"fmt"\n\t"net"\n\t"os"\n)\n\nfunc F(p string) {\n'
        '\tcon, _ := net.Dial("unix", p)\n'
        '\tfmt.Fprintln(con, os.Getenv("API_KEY"))\n}\n',
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


def test_a_tcp_dials_print_reaches_the_network_zone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WI-potog's TCP control, fixed by WI-gajir. io-boundaries says
    ``net_send``. Taint used to report NO flow: the content flow into
    ``fmt.Fprintln`` and ``net.Dial``'s resource-naming flow (a class-R row,
    WI-bulag) collapsed into one ``network`` finding that took
    ``resource_naming_only`` from ``net.Dial`` alone, and the consumer
    excluded the whole group. The two now stay separate rows."""
    repo = _repo(tmp_path, (
        'import (\n\t"fmt"\n\t"net"\n\t"os"\n)\n\nfunc F(a string) {\n'
        '\tcon, _ := net.Dial("tcp", a)\n'
        '\tfmt.Fprintln(con, os.Getenv("API_KEY"))\n}\n'
    ))
    assert _write_boundaries(tmp_path, repo, monkeypatch) == {"net_send"}
    assert _writer_zones(tmp_path, repo, monkeypatch) == {"network"}
    network = _network_verdict(tmp_path, repo, monkeypatch)
    assert network["evidence_count"] == 1
    assert network["evidence"][0]["sink_primitives"] == ["fmt.Fprintln"]
    assert network["resource_naming_flows"] == 1


def test_a_dial_alone_only_names_the_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WI-gajir's control: a function whose only network sink is
    ``net.Dial`` can only have told it WHICH host to reach, so the flow stays
    excluded from the verdict and disclosed beside it."""
    repo = _repo(tmp_path, (
        'import (\n\t"net"\n\t"os"\n)\n\nfunc F() {\n'
        '\tcon, _ := net.Dial("tcp", os.Getenv("API_KEY"))\n'
        '\t_ = con\n}\n'
    ))
    network = _network_verdict(tmp_path, repo, monkeypatch)
    assert network["verdict"] != "violated"
    assert network["evidence_count"] == 0
    assert network["resource_naming_flows"] == 1


def _network_verdict(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> dict:
    claims = tmp_path / "network.yaml"
    claims.write_text(
        "claims:\n  - id: network\n    text: t\n    constraint:\n"
        "      taint_flow:\n        source_taint: host_secret\n"
        "        prohibited_sink_zone: network\n"
    )
    out = _run(["verify-claims", str(repo), "--claims", str(claims),
                "--format", "json"], tmp_path / "cache", monkeypatch)
    (verdict,) = json.loads(out)["verdicts"]
    return verdict
