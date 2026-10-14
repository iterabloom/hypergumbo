<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Release notes — hypergumbo 9.x

This file is the user-facing view of what's in each 9.x release. The
[CHANGELOG.md](../CHANGELOG.md) remains the implementer-facing log
(every Added / Changed / Fixed entry, every internal refactor, every
test pin). When you upgrade, read here first; consult the changelog
if you need the implementation detail behind a given change.

One file exists per major version line. Previous lines:
[8.x](RELEASE-NOTES-8.X.md) · [7.x](RELEASE-NOTES-7.X.md) ·
[6.x](RELEASE-NOTES-6.X.md) · [5.x](RELEASE-NOTES-5.X.md).

---

## TL;DR

**9.0.0 makes symbol ids hold still, makes catalogue trust explicit, and stops
analysing installed dependencies by default.** It is a major release because
stored ids, node sets and some verdicts differ from 8.x.

**Five things will change a working setup. Act on these:**

1. **`stable_id` values from 8.x are not comparable** (`stable_id_scheme`
   v8 → v9). Re-analyze with 9.0.0; to carry old data across, join on path,
   kind, name and span, or on `fingerprint`. Top-level declarations keep their
   id unless something they hash changed; nested declarations, doc-family
   symbols and overloads in many languages get new ids.
2. **Some nodes merge or are renamed.** An Elixir function's clauses are one
   node per name and arity; a PureScript function's signature and equations
   are one node; nested OCaml, F#, Agda and Lean declarations carry their
   module (`A.run`); `qualified_name` carries every enclosing scope in Proto,
   Cap'n Proto, C#, TypeScript, PHP and Tcl.
3. **Installed dependency source is skipped by default** (`deps/`,
   `node_modules` installed by npm, virtualenvs and similar, recognised by
   content). Calls into it become I/O unknown, not "no I/O". Pass
   `--trace-deps <pkg,...|all>` to analyse named packages. A *committed*
   `node_modules` or `vendor` directory is now analysed; `--exclude vendor`
   restores the old behaviour. "Choosing which dependency code to analyse"
   below says which setting suits which goal.
4. **Community catalogue rows can no longer make a verdict cleaner.** Their
   sanitizers, function summaries and grants are withheld and named, so some
   `verify-claims` verdicts move from `confirmed` to `violated`. Catalogue
   data in a repository's own `.hypergumbo.toml` loads only on opt-in
   (`--in-repo-catalogues`, or `hypergumbo trust-catalogues [PATH]`).
5. **New exit code 2 cases**: a broken catalogue or config file (it used to
   crash with exit 1, the code for `violated`, or be skipped in silence), a
   missing or unknown `--backend` value, and `python -m hypergumbo_core.cli`.

`SCHEMA_VERSION` advances 0.20.13 → 0.20.14 (additive).

---

## Unreleased

> `SCHEMA_VERSION` 0.20.13 → 0.20.14, the `verify-claims --json` envelope
> 2.4 → 2.6, the `io-boundaries --json` envelope 2.3 → 2.4, and the embedded
> `validation_report` 0.3 → 0.4. Every one of these steps only adds fields.
> The breaking changes are in values (ids, names, verdicts) and in what a
> default run reads, not in the shape of the JSON.

### At a glance

- **Symbol ids survive edits elsewhere in the file (ADR-0035).** Deleting a
  symbol no longer moves any other symbol's `stable_id`, ids no longer depend
  on line numbers or on where the repository is checked out, and every kind of
  symbol has an id. A test runs 204 per-language fixtures and fails on any id
  that moves when an earlier sibling or enclosing scope is deleted.
- **Catalogue tiers (ADR-0061).** Every shipped catalogue file says whether it
  describes the standard library (`builtin`) or a third-party library
  (`community`). Only `builtin` rows can clear a flow; the verdict lists every
  catalogue file it rested on, by tier, under `catalog_provenance.tiers`.
- **Installed dependencies are skipped and the skip is disclosed (ADR-0004).**
  The skip is reported on stderr, in the sketch and in
  `supply_chain_summary.installed_deps_skipped`. Build output such as
  `target/` is pruned only when the tool that writes it is present (a
  `Cargo.toml` beside it), so a source directory named `target/` is analysed.
- **Calls bind to the declaration in scope** in Go, JS/TS, Python, Swift and
  Perl, and C++, Elixir and Nim choose overloads by arity. On four JS/TS
  repositories this removed 7,153 wrong call edges.
- **Taint follows more flows**: a source returned by a helper is followed into
  its callers; loop headers and `if`/`switch` initializers define their
  variables; new `code_execution` and `dom_injection` sinks (`eval`,
  `document.write`) and a `navigation_read` boundary (the page URL is
  untrusted input).
- **Elixir and Erlang behaviours and Elixir protocols are modelled**:
  `@callback`, `defprotocol` and `defimpl` produce `implements` edges and
  dispatch by name and arity (ejabberd: 0 → 242 `implements` edges).

### Choosing which dependency code to analyse

9.0.0 sorts dependency code into two kinds and treats them differently by
default:

- **Installed dependency source** is what a package manager put into the
  working tree and git does not track: Mix `deps/`, `node_modules/` beside a
  `package.json`, a virtualenv, Composer and Go `vendor/`, Bundler
  `vendor/bundle`, CocoaPods `Pods/`. It is **skipped**, and the skip is
  reported on stderr, in the sketch's Overview and in
  `supply_chain_summary.installed_deps_skipped`.
- **Committed vendored code** (any of those directories whose files are in
  git, plus `third_party/` and submodules) is **analysed** and classified
  tier 3.

Pick the setting from what you want to learn:

| Goal | Setting | Effect on the run |
|---|---|---|
| Map, slice or search your own code; find dead code | the default | Installed dependencies are not parsed. Calls into them stay in the graph as external stubs, so you still see which library functions your code calls. |
| Find out what a library call actually does with your data (taint, `io-boundaries`, `verify-claims` through a library) | `--trace-deps <pkg,...>`, naming only the packages on the path | Only the named packages are parsed, as tier 3. Without this, calls into them are reported as I/O unknown, and a claim that depends on them is withheld unless an I/O overlay vouches for the module. |
| Trace everything installed | `--trace-deps all` | Every installed package is parsed. This is the costly setting: parsing a 40 MB Mix `deps/` (81 packages) got a 6 GB VM killed for running out of memory, which is why it is no longer the default. |
| Leave a committed vendor tree out | `--exclude vendor` (or the directory's name) | The tree is never parsed, which saves its parse time. This is the 8.x default behaviour for `vendor/` and `node_modules/`. |
| Hide dependency symbols from the output | `--first-party-only` or `--max-tier N` | Symbols above the tier are dropped **after** parsing, so this saves no parse time. Use `--exclude` when time or memory is the concern. |

Two things to know when you opt in:

- **Make it persistent for read commands.** The setting is part of the
  results-cache key. `search`, `slice`, `io-boundaries` and `verify-claims`
  find a traced survey only when the `trace_deps` key in `config.toml` or
  `.hypergumbo.toml` matches, so put `trace_deps = ["httpx"]` there rather
  than passing the flag to a single command.
- **Package names follow each ecosystem's layout**: the Mix app directory
  (`phoenix_html`), the npm package (`react`, `@scope/name`), Composer's
  `vendor/package`, the Go module path, the gem name (`rack`), the pod, and a
  virtualenv's import name (`yaml`, not `PyYAML`).

We have not measured the run-time cost of `--trace-deps` beyond the
out-of-memory case above; it grows with the amount of source traced.
Carthage checkouts, Yarn PnP `.yarn/cache`, `bower_components`, Swift PM
`.build/checkouts` and `cargo vendor` output are not recognised as installed
yet, so they are analysed when present; `--exclude` skips them.

### For `verify-claims` users

- **A call into dependency source that was not analysed is I/O unknown.**
  `io-boundaries` lists such modules under *I/O unknown* instead of printing
  "No I/O boundary calls detected", and `verify-claims` says why it withholds
  the verdict.
- **One write is one finding.** A stream use carried by a sink call folds into
  it; the carriers are listed in `sink_carriers` (envelope 2.6).
- **`trust_level: trusted` sinks are excluded and disclosed** in
  `trusted_sink_flows` (envelope 2.5).
- **C and C++ taint finds sources and sinks through the `#include` set.** On
  11 C/C++ repositories, 11 withheld verdicts became `violated`.
- **Fewer verdicts are withheld for no reason**, and caveats are more
  accurate: a clean `untrusted_input` verdict carries `deferred_crossing`, and
  modules reached through a JS handler assignment or a Python dunder are no
  longer reported as structurally invisible.
- **`verify-claims` no longer hangs on a directory symlink that points to
  itself.**

### For JSON consumers

- **New fields**: `metrics.edge_confidence`, `profile.languages[*].test_files`,
  `analysis_runs[].cpu_ms`, per-limit slice truncation counts
  (`limits_detail`), `supply_chain_summary.installed_deps_skipped` and
  `generated_output_skipped`, and `validation_report.stable_id_stats`'s
  `floor_cohort` and `floor_abstained`.
- **Mix and npm manifests mark external calls `direct` or `transitive`**, read
  from `mix.exs`/`package.json` and their lockfiles (`mix.exs` is read, never
  executed). `Cargo.toml`, `Gemfile` and `composer.json` are not read yet.
- **`profile.frameworks` lists each framework once**, and sketch output no
  longer varies between runs with the per-process hash salt.
- **Pub/sub edges store an unresolved identifier under `topic_identifier`,
  `channel_identifier` or `event_identifier`**, not as the topic name.

### For specific languages

- **Elixir**: Phoenix routes match `mix phx.routes`, including `resources`
  (eight routes, honouring `only:`, `except:`, `singleton:` and `param:`) and
  `scope` aliases and prefixes; `use AppWeb, :live_view` modules implement
  `Phoenix.LiveView`.
- **Java**: records are analysed (trino: +4,480 `instantiates` edges); their
  implicit accessors are not emitted yet.
- **TypeScript, Dart and Kotlin**: interface inheritance emits edges; C++ and
  C# dispatch through abstract bases (grpc: +1,905 `dispatches_to`).
- **JS/TS**: `module_exports` edges reach only exported names (koel:
  2,084 → 94), and every JS/TS extension (`.mjs`, `.cjs`, `.jsx`, `.tsx`,
  `.mts`, `.cts`) reaches the data-flow walk and the linkers.
- **Declarations that produced no symbol now do** in C, C++, CUDA, C#, Pony,
  Pascal and Rust (`extern` functions), so the calls inside them are kept.
- **A Circom file with an intermediate signal no longer crashes the run.**

### Security

- **The README has a plain-language "Security and trust" section**: what a
  default run reads, writes and downloads, which commands download or run
  code, what an untrusted repository can influence, and how to run
  hypergumbo confined.
- **Every risky call site in hypergumbo's own source is listed with its
  purpose** in `docs/hypergumbo.io-allowlist.yaml`, checked in CI by
  `scripts/check-io-allowlist`, which imports nothing from hypergumbo.
- **A repository can no longer feed catalogue data into its own analysis by
  default** (item 4 of the TL;DR).

### Performance and installation

- **Three survey stalls on large repositories are fixed**: supply-chain
  classification read each file once per symbol, the crypto-flow linker parsed
  files with no crypto keyword, and the sketch re-tokenized docs. End-to-end
  times have not been re-measured.
- **`hypergumbo build-grammars` works from a PyPI install.** The Lean, Wolfram
  and Circom grammar sources now ship in `hypergumbo-core` (+0.7 MB).

### Known limitations

- Data-flow for Rust tail expressions and JS/TS arrow bodies does not follow
  a returned source into its callers yet, and the data-flow walk does not
  cover C++.
- Java record accessors are not emitted.
- `Cargo.toml`, `Gemfile` and `composer.json` are not read for direct versus
  transitive dependency marking.
- End-to-end survey times after the stall fixes have not been re-measured.
