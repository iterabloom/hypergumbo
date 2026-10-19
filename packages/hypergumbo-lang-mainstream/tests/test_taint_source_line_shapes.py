# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-jusoj's filed shapes, end to end through verify-claims.

A sink containing the source on one line read ``sink_before_source`` and was
withheld (``fetch(url, {headers: {A: process.env.T}})``, ``requests.post(url,
headers={"A": os.environ["T"]})``); a non-sink consumer on the source's line
(``acc.push(process.env.K)``) let the walk remove a flow whose secret is
sent. Each now reads ``violated``. The walk itself is tested in
hypergumbo-core's ``test_taint_source_line.py``.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

_CLAIMS = """\
claims:
  - id: SECRET-NO-NET
    text: A secret is never sent.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: network
"""

_SHAPES = {
    "fetch_on_source_line": ("a.js", (
        "async function go(url) {\n"
        "  const res = await fetch(url, { headers: { Authorization: process.env.API_TOKEN } });\n"
        "  return res.json();\n"
        "}\n"
        "module.exports = { go };\n"
    )),
    "post_on_source_line": ("a.py", (
        "import os\nimport requests\n\n\n"
        "def go(url):\n"
        "    resp = requests.post(url, headers={\"Authorization\": os.environ[\"API_TOKEN\"]})\n"
        "    return resp.json()\n"
    )),
    "sendall_on_source_line": ("a.py", (
        "import os\nimport socket\n\n\n"
        "def go(host):\n"
        "    sock = socket.create_connection((host, 80))\n"
        "    r = sock.sendall(os.environ[\"API_KEY\"].encode())\n"
        "    print(r)\n"
    )),
    "push_beside_source": ("a.js", (
        "async function go(url) {\n"
        "  const acc = [];\n"
        "  const n = acc.push(process.env.API_KEY);\n"
        "  console.log(n);\n"
        "  return fetch(url + acc[0]);\n"
        "}\n"
        "module.exports = { go };\n"
    )),
}


@pytest.mark.parametrize("shape", list(_SHAPES))
def test_the_filed_shape_is_violated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str,
) -> None:
    from hypergumbo_core.cli import main

    name, text = _SHAPES[shape]
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / name).write_text(text)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    assert verdict["verdict"] == "violated", verdict["details"]
