# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-kozaj: the stdlib ``csv`` write surface is rowed where the data goes in.

``python.yaml`` declares ``csv`` ``completeness: complete``. The write surface
is three types, read from CPython 3.12's ``csv.py`` / ``_csv.c``:

* ``csv.writer(f)`` -- a builtin FUNCTION returning a ``_csv.Writer``; the data
  enters at ``Writer.writerow`` / ``writerows``.
* ``csv.DictWriter(f, fieldnames)`` -- a Python CLASS; the data enters at
  ``writerow`` / ``writerows``, and ``writeheader`` writes the field names.

Each write goes INTO an object the caller opened, so it is rowed ``fs_write``
under the ``json.dump`` / ``pickle.dump`` precedent; the reads (``reader``,
``DictReader``) take a caller-opened object and stay unrowed, tomllib's rule.

DEFECT 1, THE DictWriter HALF. ``csv.DictWriter`` carried no row. The analyzer
already types the instance (``dw.writerow`` arrives as module
``csv.DictWriter``), so nothing classified the write, and the coverage gate
withheld every verdict over a program that used it: a ``host_secret -> host_fs``
claim read ``inconclusive``, "could not classify (csv.DictWriter)", although the
secret visibly reached the file. ``module_io_is_enumerated`` matches the slot
exactly, so the ``csv: complete`` grant never covered ``csv.DictWriter``; the
loss was the finding, in the safe direction.

Every assertion here runs on REAL analyzer output and asserts reach first.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import _rehydrate_io_boundary_edges, main
from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_core.taint import load_builtin_taint_catalog

_DICTWRITER = (
    "import csv\n"
    "import os\n"
    "\n"
    "\n"
    "def dump(f):\n"
    "    token = os.environ['API_KEY']\n"
    "    dw = csv.DictWriter(f, fieldnames=['a'])\n"
    "    dw.writeheader()\n"
    "    dw.writerow({'a': token})\n"
    "    dw.writerows([{'a': token}])\n"
)

_DICTWRITER_FROM = (
    "import os\n"
    "from csv import DictWriter\n"
    "\n"
    "\n"
    "def dump(f):\n"
    "    token = os.environ['API_KEY']\n"
    "    DictWriter(f, fieldnames=['a']).writerow({'a': token})\n"
)

_CLAIMS = """claims:
  - id: secret-no-host-fs
    text: An environment secret is never written to the filesystem.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: host_fs
"""

_DICTWRITER_METHODS = ("writerow", "writerows", "writeheader")


def _rows(module: str) -> dict[str, tuple[str, str]]:
    return {p.name: (p.boundary, p.kind)
            for p in load_catalog("python").primitives if p.module == module}


def _tagged(tmp_path: Path, source: str) -> dict:
    """``{(caller, callee module, callee name): (boundary, primitive)}``."""
    from hypergumbo_lang_mainstream.py import analyze_python

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(source)
    raw = [e.to_dict() for e in analyze_python(repo).edges]
    edges = _rehydrate_io_boundary_edges(raw)
    tag_io_boundaries(edges, {"python": load_catalog("python")})
    out = {}
    for e in edges:
        if e.edge_type != "calls":
            continue
        parts = e.dst.split(":")
        meta = e.meta or {}
        out[(e.src.split(":")[-2], parts[1], parts[-2])] = (
            meta.get("io_boundary"), meta.get("io_primitive"))
    return out


def _verify(tmp_path: Path, source: str, monkeypatch: pytest.MonkeyPatch) -> dict:
    """The one verdict of the shipped ``verify-claims`` over ``source``."""
    repo = tmp_path / "vrepo"
    repo.mkdir()
    (repo / "app.py").write_text(source)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict


class TestDictWriterIsRowed:

    def test_the_write_surface_is_fs_write_and_method_kind(self) -> None:
        """ADR-0059: ``dw.writerow(...)`` is called on an INSTANCE of
        ``csv.DictWriter``, so the rows are ``methods:``."""
        rows = _rows("csv.DictWriter")
        for name in _DICTWRITER_METHODS:
            assert rows.get(name) == ("fs_write", "method"), (name, rows)

    @pytest.mark.parametrize("name", _DICTWRITER_METHODS)
    def test_each_method_is_a_host_fs_sink(self, name: str) -> None:
        sinks = {(s.module, s.name): s.zone
                 for s in load_builtin_taint_catalog().sinks_for_language("python")}
        assert sinks.get(("csv.DictWriter", name)) == "host_fs"

    @pytest.mark.parametrize("name", _DICTWRITER_METHODS)
    def test_the_analyzed_call_classifies(self, tmp_path: Path, name: str) -> None:
        tagged = _tagged(tmp_path, _DICTWRITER)
        site = ("dump", "csv.DictWriter", name)
        assert site in tagged, sorted(tagged)  # reach: the instance is typed
        assert tagged[site] == ("fs_write", f"csv.DictWriter.{name}")

    def test_the_from_import_chain_root_classifies(self, tmp_path: Path) -> None:
        tagged = _tagged(tmp_path, _DICTWRITER_FROM)
        site = ("dump", "csv.DictWriter", "writerow")
        assert site in tagged, sorted(tagged)  # reach
        assert tagged[site] == ("fs_write", "csv.DictWriter.writerow")


class TestTheFindingIsReported:
    """The item's repro at the surface a user reads."""

    def test_a_secret_written_through_dictwriter_is_violated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Was ``inconclusive``: "could not classify (csv.DictWriter)"."""
        verdict = _verify(tmp_path, _DICTWRITER, monkeypatch)
        assert verdict["verdict"] == "violated", verdict["details"]
        sinks = {p for ev in verdict["evidence"] for p in ev["sink_primitives"]}
        assert sinks & {f"csv.DictWriter.{n}" for n in _DICTWRITER_METHODS}, sinks

    def test_control_a_dictwriter_with_no_secret_is_confirmed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The gate admits the program: the rows make the slot examined, and a
        clean program reads clean rather than inconclusive."""
        source = _DICTWRITER.replace("os.environ['API_KEY']", "'constant'")
        verdict = _verify(tmp_path, source, monkeypatch)
        assert verdict["verdict"].startswith("confirmed"), verdict["details"]
