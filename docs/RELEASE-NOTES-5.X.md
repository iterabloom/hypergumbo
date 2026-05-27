<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Release notes — hypergumbo 5.x

This file is the user-facing view of what's in each 5.x release. The
[CHANGELOG.md](../CHANGELOG.md) remains the implementer-facing log
(every Added / Changed / Fixed entry, every internal refactor, every
test pin). When you upgrade, read here first; consult the changelog
if you need the implementation detail behind a given change.

A new file will exist for each major version line (6.x will get its
own `RELEASE-NOTES-6.X.md` when it ships).

---

## Upcoming 5.x release (Unreleased on `dev`)

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
- **JSON wire format evolves** — schema version 0.5.8 → 0.10.0,
  retiring 71 `Symbol.kind` values and 111 `Edge.evidence_type`
  values into canonical names + `meta` keys, plus new provenance
  fields. JSON consumers should read
  [MIGRATION-5.X-CONCEPT-AXES.md](MIGRATION-5.X-CONCEPT-AXES.md)
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

**Schema version chain: 0.5.8 → 0.10.0.** Nine bumps in flight,
two carrying breaking enum changes and one carrying a type change:

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

If you consume the JSON output, **read
[MIGRATION-5.X-CONCEPT-AXES.md](MIGRATION-5.X-CONCEPT-AXES.md)
before upgrading.** It has the full rename tables for both
registries and adoption guidance for `Edge.dst_ref`.

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
- [MIGRATION-5.X-CONCEPT-AXES.md](MIGRATION-5.X-CONCEPT-AXES.md) — schema enum migration tables for JSON consumers
- [SECURITY.md](../SECURITY.md) — per-entry-point safety claims (auto-generated)
- [LINKERS.md](LINKERS.md) — full linker catalogue
- [FRAMEWORKS.md](FRAMEWORKS.md) — framework pattern catalogue
- [LANGUAGES.md](LANGUAGES.md) — language support matrix
