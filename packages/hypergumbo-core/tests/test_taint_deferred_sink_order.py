# SPDX-License-Identifier: AGPL-3.0-or-later
"""A sink inside a closure that runs later is not "before" the source (WI-tivok).

The §3a walk does not run when every sink call line precedes every source call
line (``sink_before_source``), and verify-claims lets such a flow raise only a
caveat. Line order is execution order for straight-line code. It is not for a
closure: its body runs when it is CALLED, which for a Go ``defer``, a
goroutine, a Python ``ExitStack.callback`` or an ``atexit`` hook is after the
statements written below it::

    k := ""
    defer func() { os.WriteFile("o.txt", []byte(k), 0o600) }()
    k = os.Getenv("API_KEY")     // the closure writes THIS value at return

read ``confirmed_with_caveats`` (exit 3) while the secret is written.

The fix lets the walk run when a definition at or after a source reaches,
through a late-capture edge (:func:`cfg.late_capture_edges`), the nested
function that holds the sink call. The edge is the data evidence that the
closure may read a value the source produced; the walk then decides. A
closure that reads nothing defined after the source keeps the old ordering,
and so does a deferred DIRECT call: Go evaluates a deferred call's arguments
at the ``defer`` statement, so ``defer os.WriteFile(.., k, ..)`` writes the
value ``k`` held there.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core import dataflow_scope
from hypergumbo_core.cli import main
from hypergumbo_core.ddg_build import build_repo_ddg

_FS_CLAIMS = '''claims:
  - id: SECRET-FS
    text: Secrets never reach the file system.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: host_fs
'''


def _fs_verdict(tmp_path: Path, name: str, text: str) -> dict[str, Any]:
    """Run the shipped CLI on one file and return the SECRET-FS verdict."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / name).write_text(text)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_FS_CLAIMS)
    mp = pytest.MonkeyPatch()
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), \
                contextlib.redirect_stderr(io.StringIO()):
            main(["verify-claims", str(repo), "--claims", str(claims),
                  "--format", "json"])
    finally:
        mp.undo()
    verdicts = {v["claim_id"]: v for v in json.loads(buf.getvalue())["verdicts"]}
    return verdicts["SECRET-FS"]


_GO_HEAD = 'package main\n\nimport (\n\t"fmt"\n\t"os"\n)\n\n'

# Each form writes, from a closure created BEFORE the assignment, the value the
# secret is assigned AFTER it. Every one runs the closure after the assignment.
_LATE_WRITES = {
    "go-defer-closure": ("main.go", _GO_HEAD + (
        "func leak() {\n"
        "\tk := \"\"\n"
        "\tdefer func() { os.WriteFile(\"o.txt\", []byte(k), 0o600) }()\n"
        "\tk = os.Getenv(\"API_KEY\")\n"
        "\tfmt.Println(len(k))\n"
        "}\n"
    )),
    # The closure reads ``k`` on one line and writes on the next: the sink line
    # carries no late edge of its own, the closure holding it does.
    "go-defer-closure-two-lines": ("main.go", _GO_HEAD + (
        "func leak() {\n"
        "\tk := \"\"\n"
        "\tdefer func() {\n"
        "\t\tb := []byte(k)\n"
        "\t\tos.WriteFile(\"o.txt\", b, 0o600)\n"
        "\t}()\n"
        "\tk = os.Getenv(\"API_KEY\")\n"
        "\tfmt.Println(len(k))\n"
        "}\n"
    )),
    "go-defer-call-with-closure-argument": ("main.go", _GO_HEAD + (
        "func run(f func()) { f() }\n\n"
        "func leak() {\n"
        "\tk := \"\"\n"
        "\tdefer run(func() { os.WriteFile(\"o.txt\", []byte(k), 0o600) })\n"
        "\tk = os.Getenv(\"API_KEY\")\n"
        "\tfmt.Println(len(k))\n"
        "}\n"
    )),
    "go-goroutine": ("main.go", _GO_HEAD + (
        "func leak() {\n"
        "\tk := \"\"\n"
        "\tgo func() { os.WriteFile(\"o.txt\", []byte(k), 0o600) }()\n"
        "\tk = os.Getenv(\"API_KEY\")\n"
        "\tfmt.Println(len(k))\n"
        "}\n"
    )),
    "python-exitstack-callback": ("app.py", (
        "import contextlib\n"
        "import os\n\n\n"
        "def leak():\n"
        "    k = ''\n"
        "    with contextlib.ExitStack() as stack:\n"
        "        stack.callback(lambda: open('o.txt', 'w').write(k))\n"
        "        k = os.environ['API_KEY']\n"
        "        print(len(k))\n"
    )),
    "python-atexit-lambda": ("app.py", (
        "import atexit\n"
        "import os\n\n\n"
        "def leak():\n"
        "    k = ''\n"
        "    atexit.register(lambda: open('o.txt', 'w').write(k))\n"
        "    k = os.environ['API_KEY']\n"
        "    print(len(k))\n"
    )),
}


@pytest.mark.parametrize("form", sorted(_LATE_WRITES))
def test_a_closure_that_runs_later_writes_the_later_secret(
    tmp_path: Path, form: str,
) -> None:
    """Each read ``confirmed_with_caveats`` or ``inconclusive`` with the
    ``sink_before_source_only`` caveat: the walk never ran. It runs now, and
    follows the late-capture edge from the assignment into the closure."""
    name, text = _LATE_WRITES[form]
    verdict = _fs_verdict(tmp_path, name, text)
    assert verdict["verdict"] == "violated"
    assert [e["walk_verdict"] for e in verdict["evidence"]] == ["reached"]
    kinds = {c["kind"] for c in verdict.get("caveats") or []}
    assert "sink_before_source_only" not in kinds


# PRECISION. Sinks written before the source that cannot see the secret.
_NO_LATE_READ = {
    # Go evaluates a deferred call's ARGUMENTS at the defer statement: this
    # writes the empty string, whatever ``k`` holds when ``leak`` returns.
    "go-defer-direct-call": ("main.go", _GO_HEAD + (
        "func leak() {\n"
        "\tk := \"\"\n"
        "\tdefer os.WriteFile(\"o.txt\", []byte(k), 0o600)\n"
        "\tk = os.Getenv(\"API_KEY\")\n"
        "\tfmt.Println(len(k))\n"
        "}\n"
    )),
    # A closure invoked where it is written has run before the assignment.
    "go-closure-invoked-in-place": ("main.go", _GO_HEAD + (
        "func leak() {\n"
        "\tk := \"\"\n"
        "\tfunc() { os.WriteFile(\"o.txt\", []byte(k), 0o600) }()\n"
        "\tk = os.Getenv(\"API_KEY\")\n"
        "\tfmt.Println(len(k))\n"
        "}\n"
    )),
    # The deferred closure reads only a value fixed before the source.
    "go-defer-closure-without-the-secret": ("main.go", _GO_HEAD + (
        "func leak() {\n"
        "\tk := \"\"\n"
        "\tm := \"static\"\n"
        "\tdefer func() { os.WriteFile(\"o.txt\", []byte(m), 0o600) }()\n"
        "\tk = os.Getenv(\"API_KEY\")\n"
        "\tfmt.Println(len(k))\n"
        "}\n"
    )),
}


@pytest.mark.parametrize("form", sorted(_NO_LATE_READ))
def test_a_deferred_sink_that_cannot_read_the_secret_is_not_a_violation(
    tmp_path: Path, form: str,
) -> None:
    """No definition after the source reaches the deferred sink, so its line
    order stands: the flow is reported under the caveat and holds no verdict."""
    name, text = _NO_LATE_READ[form]
    verdict = _fs_verdict(tmp_path, name, text)
    assert verdict["verdict"] == "confirmed_with_caveats"
    assert {c["kind"] for c in verdict["caveats"]} == {"sink_before_source_only"}


def test_a_closure_in_a_go_defer_reads_a_later_definition(tmp_path: Path) -> None:
    """The CFG side. ``defer func() { ..k.. }()`` held no region: the deferred
    statement was one marker, so no late edge could reach the closure's read of
    ``k``. A deferred DIRECT call keeps none: it reads ``k`` where it stands."""
    (tmp_path / "main.go").write_text(_LATE_WRITES["go-defer-closure"][1])
    (tmp_path / "direct.go").write_text(
        _NO_LATE_READ["go-defer-direct-call"][1].replace("leak", "direct"),
    )
    dataflow_scope.ensure_def_use_extractors_registered()
    ddg = build_repo_ddg(tmp_path, languages=("go",))
    late = {
        (e.symbol_id.split(":")[1], e.def_line, e.use_line, e.late_region)
        for e in ddg.ddg_edges if e.variable == "k" and e.late_region is not None
    }
    assert late == {("main.go", 11, 10, (10, 10))}


# Where each nested function runs, as the late edge's ``late_region`` says it:
# (language, file, text, the line of the later definition, the closure's read
# line, whether the edge carries a region). An edge into a function invoked in
# place carries none: it has run before the later definition is made.
_WHERE_IT_RUNS = {
    "go-invoked-in-place": ("go", "main.go", _GO_HEAD + (
        "func f() {\n\tk := \"\"\n\tfunc() { println(k) }()\n"
        "\tk = os.Getenv(\"K\")\n\tfmt.Println(k)\n}\n"
    ), 11, 10, False),
    "go-defer": ("go", "main.go", _GO_HEAD + (
        "func f() {\n\tk := \"\"\n\tdefer func() { println(k) }()\n"
        "\tk = os.Getenv(\"K\")\n\tfmt.Println(k)\n}\n"
    ), 11, 10, True),
    "go-goroutine": ("go", "main.go", _GO_HEAD + (
        "func f() {\n\tk := \"\"\n\tgo func() { println(k) }()\n"
        "\tk = os.Getenv(\"K\")\n\tfmt.Println(k)\n}\n"
    ), 11, 10, True),
    "python-lambda-invoked-in-place": ("python", "app.py", (
        "import os\ndef f():\n    k = ''\n    (lambda: print(k))()\n"
        "    k = os.environ['K']\n    return k\n"
    ), 5, 4, False),
    "javascript-arrow-invoked-in-place": ("javascript", "app.js", (
        "function f() {\n  let k = '';\n  (() => { console.log(k); })();\n"
        "  k = process.env.K;\n  return k;\n}\n"
    ), 4, 3, False),
    "javascript-async-arrow-invoked-in-place": ("javascript", "app.js", (
        "function f() {\n  let k = '';\n  (async () => { console.log(k); })();\n"
        "  k = process.env.K;\n  return k;\n}\n"
    ), 4, 3, True),
    "javascript-generator-invoked-in-place": ("javascript", "app.js", (
        "function f() {\n  let k = '';\n  (function* () { console.log(k); })();\n"
        "  k = process.env.K;\n  return k;\n}\n"
    ), 4, 3, True),
    "rust-closure-invoked-in-place": ("rust", "lib.rs", (
        "pub fn f() -> String {\n    let mut k = String::new();\n"
        "    (|| println!(\"{}\", k))();\n    k = std::env::var(\"K\").unwrap();\n"
        "    k\n}\n"
    ), 4, 3, False),
}


@pytest.mark.parametrize("form", sorted(_WHERE_IT_RUNS))
def test_a_late_edge_names_the_function_only_when_it_runs_later(
    tmp_path: Path, form: str,
) -> None:
    """``late_region`` is set unless the function runs where it is written."""
    language, name, text, def_line, read_line, runs_later = _WHERE_IT_RUNS[form]
    (tmp_path / name).write_text(text)
    dataflow_scope.ensure_def_use_extractors_registered()
    ddg = build_repo_ddg(tmp_path, languages=(language,))
    late = [
        e for e in ddg.ddg_edges
        if e.variable == "k" and e.def_line == def_line and e.use_line == read_line
    ]
    assert late, "the late-capture edge itself exists in every form"
    regions = {e.late_region for e in late}
    assert regions == ({(read_line, read_line)} if runs_later else {None})
