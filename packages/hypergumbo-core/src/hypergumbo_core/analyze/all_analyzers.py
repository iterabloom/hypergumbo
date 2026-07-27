# SPDX-License-Identifier: AGPL-3.0-or-later
"""Analyzer orchestration + the stable analyzer-dispatch import points.

Analyzer *discovery* delegates to the canonical decorator-based registry in
analyze/registry.py (ADR-0012 Step 1) — ``get_analyzers`` /
``clear_analyzer_cache`` are thin pass-throughs. But this module also houses
the *orchestrator*: ``run_all_analyzers`` runs the registered analyzers via a
parallel ``ThreadPoolExecutor`` dispatch, stamps the config fingerprint,
filters by file presence, runs the file-symbol / anchor synthesis passes,
normalizes paths, dedups edges, and merges the dependency manifest — so it is
not a pure facade. It provides the stable import points used by cli.py and
partial_install_warnings.py.

Import points:
    - cli.py: ``from .analyze.all_analyzers import run_all_analyzers``
    - partial_install_warnings.py: ``from .analyze.all_analyzers import get_analyzers``
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..discovery import DEFAULT_EXCLUDES, get_file_index, set_global_on_file_skipped
from ..ir import (
    AnalysisRun, Edge, PASS_VERSION, Symbol, UsageContext,
    _default_config_fingerprint, compute_config_fingerprint,
)
from ..limits import Limits
from ..paths import normalize_path
from .base import (
    populate_kind_stable_ids,
    synthesize_file_anchors_for_node_bearing_paths,
    synthesize_file_anchors_for_paths,
    synthesize_file_symbols_for_dangling_edges,
)
from .registry import (
    RegisteredAnalyzer,
    clear_registry,
    ensure_discovered,
    get_all_analyzers as _registry_get_all,
)


def get_analyzers() -> list[RegisteredAnalyzer]:
    """Get all registered analyzers (triggers discovery on first call).

    Returns list of RegisteredAnalyzer objects from the canonical registry.
    """
    ensure_discovered()
    return list(_registry_get_all())


def clear_analyzer_cache() -> None:
    """Clear the analyzer cache and registry. For testing only."""
    clear_registry()


def stamp_analyzer_config_fingerprint(result: Any, analyzer: Any) -> None:
    """Orchestrator chokepoint that stamps a producer-identity
    config_fingerprint onto the run of any analyzer that bypasses
    ``TreeSitterAnalyzer._analyze_body``.

    The ~30 inherited-``analyze()`` tree-sitter analyzers self-stamp via
    ``_stamp_config_fingerprint`` (hashing the richer class+grammar+globs
    dict). The remaining cohort — override-``analyze()`` subclasses (go, java,
    js_ts, json, csharp, toml, xml, make, yaml_ansible, manifest_targets,
    play-routes) and bare ``@register_analyzer`` functions (python, html, the
    rust-analyzer SCIP path) — build their ``AnalysisRun`` by hand and never
    call it, so each falls through to the constant ``_default_config_fingerprint``
    sentinel (INV-lidul: 56/86 self-analysis runs collapsed onto one cache key).

    This is the analyzer analogue of the linker registry's
    ``_stamp_config_fingerprint``: the ``RegisteredAnalyzer`` (in scope at the
    orchestrator loop, unlike inside ``collect_analyzer_result``) is the only
    producer handle available — ``AnalysisResult`` carries no analyzer
    instance, so the digest keys on the registration identity. Guarded on the
    default sentinel so the self-stamped cohort is never overwritten, and so
    new override analyzers are covered automatically (WI-mipul future-proofing).
    """
    run = getattr(result, "run", None)
    if run is None or run.config_fingerprint != _default_config_fingerprint():
        return
    func = analyzer.func
    run.config_fingerprint = compute_config_fingerprint({
        "func": f"{func.__module__}.{getattr(func, '__qualname__', func.__name__)}",
        "name": analyzer.name,
        "backend": analyzer.backend,
        "languages": list(analyzer.languages),
    })


def collect_analyzer_result(
    result: Any,
    analysis_runs: list[dict],
    all_symbols: list[Symbol],
    all_edges: list[Edge],
    all_usage_contexts: list[UsageContext],
    limits: Limits,
    analyzer_name: str = "",
) -> None:
    """Collect results from an analyzer into the aggregated lists.

    This replaces 50+ repetitive code blocks in run_behavior_map().
    Each block had the same pattern; this function captures that pattern once.

    Args:
        result: The analyzer result (any XxxAnalysisResult type)
        analysis_runs: List to append run metadata to
        all_symbols: List to append symbols to
        all_edges: List to append edges to
        all_usage_contexts: List to append usage contexts to
        limits: Limits object to track skipped passes
        analyzer_name: The dispatching analyzer's registration name, used to
            record a ``skipped_passes`` entry when the result carries no run
            (WI-didil). Optional for backward compatibility; when empty, a
            ``run=None`` result is drained but not recorded as a skip.
    """
    # A result with no run is an analyzer that produced nothing (WI-didil).
    # It reaches here — rather than being short-circuited by the file-presence
    # pre-filter (_filter_by_file_presence) — when its declared language is
    # absent from the taxonomy (matlab/meson/puppet/racket/robot/scheme/scss)
    # or it is an opt-in backend that stayed off (rust_analyzer). Formerly this
    # branch silently returned, recording NEITHER an AnalysisRun NOR a skip, so
    # those passes violated the "every catalog pass → AR or skip" invariant.
    # Now it records a skip so coverage of the catalog stays complete. The
    # reason honours a self-declared ``skip_reason`` (e.g. rust_analyzer's
    # "backend not enabled") and otherwise falls back to "no files matched" —
    # the same wording the pre-filter uses (a bare ``run=None`` result is a
    # no-input analyzer). Symbols/edges are still drained fail-open.
    if result.run is None:
        all_symbols.extend(result.symbols)
        all_edges.extend(result.edges)
        all_usage_contexts.extend(getattr(result, "usage_contexts", []))
        # Only a genuinely-empty result (no run, no output) is a skip. A
        # producer that emitted symbols/edges without a run is a distinct
        # anomaly (e.g. rust_analyzer's success path returns run=None with
        # SCIP output) — keep its output, don't mislabel it a skip.
        produced_nothing = not result.symbols and not result.edges
        if analyzer_name and produced_nothing:
            reason = (
                getattr(result, "skip_reason", "")
                if getattr(result, "skipped", False)
                and getattr(result, "skip_reason", "")
                else "no files matched"
            )
            limits.skipped_passes.append({"pass": analyzer_name, "reason": reason})
        return

    # Check if analyzer was skipped (optional deps missing)
    # Some analyzers (Python, HTML) don't have skipped attribute
    is_skipped = getattr(result, "skipped", False)
    skip_reason = getattr(result, "skip_reason", "")

    if is_skipped:
        limits.skipped_passes.append({
            "pass": result.run.pass_id,
            "reason": skip_reason,
        })
    else:
        # INV-gizik / INV-pitab: stamp per-pass productivity counters at the
        # universal analyzer chokepoint, BEFORE to_dict() snapshots the run.
        # result.symbols/result.edges are this analyzer's direct output. Covers
        # inherited-_analyze_body, override-analyze, and function-registered
        # analyzers in one site (the same chokepoint as the origin_run_id /
        # config_fingerprint stamps).
        result.run.nodes_emitted = len(result.symbols)
        result.run.edges_emitted = len(result.edges)
        analysis_runs.append(result.run.to_dict())
        # WI-mosil central origin_run_id backstop. Direct-constructor analyzers
        # (toml/json/wgsl/sql and any future ones that build Symbols by hand
        # rather than through _analyze_body) leave Symbol.origin_run_id='' — a
        # sentinel that resolves to no AnalysisRun, breaking the node->AR join.
        # This is the single point where every analyzer's symbols meet their run,
        # so stamp the run's execution_id onto any Symbol the producer left
        # unstamped (the chokepoint fix, not a per-analyzer sweep). Pure fill:
        # a value a multi-pass producer already set is preserved. Edges are not
        # backfilled — Edge.__post_init__ already requires a non-empty
        # origin_run_id (WI-higap), so none can reach here empty.
        _run_exec_id = getattr(result.run, "execution_id", "")
        if _run_exec_id:
            for sym in result.symbols:
                if not sym.origin_run_id:
                    sym.origin_run_id = _run_exec_id
        all_symbols.extend(result.symbols)
        all_edges.extend(result.edges)
        all_usage_contexts.extend(getattr(result, "usage_contexts", []))

    # Drain per-run failed_files into the cross-analyzer Limits (runs in
    # both the skipped and non-skipped branches: a partial-skip analyzer
    # may still have recorded files before bailing).
    for ff in getattr(result.run, "failed_files", []):
        limits.add_failed_file(
            path=ff["path"],
            reason=ff["reason"],
            analyzer=result.run.pass_id,
        )


def _filter_by_file_presence(
    analyzers: list[RegisteredAnalyzer],
    profile: dict | None,
    limits: Limits,
) -> list[RegisteredAnalyzer]:
    """Drop analyzers whose languages have zero files in the profile.

    WI-jadig / INV-manov lifecycle policy ("file-presence pre-filter"): an
    analyzer is short-circuited only when **every** language it declares is
    in the taxonomy's ``LANGUAGE_EXTENSIONS`` (so the profile actually
    checked for it) **and** none of those languages appear in
    ``profile.languages`` with ``files > 0``. In that case the dispatcher
    records a ``skipped_passes`` entry with reason ``"no files matched"``
    and does not invoke the analyzer's function — saving the wall-clock
    cost of opening a parser / walking the FileIndex / building an empty
    tree for a pass with no input.

    Analyzers whose declared languages are NOT in the taxonomy
    (``gitignore``, ``requirements``, ``manifest_targets``, ``play-routes``,
    ``yaml_ansible``, etc.) are dispatched unconditionally — the profile
    has no opinion about them, so the safe default is to let the analyzer
    self-determine via its own file walk.

    When ``profile`` is ``None`` the filter is a no-op (callers outside the
    full ``run_behavior_map`` pipeline don't have a profile to consult).
    """
    if profile is None:
        return analyzers
    from ..taxonomy import LANGUAGE_EXTENSIONS
    known_langs = set(LANGUAGE_EXTENSIONS)
    profile_langs = profile.get("languages") or {}
    retained: list[RegisteredAnalyzer] = []
    for analyzer in analyzers:
        analyzer_langs = (
            set(analyzer.languages) if analyzer.languages else {analyzer.name}
        )
        # Defensive dispatch when any declared language is outside the
        # taxonomy — profile didn't count files for those, so we can't
        # tell whether the analyzer has work.
        if not analyzer_langs <= known_langs:
            retained.append(analyzer)
            continue
        any_with_files = any(
            (profile_langs.get(lang) or {}).get("files", 0) > 0
            for lang in analyzer_langs
        )
        if any_with_files:
            retained.append(analyzer)
        else:
            limits.skipped_passes.append({
                "pass": analyzer.name,
                "reason": "no files matched",
            })
    return retained


def run_all_analyzers(
    repo_root: Path,
    max_files: int | None = None,
    profile: dict | None = None,
) -> tuple[
    list[dict],  # analysis_runs
    list[Symbol],  # all_symbols
    list[Edge],  # all_edges
    list[UsageContext],  # all_usage_contexts
    Limits,  # limits
    dict[str, list[Symbol]],  # captured_symbols (for linkers)
    object | None,  # merged dependency manifest
]:
    """Run all registered analyzers and collect their results.

    Triggers entry-point discovery, then iterates all registered analyzers
    in priority order. Handles supports_max_files, capture_symbols_as,
    result collection, and edge deduplication.

    Args:
        repo_root: Repository root path
        max_files: Optional max files per analyzer
        profile: Optional repo profile dict (the ``behavior_map["profile"]``
            block). When supplied, the dispatcher applies WI-jadig's
            file-presence pre-filter: an analyzer is dispatched only when
            at least one of its declared ``languages`` has ``files > 0`` in
            ``profile.languages``; otherwise it's recorded in
            ``limits.skipped_passes`` with reason ``"no files matched"`` and
            not invoked.

    Returns:
        Tuple of (analysis_runs, all_symbols, all_edges, all_usage_contexts,
        limits, captured_symbols, dependency_manifest) where captured_symbols
        is a dict mapping capture names to symbol lists (e.g., {"c": [...],
        "java": [...]} for the JNI linker) and dependency_manifest is a
        merged DependencyManifest from all analyzers (or None).
    """
    ensure_discovered()

    analysis_runs: list[dict] = []
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []
    all_usage_contexts: list[UsageContext] = []
    limits = Limits()
    limits.max_files_per_analyzer = max_files
    captured_symbols: dict[str, list[Symbol]] = {}
    dep_manifests: list = []

    # Wire global callback so find_files() reports skipped files to limits
    def _on_file_skipped(path: Path, size_bytes: int, reason: str) -> None:
        limits.add_truncated_file(str(path), size_bytes, reason)

    set_global_on_file_skipped(_on_file_skipped)

    # Run all analyzers in parallel using threads.  Tree-sitter (C
    # extension) and file I/O release the GIL, so threads provide real
    # parallelism for the expensive parts of each analyzer.
    analyzers = list(_registry_get_all())
    analyzers = _filter_by_file_presence(analyzers, profile, limits)
    worker_count = max(1, min(len(analyzers), os.cpu_count() or 1))

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {}
        for analyzer in analyzers:
            kwargs: dict[str, Any] = {}
            if analyzer.supports_max_files and max_files is not None:  # pragma: no cover
                kwargs["max_files"] = max_files
            future = pool.submit(analyzer.get_func(), repo_root, **kwargs)
            futures[future] = analyzer

        for future in as_completed(futures):
            analyzer = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                # §17 fail-open (WI-madal L3): a single analyzer raising must
                # not abort the whole run. Record it pass-level and continue so
                # the behavior map is still emitted with partial results.
                limits.record_crashed_pass(analyzer.name, exc)
                continue

            # INV-lidul / WI-mipul: stamp a producer-identity config_fingerprint
            # for the override-analyze / function-registered cohort that bypasses
            # _analyze_body. Must run before collect_analyzer_result captures
            # result.run.to_dict(). Guarded on the default sentinel, so the
            # self-stamped analyzers are untouched.
            stamp_analyzer_config_fingerprint(result, analyzer)

            collect_analyzer_result(
                result, analysis_runs, all_symbols, all_edges, all_usage_contexts,
                limits, analyzer_name=analyzer.name,
            )

            # Capture symbols for linkers (e.g., JNI needs c_symbols and java_symbols)
            if analyzer.capture_symbols_as and not result.skipped:
                captured_symbols[analyzer.capture_symbols_as] = list(result.symbols)

            # Collect dependency manifests for tier classification of boundary nodes
            if not result.skipped and getattr(result, "dependency_manifest", None) is not None:
                dep_manifests.append(result.dependency_manifest)

    # Deduplicate edges by ID (some analyzers may produce duplicate edges)
    seen_edge_ids: set[str] = set()
    deduped_edges: list[Edge] = []
    for edge in all_edges:
        if edge.id not in seen_edge_ids:
            seen_edge_ids.add(edge.id)
            deduped_edges.append(edge)
    all_edges = deduped_edges

    # WI-ramuv: synthesize real file Symbols for any edge endpoint matching
    # ``make_file_id`` shape that has no producer-side Symbol. Without this,
    # ``ir.create_boundary_nodes`` would treat these dangling endpoints as
    # external boundaries even though the file is first-party. Doing it
    # here at the orchestrator chokepoint covers every analyzer in one
    # place and obviates per-analyzer fixes.
    # synthetic:F1 (WI-dizir/WI-mosil): emit a real AnalysisRun for this
    # synthesis pass and stamp its execution_id into the synthesized file
    # Symbols' origin_run_id, so the node->AnalysisRun JOIN resolves (the
    # nodes previously carried origin_run_id=''). Only record the run when
    # synthesis actually produced Symbols (no empty-pass records).
    _file_synth_run = AnalysisRun.create(  # nosec B106 — pass_id is a pass identifier, not a password (bandit B106 false-positives on any "pass*" funcarg)
        pass_id="orchestrator_file_symbol_synthesis", version=PASS_VERSION,
        config_fingerprint=compute_config_fingerprint(
            {"pass_id": "orchestrator_file_symbol_synthesis"}
        ),
    )
    _file_synth_t0 = time.perf_counter()
    _synth_file_symbols = synthesize_file_symbols_for_dangling_edges(
        all_symbols, all_edges, repo_root=repo_root,
        origin_run_id=_file_synth_run.execution_id,
    )
    if _synth_file_symbols:
        all_symbols.extend(_synth_file_symbols)
    # WI-dagif (file-anchor:F1, node-bearing slice): after the dangling-edge
    # pass, mint a file anchor for every path that has content nodes but still
    # no file anchor, so the contains tree has a reachable file root (the
    # containment linker's span-based pass then roots top-level members at it).
    # Runs after the dangling pass so its anchors count as already-present, and
    # shares the one synthesis AnalysisRun. Safe without file-anchor:F4 — these
    # paths already carry content nodes, so they are already in source_paths and
    # cannot empty the Additional-Files surface.
    _synth_node_anchors = synthesize_file_anchors_for_node_bearing_paths(
        all_symbols, repo_root=repo_root,
        origin_run_id=_file_synth_run.execution_id,
    )
    if _synth_node_anchors:
        all_symbols.extend(_synth_node_anchors)
    # file-anchor:F1 (additional-file-candidate cohort) + F4 co-release: anchor
    # every Additional-Files candidate (config/doc file) that still lacks a node
    # so the `additional_file_centrality_scores` keys are real node paths (the
    # WI-rajod subset invariant). Co-released with F4 — cli.py computes
    # source_paths as CONTENT-only, so these bare leaf anchors are NOT
    # re-subtracted from the Additional-Files surface. Selection (candidate
    # filter + language) lives here because base.py cannot import taxonomy/
    # discovery/sketch without a cycle; minting stays in base.
    _synth_candidate_anchors: list[Symbol] = []
    _af_file_index = get_file_index()
    if _af_file_index is not None:
        from ..sketch import ADDITIONAL_FILES_EXCLUDES
        from ..taxonomy import additional_file_candidates, get_language
        _content_paths = {s.path for s in all_symbols if s.kind != "file" and s.path}
        _af_excludes = list(DEFAULT_EXCLUDES) + ADDITIONAL_FILES_EXCLUDES
        _af_path_lang = {
            str(_c.relative_to(repo_root)): get_language(_c)
            for _c in additional_file_candidates(
                repo_root, _af_file_index.all_files(), _content_paths, _af_excludes,
            )
        }
        _synth_candidate_anchors = synthesize_file_anchors_for_paths(
            all_symbols, _af_path_lang, repo_root=repo_root,
            origin_run_id=_file_synth_run.execution_id,
        )
        if _synth_candidate_anchors:
            all_symbols.extend(_synth_candidate_anchors)
    _total_file_synth = (
        len(_synth_file_symbols) + len(_synth_node_anchors)
        + len(_synth_candidate_anchors)
    )
    if _total_file_synth:
        # INV-gizik: this synthesis pass bypasses both analyzer + linker
        # chokepoints; stamp its duration + node count (it emits only Symbols).
        _file_synth_run.duration_ms = int((time.perf_counter() - _file_synth_t0) * 1000)
        _file_synth_run.nodes_emitted = _total_file_synth
        analysis_runs.append(_file_synth_run.to_dict())

    # Normalize paths: some analyzers produce absolute paths instead of
    # paths relative to repo_root.  Stripping the repo_root prefix ensures
    # consistent tier classification, test-file detection, and
    # machine-independent output.
    root_prefix = normalize_path(str(repo_root)).rstrip("/") + "/"
    for sym in all_symbols:
        normed = normalize_path(sym.path)
        if normed.startswith(root_prefix):
            sym.path = normed[len(root_prefix):]
    for uc in all_usage_contexts:
        normed = normalize_path(uc.path)
        if normed.startswith(root_prefix):
            uc.path = normed[len(root_prefix):]
    # INV-buhur: producers that record failures from deep helper functions
    # may not have repo_root in scope, so they may emit absolute paths.
    # Normalize here for consistency with sym.path / uc.path treatment.
    for ff in limits.failed_files:
        normed = normalize_path(ff.path)
        if normed.startswith(root_prefix):
            ff.path = normed[len(root_prefix):]

    # INV-sotiv: fill missing Symbol.stable_id for the kinds whose producers
    # don't compute one (variable / module / dependency / export / project /
    # interface / type, plus orchestrator-synthesized files). Runs after path
    # normalisation so the file-path identity component is repo-relative.
    # Producers that already computed a stable_id keep priority.
    populate_kind_stable_ids(all_symbols)

    # Clear global callback to avoid leaking state
    set_global_on_file_skipped(None)

    # Merge dependency manifests from all analyzers
    merged_manifest = None
    if dep_manifests:
        from ..supply_chain import DependencyManifest
        merged_manifest = DependencyManifest.merge(dep_manifests)

    return (
        analysis_runs, all_symbols, all_edges, all_usage_contexts,
        limits, captured_symbols, merged_manifest,
    )
