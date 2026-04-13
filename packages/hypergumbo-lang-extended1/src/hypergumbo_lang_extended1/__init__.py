# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hypergumbo extended language analyzers (set 1).

This package provides analyzers for specialized and emerging languages,
including systems programming alternatives, proof assistants, blockchain,
hardware description languages, and niche domain-specific languages.
"""

__version__ = "2.6.0"

# Module paths for analyzer discovery via entry-points (ADR-0012 Step 1).
# Importing each module triggers the @register_analyzer decorator within it.
ANALYZER_MODULES = [
    # Systems programming (modern alternatives to C/C++)
    "hypergumbo_lang_extended1.asm",
    "hypergumbo_lang_extended1.zig",
    "hypergumbo_lang_extended1.odin",
    "hypergumbo_lang_extended1.nim",
    "hypergumbo_lang_extended1.d_lang",
    "hypergumbo_lang_extended1.ada",
    "hypergumbo_lang_extended1.pascal",
    "hypergumbo_lang_extended1.v_lang",

    # Proof assistants and formal verification
    "hypergumbo_lang_extended1.agda",
    "hypergumbo_lang_extended1.lean",
    "hypergumbo_lang_extended1.tlaplus",

    # Enterprise and legacy
    "hypergumbo_lang_extended1.cobol",
    "hypergumbo_lang_extended1.apex",

    # Blockchain and smart contracts
    "hypergumbo_lang_extended1.solidity",
    "hypergumbo_lang_extended1.circom",

    # Hardware description
    "hypergumbo_lang_extended1.verilog",
    "hypergumbo_lang_extended1.vhdl",

    # Actor model and concurrent
    "hypergumbo_lang_extended1.pony",
    "hypergumbo_lang_extended1.gleam",

    # Lisp family
    "hypergumbo_lang_extended1.janet",
    "hypergumbo_lang_extended1.fennel",

    # Cross-platform and game development
    "hypergumbo_lang_extended1.hack",
    "hypergumbo_lang_extended1.haxe",
    "hypergumbo_lang_extended1.gdscript",
    "hypergumbo_lang_extended1.luau",

    # Scientific computing
    "hypergumbo_lang_extended1.wolfram",
    "hypergumbo_lang_extended1.llvm_ir",

    # Interface definitions and config
    "hypergumbo_lang_extended1.capnp",
    "hypergumbo_lang_extended1.smithy",
    "hypergumbo_lang_extended1.jsonnet",
    "hypergumbo_lang_extended1.kdl",
    "hypergumbo_lang_extended1.prisma",

    # Web templating
    "hypergumbo_lang_extended1.twig",
    "hypergumbo_lang_extended1.blade",

    # Diagram and plotting
    "hypergumbo_lang_extended1.mermaid",
    "hypergumbo_lang_extended1.gnuplot",

    # Query languages
    "hypergumbo_lang_extended1.sparql",

    # Shell and scripting
    "hypergumbo_lang_extended1.tcl",
    "hypergumbo_lang_extended1.fish",

    # Documentation
    "hypergumbo_lang_extended1.bibtex",

    # Build systems
    "hypergumbo_lang_extended1.bitbake",
]

__all__ = ["ANALYZER_MODULES", "__version__"]
