# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file in your taint channels changes the verdict, and the verdict names it (WI-mimap).

Before: ``$XDG_CONFIG_HOME/hypergumbo/taint_sources.d/`` and
``taint_sanitizers.d/`` were created by ``init-catalogs`` and read by nothing,
and ``taint_sinks.d/`` did not exist -- a file dropped in any of them had no
effect and raised no error. ADR-0061 ruling 7 makes them the operator's one
persistent home for a taint model; ruling 5 lists them under ``yours``.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    repo.mkdir()
    # Source and sink on separate lines (INV-muhij ordering).
    (repo / "main.py").write_text(
        "import crm\nimport subprocess\n\n\ndef run(cid):\n"
        "    who = crm.lookup(cid)\n"
        '    subprocess.run(["echo", who])\n'
    )
    return tmp_path / "config" / "hypergumbo"


def _claim(tmp_path: Path) -> Path:
    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "claims:\n  - id: C\n    text: t\n    constraint:\n      taint_flow:\n"
        "        source_taint: pii\n        prohibited_sink_zone: subprocess\n"
    )
    return claims


def _verify(home: Path) -> tuple[int, dict]:
    root = home.parent.parent
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        rc = main(["verify-claims", str(root / "repo"), "--claims",
                   str(_claim(root)), "--format", "json"])
    return rc, json.loads(out.getvalue())


def _channel(home: Path, family: str, name: str, body: str) -> Path:
    d = home / f"{family}.d"
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    path.write_text(body)
    return path


_SOURCE = ("taint_label: pii\nsources:\n  python:\n"
           "    - module: crm\n      functions: [lookup]\n")


def test_a_source_in_your_channel_makes_the_flow_visible(home: Path) -> None:
    src = _channel(home, "taint_sources", "crm.yaml", _SOURCE)
    rc, doc = _verify(home)
    (verdict,) = doc["verdicts"]
    assert (rc, verdict["verdict"]) == (1, "violated")
    yours = doc["catalog_provenance"]["tiers"]["yours"]
    assert {"family": "taint_sources", "path": str(src)} in yours


def test_a_sanitizer_in_your_channel_clears_it_and_says_whose_word_it_was(
    home: Path,
) -> None:
    (home.parent.parent / "repo" / "main.py").write_text(
        "import crm\nimport subprocess\n\n\ndef run(cid):\n"
        "    who = crm.lookup(cid)\n"
        "    safe = crm.scrub(who)\n"
        '    subprocess.run(["echo", safe])\n'
    )
    _channel(home, "taint_sources", "crm.yaml", _SOURCE)
    _channel(home, "taint_sanitizers", "scrub.yaml",
             "transforms:\n  - input_taint: pii\n    output_taint: redacted\n"
             "    functions:\n      python:\n        - crm.scrub\n")
    rc, doc = _verify(home)
    (verdict,) = doc["verdicts"]
    # The sanitizer removed the flow and is credited by name; the verdict is
    # not clean only because nothing classifies ``crm`` itself.
    assert (rc, verdict["verdict"]) == (2, "inconclusive")
    assert verdict["sanitized_flows"] == 1
    assert [(c["kind"], c["entries"]) for c in verdict["caveats"]] == [
        ("user_supplied_sanitizer", ["crm.scrub"]),
    ]


def test_a_sink_in_your_channel_is_read(home: Path, tmp_path: Path) -> None:
    """taint_sinks.d exists now: a project sink zone declared there is
    matched like a --taint-sinks one."""
    _channel(home, "taint_sources", "crm.yaml", _SOURCE)
    _channel(home, "taint_sinks", "audit.yaml",
             "zone: audit_log\ntrust_level: untrusted\nsinks:\n  python:\n"
             "    - module: subprocess\n      functions: [run]\n")
    claims = tmp_path / "claims.yaml"
    out = io.StringIO()
    claims_text = (
        "claims:\n  - id: C\n    text: t\n    constraint:\n      taint_flow:\n"
        "        source_taint: pii\n        prohibited_sink_zone: audit_log\n"
    )
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        claims.write_text(claims_text)
        rc = main(["verify-claims", str(tmp_path / "repo"), "--claims",
                   str(claims), "--format", "json"])
    (verdict,) = json.loads(out.getvalue())["verdicts"]
    assert (rc, verdict["verdict"]) == (1, "violated")


def test_with_nothing_in_the_channels_the_label_is_unknown(home: Path) -> None:
    """THE CONTROL: the same claim with no channel file names a label no
    catalogue declares, which is refused rather than silently confirmed."""
    root = home.parent.parent
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(["verify-claims", str(root / "repo"), "--claims",
                   str(_claim(root)), "--format", "json"])
    assert rc == 2
    assert "pii" in err.getvalue()
