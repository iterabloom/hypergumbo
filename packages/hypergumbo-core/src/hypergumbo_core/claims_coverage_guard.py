# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-PR guard for the self-claims COVERAGE question (INV-jojog).

WHAT THIS PREVENTS. A shipped-code call into a Python name the I/O catalogue
neither rows nor enumerates makes the cron ``self-claims-gate`` withhold every
self-claim verdict at once -- ``confirmed_with_caveats`` -> ``inconclusive``,
the ``opaque_boundary`` caveat gone from all of them. The full gate runs only on
the twice-daily cron (its per-PR arm costs too much critical path;
``test_ci_self_claims_gate_scope`` carries that decision), so without this
guard such a call reaches ``dev`` and leaves it red for up to twelve hours,
followed by a bisect. (history: INV-bofab, INV-fugus, INV-jojog, INV-konad)

WHY THE WHOLE GATE IS TOO SLOW AND THIS IS NOT. ``verify-claims`` needs the full
behaviour map -- every language, the linkers, taint -- which takes many minutes
cold. The coverage question needs none of that.
``_call_production_coverage`` decides ``qualifying_only`` from the EDGE SET
alone: which external modules the shipped Python calls into, and whether the
catalogue rows or enumerates each. The Python analyzer produces that edge set
by itself, receiver typing included -- ``resolution_quality: type_inferred`` is
stamped in ``py.py``, not by a later pass -- in tens of seconds, and the three
pipeline stages below add little to that.

NOT A SECOND RULE. This module runs the registered ``python`` analyzer over the
packaging-declared source roots (``shipped_artifact_roots`` -- the denominator
``analysis_scope: shipped_artifact`` gives the cron), loads the catalogue the
cron loads (``python.yaml``, the shipped community overlays beneath it, the
claims file's overlays on top) and asks the cron's own decision function. There
is no AST walk of its own and no second spelling of "module slot": the symbol
ids are the analyzer's, relativised by the pipeline's own ``_relativize_ir_paths``,
and the adjudication is ``verify_claims``'. A hand-rolled walk would be one more
home for module extraction (WI-ribuz), and it would have to re-derive receiver
typing to catch a call through a typed receiver (the ``threading.Event`` shape)
at all.

THREE PIPELINE STAGES ARE PART OF THE QUESTION. The adjudicator reads the edges
AFTER three pipeline stages that change the population, so the raw analyzer
edges are not enough: over them this guard passes trees the cron withholds
(INV-hotib / WI-jizam). ``analyzer_edges`` runs the stages through the
pipeline's own functions:

* the orchestrator's file-symbol synthesis (``all_analyzers``: WI-ramuv,
  WI-dagif), so a dropped file's import edge has a node to be dropped with;
* ``_classify_symbols`` and the tier filter, ``tier_filter.apply_tier_filter``
  (one copy, shared with ``run_survey``): a DERIVED file (``_pb2.py``) leaves
  with every edge touching it, and the cross-package call into it stays as the
  unresolved module slot the cron names. Over the raw edges the file would
  count as analysed and ``_is_analyzed_module`` would suppress that call as
  first-party;
* boundary synthesis's id remap (``ir.create_boundary_nodes`` +
  ``apply_external_id_remap``), which normalises a dangling id's kind slot to
  ``external_symbol`` (ADR-0036 ruling 2). ``_external_call_sites`` admits only
  ``external_symbol`` / ``unresolved``, so without the remap an attribute read
  -- the analyzer's
  ``python:datetime.timezone:0-0:datetime.timezone.utc:attribute`` -- would be
  invisible here and counted in the cron.

Edge counts here and in the cron still differ -- linkers, taint and the
non-Python analyzers are not run -- but the population the coverage gate
reads is built by the same stages.

WHAT IT DOES NOT CATCH, said plainly. The drift gate compares every self-claim's
verdict, its caveat kinds and its credited-flow buckets; this compares one
bit of coverage. A taint-flow regression, a sanitizer that stops crediting, a
verdict moving to ``violated`` -- all still wait for the cron. The guard asks
only: can the shipped tree's boundary verdicts still be QUALIFIED?

THE FILE LIST IS NOT TEST-FILTERED. ``is_test_file`` calls ``spec_validator.py``
a test (the ``spec_*`` rule), so test-filtering would drop that module from the
analysed set and its own callers would report
``hypergumbo_core.spec_validator`` as an untraced third-party module. The cron's
denominator is "edges whose src is under a shipped root" (``edge_in_artifact``)
and nothing else; this uses exactly that, through ``find_files`` so discovery's
own exclude rules still apply.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .discovery import FileIndex, find_files, get_file_index, set_file_index
from .io_boundary import IoBoundaryCatalog, load_catalog
from .limits import Limits
from .supply_chain import collect_first_party_package_names
from .verify_claims import (
    BoundaryCoverage,
    _call_production_coverage,
    edge_in_artifact,
    load_extra_catalog_paths,
    shipped_artifact_roots,
)

#: The one analyzer this guard runs, and the one catalogue language it asks.
#: The self-claims are claims about a Python CLI; a repository shipping another
#: language would widen both, and the cron gate's language census is the model.
ANALYZER = "python"
LANGUAGE = "python"


@dataclass
class GuardReport:
    """What one run of the guard saw, and the cron's decision over it."""

    files: int
    edges: int
    coverage: BoundaryCoverage
    seconds: float

    @property
    def qualifiable(self) -> bool:
        """The cron would reach a verdict: coverage complete, or qualified by
        opaque launch sites alone (ADR-0016 section 4). ``False`` is the state
        that withholds every self-claim verdict at once."""
        return self.coverage.complete or self.coverage.qualifying_only


def shipped_python_files(repo_root: Path, roots: Iterable[str]) -> list[Path]:
    """Every ``*.py`` under the shipped roots, by discovery's own exclude rules.

    Listed with NO index installed, so ``find_files`` walks each root with
    ``DEFAULT_EXCLUDES`` -- the rule the pipeline's index is built with. Not
    test-filtered: see the module docstring.
    """
    previous = get_file_index()
    set_file_index(None)
    try:
        found: set[Path] = set()
        for root in roots:
            found.update(find_files(repo_root / root, ["*.py"]))
    finally:
        set_file_index(previous)
    return sorted(found)


def analyzer_edges(repo_root: Path, files: list[Path]) -> list[dict[str, Any]]:
    """The ``python`` analyzer's edges over exactly ``files``, as the cron's
    adjudicator would read them: tier-filtered, boundary-normalised, ids
    repo-relative.

    A :class:`FileIndex` built from the list is installed for the duration, so
    the analyzer's ``find_files`` sees only the shipped files (INV-jumim's
    mechanism, the one ``sketch --input`` uses); the previous index is restored
    whatever happens. ``KeyError`` from the registry means the analyzer package
    is not installed -- an infrastructure condition the caller reports as such.

    THREE PIPELINE STAGES RUN HERE BECAUSE THE ADJUDICATOR READS THEIR OUTPUT;
    skipping them lets this guard pass a tree on which the cron withholds every
    self-claim (INV-hotib / WI-jizam). All three are the pipeline's own
    functions, not a restatement:

    * the orchestrator's file-symbol synthesis
      (``synthesize_file_symbols_for_dangling_edges`` and
      ``synthesize_file_anchors_for_node_bearing_paths``, as
      ``all_analyzers`` runs them), so a dropped file's import edge has a
      node to be dropped with.
    * ``_classify_symbols`` + :func:`tier_filter.apply_tier_filter`: a DERIVED
      file (``scip/_generated/scip_pb2.py``, tier 4 by the ``_pb2.py`` rule)
      leaves with every edge touching it, so a cross-package call into it
      stays as the unresolved ``hypergumbo_core.scip._generated.scip_pb2``
      slot the cron names. Over the raw analyzer edges the file would count as
      analysed and ``_is_analyzed_module`` would suppress that call as
      first-party.
    * :func:`ir.create_boundary_nodes` + :func:`ir.apply_external_id_remap`:
      boundary synthesis normalises a dangling id's kind slot to
      ``external_symbol`` (ADR-0036 ruling 2). ``_external_call_sites`` admits
      only ``external_symbol`` / ``unresolved`` terminals, so without the remap
      an attribute read -- ``python:datetime.timezone:0-0:datetime.timezone.utc:attribute``
      straight from the analyzer -- would be invisible to the coverage gate here
      while counted in the cron. The synthesised nodes themselves are discarded;
      only the remap is wanted.

    Order follows ``run_survey`` except for relativisation: ``run_survey``
    relativises right after ``run_all_analyzers``, before classifying; this
    guard classifies, filters and synthesises boundary nodes on the
    analyzer's absolute-path ids, then relativises last.
    """
    from .analyze.base import (
        synthesize_file_anchors_for_node_bearing_paths,
        synthesize_file_symbols_for_dangling_edges,
    )
    from .analyze.registry import ensure_discovered, run_analyzer
    from .cli import _classify_symbols
    from .finalize import _relativize_ir_paths
    from .ir import apply_external_id_remap, create_boundary_nodes
    from .supply_chain import detect_package_roots
    from .tier_filter import apply_tier_filter

    ensure_discovered()
    previous = get_file_index()
    set_file_index(FileIndex.from_paths(repo_root, files))
    try:
        result = run_analyzer(ANALYZER, repo_root)
    finally:
        set_file_index(previous)
    symbols = list(result.symbols)
    # The orchestrator's file-symbol synthesis (WI-ramuv, WI-dagif) runs before
    # classification in ``all_analyzers``; without it a dropped file's import
    # edge lands on a file id no Symbol carries, which the tier filter cannot
    # remove, and the file counts as analysed.
    symbols.extend(synthesize_file_symbols_for_dangling_edges(
        symbols, result.edges, repo_root=repo_root, origin_run_id="",
    ))
    symbols.extend(synthesize_file_anchors_for_node_bearing_paths(
        symbols, repo_root=repo_root, origin_run_id="",
    ))
    _classify_symbols(symbols, repo_root, detect_package_roots(repo_root))
    symbols, edges = apply_tier_filter(symbols, result.edges, None, Limits())
    _boundary_nodes, id_remap = create_boundary_nodes(symbols, edges)
    edges = apply_external_id_remap(edges, id_remap)
    _relativize_ir_paths(repo_root, symbols, edges, result.usage_contexts)
    return [edge.to_dict() for edge in edges]


def claim_catalogs(claims_path: Path) -> dict[str, IoBoundaryCatalog]:
    """The catalogue the cron adjudicates with, assembled the way the CLI does.

    Shipped ``python.yaml`` with the shipped community overlays beneath
    (``include_defaults``), and the ``extra_catalogs: io_primitives:`` overlays
    the claims file names on top -- for this repository, the self overlay that
    carries its third-party grants.
    """
    _sources, _sinks, _sanitizers, overlays = load_extra_catalog_paths(claims_path)
    catalog = load_catalog(LANGUAGE, overlay_paths=overlays, include_defaults=True)
    return {LANGUAGE: catalog}


def coverage_of(
    edges: list[dict[str, Any]],
    catalogs: dict[str, IoBoundaryCatalog],
    first_party_packages: Iterable[str] = (),
) -> BoundaryCoverage:
    """The cron's own decision over ``edges``: :func:`_call_production_coverage`.

    Thin on purpose. Every rule -- row hits, enumeration grants, launches,
    starved method-shaped modules, first-party suppression -- lives in
    ``verify_claims`` and is asked there, so this guard and the cron cannot
    disagree about what withholds a verdict.
    """
    return _call_production_coverage(
        edges, {LANGUAGE}, catalogs, frozenset(first_party_packages),
    )


def run_guard(repo_root: Path, claims_path: Path) -> GuardReport:
    """Analyse the shipped tree and decide whether its self-claims are qualifiable."""
    started = time.monotonic()
    repo_root = repo_root.resolve()
    roots = shipped_artifact_roots(repo_root)
    files = shipped_python_files(repo_root, roots)
    edges = [
        edge for edge in analyzer_edges(repo_root, files)
        if edge_in_artifact(edge, roots)
    ]
    coverage = coverage_of(
        edges, claim_catalogs(claims_path),
        collect_first_party_package_names(repo_root),
    )
    return GuardReport(
        files=len(files), edges=len(edges), coverage=coverage,
        seconds=time.monotonic() - started,
    )
