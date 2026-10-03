# SPDX-License-Identifier: AGPL-3.0-or-later
"""A PATH lookup launches nothing (INV-dukam, the LookPath half).

WHAT WAS WRONG. go.yaml rowed ``os/exec`` [Command, CommandContext, LookPath]
under ``subprocess``, and the community overlay go-x-sys-and-grpc.yaml
repeated the row for ``golang.org/x/sys/execabs``. ``LookPath`` loops over
``$PATH`` calling ``os.Stat`` / ``unix.Eaccess`` on each candidate and returns
a string; it creates no process. ``subprocess`` is an auto-derived taint SINK
(zone ``subprocess``) and OPAQUE, so ``exec.LookPath(os.Getenv("TOOL"))`` --
a program that runs nothing -- violated ``host_secret -> subprocess`` and
"never launches a subprocess", and withheld every other clean verdict on the
repo behind "the analysis launches an external program".
``HIGH_RISK_EXEMPTIONS_SUBPROCESS`` already said so in a comment ("PATH-lookup
helpers (string in, string out; no exec)") while keeping the row.

WHY fs_read AND NOT A DELETION. It stats files, which is what go.yaml's own
``os.Stat`` row is, and python rows its spelling of the same operation,
``shutil.which``, under ``fs_read``. ``fs_read`` derives no taint source, so
the only taint consequence is the ``subprocess`` SINK disappearing.

WHAT IS DELIBERATELY NOT CHANGED. ``Command`` / ``CommandContext`` only BUILD
a ``*Cmd``; the process is created later by ``Start``. But the constructor's
arguments are the only carrier of the launched program's name and argv --
``Run`` / ``Start`` take none -- so removing them is an ADR-0049 Ruling-3
crossing that needs taint represented through the ``*Cmd`` receiver first
(WI-kozaj). That half of INV-dukam stays open; the control below pins it
unchanged so this file cannot be read as having decided it.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

import hypergumbo_core.io_boundary as iob
from hypergumbo_core.cli import main
from hypergumbo_core.io_boundary import (
    HIGH_RISK_EXEMPTIONS_SUBPROCESS,
    load_catalog,
)
from hypergumbo_core.taint import _derive_auto_imports_from_io_primitives

_CATALOG_DIR = Path(iob.__file__).parent / "io_primitives"


def _boundaries(module: str, name: str) -> set[str]:
    catalog = load_catalog("go")
    assert catalog is not None
    return {
        p.boundary for p in catalog.primitives
        if p.module == module and p.name == name
    }


@pytest.mark.parametrize("module", ["os/exec", "golang.org/x/sys/execabs"])
def test_lookpath_is_a_filesystem_read(module: str) -> None:
    assert _boundaries(module, "LookPath") == {"fs_read"}


def test_parity_python_which_is_the_same_row() -> None:
    catalog = load_catalog("python")
    assert catalog is not None
    assert {p.boundary for p in catalog.primitives
            if p.module == "shutil" and p.name == "which"} == {"fs_read"}


@pytest.mark.parametrize("module", ["os/exec", "golang.org/x/sys/execabs"])
@pytest.mark.parametrize("name", ["Command", "CommandContext"])
def test_control_the_constructors_are_untouched_pending_wi_kozaj(
    module: str, name: str,
) -> None:
    assert _boundaries(module, name) == {"subprocess"}


def test_no_subprocess_sink_and_no_stale_exemption() -> None:
    _sources, sinks, _amb = _derive_auto_imports_from_io_primitives(_CATALOG_DIR)
    derived = {(s.module, s.name) for s in sinks.get("go", ())
               if s.zone == "subprocess"}
    assert ("os/exec.Cmd", "Run") in derived  # reach
    assert ("os/exec", "LookPath") not in derived
    assert "os/exec.LookPath" not in HIGH_RISK_EXEMPTIONS_SUBPROCESS
    assert "golang.org/x/sys/execabs.LookPath" not in HIGH_RISK_EXEMPTIONS_SUBPROCESS


_GO = '''package main

import (
	"fmt"
	"os"
	"os/exec"
)

func main() {
	name := os.Getenv("TOOL")
	p, err := exec.LookPath(name)
	if err != nil {
		return
	}
	fmt.Println(p)
}
'''

_CLAIMS = '''claims:
  - id: NO-SUBPROCESS
    text: This program never launches a subprocess.
    constraint:
      boundary: subprocess
      must_not_exist: true
  - id: SECRET-NO-SUBPROCESS
    text: No environment value reaches a subprocess.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: subprocess
'''


def _run(tmp_path: Path, argv: list[str]) -> str:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "go.mod").write_text("module example.com/lp\n\ngo 1.21\n")
    (repo / "main.go").write_text(_GO)
    (tmp_path / "claims.yaml").write_text(_CLAIMS)
    mp = pytest.MonkeyPatch()
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), \
                contextlib.redirect_stderr(io.StringIO()):
            main([a.replace("{repo}", str(repo))
                  .replace("{claims}", str(tmp_path / "claims.yaml"))
                  for a in argv])
    finally:
        mp.undo()
    return buf.getvalue()


def test_a_program_that_only_looks_a_tool_up_launches_nothing(
    tmp_path: Path,
) -> None:
    """At the FINDING level, through the shipped CLI."""
    report = json.loads(_run(
        tmp_path, ["io-boundaries", "{repo}", "--format", "json", "--include-tests"],
    ))
    chains = {b: {c["primitive"] for c in v["chains"]}
              for b, v in report["boundaries"].items()}
    assert "os.Getenv" in chains.get("env_read", set())  # reach
    assert "os/exec.LookPath" in chains.get("fs_read", set())
    assert "subprocess" not in chains, chains.get("subprocess")

    out = _run(tmp_path, ["verify-claims", "{repo}", "--claims", "{claims}",
                          "--format", "json"])
    verdicts = {v["claim_id"]: v for v in json.loads(out)["verdicts"]}
    for claim_id in ("NO-SUBPROCESS", "SECRET-NO-SUBPROCESS"):
        verdict = verdicts[claim_id]
        assert verdict["verdict"] != "violated", verdict["details"]
        assert "launches an external program" not in verdict["details"]
