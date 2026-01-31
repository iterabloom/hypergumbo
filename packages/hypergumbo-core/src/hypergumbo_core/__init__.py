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
"""
__all__ = ["__version__"]
__version__ = "2.0.0"
