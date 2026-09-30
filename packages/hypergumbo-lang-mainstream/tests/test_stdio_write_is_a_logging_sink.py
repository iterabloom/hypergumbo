# SPDX-License-Identifier: AGPL-3.0-or-later
"""A secret written to stdout / stderr is a LOGGING flow, not an IPC one (WI-runos).

The catalogue side is pinned in core (``test_stdio_output_is_logging``); this is
the verdict a user reads. Measured on the shipped CLI before the change:

- go   ``io.WriteString(os.Stderr, os.Getenv("API_KEY"))``: ``host_secret ->
  ipc`` VIOLATED through ``os.Stderr``, beside the logging flow through
  ``io.WriteString``. One write, two zones.
- java ``System.out.println(System.getenv("API_KEY"))``: ``host_secret -> ipc``
  VIOLATED, ``host_secret -> logging`` CLEAN (confirmed_with_caveats). The
  wrong zone, and a false all-clear in the right one.
- cpp  ``std::cerr << getenv("API_KEY")``: ``ipc`` VIOLATED, ``logging``
  CONFIRMED.

So the change removes a finding AND adds one, and each case asserts both
halves.

kotlin and scala are NOT cases here, and that is measured, not forgotten: their
analyzers emit no attribute reference for ``System.out`` / ``System.err`` (java
emits ``module_attr_ref``), so the row -- correct for them too, through
``_CATALOG_PARENTS``, and pinned in core -- is never reached, and a stdio write
there is seen by neither zone. That is a reachability gap of its own, filed
separately.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_CLAIMS = (
    "claims:\n"
    "  - id: HS-IPC\n    text: t\n    constraint:\n      taint_flow:\n"
    "        source_taint: host_secret\n        prohibited_sink_zone: ipc\n"
    "  - id: HS-LOG\n    text: t\n    constraint:\n      taint_flow:\n"
    "        source_taint: host_secret\n        prohibited_sink_zone: logging\n"
)

#: case -> (file name, body, the stream's sink primitive as verify-claims names
#: it, the sink the LOGGING row names). The two differ for go only: since
#: INV-hopib a stream handed to a call that is itself a sink in the same zone is
#: reported by that call, so go's write is ``io.WriteString``'s -- and the
#: stream must still be absent from ipc, which is this item's half.
_CASES = {
    "go": (
        "main.go",
        'package main\n\nimport (\n\t"io"\n\t"os"\n)\n\nfunc main() {\n'
        '\tio.WriteString(os.Stderr, os.Getenv("API_KEY"))\n}\n',
        "os.Stderr", "io.WriteString",
    ),
    "java": (
        "Main.java",
        "public class Main {\n    public static void main(String[] args) {\n"
        '        String k = System.getenv("API_KEY");\n'
        "        System.out.println(k);\n    }\n}\n",
        "java.lang.System.out", "java.lang.System.out",
    ),
    "cpp": (
        "main.cpp",
        "#include <stdlib.h>\n#include <iostream>\n\nint main() {\n"
        '    const char *k = getenv("API_KEY");\n'
        "    std::cerr << k << std::endl;\n    return 0;\n}\n",
        "std.cerr", "std.cerr",
    ),
}


def _verdicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
              name: str, body: str) -> dict[str, dict]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / name).write_text(body)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    return {v["claim_id"]: v for v in json.loads(buf.getvalue())["verdicts"]}


def _sinks(verdict: dict) -> set[str]:
    return {s for e in verdict["evidence"] for s in e.get("sink_primitives", ())}


@pytest.mark.parametrize("case", sorted(_CASES))
def test_the_write_is_a_logging_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    name, body, _stream, logged_as = _CASES[case]
    verdicts = _verdicts(tmp_path, monkeypatch, name, body)
    log = verdicts["HS-LOG"]
    assert log["verdict"] == "violated", log["details"]
    assert logged_as in _sinks(log), _sinks(log)


@pytest.mark.parametrize("case", sorted(_CASES))
def test_the_write_is_not_an_ipc_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    name, body, stream, _logged_as = _CASES[case]
    ipc = _verdicts(tmp_path, monkeypatch, name, body)["HS-IPC"]
    assert stream not in _sinks(ipc), ipc["details"]
    assert ipc["verdict"] != "violated", ipc["details"]
