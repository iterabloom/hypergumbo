# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hypergumbo common language analyzers.

This package provides analyzers for languages that are popular in specific
domains - functional programming, data science, web frameworks, DevOps, etc.

These languages are commonly encountered but more domain-specific than
the mainstream package.
"""

__version__ = "2.5.0"

# Module paths for analyzer discovery via entry-points (ADR-0012 Step 1).
# Importing each module triggers the @register_analyzer decorator within it.
ANALYZER_MODULES = [
    # Functional languages
    "hypergumbo_lang_common.haskell",
    "hypergumbo_lang_common.ocaml",
    "hypergumbo_lang_common.elixir",
    "hypergumbo_lang_common.erlang",
    "hypergumbo_lang_common.clojure",
    "hypergumbo_lang_common.fsharp",
    "hypergumbo_lang_common.elm",
    "hypergumbo_lang_common.purescript",
    "hypergumbo_lang_common.racket",
    "hypergumbo_lang_common.scheme",
    "hypergumbo_lang_common.commonlisp",

    # Data science and scientific computing
    "hypergumbo_lang_common.julia",
    "hypergumbo_lang_common.r_lang",
    "hypergumbo_lang_common.matlab",
    "hypergumbo_lang_common.fortran",

    # Web frontend frameworks
    "hypergumbo_lang_common.dart",
    "hypergumbo_lang_common.vue",
    "hypergumbo_lang_common.svelte",
    "hypergumbo_lang_common.astro",
    "hypergumbo_lang_common.scss",

    # Interface definitions and schemas
    "hypergumbo_lang_common.graphql",
    "hypergumbo_lang_common.proto",
    "hypergumbo_lang_common.thrift",

    # Templating
    "hypergumbo_lang_common.handlebars",

    # UI frameworks
    "hypergumbo_lang_common.qml",

    # Build systems
    "hypergumbo_lang_common.just",

    # Infrastructure and config
    "hypergumbo_lang_common.nix",
    "hypergumbo_lang_common.hcl",
    "hypergumbo_lang_common.puppet",
    "hypergumbo_lang_common.starlark",
    "hypergumbo_lang_common.meson",

    # Documentation
    "hypergumbo_lang_common.latex",
    "hypergumbo_lang_common.rst",

    # Testing
    "hypergumbo_lang_common.robot",

    # GPU and graphics
    "hypergumbo_lang_common.cuda",
    "hypergumbo_lang_common.glsl",
    "hypergumbo_lang_common.hlsl",
    "hypergumbo_lang_common.wgsl",
]

__all__ = ["ANALYZER_MODULES", "__version__"]
