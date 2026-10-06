# SPDX-License-Identifier: AGPL-3.0-or-later
"""A call into dependency source that was not analysed reports I/O UNKNOWN,
never "no I/O" (WI-pogar, ADR-0016 §7 "Transitive dependencies").

THE POLICY. Installed dependency source (``node_modules/``, a virtualenv's
``site-packages``, and Mix ``deps/`` once WI-bapal lands) is not parsed by
default, so a chain like ``chain.run -> library internals -> httpx -> socket``
stops at a boundary stub at the first call into the dependency. The absence of
a chain into code that was never read is not the absence of I/O.

WHAT WAS ALREADY TRUE, measured before this change on four one-file fixtures
(Python + ``.venv``, JS + ``node_modules``, Elixir + ``mix.exs``, Go + ``go.mod``):
``verify-claims`` returned ``inconclusive`` for "never sends data over the
network" in every language, because the uncatalogued-module gate counts the
call. ``io-boundaries`` printed ``No I/O boundary calls detected.`` in every
language, because the call's only home was the ``external_potential`` bucket,
which the text view hides by default. That headline is the "no I/O" the policy
forbids.

THE MECHANISM, in the existing vocabulary rather than a new boundary value:

* ``external_potential`` already IS this population -- an unclassified call
  into a named module whose source was not read (its dst is a synthetic
  ``external_boundary`` stub). Each chain now carries ``dst_ecosystem``, the
  ADR-0041 §3 stamp the boundary node already holds, so a dependency
  (``third_party``) is told apart from an unenumerated standard-library module
  (``stdlib``) where the language enumerates its stdlib, and left ``unknown``
  where it does not.
* ``io-boundaries`` never prints "No I/O boundary calls detected." over that
  population; it prints an I/O-unknown disclosure naming the modules, grouped
  by ecosystem, whatever ``--boundary`` / ``--primitive`` asked about (the
  JSON envelope carries the same grouping as ``untraced_modules``).
* ``verify-claims`` names untraced dependency source in the reason it withholds
  a verdict for, and a clean verdict that rests on an OVERLAY's completeness
  grant for a module whose source was not read says so (the grant stands; the
  owner decision is recorded in ADR-0016 §7).
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import _make_ecosystem_classifier, cmd_io_boundaries
from hypergumbo_core.io_boundary import (
    IO_BOUNDARIES_SCHEMA_VERSION,
    IoChain,
    compute_boundary_map,
    load_catalog,
    module_ecosystem,
    untraced_modules,
)
from hypergumbo_core.ir import Edge
from hypergumbo_core.schema import SCHEMA_VERSION
from hypergumbo_core.verify_claims import (
    BoundaryMap,
    Claim,
    catalog_provenance,
    compute_boundary_coverage,
    render_catalog_provenance_text,
    verify_claims,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _node(sym_id: str, *, external: bool = False, ecosystem: str | None = None,
          path: str = "src/app.py") -> dict:
    lang, rest = sym_id.split(":", 1)
    name = sym_id.split(":")[-2]
    node: dict = {
        "id": sym_id, "name": name, "language": lang,
        "kind": "external_symbol" if external else "function",
        "path": "<external>" if external else path,
        "span": {"start_line": 1, "end_line": 5},
    }
    if external:
        meta: dict = {"external_boundary": True}
        if ecosystem is not None:
            meta["ecosystem"] = ecosystem
        node["meta"] = meta
        node["supply_chain"] = {"tier": 3, "tier_name": "external_dep"}
    else:
        node["supply_chain"] = {"tier": 1, "tier_name": "first_party"}
    return node


_PY_CALLER = "python:src/app.py:1-5:summarize:function"
_PY_DEP = "python:llmkit.Chain:0-0:run:external_symbol"
_PY_STDLIB = "python:tkinter:0-0:Tk:external_symbol"
_JS_CALLER = "javascript:src/index.js:3-5:summarize:function"
_JS_DEP = "javascript:llmkit:0-0:run:external_symbol"


def _behavior_map(nodes: list[dict], edges: list[tuple[str, str]]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "nodes": nodes,
        "edges": [{"src": s, "dst": d, "type": "calls", "confidence": 0.9}
                  for s, d in edges],
    }


def _py_dependency_map(*, with_print: bool = False) -> dict:
    nodes = [_node(_PY_CALLER), _node(_PY_DEP, external=True, ecosystem="third_party")]
    edges = [(_PY_CALLER, _PY_DEP)]
    if with_print:
        printer = "python:builtins:0-0:print:external_symbol"
        nodes.append(_node(printer, external=True, ecosystem="stdlib"))
        edges.append((_PY_CALLER, printer))
    return _behavior_map(nodes, edges)


def _js_dependency_map() -> dict:
    return _behavior_map(
        [_node(_JS_CALLER, path="src/index.js"), _node(_JS_DEP, external=True)],
        [(_JS_CALLER, _JS_DEP)],
    )


class _Args:
    pass


def _args(tmp_path: Path, bmap: dict, **overrides) -> _Args:
    hg = tmp_path / "hg.json"
    hg.write_text(json.dumps(bmap))
    args = _Args()
    args.path = str(tmp_path)
    args.input = str(hg)
    args.json_output = False
    args.by_file = False
    args.boundary = None
    args.primitive = None
    args.show_external_potential = False
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


# ---------------------------------------------------------------------------
# the ecosystem predicate has one home
# ---------------------------------------------------------------------------

class TestModuleEcosystem:
    def test_python_tells_a_dependency_from_the_standard_library(self) -> None:
        catalog = load_catalog("python")
        assert module_ecosystem(catalog, "os.path") == "stdlib"
        assert module_ecosystem(catalog, "llmkit") == "third_party"

    def test_a_language_with_no_enumerated_stdlib_says_it_does_not_know(self) -> None:
        """``not in set`` is indistinguishable from ``set unknown`` there, so
        the answer is None -- never a guessed ``third_party``."""
        assert module_ecosystem(load_catalog("javascript"), "llmkit") is None

    def test_the_boundary_node_stamp_uses_the_same_predicate(self) -> None:
        classify = _make_ecosystem_classifier()
        assert classify("python", "llmkit") == "third_party"
        assert classify("python", "json") == "stdlib"
        assert classify("javascript", "llmkit") is None


# ---------------------------------------------------------------------------
# io-boundaries: the boundary map
# ---------------------------------------------------------------------------

class TestTheBoundaryMapCarriesTheEcosystem:
    def _bmap(self, bmap: dict) -> BoundaryMap:
        nodes_by_id = {n["id"]: n for n in bmap["nodes"]}
        edges = [Edge.create(src=e["src"], dst=e["dst"], edge_type="calls",
                             line=1, origin="test-fixture",
                             origin_run_id="uuid:test") for e in bmap["edges"]]
        catalogs = {lang: load_catalog(lang) for lang in ("python", "javascript")}
        return compute_boundary_map(edges, catalogs, nodes_by_id=nodes_by_id)

    def test_an_external_potential_chain_names_its_dst_ecosystem(self) -> None:
        bmap = self._bmap(_py_dependency_map(with_print=True))
        (chain,) = bmap.entries["external_potential"].chains
        assert chain.dst_ecosystem == "third_party"
        assert chain.to_dict()["dst_ecosystem"] == "third_party"
        (printed,) = bmap.entries["logging"].chains
        assert printed.dst_ecosystem == "stdlib"

    def test_the_envelope_groups_the_untraced_modules(self) -> None:
        out = self._bmap(_py_dependency_map()).to_dict()
        assert out["schema_version"] == IO_BOUNDARIES_SCHEMA_VERSION == "2.4"
        assert out["untraced_modules"] == {
            "third_party": ["llmkit.Chain"], "stdlib": [], "unknown": [],
        }

    def test_a_language_without_a_stdlib_list_is_grouped_unknown(self) -> None:
        out = self._bmap(_js_dependency_map()).to_dict()
        assert out["untraced_modules"]["unknown"] == ["llmkit"]
        assert out["untraced_modules"]["third_party"] == []


class TestUntracedModules:
    def test_only_external_potential_chains_count(self) -> None:
        """A classified call into a dependency (a community row) is a
        DETECTION, reported under its boundary; it is not I/O-unknown."""
        chains = [
            IoChain(boundary="external_potential", primitive="llmkit.Chain.run",
                    io_edge_src=_PY_CALLER, io_edge_dst=_PY_DEP,
                    dst_external_boundary=True, dst_ecosystem="third_party"),
            IoChain(boundary="net_send", primitive="requests.post",
                    io_edge_src=_PY_CALLER,
                    io_edge_dst="python:requests:0-0:post:external_symbol",
                    dst_external_boundary=True, dst_ecosystem="third_party"),
            IoChain(boundary="external_potential", primitive="tkinter.Tk",
                    io_edge_src=_PY_CALLER, io_edge_dst=_PY_STDLIB,
                    dst_external_boundary=True, dst_ecosystem="stdlib"),
        ]
        assert untraced_modules(chains) == {
            "third_party": ["llmkit.Chain"], "stdlib": ["tkinter"], "unknown": [],
        }

    def test_every_key_is_present_when_nothing_is_untraced(self) -> None:
        assert untraced_modules([]) == {"third_party": [], "stdlib": [], "unknown": []}


# ---------------------------------------------------------------------------
# io-boundaries: the CLI text and JSON
# ---------------------------------------------------------------------------

class TestIoBoundariesNeverSaysNoIoOverAnUntracedCall:
    def test_the_headline_is_not_no_io(self, tmp_path: Path, capsys) -> None:
        assert cmd_io_boundaries(_args(tmp_path, _py_dependency_map())) == 0
        out = capsys.readouterr().out
        assert "No I/O boundary calls detected" not in out
        assert "No classified I/O boundary calls detected" in out
        assert "I/O unknown" in out
        assert "untraced dependency" in out
        assert "llmkit.Chain" in out
        assert "--show-external-potential" in out

    def test_a_language_without_a_stdlib_list_still_discloses(
        self, tmp_path: Path, capsys,
    ) -> None:
        """JS: the call is reported I/O-unknown even though hypergumbo cannot
        say whether ``llmkit`` is a dependency or a runtime module."""
        cmd_io_boundaries(_args(tmp_path, _js_dependency_map()))
        out = capsys.readouterr().out
        assert "No I/O boundary calls detected" not in out
        assert "I/O unknown" in out
        assert "llmkit" in out
        assert "untraced dependency:" not in out

    def test_the_disclosure_survives_a_boundary_filter(
        self, tmp_path: Path, capsys,
    ) -> None:
        """``--boundary net_send`` printed "No I/O boundary calls detected."
        about a program whose network I/O sits behind the untraced call."""
        cmd_io_boundaries(_args(tmp_path, _py_dependency_map(), boundary="net_send"))
        out = capsys.readouterr().out
        assert "No I/O boundary calls detected" not in out
        assert "I/O unknown" in out

    def test_shown_bucket_tags_the_dependency(self, tmp_path: Path, capsys) -> None:
        cmd_io_boundaries(_args(tmp_path, _py_dependency_map(with_print=True),
                                show_external_potential=True))
        out = capsys.readouterr().out
        assert "[untraced dependency]" in out
        assert "I/O unknown" in out
        assert "--show-external-potential" not in out

    def test_by_file_view_discloses_too(self, tmp_path: Path, capsys) -> None:
        cmd_io_boundaries(_args(tmp_path, _py_dependency_map(), by_file=True))
        out = capsys.readouterr().out
        assert "No I/O boundary calls detected" not in out
        assert "I/O unknown" in out

    def test_json_filtered_path_carries_the_grouping(self, tmp_path: Path, capsys) -> None:
        cmd_io_boundaries(_args(tmp_path, _py_dependency_map(), json_output=True,
                                boundary="net_send"))
        data = json.loads(capsys.readouterr().out)
        assert data["schema_version"] == "2.4"
        assert data["untraced_modules"]["third_party"] == ["llmkit.Chain"]

    def test_control_a_repo_with_no_external_calls_still_says_no_io(
        self, tmp_path: Path, capsys,
    ) -> None:
        bmap = _behavior_map([_node(_PY_CALLER)], [])
        cmd_io_boundaries(_args(tmp_path, bmap))
        out = capsys.readouterr().out
        assert "No I/O boundary calls detected." in out
        assert "I/O unknown" not in out


# ---------------------------------------------------------------------------
# verify-claims
# ---------------------------------------------------------------------------

def _raw(module: str, name: str, lang: str = "python") -> list[dict]:
    src = f"{lang}:src/app.py:3-9:run:function"
    return [{"src": src, "dst": f"{lang}:{module}:0-0:{name}:external_symbol",
             "type": "calls"}]


_NET = Claim(id="no-net", text="never sends data over the network",
             constraint_boundary="net_send", constraint_must_not_exist=True)


class TestVerifyClaimsNamesUntracedDependencySource:
    def test_the_withheld_reason_names_the_dependency(self) -> None:
        coverage = compute_boundary_coverage(
            _raw("llmkit", "Chain"), {"python"}, {"python": load_catalog("python")})
        assert coverage.complete is False
        assert "untraced dependency" in coverage.reason
        assert "llmkit" in coverage.reason
        (verdict,) = verify_claims([_NET], BoundaryMap(), coverage=coverage)
        assert verdict.verdict == "inconclusive"
        assert "untraced dependency" in verdict.details

    def test_an_unenumerated_stdlib_module_is_not_called_a_dependency(self) -> None:
        coverage = compute_boundary_coverage(
            _raw("tkinter", "Tk"), {"python"}, {"python": load_catalog("python")})
        assert coverage.complete is False
        assert "untraced dependency" not in coverage.reason

    def test_a_language_with_no_stdlib_list_is_not_guessed(self) -> None:
        coverage = compute_boundary_coverage(
            _raw("llmkit", "run", "javascript"), {"javascript"},
            {"javascript": load_catalog("javascript")})
        assert coverage.complete is False
        assert "llmkit" in coverage.reason
        assert "untraced dependency" not in coverage.reason

    def test_beside_an_opaque_launch_the_withholding_clause_names_it_too(self) -> None:
        edges = _raw("llmkit", "Chain") + _raw("subprocess", "run")
        coverage = compute_boundary_coverage(
            edges, {"python"}, {"python": load_catalog("python")})
        assert coverage.opaque_sites
        assert "untraced dependency" in coverage.reason


_GRANT = """\
language: python
status: overlay
module_completeness:
  - module: llmkit
    completeness: complete
    retrieved: "2026-10-06"
"""


class TestAnOverlayGrantStandsButIsDisclosed:
    """Owner decision recorded in ADR-0016 §7: an explicit, dated grant a user
    loaded is a deliberate vouch and may confirm; the verdict must say the
    dependency source behind it was not traced."""

    def _coverage(self, tmp_path: Path, edges: list[dict]):
        overlay = tmp_path / "grant.yaml"
        overlay.write_text(_GRANT)
        return compute_boundary_coverage(
            edges, {"python"},
            {"python": load_catalog("python", overlay_paths=[overlay])})

    def test_the_grant_confirms_and_the_verdict_discloses(self, tmp_path: Path) -> None:
        coverage = self._coverage(tmp_path, _raw("llmkit", "Chain"))
        assert coverage.complete is True, coverage.reason
        assert coverage.untraced_vouched_grants == {"python": ["llmkit"]}
        (verdict,) = verify_claims([_NET], BoundaryMap(), coverage=coverage)
        assert verdict.verdict == "confirmed"
        assert "not traced" in verdict.details
        assert "llmkit" in verdict.details

    def test_a_builtin_grant_is_not_an_untraced_vouch(self, tmp_path: Path) -> None:
        """``struct`` is granted by the shipped stdlib catalogue -- hypergumbo's
        own dated audit of the runtime, not a vouch for unread dependency code."""
        coverage = self._coverage(tmp_path, _raw("struct", "pack"))
        assert coverage.complete is True
        assert coverage.load_bearing_grants == {"python": ["struct"]}
        assert coverage.untraced_vouched_grants == {}
        (verdict,) = verify_claims([_NET], BoundaryMap(), coverage=coverage)
        assert "not traced" not in verdict.details

    def test_a_withheld_verdict_gets_no_grant_sentence(self, tmp_path: Path) -> None:
        edges = _raw("llmkit", "Chain") + _raw("tkinter", "Tk")
        coverage = self._coverage(tmp_path, edges)
        (verdict,) = verify_claims([_NET], BoundaryMap(), coverage=coverage)
        assert verdict.verdict == "inconclusive"
        assert "the grant, not a trace" not in verdict.details

    def test_the_provenance_note_marks_the_untraced_vouch(self, tmp_path: Path) -> None:
        coverage = self._coverage(tmp_path, _raw("llmkit", "Chain"))
        prov = catalog_provenance(
            {}, (), load_bearing=coverage.load_bearing_grants,
            untraced_vouched=coverage.untraced_vouched_grants)
        assert prov["load_bearing_grants"] == [
            {"language": "python", "modules": ["llmkit"],
             "untraced_vouched": ["llmkit"]},
        ]
        text = "\n".join(render_catalog_provenance_text(prov))
        assert "source was not analysed" in text
