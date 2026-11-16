# SPDX-License-Identifier: AGPL-3.0-or-later
"""Schema versioning and behavior map factory.

This module defines the output schema version and provides a factory
for creating empty behavior map structures with all required fields.

Version Distinction
-------------------
**SCHEMA_VERSION vs Tool Version:**

- **SCHEMA_VERSION** (defined here): The schema documentation version, embedded
  in every JSON output as `schema_version`. It increments for significant changes
  to `docs/schema.json`, which is a **unified schema** containing both behavior map
  output definitions AND framework pattern types for YAML validation. Breaking
  changes to output format bump minor; additions like new type definitions for
  YAML patterns bump patch. One recorded exception: 0.20.10 renamed
  `skip_reason_code` to `silence_reason` (breaking) as a patch bump, the old key
  having shipped only one version earlier in 0.20.9. Consumers can use this to
  check compatibility.

- **__version__** (in __init__.py): The tool/package version. This increments
  with every release (new analyzers, bug fixes, performance improvements,
  CLI changes, etc.). It does NOT indicate output format changes. It surfaces
  in output under TWO field names that are both aliases of it, not schema
  versions: `reproducibility_context.captured.hypergumbo_version` and
  `limits.analyzer_version` (`f"hypergumbo-{__version__}"`).

- **Per-view / per-sub-schema versions** (the third axis — WI-bobog / WI-romup):
  several JSON surfaces carry their OWN version, independent of both of the
  above. The CLI read-view envelopes (`routes` / `test-coverage` / `config` /
  `catalog` / `cache-status` / `repeat_finder`) share
  `READ_VIEW_SCHEMA_VERSION` — one placeholder until a view needs to evolve its
  wire shape independently, at which point it promotes to its own named
  constant, as `dead-code-maybe` (`DEAD_CODE_MAYBE_SCHEMA_VERSION`),
  `io-boundaries` (`io_boundary.IO_BOUNDARIES_SCHEMA_VERSION`),
  `verify-claims` (`verify_claims.VERIFY_CLAIMS_SCHEMA_VERSION`), and the
  embedded `validation_report` block
  (`spec_validator.VALIDATION_REPORT_SCHEMA_VERSION`) already have. A change to
  one view's wire shape bumps only that view's version, NOT the top-level
  `schema_version`.

These three axes are deliberately independent. Do NOT consolidate them onto a
single number, and do NOT rename the wire fields (`hypergumbo_version`,
`analyzer_version`, the per-view `schema_version`) — each has spec text and
consumers, so a rename is a covert wire-break requiring a major bump. The tool
can have many releases while the schema stays stable if the output format
doesn't change.

How It Works
------------
The behavior map is the primary output format for hypergumbo analysis.
This module defines several versioned schemes:

- **schema_version**: Overall format version (breaking changes increment minor,
  save the 0.20.10 patch-bump exception noted above)
- **confidence_model**: How confidence scores are computed
- **stable_id_scheme**: How stable_id hashes are generated
- **shape_id_scheme**: How shape_id (structure) hashes are generated
- **repo_fingerprint_scheme**: How repo state is fingerprinted for caching
- **symbol_fingerprint_scheme**: How Symbol.fingerprint values are computed
  (context-aware whole-file subtree hashing; populated by the
  `hypergumbo_core.fingerprint` post-pass)
- **scip_stable_id_scheme**: How the rust-analyzer (SCIP) arm computes the
  stable_id values it mints (a second algorithm under one field, so a
  scheme and a value label of its own)

Every identity value carries the label of the scheme that produced it
(`hgstableid15:`, `hgshapeid4:`, `hgrepofp3:`, `hgscipid1:`, `hgfp2:`):
`value_label()` derives each label from its scheme constant, so a scheme
bump is one edit. The labels name the value and its scheme version; the
digest after the label is what the algorithm computed.

new_behavior_map() returns an empty structure with all top-level fields
initialized, ensuring consistent output even for empty analyses. It also
embeds a reproducibility_context block built by build_reproducibility_context().

VIEW_NAMES enumerates the `view` values a behavior map may carry (the base
`behavior_map` plus the `compact` / `tiered` budget projections), so the schema
generator pins `view` to one single-sourced enum.

Why This Design
---------------
- Explicit versioning enables consumers to detect format changes
- Scheme identifiers let consumers know how to interpret computed IDs
- Factory function ensures all required fields are present
- Separating schema from IR keeps output format concerns isolated

Related Files
-------------
This module works with two other components to provide schema infrastructure:

**This file (schema.py)** - Runtime constants and factory
- Defines SCHEMA_VERSION and scheme identifiers
- Provides new_behavior_map() factory for output generation
- Builds the L2 reproducibility_context block via
  build_reproducibility_context() (INV-morag): captures
  hypergumbo/Python/tree-sitter/grammar versions and documents the
  L3-L5 factors that are explicitly not captured.
- Used at runtime when hypergumbo generates JSON output

**scripts/generate-schema** - Documentation generator
- Generates docs/schema.json from Python dataclasses
- Imports SCHEMA_VERSION from here to embed in the JSON Schema
- Run at dev time; pre-commit hooks verify it stays in sync

**docs/schema.json** - Unified formal schema
- Formal JSON Schema for external validation and IDE autocompletion
- Contains BOTH behavior map output definitions AND framework pattern
  types (Pattern, FrameworkPatternDef) for YAML validation
- Auto-generated; do not edit directly
"""
from __future__ import annotations

import importlib.metadata
import platform
import re
from datetime import datetime, timezone
from typing import Any, Dict

from .installed_env import kept_per_environment

SCHEMA_VERSION = "0.21.1"
# Breaking output changes bump MINOR and additions bump PATCH (spec "Breaking
# output changes bump minor"; the one exception is noted in the module
# docstring). The per-version record of what changed is CHANGELOG.md.
# Canonical ``view`` field values a behavior map (or its budget-limited
# projections) may carry: the base analysis emits ``behavior_map``; the compact
# and tiered projections emit their own view name. Single-sourced here so the
# schema generator enumerates them in one place — future projection views append
# here (WI-tagaj). The published schema pins ``view`` to this enum, so every
# projected view validates.
VIEW_NAMES = ("behavior_map", "compact", "tiered")
# Wire-format version carried by the CLI *read-view* JSON envelopes that project
# or summarize a behavior map without BEING the behavior map (routes /
# test-coverage / config / catalog / cache-status / repeat_finder). These
# share one placeholder version until a view needs to evolve its wire shape
# independently — at which point it promotes to its own named constant, as
# dead-code-maybe (DEAD_CODE_MAYBE_SCHEMA_VERSION, below), io-boundaries
# (IO_BOUNDARIES_SCHEMA_VERSION) and verify-claims
# (VERIFY_CLAIMS_SCHEMA_VERSION) already have. Single-sourced here (WI-bobog) so
# the six view sites cannot drift; DISTINCT from the top-level bm.json
# SCHEMA_VERSION (a read view is not the behavior map) and from __version__ (the
# tool version). See the "Version Distinction" module docstring for the three
# version axes.
READ_VIEW_SCHEMA_VERSION = "0.1.0"
# Version of the ``dead-code-maybe`` envelope. It has its own constant rather
# than READ_VIEW_SCHEMA_VERSION because its wire shape evolves on its own;
# bumping the shared placeholder instead would announce a new version for
# views that did not change, and force an edit at every test that pins the
# placeholder (history: WI-jozah). Additions bump MINOR; the per-version record
# is CHANGELOG.md.
DEAD_CODE_MAYBE_SCHEMA_VERSION = "0.3.0"
# Format is ``hypergumbo-evidence-vMAJOR.MINOR`` (spec Appendix C): bump MINOR
# for a refinement (new evidence types, new evidence handling, re-derived
# scores in the same bands), MAJOR for an incompatible change.
# ``test_confidence_model_matches_documented_grammar`` pins the format; the
# per-version record is the spec's §12 / Appendix C (history: WI-huhin).
CONFIDENCE_MODEL = "hypergumbo-evidence-v2.2"
STABLE_ID_SCHEME = "hypergumbo-stableid-v15"
# Any change that alters computed stable_id values bumps the scheme (spec §6),
# even when only one language's values move: the scheme names the whole
# output, and there is no in-place migration. The per-transition record
# (driver, hash-basis change, which values moved) is the spec's
# "stable_id_scheme version history"; record a transition there before bumping.
# Every value carries the label STABLE_ID_PREFIX derived below, so a bump
# also relabels every value (ADR-0035 §"Value labels").
# shape_id: the Python hash folds the symbol kind (class/method/function) and
# the concrete AST node type into its prefix, so structurally-trivial symbols
# of different kinds do not collide — a module-level function vs a class method
# (both ``ast.FunctionDef``), an ``async def`` vs a ``class``. The scheme is
# global: a change to the Python or the tree-sitter path that alters computed
# values bumps it (spec §6). A change to the label alone is a change to the
# emitted value, so it bumps too (v3 -> v4 was the label).
SHAPE_ID_SCHEME = "hypergumbo-shapeid-v4"
# The AnalysisRun ``repo_fingerprint`` FIELD is rendered
# ``REPO_FINGERPRINT_PREFIX<64hex>`` (compute_repo_fingerprint_field). The
# bare digest is used only as the colon-free cache-dir path segment
# (compute_repo_fingerprint). A rendering change alters the emitted value, so
# it bumps the scheme like an algorithm change (spec §6; v2 -> v3 was the
# label).
REPO_FINGERPRINT_SCHEME = "hypergumbo-repofp-v3"
# The rust-analyzer (SCIP) arm's stable_id: a digest of the defining
# document's path and the SCIP moniker (scip/index.py ``scip_stable_id``), a
# different algorithm from the tree-sitter factories, so it is a scheme of
# its own with a label of its own (ADR-0035 §"Value labels"). A record the
# SCIP arm emits whose id the rust.py parity pass replaced carries the
# tree-sitter label: that value was computed by the tree-sitter formula.
SCIP_STABLE_ID_SCHEME = "hypergumbo-scip-stableid-v1"
# Scheme tag for Symbol.fingerprint, whose sole producer is the orchestrator
# post-pass in ``hypergumbo_core.fingerprint`` (whole-file parse + subtree
# hash). v2 corresponds to the ``hgfp2:`` prefix on every value; a change that
# alters emitted values bumps both (history: WI-fanun, WI-falum).
SYMBOL_FINGERPRINT_SCHEME = "hypergumbo-symbol-fp-v2"

#: ``-v<N>`` at the end of a scheme constant: the number a value label carries.
_SCHEME_NUMBER = re.compile(r"-v(\d+)$")


def value_label(word: str, scheme: str) -> str:
    """The in-value label ``hg<word><N>:`` of a scheme whose constant ends ``-v<N>``.

    The one place a label's number is read (ADR-0035 §"Value labels"): each
    identity value starts with the label of the scheme that produced it, and
    the number is the scheme constant's, so bumping the constant relabels
    every value with no second edit. ``word`` names the value (``stableid``,
    ``shapeid``, ``repofp``, ``scipid``); it is what tells a stable id from a
    shape id when the value is read without its field.
    """
    match = _SCHEME_NUMBER.search(scheme)
    if match is None:
        raise ValueError(f"scheme {scheme!r} does not end in -v<N>")
    return f"hg{word}{match.group(1)}:"


#: The label of every tree-sitter (and Python ``ast``) ``Symbol.stable_id``.
STABLE_ID_PREFIX = value_label("stableid", STABLE_ID_SCHEME)
#: The label of every ``Symbol.shape_id``.
SHAPE_ID_PREFIX = value_label("shapeid", SHAPE_ID_SCHEME)
#: The label of ``AnalysisRun.repo_fingerprint``.
REPO_FINGERPRINT_PREFIX = value_label("repofp", REPO_FINGERPRINT_SCHEME)
#: The label of a ``Symbol.stable_id`` the rust-analyzer (SCIP) arm minted.
SCIP_STABLE_ID_PREFIX = value_label("scipid", SCIP_STABLE_ID_SCHEME)


def identity_digest(value: str) -> str:
    """The digest of an identity value: the text after its label.

    For a consumer comparing digests across a label-only change (the
    stableid-v14 ``sha256:<hex>`` and stableid-v15 ``hgstableid15:<hex>``
    forms of one symbol's id share the hex). A digest says nothing about
    which algorithm produced it; compare digests only where the scheme
    history says the algorithm did not change.
    """
    return value.rpartition(":")[2]


def _now_iso_utc() -> str:
    """Return an ISO-8601 timestamp in UTC."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_behavior_map() -> Dict[str, Any]:
    """
    Construct an empty behavior_map view with all required top-level fields.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "confidence_model": CONFIDENCE_MODEL,
        "stable_id_scheme": STABLE_ID_SCHEME,
        "shape_id_scheme": SHAPE_ID_SCHEME,
        "repo_fingerprint_scheme": REPO_FINGERPRINT_SCHEME,
        "symbol_fingerprint_scheme": SYMBOL_FINGERPRINT_SCHEME,
        "scip_stable_id_scheme": SCIP_STABLE_ID_SCHEME,
        "view": "behavior_map",
        "generated_at": _now_iso_utc(),
        "analysis_incomplete": False,
        "analysis_runs": [],
        "profile": {},
        "nodes": [],
        "edges": [],
        "module_sets": {},
        "usage_contexts": [],
        "features": [],
        "metrics": {},
        "limits": {},
        "entrypoints": [],
        "request_ingress": [],
        "reproducibility_context": build_reproducibility_context(),
    }


# L2 reproducibility context (history: INV-morag).
#
# Reproducibility is a spectrum, not a yes/no claim. This block captures
# the L2 level — direct dependencies and runtime identity — and explicitly
# disclaims L3 (transitive pip packages), L4 (OS / libc / locale), and L5
# (hardware / microcode). The "not_captured" array is the honest part: it
# tells the consumer which factors may affect output reproducibility but
# aren't recorded in the behavior map. A consumer observing unexplained
# diffs between two behavior maps with matching captured fields should
# suspect a not_captured factor.

_REPRO_NOT_CAPTURED: tuple[str, ...] = (
    "Transitive Python package versions (only direct deps like tree-sitter "
    "library + grammar packages are captured; the full pip freeze is not).",
    "OS version, kernel, libc, locale, timezone, environment variables.",
    "Hardware (CPU model, microcode, instruction set extensions, "
    "memory layout). Floating-point determinism is also not guaranteed.",
)

_REPRO_IMPLICATIONS: str = (
    "Behavior maps with matching per-pass code hashes "
    "(analysis_runs[].pass_version), hypergumbo_version, "
    "python_version, tree_sitter_version, and per-grammar versions should "
    "be functionally identical up to OS-level and hardware variation. "
    "Diffs that are not explained by these fields suggest a not_captured "
    "factor (transitive deps, OS, hardware) — file as a tracker item if "
    "you can isolate one."
)


def _detect_tree_sitter_versions() -> tuple[str | None, Dict[str, str]]:
    """Best-effort introspection of installed tree-sitter library + grammars.

    Returns (library_version_or_None, {grammar_pkg: version}). When the
    library is not importable (hypergumbo can run without tree-sitter, e.g.
    when only regex / AST analyzers are active), both halves are None / {}.
    The hypergumbo behavior map is honest about absence: an empty
    ``grammars`` block means "no tree-sitter-* packages were found on the
    PYTHONPATH at run time" — not "this analysis didn't use any grammars".

    READ ONCE PER ENVIRONMENT. Finding the grammars means parsing the
    metadata of every installed distribution (33 ms for 260 of them, a third
    of a run on a one-file repository), and the answer is a fact about the
    environment, so it is kept by :func:`installed_env.kept_per_environment`
    until a distribution is installed, upgraded or removed. The slot names
    the two ``importlib.metadata`` functions read, so a stand-in for either
    is never answered with the real environment's versions. Each caller gets
    its own dictionary.
    """
    version, grammars = kept_per_environment(
        ("tree-sitter-versions", importlib.metadata.version,
         importlib.metadata.distributions),
        _read_tree_sitter_versions,
    )
    return version, dict(grammars)


def _read_tree_sitter_versions() -> tuple[str | None, tuple[tuple[str, str], ...]]:
    """The walk behind :func:`_detect_tree_sitter_versions`, as immutable pairs."""
    try:
        tree_sitter_version: str | None = importlib.metadata.version("tree-sitter")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - tree-sitter is installed in dev/CI; defensive for non-tree-sitter installs
        tree_sitter_version = None  # pragma: no cover

    grammars: Dict[str, str] = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"]
        if not name:  # pragma: no cover - defensive: malformed dist metadata
            continue  # pragma: no cover
        if name.startswith("tree-sitter-") or name == "tree-sitter-language-pack":
            grammars[name] = dist.version

    return tree_sitter_version, tuple(grammars.items())


def build_reproducibility_context() -> Dict[str, Any]:
    """Build the top-level ``reproducibility_context`` block (INV-morag).

    Captures the L2 reproducibility level (hypergumbo version, Python
    interpreter version, tree-sitter library + grammar versions when
    available) and documents the L3-L5 factors that are explicitly NOT
    captured. The ``implications`` text tells the consumer what level of
    diff-attribution they can expect from these fields alone.

    Called at map-init (``new_behavior_map``) before any node exists, so the
    ``captured.grammars`` seeded here is EVERY installed ``tree-sitter-*`` dist.
    The finalize chokepoint (``finalize._finalize_prune_repro_grammars``, ADR-0043)
    later replaces it with only the grammars whose analyzer pass actually emitted
    nodes (WI-fonod), pack-backed grammars expanded to per-language
    ``tree-sitter-language-pack:<lang>`` entries (WI-givad); a repo whose only
    analyzers are ast-based (e.g. python) ends up with no ``grammars`` key at all.
    ``_detect_tree_sitter_versions`` itself stays unscoped — its other caller,
    ``analyzer_identity``, needs the full install list for cache correctness.

    See the module-level commentary and INV-morag's tracker description for
    the design rationale.
    """
    from . import __version__ as _hypergumbo_version

    ts_version, grammars = _detect_tree_sitter_versions()

    captured: Dict[str, Any] = {
        "hypergumbo_version": _hypergumbo_version,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
    }
    if ts_version is not None:
        captured["tree_sitter_version"] = ts_version
    if grammars:
        captured["grammars"] = dict(sorted(grammars.items()))

    return {
        "level": "L2",
        "captured": captured,
        "not_captured": list(_REPRO_NOT_CAPTURED),
        "implications": _REPRO_IMPLICATIONS,
    }

