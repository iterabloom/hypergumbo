# SPDX-License-Identifier: AGPL-3.0-or-later
"""A C secret sent over a socket is a taint flow, not a clean verdict (INV-foda).

The c analyzer puts the file's ``#include`` headers in an unresolved call's
module slot (``stdlib.h,string.h,sys/socket.h``). io-boundaries has split that
slot into candidates since INV-funuf; taint's lookup was a separate copy that
compared the joined string, so neither ``getenv`` nor ``send`` had a taint
entry. Measured on the shipped CLI before this change, both files below:

* io-boundaries: ``env_read stdlib.getenv``, ``net_send sys/socket.send``;
* verify-claims ``host_secret -> network``: CONFIRMED, 0 flows.

Both consumers now take the row from one rule (``io_boundary.named_lookup_arm``).
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_BODY = (
    "void leak(int fd) {\n"
    '    char *k = getenv("API_KEY");\n'
    "    send(fd, k, 8, 0);\n"
    "}\n"
)

_FILES = {
    # Three headers: the joined slot taint could not split.
    "include_list": "#include <stdlib.h>\n#include <string.h>\n#include <sys/socket.h>\n\n" + _BODY,
    # One header: the slot keeps ``sys/socket.h`` while c.yaml says ``sys/socket``.
    "single_include": "#include <sys/socket.h>\n\nextern char *getenv(const char *);\n" + _BODY,
}


def _run(argv: list[str], cache: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(argv)
    return buf.getvalue()


@pytest.mark.parametrize("name", sorted(_FILES))
def test_secret_sent_over_a_socket_is_violated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "leak.c").write_text(_FILES[name])
    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "claims:\n  - id: C\n    text: t\n    constraint:\n      taint_flow:\n"
        "        source_taint: host_secret\n        prohibited_sink_zone: network\n"
    )
    out = _run(["io-boundaries", str(repo), "--format", "json"], tmp_path / "cache", monkeypatch)
    chains = {b: {c["primitive"] for c in v["chains"]} for b, v in json.loads(out)["boundaries"].items()}
    assert "stdlib.getenv" in chains.get("env_read", set()), chains
    assert "sys/socket.send" in chains.get("net_send", set()), chains

    out = _run(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"],
               tmp_path / "cache", monkeypatch)
    (verdict,) = json.loads(out)["verdicts"]
    assert verdict["verdict"] == "violated", verdict["details"]
    (flow,) = verdict["evidence"]
    assert flow["source_primitives"] == ["stdlib.getenv"]
    assert flow["sink_primitives"] == ["sys/socket.send"]
