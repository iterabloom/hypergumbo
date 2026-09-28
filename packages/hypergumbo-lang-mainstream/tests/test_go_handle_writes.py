# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Go write through the handle's own method is a write (WI-nunab, WI-ninuz).

Two gaps in go.yaml, measured on the shipped commands before this change:

* WI-nunab. ``os.File.{Write,WriteString}`` and ``bufio.Writer.{Write,
  WriteString}`` had no row, so ``f.Write(b)`` -- the idiomatic Go file write
  -- had no sink and no chain. It was disclosed under ``external_potential``,
  and a must_not_exist fs_write claim over a program whose only write went
  through the handle was ``inconclusive`` (go.yaml grants no module, so the
  gate withheld rather than confirmed).
* WI-ninuz. ``os.OpenFile`` had no row at all, so Go's flag-based open was
  unclassified at the open itself.

The handle rows follow ``io.WriteString``'s shape (WI-suhug): one row per
write boundary, ``call_site_undecidable``, abstaining to ``fs_write``, with the
analyzer stamping ``io_target_kind`` from where the RECEIVER came from -- an
``os.Create`` / ``os.OpenFile`` handle is a file, ``os.Stdout`` is terminal
output (``logging``, WI-dutah), and a ``bufio.Writer`` is whatever its
``bufio.NewWriter`` argument was. (An ``os.Pipe()`` end would stamp ``pipe``,
but go.py types only the FIRST name a multi-result ``:=`` binds, so the write
end ``w`` of ``r, w, _ := os.Pipe()`` has no type to take a row from.)

``os.OpenFile`` is mode-discriminated like python's ``open``: the flag
argument is a bitwise OR of package constants rather than a string, so the
mode seam reads the flag names (``O_WRONLY``, ``O_RDWR``, ``O_APPEND``,
``O_CREATE``, ``O_TRUNC`` write; ``O_RDONLY`` alone reads).
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_HDR = 'package main\n\nimport (\n\t"bufio"\n\t"os"\n)\n\n'

_CASES = {
    "create_write": _HDR + "func f(p string, b []byte) {\n\tfh, _ := os.Create(p)\n"
                    "\tfh.Write(b)\n\tfh.WriteString(\"x\")\n}\n",
    "openfile_write": _HDR + "func f(p string, b []byte) {\n"
                      "\tfh, _ := os.OpenFile(p, os.O_WRONLY|os.O_CREATE, 0o644)\n"
                      "\tfh.Write(b)\n}\n",
    "openfile_read": _HDR + "func f(p string) {\n"
                     "\tfh, _ := os.OpenFile(p, os.O_RDONLY, 0)\n\t_ = fh\n}\n",
    "param_write": _HDR + "func f(fh *os.File, b []byte) {\n\tfh.Write(b)\n}\n",
    "stdout_write": _HDR + "func f(b []byte) {\n\tos.Stdout.Write(b)\n}\n",
    "bufio_file": _HDR + "func f(p string) {\n\tfh, _ := os.Create(p)\n"
                  "\tw := bufio.NewWriter(fh)\n\tw.WriteString(\"x\")\n\tw.Flush()\n}\n",
    "bufio_stdout": _HDR + "func f() {\n\tw := bufio.NewWriter(os.Stdout)\n"
                    "\tw.WriteString(\"x\")\n\tw.Flush()\n}\n",
    "builder": 'package main\n\nimport "strings"\n\nfunc f() string {\n'
               "\tvar sb strings.Builder\n\tsb.WriteString(\"x\")\n\treturn sb.String()\n}\n",
}


def _run(argv: list[str], cache: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(argv)
    return buf.getvalue()


def _repo(tmp_path: Path, src: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.go").write_text(src)
    (repo / "go.mod").write_text("module example.com/app\n\ngo 1.22\n")
    return repo


def _chains(tmp_path: Path, name: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, set[str]]:
    out = _run(["io-boundaries", str(_repo(tmp_path, _CASES[name])), "--format", "json",
                "--include-tests"], tmp_path / "cache", monkeypatch)
    return {b: {c["primitive"] for c in v["chains"]}
            for b, v in json.loads(out)["boundaries"].items()}


@pytest.mark.parametrize("name,boundary,primitives", [
    ("create_write", "fs_write", {"os.File.Write", "os.File.WriteString"}),
    ("openfile_write", "fs_write", {"os.OpenFile", "os.File.Write"}),
    ("openfile_read", "fs_read", {"os.OpenFile"}),
    ("param_write", "fs_write", {"os.File.Write"}),
    ("stdout_write", "logging", {"os.File.Write"}),
    ("bufio_file", "fs_write", {"bufio.Writer.WriteString"}),
    ("bufio_stdout", "logging", {"bufio.Writer.WriteString"}),
])
def test_the_write_is_reported_under_its_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    name: str, boundary: str, primitives: set[str],
) -> None:
    chains = _chains(tmp_path, name, monkeypatch)
    assert primitives <= chains.get(boundary, set()), chains


@pytest.mark.parametrize("name,not_boundary,primitive", [
    ("openfile_read", "fs_write", "os.OpenFile"),
    ("stdout_write", "fs_write", "os.File.Write"),
    ("bufio_stdout", "fs_write", "bufio.Writer.WriteString"),
])
def test_the_target_decides_which_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    name: str, not_boundary: str, primitive: str,
) -> None:
    assert primitive not in _chains(tmp_path, name, monkeypatch).get(not_boundary, set())


def test_an_in_memory_builder_is_still_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    chains = _chains(tmp_path, "builder", monkeypatch)
    assert not any("WriteString" in p for b, ps in chains.items()
                   if b != "external_potential" for p in ps)


def test_a_write_through_an_opened_handle_fails_the_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The finding the rows were filed on: this was inconclusive."""
    repo = _repo(tmp_path, _CASES["openfile_write"])
    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "claims:\n  - id: C\n    text: t\n    constraint:\n"
        "      boundary: fs_write\n      must_not_exist: true\n"
    )
    out = _run(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"],
               tmp_path / "cache", monkeypatch)
    (verdict,) = json.loads(out)["verdicts"]
    assert verdict["verdict"] == "violated", verdict["details"]
