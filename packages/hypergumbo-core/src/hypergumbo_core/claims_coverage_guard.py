# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-jojog: the per-PR arm of the self-claims COVERAGE question.

WHAT THIS PREVENTS. Four times a shipped-code call into a Python name the I/O
catalogue neither rows nor enumerates reached ``dev`` and withheld every one of
the 18 self-claim verdicts at once -- ``confirmed_with_caveats`` ->
``inconclusive``, the ``opaque_boundary`` caveat gone from all of them:
``builtins`` (INV-bofab, 09-03), ``datetime.date.today`` (INV-fugus, 09-23),
``datetime.date.fromisoformat`` (INV-jojog, 10-14) and ``threading.Event``
(INV-konad, 10-15). Each was seen only by the twice-daily cron
``self-claims-gate``, because the per-PR arm of that gate was removed on
2026-08-30 at ~14 minutes of critical path (``test_ci_self_claims_gate_scope``
carries both decisions). Up to twelve hours of red ``dev`` per occurrence, and a
bisect each time.

WHY THE WHOLE GATE IS TOO SLOW AND THIS IS NOT. ``verify-claims`` needs the full
behaviour map -- every language, the linkers, taint; 1009 s cold with
``--minimal``. The question these four regressions ask needs none of that.
``_call_production_coverage`` decides ``qualifying_only`` from the EDGE SET
alone: which external modules the shipped Python calls into, and whether the
catalogue rows or enumerates each. The Python analyzer produces that edge set
by itself, receiver typing included -- ``resolution_quality: type_inferred`` is
stamped in ``py.py``, not by a later pass -- in 26 s over the 382 shipped files
on 2026-10-17, against ~6 minutes for the map.

NOT A SECOND RULE. This module runs the registered ``python`` analyzer over the
packaging-declared source roots (``shipped_artifact_roots`` -- the denominator
``analysis_scope: shipped_artifact`` gives the cron), loads the catalogue the
cron loads (``python.yaml``, the shipped community overlays beneath it, the
claims file's overlays on top) and asks the cron's own decision function. There
is no AST walk of its own and no second spelling of "module slot": the symbol
ids are the analyzer's, relativised by the pipeline's own ``_relativize_ir_paths``,
and the adjudication is ``verify_claims``'. A hand-rolled walk would have been
the seventh home for module extraction WI-ribuz warns about, and it would have
had to re-derive receiver typing to catch the ``threading.Event`` shape at all.

CALIBRATED THE DAY IT WAS WRITTEN. On ``dev`` at 416315836b the analyzer-only
edge set (67,220 edges) and the full map reach the same verdict --
``complete=False, qualifying_only=True`` with the same three launch sites,
``subprocess.Popen``, ``subprocess.Popen.communicate``, ``subprocess.run`` --
and removing either the ``threading.Event`` or the ``datetime.date`` grant from
the loaded catalogue flips it to ``qualifying_only=False`` naming that module,
which is exactly the regression this exists to catch.

RECALIBRATED 2026-11-04, AND THE CALIBRATION ABOVE WAS INCOMPLETE (INV-hotib /
WI-jizam). On ``dev`` at acf67df4cb the cron withheld all eighteen verdicts on
three slots -- ``hypergumbo_core.scip._generated.scip_pb2``,
``textual.widgets.Select``, ``datetime.timezone`` -- and this guard returned 0
on the same tree. "The analyzer produces that edge set by itself" was true and
not enough: the adjudicator reads the edges AFTER three pipeline stages that
change the population, and the raw analyzer edges skipped all three.
``analyzer_edges`` now runs them, through the pipeline's own functions:

* the orchestrator's file-symbol synthesis (``all_analyzers``: WI-ramuv,
  WI-dagif), so a dropped file's import edge has a node to be dropped with;
* ``_classify_symbols`` and the tier filter, lifted out of ``run_survey`` into
  ``tier_filter.apply_tier_filter`` so there is ONE copy: a DERIVED file
  (``_pb2.py``) leaves with every edge touching it, and the cross-package call
  into it stays as the unresolved module slot the cron names. Over the raw
  edges the file counted as analysed and ``_is_analyzed_module`` suppressed
  that call as first-party;
* boundary synthesis's id remap (``ir.create_boundary_nodes`` +
  ``apply_external_id_remap``), which normalises a dangling id's kind slot to
  ``external_symbol`` (ADR-0036 ruling 2). ``_external_call_sites`` admits only
  ``external_symbol`` / ``unresolved``, so an attribute read -- the analyzer's
  ``python:datetime.timezone:0-0:datetime.timezone.utc:attribute`` -- was
  invisible here and counted in the cron.

Measured on the acf67df4cb worktree with the current catalogue: 46,029 edges
after the three stages (71,539 raw), ``qualifying_only=False`` naming
``hypergumbo_core.scip._generated.scip_pb2`` and ``textual.widgets.Select``
(``datetime.timezone`` is granted since #2042), 31 s. On dev after #2042: 0.
Edge counts here and in the cron still differ -- linkers, taint and the
non-Python analyzers are not run -- but the population the coverage gate
reads is now built by the same stages.

WHAT IT DOES NOT CATCH, said plainly. The drift gate compares eighteen
verdicts, their caveat kinds and their credited-flow buckets; this compares one
bit of coverage. A taint-flow regression, a sanitizer that stops crediting, a
verdict moving to ``violated`` -- all still wait for the cron. The guard asks
only: can the shipped tree's boundary verdicts still be QUALIFIED?

THE FILE LIST IS NOT TEST-FILTERED, and the first draft was. ``is_test_file``
calls ``spec_validator.py`` a test (the ``*spec*`` rule), so that module vanished
from the analysed set and its own callers reported
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
        that withholds all eighteen self-claims at once."""
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

    TWO PIPELINE STAGES RUN HERE BECAUSE THE ADJUDICATOR READS THEIR OUTPUT,
    and skipping them is how this guard passed dev acf67df4cb while the cron
    withheld every self-claim on three slots (INV-hotib / WI-jizam). Both are
    the pipeline's own functions, not a restatement:

    * ``_classify_symbols`` + :func:`tier_filter.apply_tier_filter`: a DERIVED
      file (``scip/_generated/scip_pb2.py``, tier 4 by the ``_pb2.py`` rule)
      leaves with every edge touching it, so a cross-package call into it
      stays as the unresolved ``hypergumbo_core.scip._generated.scip_pb2``
      slot the cron names. Over the raw analyzer edges the file counted as
      analysed and ``_is_analyzed_module`` suppressed that call as first-party.
    * :func:`ir.create_boundary_nodes` + :func:`ir.apply_external_id_remap`:
      boundary synthesis normalises a dangling id's kind slot to
      ``external_symbol`` (ADR-0036 ruling 2). ``_external_call_sites`` admits
      only ``external_symbol`` / ``unresolved`` terminals, so an attribute read
      -- ``python:datetime.timezone:0-0:datetime.timezone.utc:attribute``
      straight from the analyzer -- was invisible to the coverage gate here and
      counted in the cron. The synthesised nodes themselves are discarded; only
      the remap is wanted.

    Order follows ``run_survey``: classify and filter on the analyzer's
    absolute-path ids, synthesise, then relativise.
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
