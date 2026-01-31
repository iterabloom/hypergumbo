"""Hypergumbo common language analyzers.

This package provides analyzers for languages that are popular in specific
domains - functional programming, data science, web frameworks, DevOps, etc.

These languages are commonly encountered but more domain-specific than
the mainstream package.
"""
from hypergumbo_core.analyze.all_analyzers import AnalyzerSpec

__version__ = "2.0.0"

# Analyzer specifications for common languages
# These are registered via entry_points in pyproject.toml
ANALYZER_SPECS = [
    # Functional languages
    AnalyzerSpec("haskell", "hypergumbo_lang_common.haskell", "analyze_haskell"),
    AnalyzerSpec("ocaml", "hypergumbo_lang_common.ocaml", "analyze_ocaml"),
    AnalyzerSpec("elixir", "hypergumbo_lang_common.elixir", "analyze_elixir"),
    AnalyzerSpec("erlang", "hypergumbo_lang_common.erlang", "analyze_erlang"),
    AnalyzerSpec("clojure", "hypergumbo_lang_common.clojure", "analyze_clojure"),
    AnalyzerSpec("fsharp", "hypergumbo_lang_common.fsharp", "analyze_fsharp"),
    AnalyzerSpec("elm", "hypergumbo_lang_common.elm", "analyze_elm"),
    AnalyzerSpec("purescript", "hypergumbo_lang_common.purescript", "analyze_purescript"),
    AnalyzerSpec("racket", "hypergumbo_lang_common.racket", "analyze_racket"),
    AnalyzerSpec("scheme", "hypergumbo_lang_common.scheme", "analyze_scheme"),
    AnalyzerSpec("commonlisp", "hypergumbo_lang_common.commonlisp", "analyze_commonlisp"),

    # Data science and scientific computing
    AnalyzerSpec("julia", "hypergumbo_lang_common.julia", "analyze_julia"),
    AnalyzerSpec("r", "hypergumbo_lang_common.r_lang", "analyze_r_files"),
    AnalyzerSpec("matlab", "hypergumbo_lang_common.matlab", "analyze_matlab"),
    AnalyzerSpec("fortran", "hypergumbo_lang_common.fortran", "analyze_fortran_files"),

    # Web frontend frameworks
    AnalyzerSpec("dart", "hypergumbo_lang_common.dart", "analyze_dart"),
    AnalyzerSpec("vue", "hypergumbo_lang_common.vue", "analyze_vue"),
    AnalyzerSpec("svelte", "hypergumbo_lang_common.svelte", "analyze_svelte"),
    AnalyzerSpec("astro", "hypergumbo_lang_common.astro", "analyze_astro"),
    AnalyzerSpec("scss", "hypergumbo_lang_common.scss", "analyze_scss"),

    # Interface definitions and schemas
    AnalyzerSpec("graphql", "hypergumbo_lang_common.graphql", "analyze_graphql_files"),
    AnalyzerSpec("proto", "hypergumbo_lang_common.proto", "analyze_proto"),
    AnalyzerSpec("thrift", "hypergumbo_lang_common.thrift", "analyze_thrift"),

    # Infrastructure and config
    AnalyzerSpec("nix", "hypergumbo_lang_common.nix", "analyze_nix_files"),
    AnalyzerSpec("hcl", "hypergumbo_lang_common.hcl", "analyze_hcl"),
    AnalyzerSpec("puppet", "hypergumbo_lang_common.puppet", "analyze_puppet"),
    AnalyzerSpec("starlark", "hypergumbo_lang_common.starlark", "analyze_starlark"),
    AnalyzerSpec("meson", "hypergumbo_lang_common.meson", "analyze_meson"),

    # Documentation
    AnalyzerSpec("latex", "hypergumbo_lang_common.latex", "analyze_latex"),
    AnalyzerSpec("rst", "hypergumbo_lang_common.rst", "analyze_rst"),

    # Testing
    AnalyzerSpec("robot", "hypergumbo_lang_common.robot", "analyze_robot"),

    # GPU and graphics
    AnalyzerSpec("cuda", "hypergumbo_lang_common.cuda", "analyze_cuda_files"),
    AnalyzerSpec("glsl", "hypergumbo_lang_common.glsl", "analyze_glsl_files"),
    AnalyzerSpec("hlsl", "hypergumbo_lang_common.hlsl", "analyze_hlsl"),
    AnalyzerSpec("wgsl", "hypergumbo_lang_common.wgsl", "analyze_wgsl_files"),
]

__all__ = ["ANALYZER_SPECS", "__version__"]
