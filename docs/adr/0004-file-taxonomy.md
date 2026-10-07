<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# 4. File Taxonomy: Tier and Role Classification

Date: 2025-01-14
Status: Accepted

> Edited in place per [`README.md` §"ADR lifecycle"](README.md#adr-lifecycle-status-supersession-and-excision); git history holds earlier text. The 2026-10-06 owner decision added §"Installed dependency source" (a third answer to "why was this file skipped?"; implemented — the skip, disclosure and `--trace-deps` opt-in by WI-bapal-sanok-vujoj-jisub-duzor-dojuz-misiz-fudok, the I/O-unknown report for calls into untraced source by WI-pogar-gahij-nolun-ruhun-konij-rokup-budof-ninif), corrected the implementation-state note on symbol extraction, and revised the matching Consequences line. A second owner decision the same day settled that section's open question: a committed directory is never installed dependency source (point 3).
>
> Implementation state: the Decision/Migration Path below read as a forward-looking proposal, but the core taxonomy has landed. `Tier` (IntEnum: FIRST_PARTY/INTERNAL_DEP/EXTERNAL_DEP/DERIVED) lives in `packages/hypergumbo-core/src/hypergumbo_core/supply_chain.py`; `FileRole` (Flag: ANALYZABLE/CONFIG/DOCUMENTATION/DATA), the `LanguageSpec` dataclass, the `LANGUAGES` dict, and the composed `CODE_ROLES` constant all live in `packages/hypergumbo-core/src/hypergumbo_core/taxonomy.py`. (Role flags on `FileClassification` — `is_test`/`is_example`/`is_config`/`is_generated` — are independent boolean axes per WI-jobuj/WI-rigun-patuz and INV-tisid.) The `should_extract_symbols`/`config.analysis_tiers` knob in the Decision-section pseudocode below was **not** implemented, and symbol extraction is **not gated by tier at all**. A file pruned by discovery's excludes (`DEFAULT_EXCLUDES`, the content rules for installed dependency source and build output — §"Installed dependency source" — and `--exclude`) is never parsed; every other file is parsed, its symbols are tier-stamped, and a post-parse filter in `cli.run_survey` drops symbols above the effective tier ceiling — 3 by default, which removes tier 4; `--max-tier` / `--first-party-only` lower it. Tier-3 files that discovery does not prune are therefore analysed by default. Measured 2026-10-06 on a three-file fixture (`mix.exs`; `lib/app.ex` whose `App.hi` calls `Foo.greet`; `deps/foo/lib/foo.ex`), before §"Installed dependency source" was implemented: `Foo` and `Foo.greet` were emitted as tier-3 symbols (`reason: in deps/`) and `App.hi -> Foo.greet` was a resolved `calls` edge. With a `mix.lock` beside `deps/` that is now what `--trace-deps foo` (or `all`) produces; by default `deps/` is skipped and the call is an unresolved stub to `Foo.greet`. The vestigial `SupplyChainConfig.analysis_tiers` field (round-tripped but never read) was removed (WI-josad); the pseudocode is preserved as the original proposal. A consumer that needs a language's file extensions or a file's language reads `LANGUAGES` through `extension_globs` / `extension_suffixes` / `get_language` (and `js_ts_language_for_path` / `grammar_for_path` for the JS/TS family) rather than holding a private literal: the JS/TS-scanning linkers and the DDG language specs do, and two gates (`test_js_ts_extension_single_source.py`, `test_js_ts_ddg_extension_reach.py`) fail when one re-grows a private list (WI-fovus, WI-hizon, WI-pokij).
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
    INTERNAL_DEP = 2  # Internal libraries, examples
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

The npm marker is the sibling manifest because npm, Yarn and pnpm all install next to the `package.json` they resolved; a bare `node_modules` with neither manifest nor state file (a resolver's test fixtures) is analysed. Bundler is keyed on `bundle` inside `vendor/`, not on `vendor/`, because a Rails `vendor/` also holds committed hand-placed code. `.dart_tool/`, `.stack-work/` and Elm 0.19's `elm-stuff/` turned out to hold tool state and build products — Dart, Stack and Elm keep package source in a per-user cache outside the repository — so they are build output under point 7, not entries here. **Not yet covered** (analysed if present): Carthage `Carthage/Checkouts`, Yarn PnP `.yarn/cache`, `bower_components`, Swift PM `.build/checkouts`, `cargo vendor` output.

**3. Committed vendored code is not installed dependency source.** Decided 2026-10-06 (owner): **a directory whose files are tracked by git is committed vendored code, never "installed"** — it stays parsed and tier-classified (tier 3 via `supply_chain.EXTERNAL_DEP_PATTERNS` / `EXTERNAL_DEP_DEEP_PATTERNS`), even if it carries a package-manager marker, as a committed `go mod vendor` tree carries `vendor/modules.txt`. Markers apply only to untracked directories. "Tracked" is read from HEAD's tree (`git ls-tree --name-only HEAD -- <dir>/`, one call per marker-positive candidate, cached) rather than the index, because reading the index with `core.fsmonitor` configured runs a program the analysed repository names (the hazard that removed `git status` from `repo_fingerprint`); a tree that is only staged therefore counts as untracked, and a tree whose only committed entries are dot-files (`vendor/.gitkeep`) does not count as committed. A non-git directory, or a repository with no commits, falls back to markers. `third_party/`, a `vendor/` or `node_modules/` with no marker, git submodules, and vendored SDKs inside source trees (`EXTERNAL_DEP_DEEP_PATTERNS`, e.g. `*-sdk-go/`) are analysed and tier 3; `node_modules/` and `vendor/` below the root are tier 3 too.

**4. Disclosed when something was skipped.** A stderr line, a line in the sketch's Overview, and `supply_chain_summary.installed_deps_skipped` beside `derived_skipped` — for example *"Installed dependency source not analysed: deps/ (81 Mix packages). Calls into them appear as external stubs. To trace into them: --trace-deps <pkg,...|all>"*. Nothing is printed when nothing was skipped. The JSON bucket is `{dirs, packages, entries: [{path, ecosystem, packages, traced}]}` (entries capped at 10). Directory sizes are not reported: measuring one walks the tree the skip exists to avoid.

**5. Opt-in, per package.** `--trace-deps <pkg,...|all|none>` on `sketch`, `survey` and `slice`, and the `trace_deps` key in the [ADR-0045](0045-user-config-and-backend-trust.md) user/project configuration (the flag outranks it for one command). The setting is part of the results-cache key (the state segment gains `-deps-all` / `-deps-<hash>`). Per package, only the named packages inside an installed directory are analysed. Traced dependency source is classified tier 3, exactly as dependency source was classified before; where no path pattern reaches it (a virtualenv, a nested `server/deps/`), through the same content test.

**6. What stays on by default** (cheap, and needed for the default output): boundary stubs for calls into dependencies (unresolved external edges, [ADR-0037](0037-edge-resolution-semantics.md)); manifest and lockfile reading (`directness`, [ADR-0041](0041-supply-tier-purity.md)); tier-3 classification of committed vendored code (point 3); tier-4 derived detection.

**7. Tool build output is build output, not dependency source.** Excluded with no opt-in and no disclosure: Mix `_build/` and `.elixir_ls/` by name (`DEFAULT_EXCLUDES`); and, by content (`discovery.BUILD_OUTPUT_RULES`, uncommitted only, like point 3), Mix `cover/` beside `mix.exs`, Phoenix `priv/static/assets/` under a `mix.exs` project, `.dart_tool/` beside `pubspec.yaml`, `.stack-work/` beside `stack.yaml`, `elm-stuff/` beside `elm.json`. **Not yet covered:** `mix phx.digest` output (`priv/static/*-<hash>.*` beside `cache_manifest.json`) is per-file rather than per-directory.

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

* **Clear semantics**: "Why was this file skipped?" has an answer: its tier, its role, or — for a package-manager-filled directory nobody opted into — that it is installed dependency source (§"Installed dependency source"), which is disclosed rather than skipped silently.

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
