# SPDX-License-Identifier: AGPL-3.0-or-later
"""The adjacent membership defects audit-findings 0021 filed and did not move.

``docs/audits/0021-logging-family.md`` Step 5 found, while inventorying the
``logging`` / ``ipc_send`` seam, rows whose boundary was chosen by what the
call is ABOUT rather than what it DOES at the call site. The axiom they are
held to is ADR-0050 §1 (ADR-0049 ruling 1): *a boundary value names what data
crosses the process boundary at this call site, in which direction -- not
what the program is thereby arranged to do later.*

Every boundary here auto-derives taint (``taint.AUTO_SOURCE_LABEL_MAP`` /
``AUTO_SINK_ZONE_MAP``), so each class pins three things, not one:

1. the ROW: what the shipped catalogue declares for the name;
2. the DERIVED taint half: which source label or sink zone the row mints, so
   "a pure relabel" and "a sink disappears" are tested claims rather than
   prose in the commit message;
3. REACH: the shipped ``io-boundaries`` command run on a one-file program
   that calls the name, with a CONTROL in the same file that must still be
   reported. A row nothing reaches can be argued about at length while
   minting nothing (the ``flask.Flask.run`` lesson), and a fixture that
   reaches nothing makes every "it is gone" assertion vacuously true -- so
   each fixture asserts its control first.

A DELETED row is not silent. In a module whose I/O surface the catalogue has
ENUMERATED (``module_completeness``), an unrowed call is an examined
negative; in any other named external module it is disclosed as
``external_potential``. Each deletion below says which, because "the chain
went away" and "the call is now declared pure" are different claims.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

import hypergumbo_core.io_boundary as iob
from hypergumbo_core.cli import main
from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.taint import _derive_auto_imports_from_io_primitives

_CATALOG_DIR = Path(iob.__file__).parent / "io_primitives"


def _rows(language: str, module: str, name: str) -> set[str]:
    """Every boundary the shipped catalogue (overlays included) declares."""
    return {
        p.boundary for p in load_catalog(language).primitives
        if p.module == module and p.name == name
    }


@pytest.fixture(scope="module")
def derived() -> tuple[dict, dict]:
    """``(sources, sinks)`` keyed ``language -> (module, name) -> {label|zone}``."""
    sources, sinks, _amb = _derive_auto_imports_from_io_primitives(_CATALOG_DIR)
    src: dict = {}
    snk: dict = {}
    for lang, entries in sources.items():
        for s in entries:
            src.setdefault(lang, {}).setdefault((s.module, s.name), set()).add(s.taint_label)
    for lang, entries in sinks.items():
        for s in entries:
            snk.setdefault(lang, {}).setdefault((s.module, s.name), set()).add(s.zone)
    return src, snk


def _chains(root: Path, name: str, text: str) -> dict[str, set[str]]:
    """Run the shipped ``io-boundaries`` on a one-file repository."""
    repo = root / "repo"
    repo.mkdir()
    (repo / name).write_text(text)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("XDG_CACHE_HOME", str(root / "cache"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = main(["io-boundaries", str(repo), "--format", "json", "--include-tests"])
    assert rc == 0
    report = json.loads(buf.getvalue())
    return {b: {c["primitive"] for c in v["chains"]} for b, v in report["boundaries"].items()}


def _every_primitive(chains: dict[str, set[str]]) -> set[str]:
    return set().union(*chains.values()) if chains else set()


# ---------------------------------------------------------------------------
# WI-ragaz: logging rows that write nothing at the call.
# ---------------------------------------------------------------------------

_PY_LOGGING = '''import logging


def setup() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.StreamHandler()
    logging.FileHandler("app.log")
    logging.info("ready")
'''

_GO_SLOG = '''package main

import (
	"log/slog"
	"os"
)

func main() {
	h := slog.NewTextHandler(os.Stderr, nil)
	j := slog.NewJSONHandler(os.Stdout, nil)
	_, _ = h, j
	slog.Info("ready")
}
'''

_EX_LOGGER = '''defmodule App do
  require Logger

  def setup(user) do
    Logger.configure(level: :info)
    Logger.metadata(user: user)
    Logger.info("ready")
  end
end
'''


class TestWiRagazConfiguringAFacilityWritesNothing:
    """Constructing or configuring a logging facility writes nothing at the
    call; the write happens at a later call, which is rowed (the erlang
    ``io_lib`` precedent: adjacency is not a crossing, and the represented
    later call is what licenses the removal under ADR-0049 ruling 3)."""

    @pytest.mark.parametrize(("language", "module", "name"), [
        ("python", "logging", "basicConfig"),
        ("python", "logging", "StreamHandler"),
        ("go", "log/slog", "NewTextHandler"),
        ("go", "log/slog", "NewJSONHandler"),
        ("elixir", "Logger", "metadata"),
        ("elixir", "Logger", "configure"),
    ])
    def test_the_row_is_gone(self, language: str, module: str, name: str) -> None:
        assert _rows(language, module, name) == set()

    @pytest.mark.parametrize(("language", "module", "name", "boundary"), [
        ("python", "logging", "info", "logging"),
        ("python", "logging", "FileHandler", "fs_write"),
        ("go", "log/slog", "Info", "logging"),
        ("elixir", "Logger", "info", "logging"),
    ])
    def test_control_the_writing_siblings_stay(
        self, language: str, module: str, name: str, boundary: str,
    ) -> None:
        assert _rows(language, module, name) == {boundary}

    @pytest.mark.parametrize(("language", "module", "name"), [
        ("python", "logging", "basicConfig"),
        ("python", "logging", "StreamHandler"),
        ("go", "log/slog", "NewTextHandler"),
        ("go", "log/slog", "NewJSONHandler"),
        ("elixir", "Logger", "metadata"),
        ("elixir", "Logger", "configure"),
    ])
    def test_no_logging_sink_is_derived(
        self, derived, language: str, module: str, name: str,
    ) -> None:
        _src, sinks = derived
        assert (module, name) not in sinks[language]

    def test_python_logging_stays_an_enumerated_module(self) -> None:
        """So an unrowed ``basicConfig`` is an EXAMINED negative, not a hole."""
        assert load_catalog("python").module_io_is_enumerated("logging")

    def test_python_reach(self, tmp_path: Path) -> None:
        chains = _chains(tmp_path, "setup.py", _PY_LOGGING)
        assert chains["logging"] == {"logging.info"}  # control: reached
        assert chains["fs_write"] == {"logging.FileHandler"}  # control: reached
        assert not {"logging.basicConfig", "logging.StreamHandler"} & _every_primitive(chains)

    def test_go_reach(self, tmp_path: Path) -> None:
        chains = _chains(tmp_path, "main.go", _GO_SLOG)
        assert "log/slog.Info" in chains["logging"]  # control: reached
        assert not {"log/slog.NewTextHandler", "log/slog.NewJSONHandler"} & chains["logging"]
        # log/slog is not an enumerated module: the call is disclosed, not dropped.
        assert {"log/slog.NewTextHandler", "log/slog.NewJSONHandler"} <= chains[
            "external_potential"]

    def test_elixir_reach(self, tmp_path: Path) -> None:
        chains = _chains(tmp_path, "app.ex", _EX_LOGGER)
        assert chains["logging"] == {"Logger.info"}  # control: reached
        assert {"Logger.metadata", "Logger.configure"} <= chains["external_potential"]
