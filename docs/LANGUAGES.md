<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Supported Languages

Hypergumbo includes language analyzers for dozens of languages and file formats. Each analyzer detects symbols (functions, classes, methods, interfaces) and edges (calls, imports, instantiates, extends, implements).

## Language Table

| Category | Languages |
|----------|-----------|
| **Application** | Python, JavaScript, TypeScript, Java, C#, F#, Go, Rust, Ruby, PHP, Perl, Swift, Kotlin, Scala, Groovy, Clojure, Common Lisp, Erlang, Elixir, Lua, Haskell, OCaml, Julia, R, Dart |
| **Systems** | C, C++, Zig, Objective-C, D, Ada, Nim, Pascal, V, Odin, Fortran, CUDA, LLVM IR |
| **Functional** | PureScript, Racket, Scheme, Elm, Gleam, Janet, Fennel |
| **Frontend/Web** | Vue, Svelte, Astro, HTML, CSS/SCSS, QML |
| **Templating** | Blade, Handlebars |
| **Smart Contracts** | Solidity, Circom*, Apex |
| **Hardware/GPU** | Verilog/SystemVerilog, VHDL, GLSL, HLSL, WGSL |
| **Infrastructure** | Terraform/HCL, Dockerfile, CMake, Make, Just, Meson, Nix, Starlark, Bash, PowerShell, Fish, Puppet, Bitbake, YAML/Ansible |
| **Data/Schema** | SQL, SPARQL, GraphQL, Protocol Buffers, Thrift, Cap'n Proto, Smithy, Prisma, JSON, TOML, XML, INI, Properties, KDL, JSONNet |
| **Game Dev** | GDScript, Haxe, Luau |
| **Proof/Formal** | Agda, Lean*, TLA+, Wolfram* |
| **Notebooks** | Jupyter Notebook (.ipynb) |
| **Diagram/Plotting** | Mermaid, Gnuplot |
| **Documentation** | Markdown, LaTeX, BibTeX, reStructuredText, AsciiDoc |
| **Testing** | Robot Framework |
| **Legacy** | COBOL, Tcl, Pony, Hack, Twig |

\* Lean, Wolfram, and Circom require building tree-sitter grammars from source (not yet on PyPI). Run `hypergumbo build-grammars` to enable these analyzers.

Analyzers are distributed across three packages: `hypergumbo-lang-mainstream`, `hypergumbo-lang-common`, and `hypergumbo-lang-extended1`. Installing the `hypergumbo` meta-package includes all three.

## Recent capability improvements

These improvements live within already-supported languages — no new languages are added; existing analyzers do more.

- **Kotlin** — Receiver-type inference strips the nullable `?` suffix so methods returning `User?` propagate `User` into `var_types`. Chained-receiver resolution on nullable getters no longer drops methods.
- **C#** — Receiver-type inference unwraps async wrapper types `Task<T>` / `ValueTask<T>` / `IAsyncEnumerable<T>` so `var x = await SomeAsync()` binds to the awaited type. Bare wrapper-only returns stay `None`. Both extensions follow ADR-0006's Return-Type Registry program (Java + Go shipped earlier).
- **JavaScript / TypeScript** — `access_mode` annotation coverage extended to `return` / `throw` / `yield` / `await` / `update_expression` contexts. `--dataflow` slices on TypeScript repos are useful again. Bare-Node and Apollo standalone HTTP routes detected (see [FRAMEWORKS.md](FRAMEWORKS.md)).
- **Rust** — PyO3 `#[pymethods] impl Foo { fn bar() {} }` propagates the annotation to every method declared inside; path-qualified spellings like `#[pyo3::pyfunction]` are recognized. Python → Rust FFI chain tracing now finds the canonical ~100-method PyO3 surface. Aliased `use` imports get proper canonical `Edge.dst_ref` instead of the historical 6-segment dst fabrication.
- **C / C++ (Node-API)** — Template forms `Napi::Function::New<F>(env)`, `InstanceMethod<&C::M>("name")`, and `InstanceAccessor` property bindings match alongside the function-argument forms. Sharp, canvas, and similar modern node-addon-api projects benefit.
- **Python** — `verify-claims` resolves more method-call receivers through a post-DDG IR refinement pass. Eight zone-tagged fs-write wrappers in `hypergumbo_core.safety_zones` provide a discipline pattern downstream Python projects can adopt.
- **Elixir** — Phoenix test files at `test/<context>/<thing>_test.exs` classify as `supply_chain.tier=1` with `is_test=True` (previously conflated with vendored code at tier=2).
- **Solidity** — `contract` is now a canonical `Symbol.kind` (previously emitted but unregistered).
- **CUDA / Android XML** — Producer-side folds onto canonical kind + `meta` discriminator: CUDA emits `kind="function"` + `meta["cuda_execution_space"]`; Android XML emits `kind="component"` + `meta["component_type"]`. See [MIGRATION-6.0-CONCEPT-AXES.md](MIGRATION-6.0-CONCEPT-AXES.md) for the mapping detail.
- **Ansible** — `include_tasks` / `import_tasks` with Jinja-templated paths fan out to real target files instead of leaving a single unresolved edge.

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
