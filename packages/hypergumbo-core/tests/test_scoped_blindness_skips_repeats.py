# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-rusil's per-label blindness is computed once per LABEL (WI-dojuh).

The scoped no-taint-catalogue loop in ``cmd_verify_claims`` skips a claim with
no taint flow (a boundary claim) and a label already scoped by an earlier
claim. No core test reached that skip, so ``cli.py`` was below 100% in
core-package isolation from the day it landed (PR CI checks only a PR's
changed files, in a combined run). Two claims on one label must get one
answer, and a boundary claim beside them must not disturb it.
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
    subprocess.run(os.environ["CMD"], shell=True)
'''

_CLAIMS = '''claims:
  - id: NO-LAUNCH
    text: Never launches a program.
    constraint:
      boundary: subprocess
      must_not_exist: true
  - id: SECRET-EXEC-1
    text: A secret never reaches a launched command.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: subprocess
  - id: SECRET-EXEC-2
    text: The same question, asked again.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: subprocess
'''


def test_a_repeated_label_and_a_boundary_claim_share_one_scoping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(_APP)
    # A language with no taint catalogue, so the scoped census runs at all.
    (repo / "index.php").write_text("<?php echo 'x';\n")
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    verdicts = {v["claim_id"]: v for v in json.loads(buf.getvalue())["verdicts"]}
    assert verdicts["NO-LAUNCH"]["verdict"] == "violated"
    first, second = verdicts["SECRET-EXEC-1"], verdicts["SECRET-EXEC-2"]
    assert first["verdict"] == second["verdict"] == "violated"
    assert first["evidence_count"] == second["evidence_count"] == 1
