# SPDX-License-Identifier: AGPL-3.0-or-later
"""A community overlay seeded into your config home stays community (INV-lamap).

THE FILED REPRO, end to end. A repository calls ``requests.get``; the claim is
"never writes to the host filesystem" (``fs_write`` must not exist). The
``requests`` rows are community rows (``python-http-clients.yaml``), which may
make the call VISIBLE but may not make it count as EXAMINED, so the honest
verdict is inconclusive: nothing enumerates what ``requests`` does to the disk.

Measured on dev 4a89b020ff before the fix:

    empty config home                 inconclusive  rc 2
    after `hypergumbo init-catalogs`  confirmed     rc 0

``init-catalogs`` seeds copies of the shipped overlays into
``$XDG_CONFIG_HOME/hypergumbo/io_primitives.d/``; the copies keep
``provenance: community``, and the loader stamped a row unvouched only by the
directory its file sat in. ADR-0061 ruling 3: the tier comes from the line,
and deleting the line is how you vouch -- the control below.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main


def _run(argv: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
         config: Path) -> tuple[int, str, str]:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


def _verify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
            config: Path) -> tuple[int, str, str]:
    repo = tmp_path / "repo"
    if not repo.exists():
        repo.mkdir()
        (repo / "main.py").write_text(
            "import requests\n\n\ndef go(url):\n    return requests.get(url)\n"
        )
    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "claims:\n  - id: C\n    text: t\n    constraint:\n"
        "      boundary: fs_write\n      must_not_exist: true\n"
    )
    rc, out, err = _run(
        ["verify-claims", str(repo), "--claims", str(claims), "--format", "json"],
        tmp_path, monkeypatch, config,
    )
    (verdict,) = json.loads(out)["verdicts"]
    return rc, verdict["verdict"], err


def _seeded_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "config"
    rc, _out, _err = _run(["init-catalogs"], tmp_path, monkeypatch, config)
    assert rc == 0
    seeded = config / "hypergumbo" / "io_primitives.d" / "python-http-clients.yaml"
    assert "provenance: community" in seeded.read_text(), "reach: the seed"
    return config


def test_with_an_empty_home_the_call_is_not_examined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rc, verdict, _err = _verify(tmp_path, monkeypatch, tmp_path / "empty")
    assert (rc, verdict) == (2, "inconclusive")


def test_a_seeded_community_copy_does_not_license_a_clean_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE DEFECT: the seeded copies must read exactly as the originals do,
    and the run must name them and say how to vouch."""
    config = _seeded_home(tmp_path, monkeypatch)
    rc, verdict, err = _verify(tmp_path, monkeypatch, config)
    assert (rc, verdict) == (2, "inconclusive")
    assert "python-http-clients.yaml" in err
    assert "delete its `provenance: community` line" in err


def test_deleting_the_line_vouches_for_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE CONTROL: the owner's act of vouching. Every seeded python overlay
    that rows ``requests`` loses its line, and the rows now count."""
    config = _seeded_home(tmp_path, monkeypatch)
    for path in (config / "hypergumbo" / "io_primitives.d").glob("python-*.yaml"):
        path.write_text(path.read_text().replace("provenance: community\n", ""))
    rc, verdict, _err = _verify(tmp_path, monkeypatch, config)
    assert (rc, verdict) == (0, "confirmed")
