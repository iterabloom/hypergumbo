<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Supported Languages

Hypergumbo includes language analyzers for dozens of languages and file formats. Each analyzer detects symbols (functions, classes, methods, interfaces) and edges (calls, imports, instantiates, extends, implements).

## Language Table

| Category | Languages |
|----------|-----------|
| **Application** | Python, JavaScript, TypeScript, Java, C#, F#, Go, Rust, Ruby, PHP, Perl, Swift, Kotlin, Scala, Groovy, Clojure, Common Lisp, Erlang, Elixir, Lua, Haskell, OCaml, Julia, R, Dart |
| **Systems** | C, C++, Zig, Objective-C, D, Ada, Nim, Pascal, V, Odin, Fortran, CUDA, LLVM IR |
| **Functional** | PureScript, Racket, Scheme, Elm, Gleam, Janet, Fennel |
| **Frontend/Web** | Vue, Svelte, Astro, HTML, CSS/SCSS |
| **Smart Contracts** | Solidity, Circom*, Apex |
| **Hardware/GPU** | Verilog/SystemVerilog, VHDL, GLSL, HLSL, WGSL |
| **Infrastructure** | Terraform/HCL, Dockerfile, CMake, Make, Meson, Nix, Starlark, Bash, PowerShell, Fish, Puppet, Bitbake, YAML/Ansible |
| **Data/Schema** | SQL, SPARQL, GraphQL, Protocol Buffers, Thrift, Cap'n Proto, Smithy, Prisma, JSON, TOML, XML, INI, Properties, KDL, JSONNet |
| **Game Dev** | GDScript, Haxe, Luau |
| **Proof/Formal** | Agda, Lean*, Wolfram* |
| **Documentation** | Markdown, LaTeX, BibTeX, reStructuredText, AsciiDoc |
| **Testing** | Robot Framework |
| **Legacy** | COBOL, Tcl, Pony, Hack, Twig |

\* Lean, Wolfram, and Circom require building tree-sitter grammars from source (not yet on PyPI). Run `hypergumbo build-grammars` to enable these analyzers.

Analyzers are distributed across three packages: `hypergumbo-lang-mainstream`, `hypergumbo-lang-common`, and `hypergumbo-lang-extended1`. Installing the `hypergumbo` meta-package includes all three.

## How Analyzers Work

Each analyzer follows the same pattern:

1. **Parse**: Use tree-sitter (or AST for Python) to parse source files
2. **Extract symbols**: Identify functions, classes, methods, interfaces
3. **Extract edges**: Find calls, imports, inheritance, implementations
4. **Return IR**: Output `Symbol` and `Edge` objects with location spans

Analyzers are registered via decorator and selected automatically based on file extensions detected during profiling.

## Adding a New Language

See `packages/hypergumbo-lang-*/src/` for examples. The typical pattern:

```python
from hypergumbo_core.analyze.base import register_analyzer

@register_analyzer("mylang", extensions=[".ml"])
def analyze_mylang(path: Path) -> AnalysisResult:
    # Parse files, extract symbols and edges
    ...
```

All analyzers must have 100% test coverage.
