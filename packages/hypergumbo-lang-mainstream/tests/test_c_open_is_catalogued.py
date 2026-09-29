# SPDX-License-Identifier: AGPL-3.0-or-later
"""POSIX ``open`` classifies by its flags; ``close`` is ruled no boundary (WI-bulub).

``c.yaml`` listed ``open`` and ``close`` in ``ambiguous_names`` -- withhold a
bare match unless module evidence names the header -- and then carried NO row
for either, so perfect module evidence reached nothing. Measured on the shipped
CLI before this change: ``int fd = open(path, O_WRONLY | O_CREAT, 0644);`` in a
file that includes ``<fcntl.h>`` was ``external_potential``, never ``fs_write``,
so no ``open`` could be a taint sink or a classified read.

``open`` is now ``fcntl.open`` under ``fs_read`` AND ``fs_write``, the shape
``stdio.fopen`` and Go's ``os.OpenFile`` already have: the flag NAMES decide the
row (``O_WRONLY`` / ``O_RDWR`` / ``O_CREAT`` / ``O_TRUNC`` / ``O_APPEND`` write),
and a flag the producer cannot read keeps the first-declared ``fs_read`` row.
``close`` gets no row, by a declared decision the last test pins.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.io_boundary import load_catalog

_HEAD = "#include <fcntl.h>\n#include <stdlib.h>\n#include <unistd.h>\n\n"

#: (body, the boundary io-boundaries must report for ``fcntl.open``).
_CASES = {
    "write_flags": (
        "int w(const char *p) {\n    int fd = open(p, O_WRONLY | O_CREAT, 0644);\n"
        "    close(fd);\n    return 0;\n}\n",
        "fs_write",
    ),
    "read_only": (
        "int r(const char *p) {\n    int fd = open(p, O_RDONLY);\n"
        "    close(fd);\n    return 0;\n}\n",
        "fs_read",
    ),
    "unreadable_flags": (
        "int v(const char *p, int fl) {\n    int fd = open(p, fl);\n"
        "    close(fd);\n    return 0;\n}\n",
        "fs_read",
    ),
    "openat_write": (
        "int a(int d, const char *p) {\n    int fd = openat(d, p, O_RDWR);\n"
        "    close(fd);\n    return 0;\n}\n",
        "fs_write",
    ),
}


def _run(argv: list[str], cache: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(argv)
    return buf.getvalue()


def _chains(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, set[str]]:
    out = _run(["io-boundaries", str(repo), "--format", "json"], tmp_path / "cache", monkeypatch)
    return {
        b: {c["primitive"] for c in v["chains"]}
        for b, v in json.loads(out)["boundaries"].items()
    }


@pytest.mark.parametrize("case", sorted(_CASES))
def test_open_takes_its_boundary_from_its_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    body, boundary = _CASES[case]
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "f.c").write_text(_HEAD + body)
    chains = _chains(repo, tmp_path, monkeypatch)
    opened = {b for b, prims in chains.items() if prims & {"fcntl.open", "fcntl.openat"}}
    assert opened == {boundary}, chains
    # ``external_potential`` is the disclosure of an unclassified call, not a row.
    assert not any("close" in p for b, prims in chains.items()
                   if b != "external_potential" for p in prims), chains


def test_a_project_local_open_is_not_the_syscall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE CONTROL: no ``<fcntl.h>`` and a first-party ``open`` -- not I/O."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "f.c").write_text(
        "#include <stdlib.h>\n\nstatic int open(const char *p, int f) { return f; }\n\n"
        "int w(const char *p) {\n    return open(p, 1);\n}\n"
    )
    chains = _chains(repo, tmp_path, monkeypatch)
    assert not any("open" in p for prims in chains.values() for p in prims), chains


def test_a_secret_path_opened_for_writing_is_a_host_fs_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "f.c").write_text(
        _HEAD + "int w(void) {\n    char *p = getenv(\"LOG_PATH\");\n"
        "    return open(p, O_WRONLY | O_CREAT, 0644);\n}\n"
    )
    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "claims:\n  - id: C\n    text: t\n    constraint:\n      taint_flow:\n"
        "        source_taint: host_secret\n        prohibited_sink_zone: host_fs\n"
    )
    out = _run(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"],
               tmp_path / "cache", monkeypatch)
    (verdict,) = json.loads(out)["verdicts"]
    assert verdict["verdict"] == "violated", verdict["details"]
    assert any("fcntl.open" in e["sink_primitives"] for e in verdict["evidence"])


def test_every_ambiguous_name_has_a_row_or_a_declared_refusal() -> None:
    """``ambiguous_names`` without a row is a withheld match nothing could make.

    The names below are rowless ON PURPOSE, each with a dated note in c.yaml:
    ``bind``/``listen`` (INV-nular, WI-dosov) mint no source; ``close`` is
    descriptor lifecycle (WI-bulub; python's ``os.close`` precedent). A new
    rowless ambiguous name fails here until someone decides it.
    """
    catalog = load_catalog("c")
    rowed = {p.name for p in catalog.primitives}
    assert set(catalog.ambiguous_names) - rowed == {"bind", "listen", "close"}
