"""Hypergumbo extended language analyzers (set 1).

This package provides analyzers for specialized and emerging languages,
including systems programming alternatives, proof assistants, blockchain,
hardware description languages, and niche domain-specific languages.
"""
from hypergumbo_core.analyze.all_analyzers import AnalyzerSpec

__version__ = "2.0.0"

# Analyzer specifications for extended languages
# These are registered via entry_points in pyproject.toml
ANALYZER_SPECS = [
    # Systems programming (modern alternatives to C/C++)
    AnalyzerSpec("zig", "hypergumbo_lang_extended1.zig", "analyze_zig"),
    AnalyzerSpec("odin", "hypergumbo_lang_extended1.odin", "analyze_odin"),
    AnalyzerSpec("nim", "hypergumbo_lang_extended1.nim", "analyze_nim"),
    AnalyzerSpec("d", "hypergumbo_lang_extended1.d_lang", "analyze_d"),
    AnalyzerSpec("ada", "hypergumbo_lang_extended1.ada", "analyze_ada"),
    AnalyzerSpec("pascal", "hypergumbo_lang_extended1.pascal", "analyze_pascal"),
    AnalyzerSpec("v", "hypergumbo_lang_extended1.v_lang", "analyze_v"),

    # Proof assistants and formal verification
    AnalyzerSpec("agda", "hypergumbo_lang_extended1.agda", "analyze_agda"),
    AnalyzerSpec("lean", "hypergumbo_lang_extended1.lean", "analyze_lean"),

    # Enterprise and legacy
    AnalyzerSpec("cobol", "hypergumbo_lang_extended1.cobol", "analyze_cobol"),
    AnalyzerSpec("apex", "hypergumbo_lang_extended1.apex", "analyze_apex"),

    # Blockchain and smart contracts
    AnalyzerSpec("solidity", "hypergumbo_lang_extended1.solidity", "analyze_solidity"),

    # Hardware description
    AnalyzerSpec("verilog", "hypergumbo_lang_extended1.verilog", "analyze_verilog_files"),
    AnalyzerSpec("vhdl", "hypergumbo_lang_extended1.vhdl", "analyze_vhdl_files"),

    # Actor model and concurrent
    AnalyzerSpec("pony", "hypergumbo_lang_extended1.pony", "analyze_pony"),
    AnalyzerSpec("gleam", "hypergumbo_lang_extended1.gleam", "analyze_gleam"),

    # Lisp family
    AnalyzerSpec("janet", "hypergumbo_lang_extended1.janet", "analyze_janet"),
    AnalyzerSpec("fennel", "hypergumbo_lang_extended1.fennel", "analyze_fennel"),

    # Cross-platform and game development
    AnalyzerSpec("hack", "hypergumbo_lang_extended1.hack", "analyze_hack"),
    AnalyzerSpec("haxe", "hypergumbo_lang_extended1.haxe", "analyze_haxe"),
    AnalyzerSpec("gdscript", "hypergumbo_lang_extended1.gdscript", "analyze_gdscript"),
    AnalyzerSpec("luau", "hypergumbo_lang_extended1.luau", "analyze_luau"),

    # Scientific computing
    AnalyzerSpec("wolfram", "hypergumbo_lang_extended1.wolfram", "analyze_wolfram"),
    AnalyzerSpec("llvm_ir", "hypergumbo_lang_extended1.llvm_ir", "analyze_llvm_ir"),

    # Interface definitions and config
    AnalyzerSpec("capnp", "hypergumbo_lang_extended1.capnp", "analyze_capnp"),
    AnalyzerSpec("smithy", "hypergumbo_lang_extended1.smithy", "analyze_smithy"),
    AnalyzerSpec("jsonnet", "hypergumbo_lang_extended1.jsonnet", "analyze_jsonnet"),
    AnalyzerSpec("kdl", "hypergumbo_lang_extended1.kdl", "analyze_kdl"),
    AnalyzerSpec("prisma", "hypergumbo_lang_extended1.prisma", "analyze_prisma"),

    # Web templating
    AnalyzerSpec("twig", "hypergumbo_lang_extended1.twig", "analyze_twig"),

    # Query languages
    AnalyzerSpec("sparql", "hypergumbo_lang_extended1.sparql", "analyze_sparql"),

    # Shell and scripting
    AnalyzerSpec("tcl", "hypergumbo_lang_extended1.tcl", "analyze_tcl"),
    AnalyzerSpec("fish", "hypergumbo_lang_extended1.fish", "analyze_fish"),

    # Documentation
    AnalyzerSpec("bibtex", "hypergumbo_lang_extended1.bibtex", "analyze_bibtex"),

    # Build systems
    AnalyzerSpec("bitbake", "hypergumbo_lang_extended1.bitbake", "analyze_bitbake"),
]

__all__ = ["ANALYZER_SPECS", "__version__"]
