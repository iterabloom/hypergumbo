# SPDX-License-Identifier: AGPL-3.0-or-later
"""The finalize stage: the single pre-serialization reconcile point (ADR-0043 §6/§6.1).

`finalize(ctx)` is the one place where placeholder-derived AnalysisRun fields are
reconciled against the *final* node/edge set, just before the behavior map is projected
and serialized. Its **body is the order contract** — a flat, hand-ordered call list, not a
registry or a toposort. ADR-0043 §6 deliberately rejected registry/Protocol/toposort
machinery: the finalize DAG is small and closed (it will not grow at runtime), so a single
legible function whose call order *is* the spec is more honest than reconstructing the
order from capability tags. The two load-bearing dependencies are visible as adjacent call
sites rather than declared edges:

* **R2 (hard):** `_finalize_recompute_run_signature` runs strictly *after*
  `_finalize_stamp_run_lifecycle` — else it would re-hash a signature over fields a later
  stamp still changes. Pinned by a white-box ordering test, not just by position.
* **R3 (hard):** `_finalize_referential_integrity` is the *last* violation-appending
  sub-step — it validates exactly the substrate that serializes (§7). Pinned by a test.

Entry precondition (R1): the node/edge set is final on entry (Phase D filtering + Phase E
boundary synthesis already ran; see ``cli.run_behavior_map``). finalize never changes set
membership; it reconciles fields and commits the reconciled view into ``behavior_map``.

What this carrier (run-lifecycle:F1) implements vs. defers
----------------------------------------------------------
* **Fully implemented:** re-relativize backstop (1); pass_version backfill (2, the WI-mipul
  pass_version closure); run_signature recompute (3, the META-hufaz/WI-luzud headline —
  re-hash each AR from its *final* config_fingerprint/toolchain); repo_fingerprint stamp
  (4); skipped→limits (6); commit-dicts (8); referential-integrity validate_ir lift (10).
* **Documented stub slots** (filled in Phase 2 with zero orchestrator change, per §6.1):
  confidence aggregates (7 → confidence:F1/F2) and declared-fields (9 → declared-fields:
  F1(a)/F5). These have named Phase-2 owners; they are NOT dead code.
* **Deliberately absent:** an ``emission_counts`` sub-step was removed (not stubbed) — the
  ratified "recompute files_analyzed by origin_run_id" mechanism is unsound (files_analyzed
  is contractually a file count == profile.files; the recompute yields a node/path count and
  does not close INV-gizik, whose real fix is a new provenance field). And the
  config_fingerprint backstop-with-violation is deferred to WI-mipul's producer-side work
  rather than recorded here. Both are tracked (INV-gizik, WI-mipul); git carries the history.
  See the ADR-0043 §6.1 amendment that lands with this module.

`FinalizedMap` is a shallow ``frozen=True`` handle (ratified §6 #6): rebinding a field
raises, but the inner dicts remain mutable so the downstream budget-tier/compact
projections (still in ``cli`` for the carrier; rewired to a pure ``re_derive_view`` in
projection:F1) can run after finalize returns. Consumers must honor a no-mutation contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .ir import _compute_run_signature
from .pass_metadata import PassMetadataLookup
from .repo_fingerprint import compute_repo_fingerprint
from .spec_validator import build_validation_report, validate_ir

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .ir import Edge, Symbol, UsageContext
    from .limits import Limits


def _relativize_ir_paths(
    repo_root: Path,
    symbols: "list[Symbol]",
    edges: "list[Edge]",
    usage_contexts: "list[UsageContext]",
) -> None:
    """Rewrite absolute paths under ``repo_root`` to repo-relative in IR objects in place.

    WI-hopug: the behavior map JSON output embeds each Symbol's absolute file path in its
    ``id`` and Edge endpoints, which makes the output non-portable across machines and
    branches that hold the same repo under different checkout roots. Stripping the
    ``str(repo_root) + "/"`` prefix from every id-bearing string collapses those IDs to
    repo-relative form so two runs of the same commit on two machines produce
    byte-identical behavior maps (modulo analyzer nondeterminism).

    Paths that do not sit under ``repo_root`` — external-library symbols, synthetic module
    hints like ``python:external:0-0:foo:unresolved``, and the like — never match the
    prefix and are left untouched. ``UsageContext.id`` is a sha256 over
    ``path:start_line:context_name:position``; because ``path`` is part of the preimage we
    recompute the id from the relativized path so the hash is stable across machines.

    Runs once at Phase B (``cli``) and again as finalize sub-step 1 — the second call is an
    idempotent backstop catching any absolute path minted after Phase B (e.g. by a linker
    or boundary synthesis); on already-relative paths it is a no-op.
    """
    prefix = str(repo_root) + "/"
    for sym in symbols:
        if prefix in sym.id:
            sym.id = sym.id.replace(prefix, "")
        if sym.path and prefix in sym.path:
            sym.path = sym.path.replace(prefix, "")
    for edge in edges:
        if prefix in edge.src:
            edge.src = edge.src.replace(prefix, "")
        if prefix in edge.dst:
            edge.dst = edge.dst.replace(prefix, "")
    for uc in usage_contexts:
        path_was_absolute = prefix in uc.path
        if path_was_absolute:
            uc.path = uc.path.replace(prefix, "")
            from .ir import _compute_usage_context_id
            uc.id = _compute_usage_context_id(
                uc.path, uc.span.start_line, uc.context_name, uc.position,
            )
        if uc.symbol_ref and prefix in uc.symbol_ref:
            uc.symbol_ref = uc.symbol_ref.replace(prefix, "")


@dataclass
class FinalizeContext:
    """Mutable carrier threaded through the finalize sub-steps.

    ``symbols`` are the ranked Symbol objects (output order); ``analysis_runs`` is a list
    of ``AnalysisRun.to_dict()`` dicts — note the JSON key is ``"pass"``, NOT ``"pass_id"``
    (a Python-keyword alias; the recurring footgun). ``violations`` accumulates across
    sub-steps and is rendered into ``behavior_map["validation_report"]`` by the orchestrator.
    """

    symbols: "list[Symbol]"
    edges: "list[Edge]"
    usage_contexts: "list[UsageContext]"
    analysis_runs: list[dict]
    behavior_map: dict
    limits: "Limits"
    repo_root: Path
    pass_metadata: PassMetadataLookup
    violations: list = field(default_factory=list)
    repo_fingerprint: str = ""  # set by sub-step 4; surfaced by _freeze


@dataclass(frozen=True)
class FinalizedMap:
    """The single reconciled view §8's round-trip test asserts on (shallow ``frozen``)."""

    behavior_map: dict
    nodes: tuple
    edges: tuple
    repo_fingerprint: str
    validation_report: dict


def _finalize_re_relativize(ctx: FinalizeContext) -> None:
    """Sub-step 1 — idempotent re-relativize backstop (§2). No-op on relative paths."""
    _relativize_ir_paths(ctx.repo_root, ctx.symbols, ctx.edges, ctx.usage_contexts)


def _finalize_stamp_run_lifecycle(ctx: FinalizeContext) -> None:
    """Sub-step 2 — backfill empty ``pass_version`` from pass_metadata (WI-mipul).

    The ~13 override-``analyze()`` analyzers + the enclosure synthetic emit an empty
    ``pass_version`` (they bypass ``_analyze_body``'s auto-stamping). pass_metadata carries
    the canonical code-hash for every pass_id, so we fill the empty ones here. Pure fill —
    never overwrites a present value — so it is shrink-only w.r.t. the validation ratchet.
    """
    for run in ctx.analysis_runs:
        if not run.get("pass_version"):
            meta = ctx.pass_metadata.get(run["pass"])
            if meta is not None and meta.pass_version:
                run["pass_version"] = meta.pass_version


def _finalize_recompute_run_signature(ctx: FinalizeContext) -> None:
    """Sub-step 3 — re-hash each AR's run_signature from its FINAL fields (META-hufaz).

    ``AnalysisRun.create`` hashes run_signature at construction from placeholder
    config_fingerprint/toolchain (ir.py); analyzers then stamp the real values but never
    re-hash, so the emitted signature is stale (META-hufaz / WI-luzud). Recompute from the
    AR dict's final ``pass``/``version``/``config_fingerprint``/``toolchain``. R2: strictly
    after the stamp sub-step (so the inputs are final before they are hashed).
    """
    for run in ctx.analysis_runs:
        run["run_signature"] = _compute_run_signature(
            run["pass"], run["version"], run["config_fingerprint"], run["toolchain"],
        )


def _finalize_repo_fingerprint(ctx: FinalizeContext) -> None:
    """Sub-step 4 — stamp the spec-defined repo_fingerprint into every AR (INV-tofur)."""
    repo_fp = compute_repo_fingerprint(ctx.repo_root)
    ctx.repo_fingerprint = repo_fp
    for run in ctx.analysis_runs:
        if run.get("repo_fingerprint") is None:
            run["repo_fingerprint"] = repo_fp


def _finalize_skipped_into_limits(ctx: FinalizeContext) -> None:
    """Sub-step 6 — drain skipped-file counts into limits.partial_results_reason.

    Don't clobber a reason already set by ``record_crashed_pass`` (WI-madal L3): a crashed
    pass is the more severe signal; the file-skip note only fills an otherwise-empty summary.
    """
    for run in ctx.analysis_runs:
        if run.get("files_skipped", 0) > 0 and not ctx.limits.partial_results_reason:
            ctx.limits.partial_results_reason = "some files skipped during analysis"
    ctx.behavior_map["limits"] = ctx.limits.to_dict()


def _finalize_confidence_aggregates(ctx: FinalizeContext) -> None:
    """Sub-step 7 — STUB slot for confidence:F1/F2 (Phase 2). No-op in the carrier.

    Per-edge ``Edge.confidence`` is left untouched (ADR-0039). The confidence family fills
    this slot with zero orchestrator change.
    """


def _finalize_commit_dicts(ctx: FinalizeContext) -> None:
    """Sub-step 8 — commit the reconciled IR into behavior_map as one view."""
    ctx.behavior_map["analysis_runs"] = ctx.analysis_runs
    ctx.behavior_map["nodes"] = [s.to_dict() for s in ctx.symbols]
    ctx.behavior_map["edges"] = [e.to_dict() for e in ctx.edges]
    ctx.behavior_map["usage_contexts"] = [uc.to_dict() for uc in ctx.usage_contexts]


def _finalize_declared_fields(ctx: FinalizeContext) -> None:
    """Sub-step 9 — STUB slot for declared-fields:F1(a)/F5 (Phase 2). No-op in the carrier.

    The writer/population-contract re-check over the final stamped substrate lands here; the
    carrier relies on the existing validate_ir writer-contract subset run in sub-step 10.
    """


def _finalize_referential_integrity(ctx: FinalizeContext) -> None:
    """Sub-step 10 — validate the emitted substrate (§7). STRUCTURALLY LAST (R3).

    Lifts the existing ``validate_ir`` call (axis/writer-contract/cross-field/verdict/
    id-format/stable-id/round-trip). The ADR-0037 referential-integrity FK predicate itself
    lands later (validator:F3); this carrier preserves today's validation behavior, moved to
    its structurally-last home so it sees exactly the substrate that serializes.
    """
    ctx.violations.extend(validate_ir(ctx.symbols, ctx.edges, ctx.analysis_runs))


def _violation_sort_key(v) -> tuple:
    """Total, deterministic order for validation violations (ADR-0043 §6 determinism).

    Sorting before the report is built makes the emitted ``validation_report.violations``
    array independent of symbol-iteration order (e.g. ranked vs analyzer-append), so two
    runs of the same commit serialize the array identically.
    """
    return (
        v.validator_class,
        v.severity,
        v.field_name or "",
        v.record_id or "",
        v.message,
    )


def _freeze(ctx: FinalizeContext) -> FinalizedMap:
    """Build the immutable reconciled handle from the committed behavior_map."""
    return FinalizedMap(
        behavior_map=ctx.behavior_map,
        nodes=tuple(ctx.behavior_map.get("nodes", [])),
        edges=tuple(ctx.behavior_map.get("edges", [])),
        repo_fingerprint=ctx.repo_fingerprint,
        validation_report=ctx.behavior_map.get("validation_report", {}),
    )


def finalize(ctx: FinalizeContext) -> FinalizedMap:
    """ADR-0043 §6 single pre-serialization reconcile point. Body IS the order contract."""
    ctx.violations.clear()
    _finalize_re_relativize(ctx)            # 1  re-relativize backstop (§2)
    _finalize_stamp_run_lifecycle(ctx)      # 2  pass_version backfill (WI-mipul)
    _finalize_recompute_run_signature(ctx)  # 3  META-hufaz (R2: after 2)
    _finalize_repo_fingerprint(ctx)         # 4  repo_fingerprint stamp
    _finalize_skipped_into_limits(ctx)      # 6  skipped → limits
    _finalize_confidence_aggregates(ctx)    # 7  stub (confidence:F1/F2)
    _finalize_commit_dicts(ctx)             # 8  commit reconciled view
    _finalize_declared_fields(ctx)          # 9  stub (declared-fields:F1(a)/F5)
    _finalize_referential_integrity(ctx)    # 10 validate_ir — LAST (R3)
    ctx.violations.sort(key=_violation_sort_key)  # §6 determinism: stable serialized order
    ctx.behavior_map["validation_report"] = build_validation_report(ctx.violations)
    return _freeze(ctx)
