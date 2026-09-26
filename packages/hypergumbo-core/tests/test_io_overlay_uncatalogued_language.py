# SPDX-License-Identifier: AGPL-3.0-or-later
"""A project-local I/O overlay applies to a language that ships no catalogue.

WI-guhuv. ADR-0016 scopes the shipped catalogues to the stdlib and sends
everything else to user overlays. For a language with no shipped catalogue that
remedy did not exist, and the CLI said the opposite. Reproduced on the shipped
CLI at dev f587b0a806 with a ruby overlay rowing ``get`` on ``net/http``:

* ruby-only repo: "Loaded 1 project-local I/O primitive overlay(s)", then
  ``inconclusive`` rc 2 because "language(s) ruby made calls but have no I/O
  catalog". ``load_catalog`` returned before its overlay loop.
* ruby + python repo: the WHOLE RUN aborted, rc 2 with no JSON: "declares
  language 'ruby', which has no I/O primitive catalogue ... check the
  spelling". The python load met the ruby overlay and called it a typo.

THE FIX KEEPS THE TYPO GUARD AND MOVES ITS TEST. A typo is a language no
ANALYZER knows (``pyton``), not a language no catalogue file names.

WHY OPENING THE DOOR CANNOT BUY A FALSE ALL-CLEAR. An overlay-only catalogue
declares no stdlib and dates no module unless the user writes a
``module_completeness`` entry, so every module it does not vouch for stays
UNADJUDICATED and still withholds a clean verdict
(``_uncatalogued_external_modules``). The item's hard prerequisite, that a
launch keep its opacity whatever row classifies it, is INV-larol, satisfied.
``test_a_clean_claim_over_an_unvouched_module_is_still_withheld`` pins that
end to end.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import (
    CATALOG_STATUS_UNSUPPORTED,
    BoundaryMap,
    IoPrimitiveOverlayError,
    compute_boundary_map,
    load_catalog,
)
from hypergumbo_core.verify_claims import (
    Claim,
    compute_boundary_coverage,
    verify_claim,
)

#: Spelled ``net/http`` (the require path), NOT ``Net::HTTP`` as ruby source
#: writes it: the ruby analyzer emits the receiver constant lowercased and
#: without its namespace (``ruby:http:0-0:get``), and ``_module_matches``
#: requires a suffix match to agree in case (INV-dijor), so a ``Net::HTTP`` row
#: cannot match. That is the analyzer's slot, filed separately; this file is
#: about whether the overlay applies at all.
RUBY_OVERLAY = """\
language: ruby
status: overlay
net_recv:
  - module: net/http
    functions: [get]
    notes: Class-level HTTP GET; returns the body the server chose.
"""

#: Captured from ``hypergumbo survey`` over ``Net::HTTP.get(URI(u))``.
RUBY_GET_EDGE = {
    "src": "ruby:app.rb:2-4:fetch:function",
    "dst": "ruby:http:0-0:get:external_symbol",
    "type": "calls",
    "line": 3,
    "meta": {"call_construct": "method"},
}


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


class TestTheOverlayApplies:

    def test_without_an_overlay_nothing_changes(self) -> None:
        """INV-javam's signal is untouched for the ordinary case."""
        cat = load_catalog("ruby")
        assert cat.is_supported is False
        assert cat.status == CATALOG_STATUS_UNSUPPORTED
        assert cat.primitives == []
        assert cat.overlay_only is False

    def test_the_overlay_rows_reach_the_catalogue(self, tmp_path: Path) -> None:
        overlay = _write(tmp_path, "rb.yaml", RUBY_OVERLAY)
        cat = load_catalog("ruby", overlay_paths=[overlay])
        assert {(p.module, p.name, p.boundary) for p in cat.primitives} == {
            ("net/http", "get", "net_recv"),
        }
        assert cat.is_supported is True

    def test_it_is_marked_overlay_only_and_claims_no_stdlib(
        self, tmp_path: Path,
    ) -> None:
        """Supported for CLASSIFICATION, never read as a curated catalogue."""
        overlay = _write(tmp_path, "rb.yaml", RUBY_OVERLAY)
        cat = load_catalog("ruby", overlay_paths=[overlay])
        assert cat.overlay_only is True
        assert cat.status == "in_progress"
        assert cat.stdlib_provenance is None
        assert not cat.stdlib_modules and not cat.stdlib_prefixes
        assert cat.module_completeness == {}

    def test_a_shipped_language_is_never_overlay_only(self, tmp_path: Path) -> None:
        overlay = _write(tmp_path, "py.yaml", """\
            language: python
            status: overlay
            net_send:
              - module: niquests
                functions: [post]
            """)
        assert load_catalog("python", overlay_paths=[overlay]).overlay_only is False

    def test_an_overlay_for_another_language_leaves_it_unsupported(
        self, tmp_path: Path,
    ) -> None:
        """The overlay list is fanned out over every language in the repo, so
        a ruby load is also handed the python overlay. Not mine, not an error,
        and not a reason to call ruby supported."""
        overlay = _write(tmp_path, "py.yaml", """\
            language: python
            status: overlay
            net_send:
              - module: niquests
                functions: [post]
            """)
        cat = load_catalog("ruby", overlay_paths=[overlay])
        assert cat.is_supported is False
        assert cat.primitives == []
        assert cat.overlay_only is False


class TestTheTypoGuardMoved:

    def test_a_python_load_skips_a_ruby_overlay(self, tmp_path: Path) -> None:
        """The second facet: this used to raise and abort the whole run."""
        overlay = _write(tmp_path, "rb.yaml", RUBY_OVERLAY)
        cat = load_catalog("python", overlay_paths=[overlay])
        assert cat.language == "python"
        assert not any(p.module == "net/http" for p in cat.primitives)

    @pytest.mark.parametrize("load_as", ["python", "ruby"])
    def test_a_language_no_analyzer_knows_is_still_a_typo(
        self, tmp_path: Path, load_as: str,
    ) -> None:
        overlay = _write(tmp_path, "typo.yaml", """\
            language: rubby
            status: overlay
            net_recv:
              - module: net/http
                functions: [get]
            """)
        with pytest.raises(IoPrimitiveOverlayError, match="rubby"):
            load_catalog(load_as, overlay_paths=[overlay])


class TestTheVerdicts:

    def _coverage(self, tmp_path: Path, edges: list[dict]):
        overlay = _write(tmp_path, "rb.yaml", RUBY_OVERLAY)
        catalogs = {"ruby": load_catalog("ruby", overlay_paths=[overlay])}
        return catalogs, compute_boundary_coverage(edges, {"ruby"}, catalogs)

    def _claim(self, boundary: str) -> Claim:
        return Claim(id="C", text="t", constraint_boundary=boundary,
                     constraint_must_not_exist=True)

    def test_the_overlay_row_finds_the_crossing(self, tmp_path: Path) -> None:
        from hypergumbo_core.ir import Edge

        catalogs, coverage = self._coverage(tmp_path, [RUBY_GET_EDGE])
        edge = Edge(
            id="e1", src=RUBY_GET_EDGE["src"], dst=RUBY_GET_EDGE["dst"],
            edge_type="calls", line=3, meta=dict(RUBY_GET_EDGE["meta"]),
            origin="ruby-v1", origin_run_id="r1",
        )
        bmap = compute_boundary_map([edge], catalogs)
        verdict = verify_claim(self._claim("net_recv"), bmap, coverage)
        assert verdict.verdict == "violated", verdict.details

    def test_a_clean_claim_over_an_unvouched_module_is_still_withheld(
        self, tmp_path: Path,
    ) -> None:
        """THE FAIL-CLOSED PROOF. The overlay names only net/http get; a call
        into ``File`` is a module it does not vouch for, so a clean fs_write
        verdict must stay withheld rather than read the overlay as the whole
        of ruby's I/O."""
        file_write = {
            "src": "ruby:app.rb:6-8:save:function",
            "dst": "ruby:file:0-0:write:external_symbol",
            "type": "calls",
            "line": 7,
            "meta": {"call_construct": "method"},
        }
        _catalogs, coverage = self._coverage(tmp_path, [RUBY_GET_EDGE, file_write])
        assert coverage.complete is False
        assert "file" in (coverage.reason or "")
        verdict = verify_claim(self._claim("fs_write"), BoundaryMap(), coverage)
        assert verdict.verdict == "inconclusive", verdict.verdict


class TestTheTaintArmSeesItToo:

    def test_an_overlay_only_row_derives_a_taint_source(
        self, tmp_path: Path,
    ) -> None:
        """INV-fotav's unification, for a language the glob over shipped
        catalogue files never visits: one declaration, both arms."""
        from hypergumbo_core.taint import load_full_taint_catalog

        overlay = _write(tmp_path, "rb.yaml", RUBY_OVERLAY)
        before = load_full_taint_catalog()
        after = load_full_taint_catalog(io_overlay_paths=[overlay])
        assert not any(s.module == "net/http" for s in before.sources_for_language("ruby"))
        hit = [s for s in after.sources_for_language("ruby") if s.module == "net/http"]
        assert [(s.name, s.taint_label) for s in hit] == [("get", "untrusted_input")]


class TestTheDisclosure:

    def test_an_overlay_only_language_is_named_once(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import _warn_overlay_only_catalogs

        overlay = _write(tmp_path, "rb.yaml", RUBY_OVERLAY)
        ruby = load_catalog("ruby", overlay_paths=[overlay])
        warned = _warn_overlay_only_catalogs(
            {"ruby": ruby, "ruby-alias": ruby, "python": load_catalog("python")},
        )
        assert warned == ["ruby"]
        err = capsys.readouterr().err
        assert err.count("ships no I/O primitive catalogue") == 1
        assert "'ruby'" in err

    def test_a_catalogued_language_is_not_named(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import _warn_overlay_only_catalogs

        assert _warn_overlay_only_catalogs({"python": load_catalog("python")}) == []
        assert capsys.readouterr().err == ""
