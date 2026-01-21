# Changelog

All notable changes to hypergumbo are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

- Released **tool** is at: v1.0.0
- Released **schema** is at: v0.2.0

This changelog tracks the **tool version** (package releases). The **schema version** (output format) is tracked separately in `schema.py` as `SCHEMA_VERSION`. The schema version only changes when the JSON output format has breaking changes.

## [Unreleased]

### Added
- **YAML-based main() function detection (ADR-0003 v1.2.x):** Language-level main() entry points
  are now detected via data-driven YAML patterns in `main-functions.yaml`, not hardcoded logic.
  Supports Go, Java, Python, C, C++, Rust, C#, Kotlin, Swift, and Dart. Detected main functions
  appear in the entrypoints list with confidence 0.80 (lower than framework patterns at 0.95).
- **Pattern system: `symbol_name` and `language` fields:** Extended the Pattern class to support
  matching symbols by name (regex) and filtering by language. Enables language-convention patterns
  like main() detection that need to match by name+kind+language, not just decorators or base classes.
- **YAML-based test function detection (ADR-0003 v1.2.x):** Test functions across 10+ languages/frameworks
  are now detected via `test-frameworks.yaml`. Supports pytest, unittest, Go testing, JUnit, xUnit,
  NUnit, MSTest, Rust #[test], RSpec, PHPUnit, XCTest, and jest/mocha/vitest. Patterns use naming
  conventions (test_*, Test*, test*) or decorators (@Test, [Fact], #[test]).
- **YAML-based language convention patterns:** Non-framework domain metadata is now enriched via YAML:
  - `language-conventions.yaml`: CUDA kernels (__global__/__device__), WGSL shaders (@vertex/@fragment/@compute),
    COBOL programs/sections, LaTeX document structure, Starlark build rules/macros
  - `config-conventions.yaml`: NPM dependencies/scripts, Maven dependencies/modules, Android permissions/components,
    Cargo dependencies/workspaces, Poetry dependencies, TypeScript project references
- **Slice path suffix matching:** The `--entry` flag now supports relative paths that match as
  suffixes of absolute paths. For example, `--entry src/main.go` will match a symbol with path
  `/home/user/repo/src/main.go`. Previously required exact path match.
- **Auto-run analysis for query commands:** Commands `search`, `routes`, `explain`, `symbols`,
  `test-coverage`, and `slice` now automatically run `hypergumbo run` if no cached results exist.
  Previously these commands failed with "Run 'hypergumbo run' first" - now they seamlessly generate
  the behavior map on demand. Shows `[hypergumbo] No cached results found, running analysis...`
  when auto-running.
- **Auto-discovery of cached results:** Query commands automatically discover behavior maps from the
  cache directory (`~/.cache/hypergumbo/<fingerprint>/results/<state>/`) or fall back to repo root.
  This fixes the mismatch where `hypergumbo run` saved to cache but query commands looked in repo root.
- **Artifact location reporting:** All commands now report where results came from at the end of
  execution. Shows "[cached]" prefix for pre-existing files vs freshly generated ones. Example:
  `[hypergumbo search] Using 1 cached` followed by the file path.
- **Symbol-specific slice output naming:** `hypergumbo slice` now generates output filenames that
  include the entry symbol name when using defaults (e.g., `slice.main.json` instead of `slice.json`).
  This prevents accidental overwrites when slicing different symbols in the same session.
- **README-first hybrid ranking for Additional Files:** The Additional Files section now uses a
  smarter ordering algorithm:
  - README always appears first (truncated if it exceeds the token budget)
  - Remaining files selected via round-robin from: README-linked files, similarity-ranked files,
    and centrality-ranked files
  - Dynamic truncation based on median token count of already-selected files (500-token floor)
- **Multi-format README link extraction:** Extracts internal links from READMEs in multiple formats:
  - Markdown: inline `[text](url)` and reference-style `[text][ref]` with `[ref]: url` definitions
  - Org-mode: `[[url][text]]` and `[[url]]` formats, plus `file:path` scheme
  - RST: `` `text <url>`_ `` inline and `.. _text: url` reference links
  - AsciiDoc: `https://url[text]`, `link:url[text]`, and `{attr}[text]` with `:attr: url` definitions
- **Forge URL resolution for README links:** Resolves links to GitHub/GitLab/Codeberg URLs pointing
  to the same repo, including raw content URLs and GitHub/GitLab Pages URLs.
- **`-x/--exclude-tests` flag for sketch:** Exclude test files from all sketch sections (symbols,
  source files, LOC counts). Shows `[IGNORING TESTS]` markers on section headers. The test LOC
  counter reveals if any test files slip through the filter.
- **"How Representative Is This Sketch?" table:** When using a token budget, displays a Rich table
  showing what fraction of the codebase's importance is captured in each section. Compares the
  requested budget against a double-budget sketch. Shows confidence mass for framework concepts
  (Entry Points, Data Models) and symbol mass for file-based sections (Key Symbols, Source Files,
  Additional Files).

### Fixed
- **Sketch file content truncation:** Files in Source Files Content and Additional Files Content
  sections are now budgeted accurately, including START/END markers and code fences (~130 chars
  overhead per file). Previously files could be truncated mid-content.
- **Sketch files now end with newline:** Standard text file convention.
- **`-x` flag showing 0 files/LOC for non-code repos:** Repos primarily containing markdown, JSON,
  or YAML files now display correct file and LOC counts when using `-x/--exclude-tests`. Previously
  showed "0 files" and "~0 LOC" because the function was recalculating totals from an empty
  source files list.
- **Unified test detection between Overview and Tests sections:** Both sections now use the same
  `_is_test_path` function for test classification and `SOURCE_EXTENSIONS` for file discovery.
  Previously the Overview counted config files like Makefile as tests (showing "6 test files")
  while the Tests section used different patterns (showing "No test files detected" or "4 test
  files"). Now both agree on test file counts.
- **Added `tests.py` and `*_spec.rb` to test detection:** The `is_test_path` function now
  recognizes Python's single-file test module (`tests.py`) and Ruby RSpec files (`*_spec.rb`)
  as test files.
- **Structure section uses tree format with `-x` flag:** When all source files are tests and
  `-x` excludes them, the Structure section now uses the modern tree format (with `├──` and
  `└──` characters) instead of falling back to the deprecated bullet-list format.
- **Representativeness table shows with `-x` flag:** The "How Representative Is This Sketch?"
  table now appears when using `-x/--exclude-tests`, showing coverage for Additional Files
  sections. Previously the table was hidden when all source symbols were excluded.
- **Removed misleading "Coverage requires execution" message:** When coverage cannot be estimated
  (e.g., no production functions to measure), the Tests section now simply omits the coverage
  line instead of showing a confusing hint about running tests. Coverage estimation is shown
  when available; otherwise, only the test count and framework detection are displayed.
- **Overview totals match `-x` semantics:** When using `-x/--exclude-tests`, the Overview section
  now shows adjusted totals (e.g., "2 files (2 non-test + 0 test)") instead of showing all files
  with the `[IGNORING TESTS]` marker. The total now equals the non-test count, making the semantics
  clear: tests are truly being ignored in the counts.
- **Structure section filters test files with `-x` flag:** Directory item counts in the Structure
  section now exclude test source files when `-x` is used. Config/documentation files (like
  `Makefile`, `Cargo.toml`) inside test directories are still counted since they're not test code.
- **C/C++ analyzers prefer definitions over declarations:** Call edges now point to function
  definitions (`.c`, `.cpp` files) instead of declarations (`.h` files). This fixes transitive
  test coverage estimation for C/C++ codebases where the previous behavior could truncate the
  call graph at header file declarations (which have no outgoing call edges).
- **Elevator pitch truncation respects sentence boundaries:** The README description in the
  sketch header now prefers to truncate at sentence-ending punctuation (. ! ? :) instead of
  cutting off mid-sentence. Falls back to word boundary if no sentence boundary is found
  within a reasonable range. Minimum 10 characters to avoid single-word sentences.
- **Minimum chunk size for license files in semantic search:** License/copying files now
  enforce a minimum chunk size (80 chars) during config extraction to prevent undersized
  chunks like heading-only fragments (e.g., `> Preamble`) from appearing in the "Additional
  context (semantic)" section. Non-license config files retain the ability to have short
  chunks since they often contain meaningful short lines (commands, flags, identifiers).
- **Embedding-based README extraction handles soft line breaks:** READMEs with paragraphs spanning
  multiple physical lines (Markdown soft breaks) now extract complete sentences instead of cutting
  off mid-sentence (e.g., "A container init that is so simple it's effectively brain-dead. This is a"
  now correctly continues to "...rewrite of initrs in C..."). Headers selected during embedding
  search are expanded with their following content paragraphs, then the header markup is stripped
  from the final description so the elevator pitch reads naturally.
- **Structure section shows all root directories:** The tree structure now ensures directory
  diversity by including one representative file from each root-level directory. Uses separate
  limits for root-level files (max 5) vs directories (max 10) to prevent many root configs from
  crowding out directory representation. After processing source files by centrality, also scans
  for additional files (CONFIG/DOCUMENTATION) in unrepresented directories (max 5 additional).
  Previously, directories with lower-ranked files would be hidden under "[and N other items]".
- **Structure section shows root-level files for flat repos:** When a repository has only
  root-level files and no subdirectories (or when `-x` excludes all subdirectory contents),
  the Structure section now shows root-level source and config files instead of "(empty)".
  Previously, the fallback tree renderer only showed directories and would display "(empty)"
  for repos like `qemu-sgabios` that have all source files at the root level.
- **Unified sketch output with and without `-t` flag:** Running `hypergumbo .` now produces
  identical output to `hypergumbo . -t 4000` (the default budget). Previously, without `-t`,
  the Structure section used an old bullet-list format and the Representativeness Table was
  missing. Now both modes use the tree format and include all sections.
- **Consistent item counting in Structure section:** The fallback Structure format (used for
  small token budgets) now counts all non-excluded root items, matching the full tree format.
  Previously, it only counted source/config files, showing "[and 8 other items]" when there
  were actually 20 hidden items.
- **Representativeness Table shows correct token budget for small sketches:** When using a
  very small token budget (e.g., `-t 500`), the table now shows the correct budget (e.g., "500t")
  instead of "0t". Previously, the early-return code path for tiny budgets didn't set the
  token_budget stat before returning.
- **Additional Files representativeness uses mention centrality:** The symbol mass metric for
  Additional Files now uses mention centrality (how many high-connectivity symbols are mentioned
  in the documentation) instead of symbol definitions (which are always 0 for doc/config files).
  This gives a meaningful measure of how much of the codebase's important symbols are documented.
- **`explain --with-source` output ordering:** Caller sources now appear immediately after the
  "Called by" list, and callee sources after the "Calls" list (previously all sources were grouped
  at the end). Budget pruning now correctly omits items one-at-a-time in priority order (module-level
  first, then ascending in-degree) until the total fits, rather than greedy selection which could
  skip important items due to size. Separate omission messages for callers vs callees.
- **Compact mode produces valid induced subgraphs:** Fixed `--compact` and tiered behavior maps to
  produce structurally valid output that downstream commands can use. Three critical bugs were fixed:
  1. **Edge filter changed from OR to AND:** Previously kept edges where *either* endpoint existed;
     now only keeps edges where *both* endpoints exist. This was causing 99%+ of compact file size
     to be wasted on unusable edges with dangling references (e.g., tensorflow compact was 248MB
     but only 0.05MB of edges were actually usable).
  2. **Entrypoints filtered to resolvable IDs:** Entrypoints are now filtered to only include those
     whose `symbol_id` exists in the included nodes. Previously all entrypoints were copied verbatim,
     causing `slice --list-entries` to report "No entrypoints detected" on compact output.
  3. **Force-include entrypoints in selection:** Entrypoint symbols are now automatically included
     in compact/tiered selection (controlled by `force_include_entrypoints` parameter, default True).
     This ensures semantic anchors (main functions, HTTP routes, CLI commands) are preserved even
     if they have low centrality. Remaining budget is filled with highest-centrality symbols.
- **Connectivity-aware selection for compact mode:** Compact mode now uses a greedy frontier-based
  algorithm that prioritizes nodes which bridge disconnected components. This replaces the previous
  centrality-only selection which produced isolated nodes with no edges between them. Key improvements:
  - **Component-growth scoring:** Nodes that merge disconnected entrypoints score highest
  - **Edge-count secondary:** Ties broken by number of edges added to the selected set
  - **Centrality fallback:** Original importance used as final tiebreaker
  - **Performance:** O(k·frontier·degree) with Union-Find, handles tensorflow (154k symbols, 505k edges)
    in ~6 seconds
  - **Results:** Django compact output improved from 21 edges to 86 edges (4x) with same 200-node budget
  - **Default enabled:** Connectivity-aware is now the default; use `--no-connectivity` to disable
- **Python call graph: imported class method calls and parameter type inference:** The Python
  analyzer now detects two previously-missed call patterns:
  1. **Imported class method calls:** `Item.model_validate(data)` where `Item` is imported via
     `from models import Item` now produces a calls edge to `Item.model_validate`.
  2. **Parameter type annotations:** `def handler(db: Database)` now enables resolution of
     `db.add()` and `db.commit()` calls to `Database.add` and `Database.commit` methods.
  Previously, type inference only worked for constructor-assigned variables (`db = Database()`).
  This improves call graph completeness for FastAPI/Flask patterns that use dependency injection
  and Pydantic model classmethods. Note: only works when the target class/method is defined in
  the analyzed codebase (not inherited from external libraries like Pydantic).

### Changed
- **Section header renames for clarity:**
  - "Source Content" → "Source Files Content"
  - "Additional File Content" → "Additional Files Content"
- **Overview always shows test/non-test breakdown:** The Overview section now consistently shows
  the file and LOC breakdown (e.g., "43 files (43 non-test + 0 test)") whether or not the `-x`
  flag is used. Previously the compact format was shown when there were no test files.
- **Tests section always present:** The `## Tests` section is now always included in the sketch,
  showing "No test files detected" when no tests are found. Previously the section was omitted
  entirely for repos without tests.

### Added (continued)
- **ADR-0004: File Taxonomy (Tier + Role):** Two-dimensional file classification system implemented.
  The taxonomy module provides a single source of truth for file type information:
  - `FileRole` enum: ANALYZABLE, CONFIG, DOCUMENTATION, DATA
  - `LanguageSpec` dataclass with 75+ languages
  - `is_code()`, `is_analyzable()`, `is_additional_file_candidate()` helpers
  - JSON disambiguation (config vs data) using filename patterns and size heuristics
  - Replaces scattered constants (LANGUAGE_EXTENSIONS, SOURCE_EXTENSIONS) with unified registry
  - Reduces ADDITIONAL_FILES_EXCLUDES from 100+ patterns to ~30 boilerplate patterns
- **`explain --with-source` flag:** Shows source code for queried symbol, its callers, and callees.
  Module-level calls show only the single call line. Supports `-t/--tokens` to limit output.
  (Renamed from `--verbose` for consistency with `sketch --with-source`.)
- **`sketch --with-source` flag:** Appends full source file contents after the regular sketch,
  ordered by symbol importance density. Respects token budget, skips files under 5 LOC.
- **Common Lisp analyzer:** Full support for `.lisp`, `.lsp`, `.cl`, and `.asd` files. Detects
  functions (defun/defmacro/defmethod/defgeneric), classes (defclass/defstruct), variables
  (defvar/defparameter/defconstant), and packages (defpackage). Handles both lowercase and
  uppercase forms (Common Lisp is case-insensitive). Includes cross-file call resolution.
- **LLVM IR analyzer:** Support for `.ll` files (LLVM Intermediate Representation). Detects
  function definitions, function declarations (external functions), global variables, and
  function call edges. Useful for analyzing compiler and toolchain projects.
- **Improved README extraction:** Case-insensitive README finding (fixes `Readme.md` detection),
  filters link reference definitions and GitHub callout syntax that were matching probes incorrectly.
- **`hypergumbo test-coverage` command:** Static analysis test coverage estimation. Identifies
  "test-dense" functions (high tests/LOC ratio - may indicate redundant tests) and "cold spots"
  (untested functions). Uses call graph analysis without executing any code. Language agnostic.
- **`--help --all` flag:** Shows comprehensive help including all subcommand documentation at once.
- **Estimated coverage in sketch:** The Tests section now shows estimated coverage
  (e.g., "~35% estimated coverage (460/1318 functions called by tests)") even without a token
  budget. Previously required `-t` flag; now runs analysis for coverage by default.
- **Embedding-based README description extraction:** Sketch now uses semantic similarity to extract
  project descriptions from READMEs. Compares each line against probe embeddings of known project
  mission statements, using a sliding window approach to find the best consecutive description lines.
  Falls back to heuristic parsing if embeddings unavailable.
- **Pre-computed probe embeddings:** Probe embeddings for README extraction and config analysis are
  now pre-computed and stored as base64-encoded float16 arrays, avoiding ~2-3s startup cost.
- **README extraction debug mode:** `extract_readme_description_embedding(debug=True)` returns
  `ReadmeExtractionDebug` object with k-value scores, timing, and early stopping info.
- **`--progress` flag for sketch:** Shows progress indicator with ETA to stderr during sketch
  generation (e.g., `[45%] Extracting config... ETA 23s`).
- **`--readme-debug` flag for sketch:** Shows README extraction debug info (k-scores, timing,
  early stopping) to stderr. Useful for understanding how the sliding window extraction works.
- **Separate test LOC in Overview:** The overview now shows test and non-test counts on separate
  lines with aligned columns for easy comparison:
  ```
  314 files    (186 non-test + 128 test)
  ~115,510 LOC (~59,306 non-test + ~56,204 test)
  ```
- **Artifact summary after `hypergumbo run`:** The run command now displays a summary listing all
  generated files at the end. Shows the main output file plus any tier files (e.g., 4k, 16k, 64k)
  that were created, making it easy to see where all outputs were written.

### Changed
- **Additional Files excludes boilerplate:** License files (LICENSE, COPYING, NOTICE), hypergumbo
  artifacts (hypergumbo.results.json), and config files (.gitignore, .editorconfig, CODEOWNERS) are
  now excluded from the Additional Files section. Improves signal-to-noise ratio.
- **CI skips expensive jobs for docs-only PRs:** Lint, audit, and pytest jobs now skip when only
  markdown files change, saving ~6 minutes of CI time.
- **pytest-xdist for parallel tests:** Added pytest-xdist dependency. Run `pytest -n auto` for
  parallel execution (~2 min vs ~5 min sequential). Not enabled by default to preserve debug-ability.

## [1.0.0] - 2026-01-12

First stable release. Major focus on memory optimization, framework detection improvements,
and completing the migration to YAML-driven semantic analysis.

### Fixed
- **Memory optimization for large repos:** Reduced peak memory from ~11GB to ~2.1GB (80%
  reduction) for repositories like tensorflow. Uses streaming JSON output and aggressive
  cleanup of intermediate data structures.
- **Android framework detection:** Now detects Android via `android {}` blocks in build.gradle,
  AndroidManifest.xml presence, and gradle plugin dependencies.
- **JSON serialization of Python literals:** Complex numbers and bytes literals no longer cause errors.
- **`--frameworks all` and explicit lists:** Now bypass dependency scanning, enabling pattern
  matching even when manifests are in subdirectories.
- **Express route detection:** Fixed case-sensitive HTTP method comparison.
- **Slice command:** Now runs all language analyzers, not just Python/HTML.

### Added
- **Recursive manifest scanning:** Scans up to 3 levels deep for dependency manifests (monorepo support).
- **Ruby/Elixir framework detection:** Gemfile and mix.exs scanning for Rails, Phoenix, etc.
- **Usage-based pattern matching:** Route detection for call-based frameworks (Django `path()`,
  Express `app.get()`, Rails route DSL, Go Gin, etc.) via YAML patterns.
- **12 new framework YAML patterns:** ktor, vapor, plug, fastify, grape, tornado, aiohttp,
  slim, micronaut, graphql, electron, cli.

### Changed
- **Entrypoint detection now 100% YAML-driven:** Removed 26 legacy detection functions (~1,700 lines).

## [0.9.1] - 2026-01-09

### Fixed
- **Incomplete v0.9.0 release:** v0.9.0 was accidentally built from the wrong branch. This
  release includes all ADR-0003 features. Users should upgrade from v0.9.0 to v0.9.1.

## [0.9.0] - 2026-01-09 (INCOMPLETE RELEASE)

> **Warning:** This release was built from the wrong branch. Please use v0.9.1 instead.

### Changed (Breaking)
- **Schema version 0.2.0:** New `entrypoints` field added to behavior map output.

### Added
- **`--frameworks` flag:** Control framework detection (`none`, `all`, `fastapi,celery`, or auto-detect).
- **Entrypoints in JSON output:** Detected entrypoints now persisted in output with stable IDs.
- **Smart JSON detection in slice command:** `.json` files auto-detected as `--input`.
- **Connectivity-based entrypoint ranking:** Entrypoints ranked by graph connectivity for better `--entry auto`.
- **Linker activation conditions:** Linkers now have structured activation criteria (always, frameworks, language_pairs).
- **Rich metadata extraction:** Decorators/annotations with args/kwargs for Python, JS/TS, Java, C#.
- **YAML-driven framework patterns:** Data-driven symbol enrichment via `src/hypergumbo/frameworks/*.yaml`.
  - Initial patterns for: FastAPI, Flask, Django, Express, NestJS, Spring Boot, Rails, Phoenix,
    Laravel, Go web frameworks (Gin/Echo/Fiber/Chi), Rust web frameworks (Actix/Rocket),
    ASP.NET Core, Hapi, Koa, Celery, and more.
  - See `docs/ARCHITECTURE.md` for the full pattern inventory.
- **Semantic entry detection:** Entrypoint detection via concept metadata (highest priority, 0.95 confidence).
- **HTTP linker concept support:** Extracts route info from concept metadata.

### Changed
- **Python analyzer purified:** Route detection moved from analyzer to YAML patterns.

### Deprecated
- **Packs:** Framework-specific analysis now uses `--frameworks` flag instead of packs.
- **Path-based entrypoint heuristics:** Prefer semantic detection via YAML patterns.
- **Analyzer-level route detection:** Route detection moving to YAML patterns (1.0.x migration).

## [0.6.9] - 2026-01-07

### Added
- **Connectivity-aware auto-slicing:** `--entry auto` prefers well-connected entrypoints.
- **Improved slice traversal:** Synthetic linker nodes connected via `uses` edges.
- **Stronger cross-file call resolution:** Module-qualified calls and lightweight type inference.
- **Linker diagnostics:** `LinkerRequirement` checks and registry pattern for linker execution.
- **Variable-based linker matching:** URLs/event names in variables detected (lower confidence).

### Fixed
- **Route detection false positives:** Excluded `fetchMock.get()`, `axios.post()`, etc. from Express routes.
- **Entrypoint false positives:** Excluded React file-routing, non-web handlers, DNS resolvers, etc.

### Changed
- **Linker consolidation:** All linkers migrated to `@register_linker` registry pattern.

## [0.6.0] - 2025-12-29

### Added
- **New analyzers:** Lean 4 (theorem prover), Wolfram Language (Mathematica), Agda (proof assistant).
- **Build-from-source grammars:** `scripts/build-source-grammars` for experimental tree-sitter grammars.
- **Contributor workflow:** `scripts/contribute` for fork-based contributions.
- **Release automation:** `scripts/release-check`, `scripts/release`, `scripts/integration-test`.
- **Sketch improvements:** Two-phase symbol selection, per-file compression, deterministic output.

See `docs/ARCHITECTURE.md` for the full language/framework support matrix.

## [0.5.0] - 2025-12-26

Initial public release with comprehensive static analysis capabilities.

### Core Commands
- `hypergumbo [path]` - Token-budgeted Markdown sketch
- `hypergumbo run [path]` - Full JSON behavior map
- `hypergumbo slice --entry X` - BFS/DFS subgraph extraction
- `hypergumbo routes [path]` - HTTP route listing
- `hypergumbo search <query>` - Symbol search

### Analysis Capabilities
- **32 language analyzers:** Python (AST), Java, Rust, Go, JavaScript, TypeScript, C, C++, C#,
  Ruby, PHP, Swift, Kotlin, Scala, Haskell, OCaml, Elixir, Lua, Zig, Solidity, Julia, Groovy,
  SQL, CUDA, Verilog, VHDL, GLSL, WGSL, Fortran, Bash, and more. See `docs/ARCHITECTURE.md`.
- **12 cross-language linkers:** HTTP, WebSocket, Message Queue (Kafka/RabbitMQ/SQS/Redis),
  GraphQL, gRPC, Database Query, Event Sourcing, IPC (Electron/WebWorker), JNI, Swift-ObjC,
  Phoenix Channels.
- **Framework detection:** 100+ frameworks across Python, JavaScript, Rust, Go, PHP, Java, etc.
- **Supply chain classification:** Tier 1-4 (first-party, internal deps, external deps, derived).

### Output Schema
- `schema_version`, `profile`, `nodes[]`, `edges[]`, `analysis_runs[]`, `metrics`, `limits`
- Symbols include spans, stable IDs, supply chain tier, and optional metrics.

---

## Version History

| Version | Date       | Highlights                                                   |
| ------- | ---------- | ------------------------------------------------------------ |
| 1.0.0   | 2026-01-12 | Memory optimization (80% reduction), 100% YAML-driven entrypoints |
| 0.9.1   | 2026-01-09 | ADR-0003 implementation (was missing in 0.9.0)               |
| 0.9.0   | 2026-01-09 | Schema 0.2.0, --frameworks flag, YAML patterns (incomplete)  |
| 0.6.9   | 2026-01-07 | Fewer false positives, richer slice traversal                |
| 0.6.0   | 2025-12-29 | Lean, Wolfram, Agda analyzers; release automation            |
| 0.5.0   | 2025-12-26 | Initial release: 32 analyzers, 12 linkers                    |

[Unreleased]: https://codeberg.org/iterabloom/hypergumbo/compare/v1.0.0...HEAD
[1.0.0]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.9.1...v1.0.0
[0.9.1]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.9.0...v0.9.1
[0.9.0]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.6.9...v0.9.0
[0.6.9]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.6.0...v0.6.9
[0.6.0]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.5.0...v0.6.0
[0.5.0]: https://codeberg.org/iterabloom/hypergumbo/releases/tag/v0.5.0
