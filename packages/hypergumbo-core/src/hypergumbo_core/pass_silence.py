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

**That drift was called LATENT. It is now OBSERVED** (WI-dukoh). The original
sizing — 490 surveys carrying 39,757 skip records, only TWO strings, because
the machine that produced them has every grammar installed — was honest about
its own blindness and wrong about the conclusion people drew from it. The
blindness was a venv, not a law: a venv built WITHOUT
``tree-sitter-language-pack``, surveying one ordinary repository, produces
**29 skip records in 28 distinct spellings** in a single run. The drift bites
a user in a bare environment, which is exactly who cannot report it to us —
so the bare environment was constructed instead of waited for.

``skip_reason_code`` is therefore a MIGRATION, not a new signal: the prose
``reason`` stays as human-readable detail (it carries the pip command and the
exception message, payload a code cannot express) and the code carries the
classification. One channel, two fields — never two channels, which is the
defect WI-finij named.

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
from collections.abc import Iterable, Mapping, Sequence, Sized
from typing import Final

#: The pass received zero input files. State A — nothing to find, and no
#: ordering or declaration mechanism would change it.
NO_CANDIDATE_FILES: Final[str] = "no_candidate_files"

#: Files were read and none contained the construct this pass looks for.
#: State A one level down: a GraphQL resolver linker scanning a repository
#: with no GraphQL resolvers correctly emits nothing, having opened the files
#: to find that out. Only a pass BODY may report this.
NO_CANDIDATE_CONSTRUCT: Final[str] = "no_candidate_construct"

#: The pass FOUND its construct and carried none of it through resolution.
#: State A's near neighbour and its opposite in consequence: an import whose
#: export is missing, a DI binding resolving to no symbol, a write with no
#: matching read. :data:`NO_CANDIDATE_CONSTRUCT` says the construct is not
#: there; this says it is there and the pass could not relate it to anything.
#: A consumer asking "is this pass useful on my repository?" needs them
#: separated: "you have no GraphQL resolvers" and "you have GraphQL resolvers
#: we could not connect to a schema" are opposite answers.
#:
#: WHAT IT DOES NOT CLAIM: why resolution failed. The counterpart may be
#: genuinely absent (a correct no-op) or the pass's own matcher may have missed
#: it (a recall defect), and a pass cannot tell those apart from the inside --
#: they need opposite responses, which is why this value names the pass's own
#: stage rather than the repository's contents. Only a pass BODY may report it.
CANDIDATES_UNRESOLVED: Final[str] = "candidates_unresolved"

#: A required grammar, parser or toolchain was absent.
DEPENDENCY_UNAVAILABLE: Final[str] = "dependency_unavailable"

#: An opt-in backend was not enabled. Distinct from
#: :data:`DEPENDENCY_UNAVAILABLE` precisely because the repository may well
#: contain the language's files — the backend simply did not run.
BACKEND_DISABLED: Final[str] = "backend_disabled"

#: A declared upstream pass did not run, AND it went missing for a reason
#: other than the repository lacking its files. State C — the ordering defect.
#:
#: **Instances are now observed (WI-dabup).** This used to read "no instance
#: has been observed", and that was true only of an environment where every
#: grammar is installed. In a venv without ``tree-sitter-rust``, surveying a
#: JS+Rust repository, ``tauri-ipc-linker`` and ``rust-trait-dispatch-linker``
#: are silent while Rust files sit in the tree — and before this value had a
#: producer they claimed :data:`NO_CANDIDATE_FILES`, which is declared to mean
#: "nothing to find, and no ordering or declaration mechanism would change it".
#: Installing the grammar changes it. The axis was asserting something false,
#: which is worse than abstaining, and is why the producer was built rather
#: than the value deprecated.
#:
#: Produced by :func:`prerequisite_absent_clauses` at the linker chokepoint,
#: and ONLY where the pass body left the field empty — a body that spoke keeps
#: its word.
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
    CANDIDATES_UNRESOLVED,
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


def silence_reason_for_candidates(candidates: "Sized") -> str:
    """A pass body's positive claim about what its own scan found.

    :func:`derive_silence_reason` refuses to infer either of this function's
    two values, because the orchestrator cannot know what a pass was looking
    for; only the body can say. This is the producer side of that refusal, and
    the chokepoints leave a body-supplied reason alone, so a call here survives
    to the output.

    ``candidates`` MUST be what the pass's own scan FOUND — the patterns, the
    bindings, the declaration sites — and never what it finally EMITTED. The
    two differ exactly where it matters. A linker that emits one symbol per
    candidate is silent precisely when it found none, so for it the
    distinction is invisible. A linker that emits only PAIRING edges is also
    silent when it found candidates on one side and none on the other, and
    there the construct IS present.

    Empty gives :data:`NO_CANDIDATE_CONSTRUCT`; non-empty gives
    :data:`CANDIDATES_UNRESOLVED`. **Both are positive claims**, and the second
    one is why this function was rewritten. It used to return ``""`` on the
    non-empty branch — asserting nothing, and sending a run whose body had just
    measured the answer into :data:`UNREPORTED`, which is declared to mean the
    pass "did not say why". A producer that computes a fact and discards it is
    the INV-bikaj complaint one level down, inside the axis built to cure it.

    The superseded ruling in this docstring argued against declaring the second
    value on three grounds, each of which the tree refutes: it would be a
    "seventh" member (the vocabulary already held seven), with "a single
    producer" (eleven call sites through this helper — the same shape as
    :data:`NO_CANDIDATE_CONSTRUCT`, which is considered good design) and "no
    consumer" (:func:`summarize_silence` counts by whatever string it finds and
    :func:`format_silence_summary` prints every reason by name, both wired into
    the CLI). Recorded in ``docs/adr/0054-pass-silence-candidates-unresolved.md``.

    Note it never returns ``""``: a pass that emitted has no silence to explain,
    and the chokepoints clear a body claim in that case without the body having
    to know its own output.
    """
    return NO_CANDIDATE_CONSTRUCT if not len(candidates) else CANDIDATES_UNRESOLVED


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


# ---------------------------------------------------------------------------
# The skipped_passes half of the axis (WI-dukoh / ADR-0056 W2)
#
# ``silence_reason`` lives on ``AnalysisRun`` and answers "this pass RAN and
# emitted nothing — why?". Three of the axis's values could never be stamped
# there, for a structural reason rather than an accidental one: a pass that is
# `dependency_unavailable`, `backend_disabled` or `pass_crashed` HAS NO
# AnalysisRun. ``all_analyzers`` returns early or appends a skip (the sole
# ``analysis_runs.append`` sits in the ``else`` of ``if is_skipped:``), and a
# crashing linker goes to ``_record_linker_crash`` and continues. There is no
# carrier in existence to stamp. Their home is ``limits.skipped_passes``,
# where their free-text twins already lived.
#
# THE INIT-FAILURE RULING. Six producer sites report a parser that failed to
# INITIALISE (``"Failed to load Go parser: {e}"`` and kin) — the grammar is
# installed and construction raised. That is NOT `dependency_unavailable`:
# "install the package" is the wrong advice for it. It is stamped
# :data:`PASS_CRASHED`, whose declaration is "the pass raised and the crash was
# contained" — true of a caught constructor exception, and the value says
# nothing about WHO contained it. This gives `pass_crashed` real producers;
# ADR-0056 recorded it as speculative because its free-text twin had fired zero
# times in 229,541 records, and that was measuring only the twin.
# ---------------------------------------------------------------------------


def summarize_skip_reasons(
    skipped_passes: Iterable[Mapping[str, object]],
) -> dict[str, int]:
    """Count ``limits.skipped_passes`` entries by their structured code.

    An entry with NO ``skip_reason_code`` counts as :data:`UNREPORTED`, and
    that choice is the whole discipline of this function. The obvious
    alternative — bucketing a missing code under :data:`NO_CANDIDATE_FILES`,
    which is 98.94% of all skip records — would manufacture the majority answer
    on behalf of every producer that has not been converted, turning "this
    producer did not classify itself" into "the repository lacks those files".
    That is the absent-versus-empty substitution the axis exists to cure, and
    the migration is exactly when it would be easiest to commit.

    A code OUTSIDE the vocabulary is counted under its own name rather than
    dropped, so a drifted producer cannot be laundered into a clean count.
    """
    counts: dict[str, int] = {}
    for entry in skipped_passes:
        code = entry.get("skip_reason_code")
        key = str(code) if code else UNREPORTED
        counts[key] = counts.get(key, 0) + 1
    return counts


def format_skip_summary(
    counts: Mapping[str, int], *, total_passes: int
) -> str | None:
    """Render the one-line skipped-passes summary, or ``None`` when none is due.

    Same discipline as :func:`format_silence_summary`: ``None`` on empty so a
    run that skipped nothing generates no chatter, most-common-first with
    alphabetical tie-breaking so two surveys diff without spurious churn, and
    the line names WHERE TO LOOK rather than only that something happened.
    """
    if not counts:
        return None
    skipped = sum(counts.values())
    parts = ", ".join(
        f"{code}={n}"
        for code, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    return (
        f"[passes] {skipped} of {total_passes} did not run: {parts} "
        f"(per-pass detail in limits.skipped_passes)"
    )


def emit_skip_summary(
    skipped_passes: Sequence[Mapping[str, object]], *, ran: int = 0,
) -> None:
    """Write the skipped-passes summary to stderr; silent when nothing skipped.

    The reader ``skipped_passes`` has never had. It has been serialized on
    every survey since the field existed and **nothing summarized it** — so the
    2,383 ``backend_disabled`` records and the 60 ``dependency_unavailable``
    ones sat in artifacts nobody was shown. Landing a structured code without
    this would have been a vocabulary with no consumer, which is the same
    defect as a value with no producer, one step to the right.
    """
    # The denominator is passes ATTEMPTED — those that produced a run plus
    # those that did not. A share of the runs alone would exclude exactly the
    # population being reported and read as >100%.
    line = format_skip_summary(
        summarize_skip_reasons(skipped_passes),
        total_passes=ran + len(skipped_passes),
    )
    if line is not None:
        sys.stderr.write(line + "\n")


def prerequisite_absent_clauses(
    depends_on: "Sequence[Sequence[str]]",
    skip_reason_codes: Mapping[str, str],
) -> list[list[str]]:
    """Conjuncts a pass declared, that went missing for a TOOLCHAIN reason.

    The producer :data:`PREREQUISITE_ABSENT` never had (WI-dabup / ADR-0056 W3),
    and the reason it took three steps to build is that **the obvious rule is
    wrong**. "Stamp it when a ``depends_on`` conjunct is unsatisfied" fails on
    measurement: 98.94% of 229,541 skip records are ``no files matched``, so an
    unsatisfied conjunct nearly always means *the repository lacks that
    language* — State A, a correct no-op. Stamping that as an ordering defect
    would make the field assert a defect about a repo that simply has no Rust
    in it: the axis's founding sin, committed inside the axis built to cure it.

    **The discriminator is not "is the conjunct satisfied" but "was the missing
    literal skipped for a FILE reason or a TOOLCHAIN reason"** — which is
    precisely what ``skip_reason_code`` made askable, and why W2 had to land
    first.

    A conjunct qualifies when EVERY literal in it is absent from the run
    (present in ``skip_reason_codes``) and AT LEAST ONE of them went missing
    for something other than :data:`NO_CANDIDATE_FILES`. The disjunction is
    honoured both ways: one surviving member satisfies the clause, and a clause
    all of whose members are merely file-absent stays State A.

    An :data:`UNREPORTED` prerequisite COUNTS as a toolchain reason. That is
    the conservative direction and it follows from the axis's own distinction:
    a producer that did not classify itself has not said the repository lacked
    the files, so waving it through as State A would be reading "cannot
    determine" as "not applicable".

    **What it structurally cannot see.** Only ANALYZER passes are enumerated in
    ``limits.skipped_passes`` — the spec is explicit that a linker with no
    applicable targets is a correct no-op, not a pass that did not run. A
    clause naming a LINKER (``inheritance-linker`` is one) can therefore never
    be observed missing and always reads satisfied. That under-reports rather
    than inventing an ordering defect out of a population this channel does not
    cover, which is the right way round for a disclosure field.

    Args:
        depends_on: The pass's CNF declaration — outer AND of inner ORs. See
            ``catalog.Pass.depends_on`` for the schema and
            ``catalog.validate_pass_dependencies`` for the satisfaction
            semantics this mirrors.
        skip_reason_codes: ``{pass_id: skip_reason_code}`` for every pass that
            did NOT run, from ``limits.skipped_passes``. A pass absent from
            this mapping ran.

    Returns:
        The offending conjuncts, in declaration order. Empty means no stamp is
        due and the caller falls through to :func:`derive_silence_reason`.
    """
    blocked: list[list[str]] = []
    for clause in depends_on:
        if not clause:
            continue
        if not all(literal in skip_reason_codes for literal in clause):
            continue
        if any(
            skip_reason_codes[literal] != NO_CANDIDATE_FILES for literal in clause
        ):
            blocked.append(list(clause))
    return blocked
