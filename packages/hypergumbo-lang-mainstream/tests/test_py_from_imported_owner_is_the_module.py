# SPDX-License-Identifier: AGPL-3.0-or-later
"""A from-imported owner belongs in the module slot (WI-sugom, WI-torin).

``from urllib import request`` then ``request.urlopen(u)`` was emitted as
module ``urllib``, name ``request.urlopen``: the owner's last segment sat in
the NAME slot. ADR-0051 says the module slot names the owner path, and the
plain-import spelling of the same call (``import urllib.request``) already
emits module ``urllib.request``, name ``urlopen``.

The misplaced segment broke both consumers of the slot, and each failure was a
clean verdict over code that performs the I/O:

* CLASSIFICATION. No catalogue row is keyed on a dotted name, so the call
  matched nothing: ``request.urlopen``, ``path.exists``, ``Path.cwd``,
  ``date.today``. ``datetime.now()`` matched only because the class shares its
  module's name (INV-januj's retry drops a qualifier that repeats the module).
* THE COVERAGE GATE. An unmatched call is an examined negative only over an
  ENUMERATED module, and the gate asked about the slot: ``urllib`` (granted)
  rather than ``urllib.request``, ``logging`` rather than ``logging.handlers``
  (not granted). python.yaml's grant header forbids exactly that reading.

The analyzer cannot tell a submodule from a class or an object imported by
name, and does not need to: in every case ``P.X`` is the path the owner is
reachable at, which is what the slot means.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_lang_mainstream.py import analyze_python


def _dsts(tmp_path: Path, src: str) -> set[str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(src)
    result = analyze_python(repo)
    return {
        e.dst.split(":", 1)[1].rsplit(":", 1)[0]
        for e in result.edges if e.edge_type == "calls"
    }


def test_a_from_imported_submodule_is_the_module(tmp_path: Path) -> None:
    dsts = _dsts(tmp_path, "from urllib import request\n\n\ndef f(u):\n"
                           "    return request.urlopen(u)\n")
    assert "urllib.request:0-0:urlopen" in dsts
    assert not any(".urlopen" in d.split(":")[2] for d in dsts)


def test_a_from_imported_class_is_the_module(tmp_path: Path) -> None:
    dsts = _dsts(tmp_path, "from pathlib import Path\n\n\ndef f():\n"
                           "    return Path.cwd()\n")
    assert "pathlib.Path:0-0:cwd" in dsts


def test_the_dst_ref_agrees_with_the_id(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("from datetime import date\n\n\ndef f():\n"
                                 "    return date.today()\n")
    (edge,) = [e for e in analyze_python(repo).edges
               if e.edge_type == "calls" and "today" in e.dst]
    assert edge.dst_ref is not None
    assert (edge.dst_ref.module_path, edge.dst_ref.name) == ("datetime.date", "today")


def _verdict(tmp_path: Path, name: str, src: str, boundary: str) -> str:
    repo = tmp_path / name
    repo.mkdir()
    (repo / "app.py").write_text(src)
    claims = tmp_path / f"{name}.yaml"
    claims.write_text(
        "claims:\n  - id: C\n    text: t\n    constraint:\n"
        f"      boundary: {boundary}\n      must_not_exist: true\n"
    )
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict["verdict"]


_ROWED = {
    # name: (source, boundary) -- each was a clean ``confirmed``
    "urlopen": ("from urllib import request\n\n\ndef f(u):\n    return request.urlopen(u)\n",
                "net_send"),
    "etree": ("from xml.etree import ElementTree\n\n\ndef f(p):\n    return ElementTree.parse(p)\n",
              "fs_read"),
    "os_path": ("from os import path\n\n\ndef f(p):\n    return path.exists(p)\n", "fs_read"),
    "metadata": ("from importlib import metadata\n\n\ndef f():\n    return metadata.version('x')\n",
                 "fs_read"),
    "path_cwd": ("from pathlib import Path\n\n\ndef f():\n    return Path.cwd()\n",
                 "host_info_read"),
    "date_today": ("from datetime import date\n\n\ndef f():\n    return date.today()\n",
                   "host_info_read"),
    # control: matched before, by the coincidence the docstring names
    "datetime_now": ("from datetime import datetime\n\n\ndef f():\n    return datetime.now()\n",
                     "host_info_read"),
}


@pytest.mark.parametrize("name", list(_ROWED))
def test_a_rowed_member_fails_the_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    src, boundary = _ROWED[name]
    assert _verdict(tmp_path, name, src, boundary) == "violated"


_UNAUDITED = {
    # an unrowed member of a submodule nobody audited, under a granted parent
    "handlers": ("from logging import handlers\n\n\ndef f(p):\n"
                 "    return handlers.RotatingFileHandler(p)\n", "fs_write"),
    "sax": ("from xml import sax\n\n\ndef f(p, h):\n    return sax.parse(p, h)\n", "fs_read"),
}


@pytest.mark.parametrize("name", list(_UNAUDITED))
def test_a_parent_grant_does_not_vouch_for_its_submodule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    """The plain-import spelling of each was already inconclusive."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    src, boundary = _UNAUDITED[name]
    assert _verdict(tmp_path, name, src, boundary) == "inconclusive"
