# SPDX-License-Identifier: AGPL-3.0-or-later
"""An import edge is not a call site for a FUNCTION row (INV-lagir).

``tag_io_boundaries`` counts ``imports`` edges among its call types, and has
since the tagger was written. A Python import's dst is the imported name, so
``import glob`` (dst ``python:glob:0-0:glob``) has the same (module, name) as
the catalogued FUNCTION ``glob.glob`` and matched it. A file containing only
``import glob`` was an ``fs_read`` chain, and failed a
``{boundary: fs_read, must_not_exist: true}`` claim: violated, rc 1, for a
program that performs no I/O. On hypergumbo's own survey 158 import edges were
tagged, 133 of them ``import time`` as ``host_info_read``.

WHY NOT DROP ``imports`` FROM THE TAGGER. Measured before changing it: after
``from os import environ`` or ``from sys import argv``, the USE
(``environ["K"]``, ``argv[1]``) emits no edge at all, so the import edge is
the only thing that makes those reads visible. Those are ATTRIBUTE rows (read,
not called). A FUNCTION row has a call site of its own: ``from subprocess
import run`` then ``run(cmd)`` emits a ``calls`` edge, and the import was a
second, file-level chain for the same call. So an import may match an
attribute row and never a function or method row.

The only residual is a function imported and then used without a call (passed
as a callback): that shape now produces no chain.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main


def _run(argv: list[str], cache: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(argv)
    return buf.getvalue()


def _chains(tmp_path: Path, files: dict[str, str],
            monkeypatch: pytest.MonkeyPatch) -> set[tuple[str, str, str]]:
    """(boundary, primitive, source symbol name) from the shipped command."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, text in files.items():
        (repo / name).write_text(text)
    out = _run(["io-boundaries", str(repo), "--format", "json", "--include-tests"],
               tmp_path / "cache", monkeypatch)
    return {
        (b, c["primitive"], c["io_edge_src"].split(":")[-2])
        for b, v in json.loads(out)["boundaries"].items() for c in v["chains"]
    }


_IMPORTS_ONLY = "import glob\nimport time\nimport platform\nimport pprint\nimport fcntl\n"


def test_importing_a_module_named_like_its_function_is_no_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _chains(tmp_path, {"m.py": _IMPORTS_ONLY}, monkeypatch) == set()


_FROM_IMPORTS = '''from os import environ
from sys import argv
from subprocess import run
from urllib.request import urlopen
import glob


def a():
    return environ["K"]


def c():
    return argv[1]


def d(cmd):
    return run(cmd)


def e(u):
    return urlopen(u)


def g():
    return glob.glob("*")
'''


def test_an_imported_attribute_is_still_a_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control: the use of an imported attribute emits no edge, so the import
    is what makes the read visible. It must stay."""
    chains = _chains(tmp_path, {"m.py": _FROM_IMPORTS}, monkeypatch)
    assert ("env_read", "os.environ", "file") in chains
    assert ("env_read", "sys.argv", "file") in chains


def test_an_imported_function_is_reported_where_it_is_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    chains = _chains(tmp_path, {"m.py": _FROM_IMPORTS}, monkeypatch)
    assert ("subprocess", "subprocess.run", "d") in chains
    assert ("net_send", "urllib.request.urlopen", "e") in chains
    assert ("fs_read", "glob.glob", "g") in chains
    at_file = {(b, p) for b, p, src in chains if src == "file"}
    assert at_file == {("env_read", "os.environ"), ("env_read", "sys.argv")}


def test_an_import_no_longer_fails_a_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The finding the item was filed on: violated, rc 1, for no I/O."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text(_IMPORTS_ONLY)
    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "claims:\n"
        "  - id: NO-FS-READ\n"
        "    text: This code never reads the filesystem.\n"
        "    constraint:\n"
        "      boundary: fs_read\n"
        "      must_not_exist: true\n"
    )
    out = _run(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"],
               tmp_path / "cache", monkeypatch)
    (verdict,) = json.loads(out)["verdicts"]
    assert verdict["verdict"] != "violated", verdict["details"]
