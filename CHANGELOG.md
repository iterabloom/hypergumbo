<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Changelog

All notable changes to hypergumbo are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

- Released **tool** is at: v2.6.0
- Released **schema** is at: v0.2.2

This changelog tracks the **tool version** (package releases). The **schema version** is tracked separately in `schema.py` as `SCHEMA_VERSION`. The schema version changes when `docs/schema.json` has significant updates: breaking changes to the behavior map output format (minor bump) or additions like new type definitions for YAML validation (patch bump).

## [Unreleased]

### Fixed

- **`test-agent-infra` full-suite job fixed (hypothesis + fetch-depth + root-aware heartbeat test):** three distinct environment problems in the `test-agent-infra` job of `.github/workflows/full-suite.yml`, one test harness problem in `tests/test_touch_heartbeat.py`. (1) `tests/test_transcript_pipeline_properties.py` imports `from hypothesis import given, settings` but the job's pip install line only installed `pytest pytest-xdist pyyaml` — collection errored with `ModuleNotFoundError: No module named 'hypothesis'`. Added `hypothesis~=6.100` (same pin as `packages/hypergumbo-tracker/pyproject.toml:46`). (2) `tests/test_backfill_training_data_cohort_tags.py::TestRealV0WindowBoundaries` walks `git log -- .agent/hooks/_shared/on_transcript_change.py` against the repo's real history and asserts on four v0-window SHAs (`75596a4153d4`, `704548fe1e90`, `7310783e1b26`, `9ac06cc88a00`); the checkout had no `fetch-depth: 0` so the default depth=1 shallow clone dropped those SHAs and three tests failed with `SHA starting with 75596a4153d4 not found in timeline`. Added `with: fetch-depth: 0` to match the `changes` job at lines 30-33. (3) `tests/test_touch_heartbeat.py::test_unwritable_heartbeat_dir_is_silent` used `readonly_parent.chmod(0o555)` to simulate an unwritable heartbeat directory, but CI runs as root and root bypasses DAC write permissions — `mkdir -p` on a `chmod 0o555` directory succeeds for root, so the heartbeat file was created and `not hb_dir.exists()` failed. Swapped the precondition: the test now sets `HEARTBEAT_DIR=<regular_file>/agent-supervisor` so `mkdir -p` fails with `ENOTDIR` regardless of uid. Preserves the test's intent (verify the helper swallows mkdir errors silently) without an euid-conditional skip.

- **`concurrency.cancel-in-progress` on tracker-ci.yml prevents stacked runs on retry:** `ci.yml` has carried `concurrency: cancel-in-progress: true` for a while, but `.github/workflows/tracker-ci.yml` did not. When the tracker auto-sync's `stale_pending` retry path re-pushed to a sync PR whose CI was queued behind a saturated runner, Forgejo dispatched a fresh run *without* cancelling the prior queued one — observed 2026-04-21 on PR #3250 where three tracker-ci.yml runs on head SHA `586d52e` stacked up and the third hung for ~2h. The fix is the same block as `ci.yml`: `group: tracker-ci-${{ github.head_ref || github.ref_name }}` with `cancel-in-progress: true` so the newest run supersedes any earlier in-flight run on the same ref. Runs on distinct refs (different branches / PRs) remain isolated. Does not fix the underlying sync-retry logic — that still attempts to cancel via PR-close, which has no direct effect on already-queued workflow runs on Codeberg's Forgejo (no `POST /actions/runs/{id}/cancel` endpoint exposed in the swagger) — but makes the stacking harmless because the newer push auto-cancels the older dispatch.

### Added

- **Elm `let`-bound `String.join "/"` URL folding in the cross-language HTTP linker (WI-rosan):** Phase-1 of `_scan_elm_file` only handled the direct `Utils.Api.<method> (apiUrl ++ "/path")` form, which missed the indirect `let url = String.join "/" [ apiUrl, "silence", uuid ]` pattern used throughout Alertmanager's `Silences/Api.elm` and `Alerts/Api.elm` (`getSilence`, `getSilences`, `create`, `destroy`, `fetchAlerts`, `fetchAlertGroups`). New `ELM_LET_STRING_JOIN_PATTERN` regex pair + `_parse_string_join_parts` / `_fold_elm_string_join` / `_find_elm_let_bound_url_calls` helpers find every `let NAME = String.join "/" [...]` binding, scan an 800-char window for a consuming `Utils.Api.<method> NAME` call, and fold the list items into a URL path. First list item is dropped as the base-URL variable (`apiUrl`); remaining items become literal path segments (`"silence"` → `/silence`, `"alerts" ++ queryStr` → `/alerts` — literal wins and the `++` tail is dropped) or `{name}` placeholders when the item is an identifier reference (`uuid` → `/{uuid}`, `silence.id` → `/{id}`), so `_match_route_pattern` can bind them against Go route parameters. A let-binding whose identifier is never referenced by a subsequent `Utils.Api` call within the scan window is dropped — we don't invent calls that aren't in the source. 13 new tests in `tests/test_http_linker.py::TestElmLetStringJoin` cover every idiom shape (two/three literal segments, variable path segment, literal-plus-`++`-tail, dotted-identifier, DELETE method, non-`url` binding names, multi-function modules, line numbers), plus three edge cases for the empty / too-short / unconsumed forms. Expected outcome: the Alertmanager Elm → Go cross-language `http_calls` edges previously missing for these endpoints materialize on the next DEEP bakeoff, closing the second of the two concrete test cases named in WI-tinip's title (WI-sijoh closed the first).

- **`--backend rust-analyzer` CLI flag (WI-vozof):** new root-parser `--backend` flag with two choices, `tree-sitter` (default) and `rust-analyzer`. Passing `--backend rust-analyzer` sets `HYPERGUMBO_RUST_ANALYZER=1` in the process env so the opt-in gate from WI-duzul Slice C picks it up; passing `--backend tree-sitter` (or omitting the flag) leaves the env untouched so an already-set env var still wins. The flag is stripped from argv before the subcommand parser runs, matching the `--debug` convention, so both `hypergumbo --backend rust-analyzer run .` and `hypergumbo run . --backend rust-analyzer` work; the `--backend=rust-analyzer` equals-form is also supported. Unknown choices are rejected by argparse with its usual error. 11 tests cover the parser (tree-sitter / rust-analyzer / default=None / invalid-choice rejected) and the env side-effect (rust-analyzer sets, tree-sitter does not, flag in post-subcommand position works, --backend=VALUE form supported, --backend=tree-sitter leaves env unchanged, no-flag leaves env unchanged, trailing bare --backend falls through to argparse). Closes WI-vozof — the final piece of the rust-analyzer backend UX; users can now opt into the SCIP pass via either env var or CLI flag, `hypergumbo install-extras` installs the binary, and the registered analyzer handles the dispatch + fall-through.

- **Registered analyzer for the SCIP-backed Rust backend (WI-duzul Slice C-final):** new `hypergumbo_lang_rust_analyzer.analyzer.analyze_rust_with_scip(repo_root) -> AnalysisResult`, decorated with `@register_analyzer("rust_analyzer", priority=45)` so it dispatches through the hypergumbo-core analyzer registry alongside the existing `rust.py` analyzer (priority 50 default). When the opt-in gate (`should_use_rust_analyzer_backend`) returns False — the default for every session that hasn't set `HYPERGUMBO_RUST_ANALYZER=1` or passed `--backend rust-analyzer` — this analyzer returns an empty `AnalysisResult` immediately with no file walk and no subprocess; `rust.py` takes care of Rust analysis. When the gate is True, `try_analyze_with_rust_analyzer(repo_root, _disk_source_reader)` from WI-nohah shells out to `rust-analyzer scip`, translates the emitted SCIP index to Symbols + Edges with rust.py stable_id parity, and returns them. The three WI-nohah fall-through conditions all surface here as the graceful-degrade helper's `None` return; this analyzer swallows `None` to an empty `AnalysisResult` so the registry treats "no SCIP run" identically to "SCIP run produced nothing" — `rust.py` continues to emit its output unchanged. Provenance is already set upstream (SCIP Symbols carry `origin="scip"` from `scip_index_to_symbols`), so downstream tools can trust-weight SCIP-derived symbols separately from tree-sitter ones. Entry-point registration added to the package's `pyproject.toml` under `[project.entry-points."hypergumbo.analyzers"]` with an `ANALYZER_MODULES` list, matching the pattern `hypergumbo-lang-common` / `hypergumbo-lang-extended1` use for analyzer discovery. 7 tests cover `_disk_source_reader` (reads existing file), `analyze_rust_with_scip` (gate False → empty result; gate False → backend never invoked; gate True but backend returns None → empty; gate True and backend succeeds → passes symbols/edges through; passes the repo_root + disk reader to the backend), and the registry entry (present, correct name, priority 45). Closes the WI-duzul dependency chain: the package now ships the full stack of translate (A) + invoke (B-first) + install (WI-dotud) + umbrella (WI-huham) + graceful-degrade (WI-nohah) + opt-in gate (Slice C gate) + registered analyzer (this slice), ready for a CLI `--backend rust-analyzer` flag (still deferred; env var activation works today).

- **Opt-in gate for the SCIP-backed Rust analyzer (WI-duzul Slice C gate):** new `hypergumbo_lang_rust_analyzer.gate.should_use_rust_analyzer_backend(*, backend_flag=None, environ=None, is_available=None)` — the single decision point for "should the rust-analyzer backend activate for this run?". Returns True iff BOTH conditions hold: (a) the user explicitly opted in, either via the `HYPERGUMBO_RUST_ANALYZER` env var (``"1"`` / ``"true"`` / ``"yes"`` / ``"on"``, case-insensitive, whitespace-tolerant) OR via `backend_flag="rust-analyzer"` / ``"rust_analyzer"`` / ``"scip"`` (same tolerance); AND (b) the `rust-analyzer` binary is resolvable on PATH per `hypergumbo_core.rust_analyzer_install.is_rust_analyzer_available`. When the user opted in but the binary is missing, the function returns False silently — the opt-in is honoured up to the limits of what is installed, and the analyzer-registry wrapper falls through to `rust.py`. `environ` / `is_available` are injectable so tests cover every branch without mutating `os.environ` or shelling out to `shutil.which`; production callers pass None for both. The split between this gate and `graceful_degrade` is intentional: graceful-degrade answers "the user asked; did it work?" (runtime failures); this gate answers "did the user actually ask?" (opt-in). Slice C's analyzer-registry wrapper will chain them in that order. 26 tests cover `_is_env_enabled` (10 truthy + 6 falsy values, missing key), `_is_flag_enabled` (5 selecting values + 6 rejecting values), and `should_use_rust_analyzer_backend` (env+available, flag+available, opt-in-without-binary returns False, no-opt-in short-circuits without calling is_available, flag-beats-missing-env, env-beats-missing-flag, neither-returns-False, default-environ-is-os-environ round-trip, default-is-available-resolver-wired-in via monkeypatched shutil.which).

- **Graceful-degrade orchestrator for rust-analyzer backend (WI-nohah):** new `hypergumbo_lang_rust_analyzer.graceful_degrade.try_analyze_with_rust_analyzer(workspace, source_reader, *, invoke=None, translate=None, log=None)` that invokes rust-analyzer scip + translate into ``(symbols, edges)`` on the happy path, OR returns ``None`` on any of the three WI-nohah fall-through conditions: (1) binary not on PATH via `RustAnalyzerNotInstalled`, (2) invocation failure / timeout via `RustAnalyzerInvocationFailed`, (3) `cargo metadata` / invalid SCIP via `RustAnalyzerNoOutput` or `google.protobuf.message.DecodeError`. The ``None`` return is the single-expression decision point for "use rust-analyzer vs fall through to rust.py" — keeps the fall-through logic testable without mounting a real analyzer registry. The scratch `cwd` is owned here (a `tempfile.TemporaryDirectory`), so callers don't have to manage it. Fall-through messages are deduplicated per `(exception_type, workspace)` so a session analyzing one workspace repeatedly emits the "falling through to rust.py" line exactly once; distinct workspaces each get their own emission. `invoke` / `translate` / `log` are all injectable so tests cover every failure shape without spawning a subprocess or constructing a SCIP fixture (log defaults to a no-op so production callers that don't want user-visible chatter stay silent). 9 tests cover the happy path (invoke + translate returns the symbol/edge lists), every invoke failure mode (not-installed, invocation-failed, no-output, and a positive-dedup test showing 3 calls on one workspace log once), per-distinct-workspace dedup (two workspaces log twice), translate DecodeError (returns None, decode-failed message, once-per-workspace dedup), and the default-log no-op branch (no stderr/stdout chatter). Unblocks the WI-duzul Slice C analyzer-registry wrapper: Slice C calls this helper per workspace and the `None` return drives the fall-through to `rust.py`.

- **`hypergumbo install-extras` + `uninstall-extras` umbrella subcommands (WI-huham):** single-call installer over every optional component — gitleaks, embeddings, rust-analyzer. `install-extras --check` prints a status table (one row per component with installed / not-installed symbol) and exits 0 iff every component is present, 1 otherwise (so scripts can gate on it). The default action runs each installer in sequence; components that already report as installed are skipped; a failure in one installer doesn't abort the rest (the umbrella is best-effort, and the final exit code reflects whether any component failed). `--skip gitleaks,embeddings` omits the named components from both the table and the install pass. `uninstall-extras` mirrors the install surface. Component rows are defined in a single helper `_extras_components()` so adding a fourth optional dependency later is a one-line change. Thin `_install_embeddings_impl` / `_uninstall_embeddings_impl` adapters expose the embeddings installer with the same `(quiet=...) -> bool` signature the umbrella expects (uniform with the gitleaks and rust-analyzer installers). Both subcommands registered in the `extras` group, added to the usage-line metavar and the valid-subcommands set. 19 tests cover the umbrella (--check all-present / one-missing / --skip exclusion / --skip tolerates empty segments), install path (skip already-installed / run all missing / failure continues and exit 1), uninstall path (all-succeed / any-failure / --skip), the components table (three rows with expected names and callables), and the embeddings adapters (install success with chatter, quiet mode, non-zero exit reports stderr, TimeoutExpired, str-stderr variant; uninstall success with chatter, TimeoutExpired). WI-huham closes the human-requested discoverability goal — a single command documents the full opt-in story instead of N separate installers.

- **`hypergumbo install-rust-analyzer` + `uninstall-rust-analyzer` subcommands (WI-dotud):** new `hypergumbo_core.rust_analyzer_install` module and two CLI subcommands mirroring the existing `install-gitleaks` / `install-embeddings` pattern. `is_rust_analyzer_available()` returns True when the binary is resolvable on `PATH` (shutil.which-based, injectable `which` kwarg for hermetic tests); `install_rust_analyzer()` runs `rustup component add rust-analyzer` when rustup is present and prints a pointer to rustup.rs + rust-analyzer's upstream install page when it is not; `uninstall_rust_analyzer()` runs `rustup component remove rust-analyzer` when rustup is present and no-ops (success) otherwise to avoid clobbering system-package or hand-copied binaries. CLI wrappers `cmd_install_rust_analyzer` (supports `--check`) and `cmd_uninstall_rust_analyzer` emit the same symbol+status table as the gitleaks / embeddings commands. Both subcommands registered in the "extras" subparser group and added to the usage-line metavar and the valid-subcommands set. First cut is rustup-only; static-binary-download fallback (when rustup itself is not installable, e.g. minimal CI images) is a follow-up. 24 tests cover `is_rust_analyzer_available` (found / missing / default-shutil), `install_rust_analyzer` (missing-rustup prints pointer and returns False, successful invocation, quiet mode, non-zero exit reports stderr, TimeoutExpired, OSError, str-stderr variant), `uninstall_rust_analyzer` (missing-rustup is idempotent success, missing-rustup quiet mode, successful invocation, non-zero exit, TimeoutExpired, OSError, str-stderr variant, quiet successful), and the CLI wrappers (`--check` available and unavailable, install success/failure, uninstall success/failure). WI-dotud is the prerequisite for WI-huham (`install-extras` umbrella) and feeds the `is_rust_analyzer_available` helper consumed by WI-duzul Slice C's opt-in gating and WI-nohah's graceful-degrade fall-through.

- **`rust-analyzer scip` shell-out wrapper (WI-duzul Slice B-first):** new `hypergumbo_lang_rust_analyzer.invoke.run_rust_analyzer_scip(workspace, cwd=..., rust_analyzer_bin=..., timeout_sec=..., which=..., runner=...)` wraps the `rust-analyzer scip <workspace>` invocation and returns the emitted `index.scip` bytes. Three dedicated exception classes let callers discriminate failure modes: `RustAnalyzerNotInstalled` (binary missing on PATH — WI-nohah territory, fall through to `rust.py`), `RustAnalyzerInvocationFailed` (binary ran but exited non-zero or timed out, carries captured stderr for surfacing), and `RustAnalyzerNoOutput` (binary exited 0 but produced no `index.scip` — typically a `cargo metadata` error). The `which` and `runner` kwargs are injectable so the whole module can be exercised without a real rust-analyzer install; production callers pass `None` (the defaults use `shutil.which` and `subprocess.run`). Default timeout is 600s per WI-zakub §4 (rust-analyzer SCIP is 10× slower than tree-sitter at every realistic size — a visible timeout is better than an indefinite hang). 11 tests cover binary resolution (missing, custom-name error message), success path (returns bytes, cmd shape, workspace arg position, custom timeout forwarded), failure paths (non-zero exit, `TimeoutExpired` with/without stderr, success-but-no-output, `CompletedProcess.stderr=None` defensive handling on both failure branches). Out of scope for this slice: analyzer-registry wiring at higher priority than `rust.py`, the `HYPERGUMBO_RUST_ANALYZER` env var + `--backend rust-analyzer` CLI flag, and the end-to-end integration of translate+invoke into an `Analyzer` subclass — all deferred to WI-duzul Slice C.

- **`hypergumbo-lang-rust-analyzer` package skeleton + SCIP→IR translate surface (WI-duzul Slice A):** new optional package `packages/hypergumbo-lang-rust-analyzer/` that sits on top of the hypergumbo-core SCIP shim (`scip_index_to_symbols` / `scip_index_to_edges` / `scip_index_to_call_edges`) and the `hypergumbo_lang_mainstream.rust_scip.compute_rust_stable_id_from_source` parity helper. Slice A delivers the pure-Python translate surface: `translate_scip_to_hg(scip_bytes, source_reader) -> (symbols, edges)` parses a serialized SCIP `Index`, emits Symbols via the core shim, post-processes Rust function/method Symbols by rewriting their `stable_id` through the rust.py parity helper so cross-pass dedup with the tree-sitter `rust.py` backend works, and concatenates Relationship-derived edges with Occurrence-ref (span-enclosure) call edges from the shim. The helper `reassign_rust_stable_ids(symbols, source_reader)` is also exported for callers that already hold a Symbol list and only need the parity pass. Non-Rust symbols, non-function Rust symbols, span-less symbols, path-less symbols, reader-returns-None cases, reader exceptions (FileNotFoundError / OSError / ValueError), and parity-helper-returns-None cases all pass through unchanged — the rust-analyzer stable_id is a best-effort rewrite, not a hard requirement. Source reader is called once per path (per-path cache) so multiple symbols in the same file don't re-read it. 15 tests cover the reassignment's 8 passthrough branches (non-rust, non-function, span-less, path-less, source=None, source raises, parity returns None, reader-caches-per-path), the successful-parity branch (graceful-degrades to passthrough when tree-sitter-rust is unavailable in the test env, per WI-zakub §4 opt-in discipline), and `translate_scip_to_hg` end-to-end (empty blob, single Rust function round-trip, multi-document walk, call-edge surfacing from non-Definition Occurrence, reader invocation count, malformed-bytes raises). No live `rust-analyzer` invocation yet; no analyzer-registry wiring; no opt-in CLI flag — those arrive in Slice B per the scope plan posted on WI-duzul's discussion. Per-package coverage clean (100% on `translate.py`).

- **`serializer` concept → SERIALIZER entrypoint (WI-gudob Phase 5):** mirrors Phase 4 (form) — any symbol tagged `concept: serializer` is classified as a `SERIALIZER` entrypoint with confidence 0.90 and a framework-derived label (``"Django serializer"``, ``"Flask serializer"``, or plain ``"Serializer"`` when no framework attribution is present). Producers are uniformly class-level ``base_class`` matches across 9 frameworks (django DRF `Serializer` / `ModelSerializer` / `HyperlinkedModelSerializer`, flask Marshmallow `Schema` / `SQLAlchemySchema` / `SQLAlchemyAutoSchema`, grape `Grape::Entity`, laravel `JsonResource` / `ResourceCollection`, litestar `AbstractDTO` / `DTOData`, plus plumber/pyramid/quart/rails), so the scope is clean — no heterogeneous decorator/call-level noise of the kind that caused validator/auth to be deferred at Phase 4. Restores class-level reachability for serializer/DTO classes that previously looked dead to the static call graph because the framework instantiates them and reflectively calls `to_representation` / `dump` / `to_internal_value` / `toArray` / `exposure`. Per-method dispatch (`to_representation` → field-specific methods) is deferred to a per-framework dispatch-registry follow-up because each framework names its reflective methods differently. `docs/CONCEPTS.md` regenerated via `scripts/generate-concepts`: `serializer` flipped inert → live (9 producers). 3 new tests (framework label, no-framework fallback, multi-framework dedup) in `tests/test_entrypoints.py`.

- **Kafka Streams topology-callback dispatch linker (WI-lisov):** new Framework-subcategory linker `packages/hypergumbo-core/src/hypergumbo_core/linkers/kafka_streams_dispatch.py` that recovers reflective-dispatch edges the Kafka Streams runtime uses to invoke topology callbacks on a per-record basis. For every Java / Kotlin / Scala class whose declared `base_classes` or `interfaces` short-match one of 17 Kafka Streams callback types (`ValueMapper`, `ValueMapperWithKey`, `KeyValueMapper`, `Predicate`, `ForeachAction`, `Aggregator`, `Reducer`, `Initializer`, `Merger`, `ValueTransformer`, `ValueTransformerWithKey`, `Transformer`, `Processor`, and their four `*Supplier` factory forms), the linker emits `dispatches_to` edges from the class to the interface-specific framework-called methods (`apply`, `transform`, `process`, `get`, `init`, `close`, `test`) with confidence 0.90 and evidence `kafka_streams_dispatch`. Fully-qualified names like `org.apache.kafka.streams.kstream.ValueMapper<K, V, VR>` normalize to the short form before matching, so the Scala wrapper namespace (`org.apache.kafka.streams.scala.kstream.*`) is covered by the same interface table. Lifecycle methods for stateful transformers (`init` / `close`) are emitted alongside the per-record method so slices through a `Transformer` reach the setup and teardown as well. Registered at linker priority 21 (same tier as `jackson-dispatch`) and wired into `cli.py` alongside the other Framework-dispatch linkers. 37 tests in `tests/test_kafka_streams_dispatch_linker.py` cover `_short_type_name` (bare / qualified / generic-parameter / qualified-with-generics), `_callback_interfaces_on` (non-dict meta, base_classes match, interfaces match, qualified-name normalization, unknown-interface reject, dedup across both keys, non-list entry skip, non-string entry skip, missing keys, multi-interface ordered), `_expected_method_names` (single, Transformer lifecycle, multi-interface union, empty-list), `_build_class_method_index` (class grouping, non-method skip, non-JVM-language skip, top-level-function skip), and the integration pass (ValueMapper → apply, Transformer → init/transform/close, qualified-name match, non-callback ignored, non-JVM class ignored, no-classes early return, Kotlin support, Scala support, interface-kind target, existing-edge dedup, ProcessorSupplier → get, multi-interface union, class-without-span emits `line=0`). Scope is intentionally limited to class→method edges (sufficient to lift the 2386 `kafka_streams_internal` dead-code candidates per WI-tubot aggregate-v5); call-site → impl edges are left as a Phase 2 follow-up in WI-lisov.

- **Auto-strip / regression-spawn for `awaits_bakeoff_validation` verdicts (WI-dolil slice 2b):** `scripts/bakeoff-deep-reflect aggregate` gains a `--apply-verdicts` flag that executes the tracker mutations implied by the slice-2 per-claim verdict distribution. On `plurality=moved` the `awaits_bakeoff_validation` tag is stripped from the item and a discuss note records the cohort + per-repo evidence; on `plurality=no_move` a regression sub-item is spawned under the original (kind `work_item`, tags `regression` + `awaits_bakeoff_validation`, priority 1, status `todo_soft`) and a discuss note is posted on the parent; on `plurality=tied` or `inconclusive` the cohort did not settle the claim and no mutation is performed. Default behaviour is dry-run: the plan is printed (one line per step) with `(dry-run; re-run with --apply-verdicts to execute)` so humans can preview before the mutations happen. New pure helpers `build_validation_mutation_plan(summary, cohort_label)` and `apply_mutation_plan(plan, runner=...)` are exercised via an injected runner in tests so no real tracker calls occur. Multi-cohort aggregate (`--all` / `--some`) invokes the surface helper per cohort so a batch re-aggregate can settle every open claim at once. 19 new tests in `tests/test_bakeoff_deep_reflect.py` cover the plan-builder (empty summary, moved → remove_tag+discuss, no_move → spawn_regression+discuss, tied/inconclusive → note-only, missing item_id/plurality skipped, evidence truncation at 120 chars, empty-evidence fallback, `+N more` roll-up when evidence exceeds max_items), the CLI-runner (remove_tag / discuss / spawn_regression argv shapes, note + unknown-action skip, non-zero exit marks failed, `OSError` runner exception marks failed with message captured), and the print/apply surface (no-plan returns None, dry-run prints plan without applying, apply returns stats dict and prints summary line, apply prints per-step failure lines, dry-run prints every action shape). Slice 2b closes the tooling half of WI-dolil; only slice 4 (acceptance demo — a real DEEP cycle exercising the end-to-end tag → validate → strip/regress round-trip) remains.

- **`awaits_bakeoff_validation` cross-reference in reflect pipeline (WI-dolil slice 2):** `scripts/bakeoff-deep-reflect` now loads active `awaits_bakeoff_validation` tracker items (status `todo_soft` or `in_progress`) via `scripts/tracker --json list` at reflect-prompt-generation time and injects a per-claim `## Active Bakeoff Validation Claims` section into every repo's prompt. The YAML output schema gains a new `awaits_bakeoff_validation_verdicts` field keyed by `item_id`, with `verdict ∈ {moved, no_move, inconclusive}` + `evidence`. `aggregate_assessments` collects the per-item verdict distribution across repos in the cohort and exposes it on the summary as `awaits_bakeoff_validation_verdicts` with a `plurality` field (`moved` / `no_move` / `inconclusive` / `tied`); `_print_aggregate_summary` surfaces one line per item so the human-visible run summary shows whether a cohort moved each open claim. Tracker CLI failure (missing script, non-zero exit, non-JSON output, non-list payload, `OSError`) degrades silently to "no claims" so the reflect step still works in fresh clones or during auto-sync churn. 20 tests in `tests/test_bakeoff_deep_reflect.py` cover the loader's five failure modes, the section formatter (empty input, ID/title echo, 1200-char truncation, missing fields, first-paragraph-only), prompt injection into `cmd_reflect` (section omitted when no claims, section present when claims active), aggregate verdict collection (no-field-without-data, single-item plurality moved, tied plurality, invalid-verdict filtering, all-inconclusive, empty-counts helper), and the print-summary surfacing. No tag-stripping / regression-spawn auto-actions yet — those are deferred to slice 2b once the verdict schema has been validated on a real cohort.

- **Per-PR smart-test coverage for top-level infrastructure (WI-jozan):** follow-up to WI-javan. `scripts/smart-test` used `hypergumbo slice --files` for reverse-slice to find test files that depend on changed source, but hypergumbo's reverse-slice only reaches the `packages/*/src/**` tree — it does not know about `.agent/hooks/_shared/*.py` or `scripts/*`. A PR that only touched those produced an empty slice result, the `CHANGED_SOURCE_FILES` filter (`^packages/.*/src/.*\.py$`) kept it empty, and `smart-test` wrote an empty-manifest "no Python source files changed" pass that `ci.yml` then used to skip `pytest` entirely. WI-javan closed the 4-hour full-suite gap; this closes the per-PR gap. New `.agent/hooks/_shared/top_level_test_map.py` pure-Python helper maps each changed top-level source path to its matching `tests/test_<basename>.py` when that file exists: `.agent/hooks/_shared/<name>.py` → `tests/test_<name>.py`; `scripts/<name>` (single path component, optional `.py`/`.sh` suffix, hyphens collapsed to underscores) → `tests/test_<name>.py`. Sub-directory helpers (`scripts/lib/forgejo-api.sh`) and unrelated paths are intentionally skipped; false negatives for those still get full-suite coverage via the WI-javan job. `scripts/smart-test` pipes `CHANGED_FILES` through the helper and folds the output into `AFFECTED_TESTS` between the slice-result filter and the empty-manifest check, so a PR that touches only `.agent/hooks/_shared/watched_process.py` now runs `tests/test_watched_process.py` in `ci.yml`. 21 tests cover the pure `_candidate_test_basename` predicate (all match rules + all non-match cases), the `map_to_tests` file-existence filter (dedup, sort, empty input, missing tests dir, unmapped-non-empty-path continue branch, hyphen→underscore), and the `main()` / subprocess CLI entry points (success, empty stdin, missing arg).

- **`test-agent-infra` full-suite CI job runs the top-level `tests/` directory (WI-javan):** prior to this change, every test under the repo-top-level `tests/` directory — `test_awaits_bakeoff_nudge.py`, `test_stop_hook_state_write_discipline.py`, `test_normalize_interjections.py`, `test_filter_transcript.py`, `test_agent_supervisor*.py`, `test_watched_process.py`, `test_generate_concepts.py`, `test_training_log_parse_misses.py`, and ~a dozen more — executed only on developer machines. `full-suite.yml` enumerated `pytest packages/<name>/tests/` per package without the top-level path, and `ci.yml`'s smart-test manifest keyed off `packages/.*/src/.*\.py` changed-file detection so any change under `.agent/hooks/_shared/`, `scripts/`, or root-level Python produced an empty manifest and the `Run tests with coverage` step skipped pytest entirely. `scripts/agent-supervisor`, `.agent/hooks/_shared/*.py` (stop_logic, on_transcript_change, normalize_interjections, filter-transcript, awaits_bakeoff_nudge, watched_process, …), and the tracker-sync glue therefore caught regressions locally at best. New `test-agent-infra` job in `full-suite.yml` mirrors the existing package jobs' shape (self-hosted runner, Python 3.11 setup, cgroup-aware `pytest-xdist` worker count, `--no-cov --tb=short`), is wired into the `aggregate` job's `needs` list and the `conclusion` calculation so it becomes a hard gate, and is guarded by the shared `changes.outputs.code == 'true'` filter so doc-only PRs skip it. `tests/` currently has 941 tests passing after WI-bukag cleared three pre-existing failures. Per-PR coverage under `ci.yml` is deferred to a follow-up (teaching smart-test to recognise `.agent/hooks/_shared/*.py` and `scripts/` as source paths); the 4-hour full-suite cadence is the immediate gap this closes.

### Fixed

- **Top-level `tests/` drift after WI-tasab and WI-nadud (WI-bukag):** three pre-existing failures were surfaced when WI-javan (still pending) instrumented the top-level `tests/` directory to run in CI for the first time. (1) `tests/test_generate_concepts.py::test_committed_file_is_up_to_date` regenerated expected content via `scan_producers(FRAMEWORKS_DIR)` — the one-argument form — while the production `scripts/generate-concepts` had moved to `scan_producers(FRAMEWORKS_DIR, ANALYZER_SRC_DIRS)` in WI-tasab to pick up programmatic concept emitters in analyzer `.py` source (e.g. `main_guard` from `py.py`). Test now passes `ANALYZER_SRC_DIRS` so its in-memory regeneration matches the committed `docs/CONCEPTS.md`. (2)/(3) `TestLogTrainingExampleCohortMetadata::test_pipeline_version_is_v1` and `test_extra_still_works_alongside_cohort` both asserted `entry["pipeline_version"] == "v1"`, but the emitter at `.agent/hooks/_shared/on_transcript_change.py:464` has emitted `"v2"` since WI-nadud's normalization pass. Tests updated to assert `"v2"`; the v1-named test method is renamed to `test_pipeline_version_is_v2` so the fixture's intent matches its assertion. All 941 top-level tests now pass (`pytest tests/ --no-cov`). This unblocks WI-javan — the full-suite.yml addition of a `test-agent-infra` job would have gone red the moment it landed.

- **Stop hook watched-process detector no longer over-matches on pytest-pathed helpers (WI-zajob):** the WI-varid process-aware pause used `pgrep -af "pytest|python -m pytest|smart-test|bash ./scripts/auto-pr|bash ./scripts/merge-pr"` and a single `grep -vE` filter that only stripped `bash -c` / `sh -c` wrappers. Any other process whose argv contained one of the pattern substrings — notably `inotifywait` helpers spawned by pytest tempdir fixtures with `/tmp/pytest-<id>/...` path arguments — was treated as a live watched process, so the stop hook slept up to 30 minutes (`watched_max_wait_seconds=1800`) on a cohort of leaked helpers while no pytest was actually running. Reproducer: four `inotifywait` processes with etime 1h1m matching `pgrep -af pytest` blocked the hook until the user hit Escape. New `.agent/hooks/_shared/watched_process.py` module owns the decision: splits each cmdline into tokens, strips the directory prefix from argv[0], collapses `python3.11`/`python3` to `python`, and requires the full pattern to match the LEADING argv tokens (so `inotifywait /tmp/pytest-…/foo` no longer matches `pytest`, and `vim ./scripts/auto-pr` no longer matches `bash ./scripts/auto-pr`). The `bash -c` / `sh -c` exclusion is preserved because `-c` means "execute this string", not "invoke the binary the string happens to name". `stop_logic.sh` now pipes `pgrep -af` output through `python3 watched_process.py "${WATCHED_PATTERNS[@]}"`, with the pgrep regex retained only as a cheap prefilter. 22 tests in `tests/test_watched_process.py` cover the pure predicate (pytest binary, `python -m pytest`, versioned python, `smart-test`, `bash ./scripts/auto-pr` / `merge-pr`, absolute-path variants, the inotifywait-with-tmpdir reproducer, `grep pytest foo.log`, `vim ./scripts/auto-pr`, both shell `-c` wrappers, empty/whitespace cmdline, empty pattern list, defensive empty-pattern-token skip) plus the `any_watched_alive_in` parser over pgrep output (positive, all-false-positives, empty, malformed-line) and the CLI (alive → exit 0, not-alive → exit 1, empty stdin → exit 1).

### Added

- **Stop-hook nudge for `awaits_bakeoff_validation` backlog (WI-dolil slice 3):** new `.agent/hooks/_shared/awaits_bakeoff_nudge.py` worker, invoked from `stop_logic.sh`, appends an `## AWAITS_BAKEOFF_VALIDATION BACKLOG` section to the active guidance file when the count of tag-bearing items in a blocking status reaches `threshold` AND the most recent DEEP bakeoff cycle's `state.json` is older than `stale_cycle_hours`. Both knobs live under `stop_hook.awaits_bakeoff_validation_nudge` in `.agent/tracker/config.yaml` (defaults `threshold=5`, `stale_cycle_hours=72`); the staleness gate uses strict-greater-than so hour 73 is the first stale tick. AGENTS.md §Bakeoff Validation Discipline now documents the surfacing mechanism. 25 tests cover the pure `compute_nudge` function, YAML config loading, `find_latest_deep_cycle_mtime`, `count_tagged_items`, end-to-end CLI invocation via a fake tracker script, and integration guards that `stop_logic.sh` invokes the worker and AGENTS.md documents it.

### Changed

- **generate-concepts scans Python source for programmatic concept emitters** (WI-tasab): follow-up to INV-rikis. After that fix, docs/CONCEPTS.md still flagged `main_guard` as `ghost` — `entrypoints.py` consumed it but no framework YAML emitted it. Root cause: `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/py.py` emits `{"concepts": [{"concept": "main_guard", "framework": "python"}]}` programmatically from its AST walker when it detects `if __name__ == "__main__":`, bypassing the YAML pipeline. `scan_producers` only scanned YAMLs, so the emission was invisible to the registry. `scan_producers` now optionally takes a list of Python source trees (`ANALYZER_SRC_DIRS` — the four `hypergumbo_*` src dirs) and scans them with a new `_PROGRAMMATIC_PRODUCER_RE` (`"concept": "X"` / `'concept': 'X'` literal form). Variable-expression forms (`{"concept": self.concept}`, `{"concept": concept_type}`) are deliberately not matched — they appear when a YAML-driven pattern reflects its own value back out (`framework_patterns.py`'s pattern compiler) and matching them would double-credit every YAML concept. `framework_patterns.py` and `_concept_utils*` are explicitly skipped during the scan. `main_guard` now flips ghost → live with `py.py` as the producer; ghost count drops from 1 to 0 (38 live / 279 inert / 0 ghost). 11 new tests extend `test_generate_concepts.py` to cover literal detection (double/single quotes, multi-concept files), explicit variable-form non-match, non-string value rejection (`int`/`bool`/`None` after `"concept":`), empty-text yield, YAML-only integration path, programmatic-only integration path, the `framework_patterns.py` skip, the `_concept_utils.py` skip, and the legacy-API (omitted `source_dirs`) preservation.

- **generate-concepts detects variable-name and tuple-membership consumer patterns** (INV-rikis): `scripts/generate-concepts` used to only recognize `has_concept(x, "X")`, `get_concept(x, "X")`, `.get("concept") == "X"`, and bare `concept == "X"` as consumer forms. The ~30 concepts consumed in `hypergumbo_core.entrypoints._detect_from_concepts` via a local `concept_type` variable (`concept_type == "X"` branches and `concept_type in ("X", "Y")` tuple-membership tests) never matched, so `docs/CONCEPTS.md` mis-classified them as `inert` despite being live in production. The consumer regex set now matches any identifier containing `concept` for `==` / `!=` comparisons in either order, plus a two-step membership-test extractor that reads the container body of `concept_type in (...)`, `concept_type in {...}`, `concept_type in [...]`, and the `not in` form, and pulls every quoted identifier out. Regenerating `docs/CONCEPTS.md` flips 30 concepts from inert → live: `error_handler`, `websocket_handler`, `websocket_gateway`, `event_handler`, `middleware`, `command`, `command_by_name`, `controller_by_name`, `handler_by_name`, `broker_lifecycle_by_name`, `service_by_name`, `liveview`, `graphql_resolver`, `graphql_schema`, `lifecycle_hook`, `main_function`, `main_guard`, `library_export`, `serialization_callback`, `entrypoint`, `app_bootstrap`, `ipc_handler`, `application`, `npm_bin`, `cargo_binary`, `pyproject_script`, `route`, `task`, `scheduled_task`, `controller`. Coverage total moves from 7 live / 309 inert / 0 ghost to 37 live / 279 inert / 1 ghost — no code change to the consumers themselves, just an honest accounting. New `tests/test_generate_concepts.py` (25 tests) covers every consumer shape (equality, inequality, reverse-order, concept-variant variable names, tuple / set / list / not-in membership, multi-line tuple, unrelated-variable non-match, empty input, integration scan across a temp dir, `_concept_utils.py` skip) so the blind spot cannot silently regress.

- **Behavior map node IDs use repo-relative paths** (WI-hopug, UAT 2026-04-13 UX-06): the `id` field on every Symbol and the `src`/`dst` fields on every Edge used to embed the absolute file path (e.g. `python:/home/user/repo/src/main.py:1-10:foo:function`), which made `hypergumbo.results.json` non-portable across machines and branches that hold the same commit under different checkout roots. `run_behavior_map` now calls a new `_relativize_ir_paths` helper immediately after `resolve_deferred_symbol_refs` to strip the `str(repo_root) + "/"` prefix from every Symbol id/path, Edge src/dst, and UsageContext path/symbol_ref/id in place, so ranking, entrypoint detection, handler-slice emission, and the final JSON write all observe the portable form. Paths outside `repo_root` (external-dependency symbols, synthetic module hints like `python:external:0-0:foo:unresolved`, and absolute paths under unrelated roots) never match the prefix and are left untouched. UsageContext.id is a sha256 over its path — the rewrite recomputes it from the relativized path so the hash is stable across machines. Single-site change: every analyzer's own ID-construction helpers (`_make_symbol_id` in ~35 files) still stringify paths as-passed; only the CLI emit path strips the prefix. 7 new tests in `tests/test_cli_relativize_paths.py` cover the Symbol id/path rewrite, the Edge src/dst rewrite, paths outside `repo_root` being preserved, UsageContext id recomputation, `symbol_ref=None` handling, outside-repo UsageContext preservation, and the empty-input no-op path.

### Added

- **SCIP Occurrence-ref → call-edge translator (WI-mafut Phase 2 Slice D, WI-kopav)**: new `hypergumbo_core.scip.calls.scip_index_to_call_edges` completes the SCIP ingest by attributing every non-`Definition` `Occurrence` to its enclosing `Definition` via pure span containment, emitting one `references`-typed `Edge` from the enclosing definition's symbol to the occurrence's symbol. Enclosure is resolved per-document by scanning the Definition occurrences, picking every span that fully contains the ref's `(start_line, start_col, end_line, end_col)`, and selecting the innermost by `(line-span, col-span)` lexicographic comparison — document order breaks ties so two Definitions with identical spans map to the first. Occurrences with no enclosing Definition (module-top-level expressions, imports at file scope, occurrences that precede the first definition) are skipped silently rather than attributed to a phantom caller; `Definition` occurrences themselves emit no edges (the introduction of a symbol is not a reference to it); self-edges are dropped. Edges carry `edge_type="references"`, `evidence_type="scip_occurrence_ref"`, `origin="scip"`, `confidence=0.85`, and `meta={"scip_src_symbol", "scip_dst_symbol", "symbol_roles"}` where `symbol_roles` preserves the upstream bitfield (ReadAccess / WriteAccess / Import / Generated / Test) so a downstream specialization pass can refine to `"calls"` / `"writes_to"` / `"imports"` when it has target-kind context. Same `resolve_symbol` callable contract as Slice C — None for either endpoint drops the edge. 15 tests cover empty input, ref inside definition, ref outside any definition dropped, innermost-wins under nesting, single-line vs multi-line enclosure, definition-itself-no-edge, recursive-self drop, tie-break first-in-document, multi-ref same definition, multi-document independence, resolver round-trip and None drop, role preservation on meta, and malformed-range graceful skip for both definitions and refs.

- **SCIP Index → Edge translator (WI-mafut Phase 2 Slice C)**: new `hypergumbo_core.scip.edges.scip_index_to_edges` emits hypergumbo `Edge` objects from `SymbolInformation.relationships` entries on a parsed SCIP Index. Edge direction follows the natural reading: `src = SymbolInformation.symbol`, `dst = Relationship.symbol`, so "X implements Y" becomes `src=X, dst=Y, edge_type="implements"`. Each relationship boolean flag maps to one edge type — `is_implementation` → `implements`, `is_type_definition` → `has_type`, `is_reference` → `references`, `is_definition` → `defined_by` — with multiple flags on a single Relationship entry fanning out to multiple Edges. Self-relationships (`src == dst`) are dropped as noise. Edges are emitted unresolved by default: `src` / `dst` are raw SCIP symbol strings that downstream callers resolve against the Slice B Symbol output's `stable_id` field; a caller-supplied `resolve_symbol` callable remaps either endpoint, and the Edge is dropped if the resolver returns `None` for either side. Slice C is intentionally narrow: Occurrence-level refs (non-Definition `Occurrence` entries, which conceptually represent call / read / write sites) are NOT turned into edges here because that requires enclosing-symbol span resolution not encoded by SCIP — a follow-up slice / linker will own that pass. Per WI-zakub §1, rust-analyzer leaves `relationships` empty; its trait-dispatch information lives inside the descriptor chain and is handled by the rust-analyzer-specific decoder (WI-duzul territory), so this module silently produces an empty list for rust-analyzer input. Edges carry `origin="scip"`, `evidence_type="scip_relationship"`, `confidence=0.9`, and `meta={"scip_src_symbol", "scip_dst_symbol", "scip_relationship_flag"}` for provenance. 16 tests cover empty input, symbol without relationships, each of the four relationship flags individually, multiple flags fanning out, multiple relationships per symbol, all-false-flags no-op, self-reference drop, multi-document walk, resolver happy path, resolver returning None for either endpoint, and evidence/meta fields.

- **SCIP Index → Symbol translator (WI-mafut Phase 2 Slice B)**: new `hypergumbo_core.scip.index.scip_index_to_symbols` walks a parsed `scip_pb2.Index` and emits hypergumbo `Symbol` objects. Thin glue over Phase 1's descriptor parser and the Phase 2 Slice A vendored `scip_pb2.py`: for every `SymbolInformation` with a matching `Definition`-role `Occurrence` in the same document, emit one Symbol with `name` / `kind` pulled from the last descriptor in the chain (METHOD → `method`, TYPE → `class`, TERM → `variable`, etc.), `language` lower-cased from `Document.language`, `path` from `Document.relative_path`, `span` rewritten from the 3- or 4-int `Occurrence.range` to a 1-indexed hypergumbo `Span`, `origin="scip"`, `stable_id` preserving the raw SCIP symbol string for later linkers, and `meta` carrying `scip_symbol` / `display_name` / `scip_kind` when provided. Local symbols (`local <id>`) keep `kind="local"` rather than being dropped because rust-analyzer and scip-python both emit locals that participate in cross-reference resolution. Malformed SCIP symbol strings and `SymbolInformation` without a `Definition` Occurrence are skipped silently so a buggy upstream emitter cannot abort the whole translation pass. SCIP column values are UTF-8 code-unit offsets by default (WI-zakub §3); the non-UTF-8 `PositionEncoding` case is deferred because no in-the-wild emitter uses it. Slice C (Occurrence refs → `calls` edges, `Relationship` → `implements` / `has_type` edges) ships separately. 20 tests cover span conversion (3-int single-line, 4-int multi-line, rejected lengths), empty index, definition vs import vs unrolled reference, symbol without any occurrence, multiple-definition-picks-first, descriptor kind mapping (TYPE → class, TERM → variable, METHOD → method), local symbol preservation, malformed and empty-descriptor skip, language lower-casing, empty-language fallback to `unknown`, multi-document walk, and meta preservation of `display_name` / `scip_kind`.

- **Vendored SCIP protobuf binding (WI-mafut Phase 2 Slice A)**: new `scripts/build-scip-proto` regenerates `packages/hypergumbo-core/src/hypergumbo_core/scip/_generated/scip_pb2.py` from a pinned Sourcegraph `scip.proto` revision (`9330cbd49aeb85aee026842770a61ad28e5c4093`, 2026-04-14). The script is developer-run, not CI-run — the generated `scip_pb2.py` is checked in so end users pay no install-time toolchain cost; bumping the pin requires rerunning the script and reviewing the diff. Path B was chosen after a Phase 2 PyPI probe found no published Python binding for the SCIP wire format (`scip`, `scip-python`, `sourcegraph-scip`, `scip-pb`, `scip-protobuf`, `scip-index`, `scip-pb2` — all 404 or unrelated). Uses `grpcio-tools`' bundled `protoc` rather than a system-level `protoc`, so no apt/brew prerequisite is introduced. Adds `protobuf~=6.33` to `hypergumbo-core` dependencies (the generated module imports `google.protobuf`); the root and package `pyproject.toml` coverage configs omit `*/scip/_generated/scip_pb2.py` because hand-auditing generated code is pointless. `SOURCE_COMMIT.txt` in the `_generated/` directory records the pinned SHA for reproducibility. Downstream `hypergumbo_core.scip.index` and `hypergumbo_core.scip.edges` (Slices B / C) will import from this module. 4 smoke tests (`test_scip_generated.py`) cover: the module imports, every message type the Phase 2+ shim consumes (`Index`, `Metadata`, `ToolInfo`, `Document`, `SymbolInformation`, `Occurrence`, `Relationship`, `Diagnostic`, `SymbolRole`, `SyntaxKind`) is present, an `Index` round-trips through serialize/parse, and `SOURCE_COMMIT.txt` is a 40-char hex SHA.

- **SCIP symbol-string descriptor parser (WI-mafut Phase 1)**: new `hypergumbo_core.scip.descriptor` is a language-agnostic parser for Sourcegraph SCIP symbol strings — the `<scheme> <manager> <package> <version> <descriptor>+` chain used by rust-analyzer, scip-python, scip-typescript, scip-java, and scip-clang. Phase 1 intentionally stops at the parser: downstream phases will walk SCIP `Index.documents` and emit `Symbol`/`Edge` objects, but the descriptor grammar is the non-trivial piece (backtick-quoted names with escaped backticks, method disambiguators, `[type-param]` / `(param)` brackets, WI-zakub's trait-dispatch `impl#[T][Trait]method().` shape) and merits its own module. `parse_scip_symbol` returns a frozen `ScipSymbol` dataclass carrying the four header fields plus a tuple of `ScipDescriptor(name, kind, disambiguator)` values; `DescriptorKind` enumerates the eight kinds (`NAMESPACE`, `TYPE`, `TERM`, `META`, `METHOD`, `MACRO`, `TYPE_PARAMETER`, `PARAMETER`). Local symbols (`local <id>`) set `is_local=True` with empty header/descriptors. Malformed input raises `ValueError` with a position-qualified message so callers can decide whether to skip-with-warning or propagate. No protobuf dependency is introduced at Phase 1 — the parser operates on the wire string directly, which keeps the hypergumbo-core package weight unchanged and postpones the optional-dep story to WI-duzul (the rust-analyzer-backed scaffold). 30 tests cover every descriptor kind, the local path, space-escape in header fields, backtick quoting with embedded suffix chars and doubled-backtick escape, method disambiguator + no-disambiguator cases, multi-descriptor chains including the trait-dispatch shape, and every error path (empty input, missing package fields, truncated-at-boundary header, missing descriptor chain, missing suffix, unterminated backtick / method / type-param / parameter, unknown suffix).

- **`form` concept mapped to `FORM` entrypoint kind (WI-gudob Phase 4)**: per the WI-dajul concept-vocabulary registry audit, `form` was the #4 inert concept after Phase-1 `error_handler` (37), Phase-2 `controller` (34), and Phase-3 `router` (14) — 12 framework YAML patterns emit it (`django` `Form`/`ModelForm`, `flask-wtf` `FlaskForm`, `laminas` `Form`/`Fieldset`, `fuelphp` `Fieldset`, `cakephp` `Form`, `laravel`, `symfony`, `yii`, `pyramid`, `rails`, `remix`, `sveltekit`, `yesod`) but no downstream stage consumed it, so every framework-instantiated form class looked dead to static call-graph analysis. The framework instantiates the form class from request data and reflectively calls `is_valid()` / `save()` / `authorize()` / `clean()` / `rules()` — the class body therefore looks unreachable even when wired into a reachable route. `detect_entrypoints` now maps the `form` concept to a new `EntrypointKind.FORM` with confidence 0.90 (lower than `ERROR_HANDLER`'s 0.95 because not every form-tagged class is actually wired into a reachable route; the lower floor encodes residual uncertainty), framework-aware labelling (`"Django form"` etc., or a generic `"Form"` when the concept has no framework hint), and per-symbol dedup so a form matched by two framework patterns still produces at most one entrypoint. Class-level reachability is restored uniformly; internal-method dispatch (form.clean_FIELD, form.save) is deferred to a dispatch-registry follow-up because each framework names its reflective methods differently. Flips `form` from inert → live in `docs/CONCEPTS.md`. 3 new tests cover the framework-labeled path, the no-framework fallback path, and the multi-framework dedup path.

- **Router-routes Framework linker (WI-gudob Phase 3)**: new `hypergumbo_core.linkers.router_routes` is a Framework-subcategory linker that emits `registers_routes` edges from a `concept: router` module / combinator / route-table symbol to each nested `concept: route` registration in the same file. Honest scope: effective for Phoenix (`use Phoenix.Router` module enclosing `get`/`post`/`resources`/`live` macro calls), http4s (`HttpRoutes.of` enclosing pattern-match case arms), http4k (routing combinator calls), Yesod (`mkYesod`/`parseRoutes` quasiquote), giraffe / pedestal / ring-compojure route-table expressions, cowboy `routes` list, sveltekit / remix / nuxt file-route tables, vertx `Router.route()` chains, plumber `pr_get`/`pr_post` attachments, laminas module router config. Out of scope: frameworks whose router is purely configuration-file-based with no code symbol (pure Rails `config/routes.rb`) and filesystem-convention routing that requires cross-file containment. Keeps edge type (`registers_routes`) distinct from `controller_routes`'s `contains_routes` so slice queries can distinguish "routes registered by this router module" from "route handler methods contained by this controller class". Span-nesting picks the innermost router when nested combinators coexist (http4s: outer `HttpRoutes` + inner `HttpRoutes.of` both enclose the same arm — only the innermost wins). Test-file symbols are excluded; bare-string concept shape is deliberately not matched per INV-tuzub. Edges are confidence 0.80 with `evidence_type="router_routes"`. Third-highest producer-count inert concept (14 frameworks) per the WI-dajul concept-vocabulary audit, following Phase 1's `error_handler` and Phase 2's `controller`. Flips `router` from inert → live in `docs/CONCEPTS.md`. 18 new tests cover single-router / multi-route / different-file / outside-span / no-router / no-route / missing-span / test-file (`.exs` / `.py` / `.rb` variants) / bare-string-concept / nested-innermost-wins / cross-framework-synthetic (Phoenix + http4s + Yesod fixtures — linker-logic exercise, not framework-realism claim) / run-metadata / confidence / empty-input paths.

- **Controller-routes Framework linker (WI-gudob Phase 2)** (WI-gokop): new `hypergumbo_core.linkers.controller_routes` is a Framework-subcategory linker that emits `contains_routes` edges from a `concept: controller` class symbol to each nested `concept: route` handler method in the same file. Honest scope: the linker is effective for decorator-based controller families where the handler method itself carries a `concept: route` tag and lives inside the controller class body — NestJS (`@Controller` + `@Get`/`@Post`), Spring Boot (`@Controller`/`@RestController` + `@GetMapping`/`@PostMapping`), ASP.NET (controller base + `[HttpGet]`/`[HttpPost]`), Laravel, Symfony (`#[Route]`), Phoenix, Micronaut, Ktor, Grails, CakePHP. Rails and Django class-based views without `@api_view` are explicitly out of scope because their handler methods never receive the `concept: route` tag (Rails routes live in `config/routes.rb`; Django CBV methods are bare overrides); cross-file controller↔route association for those frameworks is `route_handler.py`'s concern. Span-nesting picks the tightest enclosing controller (so base-class + subclass collisions in the same file collapse to the innermost). Test-file symbols are excluded; bare-string concept shape is deliberately not matched per INV-tuzub. Edges are confidence 0.80 with `evidence_type="controller_routes"`. Second-highest producer-count inert concept (34 frameworks) per the WI-dajul concept-vocabulary audit, following WI-gudob Phase 1's `error_handler` consumption. 16 new tests cover single-controller / multi-route / multi-controller-per-file / different-file / outside-span / no-controller / no-route / missing-span / test-file / bare-string-concept / nested-innermost-wins / cross-framework-synthetic (rails + aspnet + django fixtures — linker-logic exercise, not framework-realism claim) / run-metadata / confidence / empty-input paths.

- **IO-boundary leaf-caller roll-ups** (WI-darad Phase 1): `BoundaryMapEntry` now carries two new rollup fields — `leaf_callers: list[str]` (deduplicated set of immediate callers of every `io_edge_src` reaching this boundary) and `entry_points_per_leaf: dict[str, list[str]]` (entrypoints reachable through each leaf caller) — surfaced in `hypergumbo.results.json` under each boundary. The bakeoff assessment aggregation raised WI-darad after noticing that a shared helper like `notify/util.go:request` collapsed every caller (`slack.Notify`, `discord.Notify`, `pushover.Notify`) into a single chain whose entrypoint list was the union of everyone's entrypoints — taint-style reasoning could no longer answer "which concrete callers does this entrypoint reach?". The new roll-ups restore that association without materializing per-path chains (which would blow up output size on repos with many entrypoints × many IO edges). When `io_edge_src` has no callers in the reverse graph it is treated as its own leaf, so the single-function-reaches-primitive case still has a populated leaf list. The underlying BFS over the reverse call graph is reused from entry-point tracing via a new shared `_build_reverse_graph` / `_reachable_entry_points` helper pair (the old `_trace_entry_points` wrapper was deleted — it had only one caller and the inlined version threads the shared reverse graph into both the EP-per-io-src map and the EP-per-leaf map without rebuilding it). 4 new tests cover the multi-Notifier leaf surfacing, the EP-per-leaf disjoint-reach case, the no-callers self-leaf fallback, and the `to_dict` round-trip. Per-path full-chain materialization deferred to a follow-up if bakeoff still shows collapse as a problem.

- **`error_handler` concept mapped to `ERROR_HANDLER` entrypoint kind** (WI-gudob Phase 1): per the WI-dajul concept-vocabulary registry audit (2026-04-18), `error_handler` was the #1 inert concept — 37 framework YAML patterns emit it (`fastapi`, `express`, `django`, `aspnet`, `flask`, `actix`, `axum`, `gin`, `nestjs`, `rails`, `laravel`, `symfony`, `phoenix`, `akka-http`, `ktor`, `vapor`, …) but no downstream stage consumed it, so every decorator-registered exception handler (`@app.errorhandler`, `@app.exception_handler`, `app.use(errorMid)`, `rescue_from`, `#[catch(…)]`, etc.) looked dead to dead-code analysis. `detect_entrypoints` now maps the `error_handler` concept to a new `EntrypointKind.ERROR_HANDLER` with confidence 0.95, framework-aware labelling (`"Fastapi error handler"` etc., or a generic `"Error handler"` when the concept has no framework hint), and per-symbol dedup so a handler matched by two framework patterns still produces at most one entrypoint. Uniform recovery across every framework that already emits the concept, without per-framework dispatch logic. 3 new tests cover the framework-labeled path, the no-framework fallback path, and the multi-framework dedup path.

- **Rust trait-impl method-dispatch linker (Phase 1)** (WI-kivut): new `hypergumbo_core.linkers.rust_trait_dispatch` is a Framework-subcategory linker that consumes the `implements` edges the Rust analyzer's inheritance pass already emits (WI-tulid: one per `impl Trait for Struct` block) and fans `dispatches_to` edges out from each trait symbol to every concrete method on the implementing struct (`"{Struct}::{method}"` in the same file). WI-tubot's 2026-04-11 aggregate-v5 prospector run pinned `rust_trait_impl + rust_instruction_descriptor` at ~3004 dead-code-maybe candidates because Rust dispatches trait methods either via generic bounds (monomorphized at compile time — the call graph sees an unbound call on the type parameter) or via `dyn Trait` trait objects (dispatched through a vtable — the call graph sees a call on the trait). Either way, static call-graph analysis treats the concrete `impl Trait for MyStruct` bodies as unreachable. This Phase-1 linker connects trait → concrete-impl-methods so any reachable path through the trait (a trait-object call site, a generic bound, a `use Trait` import at a reachable module) transitively reaches the implementation bodies. Edges are confidence 0.85 with `evidence_type="rust_trait_dispatch"`; dedupe against existing `dispatches_to` edges; per-file keying disambiguates structs sharing a name across modules (`mod a { struct Config }` vs `mod b { struct Config }`); unresolved `implements` edges pointing at external traits are silently skipped. Phase 2 (static-dispatch parsing of generic bounds and dynamic-dispatch call-site resolution) is deferred — it requires call-site type information the Rust analyzer does not currently expose; SCIP-level data (WI-duzul territory) would provide it. 22 new tests cover method-owner extraction (`Foo::bar` → `Foo`, `outer::Inner::method` → `outer::Inner`, plain functions rejected), per-file struct-method indexing, same-named structs across modules, unresolved-edge silent-skip, non-rust / non-implements edge filtering, edge dedup, multi-impl union, multi-trait per-struct fan-out, and the empty-input no-op path.

- **Django ORM method-dispatch linker** (WI-nosug): new `hypergumbo_core.linkers.django_orm_dispatch` is a Framework-subcategory linker that emits `dispatches_to` edges from Python classes subclassing any Django base (`Model`, `AbstractUser`, `AbstractBaseUser`, `Manager`, `QuerySet`, `ModelAdmin`, `ModelForm`, `Form`, `View`, `TemplateView`, `ListView`, `DetailView`, `CreateView`, `UpdateView`, `DeleteView`, `Migration`) to each user-defined override of the framework-called methods on that base. WI-tubot's 2026-04-11 aggregate-v5 prospector run pinned `python_orm_dispatch` at 2441 dead-code-maybe candidates because Django's ORM, admin site, and class-based-view dispatcher invoke these methods reflectively (`ObjectManager.objects.filter(...)`, `instance.save()`, `admin.register(MyAdmin)`) and the static call graph never sees those invocations. Per-base method maps (e.g. `Model` → `{save, delete, clean, clean_fields, full_clean, validate_unique, validate_constraints, get_absolute_url, __str__, __repr__, refresh_from_db, get_deferred_fields, ...}`; `View` → `{dispatch, get, post, put, patch, delete, head, options, trace, setup}`; `Manager` → `{get_queryset, contribute_to_class, ...}`) avoid spurious cross-base edges like `Model.get_queryset` (Manager-only). Qualified base names (`django.db.models.Model`) and generic parameters (`Manager[User]`, `QuerySet<Foo>`) normalize to the short final segment. Multi-inherit CBVs receive the union of all matched bases' method sets. Edges are confidence 0.90 with `evidence_type="django_orm_dispatch"`, dedupe against existing `dispatches_to` edges, and disambiguate same-named classes in different files via `(path, qualified_name)` keying. Python-internal per WI-nosug's framework_dispatch_linkers tag. 39 new tests cover base-name normalization (qualified / generic forms), subclass detection, method indexing, empty-corpus / no-Django no-op paths, per-base method filtering, cross-file disambiguation, Manager / QuerySet / Admin / Form / View subclass edges, multi-base method-set union, and edge dedup.

- **Jackson / JavaBean serialization dispatch linker** (WI-gupah): new `hypergumbo_core.linkers.jackson_dispatch` is a Framework-subcategory linker that emits `dispatches_to` edges from Java/Kotlin/Scala classes that the Jackson/JAX-B/Spring-binding runtime will reflect over to each of their bean-convention accessors (`getX`/`setX`/`isX`) and method-level-annotated handlers. WI-tubot's 2026-04-16 aggregate-v5 run pinned `java_bean_accessor` at 7614 dead-code-maybe candidates (8.3 % of the 92218-candidate pool) — the #1 non-uncategorized category — because `ObjectMapper.writeValueAsString(obj)` calls every getter reflectively at serialization time, so static call-graph analysis sees them as dead. A class is treated as a serialization target when it carries a class-level Jackson annotation (`@JsonSerialize`, `@JsonDeserialize`, `@JsonIgnoreProperties`, `@JsonFormat`, `@JsonAutoDetect`, `@JsonRootName`, `@JsonTypeName`, `@JsonTypeInfo`, `@JsonPropertyOrder`, `@JsonInclude`, `@JsonNaming`), a JAX-B annotation (`@XmlRootElement`, `@XmlType`, `@XmlAccessorType`), or a Spring binding annotation (`@ConfigurationProperties`, `@ConstructorBinding`); extends a `ConfigurationProperties`-family base class; or has *any* method whose decorators include a method-level Jackson annotation (`@JsonProperty`, `@JsonGetter`, `@JsonSetter`, `@JsonCreator`, `@JsonValue`, `@JsonAnyGetter`, `@JsonAnySetter`, `@JsonRawValue`) — this last rule captures the common `@JsonProperty` field→paired-accessor case without needing field-level metadata. Fully-qualified annotation names (`com.fasterxml.jackson.annotation.JsonProperty`) and generic-parameterized forms are normalized to their short final segment before matching. Accessor selection uses `Symbol.signature` arity when available: `getX`/`isX` must be zero-arg, `setX` must be one-arg, next-character-after-prefix must be uppercase ASCII (so `getUser` qualifies and `getter` does not); method-level-annotated handlers bypass the arity check. Edges are confidence 0.90 with `evidence_type="jackson_bean_dispatch"`, dedupe against existing `dispatches_to` edges, and dedupe within-run so `@JsonProperty`-annotated bean accessors don't get double-edges. Java-internal per WI-gupah's 2026-04-17 subcategory clarification — the cross-language consequence (serialized JSON consumed by TS/Python/Ruby clients) is already modelled by `openapi.py` / `http.py`. 69 new tests cover annotation-name normalization, decorator extraction from `decorators` / `annotations` meta keys, class-level and method-level annotation detection, JAX-B and Spring-binding recognition, bean-marker base class matching, bean-convention name predicates (bare `get` / `getter` / `is` rejection, zero-arg vs one-arg filtering), signature-arity parsing (nested generics, missing parens, unclosed parens), the method→class propagation path, per-file class isolation, Kotlin + Scala parity, edge dedup, empty-input no-op, and run-duration bookkeeping.

- **Airflow framework-dispatch linker** (WI-nutav): new `hypergumbo_core.linkers.airflow_framework_dispatch` is a Framework-subcategory linker that emits `dispatches_to` edges from classes inheriting any of `BaseOperator` / `BaseHook` / `BaseSensor` / `BaseTrigger` / `BaseSensorOperator` to the framework-called lifecycle methods defined on those subclasses (`execute`, `execute_complete`, `pre_execute`, `post_execute`, `on_kill`, `poke`, `run`, `hook`, `get_conn`, `test_connection`). Airflow's scheduler dispatches these methods dynamically at runtime, so the static call graph never sees them, and every override used to look like a dead function to dead-code analysis. The linker relies on `meta.base_classes` (already emitted by the Python analyzer and consumed by the `inheritance` linker) — the Airflow-specific knowledge is limited to the per-base method map. Qualified base names (`airflow.models.BaseOperator`) and generic parameters (`BaseSensor<Any>`) resolve to the same short name. Multi-inherit cases (e.g. a class that mixes `BaseOperator` and `BaseHook`) receive the union of both bases' method sets. The linker is Python-internal per WI-nutav's 2026-04-16 clarification — the cross-language consequence (`Hook.get_conn` eventually calling cloud-SDK clients) remains a leaf IO boundary, not a linker edge. 27 new tests cover base-name normalization, subclass detection, method indexing (by `(path, qualified_name)` key to respect file boundaries), empty-corpus/no-Airflow no-op paths, per-base method filtering, cross-file disambiguation, Hook/Trigger method edges, dedupe against prior `dispatches_to` edges, and within-run dedup on shared method targets.

- **SCIP → `rust.py` `stable_id` mapping helper** (WI-bajuz, ADR-0014 §3): new `hypergumbo_lang_mainstream.rust_scip.compute_rust_stable_id_from_source(source, start_line, end_line)` returns the exact same `stable_id` that `rust.py` would assign the Rust function occupying the given source span. The SCIP-backed rust-analyzer backend (WI-duzul, scaffold follow-up) will call this helper so that symbols seen by both the tree-sitter and SCIP passes dedup under a single identity — without the mapping, every shared Rust symbol would be double-counted in cached analyses. Implementation reuses `_extract_rust_signature`, `_extract_modifiers_rust`, `_get_impl_target`, `normalize_rust_signature`, `visibility_from_modifiers`, and `make_typed_stable_id` verbatim from `rust.py`, so the two passes cannot drift. Graceful-degrades to `None` when tree-sitter-rust is unavailable, when no `function_item` starts and ends on the provided lines, or when signature extraction fails (matching `rust.py`'s own `stable_id=None` behavior for unrecoverable symbols). Macro-expanded items are not covered because rust-analyzer does not emit them in SCIP (per WI-zakub). Column units follow SCIP's UTF-8-code-unit line/col convention; for Rust source this collapses to start-line + end-line uniqueness. 4 new tests cover the parity contract against `rust.py` on a multi-function sample, trait-impl vs inherent-impl method disambiguation at different spans, the no-match-at-span path, and the tree-sitter-unavailable fallback.

- **`agent-supervisor stop` no longer ambushes the next `run`**: `cmd_stop` used to unconditionally write `supervisor.stop-sentinel`, which the next `run` invocation consumed on its first tick and exited immediately — an operator running `stop` before any supervisor was live armed a trap that made the next launch die within seconds. `cmd_stop` now checks the pid in `supervisor.lock` with `os.kill(pid, 0)`; if the pid is missing, malformed, or dead, it cleans up the stale lock and any leftover stop-sentinel and prints `nothing to stop` instead of writing a fresh sentinel. A supervisor that is actually running still gets shut down normally. 4 new tests cover the live-supervisor path (lock pid = test process pid), the no-lock no-op, the stale-lock + stale-sentinel cleanup path, and the malformed-lock-content path.

- **`agent-supervisor debugging-reset-rate-limit` subcommand**: operator escape hatch that zeroes the rolling 24h spawn counter by deleting `rate_limit.json`. Added so that troubleshooting sessions which burn through the 8-per-24h cap don't force the operator to wait a day or hand-edit state files. Idempotent (no-op when the file is absent) and records the intervention in `respawn_log.log` so the reset is auditable. Named with the `debugging-` prefix to make it obvious in `--help` output that this is not part of the normal supervisor lifecycle.

- **Agent-supervisor non-interactive seed-prompt bootstrap** (follow-up to WI-sakod / WI-razub): fresh CLIs spawned by the supervisor used to sit idle at their prompt forever, because the Claude Code `SessionStart` hook injects *context* but does not trigger a model turn — the respawned CLI needed a first user message to start acting on the hook's instructions. `scripts/agent-supervisor::spawn_fresh` now, right after `tmux new-session`, polls `tmux capture-pane` in a tight loop (250 ms cadence, 32-byte floor, 3 consecutive identical samples) until the pane content stabilizes or a 15-second deadline elapses, then injects a generic seed keystroke (`"begin"`) via `tmux send-keys` to kick off the first turn. The stability check is deliberately vendor-agnostic: no banner / prompt regex is required, so it works for any CLI (Claude Code, Codex, Cursor, Gemini) whose banner must finish rendering before it accepts input. If the banner keeps animating past the deadline (e.g. an indefinite spinner), we send the keystroke anyway — a CLI that's tolerating spinner animation will tolerate a keystroke. New `wait_for_pane_ready` module-level helper and a `Supervisor._wait_for_pane_ready` instance wrapper that reads `PANE_READY_MAX_WAIT_SEC` at call time, so test modules can monkeypatch it to 0 to skip the wait without slowing down the existing spawn test suite. New `tmux_send_line` helper (distinct from `tmux_send_exit` for log-trace clarity). 10 new tests cover pane-ready stability detection (stable-above-threshold returns True, ever-changing content times out to False, stable-but-below-min-bytes times out to False, capture-failure treated as empty), the Supervisor wrapper's skip-when-disabled / delegate-when-enabled contract, seed-after-new-session ordering, per-pane targeting across successive spawns, respawn log entry for the seed injection, and the no-stray-send-keys guarantee when `spawn_fresh` is blocked by the auto-pause sentinel.

- **Agent-supervisor meta-circuit-breaker: chain tracking + no-progress kill switch** (WI-mujuk, hardens WI-razub): `scripts/agent-supervisor` now classifies every replacement as a **no-progress failure** (dying session's pane-byte count ≤ 512 — the CLI produced nothing visible between spawn and kill) or a **progress replacement** (> 512), tracks a chain via new `replaces` / `chain_length` / `consecutive_no_progress` fields in each session's `meta.json`, and **auto-pauses** after 5 consecutive no-progress failures on the same chain by writing `supervisor.auto-paused` into the state dir. Auto-paused supervisors refuse all new spawns (both the poll loop and direct `spawn_fresh` calls) until the operator runs the new `agent-supervisor resume` subcommand, which clears the sentinel and lets the next poll tick start a fresh chain (`chain_length=1`). The kill switch is deliberately **time-agnostic**: 5 consecutive no-progress failures trigger it the same whether they happened in 5 minutes or 5 days — a persistent bug absorbed by the existing 24h rate limit (8 useless spawns every morning, silence the rest of the day, invisible forever) now converts into a loud "investigate me" state. Progress replacements reset the counter to 0, so a chain that eventually makes headway is NOT at risk of auto-pause. `agent-supervisor status` surfaces `auto_paused`, `kill_switch_threshold`, and per-session `chain_length` / `consecutive_no_progress` / `replaces` so operators can see how close each chain is to tripping. Attached-client check still takes precedence: a human watching prevents replacement, which prevents the chain from growing — auto-pause can't fire on a session a human is diagnosing. Also: `respawn_log.log` records `AUTO-PAUSED: …` lines with the chain tail so retrospective audits can identify the failure pattern, and the new `resume` subcommand records the operator-driven clear so it's distinguishable from a cold start. 24 new tests cover threshold classification (0 / 100 / 512 / 513 / 4096 bytes), chain propagation across replacements, counter-reset on progress replacement, time-agnostic triggering, `autonomous_intent=OFF` short-circuit precedence, attached-client precedence, and the CLI subcommand's subprocess behavior. `docs/agent-supervisor.md` now has a "Recovering from auto-pause" section with the recommended investigation recipe before running `resume`.

- **`docs/agent-supervisor.md` operator guide** (follow-up to WI-razub): net-new user-facing doc covering the `scripts/agent-supervisor` daemon's operator workflow — prerequisites, first-time setup (`loop-toggle DEEP` + `agent-supervisor run &`), daily operations (attach / detach / pause / resume / shutdown), `status` JSON field semantics, edge cases (two supervisors, human attached, rate-limited, crashed, missing tmux), a troubleshooting matrix, the state-directory layout, an explicit "what the supervisor does NOT do" list, and cross-references to AGENTS.md + the script docstring. Fills the documentation gap the design doc left — the WI-razub "Concrete user UX" section lived in the tracker thread, not anywhere a workstation operator would find it. Linked from the main `README.md` Links section.

- **Vendor Parity for Respawn AGENTS.md section** (WI-batob, sub-item of WI-razub respawn mechanism): new authoritative table in `AGENTS.md` documenting, for each of Claude Code / Codex CLI / Cursor / Gemini CLI, the per-turn hook path (WI-sipov heartbeat), the session-start hook path (WI-sakod respawn branch), the graceful-exit keystroke the supervisor sends via `tmux send-keys`, the non-interactive CLI invocation for `tmux new-session`, and any vendor-specific quirks. Verification status is explicit: Claude Code's `/quit` is verified; the other three are marked "unverified — FIXME WI-batob" with a documented verification procedure (throwaway tmux session, send the keystroke, confirm the CLI process exits within 30s). Adding a new vendor requires four coordinated changes in the same PR (table row, `VENDOR_TABLE` entry in `scripts/agent-supervisor`, per-turn hook sourcing `touch_heartbeat.sh`, session-start hook sourcing `session_start_logic.sh`); the existing structural-guard tests in `tests/test_touch_heartbeat.py` and `tests/test_session_start_respawn.py` will fire if any hook wire-up is missed. Completes the last open sub-item of WI-razub.

- **Respawn-aware session-start hook** (WI-sakod, sub-item of WI-razub respawn mechanism): when the agent-supervisor daemon spawns a fresh CLI via `tmux new-session -e HYPERGUMBO_RESPAWN=1`, the vendor session-start hooks (via the shared `session_start_logic.sh`) now branch on the env var to auto-enable autonomous mode for this session per `autonomous_intent.txt` (narrow-write via `loop-toggle --set-session-mode`; project-level intent is left untouched) and emit the generic seed prompt ("Please familiarize yourself with this repo. Once you have done so, please set autonomous mode to DEEP."). All four vendors pick this up automatically since each hook sources the shared logic and surfaces `SESSION_START_MESSAGE` through its vendor-native path (plain stdout for Claude Code / Codex / Cursor; JSON `decision: allow, reason: ...` for Gemini). Defensive fall-through: `HYPERGUMBO_RESPAWN=1` + intent=OFF (or missing intent file / garbage value) takes the existing human-prompt path rather than force autonomous mode on. `HYPERGUMBO_RESPAWN` values other than exactly `1` are ignored so env var leakage can't accidentally trigger autonomous bootstrap. Never writes to `autonomous_intent.txt` — the hook is strictly a mirror from intent → session mode. 18 new tests cover the respawn branch for DEEP/BROAD/OFF/garbage/missing intent, the case-insensitive normalization, the "wrong env value is ignored" guard, a regression test that intent file mtime is unchanged, and structural guards that every vendor hook sources the shared logic and surfaces the message.

- **`scripts/agent-supervisor` daemon + replacement sequence** (WI-rofuv + WI-nusor, sub-items of WI-razub respawn mechanism): new Python daemon that monitors the reserved-prefix set of tmux sessions (`hypergumbo-session-*`) and replaces stuck ones with fresh CLIs seeded with `HYPERGUMBO_RESPAWN=1`. Three subcommands: `run` (the poll loop; `--interval N`, default 60s), `status` (one-shot JSON inventory), `stop` (write the stop sentinel; the running loop exits on its next tick). Single-instance invariant is enforced via `fcntl.flock` on `supervisor.lock`. State dir defaults to `~/hypergumbo_lab_notebook/agent-supervisor/` (override via `AGENT_SUPERVISOR_STATE_DIR`) and holds meta.json per session, `respawn_log.log` (append-only audit), `rate_limit.json` (rolling 24h spawn count with a default soft cap of 8), plus `supervisor.stop-sentinel` when a stop is requested. The decision matrix is a pure function: `OFF` intent always → noop; no-session + non-OFF → spawn; clients attached → noop (human is watching); CLI dead → replace; pane bytes unchanged for ≥ 15 minutes → replace. Pane-byte delta is the load-bearing "is this session working?" signal per WI-razub design — the WI-sipov heartbeat is NOT consulted for spawn/replace decisions. Replacement sequence (WI-nusor): send the vendor-specific exit keystroke (placeholder values for Codex/Cursor/Gemini marked `# FIXME WI-batob` for empirical verification by the docs item), poll `kill -0 <cli_pid>` for up to 30 seconds, and if the CLI refuses to quit, hard-kill the tmux session and directly invoke the already-idempotent `kill-transcript-sync.sh` + `rotate-on-session-end.sh` shared scripts. A fresh session is spawned with `tmux new-session -d -s <new> -e HYPERGUMBO_RESPAWN=1 <cli>`, so the respawn-aware session-start hook (WI-sakod, follow-up item) can branch on the env var to auto-enable autonomous mode from `autonomous_intent.txt` and inject the generic seed prompt. Also hardens the supervisor against missing `tmux` / vendor CLIs: the `run_subprocess` seam returns rc=127 (POSIX "command not found") instead of crashing the daemon. 47 new tests cover the decision matrix truth table, intent normalization, rate-limit pruning at the 24h window boundary, respawn log append semantics, corrupt-state-file tolerance, reserved-prefix discipline, pane-delta tracking (first observation / unchanged / changed / capture-failure preserves history / frozen timer grows), flock acquisition + second-process rejection, stop-sentinel consume-on-exit, spawn gating by rate limit and unknown vendor, the `HYPERGUMBO_RESPAWN=1` env var injection, the replacement sequence's graceful-exit path / hard-kill fallback / unknown-vendor default, the poll loop's three primary branches, the status report shape, and the CLI dispatch.

- **Per-session heartbeat helper for supervisor telemetry** (WI-sipov, sub-item of WI-razub respawn mechanism): new `.agent/hooks/_shared/touch_heartbeat.sh` — a sourced-helper that touches `~/hypergumbo_lab_notebook/agent-supervisor/<session_id>.heartbeat` whenever called by a vendor hook. Sourced + called from every per-turn hook: `claude-code/post-tool-use-transcript.sh`, `codex-cli/post-tool-use-transcript.sh`, `cursor/post-tool-use-transcript.sh`, `gemini-cli/before-model-transcript.sh` — so every tool-use / model-call in a live session updates its heartbeat mtime. Helper is deliberately fault-silent: empty session_id is a no-op, an unwritable heartbeat directory returns 0 with no stderr noise, and a touch failure is swallowed — hooks must never fail because supervisor bookkeeping can't write. Telemetry only — the forthcoming supervisor daemon's load-bearing "is this session working?" signal is tmux pane-byte delta, not the heartbeat. Stop-hook integration and long-running-command wrapper instrumentation (auto-pr, bakeoff-*, pytest smart-test) are deferred to a follow-up item because those code paths don't yet have a clean session_id handle. 14 new tests cover the helper's contract (sanitize + touch, mtime updates, missing-dir auto-create, empty-SID no-op, unwritable-dir silent graceful no-op, default HOME-derived path) plus structural guards that every per-turn hook sources and calls the helper after the SESSION_ID empty guard.

### Fixed

- **`agent-supervisor` rate-limit cap raised 8→24, auto-shutdown on sustained saturation, stop-sentinel now logs**: the 2026-04-18→04-19 trial's productive 6-session chain hit the old `RATE_LIMIT_MAX_PER_24H=8` cap at 16:15Z — every one of those 8 spawns shipped real merged commits, so the cap stopped a working agent rather than catching a runaway loop (the runaway-loop detector is WI-mujuk's `CONSECUTIVE_NO_PROGRESS_KILL_SWITCH=5`, which the rate limit duplicates for that case). The trial also exposed two log-hygiene problems once saturation hit: the daemon emitted 145 identical "rate-limit reached (8/8 in 24h); skipping spawn" lines at 1/min before the operator ran `stop`, and the `stop`-triggered exit left no log entry — which made the subsequent "tail the log" post-mortem misread a manual stop as a silent crash. Three coordinated changes: (1) raise `RATE_LIMIT_MAX_PER_24H` from 8 to 24 (≈3× the observed productive-cycle rate of ~2.5h average), (2) new `RATE_LIMIT_SATURATION_KILL=20` — when `spawn_fresh` is rate-limited on 20 consecutive poll ticks (~20 min at 60s/tick), the run loop auto-shuts-down with `auto-shutdown: N consecutive rate-limit hits; exiting`; counter lives at `Supervisor._consecutive_rate_limit_hits`, incremented in the rate-limit branch of `spawn_fresh` and reset to 0 after `record_spawn()` on a successful spawn, (3) `run()`'s stop-sentinel branch now calls `self.log_respawn("stop sentinel consumed; exiting")` before returning, so a clean operator stop leaves a traceable terminal log line. Operator can restart with `agent-supervisor run &` after auto-shutdown; the 24h rolling window will age out spawns naturally, or `debugging-reset-rate-limit` clears immediately. 4 new tests (`test_stop_leaves_log_entry` plus `TestRateLimitSaturationKill` — counter increments, counter resets on successful spawn, run loop exits with exactly RATE_LIMIT_SATURATION_KILL rate-limit log lines). `docs/agent-supervisor.md` updated (status-report doc, troubleshooting table with the new terminal-log-entry row).

- **`agent-supervisor` graceful-exit wait now actually waits**: the replacement sequence used to poll `kill -0 <cli_pid>` for up to 30 seconds before falling through to `tmux kill-session`, but `cli_pid` was set to `None` at spawn time with a "filled in later by session-start hook if possible" comment that never took effect in production. So `if cli_pid:` was always False, the 30-second wait was skipped entirely, and every replacement landed on the hard-kill path within ~2 seconds of sending the exit keystroke — regardless of whether the CLI had actually begun shutting down. The first production trial of the supervisor (2026-04-18 → 04-19, six forced-kills across the same chain) showed `forced-kill fallback for session …` in `respawn_log.log` on 6/6 replacements despite the exit keystroke being correctly delivered. Fix: introduce `tmux_session_exists(session, runner)` and `wait_for_session_gone(session, timeout_sec, ...)` helpers, and rewrite `replace_session` to poll `tmux has-session -t <name>` for up to 30 seconds — when the CLI exits cleanly, its shell exits, and tmux auto-removes the single-pane session (default `remain-on-exit off`), which is the observable the supervisor now waits for. No PID tracking, no hook cooperation, no cross-process identity reconciliation. `_evaluate_session` also now derives the `cli_alive` input to `decide_action` from the session-existence check instead of `pid_alive(meta["cli_pid"])`, so the "CLI died between polls" branch fires correctly (previously it was unreachable in production because `pid_alive(None)` never ran). `decide_action`'s parameter rename `cli_pid_alive` → `cli_alive`; `wait_for_pid_gone` helper removed (unused after the replace_session rewrite); `pid_alive` retained for the supervisor's own `_lock_pid_alive` check against `supervisor.lock`. Spawn-time meta no longer writes a `cli_pid: None` placeholder. `docs/agent-supervisor.md` updated; tests swap `monkeypatch.setattr(asv, "pid_alive", ...)` for `monkeypatch.setattr(asv, "tmux_session_exists", ...)` at every replace-session-related site and drop `cli_pid` from fixture metas. 3 new tests for `tmux_session_exists` (rc=0 alive, rc≠0 gone, and module-namespace resolution at call time for monkeypatching) plus 2 new tests for `wait_for_session_gone` (session-disappears-before-deadline and timeout-elapsed paths).

- **`stop_hook_state.json` write discipline: unmaintained keys silently dropped** (WI-joriv, sub-item of WI-razub respawn mechanism): the jq merge in `.agent/hooks/_shared/stop_logic.sh` used to start from `.` (the full existing object) and merge fresh values on top. Any key dropped by a forgotten migration or ad-hoc writer survived every subsequent write indefinitely — which is how five zombie fields (`last_pr`, `last_pr_num`, `last_pr_state`, `pending_hard_todos`, `pending_soft_todos`) from a deleted PR #2926 migration lingered for months before the 2026-04-18 cleanup found them. Fix: the merge now starts from an EXPLICIT extraction of the maintained field set (`{guidance_file, bakeoff_convergence, bakeoff_session_path, bakeoff_session_type, current_branch, last_completed_utc} | with_entries(select(.value != null))`), so any unlisted key is silently dropped on the very next write. The recover-state playbook now documents the full maintained-field table with per-field writers and meanings. Adding a new field requires editing both the extract form in `stop_logic.sh` AND the playbook table — documented at both sites so future contributors don't have to discover the rule by regression. New `tests/test_stop_hook_state_write_discipline.py` exercises the jq expression against zombie-key fixtures, asserts partial-key preservation, empty-object pass-through, end-to-end merge behavior, and guards both the code/doc sync.

### Added

- **Intent/mode split in `scripts/loop-toggle`** (WI-pobon, sub-item of WI-razub respawn mechanism): new per-workstation file `autonomous_intent.txt` at repo root (gitignored) records the human's project-level autonomous-mode intent, separately from `AUTONOMOUS_MODE.txt` which continues to reflect the current session's runtime mode. Two new flag forms on `loop-toggle` write each file independently: `--set-intent MODE` writes intent only, `--set-session-mode MODE` writes the session mode only. The existing bareword form (`loop-toggle DEEP`) continues to write BOTH files so today's human UX is preserved. The circuit-breaker trip path in every vendor stop hook (`claude-code/stop.sh`, `cursor/stop.sh`, `gemini-cli/after-agent.sh`, `codex-cli/notify.sh`) and `session_end_logic.sh` now use `--set-session-mode off` instead of the bareword `off`, so a stuck session can deactivate itself without suppressing the project intent the forthcoming agent-supervisor daemon will consult. `loop-toggle status` surfaces both values and warns when they diverge. Foundation for WI-razub; remaining sub-items (WI-joriv stop-hook write discipline, WI-sipov heartbeat, WI-rofuv supervisor daemon, WI-nusor replacement sequence, WI-sakod respawn-aware session-start, WI-batob vendor parity docs) build on this split.

- **Per-handler forward slices emitted by `run`** (WI-sihok): `hypergumbo run <repo>` now emits `slice.handler.<METHOD>.<path-sanitized>.json` alongside `hypergumbo.results.json` for each detected route handler (concept=route OR kind=route, test-file handlers excluded), using the bakeoff-proven forward-slice parameters (`--exclude-tests --exclude-imports --hub-threshold 100 --max-hops 10 --max-files 200`). Capped at 25 handlers by default; overflow is recorded in a companion `slice.handler.index.json` with pointers to re-derive on demand (`hypergumbo slice --entry <id>`). Handlers are deduplicated by symbol id, with all (method, path) registrations merged into the emitted file's `meta.routes` list; filename falls back to `slice.handler.<handler-name>.json` when route metadata is incomplete. Each slice is schema-stamped with `meta.entry_kind="handler"` and `meta.slice_params` so LLM consumers can distinguish handler-rooted slices from generic `--entry` slices without reparsing the filename. Closes the "not answered by any current artifact" gap identified by the DEEP-mode alertmanager assessor on 2026-04-10 — the bakeoff's default slice artifacts root at `main()`, not handlers, so "what does this handler touch?" required a follow-up invocation. New CLI flags: `--no-handler-slices` (disable) and `--max-handler-slices N` (raise/lower the cap).

### Changed

- **Stop hook: process-aware pause replaces 150s blanket sleep** (WI-varid): `.agent/hooks/_shared/stop_logic.sh` no longer unconditionally sleeps 150 seconds on every stop hook fire when there are TODOs. Instead, it inspects running processes via `pgrep -u $USER -af` against a configurable list of "watched" long-running commands (defaults: `pytest`, `python -m pytest`, `smart-test`, `bash ./scripts/auto-pr`, `bash ./scripts/merge-pr` — bakeoffs and ci-debug are deliberately excluded so the agent can do pipeline-overlap work in parallel rather than block on them). When at least one watched process is alive, the hook polls every 3s (configurable) and emits a single `.` to stderr per poll as a heartbeat, exiting the loop when the process is gone or after 1800s as a safety cap. When no watched process is alive, the hook skips the pause entirely and returns immediately. The circuit breaker hash semantics are unchanged: the current sentinel hash is appended to `HASH_FILE` exactly as before, so the 5-consecutive-identical-hashes trip threshold still protects against runaway loops. False-positive guard: shell-eval wrappers (`bash -c "..."` / `sh -c "..."`) whose cmdline merely contains a watched pattern as data are filtered out so the hook does not wait on its own caller's heredoc text. New optional config keys under `stop_hook:` in `.agent/tracker/config.yaml` — `watched_process_patterns`, `watched_poll_seconds`, `watched_max_wait_seconds` — let users override the defaults without code changes. Net effect on agent UX: legitimate stops with no background work return immediately (was: 150s wasted); long pytests / auto-pr runs surface as one fire with a dot-stream heartbeat (was: 4-5 near-identical `stop_guidance_*.md` files in `guidance_log/`).

### Fixed

- **auto-pr `.ops` backup/restore no longer overwrites concurrent tracker writes** (WI-buhov): when `do_merge` in `scripts/lib/forgejo-api.sh` detects a head-behind-base merge rejection, it backs up tracker `.ops` files, rebases, then restores the backup. The old restore path used `cp` which clobbered any ops appended during the run — either agent-driven `discuss` / `add` / `update` calls made during CI polling, or `tracker: sync` commits pulled in by the rebase. Observed 2026-04-17: two WI-ripuz `discuss` entries vanished from the TUI mid-session. Fix: new `_ops_union_restore_file` helper (in `forgejo-api.sh`) performs an order-preserving line-level union (`awk '!seen[$0]++'`) — semantics-correct for append-only ops files, as guaranteed by their `merge=union` gitattribute. Replaces both restore call sites (rebase-success and rebase-failure paths). Regression tests in `tests/test_autopr_ops_union_restore.py` cover fresh-target copy, identical-side no-dup, backup-has-extra, target-has-newer, both-sides-unique, target-ordering preservation, and missing-backup no-op.

### Changed

- **Linker subcategory vocabulary restored** (ADR-0003-ext): the Protocol / Bridge / Framework subcategory taxonomy from ADR-0003 §2.4, unused since the ADR was authored on 2026-01-07, is now a first-class classification. Every linker module's docstring declares its subcategory (Protocol / Bridge / Framework / Infrastructure). `docs/LINKERS.md` renamed from "Cross-Language Linkers" to "Linkers" and now enumerates all 45 linker files with a Subcategory column. `docs/hypergumbo-spec.md` §7 renamed and restructured; ADR-0003/0010/0012/0015 corrected in-place where the "cross-language linker" framing was factually wrong. The "Infrastructure" subcategory is a fourth addition to ADR-0003 §2.4's original three, covering graph-structural utilities (`containment`, `inheritance`, `js_module`, `build_target`, `dependency`, `vue_component`). Prioritisation of new linker work follows `INV-nimuj` (false-positive-reduction volume on the prospector corpus, not novelty of language pair).

### Added

- **HTTP linker: JS/TS backtick template-literal fetch/axios with module-const folding** (WI-sijoh, follow-up to WI-tinip): the HTTP linker now recognises `` fetch(`TEMPLATE`) `` and `` axios.METHOD(`TEMPLATE`) `` backtick-quoted URLs and folds them against module-scope string constants extracted from the same file. Unresolved `${NAME}` slots are handled heuristically — a leading `${VAR}/` is treated as a host/base URL and stripped (matching the Elm `apiUrl ++ "/path"` idiom handled in Phase 1), a middle slot preceded by `/` is kept as a path-segment parameter `{NAME}` (so FastAPI-style `:id`/`{id}`/`<id>` route patterns still match via the existing `_match_route_pattern`), and an inline slot not preceded by `/` truncates the URL (it is template continuation, not a single path segment). A new prefix-match fallback in `link_http` covers the truncated case: when `url_type="variable"` and the literal prefix has ≥2 non-empty path segments (e.g. `/api/v1`), any route path that starts with the prefix becomes a candidate. Closes the second concrete test case named in WI-tinip's title — `ui/mantine-ui/src/api/api.ts` `fetch(\`${pathPrefix}/${API_PATH}${path}${queryString}\`)` now links through to the Go route table. The Elm `let`-bound / `String.join` idioms remain tracked separately in WI-rosan.
- **HTTP linker: Elm client detection (WI-tinip Phase 1)**: the cross-language HTTP linker (`packages/hypergumbo-core/src/hypergumbo_core/linkers/http.py`) now scans `*.elm` files and emits `http_calls` edges from Elm HTTP calls to server-side route handlers (Go, Python, Ruby, Java, JS — anything that exposes a `route:` concept). Three idioms are recognised: (1) the `Utils.Api.get|post|put|patch|delete|head|options (apiUrl ++ "/path") ...` wrapper-module style used by Alertmanager/Prometheus-style Elm UIs, (2) the `Http.get|post|... { url = "...", ... }` record form from the `elm/http` core library, and (3) both orderings of `Http.request { method = "POST", url = "...", ... }`. Closes UAT DQ-09 for the Alertmanager Elm→Go handler linkage. The `let`-bound/`String.join`-built URL forms (a minority of real-world calls) are deferred to a follow-up full Elm analyzer.

### Changed

- **Dead-code-prospector categorizer expanded from 8 to 46 gap categories** (WI-vupin): `scripts/dead-code-prospector-run.py::_categorize_candidate` now accepts an optional `language` argument and applies language-gated rules (Rust trait impls, Python dunders / Django ORM / Airflow framework, Go receiver methods / k8s watchers / Cilium eBPF dispatch, Java JavaBean accessors / Kafka streams internals / Spring bean config, TS/JS React lifecycle / Redux / Superset chart plugin / Apollo). Reduces `uncategorized` rate on the WI-tubot 2026-04-11 prospector corpus (92,218 candidates across 11 polyglot repos) from **94.0% → 43.5%** — below WI-vupin's success-criterion threshold of 50%. Convergence observed at the 3pp-per-iteration bar: pass-3 was 6.2pp, pass-4 was 3.7pp, pass-5 was 2.3pp, so further heuristic additions overfit the corpus without producing new actionable signal (see WI-vupin discussion for full reflection on why heuristic categorization plateaus and which alternative strategies — class-hierarchy-aware linker hints, framework annotation detection, LLM-assisted clustering — would be needed to categorize the remaining residual).

### Fixed

- **Solidity file-level `using X for Y;` applies to calls inside contracts** (WI-jovur / UAT BUG-13): the Solidity edge extractor keyed its `using_libraries` map by the call-site's enclosing contract only, so a file-level `using` directive (keyed under `""`) was invisible to any call that lived inside a contract or library in the same file. Closes the shellcheck-style pattern where `Memory.load` was flagged dead despite 6+ callers via `using Memory for Slice;` at file scope invoked as `s.load(0)`. Fix: when dispatching a member call inside a contract, union the contract-scoped library set with the file-level (`""`) set before probing for `{Library}.{method}`. Works across files: the library's qualified symbol (`Memory.load`) is resolved through `global_symbols`.
- **`test-coverage` recognises framework-tagged tests outside test paths** (WI-dulav / UAT BUG-11): `cmd_test_coverage` now treats any `function`/`method` whose `meta.concepts` contains a concept starting with `test` (emitted by the framework-patterns enrichment layer — see `frameworks/test-frameworks.yaml`) as a test, in addition to the existing path-based heuristic. Same rule excludes them from coverage targets so a test is never its own thing-under-test. Closes the shellcheck scenario where Template-Haskell `$forAllProperties` discovers 2214 `prop_*` property functions in `src/` modules (not `test/`), producing 0% reported coverage before this fix — the QuickCheck `prop_*` rule in `test-frameworks.yaml` tagged them correctly, but `test-coverage` consulted paths only. Generalises to any language whose framework YAML emits `test_function` / `test_suite` / `test_lifecycle` / `test_fixture` concepts.
- **`dead-code-maybe` unconditionally drops symbols in generated files** (WI-jifup / UAT 2.6.0 round 05 precision): any candidate whose `supply_chain.is_generated_file` is True is now filtered out of the dead-candidate list before ranking. Previously, ~57 symbols in the four openapi-gen utility files (`CancelablePromise.ts`, `request.ts`, `OpenAPI.ts`, `ApiError.ts`) leaked past the centrality-based demotion in WI-vubad / WI-tizij (ranking dims generated code, but `dead-code-maybe` never consulted centrality). No opt-in flag: generated code is never actionable dead code — you regenerate it, you don't delete it manually. Applies uniformly to every language (Go swagger, Java protoc, Python `@generated`, TS `openapi-gen`, etc.) because `is_generated_file` is a language-agnostic flag.

### Added

- **Kotlin extension-function call-site dispatch** (WI-visaz, deferred half of WI-fuhav): `receiver.extFn()` call sites now emit `calls` edges to their extension-function definition when the receiver's declared type matches the extension's receiver type. WI-fuhav shipped the definition side (marking extensions with `is_exported=True` and recording `meta.extension_receiver`); WI-visaz wires the call-resolution side. Pass 1 builds an `extension_index` keyed by receiver type from the already-tagged symbols; Pass 2 consults it as a new Case 3b after Case 3 (the class-method resolution) so class methods continue to win over extensions per Kotlin semantics. Generic receivers collapse to their base name at both index-build and lookup time — `fun List<Int>.sumSafe()` matches `nums: List<Int>` via the `List` key. Cross-file resolution works because the extension index is built from `global_symbols`. Evidence type: `ast_call_extension` (confidence 0.80). Unblocks forward-slice, reverse-slice, and dead-code-maybe from treating spring-boot's `SpringApplicationExtensions.kt` and similar idiom-heavy Kotlin extensions as `is_exported` orphans.
- **Unresolved-call edges for bare global `Object.method()` in JS/TS** (WI-pinop, follow-up to WI-banaf / WI-vurop): extends the unresolved-call fallback to the no-import case. `console.log()`, `localStorage.setItem()`, `sessionStorage.getItem()`, `document.cookie`-style accesses, `navigator.sendBeacon()`, `window.fetch()`, and `Deno.readFile()` now emit `calls→{lang}:{obj}:0-0:{method}:unresolved` edges whenever the object is a known browser/runtime global and no import binding shadows it. Closes the last gap in the io-boundary fallback sequence for browser-first projects that never `import` anything (ubiquitous in React/Vue/Svelte apps). New `JS_KNOWN_GLOBALS` frozenset in `js_ts.py` (mirrors the module names in `io_primitives/javascript.yaml`). Shadowing preserved: a named or namespace import of the same name still routes through the existing WI-banaf / WI-vurop paths with the user-module hint, never the global.
- **Method-call recovery linker for chained `Class().method()` calls** (WI-gigoz / UAT BUG-14, Path B′): new `method-call-recovery` linker (priority 35) rewrites the `calls→Class` + `unresolved-call(name=foo)` pair that analyzers emit for chained calls like `CliRunner().run(args)` into a direct `calls→Class.run` edge whenever the class has a `contains` child with that name. Before this, forward slice dead-ended at the class node (the slice intentionally does not fan out through `contains` to prevent sibling-method explosion — see `test_forward_slice_class_reaches_method_then_no_sibling_explosion`), so slicing from `main()` returned 2 nodes instead of the ~19 nodes available when slicing the method directly. The linker is language-agnostic: it consumes the existing `calls→Class` / `instantiates→Class` hints plus the `unresolved-call` convention (`{lang}:{module}:0-0:{name}:unresolved`) already emitted by JS/TS, Java, Kotlin, Python, and Go analyzers. Disambiguation uses class-membership as the primary filter (the chosen class must actually contain a method with the unresolved name) and line-proximity as the tiebreaker. The original `calls→Class` constructor edge is preserved; the unresolved edge is left in place as a harmless dangling edge. Runs after `containment` (priority 12) so `contains` edges are available.
- **Haskell module exports recognized as dead-code seeds** (WI-buvun / UAT BUG-12): the Haskell analyzer now parses module headers (`module Foo (publicFn, Type(..)) where`) and marks listed symbols as `is_exported=True`. Modules with no export list (`module Foo where`) get the Haskell default — every top-level binding is exported. Type names in the export list count for data/class declarations. Instances follow their class/type: an `instance ClassName TypeName` is considered exported when either name is in the export list (instances aren't named directly in Haskell exports but are externally reachable through them). Before this, every Haskell symbol had `is_exported=False` and the `exports` / `tests` seed sets for `dead-code-maybe` were useless on Haskell — UAT showed shellcheck had 0 recognized exports.
- **Yesod framework detection + pattern set** (WI-vabiv / UAT BUG-16): Yesod (Haskell — haskellers) is now detected from `*.cabal` / `package.yaml` dependencies (`yesod`, `yesod-core`, `yesod-auth`, `yesod-persistent`) and a new `frameworks/yesod.yaml` ships patterns for the Yesod conventions: `mkYesod` / `mkYesodData` / `mkYesodSubData` / `parseRoutes` quasi-quoter calls, Warp runner (`warp`/`warpTLS`/`toWaiApp`), `Yesod` / `YesodSubsite` typeclass memberships, `RenderRoute` / `ParseRoute` router class, standard mixin typeclasses (`YesodPersist`, `YesodAuth`, `YesodBreadcrumbs`), and the `<method><Resource>R` handler naming convention (getHomeR, postUserR, deleteUserR, ...). Route materialization for the non-GET/non-POST methods is a later materializer expansion; concepts are attached today so downstream analysis recognizes the handlers as web-handler entry points.
- **Elixir I/O primitive catalog** (WI-vibur / UAT BUG-09b): new `io_primitives/elixir.yaml` fixes the "0 boundaries on plausible (Phoenix/Ecto)" gap. Covers Elixir stdlib (`File.read`/`write`/`stream!`, `IO.puts`/`write`/`read`, `System.cmd`/`get_env`, `Logger.*`), the idiomatic HTTP-client galaxy (`HTTPoison`, `Tesla`, `Req`, `Finch`, `Mint.HTTP*`, plus Erlang `:httpc`), Phoenix server surface (`Phoenix.Router.get/post/...`, `Phoenix.Controller.render/json`, `Plug.Conn.send_resp/read_body`, `Phoenix.Channel.broadcast`, `Phoenix.Endpoint.broadcast`), databases (Ecto.Repo read/write verbs, `Ecto.Multi`, `Postgrex.query`, `MyXQL.query`, `Redix.command`), and IPC (`GenServer.call/cast`, `Process.send`, `Oban.insert`, `Task.async`). `elixir` added to `_CATALOG_PARENTS` with `erlang` as parent so atom-access into Erlang (`:gen_tcp.send`, `:ets.lookup`, `:file.read_file`) is still matched. Elixir-specific `ambiguous_names` prevents Elixir pipe/Enum verbs and scope functions from producing short-name false positives.
- **Kotlin I/O primitive catalog** (WI-rujos / UAT BUG-09d): new `io_primitives/kotlin.yaml` with Kotlin-specific entries. Kotlin was previously aliased to `java.yaml` verbatim via `_CATALOG_ALIASES`, which produced only 1 boundary (net_send) on detekt because Kotlin idiom uses extension functions and top-level stdlib functions that have no Java analog. The new catalog covers: `kotlin.io` File extensions (`readText`, `writeText`, `forEachLine`, `useLines`, `copyTo`, `walk`), `kotlin.io.path` Path extensions (Kotlin 1.5+), top-level `println`/`print` (receiver `kotlin.io.ConsoleKt`), ktor client + server, `android.util.Log`, `kotlin-logging` (`mu.KLogger` and the 5.x relocated `io.github.oshai.kotlinlogging.KLogger`), and Exposed ORM read/write. Kotlin-specific `ambiguous_names` prevent scope functions (`apply`, `run`, `let`, `use`) and coroutine/Flow verbs (`send`, `receive`, `collect`) from producing short-name false positives. `kotlin` moved from `_CATALOG_ALIASES` to `_CATALOG_PARENTS` so the Java parent still provides the raw `java.io/java.net/JDBC/SLF4J` entries for Kotlin code that uses those APIs directly.
- **JVM dependency manifest scan skips test-fixture subdirectories** (WI-bukof / WI-duhom scope expansion from WI-sudug): `parse_gradle_dependencies` and `parse_maven_dependencies` in `jvm_deps.py` now skip subdirectory scans under test-fixture directories (testFixtures/, testdata/, fixtures/, tests/, etc.) via `paths.is_test_file`. Prevents test-scaffolding build.gradle/pom.xml declarations from polluting tier classification. Also expands `paths.is_test_file` to recognize `testfixtures` (the canonical Gradle sibling-of-main convention).
- **Framework manifest detection skips test-fixture directories** (WI-sudug / UAT BUG-19): `_find_manifest_files` (profile.py) now skips `package.json`, `pom.xml`, etc. when they live under a test-fixture path (anything that `paths.is_test_file` recognizes — tests/, testdata/, fixtures/, src/test/resources/, testFixtures/, etc.). Before this, detekt — a pure Kotlin static-analysis tool with no React code — was reporting `react` as a detected framework because its test fixtures contained `package.json` files referencing React. Test fixtures do NOT represent real project dependencies, so they should not drive framework labels. Existing non-test manifest discovery (monorepo subdirectories, backend/frontend splits) is unaffected because `is_test_file` only matches paths with test-directory segments.
- **Route materializer dedupes against analyzer-emitted routes** (WI-tizad / UAT DQ-01): `materialize_route_symbols` now pre-populates its dedupe set with `(method, path)` pairs from existing `kind="route"` symbols in the input. Before this, Django projects double-reported routes: the Python analyzer emits `GET /users/` at the `urls.py` registration, AND the view class's `get` method (enriched with `concept=route` by framework patterns) caused the materializer to emit another `GET /users/` symbol at the method location. UAT 2026-04-13 saw pretix reporting 985 routes where only ~500 were unique patterns. ANY-method routes (class-based views pending WI-lojoh expansion) are intentionally excluded from the pre-populate so the materializer can still emit specific-method variants for CBV handlers.
- **Class-level annotations propagate to methods for `--exclude-annotated`** (WI-rumij / UAT DQ-06): `hypergumbo dead-code-maybe --exclude-annotated` now also checks the containing class's annotations/decorators/concepts when a method's own meta is empty. Before this, Spring-style controllers (`@RestController` / `@Controller` / `@Service` at the class level with no method-level annotations) had their handler methods slip past the exclusion filter and appear as false-positive dead code. The fix builds a `method_to_class` index from `contains` edges (where src is a class/interface/struct/trait/enum), then consults the parent class's meta when the method's own meta has no annotation signal. Helps Spring, Django-style class-based views, and any framework that registers via class-level decoration. Does not yet cover parameter-level annotations (e.g. Spring's `@ModelAttribute`) — that's a separate analyzer-side fix tracked for follow-up.
- **Gradle/Maven dependency manifest for Java/Kotlin tier classification** (WI-duhom): boundary nodes from unresolved Java/Kotlin imports are now classified using declared dependencies from `build.gradle`, `build.gradle.kts`, and `pom.xml`. Direct dependencies (groupId matches the import path prefix) get tier 2 (INTERNAL_DEP); unknown imports remain tier 3 (EXTERNAL_DEP). Before this, Gradle/Maven projects like Kafka had near-zero `external_dep` nodes because external dependencies aren't physically on disk. New `jvm_deps.py` module parses both Groovy and Kotlin DSL Gradle files plus Maven pom.xml. `DependencyManifest.classify_import` prefix matching now supports both `/`-separated (Go) and `.`-separated (Java/Kotlin) paths.

### Changed

- **`test-coverage` now surfaces per-language false-negative caveats** (WI-hular / UAT DQ-05): every text-format `hypergumbo test-coverage` run prints a footer documenting (a) the empirical ~20% recall gap (the tool has high precision but limited recall — tests that reach production code via reflection / dispatch / visitor patterns produce "untested" false alarms) and (b) the known per-language blind spots applicable to the analyzed repo (Java/Spring MockMvc, Kotlin PSI visitor, Go YAML reflection, Scala ScalaTest macros, Ruby `described_class`/Rails fixtures, Python pytest fixtures + `parametrize`, JavaScript/TypeScript Jest `describe.each` + ESM tree-shaking, C# xUnit `[Theory]`/Moq proxies). JSON output gains a structured `caveats` field with `recall_disclaimer` and per-language entries. The `--help` epilog explicitly tells users to treat 'untested' as 'unreached by static call graph', not 'definitely untested'. Languages without a documented blind spot are silently skipped so output stays focused.
- **Unified path argument across subcommands** (WI-munuv / UAT UX-01): every subcommand that takes a repo path now accepts both `hypergumbo <cmd> /path` (positional) and `hypergumbo <cmd> --path /path` (flag) interchangeably. Previously, some commands took only the positional form (`sketch`, `run`, `slice`, `test-coverage`, `dead-code-maybe`) and others only `--path` (`search`, `routes`, `explain`, `symbols`, `io-boundaries`, `verify-claims`), causing errors when users carried syntax between commands. Setting both forms in the same invocation is a user error and exits 2 with a clear message. New helper `_add_path_argument()` centralizes the convention; `main()` post-process resolves the two destinations into a single `args.path` so command functions need no changes.
- **`routes` excludes test-file routes by default** (WI-godos / UAT DQ-02): UAT found 14% of plausible's reported routes were from test files, polluting the visible output. New default behavior excludes them; `--include-tests` opts back in. The legacy `-x/--exclude-tests` flag is preserved as a no-op alias for backward compatibility — existing scripts continue to behave correctly.

### Fixed

- **Django CBV routes now expand to one route per declared HTTP method** (WI-lojoh / UAT BUG-08): `path("/foo/", FooView.as_view())` and `re_path(r"^foo/$", FooView.as_view())` previously emitted a single route hardcoded as `[GET]` regardless of which methods (`get`, `post`, `put`, `patch`, `delete`, `head`, `options`, `trace`) the view class actually defined. Confirmed on pretix: `InitializeView.post`, `UpdateView.patch`, `RevokeKeyView.delete` were all reported as `[GET]`. The Python analyzer now flags class-based view registrations with `is_class_based_view=True` in route metadata and emits them with `http_method="ANY"`. A new post-pass `expand_class_based_view_routes` in `framework_patterns` walks the global symbol list, builds a ClassName → declared HTTP methods index from `Cls.<method>` method symbols, and replaces each ANY route with one variant per declared method (e.g. `UpdateView` with `.patch` and `.put` becomes two routes — PATCH /update/ and PUT /update/). When the view class lives outside the analyzed repo (e.g. `django.contrib.auth.views.LoginView` imported but not defined), the route stays as `[ANY]` rather than fabricating a wrong `[GET]` — honest unknown beats false specificity. Verified on a 4-class re_path repro: `[GET] /^get/$ -> GetView.get`, `[POST] /^initialize/$ -> InitializeView.post`, `[DELETE] /^revoke/(?P<pk>\d+)/$ -> RevokeKeyView.delete`, `[PATCH]` + `[PUT] /^update/(?P<pk>\d+)/$ -> UpdateView.patch / .put`.
- **`--config-extraction=embedding/hybrid` warns when sentence-transformers is missing** (WI-fumap / UAT DQ-07): both modes silently degraded to heuristic when `sentence-transformers` was not installed, producing output identical to `--config-extraction=heuristic` and giving users no signal that the requested mode never ran. The dispatcher now pre-checks embedding availability via a new `_embedding_extraction_available()` helper, emits a one-shot stderr notice when the requested mode requires embeddings but the package is missing (`hypergumbo: --config-extraction=embedding requested but sentence-transformers is not installed; falling back to heuristic mode... WI-fumap`), and explicitly falls back to heuristic. Module-level `_embedding_fallback_warned` flag dedupes the warning across multiple `_extract_config_info` calls within the same process. Heuristic mode never warns; when embeddings ARE available, no warning fires regardless of mode. Underlying mode behavior unchanged when the embedding stack is present — this is purely a visibility fix for the silent-fallback ergonomics gap.
- **Java `size`, `length`, `copy`, `find` no longer misclassified as fs_read** (WI-gonav / UAT BUG-10): `List.size()`, `Map.size()`, `String.length()`, and similar bare method calls on the generic JDK collection/string APIs were being tagged as `java.nio.file.Files.size`, `java.io.File.length`, `java.nio.file.Files.copy`, and `java.nio.file.Files.find` respectively. The Java io-boundary matcher already rejects short-name matches on `ambiguous_names`, but `size` / `length` / `copy` / `find` were missing from that list. UAT 2026-04-13 observed the false positive on pretix's Vet.java:66 (`getSpecialtiesInternal().size()`). Resolved calls with proper module context (e.g. `java.nio.file.Files.size` explicitly) still match as fs_read — the ambiguous filter only rejects short-name matches on unresolved-external destinations.
- **Scala framework detection reads `project/*.scala` and `project/*.sbt`** (WI-piban): the SBT meta-build convention puts every real library coordinate (`"org.http4s" %% "http4s-dsl" % ...`) in `project/Dependencies.scala`, while the top-level `build.sbt` only references scala helper objects (`libraryDependencies ++= Dependencies.http4sClient`). Before this fix, `_detect_scala_frameworks` scanned only `build.sbt` and missed every project that uses the standard SBT meta-build split — including docspell, which imports `org.http4s` on hundreds of lines yet produced `profile.frameworks=[]`. The detector now concatenates `build.sbt` (recursive, existing behavior) plus all `*.scala` and `*.sbt` files in the top-level `project/` directory (Dependencies.scala, plugins.sbt, Build.scala). Surfaced via UAT follow-up on WI-vabiv — "YAML exists" is not evidence the detector actually fires on real repos.
- **verify-claims surfaces languages where taint-flow has no catalog** (INV-javam, taint-flow side): when `hypergumbo verify-claims` evaluates a `taint_flow` constraint on a repo whose languages have no sources/sinks in the taint catalog, a stderr notice now fires ("no taint-flow catalog for language(s): X, Y. Claims touching these languages are NOT actually verified ... Treat 'confirmed' verdicts on these languages as inconclusive. INV-javam"). Before this, a trivially-passing claim against an unanalyzed language gave false security confidence — the verdict was "confirmed" because no propagation findings existed, not because the code was safe. The notice only fires when taint claims are present (not on pure boundary claims) and only when at least one detected language has zero coverage. JSON output schema unchanged for backward compatibility; the signal goes to stderr for human review.
- **io-boundaries distinguishes "no I/O detected" from "language unsupported"** (INV-javam / UAT DQ-03+DQ-04): previously, `hypergumbo io-boundaries` on a codebase containing an unsupported language (pre-banaf TypeScript, pre-vibur Elixir, pre-rujos Kotlin, Solidity, Nim, ...) returned zero boundaries with no warning — output identical to a genuinely I/O-free codebase, and downstream taint-flow assertions trivially passed with false security confidence. `IoBoundaryCatalog` now carries an `is_supported: bool` field (False when no YAML / alias / parent resolves), `io_boundary.is_language_supported(lang)` exposes this to callers, `cmd_io_boundaries` emits a stderr notice ("no I/O primitive catalog for language(s): X, Y. Zero boundaries reported for these languages does NOT mean the code is I/O-free — INV-javam"), and the JSON output includes a stable `unsupported_languages: []` field so programmatic consumers can detect the condition. Complements the organic catalog expansion in WI-banaf/sakan/rujos/vibur: even after coverage grows, some languages will always lack catalogs and the invariant needs to hold.
- **Laravel `apiResource()` no longer emits phantom HTML-form routes; `.except()` / `.only()` honored** (WI-jorim / UAT BUG-07): `Route::apiResource('posts', PostController::class)` now produces 5 routes (index/store/show/update/destroy) instead of 7 — the `GET /create` and `GET /{id}/edit` HTML-form routes that don't exist for an API resource are dropped. Chained `.except([...])` / `.only([...])` modifiers (variadic strings or array literal) are now parsed and applied to both `resource` and `apiResource`. Multiple modifiers compose in source-order. Variable args (e.g. `->except($actions)`) and unrelated chained methods (e.g. `->name(...)`, `->middleware(...)`) are correctly ignored. On koel: ~40 phantom routes were eliminated (~19% of the 207 originally reported). New constant `LARAVEL_RESOURCE_ACTIONS` and `LARAVEL_API_RESOURCE_EXCLUDED_ACTIONS` make the action set self-documenting.
- **Subcommand parser cleanup** (WI-balij / UAT UX-03 + UX-04): two argparse plumbing rough edges.
  - `hypergumbo foobar` no longer silently inserts `sketch` and reports `path does not exist`. The dispatch in `main()` now detects when the first positional looks like a subcommand attempt (no path separators, no leading `.`/`~`, doesn't exist on disk) and prints `'foobar' is not a valid subcommand` plus a `Did you mean: ...` line via `difflib.get_close_matches`. Exits 2.
  - `hypergumbo sketch . --debug` no longer fails with `unrecognized arguments: --debug`. The `--debug` flag is now stripped from `argv` in `main()` before `parse_args`, so it works in any position. The original `hypergumbo --debug sketch .` form still works (regression-tested).
- **Embedding-model load no longer dumps progress bars to stderr** (WI-gatot / UAT UX-02): the first analysis per session previously emitted ~199 weight-loading progress bars and an HF auth-prompt warning to stderr — output that ignored `--no-progress` and polluted piped consumers. New `_hf_noise.suppress_hf_noise()` runs at `sketch_embeddings` import (well before `sentence_transformers` and its transitive `huggingface_hub`/`transformers`/`safetensors` imports cache the env), setting `HF_HUB_DISABLE_PROGRESS_BARS=1`, `TRANSFORMERS_VERBOSITY=error`, `HF_HUB_DISABLE_SYMLINKS_WARNING=1`, `TRANSFORMERS_NO_ADVISORY_WARNINGS=1`. Uses `setdefault` so explicit user overrides (e.g. `TRANSFORMERS_VERBOSITY=info` for debugging) are preserved.
- **`auto-pr` Exit 2 (timeout) soft-retry** (WI-dotod): the hung-run retry loop in `scripts/auto-pr` only fired on Exit 3 (no CI jobs started); Exit 2 (CI started but timed out) had no built-in recovery and escalated immediately to Scenario B. On 2026-04-14 the WI-banaf PR lost ~3h to this — a fresh `auto-pr` invocation 3h later merged in 8 min, indicating the stuck state was tied to the PR rather than infrastructure. On Exit 2 the script now re-polls once with a 300s timeout, then does one close-PR + repush, before escalating. Exit 3 loop unchanged; if the cascade produces Exit 3 the existing 4-retry loop picks it up. Adds an `AUTOPR_TEST_POLL_EXITS` seam in `scripts/lib/forgejo-api.sh` for testing.
- **`-e/--exclude` glob normalization** (WI-zirik / UAT BUG-01): user patterns `ui/`, `ui/**`, `**/ui/**`, `**/ui` now behave consistently with the bare-name form `ui` (per-name fnmatch previously silently dropped any pattern containing `/`). Path-anchored patterns such as `cmd/server.go` are honored against the relative path. Affects both `run` and `sketch`; structure-tree code uses the same normalization.
- **README / markdown heading bleed** (WI-bilul / UAT BUG-06): when a `.md` / `.mdx` / `.markdown` / `.rst` file is rendered as Additional Files Content, its ATX headings are now demoted by 2 levels so they cannot compete with hypergumbo's structural H2 sections. `# My Project` becomes `### My Project`; `## Installation` becomes `#### Installation`. Previously a README with 12 `##` sections (gemini-cli) or 2 (alertmanager) added that many phantom structural headings to the sketch.
- **Token budget validation** (WI-pokor / UAT BUG-02 + BUG-03): `-t 0` and negative `-t` values are now rejected by argparse with a clear "token budget must be a positive integer" error. Previously `-t 0` was silently treated as "no budget" (defaulting to 8000) and `-t -1` produced header-only output with exit code 0. Applies to both `sketch` and `explain`.
- **Single-file input exits cleanly** (WI-zujum / UAT BUG-04): `hypergumbo run` / `sketch` pointed at a file (not a directory) now print an actionable hint and `sys.exit(1)` instead of crashing with `NotADirectoryError` from `Path.iterdir()`. The hint points at the file's parent directory as a likely target.
- **Quieter partial-linker warnings on polyglot repos** (WI-vasir / UAT UX-05): suppress the partial-install warning when the only met requirement is a language-file presence check (`*_files` name suffix). Every polyglot repo tested hit 1-7 false warnings per invocation (NAPI, TAURI_IPC, SOLIDITY_ABI, WASM_BINDGEN, IPC, LUA_FFI, RUBY_FFI, PYFFI) because "has JS/TS files" is not signal of intent to use those integrations. Warnings still fire when a pattern-level requirement is met. Verified on alertmanager: 8 warnings → 1 (-87%).
- **`--require-section` now actually works** (WI-nakam / UAT BUG-05): the flag was a no-op on tight budgets because `max_tokens <= base_tokens` triggered an early-return that bypassed all section gates. The fix now (1) skips the early-return when any required sections were requested, (2) forces analysis to run regardless of remaining budget when a required section depends on it, (3) makes `_section_ok` return True unconditionally for required sections, and (4) skips the final `truncate_to_tokens` so the output may exceed `max_tokens` rather than chop the section we were asked to keep. Verified on alertmanager: at `-t 500` (well under the 1000-token base) `--require-section "Key Symbols" "Entry Points"` now adds both sections, vs no effect before.

### Performance

- **Cached secret-scan results across warm sketch runs** (WI-julir / UAT BUG-20): `scan_content_cached` keys gitleaks output by sha256 of the sketch content and stores up to 8 entries in the per-state results cache directory. A warm `hypergumbo sketch` now completes in roughly the `--no-secret-scan` time (~7s on alertmanager) instead of paying the ~8s gitleaks cost on every invocation. The cache invalidates automatically when the repo state hash changes (the cache lives inside the per-state directory).

### Added

- **Java I/O primitive catalog expansion** (WI-sakan / UAT BUG-09c): grew from ~136 primitives to 312 by adding full JDBC + JPA + Hibernate + Spring Data coverage (new `db_read` / `db_write` boundaries, 100+ entries spanning `java.sql.*`, `javax.persistence` / `jakarta.persistence`, `JdbcTemplate`, `CrudRepository`, `JpaRepository`, `org.hibernate.Session`), logging-facade coverage (SLF4J, Log4j 1.x / 2.x, Logback, `java.util.logging`), Apache HttpClient 4.x & 5.x, Spring WebClient, Unirest, Retrofit, and Apache Commons IO (`FileUtils`, `IOUtils`). Kotlin inherits via catalog alias.
- **Unresolved-call edges for TS/JS member calls on namespace/default imports** (WI-vurop, follow-up to WI-banaf): extend the WI-banaf fix to cover `import * as fs from 'node:fs'; fs.readFileSync()` and `import axios from 'axios'; axios.get()`. Emits an unresolved-call edge with the import path as module hint when the namespace-alias method call doesn't resolve to an intra-repo symbol. Verified: apollo-server boundaries 7 → 14 (env_read +6 via os.*); create-next-app 27 → 35.
- **TypeScript/JavaScript I/O boundary tracing** (WI-banaf / UAT BUG-09a): the JS/TS analyzer now emits unresolved-call edges for bare-name calls to named imports (e.g. `import { existsSync } from 'node:fs'; existsSync()`), so the io-boundaries pipeline can match them against the catalog. Browser-API entries added to the JavaScript catalog: WebSocket, EventSource, BroadcastChannel, XMLHttpRequest, navigator.sendBeacon, localStorage / sessionStorage / indexedDB / caches, console logging, navigator/window/document env reads, and HTTP-client modules ky/got/superagent/undici. Verified on `nextjs/packages/create-next-app`: total_io_edges 0 → 27 (fs_read/fs_write/subprocess populated). Member calls on namespace and default imports remain a follow-up (WI-vurop).

## [2.6.0] - 2026-04-12

### Changed

- **Stop hook relaxed on CONVERGED bakeoffs** (WI-bibul): guidance now leads with `tracker ready` instead of requiring reflect/aggregate when bakeoff is converged.
- **Bakeoff-deep hub-collision warning** (WI-gapom): `pick_reverse_slice_seeds` warns on seeds with `prod_in_degree > 1000`.
- **`io-boundaries` defaults to production-only** (WI-sifif): test chains excluded by default (was 78% noise). `--include-tests` opts back in.
- **Adaptive hop limit removed from slice**: 3-10 hop limit replaced by `max_files` (100) and hub pruning (50). `--max-hops` still available for explicit control.

### Added

#### Developer experience

- **`auto-pr --tracker-id`** (WI-mokak): on merge, appends a discussion entry to the referenced tracker item citing the PR number and dev SHA.
- **`bakeoff-map` script**: walks bakeoff artifacts and emits a chronological map of sessions with convergence verdicts, pipeline-stage completion, and anomalies.
- **`tracker-path-linter` V1** (WI-sihih): verifies file-path tokens in tracker items resolve to real files. Stale references carry fuzzy-match suggestions.
- **`audit-stale-timestamps` V1** (WI-sofop): checks agent state files for embedded-timestamp drift (e.g. `last_completed_utc` vs file mtime).

#### Slice telemetry

- **Forward-dataflow admission-rule telemetry and option 2 evaluation** (WI-hukoh): `SliceResult.admission_stats` records per-rule counters for edges admitted/rejected during forward dataflow BFS. Telemetry across 4 repos (~188k edges) shows zero additional edges from option 2 — option 1 (writer-source admission) remains canonical. Re-evaluation trigger in ADR-0015 §6.1.

#### Linkers (Framework subcategory)

- **`go_memberlist` linker** (WI-lojuf): `dispatches_to` edges from `memberlist.Create` to the 12 canonical delegate methods (`NotifyMsg`, `GetBroadcasts`, `LocalState`, etc.). Used by alertmanager, consul, nomad, serf, vault.
- **`go_cobra` linker** (WI-gohad): `dispatches_to` edges from `cobra.Command{…}` struct literals to handler functions in `Run`/`RunE`/`PreRun`/`PostRun` and `Persistent*` variants. Used by kubectl, helm, hugo, prometheus, terraform, docker. Package-level `var cmd = &cobra.Command{…}` declarations (WI-lihih) now emit edges from the var symbol when no enclosing function exists.

#### Behavior map

- **`hypergumbo dead-code-maybe` subcommand** (WI-fisam): finds production callables unreachable from entrypoints via BFS over `calls`, `dispatches_to`, `routes_to`, and `wraps` edges. Configurable seed sets (`--seeds {entrypoints,tests,exports,all}`), text/JSON output, `--min-confidence` filtering, ranked by LOC. Cross-language string collision signal (WI-pimig) detects missing linker edges; FFI-signature auto-flag (WI-hadap) boosts FFI-marked candidates; `--exclude-exports` filter (WI-zafab) completes the three-filter set.
- **`Symbol.is_exported` across 5 languages** (WI-zimum, WI-gipag, WI-nimug, WI-fuhav, WI-rupum): new boolean marking public-API callables. Go capitalized identifiers, Rust `pub`/`pub(crate)`, `public` modifier (Phase 1); Python `__all__` / leading-underscore (Phase 2); TS/JS `export` statements; Kotlin extension functions; Scala secondary constructors. `--seeds exports` treats exports as reachability seeds. Drops dead-code false-positive rates 70-83% on Python framework libraries.
- **Generated-code detection and centrality demotion** (WI-tizij, WI-pofin, WI-vubad, WI-sozah): `is_generated` flag on files/symbols detects OpenAPI models, protobuf stubs, K8s code-gen, go-swagger output (`api/v2/restapi/`, `api/v2/models/`, fingerprint files), and `openapi-gen/` directories. Content-based header scanning (`// @generated`, `// Code generated … DO NOT EDIT.`) in the first 4 KiB of 36 text-like extensions. Generated code receives 95% centrality penalty, and `dead-code-maybe` unconditionally drops any candidate whose file is flagged generated (WI-jifup).
- **Test file classification** (WI-rigun-patuz, WI-gifuz): `is_test` decoupled from supply-chain tier as independent axis. Co-located test files (`_test.go`, `.test.js`, `.spec.ts`) classified as tier 1 instead of tier 2.
- **Return-type registry for chained receiver resolution** (WI-kuroj / INV-dihos Phase 1, WI-vadin): `method_return_types` populated during Pass 1 for Go and Java. Enables `x := e.Query(); x.Rows()` resolution via the registry. Inline chained calls like `e.NewQuery().Exec()` resolve at confidence 0.75.
- **Go build-tag-gated alternate definitions** (WI-potun): `//go:build` directives emit `build_tag_alternative_of` edges between same-named symbols in mutually exclusive files.
- **Event-sourcing linker expansion** (WI-zadat): extends event detection to Guava EventBus, generic Java event bus, Go channel-based events, and Go event bus method calls.
- **Go closure wrapper edges** (WI-nikul): route registrations through closure wrappers (e.g. `wrapAgent(api.query)`) emit `wraps` edges. Covers Gin/Echo/Fiber and Gorilla mux/stdlib.
- **Import-based framework validation**: manifest-detected frameworks cross-referenced against import edges. Test-only or unimported frameworks reclassified as `dev_frameworks`.
- **Go tier 2/3 classification via go.mod** (WI-vovuk): unresolved Go external references classified using `go.mod` — direct deps tier 2, indirect/stdlib tier 3. Language-agnostic `DependencyManifest` enables future extension.
- **Gradle multi-project workspace detection** (WI-zizuf): `detect_package_roots()` now parses `settings.gradle` / `settings.gradle.kts` `include` directives. Gradle subprojects are classified as workspace members, fixing degenerate tier distribution on Gradle monorepos like Kafka.
- **Orchestration hub floor for symbol ranking**: functions with out-degree ≥ 20 get a minimum effective in-degree of `sqrt(out_degree) * 0.8`, preventing orchestration hubs (main, run, app) from being buried by within-file dampening.
- **Event edge type weights**: `event_subscribes`/`event_publishes` raised to 0.8 (was 0.5). `dispatches_to` added at 0.6.

#### Language analyzers

- **TLA+** (WI-jurin, WI-ludil): tree-sitter analyzer for `.tla` formal specification files. Extracts module, operator, constant, variable, theorem, and assumption symbols. EXTENDS/INSTANCE as `imports`, cross-references as `references`.

#### Dataflow library_patterns expansions

- **Python AST wiring** (WI-hivud): `python.yaml` ships `library_patterns` for common mutating/reading methods. `annotate_dataflow_ast` now consumes these as a per-language fallback for Python's AST analyzer.
- **Python serialization + file-position primitives** (WI-fogis): 14 patterns — `json.dump`/`pickle.dump`/`yaml.dump` as write, `json.load`/`pickle.load`/`yaml.load` as read, `.seek` as mutate, `.truncate` as write.
- **Cross-language library_patterns** (WI-vinub): name-based access_mode heuristics for Java (25 patterns), JS/TS (23 each), C# (24), and Kotlin (17). Enables `access_mode` annotation for dataflow slicing in these languages.
- **Go state-mutating verbs** (WI-supih): `.Expire`, `.GC`, `.Truncate`, `.Drop`, `.Init`, `.Reload` tagged `access_mode=write`.

#### Training data pipeline

- **Per-session transcript sync** (ADR-0018 amendment, INV-filaj): concurrent sessions now write to isolated files keyed by `session_id` instead of racing on shared state. Session-end rotation atomically promotes files into `.last_*`/`.second_to_last_*` slots. Cursor exempted via sibling check; injection-history sidecar tracks playbook events.
- **v0 corpus cohort backfill** (WI-gigil): `backfill-training-data-cohort-tags.py` writes a sidecar with per-entry `infra_sha`, `playbook_registry_sha`, `main_llm_presumed`, and playbook counts. Re-runnable, non-destructive.
- **Per-entry cohort metadata** (WI-tatuh / INV-gajap): `log_training_example` now writes `pipeline_version`, `infra_sha`, `playbook_registry_sha`, `main_llm`, `vendor`, `vendor_version`, and `scoring_model` on every entry. Distribution shifts discoverable from the corpus alone.
- **Multi-vendor interjection normalization** (WI-nadud): `filter-transcript.py` emits `normalized_user_interjection` rows for user interjections across Claude Code, Codex CLI, and OpenHands. `pipeline_version` bumped to v2.

#### CLI & infrastructure

- **`hypergumbo config <lang>`** (WI-siran): shows all per-language configuration (dataflow patterns, IO primitives, function summaries) in one view. Supports `--format json|yaml|text`.
- **smart-test flock guard** (WI-sinap): concurrent invocations prevented via `flock`. Second invocation exits immediately naming the holding PID.
- **`auto-pr` resilience** (WI-kugob, WI-bahuf, WI-miriz): `list`/`status` detect and `prune` removes stale vPR entries. Already-merged push rejections handled gracefully. New `.git/AUTOPR_LAST_RESULT.json` sentinel records outcome on every exit.
- **`merge-pr close <PR>`** (WI-vonis): close a PR without merging, with optional `--reason` audit-trail comment.
- **Bakeoff-deep integration tests** (WI-gutan): 13 tests covering `init → cohort → cycle → iter-NNN/` end-to-end.

#### Dead-code prospector: polyglot-only filter (WI-zafab)

- `dead-code-prospector-run.py` skips monoglot repos (fewer than 2 languages with ≥10 files each). `--include-monoglot` bypasses.

#### Go encoding/serialization callback entrypoints (WI-pimig Phase 1)

- Go marshal/unmarshal methods (`MarshalJSON`, `UnmarshalYAML`, etc.) detected as `serialization_callback` entrypoints via `go-encoding-callbacks.yaml`. Previously invisible to the call graph.

#### Broker / server lifecycle entrypoint heuristics (WI-nazir)

- Three new naming-tier patterns detect JVM broker lifecycle methods (`*Server.startup/start/run/shutdown`, `*Apis.handle*/process*/dispatch*`, `*Acceptor.run`) as `CONTROLLER` entrypoints. Surfaces the broker request-dispatch surface on Kafka and similar services.

### Fixed

#### Java analyzer

- **Short-name collision** (WI-fuhaj): local classes with names colliding with library classes (e.g. `Logger` POJO vs slf4j `Logger`) no longer absorb cross-file calls. Eliminated 2057+ bogus edges on Kafka.

#### Hook test infrastructure (INV-pofam)

- Fixed silent failures in `.githooks/test_hooks.sh` (stale PID from command-substitution subshell). Wired into CI as a `hook-tests` job.

#### Dataflow annotation preservation

- **`access_mode`/`dest_access_mode` preserved through 4 linkers** (INV-forim): `event_sourcing`, `ipc`, `websocket`, and `message_queue` linkers were overwriting the meta dict, stripping dataflow fields. Fix: pass metadata via `Edge.create` kwarg.

#### Agent state recovery

- **Delete vestigial `.agent/last_stop_check.json`** (INV-jofaf facet 1): removed stale file left after migration to guidance_log.
- **Split stop-hook state file** (INV-jofaf facet 2): split into `stop_hook_state.json` (hook-written) and `agent_notes.json` (agent-written via `scripts/agent-notes`).

#### IO boundaries

- **Go logging reclassified**: `fmt.Print*`, `log.*`, `log/slog.*` moved from `ipc_send` to `logging`. Eliminates 134 false-positive IPC chains on alertmanager. `os.Stdout`/`os.Stderr` remain `ipc_send`.

#### Go analyzer

- **Receiver-type guard for interface_dispatch** (WI-jopar): calls on external/stdlib receivers no longer dispatch to local interface methods of the same name. Eliminated 13 spurious edges on alertmanager.
- **Cross-file struct method aggregation** (WI-hobuk): structural interface matcher now aggregates `struct_method_sets` per package directory. Methods in sibling files within the same package are no longer dropped.
- **Cross-package struct collision** (INV-zomuk): struct method sets keyed by short name caused merging across packages. Fix: iterate per-file. `dispatches_to` edges 3 → 19 on alertmanager.
- **Structural interface arity matching**: satisfaction check now verifies parameter and return counts, not just method names. Removes 463 false `dispatches_to` edges on alertmanager.
- **Cross-package interface dispatch resolution**: cross-package interface fields (e.g. `stage notify.Stage`) now strip package prefix before method lookup.
- **Route resolver receiver-method shadow** (INV-kunam): handler `api.query` (lowercase receiver) couldn't match symbol `API.query` (uppercase type). Fix: prefer same-file candidates via `symbols_by_short_name` index.

#### Symbol resolution

- **Go promoted-method interface satisfaction** (WI-tudib): structural interface matcher traverses embedding chains. Promoted methods included in satisfaction check.
- **Type hierarchy per-language gate** (WI-sukav): `extends` edges in Go, C++, Rust, C# no longer emit `dispatches_to` (composition, not inheritance). Eliminated false edges in reverse slices.
- **Type hierarchy concrete→concrete fan-out**: same-named concrete types across packages no longer produce false `dispatches_to` edges. 70% of alertmanager's 459 edges were false positives.
- **`ListNameResolver` path-hint false positives** (INV-popup): path hints require segment-level suffix matching instead of substring.
- **`library_patterns` YAML never applied** (INV-halar): `scan_library_patterns` had no callers — wired into `annotate_dataflow`. Alertmanager `access_mode='write'` edges: 0 → 274.

#### Slice

- **Forward dataflow admits downstream reads** (WI-saful): read edges downstream of writers now admitted as one-hop terminals in forward slices, per ADR-0015 §6.
- **Reverse-slice filename collision**: reverse slices now write to `slice.<name>.reverse.json` to avoid overwriting forward slices.

#### Profile & sketch

- **Profile LOC always zero in behavior map**: `hypergumbo run` now populates per-language LOC in the profile. Previously LOC was only backfilled in the sketch path.
- **False positive `cargo test` in sketch**: ambiguous test framework patterns (e.g. `#[test]`) now scoped to their language's file extensions.

#### Bakeoff signals

- **`bakeoff-deep init` recency check** (WI-kumub): warns before creating a new session when a recent one (< 7 days) matches the same pool and code hash.
- **`bakeoff-deep compare` metric ranking** (WI-lazat): dynamically ranks metrics by mean absolute delta instead of using a hardcoded set.
- **`LOW_DATAFLOW_SLICE_RATIO` false alarm** (WI-tutob): suppressed when `slice_access_mode_coverage ≥ 50%` (denominator growth was inflating the metric).
- **Tier slice byte-identical artifacts** (WI-puvab): tier slices use explicit non-test entry instead of `--entry auto --exclude-tests`, which eliminated all entries in test-dominated repos.
- **`cross_language_io_pct` false WARN**: gated on FFI bridge edges; no longer fires on HTTP-connected polyglot repos.

#### CI debug

- **Null statuses on freshly-pushed PR head**: `ci-debug` crashed when `commits/{sha}/status` returned `"statuses": null`. Fix: treat null and missing the same way.
- **Job log fetch 404s on Codeberg**: `fetch_job_log()` now selects log path by Forgejo version (`/logs` vs `/attempt/1/logs`).

#### Hooks

- **Stop hook hash recording throttle**: 150-second pause between hash recordings prevents the circuit breaker from tripping during background sub-agent waits.

#### Other

- **`loop-toggle` accepts uppercase mode arguments**: case-insensitive dispatch via `${var,,}`.
- **Flaky auto-run tests** (WI-miguf): stale cache state from prior sessions could short-circuit the auto-run check. Fix: autouse `isolate_hypergumbo_cache` fixture redirects `XDG_CACHE_HOME` per test.

### Documentation

- **ADR-0006 augmented with Return-Type Registry Pre-Pass** (INV-dihos): adds source 5 ("return-type chaining via global registry") to §"Type Inference Sources" with rollout plan.
- **Stash safety rule for `.ci/affected-tests.txt`**: added to AGENTS.md and smart-test playbook. Reset the file before `git stash pop` to avoid merge conflicts.
- **Bakeoff iteration vs. new session clarification**: artifacts guide explains session/cohort/iteration nesting and the `cycle` vs `init` rule.
- **Dogfooding playbook IR class names corrected**: `IRNode`/`IREdge` → `Symbol`, `Edge`, `Span`, `AnalysisRun`.

## [2.5.1] - 2026-04-05

## [2.5.0] - 2026-04-04

### Added

#### Go qualified-type parameter tracking

- **Qualified type propagation**: Function parameters and struct fields with package-qualified types (e.g. `client *http.Client`) now carry full module hints through to unresolved edges and field chain access. IO boundary detection can now classify `http.Client.Do()` as `net_send` and chained patterns like `n.client.Do(req)` — previously blocked by `ambiguous_names` guard due to missing module context.
- **Interface dispatch narrowing** (WI-doval): `var n Notifier = &DiscordNotifier{}` now tracks the concrete type, eliminating spurious `dispatches_to` edges.

#### Taint-flow analysis (ADR-0017)

- **Structural propagation** (Phase 1): YAML-driven taint catalogs (crypto, key material, fs writes, network sends) for Python, Rust, TS, Go, Java. Call-graph BFS with sanitizer checking. `verify-claims` supports `taint_flow` constraints.
- **Intraprocedural dataflow** (Phase 2): Language-parameterized CFG builder (Rust `?`, Python `with`, Go `defer`). Reaching-def solver with worklist fixpoint. Def/use extractors for Python, Rust, TypeScript. DDG-backed propagation upgrades taint findings from `approximate` to `precise`. Budget-capped target selection (500 functions).
- **Interprocedural propagation** (Phases 3-5): Function summary inference and YAML-declared summaries (TS 12, Rust 11 built-in). Cross-language propagation through 12 linker edge types. Field-sensitivity: `x` tainted → `x.field`/`x[key]` tainted.

#### I/O boundary catalogs

- **Objective-C** (`objc.yaml`): 90+ Foundation/Cocoa primitives (filesystem, networking, Core Data, subprocess, IPC).
- **Scala** (`scala.yaml`): scala.io, cats-effect, ZIO, sttp/http4s/akka-http, fs2, Slick/Doobie/Quill. Inherits Java catalog.
- **Haskell** (`haskell.yaml`): Prelude, System.IO, Network.Socket, System.Process, Data.IORef, Control.Concurrent.
- **Swift** (`swift.yaml`): Foundation IO catalog (FileManager, URLSession, Process, NotificationCenter). 14 server-side primitives (AsyncHTTPClient, NIOSSL, distributed tracing). SwiftNIO channel/file I/O (`NonBlockingFileIO`, `Channel`, `ChannelHandlerContext`). 7 swift-log Logger level methods. Ambiguous names for generic identifiers (write, read, Data, URL).

#### FFI unresolved edges for IO tracing

- **Ruby FFI** (WI-valiv): `attach_function` to external libraries emits `ruby:C_ffi:0-0:<name>:unresolved` edges, redirected to C catalog for IO tagging.
- **Python FFI** (WI-dokum): `ctypes.CDLL(None)` and `ffi.dlopen(None)` emit `python:C_stdlib:0-0:<name>:unresolved` edges. Repo-local C symbols still produce resolved edges when available.

#### Dataflow access mode patterns

- **Rust** (`rust.yaml`): 44 method-name heuristics (write/read/delete). Previously all Rust call edges had no access_mode.
- **Go** (`go.yaml`): 30 regex patterns (15 write, 15 read) for mutating method calls (WI-satuv).
- **Erlang**: Name-based heuristics (get_*/set_*, ETS/Mnesia ops, gen_server call/cast).

#### `io-boundaries` CLI

- Enriched text output: per-primitive counts, call-site locations, entry-point traces, high-risk highlighting.
- New flags: `--by-file`, `--boundary TYPE`, `--primitive NAME`, `--exclude-tests`.
- Enriched JSON: `chains`, `primitive_counts`, `has_high_risk` (backward-compatible).

#### Language analyzers

- **Swift**: Computed property/subscript extraction. Vapor/Hummingbird route extraction (kind="route").
- **Objective-C**: Cocoa/UIKit lifecycle patterns (`cocoa.yaml`). Method `parent_base_classes` propagation.
- **Scala**: Play Framework routes parser. IOApp/ZIOAppDefault/Scalatra entrypoint detection.
- **Haskell**: Typeclass instance `implements` edges. Dataflow access_mode patterns.
- **Erlang**: `gen_server:call/cast` dispatch linking.
- **Go**: Cobra `AddCommand()` command tree detection.

#### Framework & entrypoint detection

- Hummingbird added to Swift framework list.
- SwiftUI App, UIApplicationDelegate/NSApplicationDelegate, UIViewController/NSViewController, ParsableCommand (Swift Argument Parser), and XCTestCase entrypoint patterns (`swiftui.yaml`).
- Hummingbird route/middleware/application patterns (`hummingbird.yaml`).
- Middleware concept (59+ YAML patterns) now mapped to `middleware_handler` entrypoints.
- Haskell `main :: IO ()` and Erlang `main/0`/`start/0` entrypoints.

#### Tier classification

- Swift `.build/` → tier 4. DocC `.docc/` → tier 2 (was tier 1; fixes 33% inflation in TCA).

#### Rust def/use extractor enhancements

- Borrow alias tracking: `let y = &mut x` records `x` as a use of `y`.
- `ref`/`ref mut` patterns in match arms now bind variables correctly.
- Dereference assignments (`*ptr = val`) generate defines for `ptr`.

#### Transcript sync and local model pipeline (ADR-0018)

- **Vendor-agnostic transcript sync**: Background watcher mirrors session transcripts to `.agent/.current_session_transcript.jsonl` (~83% noise filtered). Supports Claude Code, Codex CLI, Gemini CLI, Cursor.
- **LLM-driven playbook injection**: Two-model sparse-selection pipeline rates playbook relevance and injects high-scoring ones into conversation context. Compaction-aware dedup with token-distance window. 14 playbooks extracted from AGENTS.md.
- **G-Vendi finetuning pipeline** (`scripts/finetune-transcript-model`): Diversity-guided data selection (arXiv:2505.20161) for local Qwen2.5-0.5B-Instruct model. Parse-outcome sidecar log for tracking failures.

#### Autonomous mode management

- **Session-start hook**: Prompts for BROAD/DEEP/OFF mode selection when autonomous mode is OFF or has a stale PID. Vendor-agnostic with thin adapters per AI tool.
- **Session-end hook**: Disables autonomous mode (`loop-toggle off`) when the user ends their session. Shared logic in `_shared/session_end_logic.sh`.
- **Circuit breaker reset**: `loop-toggle` now deduplicates the last hash in the stop-hook hash file when activating a mode, preventing stale state from auto-approving stops.

#### CI resilience

- **Stale-pending detection in auto-pr**: `poll_ci()` detects when all CI jobs remain pending after 5 minutes (exit code 3). Auto-pr closes the PR, waits with exponential backoff (2/4/8/16 min), and repushes. Up to 4 retries.
- **Stale-pending detection in tracker sync**: Same mitigation applied to `_poll_ci`/`do_sync` — 90-second timeout, close/wait/repush with up to 2 retries.
- **Tracker sync PR verification**: Stop hook's stale-PR audit calls `verify-tracker-pr` to check safety before recommending close.

#### Reverse slice seed selection

- **Library export boost** (1.4×): `library_export`-tagged symbols in the entrypoints section are boosted in rslice seed scoring. Ensures reverse slices answer "who calls this library's public API?" (WI-bosik).
- **Architectural concept boost** (1.3×): Middleware, controller, application, and model symbols boosted over pure hub nodes (OutputBuffer.append, Iterator.next).

#### I/O boundary catalog additions

- **C**: `fclose`, `fflush`, `fseek`, `rewind`, `ungetc`, `ftell` (stdio lifecycle). `tmpfile`, `tmpnam`, `mkstemp`, `mkdtemp`, `mkostemp`, `mkstemps` (temp files).
- **Go**: `http.Transport.RoundTrip` (net_send). `golang.org/x/sys/execabs.Command` (subprocess). `testing.T.TempDir`/`testing.B.TempDir` (fs_write). `log`/`log/slog` families (ipc_send). `crypto/tls` Dial/Client and `net/smtp` NewClient/Dial/SendMail (net_send). Removes 6 false positives (`bytes.Buffer.WriteString`, `strings.Builder.WriteString`, `kingpin.Command()`).

#### Other

- `sketch --require-section`: force specific sections into output regardless of token budget.

### Fixed

#### FFI IO boundary tracing (INV-kagob, INV-zogun)

- All 6 FFI linkers (cgo, JNI, PyFFI, N-API, Lua FFI, Ruby FFI) now annotate bridge edges with `access_mode=write, dest_access_mode=read`. Validated: chai2010/cgo 0→38 annotated edges.
- `cgo_bridge` and `ffi_bridge` added to IO boundary tag and trace sets. IO chains now cross Go→C and Python→Rust boundaries (go-sqlite3: 116 edges, polars: 5,617 edges previously had zero IO metadata).
- FFI catalog redirect: `go:C:` pseudo-namespace from cgo redirected to C catalog. Validated: chai2010/cgo 0→7 IO edges.

#### Dataflow slice quality (INV-jahov)

- **Position-aware access_mode**: Tree-sitter child field names distinguish LHS (write) from RHS (read) in assignments. Python AST reclassifies call edges on assignment lines as "read". `returns` YAML section now loaded (was silently dropped). Net effect: dataflow slices are tighter than structural slices — forward follows write/mutate, reverse follows read.

#### Java annotation and route fixes (INV-nimik, WI-tojuz)

- JAX-RS `@Path(value="/foo")` kwargs extraction (was only checking positional args). Same fix for Micronaut. Generic return type extraction (`Response<User>` → `Response`) for subresource locator detection.
- Empty route paths normalized to `"/"` in stable IDs and materialized symbols.
- `in`, `out`, `err` added to Java `ambiguous_names` (20 false positives in keycloak from JPA `CriteriaBuilder.in()`).

#### I/O boundary detection

- **Ambiguous name filtering for 10 catalogs** (INV-tapat): Go, Rust, Python, Java, C, JavaScript, Erlang, Haskell, Objective-C. Measured: polars net_send 285→89 (69% reduction). JavaScript `remove`/`rename` added (8 false `fs_write` chains eliminated in keycloak).
- Case-insensitive module matching. ObjC catalog key bridging. Scala fs2/akka ops reclassified from `net_recv` to `fs_read`/`fs_write`. Haskell `external` sentinel for short-name fallback.

#### Symbol resolution

- ObjC selectors include colons (`removeItemAtPath:error:`). Callee extraction handles colon-containing names.
- ObjC `protocol` symbols indexed for `implements` edges in inheritance linker.
- Short-name confidence penalties (single-letter 0.15×, two-letter 0.50×) for Scala and Haskell.
- Scala: 30+ collection/FP names added to `ambiguous_names` blocklist.

#### Swift

- Methods registered by qualified name only (`Type.method`), preventing false call edges from same-name methods.
- ERROR node recovery for declarations broken by preprocessor directives or `_$` identifiers.
- Receiver type tracking from property declarations. Navigation call target walks to method, not receiver.

#### Haskell & Erlang

- Where-clause/let bindings no longer extracted as top-level symbols (fixes 24-31% orphan rate).
- Erlang function clauses with same name/arity coalesced (fixes 47-64% orphan rate).

#### Python

- Unresolved method calls emit `unresolved_variable_method_call` edges (0.40 confidence) instead of being dropped.

#### C dataflow

- `returns` section added to C dataflow YAML (was missing — Go, Java, C++, Rust, Python, TypeScript all had it). Return statement edges now get `access_mode="read"`.

#### auto-pr & tracker sync

- **Gate timing race** (INV-rahib): `PR_PENDING` gate now created before push (was after), closing a window where tracker sync could advance dev mid-flight. Added re-check before push and proactive fetch+rebase after CI poll.
- **Variable name bug**: `$PUSH_REMOTE` (undefined uppercase) → `$push_remote`; hardcoded `"dev"` → `$BASE_BRANCH` in hung-run retry.
- **Tracker `pending_sync_lines` failover**: Now checks `.git/CI_FAILOVER_ACTIVE` and prefers `selfh/dev` as diff base. Previously all ops synced via selfh showed as "pending" relative to stale `origin/dev` (e.g., 435 lines when true delta was near zero).

#### CI & release scripts

- **CI rootdir pinning**: Added `--rootdir=.` to CI pytest invocations. When all manifest tests belong to one package, pytest selected the package subdirectory as rootdir, breaking repo-root-relative paths (0 items collected).
- **ci-debug SIGPIPE**: `_find_job_from_log_probe` used `curl | head -1` under `set -o pipefail`, sending SIGPIPE to curl (exit 141). Now uses `curl -r 0-1023` (HTTP range request) instead of piping.
- **ci-debug Forgejo API fallback**: `/actions/runs` endpoint doesn't exist on Forgejo 11.x. Falls back to `/actions/tasks` to discover run numbers, then probes job logs. Transparent to Codeberg (tries `/runs` first).
- **ci-debug ops-exclusion failover**: Fetches `selfh/dev` during failover so ops-exclusion diff matches CI's base SHA.
- **Empty manifest for docs+CI-only PRs**: Generates empty targeted manifest when no Python source files changed.
- **Release: smart-test version handling**: Version-only `__init__.py` diffs now generate targeted manifests (one test per package) instead of falling back to full-suite.
- **Release: branch creation order**: `prepare-release` creates feature branch before committing (was after).
- **Release: tracker flush**: Flushes pending tracker ops before clean-tree check.
- **`requests` upgraded to 2.33.0** for CVE-2026-25645.

#### Hooks

- **Stale-PR audit failover** (WI-soboh): Now respects `CI_FAILOVER_ACTIVE`, querying selfh instead of origin.
- **Circuit breaker**: Fixed TOCTOU race (two `tail` reads) and off-by-one (current stop counted toward threshold). Mechanically runs `loop-toggle off` on trip.
- **Pre-push failover verification** (INV-bifud): Blocks pushes to origin during failover.

#### Bakeoff threshold tuning

- `io_tag_rate`: Log-linear scaling from 500 nodes (was 10K), `warn_min` 0.1%. `dataflow_slice_ratio`: Skipped when `access_mode_coverage < 30%`. `limit_hit_frequency`: Log-linear boost for 35K+ node repos.
- `tier1_pct`: 100% for single-language library repos. `cross_language_io_pct`: Per-chain source vs catalog language (FFI repos now detected correctly). Polyglot threshold: <5% secondary language ignored.

#### Transcript pipeline (ADR-0018)

- Session state now cleared on session start with session-token self-healing. Poll race condition fixed (state marker written after hook succeeds). Goal injection removed (wasted tokens, risked bias).
- Rating parser: greedy fallback regex removed. Hook wiring gaps fixed for Cursor and Codex CLI. Transcript window reduced from 16K to 8K tokens.

### Changed

- **Bakeoff script rename**: `bakeoff` → `bakeoff-broad`, `bakeoff-features` → `bakeoff-deep`, `bakeoff-reflect` → `bakeoff-broad-reflect`, `bakeoff-features-reflect` → `bakeoff-deep-reflect`. All references updated across AGENTS.md, ADRs, hooks, and scripts.
- **Cooldown prompt restructure**: Process Retrospective promoted to Section 1. Gates discouraging analysis/tooling work during CI waits removed.
- **State file fallbacks removed**: `last_stop_check.json` uses primary location only (`~/hypergumbo_lab_notebook/guidance_log/`). Legacy paths no longer checked.

### Documentation

- **ADR-0017** (Taint-Zone Dataflow Analysis): Proposed and accepted. Python-first extractor ordering.
- **Governance docs**: Autonomous mode management, circuit breaker, and ADR index (`docs/adr/README.md`).
- **Deprecated invariant ledger removed**: Superseded by structured tracker (ADR-0013). References updated.
- **Agent playbooks**: Changelog audit playbook, playbook creation guide, bakeoff artifact guide added.
- **Spec updates**: ADR-0017 taint_flow constraint in §3 verify-claims. Dataflow non-goal narrowed in §2. ADR-0014/0015 synced with implementation state.

## [2.4.0] - 2026-03-21

### Added

#### I/O boundary analysis (ADR-0016)

- **`hypergumbo io-boundaries`**: Identifies call edges reaching I/O primitives (filesystem, network, subprocess, environment) and groups by boundary type. YAML-based catalogs for 10 languages (Python, Rust, JS/TS, Go, C/C++, Java + Kotlin/Scala/Groovy via alias). 60+ framework entries across Netty, Tokio, Express, Flask, and others. Module-qualified matching prevents false positives (e.g., `crypto/rand.Read` no longer matches `net.Conn.Read`).
- **Entry-point reverse tracing**: IO boundary map traces backward from each IO edge through the call graph to find which entrypoints reach each IO call. Follows FFI bridge edges (JNI, NAPI, PyFFI, WASM, gRPC) across language boundaries.
- **`hypergumbo verify-claims`**: Verifies security claims (`must_not_exist`, `max_chains`) against the IO boundary map. YAML input, `--json` output; exit code 1 on violations.

#### Cross-language linkers

- **React Router v6.4+ loader/action linking**, **Electron contextBridge exposure**, **React.lazy() route detection**
- **Yjs sub-document accessors**, **BlockSuite document model linker** (CRDT edges)
- **Crypto-flow linker**: Traces encryption/decryption boundaries across WebCrypto and Rust crypto
- **Message dispatch linker**: Typed wire protocol matching (JS/TS discriminated unions, Rust serde variants)
- **gRPC CSI-style linking**, **Dynamic WASM loading**, **Annotation convention** (`@hg:route`, `@hg:dispatches`)

#### Dataflow (ADR-0015)

- **Expanded dataflow patterns**: Go, Python, Rust, Java, C++ now have 8-12 patterns each (range loops, returns, yields, context managers, match arms). Python ast-module analyzer also expanded.

#### Language analyzers

- **Unresolved-external call edges**: All 30+ analyzers with call resolution now emit `unresolved_external_call` edges for stdlib/third-party calls via shared `make_unresolved_edge()` utility. Previously most analyzers silently discarded these, breaking IO boundary detection for C/Java repos.
- **Go interface dispatch**: Ambiguous method calls resolve to interface method candidates instead of remaining unresolved.
- **C designated initializer function pointers**: `.callback = my_handler` patterns create call edges.
- **Web Audio API framework patterns**

#### Symbol identity (ADR-0014)

- **stable_id**: Hash-based content-addressable identity for C, C++, Ruby, Bash, Perl, PowerShell, Lua, Objective-C, SQL. **shape_id**: Structural fingerprint for Java, Go, JS/TS, Kotlin, PHP and 8 additional analyzers.

#### Analysis core

- **Edge-type-weighted centrality**: Per-type weights for 19 edge types (calls=1.0, imports=0.3, structural=0.1)
- **Runtime memory pressure guard**: Monitors RSS, skips analyzers before OOM
- **Dataflow annotation line index**: ~47% faster Java analysis

### Changed

- **Weighted import inclusion in ranking**: Import edges now included at reduced weight (0.3) instead of excluded entirely. Widely-imported core types rise in rankings while call edges still dominate.
- **Tier classification**: Vendored directories (`third-party/`, `thirdparty/`, `external/`, `deps/`) → tier 3. Workspace package non-test files → tier 1 (was tier 2; fixes deno 3.5% → 89% tier 1).
- **Entrypoint ranking**: Library exports with high in-degree receive confidence boost (+0.35 cap). `microbench/` directories demoted as utility code. C/C++ symbols in `include/` detected as library exports.

### Fixed

- **IO boundary false positives**: Module-qualified matching checks edge module context against catalog entries
- **PyO3 linker**: Matches `#[pyo3(...)]` crate-name annotations; strips `Py` prefix for Python-style name matching (`PyTokenizer::encode` → `Tokenizer.encode`)
- **Dataflow call-edge annotations**: Removed incorrect `calls` section from all 19 dataflow pattern files (was causing forward slices to skip call chains)
- **Test-edge filter**: Phantom source symbol import edges no longer leak through to inflate centrality
- **`rank_files()` consistency**: Now uses same centrality parameters as `rank_symbols()`
- **Erlang local call resolution**: Intra-module calls without explicit module qualification now resolved

### Performance

- **Java symbol import resolution**: O(n*m) → indexed O(1) lookup, ~10x faster on large repos
- **Python global symbol resolution**: O(n) → (path, name) index for O(1) lookup

## [2.3.0] - 2026-03-16

### Added

- **Dataflow access modes (ADR-0015)**: Edges carry optional `access_mode` (`read`/`write`/`mutate`/`delete`), `dest_access_mode`, and `channel` metadata. YAML-driven annotation for 9 languages plus 65 tree-sitter analyzers. `slice --dataflow` follows write→read dependencies.
- **Yjs/CRDT linker**: `crdt_publishes` edges between Yjs writers and observers, plus awareness API.
- **Annotation convention linker**: `@hg:publishes`/`@hg:subscribes` comment annotations create cross-language pub/sub edges.
- **Tauri IPC event linker**: `ipc_event` edges from Rust `window.emit()` to TS `listen()`/`once()`.
- **React Router v6.4+**: `createBrowserRouter` object-based route configs with nested children, `loader_ref`, `action_ref`, and `lazy_import` metadata.
- **Shared file index**: Single `os.walk()` replaces ~80 redundant `rglob()` calls per run (~75% of uncached runtime eliminated).
- **Embedding model cache**: Singleton avoids 2 redundant model loads per run (~9% faster).
- **smart-test ETA**: Estimates wall-clock duration from test timing history before the run starts.
- **Test timing leaderboard**: `scripts/test-leaderboard` tracks per-test durations with rolling windows.

### Fixed

- **`slice --dataflow` reverse mode**: Correctly follows read edges instead of write edges.
- **Solidity ABI linker**: Qualified function names now also indexed by unqualified name.
- **Entrypoint diversity cap**: No single `EntrypointKind` can take more than 40% of slots.

## [2.2.1] - 2026-03-15

### Added

#### Language analyzers

- **Jupyter** (`.ipynb`): Extracts Python symbols and call edges from notebook code cells. Strips IPython magics/shell commands, tracks cross-cell line offsets.
- **Blade** (`.blade.php`), **Gnuplot** (`.gnuplot`, `.gp`, `.plt`), **Handlebars** (`.hbs`), **Just** (`justfile`), **Mermaid** (`.mmd`), **QML** (`.qml`): New regex-based analyzers for templates, build files, diagrams, and Qt components.

### Fixed

- **`slice --files` crash** when `--max-hops` not passed (`int < None` TypeError). Broken since 2.2.0 — caused `smart-test` to silently fall back to full test suite on every run.
- **`dev-install`** now calls `install-hooks` automatically (was a separate manual step).

## [2.2.0] - 2026-03-12

### Added

#### Cross-language linkers

- **Solidity ABI bridge**: `abi_call` edges between TS/JS contract calls (ethers.js, viem) and Solidity function definitions.
- **Tauri IPC**: `ipc_calls` edges between TS/JS `invoke()` calls and Rust `#[tauri::command]` functions. Handles rename overrides, tauri-specta bindings, and plugin patterns.
- **wasm_bindgen**: `wasm_bridge` edges between JS/TS wasm-pack imports and Rust `#[wasm_bindgen]` exports. Handles `js_name` renames and aliased imports.
- **Electron IPC expansion**: Detects `sendSync`, `handleOnce`, `webContents.send` (main-to-renderer), and `ipcRenderer.on`/`once` (renderer-side).
- **React component**: `renders_component` edges from JSX usage (`<Button />`) to component definitions.
- **Decorator dispatch**: `dispatches_to` edges from registry-based dispatch sites to registered handlers, enabling forward slices through plugin patterns.
- **Middleware chain**: `middleware_chain` edges between consecutive middleware symbols. Works with all 58 framework patterns that tag `concept: middleware`.

#### gRPC

- **Proto RPC route detection**: Proto RPC methods produce `kind="route"` symbols using HTTP/2 wire paths, visible in `routes.txt`.
- **Proto-to-Go implementation linkage**: Go methods embedding `UnimplementedXxxServer` are linked to proto RPC routes via `implements_rpc` edges. Also supports ttrpc `RegisterXxxService` patterns.
- **Server-to-service bridge**: `dispatches_to` edges connect server/servicer symbols to proto service definitions. Forward slices now traverse: stub → server → service → route → handler.

#### Route detection

- **React Router JSX**: `<Route path="..." element={<X />} />` produces route symbols with metadata.
- **Go**: Anonymous closure handlers. String concatenation paths (`baseUrl + "/users"`). Variable-based router group prefixes (Gin/Echo/Fiber). Go 1.22+ `http.ServeMux` combined method-path patterns.
- **Python**: Constant propagation for Django `path()`/Flask `add_url_rule()` with string concatenation and cross-file constant references. FastAPI `APIRouter` prefix composition. Flask-RESTful `add_resource()`.
- **Rails**: Inline `on: :member`/`on: :collection` routes. `only:`/`except:` action filters for `resources`.
- **Stapler**: Convention-based `doXxx` → POST, `getXxx` → GET for Jenkins handlers.

#### Language analyzers

- **Rust**: `implements` edges, turbofish/fully-qualified call resolution, generic trait method blocklist, `#[cfg(test)]` module inheritance, unresolved trait impl edges, `Self::method()` resolution, async spawn detection, macro body call detection, module-qualified call resolution.
- **Solidity**: Call graph with inheritance, override, and emit edges. Visibility modifiers. `using Library for Type` resolution.
- **Elixir**: `@behaviour` directive detection, WebSock callbacks, guard clause function extraction, pipe operator call edges, stdlib function exclusion.
- **Go**: Structural interface matching (no explicit assertions needed), interface method symbols, chained field access resolution via `class_field_types` registry, constructor return type inference (`NewXxx()` → `*Xxx`).
- **TypeScript**: Type reference edges (`type_ref`) from type aliases and interfaces. Abstract class support.
- **Java**: Inherited method/field resolution via extends chain. Inferred concrete return type for `Object`-returning methods. Annotation positional argument extraction (constants, concatenation).
- **C++/C#**: Chained field type resolution (`this->field->method()`) via `class_field_types` registry.
- **Circom**: New tree-sitter analyzer for `.circom` zero-knowledge circuits.
- **Formal methods**: Reference edges in Agda and Lean. Library export detection for Lean, Agda, and Wolfram.
- **Ansible**: Include/import edges resolve to file-level node IDs via basename and role name lookup.

#### Entrypoints and build targets

- **Build target linker**: Connects manifest-declared build targets to entry functions across 15 ecosystems (Cargo, npm, pyproject.toml, Maven, Gradle, C#, Dart, Swift, Haskell, Elixir, Ruby, Scala, OCaml, Zig, Nim).
- **package.json `exports`**: Subpath exports produce `export_entry` symbols and `defines_target` edges.
- **React SPA bootstrap**: `createRoot()`, `ReactDOM.render()`, etc. produce `SPA_BOOTSTRAP` entrypoints.
- **Electron main process**: `app.whenReady()` and `app.on('ready')` produce `ELECTRON_MAIN` entrypoints.
- **Top-level call attribution**: JS/TS, Bash, PHP, Perl, PowerShell now attribute module-level calls to a `<module:filename>` symbol.
- **CDI scope-annotated DI binding**: Java classes with `@ApplicationScoped` etc. that implement an interface produce explicit DI binding edges (0.85 confidence).

#### Framework patterns

- **MCP**: 8 TypeScript + 10 Python patterns for tool/resource/prompt registration.
- **Solid.js**: 12 patterns (reactive primitives, stores, context, lifecycle, bootstrap).
- **Lit**: `@customElement`, `@property`/`@state`, `@query`/`@queryAll`/`@queryAsync`, lifecycle hooks.
- **NestJS/TypeGraphQL**: `@Resolver` + `@Query`/`@Mutation`/`@Subscription`/`@ResolveField`. `@Module` providers/controllers.
- **Jakarta CDI `@Produces`**: Producer methods for interface-to-implementation resolution.

#### CLI and output

- **`--max-file-bytes`**: Skips oversized files. Recorded in `limits.truncated_files[]`.
- **`--locale`**: Detects translated doc directories (GitLab/FastAPI conventions). Excludes translations by default.
- **`--group-by-module` (slice)**: Groups inline slice nodes by file path with cross-file edge summary.
- **Sketch harmonic budget**: `--with-source` uses harmonic weighting for proportionally deeper top-ranked files.
- **Parallel execution**: Analyzers run concurrently; same-priority linkers run in parallel.
- **Adaptive slice hop limit**: `--max-hops` default scales with graph size (10 for small graphs, 3 for large).

### Fixed

#### Slicing and graph traversal

- **Forward slice traverses `dispatches_to`**: Slices follow interface methods to concrete implementations instead of dead-ending.
- **Reverse slice ignores `contains`**: No longer follows `contains` edges up to parent classes, eliminating false positives.
- **Event-driven traversal**: `event_subscribes` edges enable forward slices through publisher → subscriber → handler chains.
- **Hub pruning exempts dispatch edges**: `dispatches_to` edges always followed even when `calls` edges are hub-pruned.
- **Pass-through node filtering**: Synthetic IPC event nodes traversed during BFS but excluded from slice output.
- **Linker pipeline accumulation**: Earlier linkers' output now visible to later linkers, unblocking `dispatches_to` creation from linker-produced inheritance edges.
- **Slice `node_tiers`**: Supply chain tier propagated into slice output for tier-based filtering.

#### Cross-language IPC/WASM

- **Synthetic source nodes**: Tauri IPC and wasm_bindgen linkers create Symbol nodes for edge sources, fixing reverse slice traversal through bridges.
- **Tauri specta wrappers**: Both standalone function exports and object-method wrappers (`export const commands = { ... }`) create `caller_invokes` edges from import sites.
- **Electron contextBridge**: `contextBridge.exposeInMainWorld()` preload patterns resolved, creating `bridge_invokes` edges from renderer calls through to main process handlers.

#### Route handler linking

- **Symbol ID resolution**: Routes with full symbol ID `handler_ref` resolve directly instead of failing name-based lookup.
- **JSX component linking**: `<Route element={<Users />} />` links to `class`/`module_file` symbols. Tries React naming suffixes on mismatch.
- **Route deduplication**: Concepts deduplicated across matching phases. Dedup key scoped to (method, path, file) — different files preserved.
- **Go-swagger handler wiring**: Resolves to implementation methods instead of constructors.
- **JAX-RS `@Path` combination**: Class + method `@Path` composed (e.g., `/users/{id}`).
- **Phoenix LiveView**: LIVE routes resolve to LiveView module by name suffix.
- **False positive suppression**: NestJS `app.get(Service)` DI lookups, Go single-arg `.Get()` on caches/headers, and ambiguous SPA bootstrap names (Solid/Svelte/Vue prioritized over React).

#### Ranking and centrality

- **Confidence-based edge filtering**: Rankings exclude edges below 0.5 confidence. Ambiguous resolution scales as `0.70/sqrt(N)`. `dispatches_to` scales as `0.85/sqrt(N)`.
- **Cross-file degree weighting**: Within-file edges contribute 0.3× to in-degree. Per-file cap of 5 edges per target.
- **Dampening**: Utility/helper files (×0.1), FP primitives (`map`/`filter`/`reduce`/etc.), assertion/panic/exit builtins, leaf UI components (`Button`/`Icon`/`Modal`/etc.), pure sinks (out_degree=0, relaxed to 20 LOC), and sibling implementations (6+ same-name methods: top 3 keep full weight, rest ×0.15).
- **Entrypoint selection**: `--entry auto` boosts `MAIN_FUNCTION`/`CLI_MAIN` 2× over route handlers. Connectivity boost skipped for test entrypoints. Telemetry/logging exports excluded from boost. Adaptive seed budget (max_symbols/3) reduces disconnected singletons.

#### Symbol resolution

- **Test-path preference**: Non-test callers prefer production candidates in suffix matching.
- **Method blocklists**: JS/TS (60+ built-ins), Rust (logging + `output`/`status`/`spawn`), C++ (35 STL methods).
- **C++ class qualification**: Inline methods get qualified names (`Parser::Initialize`), with key-based `path_hint` matching.
- **Go local variable exclusion**: Scoped variables tracked and excluded from function reference matching.
- **Java nested class guard**: `new Properties()` no longer resolves to `Log4jConfiguration.Properties` from other files.
- **Elixir import-gated resolution**: Cross-module bare calls require explicit `import` directive.
- **Binary `.ts` skip**: MPEG Transport Stream files (null bytes in first 8KB) skipped.

#### Classification and output

- **Tiered view boundary exclusion**: Compact views exclude external_symbol/tier=3 nodes.
- **Test/utility classification**: `fv/`, `harnesses/` as test dirs; `build.rs` as utility; `bench/`/`benches/` excluded from production slices. `dev/`/`utils/` only match at project root, not inside source roots.
- **Codegen classification**: `.serde.rs`, `.pb.go`, `_pb2.py` as derived (tier 4).
- **Path normalization**: All symbol paths normalized to relative, fixing tier misclassification across 8 languages.
- **TOML symbol IDs**: Location-based format instead of sha256 hashes.
- **JSON reproducibility**: Sorted keys in all JSON output.
- **ASM register filtering**: CPU register names no longer create false external call edges.
- **Annotation-aware test exclusion**: `is_test_node()` checks `#[cfg(test)]`, `@Test`, `[Fact]` annotations, not just file paths.
- **Lean import resolution**: Intra-repo imports resolve to file node IDs instead of dangling module IDs.

### Changed

- Migrated all `Edge()` constructor calls to `Edge.create()` for consistent edge_key generation.

## [2.1.0] - 2026-03-01

### Added

#### Linkers

- **DI resolution linker**: Creates `di_resolves` edges from interface methods to DI-bound implementations. Supports Guice, Spring `@Bean`, ASP.NET Core DI, NestJS/Angular, InversifyJS, Python injector, Kotlin Koin, and Java SPI with heuristic fallbacks. Edges are followed by forward BFS — correct for DI-heavy codebases.
- **HTTP linker: Ruby, Java, AngularJS, jQuery clients**: Detects HTTP client calls in Ruby (RestClient, HTTParty, Faraday, Net::HTTP), Java (RestTemplate, Retrofit), AngularJS `$http`, and jQuery `$.ajax`/`$.get`/`$.post`. Creates cross-language `http_calls` edges to server route handlers.
- **JS/TS module resolution**: Resolves imports via relative paths (extension/index probing), `tsconfig`/`jsconfig`/`vite.config` path aliases, and monorepo tsconfig discovery.
- **Vue linkers**: Template-method linker connects event handlers to `<script>` symbols; component linker resolves import paths to `.vue` files.
- **FFI (5 languages)**: Cross-language call linking to C/C++ from Python (ctypes/cffi/PyO3), Ruby (FFI gem, C extensions), Go (Cgo), Node.js (N-API), and Lua (LuaJIT ffi).
- **ORM query, containment, Rails view template linkers**: Django/SQLAlchemy call-to-model linking; `contains` edges across 15 languages; convention-based controller-to-view linking (ERB, Haml, Slim, Jbuilder).

#### Frameworks

- **JAX-RS subresource locator path chaining**: Propagates `@Path` prefixes through locator chains with cycle detection.
- **Stapler (Jenkins)**: `@WebMethod`, `@RequirePOST`, `doXxx()`/`getXxx()` conventions. Auto-detected from `org.kohsuke.stapler`.
- **Google Guice + Jakarta CDI**: Guice DI annotations, `AbstractModule`, EventBus `@Subscribe`. Jakarta CDI scoping, `@Produces`, `@Interceptor`, `@Alternative`.
- **Rails**: Lifecycle/controller callbacks, Wisper pub/sub, scheduled tasks/Rack middleware entrypoints, namespace-aware route extraction.
- **Django & Flask**: Template tags/filters, signal receivers, Jinja2/Blinker/Flask-RESTful patterns.
- **Kafka Connect, XORM, FastAPI named routers, Express Controller.route()**: Streaming connector entrypoints, Go ORM detection, named `APIRouter` matching, config-object route registration.
- **Framework detection for 16 languages** (Haskell, Clojure, R, Lua, C++, Erlang, F#, Kotlin, C#, Dart, Julia, OCaml, Nim, Zig, D, Groovy). **Test framework patterns for 16 languages** (Elixir, Scala, Dart, Clojure, Haskell, Erlang, F#, Ruby, Julia, OCaml, Lua, R, Nim, Zig, D, Groovy). Main function detection for 7 more (D, Nim, Zig, V, Odin, Gleam, Haxe).
- **Test/utility file classification**: Test dirs as tier 2 with 90% penalty; `t/`, `test-*.c`, root-only `spec/` patterns. `dev/`, `contrib/`, `hack/`, `devel*` as utility. Removed `public/` from DEFAULT_EXCLUDES.

#### Analyzers

- **Clojure UsageContext**: Enables YAML-driven Ring/Compojure route detection.
- **JS/TS callback + middleware edges**: Function-as-argument `references` edges, Express `middleware_chain` edges, object literal and Ruby hash literal function references.
- **Assembly language**: Tree-sitter analyzer for `.s`/`.asm`/`.S` with cross-file call resolution.

#### Analysis core — Centrality & ranking

- **Bidirectional centrality**: `in_degree * (1 + ln(1 + out_degree))` rewards connectors over sinks. Hub in-degree saturation above 100.
- **Four dampening mechanisms**: Trivial sinks (≤1 out, ≤5 LOC), common method names (10+ symbols), utility symbols (Logger, `*Exception`, etc.), and pure sinks — all get 70–90% reduction in both `rank_symbols()` and `symbols` output.
- **Edge confidence filtering**: Edges <0.5 confidence excluded from centrality and degree computation. Import edges excluded by default. Documentation kinds and migration paths excluded/de-weighted.

#### Analysis core — Slices

- **Hub pruning depth-1 exemption**: Fixes "main → run()" patterns where orchestrators were hub-pruned.
- **`--exclude-imports` flag**: Call-graph-only slices (up to 64% noise reduction). **`--hub-threshold N`** (default 50). **Node depth tracking** in `SliceResult.node_depths`. Forward slices skip structural edges; reverse slices downweight test callers; class/interface entries auto-expand.

#### Analysis core — Entrypoints

- **Scaled cap** (base 50, max 500) with confidence threshold (0.10) and count cap (50).
- **library_export demotion**: 90% penalty when semantic entrypoints exist. Language dominance ranking for polyglot repos.
- **New detectors**: C `cmd_*` functions, Java/Kotlin/Rust library exports, C forward declaration dedup.
- **Tier classification**: Fuzz/benchmark dirs as tier 2; generated route symbols promoted to tier 2.

#### Analysis core — Call resolution

- **Go**: Module path resolution via `go.mod`, chained-call ambiguity guard, stdlib method guard (50+ methods), route handler unwrapping, route path validation, var alias extraction, struct embedding + interface assertion detection, Chi `Del()` and Go-swagger route detection.
- **Rust**: Suffix index splits on `::`, scoped calls prefer full qualified names, span-based enclosing function disambiguation.
- **C/C++**: Function pointer callback edges, dispatch table `dispatches_to`/`uses_dispatch_table` edges, declaration/definition deduplication with edge remapping.
- **Cross-language**: Unified suffix index for all separators (`.`, `::`, `#`, `\`, `:`) across 10+ languages. Ambiguous method scaling (`1/sqrt(N)`); ListNameResolver returns unresolved at threshold.

#### Analysis core — Other

- **Docstring extraction** (103/105 analyzers): First-line doc summaries in `Symbol.docstring`.
- **Typed stable_id (ADR-0014 Phase 3)**: Per-language signature normalization and typed hashing for 12 analyzers.
- **Decorator/annotation edges** (Python, TS, Java, C#, Rust), **return type tracking** (6 languages), **Go route mount detection**, **inheritance linker struct support**. Edge deduplication fixed for `None`-keyed edges.

#### Sketch, Supply chain, CLI

- **Sketch**: Exclude 9 lock files from config section. **Supply chain**: Maven multi-module workspace detection.
- **CLI**: Secret scanning via gitleaks, extras/cache management subcommands, redesigned bakeoff tooling (numeric scores, trajectory, orphan recovery, idea ingestion, artifact compression, domain-scored seed selection).

#### Documentation & Testing

- Scoped smart-test coverage, per-package checks, CI auto-retry, `ci-debug logs`.

### Changed

#### Language analyzers

- **Elixir/OTP**: GenServer dispatch, 11 behaviour callbacks, `live` routes, multi-clause edges, cross-file resolution.
- **C/C++**: Enclosing-function fix for duplicate names, definition-only struct/enum extraction. C++ adds template calls, pointer/reference returns, stack construction.
- **Go**: Function-scoped type tracking, unified ambiguity guard (all selector types), unexported method guard, builtin filter, receiver disambiguation, self-call resolution, route linking (Gin/Echo/Fiber/Chi/Gorilla), Group prefix composition, HTTP client detection, `lines_of_code`.
- **Ruby**: Class methods, `.new`→`#initialize`, namespaced receivers, job enqueue/callback/delegate/association edges, ambiguity guard, ListNameResolver.
- **Rust**: ListNameResolver with ambiguity threshold; 3+ candidates → no edge. `lines_of_code` populated.
- **JS/TS, PHP, Java, Lua, D**: Method ambiguity guards, inherited method fallback, require-alias resolution, import disambiguation improvements.

#### Algorithms & output

- **Slices**: Skip structural edges forward, downweight test callers reverse, `--exclude-tests` preserves inheritance. **Entrypoints**: Transitive scoring, connectivity fallback, test demotion, `--entry auto` filter support. **Default exclusions**: Doc/config nodes, CSS variables, npm/TS types, SCSS.
- **Output**: Tiered view overhaul (budget enforcement, connectivity-aware selection). Route improvements (`-x`, `kind=route`, Django/Rails format fixes). Symbols sorted by per-symbol degree. Derived/minified excluded by default; `--max-files` raised to 50.
- **Deps**: Embeddings optional. All deps pinned `~=X.Y.Z`.

#### CI, agent governance, internal

- smart-test improvements, infra-only PR skip, shared Forgejo API lib, parallel coverage, retry-aware `merge-pr`.
- Three-way stop hook, post-compaction recovery, pre-push hook, fail-closed tracker, fork workflow hardening.
- Standardized pass IDs via `make_pass_id()`. Generalized symbol identity (ADR-0014): location `id`, signature `stable_id`, CST `shape_id`.

### Fixed

#### JS/TS

- **Cross-package false positives**: Comprehensive guard on all edge paths (direct/namespace/method/callback/object-field/shorthand) using import disambiguation, same-package preference, and npm boundary checks. Built-in name guard (`Number`, `String`, `parseInt`, etc.). Parameter shadowing respects lexical scoping in Promises/closures. `npm_package` symbols correctly tier 3.

#### Go

- Vendored SDKs classified as tier 3. Method ambiguity threshold lowered to 2. Route handler from last non-string arg. Test functions require `_test.go` suffix. Same-package method resolution fixed.

#### Java/Kotlin/Scala

- `main()` patterns match qualified names. Import-aware class name disambiguation. Field access receiver extraction. Integration test path detection.

#### Other languages

- **Rust**: Built-in attribute guard (45 names); impl method name extraction. **Clojure**: `test-*` requires `test/` dir. **D**: `.d` file disambiguation vs GCC deps. **Rails**: Route-to-controller reverse suffix matching. **Kotlin/C#/Scala/Python**: Chained member access resolution.

#### Framework detection

- False positive guards for GraphQL (requires server packages), Dropwizard (requires `-core`/`-jersey`), handler naming (requires HTTP-context dir). Route path prefix inheritance (Spring Boot, JAX-RS, Micronaut, ASP.NET). Pattern `base_class` no longer falls through to kind-only matching. Word-boundary regex. Micronaut field fix.

#### Graph & output quality

- Tiered view budget compliance (was 177× over) with connectivity-preserving shrink. Dangling edges after tier filtering. WebSocket N×M explosion. Event symbol ID format. Supply chain tier deserialization. Route-handler linking (Rails suffix, Django view_name, Phoenix concept, Ruby hash rockets). Vue/C/C++ analyzer deduplication. Name-collision fan-out → single best match. Cross-language containment filtering. Language-proportional sketch seeding. Route symbol entrypoint promotion. Spurious TS warning. Minified file skip. smart-test scoped mode. ListNameResolver full-path disambiguation.

### Removed

- **Bootstrap mode in CI**: Stable hypergumbo includes `slice --files`, so smart-test always generates proper manifests.


## [2.0.2] - 2026-02-01

### Changed

- **Default token budget increased to 8000**: Ensures Source Files Content section has sufficient budget to include production files. Use `-t` flag to override.

### Fixed

- **Density score path normalization**: Fixed path mismatch where cached absolute paths weren't normalized to relative paths, causing files to sort arbitrarily instead of by density.

## [2.0.1] - 2026-01-31

### Added

- **`--files` flag for slice command**: Enables smart test selection by finding all files that depend on changed code. Usage: `hypergumbo slice --files changed.txt --output affected.txt`. This reads a list of changed file paths and performs reverse dependency analysis to identify affected test files. Used by `scripts/smart-test` to generate manifests for CI.

### Fixed

- **CI manifest validation**: CI now properly filters comment lines from manifests and detects bootstrap mode (when manifest indicates full suite is required due to missing stable hypergumbo).

## [2.0.0] - 2026-01-31

### Changed

- **Modular package structure (ADR-0010)**: Restructured from a single package into 5 modular packages: `hypergumbo-core` (CLI, IR, slice, sketch, linkers), `hypergumbo-lang-mainstream` (Python, JS/TS, Java, Go, Rust, etc.), `hypergumbo-lang-common` (Haskell, Elixir, GraphQL, etc.), `hypergumbo-lang-extended1` (Zig, Agda, Solidity, etc.), and `hypergumbo` (meta-package). **Breaking change:** import paths changed from `hypergumbo.*` to `hypergumbo_core.*` / `hypergumbo_lang_*.*`. CLI usage is unchanged. See `docs/MIGRATION-2.0.md`.

### Added

- **Smart test selection (ADR-0010)**: `smart-test` uses hypergumbo's reverse-slice to run only affected tests from changed files, generating `.ci/affected-tests.txt` for CI. Includes stop-the-line protocol (bypass with `fix(job-XXXXX):` title prefix).
- **Two-tier CI system**: Fast CI uses manifest-based test selection; `full-suite.yml` runs as lazy singleton after dev merges.
- **Framework pattern detection for 30+ frameworks** across 10 ecosystems. Each framework gets route, handler, middleware, and component detection via YAML patterns. See `docs/FRAMEWORKS.md` for per-framework details.
  - **Python (8):** Falcon, Quart, Sanic, Pyramid, Bottle, Litestar, Masonite, Flask-Appbuilder
  - **PHP (7):** Symfony, CodeIgniter, Lumen, CakePHP, Yii, Laminas, FuelPHP
  - **Java/JVM (3):** Quarkus, Javalin, Vert.x; plus JAX-RS aliases for Dropwizard, Jersey, RESTEasy
  - **Kotlin (1):** Http4k
  - **Scala (2):** Scalatra, http4s
  - **Node.js (5):** Nuxt, Remix, SvelteKit, Feathers.js, AdonisJS, Restify
  - **Ruby (3):** Hanami, Roda, Padrino
  - **Clojure (2):** Ring/Compojure, Pedestal
  - **Haskell (2):** Servant, Scotty
  - **Elixir (1):** Nex
- **Utility file entrypoint penalty**: Entrypoints in utility directories (docs, examples, scripts, tools, benchmarks) receive a 50% confidence penalty.
- **Test file weighting for slice ranking**: `rank_slice_nodes()` now downweights test file nodes so production code ranks higher in reverse slices.

### Fixed

- **TypeScript constructor injection resolution (INV-013)**: `this.property.method()` calls now resolve when the property is a constructor-injected dependency (e.g., NestJS `constructor(private catsService: CatsService)`). Forward slices from controllers now include service layer calls.
- **Linker duplicate edge elimination**: Edge deduplication after linkers run prevents duplicates from the event-sourcing linker (e.g., killbill: 25494 → 25022 edges).

## [1.3.1] - 2026-01-29

### Added

- **C++ test framework patterns**: Google Test (`TEST`, `TEST_F`, `TEST_P`) and Catch2 (`TEST_CASE`, `SCENARIO`) macros now detected as `test_function` concepts. Reduces orphan function count in C++ test codebases.
- **go-restful framework support**: Added patterns for the go-restful framework (used by Kubernetes). Detects `.To()` method calls as route handlers and `restful.WebService` base class. Improves framework detection for Kubernetes-style Go APIs using the fluent RouteBuilder pattern.
- **HTTP client patterns for JavaScript/TypeScript**: Added patterns to detect frontend API calls for cross-language linking. Detects fetch(), axios, ky, got, and superagent HTTP clients as `http_client` concept. Enables future route-client linker to connect frontend API calls to backend route handlers in polyglot repos.
- **JAX-RS framework detection**: Added detection for JAX-RS (`javax.ws.rs`, `jakarta.ws.rs`), Jersey, RESTEasy, and Swagger dependencies in Java projects. Enables pattern enrichment for Java REST APIs using JAX-RS annotations (`@GET`, `@POST`, `@Path`, etc.).

### Fixed

- **F# analyzer Forth file disambiguation**: The F# analyzer now detects and skips Forth files that share the `.fs` extension (Open Firmware Forth, GForth). Prevents analyzer hangs on repositories like qemu-slof that contain Forth code with `.fs` extension. Detection uses content heuristics (backslash comments, Forth keywords like `VALUE`, `CONSTANT`, `:` word definitions).

- **Ruby analyzer duplicate edge elimination**: Fixed duplicate edges being created for the same call site when an identifier was both processed as part of a `call` node and separately as a bare `identifier`. Now skips identifiers that are children of call-related nodes, reducing edge count noise by 10-30% in Ruby codebases.

- **Bakeoff GraphQL false positive**: Fixed `EXPECTED_ROUTES_BUT_FOUND_0` false positive for GraphQL frameworks (apollo-server, etc.) that don't use traditional HTTP routes. Repos with "graphql" or "apollo" in name are now excluded from route expectations.
- **Bakeoff diagnostic false positive reduction**: `NO_CALL_EDGES` now requires ≥3 function/method symbols (repos with 0-2 functions can't have meaningful call edges). `EXPECTED_ROUTES_BUT_FOUND_0` removed overly broad "web" keyword match (caught webtunnel, webpack, webrtc); now requires name keywords like "api", "server", "http", "rest" OR evidence of route edges/framework detection.

- **GraphQL entrypoint detection**: Updated GraphQL framework patterns (graphql.yaml, graphql-python.yaml, graphql-ruby.yaml) to use `graphql_resolver` and `graphql_schema` concept names, enabling proper entrypoint detection for GraphQL resolvers in JavaScript/TypeScript, Python, and Ruby codebases.

- **Duplicate edge elimination in analysis pipeline**: Added edge deduplication by ID after analyzer runs complete. Some analyzers (e.g., Ruby) could produce duplicate edges with identical IDs; these are now filtered out before writing the behavior map. Example: postal repo went from 3220 edges (114 duplicates) to 3097 unique edges.

- **Ruby analyzer method field extraction**: Fixed root cause of duplicate edges in Ruby analyzer. The code was finding the first identifier child of call nodes, which for `receiver.method` calls like `data.chop` would incorrectly identify "data" (receiver) instead of "chop" (method). Now uses tree-sitter's `child_by_field_name("method")` to correctly extract the method name.

## [1.3.0] - 2026-01-29

### Added

- **Centralized inheritance linker**: New `linkers/inheritance.py` creates `extends`/`implements` edges from `base_classes` metadata across ALL languages, eliminating duplicate edge-creation logic in individual analyzers.

### Fixed

- **Python/JS/TS inheritance edges (INV-008)**: Classes with `base_classes` metadata now create `extends` and `implements` edges to base classes/interfaces defined in the repo. This enables the type hierarchy linker to create `dispatches_to` edges for polymorphic dispatch.
- **Ruby/Kotlin inheritance edges (INV-009)**: Ruby and Kotlin analyzers now extract inheritance information.
- **Swift/C++/Objective-C/Apex base_classes extraction**: Completes META-001 (Metadata Must Become Graph Structure) at 100%. All 13 languages with class inheritance now extract `base_classes` metadata:
  - Swift: class/struct/protocol inheritance and protocol conformance
  - C++: class/struct inheritance with qualified names (std::exception)
  - Objective-C: superclass + protocol conformance
  - Apex: extends + implements clauses

## [1.2.1] - 2026-01-29

### Summary

Major expansion: **37 new analyzers** across languages, templates, config formats, and build systems. New **route-handler** and **type hierarchy** linkers improve web framework and OO codebase navigation. CLI gains `compact` subcommand. Multiple bug fixes for edge uniqueness, entrypoint detection, and crash resilience.

### Added

#### CLI
- **`compact`**: Post-process behavior maps into compact form. Options: `--input`, `--out`, `--max-symbols`, `--coverage`, `--no-connectivity`.

#### Analyzers: Frontend & templates
- **Twig**: blocks/extends/includes/macros; `extends_template` / `includes_template` edges.
- **SCSS/Sass**: variables/mixins/functions/rules; `uses_mixin` edges.
- **Svelte**: imports, slots, events, control flow; `imports_component` edges.
- **Vue SFC**: directives/slots/methods/props; two-pass import resolution.
- **Astro**: frontmatter, imports, slots, client directives; two-pass import resolution.

#### Analyzers: Programming languages (16)
- **Odin**: procedures/structs/enums/unions; imports + cross-file calls.
- **Gleam**: functions/types/aliases; visibility + signatures; imports + calls.
- **V**: functions/structs/enums/interfaces; visibility + signatures; imports + calls.
- **MATLAB**: functions/classes/methods/properties; signatures + cross-file calls.
- **Tcl/Tk**: procedures/namespaces; call edges (filters built-ins).
- **Scheme**: defs + recursive calls; filters special forms (`.scm/.ss/.sld/.sls`).
- **Racket**: defs/structs + recursive calls; `struct`/`module+` (`.rkt/.rktl/.rktd`).
- **Janet**: defs + recursive calls; filters special forms.
- **Fennel**: defs + recursive calls; compiles to Lua.
- **Pascal**: programs/units/functions/procs; case-insensitive calls (`.pas/.pp/.dpr/.lpr`).
- **Haxe**: classes/interfaces/functions; visibility/static; qualified calls.
- **PureScript**: modules/functions/types/classes/instances; qualified calls.
- **Hack**: classes/traits/functions/methods; visibility/static (`.hack/.hh`).
- **Apex**: classes/triggers/methods/fields; visibility/override; qualified calls.
- **Luau**: typed functions + types; qualified calls (`.luau/.lua`).
- **Pony**: actors/classes; reference capabilities; cross-file calls.

#### Analyzers: Data, schema & DSLs (5)
- **KDL**: nodes/sections; arguments/properties; nested hierarchies.
- **Prisma**: models/enums/datasources/generators; `@relation` edges.
- **Smithy**: services/operations/shapes; namespace-qualified names; type refs.
- **SPARQL**: PREFIX/BASE + queries; `uses_vocabulary` edges.
- **Jsonnet**: locals/methods/fields; imports + calls.

#### Analyzers: Build systems & DevOps (4)
- **Meson**: projects/targets/custom targets; deps + subdir includes.
- **BitBake**: recipe vars, inherit, tasks; DEPENDS/RDEPENDS edges.
- **Robot Framework**: keywords/tests/vars; cross-file keyword invocation.
- **Puppet**: classes/defined types/resources; parameter extraction.

#### Analyzers: Docs & config files (7)
- **BibTeX**: bibliography entries, citation keys, authors/years/titles.
- **Markdown**: headings/code blocks/links; `links_to` edges.
- **RST**: sections/directives/refs; toctree/include + cross-doc refs.
- **requirements.txt**: constraints, VCS/URL/editable; `-r/-c` includes.
- **.properties**: key/value + domain categorization; masks secrets.
- **.gitignore**: pattern classification + domain categories.
- **INI/CFG**: sections/settings + domain categorization; masks secrets.

#### Linkers (2)
- **Route-handler linker**: Creates `routes_to` edges from route symbols to handler functions. Supports Rails, Phoenix, Laravel, and Express metadata formats.
- **Type hierarchy linker**: Creates `dispatches_to` edges for polymorphic dispatch. Connects interface/parent methods to concrete implementations (valuable for DI-heavy codebases).

#### Entrypoint detection
- **Manifest-based**: `package.json "bin"`, `pyproject.toml [project.scripts]`, `Cargo.toml [[bin]]` detected with 0.99 confidence.
- **Naming-based**: Classes named `*Controller`, `*Handler`, `*Service` detected with 0.70 confidence (heuristic fallback).
- **Structural**: Python `if __name__ == "__main__"` detected with 0.85 confidence.

#### Framework route extraction
- **Rails**: `resources`/`resource` macros emit individual route symbols for all RESTful actions.
- **Phoenix**: Elixir analyzer creates route symbols with controller/action metadata.
- **Laravel**: PHP analyzer creates route symbols including `Route::resource()` expansion.

#### Quality & governance
- **Meta-invariants**: Introduced three high-level quality principles that unify specific bug fixes:
  - META-001: Metadata Must Become Graph Structure (90%) — semantic relationships in metadata must become traversable edges
  - META-002: Extraction Completeness (95%) — symbols in source code must be extracted for analysis
  - META-003: Data Integrity (100%) — graph elements must have valid, unique identifiers
- **Invariant ledger**: Tracks discovered invariants, root causes, fixes, and regression tests (`.agent/invariant-ledger.md`).

### Fixed

#### Crashes & robustness
- **JSON manifests**: No longer crash when `package.json`/`composer.json` top-level is non-object.
- **Ruby analyzer**: Prevent self-referential call edges.

#### Graph quality (INV-002 through INV-006)
- **INV-006**: Rails `resources`/`resource` macros now infer `controller_action` metadata for route-handler linking.
- **INV-005**: Edge IDs include line number, ensuring uniqueness for multiple calls to same target.
- **INV-004**: Routes get `routes_to` edges to handler functions (metadata now converted to traversable edges).
- **INV-002**: Deferred resolution for cross-file handler references (Django URL patterns, Express routes, etc.).

#### Python analyzer
- **Nested functions**: Extract decorated nested functions (FastAPI router factory pattern).
- **Main guard**: `if __name__ == "__main__"` uses correct concept format for entrypoint detection.
- **Django**: Empty path URL patterns (`path('')`) now correctly detected as routes.

#### Entrypoint detection
- **cargo_binary**: YAML pattern now matches `kind="binary"` (actual analyzer output).
- **HTTP linker**: Falls back to direct `meta.route_path`/`meta.http_method` when concept metadata unavailable.

#### Symbol resolution
- **INV-007**: Go import path resolution now correctly disambiguates when multiple files define the same symbol (e.g., generated protobuf files). `ListNameResolver` tries progressively shorter path suffixes and falls back to deterministic ordering.

## [1.1.0] - 2026-01-24

> Note: This version was tagged in the codebase but never published to PyPI. It marks a milestone with breaking changes relative to v1.0.0. Hopefully our next release will be hiccup-free and actually publish to PyPI.

### Removed

- **Vestigial capsule system** (ADR cleanup)
  - Removed `init` and `export-capsule` commands (marked vestigial in spec)
  - Removed `plan.py`, `llm_assist.py`, `export.py` modules
  - Removed deprecated `Pack` class from catalog (packs replaced by linker activation conditions)
  - Removed `llm-assist` and `llm-local` optional dependencies from pyproject.toml

### Added

**YAML-Driven Analysis (ADR-0003)**
- Main function detection via `main-functions.yaml` for 10 languages (Go, Java, Python, C, C++, Rust, C#, Kotlin, Swift, Dart)
- Test function detection via `test-frameworks.yaml` for 10+ frameworks (pytest, JUnit, RSpec, etc.)
- Language conventions: CUDA kernels, WGSL shaders, COBOL, LaTeX, Starlark (`language-conventions.yaml`)
- Config conventions: NPM, Maven, Android, Cargo, Poetry, TypeScript (`config-conventions.yaml`)
- Pattern system extended with `symbol_name`, `language`, and `prefix_from_parent` fields
- Framework pattern types added to `docs/schema.json` for YAML validation
- YAML linting via `yamllint` in pre-commit hooks
- Play Framework patterns for Scala (`play.yaml`): controllers, Action blocks, WebSocket handlers
- Akka HTTP patterns for Scala (`akka-http.yaml`): route directives, method handlers, WebSocket, auth
- Library export detection (`library-exports.yaml`): Detects exports from index files (index.ts/js/jsx/tsx) as library entry points for JS/TS libraries
- Naming conventions (`naming-conventions.yaml`): Heuristic patterns for `*Controller`, `*Handler`, `*Service` classes (0.70 confidence fallback tier)

**New Commands & Flags**
- `hypergumbo test-coverage`: Static coverage estimation via call graph analysis
- `-x/--exclude-tests`: Exclude test files from sketch sections
- `--progress`: Show ETA during sketch generation
- `--readme-debug`: Debug README extraction algorithm
- `--help --all`: Show all subcommand help at once
- `slice --flat`: Output simple `{nodes, edges}` format for external tools (implies `--inline`)

**Sketch Improvements**
- Source code included by default (`--no-source` to disable)
- "How Representative Is This Sketch?" table showing coverage per section
- README-first hybrid ranking for Additional Files (round-robin: linked/similar/central)
- Multi-format README link extraction (Markdown, Org-mode, RST, AsciiDoc)
- Embedding-based README description extraction with pre-computed probes
- Estimated coverage in Tests section (e.g., "~35% estimated coverage")
- Separate test/non-test LOC breakdown in Overview

**Analyzer Improvements**
- Shared SymbolResolver framework for cross-file resolution (45+ analyzers)
- Parameter type inference for Python, Java, Kotlin, TypeScript
- Common Lisp analyzer (`.lisp`, `.lsp`, `.cl`, `.asd`)
- LLVM IR analyzer (`.ll` files)
- ADR-0004: File taxonomy with `FileRole` enum and 75+ language specs
- ADR-0007: Import tracking for cross-file call resolution
  - Phase 1 complete: JS/TS, Kotlin bug fixes
  - Phase 2 complete: Rust, C#, Ruby, Elixir, Swift, PHP, Scala, Dart
  - Phase 3A complete: Ada, Agda, Clojure, C++, D, Elm, Erlang, F#, Fortran, Groovy, Haskell, Julia, Nim, OCaml, R, Solidity, Starlark, Zig (18 done; Lean blocked by grammar, VHDL has no aliasing)

**CLI Ergonomics**
- Auto-run analysis for query commands when no cached results exist
- Auto-discovery of cached results from `~/.cache/hypergumbo/`
- Slice path suffix matching (`--entry src/main.go` matches full paths)
- Symbol-specific slice output naming (`slice.main.json`)
- Artifact location reporting and summary after `hypergumbo run`
- Forge URL resolution for README links (GitHub/GitLab/Codeberg)

### Changed
- **auto-pr uses fast-forward merge by default**: Preserves commit bodies and DCO. Prompts to rebase if diverged. `--squash` available as emergency fallback (uses git notes).
- **Schema version 0.2.1**: Added framework pattern types to `docs/schema.json`
- Section headers renamed: "Source Content" → "Source Files Content", etc.
- Overview always shows test/non-test breakdown; Tests section always present
- Additional Files excludes boilerplate (LICENSE, .gitignore, CODEOWNERS)
- CI skips expensive jobs for docs-only PRs
- pytest-xdist for parallel tests (`pytest -n auto`)

### Fixed

**Git Notes Recovery**
- Restored 193 orphaned commit bodies via git notes (squash-merged Jan 9-22 2026). View: `git log --show-notes`

**Compact Mode**
- Edge filter changed from OR to AND (was wasting 99%+ on dangling edges)
- Entrypoints filtered to resolvable IDs (fixes "No entrypoints detected")
- Force-include entrypoints in selection (preserves semantic anchors)
- Connectivity-aware selection using greedy frontier algorithm (4x more edges)
- Entrypoints capped to `max_symbols // 2` to leave room for bridge nodes

**Sketch Output**
- File content truncation accounts for markers (~130 chars overhead); files end with newline
- `-x` flag correctly counts non-code repos
- Unified test detection between Overview and Tests sections
- Added `tests.py` and `*_spec.rb` to test detection
- Structure section: tree format with `-x`, shows all root directories, handles flat repos
- Representativeness table shows with `-x` and correct budget for small sketches
- Additional Files representativeness uses mention centrality
- Elevator pitch truncation respects sentence boundaries
- Embedding-based README extraction handles soft line breaks

**Call Graph**
- C/C++ analyzers prefer definitions over declarations (fixes coverage estimation)
- NestJS route paths combine controller + method via `prefix_from_parent`
- NestJS routes normalize to start with `/` (fixes `[GET] test` → `[GET] /test`)
- Framework aliases: Go web frameworks (gin, chi, echo, fiber) now load `go-web.yaml`; Rust web frameworks (axum, actix-web, rocket, warp) now load `rust-web.yaml`
- Python: submodule imports resolve (`from app import crud; crud.func()`)
- Python: imported class method calls resolve (`from X import Class; Class.method()`)

**Entrypoints**
- `slice --list-entries` now respects `--exclude-tests` and `--max-tier` filters

**Other**
- `explain --with-source` output ordering (callers/callees grouped with sources)
- Minimum chunk size for license files in semantic search
- Removed misleading "Coverage requires execution" message

## [1.0.0] - 2026-01-12 (not released to PyPI)

> **Note:** This version was tagged in the codebase but never published to PyPI. It marks a milestone with breaking changes relative to v0.9.1.

Major focus on memory optimization, framework detection improvements,
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
| 2.1.0   | 2026-03-01 | 9 new linkers (DI, HTTP, FFI, Vue, ORM, etc.), 150+ framework patterns, smart test selection |
| 2.0.2   | 2026-02-01 | Default token budget increased to 8000                       |
| 2.0.1   | 2026-01-31 | `--files` flag for slice (smart test selection support)       |
| 2.0.0   | 2026-01-31 | **Breaking:** modular package structure (5 packages), import paths changed |
| 1.3.1   | 2026-01-29 | C++ test framework patterns, go-restful support              |
| 1.3.0   | 2026-01-29 | Centralized inheritance linker, type hierarchy linker        |
| 1.2.1   | 2026-01-29 | 37 new analyzers, route-handler linker, compact subcommand   |
| 1.1.0   | 2026-01-24 | Breaking changes (not published to PyPI)                     |
| 1.0.0   | 2026-01-12 | Memory optimization (80% reduction), YAML-driven entrypoints (not published to PyPI) |
| 0.9.1   | 2026-01-09 | ADR-0003 implementation (was missing in 0.9.0)               |
| 0.9.0   | 2026-01-09 | Schema 0.2.0, --frameworks flag, YAML patterns (incomplete)  |
| 0.6.9   | 2026-01-07 | Fewer false positives, richer slice traversal                |
| 0.6.0   | 2025-12-29 | Lean, Wolfram, Agda analyzers; release automation            |
| 0.5.0   | 2025-12-26 | Initial release: 32 analyzers, 12 linkers                    |

[Unreleased]: https://codeberg.org/iterabloom/hypergumbo/compare/v2.1.0...HEAD
[2.1.0]: https://codeberg.org/iterabloom/hypergumbo/compare/v2.0.2...v2.1.0
[2.0.2]: https://codeberg.org/iterabloom/hypergumbo/compare/v2.0.0...v2.0.2
[2.0.0]: https://codeberg.org/iterabloom/hypergumbo/compare/v1.2.1...v2.0.0
[1.2.1]: https://codeberg.org/iterabloom/hypergumbo/compare/v1.2.0...v1.2.1
[1.1.0]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.9.1...v1.1.0
[0.9.1]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.9.0...v0.9.1
[0.9.0]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.6.9...v0.9.0
[0.6.9]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.6.0...v0.6.9
[0.6.0]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.5.0...v0.6.0
[0.5.0]: https://codeberg.org/iterabloom/hypergumbo/releases/tag/v0.5.0
