# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical pass-silence-reason axis (INV-bikaj / INV-hujog, arc T6).

Why this axis exists
--------------------
INV-bikaj's headline — "61 of 84 AnalysisRun passes produce 0 edges" — is a
count of an UNDIFFERENTIATED population. A pass that emitted no edges is in
one of three states that need opposite responses, and before this field a
reader of a survey could not tell which one they were looking at:

    A  ran, and there was genuinely nothing to find   (correct, uninteresting)
    B  ran, and could not find what was there         (a recall defect)
    C  did not really run — a prerequisite was absent (an ordering defect)

Sizing the population (2026-09-11, five repositories, 405 pass-runs) found
**274 of 322 zero-edge pass-runs analysed ZERO FILES** — state A, and no
mechanism changes them — 26 emitted nodes but no edges (not silent at all),
and only **22 were truly silent**: they opened files and produced nothing.
Those 22 are the population this axis exists to explain, and they had nowhere
to record a reason, structurally rather than accidentally: the spec
deliberately excludes non-analyzer passes from ``limits.skipped_passes``
("a linker with no applicable targets is a correct no-op, not a pass that did
not run"), so a silent linker was invisible by design.

The axiom
---------
**A pass-silence reason names WHY a pass produced no symbols and no edges; it
never names what the pass would have produced, nor what it cost.** The axiom
rejects ``slow`` and ``low_value`` (cost, not reason) and
``would_have_found_graphql`` (a counterfactual about output).

Why a registry and not a free-text string
-----------------------------------------
Because the free-text version is already in the tree and has already drifted.
``limits.skipped_passes[].reason`` carries prose, and across ``packages/*/src/``
there are **nineteen distinct spellings** — ten of which mean "the grammar is
missing" (``"{lang} tree-sitter grammar not available"``,
``"tree-sitter-language-pack not available"``, ``"tree-sitter-kotlin not
available"``, ``"java analysis skipped: grammar not available. "`` with a
trailing space, one embedding a pip command …) and five of which mean "the
parser failed to initialise". A consumer cannot ask "was this skipped for a
missing grammar?" without matching ten spellings.

**That drift is LATENT, not observed** — honest sizing, taken before this
module was written: across 490 surveys on disk carrying 39,757 skip records,
only TWO strings ever appear (``"no files matched"`` 39,268 and
``"rust-analyzer backend not enabled"`` 489), because the machine that
produced them has every tree-sitter grammar installed, so the ten-spelling
family is unreachable there by construction. The drift bites a user in a bare
environment, which is exactly who cannot report it to us. This axis is
therefore declared for the NEW field rather than retrofitted onto the old
one; retrofitting ~29 analyzer files is separately payable work.

The empty string is NOT a member, and that is the design
--------------------------------------------------------
``AnalysisRun.silence_reason == ""`` means NOT APPLICABLE — the pass emitted
something, so there is no silence to explain. :data:`UNREPORTED` means CANNOT
DETERMINE — the pass was silent and did not say why. Collapsing the two would
repeat a defect this project has already paid for once (a relative
``--tracker-root`` read as "not a repo" fell through to a writable config
because "cannot determine" and "not applicable" looked alike).

Why the orchestrator does not guess
-----------------------------------
:func:`derive_silence_reason` stamps only what is derivable with CERTAINTY
from counters the orchestrator already holds. It never infers
:data:`NO_CANDIDATE_CONSTRUCT`, because only the pass body knows whether it
looked for a construct and failed to find one; a reason invented on the
producer's behalf would be a fabricated disclosure, which is worse than none.
The residue is stamped :data:`UNREPORTED`, which makes it COUNTABLE instead of
invisible and leaves a later pass to drain it producer by producer.
"""
from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import Final

#: The pass received zero input files. State A — nothing to find, and no
#: ordering or declaration mechanism would change it.
NO_CANDIDATE_FILES: Final[str] = "no_candidate_files"

#: Files were read and none contained the construct this pass looks for.
#: State A one level down: a GraphQL resolver linker scanning a repository
#: with no GraphQL resolvers correctly emits nothing, having opened the files
#: to find that out. Only a pass BODY may report this.
NO_CANDIDATE_CONSTRUCT: Final[str] = "no_candidate_construct"

#: A required grammar, parser or toolchain was absent.
DEPENDENCY_UNAVAILABLE: Final[str] = "dependency_unavailable"

#: An opt-in backend was not enabled. Distinct from
#: :data:`DEPENDENCY_UNAVAILABLE` precisely because the repository may well
#: contain the language's files — the backend simply did not run.
BACKEND_DISABLED: Final[str] = "backend_disabled"

#: A declared upstream pass did not run. State C — the ordering defect. No
#: instance has been observed; the value is declared so that an instance
#: would have somewhere to land rather than being folded into a neighbour.
PREREQUISITE_ABSENT: Final[str] = "prerequisite_absent"

#: The pass raised and the crash was contained (fail-open, WI-madal L3).
PASS_CRASHED: Final[str] = "pass_crashed"

#: The pass produced nothing and did not say why. The honest residue: it is
#: CANNOT-DETERMINE, never NOT-APPLICABLE, and its count is the size of the
#: remaining work.
UNREPORTED: Final[str] = "unreported"

#: The canonical, closed pass-silence vocabulary. Computed-not-derived (like
#: ``visibility``), exposed via :func:`all_pass_silence_reason_names` and
#: wired into ``multi_value_field_axis._known_axes`` so the
#: ``# axis: pass-silence-reason`` field annotation resolves like every other.
SILENCE_REASONS: Final[frozenset[str]] = frozenset({
    NO_CANDIDATE_FILES,
    NO_CANDIDATE_CONSTRUCT,
    DEPENDENCY_UNAVAILABLE,
    BACKEND_DISABLED,
    PREREQUISITE_ABSENT,
    PASS_CRASHED,
    UNREPORTED,
})


def all_pass_silence_reason_names() -> frozenset[str]:
    """Single source of truth for the pass-silence vocabulary.

    Mirrors ``visibility.all_known_visibility_levels`` /
    ``catalog.all_known_languages``; wired into
    ``multi_value_field_axis._known_axes`` so a
    ``# axis: pass-silence-reason`` annotation can be resolved.
    """
    return SILENCE_REASONS


def derive_silence_reason(
    *,
    files_analyzed: int,
    nodes_emitted: int,
    edges_emitted: int,
) -> str:
    """Stamp the silence reason derivable from the orchestrator's own counters.

    Returns ``""`` when the pass emitted anything — NOT APPLICABLE, there is
    no silence to explain. A pass that emitted nodes but no edges is NOT
    silent (26 of the 48 file-reading zero-edge pass-runs in the sizing were
    exactly this, and calling them silent would have been the headline's
    original error repeated one level down).

    Returns :data:`NO_CANDIDATE_FILES` when the pass saw no input at all —
    the only silence the orchestrator can attribute with certainty.

    Returns :data:`UNREPORTED` otherwise: the pass opened files and produced
    nothing, and the orchestrator does not know why. This is the population
    T6 exists to explain, and naming it ``no_candidate_files`` to make the
    output look complete would be the field lying about exactly those runs.
    """
    if nodes_emitted or edges_emitted:
        return ""
    if files_analyzed == 0:
        return NO_CANDIDATE_FILES
    return UNREPORTED


def summarize_silence(analysis_runs: Iterable[Mapping[str, object]]) -> dict[str, int]:
    """Count serialized pass-runs by their recorded silence reason.

    Takes the SERIALIZED runs (``AnalysisRun.to_dict()`` output, which is what
    the orchestrator holds by the time anything wants to report on them), so a
    caller never has to reconstruct dataclasses to read the field back.

    A run with no ``silence_reason`` key emitted something and is NOT counted:
    its absence is NOT APPLICABLE, not an unknown reason, and folding the two
    together here would undo the distinction the field exists to draw.

    A value OUTSIDE the vocabulary is counted under its own name rather than
    dropped or bucketed. Dropping it would make a drifted producer look like a
    clean run — the failure mode this axis was declared to prevent.
    """
    counts: dict[str, int] = {}
    for run in analysis_runs:
        reason = run.get("silence_reason")
        if not reason:
            continue
        key = str(reason)
        counts[key] = counts.get(key, 0) + 1
    return counts


def format_silence_summary(
    counts: Mapping[str, int], *, total_passes: int
) -> str | None:
    """Render the one-line silence summary, or ``None`` when nothing was silent.

    Returns ``None`` on an empty summary so a corpus where every pass produced
    output generates no chatter — the same discipline as
    ``spec_validator.emit_stderr_summary``, which is silent on a clean run.

    Reasons are ordered most-common-first, ties broken alphabetically, so the
    line is DETERMINISTIC across runs and two surveys can be diffed without
    spurious churn (the elixir non-determinism of WI-jozap is a standing
    reminder that arbitrary iteration order is a measurement hazard, not a
    cosmetic one).

    The line names where to look rather than only that a problem exists: a
    reader who sees a non-zero ``unreported`` needs the per-pass detail in
    ``analysis_runs[].silence_reason`` to act on it.
    """
    if not counts:
        return None
    silent = sum(counts.values())
    parts = ", ".join(
        f"{reason}={n}"
        for reason, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    return (
        f"[passes] {silent} of {total_passes} emitted nothing: {parts} "
        f"(per-pass detail in analysis_runs[].silence_reason)"
    )


def emit_silence_summary(analysis_runs: Sequence[Mapping[str, object]]) -> None:
    """Write the one-line silence summary to stderr; silent when none is due.

    This is the CONSUMER half of the axis, and it is the half that discharges
    the observability complaint INV-nanon actually raised. Stamping the field
    made the reason RECORDABLE; without a surface that reads it back, the
    survey would carry a value nobody is shown — and a disclosure nothing reads
    is not a disclosure.
    """
    line = format_silence_summary(
        summarize_silence(analysis_runs), total_passes=len(analysis_runs)
    )
    if line is not None:
        sys.stderr.write(line + "\n")
