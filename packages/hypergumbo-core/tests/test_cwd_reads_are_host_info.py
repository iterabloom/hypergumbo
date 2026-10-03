# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading the working directory is a host DESCRIPTION read, in every
catalogue (INV-zufiz).

WHAT WAS WRONG. rust.yaml rowed ``std::env::current_dir`` under ``fs_read``
while python ``os.getcwd`` / ``pathlib.Path.cwd``, go ``os.Getwd``, c
``unistd.getcwd``, javascript ``process.cwd`` and elixir ``System.cwd`` are all
``host_info_read`` -- and while rust's own ``std::env`` neighbours
``current_exe``, ``temp_dir`` and ``home_dir`` are too. rust-src env.rs shows
``current_dir()`` is exactly ``os_imp::getcwd()``: the getcwd(2) syscall,
which returns process state and reads no file content. INV-tutar split
``host_info_read`` out of ``env_read`` for exactly this class ("host
description and user identity"); the row was set without a parity check.

THE SAME MECHANISM, SWEPT (LIVE rule 3) -- every row that resolves a path
against the working directory, enumerated over every shipped catalogue and
default overlay, not recalled:

* rust ``std::path::absolute``. Its own note said it was ``fs_read`` "to match
  the boundary already assigned to std::env::current_dir ... one fact, one
  kind". It follows current_dir, so it moves with it. python's spelling of the
  same operation, ``pathlib.Path.absolute``, is already ``host_info_read``.
* haskell ``System.Directory`` ``getCurrentDirectory`` and ``makeAbsolute``
  (``makeAbsolute = fmap normalise . absolutize``; ``absolutize`` prepends
  ``getCurrentDirectory`` to a relative path and touches no file). They sat in
  a fourteen-function ``fs_read`` block whose other members do stat or list
  the filesystem. ``canonicalizePath`` stays ``fs_read``: it resolves
  symlinks, which reads the filesystem, the same reason python keeps
  ``os.path.realpath``.
* The community overlay ``System.Directory.Extra`` re-exports
  ``System.Directory`` and its header says its rows carry "the same
  boundaries" as the base rows they mirror, so it moves too.

WHAT CHANGES DOWNSTREAM. ``fs_read`` derives no taint source;
``host_info_read`` derives ``host_description`` (``AUTO_SOURCE_LABEL_MAP``).
So each of these calls now STARTS a ``host_description`` flow it did not start
before (a ``host-description-no-network`` / ``-no-logging`` claim can now be
violated by it), and a ``must_not_exist`` ``fs_read`` claim is no longer
violated by a cwd read. No sink appears or disappears.
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

#: Every shipped row whose call reads the working directory (or resolves a
#: path against it without touching the file). Enumerated by name over every
#: catalogue and default overlay on 2026-10-03.
_CWD_READS = [
    ("rust", "std::env", "current_dir"),
    ("rust", "std::path", "absolute"),
    ("haskell", "System.Directory", "getCurrentDirectory"),
    ("haskell", "System.Directory", "makeAbsolute"),
    ("haskell", "System.Directory.Extra", "getCurrentDirectory"),
    ("haskell", "System.Directory.Extra", "makeAbsolute"),
    # The parity anchors: these were already right.
    ("python", "os", "getcwd"),
    ("python", "os", "getcwdb"),
    ("python", "pathlib.Path", "cwd"),
    ("python", "pathlib.Path", "absolute"),
    ("go", "os", "Getwd"),
    ("c", "unistd", "getcwd"),
    ("javascript", "process", "cwd"),
    ("elixir", "System", "cwd"),
    ("scala", "scala.util.Properties", "userDir"),
]


def _boundaries(lang: str, module: str, name: str) -> set[str]:
    catalog = load_catalog(lang)
    assert catalog is not None
    return {
        p.boundary for p in catalog.primitives
        if p.module == module and p.name == name
    }


@pytest.mark.parametrize("lang,module,name", _CWD_READS)
def test_a_cwd_read_is_host_info_read_in_every_catalogue(
    lang: str, module: str, name: str,
) -> None:
    assert _boundaries(lang, module, name) == {"host_info_read"}


@pytest.mark.parametrize("lang,module,name", [
    ("haskell", "System.Directory", "canonicalizePath"),
    ("haskell", "System.Directory", "doesFileExist"),
    ("haskell", "System.Directory.Extra", "canonicalizePath"),
    ("rust", "std::fs", "canonicalize"),
    ("rust", "std::fs", "read_to_string"),
])
def test_control_the_filesystem_reads_beside_them_stay_fs_read(
    lang: str, module: str, name: str,
) -> None:
    """canonicalize resolves symlinks -- a real filesystem read."""
    assert _boundaries(lang, module, name) == {"fs_read"}


@pytest.mark.parametrize("lang,module,name", [
    r for r in _CWD_READS if r[1] != "System.Directory.Extra"
])
def test_a_cwd_read_derives_a_host_description_source(
    lang: str, module: str, name: str,
) -> None:
    """At the layer the taint arm consumes (the overlay is checked above
    through load_catalog, which merges it; this helper reads the base dir)."""
    sources, _sinks, _amb = _derive_auto_imports_from_io_primitives(_CATALOG_DIR)
    labels = {s.taint_label for s in sources.get(lang, ())
              if s.module == module and s.name == name}
    assert labels == {"host_description"}


_HASKELL = '''module Main where

import System.Directory (getCurrentDirectory, makeAbsolute)

main :: IO ()
main = do
  c <- getCurrentDirectory
  a <- makeAbsolute "x"
  putStrLn c
  putStrLn a
'''

_RUST = '''use std::env;
use std::path;

fn main() {
    let cwd = env::current_dir().unwrap();
    let abs = path::absolute("x").unwrap();
    println!("{:?} {:?}", cwd, abs);
}
'''

_CLAIMS = '''claims:
  - id: NO-FS-READ
    text: This program never reads the filesystem.
    constraint:
      boundary: fs_read
      must_not_exist: true
  - id: DESCRIPTION-NO-LOGGING
    text: Host description is never written to a log.
    constraint:
      taint_flow:
        source_taint: host_description
        prohibited_sink_zone: logging
'''


def _run(tmp_path: Path, files: dict[str, str], argv: list[str]) -> str:
    repo = tmp_path / "repo"
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
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


def _chains(tmp_path: Path, files: dict[str, str]) -> dict[str, set[str]]:
    report = json.loads(_run(
        tmp_path, files,
        ["io-boundaries", "{repo}", "--format", "json", "--include-tests"],
    ))
    return {b: {c["primitive"] for c in v["chains"]}
            for b, v in report["boundaries"].items()}


def _verdicts(tmp_path: Path, files: dict[str, str]) -> dict[str, dict]:
    out = _run(tmp_path, files,
               ["verify-claims", "{repo}", "--claims", "{claims}",
                "--format", "json"])
    return {v["claim_id"]: v for v in json.loads(out)["verdicts"]}


def test_rust_cwd_reads_are_reported_as_host_info(tmp_path: Path) -> None:
    files = {"Cargo.toml": '[package]\nname = "x"\nversion = "0.1.0"\n',
             "src/main.rs": _RUST}
    chains = _chains(tmp_path, files)
    assert {"std::env.current_dir", "std::path.absolute"} <= chains.get(
        "host_info_read", set())
    assert "fs_read" not in chains, chains.get("fs_read")
    verdict = _verdicts(tmp_path, files)["NO-FS-READ"]
    assert verdict["verdict"] != "violated", verdict["details"]


def test_haskell_cwd_read_now_starts_a_host_description_flow(
    tmp_path: Path,
) -> None:
    """The source that APPEARS: before the move, getCurrentDirectory minted
    no source and this claim was confirmed over a program that prints the
    working directory."""
    files = {"Main.hs": _HASKELL}
    chains = _chains(tmp_path, files)
    assert {"System.Directory.getCurrentDirectory",
            "System.Directory.makeAbsolute"} <= chains.get(
                "host_info_read", set())
    assert "fs_read" not in chains, chains.get("fs_read")
    verdicts = _verdicts(tmp_path, files)
    assert verdicts["NO-FS-READ"]["verdict"] != "violated"
    flows = verdicts["DESCRIPTION-NO-LOGGING"]
    assert flows["verdict"] == "violated", flows["details"]
    # The NAME, not the module: the taint arm attributes a bare-name haskell
    # call to the community System.Directory.Extra twin ahead of the vouched
    # System.Directory row (a pre-existing substitution, filed separately;
    # io-boundaries above attributes it correctly).
    assert "getCurrentDirectory" in flows["details"]
