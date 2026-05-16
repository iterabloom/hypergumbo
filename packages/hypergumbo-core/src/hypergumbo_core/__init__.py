# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hypergumbo Core: Core infrastructure for repo behavior map generation.

This package contains the substrate that the ``hypergumbo-lang-*``
analyzer packages and the CLI build on top of. Top-level Python
exports from this module are intentionally minimal (just
``PASS_VERSION`` / ``__version__`` / ``make_pass_id``); the rest of
the surface is reached through submodule imports.

Submodules
----------
- ``ir`` — Symbol, Edge, Span, AnalysisRun, ExternalRef,
  UsageContext (the IR types every analyzer emits)
- ``analyze`` — analyzer base classes + entry-point registry
- ``linkers`` — Tier 2 edge-recovery passes (Protocol / Bridge /
  Framework / Infrastructure, per ADR-0003-ext)
- ``frameworks`` (data) + ``framework_patterns`` (code) — YAML-driven
  framework detection
- ``cli`` — argparse entry point
- ``sketch`` / ``sketch_embeddings`` — Markdown sketch generation
- ``compact`` — budget-aware selection + residual summarization
- ``slice`` — entry-point subgraph extraction
- ``selection`` — shared filters / token budget / language-
  proportional selection helpers
- ``ranking`` — centrality computation + dampener stack
- ``cfg`` + ``cfg_nodes`` + ``dataflow`` + ``dataflow_patterns`` —
  control-flow + reaching-definitions infrastructure for ADR-0017
  precise taint-flow
- ``io_boundary`` + ``io_primitives`` — IO-edge composition
  (ADR-0016)
- ``taint`` + ``taint_refine`` + ``taint_sources`` +
  ``taint_sanitizers`` — taint-zone analysis (ADR-0017)
- ``verify_claims`` — security-claim verification CLI surface
- ``safety_zones`` — wrapper functions for fs-write sites that
  carry per-entry-point trust-zone labels
- ``supply_chain`` — first-party / internal / external / derived
  tier classification
- ``edge_types`` / ``symbol_kinds`` / ``evidence_types`` /
  ``axis_meta_keys`` — canonical registries for axis-bearing IR
  fields (ADR-0023 / ADR-0024 / ADR-0027 / ADR-0028)
- ``audit_findings`` — verdict-table loader for per-axis audits
- ``scip`` — SCIP protobuf shim shared with
  ``hypergumbo-lang-rust-analyzer``

Version Note
------------
- ``__version__`` — the tool/package version. Tracks CLI features,
  analyzer additions, and bug fixes. Updated with each release.

- ``SCHEMA_VERSION`` (in ``schema.py``) — the output format version.
  Tracks changes to the JSON output schema. Consumers should check
  ``schema_version`` in output to ensure compatibility.

These versions are independent. The schema version only changes when
the output format changes; the tool version changes with any release.

See ADR-0010 for the modular package architecture.
"""
__all__ = ["PASS_VERSION", "__version__", "make_pass_id"]
__version__ = "5.0.1"

from .ir import PASS_VERSION, make_pass_id
