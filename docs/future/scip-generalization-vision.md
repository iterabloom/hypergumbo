<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# SCIP Generalization Vision (Future Work)

> **Note**: This document captures the design sketch for WI-nokoh: can the same
> SCIP-ingest shim be reused for languages other than Rust? Currently
> hypergumbo runs a SCIP-backed analyzer only for Rust (via the rust-analyzer
> backend, opt-in). This sketch surveys the SCIP emitter ecosystem, catalogs
> per-language translation quirks, and recommends whether to extend to other
> languages — and if so, in what architectural shape.

## Background

[SCIP](https://github.com/sourcegraph/scip) — Source Code Intelligence Protocol
— is Sourcegraph's protobuf schema for cross-language semantic indexes. It's
already the format hypergumbo's rust-analyzer backend consumes via
`packages/hypergumbo-lang-rust-analyzer/` (WI-duzul scaffold + WI-mafut
SCIP-to-IR translation shim).

The shim itself
(`hypergumbo_core.scip.{index,edges,calls}`) was deliberately built
language-agnostic — Rust-specific quirks (trait-suffix descriptor parsing, the
proc-macro carve-out) live in the rust-analyzer wrapper package, not in the
shim. That means the shim's surface is already a reusable substrate; the
question is whether the SCIP emitters for other languages produce output
shaped the way the shim expects.

WI-nokoh asks: which other languages have SCIP emitters, what would per-language
translation look like, and is a unified "SCIP-backed analyzer" tier worth the
engineering investment?

## (a) Compatible Emitter Survey

Maintained by the Sourcegraph team or community-contributed:

| Emitter | Language | Status (2026-05) | Notes |
|---|---|---|---|
| `rust-analyzer scip` | Rust | Production (default invocation; integrated in hypergumbo via WI-duzul) | No proc-macro expansion at default invocation; see WI-totub finding 2026-05-24 |
| `scip-typescript` | TypeScript / JavaScript | Production | npm package `@sourcegraph/scip-typescript`; CLI invocation `scip-typescript index` |
| `scip-java` | Java / Kotlin / Scala | Production | Uses semanticdb under the hood; works on Maven / Gradle / sbt / Bazel; somewhat heavy |
| `scip-python` | Python | Production | Uses Pyright as analyzer; respects `pyrightconfig.json` |
| `scip-clang` | C / C++ | Production | Requires `compile_commands.json` (CMake / Bazel) |
| `scip-ruby` | Ruby | Beta | Uses Sorbet for type inference; can be slow on un-typed Ruby |
| `scip-go` | Go | Production | Uses Go's stdlib `go/packages`; needs `go.mod` |
| `scip-dotnet` | C# / F# / VB.NET | Preview | Limited adoption; Sourcegraph's own use only as of last check |

All emitters write the same SCIP protobuf format. The shim's `scip.index_to_symbols`
and `scip.edges_from_scip` produce IR-shaped output without caring which
emitter produced the bytes. The fall-through point is the **descriptor
syntax** — SCIP symbol descriptors carry language-specific symbol-shape
information that the shim does not interpret today (it just stores the raw
descriptor as `stable_id` parts).

**Implication**: the shim's "consume SCIP protobuf → emit IR Symbols + Edges"
path is language-agnostic by construction. What is NOT language-agnostic is:

1. **How to invoke the emitter** (different CLI shapes, different config files).
2. **How to map SCIP symbol descriptors to hypergumbo `stable_id` parity** with
   the existing tree-sitter analyzer for the same language (so dedup at
   import time still works).
3. **Per-language quirks** in the descriptor syntax (Rust's trait-suffix `#`
   notation, Java's `.` between package segments vs. `/`, Python's
   `:` between module / class / function, etc.).
4. **Failure-mode handling** (each emitter has its own crash signature).

## (b) Per-Language Translation Quirks

What changes per language in the wrapper-package layer (NOT in the shim):

### Rust (already done via rust-analyzer)

- Trait-suffix descriptor: `wi-totub-clap-test 0.1.0 Args#parse().` — the `#`
  before a method indicates it's a method on the preceding type.
- Proc-macro carve-out: default `rust-analyzer scip` does NOT surface
  proc-macro-expanded symbols (confirmed by WI-totub 2026-05-24). Linkers
  that depend on macro-generated wrappers (`#[tauri::command]`,
  `#[wasm_bindgen]`, N-API) cannot improve over tree-sitter at default
  invocation. A `procMacro.enable=true` investigation could close this gap
  but is out of WI-nokoh's scope.
- stable_id parity: handled by
  `hypergumbo_lang_rust_analyzer.translate.reassign_rust_stable_ids`
  (WI-bajuz) — descriptors are remapped to match `rust.py`'s tree-sitter
  stable_id formula.

### TypeScript / JavaScript (`scip-typescript`)

- Descriptor: `lodash@4.17.21 src/util.ts/getValue().` — uses `/` between
  scope levels, `().` for functions, `.` (without parens) for properties.
- Ambient declarations: `.d.ts` files produce Symbol descriptors but no
  concrete implementations. Tree-sitter `js_ts.py` currently includes `.d.ts`
  but doesn't distinguish ambient-only symbols; the wrapper would need to
  flag them.
- Module resolution: SCIP knows the actual resolved module path (after
  `tsconfig.json` baseUrl / paths resolution). Tree-sitter has only the
  literal import string. Useful for cross-file edge resolution.
- Decorators: TypeScript decorators are NOT macro-expanded (they execute at
  runtime); SCIP captures the decorator call site as a reference, not a
  synthesized impl. Same gotcha as Rust proc-macros for the same reasons.
- stable_id parity work: re-map SCIP descriptors to `js_ts.py`'s
  `js:src/file.ts:L-R:name:kind` stable_id formula. Probably ~50-100 lines.

### Java / Kotlin / Scala (`scip-java`)

- Descriptor: `maven/com.example/foo/Bar#method().` — Maven coords + package
  path with `/` + class with `#`.
- Type erasure: Generic types are erased in SCIP descriptors (matches
  bytecode behavior). Tree-sitter sees the source-level generics; SCIP sees
  the erased form. Either is workable but they differ.
- Inner / nested / local classes: SCIP uses `Class$Inner#` notation; the
  wrapper would need to translate to hypergumbo's existing inner-class naming.
- Annotation processors: code generated by `@Processor` (e.g., Lombok,
  Dagger) is not in source files but DOES appear in the semanticdb. SCIP
  surfaces these symbols — unlike Rust proc-macros at default. This means
  framework-dispatch linkers (e.g., Dagger DI) could see the generated
  module / component classes that tree-sitter cannot.
- stable_id parity work: re-map. ~50-100 lines.

### Python (`scip-python`)

- Descriptor: `pypi/django/4.2.0 django/views.py/View#dispatch().` — module
  path with `/`, class with `#`.
- Dynamic typing: SCIP symbol kinds are less informative than for static
  languages. Symbols flagged as `parameter` may not have a concrete type;
  decorator-wrapped functions may show up with the wrapper's signature.
- Decorators: Python decorators don't expand at SCIP time (they're runtime
  function wrappers). Same gotcha as TS / Rust.
- Import resolution: scip-python uses Pyright's import resolver, which is
  significantly more accurate than `py_deps.py`'s tree-sitter-only resolution.
  Could improve `imports`-edge fidelity meaningfully.
- stable_id parity work: this is the trickiest one — `py.py`'s stable_id
  formula went through v4 evolution (INV-zudob, file-folding for top-level
  symbols). Pyright's symbol naming may not map 1:1; some normalization
  needed.

### C / C++ (`scip-clang`)

- Descriptor: header-aware. SCIP-clang produces one symbol per *declaration*
  and resolves to a canonical *definition*; references work correctly across
  headers. Tree-sitter `c.py` / `cpp.py` see each header file independently.
- Macros: C preprocessor macros expand at SCIP time. Macro-defined functions
  appear as synthesized symbols at the expansion sites — unlike Rust
  proc-macros (which don't expand) or TS / Python decorators (which don't
  expand either). C is closer to Java's annotation processor model here.
- Templates: C++ template instantiations appear in SCIP as separate symbols
  per instantiation. Tree-sitter sees only the template definition. This
  could substantially improve C++ edge counts.
- Build dependency: `compile_commands.json` is mandatory. Repos without it
  (header-only libs, hand-written Makefiles) cannot be indexed.
- stable_id parity work: substantial — `c.py` / `cpp.py` have separate
  stable_id schemes; SCIP-clang produces one set.

### Go (`scip-go`)

- Descriptor: import-path-based: `github.com/foo/bar baz/qux.Method`. Looks
  the most like hypergumbo's existing `go.py` stable_id scheme.
- Build tags: SCIP-go honors `go.mod` and build tags; tree-sitter `go.py`
  doesn't pay attention to tags.
- stable_id parity work: lightest — already similar shape. ~30-50 lines.

### Ruby (`scip-ruby`)

- Descriptor: Sorbet-based, looks like Rust's trait-suffix `#` notation.
- Dynamic typing: even with Sorbet's `# typed: strict`, large parts of
  un-typed Ruby produce `untyped` symbol kinds. Less SCIP-fidelity advantage
  over tree-sitter `ruby.py` than static languages give.
- Pace: scip-ruby is beta; less battle-tested than the others.

## (c) Single Shim vs. Per-Language Shims

The shim is already split:
- **Shared shim** (`hypergumbo_core.scip.{index,edges,calls}`) — consumes
  SCIP protobuf, emits hypergumbo IR Symbols + Edges, language-agnostic
- **Per-language wrapper** (`hypergumbo_lang_<lang>_<emitter>/`) — handles
  emitter invocation, stable_id parity, language-specific failure-mode
  handling, opt-in gate

This is the right architecture. Adding a new language doesn't change the
shim; it adds a new wrapper package mirroring the rust-analyzer one.

**Recommendation: keep the existing shape**. Reusing the shim is "free" at
the architecture layer; the work per language is in the wrapper. Estimated
per-language wrapper cost:

| Language | Wrapper effort | Engagement / OOM risk |
|---|---|---|
| Go | ~Low (1-2 days) | Low — fast emitter, lightweight |
| TypeScript | Medium (~3-5 days) | Low — fast emitter |
| Java | Medium (~3-5 days) | Medium — heavy, needs build setup |
| Python | High (~5-7 days) | Low — depends on Pyright availability |
| C / C++ | High (~5-7 days) | Medium — needs `compile_commands.json` |
| Ruby | Medium (~3-5 days) | Medium — Sorbet dependency |
| C# / F# | Defer | scip-dotnet preview-only |

(Effort estimates exclude bakeoff validation cohorts, which add 1-2 days each.)

## When Does This Matter?

A SCIP-backed analyzer is **strictly better than tree-sitter** for:
- Symbol resolution across file boundaries (the resolved import path is in SCIP, not just the source string)
- Macro / annotation-processor expansion (Java's Lombok / Dagger; C's macros;
  C++'s template instantiations)
- Type-aware call resolution (especially Java / Kotlin / TS with generics
  and Python with Pyright)

It is **the same as or worse than tree-sitter** for:
- Languages with weak type info (Python without Pyright config, Ruby
  without Sorbet `# typed: strict`)
- Macro-heavy code where the macros are not expanded by the emitter (Rust
  proc-macros at default invocation, TS decorators)
- Repos without the right build metadata (no `compile_commands.json` for
  C / C++, no `go.mod` for Go, no `tsconfig.json` for TS)

Each new language gets the "engagement check" infrastructure (per WI-todon)
out-of-the-box if it follows the rust-analyzer wrapper template — every new
wrapper that mirrors `analyzer.py:analyze_rust_with_scip` automatically gets
the artifact-level "produced no SCIP edges despite .X files present" warning
for free.

## Recommendation: Sequencing

If pursued, sequence by expected value vs. effort:

1. **Go** first — lowest effort, similar stable_id shape, immediate value via
   better cross-file resolution.
2. **TypeScript** second — high JS/TS surface in modern apps, big improvement
   to module-resolution edge fidelity.
3. **Java** third — substantial value via annotation-processor expansion
   (Dagger, Lombok); higher effort to set up.
4. **Python** fourth — high payoff via Pyright import resolution, but
   stable_id parity is non-trivial.
5. **C / C++** fifth — large potential value via macro / template
   expansion; build-metadata dependency is a real friction.
6. **Ruby** later — beta emitter, lower confidence.

Or, alternatively: **don't generalize at all**. The Rust pilot has been
substantial engineering; if BROAD bakeoff signals (WI-vovad cohort, run
post-WI-todon diagnostics) don't show the SCIP backend producing notably
better outputs than tree-sitter at the default invocation, the case for
extending to other languages weakens. Wait for empirical signal before
investing in per-language wrappers.

## Open Questions

These are NOT for WI-nokoh's sketch to answer — they're prompts for the
follow-up tracker items if generalization is pursued:

1. **Bakeoff first or wrapper first per language?** For Rust, the wrapper
   was built first (WI-duzul → WI-mafut → WI-vovad). Reversing order
   (cohort first, wrapper sized to the signal) might be cheaper if the
   payoff is unclear up front.

2. **Coexistence vs. replacement.** Does the SCIP-backed analyzer for
   language X run alongside the tree-sitter analyzer (as Rust does now) or
   replace it? Coexistence preserves the fallback; replacement avoids
   double-dipping but loses the safety net.

3. **Cross-language linker upgrades.** WI-burud was the Rust-side example;
   if SCIP-backed analyzers ship for more languages, their cross-language
   linkers should similarly check for backend markers. Each language pair
   needs its own decision.

## Cross-References

- **WI-mafut**: SCIP → IR translation shim (the foundation this sketch builds on).
- **WI-duzul**: rust-analyzer package scaffold.
- **WI-bajuz**: SCIP descriptor → tree-sitter stable_id parity helper.
- **WI-todon**: silent fall-through diagnostics — the engagement-check
  pattern other wrappers should mirror.
- **WI-vovad**: rust-analyzer validation curriculum — the cohort/metric
  template other languages should mirror.
- **WI-totub**: proc-macro expansion gate question (negative result 2026-05-24
  on the default invocation, recorded for procMacro.enable follow-ups).
- **WI-burud**: cross-language linker upgrades — the per-language story this
  sketch implicitly extends.
- **`docs/future/registry-factory-vision.md`**: the broader "what could
  hypergumbo become?" vision that this fits inside.
