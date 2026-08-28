# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two disclosures that are correct and unreadable (INV-hosul, WI-fosir).

Both sit on the same argument. ADR-0016 §4 lets a verdict be QUALIFIED rather
than withheld precisely because the caveat names what the analysis could not
see — the reader is trusted to act on the names. A caveat whose names are
malformed, or whose truncation drops the one name that matters, keeps the
verdict's licence while removing the thing that earned it.

INV-hosul — ``_launch_site_name`` joins ``module_path`` and ``name`` with a dot
so both branches of ``_opaque_launch_sites`` spell a site identically. For a
bash launch BOTH SLOTS HOLD THE COMMAND, so a bare name renders ``curl.curl``
and a path renders ``./scripts/auto-pr../scripts/auto-pr``. Observed live on the
self-survey: "launches an external program at 81 call site(s)
(./scripts/auto-pr../scripts/auto-pr, ./scripts/bump-version../scripts/bump-...".

WI-fosir — the uncatalogued-module list is ``sorted()`` and capped at five, so
alphabetically-early stdlib names deterministically evict third-party ones.
Measured on a fixture importing ten stdlib modules plus one ``requests.post``:
"12 module(s) that the I/O catalog could not classify (argparse, base64,
collections, csv, dataclasses (+7 more))". ``requests`` — the only module a
reader can act on and the one carrying the actual network risk — is in the
"+7 more".

WHY RANKING BY ``is_stdlib_module`` IS THE RIGHT TOOL HERE AND NOT ELSEWHERE.
That predicate is RECOGNITION, not examination, and using it to decide whether
a module was EXAMINED cost a P0 (INV-buzab). This is a DISPLAY ORDER, not a
claim: nothing about the verdict changes, the count is unchanged, and the full
list stays in the caveat's machine surface. Its failure mode also points the
right way — a module the recogniser has never heard of sorts as third-party,
which is exactly the name a reader most needs to see.
"""

from __future__ import annotations

from hypergumbo_core.io_boundary import IoBoundaryCatalog
from hypergumbo_core.verify_claims import (
    _launch_site_name,
    _rank_modules_for_disclosure,
    _render_capped_names,
    _MAX_REPORTED_UNCATALOGUED_MODULES,
)


class TestOpaqueLaunchSiteNames:
    """INV-hosul."""

    @staticmethod
    def _edge(module_path: str, name: str) -> dict:
        return {"meta": {"dst_ref": {"lang": "bash", "module_path": module_path,
                                     "name": name}}}

    def test_a_bare_command_is_not_doubled(self) -> None:
        assert _launch_site_name(self._edge("curl", "curl"),
                                 "bash:curl:0-0:curl:external_symbol") == "curl"

    def test_a_path_command_is_not_doubled(self) -> None:
        """The shape actually observed, and the one that reads as corruption
        rather than as repetition: the join lands a dot between two paths."""
        p = "./scripts/auto-pr"
        assert _launch_site_name(self._edge(p, p),
                                 f"bash:{p}:0-0:{p}:external_symbol") == p

    def test_a_genuine_module_and_name_still_join(self) -> None:
        """CONTROL. The dedup must not collapse a real ``module.name``, which
        is what the catalogue branch produces and what the two branches have to
        keep agreeing on."""
        assert _launch_site_name(self._edge("os", "system"),
                                 "python:os:0-0:system:external_symbol") == "os.system"

    def test_the_slot_fallback_dedups_too(self) -> None:
        """The ``dst_ref``-less branch spells sites for the same disclosure, so
        a fix on one branch only would make the two disagree — which is the
        exact hazard ``_launch_site_name``'s docstring exists to prevent."""
        assert _launch_site_name({}, "bash:curl:0-0:curl:external_symbol") == "curl"

    def test_a_missing_half_is_unchanged(self) -> None:
        assert _launch_site_name(self._edge("", "curl"),
                                 "bash::0-0:curl:external_symbol") == "curl"


class TestUncataloguedModuleRanking:
    """WI-fosir."""

    @staticmethod
    def _catalogs() -> dict[str, IoBoundaryCatalog]:
        return {"python": IoBoundaryCatalog(
            language="python", primitives=[],
            stdlib_modules=frozenset({
                "argparse", "base64", "collections", "csv", "dataclasses",
                "json", "logging", "os", "pathlib", "sys"}))}

    def test_the_third_party_module_survives_truncation(self) -> None:
        """THE DEFECT. Ten stdlib names and one ``requests``; five are shown."""
        mods = ["argparse", "base64", "collections", "csv", "dataclasses",
                "json", "logging", "os", "pathlib", "sys", "requests"]
        ranked = _rank_modules_for_disclosure(mods, self._catalogs())
        shown = ranked[:_MAX_REPORTED_UNCATALOGUED_MODULES]
        assert "requests" in shown, (
            f"the one actionable module was evicted by stdlib names: {shown}"
        )
        assert ranked[0] == "requests"

    def test_ordering_is_alphabetical_within_each_group(self) -> None:
        """Deterministic, so the disclosure does not churn between runs."""
        mods = ["sys", "os", "requests", "boto3", "argparse"]
        assert _rank_modules_for_disclosure(mods, self._catalogs()) == [
            "boto3", "requests", "argparse", "os", "sys"]

    def test_an_unrecognised_module_ranks_as_third_party(self) -> None:
        """The predicate is RECOGNITION, so its failure mode is a stdlib module
        it has not enumerated sorting first. For a display order that is the
        direction to fail in: the unfamiliar name is the one worth showing."""
        ranked = _rank_modules_for_disclosure(["os", "never_heard_of_it"],
                                              self._catalogs())
        assert ranked == ["never_heard_of_it", "os"]

    def test_all_stdlib_keeps_plain_alphabetical_order(self) -> None:
        """CONTROL. With nothing to promote, the output is what it always was."""
        mods = ["sys", "os", "json", "csv"]
        assert _rank_modules_for_disclosure(mods, self._catalogs()) == [
            "csv", "json", "os", "sys"]

    def test_ranking_does_not_change_the_count(self) -> None:
        mods = ["sys", "os", "requests"]
        assert len(_rank_modules_for_disclosure(mods, self._catalogs())) == 3


class TestCappedRendering:
    """The truncation string was written out three times; it is now one helper,
    so the three disclosures cannot drift in how they say '+N more'."""

    def test_under_the_cap_has_no_suffix(self) -> None:
        assert _render_capped_names(["a", "b"]) == "a, b"

    def test_at_the_cap_has_no_suffix(self) -> None:
        names = [f"m{i}" for i in range(_MAX_REPORTED_UNCATALOGUED_MODULES)]
        assert _render_capped_names(names) == ", ".join(names)

    def test_over_the_cap_counts_the_remainder(self) -> None:
        names = [f"m{i}" for i in range(_MAX_REPORTED_UNCATALOGUED_MODULES + 3)]
        out = _render_capped_names(names)
        assert out.endswith(" (+3 more)")
        assert out.count(",") == _MAX_REPORTED_UNCATALOGUED_MODULES - 1

    def test_empty_renders_empty(self) -> None:
        assert _render_capped_names([]) == ""
