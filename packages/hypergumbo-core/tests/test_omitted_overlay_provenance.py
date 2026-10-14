# SPDX-License-Identifier: AGPL-3.0-or-later
"""An overlay ``--no-default-overlays`` omitted is reported as omitted (INV-dozav).

The flag drops every overlay declaring ``provenance: community`` before the
load -- including one named with ``--io-primitives`` or through a claims file's
``extra_catalogs: io_primitives:`` -- and stderr says so. But
``catalog_provenance`` was handed the RAW flag and claims-file lists, so the
report described a file the run never read: the text said the verdicts were
"computed against USER-SUPPLIED catalogue input" naming it, listed its grant
under WITHHELD GRANTS ("delete the line to vouch for a file" -- which would not
help while the flag is set), and the JSON said ``user_supplied: true`` with a
withheld grant while ``tiers.yours`` and ``tiers.community`` were both empty.

The invariant: the provenance report describes only files the run loaded. An
omitted file appears under ``omitted_community_overlays`` (and the text's
matching block), never as user-supplied input or as a withheld grant.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from hypergumbo_core.cli import cmd_verify_claims
from hypergumbo_core.schema import SCHEMA_VERSION
from hypergumbo_core.verify_claims import (
    catalog_provenance,
    render_catalog_provenance_text,
)

_COMMUNITY_GRANT = """\
language: python
status: overlay
provenance: community
retrieved: 2026-01-01
module_completeness:
  - module: telnetlib
    completeness: complete
    retrieved: "2026-01-01"
"""


class _Args:
    pass


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    origin: str,
    flag: bool,
    json_output: bool,
) -> "tuple[int, str, str, Path]":
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-config"))
    node = "python:app.py:1:main:function"
    bmap = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [{"id": node, "name": "main", "kind": "function",
                   "language": "python", "path": "app.py",
                   "span": {"start_line": 1, "end_line": 2}}],
        "edges": [{"src": node,
                   "dst": "python:telnetlib.py:1:telnetlib.Telnet:function",
                   "type": "calls", "confidence": 0.9}],
    }
    (tmp_path / "hg.json").write_text(json.dumps(bmap))
    comm = tmp_path / "comm.yaml"
    comm.write_text(_COMMUNITY_GRANT)
    claims: dict = {"claims": [{
        "id": "NS-1", "text": "never sends over the network",
        "constraint": {"boundary": "net_send", "must_not_exist": True},
    }]}
    if origin == "claims_file":
        claims["extra_catalogs"] = {"io_primitives": ["comm.yaml"]}
    (tmp_path / "claims.yaml").write_text(yaml.dump(claims))

    args = _Args()
    args.path = str(tmp_path)
    args.input = str(tmp_path / "hg.json")
    args.claims = str(tmp_path / "claims.yaml")
    args.json_output = json_output
    args.io_primitives = [str(comm)] if origin == "cli" else None
    args.no_default_overlays = flag
    rc = cmd_verify_claims(args)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err, comm


@pytest.mark.parametrize("origin", ["cli", "claims_file"])
class TestTheOmittedFile:
    def test_json_reports_it_omitted_not_supplied(
        self, origin: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _, out, err, comm = _run(
            tmp_path, monkeypatch, capsys,
            origin=origin, flag=True, json_output=True,
        )
        prov = json.loads(out)["catalog_provenance"]
        # reach: the run did omit it, and said so on stderr
        assert "--no-default-overlays: omitted 1 overlay(s)" in err
        assert prov["omitted_community_overlays"] == [
            {"path": str(comm), "origin": origin},
        ]
        assert prov["user_supplied"] is False
        assert prov["withheld_completeness_grants"] == []
        assert prov["completeness_grants"] == []
        assert prov["layers"]["io_primitives"] == {"cli": [], "claims_file": []}
        assert prov["tiers"]["yours"] == [] and prov["tiers"]["community"] == []

    def test_text_reports_it_omitted_not_supplied(
        self, origin: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _, out, _, comm = _run(
            tmp_path, monkeypatch, capsys,
            origin=origin, flag=True, json_output=False,
        )
        assert "USER-SUPPLIED" not in out
        assert "WITHHELD GRANTS" not in out
        assert "omitted by --no-default-overlays" in out
        label = "CLI flag" if origin == "cli" else "claims-file extra_catalogs"
        assert f"{comm}  [{label}]" in out


@pytest.mark.parametrize("origin", ["cli", "claims_file"])
@pytest.mark.parametrize("json_output", [True, False])
def test_without_the_flag_the_grant_is_still_withheld(
    json_output: bool, origin: str, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """THE CONTROL: loaded, the file's grant is withheld (INV-kobam), and
    nothing is reported omitted. The file is a COMMUNITY row, whatever named
    it (ADR-0061 ruling 3): it is listed under ``named_community_files``,
    never as user-supplied input -- the taint arm's rule, which the JSON
    ``tiers`` already followed while the text and ``user_supplied`` did not."""
    _, out, _, comm = _run(
        tmp_path, monkeypatch, capsys,
        origin=origin, flag=False, json_output=json_output,
    )
    if json_output:
        prov = json.loads(out)["catalog_provenance"]
        assert prov["user_supplied"] is False
        assert prov["layers"]["io_primitives"] == {"cli": [], "claims_file": []}
        assert prov["named_community_files"] == [
            {"family": "io_primitives", "path": str(comm), "origin": origin},
        ]
        assert comm.name in [
            Path(e["path"]).name for e in prov["tiers"]["community"]]
        assert prov["omitted_community_overlays"] == []
        assert [(g["path"], g["origin"]) for g in
                prov["withheld_completeness_grants"]] == [(str(comm), origin)]
        assert prov["completeness_grants"] == []
    else:
        label = "CLI flag" if origin == "cli" else "claims-file extra_catalogs"
        assert "USER-SUPPLIED" not in out
        assert "WITHHELD GRANTS" in out
        assert f"from {comm}  [{label}]" in out
        assert f"{comm}  [{label}, io_primitives]" in out
        assert "omitted by --no-default-overlays" not in out


def test_your_overlay_by_flag_is_still_user_supplied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """THE CONTROL for the tier: with the line deleted the file is yours."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-config"))
    (tmp_path / "hg.json").write_text(json.dumps({
        "schema_version": SCHEMA_VERSION, "nodes": [], "edges": []}))
    mine = tmp_path / "mine.yaml"
    mine.write_text(_COMMUNITY_GRANT.replace("provenance: community\n", ""))
    (tmp_path / "claims.yaml").write_text(yaml.dump({"claims": [{
        "id": "NS-1", "text": "never sends over the network",
        "constraint": {"boundary": "net_send", "must_not_exist": True},
    }]}))
    args = _Args()
    args.path = str(tmp_path)
    args.input = str(tmp_path / "hg.json")
    args.claims = str(tmp_path / "claims.yaml")
    args.json_output = True
    args.io_primitives = [str(mine)]
    args.no_default_overlays = False
    cmd_verify_claims(args)
    prov = json.loads(capsys.readouterr().out)["catalog_provenance"]
    assert prov["user_supplied"] is True
    assert prov["layers"]["io_primitives"]["cli"] == [str(mine)]
    assert prov["named_community_files"] == []
    assert [g["path"] for g in prov["completeness_grants"]] == [str(mine)]


def test_the_withheld_block_renders_without_user_input() -> None:
    """A community overlay is not user input, so its withheld grant must not
    wait for the user-supplied block to be rendered at all."""
    text = "\n".join(render_catalog_provenance_text({
        "user_supplied": False,
        "withheld_completeness_grants": [{
            "path": "seed.yaml", "origin": "user_channel",
            "language": "python", "modules": ["vendorlib"],
        }],
    }))
    assert "WITHHELD GRANTS" in text
    assert "from seed.yaml  [your io_primitives.d]" in text
    assert "USER-SUPPLIED" not in text


def test_the_key_is_always_present() -> None:
    assert catalog_provenance({})["omitted_community_overlays"] == []


def test_the_omitted_block_renders_without_user_input() -> None:
    lines = render_catalog_provenance_text({
        "user_supplied": False,
        "omitted_community_overlays": [
            {"path": "seed.yaml", "origin": "user_channel"},
            {"path": "odd.yaml", "origin": "somewhere"},
        ],
    })
    text = "\n".join(lines)
    assert "2 catalogue file(s) omitted by --no-default-overlays" in text
    assert "seed.yaml  [your io_primitives.d]" in text
    assert "odd.yaml  [somewhere]" in text
    assert "USER-SUPPLIED" not in text
