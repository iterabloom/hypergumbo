# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which callees may CLOSE a §3a walk step, pinned as a census (INV-lupav).

WHY THIS FILE EXISTS. ``_ddg_taint_reaches`` reaches ``return False`` through
exactly four paths that continue without setting ``escaped``: the seen-set
cycle guard, ``followed`` with no call at the line, ``followed`` with a
terminating callee, and ``no_heir`` with a terminating callee. TWO of the four
rest entirely on :func:`_summary_terminates`, which is to say on the CATALOGUE
being right. Since WI-kabif a ``False`` removes a reported flow, so a wrong
entry here deletes a real security finding — and it does so invisibly, because
no coverage gate can help: the walk DID look, and believed what it was told.

Every other INV-lupav investigation went after extraction completeness. This
one guards the other half.

WHY A CENSUS AND NOT A PREDICATE. "Does this function really consume and
discard its arguments?" is not machine-checkable. What IS checkable is that the
SET does not grow without someone noticing: a new dead-end claim has to be
added here, which is the moment to ask whether it writes into a parameter. The
set shrinking is equally a review point. This is the same shape as the declared
constants with tests on them elsewhere in this package (R16).
"""
from __future__ import annotations

from hypergumbo_core.function_summaries import load_function_summaries
from hypergumbo_core.taint import _summary_terminates

#: Qualified entries whose summary licenses closing a walk step. Read back
#: against their real semantics on 2026-09-14. Adding a name here is a claim
#: that the callee consumes its arguments and hands them NOWHERE — not to a
#: return, not to a receiver, and not into one of its own parameters.
EXPECTED_TERMINATING = frozenset({
    "builtins.hasattr",
    "builtins.print",
    "console.log",
    "fmt.Print", "fmt.Printf", "fmt.Println",
    "github.com/stretchr/testify/require.Contains",
    "github.com/stretchr/testify/require.Equal",
    "github.com/stretchr/testify/require.Error",
    "github.com/stretchr/testify/require.NoError",
    "log.Fatal", "log.Fatalf", "log.Fatalln",
    "log.Print", "log.Printf", "log.Println",
    "logging.Logger.debug", "logging.Logger.error", "logging.Logger.exception",
    "logging.Logger.info", "logging.Logger.warning",
    "logging.debug", "logging.error", "logging.exception",
    "logging.info", "logging.warning",
    "net/http/fcgi.Serve",
    "os.Remove", "os.RemoveAll",
    "std::fs::read_to_string", "std::fs::write",
    "testing.Error", "testing.Errorf", "testing.Fatal", "testing.Fatalf",
    "testing.Log", "testing.Logf",
})


def _terminating_now() -> set[str]:
    """Qualified (non-alias) entries the shipped predicate accepts.

    The alias index is skipped deliberately: ``load_function_summaries`` also
    indexes every entry under its bare last component, so counting aliases
    would census ``log`` and ``print`` as if they were separate rulings.
    """
    summaries = load_function_summaries()
    return {
        name for name, s in summaries.items()
        if name == s.function and _summary_terminates(s)
    }


def test_the_terminating_set_is_exactly_the_census() -> None:
    """A dead-end claim may not enter or leave the catalogue unnoticed."""
    assert _terminating_now() == set(EXPECTED_TERMINATING)


def test_the_fprint_family_is_not_a_dead_end() -> None:
    """THE ONE THIS FILE WAS BUILT FOR, kept as its own assertion so the
    reason survives even if the census above is edited.

    ``fmt.Fprintf(&buf, "%s", x)`` leaves ``x`` in ``buf``; the caller reads it
    back. The entry carried ``side_effect: true`` — the positive dead-end
    marker — while its own comment said "writes to arg 0". Measured before the
    fix: ``secret -> Fprintf(&buf, ...) -> buf -> sink`` returned ``False``.
    """
    summaries = load_function_summaries()
    for name in ("fmt.Fprintf", "fmt.Fprintln"):
        assert not _summary_terminates(summaries[name]), name


def test_the_print_family_still_is() -> None:
    """CONTROL, and it is what stops the fix above from being a blanket
    disable: ``fmt.Printf`` writes to stdout and genuinely ends the chain, so
    it must still close a step. A change that made everything non-terminating
    would satisfy the previous test and be a silent regression in recall."""
    summaries = load_function_summaries()
    for name in ("fmt.Printf", "fmt.Println", "log.Println"):
        assert _summary_terminates(summaries[name]), name
