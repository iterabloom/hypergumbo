# SPDX-License-Identifier: AGPL-3.0-or-later
"""One write is one finding; a stream finding names its call (INV-hopib).

The verdicts a user reads, through ``verify-claims`` on one-file repositories.
Measured on the shipped CLI before the change (dev 1a55adbce3):

- python ``print(k, file=sys.stderr)`` -> TWO evidence rows, ``sys.stderr``
  (a precise DDG "walk" to a keyword VALUE) and ``builtins.print``, and the
  details said "2 unsanitized flows" for one write.
- go ``io.WriteString(os.Stderr, k)`` -> the same two-row shape, since WI-runos
  put the stream row in the writer's zone.

The core mechanism is pinned in ``hypergumbo-core``'s
``test_one_write_one_sink``; the analyzers' stamp is what these exercise, per
language, and the controls are the shapes that must KEEP the stream: a carrier
that is not a sink (``json.dump``, ``log.SetOutput``), and a receiver whose
method no catalogue rows (``System.out.println``, ``process.stdout.write``) --
where the stream is the only sink there is, and the carrier is what makes the
record checkable.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_CLAIM = (
    "claims:\n  - id: HS-LOG\n    text: t\n    constraint:\n      taint_flow:\n"
    "        source_taint: host_secret\n        prohibited_sink_zone: logging\n"
)

_PY_HEAD = "import json\nimport os\nimport sys\n\n\ndef main():\n    k = os.environ['API_KEY']\n"
_GO_HEAD = 'package main\n\nimport (\n\t"io"\n\t"log"\n\t"os"\n)\n\nfunc main() {\n\tk := os.Getenv("API_KEY")\n'

#: case -> (file, body, the rows' (sink_primitives, sink_carriers) the claim must report)
_CASES = {
    "python_print_file_keyword": (
        "main.py", _PY_HEAD + "    print(k, file=sys.stderr)\n",
        [(["builtins.print"], [])],
    ),
    "python_json_dump_keeps_the_stream": (
        "main.py", _PY_HEAD + "    json.dump(k, sys.stdout)\n",
        [(["sys.stdout"], ["json.dump@8"])],
    ),
    "python_one_uncovered_use_keeps_the_stream": (
        "main.py", _PY_HEAD + "    print(k, file=sys.stderr)\n    json.dump(k, sys.stderr)\n",
        [(["builtins.print"], []), (["sys.stderr"], ["json.dump@9", "print@8"])],
    ),
    "go_write_string": (
        "main.go", _GO_HEAD + "\tio.WriteString(os.Stderr, k)\n}\n",
        [(["io.WriteString"], [])],
    ),
    "go_set_output_names_its_call": (
        "main.go", _GO_HEAD + "\tlog.SetOutput(os.Stderr)\n\t_ = k\n}\n",
        [(["os.Stderr"], ["log.SetOutput@11"])],
    ),
    "java_receiver": (
        "Main.java",
        "public class Main {\n    public static void main(String[] args) {\n"
        '        String k = System.getenv("API_KEY");\n'
        "        System.out.println(k);\n    }\n}\n",
        [(["java.lang.System.out"], ["System.out.println@4"])],
    ),
    "javascript_receiver": (
        "main.js",
        "function main() {\n  const k = process.env.API_KEY;\n  process.stdout.write(k);\n}\nmain();\n",
        [(["process.stdout"], ["process.stdout.write@3"])],
    ),
}


def _rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, body: str):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / name).write_text(body)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIM)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict


@pytest.mark.parametrize("case", sorted(_CASES))
def test_the_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    name, body, expected = _CASES[case]
    verdict = _rows(tmp_path, monkeypatch, name, body)
    assert verdict["verdict"] == "violated", verdict["details"]
    got = sorted((e["sink_primitives"], e["sink_carriers"]) for e in verdict["evidence"])
    assert got == sorted(expected)


def test_one_write_is_one_flow_in_the_prose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    name, body, _ = _CASES["python_print_file_keyword"]
    verdict = _rows(tmp_path, monkeypatch, name, body)
    assert verdict["details"].startswith("1 unsanitized host_secret flow(s)")
