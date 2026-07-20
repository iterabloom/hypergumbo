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
  (4); skipped→limits (6); **edge-resolution verdict (7, ADR-0037 rulings 1/2** — classify
  every ``edge.dst`` against the final node set and derive ``is_resolved`` + ``dst_ref`` from
  one verdict; closes WI-kukuk/WI-zuhon/WI-ninuv/WI-mutuk. This slot is NOT a §6.1 stub fill;
  it is an ADR-0037 addition that postdates the §6.1 plan, placed before commit so the
  serialized edges carry the verdict and before referential-integrity so the FK predicate
  validates it**); commit-dicts (8); referential-integrity validate_ir lift (10, now also
  carrying the ADR-0037 ruling-5 ``is_resolved ⇒ first-party`` FK predicate).
* **Two §6.1 stub slots stayed empty.** ADR-0043 §6.1 planned three Phase-2 families each
  filling a named finalize stub with zero orchestrator change; none did. ``projection-finalize``
  became a downstream consumer (``compact.recompute_view_summary``), not a sub-step; and the
  ``confidence`` (the former slot 7, now hosting edge-resolution) and ``declared-fields`` (9)
  stubs were both **removed** (not stubbed) once recons showed their work lands outside
  finalize:

  - ``confidence`` (7): the confidence:F1/F2 IDs denote per-edge derivation / ranking-detection
    separation (producer-side, ADR-0039, INV-suvil family), which ADR-0039 keeps *out* of
    finalize (``Edge.confidence`` untouched). The behavior_map confidence aggregate this slot
    described already exists over the final set — ``metrics.avg_confidence`` (computed *after*
    finalize) and ``sketch.confidence_mass`` (EP/datamodel) — so there is no reconcile gap and
    no consumer for a finalize-time tenant.
  - ``declared-fields`` (9): its writer-contract half already runs in sub-step 10's
    ``validate_ir`` (declared-fields:F1(a) / INV-zotip), and its population-contract half lands
    in the writer-contract *validator* (WI-libib) and producers (declared-fields:F5 / INV-dubam);
    a finalize-time population re-check would grow the shrink-only validation ratchet.
* **Other deliberately-absent run-lifecycle work:** an ``emission_counts`` sub-step was removed
  (not stubbed) — the ratified "recompute files_analyzed by origin_run_id" mechanism is unsound
  (files_analyzed is contractually a file count == profile.files; the recompute yields a
  node/path count and does not close INV-gizik, whose real fix is a new provenance field). The
  config_fingerprint backstop-with-violation landed in WI-mipul's producer-side work (done). Of
  the tracked work, INV-gizik (satisfied — closed by the new nodes_emitted/edges_emitted
  provenance field), WI-mipul (done), and INV-zotip (satisfied) are resolved; WI-libib
  (per-(kind,field) writer-contract validator) and INV-suvil (evidence-derived confidence) remain
  open. git carries the history.
  See the ADR-0043 §6.1 amendment chain (#4/#5/#7/#9).

`FinalizedMap` is a shallow ``frozen=True`` handle (ratified §6 #6): rebinding a field
raises, but the inner dicts remain mutable so the downstream budget-tier/compact
projections (still in ``cli`` for the carrier) can run after finalize returns. The tiered
projection re-derives its ``nodes_summary`` from the final post-shrink arrays via
``compact.recompute_view_summary`` (projection:F1 / INV-pazur — a summary-only re-derive,
narrower than the design-time ``re_derive_view``). Consumers must honor a no-mutation
contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .ir import ExternalRef, _compute_run_signature, _parse_dangling_id
from .pass_metadata import PassMetadataLookup
from .receiver_blind_magnets import demote_harmful_magnets
from .repo_fingerprint import compute_repo_fingerprint
from .visibility import (
    VISIBILITY_MODIFIER_TERMS,
    VISIBILITY_PUBLIC,
    compute_visibility,
)
from .spec_validator import (
    build_validation_report,
    compute_stable_id_stats,
    validate_ir,
)

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

    ID-embedding values in ``Symbol.meta`` AND ``Edge.meta`` are relativized the same way
    (dispatch:F1 / INV-pohik symptom 2), covering both string values and one level of
    ``dict`` values. The original case is a route symbol's ``handler_ref`` (a string):
    the route_handler linker runs *after* this pass and resolves the direct case by ID
    against the relativized id index, so an un-relativized ``handler_ref`` misses every
    lookup and the route → handler ``dispatches_to`` edge never lands. WI-supat adds two
    more shapes with the SAME failure mode: the concrete-class ids threaded for
    inherited-call resolution — ``enclosing_class_id`` / ``receiver_type_id`` on **Edge**
    meta (which was formerly not relativized at all — only ``src``/``dst`` were), and
    ``field_type_ids`` (a ``{field: id}`` **dict**) on a class Symbol's meta — are compared
    against the relativized ``class_symbols`` index by the inherited_calls linker (also run
    after this pass), so an un-relativized id silently falls back to the name path and the
    concrete-id collision-recovery never fires. Short-name refs (Express ``handler_ref``,
    ``view_name``) and values that don't carry the prefix are left untouched.

    Runs once at Phase B (``cli``) and again as finalize sub-step 1 — the second call is an
    idempotent backstop (the prefix-guarded ``str.replace`` is a no-op on already-relative
    values) catching any absolute path minted after Phase B in a *string* or one-level
    *dict-of-str* meta value (e.g. by a linker or boundary synthesis). SHAPE SCOPE: only
    ``sym.id``/``sym.path``/``edge.src``/``edge.dst`` and string + one-level dict-of-str
    ``meta`` values are relativized; **list-valued and nested-dict meta shapes are
    intentionally out of scope** because no current producer mints a repo-root-absolute path
    in those shapes (``edge.meta['referring_paths']``, the only list-of-paths meta, is minted
    AFTER this pass from already-relative ``edge.src`` slots — see ``_relativize_meta``).
    """
    prefix = str(repo_root) + "/"

    def _relativize_meta(meta: "dict | None") -> None:
        """Relativize prefix-bearing ID strings in a meta dict, in place.

        SHAPE CONTRACT (load-bearing — read before threading a new id-embedding
        meta key): an id/path-embedding meta value MUST be either a top-level
        ``str`` (``handler_ref``, ``enclosing_class_id``, ``receiver_type_id``) or
        a one-level ``{key: id_str}`` ``dict`` (``field_type_ids``). Those two
        shapes are relativized here; LIST values and NESTED dicts are deliberately
        NOT (no current producer mints a repo-root-absolute path in them). If you
        add a concrete symbol id to meta as a list element or a two-level dict,
        this helper will SILENTLY SKIP it — the stale absolute id then misses the
        relativized ``class_symbols`` index and the consumer inertly falls back
        (the exact name-path masking that hid the original WI-supat bug). So:
        extend this helper to that shape AND add a POSITIVE end-to-end assertion
        (a resolved edge, not just "no wrong edge") that fails on revert.
        Non-string / non-dict values and short-name refs are untouched (they never
        carry the ``repo_root`` prefix).
        """
        if not meta:
            return
        for key, value in list(meta.items()):
            if isinstance(value, str) and prefix in value:
                meta[key] = value.replace(prefix, "")
            elif isinstance(value, dict):
                for k2, v2 in list(value.items()):
                    if isinstance(v2, str) and prefix in v2:
                        value[k2] = v2.replace(prefix, "")

    for sym in symbols:
        if prefix in sym.id:
            sym.id = sym.id.replace(prefix, "")
        if sym.path and prefix in sym.path:
            sym.path = sym.path.replace(prefix, "")
        _relativize_meta(sym.meta)
    for edge in edges:
        if prefix in edge.src:
            edge.src = edge.src.replace(prefix, "")
        if prefix in edge.dst:
            edge.dst = edge.dst.replace(prefix, "")
        _relativize_meta(edge.meta)
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


def _detected_unanalyzed_languages(ctx: FinalizeContext) -> list[str]:
    """Code languages the profile detected but no analyzer pass covered (WI-nihir).

    Detected = ``behavior_map["profile"]["languages"]`` keys minus the config-only
    languages (JSON/YAML/… have no code analyzer by design, so they are never
    "skipped"). Analyzed = the union of languages covered by the analyzer whose
    ``pass`` id appears in ``analysis_runs`` — a skipped or grammar-failed analyzer
    never appends a run (``all_analyzers.collect_analyzer_result`` routes it to
    ``limits.skipped_passes`` instead), so its language falls out of this set and
    surfaces as skipped. The pass_id→languages map mirrors the analyzer's own
    ``set(languages) if languages else {name}`` convention (all_analyzers.py:202);
    non-analyzer passes (linkers, synthesis) simply don't appear in the map and
    contribute nothing. The difference is returned sorted for deterministic output.
    """
    from .analyze.registry import ensure_discovered, get_all_analyzers
    from .catalog import CONFIG_LANGUAGES

    profile = ctx.behavior_map.get("profile", {})
    detected = set(profile.get("languages", {})) - CONFIG_LANGUAGES
    if not detected:
        return []
    ensure_discovered()
    pass_to_langs = {
        a.name: (set(a.languages) if a.languages else {a.name})
        for a in get_all_analyzers()
    }
    analyzed: set[str] = set()
    for run in ctx.analysis_runs:
        analyzed.update(pass_to_langs.get(run.get("pass", ""), ()))
    return sorted(detected - analyzed)


def _finalize_skipped_into_limits(ctx: FinalizeContext) -> None:
    """Sub-step 6 — drain skipped-file counts + unanalyzed languages into limits.

    Two honesty signals are reconciled at this single pre-serialization chokepoint:

    * ``partial_results_reason`` — set from any run's ``files_skipped`` count, but
      never clobbering a reason already set by ``record_crashed_pass`` (WI-madal L3):
      a crashed pass is the more severe signal; the file-skip note only fills an
      otherwise-empty summary.
    * ``skipped_languages`` (WI-nihir) — the ``add_skipped_language`` setter had zero
      callers, so a language the profile DETECTED but for which no analyzer pass ran
      (grammar unavailable / unsupported / crashed) was silently absent. This is the
      one stage holding both the detected set (profile) and the analyzed set
      (analysis_runs), so the "detected minus analyzed" diff is drained here once —
      covering every cause without any per-analyzer edit. See
      ``_detected_unanalyzed_languages``.
    """
    for run in ctx.analysis_runs:
        if run.get("files_skipped", 0) > 0 and not ctx.limits.partial_results_reason:
            ctx.limits.partial_results_reason = "some files skipped during analysis"
    for language in _detected_unanalyzed_languages(ctx):
        ctx.limits.add_skipped_language(language)
    ctx.behavior_map["limits"] = ctx.limits.to_dict()


def _derive_dst_ref_from_id(dst_id: str) -> Optional[ExternalRef]:
    """Reconstruct an ``ExternalRef`` from a placeholder dst id (ADR-0037 ruling 2 backstop).

    The edge-resolution verdict needs a structured ``dst_ref`` on every external edge, but
    ~68% of producers stamp the legacy ``dst`` string without one (WI-zuhon). When the dst
    id is well-formed (``{lang}:{path}:{span}:{name}:{kind}``) we recover the ref from it.
    Two ids yield ``None`` — the legitimate "unidentified reference" cell
    (``is_resolved=False, dst_ref=None``): a malformed/short id (``_parse_dangling_id``
    returns the ``"<unknown>"`` path sentinel), and one whose module path is the
    ``"external"`` sentinel (module unknown — WI-huzuv forbids promoting the literal
    sentinel to a fabricated path).
    """
    language, path, name, _kind = _parse_dangling_id(dst_id)
    if path in ("<unknown>", "external"):
        return None
    return ExternalRef(lang=language, module_path=path, name=name)


def _finalize_demote_receiver_blind_magnets(ctx: FinalizeContext) -> None:
    """Sub-step 6c — INV-fahub: demote cleanly-harmful receiver-blind magnets.

    A receiver-blind method magnet is a high-confidence ``calls`` edge that bound
    an unresolvable-receiver call to an *arbitrary* same-named internal method
    (``Peer.removeFailedPeers`` → a *test* ``Collector.Add``; ``tmpl.Parse()`` →
    a local ``Template.Parse`` instead of ``text/template``). This gate demotes
    only the two sub-classes where the internal target is almost-certainly wrong
    — a production→test-helper misbind and a stdlib-interface-method shadow — by
    **redirecting** the edge's ``dst`` to an ``external:unresolved`` id. The
    correct-but-unprovable trait-method funnel (Rust ``x.next()``) is left intact
    (ADR-0012 scope; owner ruling) — see ``demote_harmful_magnets``.

    Runs on the RESOLVED graph (producer/linker binds in place) but BEFORE
    sub-step 7 ``_finalize_edge_resolution``, which then re-derives
    ``is_resolved=False`` + ``dst_ref`` from the now-external dst — so this
    sub-step never hand-sets a resolution surface. R1-safe: mutates edge fields
    only, adds/removes no node or edge.
    """
    demote_harmful_magnets(ctx.symbols, ctx.edges)


def _finalize_edge_resolution(ctx: FinalizeContext) -> None:
    """Sub-step 7 — single edge-resolution verdict (ADR-0037 rulings 1/2).

    Classifies every ``edge.dst`` exactly once against the FINAL node set and derives both
    resolution surfaces from one verdict, overriding the producer-stamped (now advisory)
    ``is_resolved`` / ``dst_ref``:

    * dst is a real in-repo node (``kind != "external_symbol"``) → ``is_resolved=True``,
      ``dst_ref=None`` (first-party). ``is_resolved`` names in-repo-ness, not
      target-identification (ADR-0037 ruling 1).
    * dst is an ``external_symbol`` placeholder, or absent from the node set →
      ``is_resolved=False``; ``dst_ref`` kept if the producer supplied one, else derived
      from the dst id (backstop above). A dst id we cannot parse leaves ``dst_ref=None``.

    Runs BEFORE ``_finalize_commit_dicts`` (8) so the serialized ``edges`` array carries the
    verdict (mutating after commit would diverge the JSON from the live objects), and BEFORE
    ``_finalize_referential_integrity`` (10) so the ADR-0037 FK predicate validates exactly
    this verdict. R1-safe: only mutates fields, never adds/removes a node or edge — appends
    no violations, so R3 holds. Because no surface is written independently of this verdict,
    WI-kukuk's flag/suffix contradiction (4,507 edges ``is_resolved=True`` at an external
    placeholder) becomes structurally impossible, and external ``dst_ref`` coverage reaches
    100% (WI-zuhon).
    """
    node_kind_by_id = {s.id: s.kind for s in ctx.symbols}
    for edge in ctx.edges:
        dst_kind = node_kind_by_id.get(edge.dst)
        if dst_kind is not None and dst_kind != "external_symbol":
            edge.is_resolved = True
            edge.dst_ref = None
        else:
            edge.is_resolved = False
            if edge.dst_ref is None:
                edge.dst_ref = _derive_dst_ref_from_id(edge.dst)


def _finalize_compute_visibility(ctx: FinalizeContext) -> None:
    """INV-jusot: fold the per-symbol visibility signals into one canonical
    ``Symbol.visibility`` level and record the deciding signal, retiring the
    legacy ``meta['visibility']`` key.

    Single computation point: a language ``modifiers`` term wins over the
    legacy ``meta['visibility']`` term (Apex / Clojure), which wins over the
    Python leading-underscore name convention, which wins over the public
    default. The legacy ``meta['visibility']`` key is removed once folded — the
    typed field is its canonical home.

    Reconciliation of the two remaining visibility encodings (INV-jusot
    follow-up):

    - ``is_exported`` — a public API cannot be non-public, so language
      visibility is a **necessary but not sufficient** condition:
      ``is_exported`` is downgraded to False for any non-public symbol, but is
      NOT set True merely because a symbol is language-public. (A pure
      ``is_exported = visibility=='public'`` alias would flip 58% of the
      self-corpus — 19k of them test-file symbols — from not-exported to
      exported, redefining ``is_exported`` from "public API member" (29%) to
      "language-public" (87%) and turning every public test function into an
      exported dead-code root. The `and`-with-visibility keeps ``is_exported``'s
      public-API-membership meaning and only resolves the real disagreement:
      the 5 self-corpus ``src/_foo`` symbols the path heuristic marked exported
      despite being private.) The publishedness/test-penalty inputs remain on
      ``supply_chain`` (``is_test_file`` / ``tier``).
    - ``modifiers`` — the visibility terms (now on the ``visibility`` field)
      are stripped, so ``modifiers`` keeps only non-visibility terms
      (``static`` / ``native`` / ``abstract`` / …).
    """
    for sym in ctx.symbols:
        meta = sym.meta if sym.meta is not None else {}
        level, signal = compute_visibility(
            modifiers=sym.modifiers,
            name=sym.name,
            language=sym.language,
            meta_visibility=meta.get("visibility"),
        )
        sym.visibility = level
        if sym.meta is None:
            sym.meta = {}
        sym.meta["visibility_signal"] = signal
        sym.meta.pop("visibility", None)
        # is_exported requires public visibility (necessary, not sufficient).
        if level != VISIBILITY_PUBLIC:
            sym.is_exported = False
        # modifiers keeps only non-visibility terms.
        if sym.modifiers:
            sym.modifiers = [
                m for m in sym.modifiers if m not in VISIBILITY_MODIFIER_TERMS
            ]


def _finalize_commit_dicts(ctx: FinalizeContext) -> None:
    """Sub-step 8 — commit the reconciled IR into behavior_map as one view."""
    # WI-haguz: serialize analysis_runs in a documented, deterministic order —
    # ascending started_at, ties broken by pass id — rather than the accidental
    # pass-completion order, so the array has a stable ordering contract. Sort
    # in place so behavior_map["analysis_runs"] stays the same list object as
    # ctx.analysis_runs (an identity other sub-steps and tests rely on).
    ctx.analysis_runs.sort(key=lambda r: (r.get("started_at") or "", r.get("pass") or ""))
    ctx.behavior_map["analysis_runs"] = ctx.analysis_runs
    ctx.behavior_map["nodes"] = [s.to_dict() for s in ctx.symbols]
    ctx.behavior_map["edges"] = [e.to_dict() for e in ctx.edges]
    ctx.behavior_map["usage_contexts"] = [uc.to_dict() for uc in ctx.usage_contexts]


def _finalize_referential_integrity(ctx: FinalizeContext) -> None:
    """Sub-step 10 — validate the emitted substrate (§7). STRUCTURALLY LAST (R3).

    Runs the full ``validate_ir`` aggregator (axis/writer-contract/cross-field/verdict/
    id-format/stable-id/round-trip, the ADR-0037 is_resolved<->dst FK, and the
    validator:F2 origin_run_id->analysis_runs FK). Lives in its structurally-last home so
    it sees exactly the substrate that serializes. The remaining endpoint-closure predicate
    (dangling src/dst) lands with its producer-side src-placeholder synthesis (WI-mujor),
    not here.
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
    _finalize_demote_receiver_blind_magnets(ctx)  # 6c INV-fahub magnet demote (before 7)
    _finalize_edge_resolution(ctx)          # 7  edge-resolution verdict (ADR-0037; before 8)
    _finalize_compute_visibility(ctx)       # 7b visibility fold (INV-jusot; before 8)
    _finalize_commit_dicts(ctx)             # 8  commit reconciled view
    _finalize_referential_integrity(ctx)    # 10 validate_ir — LAST (R3)
    ctx.violations.sort(key=_violation_sort_key)  # §6 determinism: stable serialized order
    ctx.behavior_map["validation_report"] = build_validation_report(
        ctx.violations,
        stable_id_stats=compute_stable_id_stats(ctx.symbols),
    )
    return _freeze(ctx)
