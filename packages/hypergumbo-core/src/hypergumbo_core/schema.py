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
  YAML patterns bump patch. Consumers can use this to check compatibility.

- **__version__** (in __init__.py): The tool/package version. This increments
  with every release (new analyzers, bug fixes, performance improvements,
  CLI changes, etc.). It does NOT indicate output format changes.

These versions evolve independently. The tool can have many releases while
the schema stays stable if the output format doesn't change.

How It Works
------------
The behavior map is the primary output format for hypergumbo analysis.
This module defines several versioned schemes:

- **schema_version**: Overall format version (breaking changes increment minor)
- **confidence_model**: How confidence scores are computed
- **stable_id_scheme**: How stable_id hashes are generated
- **shape_id_scheme**: How shape_id (structure) hashes are generated
- **repo_fingerprint_scheme**: How repo state is fingerprinted for caching

new_behavior_map() returns an empty structure with all top-level fields
initialized, ensuring consistent output even for empty analyses.

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
from datetime import datetime, timezone
from typing import Any, Dict

SCHEMA_VERSION = "0.10.0"
CONFIDENCE_MODEL = "hypergumbo-evidence-v1"
STABLE_ID_SCHEME = "hypergumbo-stableid-v4"
SHAPE_ID_SCHEME = "hypergumbo-shapeid-v2"
REPO_FINGERPRINT_SCHEME = "hypergumbo-repofp-v1"
# WI-fanun: scheme tag for Symbol.fingerprint on source-code Symbols.
# Populated by the orchestrator post-pass in ``hypergumbo_core.fingerprint``
# (manifest producers like toml-v1 / json-v1 / wgsl-v1 keep their own
# scheme — this tag only describes the structural-AST fingerprints).
SYMBOL_FINGERPRINT_SCHEME = "hypergumbo-symbol-fp-v1"


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
        "view": "behavior_map",
        "generated_at": _now_iso_utc(),
        "analysis_incomplete": False,
        "analysis_runs": [],
        "profile": {},
        "nodes": [],
        "edges": [],
        "usage_contexts": [],
        "features": [],
        "metrics": {},
        "limits": {},
        "entrypoints": [],
        "reproducibility_context": build_reproducibility_context(),
    }


# INV-morag option B: L2 reproducibility context.
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
    "Behavior maps with matching pass_versions, hypergumbo_version, "
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
    """
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

    return tree_sitter_version, grammars


def build_reproducibility_context() -> Dict[str, Any]:
    """Build the top-level ``reproducibility_context`` block (INV-morag B).

    Captures the L2 reproducibility level (hypergumbo version, Python
    interpreter version, tree-sitter library + grammar versions when
    available) and documents the L3-L5 factors that are explicitly NOT
    captured. The ``implications`` text tells the consumer what level of
    diff-attribution they can expect from these fields alone.

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

