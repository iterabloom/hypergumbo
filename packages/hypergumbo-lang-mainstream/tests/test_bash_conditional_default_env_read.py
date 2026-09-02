# SPDX-License-Identifier: AGPL-3.0-or-later
"""A shell variable given a DEFAULT is still read from the environment (INV-sihom).

THE DEFECT, found by carrying measurement 0006's adjudicated labels forward
against a fresh collect and asking which USEFUL true positives the tool stopped
reporting. Seven of 27 had gone; three were bash ``environ -> redirect`` flows,
and two of those are this shape:

    gocryptfs/test.bash:17     if [[ -z ${TMPDIR:-} ]]; then TMPDIR=/var/tmp; fi
    guacamole-client .../500-generate-tomcat-catalina-base.sh:49
                               ...${WEBAPP_CONTEXT:-guacamole}.war

INV-jurif's env-read discriminator is "a variable expansion whose name is never
ASSIGNED in this file". That rule is right for a local and wrong for the
conditional-default idiom, which is one of the commonest ways a shell script
reads its environment: the script tests whether the environment supplied the
name and assigns a FALLBACK only when it did not. The assignment is evidence
the value MAY come from outside, and the old rule read it as proof that it
cannot.

``gocryptfs#2`` is worth naming because 0006's record already flagged it as
fragile: it was first labelled discard-only, and a second pass found
``exec 200> "$LOCKFILE"`` — a numbered-fd redirect to an env-derived path —
and reversed it. The reversal survived human review and was then lost to this.

THE SHELL'S OWN SEMANTICS DECIDE. ``${VAR:-x}`` means "if VAR is unset or null,
use x", so writing it is the script declaring VAR may arrive from outside. The
same holds for ``-``, ``:=``, ``=``, ``:?``, ``?``, ``:+`` and ``+``. It does
NOT hold for ``${VAR#p}``, ``${VAR%p}``, ``${VAR/a/b}`` or ``${#VAR}``, which
transform a value the script already has and say nothing about its origin.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.bash import analyze_bash

pytest.importorskip("tree_sitter_bash")


def _env_edges(tmp_path: Path, body: str) -> list:
    (tmp_path / "s.sh").write_text(body, encoding="utf-8")
    res = analyze_bash(tmp_path)
    return [
        e for e in res.edges
        if (e.dst or "").startswith("bash:env")
        or "environ" in str((e.meta or {}).get("io_primitive", ""))
    ]


class TestConditionalDefaultIsStillAnEnvironmentRead:
    def test_the_gocryptfs_shape_emits_a_source(self, tmp_path: Path) -> None:
        """The filed repro, reduced. Was 0 edges; must be at least 1."""
        edges = _env_edges(tmp_path, (
            "#!/bin/bash\n"
            "if [[ -z ${TMPDIR:-} ]]; then\n"
            "\tTMPDIR=/var/tmp\n"
            "fi\n"
            'echo "$TMPDIR" > /tmp/out\n'
        ))
        assert edges, "a conditionally-defaulted name is an environment read"

    def test_the_guacamole_shape_emits_a_source(self, tmp_path: Path) -> None:
        edges = _env_edges(tmp_path, (
            "#!/bin/bash\n"
            'ln -sf a.war "${WEBAPP_CONTEXT:-guacamole}.war"\n'
        ))
        assert edges

    @pytest.mark.parametrize(
        "expansion",
        ["${FOO:-d}", "${FOO-d}", "${FOO:=d}", "${FOO=d}",
         "${FOO:?e}", "${FOO?e}", "${FOO:+a}", "${FOO+a}"],
    )
    def test_every_default_operator_counts(
        self, tmp_path: Path, expansion: str,
    ) -> None:
        """All eight forms declare the name may be unset, so all eight say
        'this may come from outside'. Parametrised because picking only the
        two that appear in the cohort is how the next one gets missed."""
        edges = _env_edges(tmp_path, (
            f"#!/bin/bash\nFOO=local\necho \"{expansion}\" > /tmp/out\n"
        ))
        assert edges, f"{expansion} should mark FOO externally-supplied"


class TestTheOldRuleStillHoldsWhereItWasRight:
    """Non-vacuity floors. The fix must not make every local an env read."""

    def test_a_plain_local_is_not_an_environment_read(
        self, tmp_path: Path,
    ) -> None:
        edges = _env_edges(tmp_path, (
            '#!/bin/bash\nFOO=bar\necho "$FOO" > /tmp/out\n'
        ))
        assert edges == []

    def test_an_unassigned_name_is_still_an_environment_read(
        self, tmp_path: Path,
    ) -> None:
        """The control: the pre-existing rule must keep working."""
        edges = _env_edges(tmp_path, '#!/bin/bash\necho "$FOO" > /tmp/out\n')
        assert edges

    @pytest.mark.parametrize(
        "expansion", ["${FOO#p}", "${FOO%p}", "${FOO/a/b}", "${#FOO}"],
    )
    def test_a_TRANSFORMING_expansion_does_not_count(
        self, tmp_path: Path, expansion: str,
    ) -> None:
        """These operate on a value the script already holds and say nothing
        about where it came from. Treating them as evidence of external supply
        would turn every local string manipulation into a taint source."""
        edges = _env_edges(tmp_path, (
            f"#!/bin/bash\nFOO=local\necho \"{expansion}\" > /tmp/out\n"
        ))
        assert edges == []
