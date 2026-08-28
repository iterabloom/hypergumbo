# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shipped community overlays load by default and say so (ADR-0047, WI-riros).

The owner's ruling that admits these rows is conditional: hypergumbo may ship
third-party rows it does not vouch for, loaded by default, **provided a run
that loads them says so in default human output**. A JSON field alone does not
satisfy it. So the disclosure is as much the subject of these tests as the
loading is.

The third state is the point. ``user_supplied`` is a boolean, and this creates
a value neither of its settings describes — "hypergumbo shipped it and does not
vouch for it". Collapsing it into either neighbour re-opens the INV-zosun gap
that disclosure exists to close.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import (
    default_overlays,
    load_catalog,
    load_overlay_catalog,
)

REQUESTS_EDGE = [{
    "type": "calls",
    "src": "python:app.py:1-3:main:function",
    "dst": "python:requests:0-0:post:external_symbol",
    "is_resolved": False,
    "meta": {"call_construct": "function"},
}]

OVERLAY_DIR = (
    Path(__file__).resolve().parents[1]
    / "src" / "hypergumbo_core" / "io_primitives_overlays"
)


class TestTheyShipAndLoad:
    def test_overlays_live_in_the_wheel_not_in_docs(self):
        """docs/ is not in the wheel, so an installed hypergumbo cannot read it.

        ``pyproject.toml`` declares ``packages = ["src/hypergumbo_core"]``.
        Overlays under ``docs/`` were unreachable from any installed copy —
        they shipped to nobody.
        """
        assert OVERLAY_DIR.is_dir()
        assert {p.name for p in OVERLAY_DIR.glob("*.yaml")} == {
            "go-web-frameworks.yaml", "python-http-clients.yaml",
        }

    def test_default_overlays_are_selected_per_language(self):
        """INV-lufib: a catalogue applied to EVERY language hard-fails.

        A claims-file overlay was once applied to every language in the repo,
        so a python overlay hard-failed any repo that also contained
        javascript. Defaults must therefore be language-keyed, not global.
        """
        assert [o.path.name for o in default_overlays("python")] == [
            "python-http-clients.yaml",
        ]
        assert [o.path.name for o in default_overlays("go")] == [
            "go-web-frameworks.yaml",
        ]
        assert default_overlays("rust") == []
        assert default_overlays("javascript") == []

    def test_default_rows_reach_the_catalogue_without_any_flag(self):
        """The ruling's substance: no flag, no claims file, rows present."""
        python = load_catalog("python")
        assert any(
            p.module == "requests" and p.name == "post"
            for p in python.primitives
        ), "requests.post is not reachable from a default python catalogue"

    def test_defaults_can_be_turned_off(self):
        without = load_catalog("python", include_defaults=False)
        assert not any(
            p.module == "requests" for p in without.primitives
        )

    def test_a_default_does_not_leak_into_another_language(self):
        go = load_catalog("go")
        assert not any(p.module == "requests" for p in go.primitives)


class TestTheyAreDisclosable:
    @pytest.mark.parametrize(
        "name", ["go-web-frameworks.yaml", "python-http-clients.yaml"],
    )
    def test_each_declares_community_provenance_and_a_date(self, name: str):
        overlays = [o for o in _all_defaults() if o.path.name == name]
        assert len(overlays) == 1
        o = overlays[0]
        assert o.provenance == "community"
        # A date a reader can judge staleness against, not a package date.
        assert o.retrieved and o.retrieved.count("-") == 2


class TestTheGuardThatMattersMost:
    """A shipped default must never grant module_completeness.

    A ``module_completeness`` entry is "the single grant of confirmability": it
    turns a call the catalogue could not classify from *a place the analysis
    could not look* into an *examined negative*, and so decides whether a
    ``must_not_exist`` claim over some other boundary may be CONFIRMED. Shipped
    by default into an arbitrary user's repo that is a false-all-clear channel,
    which is the expensive direction. A user may grant it for their own tree;
    hypergumbo may not grant it on their behalf.
    """

    @pytest.mark.parametrize(
        "name", ["go-web-frameworks.yaml", "python-http-clients.yaml"],
    )
    def test_no_shipped_default_grants_completeness(self, name: str):
        import yaml

        raw = yaml.safe_load((OVERLAY_DIR / name).read_text())
        assert "module_completeness" not in raw, (
            f"{name} grants module_completeness. A default overlay must not: "
            "it would convert unexamined calls into examined negatives in "
            "repositories whose authors never opted in."
        )

    def test_the_rule_is_enforced_not_merely_observed(self, tmp_path: Path):
        """The gate refuses such a file rather than trusting review."""
        from hypergumbo_core.io_boundary import (
            DefaultOverlayError, validate_default_overlays,
        )

        bad = tmp_path / "bad.yaml"
        bad.write_text(
            "language: python\nstatus: overlay\nprovenance: community\n"
            "retrieved: 2026-01-01\nmodule_completeness:\n"
            "  - module: requests\n    complete: true\n"
        )
        with pytest.raises(DefaultOverlayError, match="module_completeness"):
            validate_default_overlays([bad])

    def test_a_default_must_declare_provenance_and_retrieved(
        self, tmp_path: Path,
    ):
        from hypergumbo_core.io_boundary import (
            DefaultOverlayError, validate_default_overlays,
        )

        undated = tmp_path / "undated.yaml"
        undated.write_text(
            "language: python\nstatus: overlay\nprovenance: community\n"
        )
        with pytest.raises(DefaultOverlayError, match="retrieved"):
            validate_default_overlays([undated])

        unvouched = tmp_path / "unmarked.yaml"
        unvouched.write_text(
            "language: python\nstatus: overlay\nretrieved: 2026-01-01\n"
        )
        with pytest.raises(DefaultOverlayError, match="provenance"):
            validate_default_overlays([unvouched])

    def test_the_shipped_set_passes_its_own_gate(self):
        from hypergumbo_core.io_boundary import validate_default_overlays

        validate_default_overlays(
            [o.path for o in _all_defaults()]
        )  # must not raise


def _all_defaults():
    return default_overlays("python") + default_overlays("go")


def test_an_overlay_still_loads_standalone():
    """Moving the files must not change what they are."""
    c = load_overlay_catalog(OVERLAY_DIR / "python-http-clients.yaml")
    assert c.language == "python"


class TestTheDisclosureTheRulingRequires:
    """"Yes as long as it's LOUD about it" — the condition, tested.

    The ruling admits unvouched rows ON CONDITION that a run which loads them
    says so in DEFAULT HUMAN OUTPUT. A JSON field does not satisfy it, so the
    stderr notice is the load-bearing artifact and is tested as such: it must
    fire without any flag, name the overlay, and name its date.
    """

    def test_it_fires_per_queried_language_naming_file_and_date(self, capsys):
        from hypergumbo_core.cli import _warn_default_overlays

        warned = _warn_default_overlays(["python", "rust"])
        err = capsys.readouterr().err
        assert warned == ["python"], warned
        assert "python-http-clients.yaml" in err
        assert "2026-08-11" in err
        assert "does not vouch" in err.lower()

    def test_a_language_with_no_default_overlay_says_nothing(self, capsys):
        from hypergumbo_core.cli import _warn_default_overlays

        assert _warn_default_overlays(["rust", "haskell"]) == []
        assert capsys.readouterr().err == ""

    def test_it_fires_once_per_language_not_once_per_file(self, capsys):
        from hypergumbo_core.cli import _warn_default_overlays

        assert _warn_default_overlays(["python", "python", "go"]) == [
            "go", "python",
        ]

    def test_the_three_catalogue_consumers_all_disclose(self):
        """Same three call sites as the in_progress warning, so it is uniform.

        A disclosure wired into one consumer is a disclosure the other two
        silently omit — the shape INV-karud recorded when an exclusion bucket
        reached the dataclass and never the text renderer.
        """
        import inspect

        from hypergumbo_core import cli

        src = inspect.getsource(cli)
        in_progress = src.count("_warn_in_progress_catalogs(")
        defaults = src.count("_warn_default_overlays(")
        # -1 on each for the definition itself.
        assert defaults - 1 == in_progress - 1 > 0, (
            f"default-overlay disclosure fires at {defaults - 1} sites but "
            f"the in_progress warning fires at {in_progress - 1}"
        )


class TestADefaultDoesNotRestateTheCatalogueSStatus:
    """A community row is not part of the stdlib enumeration.

    ``status: provenance_declared`` is a claim about a language's STDLIB
    surface being enumerated against a cited source. Community rows are
    explicitly outside that claim, so loading them must neither strengthen nor
    weaken it.

    The failure this pins is concrete and was live in the first cut: ``merge``
    is self-over-argument and the overlay is the receiver, so a default
    overlay's own ``in_progress`` became the merged catalogue's status. Python
    went ``provenance_declared`` -> ``in_progress``, which would have made
    EVERY python run emit "io-boundary results may be incomplete" — a second,
    different, and false disclosure riding along with the true one.
    """

    def test_python_keeps_provenance_declared_with_defaults_loaded(self):
        assert load_catalog("python", include_defaults=False).status == (
            load_catalog("python").status
        )
        assert load_catalog("python").status == "provenance_declared"

    def test_in_progress_languages_is_unmoved_by_defaults(self):
        from hypergumbo_core.io_boundary import in_progress_languages

        assert "python" not in in_progress_languages(["python"])

    def test_a_user_overlay_still_behaves_as_before(self, tmp_path: Path):
        """The narrow fix must not silently change user-overlay semantics.

        Whether a USER overlay should restate status is a separate question
        with its own measurement; this change is scoped to shipped defaults.
        """
        ov = tmp_path / "u.yaml"
        ov.write_text(
            "language: python\nstatus: overlay\n"
            "net_send:\n  - module: zzz\n    name: send\n    kind: function\n"
        )
        with_user = load_catalog(
            "python", overlay_paths=[ov], include_defaults=False,
        )
        assert with_user.status == "in_progress"


class TestAnUnvouchedRowAddsFindingsButNeverLicensesTheAllClear:
    """THE ASYMMETRY THIS WHOLE FEATURE RESTS ON.

    Shipping unvouched rows is safe in exactly one direction. A community row
    makes third-party egress VISIBLE, and adding a sink can only ever ADD a
    finding. But INV-buzab makes "the catalogue classified this call" mean
    "this call was EXAMINED", and an examined call stops blocking a clean
    verdict — so the same rows, left unmarked, would silently convert
    ``inconclusive`` into ``confirmed`` for boundaries they say nothing about.

    Concretely, and this was live before the fix: a repository calling
    ``requests.post`` would have had "never writes to the host filesystem"
    CONFIRMED, on the strength of rows describing only requests' NETWORK
    surface. That is INV-zubuh — "presence of SOME rows must not vouch for the
    rest" — arriving through a new door, and it is the false-all-clear
    direction, shipped by default to everyone.
    """

    def _coverage(self):
        from hypergumbo_core.verify_claims import compute_boundary_coverage

        return compute_boundary_coverage(
            REQUESTS_EDGE, {"python"}, {"python": load_catalog("python")},
        )

    def test_the_rows_are_marked_at_merge_not_trusted_from_the_file(self):
        """A file cannot claim to be vouched-for by omitting a key."""
        cat = load_catalog("python")
        unvouched = [p for p in cat.primitives if p.unvouched]
        assert unvouched, "no row was marked unvouched"
        assert all(p.module.startswith(("requests", "httpx", "aiohttp", "urllib3"))
                   for p in unvouched), sorted(
                       {p.module for p in unvouched})[:5]
        # ...and nothing hypergumbo DOES vouch for got swept up.
        assert not any(p.unvouched for p in cat.primitives if p.module == "os")

    def test_an_unvouched_classification_does_not_make_a_call_examined(self):
        coverage = self._coverage()
        assert coverage.complete is False, (
            "a call into requests was treated as EXAMINED on the strength of "
            "community rows hypergumbo does not vouch for, so a claim about "
            "some OTHER boundary could be confirmed"
        )
        assert "requests" in coverage.reason

    def test_the_control_a_vouched_row_still_examines(self):
        """Both directions. A gate that blocks everything proves nothing."""
        from hypergumbo_core.verify_claims import compute_boundary_coverage

        # `math` is the module python.yaml flags complete, backed by a dated
        # per-module audit — the shipped catalogue's own permitting case.
        edges = [{
            "type": "calls",
            "src": "python:app.py:1-3:main:function",
            "dst": "python:math:0-0:sqrt:external_symbol",
            "is_resolved": False,
            "meta": {"call_construct": "function"},
        }]
        coverage = compute_boundary_coverage(
            edges, {"python"}, {"python": load_catalog("python")},
        )
        assert coverage.complete is True, coverage.reason

    def test_the_detection_direction_is_unaffected(self):
        """The rows must still make the egress visible — that is the point."""
        cat = load_catalog("python")
        hit = [p for p in cat.primitives
               if p.module == "requests" and p.name == "post"]
        assert len(hit) == 1
        assert hit[0].boundary == "net_send"
        assert hit[0].unvouched is True


class TestTheEnvelopeCarriesTheThirdState:
    """``user_supplied`` is a boolean; this is a value it cannot express.

    "hypergumbo shipped it and does not vouch for it" is neither "shipped,
    therefore vouched" nor "the user supplied it". Collapsing it into the first
    overstates the rows; collapsing it into the second blames the user for a
    file they never wrote. Either re-opens the INV-zosun gap, so it gets its
    own key and ``user_supplied`` keeps meaning what it meant.
    """

    def test_shipped_defaults_do_not_set_user_supplied(self):
        from hypergumbo_core.verify_claims import catalog_provenance

        prov = catalog_provenance({}, ["python"])
        assert prov["user_supplied"] is False
        assert [r["file"] for r in prov["shipped_default"]] == [
            "python-http-clients.yaml",
        ]
        assert prov["shipped_default"][0]["retrieved"] == "2026-08-11"

    def test_a_language_with_no_default_contributes_nothing(self):
        from hypergumbo_core.verify_claims import catalog_provenance

        assert catalog_provenance({}, ["rust"])["shipped_default"] == []

    def test_the_text_renderer_discloses_it_too(self):
        """A disclosure that exists only under --json is half shipped.

        This file's own precedent (INV-karud a3), and here it is also the
        ruling's explicit condition: a JSON field does not satisfy "loud".
        """
        from hypergumbo_core.verify_claims import (
            catalog_provenance, render_catalog_provenance_text,
        )

        text = "\n".join(
            render_catalog_provenance_text(catalog_provenance({}, ["python"]))
        )
        assert "does not vouch for" in text
        assert "python-http-clients.yaml" in text
        assert "2026-08-11" in text
        assert "never license a clean verdict" in text

    def test_nothing_is_rendered_for_a_run_that_loaded_none(self):
        from hypergumbo_core.verify_claims import (
            catalog_provenance, render_catalog_provenance_text,
        )

        assert render_catalog_provenance_text(
            catalog_provenance({}, ["rust"])) == []
