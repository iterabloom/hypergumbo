<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Release notes — hypergumbo 6.x

This file is the user-facing view of what's in each 6.x release. The
[CHANGELOG.md](../CHANGELOG.md) remains the implementer-facing log
(every Added / Changed / Fixed entry, every internal refactor, every
test pin). When you upgrade, read here first; consult the changelog
if you need the implementation detail behind a given change.

One file exists per major version line; for the previous line see
[RELEASE-NOTES-5.X.md](RELEASE-NOTES-5.X.md).

---

## Unreleased

> **These versions are in active development.** Everything below is part of an ongoing correctness campaign, and it is *not* a stabilized release. In particular, **identity values (`stable_id`, fingerprints) and the `validation_report` schema are still moving between in-development versions** — diffing or persisting them across these versions is at your own risk. Re-run hypergumbo to regenerate; do not treat cross-version identity or validation output as stable. (This section is intentionally leaner than 6.0.0; it is expanded at release-cut.)

### At a glance

- **The results cache now invalidates on a tree-sitter grammar upgrade** — previously an unchanged repo returned a stale cache hit computed by the *old* grammar after you upgraded a grammar package. If you upgrade tree-sitter or a grammar, hypergumbo now re-analyzes instead of serving stale output.
- **Analyzer and linker crashes no longer abort the whole run** — a single crashing analyzer, linker, or unreadable file now yields *partial* results (the crashed pass is recorded in `limits.skipped_passes`) instead of failing the entire `hypergumbo run`.
- **Python symbol and call-graph fixes** — Python functions/methods/classes now populate `qualified_name` (was `null` for 100% of Python symbols), and `calls` / `instantiates` / `references` edges now attribute `src` to the correct method instead of a last-write-wins bare-name lookup.
- **More complete identity on synthetic nodes** — `AnalysisRun` gains per-pass productivity counters and an always-on timer, synthetic nodes now carry resolvable provenance and (for protocol stand-ins) populated `stable_id` / `display_label` / `fingerprint`, and the validator surface grew.
- **Identity scheme moved again** — `stable_id` advanced v5 → v8 and fingerprinting was hardened, so identity values **differ from 6.0.0**. This is a cross-version-stability caveat, not a feature break: within a single run identities still discriminate correctly.

### For JSON consumers

These are the consumer-visible field and schema changes since 6.0.0. Because the campaign is mid-flight, treat identity values and the `validation_report` schema as unstable across in-development versions.

- **Python `qualified_name` now populated.** Python functions, methods, and classes now carry `qualified_name` (the existing container-qualified name threaded through), where 6.0.0 emitted `null` for 100% of Python symbols. `name` is unchanged. Identity-neutral.
- **Python call-graph `src` attribution corrected.** Caller resolution for `calls` / `instantiates` / `references` edges now keys on AST node identity rather than a last-write-wins bare-name dict, so each edge's `src` is attributed to the correct method (a paired guard keeps methods out of `inner_scope` so nested helpers aren't shadowed). Consumers reading the Python call graph will see corrected source attribution.
- **Synthetic protocol stand-ins now carry full identity.** A post-linker backstop stamps `stable_id`, `display_label`, and `fingerprint` on Class-B synthetic protocol-synth Symbols (from ~7 linkers) that previously emitted those fields as null; a validator biconditional pins the `display_label` invariant. Additive and identity-neutral for non-synthetic nodes.
- **`AnalysisRun` productivity counters and timer** (SCHEMA_VERSION 0.14.1 → 0.14.2). New `nodes_emitted` / `edges_emitted` fields per `AnalysisRun`, and `duration_ms` now starts for *every* pass (previously IR-consuming passes reported `0ms` while emitting edges). A floor makes `0ms` now unambiguously mean "did nothing."
- **`stable_id_stats` block added** (`validation_report` schema 0.1 → 0.2). An always-present stats block lands; per-file duplicate `stable_id` is now a hard validator error and the corpus-collision umbrella reports against an all-Symbols denominator with the `None`-cohort disclosed.
- **`wired_checks` manifest added** (`validation_report` schema 0.2 → 0.3). `build_validation_report` now emits a `wired_checks` disclosure manifest so an unvalidated defect class is visible by its absence. New validator predicates cover dangling edge endpoints and the `origin_run_id → analysis_runs.execution_id` foreign key.
- **Identity values differ from 6.0.0** (cross-version-stability caveat). `stable_id` advanced **v5 → v8** (full enclosing-scope chain folded into one shared formula, occurrence-index slot, path-anchoring of module/interface/type/entry/dependency IDs, cross-file disambiguation, and route/call-site path anchoring), decorated Python declarations now fingerprint name+signature+body whole (they previously collapsed to a body-only hash), and producer-side bare-hex fingerprint leaks are normalized to the canonical `hgfp2:` prefix at the output boundary. Net effect: identity values emitted by these in-development versions are *not* comparable to 6.0.0's. Detect the boundary and regenerate; do not diff identities across it.
- **`Edge.is_resolved` semantics tightened.** `is_resolved` now contractually means the destination is a real in-repo first-party node; a finalization step derives both `is_resolved` and `dst_ref` from one verdict, so producer-stamped values are advisory. Consumers that branched on the prior looser meaning should re-check.
- **GraphQL operation kinds now classified.** GraphQL mutation and subscription are registered as `language_construct`, and the anonymous-operation fallback as `pending_classification` (previously unclassified).

### Reliability & correctness

- **Results cache invalidates on tree-sitter grammar upgrade.** The analyzer-identity cache key hashed only hypergumbo package versions, so a grammar upgrade on an unchanged repo returned a stale hit from the old grammar. The key now folds in the tree-sitter library and grammar package versions, so a grammar upgrade forces re-analysis.
- **Orchestrator passes fail open.** Every pass-level crash site in both orchestrators was unguarded, so any single-pass exception aborted the whole run. Crashes are now contained and recorded pass-level (in `limits.skipped_passes` with a `crashed:` reason and a `partial_results_reason`) so the remaining passes still run and you get partial results. Unreadable files are routed into `failed_files` rather than crashing the run.
- **Tiered `nodes_summary` now matches the on-disk arrays.** `format_tiered_behavior_map` wrote `nodes_summary` from the pre-shrink connectivity selection, so summary counts overstated the arrays actually serialized after the budget-shrink loop pruned them. The summary is now recomputed from the final post-shrink arrays, so the counts match what is on disk.
- **Synthetic-node provenance repaired.** File-symbol synthesis and boundary `external_symbol` synthesis each now emit a real `AnalysisRun` and stamp a resolvable `origin_run_id`, where the empty-string sentinel previously broke the node → AnalysisRun join for thousands of synthetic nodes. Direct-constructor analyzers (toml/json/wgsl/sql) also get a central `origin_run_id` backstop. Additive and identity-neutral.

## 6.0.0 — 2026-06-10

### At a glance

- **View-template detection grew from Rails-only to five frameworks** —
  Django, Phoenix, Spring MVC, and Laravel Blade now emit `renders`
  edges alongside Rails. If your repo uses any of these, `hypergumbo`
  now traces controller → template relationships.
- **Five more frameworks recognized for HTTP routes and dispatch** —
  bare-Node `http.createServer`, Apollo standalone GraphQL, plus
  Django-ecosystem third-party base classes (HierarkeyForm,
  django-filter `FilterSet`, DRF `Serializer`, Wagtail `Page`).
- **Provenance and reproducibility** — every edge records which
  passes created it (`origin` as a list), which Symbols it was
  derived from (`derived_from`), and a code-hash `pass_version`.
  `behavior_map["reproducibility_context"]` captures tool and
  grammar versions. `hypergumbo explain --provenance` surfaces
  derivation chains.
- **Inherited-calls linker** — a new centralized linker walks
  ancestor chains to resolve unresolved `calls` edges across
  Ruby, Groovy, and Java.
- **`--gzip` and consumer-side `.gz` support** — `hypergumbo run
  --gzip` compresses output ~90-95%. All `--input`-taking
  subcommands now transparently read `.gz` files.
- **Cleaner I/O-boundary output by default** — the `external_potential`
  bucket no longer dominates the text view; opt back in with
  `--show-external-potential` when you want it.
- **Better cross-language analysis** — Kotlin / C# return-type
  inference unwraps `User?` / `Task<T>` / `ValueTask<T>` so chained
  receiver resolution stops dropping methods; PyO3 and N-API FFI
  bridges now trace canonical project shapes.
- **Per-entry-point safety claims** — `hypergumbo verify-claims`
  now ships a project-local taint catalog used to audit hypergumbo's
  own IO surface; see [SECURITY.md](../SECURITY.md) for the
  generated audit.
- **52% more entrypoints detected** — bash/sh scripts, `index.html`
  SPA roots, and standalone TS/JS script modules are now recognized.
  64 → 97 entrypoints on self-analysis.
- **Orphan-node rate cut from 5.5% to 2.0%** — intra-file variable
  references, nested function defs, and identity/dedup fixes reduce
  disconnected Symbols.
- **JSON wire format evolves** — schema version 0.5.8 → 0.11.0,
  retiring 71 `Symbol.kind` values and 111 `Edge.evidence_type`
  values into canonical names + `meta` keys, plus new provenance
  fields. JSON consumers should read
  [MIGRATION-6.0-CONCEPT-AXES.md](MIGRATION-6.0-CONCEPT-AXES.md)
  before upgrading.

### For CLI users

**New flags.**

| Flag | Subcommand | What it does |
|---|---|---|
| `--gzip` | `run` | Compress output as gzipped JSON (~90-95% reduction). `--out` auto-appends `.gz` when the path doesn't already end with it. |
| `--no-sketch-fan-out` | `run` | Skip emission of the precomputed sketch-tier previews (`<stem>.{4k,16k,64k}.json`). Equivalent to `--budgets none`; surfaced as a named flag so it shows up next to `--no-handler-slices`. |
| `--show-external-potential` | `io-boundaries` | Re-enable the `external_potential` bucket in text output. The bucket is now suppressed by default because it dominated output volume on large repos (kafka 76k chains, airflow 28k of ~30k). JSON output is unchanged. |
| `--provenance` | `explain` | Show per-edge derivation chains: resolves `Edge.derived_from` IDs to `name (kind)` pairs. `explain` now always shows `Origin:` with contributing passes and annotates callers/callees with edge type. |

**Changed defaults.**

- `hypergumbo io-boundaries` no longer prints the `external_potential`
  bucket by default. The bucket remains in JSON output and is
  reachable via `--show-external-potential` or
  `--boundary external_potential`.
- `hypergumbo run --out foo.json` now lists its side-output files
  (budget-tier previews + `<stem>.slices/`) up front in the help
  text and run-command epilog, with a `--budgets none --no-handler-slices`
  example for single-file output.
- All `--input`-taking subcommands now transparently read `.gz`
  files via a shared `load_behavior_map()` helper routing all 11
  consumer sites.
- `pass_id` values in JSON output no longer carry a `-v1` / `-ts-v1`
  / `-ast-v1` suffix. This is a breaking change for consumers that
  match on pass IDs.

**Bugfixes worth knowing about.**

- `hypergumbo slice` output summary now reads "Generated N
  artifact(s)" (was truncated) and duplicate artifact listings
  across 8 subcommands fixed.
- `hypergumbo symbols` Kind column no longer truncates
  (e.g., "functi…"). Width computed from data.
- `hypergumbo explain Symbol | head` no longer prints a
  `BrokenPipeError` traceback. POSIX-only; Windows was never affected.
- `--backend rust-analyzer` install advice now suggests `pipx install
  --force` or `pipx inject hypergumbo hypergumbo-lang-rust-analyzer`
  when `hypergumbo` is already on the system (the old advice silently
  no-op'd because `pipx install` already saw the bare package).
- `--backend rust-analyzer` crash now surfaces exit code, stderr tail,
  and OOM-kill hint instead of silently falling through to tree-sitter.
- `hypergumbo run` no longer emits two false-positive warnings on
  repos that don't contain Circom files or TOML manifests.
- `remove-extras` actually uninstalls the three source-built grammars
  now (`tree-sitter-lean` / `tree-sitter-wolfram` / `tree-sitter-circom`).
  The previous behavior was a silent no-op.
- Sketch progress no longer contaminates captured stderr (gates on
  `sys.stderr.isatty()`).
- `limits.failed_files[]` now actually populated — previously always
  `[]` even when files were dropped.

### For JSON consumers

**Schema version chain: 0.5.8 → 0.14.1.** Thirteen bumps in this
release — two carrying breaking enum closures, one a type change,
and one a field removal:

| Version | What it brings |
|---|---|
| 0.6.0 | `Symbol.kind` endpoint_shape closure: 71 values retired, folded onto canonical kind + `meta["framework_role"]` / `meta["call_kind"]` / etc. |
| 0.7.0 | `Edge.evidence_type` endpoint_shape closure: 111 values retired, folded onto canonical inference label + `Edge.is_resolved` / `meta["framework_dispatch"]` / `meta["call_construct"]`. |
| 0.7.1 | 6 evidence_type additions; `error_set` on `Symbol.kind` for Zig. Additive. |
| 0.7.2 | `Edge.dst_ref` field lands as optional sibling of legacy `Edge.dst`. |
| 0.8.0 | Producers canonicalize on `Edge.dst_ref`; consumers should prefer it over the legacy colon-split heuristic on `Edge.dst`. |
| 0.9.0 | Self-analysis validates clean. `line=0` fix on module_exports edges; missing top-level keys added. |
| 0.9.1 | `Edge.derived_from: list[str]` lands. `pass_id` suffix removal. `behavior_map["features"]` and `behavior_map["reproducibility_context"]` added. Additive. |
| 0.10.0 | **Breaking:** `Symbol.origin` and `Edge.origin` change from `str` to `list[str]`. Multi-source attribution: when multiple passes contribute, all are credited. |
| 0.11.0 | `origin_run_signature` removed from `Symbol` and `Edge` output. The field was never stamped by any producer, so emitted JSON is unchanged in practice; consumers that read it should drop the key. `from_dict()` silently ignores it for backward compatibility with pre-removal JSON. |
| 0.12.0 | Symbol-field axis decomposition: four new fields (`discovery_language`, `protocol_origin`, `display_label`, `qualified_name`); `Symbol.language` relaxes to nullable (synthetic protocol stand-ins carry `null` + the new sibling fields); `canonical_name` deprecated. |
| 0.13.0 | **Breaking:** `Symbol.canonical_name` removed. Read `qualified_name` (or `display_label` for synthetic stand-ins' UI strings). `from_dict()` ignores the legacy key. |
| 0.14.0 | Published schema $defs now introspected from the dataclasses (the hand-coded schema had drifted: it rejected every real linker-bearing document). `language` nullable in the schema; stale `canonical_name` property gone; whole-document validation passes on real output. |
| 0.14.1 | `limits`, `features[]`, and `metrics` get real type definitions; `reproducibility_context`, `symbol_fingerprint_scheme`, and `validation_report` declared. Additive. |

If you consume the JSON output, **read
[MIGRATION-6.0-CONCEPT-AXES.md](MIGRATION-6.0-CONCEPT-AXES.md)
before upgrading.** It has the full rename tables for both
registries and adoption guidance for `Edge.dst_ref`.

**Every `Symbol.fingerprint` value changed** (`hgfp1:` → `hgfp2:`;
`symbol_fingerprint_scheme` moves v1 → v2). The fingerprinter now
parses each file once and hashes the parse subtree covering the
symbol's span; v1 parsed span slices out of context and silently
degraded on content that doesn't parse standalone — in 5.x all TOML
dependency nodes shared one fingerprint and ~thousands of Python
test methods had `null`. Don't diff fingerprints across the
5.x / 6.0 boundary (detect the boundary via the scheme tag or the
value prefix); within 6.0 they discriminate correctly, and spans
whose content can't be parsed yield an honest `null` rather than a
degenerate shared constant.

**New: `Edge.dst_ref`.** A structured external-target sibling of
`Edge.dst`, carrying `(lang, module_path, name)` as an
`ExternalRef` dataclass. Aliased imports bind `name` to the
imported symbol, not the local alias. Eight analyzers have adopted
it (Java, Go, Elixir, JS/TS, C++, Rust, Ruby, Python); four
consumers prefer it over the legacy `Edge.dst` colon-split heuristic.
Cached JSON from earlier releases loads cleanly with
`dst_ref=None`.

**New: `Edge.derived_from`.** Every linker-produced Edge records
which Symbol IDs were consumed to construct it. Populated across
all 55 linker modules.

**New: `Symbol.origin` / `Edge.origin` as `list[str]`.**
Previously a single string; now a list to support multi-source
attribution. Consumers that read `origin` as a string must switch
to list iteration.

**New: `behavior_map["reproducibility_context"]`.**
Captures L2 reproducibility metadata (hypergumbo / Python /
tree-sitter / grammar versions) plus an explicit `not_captured`
array.

**New: `behavior_map["features"]`.** Spec-shape index entries for
detected route handlers. Stable feature IDs enable
diff-across-commits.

**New: `disambiguation_fallback` discipline across 13 linkers.**
When a linker resolves a simple name (e.g. `User`) to a structurally
ambiguous target, the resulting edge now carries
`confidence ≤ 0.5` and `meta["disambiguation_fallback"]=True`.
If you filter or weight edges by confidence, this is the contract
you can rely on. A static linter pins the contract at every
emission site.

**Changed: `pass_id` suffix removal.** The legacy `-v1` / `-ts-v1`
/ `-ast-v1` suffixes on pass IDs are removed;
`make_pass_id(name) == name`. Backend identity moves to
`Pass.backend`; display labels to `Pass.pass_label`. If you match
on pass IDs, update your patterns.

**New wire contract: `io-boundaries --json`.**
`IO_BOUNDARIES_SCHEMA_VERSION = "1.0"` is now part of the envelope.
Top-level keys are locked: `schema_version`, `total_io_edges`,
`boundaries`, `unsupported_languages`. Bumping rules live in the
module docstring; a test fails loudly on silent drift.

**Changed: `has_high_risk` coverage extends across 14 languages.**
The `has_high_risk` field in the `io-boundaries --json` envelope
will now flip `true` for many more subprocess-launching call
sites. Coverage was previously concentrated on Python / Go / Java /
Rust / JS / C; it now also covers Kotlin, Scala, Elixir, Erlang,
Haskell, Swift, Objective-C, C++, and additional Go / Node / Rust /
C surface (48 new qualified names; 100 entries total). 18
wait/signal/PATH-lookup/self-exit entries are explicitly exempted —
they're subprocess-boundary for taint tracking but don't represent
arbitrary code execution. JSON consumers that filter on
`has_high_risk` will see higher counts on repos using these
languages.

### For specific languages and frameworks

**Django.** New `dispatches_to` coverage for HierarkeyForm,
django-filter `FilterSet` / `WagtailFilterSet`, DRF `Serializer`
family, and Wagtail `Page`. New `renders` edges for class-based
views (`DetailView` / `ListView` / etc.) deriving template from
`model = <Name>`, and for explicit `render(request, "<template>", ctx)`
calls.

**Phoenix (Elixir).** `renders` edges now emitted for 1.x
templates (`lib/my_app_web/templates/<ctx>/<action>.html.{eex,heex,leex}`),
1.7+ co-located controllers, and the `MyAppWeb.UserHTML.show`
function-component shape. Phoenix test files at `test/.../*_test.exs`
now classify as `tier=1` + `is_test=True` (previously conflated
with vendored code at tier=2).

**Spring MVC (Java).** `renders` edges for `@Controller` methods
returning view-name strings or `ModelAndView("users/show", model)`.
Thymeleaf, FreeMarker, Velocity, and JSP locations probed.
`@RestController` correctly excluded.

**Laravel (PHP).** `renders` edges for `app/Http/Controllers/`
methods calling `view(...)` or `View::make(...)`. Dotted view names
map to directory paths; `.blade.php` probed before plain `.php`.
The Blade analyzer now actually enrols on Laravel repos (a
file-indexing bug caused `.blade.php` files to be silently dropped).

**JavaScript / TypeScript.** Bare-Node `http.createServer` /
`https.createServer` / `http2.createServer` and Apollo
`startStandaloneServer` now produce `framework_role='route'`. Cross-
codebase setups where a TS client calls a gRPC server in a separate-
language repo now produce `calls` edges from unmatched TS stubs
directly to the proto service Symbol. `access_mode` annotation
coverage extended to `return` / `throw` / `yield` / `await` /
`update_expression` contexts — `--dataflow` slices on TypeScript
repos are useful again.

**Kotlin.** Receiver-type inference strips the nullable `?` suffix
so methods returning `User?` propagate `User` into `var_types`.
Chained-receiver resolution on nullable getters no longer drops
methods.

**C#.** Receiver-type inference unwraps `Task<T>` / `ValueTask<T>` /
`IAsyncEnumerable<T>` so `var x = await SomeAsync()` binds to the
awaited type. Bare wrapper-only returns stay `None`.

**Ruby.** Constructor-call `.new` redirect now walks the inheritance
chain when the named class doesn't define `#initialize` directly.
Rails routes are now distinct entrypoints, and `dispatch_inherited`
handles Ruby's `Class#method` separator.

**Java.** Wildcard imports (`import java.util.*`) now resolve to the
source package for class-shaped receivers. Java's inline parent-chain
walk for inherited calls replaced by the centralized inherited-calls
linker.

**Python.** `verify-claims` now resolves more method-call receivers
through a post-DDG IR refinement pass — `x = os.environ; x.get(...)`
no longer conflates with `dict.get` / `args.get`. Eight zone-tagged
wrappers in `hypergumbo_core.safety_zones` provide fs-write
discipline that downstream Python projects can adopt. Nested function
defs are now emitted as Symbols with qualified names and bare-name
calls resolved via LEGB scope walk (~121 missing Symbols and ~360
missing call edges recovered on self-analysis). BOM-prefixed files
(`utf-8-sig`) no longer silently dropped. Intra-file variable
reference edges for module-level constants reduce orphan Symbols.

**Rust.** PyO3 `#[pymethods] impl Foo { fn bar() {} }` propagates
the annotation to every method declared inside; path-qualified
spellings like `#[pyo3::pyfunction]` are recognized. Python → Rust
FFI chain tracing now finds the canonical ~100-method PyO3 surface
(was 4/~100 on Robyn pre-fix). The `Edge.dst` 6-segment fabrication
on aliased `use` imports is gone — Rust edges now carry proper
canonical names in `Edge.dst_ref`.

**Node-addon-api (C++).** N-API template forms
(`Napi::Function::New<F>(env)`, `InstanceMethod<&C::M>("name")`,
`InstanceAccessor` property bindings) are now matched alongside the
function-argument forms. Sharp, canvas, and similar modern
node-addon-api projects benefit.

**Ansible.** `include_tasks: "{{ ansible_os_family }}.yml"` and
`import_tasks: "{{ tasks_path }}/yumrepos.yml"` fan out to real
target files instead of leaving a single unresolved `dst='{{ ... }}'`
edge. 191/192 unresolved `imports` on fedora-infra/ansible now fan
out.

**Solidity.** `contract` is now a canonical `Symbol.kind`
(previously emitted but unregistered).

**CUDA, Android XML.** Producer-side folds onto canonical kind +
`meta` discriminator. CUDA emits `kind="function"` +
`meta["cuda_execution_space"]`; Android emits `kind="component"` +
`meta["component_type"]` for `<activity>` / `<service>` /
`<receiver>` / `<provider>`.

### Cross-cutting quality improvements

**Identity and deduplication.** Python class `stable_id` collisions
fixed — five `@dataclass` classes in `ir.py` previously shared one
hash. File identity now threaded into top-level `stable_id`
computation so structurally identical classes in different modules
produce distinct hashes. File/module double-representation collapsed
(Python, JS/TS, Bash, Perl, PHP, PowerShell no longer emit both
`kind="module"` and `kind="file"` for the same path).
STABLE_ID_SCHEME bumped from v1 to v4.

**Dead-code analysis.** `dead-code-maybe` now demotes
framework-dispatched symbols (route handlers, decorator callbacks)
and polymorphic-dispatch overrides via usage_contexts
cross-referencing and ancestor-chain method matching.

**Entrypoint detection.** Bash/sh scripts recognized via
`shell_script` concept; `index.html` SPA roots via `html_entry`;
standalone TS/JS script modules (no inbound imports + has outbound
calls) via `script_module` kind. Main-function dedup prevents
double-counting. 64 → 97 entrypoints on self-analysis (+52%).

**Supply chain classification.** Test directories no longer route
to tier=2 (internal_dep) — tests are first-party. Previously
99.8% of tier-2 paths on self-analysis were test files.

**Analyzer dispatch.** 113 of 133 analyzers were previously
dispatched to repos with zero matching files, consuming ~13% of
wall-clock time. Now pre-filtered by file presence with reason
recorded in `limits.skipped_passes`.

### For framework-pattern YAML authors

`Pattern.meta_match: dict[str, str]` is a new field on framework
YAML patterns. Each key must be present on `Symbol.meta` and its
`str()`-coerced value must match the regex. Multiple keys AND
together and can combine with `symbol_kind` / `framework_role`.
This is the canonical post-ADR-0027 form for rules that previously
hardcoded bare-kind regexes. 11 rules in
`frameworks/config-conventions.yaml` and
`language-conventions.yaml` migrated.

### For users of `hypergumbo verify-claims`

Per-entry-point safety claims and a project-local taint catalog
now ship. Hypergumbo's own audited-IO-surface self-audit lives at
[`docs/hypergumbo.claims.yaml`](hypergumbo.claims.yaml). Project-
local sources / sinks / sanitizers are loadable via
`extra_catalogs:` in the claims YAML; flags `--taint-sources`,
`--taint-sinks`, `--taint-sanitizers` each accept a YAML file or a
directory glob and are repeatable. User entries whose
`(module, name, kind)` triple matches a built-in replace it;
sanitizers concatenate.

Propagation now runs per-language (previously an Elixir
`HTTPoison.get` sink had been matching every Python `.get()`,
producing ~15K spurious findings on self-analysis).

### Preview features

None at this time. (`--gzip` graduated to a full feature now that
all `--input`-taking subcommands transparently read `.gz` files.)

### Known limitations

- Short-name sink matching in `verify-claims` overapproximates for
  method-call receivers the DDG can't resolve (call-return
  bindings, closure captures). The post-DDG IR refinement pass
  resolves the import-rooted case in Python; `taint_refine` now
  also pins parameter-receiver types from function-signature
  annotations (e.g. `name: str` → `name.replace(...)` no longer
  matches `pathlib.Path.replace`). Rust and TypeScript follow when
  those ship def/use extractors per ADR-0017 §1c. Load-bearing
  claims (dev-zone and install-zone unreachability) verify cleanly.
  See [SECURITY.md](../SECURITY.md) for the residual detail.

### Where to read more

- [CHANGELOG.md](../CHANGELOG.md) — full implementation log
- [MIGRATION-6.0-CONCEPT-AXES.md](MIGRATION-6.0-CONCEPT-AXES.md) — schema enum migration tables for JSON consumers
- [SECURITY.md](../SECURITY.md) — per-entry-point safety claims (auto-generated)
- [LINKERS.md](LINKERS.md) — full linker catalogue
- [FRAMEWORKS.md](FRAMEWORKS.md) — framework pattern catalogue
- [LANGUAGES.md](LANGUAGES.md) — language support matrix
