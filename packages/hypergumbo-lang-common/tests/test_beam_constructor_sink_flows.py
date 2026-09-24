# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-gujoh, erlang and elixir: a flow through a constructor is still
reported, at the call that crosses.

``ets:new`` creates an empty table. ``Req.new`` and ``Finch.build`` build a
request value. None of them stores or sends anything, so none is rowed under a
sink any more. ADR-0049 Ruling 3 licenses each removal only if the finding
survives it, at the executor that is rowed: ``ets:insert``, ``Req.request!``,
``Finch.request``. These tests are that check, through ``hypergumbo
verify-claims`` on real source files.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_CLAIMS = """claims:
  - id: secret-no-network
    text: Environment secrets never reach the network.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: network
  - id: secret-no-database
    text: Environment secrets never reach a database.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: database
"""


def _verdicts(tmp_path: Path, filename: str, source: str,
              capsys: pytest.CaptureFixture[str],
              monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / filename).write_text(source)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    return {v["claim_id"]: v for v in json.loads(capsys.readouterr().out)["verdicts"]}


def _sinks(verdict: dict) -> list[str]:
    return [e["sink_symbol"].split(":")[1] + "." + e["sink_symbol"].split(":")[-2]
            for e in verdict.get("evidence", [])]


def test_erlang_the_write_is_reported_at_insert(
        tmp_path: Path, capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch) -> None:
    got = _verdicts(tmp_path, "a.erl", """-module(a).
-export([store/0]).

store() ->
    Secret = os:getenv("API_KEY"),
    T = ets:new(secrets, [set]),
    ets:insert(T, {key, Secret}).
""", capsys, monkeypatch)
    db = got["secret-no-database"]
    assert db["verdict"] == "violated", db
    assert _sinks(db) == ["ets.insert"], db["evidence"]


@pytest.mark.parametrize("body,executor", [
    ("req = Req.new(url: url, body: secret)\n    Req.request!(req)", "Req.request!"),
    ("req = Finch.build(:post, url, [], secret)\n    Finch.request(req, MyFinch)", "Finch.request"),
    # plausible's shape. This flow was carried ONLY by the Req.new row until
    # Req.Request.run_request was rowed with its removal.
    ("{_req, resp} = [base_url: url, body: secret] |> Req.new() |> Req.Request.run_request()\n    resp",
     "Req.Request.run_request"),
], ids=["req", "finch", "req-pipeline"])
def test_elixir_the_send_is_reported_at_the_executor(
        tmp_path: Path, capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch, body: str, executor: str) -> None:
    got = _verdicts(tmp_path, "a.ex", f"""defmodule A do
  def send(url) do
    secret = System.get_env("API_KEY")
    {body}
  end
end
""", capsys, monkeypatch)
    net = got["secret-no-network"]
    assert net["verdict"] == "violated", net
    assert _sinks(net) == [executor], net["evidence"]
