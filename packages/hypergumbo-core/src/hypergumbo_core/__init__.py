# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hypergumbo Core: Core infrastructure for repo behavior map generation.

This package provides the core infrastructure for static analysis, including:
- IR (Symbol, Edge, Span) data structures
- Analysis framework (base classes, registry)
- Linkers for cross-language/cross-component relationships
- Framework pattern detection
- CLI entry point

Version Note
------------
- **__version__**: The tool/package version. This version tracks CLI features,
  analyzer additions, and bug fixes. Updated with each release.

- **SCHEMA_VERSION** (in schema.py): The output format version. This version
  tracks breaking changes to the JSON output schema. Consumers should check
  schema_version in output to ensure compatibility.

These versions are independent. The schema version only changes when the output
format has breaking changes, while the tool version changes with any release.

See ADR-0010 for the modular package architecture.
"""
__all__ = ["PASS_VERSION", "__version__", "make_pass_id"]
__version__ = "2.4.0"

from .ir import PASS_VERSION, make_pass_id
