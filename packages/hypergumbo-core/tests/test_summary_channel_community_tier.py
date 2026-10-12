# SPDX-License-Identifier: AGPL-3.0-or-later
"""A community file in function_summaries.d is community, not yours.

ADR-0061 ruling 3: a catalogue file's tier is its own ``provenance:`` line,
"never the directory". The taint channels obeyed it; the function-summary
channel did not. ``load_function_summaries`` ranked EVERY
``$XDG_CONFIG_HOME/hypergumbo/function_summaries.d/`` file as the user's before
reading its provenance line, so a file still declaring ``provenance:
community``

- was stamped ``user_supplied``: its TERMINATING summary closed a taint branch
  (``violated`` -> ``inconclusive``, caveat ``user_supplied_sanitizer`` naming
  "your own function_summaries.d/"), against ruling 2 -- a community row may
  add a finding, never remove one;
- won a name collision over a built-in row, where community rows only add;
- survived ``--no-default-overlays`` (only shipped community files were
  dropped), against ruling 5, while the JSON ``tiers`` listed it as community.

The invariant: a community channel file loads like a shipped community file
(adds only, stamped ``community``, its terminating summaries withheld and named
``withheld_community_summary``), ``--no-default-overlays`` drops it and lists it
under ``omitted_community_overlays`` with ``family: function_summaries``, and a
file with the line deleted is still yours (the control).

The repro is a survey of ``key = os.urandom(32); mylib.swallow(key);
open("out.bin", "wb").write(b"constant")`` -- the key is consumed by a callee
whose source is not analysed, so only a terminating summary can close the
branch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from hypergumbo_core.cli import cmd_verify_claims
from hypergumbo_core.function_summaries import (
    clear_summary_cache,
    load_function_summaries,
)
from hypergumbo_core.schema import SCHEMA_VERSION

_COMMUNITY_HEADER = "provenance: community\nretrieved: 2026-01-01\n"
_SWALLOW = """\
summaries:
  - function: "mylib.swallow"
    param_to_return: {}
    side_effect: true
"""
_MAIN = "python:app.py:5-8:main:function"


@pytest.fixture(autouse=True)
def _clean() -> "object":
    clear_summary_cache()
    yield
    clear_summary_cache()


def _channel_file(xdg: Path, body: str, *, community: bool) -> Path:
    channel = xdg / "hypergumbo" / "function_summaries.d"
    channel.mkdir(parents=True, exist_ok=True)
    path = channel / "mylib.yaml"
    path.write_text((_COMMUNITY_HEADER if community else "") + body)
    return path


# ---------------------------------------------------------------------------
# The loader
# ---------------------------------------------------------------------------

class TestTheLoader:
    def test_a_community_channel_file_is_stamped_community(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _channel_file(tmp_path, _SWALLOW, community=True)
        row = load_function_summaries()["mylib.swallow"]
        assert row.community is True
        assert row.user_supplied is False

    def test_the_control_with_the_line_deleted_is_yours(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _channel_file(tmp_path, _SWALLOW, community=False)
        row = load_function_summaries()["mylib.swallow"]
        assert row.user_supplied is True
        assert row.community is False
        # ... and --no-default-overlays leaves the operator's file alone.
        clear_summary_cache()
        assert "mylib.swallow" in load_function_summaries(
            include_community=False)

    def test_no_default_overlays_drops_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _channel_file(tmp_path, _SWALLOW, community=True)
        assert "mylib.swallow" not in load_function_summaries(
            include_community=False)

    def test_it_never_displaces_a_builtin_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``builtins.print`` terminates in the shipped catalogue; a community
        row saying it propagates must not replace it -- nor take the short
        name, which the built-in row already holds."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _channel_file(tmp_path, (
            "summaries:\n"
            '  - function: "builtins.print"\n'
            "    param_to_return: {0: true}\n"
        ), community=True)
        rows = load_function_summaries()
        assert rows["builtins.print"].side_effect is True
        assert rows["builtins.print"].community is False
        assert rows["builtins.print"].user_supplied is False


# ---------------------------------------------------------------------------
# verify-claims, end to end
# ---------------------------------------------------------------------------

def _bmap() -> dict:
    def ext(module: str, name: str, line: int) -> dict:
        return {"src": _MAIN, "dst": f"python:{module}:0-0:{name}:external_symbol",
                "type": "calls", "confidence": 0.5, "line": line,
                "is_resolved": False,
                "dst_ref": {"lang": "python", "module_path": module,
                            "name": name}}

    return {
        "schema_version": SCHEMA_VERSION,
        "nodes": [{"id": _MAIN, "name": "main", "kind": "function",
                   "language": "python", "path": "app.py",
                   "span": {"start_line": 5, "end_line": 8}}],
        "edges": [
            ext("os", "urandom", 6),
            ext("mylib", "swallow", 7),
            ext("file", "write", 8),
            ext("builtins", "open", 8),
        ],
    }


class _Args:
    pass


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    community: "bool | None",
    flag: bool = False,
    json_output: bool = True,
) -> "tuple[int, str, str, Path]":
    """One verify-claims run. ``community=None`` writes no summary file."""
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "import os\nimport mylib\n\n\ndef main():\n    key = os.urandom(32)\n"
        "    mylib.swallow(key)\n"
        "    open(\"out.bin\", \"wb\").write(b\"constant\")\n"
    )
    (tmp_path / "hg.json").write_text(json.dumps(_bmap()))
    path = xdg / "none.yaml"
    if community is not None:
        path = _channel_file(xdg, _SWALLOW, community=community)
    (tmp_path / "claims.yaml").write_text(yaml.dump({"claims": [{
        "id": "TF-1", "text": "key material never reaches the filesystem",
        "constraint": {"taint_flow": {"source_taint": "key_material",
                                      "prohibited_sink_zone": "host_fs"}},
    }]}))
    args = _Args()
    args.path = str(repo)
    args.input = str(tmp_path / "hg.json")
    args.claims = str(tmp_path / "claims.yaml")
    args.json_output = json_output
    args.no_default_overlays = flag
    rc = cmd_verify_claims(args)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err, path


def _caveats(verdict: dict) -> "set[str]":
    return {c["kind"] for c in verdict.get("caveats") or []}


def _tier_paths(prov: dict, tier: str) -> "list[str]":
    return [e["path"] for e in prov["tiers"][tier]]


def test_the_baseline_without_a_summary_is_violated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """REACH: with no summary the walk loses the key at ``mylib.swallow`` and
    the flow stands."""
    rc, out, _, _ = _run(tmp_path, monkeypatch, capsys, community=None)
    verdict = json.loads(out)["verdicts"][0]
    assert (rc, verdict["verdict"]) == (1, "violated")


def test_the_control_your_summary_closes_the_branch_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc, out, _, path = _run(tmp_path, monkeypatch, capsys, community=False)
    data = json.loads(out)
    verdict = data["verdicts"][0]
    assert verdict["verdict"] != "violated" and rc != 1
    assert "user_supplied_sanitizer" in _caveats(verdict)
    prov = data["catalog_provenance"]
    assert str(path) in _tier_paths(prov, "yours")
    assert prov["user_supplied"] is True


class TestACommunityChannelSummary:
    def test_it_never_closes_the_branch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc, out, _, path = _run(tmp_path, monkeypatch, capsys, community=True)
        data = json.loads(out)
        verdict = data["verdicts"][0]
        assert (rc, verdict["verdict"]) == (1, "violated")
        assert "withheld_community_summary" in _caveats(verdict)
        assert "user_supplied_sanitizer" not in _caveats(verdict)
        withheld = next(c for c in verdict["caveats"]
                        if c["kind"] == "withheld_community_summary")
        assert withheld["entries"] == ["mylib.swallow"]
        prov = data["catalog_provenance"]
        assert str(path) in _tier_paths(prov, "community")
        assert str(path) not in _tier_paths(prov, "yours")
        assert prov["user_supplied"] is False

    def test_the_text_does_not_call_it_user_supplied(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _, out, _, _ = _run(tmp_path, monkeypatch, capsys, community=True,
                            json_output=False)
        assert "USER-SUPPLIED" not in out
        assert "your own function_summaries.d" not in out

    def test_no_default_overlays_omits_it_and_says_so(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc, out, err, path = _run(tmp_path, monkeypatch, capsys,
                                  community=True, flag=True)
        data = json.loads(out)
        verdict = data["verdicts"][0]
        assert (rc, verdict["verdict"]) == (1, "violated")
        # Not loaded at all: nothing closed a branch, nothing was withheld.
        assert "withheld_community_summary" not in _caveats(verdict)
        assert "user_supplied_sanitizer" not in _caveats(verdict)
        assert "--no-default-overlays: omitted 1 function-summary file(s)" in err
        prov = data["catalog_provenance"]
        assert prov["omitted_community_overlays"] == [
            {"path": str(path), "origin": "user_channel",
             "family": "function_summaries"},
        ]
        assert str(path) not in _tier_paths(prov, "community")
        assert str(path) not in _tier_paths(prov, "yours")
        assert prov["user_supplied"] is False

    def test_no_default_overlays_text_reports_it_omitted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _, out, _, path = _run(tmp_path, monkeypatch, capsys, community=True,
                               flag=True, json_output=False)
        assert "USER-SUPPLIED" not in out
        assert "omitted by --no-default-overlays" in out
        assert f"{path}  [your function_summaries.d]" in out


def test_no_default_overlays_keeps_your_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """THE CONTROL for the flag: it omits community rows, not the operator's."""
    rc, out, _, path = _run(tmp_path, monkeypatch, capsys, community=False,
                            flag=True)
    data = json.loads(out)
    verdict = data["verdicts"][0]
    assert "user_supplied_sanitizer" in _caveats(verdict)
    prov = data["catalog_provenance"]
    assert prov["omitted_community_overlays"] == []
    assert str(path) in _tier_paths(prov, "yours")
