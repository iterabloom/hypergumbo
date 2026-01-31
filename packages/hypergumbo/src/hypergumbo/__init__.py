"""Hypergumbo: Local-first repo behavior map generator.

This is a meta-package that installs all hypergumbo components:
- hypergumbo-core: Core infrastructure (CLI, IR, slice, sketch)
- hypergumbo-lang-mainstream: Popular language analyzers (Python, JS, Java, etc.)
- hypergumbo-lang-common: Domain-specific languages (Haskell, Elixir, etc.)
- hypergumbo-lang-extended1: Specialized languages (Zig, Agda, Solidity, etc.)

Usage:
    pip install hypergumbo      # Install all components
    hypergumbo run .            # Run analysis on current directory
    hypergumbo sketch .         # Generate token-budgeted overview
"""

# Re-export version from core
from hypergumbo_core import __version__

__all__ = ["__version__"]
