# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hypergumbo: Local-first repo behavior map generator.

This is a meta-package that installs the core and the three tree-sitter
language packages:
- hypergumbo-core: Core infrastructure (CLI, IR, slice, sketch)
- hypergumbo-lang-mainstream: Popular language analyzers (Python, JS, Java, etc.)
- hypergumbo-lang-common: Domain-specific languages (Haskell, Elixir, etc.)
- hypergumbo-lang-extended1: Specialized languages (Zig, Agda, Solidity, etc.)

Optional extras: ``[rust-analyzer]`` and ``[scip-python]`` (SCIP backends),
``[embeddings]``.

Usage:
    pip install hypergumbo      # Install core + the three language packages
    hypergumbo survey .         # Full analysis -> survey.json (cache dir; --out to choose)
    hypergumbo sketch .         # Generate token-budgeted overview
"""

# Re-export version from core
from hypergumbo_core import __version__

__all__ = ["__version__"]
