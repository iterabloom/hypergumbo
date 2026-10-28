# SPDX-License-Identifier: AGPL-3.0-or-later
"""A sink the project declares trusted is excluded AND disclosed (WI-lukoz).

``TaintSink.trust_level`` was documented as the knob a project turns for "a
sink that is safe in context" -- docs/hypergumbo-spec.md said so twice, and the
``verify-claims`` implementation's docstring once -- and nothing read it.
Measured before this change: a project sink catalogue declaring
``subprocess.run`` with ``trust_level: trusted`` left a host_secret ->
subprocess verdict ``violated`` with the same one flow, while the same entry
with a different ZONE moved the verdict, so the override was applied and the
level alone did nothing.

A trusted sink's flows now leave ``evidence_count`` and are counted in
``trusted_sink_flows``, the ``resource_naming_flows`` shape (WI-bulag): a true
flow set aside by a declaration the project made, said so in the sentence on
the path where silence would mislead. ``untrusted`` and every other value
behave as before.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_APP = '''import os
import subprocess


def go():
    cmd = os.environ["CMD"]
    subprocess.run(cmd, shell=True)
'''

_CLAIMS = '''claims:
  - id: SECRET-NOT-EXEC
    text: A secret never reaches a launched command.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: subprocess
'''


def _verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trust: str | None,
    app: str = _APP,
) -> dict:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(app)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    argv = ["verify-claims", str(repo), "--claims", str(claims), "--format", "json"]
    if trust is not None:
        sinks = tmp_path / "sinks.yaml"
        sinks.write_text(
            f"description: vetted launcher\nzone: subprocess\ntrust_level: {trust}\n"
            "sinks:\n  python:\n    - module: subprocess\n      functions: [run]\n"
        )
        argv += ["--taint-sinks", str(sinks)]
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(argv)
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict


def test_a_trusted_sink_moves_the_verdict_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdict = _verdict(tmp_path, monkeypatch, "trusted")
    assert verdict["verdict"] in ("confirmed", "confirmed_with_caveats")
    assert verdict["evidence_count"] == 0
    assert verdict["trusted_sink_flows"] == 1
    assert "trusted" in verdict["details"]


@pytest.mark.parametrize("trust", [None, "untrusted", "semi-trusted"])
def test_any_other_level_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trust: str | None,
) -> None:
    verdict = _verdict(tmp_path, monkeypatch, trust)
    assert verdict["verdict"] == "violated"
    assert verdict["evidence_count"] == 1
    assert verdict["trusted_sink_flows"] == 0


_TWO_LAUNCHES = '''import os
import subprocess


def launch(cmd):
    subprocess.run(["true"])
    subprocess.Popen(cmd, shell=True)


def go():
    launch(os.environ["CMD"])
'''


def test_a_trusted_sink_does_not_clear_an_untrusted_one_beside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WI-gajir. ``go`` reads the secret and reaches TWO launchers, only one
    of them declared trusted. Both flows are call-reachability-only, so they
    collapse into one situation; before the fix the group took
    ``trusted_sink`` from its first member (``subprocess.run``) and the claim
    read ``confirmed_with_caveats`` with 0 evidence, although the secret
    reaches ``subprocess.Popen``, which nobody vouched for."""
    verdict = _verdict(tmp_path, monkeypatch, "trusted", app=_TWO_LAUNCHES)
    assert verdict["verdict"] == "violated"
    assert verdict["evidence_count"] == 1
    assert verdict["evidence"][0]["sink_primitives"] == ["subprocess.Popen"]
    assert verdict["trusted_sink_flows"] == 1


_SHIP_APP = '''import os

import mylib


def go():
    mylib.ship(os.environ["CMD"])
'''


def _community_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provenance: str) -> dict:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(_SHIP_APP)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    sinks = tmp_path / "sinks.yaml"
    sinks.write_text(
        f"description: a launcher row\n{provenance}zone: subprocess\ntrust_level: trusted\n"
        "sinks:\n  python:\n    - module: mylib\n      functions: [ship]\n"
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json",
              "--taint-sinks", str(sinks)])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict


def test_a_community_files_trust_is_withheld_and_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0061 ruling 2 (WI-savoh): trust only takes flows out of the
    evidence, so a community row may not grant it. The flow is reported and the
    verdict names the sink whose trust was withheld."""
    verdict = _community_verdict(tmp_path, monkeypatch, "provenance: community\n")
    assert verdict["verdict"] == "violated"
    assert verdict["trusted_sink_flows"] == 0
    (caveat,) = [c for c in verdict["caveats"] if c["kind"] == "withheld_community_trust"]
    assert caveat["entries"] == ["mylib.ship"]


def test_control_the_same_row_vouched_for_is_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdict = _community_verdict(tmp_path, monkeypatch, "")
    assert verdict["verdict"] != "violated"
    assert verdict["trusted_sink_flows"] == 1
    assert "withheld_community_trust" not in {c["kind"] for c in verdict["caveats"]}
