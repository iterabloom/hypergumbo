# SPDX-License-Identifier: AGPL-3.0-or-later
"""A taint file's tier is its provenance line, whatever path named it.

ADR-0061 ruling 3: "The loader reads the tier from that line, never from the
directory." The taint CHANNELS (``taint_*.d/``) obeyed it; the
``--taint-sources`` / ``--taint-sinks`` / ``--taint-sanitizers`` flags and a
claims file's ``extra_catalogs:`` did not. A file declaring
``provenance: community`` named that way loaded as a YOURS layer, so

- its sanitizer CLEARED a flow (``violated`` -> ``confirmed_with_caveats``,
  exit 3, caveat ``user_supplied_sanitizer``), against ruling 2;
- ``--no-default-overlays`` kept it, against ruling 5;
- its sources and sinks acted at user precedence and could replace a shipped
  entry, where the community layer only adds;
- the text report listed it as USER-SUPPLIED input while the JSON ``tiers``
  listed it as community.

The invariant: a community file named by flag or claims file loads into the
community layer -- it adds, its sanitizers are withheld, and the
``--no-default-overlays`` omission drops it and says so -- and text and JSON
report the one tier. A file with no provenance line named the same way is
still yours (the control).

The map mirrors a real survey of ``key = os.urandom(32); wrapped =
mylib.wrap(key); open("out.bin", "wb").write(wrapped)``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from hypergumbo_core.cli import cmd_verify_claims
from hypergumbo_core.schema import SCHEMA_VERSION
from hypergumbo_core.taint import load_full_taint_catalog

_MAIN = "python:app.py:5-8:main:function"
_WRAP = "python:mylib.py:1-2:wrap:function"

_SANITIZER = """\
transforms:
  - input_taint: key_material
    output_taint: wrapped_key
    functions:
      python:
        - mylib.wrap
"""
_COMMUNITY_HEADER = "provenance: community\nretrieved: 2026-01-01\n"


def _bmap() -> dict:
    def ext(module: str, name: str) -> str:
        return f"python:{module}:0-0:{name}:external_symbol"

    def call(dst: str, line: int, module: "str | None" = None,
             name: "str | None" = None) -> dict:
        edge: dict = {"src": _MAIN, "dst": dst, "type": "calls",
                      "confidence": 0.5, "line": line}
        if module is not None:
            edge["is_resolved"] = False
            edge["dst_ref"] = {"lang": "python", "module_path": module,
                               "name": name}
        return edge

    return {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {"id": _WRAP, "name": "wrap", "kind": "function",
             "language": "python", "path": "mylib.py",
             "span": {"start_line": 1, "end_line": 2}},
            {"id": _MAIN, "name": "main", "kind": "function",
             "language": "python", "path": "app.py",
             "span": {"start_line": 5, "end_line": 8}},
        ],
        "edges": [
            call(ext("os", "urandom"), 6, "os", "urandom"),
            call(_WRAP, 7),
            call(ext("file", "write"), 8, "file", "write"),
            call(ext("builtins", "open"), 8, "builtins", "open"),
        ],
    }


class _Args:
    pass


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    origin: str,
    community: bool = True,
    flag: bool = False,
    json_output: bool = True,
) -> "tuple[int, str, str, Path]":
    """One verify-claims run; ``origin`` is ``cli``, ``claims_file`` or
    ``user_channel`` (the control the flag and claims file must match)."""
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "import os\nimport mylib\n\n\ndef main():\n    key = os.urandom(32)\n"
        "    wrapped = mylib.wrap(key)\n"
        "    open(\"out.bin\", \"wb\").write(wrapped)\n"
    )
    (repo / "mylib.py").write_text(
        "def wrap(data):\n    return bytes(reversed(data))\n"
    )
    (tmp_path / "hg.json").write_text(json.dumps(_bmap()))
    body = (_COMMUNITY_HEADER if community else "") + _SANITIZER
    if origin == "user_channel":
        channel = xdg / "hypergumbo" / "taint_sanitizers.d"
        channel.mkdir(parents=True)
        san = channel / "san.yaml"
    else:
        san = tmp_path / "san.yaml"
    san.write_text(body)
    claims: dict = {"claims": [{
        "id": "TF-1", "text": "key material never reaches the filesystem",
        "constraint": {"taint_flow": {"source_taint": "key_material",
                                      "prohibited_sink_zone": "host_fs"}},
    }]}
    if origin == "claims_file":
        claims["extra_catalogs"] = {"sanitizers": [str(san)]}
    (tmp_path / "claims.yaml").write_text(yaml.dump(claims))

    args = _Args()
    args.path = str(repo)
    args.input = str(tmp_path / "hg.json")
    args.claims = str(tmp_path / "claims.yaml")
    args.json_output = json_output
    args.taint_sanitizers = [str(san)] if origin == "cli" else None
    args.no_default_overlays = flag
    rc = cmd_verify_claims(args)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err, san


def _caveats(verdict: dict) -> "set[str]":
    return {c["kind"] for c in verdict.get("caveats") or []}


def _tier_paths(prov: dict, tier: str) -> "list[str]":
    return [e["path"] for e in prov["tiers"][tier]]


_ORIGINS = ["cli", "claims_file", "user_channel"]


@pytest.mark.parametrize("origin", _ORIGINS)
class TestACommunitySanitizerNeverClearsAFlow:
    def test_the_flow_stays_violated_and_the_sanitizer_is_named_withheld(
        self, origin: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc, out, _, san = _run(tmp_path, monkeypatch, capsys, origin=origin)
        data = json.loads(out)
        verdict = data["verdicts"][0]
        assert (rc, verdict["verdict"]) == (1, "violated")
        assert "withheld_community_sanitizer" in _caveats(verdict)
        assert "user_supplied_sanitizer" not in _caveats(verdict)
        prov = data["catalog_provenance"]
        # One tier, in every place the JSON names the file.
        assert str(san) in _tier_paths(prov, "community")
        assert str(san) not in _tier_paths(prov, "yours")
        assert prov["user_supplied"] is False
        assert prov["layers"]["taint_sanitizers"] == {
            "cli": [], "claims_file": []}

    def test_the_text_names_it_community_not_user_supplied(
        self, origin: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _, out, _, san = _run(tmp_path, monkeypatch, capsys, origin=origin,
                              json_output=False)
        assert "USER-SUPPLIED" not in out
        if origin != "user_channel":
            label = ("CLI flag" if origin == "cli"
                     else "claims-file extra_catalogs")
            assert "declare provenance: community" in out
            assert f"{san}  [{label}, taint_sanitizers]" in out


@pytest.mark.parametrize("origin", ["cli", "claims_file"])
def test_the_json_lists_the_named_community_file(
    origin: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, out, _, san = _run(tmp_path, monkeypatch, capsys, origin=origin)
    prov = json.loads(out)["catalog_provenance"]
    assert prov["named_community_files"] == [
        {"family": "taint_sanitizers", "path": str(san), "origin": origin},
    ]


@pytest.mark.parametrize("origin", _ORIGINS)
class TestNoDefaultOverlaysOmitsIt:
    def test_json_reports_it_omitted_and_not_loaded(
        self, origin: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc, out, err, san = _run(tmp_path, monkeypatch, capsys,
                                 origin=origin, flag=True)
        data = json.loads(out)
        verdict = data["verdicts"][0]
        assert (rc, verdict["verdict"]) == (1, "violated")
        # Not loaded at all: nothing to withhold.
        assert "withheld_community_sanitizer" not in _caveats(verdict)
        assert "user_supplied_sanitizer" not in _caveats(verdict)
        assert "--no-default-overlays: omitted 1 taint catalogue file(s)" in err
        prov = data["catalog_provenance"]
        assert prov["omitted_community_overlays"] == [
            {"path": str(san), "origin": origin, "family": "taint_sanitizers"},
        ]
        assert str(san) not in _tier_paths(prov, "community")
        assert prov["named_community_files"] == []
        assert prov["user_supplied"] is False

    def test_text_reports_it_omitted(
        self, origin: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _, out, _, san = _run(tmp_path, monkeypatch, capsys,
                              origin=origin, flag=True, json_output=False)
        assert "USER-SUPPLIED" not in out
        assert "omitted by --no-default-overlays" in out
        label = {"cli": "CLI flag, taint_sanitizers",
                 "claims_file": "claims-file extra_catalogs, taint_sanitizers",
                 "user_channel": "your taint_sanitizers.d"}[origin]
        assert f"{san}  [{label}]" in out


@pytest.mark.parametrize("origin", ["cli", "claims_file"])
def test_a_file_with_no_provenance_line_is_still_yours(
    origin: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """THE CONTROL: vouched-for by deleting the line, the same file clears the
    flow on the operator's word, with the caveat saying so (exit 3), and
    --no-default-overlays leaves it alone."""
    for flag in (False, True):
        sub = tmp_path / str(flag)
        sub.mkdir()
        rc, out, _, san = _run(sub, monkeypatch, capsys, origin=origin,
                               community=False, flag=flag)
        data = json.loads(out)
        verdict = data["verdicts"][0]
        assert (rc, verdict["verdict"]) == (3, "confirmed_with_caveats")
        assert "user_supplied_sanitizer" in _caveats(verdict)
        prov = data["catalog_provenance"]
        assert prov["user_supplied"] is True
        assert prov["layers"]["taint_sanitizers"][origin] == [str(san)]
        assert str(san) in _tier_paths(prov, "yours")
        assert prov["named_community_files"] == []
        assert prov["omitted_community_overlays"] == []


# ---------------------------------------------------------------------------
# The loader: a community source or sink only ADDS
# ---------------------------------------------------------------------------

_SOURCE = """\
description: relabels os.urandom
taint_label: relabelled
sources:
  python:
    - module: os
      functions: [urandom]
      return_tainted: true
"""
_SINK = """\
description: rezones builtins.eval
zone: host_fs
trust_level: untrusted
sinks:
  python:
    - module: builtins
      functions: [eval]
"""


def _write(path: Path, body: str, community: bool) -> Path:
    path.write_text((_COMMUNITY_HEADER if community else "") + body)
    return path


@pytest.mark.parametrize("via", ["cli", "extra"])
def test_a_community_source_or_sink_never_displaces_a_shipped_one(
    via: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    src = _write(tmp_path / "src.yaml", _SOURCE, community=True)
    sink = _write(tmp_path / "sink.yaml", _SINK, community=True)
    catalog = load_full_taint_catalog(**{
        f"{via}_source_paths": [src], f"{via}_sink_paths": [sink],
    })
    labels = {s.taint_label for s in catalog.sources_for_language("python")
              if (s.module, s.name) == ("os", "urandom")}
    zones = {s.zone for s in catalog._sinks["python"]
             if (s.module, s.name) == ("builtins", "eval")}
    assert labels == {"key_material"}
    assert zones == {"code_execution"}
    assert not catalog._displaced_sources and not catalog._displaced_sinks
    # ... and --no-default-overlays drops the community layer, file and all.
    assert load_full_taint_catalog(
        **{f"{via}_source_paths": [src]}, include_community=False,
    ).sources_for_language("python")


@pytest.mark.parametrize("via", ["cli", "extra"])
def test_a_yours_source_or_sink_still_displaces_and_says_so(
    via: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE CONTROL: the operator's own file overrides the shipped row and the
    displacement is carried for the verdict to disclose (INV-faput)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    src = _write(tmp_path / "src.yaml", _SOURCE, community=False)
    sink = _write(tmp_path / "sink.yaml", _SINK, community=False)
    catalog = load_full_taint_catalog(**{
        f"{via}_source_paths": [src], f"{via}_sink_paths": [sink],
    })
    labels = {s.taint_label for s in catalog.sources_for_language("python")
              if (s.module, s.name) == ("os", "urandom")}
    assert labels == {"relabelled"}
    assert catalog._displaced_sources["python"]
    assert catalog._displaced_sinks["python"]


def test_a_community_sanitizer_by_flag_is_stamped_community(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    san = _write(tmp_path / "san.yaml", _SANITIZER, community=True)
    catalog = load_full_taint_catalog(cli_sanitizer_paths=[san])
    mine = [s for s in catalog._sanitizers["python"]
            if s.qualified_name == "mylib.wrap"]
    assert mine and all(s.community and not s.user_supplied for s in mine)
    dropped = load_full_taint_catalog(
        cli_sanitizer_paths=[san], include_community=False)
    assert not [s for s in dropped._sanitizers.get("python", [])
                if s.qualified_name == "mylib.wrap"]


@pytest.mark.parametrize("via", ["cli", "extra", "channel"])
def test_an_unparseable_file_is_a_clean_catalogue_error(
    via: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading the provenance line comes before the real load, so it must
    refuse a broken file the way the load does (TaintCatalogError, exit 2 in
    verify-claims), not with a raw yaml traceback."""
    from hypergumbo_core.taint import TaintCatalogError

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    bad = tmp_path / "broken.yaml"
    bad.write_text("sanitizers: [unclosed\n")
    with pytest.raises(TaintCatalogError, match="could not parse"):
        load_full_taint_catalog(**{f"{via}_sanitizer_paths": [bad]})
