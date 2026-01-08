"""Consolidated analyzer registry for cli.py.

This module provides a single import point for all language analyzers,
eliminating 65+ separate imports in cli.py.

How It Works
------------
- Imports all analyzer functions
- Exports an ANALYZERS list with (name, function, options) tuples
- run_behavior_map() iterates over this list instead of 50+ code blocks

Why This Design
---------------
- Single import point: `from .analyze.all_analyzers import ANALYZERS, collect_result`
- Adding a new analyzer: add one line here, no changes to cli.py
- The list is ordered by priority (core analyzers first)
- Options dict allows per-analyzer configuration (max_files support, etc.)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, NamedTuple

from ..ir import Symbol, Edge
from ..limits import Limits

# Analyzer imports removed - using lazy loading via get_func() for test patchability


class AnalyzerSpec(NamedTuple):
    """Specification for an analyzer in the registry.

    Attributes:
        name: Unique identifier for this analyzer (for logging/debugging)
        module_path: Module path containing the analyzer function
        func_name: Name of the analyzer function in the module
        supports_max_files: Whether this analyzer accepts max_files parameter
        capture_symbols_as: If set, store symbols in a separate variable for linkers
    """

    name: str
    module_path: str
    func_name: str
    supports_max_files: bool = False
    capture_symbols_as: str | None = None

    def get_func(self) -> Callable[..., Any]:
        """Get the analyzer function via dynamic import.

        This enables patching to work correctly in tests, since
        we look up the function at call time rather than import time.
        """
        import importlib
        module = importlib.import_module(self.module_path)
        return getattr(module, self.func_name)


# Ordered list of all analyzers.
# Core analyzers (no tree-sitter deps) come first.
# Language analyzers are roughly ordered by popularity.
# Using module paths for lazy loading to enable test patching.
ANALYZERS: list[AnalyzerSpec] = [
    # Core analyzers (no optional dependencies)
    AnalyzerSpec("python", "hypergumbo.analyze.py", "analyze_python", supports_max_files=True),
    AnalyzerSpec("html", "hypergumbo.analyze.html", "analyze_html", supports_max_files=True),

    # Popular languages with tree-sitter support
    AnalyzerSpec("javascript", "hypergumbo.analyze.js_ts", "analyze_javascript", supports_max_files=True),
    AnalyzerSpec("php", "hypergumbo.analyze.php", "analyze_php"),
    AnalyzerSpec("c", "hypergumbo.analyze.c", "analyze_c", capture_symbols_as="c"),
    AnalyzerSpec("java", "hypergumbo.analyze.java", "analyze_java", capture_symbols_as="java"),
    AnalyzerSpec("elixir", "hypergumbo.analyze.elixir", "analyze_elixir"),
    AnalyzerSpec("rust", "hypergumbo.analyze.rust", "analyze_rust"),
    AnalyzerSpec("go", "hypergumbo.analyze.go", "analyze_go"),
    AnalyzerSpec("ruby", "hypergumbo.analyze.ruby", "analyze_ruby"),
    AnalyzerSpec("kotlin", "hypergumbo.analyze.kotlin", "analyze_kotlin"),
    AnalyzerSpec("swift", "hypergumbo.analyze.swift", "analyze_swift"),
    AnalyzerSpec("scala", "hypergumbo.analyze.scala", "analyze_scala"),
    AnalyzerSpec("lua", "hypergumbo.analyze.lua", "analyze_lua"),
    AnalyzerSpec("dart", "hypergumbo.analyze.dart", "analyze_dart"),
    AnalyzerSpec("clojure", "hypergumbo.analyze.clojure", "analyze_clojure"),
    AnalyzerSpec("elm", "hypergumbo.analyze.elm", "analyze_elm"),
    AnalyzerSpec("erlang", "hypergumbo.analyze.erlang", "analyze_erlang"),
    AnalyzerSpec("haskell", "hypergumbo.analyze.haskell", "analyze_haskell"),

    # Specialized/niche languages
    AnalyzerSpec("agda", "hypergumbo.analyze.agda", "analyze_agda"),
    AnalyzerSpec("lean", "hypergumbo.analyze.lean", "analyze_lean"),
    AnalyzerSpec("wolfram", "hypergumbo.analyze.wolfram", "analyze_wolfram"),
    AnalyzerSpec("ocaml", "hypergumbo.analyze.ocaml", "analyze_ocaml"),
    AnalyzerSpec("solidity", "hypergumbo.analyze.solidity", "analyze_solidity"),
    AnalyzerSpec("csharp", "hypergumbo.analyze.csharp", "analyze_csharp"),
    AnalyzerSpec("cpp", "hypergumbo.analyze.cpp", "analyze_cpp"),
    AnalyzerSpec("zig", "hypergumbo.analyze.zig", "analyze_zig"),
    AnalyzerSpec("groovy", "hypergumbo.analyze.groovy", "analyze_groovy"),
    AnalyzerSpec("julia", "hypergumbo.analyze.julia", "analyze_julia"),
    AnalyzerSpec("bash", "hypergumbo.analyze.bash", "analyze_bash"),
    AnalyzerSpec("objc", "hypergumbo.analyze.objc", "analyze_objc"),
    AnalyzerSpec("fsharp", "hypergumbo.analyze.fsharp", "analyze_fsharp"),
    AnalyzerSpec("perl", "hypergumbo.analyze.perl", "analyze_perl"),
    AnalyzerSpec("cobol", "hypergumbo.analyze.cobol", "analyze_cobol"),
    AnalyzerSpec("latex", "hypergumbo.analyze.latex", "analyze_latex"),
    AnalyzerSpec("ada", "hypergumbo.analyze.ada", "analyze_ada"),
    AnalyzerSpec("d", "hypergumbo.analyze.d_lang", "analyze_d"),
    AnalyzerSpec("nim", "hypergumbo.analyze.nim", "analyze_nim"),

    # Infrastructure and config
    AnalyzerSpec("hcl", "hypergumbo.analyze.hcl", "analyze_hcl"),
    AnalyzerSpec("ansible", "hypergumbo.analyze.yaml_ansible", "analyze_ansible"),
    AnalyzerSpec("sql", "hypergumbo.analyze.sql", "analyze_sql_files"),
    AnalyzerSpec("dockerfile", "hypergumbo.analyze.dockerfile", "analyze_dockerfiles"),
    AnalyzerSpec("cmake", "hypergumbo.analyze.cmake", "analyze_cmake_files"),
    AnalyzerSpec("make", "hypergumbo.analyze.make", "analyze_make_files"),
    AnalyzerSpec("nix", "hypergumbo.analyze.nix", "analyze_nix_files"),
    AnalyzerSpec("toml", "hypergumbo.analyze.toml_config", "analyze_toml_files"),
    AnalyzerSpec("xml", "hypergumbo.analyze.xml_config", "analyze_xml_files"),
    AnalyzerSpec("json", "hypergumbo.analyze.json_config", "analyze_json_files"),
    AnalyzerSpec("css", "hypergumbo.analyze.css", "analyze_css_files"),

    # Hardware description
    AnalyzerSpec("cuda", "hypergumbo.analyze.cuda", "analyze_cuda_files"),
    AnalyzerSpec("verilog", "hypergumbo.analyze.verilog", "analyze_verilog_files"),
    AnalyzerSpec("vhdl", "hypergumbo.analyze.vhdl", "analyze_vhdl_files"),
    AnalyzerSpec("glsl", "hypergumbo.analyze.glsl", "analyze_glsl_files"),
    AnalyzerSpec("wgsl", "hypergumbo.analyze.wgsl", "analyze_wgsl_files"),
    AnalyzerSpec("hlsl", "hypergumbo.analyze.hlsl", "analyze_hlsl"),

    # Interface definitions
    AnalyzerSpec("graphql", "hypergumbo.analyze.graphql", "analyze_graphql_files"),
    AnalyzerSpec("proto", "hypergumbo.analyze.proto", "analyze_proto"),
    AnalyzerSpec("thrift", "hypergumbo.analyze.thrift", "analyze_thrift"),
    AnalyzerSpec("capnp", "hypergumbo.analyze.capnp", "analyze_capnp"),
    AnalyzerSpec("r", "hypergumbo.analyze.r_lang", "analyze_r_files"),
    AnalyzerSpec("fortran", "hypergumbo.analyze.fortran", "analyze_fortran_files"),

    # Shell/scripting
    AnalyzerSpec("powershell", "hypergumbo.analyze.powershell", "analyze_powershell"),
    AnalyzerSpec("gdscript", "hypergumbo.analyze.gdscript", "analyze_gdscript"),
    AnalyzerSpec("starlark", "hypergumbo.analyze.starlark", "analyze_starlark"),
    AnalyzerSpec("fish", "hypergumbo.analyze.fish", "analyze_fish"),
]


def collect_analyzer_result(
    result: Any,
    analysis_runs: list[dict],
    all_symbols: list[Symbol],
    all_edges: list[Edge],
    limits: Limits,
) -> None:
    """Collect results from an analyzer into the aggregated lists.

    This replaces 50+ repetitive code blocks in run_behavior_map().
    Each block had the same pattern; this function captures that pattern once.

    Args:
        result: The analyzer result (any XxxAnalysisResult type)
        analysis_runs: List to append run metadata to
        all_symbols: List to append symbols to
        all_edges: List to append edges to
        limits: Limits object to track skipped passes
    """
    # Handle results without run (shouldn't happen but be defensive)
    if result.run is None:  # pragma: no cover
        all_symbols.extend(result.symbols)
        all_edges.extend(result.edges)
        return

    # Check if analyzer was skipped (optional deps missing)
    # Some analyzers (Python, HTML) don't have skipped attribute
    is_skipped = getattr(result, "skipped", False)
    skip_reason = getattr(result, "skip_reason", "")

    if is_skipped:
        limits.skipped_passes.append({
            "pass": result.run.pass_id,
            "reason": skip_reason,
        })
    else:
        analysis_runs.append(result.run.to_dict())
        all_symbols.extend(result.symbols)
        all_edges.extend(result.edges)


def run_all_analyzers(
    repo_root: Path,
    max_files: int | None = None,
) -> tuple[
    list[dict],  # analysis_runs
    list[Symbol],  # all_symbols
    list[Edge],  # all_edges
    Limits,  # limits
    dict[str, list[Symbol]],  # captured_symbols (for linkers)
]:
    """Run all registered analyzers and collect their results.

    This replaces ~800 lines of repetitive analyzer invocation code
    in run_behavior_map() with a clean loop.

    Args:
        repo_root: Repository root path
        max_files: Optional max files per analyzer

    Returns:
        Tuple of (analysis_runs, all_symbols, all_edges, limits, captured_symbols)
        where captured_symbols is a dict mapping capture names to symbol lists
        (e.g., {"c": [...], "java": [...]} for the JNI linker).
    """
    analysis_runs: list[dict] = []
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []
    limits = Limits()
    limits.max_files_per_analyzer = max_files
    captured_symbols: dict[str, list[Symbol]] = {}

    for spec in ANALYZERS:
        # Build kwargs based on analyzer capabilities
        kwargs: dict[str, Any] = {}
        if spec.supports_max_files and max_files is not None:  # pragma: no cover
            kwargs["max_files"] = max_files

        # Run the analyzer (get_func enables test patching via lazy import)
        func = spec.get_func()
        result = func(repo_root, **kwargs)

        # Collect results
        collect_analyzer_result(result, analysis_runs, all_symbols, all_edges, limits)

        # Capture symbols for linkers if needed (e.g., JNI needs c_symbols and java_symbols)
        if spec.capture_symbols_as and not result.skipped:
            captured_symbols[spec.capture_symbols_as] = list(result.symbols)

    return analysis_runs, all_symbols, all_edges, limits, captured_symbols
