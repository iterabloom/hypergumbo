"""Schema versioning and behavior map factory.

This module defines the output schema version and provides a factory
for creating empty behavior map structures with all required fields.

Version Distinction
-------------------
**SCHEMA_VERSION vs Tool Version:**

- **SCHEMA_VERSION** (defined here): The output format version, embedded in
  every JSON output as `schema_version`. It only increments when there are
  breaking changes to the output schema (new required fields, changed field
  types, removed fields, etc.). Consumers can use this to check compatibility
  with their parsers.

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
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

SCHEMA_VERSION = "0.2.0"
CONFIDENCE_MODEL = "hypergumbo-evidence-v1"
STABLE_ID_SCHEME = "hypergumbo-stableid-v1"
SHAPE_ID_SCHEME = "hypergumbo-shapeid-v1"
REPO_FINGERPRINT_SCHEME = "hypergumbo-repofp-v1"


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
        "view": "behavior_map",
        "generated_at": _now_iso_utc(),
        "analysis_incomplete": False,
        "analysis_runs": [],
        "profile": {},
        "nodes": [],
        "edges": [],
        "features": [],
        "metrics": {},
        "limits": {},
    }

