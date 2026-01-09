"""Hypergumbo: Local-first repo behavior map generator.

This package provides static analysis tools for generating behavior maps
from source code repositories.

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
__version__ = "0.9.0"
