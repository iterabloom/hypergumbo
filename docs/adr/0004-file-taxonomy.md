<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# 4. File Taxonomy: Tier and Role Classification

Date: 2025-01-14
Status: Accepted

> Edited in place per [`README.md` §"ADR lifecycle"](README.md#adr-lifecycle-status-supersession-and-excision); git history holds earlier text. The 2026-10-06 owner decision added §"Installed dependency source" (a third answer to "why was this file skipped?"; implemented — the skip, disclosure and `--trace-deps` opt-in by WI-bapal-sanok-vujoj-jisub-duzor-dojuz-misiz-fudok, the I/O-unknown report for calls into untraced source by WI-pogar-gahij-nolun-ruhun-konij-rokup-budof-ninif), corrected the implementation-state note on symbol extraction, and revised the matching Consequences line. A second owner decision the same day settled that section's open question: a committed directory is never installed dependency source (point 3). Point 7 was amended 2026-10-12: generated output (build, docs-site and coverage directories) is recognised by the tool that writes it rather than by name, the fallback for a name with no evidence is to analyse, and pruned output is disclosed (INV-hagof-kuzun-havub-zihaz-fudin-rihop-vafaf-dulud). Point 3 was amended 2026-10-15 (architect decision adopted by the owner, WI-vakis-ziram-runih-famim-mufor-gomaf-fipon-bigim). Committed vendored code stays analysed, but it no longer counts in the project's summaries (point 3a; amended 2026-10-16 to drop linker activation from that list, see the note under 3a). Declarations made by the analysed repository never cause committed code to be skipped (point 3b). Git submodules move from the analysed list to installed-dependency handling (point 2 table).
>
> Implementation state: the Decision/Migration Path below read as a forward-looking proposal, but the core taxonomy has landed. `Tier` (IntEnum: FIRST_PARTY/INTERNAL_DEP/EXTERNAL_DEP/DERIVED) lives in `packages/hypergumbo-core/src/hypergumbo_core/supply_chain.py`; `FileRole` (Flag: ANALYZABLE/CONFIG/DOCUMENTATION/DATA), the `LanguageSpec` dataclass, the `LANGUAGES` dict, and the composed `CODE_ROLES` constant all live in `packages/hypergumbo-core/src/hypergumbo_core/taxonomy.py`. (Role flags on `FileClassification` — `is_test`/`is_example`/`is_config`/`is_generated` — are independent boolean axes per WI-jobuj/WI-rigun-patuz and INV-tisid.) The `should_extract_symbols`/`config.analysis_tiers` knob in the Decision-section pseudocode below was **not** implemented, and symbol extraction is **not gated by tier at all**. A file pruned by discovery's excludes (`DEFAULT_EXCLUDES`, the content rules for installed dependency source and generated output — §"Installed dependency source" points 2 and 7 — and `--exclude`) is never parsed; every other file is parsed, its symbols are tier-stamped, and a post-parse filter in `cli.run_survey` drops symbols above the effective tier ceiling — 3 by default, which removes tier 4; `--max-tier` / `--first-party-only` lower it. Tier-3 files that discovery does not prune are therefore analysed by default. Measured 2026-10-06 on a three-file fixture (`mix.exs`; `lib/app.ex` whose `App.hi` calls `Foo.greet`; `deps/foo/lib/foo.ex`), before §"Installed dependency source" was implemented: `Foo` and `Foo.greet` were emitted as tier-3 symbols (`reason: in deps/`) and `App.hi -> Foo.greet` was a resolved `calls` edge. With a `mix.lock` beside `deps/` that is now what `--trace-deps foo` (or `all`) produces; by default `deps/` is skipped and the call is an unresolved stub to `Foo.greet`. The vestigial `SupplyChainConfig.analysis_tiers` field (round-tripped but never read) was removed (WI-josad); the pseudocode is preserved as the original proposal. A consumer that needs a language's file extensions or a file's language reads `LANGUAGES` through `extension_globs` / `extension_suffixes` / `get_language` (and `js_ts_language_for_path` / `grammar_for_path` for the JS/TS family) rather than holding a private literal: the JS/TS-scanning linkers and the DDG language specs do, and two gates (`test_js_ts_extension_single_source.py`, `test_js_ts_ddg_extension_reach.py`) fail when one re-grows a private list (WI-fovus, WI-hizon, WI-pokij).
>
> Cross-ref (NOT supersession): ADR-0022 (Language Profile Registry) and ADR-0041 (Supply-Tier Purity) are topically adjacent successors that build on this taxonomy but do NOT supersede it; this ADR remains Accepted/in force.

## Context

Hypergumbo currently classifies files using several overlapping, independently-maintained systems:

| System | Location | Purpose |
|--------|----------|---------|
| `LANGUAGE_EXTENSIONS` | profile.py | LOC counting (67+ languages) |
| `SOURCE_EXTENSIONS` | sketch.py | Exclude from Additional Files (10 languages) |
| `CONFIG_FILES_BY_LANG` | sketch.py | Config extraction targets |
| `ADDITIONAL_FILES_EXCLUDES` | sketch.py | Patterns to skip for embedding |
| `DEFAULT_EXCLUDES` | discovery.py | Patterns to skip everywhere |
| `Tier` enum | supply_chain.py | Provenance classification (4 tiers) |

This scattered approach creates problems:

1. **Duplication**: `SOURCE_EXTENSIONS` is a subset of `LANGUAGE_EXTENSIONS` but maintained separately.

2. **Ambiguity**: JSON files can be config (`package.json`), data (`model_prices.json`), or generated (`package-lock.json`). The extension alone doesn't distinguish them.

3. **Conflation**: "Should we count this in LOC?" and "Should we extract symbols from this?" are different questions with different answers, but the current system conflates them.

4. **Inconsistent definitions of "code"**: Lock files are excluded (correctly), but data files like `model_prices_and_context_window.json` (34K lines) inflate LOC statistics.

### What is "Code"?

A reasonable definition:

| Category | Examples | Is it code? |
|----------|----------|-------------|
| **Instructions** | Python, JavaScript, SQL | Yes - tells computer what to do |
| **Configuration** | package.json, YAML configs | Yes - parameterizes behavior |
| **Documentation** | Markdown, RST | Yes - tells humans what to do |
| **Data** | JSON datasets, CSV, fixtures | No - input/output, not instructions |
| **Generated** | Lock files, minified bundles | No - machine output |

This definition treats documentation as code (the instructions happen to be in natural language) while excluding pure data files. This aligns with modern "Docs as Code" and "Infrastructure as Code" practices.

## Decision

We will introduce a **two-dimensional classification** where every file has both a **Tier** (provenance) and a **Role** (purpose):

### Dimension 1: Tier (Provenance)

> Implemented as the `Tier` IntEnum in `supply_chain.py` (see implementation-state note above).

Unchanged from the existing supply chain model:

```python
class Tier(IntEnum):
    """Where does this file come from?"""
    FIRST_PARTY = 1   # Project's own code
    INTERNAL_DEP = 2  # Org-internal code outside the project (ADR-0041 §1)
    EXTERNAL_DEP = 3  # Third-party dependencies
    DERIVED = 4       # Build artifacts, generated output
```

### Dimension 2: Role (Purpose)

> Implemented as the `FileRole` Flag enum in `taxonomy.py` (see implementation-state note above).

New classification for content type:

```python
class FileRole(Flag):
    """What is this file for?"""
    ANALYZABLE = auto()     # Has symbols to extract (functions, classes)
    CONFIG = auto()         # Parameterizes behavior
    DOCUMENTATION = auto()  # Human-readable instructions/explanations
    DATA = auto()           # Raw information, not instructions
```

### Single Source of Truth

> Implemented as the `LanguageSpec` dataclass and `LANGUAGES` dict in `taxonomy.py` (see implementation-state note above).

All file type information consolidated in one place:

```python
@dataclass
class LanguageSpec:
    """Complete specification for a language/file type."""
    name: str
    extensions: list[str]
    roles: FileRole

    # For ambiguous extensions, filename-level overrides
    config_files: list[str] | None = None
    data_patterns: list[str] | None = None

LANGUAGES: dict[str, LanguageSpec] = {
    "python": LanguageSpec(
        name="python",
        extensions=["*.py", "*.pyi"],
        roles=FileRole.ANALYZABLE,
    ),
    "markdown": LanguageSpec(
        name="markdown",
        extensions=["*.md", "*.markdown"],
        roles=FileRole.DOCUMENTATION,
    ),
    "json": LanguageSpec(
        name="json",
        extensions=["*.json"],
        roles=FileRole.CONFIG | FileRole.DATA,  # Ambiguous, needs filename rules
        config_files=["package.json", "tsconfig.json", "composer.json"],
        data_patterns=["*_data.json", "**/fixtures/**/*.json"],
    ),
    # ... etc
}
```

### Composed Decision Functions

Replace scattered logic with composed queries:

```python
# What counts as "code" for LOC
CODE_ROLES = FileRole.ANALYZABLE | FileRole.CONFIG | FileRole.DOCUMENTATION

def should_count_loc(path: Path) -> bool:
    """Should this file contribute to LOC statistics?"""
    tier = get_tier(path)
    role = get_role(path)

    if tier == Tier.DERIVED:
        return False  # Build artifacts don't count
    if tier == Tier.EXTERNAL_DEP:
        return False  # Third-party code isn't "ours"
    if role == FileRole.DATA:
        return False  # Datasets aren't code

    return bool(role & CODE_ROLES)

def should_extract_symbols(path: Path, config: SupplyChainConfig) -> bool:
    """Should we run tree-sitter analysis on this file?"""
    tier = get_tier(path)
    role = get_role(path)

    if tier.value not in config.analysis_tiers:
        return False

    return role == FileRole.ANALYZABLE

def is_additional_file_candidate(path: Path) -> bool:
    """Should this appear in Additional Files section?"""
    tier = get_tier(path)
    role = get_role(path)

    # Only first-party/internal context files
    if tier not in (Tier.FIRST_PARTY, Tier.INTERNAL_DEP):
        return False

    # Config and docs are useful context; data is not
    return role in (FileRole.CONFIG, FileRole.DOCUMENTATION)
```

### Handling Ambiguous Extensions

JSON is the primary ambiguous case. Resolution order:

1. **Explicit config files**: `package.json` → CONFIG
2. **Data patterns**: `**/fixtures/*.json` → DATA
3. **Size heuristic**: >100KB → likely DATA
4. **Default**: CONFIG (conservative)

```python
def classify_json_file(path: Path) -> FileRole:
    spec = LANGUAGES["json"]

    if path.name in spec.config_files:
        return FileRole.CONFIG

    if any(path.match(p) for p in spec.data_patterns):
        return FileRole.DATA

    if path.stat().st_size > 100_000:
        return FileRole.DATA

    return FileRole.CONFIG
```

### Installed dependency source

> **Decided 2026-10-06 (owner); implemented** — detection, skip, disclosure and the `--trace-deps` opt-in by WI-bapal-sanok-vujoj-jisub-duzor-dojuz-misiz-fudok (`discovery.INSTALLED_DEP_RULES`, `match_content_rule`, `content_rule_prunes`); the I/O consequence (a call into untraced dependency source reports I/O unknown, never clean — [ADR-0016](0016-io-boundary-analysis.md) §7) by WI-pogar-gahij-nolun-ruhun-konij-rokup-budof-ninif.

Tier and role do not cover every skipped file. There is a third answer to "why was this file skipped?": **it is installed dependency source** — code a package manager put into the working tree — and parsing that source is **off by default**.

**1. What counts.** A directory a package manager fills from a manifest/lockfile and that is not committed: Mix `deps/`, `node_modules/`, a virtualenv's `site-packages`, a package-manager `vendor/`, Bundler's `vendor/bundle`, CocoaPods `Pods/`, and their analogues.

**2. Recognised by content, not by name.** This generalises discovery.py's virtualenv rule: a directory is a virtualenv because it holds `pyvenv.cfg` (or `bin/activate` / `Scripts/activate`), not because it is called `venv` or `env` — name-matching `env` once deleted 427 source files (425 of them first-party) across 39 corpus repos. Each ecosystem has a {candidate directory name, content marker} entry in `discovery.INSTALLED_DEP_RULES`; the name only selects which directories get the test.

| Ecosystem | Candidate | Content marker | Package names (for `--trace-deps`) |
|---|---|---|---|
| Python | `venv/`, `.venv/`, `env/` | `pyvenv.cfg`, `bin/activate` or `Scripts/activate` at the root | import names in `lib/python*/site-packages` / `Lib/site-packages` |
| Elixir (Mix) | `deps/` | sibling `mix.exs` + `mix.lock`, or `deps/*/hex_metadata.config` | `deps/<app>` |
| npm / Yarn / pnpm | `node_modules/` | sibling `package.json`, or a state file inside (`.package-lock.json`, `.yarn-integrity`, `.modules.yaml`, `.yarn-state.yml`) | `<pkg>`, `@scope/<pkg>` |
| PHP (Composer) | `vendor/` | sibling `composer.json` + `vendor/autoload.php` | `vendor/package` |
| Go modules | `vendor/` | sibling `go.mod` + `vendor/modules.txt` | module paths listed in `modules.txt` |
| Ruby (Bundler) | `vendor/bundle/` | `Gemfile.lock` beside `vendor/` + `bundle/ruby/` | gem name (`rack` for `rack-3.0.8`) |
| CocoaPods | `Pods/` | sibling `Podfile.lock` + `Pods/Manifest.lock` | pod directories |
| Git submodule | the path a `.gitmodules` entry names | a gitlink (mode `160000`) at that path in HEAD, with the directory populated | the submodule path |

*Why a submodule is installed, not committed (2026-10-15).* HEAD holds only a gitlink for a submodule: a commit id, not the files. By point 3's own test the contents are not committed. A fresh clone never has them, because `git clone` without `--recurse-submodules` leaves the directory empty, while a developer's box may have them. That is the dev-box/fresh-clone parity argument point 3 rests on. Measured over the local corpus: 35 repositories declare 362 submodule paths, and none of the 362 is populated. The skip, its disclosure and the `--trace-deps <path>` opt-in follow points 4 and 5. Implemented by WI-tiril-budah-fazaz-nahov-mazir-nufuv-dataj-bitud (`discovery.SUBMODULE_RULE`): a directory holding a `.git`, declared in the enclosing work tree's `.gitmodules`, whose HEAD entry is a gitlink (mode 160000; outside git or with no commits, a `.git` file). Its one `--trace-deps` package is the declared path.

The npm marker is the sibling manifest because npm, Yarn and pnpm all install next to the `package.json` they resolved; a bare `node_modules` with neither manifest nor state file (a resolver's test fixtures) is analysed. Bundler is keyed on `bundle` inside `vendor/`, not on `vendor/`, because a Rails `vendor/` also holds committed hand-placed code. `.dart_tool/`, `.stack-work/` and Elm 0.19's `elm-stuff/` turned out to hold tool state and build products — Dart, Stack and Elm keep package source in a per-user cache outside the repository — so they are build output under point 7, not entries here. **Not yet covered** (analysed if present): Carthage `Carthage/Checkouts`, Yarn PnP `.yarn/cache`, `bower_components`, Swift PM `.build/checkouts`, `cargo vendor` output.

**3. Committed vendored code is not installed dependency source.** Decided 2026-10-06 (owner): **a directory whose files are tracked by git is committed vendored code, never "installed"** — it stays parsed and tier-classified (tier 3 via `supply_chain.EXTERNAL_DEP_PATTERNS` / `EXTERNAL_DEP_DEEP_PATTERNS`), even if it carries a package-manager marker, as a committed `go mod vendor` tree carries `vendor/modules.txt`. Markers apply only to untracked directories. "Tracked" is read from HEAD's tree (`git ls-tree --name-only HEAD -- <dir>/`, one call per marker-positive candidate, cached) rather than the index, because reading the index with `core.fsmonitor` configured runs a program the analysed repository names (the hazard that removed `git status` from `repo_fingerprint`); a tree that is only staged therefore counts as untracked, and a tree whose only committed entries are dot-files (`vendor/.gitkeep`) does not count as committed. A non-git directory, or a repository with no commits, falls back to markers. `third_party/`, a `vendor/` or `node_modules/` with no marker, and vendored SDKs inside source trees (`EXTERNAL_DEP_DEEP_PATTERNS`, e.g. `*-sdk-go/`) are analysed and tier 3; `node_modules/` and `vendor/` below the root are tier 3 too.

**3a. Analysed is not representative (2026-10-15).** Committed vendored code (tier 3) is parsed, linked and traced, but it does not speak for the project. **Summaries that describe the project count only tiers 1 and 2:**

- the language breakdown and LOC totals (profile, sketch Overview);
- the partial-install diagnostics ("linker found N C/C++ implementations but 0 Go cgo calls").

Tier-3 code is disclosed on its own line instead, naming the directories and their size. The motivating case is hypergumbo itself. Its committed tree-sitter grammar sources (`hypergumbo_core/vendor/`: Lean, Wolfram and Circom parsers, 305,712 lines of generated C) made the profile report the project as 26% C. They also fired CGO and Ruby-FFI partial-install warnings about 53 C functions that no project code calls. Linking is unchanged: an edge from first-party code into vendored code is still found. Implemented: WI-dipag-fonik-kamok-migas-taval-fuvun-dogus-rotoz (diagnostics, `partial_install_warnings.first_party_view`), WI-kojul-mahuk-didiz-bojaj-luhod-ruvub-pafob-godum (language/LOC, `profile.languages[*].own_files` / `own_loc`), WI-pasan-hajad-zinuv-lalur-lakid-lovad-muloz-movon (disclosure line, `supply_chain_summary.vendored_analysed`).

*Linker activation is not a summary (amended 2026-10-16).* The first text of 3a also listed "which linkers count as active". It is removed. Activation decides which linkers run, and so which edges exist; it describes nothing about the project. Counting only tiers 1–2 there would contradict the sentence above: a cgo, JNI, Python-FFI, N-API or Ruby-FFI bridge from first-party code into a vendored C library would stop activating when the C exists only in vendored code, and its edges would be lost. The loss would be silent: a linker that does not activate records no run, so the map would look as if the bridge were absent (the same reason the third rejected alternative below fails). Tightening activation is refused generally by [ADR-0055](0055-linker-activation-tightening-refused.md); that ADR's criterion and its blocking precondition, a disclosure channel for a closed gate, apply to a tier-based gate as well. The cost of keeping it is real but unsized: on hypergumbo's own survey of 2026-10-15, napi-linker (8.4 s) and ruby-ffi-linker (9.3 s) ran only because the vendored grammars contain C and found nothing. That is one repository; ADR-0055 sizes such decisions on a polyglot cohort.

**3b. The analysed repository's declarations never skip committed code (2026-10-15).** A `.gitattributes` `linguist-vendored` / `linguist-generated` attribute, or similar project configuration, does not cause hypergumbo to skip a committed file. `linguist-vendored` is a control for GitHub's language bar, not a claim about where code came from. Across the local corpus (`~/ALL_REPOS/whole_bunch_of_repos`, 10–14 repositories use it), it marks first-party code: kserve's `*.sh`, litellm's `*.ipynb`, emscripten's `test/*` and `system/`, envoy's `certificates.inc`. Skipping on it would be a silent wrong exclusion, the failure point 7's fallback exists to prevent. It would also let the analysed repository decide, through a file written for a different tool, what hypergumbo reads. Under [ADR-0045](0045-user-config-and-backend-trust.md) a project setting is written in `.hypergumbo.toml`; a `.gitattributes` written for GitHub is not a hypergumbo setting. The user's own `--exclude` and user configuration remain the way to skip committed code.

*Rejected alternatives (2026-10-15).*

- *Skip committed vendored code by `linguist-vendored`.* Rejected under 3b: it marks first-party code in the corpus.
- *Skip committed vendored code by a content signature.* For example, a tree-sitter grammar directory recognised by `grammar.js` + `src/parser.c`. Rejected for three reasons:
  - It reverses the 2026-10-06 ruling that a committed `go mod vendor` tree is analysed.
  - The proposed signature does not match hypergumbo's own `vendor/`, which has no `grammar.js`.
  - Skipping vendored C would cut cgo edges from first-party Go into it.
- 3a removes the symptom that motivated both alternatives, vendored code dominating the summaries, without discarding any analysis.

**4. Disclosed when something was skipped.** A stderr line, a line in the sketch's Overview, and `supply_chain_summary.installed_deps_skipped` beside `derived_skipped` — for example *"Installed dependency source not analysed: deps/ (81 Mix packages). Calls into them appear as external stubs. To trace into them: --trace-deps <pkg,...|all>"*. Nothing is printed when nothing was skipped. The JSON bucket is `{dirs, packages, entries: [{path, ecosystem, packages, traced}]}` (entries capped at 10). Directory sizes are not reported: measuring one walks the tree the skip exists to avoid.

**5. Opt-in, per package.** `--trace-deps <pkg,...|all|none>` on `sketch`, `survey` and `slice`, and the `trace_deps` key in the [ADR-0045](0045-user-config-and-backend-trust.md) user/project configuration (the flag outranks it for one command). The setting is part of the results-cache key (the state segment gains `-deps-all` / `-deps-<hash>`). Per package, only the named packages inside an installed directory are analysed. Traced dependency source is classified tier 3, exactly as dependency source was classified before; where no path pattern reaches it (a virtualenv, a nested `server/deps/`), through the same content test.

**6. What stays on by default** (cheap, and needed for the default output): boundary stubs for calls into dependencies (unresolved external edges, [ADR-0037](0037-edge-resolution-semantics.md)); manifest and lockfile reading (`directness`, [ADR-0041](0041-supply-tier-purity.md)); tier-3 classification of committed vendored code (point 3); tier-4 derived detection.

**7. Tool build output is build output, not dependency source — and it too is recognised by the tool that writes it, not by its name.** Amended 2026-10-12 (owner direction, INV-hagof-kuzun-havub-zihaz-fudin-rihop-vafaf-dulud): generated output — build, documentation-site and coverage-report directories — is excluded with no opt-in, **recognised per tool by evidence** (`discovery.BUILD_OUTPUT_RULES`, the same {candidate name, marker} shape as point 2), and **disclosed**.

*Why.* `dist`, `build`, `_build`, `out`, `target`, `site`, `_site`, `coverage`, `htmlcov`, `lcov-report`, `TestResults` (and four more report names) were bare `DEFAULT_EXCLUDES` entries matched at any depth. Measured 2026-10-12 over the 273-repo local corpus, that pruned 354 directories; with evidence required, 25 are pruned and the other 329 (5,224 files, 2,796 of them source) are analysed again — qemu's root `target/` (653 C files of CPU emulation), grafana's `pkg/build` (350), next.js's `packages/next/src/build` (259), spring-boot's `buildSrc/.../build`, gitlab's `lib/gitlab/ci/build` and `.../coverage`, plausible's `lib/plausible/site/` (the filed repro). Hugo's `public/` was the first instance of the same error (removed in v0.5.1: Airflow `routes/public/`, 109 API handlers).

*Evidence.* A rule claims a candidate directory when one of these holds (the name only selects the rules tried; first match wins and names the tool in the disclosure):

| Kind | Rules |
|---|---|
| Producing tool's manifest beside it | `site/` ← `mkdocs.yml` whose `site_dir` is unset or names it; `_site/` ← Jekyll `_config.yml`, Eleventy config, `_quarto.yml`; `_build/` ← `mix.exs`, `rebar.config`, `dune-project`, Sphinx `conf.py`, Jupyter Book `_toc.yml`; `build/` ← Gradle build/settings script, `pubspec.yaml` (Flutter), `gleam.toml`, Docusaurus config, Sphinx `source/conf.py`; `target/` ← `Cargo.toml`, `pom.xml`, `build.sbt` (and sbt's `project/target`), `project.clj` / `build.boot`, `build.clj`; `out/` ← `next.config.*` |
| Declared output | `dist`/`build`/`out` named by a `package.json` entry point (`main`, `module`, `browser`, `types`, `bin`, `exports`, …; never `files`), a `tsconfig*.json` `outDir`, or an `action.yml` `runs.main`/`pre`/`post` |
| A marker the tool writes into its output | `CMakeCache.txt`, `meson-private/`, `build.ninja` (or `out/<config>/args.gn`), setuptools `lib.*`/`bdist.*`/`temp.*`, only distribution archives in a Python `dist/`, Next.js `_next/`; for the coverage names, a report file (`lcov.info`, `coverage-final.json`, Cobertura/Clover/JaCoCo XML, SimpleCov `.resultset.json`, coverage.py's HTML script, a `.trx`, an `lcov-report/`); for any candidate, `CACHEDIR.TAG` or a `.gitignore` that ignores everything (uncommitted only — committed files there were force-added) |
| Supporting evidence | `dist`/`build`/`out` beside a `package.json` with no declared entry point, only when **positively untracked** (HEAD has commits and holds nothing under it) |

A sibling-manifest rule never claims a directory that holds a project manifest of its own (dart-lang/build's `build/` package; a Maven module named `coverage/`).

*Committed output is still output.* Unlike point 3, being committed does not exempt a directory from the build-output rules — projects commit generated output (a GitHub Action commits the `dist/index.js` its `action.yml` runs). Git is consulted in two rows, each where the marker alone is too weak. A self-ignoring `.gitignore` claims a directory only when it is not committed, because files committed there were force-added on purpose; any other rule may still claim it. The supporting-evidence row asks git because a `package.json` alone is too weak (VS Code's committed `extensions/*/build/` are build scripts), and "untracked" is evidence only when git positively says so — outside git, or with no commits, it counts for nothing.

*Fallback.* **A directory with an output name and no tool evidence is analysed.** A wrong exclusion is silent and removes findings (every instance above); a wrong inclusion is visible — extra symbols, extra time — and `--exclude <name>` removes it for one run. Tier 4 follows the same rules: `supply_chain` no longer classifies root `dist/`/`build/`/`out/`/`target/` as derived by name, but asks `match_content_rule`, so a directory the walk analyses is not then dropped as tier 4.

*Disclosure.* `FileIndex.build` records each pruned output directory; a stderr line, a line in the sketch's Overview and `supply_chain_summary.generated_output_skipped` (`{dirs, entries: [{path, tool}]}`, entries capped at 10) name the directory and the evidence — *"Generated output not analysed: target/ (Cargo build), site/ (MkDocs site). Recognised by the tool that writes each (ADR-0004)."* — so a wrong exclusion can be seen. Nothing is printed when nothing was pruned.

*What stays name-based.* VCS, caches and tool state whose names are dot-prefixed or coined by the tool for itself (`.git`, `__pycache__`, `.mypy_cache`, `.pytest_cache`, `.tox`, `.cache`, `.terraform`, `.eggs`, `*.egg-info`, `.elixir_ls`, `.nyc_output`, `.c8_output`, `.jest`, …), single report and lock files, and hypergumbo's own outputs: no project names a source directory `__pycache__`, so the name is the evidence. The earlier content rules — Mix `cover/` beside `mix.exs`, Phoenix `priv/static/assets/` under a `mix.exs` project, `.dart_tool/` beside `pubspec.yaml`, `.stack-work/` beside `stack.yaml`, `elm-stuff/` beside `elm.json` — are entries of the same registry (now also disclosed, and no longer exempted when committed).

**Not yet covered** (analysed if present): `mix phx.digest` output (`priv/static/*-<hash>.*` beside `cache_manifest.json`) is per-file rather than per-directory; a custom MkDocs `site_dir`, Jekyll `destination` or Cargo `target-dir` with a non-conventional name; Hugo `public/`; a JS bundle in `dist/` that nothing declares and that is committed.

**8. Absence is never read as cleanliness.** When dependency source is not traced, `io-boundaries` and `verify-claims` report a call into it as *untraced dependency, I/O unknown* — [ADR-0016](0016-io-boundary-analysis.md) §7. The *dependency* label is available where the language's standard library is enumerated (Python), and elsewhere the call is reported as a dependency or runtime module, I/O unknown.

**Why.**

- **A dev box and a fresh clone of the same commit should give the same default output.** A pristine clone has no `deps/`; a developer's checkout of the same commit had 40 MB / 81 Mix packages of it. Committed code is in both, which is why point 3 keys on git.
- **Cost.** Parsing that `deps/` got a 6 GB VM OOM-killed.
- **The real use of dependency source is targeted** — "what does `chain.run` actually send to the LLM API?" — so the opt-in is per package rather than all-or-nothing.

**Consequence of point 3, stated.** `node_modules` and `vendor` used to be bare names in `DEFAULT_EXCLUDES`, which silently dropped a committed `vendor/` (19,251 `vendor` files measured below the root across the corpus, kata-containers' committed `src/runtime/vendor` among them). Those trees are now analysed as tier 3, which costs analysis time on repositories that commit their vendor tree; `--exclude vendor` restores the old behaviour for one run.

## Consequences

### Positive

* **Single source of truth**: One `LANGUAGES` dict replaces 5+ scattered definitions.

* **Correct LOC counts**: Data files no longer inflate statistics. A repo with 34K lines of pricing data won't report 34K extra "lines of code."

* **Clear semantics**: "Why was this file skipped?" has an answer: its tier, its role, or — for a package-manager-filled directory nobody opted into — that it is installed dependency source, or — for a directory a tool's evidence marks as its output — that it is generated output (§"Installed dependency source" points 4 and 7); both are disclosed rather than skipped silently.

* **Extensible**: Adding a new language requires one entry in `LANGUAGES`, not edits to multiple files.

* **Composable**: Tier and Role are orthogonal; decisions compose cleanly.

### Negative

* **Migration effort**: Existing code must be refactored to use the new taxonomy. This is a significant change touching profile.py, sketch.py, discovery.py, and supply_chain.py.

* **Heuristics for ambiguous files**: The JSON disambiguation logic (patterns, size thresholds) may misclassify edge cases. This is inherent to the problem, not the solution.

* **Learning curve**: Contributors must understand two dimensions instead of one. However, the dimensions are intuitive (where from? what for?) and the composed functions hide complexity.

## Migration Path

> Status update (see implementation-state note above): the core taxonomy (`Tier`, `FileRole`, `LanguageSpec`, `LANGUAGES`, `CODE_ROLES`) has landed in `supply_chain.py` / `taxonomy.py`. The phase numbering below is retained as the original plan of record.

1. **Phase 1**: Add `FileRole` enum and `LanguageSpec` dataclass alongside existing code. Implement `get_role()` function.

2. **Phase 2**: Replace `LANGUAGE_EXTENSIONS` with derivation from `LANGUAGES`.

3. **Phase 3**: Replace `SOURCE_EXTENSIONS` with `role == ANALYZABLE` check.

4. **Phase 4**: Replace `ADDITIONAL_FILES_EXCLUDES` patterns with role-based filtering.

5. **Phase 5**: Remove deprecated constants, update tests.

Each phase can be a separate PR with tests verifying behavioral equivalence.

## References

* Existing supply chain classification: `src/hypergumbo/supply_chain.py`
* Current language extensions: `src/hypergumbo/profile.py`
* Current source extensions: `src/hypergumbo/sketch.py`
* Industry tools for comparison: cloc, tokei, sloccount
