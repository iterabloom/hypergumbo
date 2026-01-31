# Migration Guide: hypergumbo 1.x to 2.0

This guide covers the changes needed when upgrading from hypergumbo 1.x to 2.0.

## Overview

Version 2.0 restructures hypergumbo from a single monolithic package into five modular packages:

| Package | Purpose |
|---------|---------|
| `hypergumbo-core` | Core infrastructure (CLI, IR, slice, sketch, linkers) |
| `hypergumbo-lang-mainstream` | Popular languages (Python, JS/TS, Java, Go, Rust, etc.) |
| `hypergumbo-lang-common` | Domain-specific languages (Haskell, Elixir, GraphQL, etc.) |
| `hypergumbo-lang-extended1` | Specialized languages (Zig, Agda, Solidity, etc.) |
| `hypergumbo` | Meta-package that installs all of the above |

## Installation

### For most users (no change)

```bash
pip install hypergumbo
```

This installs the meta-package which pulls in all components. The CLI works exactly as before.

### For minimal installations

If you only need specific language support:

```bash
# Core only (no language analyzers)
pip install hypergumbo-core

# Core + mainstream languages
pip install hypergumbo-core hypergumbo-lang-mainstream

# Core + specific language packs
pip install hypergumbo-core hypergumbo-lang-common
```

## Import Path Changes

If you import hypergumbo modules directly in your code, update the import paths:

### Core Infrastructure

```python
# Before (1.x)
from hypergumbo.ir import Symbol, Edge, Span
from hypergumbo.sketch import generate_sketch
from hypergumbo.slice import forward_slice, reverse_slice
from hypergumbo.schema import new_behavior_map
from hypergumbo.ranking import rank_symbols
from hypergumbo.supply_chain import classify_tier

# After (2.0)
from hypergumbo_core.ir import Symbol, Edge, Span
from hypergumbo_core.sketch import generate_sketch
from hypergumbo_core.slice import forward_slice, reverse_slice
from hypergumbo_core.schema import new_behavior_map
from hypergumbo_core.ranking import rank_symbols
from hypergumbo_core.supply_chain import classify_tier
```

### Analysis Framework

```python
# Before (1.x)
from hypergumbo.analyze.base import iter_tree, node_text
from hypergumbo.analyze.registry import get_analyzer

# After (2.0)
from hypergumbo_core.analyze.base import iter_tree, node_text
from hypergumbo_core.analyze.registry import get_analyzer
```

### Linkers

```python
# Before (1.x)
from hypergumbo.linkers.registry import LINKER_REGISTRY
from hypergumbo.linkers.grpc import link_grpc

# After (2.0)
from hypergumbo_core.linkers.registry import LINKER_REGISTRY
from hypergumbo_core.linkers.grpc import link_grpc
```

### Language Analyzers

Language analyzers have moved to their respective packages:

```python
# Before (1.x)
from hypergumbo.analyze.py import analyze_python
from hypergumbo.analyze.java import analyze_java
from hypergumbo.analyze.zig import analyze_zig

# After (2.0) - Mainstream languages
from hypergumbo_lang_mainstream.py import analyze_python
from hypergumbo_lang_mainstream.java import analyze_java

# After (2.0) - Extended languages
from hypergumbo_lang_extended1.zig import analyze_zig
```

### Language Package Reference

| Package | Languages |
|---------|-----------|
| `hypergumbo_lang_mainstream` | Python, JavaScript/TypeScript, Java, C, C++, C#, Go, Rust, Ruby, PHP, Swift, Kotlin, Scala, Bash, SQL, HTML, CSS, Dockerfile, Lua, Perl, JSON, YAML, XML, TOML, Markdown, Make, CMake, Groovy, PowerShell, Objective-C, INI, Properties, Requirements, Gitignore |
| `hypergumbo_lang_common` | Haskell, OCaml, Elixir, Erlang, Clojure, F#, Julia, R, MATLAB, Fortran, Dart, Vue, Svelte, Astro, SCSS, GraphQL, Proto, Thrift, Nix, HCL, LaTeX, RST, Robot, Puppet, Starlark, Meson, CUDA, GLSL, HLSL, WGSL, Elm, PureScript, Racket, Scheme, Common Lisp |
| `hypergumbo_lang_extended1` | Zig, Odin, Nim, Agda, Lean, COBOL, Apex, Solidity, Verilog, VHDL, Ada, D, Pascal, Pony, Janet, Fennel, Gleam, Hack, Haxe, GDScript, Luau, V, Wolfram, LLVM IR, Cap'n Proto, Smithy, Jsonnet, KDL, Prisma, Twig, SPARQL, Tcl, Fish, BibTeX, BitBake |

## CLI Usage

The CLI is unchanged. All commands work as before:

```bash
hypergumbo run .                    # Generate behavior map
hypergumbo sketch .                 # Generate token-budgeted overview
hypergumbo slice --symbol foo       # Forward slice from symbol
hypergumbo slice --reverse --symbol foo  # Reverse slice to symbol
```

## API Compatibility

The output format (behavior map JSON) is unchanged. The schema version remains at 0.2.x.

## Programmatic Usage

If you use hypergumbo programmatically, the main entry points are:

```python
from hypergumbo_core.cli import main
from hypergumbo_core.ir import Symbol, Edge, Span, AnalysisRun
from hypergumbo_core.schema import new_behavior_map, SCHEMA_VERSION
from hypergumbo_core.sketch import generate_sketch
from hypergumbo_core.slice import forward_slice, reverse_slice
from hypergumbo_core.ranking import rank_symbols, rank_slice_nodes
from hypergumbo_core.supply_chain import classify_tier
from hypergumbo_core.compact import compact_behavior_map
```

## Breaking Changes Summary

1. **Import paths changed**: `hypergumbo.*` → `hypergumbo_core.*` for core modules
2. **Analyzer imports changed**: Language analyzers moved to `hypergumbo_lang_*` packages
3. **Package structure**: Five packages instead of one (transparent if using meta-package)

## Automated Migration

For large codebases, use sed to update imports:

```bash
# Update core imports
find . -name "*.py" -exec sed -i 's/from hypergumbo\./from hypergumbo_core./g' {} \;
find . -name "*.py" -exec sed -i 's/import hypergumbo\./import hypergumbo_core./g' {} \;

# Note: Language analyzer imports need manual review to determine correct package
```

## Getting Help

- Issues: https://codeberg.org/iterabloom/hypergumbo/issues
- Documentation: https://codeberg.org/iterabloom/hypergumbo/src/branch/main/docs
