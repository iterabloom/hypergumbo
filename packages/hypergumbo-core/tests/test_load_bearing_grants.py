# SPDX-License-Identifier: AGPL-3.0-or-later
"""A clean verdict names the completeness grants it rested on -- shipped
catalogue and overlay alike (WI-lavut, INV-tabaf's other half).

INV-tabaf made an OVERLAY's ``module_completeness`` grant name the modules it
vouched for, because "an overlay's completeness grant is the most powerful line
a user can write and the least visible". The shipped catalogues make the same
closed-world claim -- python carries 121 grants, rust 20, javascript 10 -- and
turn the same gate off, and a run disclosed none of them: ``catalog_provenance``
was built from the overlay FILES, never from the run, so ``completeness_grants``
read ``[]`` on every arm of every A/B that measured the grants (WI-nolut,
WI-komun, WI-dupok).

SHAPE (b), the one WI-lavut said it would build: disclose only the grants that
were LOAD-BEARING for this verdict -- the modules the repository called into,
that the classifier did not match, and that a grant declared examined. That set
already exists implicitly: it is exactly the calls the uncatalogued-module gate
lets through with ``if disjuncts and not unenumerated: continue``. Collecting it
there (one walk, shared with the unknown set, so the two cannot disagree),
carrying it on :class:`BoundaryCoverage` (the one computation site every verdict
path shares), and rendering it beside the overlay grants answers the reader's
actual question -- which dated audits does my clean verdict stand on -- without
printing 121 module names on every python run.

DISCLOSURE ONLY. No verdict category may move; the controls below pin that and
the A/B in the item's closing entry measures it on the javascript and python
cohorts.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.io_boundary import (
    BoundaryMap,
    load_catalog,
)
from hypergumbo_core.verify_claims import (
    Claim,
    catalog_provenance,
    compute_boundary_coverage,
    render_catalog_provenance_text,
    verify_claim,
)

_OVERLAY_WITH_GRANT = """\
language: python
status: overlay
module_completeness:
  - module: telnetlib
    completeness: complete
    retrieved: "2026-08-25"
"""


def _calls(*targets: tuple[str, str]) -> list[dict]:
    edges = [{"src": "python:src/app.py:1-1:file:file",
              "dst": f"python:{m}:0-0:{m}:external_symbol", "type": "imports"} for m, _ in targets]
    edges += [{"src": "python:src/app.py:3-9:run:function",
               "dst": f"python:{m}:0-0:{n}:external_symbol", "type": "calls"} for m, n in targets]
    return edges


def _coverage(edges, *, overlays=()):
    return compute_boundary_coverage(
        edges, {"python"},
        {"python": load_catalog("python", overlay_paths=list(overlays))},
    )


class TestTheLoadBearingSetIsTheGatesOwnContinue:
    def test_a_granted_module_the_classifier_did_not_match_is_load_bearing(self) -> None:
        """``struct.pack``: no row, granted 2026-09-06 -- the gate passes it,
        and now says so."""
        coverage = _coverage(_calls(("struct", "pack")))
        assert coverage.complete is True, coverage.reason
        assert coverage.load_bearing_grants == {"python": ["struct"]}

    def test_a_classified_call_is_not_a_grant(self) -> None:
        """``os.listdir`` is rowed: the row, not the grant, examined it."""
        coverage = _coverage(_calls(("os", "listdir")))
        assert coverage.load_bearing_grants == {}

    def test_an_unenumerated_module_is_unknown_not_vouched(self) -> None:
        coverage = _coverage(_calls(("tkinter", "Tk")))
        assert coverage.complete is False
        assert coverage.load_bearing_grants == {}

    def test_several_grants_are_listed_sorted_once(self) -> None:
        coverage = _coverage(_calls(("struct", "pack"), ("errno", "errorcode"), ("struct", "unpack")))
        assert coverage.load_bearing_grants == {"python": ["errno", "struct"]}

    def test_an_overlay_grant_is_load_bearing_too(self, tmp_path: Path) -> None:
        overlay = tmp_path / "deps.yaml"
        overlay.write_text(_OVERLAY_WITH_GRANT)
        coverage = _coverage(_calls(("telnetlib", "Telnet")), overlays=[overlay])
        assert coverage.complete is True, coverage.reason
        assert coverage.load_bearing_grants == {"python": ["telnetlib"]}


class TestTheDisclosureReachesBothSurfaces:
    def test_the_json_envelope_carries_the_run_derived_key(self) -> None:
        coverage = _coverage(_calls(("struct", "pack")))
        prov = catalog_provenance({}, (), load_bearing=coverage.load_bearing_grants)
        assert prov["load_bearing_grants"] == [{"language": "python", "modules": ["struct"]}]

    def test_the_text_disclosure_names_the_modules(self) -> None:
        coverage = _coverage(_calls(("struct", "pack"), ("errno", "errorcode")))
        text = "\n".join(render_catalog_provenance_text(
            catalog_provenance({}, (), load_bearing=coverage.load_bearing_grants)))
        assert "errno, struct" in text
        assert "rest" in text.lower() or "rested" in text.lower()

    def test_nothing_load_bearing_renders_nothing(self) -> None:
        prov = catalog_provenance({}, (), load_bearing={})
        assert prov["load_bearing_grants"] == []
        assert render_catalog_provenance_text(prov) == []

    def test_omitting_the_argument_keeps_the_old_shape(self) -> None:
        """Every existing caller passes no coverage; the key is still present
        and empty, so a consumer can rely on it."""
        assert catalog_provenance({}, ())["load_bearing_grants"] == []


class TestDisclosureOnlyNoVerdictMoves:
    """CONTROLS: the verdict category is untouched in both directions."""

    def test_a_clean_verdict_stays_confirmed(self) -> None:
        coverage = _coverage(_calls(("struct", "pack")))
        claim = Claim(id="C", text="t", constraint_boundary="net_send", constraint_must_not_exist=True)
        assert verify_claim(claim, BoundaryMap(), coverage).verdict.startswith("confirmed")

    def test_a_withheld_verdict_stays_withheld(self) -> None:
        coverage = _coverage(_calls(("tkinter", "Tk")))
        claim = Claim(id="C", text="t", constraint_boundary="net_send", constraint_must_not_exist=True)
        assert verify_claim(claim, BoundaryMap(), coverage).verdict == "inconclusive"
