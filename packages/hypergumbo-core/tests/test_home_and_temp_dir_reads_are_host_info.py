# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asking for the home or temporary directory is a host DESCRIPTION read
(INV-zuhat).

WHAT WAS WRONG. haskell.yaml rowed ``System.Directory`` ``getHomeDirectory``
and ``getTemporaryDirectory`` inside a fourteen-function ``fs_read`` block.
Neither touches the filesystem on POSIX: ``getTemporaryDirectory`` looks up
``TMPDIR`` and falls back to the literal ``"/tmp"``; ``getHomeDirectory``
looks up ``HOME`` and falls back to ``getpwuid_r``. They LOOK like directory
queries and sit beside real ones (``listDirectory``, ``doesFileExist``), but
the directory they name is supplied by the environment, not found on disk --
module-granularity attribution.

WHY host_info_read AND NOT env_read. The item that filed this proposed
``env_read`` ("they read environment variables"). The shipped rulings say
otherwise, and a row's own prescribed fix is a claim like any other (LIVE
rule 2). INV-tutar split ``host_info_read`` out of ``env_read`` for "host
description and user identity", and every other catalogue already rows these
two questions there, although several of them read ``HOME`` / ``TMPDIR`` the
same way: python ``pathlib.Path.home`` / ``os.path.expanduser`` /
``tempfile.gettempdir``, go ``os.UserHomeDir``, rust ``std::env::home_dir`` /
``temp_dir``, javascript ``os.homedir`` / ``os.tmpdir``, elixir
``System.user_home`` / ``tmp_dir``, scala ``Properties.userHome`` /
``tmpDir``. The shipped ``host-description-no-network`` example claim names
"home directory" in its text. ``env_read`` would mint ``host_secret`` -- a
home directory is not a credential, which is the precision loss INV-tutar
measured (22.9% on host-secret claims) and fixed.

The community overlay ``System.Directory.Extra`` mirrors the base rows "under
the same boundaries", so it moves too.

WHAT CHANGES DOWNSTREAM. ``fs_read`` derives no taint source;
``host_info_read`` derives ``host_description``. Both calls now START a
``host_description`` flow, and an ``fs_read`` ``must_not_exist`` claim is no
longer violated by them. No sink appears or disappears.
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

#: Every shipped row whose call answers "where is the home / temporary
#: directory". Enumerated by name over every catalogue and default overlay on
#: 2026-10-03; the first four are the moved rows, the rest the parity anchors.
_WELL_KNOWN_DIR_READS = [
    ("haskell", "System.Directory", "getHomeDirectory"),
    ("haskell", "System.Directory", "getTemporaryDirectory"),
    ("haskell", "System.Directory.Extra", "getHomeDirectory"),
    ("haskell", "System.Directory.Extra", "getTemporaryDirectory"),
    ("python", "pathlib.Path", "home"),
    ("python", "os.path", "expanduser"),
    ("python", "tempfile", "gettempdir"),
    ("go", "os", "UserHomeDir"),
    ("rust", "std::env", "home_dir"),
    ("rust", "std::env", "temp_dir"),
    ("javascript", "os", "homedir"),
    ("javascript", "os", "tmpdir"),
    ("elixir", "System", "user_home"),
    ("elixir", "System", "tmp_dir"),
    ("scala", "scala.util.Properties", "userHome"),
    ("scala", "scala.util.Properties", "tmpDir"),
]


def _boundaries(lang: str, module: str, name: str) -> set[str]:
    catalog = load_catalog(lang)
    assert catalog is not None
    return {
        p.boundary for p in catalog.primitives
        if p.module == module and p.name == name
    }


@pytest.mark.parametrize("lang,module,name", _WELL_KNOWN_DIR_READS)
def test_a_well_known_directory_read_is_host_info_read(
    lang: str, module: str, name: str,
) -> None:
    assert _boundaries(lang, module, name) == {"host_info_read"}


@pytest.mark.parametrize("module", ["System.Directory", "System.Directory.Extra"])
@pytest.mark.parametrize("name", ["listDirectory", "doesFileExist", "getFileSize"])
def test_control_the_block_they_left_is_still_fs_read(module: str, name: str) -> None:
    assert _boundaries("haskell", module, name) == {"fs_read"}


@pytest.mark.parametrize("name", ["getHomeDirectory", "getTemporaryDirectory"])
def test_the_derived_source_is_description_not_secret(name: str) -> None:
    sources, _sinks, _amb = _derive_auto_imports_from_io_primitives(_CATALOG_DIR)
    labels = {s.taint_label for s in sources.get("haskell", ())
              if s.module == "System.Directory" and s.name == name}
    assert labels == {"host_description"}


_HASKELL = '''module Main where

import System.Directory (getHomeDirectory, getTemporaryDirectory)

main :: IO ()
main = do
  h <- getHomeDirectory
  t <- getTemporaryDirectory
  putStrLn h
  putStrLn t
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
  - id: SECRET-NO-LOGGING
    text: No environment secret is written to a log.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: logging
'''


def _run(tmp_path: Path, argv: list[str]) -> str:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "Main.hs").write_text(_HASKELL)
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


def test_a_program_printing_its_home_directory(tmp_path: Path) -> None:
    """At the FINDING level, through the shipped CLI: before the move this
    program 'read the filesystem' and printed nothing anyone had to know about;
    now it prints host description, and no secret."""
    report = json.loads(_run(
        tmp_path, ["io-boundaries", "{repo}", "--format", "json", "--include-tests"],
    ))
    chains = {b: {c["primitive"] for c in v["chains"]}
              for b, v in report["boundaries"].items()}
    assert {"System.Directory.getHomeDirectory",
            "System.Directory.getTemporaryDirectory"} <= chains.get(
                "host_info_read", set())
    assert "fs_read" not in chains, chains.get("fs_read")

    out = _run(tmp_path, ["verify-claims", "{repo}", "--claims", "{claims}",
                          "--format", "json"])
    verdicts = {v["claim_id"]: v for v in json.loads(out)["verdicts"]}
    assert verdicts["NO-FS-READ"]["verdict"] != "violated"
    flows = verdicts["DESCRIPTION-NO-LOGGING"]
    assert flows["verdict"] == "violated", flows["details"]
    # The NAME, not the module: see the note in test_cwd_reads_are_host_info
    # (the taint arm prefers the community System.Directory.Extra twin,
    # INV-tobur).
    assert "getHomeDirectory" in flows["details"]
    assert verdicts["SECRET-NO-LOGGING"]["verdict"] != "violated", (
        verdicts["SECRET-NO-LOGGING"]["details"]
    )
