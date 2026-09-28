# SPDX-License-Identifier: AGPL-3.0-or-later
"""Three haskell.yaml rows declared a crossing the call does not perform.

A catalogue row must name the boundary its call actually crosses. Three
Haskell rows did not, each for its own reason:

* ``System.Exit`` [exitWith, exitFailure, exitSuccess, die] under ``env_write``
  (INV-dozuj). Exiting writes nothing an ``env_read`` could observe, and
  ``env_write`` derives a ``host_env`` taint SINK, so ``die msg`` read as a
  value reaching the host environment. The three exits cross nothing this
  vocabulary names; ``die`` is ``hPutStrLn stderr msg >> exitFailure``, so it
  writes to stderr and is ``logging``, like ``Debug.Trace``.
* ``Prelude.readIO`` under ``fs_read`` (INV-savuk). ``readIO :: Read a =>
  String -> IO a`` parses a String the caller already holds; the ``IO`` is the
  failure effect. A ``must_not_exist`` fs_read claim was violated by a parse.
* ``System.Info`` [os, arch, compilerName, compilerVersion] under
  ``host_info_read`` (INV-banid). All are constants fixed when GHC was built,
  and none performs a runtime read. They are values, so no call edge reached
  the row: it was wrong and inert.

The rows are removed rather than retagged, and ``System.Exit`` / ``System.Info``
gain completeness grants saying why, the precedent javascript.yaml set for
``process.exit`` ("process control, no boundary in this vocabulary"). A grant
makes the silence an examined negative, so a qualified call into either module
does not become an unexamined one.

Each removal is paired with a control from the same block that must still
classify, so the tests cannot pass by the catalogue going blind.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.io_boundary import load_catalog


@pytest.fixture(scope="module")
def haskell():
    return load_catalog("haskell")


def _boundaries(catalog, module: str, name: str) -> set[str]:
    return {p.boundary for p in catalog.primitives if p.module == module and p.name == name}


@pytest.mark.parametrize("name", ["exitWith", "exitFailure", "exitSuccess"])
def test_a_process_exit_is_not_rowed(haskell, name: str) -> None:
    assert _boundaries(haskell, "System.Exit", name) == set()


def test_die_is_a_stderr_write(haskell) -> None:
    assert _boundaries(haskell, "System.Exit", "die") == {"logging"}


def test_the_environment_writers_are_still_env_write(haskell) -> None:
    """Control: the block the exits were removed from."""
    for name in ("setEnv", "unsetEnv"):
        assert _boundaries(haskell, "System.Environment", name) == {"env_write"}


def test_read_io_is_not_rowed(haskell) -> None:
    assert _boundaries(haskell, "Prelude", "readIO") == set()


def test_the_readers_beside_read_io_still_classify(haskell) -> None:
    """Control: readFile reads a file; readLn reads stdin (it IS getLine >>= readIO)."""
    assert _boundaries(haskell, "Prelude", "readFile") == {"fs_read"}
    assert _boundaries(haskell, "Prelude", "readLn") == {"ipc_recv"}


@pytest.mark.parametrize("name", ["os", "arch", "compilerName", "compilerVersion"])
def test_a_build_time_constant_is_not_rowed(haskell, name: str) -> None:
    assert _boundaries(haskell, "System.Info", name) == set()


def test_the_runtime_host_reads_are_still_host_info_read(haskell) -> None:
    """Control: the block System.Info was removed from."""
    assert _boundaries(haskell, "System.Environment", "getProgName") == {"host_info_read"}
    assert _boundaries(haskell, "Data.Time.Clock", "getCurrentTime") == {"host_info_read"}


@pytest.mark.parametrize("module", ["System.Exit", "System.Info"])
def test_the_modules_are_examined_not_unexamined(haskell, module: str) -> None:
    assert haskell.module_io_is_enumerated(module)


_PROGRAM = '''module Main where

import System.Environment (getArgs, setEnv)
import System.Exit

parseIt :: String -> IO Int
parseIt s = readIO s

loadIt :: FilePath -> IO String
loadIt p = readFile p

bail :: IO ()
bail = do
  args <- getArgs
  die (head args)

quit :: IO ()
quit = do
  putStrLn "x"
  exitFailure

export :: String -> IO ()
export v = do
  setEnv "K" v
  exitWith (ExitFailure 2)
'''


@pytest.fixture(scope="module")
def chains(tmp_path_factory: pytest.TempPathFactory) -> dict[str, set[str]]:
    """boundary -> primitives, from the shipped ``io-boundaries`` command."""
    root = tmp_path_factory.mktemp("hs_rows")
    repo = root / "repo"
    repo.mkdir()
    (repo / "Main.hs").write_text(_PROGRAM)
    mp = pytest.MonkeyPatch()
    mp.setenv("XDG_CACHE_HOME", str(root / "cache"))
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = main(["io-boundaries", str(repo), "--format", "json", "--include-tests"])
    finally:
        mp.undo()
    assert rc == 0
    report = json.loads(buf.getvalue())
    return {b: {c["primitive"] for c in v["chains"]} for b, v in report["boundaries"].items()}


def test_the_program_reaches_the_catalogue(chains: dict[str, set[str]]) -> None:
    """Reach: without these the negative assertions below are vacuous."""
    assert "Prelude.readFile" in chains["fs_read"]
    assert "System.Environment.setEnv" in chains["env_write"]
    assert "System.Environment.getArgs" in chains["env_read"]


def test_no_exit_is_an_environment_write(chains: dict[str, set[str]]) -> None:
    assert chains["env_write"] == {"System.Environment.setEnv"}


def test_die_is_reported_as_logging(chains: dict[str, set[str]]) -> None:
    assert "System.Exit.die" in chains["logging"]


def test_a_parse_is_not_a_file_read(chains: dict[str, set[str]]) -> None:
    assert chains["fs_read"] == {"Prelude.readFile"}


_EXIT_ONLY = '''module Main where

import System.Exit

main :: IO ()
main = do
  n <- readIO "3" :: IO Int
  if n > 0 then exitWith (ExitFailure n) else exitSuccess
  exitFailure
'''

_CLAIMS = '''claims:
  - id: NO-ENV-WRITE
    text: This program never writes the process environment.
    constraint:
      boundary: env_write
      must_not_exist: true
  - id: NO-FS-READ
    text: This program never reads the filesystem.
    constraint:
      boundary: fs_read
      must_not_exist: true
'''


def test_the_claims_the_rows_used_to_falsify_are_no_longer_violated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At the FINDING level (ADR-0049 ruling 3): a program that only parses and
    exits was reported as writing the environment and reading a file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Main.hs").write_text(_EXIT_ONLY)
    (tmp_path / "claims.yaml").write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(tmp_path / "claims.yaml"),
              "--format", "json"])
    verdicts = {v["claim_id"]: v for v in json.loads(buf.getvalue())["verdicts"]}
    for claim_id in ("NO-ENV-WRITE", "NO-FS-READ"):
        assert verdicts[claim_id]["verdict"] != "violated", verdicts[claim_id]["details"]
