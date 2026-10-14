# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-fogum on the production path: a Go server launch qualifies a clean
``untrusted_input`` taint verdict, in the same run that qualifies the boundary
verdict.

The core file ``test_deferred_crossing_taint_arm.py`` pins the rule over
captured edges. This one runs the item's own repro through ``verify-claims``
with the real Go analyzer and the shipped catalogue, because a rule shown only
on hand-built inputs is not shown to fire (WI-simiv's lesson). Before the fix
the same program gave::

    untrusted-input-no-host-fs   confirmed               caveats []
    no-net-recv                  confirmed_with_caveats  [deferred_crossing]
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_SERVER = """package main

import (
	"net/http"
	"os"
)

type app struct{}

func (app) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	os.WriteFile("/var/data/"+r.URL.Path, []byte("x"), 0600)
}

func main() { http.ListenAndServe(":8080", app{}) }
"""

_CLAIMS = """claims:
  - id: untrusted-no-fs
    text: Network input never chooses what is written to disk.
    constraint:
      taint_flow:
        source_taint: untrusted_input
        prohibited_sink_zone: host_fs
  - id: secret-no-fs
    text: Environment secrets never reach the filesystem.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: host_fs
  - id: no-net-recv
    text: Never receives data from the network.
    constraint:
      boundary: net_recv
      must_not_exist: true
"""


def _verdicts(tmp_path: Path, capsys: pytest.CaptureFixture[str],
              monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.go").write_text(_SERVER)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    return {v["claim_id"]: v for v in json.loads(capsys.readouterr().out)["verdicts"]}


def _deferred(verdict: dict) -> list[dict]:
    return [c for c in verdict.get("caveats", []) if c["kind"] == "deferred_crossing"]


def test_both_arms_disclose_the_launch_in_one_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdicts = _verdicts(tmp_path, capsys, monkeypatch)

    # Reach: the boundary arm sees the launch, so the shadow is populated.
    [boundary_cav] = _deferred(verdicts["no-net-recv"])
    assert boundary_cav["entries"] == ["net/http.ListenAndServe"]

    taint = verdicts["untrusted-no-fs"]
    assert taint["verdict"] == "confirmed_with_caveats", taint
    [cav] = _deferred(taint)
    assert (cav["boundary"], cav["arm"], cav["entries"]) == (
        "net_recv", "taint", ["net/http.ListenAndServe"],
    )

    # Scope: a listener says nothing about whether a secret reaches the disk.
    assert _deferred(verdicts["secret-no-fs"]) == []
