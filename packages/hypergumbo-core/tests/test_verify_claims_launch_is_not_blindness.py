# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-vokog: a door you NAMED is not a door you failed to look through.

ADR-0016 §4's fourth verdict — ``confirmed_with_caveats`` / rc 3, carrying
``CAVEAT_OPAQUE_BOUNDARY`` — exists because hypergumbo launches external
programs BY DESIGN, so plain ``confirmed`` is permanently unreachable for its
own self-proof. It is granted only when opaque launches are the SOLE remaining
blocker, a test spelled ``qualifying_only = not unknown``.

THE DEFECT: a launch put its OWN module into ``unknown``, so the condition
"launches are the only thing I could not see" was unsatisfiable whenever a
launch existed — and vacuous when none did. Measured on hypergumbo's own
self-survey (dev 8f416a87c3, 154,505 edges): **80 of 81** opaque launch sites
were simultaneously counted as uncatalogued modules.

THE ONE-EDGE POSITIVE CONTROL, which is what makes this a defect rather than a
coverage complaint — a single bash ``id`` launch, alone on the substrate::

    unknown = ['id']        opaque = ['id.id']        qualifying_only = False

One edge, both sets, self-cancelling. So the verdict added on 2026-08-13 for
exactly this consumer could never fire on the substrate it was built for, and
NO AMOUNT OF CATALOGUE WORK would have changed it.

ROOT CAUSE — LIVE.md's "ONE FACT, TWO HOMES", and INV-larol's shape exactly.
TWO channels know a call is a launch: the catalogue
(:meth:`IoBoundaryCatalog.declares_opaque_crossing`) and the PRODUCER STAMP
(``meta.io_boundary`` in :data:`PRODUCER_OPAQUE_BOUNDARIES`, which the bash
analyzer sets because ADR-0016 rules out a bash io_primitives catalogue).
:func:`_opaque_launch_sites` was taught to read the producer stamp FIRST, so
that opacity is structural rather than a favour the catalogue does.
:func:`_uncatalogued_external_modules` was never taught it and asks
``classify_call`` only. The python path was immune only INCIDENTALLY, because
``subprocess.run`` carries a row that ``classify_call`` matches —
``test_the_python_path_was_immune_only_by_luck`` pins that, since an immunity
nobody designed is one a catalogue edit can remove.

WHY SKIPPING IS THE HONEST DIRECTION, given it moves verdicts TOWARD
confirming. The skip does not hide anything: every site it stops counting as
blindness is still NAMED in the caveat, checkable against the source. What
changes is only which of two channels reports it — "I looked, control left the
process here, and here is the site" instead of "there is a module here I hold
no opinion about". Under-reporting an unverifiable door is the failure that
matters, and ``test_the_launch_is_still_disclosed_by_name`` is the
non-destruction assertion for it.
"""

from __future__ import annotations

from hypergumbo_core.io_boundary import IoBoundaryCatalog, IoPrimitive
from hypergumbo_core.verify_claims import (
    _opaque_launch_sites,
    _uncatalogued_external_modules,
    compute_boundary_coverage,
)


def _bash_catalog() -> IoBoundaryCatalog:
    """Shaped like the shipped ``bash.yaml``: it has rows, and NONE of them
    names a command. ADR-0016 forbids cataloguing shell commands, which is the
    whole reason the producer stamp is the only evidence these edges carry."""
    return IoBoundaryCatalog(
        language="bash",
        primitives=[
            IoPrimitive(boundary="fs_write", module="redirect", name=">",
                        kind="function"),
        ],
    )


def _py_catalog() -> IoBoundaryCatalog:
    return IoBoundaryCatalog(
        language="python",
        primitives=[
            IoPrimitive(boundary="subprocess", module="subprocess", name="run",
                        kind="function"),
            IoPrimitive(boundary="fs_read", module="pathlib.Path",
                        name="read_text", kind="method"),
        ],
        stdlib_modules=frozenset({"pathlib", "subprocess"}),
        module_completeness={"pathlib": "2026-08-12"},
    )


#: A bash command launch, exactly as the analyzer emits it. The producer stamp
#: is the ONLY thing marking this opaque — there is no catalogue row for ``id``
#: and ADR-0016 says there never will be.
LAUNCH = {
    "src": "bash:scripts/run.sh:3-3:main:function",
    "dst": "bash:id:0-0:id:external_symbol",
    "type": "calls",
    "meta": {"io_boundary": "command_launch"},
}

#: A genuinely uncatalogued third-party module — the population the gate exists
#: for. Must keep blocking.
REQUESTS = {
    "src": "python:app.py:1-5:handler:function",
    "dst": "python:requests:0-0:post:external_symbol",
    "type": "calls",
}


class TestALaunchIsNotABlindSpot:
    def test_a_producer_stamped_launch_is_not_an_uncatalogued_module(self) -> None:
        """THE DEFECT, at the one-edge resolution that makes it undeniable."""
        unknown = _uncatalogued_external_modules([LAUNCH], {"bash": _bash_catalog()})
        assert unknown == [], (
            f"the launch counted itself as blindness: {unknown}. It is disclosed "
            "as an opaque launch on the same edge, so counting it here makes "
            "qualifying_only unsatisfiable whenever a launch exists."
        )

    def test_the_launch_is_still_disclosed_by_name(self) -> None:
        """NON-DESTRUCTION. The skip must move which channel reports the door,
        never whether it is reported. A silent skip would be the strictly worse
        defect — it would convert a named opaque door into no finding at all."""
        assert _opaque_launch_sites([LAUNCH], {"bash": _bash_catalog()}) == ["id.id"]

    def test_qualifying_only_holds_when_a_launch_is_the_sole_blocker(self) -> None:
        """The end-to-end consequence: rc 3 becomes REACHABLE. Language set is
        scoped to the substrate on purpose — passing languages that produced no
        call edges lets the analyzer-blind check fire first and drive this False
        for an unrelated reason, which is a control passing for the wrong
        reason (hit and corrected while measuring this)."""
        coverage = compute_boundary_coverage(
            [LAUNCH], {"bash"}, {"bash": _bash_catalog()},
        )
        assert coverage.complete is False
        assert coverage.qualifying_only is True, coverage.reason
        assert coverage.opaque_sites == ["id.id"]


class TestTheGateStillRefusesRealBlindness:
    """Controls. A fix that made ``unknown`` unconditionally empty would pass
    every assertion above while destroying the gate INV-buzab/INV-zubuh built."""

    def test_a_genuinely_uncatalogued_module_still_blocks(self) -> None:
        unknown = _uncatalogued_external_modules([REQUESTS], {"python": _py_catalog()})
        assert unknown == ["requests"]

    def test_a_launch_does_not_launder_an_unrelated_blind_module(self) -> None:
        """The two populations must stay independent: a repo that both launches
        AND calls something unadjudicable is still blind, and the qualification
        is correctly withheld because the silence is ambiguous."""
        edges = [LAUNCH, REQUESTS]
        catalogs = {"bash": _bash_catalog(), "python": _py_catalog()}
        assert _uncatalogued_external_modules(edges, catalogs) == ["requests"]
        coverage = compute_boundary_coverage(edges, {"bash", "python"}, catalogs)
        assert coverage.qualifying_only is False, (
            "a real blind spot beside a launch must still withhold the "
            "qualified verdict — the reader cannot tell which gap produced the "
            "silence"
        )

    def test_the_python_path_was_immune_only_by_luck(self) -> None:
        """``subprocess.run`` never reached the uncatalogued set because it
        carries a catalogue row, not because anything understood it to be a
        launch. Pinned so a catalogue edit cannot silently reintroduce the
        defect on the python side."""
        run = {
            "src": "python:app.py:1-5:handler:function",
            "dst": "python:subprocess:0-0:run:external_symbol",
            "type": "calls",
        }
        catalogs = {"python": _py_catalog()}
        assert _uncatalogued_external_modules([run], catalogs) == []
        assert _opaque_launch_sites([run], catalogs) == ["subprocess.run"]
