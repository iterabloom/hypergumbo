# Supported Languages

Hypergumbo includes 68 language analyzers. Each analyzer detects symbols (functions, classes, methods, interfaces) and edges (calls, imports, instantiates, extends, implements).

## Language Table

| Category | Languages |
|----------|-----------|
| **Application** | Python, JavaScript, TypeScript, Java, C#, F#, Go, Rust, Ruby, PHP, Perl, Swift, Kotlin, Scala, Groovy, Clojure, Common Lisp, Erlang, Elixir, Lua, Haskell, OCaml, Julia, R, Dart |
| **Systems** | C, C++, Zig, Objective-C, CUDA, Fortran |
| **Smart Contracts** | Solidity |
| **Hardware** | Verilog, VHDL, GLSL, WGSL |
| **Infrastructure** | Terraform/HCL, Dockerfile, CMake, Make, Nix, Bash, YAML/Ansible |
| **Data/Schema** | SQL, GraphQL, JSON, TOML, XML, CSS |
| **Frontend** | Elm, Vue, Svelte, HTML |
| **Proof/Formal** | Agda, Lean*, Wolfram* |
| **Legacy/Academic** | COBOL, LaTeX |

\* Lean and Wolfram require building tree-sitter grammars from source (not yet on PyPI). Run `hypergumbo build-grammars` to enable these analyzers.

## How Analyzers Work

Each analyzer follows the same pattern:

1. **Parse**: Use tree-sitter (or AST for Python) to parse source files
2. **Extract symbols**: Identify functions, classes, methods, interfaces
3. **Extract edges**: Find calls, imports, inheritance, implementations
4. **Return IR**: Output `Symbol` and `Edge` objects with location spans

Analyzers are registered via decorator and selected automatically based on file extensions detected during profiling.

## Adding a New Language

See `src/hypergumbo/analyze/` for examples. The typical pattern:

```python
from hypergumbo.analyze.base import register_analyzer

@register_analyzer("mylang", extensions=[".ml"])
def analyze_mylang(path: Path) -> AnalysisResult:
    # Parse files, extract symbols and edges
    ...
```

All analyzers must have 100% test coverage.
