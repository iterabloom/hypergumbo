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

After INV-fogum the taint verdict was ``confirmed_with_caveats``. Since
WI-jonin the handler, ``app.ServeHTTP``, is a request-ingress entrypoint, so
WI-fuzik's unseeded-ingress rule withheld it: ``inconclusive``, naming the
handler. Since WI-sosov the handler's ``r *http.Request`` is itself a taint
source where the handler reads it, so the repro -- which writes ``r.URL.Path``
to disk -- reads as what it always was: ``violated``, precise, through
``r -> os.WriteFile``, and a violated verdict acquires no deferred-crossing
caveat (the core file pins that too). The clean-verdict property this file
exists for is pinned on ``_SERVER_READS``: a handler that reads its request
and reaches no sink, so the seed represents the arrival for the taint arm (the
handler leaves the unseeded disclosure) and the launch qualifies the clean
verdict. The boundary arm keeps naming the handler under both programs: no
``net_recv`` crossing stands behind a parameter (ADR-0049 ruling 3).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

#: INV-fogum's own repro: network input chooses what is written to disk.
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

#: The fs sink sits OUTSIDE the handler on purpose. A request use the walk
#: cannot follow past -- a comparison, an argument to an unfollowed call --
#: escapes, and ADR-0017's mixed mode then reports every same-function sink
#: at approximate confidence, exactly as it does for a call-seeded source
#: (measured on the pre-seed tree with ``bufio.Scanner.Text`` in place of
#: ``r``: violated, approximate, ``escaped``). A clean verdict therefore needs
#: no sink in the handler, not a careful one.
_SERVER_READS = """package main

import (
	"net/http"
	"os"
)

type app struct{}

func (app) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/hit" {
		return
	}
}

func main() {
	os.WriteFile("/var/data/started", []byte("x"), 0600)
	http.ListenAndServe(":8080", app{})
}
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
              monkeypatch: pytest.MonkeyPatch, source: str) -> dict[str, dict]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.go").write_text(source)
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
    verdicts = _verdicts(tmp_path, capsys, monkeypatch, _SERVER_READS)

    # Reach: the boundary arm sees the launch, so the shadow is populated. It
    # keeps naming the handler: no crossing stands behind its parameter.
    boundary = verdicts["no-net-recv"]
    [boundary_cav] = _deferred(boundary)
    assert boundary_cav["entries"] == ["net/http.ListenAndServe"]
    assert "app.ServeHTTP at main.go:10" in boundary["details"], boundary["details"]

    taint = verdicts["untrusted-no-fs"]
    assert taint["verdict"] == "confirmed_with_caveats", taint
    # The seeded request represents the arrival for this arm: no disclosure.
    assert "NOT CONFIRMED" not in taint["details"], taint["details"]
    [cav] = _deferred(taint)
    assert (cav["boundary"], cav["arm"], cav["entries"]) == (
        "net_recv", "taint", ["net/http.ListenAndServe"],
    )

    # Scope: a listener says nothing about whether a secret reaches the disk.
    assert _deferred(verdicts["secret-no-fs"]) == []


def test_the_repro_is_violated_through_its_request_parameter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdicts = _verdicts(tmp_path, capsys, monkeypatch, _SERVER)

    taint = verdicts["untrusted-no-fs"]
    assert taint["verdict"] == "violated", taint
    assert (
        "net/http.r [go:main.go:10-12:app.ServeHTTP:method] -> os.WriteFile"
        in taint["details"]
    ), taint["details"]
    assert "[untrusted_input confidence: precise]" in taint["details"], taint["details"]
    assert taint["analysis_methods"] == {"ddg": 1}, taint["analysis_methods"]
    # A violated verdict is not qualified by what the analysis could not see.
    assert _deferred(taint) == []

    # The seed changes nothing on the boundary arm: the launch is disclosed
    # and the handler stays named, its arrival represented by no crossing.
    boundary = verdicts["no-net-recv"]
    assert boundary["verdict"] == "inconclusive", boundary
    [boundary_cav] = _deferred(boundary)
    assert boundary_cav["entries"] == ["net/http.ListenAndServe"]
    assert "no net_recv crossing represents" in boundary["details"], boundary["details"]
    assert "app.ServeHTTP at main.go:10" in boundary["details"], boundary["details"]
