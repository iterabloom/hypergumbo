<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Changelog

All notable changes to hypergumbo are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

- Released **tool** is at: v5.0.1
- Released **schema** is at: v0.5.8

This changelog tracks the **tool version** (package releases). The **schema version** is tracked separately in `schema.py` as `SCHEMA_VERSION`. The schema version changes when `docs/schema.json` has significant updates: breaking changes to the behavior map output format (minor bump) or additions like new type definitions for YAML validation (patch bump).

## [Unreleased]

> **User-facing view:** see [docs/RELEASE-NOTES-5.X.md](docs/RELEASE-NOTES-5.X.md)
> for the reader-friendly summary of what's changed. This file (CHANGELOG.md)
> remains the implementer log.

### Added (post-5.0.1)

- **WI-busij: pre-commit / CI lint enforcing axis declaration on every str-typed field of a core dataclass.** Closes ADR-0024 §"Open questions" Q2. Until this PR, ADR-0024's "Creation practice" rule ("any PR that adds a multi-value field to a core dataclass must answer: what axis does this field occupy?") was prose-only and relied on author discipline; the 2026-05-12 audit confirmed ADR-0027 and ADR-0028 both shipped without that rule being exercised at PR time, even though neither caused drift. WI-busij adds the mechanical backstop. New module `packages/hypergumbo-core/src/hypergumbo_core/multi_value_field_axis.py` AST-walks `@dataclass` decorations in `ir.py` and `datamodels.py`, finds every `str` / `Optional[str]` / `str | None` / `Literal[str, ...]` field, and requires each to carry a trailing-line comment `# axis: <category>`. Four allowed categories: `<known-axis-name>` (named axis backed by a registry; the linter resolves the axis through a small `_known_axes` table), `identity` (unique-per-record like ids and hashes), `bounded-enum` (small fixed list documented in the dataclass docstring), and `free-text — <justification>` (open-ended payload no consumer branches on; the justification is mandatory). Five known axes wired into the resolver: the three heavyweight registry-backed axes from ADR-0023 / ADR-0027 / ADR-0028 (`edge-type`, `symbol-kind`, `evidence-type`) plus two catalog-derived lightweight axes added by this PR (`language` and `pass-id`) — instantiating ADR-0024 §4's "use judgment" carveout via two new helpers `all_known_languages()` and `all_known_pass_ids()` in `catalog.py` (single source of truth: the union of `languages=[...]` kwargs and registered pass names across `@register_analyzer` / `@register_linker` call sites). Two ADR-0024 §3 open-question 2 hatches considered and explicitly dropped during the WI-busij design discussion: (a) the `# axis: pending WI-xxxxx` deferral option duplicated the structural-fix protocol's `todo_hard` circuit breaker and weakened the gate at the moment the design conversation should happen — i.e., at PR review, before the field ships; (b) `free-text` without a justification would have been the natural can-kicker since named axes have a registry, `identity` has a uniqueness invariant, and `bounded-enum` has the docstring listing, but `free-text` has no other anchor — so the justification is required. Retroactive annotation pass covers ~40 string fields across `Span` / `AnalysisRun` / `Symbol` / `Edge` / `ExternalRef` / `UsageContext` (in `ir.py`) and `DataModel` (in `datamodels.py`). New CI script `scripts/check-multi-value-field-axis-declaration` wraps the linter with conventional exit codes (0 / 1 / 2); 36 new tests in `test_multi_value_field_axis.py` including the live-tree gate (`test_live_tree_passes` will fail if a future PR adds a new str field on a core dataclass without an axis tag). ADR-0024 §"Open questions" Q2 updated from "Resolved to defer" to "Resolved by WI-busij" with full design-rationale notes. Pre-commit hook wire-up and AGENTS.md essentialization update (both touching governance files) deferred to a follow-on PR requiring human approval; the CI gate via the property test enforces the rule today.

### Fixed (post-5.0.1)

- **WI-vuton: dead-code-maybe now demotes view_func reachable-via-framework symbols and polymorphic-dispatch overrides.** Before this fix, `dead-code-maybe` reported any symbol with zero static call-edge in-edges as "potentially dead", producing 89.3% (3641/4076) FP rate on hypergumbo self-analysis post-INV-tajap. Two systematic FP classes were identified in the WI-vuton tracker item: (a) symbols reached only via framework dispatchers — route handlers, message-queue handlers, decorator-registered callbacks — for which the framework registers the call site at runtime and no static call edge points at the symbol; (b) methods overridden in subclasses where callers go through the base-class method (statically reachable) and the runtime picks the override. Both are architecturally significant code, not dead code. Fix — two heuristics added inside `cmd_dead_code_maybe`. (1) **usage_contexts view_func cross-reference**: when a behavior_map's `usage_contexts[]` entry has `position="view_func"` and a `symbol_ref` pointing at a production symbol, the BFS seed set is extended to include that symbol. Only callable-position entries seed reachability — pure name references (`position="arg_value"`) are ignored to avoid over-promotion. Summary counts the additions under `demoted_view_func`. (2) **Polymorphic dispatch demotion**: post-BFS, for each dead method, walk the transitive ancestor chain via `extends` / `inherits` / `implements` edges. For each ancestor class, look for a same-named method (last segment of `name`, agnostic of qualified-name convention); if found AND that method is in the `reachable` set, demote the dead method. Summary counts the demotions under `demoted_dispatch_inherited`. Heuristic 1 is independent of the analyzer's inheritance-edge fidelity; heuristic 2 requires `extends`-family edges, which the language analyzers already emit (144 `extends` + 1 `inherits` edges in hypergumbo self-analysis) — its yield depends on whether the source codebase uses classical inheritance vs. duck-typed Protocol / registry-based dispatch (hypergumbo's own DefUseExtractor / NameMatcher families use Protocol, so the heuristic correctly emits 0 demotions there). Two new test classes pin the invariants: `TestWiVutonViewFuncDemotion` (4 tests: view_func symbol demoted; summary counts; non-callable position ignored; missing field handled gracefully); `TestWiVutonDispatchInheritedDemotion` (5 tests: direct base-method match → demote; no-match → stays dead; both dead → both dead; transitive ancestor match; free function NOT demoted via method-name collision). Heuristics #2 (framework lifecycle convention list) and #4 (confidence annotation) from the WI-vuton plan are deferred to a follow-up — they require non-trivial framework-pattern enumeration and a schema change to the dead-candidates JSON, both better staged separately. Closes WI-vuton (Wave 6 child of cross-language parity). Tagged `awaits_bakeoff_validation`: the empirical FP-reduction impact depends on the inheritance-edge density of the target codebase and needs measurement across the bakeoff corpus.

- **WI-mafik: per-language dotted-name resolution audit (CLOSES WI-mafik).** The original WI-mafik Level 1b audit work shipped in two prior PRs — `9b63441d40` (polyglot fixture authoring for 7 non-Python languages: JS/TS, Go, Rust, Java, C++, Ruby, Elixir; each loaded with `xfail_reason` capturing the per-language gap discovered at audit time) and `be0ddcb8eb` (WI-tihup + WI-mafik PR2: 7-language analyzer retrofits closing those gaps, all `xfail_reason` fields stripped) — but the tracker item was never formally closed. This PR completes the closure with one further audit extension: two new fixtures (`java_wildcard` and `elixir_except`) covering the two idioms specifically called out in the WI-mafik description that the original 7-fixture set did not exercise. **elixir_except passes** — `import Enum, except: [count: 1]` + bare `map(...)` call resolves correctly to `Enum.map`, confirming the Elixir analyzer's import-with-except branch covers the dual of `import only:`. **java_wildcard fails** — `import java.util.*;` + `Arrays.asList(...)` emits an unresolved edge `java:external:0-0:Arrays.asList:unresolved` instead of attributing `Arrays` to `java.util`. The wildcard-import branch is missing from the Java bare-name resolver. The failing fixture is tagged with `xfail_reason="WI-tuhok: Java analyzer doesn't resolve wildcard imports (import java.util.*) to the source module"` pointing at the newly-filed WI-tuhok follow-up. Audit ledger entry: 9 idioms green, 1 xfail at fixture (WI-tuhok), no other gaps remain in the eight original WI-mafik languages. Closes WI-mafik (Wave 6 child of cross-language parity); the closure note records the audit's final empirical state.

- **WI-zolot: WebSocket linker now emits cross-language client↔server bridge edges.** Before this fix, hypergumbo's own self-analysis had a TypeScript WebSocket client (`packages/htrac-frontend/src/ws-client.ts`) connecting to a Python WebSocket handler (`packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py:433` — `WebSocketRoute("/ws", _ws_handler)`) with **zero** cross-language edges connecting them. The 2026-05-16 round-4 self-analysis hypothesis-investigation identified three concrete gaps: (1) `NATIVE_WEBSOCKET_PATTERN` required `'`/`"` quoted URLs, so template-string URLs like `new WebSocket(\`${proto}//${location.host}/ws\`)` — the canonical browser idiom — never produced an endpoint; (2) Python pattern detection only matched `@app.websocket('/path')` decorator form, but Starlette's routing-table style `WebSocketRoute("/path", handler)` — used by hypergumbo's own `serve.py` — was unrecognised; (3) even when both sides were detected, the linker only emitted within-language send/receive pairings and within-file file→endpoint references — no cross-language pairing logic at all. Fix: three targeted additions to `linkers/websocket.py`. (1) New `NATIVE_WEBSOCKET_TEMPLATE_PATTERN` accepts backtick URLs and extracts the trailing literal path (the part that matches the server's route declaration); the character class `[a-zA-Z0-9_/-]` excludes `$`/`{`/`}` so any `${...}` interpolations in the leading portion are consumed by the non-greedy prefix. (2) New `TS_TEMPLATE_URL_PATH_PATTERN` plus a `HAS_WEBSOCKET_CONSTRUCTOR` file gate handles the function-extracted URL idiom (e.g., hypergumbo's own `getWsUrl()` helper called at `new WebSocket(getWsUrl())`): when a file uses `new WebSocket(...)` anywhere, every template literal with a `${...}` interpolation followed by a trailing literal path is treated as a URL-shaped template. The interpolation requirement discriminates URL templates from plain-literal log messages. (3) New `STARLETTE_WEBSOCKET_ROUTE` regex matches `WebSocketRoute("/path", handler)` as a Python endpoint with `pattern_type="starlette"`; the framework slug joins `fastapi`, `django_channels`, `socketio`, `native_websocket`, `ws` in `_PATTERN_TYPE_TO_FRAMEWORK`. (4) New cross-language bridge logic groups endpoints by path; whenever a client-side endpoint (`pattern_type ∈ {native, socketio, ws}`) shares a path with a server-side endpoint (`pattern_type ∈ {fastapi, starlette, django_channels}`), the linker emits an `edge_type="calls"` edge with `meta["protocol"]="ws"`, `meta["url_path"]=<path>`, `meta["cross_language"]=True` (when client and server have different languages), `meta["client_framework"]`, and `meta["server_framework"]`. Edge convention matches the HTTP linker's client→server pairing shape (`http.py:1511-1528`) so consumers can treat WS and HTTP cross-language edges uniformly. New `TestWiZolotCrossLanguageBridge` (14 tests) in `test_websocket.py` pins all three gaps and their interactions: inline template URL extraction at trailing path; pure-literal backtick URL; function-extracted URL via `HAS_WEBSOCKET_CONSTRUCTOR` gate; FP-rejection of URL-shaped templates in files without `WebSocket(...)`; FP-rejection of plain literals without `${...}`; Starlette `WebSocketRoute` with single+double quotes; bridge edge shape (src/dst/meta); no-bridge when paths differ; no-bridge when only client side; no-bridge when same-language; FastAPI decorator still bridges; full self-analysis-shape end-to-end fixture mimicking `packages/htrac-frontend/src/ws-client.ts` + `serve.py`. Empirical result on hypergumbo self-analysis post-fix: **0 → 12 WS bridge edges**; the target edge `typescript:.../ws-client.ts → websocket:.../serve.py:433:/ws:endpoint` now exists with `meta["server_framework"]="starlette"`, `meta["cross_language"]=True`. The remaining 11 edges are bridge edges from `ws-client.ts` to test fixtures (FastAPI/Starlette WS declarations in `packages/hypergumbo-core/tests/test_websocket.py` and `packages/hypergumbo-lang-mainstream/tests/test_starlette_routes.py`); test exclusion is out of scope for this fix and lives with the broader find_files filter policy. Closes WI-zolot (Wave 6 child of cross-language parity); tagged `awaits_bakeoff_validation` for empirical confirmation against the bakeoff corpus.

- **WI-pulor: bash function Symbols now populate `lines_of_code`.** Before this fix, every bash function Symbol carried `lines_of_code=None` despite a valid span — the bash analyzer's `function_definition` branch in `bash.py` set `start_line` and `end_line` on the Span but never computed LOC. Downstream, the dead-code-maybe formatter's `? LOC` fallback rendered every bash function as `pass ... ? LOC` instead of `pass ... 1 LOC`, signaling "we don't even know how big this function is." The 2026-05-17 verification on self-analysis confirmed 153 of 153 bash function Symbols had `lines_of_code=None`. Fix: a single new kwarg `lines_of_code=end_line - start_line + 1` on the function Symbol constructor matches the convention documented at `ir.py:349` and used by `py.py::_compute_lines_of_code`. Scope is intentionally narrow to function Symbols — exports and aliases are 1-line declarations whose intrinsic LOC=1 isn't worth a separate change, and that out-of-scope decision is the original WI-pulor item's recommended boundary. New `TestWiPulorFunctionLinesOfCode` (3 tests) in `test_bash.py` pins the invariant: single-line function → LOC=1; multi-line function → end-start+1; multiple functions in same file → all non-null. Closes WI-pulor (Wave 6 child of cross-language parity). Note on the original item's second claim: "no intra-script call edges captured" was already refuted by the 2026-05-17 follow-up check (211 intra-script bash call edges exist on dev), so only the LOC=None part needed fixing; the dead-code FP visibility for bash helpers is now downstream of WI-vuton's polymorphic-dispatch / lifecycle heuristics, not of this item.

- **INV-tajap (PR 3 of 3 — script_module scope; CLOSES INV-tajap): entrypoint detection now recognizes TS/JS standalone-script modules.** Before this fix, TS/JS files like `packages/htrac-frontend/src/main.ts` (SPA bootstrap) and `vite.config.ts` (build-time config) were not detected as entrypoints, so their transitive callees looked unreachable. PRs 1 and 2 closed the bash and `index.html` sub-fixes; this PR closes the third and final sub-fix, satisfying the full INV-tajap statement. Fix: new `EntrypointKind.SCRIPT_MODULE = "script_module"` enum value and `_detect_script_modules(nodes, edges)` helper in `entrypoints.py`. The helper iterates over file-kind Symbols in `typescript` / `javascript` and emits a `SCRIPT_MODULE` entry with confidence 0.80 when **both** conditions hold: (a) no inbound `imports` edge has `dst == sym.id` (nobody imports this file — it isn't a library module), and (b) at least one outbound `calls` edge has `src == sym.id` (the file does work at module load — the proxy for the tracker's "top-level executable code, not just exports" criterion). Unlike `shell_script` / `html_entry` (PRs 1 + 2), which derive from analyzer-stamped `meta.concepts`, this rule requires the full edge set (you can only check "no inbound imports" after every analyzer + linker has run), so the detection lives in `detect_entrypoints` itself rather than in `_detect_from_concepts`. Empirical result on hypergumbo self-analysis post-fix: 95 → 97 entrypoints; **two SCRIPT_MODULE entries surface**: `packages/htrac-frontend/src/main.ts` (the only TS file in the repo with module-level calls and no incoming imports — the SPA bootstrap) and `packages/htrac-frontend/vite.config.ts` (the build-time Vite config that calls `defineConfig(...)` at module level). The five other TS files (`item-detail.ts`, `item-list.ts`, `tracker-app.ts`, `sw-register.ts`, `ws-client.ts`) all have inbound imports from `main.ts` and are correctly excluded. New `TestInvTajapScriptModuleEntrypoint` (6 tests) in `test_entrypoints.py` pins the invariant: no-inbound + has-calls → SCRIPT_MODULE; inbound import → excluded; no-inbound + no-calls → excluded (inert data); non-TS/JS file → excluded; JavaScript files qualify too; two qualifying files in the same repo each get an entry. Spec §entrypoints `kind` field updated to document `script_module` including the edge-set rule. **Cumulative INV-tajap impact (PRs 1-3)**: 64 → 97 entrypoints (+52%); 36 shell_script + 1 html_entry + 2 script_module = 39 non-Python entrypoints (was 0). Closes META INV-tajap (cross-language entrypoint detection — was: "Python-only, 0 of 64 entrypoints are bash/TS/JS/HTML on self-analysis"; now: every supported non-Python script class has a detector). Bakeoff validation: INV-tajap remains tagged `awaits_bakeoff_validation` per the Wave 6 entrypoint-coverage rule.

- **INV-tajap (PR 2 of N — html_entry scope): entrypoint detection now recognizes `index.html` SPA roots.** Before this fix, HTML files were parsed and got file-kind Symbols but no concept rode on the file Symbol, so the SPA root that bootstraps the JS bundle (e.g. `packages/htrac-frontend/index.html`) looked like inert content. Any JS route reachable only via the `<script src=...>` tag was thus unreachable from the entrypoint set. Fix: (1) `html.py` stamps `{"concept": "html_entry", "framework": "html"}` on every parsed `index.html` file Symbol (case-insensitive filename match — `INDEX.HTML`, `Index.html`, and `index.html` all qualify); non-index HTML files (`page.html`, `404.html`, templates) do NOT carry the concept, so the rule has zero false-positive surface on non-entrypoint pages. (2) `entrypoints.py` adds `EntrypointKind.HTML_ENTRY = "html_entry"` and a concept-handler branch that emits a `HTML_ENTRY` entry with confidence 0.85 (same structural tier as `shell_script`) and an `HTML entry ({path})` label. Empirical result on hypergumbo self-analysis post-fix: 94 → 95 entrypoints; `packages/htrac-frontend/index.html` surfaces as the single `html_entry` (the only first-party `index.html` in the repo — `packages/htrac-frontend/dist/index.html` is a build artifact filtered by DEFAULT_EXCLUDES, correctly). New `TestInvTajapHtmlEntryEntrypoint` (5 tests) in `test_entrypoints.py` pins the entrypoint side: concept → HTML_ENTRY; python-only repo emits 0; mixed repo emits MAIN_FUNCTION + SHELL_SCRIPT + HTML_ENTRY; multi-app monorepo with two `index.html` files yields two entries; duplicate concept dedup. New `test_inv_tajap_*` (4 tests) in `test_html_analysis.py` pin the analyzer side: `index.html` → concept; `page.html` → no concept; `INDEX.HTML` case-insensitive; `subdir/index.html` still qualifies. Spec §entrypoints `kind` field updated to document `html_entry`. **Scope clarification**: this PR closes the html_entry sub-fix of INV-tajap only. The `script_module` sub-fix (TS/JS files with top-level code and no inbound imports) is the third and final sub-fix, shipping in a follow-up PR. INV-tajap stays `violated` until that lands.

- **INV-tajap (PR 1 of N — bash scope): entrypoint detection now recognizes bash/sh scripts.** Before this fix, `entrypoints.py` was Python-only: on hypergumbo self-analysis it detected 64 entrypoints, 100% Python (`main_function: 17, library_export: 39, http_route: 8`) and zero of the ~50 bash scripts in `.githooks/`, `scripts/`, etc. — every bash function in the repo looked unreachable, which inflated WI-vuton's `dead-code-maybe` candidate rate to 92.6% (3719/4016) with `--seeds entrypoints` (a 53-point swing versus `--seeds all` at 39.5%). Fix: (1) the bash analyzer (`bash.py`) now stamps a `{"concept": "shell_script", "framework": "bash"}` entry on every parsed bash file's module Symbol (the file-kind Symbol with the canonical `make_file_id` shape) — this is safe to apply unconditionally because `find_bash_files` already requires either a shebang line (extensionless executables) or a `.sh`/`.bash` extension as its discovery criterion; (2) `entrypoints.py` gains a new `EntrypointKind.SHELL_SCRIPT = "shell_script"` enum value and a concept-handler branch in `_detect_from_concepts` that emits a `SHELL_SCRIPT` entrypoint with confidence 0.85 (same structural tier as Python's `main_guard`) and a `Shell script ({path})` label pointing at the script. Empirical result on hypergumbo self-analysis post-fix: **94 entrypoints (was 64, +47%); 36 shell_script entries** (was 0); top-of-list examples `.githooks/commit-msg`, `.githooks/post-checkout`, `.githooks/pre-commit`, plus 31 more bash scripts across the repo. New `TestInvTajapShellScriptConcept` (3 tests) in `test_bash.py` pins the bash-analyzer side: `.sh` file + shebang → concept on file Symbol; extensionless shebang script → concept; function Symbols inside the script do NOT carry the concept. New `TestInvTajapShellScriptEntrypoint` (6 tests) in `test_entrypoints.py` pins the entrypoint side: concept → SHELL_SCRIPT kind; Python-only repo emits zero; mixed bash+Python repo emits ≥2 entries (one each kind); label contains the script path; two distinct bash files yield two entries; duplicate `shell_script` concepts on the same Symbol dedup to one. Spec §entrypoints `kind` field updated. **Scope clarification**: this PR closes the bash sub-fix only. INV-tajap also calls for `html_entry` (index.html SPA roots) and `script_module` (TS/JS files with top-level code and no inbound imports); those ship in follow-up PRs. Tracker tagged `awaits_bakeoff_validation` per the burn-down plan's risk-mitigation rule for entrypoint-coverage PRs in Wave 6.

- **INV-judod: JS/TS import edges now use the canonical file Symbol ID as `src`.** Before this fix, every `imports` edge produced by the JS/TS analyzer wired `src` to a synthesized ID `{lang}:{path}:1-1:{file_path.name}:file` (e.g., `typescript:packages/htrac-frontend/src/main.ts:1-1:main.ts:file`) while the actual file Symbol's ID — built by the same module via `make_file_id(lang, str(file_path))` — was `{lang}:{path}:1-1:file:file` (literal `file` in the name slot, not the basename). The two IDs never collided, so every TS/JS import edge pointed at an orphan node not present in the symbol table; the js-module-linker-v1 layer faithfully propagated the wrong `src` through `import_resolution` and `ast_import` re-emissions. Downstream graph queries asking "what does file X import?" returned zero results for every first-party TS/JS file on hypergumbo self-analysis (0/15+ ground-truth imports captured per file), while the Python analyzer — which always used `make_file_id` — surfaced 10009 import edges correctly. Fix: three sites in `js_ts.py` (ES6 `import_statement`, CommonJS `require('literal')`, CommonJS `require(varname)`) replace the local `_make_symbol_id(..., file_path.name, "file", ...)` call with `make_file_id(lang, str(file_path))`. The helper is already imported in this module; the Symbol producer at line 2988 already uses it, so the analyzer is now internally consistent. New test class `TestImportEdgeSrcWiring` in `test_js_ts.py` pins the invariant across all four code paths: ES6 import, JS `require` (static literal), JS `require(name)` (dynamic), and TS import — each test asserts `edge.src == make_file_id(lang, str(file_path))` AND that this ID appears in the analyzer's emitted Symbol set (no orphan). Closes INV-judod (Wave 6 child of cross-language parity).

- **WI-jupar: comparison-budget sketches no longer accumulate in `/tmp/hypergumbo_sketch_compare/`.** Before this fix, `cmd_sketch` wrote two comparison-budget sketches (4× and 16× of the user-supplied `-t` budget) to `/tmp/hypergumbo_sketch_compare/sketch.<tokens>.withsource.md` with no cleanup and a budget-only filename — observed accumulation: 27 files / ~30 MB / oldest file 4 weeks old on a dev machine. Filenames were keyed only on token count, so two repos run back-to-back race-overwrote each other (single-file Python tmp repo `sketch.32000.withsource.md` 322 B at 00:25 → test-only tmp repo same path 318 B at 00:26 — if the user followed the suggested `cp /tmp/... /home/.../.cache/...` instruction between the two runs, they'd `cp` the wrong repo's content into their cache). The producer's user-facing message even told users to manually `cp` files into `~/.cache/hypergumbo/<hash>/...` — leaking the cache directory layout as a UX requirement. Fix: comparison sketches now write directly to `cache_dir` (the per-(repo, state, analyzer-identity) results-cache directory) alongside the main sketch, using `cache_write` (the `user_cache` safety zone). No `/tmp/hypergumbo_sketch_compare/` directory is created during a sketch run. The message updates to "hypergumbo also cached comparison sketches: 4x ... 16x ..." pointing at the cache paths (no `cp` step). One-shot drain: if a stale `/tmp/hypergumbo_sketch_compare/` exists from a prior release, `cmd_sketch` `shutil.rmtree(..., ignore_errors=True)` it on first invocation (the path is unambiguously ours — we created it). Now that the artifacts live in the cache, they ride normal cache lifecycle (`cache-status` counts them, `cache-clear` removes them, INV-padum's honk threshold sees them). Drop now-unused `tmp_artifact_write` import from `cli.py`. New test file `test_wi_jupar_sketch_compare_cleanup.py` with 4 tests pinning the invariants: no `/tmp/hypergumbo_sketch_compare/` created during a run, comparison sketches present in `cache_dir` at both 4× and 16× token counts, the user-facing message no longer says `cp` or names `/tmp/hypergumbo_sketch_compare`, and the legacy directory is cleaned up if pre-existing. Lifecycle policy declaration: cache-resident-with-state for the comparison-sketch producer; closes the third and final child of META INV-manov (Wave 5) and the META itself transitions to satisfied.

- **INV-padum: cache lifecycle policy — honk-threshold-with-retention.** Before this fix, `~/.cache/hypergumbo/` grew without bounds and the only signal was the user noticing disk pressure (observed: 7.9 GB / 76 entries / 1 repo / 3 days on dev machine). The first-class `cache-status` command reported aggregate "Entries: 4 / Total size: 7.9 GB" with no per-repo breakdown, so a user discovering the cache was large couldn't see which repo to target. Fix per the user-set design direction (retain over evict): (1) new `_get_honk_threshold_bytes()` helper reads `HYPERGUMBO_CACHE_HONK_GB` (default 1.0 GiB; `0` / `off` / `none` / `false` silences; malformed values warn and fall back); (2) new `_maybe_honk_cache()` helper emits a loud stderr warning when total cache size exceeds threshold, naming the top consumer and listing the next-step commands (`cache-status --per-repo`, `cache-clear --repo <id> --keep-latest 5`) and the silence knob; (3) `cache-status --per-repo` lists every top-level fingerprint subdir with size, state-entry count, and last-used age, sorted by size descending so the bloat source is the first line; (4) `cache-clear --repo <fingerprint>` restricts deletion to one repo's subtree; (5) `cache-clear --repo <fingerprint> --keep-latest N` keeps the N most recent state-hash subdirs under `<repo>/results/` and prunes the rest (honors `--dry-run` and `--quiet`); (6) the honk fires at the end of every `hypergumbo run` (where the user is most likely to act) AND at the end of `cache-status` (where they explicitly asked). No automatic eviction is performed at any threshold — the design is retention-with-warning, not eviction. New test classes `TestHonkThreshold` (10 tests covering threshold parsing, silence modes, malformed values, fire/silent paths, missing-cache no-op, in-cache-status integration), `TestPerRepoBreakdown` (5 tests covering per-repo listing, size-desc sort, state-subdir counting, file-skip, missing-results-dir tolerance), `TestCacheClearKeepLatest` (7 tests covering keep-N semantics, dry-run, keep-0, repo-without-keep-latest, unknown-repo, keep-latest-without-repo error, missing-results-dir), and `TestCacheCommandWiring` (2 tests covering argparse plumbing for the new flags). Lifecycle policy declaration: honk-threshold-with-retention for the analysis-cache producer (INV-manov Wave 5 child #2).

- **Test-infra: conftest autouse fixture no longer triggers per-test HuggingFace model re-downloads.** Surfaced by WI-jadig (which added fixture files to ~13 `test_cmd_run_with_<lang>_analyzer_skipped` tests, expanding the surface that triggers the full `run_behavior_map` pipeline). The autouse `isolate_hypergumbo_cache` fixture at `packages/hypergumbo-core/tests/conftest.py:69` was setting `XDG_CACHE_HOME=tmp_path/_xdg_cache` to isolate `~/.cache/hypergumbo/` between tests — but `XDG_CACHE_HOME` is the umbrella variable HuggingFace Hub also honors (`$XDG_CACHE_HOME/huggingface/hub` is its default), so every test that triggered a `SentenceTransformer(...)` load saw an empty HF cache, re-downloaded ~1.5GB of model weights (`microsoft/unixcoder-base` + `nomic-ai/modernbert-embed-base`) into the test's tmpdir, and held it there across the pytest-xdist worker's lifetime. With 6 workers, a single test session left 9-18GB in `/tmp/pytest-of-*/`, exhausting the 12GB tmpfs and either crashing later tests with "No space left on device" or wedging workers on a CLOSE-WAIT'd proxy connection for hours (xet daemon's startup ping ignores `local_files_only=True`). Fix: same fixture now also pins `HF_HOME=~/.cache/huggingface` (the existing warm cache shared by all workers via HF Hub's `flock`-based cross-process coordination — one copy on disk, one cache miss possible per cold-cache run) and `HF_HUB_OFFLINE=1` (skip every network call, including the xet freshness ping that hangs on dead proxies). Net effect on the previously-failing 263-test affected-files run: 2h+ hang → 2m 56s green. Cache-warmth verified: `~/.cache/huggingface/hub/` has both models pre-cached at 963MB + 573MB. No source code change; conftest-only fix with a docstring expansion.

- **WI-jadig: analyzer dispatch is now pre-filtered by per-language file presence.** Before this fix, `run_all_analyzers` submitted every registered analyzer to the worker pool unconditionally and let each one walk the FileIndex to discover it had zero files to process — on hypergumbo self-analysis, 113 of 133 AnalysisRuns produced zero symbols/edges yet consumed ~12.7s of wall-clock (13.3% of total analyze time) doing pure dispatch + tree-sitter init + empty-traversal overhead. Fix: the orchestrator now threads `profile` through `cli.py → analyze.all_analyzers.run_all_analyzers`, and a new `_filter_by_file_presence` helper drops every analyzer whose declared `languages` are all absent (or all `files==0`) from `profile.languages` before the executor submits the work. Skipped analyzers are recorded in `limits.skipped_passes` with `reason="no files matched"` so consumers (and the spec-documented `analysis_runs[].skipped_passes` surface) see exactly which passes the dispatcher short-circuited — distinct from the existing missing-optional-dep skip path. Multi-language analyzers (e.g., a hypothetical `ts_js` that handles both typescript and javascript) run if **any** of their declared languages has files>0, mirroring the existing per-language-glob behavior. Empirical result on hypergumbo self-analysis: 133 → 56 dispatched AnalysisRuns; 101 passes appear in `skipped_passes` with the new "no files matched" reason; metrics-level counts (`total_files=1179`, `total_nodes`, `total_edges`) unchanged because the skipped passes produced zero output by definition. Spec section §analysis_runs `skipped_passes` updated to document the second-reason case. Lifecycle policy (file-presence pre-filter) for the analyzer-dispatcher producer per ADR-0024-style declaration; closes the first child of META INV-manov (Wave 5).

- **WI-soraj: `metrics.total_files` is now the canonical "files in this repo" answer, with the two non-canonical counts relocated to `metrics.debug`.** Before this fix, three different file-count values appeared in the same behavior_map — `metrics.total_files` (unique node paths, 890 on hypergumbo self-analysis), `sum(profile.languages[*].files)` (extension/finder-based, 1179), and `count(nodes[kind=file])` (analyzer-emitted file pseudo-nodes, 877) — with no documented relationship between them. An agent reading `total_files` got an under-count that didn't match disk reality and disagreed with the per-language sum. Fix: `compute_metrics` now accepts the `profile` dict and sets `total_files = sum(profile["languages"][L]["files"])` — the same number `profile` reports, which in turn (post-INV-hokig) agrees with `analysis_runs[L].files_analyzed` for languages whose analyzer registers a `find_files` callable. The two non-canonical numbers move to `metrics.debug.unique_paths_in_analysis` and `metrics.debug.analyzed_file_symbols` for tooling that needs the introspection. When `compute_metrics` is called without a profile (callers outside `run_behavior_map`), `total_files` falls back to `unique_paths_in_analysis` and the debug block still rides — consumers can always tell which definition they're looking at. Spec section `### metrics — optional counts` updated to document the canonical semantics, the debug block, and the no-profile fallback. End-to-end verified on hypergumbo self-analysis: `metrics.total_files == 1179 == profile-language sum`. Closes the final child of META INV-tolaj (Wave 4).

- **INV-hosuh: entrypoint detection emits exactly one `main_function` entry per executable script.** Before this fix, `detect_entrypoints` flagged both the module-level `if __name__ == "__main__"` block (via the `main_guard` concept on the module Symbol) AND any `main()` function in the same file (via the `main_function` concept on the function Symbol) as independent entrypoints, with no cross-Symbol deduplication. On hypergumbo self-analysis this produced 17 `main_function` entries for 11 distinct executable scripts — 54% inflation — and consumers reading `len([e for e in entrypoints if e.kind == "main_function"])` got an over-count that disagreed with `find scripts/ -executable | xargs grep -l 'if __name__'`. The two entries also had different `symbol_id` (one on the module, one on the function) and different `label`, so consumers couldn't programmatically collapse them. Fix: added a post-detection dedup pass in `detect_entrypoints` that groups MAIN_FUNCTION entries by the source Symbol's path. When a group has both a `function`-kind entry and a module/file-kind entry, the function-kind entry wins (it's the canonical "real" entrypoint; the main-guard marker is redundant once the actual main() function is detected). Scripts that have only one of the two (e.g., `__main__.py` files with no separate `main()`, or a bare `main()` with no main-guard) keep their single entry untouched. New test class `TestInvHosuhMainFunctionDedup` pins the invariant with 4 cases (both present → function wins, guard alone → kept, function alone → kept, two scripts with both → one entry each). Verified end-to-end on hypergumbo self-analysis: 17 → 11 main_function entries, matching the 11 distinct scripts on disk. Closes the third child of META INV-tolaj (Wave 4).

- **INV-hokig: `profile.languages[L].files` now agrees with `analysis_runs[L].files_analyzed` for languages whose canonical file set extends past extension globs.** Before this fix, `profile.detect_profile` counted bash files by extension-only glob (`*.sh`, `*.bash`) and reported 11 files on hypergumbo self-analysis, while the bash analyzer's `_find_source_files` used `find_bash_files` (extension globs + shebang line detection on extensionless executables) and analyzed 42 files. The 31-file gap was real bash code in `.githooks/pre-commit`, `scripts/auto-pr`, `scripts/ci-debug`, and other extensionless shell scripts the analyzer correctly picked up but the profile missed. The originally-filed root cause (different exclude policies between profile and analyzer) was wrong — both paths apply DEFAULT_EXCLUDES correctly. The real divergence was enumeration strategy. Fix: added an optional `find_files: Callable[[Path], list[Path]] | None` field on `RegisteredAnalyzer` and a matching `find_files=` kwarg on the `register_analyzer` decorator. When an analyzer registers a canonical file-finder, `profile._detect_languages` delegates the per-language count to it; otherwise it falls back to extension globbing as before. Bash registers `find_bash_files`. Spec section `### profile — repo characteristics` and the `LanguageStats` docstring updated with the new semantics. Property test `test_inv_hokig_bash_includes_extensionless_shebang` pins the invariant: a 2-file fixture (one .sh + one extensionless shebang) yields `languages["bash"]["files"] == 2`. Closes the second child of META INV-tolaj (Wave 4); generalizes to any future analyzer needing custom enumeration (e.g., Python via `python3` shebang, Ruby via `ruby` shebang).

- **INV-tosum: profile.languages no longer double-counts shell scripts under both 'bash' and 'shell' keys.** Before this fix, `taxonomy.get_language_extensions()` and `get_analyzable_extensions()` both injected the `LANGUAGE_ALIASES` map ({"shell": "bash"}) as duplicate keys into their return dicts, with the same extension patterns under both names. `profile.py:_detect_languages` then iterated `LANGUAGE_EXTENSIONS.items()` and enumerated the same 10 .sh files twice, emitting two entries with identical stats — misleading any downstream consumer that counts languages or derives ratios from the dict. Root cause: alias resolution was implemented as "duplicate the entry" instead of "resolve to canonical at lookup time" — every future alias would inherit the same bug. Fix: drop the alias-injection loops in both `get_language_extensions()` and `get_analyzable_extensions()`, leaving exactly one entry per canonical LANGUAGES entry. `LANGUAGE_ALIASES` remains exported as a public dict; the one alias-aware caller (`partial_install_warnings.py`) already used the safe `LANGUAGE_ALIASES.get(name, name)` pattern. Cross-language audit per Structural Fix Protocol confirmed the bug is isolated to taxonomy.py: `_CATALOG_ALIASES` (io_boundary.py) and `_FRAMEWORK_ALIASES` (framework_patterns.py) both use the safe `.get(id, id)` resolution pattern, not duplicate-key injection. New property tests `test_no_alias_keys_in_language_extensions` and `test_no_alias_keys_in_source_extensions` pin the invariant via `set(EXTENSIONS).isdisjoint(LANGUAGE_ALIASES)`; new end-to-end `test_inv_tosum_bash_not_double_counted` runs the full profile pipeline on a 2-file .sh fixture and asserts `languages["bash"]["files"] == 2` AND `"shell" not in languages`. Three pre-existing tests in `test_taxonomy.py` that pinned the buggy behavior were inverted. Cleaned up the obsolete `_LANGUAGE_ALIASES` private alias (kept for ADR-0004 Phase-2 backward-compat within taxonomy.py; no remaining consumers after the fix). Closes the first child of META INV-tolaj (file-set membership reconciliation).

- **WI-vuhas: sketch progress no longer contaminates captured stderr.** Before this fix, `hypergumbo sketch . > sketch.md 2>&1` (the natural shell pattern when redirecting stdout to a file) wrote 165+ accumulated progress lines into the output file because both progress producers — `show_progress` in `cli.py:7090-7110` and the `SketchProgress` class in `sketch.py:317-485` — used `\r`-based redraw correctly for TTY output but never checked whether stderr was actually a TTY. When stderr is captured (`2>&1`, pipe, file redirect), the `\r` bytes are preserved literally and downstream consumers (text editors, markdown renderers, `git diff`) render the accumulated progress text as 165 noise lines preceding the real sketch content. Fix: both producers now early-return when `sys.stderr.isatty()` is False (the `--no-progress` opt-out flag is unchanged for explicit suppression). 15-min diagnostic spike per the burn-down plan confirmed sink-side, not producer-side: the `\r` mechanism is correct; only the gate was missing. Verified empirically — `hypergumbo sketch . > /tmp/s.md 2>&1` on a fresh fixture goes from 165+ progress lines to 0. Both surfaces are inside the existing `# pragma: no cover` block (`SketchProgress` class-level + `show_progress` / `complete_progress` nested functions) — no new test coverage required; existing 569 sketch+progress tests pass unchanged.

- **Nightly grammar build: re-pinned tree-sitter-lean and bumped `SHAPE_ID_SCHEME` from `hypergumbo-shapeid-v1` to `hypergumbo-shapeid-v2`.** Upstream `github.com/Julian/tree-sitter-lean` was force-pushed / rebased between the original pin (`efe6b87…`, dated 2026-02-20 in the script) and 2026-05-20, dropping the pinned commit from every ref on the remote and breaking nightly's "Build source-only grammars" step with `fatal: reference is not a tree: efe6b87…`. `scripts/build-source-grammars`'s `LEAN_COMMIT` now points to the current `refs/heads/main` HEAD `78b6e3d4…`. The other two source-built grammars — wolfram (`63ebdac6…`) and circom (`02150524…`) — were verified reachable on their respective `main` HEADs and left unchanged. Because the new lean commit may have CST-level grammar changes vs. the unreachable old pin, the global `SHAPE_ID_SCHEME` (`schema.py`, `test_behavior_map_schema.py`, `docs/hypergumbo-spec.md` example) was bumped per the build-script's documented bump-on-grammar-upgrade rule — downstream consumers of behavior-map `shape_id` strings should treat `v2` as a clean break from `v1`. One real CST-level rename was hit: the lean grammar's typeclass-application node is now `app` (was `apply`); `lean.py` accepts both via an `or` fallback so a future re-rebase is tolerated. Verified 20/20 lean analyzer tests pass against the new grammar. This patch is a stopgap; a follow-up campaign to vendor the three source-built grammars under `vendor/tree-sitter-{lean,wolfram,circom}/` (tracker item to be filed) removes the upstream-rebase recurrence class entirely.

### Changed (post-5.0.1)

- **INV-morag PR 2: dropped the `-v1` / `-ts-v1` / `-ast-v1` suffix from `pass_id`; catalog now derives from the analyzer/linker registries.** Breaking JSON-output change. The pre-PR-2 catalog (hand-written `Pass` list in `catalog.py`) declared IDs like `"python-ast-v1"`, `"javascript-ts-v1"`, `"bash-v1"`; the runtime emitted `make_pass_id("python") = "python-v1"`, `make_pass_id("javascript") = "javascript-v1"`, `make_pass_id("bash") = "bash-v1"` — the two sides drifted on every release, and the catalog encoded backend identity (ast / tree-sitter / pattern) inside the ID where it didn't belong. PR 2 collapses both sides onto the analyzer/linker's `@register_*` `name` argument: `make_pass_id(name) == name` (identity), `build_catalog_from_registries()` in `catalog.py` derives the catalog dynamically from `_ANALYZER_REGISTRY` + `_LINKER_REGISTRY` (no hand-written list), and the legacy suffix is gone. Backend identity moves to a new `Pass.backend` field (`"ast"`, `"tree-sitter"`, `"pattern"`, `"protocol"`, ...); display labels move to `Pass.pass_label`; per-pass versioning lives in `AnalysisRun.pass_version` (already shipped in PR 1) populated automatically at decoration time via `compute_pass_version(func)`. The `register_analyzer` and `register_linker` decorators gain optional kwargs `description`, `pass_label`, `backend`, `languages`, `availability`, `requires` that flow into the registry dataclasses; metadata not provided at the decorator site falls back to a transitional `_PASS_METADATA` dict in `catalog.py` (kept until each language package migrates its metadata into the call site). New `scripts/check-pass-id-agreement` CI gate asserts (1) every registered name yields a Pass in the catalog with matching ID, (2) no catalog ID carries a legacy suffix. New `test_inv_morag_pr2_auto_catalog.py` pins eight tests: decorator stores new metadata; decorator auto-computes `pass_version`; `make_pass_id` is the identity; no catalog ID has legacy suffix; every registered analyzer/linker appears in the derived catalog; catalog ID == runtime `make_pass_id(name)` for every registered name; the CI script exits 0 on a clean repo. Spec section §AnalysisRun.pass_id updated to document the new format + the catalog/runtime convergence. Closes INV-morag (Wave 3 child of INV-rugok).

- **INV-morag PR 1 / option A: `AnalysisRun.pass_version` — real per-pass version via code-hash.** New `compute_pass_version(module_or_func)` helper in `hypergumbo_core.ir` returns `sha256(inspect.getsource(module))` prefixed `"sha256:"`. New `AnalysisRun.pass_version: str` field (default `""` for back-compat) carries the per-pass code-hash. The legacy `-v1` suffix in `make_pass_id()` ("python-ast-v1", "bash-v1", etc.) was a fake-versioning artifact — it bumped on every package release whether or not pass logic changed, so caches and reproducibility comparisons couldn't tell "this analyzer's behavior changed" from "this analyzer's package version bumped." Module-level hashing replaces that fake signal with a real one: changes when the pass module changes, doesn't change when unrelated package code is bumped. Module-level (vs function-level) hashing was chosen because most analyzers depend on helper functions and constants in the same file; hashing just the registered function misses changes in those helpers and leads to stale cache hits. Cross-module helper changes (e.g., to `analyze.base`) are still missed — those are covered by the surrounding package version in `AnalysisRun.version`. PR 1 lands the field additively (producers default to `""`); PR 2 of INV-morag will propagate non-empty values to every registration site and drop the `-v1` suffix from pass IDs entirely. Spec section `analysis_runs[]` updated to document `pass_version`. Five new property tests in `test_provenance_inv_morag.py`: hash format (`sha256:<64 hex>`), stability under repeated calls, divergence across modules, function-input fallback, and explicit-sha256 agreement.

- **INV-morag PR 1 / option B: `behavior_map["reproducibility_context"]` — L2 capture with explicit L3-L5 disclaim.** New top-level block in every behavior map captures the L2 reproducibility level: hypergumbo version, Python interpreter version + implementation, tree-sitter library version (when importable), and per-grammar versions (every `tree-sitter-*` package on PYTHONPATH, sorted). Plus an explicit `not_captured` array documenting what we don't record: transitive pip-package versions, OS / kernel / libc / locale / timezone / environment variables, and hardware (CPU model, microcode, instruction set extensions, floating-point determinism). Plus an `implications` string telling the consumer what level of diff-attribution they can expect from these fields alone — e.g., "matching pass_versions + hypergumbo_version + python_version + tree_sitter_version + grammars implies functional identity up to OS-level and hardware variation; unexplained diffs suggest a not_captured factor." This is the honest version of the "reproducibility" claim: a spectrum, not a yes/no, and the disclaim array is the honest part. `build_reproducibility_context()` in `schema.py` is the producer; called from `create_empty_behavior_map()` so every fresh behavior map carries the block. Spec section `reproducibility_context` updated to document the field, the L0-L5 taxonomy, and consumer-side diff-attribution guidance. Five new tests in `test_provenance_inv_morag.py`: shape (level/captured/not_captured/implications keys), captured L2 fields (always-present hypergumbo/python versions; conditional tree-sitter/grammars), not_captured array content (mentions OS + transitive + hardware), implications-is-string, and end-to-end (a `hypergumbo run` on a small fixture writes `reproducibility_context` at the top level).

- **WI-bujim: populated `behavior_map["features"]` as a spec-shape index pointing into the top-level `nodes[]` / `edges[]` arrays (option (c)).** Before this fix, the producer at `schema.py:101` hardcoded `features: []`, and 100% of self-analysis runs emitted an empty array — even though `_emit_handler_slices` was already computing forward slices for every detected route handler and writing them to `<out-stem>.slices/slice.handler.<METHOD>.<path>.json` plus a companion `slice.handler.index.json`. The spec at `docs/hypergumbo-spec.md:798-802` documents `features[]` as a first-class public surface with stable query-derived IDs for diff-across-commits, listed at line 1701 as a Core field that "cannot remove or change type". The discovery layer the spec describes already existed — just as a sibling file instead of inside the behavior map. Fix: `_emit_handler_slices` now appends `result.to_dict()` (the spec-compliant `SliceResult` serialization, which already produces `id` / `name` / `entry_nodes` / `node_ids` / `edge_ids` / `query` / `limits_hit`) to `behavior_map["features"]` for every emitted handler, BEFORE the slice-file-specific mutations that add inline denormalized `nodes` / `edges` / `meta` blocks. The behavior map's `features[]` is therefore index-only — `node_ids[]` and `edge_ids[]` are graph-ID pointers into the top-level `nodes[]` / `edges[]`, not duplicated content. Full denormalized slice payloads stay in the per-handler slice files for offline/portable consumption. Handlers over the cap (default 25) do not contribute to `features[]` but still appear in the index file with `emitted=False`. Stable feature ID matches the spec formula `sha256(json.dumps(query, sort_keys=True))` via `SliceResult.feature_id` — same query on same code yields the same id, enabling diff-across-commits. Spec section §features[] updated to document the index-vs-content split. Four new pin tests in `test_handler_slices.py::test_emit_handler_slices_populates_behavior_map_features` and siblings: spec-compliant shape on the happy path, stable-id formula match, no-op when `enabled=False`, and the over-cap handlers are excluded from `features[]` but indexed elsewhere.

- **INV-bovif: stopped `cli.py` `_NOISE_KINDS` from dropping SQL `CREATE TABLE` entities alongside TOML/INI config tables.** Original INV-bovif scope (filed 2026-05-17 R17) claimed four framework linkers — event-sourcing, http, graphql-resolver, database-query — had unmigrated edge-construction code targeting deleted node kinds after the schema 0.2.2 → 0.8.0 migration. Empirical investigation (2026-05-19) refuted that diagnosis: all four linkers' filters and emission paths are migrated correctly to the new `framework_role` / `call_kind` / `language` schema (verified via the existing 338 passing tests in `test_{database_query,event_sourcing,http,graphql_resolver}_linker.py`, each asserting non-zero edge counts on synthetic overlap-fixtures). The real bug is a single conceptual leak in the CLI noise filter: `_NOISE_KINDS` in `cli.py:7370-7381` included the literal `"table"`, intended to suppress TOML/INI `[section]` headers (config noise), but the SQL analyzer (`sql.py:225`) emits the same `kind="table"` literal for `CREATE TABLE` constructs (first-class schema entities). `_is_noise(sym)` filtered on `kind` only, ignoring `sym.language`, so the SQL `users` table from `tests/fixtures/schema-coverage-corpus/sql/schema.sql` was dropped from every self-analysis output despite surviving `run_all_analyzers()`. Downstream effect: the `database_query` linker's `_get_table_symbols(ctx)` returned `[]`, `table_lookup` was empty, and the 34 detected `db_query` call_sites could never link to a table. Fix: remove `"table"` from `_NOISE_KINDS`, add a new `_TABLE_NOISE_LANGUAGES = frozenset({"toml", "ini", "properties"})`, and a `kind == "table" and language in _TABLE_NOISE_LANGUAGES` carve-out in `_is_noise` — mirroring the existing `_CSS_LANGUAGES` carve-out for `kind="variable"` (noise in CSS, real in Python/Go/YAML). Self-analysis post-fix: 1 SQL table Symbol now surfaces (was 0); `database-query-linker-v1` now produces 17 edges from origin (was 0). The other three linkers' 0-edge results on hypergumbo's codebase remain — confirmed unrelated to the schema migration: `event_sourcing` has 3 publishers and 11 subscribers with zero overlapping event names; `http` has 18 client calls to `/api/users`/`/api/orders`/`/api/products` but 8 server routes to `/api/items`/`/health`/`/ws` (non-overlapping fixtures); `graphql_resolver` has 11 resolver symbols but zero GraphQL schema files in the repo. New test class `TestSqlTableSurvivesNoiseFilter` in `test_max_tier.py` with two pin tests: `test_sql_table_survives_default_filter` (SQL `users` table appears under `include_docs=False`) and `test_toml_table_still_filtered_by_default` (regression guard — TOML `[section]` tables remain filtered). The existing `test_default_excludes_doc_kinds` was updated to drop `"table"` from its `doc_kinds` set with a docstring note pointing at the per-language carve-out. Bakeoff target: any repo with SQL DDL files + database-query call sites in source code; restored coverage will surface in the database_query edge count delta against the Apr 23 baseline.

- **INV-rolul: closed the WI-tisam end-to-end gap by extending the JS/TS analyzer's UC-emission pipeline with an HTTP/GraphQL server-handler extractor.** WI-donud validation (2026-05-19) on apollo-server's smoke-test consumer revealed that the WI-tisam framework_patterns YAML rules in `frameworks/graphql.yaml` and `frameworks/node-http.yaml` were silently no-ops end-to-end despite passing their unit tests — the unit tests synthesise UCs and feed them to `match_usage_patterns` in isolation, but at runtime the JS/TS analyzer only emitted `kind="call"` UsageContexts for the small `_APP_BOOTSTRAP_NAMES` / `_APP_BOOTSTRAP_QUALIFIED` allowlist (createRoot, ReactDOM.*, hydrateRoot, createBrowserRouter family, app.{on,once,whenReady}). The WI-tisam target names (`startStandaloneServer`, `runHttpQuery`, `executeHTTPGraphQLRequest`, `{http,https,http2,Http}.createServer`, bare `createServer`) never produced UCs, so `match_usage_patterns` had nothing to match against. Fix: new `_extract_http_handler_contexts(tree, source, file_path, module_symbol, line_offset)` in `js_ts.py` (parallel to `_extract_app_bootstrap_contexts`) walks every `call_expression` AST node, matches identifier callees against `_HTTP_HANDLER_NAMES` and member-expression callees against `_HTTP_HANDLER_QUALIFIED`, dedupes within a file, and emits a `kind="call"` UC with `position="caller"`, `symbol_ref` pointing to the file pseudo-node, and `metadata={"http_handler_function": <name>}`. The orchestrator pipeline now invokes the new extractor alongside the existing bootstrap extractor in the per-file UC sweep. Eight new pin tests in `TestHttpHandlerUsageContext` cover each name in the WI-tisam set plus a negative test for unrelated calls. The broader INV-rolul invariant — every framework YAML `usage:kind:call` pattern must be reachable from the analyzer's UC-emission registry — remains open for the still-unfixed JS/TS YAMLs (adonisjs, mcp, restify, `fastify.route`, web_audio.*) and as a cross-language audit (Python / Go / Java analyzers).

- **INV-bahov: fixed vue_component linker emitting file Symbols with a legacy id shape that prevented cross-producer dedup (third sibling of INV-ronuf / INV-movor).** Cross-linker audit after INV-movor PR #3823 (`grep -ln 'kind="file"' linkers/*.py` over the four candidates: websocket, js_module, vue_component, route_handler) surfaced `linkers/vue_component.py::link_vue_components` as the remaining unfixed analogue. Root cause is the same shape bug on the third identity axis: `_make_component_file_id(rel_path)` returned the legacy `vue:{rel_path}:component_file:1:{name}` shape while the orchestrator's `synthesize_file_symbols_for_dangling_edges` and the Vue analyzer emit canonical `vue:{rel_path}:1-1:file:file` (`make_file_id`) for the same path. Two id shapes never collide; id-equality dedup misses every cross-producer case. Fix is the exact INV-movor pattern: `linkers/vue_component.py` now imports `make_file_id` + `make_file_stable_id` from `analyze.base`, removes `_make_component_file_id`, uses `make_file_id("vue", rel_path)` for the emitted Symbol id, `make_file_stable_id("vue", rel_path)` for the stable_id, and a pre-pass map `existing_file_symbol_by_canonical_id` so `get_or_create_file_symbol` reuses an existing canonical Symbol from `ctx.symbols` when one is present rather than minting a parallel shadow. Three new tests pin the fix in `TestInvBahovCanonicalFileIdReuse`: `test_canonical_file_id_shape`, `test_skips_when_canonical_file_symbol_already_present`, `test_stable_id_uses_canonical_helper`. Hypergumbo self-analysis has no `.vue` files so dogfood magnitude here is 0; Vue-heavy bakeoff cohorts (Chatwoot reports 1093 Vue component nodes) are the validation target.

- **INV-movor: fixed js_module linker emitting file Symbols with a legacy id shape that prevented cross-producer dedup (sibling of INV-ronuf / WI-hifol).** Self-analysis on hypergumbo found 5 `.ts` paths in `packages/htrac-frontend/src/` with duplicate `kind="file"` Symbols, one from `javascript-v1` (analyzer + orchestrator file-synthesizer with canonical id `typescript:{path}:1-1:file:file`) and one from `js-module-linker-v1` (with legacy id `typescript:{path}:module_file:1:{stem}` per audit-findings 0005). The id shapes never collided so id-equality dedup silently missed every cross-producer case, mirroring the WI-hifol shape (PR #3819) on a different identity axis: WI-hifol was wrong _path component_ (absolute vs repo-relative), INV-movor is wrong _suffix shape_ (`:module_file:1:{stem}` vs `:1-1:file:file`). Fix: `linkers/js_module.py::link_js_modules` now uses `make_file_id(lang, rel_path)` for the emitted Symbol id (was `_make_module_file_id`, now removed), `make_file_stable_id` for the stable_id, and a pre-pass map `existing_file_symbol_by_canonical_id` so when the orchestrator's `synthesize_file_symbols_for_dangling_edges` or a language analyzer has already produced a canonical file Symbol for this (lang, path), the linker reuses that Symbol's id instead of emitting a parallel shadow — exact parallel of the WI-hifol `existing_ids` dedup in websocket.py. Verified by dogfood: 5 → 0 duplicate file Symbols on hypergumbo self-analysis, and `js-module-linker-v1` now emits zero new file Symbols (every resolved import target already has a canonical file Symbol from upstream). Three new tests pin the fix: `TestInvMovorCanonicalFileIdReuse::test_canonical_file_id_shape`, `::test_skips_when_canonical_file_symbol_already_present`, and `::test_stable_id_uses_canonical_helper`. One test fixture in `test_route_handler_linker.py` updated to the canonical id shape.

- **WI-hifol: fixed websocket linker dedup-against-existing miss caused by absolute-path file ids (INV-ronuf no_move regression).** PR #3805 (INV-ronuf) intended to dedup the WS linker's file Symbol synthesis against existing canonical file ids via `existing_symbol_ids`, but the dedup silently missed every cross-producer collision on hypergumbo self-analysis (3 duplicate file Symbols persisted post-fix). Root cause: `WebSocketPattern.file_path` was populated from `Path` objects yielded by `find_js_ts_files` / `find_python_files` (absolute paths), so `_make_file_id(language, pattern.file_path)` produced ids embedding `/home/.../packages/.../file.py` — while the orchestrator's `synthesize_file_symbols_for_dangling_edges` and the language analyzers emit canonical ids embedding repo-relative paths (`packages/.../file.py`), because `analyze/all_analyzers.py::run_all_analyzers` normalizes every Symbol's path before linkers run. The two id shapes never collided; the post-linker path-normalization sweep at `cli.py:7244-7247` fixed `Symbol.path` but not the embedded path in `Symbol.id`. Fix: `linkers/websocket.py::link_websocket` now mirrors the orchestrator's exact normalization algorithm (forward-slash + repo_root prefix-strip, NO symlink resolution) on every `pattern.file_path` after pattern collection, so all downstream `_make_file_id` / `_make_symbol_id` / `Symbol.path` / `Edge.src` / `Edge.dst` constructions embed repo-relative paths byte-equivalent to the orchestrator's output. Verified by dogfood: 3 → 0 websocket-origin duplicate file Symbols on hypergumbo self-analysis. Four new tests pin the fix: `test_skips_phantom_when_repo_relative_canonical_id_present` (production dedup case), `test_file_symbol_paths_are_repo_relative` (path representation invariant), `test_edge_endpoints_use_repo_relative_paths` (edge endpoint id invariant); the two existing INV-ronuf dedup tests are tightened to pass repo-relative existing ids (matching production). Structural-fix-protocol analogue check identified the same shape in the js_module linker (5 duplicate file Symbols on 5 `.ts` paths in `htrac-frontend`) — filed separately as INV-movor for a follow-up PR.

- **INV-kokaj: extended the file-canonical rule cross-language (JS/TS, Bash, Perl, PHP, PowerShell).** Sibling of INV-hojus: applies the same per-file-anchor collapse that PR #3813 shipped for Python to every other analyzer that was emitting a synthetic `<module:filename>` `kind="module"` wrapper. Each producer now emits the file pseudo-node via `make_file_id(lang, path)` with `kind="file"` and `name=<repo-relative path>`, so the orchestrator file-symbol synthesizer's `existing_ids` dedup converges on a single canonical Symbol per `(language, path)` pair. `bash.py` / `perl.py` / `powershell.py` flip the existing `make_symbol_id(..., "<module:filename>", "module")` site in `extract_symbols_from_file` and the matching string-name lookup in `extract_edges_from_file`; `bash.py::register_symbol`'s kind filter grows from `("function", "module")` to `("function", "file")`. `js_ts.py` and `php.py` thread a new optional `repo_root` parameter into `_extract_symbols` so the file Symbol's `name` is the repo-relative path even before the cli-level `_relativize_ir_paths` pass (which only touches `.id` and `.path`, never `.name`); the two Pass-2 `mod_sym_name = f"<module:{pf.path.name}>"` lookups switch to `pf.path.relative_to(repo_root)`. `js_ts.py::_extract_symbols_and_edges` (legacy single-file path) drops the `s.kind == "module"` filter for the top-level-call-attribution lookup, switching to `s.kind == "file" and s.language == lang`. `perl.py` sets `create_file_symbols = False` because the analyzer now emits the canonical file Symbol explicitly with the file's real `end_line` (the base-class auto-emitted span=1-1 Symbol would otherwise collide on id). Perl `package_statement` Symbols **keep** `kind="module"` because they represent real namespacing constructs, not per-file anchors. New cross-language property test `test_inv_kokaj_cross_lang_file_canonical.py::TestInvKokajFileCanonicalKind` pins the invariant for all five languages and asserts the no-double-representation rule.

- **INV-hojus: collapsed Python file/module double-representation to file-kind canonical.** Before this fix, every Python file with module-level executable code emitted TWO Symbols for the same path: a `kind="module"` pseudo-node from `py.py` with id shape `python:{path}:1-end:<module:filename>:module`, AND a `kind="file"` Symbol from the orchestrator file-symbol synthesizer (`analyze/base.py::synthesize_file_symbols_for_dangling_edges`) whenever an edge targeted the canonical file id. Self-analysis on hypergumbo found 332 paths with both kinds, 466 with file-only, 4 with module-only — every "both" case left the module-kind node 100% disconnected from the structural graph. `py.py:_extract_file_analysis` now emits the file pseudo-node using `_make_file_id(str(py_file))` (canonical shape `python:{path}:1-1:file:file`), `kind="file"`, and `name=<repo-relative path>`, so the synthesizer's `existing_ids` check converges on a single canonical Symbol per path. `Symbol.span` keeps the full `1..end_line` extent for slice traversal; `meta={"concepts":[{"concept":"main_guard"}]}` rides through unchanged so entrypoint detection still fires. `file` joins `module` in `linkers/containment.py::CONTAINER_KINDS` (Phase 2 span-based fallback now emits file → top-level-class contains edges), `slice.py::_CONTAINER_KINDS` (slice expansion across file containers), `linkers/registry.py::find_enclosing_symbol` defaults (kind_priority shares the file-level slot with module so nested children always win), and `linkers/orm.py` (file-kind is reachable as fallback container for module-level model references). New property tests in `test_python_ast_analysis.py::TestInvHojusFileCanonicalKind` pin three claims: no Python path carries `kind="module"`; no Python path has both kinds; and analyzer-emitted file pseudo-nodes use the canonical file-id shape. The cross-language facet noted in INV-hojus's discussion (TS / bash / perl / php / powershell analog) remains a tracker follow-up — those producers still emit `<module:filename>` synthetic file wrappers.

- **WI-fanun: populated `Symbol.fingerprint` for source-code Symbols via a new centralized helper.** Adds `hypergumbo_core.fingerprint` with `compute_symbol_fingerprint(language, span, source)` (Python uses `ast`; tree-sitter languages use the language pack via a pre-order cursor walk; comment-typed nodes are filtered; class methods retry via `textwrap.dedent` when an indented snippet fails bare `ast.parse`). A single orchestrator post-pass at `cli.py:7286` stamps the fingerprint on every Symbol whose `fingerprint` is None and whose span/path resolves against the on-disk source; existing manifest producers (toml-v1 / json-v1 / wgsl-v1) are skipped via the `is not None` guard. New top-level schema field `symbol_fingerprint_scheme: "hypergumbo-symbol-fp-v1"` documents the wire format. On a self-analysis of `packages/hypergumbo-tracker`: 3072 methods + 568 classes + 430 functions + 122 variables + 80 file Symbols + 29 modules + 13 bash functions now carry structural fingerprints (was 0 before WI-fanun). `external_symbol`, `call_site` with span 0-0, and regex-only-analyzer Symbols correctly stay null.

- **WI-higap: enforced `Edge.origin` / `Edge.origin_run_id` are non-empty at construction.** `Edge.__post_init__` now hard-raises `ValueError` if either provenance field is empty, preventing silent regressions where a future producer constructs edges without stamping `(pass_id, run.execution_id)`. The original WI-higap claim (425 edges with empty `origin_run_id`, 1 edge with both empty) was the visible symptom on hypergumbo's self-analysis; the structural sweep covered 67 production Edge construction sites across 35 files (full `_extract_edges` / `_extract_import_edges` plumbing in `py.py`; centralised `_emit` plumbing in `manifest_targets.py`; threaded `run_id` through helpers in `json_config.py`, `latex.py`, `scip/{edges,calls}.py`, and 25 niche-language analyzers). `Edge.from_dict()` injects a `LEGACY_DESERIALIZED_SENTINEL` value for empty fields in legacy on-disk JSON so cache reads still work; a new property test in `test_edge_provenance_invariant.py` asserts hypergumbo's own self-analysis emits zero edges with empty provenance and zero edges carrying the sentinel (sentinel may only appear via deserialization, never from a producer).

### Summary

- **Closes the ADR-0027 / ADR-0028 concept-axis program.** 111 `Edge.evidence_type` and 71 `Symbol.kind` endpoint_shape values retire from their registries.
- **Disambiguation-fallback discipline across 13 linkers.** Ambiguous simple-name resolutions now carry `confidence ≤ 0.5` + `meta["disambiguation_fallback"]=True`. A new fallback-coherence linter pins the contract statically; the producer-coherence linter learns to read through inline ternaries, f-strings, and Constant sentinels.
- **`Edge.dst_ref` — structured external-target sibling field — becomes canonical** across 8 analyzers and four consumers, eliminating the historical Rust 6-segment dst fabrication.
- **Per-entry-point safety claims.** Hypergumbo's own audited-IO-surface self-audit ships via a project-local taint catalog, wrapper discipline for fs-write sites, and a SECURITY.md generator.
- **View-template linker grows from Rails-only to five frameworks** (Rails + Django + Phoenix + Spring MVC + Laravel Blade). A new third-party-base allow-list adds Django-ecosystem `dispatches_to` coverage (HierarkeyForm, FilterSet, DRF Serializer, Wagtail Page).
- **`hypergumbo run --gzip` and `--no-sketch-fan-out`** shrink large-repo output ~10× on the wire. The results cache key now segments by analyzer identity, ending stable+dev cross-poisoning.
- **Twelve bugfixes** span release tooling, supply-chain tier classification (django/utils + Phoenix ExUnit), JS/TS access-mode coverage, framework-linker gating, interface-extends-interface dispatch, and `--backend rust-analyzer` install advice.

### Added

#### Convention-based view-template linker family — Rails + Django + Phoenix + Spring + Laravel

Rails was the only producer of `renders` edges. The probe-and-emit core moves into `_view_template_core.py` and four sister strategies land against it — Django, Phoenix, Spring MVC, Laravel Blade — bringing the producer set to five.

- **Shared core (`_view_template_core.py`)** factors out two reusable strategies: `MethodNameStrategy` (paths from class+method naming — Rails, Phoenix, Django CBV defaults) and `ExplicitStringStrategy` (paths from string literals — Django `render()`, Spring return value, Laravel `view()`). The shared driver filesystem-filters, deduplicates, and emits with `evidence_type="naming_convention"` + `confidence=0.85` + `meta["detection_pattern"]`. Rails refactored as a thin `RailsStrategy(MethodNameStrategy)`.
- **Django** — explicit-string strategy re-parses Python source for `render(request, "<template>", ctx)` and class-body `template_name`; CBV-default strategy walks transitive bases of `DetailView` / `ListView` / `CreateView` / `UpdateView` / `DeleteView` / `FormView` and derives the template from `model = <Name>`.
- **Phoenix** — handles 1.x `lib/my_app_web/templates/<ctx>/<action>.html.{eex,heex,leex}`, 1.7+ co-located `controllers/<ctx>_html/<action>.html.heex`, and the parallel `MyAppWeb.UserHTML.show` function-component shape. Drops `defp` private functions and Plug callbacks (`init` / `call` / `action`); LiveView out of scope (inline templates).
- **Spring MVC** — gates on `@Controller` (excludes `@RestController`) + mapping annotation. Recognizes plain string returns and `ModelAndView("users/show", model)`; skips `redirect:` / `forward:`. Probes Thymeleaf / FreeMarker / Velocity under `src/main/resources/templates/`, then JSP at `src/main/webapp/WEB-INF/views/`.
- **Laravel Blade** — gates on `app/Http/Controllers/` + transitive `Controller` base. Walks PHP method bodies for `view(...)` and `View::make(...)`; dotted view names map to directories; probes `.blade.php` before plain `.php`.

#### Structured external-target IR (`Edge.dst_ref`) + 7-language adoption

`Edge.dst_ref: ExternalRef | None` is the canonical sibling carrying `(lang, module_path, name)`; legacy `Edge.dst` stays populated for back-compat and cache compatibility.

- **`SCHEMA_VERSION` 0.7.1 → 0.7.2 → 0.8.0.** Field lands at 0.7.2; producers canonicalise at 0.8.0 with consumers preferring it over colon-split heuristics. `from_dict()` defensively reads `d.get("dst_ref")` so pre-0.7.2 cached JSON loads with `dst_ref=None`.
- **`hypergumbo_core.ir.ExternalRef`** — frozen dataclass `(lang, module_path, name)`; aliased imports bind `name` to the imported symbol, not the local alias.
- **`hypergumbo_core.import_scope.ImportScope`** — shared analyzer-layer abstraction whose population methods mirror construct shapes across the 8 audited languages, with `resolve(local_name) → CanonicalName(module, name)` and `dst_ref_for(local_name, lang) → ExternalRef` accessors.
- **Per-analyzer adoption** spans Java (static-import threading), Go (`dot_imports` for `import . "strings"`), Elixir (aliased modules + `import Mod, only:` unresolved emission), JS/TS (canonical-name attribution over local aliases), C++ (system-`#include` pre-walk with comma-joined module hints), Rust (`use_aliases` in three new cases, eliminating the historical 6-segment dst), and Ruby (constant-external fallback for `Set.new` / `JSON.parse`).
- **Consumer migration.** `io_boundary` chain composition and `ir.create_boundary_nodes` prefer `edge.dst_ref` fields over the legacy colon-split heuristics.
- **Polyglot call-site coverage.** `test_polyglot_call_site_coverage.py` extends from 1 Python fixture to 8; all 7 `xfail strict=True` markers strip and re-introducing an `xfail_reason` is a regression signal.

#### Per-entry-point safety claims + wrapper-function discipline

A per-entry-point taint-flow model distinguishes what each CLI subcommand category is allowed to do. Verified by `hypergumbo verify-claims docs/hypergumbo.claims.yaml`.

- **Claims YAML** at `docs/hypergumbo.claims.yaml`: 18 per-entry-point taint-flow claims. Runtime CLI subcommands cannot reach `host_fs` / `network` / `subprocess` / `install_artifact` / `dev_zone`; extras subcommands declare their specific allowed zones; dev-zone reachability is prohibited from every entry-point.
- **Project-local catalog** under `docs/hypergumbo-self-catalog/` (not shipped to PyPI): six entry-point source YAMLs, five peer-zone sink YAMLs, one sanitizer YAML. Loaded via `extra_catalogs:` in the claims YAML.
- **Wrapper-function discipline.** New `hypergumbo_core.safety_zones` ships eight thin wrappers around fs-write primitives, each calling `_safety_zone_barrier()` (a sanitizer for every entry-point taint label, so BFS stops at the wrapper). The 15 raw fs-write sites in `cli.py` / `build_grammars.py` / `gitleaks.py` / `sketch_embeddings.py` migrate.
- **Algorithm extensions.** `TaintSource.start_at` (default `caller`, opt-in `callee`) lets synthetic entry-point sources seed BFS precisely at the source-callee; `_sink_module_compatible` filters short-name collisions when the edge's dst carries a module hint; sanitizers index as a list per callee name (was: flat dict overwrite); `verify-claims` runs propagation per-language (an Elixir `HTTPoison.get` sink had been matching every Python `.get()`, producing ~15K spurious findings on self-analysis).
- **CFG ↔ DDG bridge (ADR-0017 §1c follow-through).** The CFG builder's def/use extractors were registered via decorator but never invoked. New `populate_def_use_for_cfg` walks the AST in parallel with the CFG; a new `atomic_statements` field on `CfgNodeMapping` (populated from cfg_nodes YAMLs) stops decomposition at statement-level nodes. `verify-claims` invokes `build_function_cfg → populate_def_use_for_cfg → solve_reaching_defs` for every Python function.
- **SECURITY.md generator.** New `scripts/generate-security-md` reads the claims YAML and produces the audited-IO-surface section between sentinel comment markers; determinism contract is byte-identical output for identical input. Vulnerability-reporting policy below the sentinels preserved verbatim; `--check` mode gates CI.
- **Known limitation.** Short-name sink matching still overapproximates for receivers the DDG can't resolve (call-return bindings, parameter receivers, closure captures). SECURITY.md documents the residual; load-bearing claims (dev-zone and install-zone unreachability) verify cleanly. Receivers bound by file-scope imports or local module-attribute assignment are resolved by the post-DDG IR refinement pass (below).
- **Post-DDG IR refinement pass.** When Python can't type-infer a method-call receiver (`x = os.environ; x.get(...)`), the call emits as `python:external:0-0:get:unresolved` — `_sink_module_compatible` exempts `external` edges from short-name-collision filtering, so `dict.get` / `args.get` / `os.environ.get` / `multiprocessing.Queue.get` conflate. New `taint_refine.py` runs between `solve_reaching_defs` and propagation: it walks DDG edges back to RHS imports, yields `(call_line, attr_name) → module_hint` for import-rooted chains and `from`-import aliases, and `refine_external_edges` rewrites the dsts in place (idempotent on edges without an applicable hint). Scope: Python today; Rust / TypeScript follow ADR-0017 §1c when those ship def/use extractors. Call-RHS bindings, parameter receivers, and closure captures stay unresolved — the pass does not invent hints.
- **Additional zone-tagged wrappers — `rmtree` / `chmod` / `unlink`.** Four further wrappers in `safety_zones` give hypergumbo's own mutating callsites distinct, zone-tagged callees so short-name sinks no longer overapproximate: `cache_rmtree` (`user_cache`) for `cmd_cache_clear`, `tmp_artifact_rmtree` (`tmp_artifact`) for `build_grammars`, and `install_artifact_chmod` / `install_artifact_unlink` (`install_artifact`) for the gitleaks install/uninstall paths. Self-catalog YAMLs updated in lockstep. Complements the post-DDG refinement pass — that pass resolves receivers the DDG can prove; wrapper discipline handles the residual.
- **Stop-the-line repair.** `io_primitives/javascript.yaml` had `methods: [on]` for `process.on` — `on` is YAML 1.1 boolean `True`, so the auto-derived TaintSource crashed `propagate_taint_structural`. Quoted to `["on"]`.

#### Third-party-base allow-list for Django-ecosystem dispatch BFS

New `django-third-party-dispatch` linker (Django-framework-gated companion to the always-on `django-orm-dispatch`) emits `dispatches_to` edges from Python subclasses of four well-known third-party bases — HierarkeyForm, django-filter `FilterSet` / `WagtailFilterSet`, DRF `Serializer` family, and Wagtail `Page` — to their framework-called override methods. Edges carry `meta["framework_dispatch"]="django_third_party"`; unqualified short-name matches colliding with in-tree classes drop to `confidence=0.5` + `meta["disambiguation_fallback"]=True`. Contributes ~38 method-edges across pretix + bakerydemo + lutris (`ModelAdmin` excluded because `django-orm-dispatch` already covers it).

#### IO-boundary noise reduction — F3 Filters 1 / 2 / 3

Three filters in `_compute_external_potential` reduce chain volume:

- **Filter 1 — skip unresolved edges** (per ADR-0028). Cuts the high-volume tail of unresolved-receiver call edges (low-signal "we don't know what this is" rows). ~4,521 chains cut on self-analysis.
- **Filter 2 — closed-world stdlib gating.** `IoBoundaryCatalog` gains `stdlib_modules` (frozenset of authoritative names), `stdlib_prefixes` (hierarchical namespaces like Java's `java.*` or Go's `encoding/*`), and `stdlib_module_completeness` (module → audit `retrieved:` date). When `module_hint` matches a closed-world module, the chain is skipped. `scripts/refresh-stdlib-modules <language> --version X.Y` regenerates `stdlib_modules` from the live interpreter; Python only for now (300 modules from 3.12; `math` is the inaugural worked-example closed-world entry).
- **Filter 3 — composition fix for self-prefixed dst names.** When the dst's `name` already carries the module-qualified form (`re.MULTILINE`) and the extracted `module_hint` is the same module, the naive prepend produced `re.re.MULTILINE`. Filter 3 skips the prepend in that case. ~30 chains cut.

`test_c.py::TestCStdioIdentifierRefs::test_io_boundary_tags_emitted_edges` updated to the post-reclassification `logging` value for `stdio.stdout` / `stdio.stderr` (missed in 8de2f67015).

#### Other

- **`hypergumbo_core.axis_meta_keys` — canonical registry for `Symbol.meta` / `Edge.meta` keys.** Structural sibling of `symbol_kinds.py` / `evidence_types.py`. Seeded with the keys empirically observed across producer code (fold residues, ADR-0023 protocol / bridge / dispatch vocabularies, dataflow access modes, common Symbol.meta annotations). Property test enforces no duplicates, valid axes, no name collisions with typed fields. Drift detection (subscript-access AST walker) is a separate follow-on.

- **Solidity `contract` kind registered canonically.** `SYMBOL_KINDS` gains `SymbolKindSpec("contract", AXIS_LANGUAGE_CONSTRUCT)` — a top-level construct sibling to `class` / `interface` / `struct`. The Solidity analyzer already emitted `kind="contract"`; this registers it canonically. CUDA / Android XML companion folds live under the ADR-0027 / ADR-0028 closure subsection in Changed.

- **Canonical dampener stack pinned end-to-end.** Four tests pin the `_CANONICAL_DAMPENERS` tuple stage order (`tier`→`file_kind`, eight names), `apply_common_method_name_weights` multipliers at three collision counts, the `apply_sibling_impl_weights` 7-member group tail factor, and an integration check that `compute_dampened_centrality` agrees with manual stage-by-stage application — catching internal-reorder regressions a tuple-identity check would miss.

- **RCT-consumer public-API surface pinned via introspection tests.** Nine tests pin the four monkey-patch surfaces the four-arm bundle RCT depends on: `ranking.rank_symbols` parameter list, eight RCT-critical `Edge` dataclass fields, ten `run_behavior_map` parameters with stable defaults, the `_LINKER_SUBCATEGORIES` tuple. Variants (`mgumbo` / `dgumbo`) attach by import path + signature; failure messages name the RCT dependency, with source-comment markers at each definition site.

- **`io-boundaries --json` envelope `schema_version`.** `IO_BOUNDARIES_SCHEMA_VERSION = "1.0"` ships as the inaugural wire-contract version (separate from the behavior-map envelope). Locked top-level keys: `schema_version`, `total_io_edges`, `boundaries`, `unsupported_languages`; bumping rules live in the module docstring; `TestIoBoundariesEnvelopeSchema` loud-fails on silent drift.

- **`limits.failed_files[]` now actually populated (INV-buhur).** `Limits.add_failed_file()` and the spec'd `{path, reason, analyzer}` shape had been defined since the original limits design, but no producer wired into it: every drop site bumped `files_skipped` and silently lost per-file granularity, so `failed_files` was always `[]` even when files had been dropped. `AnalysisRun` grows a `failed_files: list[dict]` field plus a `record_failed_file(path, reason)` helper; the orchestrator's `collect_analyzer_result` drains each run's entries into `Limits.failed_files`, stamping `analyzer` from the run's `pass_id`. Wired across 29 producer sites in 18 files spanning core/analyze (tree-sitter base, one fix covering ~30 language analyzers), 4 linkers (`ipc`, `otp`, `phoenix_ipc`, `go_cobra`), and 13 per-language analyzers (`py`, `csharp`, `kotlin`, `java`, `php`, `ruby`, `go`, `c`, `html`, `js_ts` × 3 file-kind loops, plus `cmake` / `css` / `dockerfile` / `json_config` / `make` / `r_lang` / `toml_config` / `xml_config`). `run_all_analyzers` post-drain pass normalizes absolute paths the same way it does for `Symbol.path` and `UsageContext.path`. Reproduction (Python SyntaxError + UnicodeDecodeError) and the tree-sitter base OSError path are now covered by tests asserting the dropped file appears in `limits.failed_files`.

### Changed

#### Concept-axis migrations — endpoint_shape closure (ADR-0027 / ADR-0028)

- **`SCHEMA_VERSION` 0.6.0 → 0.7.0 — `Edge.evidence_type` endpoint_shape closure.** All 111 endpoint_shape values are removed from `EVIDENCE_TYPES` after the producer-side migration completed and per-cluster bakeoff validation cleared:
  - 18 resolution-status leaks (`*_unresolved`) → canonical inference label + `Edge.is_resolved=False`.
  - 65 framework-dispatch values → canonical inference + `meta["framework_dispatch"]` / `meta["detection_pattern"]`.
  - 28 call-construct peers → apex `ast_call` + `meta["call_construct"]` / `meta["receiver"]` / `meta["resolution_quality"]`.

  `AXIS_ENDPOINT_SHAPE` is retained as a back-compat import target (no longer in `VALID_AXES`); `generate-concept-axes` drops the now-empty section. 91 audit-findings rows advance PRELIM_RESOLVED → RESOLVED.

- **`SCHEMA_VERSION` 0.5.8 → 0.6.0 — `Symbol.kind` endpoint_shape closure.** All 71 endpoint_shape values are removed from `SYMBOL_KINDS` after the producer-side migration completed:
  - 4 apex/peer (`fn` / `proc` / `structure` / `var`) → canonical (audit 0009).
  - 29 framework roles → canonical kind + `meta["framework_role"]` (audit 0013).
  - 9 edge labels — 5 DEPRECATE-NO-FOLD + 4 (`function_call` / `subprocess_call` / `db_query` / `abi_call`) → `call_site` + `meta["call_kind"]` (audit 0010).
  - 1 component_ref DEPRECATE-NO-FOLD (audit 0011).
  - 11 file-shape → `file` / `package` + meta keys (audit 0005).
  - 9 build/config → `test` / `requirement` / `dependency` / `task` / `block` + meta keys (audit 0006).
  - 8 long-tail DEPRECATE-NO-FOLD (audit 0007).

  Consumer-side cleanup: `selection/filters.py` adds `EXCLUDED_FRAMEWORK_ROLES` and trims `EXCLUDED_KINDS`; `linkers/registry.py::SYNTHETIC_KINDS` → `SYNTHETIC_FRAMEWORK_ROLES`; containment / dependency / noise / sketch sets purged. 72 audit-findings rows advance PRELIM_RESOLVED → RESOLVED.

- **CUDA / Android XML canonical-kind producer folds.** Two producer-side fixes against registry-absent `Symbol.kind` values surfaced by the ADR-0027 audit; both replace ad-hoc kind strings with canonical kind + `meta` discriminator. CUDA now emits `kind="function"` + `meta["cuda_execution_space"]` (`"global"` / `"device"` / `"host_device"` / `"host"`); `language-conventions.yaml`'s CUDA rules migrate to the new shape via `Pattern.meta_match`. Android XML now emits `kind="component"` + `meta["component_type"]` for `<activity>` / `<service>` / `<receiver>` / `<provider>`; routing Android `<provider>` through `meta["component_type"]="provider"` keeps the canonical `provider` kind (Apex/Salesforce `[Provider]`) disjoint from Android content providers.

#### Disambiguation-fallback discipline across linkers + fallback-coherence linter

Thirteen linker slices adopt the `confidence ≤ 0.5` + `meta["disambiguation_fallback"]=True` contract when a simple-name resolution lands on a structurally ambiguous target, extending the inheritance-linker pattern from v4.1.0. A new linter pins the contract statically.

- **Fallback-coherence linter (`hypergumbo_core.fallback_coherence`, `scripts/check-fallback-coherence`).** AST-walks every `Edge.create(...)` / `Edge(...)` site under `linkers/` and requires any site declaring `meta["disambiguation_fallback"]=True` (inline dict, IfExp with flag-bearing True branch, or single function-local Name assignment) to also bind `confidence ≤ 0.5` on the same call. Predicate-aware for IfExp ternaries on the same predicate. The companion `disambiguation_fallback` spec lives in `axis_meta_keys.AXIS_EDGE_META`. Wired into `.githooks/pre-commit` whenever a `linkers/*.py` file is staged.

Per-linker adoption groups along the structural shape of the collision:

- **Dispatch with FQN-prefix precision** — `django_orm_dispatch` and `airflow_framework_dispatch` adopt two new shared helpers at `linkers/_transitive_bases` (`build_short_name_collisions`, `short_name_fallback`); `jackson_dispatch` stays precision on `org.springframework.` / `jakarta.persistence.` / `javax.persistence.` qualifiers but drops unqualified bean-marker collisions; `kafka_streams_dispatch` keeps `org.apache.kafka.*`-qualified `KAFKA_STREAMS_CALLBACKS` entries at precision; `grpc.py` adds `meta["proto_package"]` and drops multi-candidate bridge `dispatches_to`.
- **Multi-value lookup conversions** — `subprocess_cli`, `route_handler`, `method_call_recovery` (line-proximity tiebreaker), simple FFI bridges (`cgo` / `napi` / `lua_ffi` / `ruby_ffi`), JNI (short + FQ dual-key), and PyFFI (ctypes/cffi + PyO3) switch from single-value `dict[str, Symbol]` to multi-value lookups; cross-file or cross-impl short-name collisions emit deterministic-by-id with the flag.
- **Emit-all-matches** — Go `interface_dispatch`, `go_cobra`, `annotation_convention` (`@hg:dispatches X`), and `di_resolution._create_di_edges` (Java/Kotlin/Scala interface→impl overloads) drop the whole candidate batch on multi-match.
- **`references` family** — `orm`, `database_query`, `di_resolution._create_di_registers_edges`, and `react_component` switch to multi-value with the same-file-preferred / deterministic-by-id cascade established for `inheritance` in PR #3545.

#### Producer-coherence linter — ternary, f-string, and Constant sentinel handling

Four coordinated extensions to the v4.1.0 producer-coherence linter: inline ternary at the kwarg site now resolves via `_resolve_simple_rhs` (surfaced two pre-existing leaks, `ipc_channel_match` and `topic_match`); non-string Constant assignments return an empty frozenset instead of poisoning the walker; a new `fstring_mode: "advisory" | "expand" | "strict"` parameter resolves each `FormattedValue` through the function-local walker and checks the Cartesian product (capped at 32 expansions, catching e.g. `f"ast_{edge_type}"` → `{ast_extends, ast_implements}`); and an opt-in `variable_form_mode` backstop surfaces producers whose `evidence_type` / `kind` comes from a function param, for-loop unpack, function-call return, or dict-subscript-target lookup.

Registry adds 6 AXIS_PENDING values (`ipc_channel_match`, `topic_match`, `qualified_call`, `ast_call_namespace`, `ctypes_stdlib_call`, `cffi_stdlib_call`) and `error_set` on `Symbol.kind` AXIS_LANGUAGE_CONSTRUCT (Zig). `SCHEMA_VERSION` 0.7.0 → 0.7.1 (additive).

#### Results cache key now includes analyzer identity

`_get_repo_state_hash` keyed only by the analyzed tree's state, not by the analyzer, so two hypergumbo installs analyzing the same source tree shared a cache entry — whichever wrote first won. Triggered live by stable+dev coexistence, wheel-pin RCT cross-arm poisoning, and lang-package partial upgrades that don't bump `hypergumbo_core.__version__`. New `analyzer_identity.py` exposes `compute_analyzer_identity_hash()` (16-char hex over `__version__` + per-package content hashes of installed `hypergumbo_*` packages, walked via `importlib.metadata.distributions()` and memoized). Cache path gains an `<analyzer_identity>/` segment. Out of scope: eviction for the larger key space, cross-machine sharing, migration of pre-fix entries.

#### `hypergumbo run`: `--gzip` and `--no-sketch-fan-out` flags for large-repo output

UAT 4.1.0 measured 90-95% gzip-reducibility on raw `hypergumbo run` JSON. `--gzip` writes main + budget-tier outputs as gzipped JSON with byte-identical content under `gunzip`; pairs with `--out foo.json.gz`. `--no-sketch-fan-out` is an explicit named alias for `--budgets none` (wins over `--budgets ...` because the named flag captures more specific user intent). New safety-zone helper `user_out_open_json_dump_gzip`; `--out OUT` help text points at the incantation.

#### `hypergumbo io-boundaries`: hide `external_potential` bucket from default text output

DEEP reflect showed the `external_potential` bucket dominating io-boundaries text output across six repos (kafka 76,015 chains, airflow 28,912 of ~30,000, prometheus ~93% of total). The bucket remains useful as a first-class signal, but its display volume drowns the per-primitive view.

New `--show-external-potential` flag. Default: suppress the bucket from `by-type` and `by-file` text output, replaced with a one-line summary + surface incantation. `--show-external-potential` opts back in; `--boundary external_potential` also bypasses the suppression. JSON output unchanged. F3 Filter 1 (`edge.is_resolved`) is the upstream noise reducer; this is the display-side complement.

#### Language-gate Circom and DEPENDENCY/TOML partial-install warnings on irrelevant repos

Two false-positive stderr warnings on every `hypergumbo run` (surfaced by UAT 4.0.0). `analyze_circom` now gates on `any(find_circom_files(repo_root))` instead of warning whenever `tree-sitter-circom` is unavailable. `partial_install_warnings.py` gains a per-requirement relevance gate (`LINKER_LANGUAGE_REQUIREMENTS`) so the `dependency` linker's TOML diagnostic suppresses on Go / JVM repos lacking TOML manifests — the existing `language_pairs` activation already handles CGO / LUA_FFI / RUBY_FFI.

#### Harden remaining `git rev-parse <ref>` call-sites against unverified-ref stdout contamination

`scripts/release-check`'s earlier fix established the invariant: bare `git rev-parse <ref>` echoes the input to stdout when the ref is unresolvable, contaminating command-substitution captures. Ten other sites in `scripts/auto-pr`, `scripts/lib/forgejo-api.sh`, and `scripts/ci-failover` adopt the `--verify <ref>^{commit} 2>/dev/null || <fallback>` form. None currently bite (refs are effectively always resolvable in those flows), but the pattern is hardened consistently so it isn't copy-pasted forward.

#### `hypergumbo run --out` help text surfaces side-output files at point of first contact

`--out analysis.json` writes compact-tier previews (`<stem>.{4k,16k,64k}.json`) and a `<stem>.slices/` directory of per-route handler slices in addition to the main file. The help line and run-command epilog now list the side-output paths up front, point at the existing suppression flags, and offer a `--budgets none --no-handler-slices` example producing exactly one output file. No behavior change.

#### `remove-extras` now actually uninstalls source-built grammars

`_uninstall_grammars` previously returned True without doing anything. The three grammars (`tree-sitter-lean` / `tree-sitter-wolfram` / `tree-sitter-circom`) are ordinary pip-installed C-extension packages and `pip uninstall` works on them like any other package. The function now mirrors `_uninstall_embeddings_impl` and pip-uninstalls all three; re-adding requires git + a C compiler again, same as the initial `add-extras` install.

#### HTTP route detection — bare-Node + Apollo standalone

Routes.txt reported "No API routes found" on apollo-server-style deployments because the route-handler linker had no YAML pattern for raw-stdlib HTTP servers or Apollo's standalone server. New `frameworks/node-http.yaml` (always-loaded) captures `http.createServer` / `https.createServer` / `http2.createServer` and bare destructured `createServer(...)`; new patterns in `frameworks/graphql.yaml` cover `startStandaloneServer`, `runHttpQuery`, and `executeHTTPGraphQLRequest`. Both produce `framework_role='route'` on the calling function.

#### gRPC cross-language coverage — TS client → proto fallback

Cross-codebase setups (TS clients calling a gRPC server in a separate-language repo) lost the entire client side of the graph: the linker emitted `calls` only when both stub and servicer were detected in-tree. Unmatched TS/JS stubs now bind directly to the proto service Symbol via `calls` + `meta['protocol']='grpc'` + `Edge.is_resolved=False`. Confidence 0.6 (0.5 with disambiguation flag when two `.proto` files declare the same short-name service across packages).

#### Receiver-type inference — Kotlin / C#

Extends the ADR-0006 Return-Type Registry program to Kotlin and C# (Java + Go shipped 2026-04-09). Kotlin strips the nullable `?` suffix so methods returning `User?` propagate `User` into `var_types` (was dropped, breaking chained-receiver resolution on every nullable getter). C# unwraps the async wrapper types `Task<T>` / `ValueTask<T>` / `IAsyncEnumerable<T>` to the inner `T` so `var x = await SomeAsync()` binds to the awaited type; bare wrapper-only returns stay None.

#### FFI bridge coverage — N-API templates + PyO3

- **N-API template forms.** The N-API linker only matched function-argument forms of `Napi::Function::New` / `InstanceMethod` / `StaticMethod`. Adds the template-argument forms (`Napi::Function::New<F>(env)`, `InstanceMethod<&C::M>("name")`, etc.) and `InstanceAccessor` property bindings used by modern node-addon-api projects (sharp, canvas). `better-sqlite3` uses the legacy V8 direct API (`v8::FunctionTemplate::New`) and would need a separate linker.
- **PyO3 — `#[pymethods] impl` propagation + path-qualified spellings.** Two coordinated fixes re-enable Python→Rust FFI chain tracing on canonical PyO3 crates (Robyn measured 4/~100 methods detected pre-fix). `rust.py` propagates annotations from the enclosing `impl` block to every method declared inside (`#[pymethods] impl Foo { fn bar() {} }` now puts `pymethods` into `bar`'s `meta["annotations"]`); `pyffi.py`'s `_find_pyo3_symbols` recognises path-qualified marker spellings like `#[pyo3::pyfunction]` by comparing the terminal `::` segment against `_PYO3_ANNOTATIONS`.

#### Framework pattern matching — `Pattern.meta_match`

ADR-0027 folded several legacy `Symbol.kind` values onto canonical kinds + per-axis `Symbol.meta` keys (e.g. `devDependency` → `dependency` + `meta["dependency_scope"]="dev"`). YAML rules in `frameworks/config-conventions.yaml` still hardcoded the pre-fold bare-kind regexes and silently matched nothing. New `Pattern.meta_match: dict[str, str]` field re-binds rules to the new emission shape — each key must be present on `Symbol.meta` and its `str()`-coerced value must match the regex; multiple meta_match keys AND together and can combine with `symbol_kind` / `framework_role`. 11 rules migrated across `config-conventions.yaml` and `language-conventions.yaml`; `toml_config.py` now emits `meta["dependency_scope"]` for cargo `[dev-dependencies]` / `[build-dependencies]` tables.

#### IO boundary catalogs — stdio→logging reclassification for non-Python catalogs

Same threat-model logic as the earlier Python `sys.{stdout,stderr}` migration: stdio terminal writes are logging, not IPC. Migrated across `c.yaml`, `rust.yaml`, `javascript.yaml` (`process.send` stays in `ipc_send` as real fork-channel IPC), and `elixir.yaml` (`IO.{puts,write,binwrite}` — device writes, not path writes). `*.stdin` stays in `ipc_recv` (can carry untrusted piped input). Cuts ipc_send false positives on non-Python codebases.

#### Ansible `include_tasks` / `import_tasks` Jinja-templated fan-out

Two shapes are now recognised instead of emitting a single unresolved `dst='{{ ... }}'` edge: basename-Jinja (`include_tasks: "{{ ansible_os_family }}.yml"`) fans out to sibling `.yml` files whose basename matches the literal portion; path-prefix-Jinja (`import_tasks: "{{ tasks_path }}/yumrepos.yml"`) fans out to repo files matching the basename literal. Each fan-out edge carries `confidence=0.30`. On fedora-infra/ansible, 191/192 unresolved `imports` now fan out to real targets.

### Added

- **Corpus-driven schema-coverage gate ratchet (WI-luzuh; foundation phase).** Self-analysis dogfooding measured (lab journal `notebookjournal_05182026_1511.md`) that the existing INV-piroh `scripts/check-schema-conformance` gate — which runs `hypergumbo run .` against the hypergumbo repo (~3.5 min wall time) — silently exercises only ~20 % of the canonical registries: 15 of 139 `Symbol.kind` values (10.8 %), 15 of 54 `Edge.type` (27.8 %), and 24 of 123 `Edge.meta.evidence_type` (19.5 %). Drift in the remaining ~80 % — every Rust / Go / Solidity / Swift / Verilog-specific kind and every framework-pattern linker's evidence — is invisible to the gate because hypergumbo is pure Python and never produces those values when analysing itself. New `scripts/check-schema-coverage` runs hypergumbo against a curated fixture corpus at `tests/fixtures/schema-coverage-corpus/` (10 fixture files across Rust / Go / Solidity / Swift / C# / Java / SQL / Bash / WGSL / TypeScript, ~5 s wall time on the self-hosted runner) and asserts that every canonical registry value appears in the corpus's analysis output, modulo a baseline of currently-uncovered values tracked at `.ci/schema-coverage-baseline.json`. The baseline can only shrink over time (the ratchet): each PR that adds a fixture file triggering a previously-uncovered value runs `--update-baseline` to drop that value from the file; subsequent PRs that delete the fixture loud-fail in `--mode=warning` with a positive-delta error pointing to the regressed values. Mode promotes to `--mode=fail` once the baseline drains to `[]`. Modelled on the existing `scripts/check-schema-conformance` baseline-tolerance pattern (per-PR delta counter, `--update-baseline` flag, exit 2 for environment errors that never block commits). Wired into the `verify-generated` CI job as a sibling step to the INV-piroh schema-conformance gate; corpus runs use `--no-sketch-fan-out` so the gate is deterministic across machines (no HF Hub fallback noise). +6 subprocess-based property tests in `tests/test_check_schema_coverage.py` covering the four cells of the {baseline matches / baseline says less / baseline says more} × {warning / fail} truth table plus missing-input env-error. As a side finding, the corpus's first run surfaced a real axis-drift: the Solidity analyzer (`solidity.py:302`) emits `Symbol.kind="modifier"` but `modifier` is not in the `SYMBOL_KINDS` registry — invisible to self-analysis because hypergumbo has no Solidity. Noted in the lab journal; out of scope for this PR (separate axis-conformance work).

### Fixed

- **Schema conformance + coverage gates folded into one ~5 s CI step (INV-piroh retirement; WI-luzuh consolidation).** The original INV-piroh `scripts/check-schema-conformance` gate ran `hypergumbo run .` against hypergumbo's own ~275-module multi-package codebase on every PR — 3.5 minutes wall-clock to validate JSON output against `docs/schema.json` via `Draft202012Validator.iter_errors`. WI-luzuh shipped the corpus-driven coverage gate as a *sibling* step (~5 s on the 10-fixture corpus) but did not retire INV-piroh's slow conformance pass. This change folds both into the corpus gate: `scripts/check-schema-coverage` now runs `jsonschema.Draft202012Validator.iter_errors` against the corpus output JSON in addition to its existing coverage delta. The baseline file gains a `schema_conformance_violations: list[str]` field carrying the same `<validator>::<path-without-array-indices>` fingerprint shape the retired gate used; mode semantics match — `--mode=warning` fails on positive violation delta, `--mode=fail` rejects any violation regardless of baseline, `--update-baseline` rewrites the conformance section. The corpus produces 0 conformance violations on first measurement, so the baseline ships with `schema_conformance_violations: []` — any future producer regression that introduces a violation loud-fails immediately. Per-PR CI wall-clock on this step drops from ~3.5 min to ~5 s; the existing `--no-sketch-fan-out` flag keeps the corpus run deterministic across machines. `scripts/check-schema-conformance` and `.ci/schema-conformance-baseline.json` retire. +6 property tests in `TestSchemaConformanceFold` pin: clean-output passes, new violation fails, baseline-tolerated violation passes, fail-mode rejects baseline, `--update-baseline` records violations, and newly-resolved violations report. The user's standing dogfooding practice covers what self-analysis catch-out used to provide; no nightly relocation needed.

- **HTTP linker emits `kind="call_site"` instead of `kind="function"` for client call sites (INV-mopif; audit-findings 0013 amendment).** Self-analysis surfaced 18 nodes with `kind="function" name="GET <url>"` — one production case (`service-worker.js:54` `fetch(event.request)` → `name="GET event.request"`) plus 17 test fixtures — that `hypergumbo dead-code-maybe` flagged as dead functions because nothing in source calls a Symbol whose name is `"GET event.request"`. Root cause: an internal inconsistency in the ADR-0027 Wave 5 audit-findings. Audit-findings 0010 sub-case (a) had added `call_site` to `AXIS_LANGUAGE_CONSTRUCT` (`symbol_kinds.py:156`) precisely for the "call expression as syntactic construct" shape and migrated `abi_call` / `function_call` / `subprocess_call` / `db_query` onto it (sister linkers `database_query.py` / `subprocess_cli.py` / `solidity_abi.py` now emit `kind="call_site"` + `meta["call_kind"]=<specialisation>`). But sister audit-findings 0013, filed at the same Wave 5, picked `fold_target: function` for `http_client` — even though its own rationale describes the producer as a *"HTTP client call site"*. Fix: amend audit-findings 0013 row to `fold_target: call_site` and migrate the producer at `linkers/http.py:1319-1347`. `_create_client_symbol` now emits `kind="call_site"` + `meta["call_kind"]="http"` (audit-0010 precedent, names the syntactic specialisation) + `meta["framework_role"]="http_client"` (audit-0013 convention preserved; framework-participation residue). The two meta keys carry orthogonal information. Existing `meta` keys (`http_method`, `url_path`, `raw_url`, `url_type`) untouched; `stable_id` still computed via `make_route_stable_id`. +5 property tests in `tests/test_http_linker.py::TestINVMopifCallSiteFold` pin: `kind="call_site"` at unit and end-to-end layers, `meta["call_kind"]="http"`, `meta["framework_role"]="http_client"` preservation, and the four `http_method` / `url_path` / `raw_url` / `url_type` keys unchanged. Existing `test_creates_client_symbols` updated to the new fold. Quantitative claim: 18 → 0 misclassified `kind="function"` HTTP call-site Symbols on self-analysis; the production `service-worker.js:54` case stops triggering dead-code false-positives.

- **WebSocket linker no longer creates phantom `kind="file"` Symbol nodes (INV-ronuf).** Self-analysis on 2026-05-16 surfaced three paths (`test_websocket.py`, `serve.py`, `ws-client.ts`) carrying two coexisting `kind="file"` Symbols — one from the language analyzer / orchestrator-side synthesizer, one phantom shadow from `linkers/websocket.py:551-562`. The shadow node carried three bugs from a single line: a `websocket:`-prefixed id that never collided with the canonical `{language}:{path}:1-1:file:file` shape (so `synthesize_file_symbols_for_dangling_edges` couldn't dedup it), `language="javascript"` for `.ts` files (the now-removed nested `get_language(pattern_type)` helper was extension-blind), and `stable_id=None` (schema gate violation under INV-piroh). Fix: `_make_file_id` now delegates to canonical `make_file_id(language, path)`; a new `_language_for_file(file_path, pattern_type)` resolves language via `_text_filters.language_from_path` first and falls back to the `pattern_type`-based default only when the extension is unknown; synthesized Symbols stamp `stable_id = make_file_stable_id(language, path)`. `link_websocket()` accepts an `existing_symbol_ids: set[str] | None` kwarg (legacy single-arg form preserved for unit tests) and the registry-based `websocket_linker(ctx)` threads `{s.id for s in ctx.symbols}` through, so when an analyzer or the orchestrator dangling-synth has already minted a canonical-shape file Symbol the WS linker skips synthesis entirely — edges still target the canonical id (no dangling). +5 property tests in `tests/test_websocket.py::TestINVRonufNoPhantomFileSymbols` pin: canonical id shape; `.ts` → `language="typescript"`; synthesized symbols carry non-`None` `stable_id`; dedup against pre-existing canonical ids; dedup'd edges still resolve to the existing Symbol.

- **`SymbolByName` helper retires the `dict[str, Symbol]` silent-overwrite anti-pattern (WI-sofaf; closes INV-paroh).** The Verilog analyzer's `register_symbol` (`verilog.py:209-211`) stored each Symbol keyed by its lowercased name in a single-value dict; two same-named symbols of different kinds (e.g. `module Foo` and `interface Foo`) collapsed to whichever was inserted last, and `extract_edges_from_file`'s instantiation lookup at `verilog.py:242-243` then resolved module-instantiation edges to whichever survived — wrong when the interface won. Same anti-pattern had fired three times in successive analyzers (Rust WI-milak, VHDL WI-morud, Verilog INV-paroh) plus five cross-linker audit-cohort instances; WI-morud's escalation rule fired on the third occurrence, mandating a shared helper instead of another per-linker fix. New `hypergumbo_core.symbol_indexes.SymbolByName` (Path B-thin per the INV-fosab decision) ships ~70 LOC: multi-value `add` (never overwrites), kind-aware `lookup(name, *, prefer_kind=)` with insertion-order stable tiebreaker, `lookup_one(name) -> tuple[Symbol, bool] | None` returning `is_fallback=True` when raw candidate count exceeds 1 so callers can apply the INV-zuhub contract (`confidence ≤ 0.5` + `meta["disambiguation_fallback"]=True`), and optional `case_insensitive=True` for Verilog's spec-level case-folding. Verilog migration: `register_symbol` now stores `global_symbols.setdefault(key, []).append(sym)` (multi-value), `extract_edges_from_file` builds a `SymbolByName(case_insensitive=True)` and uses `lookup_one(module_type, prefer_kind="module")` for module-instantiation resolution — when `is_fallback`, the edge carries `confidence=0.50` + `meta["disambiguation_fallback"]=True`, otherwise `confidence=0.90`. Out of scope by design (documented in the helper's module docstring): `route_handler.py`'s `_RailsIndex` (compound `(action, class_lower)` keys), `grpc.py`'s `servicer_by_name` + `proto_service_by_name` (dual-shape registry) — stay bespoke; a `Path B-elaborate` could subsume them later. +22 property tests in `tests/test_symbol_indexes.py` covering insertion / lookup / prefer_kind cascade / fallback signalling / case-insensitivity / Verilog-scenario end-to-end. INV-paroh's acceptance scenario (module + same-named interface; instantiation must resolve to module regardless of insert order) is pinned by `TestVerilogScenario`.

- **Eight previously-`None` `Symbol.kind` values now carry a `stable_id` (INV-sotiv).** Self-analysis surfaced 1,981 of 32,253 Symbols (6.1%) with `stable_id=None`, all concentrated in eight kinds: 100% of `kind="variable"` (1,487), `kind="module"` (385), `kind="dependency"` (81), `kind="export"` (18), `kind="project"` (7), `kind="interface"` (2), `kind="type"` (1), and 99.4% of `kind="file"` (804 of 809). Root cause: the Python AST analyzer's module-level constant emit path (`py.py:225-287`) bypasses `_compute_stable_id`, the orchestrator file-symbol synthesizer (`base.py:325-440`) and the tree-sitter analyzer's optional `create_file_symbols` path (`base.py:2170-2181`) never compute one, and ~12 analyzers emitting module / dependency / export / interface / type Symbols (`xml_config`, `toml_config`, `bash`, `csharp`, `groovy`, `wasm_bindgen`, etc.) leave the field at the dataclass default. Fix: new family of kind-specific factories in `analyze.base` modelled on `make_route_stable_id` / `make_entry_stable_id` — `make_{file,module,dependency,variable,export,project,interface,type}_stable_id` — plus a `populate_kind_stable_ids(symbols)` backstop pass that runs in `analyze.all_analyzers` after path normalisation. The backstop walks every Symbol and, for any Symbol with `stable_id=None` and a kind in the factory table, stamps the kind-specific value; producers that already computed a `stable_id` (functions / methods / classes via `_compute_stable_id`, routes via `make_route_stable_id`, typed-tier via `make_typed_stable_id`) keep precedence — the backstop never overrides a non-`None` value. Each formula expresses the cheapest identity for the kind: files are path-identified, modules / dependencies / interfaces / types are lang-namespaced-name-identified, variables / exports are file-scoped `(path, name)`-identified, projects are bare-name-identified; the language namespace separates same-name cross-language symbols (`io` in Python vs Dart, `requests` in Python vs npm). Additive only — `STABLE_ID_SCHEME` stays at `v3` (the §5a class-body-signature bump); no existing `stable_id` value changes. ADR-0014 §5b + Phase 0++ document the factories and backstop. +18 property tests in `tests/test_kind_stable_id_coverage.py` pin: every kind in the eight-kind set gets a non-`None`, `sha256:`-prefixed stable_id on real analysis; same-name-different-file variables get distinct stable_ids; the backstop is idempotent (double-run produces the same values); the backstop does not clobber producer-computed stable_ids; each kind dispatches to a distinct formula (so a same-name `(variable, export)` pair gets distinct stable_ids). Quantitative claim (BVT queue): 6.1% missing-stable_id rate → 0% for the eight covered kinds.

- **Python `stable_id` no longer collapses distinct same-module classes onto one hash (INV-fusus).** Self-analysis on 2026-05-16 measured a 91% collision rate on hypergumbo's own codebase: 26,742 of 29,367 `stable_id`-bearing nodes were in collision groups, with five `@dataclass` classes in `ir.py` (`Symbol`, `Edge`, `Span`, `AnalysisRun`, `ExternalRef`) all sharing one `stable_id` and four `to_dict` methods cascading into a second collision via the colliding containing-class identity. Root cause: `_compute_stable_id` at `py.py:1526` hashed only `kind:param_count:arity_flags:decorators:containing_stable_id`, and for two `@dataclass` classes in the same module *all five inputs were identical*. Fix: new `_extract_class_body_sig(node)` helper folds a body-shape signature — sorted method names + sorted field names (`AnnAssign` / `Assign` Name targets) + sorted base names — into the hash for `ClassDef` nodes only. Both halves of the `Symbol.stable_id` docstring promise are preserved: the class's own name is not in the body sig (`survives renames`), and no line numbers / paths / column offsets appear (`survives moves`). Two classes with byte-for-byte identical bodies still produce the same hash — that's *semantic identity*, not an artifact, and consumers wanting absolute uniqueness should join on `(stable_id, canonical_name)` per the Symbol docstring contract. Secondary fix in the same change: `isinstance(node, ast.FunctionDef)` in `_compute_stable_id` broadens to `isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))` so async functions get correct `param_count` and `arity_flags` instead of falling through the class branch with `param_count=0, arity_flags="False,False,False"` — a pre-existing structural bug that the v3 bump covers. `STABLE_ID_SCHEME` bumps `hypergumbo-stableid-v2` → `hypergumbo-stableid-v3`; all Python class and method `stable_id` values change as a result. New `tests/test_stable_id_class_collisions.py` (10 property tests, four classes) pins: distinct dataclasses get distinct stable_ids (by methods, fields, or bases); five distinct dataclasses produce five distinct stable_ids; `to_dict` cascade and `__init__` cascade resolve correctly; class rename preserves stable_id; method-order is irrelevant; byte-identical classes still collide by design; and async vs sync function-with-args don't collide via the class-branch bug path. ADR-0014 §5a + Phase 0+ document the addition; out of scope (deferred to typed tier in §3): same-shape top-level functions in the same module still collide.

- **`scripts/auto-pr` accepts `--title TEXT` and `--description TEXT` flags in addition to positional args.** Agents that invoked the script with the more conventional flag form (e.g. `./scripts/auto-pr --title "fix: X" --description "Closes ..."`) silently ended up with PRs whose Forgejo title was the literal string `--title`, because the global flag parser had no `--title)` / `--description)` cases — the unknown flags fell through the `*)` `break` and became positional `$1="--title"`, `$2="fix: X"`. The intended title moved one slot right and became the description; the actual description was dropped. Audit of recent PRs found this hit at least 9 PRs across three different days (2026-05-13, 2026-05-14, 2026-05-18) — fast-forward merges hid the symptom because the merge commit uses the commit's own message, but the Forgejo PR page rendered the malformed title and body. Fix: parser at `scripts/auto-pr:1727-1799` now handles both flags with the same require-value-or-fail discipline as `--tracker-id`. Resolution order: flag form wins per slot; any slot the flag skipped is filled by the next remaining positional left-to-right (so `--title X "Y"` binds title from flag and `Y` becomes the description); any slot the positionals didn't reach falls back to the commit subject / body. +12 tests in `test_autopr_title_desc_flags.py` cover flag form, positional BC, mixed form, missing-value errors, and coexistence with `--tracker-id` / subcommands.

- **`orchestrator_file_symbol_synthesis` no longer stamps absolute paths into `Symbol.name` or hardcodes `span=1-1` (INV-vaguj; closes INV-dihif).** Self-analysis surfaced 798/809 file-kind Python Symbols with absolute paths in `name` (while `path` was correctly relative on the same node) and 800/809 with hardcoded `span={start_line:1, end_line:1}` regardless of the file's actual extent. Root cause: `analyze/base.py:325-386` (the WI-ramuv dangling-edge synthesiser) reconstructed `path` from the endpoint id without normalising it against `repo_root`, then stamped that into both `name` and `path`; the downstream normalisation loop in `analyze/all_analyzers.py:202-206` fixed only `Symbol.path`, never `Symbol.name`. Fix: the synthesiser takes a `repo_root` argument. Paths under `repo_root` strip to repo-relative form for both `name` and `path`. When the file is readable, `span.end_line` becomes the actual line count (a 3179-line `cli.py` ships with `span.end_line=3179`); unreadable files keep `end_line=1` (a schema-valid value — the tracker's originally-proposed `-1` sentinel would violate the INV-piroh gate's `Span.end_line minimum=0` constraint). Closes INV-dihif as a downstream consequence — the explain formatter rendered `Symbol.name` for "module level" caller entries, which is the user-visible absolute-path leak INV-dihif reported. New property test in `test_run_behavior_map.py` asserts no Symbol in self-analysis output has an absolute-path `name`; +5 unit tests in `test_base.py` cover the absolute-path normalisation, line-count stamping, unreadable-file fallback, legacy `repo_root=None` BC path, and the Dart-style synthetic `dart:io` path that must pass through unchanged.

- **Every Symbol-producing linker now stamps `Symbol.origin` and `Symbol.origin_run_id` (INV-sopon).** Self-analysis surfaced 95 Symbol nodes with empty `origin` traceable to linker synthesis. The invariant ("every Symbol emitted by an analyzer or linker MUST carry a non-empty `origin` matching the producer's versioned `PASS_ID`, plus a non-empty `origin_run_id` matching the run's `execution_id`") was honored by `message_queue` and `database_query` but violated by 12 sibling linkers: `graphql`, `http`, `openapi`, `solidity_abi`, `subprocess_cli` missed both fields on synthesised symbols; `annotation_convention`, `crypto_flow`, `message_dispatch`, `tauri_ipc`, `_view_template_core`, `wasm_bindgen`, `yjs_crdt` stamped `origin` at construction but never `origin_run_id`. Each Symbol construction site now stamps both fields. For `openapi` and `_view_template_core` the `AnalysisRun.create()` call moves up before the symbol-construction loop so the `execution_id` is available at construction. (The INV-sopon tracker originally listed 19 affected files; AST inspection of the live source showed six were already correct — `ipc`, `phoenix_ipc`, `vue_component`, `swift_objc`, `websocket`, `js_module`, `grpc` — so the real fix scope was 12.) New `tests/test_linker_origin_invariant.py` enforces the invariant two ways: a structural AST scan asserts every Symbol-producing linker module contains the stamp idiom (catches future regressions when a 22nd linker lands), plus three runtime tests (HTTP, subprocess, IPC) exercise representative linkers end-to-end against minimal fixtures and confirm the stamp lands on values consumers see. Downstream consequence: mixed-fidelity analysis can finally filter or weight linker-synthesised symbols by producer trust, and the "synthesised-by-linker" vs "synthesised-as-boundary" empty-origin populations are now distinguishable.

- **Self-analysis behavior map now validates against `docs/schema.json` (INV-piroh).** Hypergumbo's own output was failing the documented public contract on two distinct shapes: (a) `linkers/js_module.py:811` hardcoded `line=0` on 26 `module_exports` edges, violating `Edge.line minimum=1`; (b) three top-level keys the CLI produced (`entrypoints`, `usage_contexts`, `sketch_precomputed`) weren't declared in `BehaviorMap.properties`, so any consumer validating against the documented schema would fail on hypergumbo's own JSON. Fix: (a) `module_exports` edges now use `file_sym.span.start_line` — the exported symbol's actual definition line — as a meaningful anchor; the previous line=0 collisions were also incorrectly deduplicating distinct exports into one edge, so the self-analysis edge count rises by 12 as a side-effect correctness gain. (b) `Entrypoint` and `UsageContext` definitions added to `$defs`, and the three keys declared in `BehaviorMap.properties` (sketch_precomputed sub-fields are nullable for repos without a README/license/centrality signal). Schema version bumps `0.8.0 → 0.9.0` (additive). New `scripts/check-schema-conformance` ships as a CI gate in **warning-only mode** with a per-PR delta counter — tolerates the baseline tracked in `.ci/schema-conformance-baseline.json` (currently 0) but loud-fails on any positive delta so silent regressions can't accumulate during the foundation/Wave-2 window. Promotes to `--mode=fail` in Wave 3 once the remaining INV-rugok members close. Wired into the existing `verify-generated` CI job.

- **`AnalysisRun.repo_fingerprint` now carries the spec-defined hash on every run (INV-tofur).** Self-analysis showed `repo_fingerprint=None` on 100% of 133 AnalysisRuns: the field was declared in `ir.py`, declared in `docs/schema.json:935`, and fully specified at `docs/hypergumbo-spec.md:378-384`, but no producer in the pipeline ever computed it. New `repo_fingerprint.py` module implements the spec algorithm — git branch hashes `git_head + sorted([(path, sha256(content_bytes)) for each dirty file])`, non-git branch hashes `sorted([(path, sha256(content_bytes)) for each source file])`. Stamped post-collection in `cli.command_run` so all 83 `AnalysisRun.create` call sites (analyzers + linkers) inherit the value without per-site wiring. As a side effect, `sketch_embeddings._get_repo_state_hash` migrates to delegate to the same shared function, retiring the path:size:mtime cache-shard key that was the mechanism behind INV-magul's tracker-`.ops` mtime pollution — content hashing ignores mtime jitter. Existing results-cache entries become unreachable on the algorithm switch (one-time miss; absorbed naturally). +15 tests covering determinism, content-vs-mtime sensitivity, git/non-git divergence, untracked-file inclusion, and end-to-end `repo_fingerprint` stamping in the behavior-map output.

- **`scripts/prepare-release` Step 7 no longer swallows push failures.** The dev→main push was suffixed with `2>&1) || true`, masking every failure mode behind a misleading success banner. Now wraps the push in `if !; then exit 1; fi`; +1 regression test asserts the trailer contains no `|| true`.

- **JS/TS `access_mode` annotation coverage on call edges.** YAMLs declared only three assignment-node shapes and one deletion shape; calls inside `return` / `throw` / `yield` / `await` stayed unclassified, leaving `--dataflow` slices useless on TypeScript repos (DEEP cohort 1: 0/61 NestJS and 0/80 Apollo sampled call edges classified). Adds positional rules for those four contexts, an `update_expression` assignment rule, and expanded `library_patterns:` for in-place mutators, ORM/persistence verbs, RxJS / EventEmitter / pub-sub, Promise / Observable readers, and iteration / collection readers.

- **`is_utility_file` no longer false-positives on `<pkg>/utils/`, `<pkg>/contrib/`, `<pkg>/tools/`.** The ambiguous-name check fired at any depth, flagging `django/utils/html.py`, `requests/utils.py`, etc. Django's `rslice.1.prod.json` had collapsed from 75 non-test edges to 0. `contrib` / `hack` move into `_AMBIGUOUS_UTILITY_DIRS`, and the ambiguous-dir check tightens to `path_parts[0]` only (repo-root depth).

- **Phoenix/Elixir test files classify as `supply_chain.tier=1` with `is_test=True`.** ExUnit test files at `test/<context>/<thing>_test.exs` matched the `(?:^|/)tests?/` dir pattern first and landed at tier=2 (internal_dep), conflating "is test" with "is vendored" — Phoenix's 1328 test files all came out at tier=2. New narrow `TEST_FILE_PATTERNS_DIR_OVERRIDE = [r"_test\.exs$"]` is checked before `TEST_DIR_PATTERNS`; auxiliary files in `test/` keep their dir-pattern routing.

- **`yjs_crdt` linker gates on a real Yjs ecosystem dependency.** The framework text-scan token vocabulary (`.set(`, `.on(`, `.observe(`) overlapped with generic Vue / Rails / Express patterns; chatwoot produced 68 spurious `event_publishes` edges. Now requires at least one `dependency` Symbol in `{yjs, y-protocols, y-websocket}` before scanning.

- **Blade analyzer enrols on Laravel repos.** `FileIndex.match_pattern` indexed `*.blade.php` under `.php` (Path.suffix retains only the last segment), so koel's 9 `.blade.php` files went unanalysed. The pure-extension fast path now requires no `.` in `pattern[2:]`; compound suffixes (`*.blade.php`, `*.d.ts`, `*.lagda.md`) fall through to the general fnmatch branch.

- **`type_hierarchy` dispatches through interface-extends-interface in deny-listed languages.** The per-language gate at `_extends_admits_dispatch` was over-aggressive: in Go (interface embedding) and C# (interface inheritance), `extends` edges between *interfaces* represent virtual dispatch, not concrete inheritance. The gate now takes a `child_kind` parameter and overrides the deny-list when the child is `interface`; concrete-extends suppression for `class` / `struct` is unaffected.

- **`bakeoff-deep-reflect` injects `awaits_bakeoff_validation` claims for items in status `done`.** The BVT playbook applies the tag at PR-merge time, so `done` is the canonical status; the pre-fix filter accepted only `todo_soft` / `in_progress`, leaving the "Active Bakeoff Validation Claims" section empty in every reflect prompt despite a 16-item queue.

- **CI pip-audit pins `urllib3>=2.7.0` for CVE-2026-44431 / CVE-2026-44432.** Both CVEs (published 2026-05-11, fixed in 2.7.0) reach the project transitively through `requests`. Mirrors the existing `requests` / `pygments` pins.

- **`--backend rust-analyzer` install advice mentions `--force` and `pipx inject`.** Three integration-missing messages had pointed users at `pipx install 'hypergumbo[rust-analyzer]'`, but pipx no-ops with `already seems to be installed` when bare `hypergumbo` is on the system. Messages now suggest `--force` or `pipx inject hypergumbo hypergumbo-lang-rust-analyzer`.

- **`yaml_catalogs` registry — `function_summaries` loader attribution corrected.** The `CatalogSpec` named `hypergumbo_core.cli` as loader, but the real loader is `load_function_summaries()` in `hypergumbo_core.function_summaries`. New property test calls `importlib.import_module(spec.loader)` for every `CatalogSpec` and fails on stale or typo'd loader names.

- **`hypergumbo explain Symbol | head` no longer prints a BrokenPipeError traceback.** `main()` now restores the POSIX default SIGPIPE handler at startup; Windows is unaffected (`signal.SIGPIPE` attribute absent).

### Documentation

- **ADR-0022 (Language Profile Registry) — status update.** New "Status Update (2026-05-13)" section records that the by-category drift-detection half landed (`hypergumbo_core.yaml_catalogs` + auto-generated `ARCHITECTURE.md` table) and the by-language `LanguageProfile` half is deferred, with three trigger conditions for revisiting.

- **ADR-0017 (Taint-Zone Dataflow) — implementation note for `taint_sinks/` absorption.** Sinks are now derived from `io_primitives/*.yaml` (every write-side IO primitive becomes an `untrusted` sink in a zone chosen by `AUTO_SINK_ZONE_MAP`); the built-in `taint_sinks/` directory no longer ships. ADR §2b opens with a note clarifying that the schemas still describe **project-local** sink catalogs passed via `--taint-sinks`.

## [5.0.1] - 2026-05-09

### Fixed

- **`--backend rust-analyzer` no longer silently falls through to tree-sitter when the rustup proxy is broken.** Closes a v5.0.0 partial-fix gap. The defensive backstop shipped in v5.0.0 only checked the integration package; `is_rust_analyzer_available()` was existence-only via `shutil.which`. On a machine where `~/.cargo/bin/rust-analyzer` is a rustup proxy whose `rust-analyzer` component has not been installed (`rustup component add rust-analyzer` was never run, or a system-package-manager rustup install put the proxy on PATH ahead of any real install), the existence check passed, the integration check passed, and `--backend rust-analyzer run` produced byte-identical output to `--backend tree-sitter run` (same `run_signature`, same node count, same toolchain, no warning). `is_rust_analyzer_available()` now smoke-tests the binary with `<binary> --version` (5s timeout, exit-code check); a new parse-time guard `_ensure_rust_analyzer_binary_or_exit()` runs alongside the existing integration-package guard, so the `--backend rust-analyzer` path errors clearly with a pointer to `rustup component add rust-analyzer` instead of degrading silently. `add-extras --check` and `install-rust-analyzer --check` inherit the smoke test, so both report `✗ not installed` for the broken-proxy state instead of a misleading green check.

## [5.0.0] - 2026-05-09

### Summary

The Rust SCIP backend is now usable end-to-end: `pipx install 'hypergumbo[rust-analyzer]'` engages it (the integration package now ships to PyPI), and the CLI errors clearly when the integration is missing instead of silently falling through to tree-sitter. The two extras-management umbrellas collapse into one (`add-extras` / `remove-extras`) with `--check` and `--skip` flags. Correctness fixes land for Rust trait resolution (two paths), Go gRPC server-to-RPC mapping when struct names collide across files, VHDL architecture-of-entity lookups, Rails `.csv.erb` templates, Circom grammar building, and partial-install warnings that fired for inactive linkers. `hypergumbo run` no longer drops handler-slice fan-out next to the main result; it co-locates them under `<out-stem>.slices/`. `hypergumbo symbols` gains column-width controls for narrow-stdout hosts like Colab.

### Changed

- **Extras umbrella collapsed to one pair of subcommands.** `add-extras` / `remove-extras` are now the single umbrella over grammars, gitleaks, embeddings, and rust-analyzer; `install-extras` / `uninstall-extras` are removed. `add-extras` gains `--check` (status table; non-zero exit if anything is missing) and `--skip COMPONENT[,...]`; `remove-extras` gains `--skip`. The `--check` rust-analyzer row now reports `✗ not installed` when the rustup binary is present but the integration package is missing (e.g. a system-package-manager rustup install, or a residual binary after uninstalling the `[rust-analyzer]` extra), instead of a misleadingly green status. **Breaking** for anyone scripting against the old names.

### Added

- **`hypergumbo[rust-analyzer]` install extra + `hypergumbo-lang-rust-analyzer` published to PyPI.** v4.1.0 shipped without the SCIP integration package or an opt-in extra, so `--backend rust-analyzer` had no way to engage. After this release, `pipx install 'hypergumbo[rust-analyzer]'` engages the SCIP backend end-to-end (the extra is pinned in lockstep with the meta-package version, and the integration package is added to the release-workflow build loop). As a defensive backstop for minimal installs, `--backend rust-analyzer` and `install-rust-analyzer` now exit non-zero with a clear message when the integration package is missing instead of silently falling through to tree-sitter; `install-rust-analyzer --check` reports binary and integration-package status as separate lines and exits 1 if either is missing.

- **`hypergumbo symbols` column-width controls.** The Symbol and File columns now default to 60 / 80 chars — about twice what Rich auto-fit picked on narrow non-TTY hosts (e.g. Google Colab, where Rich falls back to ~80 cols and squeezes those columns to ~25–30 chars each). Two new flags: `--col-width N` sets both columns to N (clamped to `[1, 1000]`); `--wrap` switches overflow from ellipsis truncation to character-level fold-wrap. Console width auto-extends when requested widths exceed the detected terminal, so narrow hosts get a horizontally-scrolling table rather than collapsed columns.

- **Smart-test slice-fallback diagnostic file.** When `scripts/smart-test`'s reverse-slice path falls back to full-suite (`slice command failed` or `no test files in slice result`), it writes a diagnostic bundle to `.ci/smart-test-fallback.log` (gitignored, overwritten each fallback) recording the fallback reason, hypergumbo path and version, the slice command + exit code + duration, slice stdout summary (first 50 lines + line count) and stderr, and the changed-files list. A one-line pointer prints to stderr when fallback fires. Motivated by a slice → full-suite fallback that cost ~12.5 minutes and could not be reproduced afterwards.

- **UAT directed-validation playbook gains a Mechanism check.** A new optional pre-commitment field captures one or two falsification probes when a claim names a specific mechanism, plus a Mechanism column on the verdict matrix (`matches` / `mismatch` / `n/a`) and a fourth `moved + Mechanism: mismatch` verdict that strips the validation tag (the public-facing claim is satisfied) and files a `needs_human_review` follow-up to reconcile claim text against linker behavior. Surfaced by a UAT round whose quantitative verdict resolved `moved` but whose claim text described a transitive base-class walk that subsequent investigation falsified (the actual mechanism was a filename convention).

### Fixed

- **`hypergumbo build-grammars` now actually builds Circom.** The Python builder iterated `SOURCE_GRAMMARS`, which only listed Lean and Wolfram, so users hitting `"Circom analysis skipped: tree-sitter-circom grammar not available. Run \`hypergumbo build-grammars\` to build it."` would run the suggested command and see the warning persist. (The shell-script CI/dev path had been building Circom all along.) Added `tree_sitter_circom` to `SOURCE_GRAMMARS`.

- **Partial-install warnings now respect linker activation.** The warning pass iterated diagnostics from every registered linker unconditionally, so e.g. a Rust + Python repo with C/C++ symbols got `"CGO linker found 151 C/C++ implementations but 0 Go cgo calls"` even though the CGO linker (Go ↔ C/C++) would not have run on that tree. Each warning now consults its linker's `should_run(detected_frameworks, detected_languages)` predicate and skips when the linker would not have activated. The gate is bypassed when both detection sets are empty (preserves crafted-diagnostic test fixtures); the dependency linker (always-on) is unaffected.

- **`view-template-linker-v1` now recognizes `.csv.erb` templates.** The Rails template probe handled `.html.erb`, `.html.haml`, `.html.slim`, `.text.erb`, `.text.haml`, and `.json.jbuilder`, but missed `.csv.erb`. CSV-export controller actions had view files at the conventional path receive no `renders` edge. Added `.csv.erb` to the recognized template extensions and language map.

- **Rust `impl Trait for Type` requires the LHS to be a trait.** The impl_item handler accepted any symbol with the trait's name. When a project also defined a non-trait symbol with that name (e.g. a marker `struct Clone;` used as a phantom-type tag) alongside a manual `impl Clone for X` referring to `std::clone::Clone`, the lookup bound to the local struct and emitted a spurious high-confidence `X implements struct-Clone` edge. The handler now requires `kind == "trait"`; non-trait matches fall through to the unresolved-trait branch (which correctly suppresses standard-library trait names).

- **Rust `impl Trait for Type` resolves trait/struct short-name collisions across files.** The guard above only catches collisions when the global-symbol-table overwrite leaves the struct as the survivor (kind check then rejects it). When the struct wins the overwrite the canonical trait is gone from the global table entirely, so the handler falls back to an unresolved-trait edge. On a typical ML framework this misresolved ~63% of `impl Module for X` edges depending on registration order. The Rust analyzer now also populates a kind-segregated multi-value index alongside the existing single-value dict; the impl_item lookup prefers `kind == "trait"` candidates, breaks ties by same-file path then stable id, and refuses to fall back to a struct/enum when no trait exists.

- **`hypergumbo run` co-locates handler-slice fan-out under `<out-stem>.slices/`.** `--out /some/path/foo.json` previously deposited 20–30 `slice.handler.*.json` files directly in `/some/path/`, clobbering prior runs when result files shared a parent directory. They now go to a stem-derived sibling directory: `--out /some/path/foo.json` writes the main result at `/some/path/foo.json` and the slices (plus `slice.handler.index.json`) at `/some/path/foo.slices/`. `--no-handler-slices` is unchanged. When `--out` is omitted, slices land in `<cache_dir>/hypergumbo.results.slices/`.

- **`grpc-linker-v1` Go server method-to-RPC mapping is now file-scoped.** The struct-to-service map was keyed by bare struct short-name, so when multiple Go files declared a struct with the same name — e.g. eight plugin packages each declaring `type service struct { ... api.UnimplementedXxxServer ... }` for a different service — the map overwrote on registration order and whichever file iterated last won the mapping for every other plugin's `service.Create` method. On a real-world repo this misresolved seven service families' `implements_rpc` edges onto a single unrelated RPC family. The map is now keyed by `(file_path, struct_name)` (both the `Unimplemented*Server`-embedding scan and the ttrpc / CSI base-class fallback), so each plugin's methods resolve only against its own file's embedding.

- **VHDL `architecture X of Y` now kind-prefers entity over a same-named package / architecture / component.** The global registry indexed entities, architectures, packages, and components together by lowercased name, single-value, last-write-wins, so an IP-block library with both `package Foo` and `entity Foo` could mis-resolve `architecture Bar of Foo` to the package depending on insertion order. The registry is now multi-value; the lookup picks the entity candidate and falls back to a synthetic external-entity ID at confidence 0.70 when no entity matches.

## [4.1.0] - 2026-05-08

### Summary

Two more concept axes — `Symbol.kind` (192 values, ADR-0027) and `Edge.evidence_type` (218 values, ADR-0028) — instantiate the ADR-0024 axis-declaration template and migrate from Draft to Phase 4a. Producer-side folds collapse ~75 framework-dispatch evidence types to canonical inference + `meta["framework_dispatch"]`, ~28 framework-role symbol kinds to `function`/`method` + `meta["framework_role"]`, ~28 call-construct peers to apex `ast_call`, and 18 `*_unresolved` evidence types to canonical + the new sibling field `Edge.is_resolved`. Phase 4a `x-deprecated` annotations ship for both axes; closed-enum return is gated on per-cluster bakeoff validation. ADR-0027 Phase 3 producer migration is empirically complete: every `Symbol.kind` registry value carries a verdict.

Framework-dispatch and inheritance correctness fixes land across nine linker modules: six dispatch linkers and the Go ttrpc / CSI path in `grpc.py` walk transitive base-class chains; `type_hierarchy` emits skip-level overrides; Django generic CBVs resolve `View` lifecycle methods; `jackson_dispatch` recognizes JPA `@Entity` types as REST response bodies; the `inheritance` linker tightens cross-language gating. `bakeoff-deep` no longer inflates reverse-slice seeds with synthetic dispatch edges.

Internal: per-cluster verdict tables in `docs/audits/` grow to 12 entries; the Fundamental Concept Audit playbook gains an indirection-aware producer-trace step; a regression-guard property test now blocks DEPRECATE-NO-FOLD verdict drift at commit time.

### Added

#### Concept-axis declarations

- **Canonical `Symbol.kind` registry** (ADR-0027 Phase 1, `symbol_kinds.py`): 192 entries classified across `language_construct` (Cluster 27A canonicals, ~50 values), `endpoint_shape` (Clusters 27D/27E + `component_ref`, ~40 values folding to canonical + `meta["framework_role"]` or producer-side drop), and `pending_classification` (Clusters 27B/27C/27G/27H, ~100 values awaiting per-cluster audit-findings). ADR-0027 status flips Draft → Accepted.
- **Canonical `Edge.evidence_type` registry** (ADR-0028 Phase 1, `evidence_types.py`): 218 entries classified across `inference_pathway` (Cluster 28A canonicals, 107 values), `endpoint_shape` (Clusters 28B/28C/28D, 111 values folding to canonical + `Edge.is_resolved` / `meta["framework_dispatch"]` / `meta["call_construct"]`), and `pending_classification`. ADR-0028 status flips Draft → Accepted.
- **`Edge.is_resolved: bool = True` sibling field** (ADR-0028 §"Sibling-field design call-out"): captures the resolution-status property previously smuggled into `*_unresolved` evidence types. Producers set `False` when folding; `from_dict` defaults missing key to `True` for backward compatibility.
- **Pre-commit + CI drift linters for `Symbol.kind` and `Edge.evidence_type`** (`scripts/check-symbol-kind-drift`, `scripts/check-evidence-type-drift`): mirror the existing `check-edge-type-drift` shape. AST-walk `packages/`, `scripts/`, `.agent/` for module-level `*KIND*` / `*EVIDENCE_TYPE*` set assignments and verify every value is in the canonical registry.
- **L3 producer-coherence linter** (`producer_coherence.py`, `scripts/check-producer-axis-coherence`): walks `Edge.create(...)` / `Edge(...)` / `Symbol.create(...)` / `Symbol(...)` call sites and verifies literal-string keyword arguments to `evidence_type` / `kind` / `edge_type` are in the corresponding canonical registry. An assignment-form extension also traces simple assignment-form references (`name = "literal"` plus ternary / if-else) within a function — surfaced 18 latent leaks on landing. F-string emits surface as advisory Phase-3 fold candidates. Closes the producer-introduction gap left by the consumer-side drift linters.
- **`docs/concept-axes.md` extends to all three axes**: `scripts/generate-concept-axes` now reads `EDGE_TYPES`, `SYMBOL_KINDS`, and `EVIDENCE_TYPES`. CI freshness gate via `--check`.
- **`docs/schema.json` carries `x-axis-of-values` annotations on all three fields**. `Symbol.kind` and `Edge.evidence_type` ship as **open** enums (current production includes dynamic f-string emits); closed-enum return is gated on Phase 4b. `Edge.edge_type` remains closed — pre-implementation audit confirmed zero f-string emit sites.
- **`axis_drift.find_drift` accepts `excluded_target_names`**: lets callers skip target names that share the filter substring but live on a different axis (e.g. `PROTOCOL_KINDS` and `BRIDGE_KINDS` are vocabularies for `Edge.meta`, not `Symbol.kind`).

#### Audit-findings docs (per-cluster verdict tables)

The `docs/audits/` series gains 12 new entries: 8 covering `Symbol.kind` Clusters 27A–27H (~201 values, including 50 RESOLVED canonicals in 27A) and 4 covering `Edge.evidence_type` Clusters 28A–28D (~221 values, including 110 RESOLVED canonicals in 28A). Each records per-row CANONICAL / FOLD / DEPRECATE-NO-FOLD verdicts and UNRESOLVED / PRELIM_RESOLVED / RESOLVED statuses.

- **Audit-findings format extended to all three axes** (`audit_findings.py`): `_REGISTRIES` carries an `_AxisRegistry` per axis, parameterising mechanical-check predicates over per-axis `canonical_axis` and `endpoint_axis`. The format previously hard-coded `relationship` as the canonical axis.

#### Audit / regression-guard infrastructure

- **DEPRECATE-NO-FOLD-zero-producer regression guard** (strict, CI-blocking): `audit_findings.find_zero_producer_violations()` enumerates DEPRECATE-NO-FOLD verdicts across the three registered axes and asserts no producer emits the value, with companion enumerators `producer_coherence.find_emitted_{symbol_kinds,evidence_types,edge_types}()`. Catches literal-kwarg and assignment-form-to-Name leaks at every commit; helper-call / f-string / dict-subscript shapes remain manual (see WI-nubuv ext B/C).
- **README index sync regression guard**: `audit_findings.find_readme_index_drift()` parses `docs/audits/README.md` and asserts the Status column agrees with each doc's verdict YAML row counts. Supports both explicit-count cells (`Mixed (6 RESOLVED, 11 PRELIM_RESOLVED)`) and bare-marker cells (`All RESOLVED`).

#### Methodology hardening

- **Fundamental Concept Audit playbook gains §"Step 4.5 — Indirection-aware producer trace"**: before claiming "no producer", auditors must check five producer-emit shapes (literal kwarg, helper-call positional/kwarg, assignment-form-to-Name, f-string interpolation, dict-subscript-target). A self-test bullet makes the trace mandatory at audit-write time. Motivated by three DEPRECATE-NO-FOLD → CANONICAL reclassifications (`theorem` / `inductive` / `message`) that a literal-grep had missed via `add_symbol(...)` / `_make_proto_symbol(...)` indirection.

#### Hooks & developer experience

- **Session-start hook prompts about prior-session `agent_notes.json`**: a non-empty notes file produces a one-line prompt naming both the notes-file age and the last-session age, asking the agent to ask the user whether to load the handoff via `./scripts/agent-notes --show`. Notes content is not dumped unprompted. When the audit-cadence prompt also fires, the two are marked as separate items.
- **Hook transcript dedup window bumped 100k → 200k tokens**: covers longer reflection sessions before the dedup-suppression heuristic engages.

### Changed

#### Concept-axis migrations (ADR-0027 / ADR-0028 Phase 3)

- **Phase 3 — eight families fold to canonical + meta across the two new axes**:
    - **`Edge.evidence_type` `*_unresolved`** (18 emit sites, 11 producer files): fold to `evidence_type=<canonical>` + new sibling field `Edge.is_resolved=False`. Two new Cluster 28A canonicals (`grpc_stub_resolution`, `luajit_ffi_lookup`) absorb sites without a prior canonical inference label. Audit-findings 0008.
    - **`Edge.evidence_type` framework-dispatch** (~75 values): fold to canonical inference (`ast_call_direct` / `ast_decorator` / `ast_import` / `naming_convention`) + `meta["framework_dispatch"]` or `meta["detection_pattern"]`. Coverage spans websocket / tauri / grpc / openapi / graphql / http / crypto / ipc / objc / event-dispatch / Go route_mount / Ruby / Django / NestJS / Vue and ~25 single-row dispatch modules. Audit-findings 0014.
    - **`Edge.evidence_type` call-construct peers** (28 values, 89 emit sites, 26 producer files): fold to apex `ast_call` + `meta["call_construct"]`. The lone non-`ast_call` apex (`cross_file_message_send`) folds to `message_send` + `meta["call_construct"]="cross_file"`. Audit-findings 0012.
    - **`Symbol.kind` framework-role** (~28 values): fold to `function` / `method` / `interface` / `class` / `reference` + `meta["framework_role"]`. Highest-blast-radius slice is `Symbol.kind="route"` (17 source files, 14 production consumers, ~330 sites). New `Pattern.framework_role` field with its own compiled-regex matcher; four YAML rules (`laravel`, `phoenix`, `rails`, `sinatra`) migrate from `symbol_kind: "^route$"`. The remaining `symbol_kind:` regex rules across `phoenix.yaml`, `falcon.yaml`, `yesod.yaml`, `library-exports.yaml`, etc. continue to match post-fold symbols via a `Pattern.matches()` fallback to `meta["framework_role"]` when the `symbol_kind` regex doesn't match the (now-canonical) `symbol.kind`. The fallback is backward-compat technical debt; the structural fix (migrate the remaining YAMLs to `framework_role:` and remove the shim) is tracked separately. Audit-findings 0013.
    - **`Symbol.kind` Cluster 27E edge-label kinds** (12 values): new canonical `call_site` absorbs subprocess / db_query / abi / twig `function_call` (→ `kind="call_site"` + `meta["call_kind"]`); other values drop the per-reference Symbol because the relationship is already on a companion Edge — 3 clean drops, 6 edge-endpoint redesigns, 4 companion-Edge introductions. Audit-findings 0010.
    - **`Symbol.kind` Cluster 27F component_ref**: vue / svelte / astro drop per-reference Symbols; `imports` edges re-route src to `make_file_id()` and fall back to a 5-part dangling component id when unresolved. DEPRECATE-NO-FOLD verdict (the original fold target `reference` was already deprecated in 0010). Audit-findings 0011.
    - **`Symbol.kind` Clusters 27B / 27G / 27H sweep**: 74 canonical promotions (registry-only); 15 FOLDs with producer migration (e.g. `module_file` → `file` + `module_system`, `npm_package` / `composer_package` → `package` + `package_ecosystem`, `test_case` → `test` + `test_dialect`, `editable` / `url_requirement` → `requirement` + `install_mode` / `install_source`, `devDependency` → `dependency` + `dependency_scope`, `python_task` → `task` + `task_implementation`); 8 DEPRECATE-NO-FOLDs (`tsconfig` subsumed by v4.0.0's `is_config_file`; `config` producer-rewritten by `prisma.py` to `kind="block"` + `meta["block_type"]`; the rest dead vocabulary or registry seed errors); 4 CANONICAL reclassifications (`theorem` / `inductive` / `message` / `external_symbol`). Consumer dual-shape predicates added at `route_handler.is_component` and `cli._is_noise`. Audit-findings 0005 / 0006 / 0007.
    - **`Symbol.kind` Cluster 27C apex/peer**: registry classification updates only (`fn` / `var` / `proc` / `structure`); no producer change. Audit-findings 0009.
- **Phase 2 consumer migration**: dual-shape predicates at `linkers/registry._is_synthetic_node`, `selection.filters.is_excluded_kind`, `route_handler.is_component`, and `cli._is_noise` recognise pre- and post-fold producer shapes so consumer filters survive the producer fold without inflating selection or compact output.
- **Phase 4a `x-deprecated` annotations**: `scripts/generate-schema` emits `x-deprecated` on `#/$defs/Symbol/properties/kind` (50 entries) and `#/$defs/Edge/properties/meta/properties/evidence_type` (111 entries), mirroring the existing `Edge.type` Phase 4a shape. Values stay valid in the open schema for the deprecation window. Phase 4b ships piecewise as each cluster's `awaits_bakeoff_validation` tag clears.
- **Verdict-correctness re-audit**: the Step 4.5 indirection-aware producer trace ran against all 19 DEPRECATE-NO-FOLD values; 2 reclassified to CANONICAL (`reference` from `swift_objc.py`, `import` from `wasm_bindgen.py`), 17 verified clean.

#### Schema versions

- **SCHEMA `0.4.0` → `0.5.8`**: additive only. The `Edge.evidence_type` enum re-opens at ADR-0028 Phase 1 land; subsequent patch bumps absorb per-Wave producer migrations. No validation that previously passed will now fail.

#### Inheritance linker

- **Inheritance linker annotates simple-name fallback edges**: when `_resolve_target_symbol` falls back to deterministic-by-sorted-ID disambiguation (multiple cross-file candidates, no same-file precision match), the resulting `extends` / `implements` edge now carries `confidence=0.5` and `meta["disambiguation_fallback"]=True`. Single-candidate and same-file resolutions remain at `confidence=0.95` with no flag. Lets downstream consumers (slice ranking, dead-code analysis, supply-chain tier classification) filter the fallback population.

#### Bakeoff infrastructure

- **`bakeoff-deep` excludes `dispatches_to` from `pick_reverse_slice_seeds` out-degree counting**: synthetic dispatch edges from interface stubs were inflating reverse-slice seed scores above real domain functions. 16 of 18 `dispatches_to` producers emit synthetic 'menu' relationships; the 2 real-dispatch producers (route_handler, grpc) score via the route and API-handler boosts already.

### Fixed

#### Framework-dispatch correctness

- **Six dispatch linkers and the Go ttrpc / CSI path in `grpc.py` walk transitive base-class chains**: `airflow_framework_dispatch`, `django_orm_dispatch`, `jackson_dispatch`, `kafka_streams_dispatch`, `view_template`, and `react_component` now BFS over `extends` / `implements` edges to discover ancestors whose `meta.base_classes` names a framework base. Fixes the dominant real-world pattern where projects extend an in-tree intermediate rather than the framework class directly (e.g. `AlloyDBWriteBaseOperator(BaseOperator)`, JPA `@Entity` extending `@MappedSuperclass`, Kafka Streams SAM wrappers, `LeafController(ApplicationController)`, project-internal React base components, ttrpc `UserHealth` → `BaseHealthImpl` → `HealthService`). New shared helper `linkers/_transitive_bases.py` (cycle-guarded BFS) is the single source of truth; `collect_transitive_base_names` accepts a `meta_keys` tuple so `kafka_streams` can fold `extends` and `implements` together. Real-world testing on airflow and pretix had previously seen 0/9 and 0/6 transitive cases. The `react_component` change also implements the base-class branch its docstring claimed (the code matched only on PascalCase).
- **Django generic CBVs inherit View lifecycle methods**: `django_orm_dispatch.DJANGO_BASE_METHODS` entries for `ListView` / `DetailView` / `CreateView` / `UpdateView` / `DeleteView` / `TemplateView` now fold in `dispatch`, `setup`, `http_method_not_allowed`, `options`, the seven HTTP verbs, `head`, and `trace`. Django's class hierarchy is external, so the transitive base-class walk above had no in-tree edge — a project class `Foo(ListView)` previously matched only `ListView`'s frozenset and never reached `View`. Pretix had zero `dispatches_to` edges to any `*.dispatch` method graph-wide. New module-level `_VIEW_LIFECYCLE` constant is the single source of truth.
- **`type_hierarchy` linker emits skip-level overrides**: the parent→children map is now closed transitively before edge emission. When `Grandparent.foo` is overridden only in `Grandchild` (intermediate `Parent` doesn't override), the edge `Grandparent.foo → Grandchild.foo` is now emitted; previously `Grandchild` was missing from `parent_to_children[Grandparent]` because the map was one-hop. New `close_parent_to_children_transitively` BFS helper preserves diamond-no-double-emit and direct-override semantics.
- **Jackson dispatch linker recognizes JPA `@Entity` / `@MappedSuperclass` / `@Embeddable`**: the prior matcher triggered only on Jackson, JAX-B, and Spring-binding annotations, missing the Spring Data JPA + Spring MVC pattern that Jackson-serializes JPA-mapped types as REST response bodies. On spring-petclinic, 6 `@Entity` classes (Owner, Pet, Visit, Vet, Specialty, PetType) had produced zero edges; bean-convention accessors now receive `dispatches_to` edges as expected.

#### Cross-language hygiene

- **Inheritance linker enforces cross-language gating + Rust kind discipline**: drops candidates whose `language` differs from the child symbol's before resolution — eliminates 31 bogus Python→Rust-trait edges in candle (e.g. `class FooModule(nn.Module)` no longer matches a Rust `Module` trait); refuses struct/enum candidates when the child is a Rust struct/enum (Rust permits no struct→struct inheritance). Bridge linkers (PyO3, cffi, wasm_bindgen, jni) remain the sanctioned path for genuine cross-language conformance edges.

## [4.0.0] - 2026-05-03

### Summary

**Breaking: 33 deprecated `edge_type` values are removed from the canonical registry** (`SCHEMA_VERSION` 0.3.1 → **0.4.0**). The cohort spans the bridge/FFI, IPC, dispatch/publish, and dst-kind families (e.g. `cgo_bridge`, `ipc_calls`, `routes_to`, `imports_module`); each was folded into a canonical relationship + `meta` key in earlier phases. Downstream consumers: see [`docs/migrating-edge-types.md`](docs/migrating-edge-types.md). The pre-commit drift gate is now `--strict`, so future endpoint_shape regressions fail at commit time.

Two new `Symbol` booleans — `is_example_file` and `is_config_file` — round out the file-role flags. Starlette routes are now detected.

Internal: the audit methodology behind ADR-0023 generalises into ADR-0024 (axis declaration template) and a new `docs/audits/` per-value verdict series; Draft ADRs 0027 / 0028 instantiate the template for `Symbol.kind` and `Edge.evidence_type`.

### Added

#### Concept-axis declarations

- **ADR-0024 — Axis Declaration Template for Multi-Value Fields**: formalises the four-part template (axis name, axiom, consumer pattern, enforcement), the seven-step declaration workflow that ADR-0023 demonstrated concretely, the CANONICAL/FOLD/DEPRECATE-NO-FOLD verdict trichotomy (§"Family-audit verdict methodology"), and the fold-residue discipline (rules for promoting recurring meta keys to dedicated fields). ADR-0023 is reframed as the worked example; future axis-shaped fields instantiate this template. AGENTS.md adds an "Axis declaration for multi-value fields" essentialization in Required Checks.
- **ADR-0027 & ADR-0028 (Drafts) — two more axes instantiate the template**: ADR-0027 names `Symbol.kind` as the source-language syntactic construct (192 values / 8 clusters; framework-participation folds to canonical + `meta["framework_role"]`). ADR-0028 names `Edge.evidence_type` as the inference pathway (210 values / 4 clusters; resolution status promotes to a sibling `Edge.is_resolved: bool`). ADR-0028 is the largest concept-axis migration on the roadmap (~140 production files have `evidence_type` literals).
- **`docs/audits/` document series**: sibling to `docs/adr/` for per-value verdict tables. Format spec at `docs/audits/README.md`; verdict rows carry `value` / `verdict` (CANONICAL | FOLD | DEPRECATE-NO-FOLD) / `fold_target`. Pre-commit gate at `scripts/check-audit-findings`.
- **Fundamental Concept Audit playbook + diagnostic catalog** (`docs/blind-spots.md`): domain-neutral procedure for detecting conceptual leaks via four leakage tests, plus a complementary catalog of four recurring question-shapes (typing axis vs values, assumed input boundaries, silently-load-bearing failure modes, null results read as confirmation). Cadence hook (`.agent/hooks/_shared/check_audit_cadence.py`) prints a soft reminder once 72+ dev commits pass without an audit. Wired into all four supported vendor session-start hooks and the agentic-session-retrospective.

#### Edge-type registry & tooling

- **Canonical edge-type registry** (`hypergumbo_core/edge_types.py`): single source of truth for `Edge.edge_type` values, each annotated with an axis classification (`relationship`, `endpoint_shape`, or `pending_classification`). `scripts/generate-schema` consumes the registry and emits an `x-axis-of-values` JSON Schema extension on `Edge.type`. A property test AST-walks the package source and fails CI if any module-level `*EDGE_TYPE*` set contains an unregistered value. Inaugural population (built up across the cycle through completeness sweeps and ADR-0023-reconciliation fixes) includes 7 newly-named relationship canonicals (`inherits`, `decorated_by`, `includes`, `defines_target`, `data_flows_to`, `module_exports`, `overrides`), 13 already-emitted values that the schema enum had been missing, 18 endpoint_shape candidates seeded for future per-pattern audits, and the four edge types (`imports_component`, `model_reference`, `type_ref`, `renders_component`) named in ADR-0023's deprecation list but previously absent.
- **Human-readable by-axis view** at `docs/concept-axes.md`, regenerated by `scripts/generate-concept-axes` with a pre-commit freshness check.
- **Pre-commit edge-type drift linter** (`scripts/check-edge-type-drift`): catches consumer-side hardcoded `*EDGE_TYPE*` sets that drift from the canonical registry. Runs in `--strict` mode by default — future endpoint_shape regressions fail at commit time. Implementation is field-agnostic (`hypergumbo_core.axis_drift.find_drift(...)`, search scope `packages/` + `scripts/` + `.agent/`) so future axis-bearing fields inherit the pattern per ADR-0024. Surfaced and fixed one phantom-value bug along the way (`bakeoff-deep::_FFI_EDGE_TYPES` referenced `jni_bridge` / `pyffi_bridge`, neither ever emitted).
- **Runtime coherence checker** (`scripts/check-edge-type-runtime-coherence`): the runtime half of ADR-0023's two-layer enforcement — partitions emitted edges by `(src.kind, src.language, dst.kind, dst.language)` and reports partitions where `edge_type` varies. Allow-list at `docs/edge-type-runtime-allowlist.yaml`.
- **`docs/migrating-edge-types.md` — downstream consumer migration guide**: rename table, meta-key vocabulary (`bridge_kind`, `channel_kind`, `mechanism`, `construct`, `dispatch_kind`, `protocol`), worked patterns, and the post-Phase-4b deprecation timeline. "What's NOT migrated yet" is grouped by next-ship: pending-classification (4), protocol-call family (3), long-tail sweep (22).

#### IR additions

- **`is_example_file` and `is_config_file` Symbol booleans**: surface two role flags mirroring `is_test_file` and `is_generated_file`. `is_example_file` fires on `examples/` / `demos/` / `samples/` / `tutorials/`; `is_config_file` fires on dependency/build manifests (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, etc.). Within tier 2 the four role flags are mutually exclusive — `is_config` is suppressed when `is_test` or `is_example` already fires. Round-trips through `Symbol.to_dict` / `from_dict`.

#### Frameworks

- **Starlette route extraction**: `Route("/path", handler, methods=[...])` and `WebSocketRoute("/ws", handler)` constructor calls from `starlette.routing` are detected and emitted as `kind="route"` symbols. Matching is import-scoped to avoid false positives from local `Route` classes; handles aliased imports. New `frameworks/starlette.yaml` attaches `concept=route` to handler functions.
- **`hypergumbo routes` empty-result hint**: when no HTTP routes are found, the command now reports related endpoint-shaped node counts (`websocket_endpoint`, `graphql_resolver`, `db_query`, `event_publisher`, `mq_publisher`, `http_client`, `subprocess_call`, …) and points at `hypergumbo run` JSON output and `hypergumbo explain <name>` for inspection.

### Changed

#### Edge-type axis migration (ADR-0023)

- **Phase 4b — 33 deprecated edge_types removed from the registry** (`SCHEMA_VERSION` 0.3.1 → **0.4.0**): the bakeoff-validated cohort across the dst-kind (6), bridge (7), IPC (7), and dispatch/publish (13) families is removed from `EDGE_TYPES`. The 25 sweep additions (protocol-call + long-tail) stay until their producers migrate. Consumer enumerations (`IMPORT_EDGE_TYPES`, `compact.CROSS_CUTTING_EDGE_TYPES`, `ranking.DEFAULT_EDGE_TYPE_WEIGHTS`, `io_boundary._TRACEABLE_EDGE_TYPES`, `taint.TAINT_CALL_EDGE_TYPES`, `cli._REACHABILITY_EDGE_TYPES`, `bakeoff-deep::_CALL_FLOW_EDGE_TYPES`) cleaned up; test fixtures rewritten to canonical shape.
- **Phase 3 — five `endpoint_shape` families folded to canonical + meta** (producers landed earlier this cycle):
    - **bridge/FFI**: `cgo_bridge` / `ffi_bridge` / `napi_bridge` / `wasm_bridge` / `native_bridge` / `bridge_invokes` → `calls + meta["bridge_kind"]`; `wasm_load` → `imports`.
    - **IPC** (Tauri, Electron, Phoenix Channels, WebSocket, message queues): `ipc_calls` → `calls + meta["protocol"]="ipc"`; event variants → `event_publishes + meta["channel_kind"]`; `websocket_connection` → `references + meta["construct"]="websocket_endpoint"`.
    - **publish/dispatch**: `routes_to` → `dispatches_to + meta["dispatch_kind"]="route"` (17 emit sites in 12 linkers, per audit-findings 0001).
    - **protocol-call** (HTTP / gRPC / GraphQL): → `calls + meta["protocol"]`; new `PROTOCOL_KINDS` constant.
    - **dst-kind leakage**: `imports_module` / `imports_component` → `imports`; `model_reference` / `type_ref` / `query_references` → `references`; `renders_component` → `references + meta["construct"]="jsx"`.
    - **DEPRECATE-NO-FOLD drops**: `message_receive` (forward `event_publishes` already captures the relationship) and `event_subscribes` from `event_sourcing.py`.
- **Earlier phases also landed this cycle**:
    - **Phase 4a** — `Edge.type` gained an `x-deprecated` JSON Schema extension listing every endpoint_shape value as a removal candidate.
    - **Phase 2** — new `IMPORT_EDGE_TYPES` predicate replaces hardcoded `{"imports", "imports_module"}` sets at `ranking.py` and `slice.py`; adding the missing `imports_component` entry closes the silent miscategorization of Vue/Svelte/Astro/React component imports (ADR-0023 §1 case 1).
- **ADR-0023 promoted Draft → Accepted**: ADR text now cites landed Phase 1 commits, cross-references ADR-0024, resolves prior Open Questions inline, and reframes the Property test section as three complementary defenses (static / runtime / cadence-hook). §6 plan reshaped from single-event to sequential micro-ships, one family at a time.

#### Audit-findings reclassification & follow-ups

- **ADR-0025 / ADR-0026 reclassified as `docs/audits/0001-dispatch-publish-family.md` / `0002-ipc-family.md`**: both were per-value verdict tables, not architecture decisions. Permanent redirect stubs preserve URL discoverability.
- **`docs/adr/README.md` — bucket rubric** for ADRs vs audit-findings vs surveys, organised around "decision present?". `docs/surveys/` is forward-declared. AGENTS.md "Required Checks" gains a one-paragraph essentialization.
- **Three IR field docstrings clarified** per the 2026-04-30 Adjacent Concept Sweep: `Symbol.origin`, `DataModelKind`, `UsageContext.kind`. Each surfaces the conflated axes and per-field re-evaluation triggers. No behavior change.

#### Governance & playbooks

- **AGENTS.md streamlined**: weasel-word bullets merged; CI Interaction Policy compressed (auto-pr exit-code recovery table moved to `ci-debug-protocol.md`); Bakeoff Validation Discipline split by cadence into per-PR (`bakeoff-validation-tagging-discipline.md`) and per-session-drain (`process-validation-queue-with-bakeoffs-and-uat.md`) playbooks.
- **Cruft-audit playbook introduced**: codifies the two-pass methodology (syntactic grep + semantic read, mediated by interactive interview) and the cruft / trim / not-cruft / doc-consistency taxonomy. First application removed the TRACKER_SYNC_PENDING workaround and stale temporal qualifiers across four playbooks.

#### CI

- **Nightly schedule shifted from 23:00 UTC → 05:30 UTC** (`.github/workflows/nightly.yml`): reduces overlap with daytime work. Doc references in CI-debug protocol and release SOP updated.

### Performance

- **Cross-linker tree-sitter parse cache**: linkers running on the same file now share a single parse via `LinkerContext.parsed_trees` keyed by `(path, language)`. Eliminates ~18,000 redundant parses per `hypergumbo run` on a 750-Python-file repo. Bound through a `contextvars.ContextVar`, so the existing 23 linker call sites need no changes.

### Fixed

- **Linker docstring/comment false positives**: 23 protocol/framework linkers ran their regex pattern detectors directly against raw file bytes, matching their own module docstrings that documented the very patterns they detect. New shared masker `linkers/_text_filters.mask_doc_regions` parses with tree-sitter and replaces comment ranges and Python module-level docstrings with spaces (newlines preserved) before regex matching. On hypergumbo self-analysis, removed 45 false-positive nodes across nine kinds (`event_publisher` -15, `mq_subscriber` -6, `mq_publisher` -5, `event_subscriber` -4, `subprocess_call` -4, `db_query` -4, `http_client` -3, `websocket_endpoint` -2, `graphql_resolver` -2). `annotation_convention` is intentionally exempt because it scans `@hg:` directives inside comments.
- **Stale references in cross-cutting / taint edge-type sets** (surfaced by the new drift property test): `taint.TAINT_CALL_EDGE_TYPES` no longer includes `unresolved_external_call` (an `evidence_type`, not an `edge_type`); `compact.CROSS_CUTTING_EDGE_TYPES` no longer includes `ffi_calls` (the name of a Python local variable inside the FFI linkers, never an emitted edge type). Pure dead-code cleanup.

## [3.0.0] - 2026-04-29

### Summary

**Breaking: `hypergumbo io-boundaries` output has changed.** The I/O catalog now holds only true stdlib primitives; wrapper chains that previously counted under `net_send` / `fs_read` / `fs_write` / `db_*` / `logging` surface through a new `external_potential` boundary, paired with a per-language `status: complete | in_progress` declaration. `--json` gains `boundaries.external_potential` and `dst_classification_unreliable` per chain; schema 0.2.2 → 0.2.4 (additive). See [`docs/MIGRATION-IO-BOUNDARIES.md`](docs/MIGRATION-IO-BOUNDARIES.md) for the migration guide.

### Added

#### IO boundaries

- **`external_potential` bucket**: every edge whose destination is a synthetic tier-3 boundary node now produces a chain carrying `dst_tier`, `dst_tier_name`, and `dst_external_boundary`. Chains from an `in_progress` source language carry `dst_classification_unreliable=True` (text output annotates `[unreliable]`). Replaces the wrapper-catalog-growth treadmill with a first-class "untrusted-territory reach" signal.
- **Per-language catalog `status` and `stdlib_provenance`**: catalogs declare `status: complete | in_progress` (default `complete`) plus a `stdlib_provenance.source_url`. Python is `complete` (3.13 stdlib, cross-checked against `sys.stdlib_module_names`); the other 12 are `in_progress`. Catalogs may also declare `stdlib_other:` for stdlib non-IO symbols that `external_potential` skips. Off-allowlist provenance hostnames are rejected at load time.
- **Attribute-style IO primitives across seven languages**: a new `module_attr_ref` edge type lights up previously-inert YAML catalog entries. Wired in Python (`os.environ`, env_read chain count 3 → 39), Go (`os.Stdout`), JS/TS (`process.env`, `window.*`, `document.*`, `navigator.*`), Java (`System.out`, imported class fields), Rust (`std::env::consts::OS`), C (non-shadowed `stdout` / `stderr` / `stdin`), and C++ (`std::cout` / `std::cerr` / `std::cin`, including aliased namespace use like `namespace fs = std::filesystem;`). Bare `cout` after `using namespace std;` remains out of scope.
- **Python stdio reclassified from `ipc_send` to `logging`**: `sys.stdout` / `sys.stderr` move to a new `logging` block, matching the fix Go's `log` / `log/slog` / `fmt` already received. Eliminated 70 of 77 `ipc_send` false-positives on self-analysis. `sys.stdin` stays in `ipc_recv` — untrusted piped input is a real IPC concern.
- **Python `pyproject.toml` dependency manifest, monorepo-aware**: a new parser walks `repo_root/pyproject.toml` and every `packages/<pkg>/pyproject.toml`, parsing `[project].dependencies`, `[project.optional-dependencies]`, and `[tool.poetry.dependencies]`. Dist-name → import-name resolution (`PyYAML` → `yaml`, `scikit-learn` → `sklearn`) via `importlib.metadata.packages_distributions()`. Wired into the Python analyzer; the manifest-aware allow-list now extends to Python so declared deps classify tier-2 instead of tier-3. On hypergumbo self-analysis, `tree_sitter` / `rich` / `pygments` / `yaml` / `sentence_transformers` / `pytest` / `jsonschema` / `requests` all flip from tier-3 to tier-2.

#### verify-claims

- **Project-local taint catalogs**: repeatable `--taint-sources PATH` / `--taint-sinks PATH` / `--taint-sanitizers PATH` flags accept YAML files or directories (globbed as `*.yaml`). Same paths can live under `extra_catalogs:` in the claims YAML. User entries matching `(module, name, kind)` replace auto-derived/built-in entries; user sanitizers concatenate.

#### Framework detection

- **Name-form normalization at matcher boundaries**: a new `NameMatcher` utility with canonical (alphanumeric + dotted-segment suffix match) and regex (terminal-segment fallback) modes lets a YAML pattern like `^BaseModel$` match `pydantic.BaseModel` from `import pydantic` + `class Foo(pydantic.BaseModel)`; same for annotations (`^RestController$` matches `org.springframework.web.bind.annotation.RestController`) and parameter types (`^Depends$` matches `fastapi.Depends`). Decorator matching stays on raw `re.compile` to avoid double-firing dispatch-set triplets like `^task$` paired with `^(celery|app)\.task$`; a regression test guards the exception. A matcher-boundary discipline lint AST-walks `Pattern.__post_init__` so future analyzers don't regress.

#### Transcript playbook injection

- **Injection output reads as a reference document, not a list of bare ids**: header is now `[Transcript Analysis — N relevant document(s)]`; each block opens with `--- <natural title> — <repo-relative path> ---` (title parsed from the first H1/H2/H3 heading); SPDX comments stripped; a one-line framing hint follows. Empirical motivation: an overlap sweep found 5 of 11 Read calls on playbook files happened *after* the pipeline had already injected the same playbook.

#### Language support

- **Jsonnet registered in `taxonomy.LANGUAGES`** under `FileRole.CONFIG` with `.jsonnet` / `.libsonnet` extensions. The existing jsonnet analyzer was already emitting jsonnet-prefixed dst ids without a registry entry, causing the strict boundary-node validator to misflag synthesized nodes (50 on alertmanager, 63 on prometheus — Grafonnet/Tanka).

#### auto-pr

- **Orphan tracker-sync PR detection**: `auto-pr`'s post-success path warns about open tracker-sync PRs whose `created_at` predates the current run (motivating incident: a sync PR sat orphaned ~3 hours). Mid-cycle sync PRs are intentionally ignored. Warning is best-effort and never affects exit code.
- **Sync-gate inspection helper**: a shared bash helper uses `flock --shared --nonblock` to inspect the tracker sync gate without disturbing the holder, auto-cleans stale lock files, and renders a friendly diagnostic. Wired into queue flush, PR preflight, and the post-merge re-check.

#### Bakeoff infrastructure

- **`--pool` recurses into collection directories**: `bakeoff-broad`, `bakeoff-deep`, and `dead-code-prospector-run.py` share a new `pool_utils` (bounded depth-2 walk + realpath-based dedup), so a catalog whose entries are themselves repo-collections — some flat symlinks, some `cohort_*/repo` real subdirs — works as `--pool` directly. Previously required a flat list of repos. Default `--pool` for `dead-code-prospector-run.py` updated to `~/ALL_REPOS`.

### Changed

- **External boundary nodes survive serialization, carry stable IDs, and stay out of top-N rankings**: synthetic boundary `Symbol`s now serialize into `behavior_map["nodes"]` with `kind="external_symbol"`, `path="<external>"`, `meta.external_boundary=True`, and `supply_chain.tier` populated (3 for most externals; 2 for Go/Java/Kotlin/Python direct deps via `DependencyManifest`). Their `stable_id` and `canonical_name` derive from a sha256 of `(language, path, name, kind)` instead of being null. `kind="file"` pseudo-IDs from `make_file_id`-style import-edge srcs collapse per-language into one canonical `<external>` Symbol; per-file attribution survives via `meta.referring_paths` (capped at 50). The orchestrator synthesises real `kind="file"` Symbols for any remaining dangling endpoints, and a new ranking dampener zeros their centrality so they stay out of top-N. Display surfaces filter via `ir.is_external_boundary()`; `cmd_explain` intentionally surfaces boundary symbols. Schema 0.2.2 → 0.2.4 (additive): `external_symbol` and `file` added to `Symbol.kind`; `Span.start_line` / `end_line` minimum loosened from 1 to 0 for zero-span nodes. On hypergumbo self-analysis: ~37% drop in external-symbol count (2,405 → 1,514), zero null `stable_id`/`canonical_name`, imports edge count rises 2,136 → 9,252.
- **Centrality and dampener pipeline aligned across all selection surfaces**: a new `compute_dampened_centrality` helper is the single source of truth for the "compute_centrality + 8-stage dampener stack" pipeline. Sketch, `select_by_coverage`, `select_by_connectivity`, and `format_tiered_behavior_map` previously called `compute_centrality` with bare defaults and ran 0–3 of the 8 dampeners; they now match `rank_symbols`'s tuned values (`hub_threshold=100`, `within_file_weight=0.3`, `max_per_file_in=5`, `edge_type_weights=DEFAULT_EDGE_TYPE_WEIGHTS`) and the full `tier → noise → utility → common_method → sibling_impl → trivial_sink → generated → file_kind` stack. A 6-repo audit (alertmanager, prometheus, kserve, chatwoot, detekt, django) showed 7–71 of top-100 churn per surface, driven mostly by external symbols and OpenAPI-generated model classes leaking into seed picks. Tagged `awaits_bakeoff_validation`.
- **Taint catalog auto-derivation**: sources/sinks auto-derive from `io_primitives/*.yaml`. Defaults: writes `fs_write`→`host_fs`, `net_send`→`network`, `subprocess`→`host_fs`, `env_write`→`host_env`, `ipc_send`→`ipc`, `browser_storage_write`→`browser_storage`; reads `env_read`→`host_secret`, `net_recv` / `ipc_recv`→`untrusted_input`. Hand-written YAML overrides on `(module, name, kind)`. 419 previously-uncovered primitives now flow through `verify-claims`. Shipped `taint_sinks/host_filesystem.yaml` and `taint_sinks/network_send.yaml` removed (they duplicated io_primitives). New zones `host_env` / `ipc` / `browser_storage` and labels `host_secret` / `untrusted_input`. `module_attr_ref` joins `TAINT_CALL_EDGE_TYPES`.
- **Browser-local storage split out of filesystem categories**: new `browser_storage_write` primitive (`localStorage.setItem` / `sessionStorage.setItem` / `.clear` / `.removeItem`, moved from `javascript.yaml#fs_write`) and new `browser_storage_read` category (`localStorage.getItem` / `sessionStorage.getItem` / `indexedDB.open` / `caches.{open,match,has,keys}`, moved from `fs_read`). Auto-import routes writes to the `browser_storage` zone; reads stay project-local since sensitivity depends on stored content. `document.cookie` stays under `env_read` pending a getter/setter split.
- **Full-suite CI cadence switched to twice-daily** (01:00 / 13:00 UTC) from every-4-hours. Singleton concurrency unchanged.

### Fixed

- **Boundary node ID well-formedness across producers**: a single invariant — boundary-node IDs must be the 5-part `{lang}:{path}:{span}:{name}:{kind}` shape — was being violated by six producers, each leaking raw paths or unresolved markers into the language slot of synthesized boundary nodes. Fixed: Markdown link extraction (URLs landing in language slot, 9 nodes); Vue `imports_component` (raw paths, 871 nodes on chatwoot); `manifest_targets` gradle/csproj `defines_target` (Java path strings, 34 nodes on kafka); bash `sources` edges (8 nodes); TOML `[project.scripts]` `defines_target` (`hypergumbo_core/cli.py` as language); five extended1 analyzers — luau / smithy / hack / jsonnet / apex — emitting 2-part `unresolved:{name}` dsts (39 jsonnet nodes on alertmanager). Each producer now emits a properly-formed 5-part id and stashes the raw path on `edge.meta.target_path`; the `build_target.py` linker reads from meta with a dst fallback. Six remaining extended1 analyzers (robot / racket / purescript / scheme / matlab / prisma) still emit a 3-part shape with malformed name/kind slots — deferred follow-up.
- **Tier 1 dataflow: trailing comments shadowing real-code nodes**: when a real-code node and a trailing comment both started on the same line — `v := compute(x)  // godoc` — the comment overwrote the real-code entry in the line→deepest-node index, leaving the call edge unannotated. Fix is a one-line predicate that skips nodes whose `type` contains `"comment"` (covers `comment` / `line_comment` / `block_comment` / `doc_comment` across every tree-sitter grammar). Empirical Go fixture: calls-edge `access_mode` coverage 43% → 71%. Benefits every tree-sitter language analyzer (Go, Kotlin, Rust, TypeScript, Erlang, Java). Tagged `awaits_bakeoff_validation`.
- **`pyproject.toml` malformed-TOML handling on Python 3.10**: `_load_pyproject` (and the parallel block in the `subprocess_cli` linker) wrapped a tomli fallback parse in `try/except ImportError`, so on 3.10 a `tomli.TOMLDecodeError` (a `ValueError` subclass) escaped the inner handler and was never reached by the outer `(ValueError, OSError)` handler — Python doesn't fall through to sibling except clauses after one fires. Refactored to resolve the loader once, then parse under a single decode-error handler. Surfaced as a 3.10 nightly test failure; 3.11+ was unaffected because tomllib's decode error reached the outer handler directly.
- **Pre-push hook no longer blocks `ci-failover disengage`'s repatriation push**: the failover guard was over-broad — it blocked every push to origin while failover was active, including the AGit feature-branch push (`refs/for/dev/<branch>`) the disengage script uses to open the Codeberg repatriation PR. Disengage was effectively bricked from the moment the guard landed. The hook now honors a `CI_FAILOVER_DISENGAGING=1` env var that the disengage script sets for that one push; direct pushes to protected branches on origin remain blocked.
- **Pre-commit bakeoff-running guard no longer false-positives on argv mentioning `bakeoff`**: the prior `pgrep -f '[s]cripts/bakeoff'` matched any process whose cmdline contained the substring — including `git add scripts/bakeoff-broad …` and heredoc commit messages naming the script. Now `pgrep` is the candidate gate; each PID's `/proc/<pid>/cmdline` is iterated NUL-delimited and only counts when some argv element matches the path-shape regex `^/?([^[:space:]/]+/)*bakeoff-(broad|deep)$`. Bash `-c` script strings (single argv element with embedded spaces) are rejected; real `python3 /path/to/scripts/bakeoff-broad …` invocations match at argv[1].
- **`auto-pr` Scenario B: PR-merged verification gate before close, post-rebase poll-and-merge loop**: timeout-recovery and hung-run paths in `scripts/auto-pr` now consult a pre-Scenario-B gate that checks whether the PR was merged during the timeout (poll endpoint 502'd while the merge actually completed) and falls back to a `mergeable=true` + short-timeout poll retry. When either fires the close-and-repush is skipped — merged → exit success, about-to-merge → fall through to the merge cascade. Post-rebase merge is now a labeled `while` loop with explicit `continue` after re-rebase (cap 3 iterations) instead of a single attempt + `Recovery:` hint.
- **`io-boundaries` CLI dropped leaf-caller roll-ups in the filter pass**: when `primitive_filter` or `exclude_tests` (default true) was set, `cmd_io_boundaries` reconstructed `BoundaryMapEntry` without `leaf_callers` or `entry_points_per_leaf`, so every bakeoff `io-boundaries.txt` artifact showed `chain_count > 0` with `leaf_callers=[]`. The leaf-rollup loop is now a public helper `compute_leaf_rollups`; the CLI lazily builds the reverse graph and recomputes rollups for the surviving chain subset. Tagged `awaits_bakeoff_validation`.
- **Python analyzer — module-level `NAME = ...` not indexed as Symbols**: the symbol-extraction pass walked classes / functions / methods only, so module-level constants (`PASS_VERSION`, `LANGUAGE_ALIASES`, `EXIT_SUCCESS`, …) were absent from `global_symbols`. Any `from <mod> import NAME` for such bindings missed the cross-file lookup and got synthesised as a tier-3 `external_symbol` instead — 151 ALL-CAPS externals on hypergumbo self-analysis. New emitter walks `tree.body` (top-level only) for `ast.Assign` and `ast.AnnAssign` with `Name` targets, including tuple-unpacking; skips `AugAssign` and Subscript / Attribute targets. The CSS-only `variable`-kind exclusion in the noise-filter is now language-conditional.
- **Python analyzer — monorepo `packages/<pkg>/src/<mod>/` layouts misqualified**: the previous helper only inspected `repo_root/src/`, so files under hatch / PDM / Poetry monorepo layouts (and hypergumbo's own `packages/<pkg>/src/`) fell back to a path-shaped qualifier like `packages.hypergumbo-core.src.hypergumbo_core.taxonomy` — invalid Python and not the real importable name. Replaced with a tree-walking source-root detector that picks the deepest matching root for each file. Single-root layouts collapse to the previous behaviour.
- **Python analyzer — dotted-submodule call resolution**: `_process_call` now emits `calls` edges for `from pkg.subpkg import X` + bare `X(...)` (e.g. `urlopen` from `urllib.request`) and `import pkg.subpkg` + `pkg.subpkg.X(...)` (multi-segment chain with an `ast.Attribute` receiver). Both were silently dropped, blocking io-boundaries and taint-flow from matching dotted stdlib primitives (`urllib`, `http.client`, `os.path`, `shutil`, `xml.etree`, `concurrent.futures`, `asyncio.subprocess`).
- **Rails exempt from import-edge framework demotion**: real Rails apps never have explicit `require 'rails'` (Bundler autoloads at boot), so `refine_frameworks` was demoting Rails to `dev_frameworks` and suppressing every `controller` / `route` / `form` / `serializer` concept tag from `rails.yaml`. New `_AUTOLOAD_BY_CONVENTION_FRAMEWORKS` exemption set, currently containing Rails. Sinatra (which IS explicitly required) stays demote-eligible — counter-test added.
- **Transcript-sync watcher doubling on SessionStart re-fires**: vendor sessions emit `session_id` on every lifecycle event (startup, resume, `/clear`, `/compact`); the unconditional launch call lacked a same-SID idempotence guard, so each re-fire stacked a fresh watcher on top of the live one — uniform 2x duplication of every event in 4 of 132 archived sessions. Two-layer fix: a same-SID PID-file kill before the orphan sweep, plus a `pgrep` fallback for watchers whose PID file was lost.
- **Transcript-change hook: TOCTOU race on injection-state file**: the load → decide → save critical section is now wrapped in an advisory `fcntl.flock` on a per-session `.lock` sibling. Before the fix, parallel PostToolUse hooks fired by the agent's parallel tool calls all read the same pre-write state, independently selected the same playbooks, and emitted duplicate injections — measured at a 33% session-wide violation rate across 6 different playbooks, with some duplicates landing 0.4 s apart.
- **IO catalog — `replace` / `rename` added to `ambiguous_names`**: the matcher's short-name fallback was matching every `something.replace(...)` as `pathlib.Path.replace` (a filesystem rename), producing 40+ false-positive `fs_write` chains on self-analysis from string-normalization sites like `name.replace("-", "_")`. Resolved calls with a `pathlib.Path` module hint still tag correctly. `fs_write` chain count: 138 → 98.
- **`sketch_embeddings` loads HuggingFace models offline-first**: a new helper tries `SentenceTransformer(name, local_files_only=True)` first and falls back to a normal load only on `(OSError, ValueError)`. Eliminates the "unauthenticated requests to the HF Hub" warning that fired on every `hypergumbo .` run with the embeddings extra installed.
- **`release.yml` pip-audit CVE-ignore aligned with `ci.yml`**: adds `--ignore-vuln CVE-2025-71176` (pytest 9.0.2 TOCTOU, dev-only transitive via `pytest-textual-snapshot 1.1.0`; single-tenant self-hosted runner). Both gates drop the ignore when `Textualize/pytest-textual-snapshot#24` ships.
- **`ci.yml` / `release.yml` pip-audit ignore for CVE-2026-3219**: pip concatenated-ZIP+tar archive confusion (CVSS 4.6 MEDIUM; AV:L, UI:A, VI:L). Fix is pip 26.1, but that release was not yet on PyPI when the CVE published 2026-04-20; the existing `pip install --upgrade pip` step picks it up automatically once it ships. Zero attack surface on the self-hosted runner.

### Removed

- **Third-party wrappers purged from every I/O catalog**: catalog membership is now strictly "the language ships it" — the previous grandfathered HTTP-client carve-out was a slippery slope. Per-language removals: **Python** — `requests` / `requests.Session`, `aiohttp.ClientSession`, `httpx.Client` / `AsyncClient`. **Java** — Apache Commons IO, Netty, OkHttp, Spring Web (`RestTemplate` / `WebClient`), Apache HttpClient 4/5, Unirest, Retrofit, Spring Data + Hibernate, SLF4J, Log4j 1.x / 2.x, Logback (JDK + Jakarta EE stay). **JavaScript** — npm HTTP clients (`axios`, `node-fetch`, `ky`, `superagent`, `got`, `undici`), Express, Fastify, Koa (Node built-ins and browser globals stay). **Rust** — `tokio::fs`, `tokio::net::*`, `hyper`, `axum`, `actix_web`, `reqwest` (`std::*` stays). **Scala** — fs2, cats-effect, sttp, http4s, akka, pekko, Play, Slick, Doobie, Quill, ScalikeJDBC, Anorm, ReactiveMongo, ZIO, scala-logging (`scala.*` + inherited `java.*` stay). Structural tests iterate every catalog primitive and assert a stdlib module prefix. Dropped chains resurface in `external_potential`.

## [2.7.0] - 2026-04-21

### Added

#### Rust analyzer backend (ADR-0014)

- **SCIP ingestion**: new `hypergumbo_core.scip` module parses Sourcegraph SCIP symbol strings and protobuf indexes (vendored binding, `scripts/build-scip-proto` regenerates at pinned SHA), then translates them to hypergumbo `Symbol`/`Edge`/call-reference objects. Adds `protobuf~=6.33` to hypergumbo-core.
- **`hypergumbo-lang-rust-analyzer` optional package**: shells out to `rust-analyzer scip`, translates the index to IR, and post-processes Rust function stable_ids through a `rust.py` parity helper so tree-sitter + SCIP symbols dedup under a single identity. Three discriminated exceptions cover missing binary / invocation failure / no output; 600 s default timeout.
- **Graceful-degrade orchestration**: `try_analyze_with_rust_analyzer` returns `None` on any failure with deduped fall-through messages. Registered analyzer at priority 45 alongside `rust.py` at 50.
- **CLI + install surface**: new `--backend rust-analyzer` root flag (sets `HYPERGUMBO_RUST_ANALYZER=1`), `install-rust-analyzer` / `uninstall-rust-analyzer` subcommands, and `install-extras` / `uninstall-extras` umbrellas with `--check` status table and `--skip` exclusion.

#### Linkers (Framework subcategory)

- **Controller-routes linker**: `contains_routes` edges from `concept: controller` classes to nested route handlers. Covers NestJS, Spring Boot, ASP.NET, Laravel, Symfony, Phoenix, Micronaut, Ktor, Grails, CakePHP.
- **Router-routes linker**: `registers_routes` edges from `concept: router` symbols to nested route registrations. Covers Phoenix, http4s, http4k, Yesod, giraffe, pedestal, ring-compojure, cowboy, sveltekit/remix/nuxt, vertx, plumber, laminas.
- **Rust trait-impl dispatch linker**: fans `dispatches_to` edges from each trait symbol to every concrete method on implementing structs. Generic-bound / `dyn Trait` call-site resolution deferred.
- **Django ORM dispatch linker**: `dispatches_to` edges from Django subclasses (`Model`, `Manager`, `QuerySet`, `ModelAdmin`, `ModelForm`, `View`, …) to user-defined overrides of framework-called methods.
- **Jackson / JavaBean serialization dispatch linker**: `dispatches_to` edges from annotated Java/Kotlin/Scala classes to bean-convention accessors (`getX`/`setX`/`isX`) and method-level handlers.
- **Airflow dispatch linker**: `dispatches_to` edges from `BaseOperator`/`BaseHook`/`BaseSensor`/`BaseTrigger` subclasses to framework-called lifecycle methods (`execute`, `pre_execute`, `poke`, `on_kill`, …).
- **Kafka Streams dispatch linker**: `dispatches_to` edges from classes implementing any of 17 Kafka Streams callback interfaces (`ValueMapper`, `Transformer`, `Processor`, `Aggregator`, +`*Supplier` forms) to their callback methods.

#### HTTP linker (cross-language)

- **Elm client detection**: HTTP linker scans `*.elm` files for `Utils.Api.<method>` wrappers, `Http.get`/`Http.post` record forms, and `Http.request`, plus indirect `let url = String.join "/" [...]` URL folding.
- **JS/TS backtick template-literal `fetch`/`axios` with module-const folding**: folds backtick URLs against module-scope constants; unresolved `${NAME}` slots map to path parameters with prefix-match fallback.

#### Entrypoints (concept → entrypoint mapping)

- **`error_handler` → `ERROR_HANDLER`** (confidence 0.95): 37 framework YAMLs — fastapi, express, django, aspnet, flask, actix, axum, gin, nestjs, rails, laravel, symfony, phoenix, …
- **`form` → `FORM`** (confidence 0.90): 12 framework YAMLs — Django, Flask-WTF, Laminas, cakephp, laravel, symfony, yii, pyramid, rails, remix, sveltekit, yesod.
- **`serializer` → `SERIALIZER`** (confidence 0.90): 9 frameworks via class-level `base_class` match — DRF, Flask Marshmallow, grape, laravel, litestar, plumber, pyramid, quart, rails.

#### Behavior map

- **Per-handler forward slices from `run`**: emits `slice.handler.<METHOD>.<path>.json` per detected route handler using bakeoff-proven parameters. Capped at 25; `--no-handler-slices` / `--max-handler-slices N` control behavior.
- **Method-call recovery linker**: rewrites `calls→Class` + `unresolved-call(name=foo)` pairs into direct `calls→Class.foo` edges when the class contains a matching child. Language-agnostic.
- **Route materializer dedupes against analyzer-emitted routes**: fixes Django CBV double-counting on pretix (985 → ~500 unique routes).
- **Class-level annotations propagate to methods for `--exclude-annotated`**: helps Spring controllers, Django CBVs, and other class-level-registered frameworks.
- **IO-boundary leaf-caller roll-ups**: `BoundaryMapEntry` gains `leaf_callers` and `entry_points_per_leaf` so shared helpers don't collapse disjoint caller chains.
- **Gradle / Maven dependency manifest for JVM tiers**: new `jvm_deps.py` parses `build.gradle`, `build.gradle.kts`, and `pom.xml`; direct deps → tier 2, unknown → tier 3. Manifest scan skips test-fixture directories (fixes detekt misdetecting `react` from fixture `package.json`).

#### Language support

- **Haskell module exports as dead-code seeds**: parses `module Foo (publicFn, Type(..)) where` headers and marks listed symbols `is_exported=True`.
- **Yesod framework detection + pattern set** (`frameworks/yesod.yaml`): covers `mkYesod`/`parseRoutes` quasi-quoter, Warp runner, `Yesod`/`YesodSubsite` typeclasses, and `<method><Resource>R` handler convention.
- **Kotlin extension-function call-site dispatch**: `receiver.extFn()` emits `calls` edges to the extension definition when receiver type matches. Evidence `ast_call_extension` at confidence 0.80.
- **Unresolved-call edges for bare global JS/TS calls**: `console.log()`, `localStorage.setItem()`, `navigator.sendBeacon()`, `window.fetch()`, `Deno.readFile()`, etc. emit unresolved edges when no import binding shadows them.

#### I/O primitive catalogs

- **TS/JS bare-name and namespace/default imports traced**: emits unresolved-call edges for `import { existsSync }`, `import * as fs`, `import axios`. Verified on create-next-app (0 → 35 boundaries) and apollo-server (7 → 14).
- **JavaScript browser APIs**: WebSocket, EventSource, BroadcastChannel, XMLHttpRequest, localStorage / sessionStorage / indexedDB / caches, ….
- **Java catalog expansion** (~136 → 312 primitives): full JDBC + JPA + Hibernate + Spring Data; SLF4J / Log4j / Logback / JUL; Apache HttpClient, Spring WebClient, Unirest, Retrofit, Commons IO. Kotlin inherits.
- **Elixir catalog** (`io_primitives/elixir.yaml`): stdlib, HTTPoison / Tesla / Req / Finch / Mint / `:httpc`, Phoenix/Plug, Ecto/Postgrex/MyXQL/Redix, GenServer/Oban/Task IPC.
- **Kotlin catalog** (`io_primitives/kotlin.yaml`): previously aliased to `java.yaml` (detekt produced only 1 boundary). Covers `kotlin.io` File/Path, ktor client/server, `android.util.Log`, `kotlin-logging`, Exposed ORM.

#### Stop hook & bakeoff validation

- **Stop-hook nudge for `awaits_bakeoff_validation` backlog**: appends an `## AWAITS_BAKEOFF_VALIDATION BACKLOG` section when tag-bearing items exceed `threshold` and the latest DEEP cycle is older than `stale_cycle_hours` (defaults 5 / 72 h). Configurable under `stop_hook.awaits_bakeoff_validation_nudge`.
- **`awaits_bakeoff_validation` cross-reference in reflect pipeline**: `bakeoff-deep-reflect` injects per-claim prompts and records `moved` / `no_move` / `inconclusive` verdicts. `aggregate --apply-verdicts` executes the tracker mutations (`moved` strips the tag; `no_move` spawns a regression sub-item). Dry-run by default.

#### CI & smart-test

- **`test-agent-infra` full-suite CI job**: new hard-gate job in `full-suite.yml` running the top-level `tests/` directory, closing the 4-hour cadence gap for `scripts/agent-supervisor`, `.agent/hooks/_shared/*.py`, and tracker-sync glue.
- **Per-PR smart-test coverage for top-level infrastructure**: new `top_level_test_map.py` maps changed top-level paths to `tests/test_<basename>.py`, folded into `AFFECTED_TESTS` by `smart-test`.

#### Agent-supervisor

- **`scripts/agent-supervisor` daemon**: Python daemon that monitors reserved-prefix tmux sessions (`hypergumbo-session-*`) and replaces stuck ones (≥ 15 min of no pane-byte delta) with fresh vendor CLIs seeded with `HYPERGUMBO_RESPAWN=1`. Subcommands `run` / `status` / `stop`; single-instance via `fcntl.flock`; state under `~/hypergumbo_lab_notebook/agent-supervisor/`. Rate-limited at 24 spawns / 24 h with auto-shutdown after 20 saturation ticks.
- **Respawn hook surface**: `.agent/hooks/_shared/touch_heartbeat.sh` sourced from every per-turn hook for telemetry; vendor session-start hooks branch on `HYPERGUMBO_RESPAWN` to auto-enable autonomous mode per `autonomous_intent.txt` and emit a seed prompt.
- **Meta-circuit-breaker**: classifies replacements as no-progress (≤ 512 pane bytes) vs progress and auto-pauses after 5 consecutive no-progress failures. `agent-supervisor resume` clears the sentinel.
- **Non-interactive seed-prompt bootstrap**: polls `tmux capture-pane` for content stability (15 s deadline), then injects `"begin"` to trigger the first model turn. Vendor-agnostic.
- **YOLO / bypass-sandbox invocation**: per-vendor flags skip approval prompts (Claude Code `--dangerously-skip-permissions`, Codex `--dangerously-bypass-approvals-and-sandbox`, Cursor `--force`, Gemini `--approval-mode=yolo`). Supervisor should run in a snapshotted VM.
- **Vendor Parity for Respawn table in AGENTS.md**: authoritative per-vendor table (Claude Code, Codex CLI, Cursor, Gemini CLI) covering hook paths, graceful-exit keystroke, and CLI invocation. Claude Code's `/quit` verified; others marked unverified with a documented verification procedure.
- **Operator-affordance fixes**: `stop` no longer ambushes the next `run` (checks `supervisor.lock` pid first); new `debugging-reset-rate-limit` subcommand zeros the 24 h spawn counter.
- **Intent/mode split in `loop-toggle`**: new gitignored `autonomous_intent.txt` records project intent separately from session runtime mode. Stop-hook circuit-breaker trips now deactivate the session without suppressing project intent.

### Changed

- **Linker subcategory vocabulary restored** (ADR-0003-ext): Protocol / Bridge / Framework / Infrastructure subcategory taxonomy is now first-class. Every linker module docstring declares its subcategory; `docs/LINKERS.md` enumerates all 45 linkers with a Subcategory column.
- **Stop hook: process-aware pause replaces 150 s blanket sleep**: polls every 3 s (1800 s cap) while `pytest` / `smart-test` / `auto-pr` / `merge-pr` are alive; returns immediately when none. Configurable via `stop_hook.watched_*` keys; `watched_process.py` filters `bash -c` / `sh -c` wrappers and normalises Python version suffixes.
- **Dead-code prospector: 8 → 46 gap categories**: adds language-gated rules (Rust trait impls; Python dunders / Django / Airflow; Go receiver methods / k8s / Cilium; Java JavaBean / Kafka / Spring; TS/JS React / Redux / Superset / Apollo). Reduces `uncategorized` on the 2026-04-11 corpus (92,218 candidates, 11 polyglot repos) from **94.0 % → 43.5 %**.
- **Behavior map node IDs use repo-relative paths**: strips the `repo_root` prefix from every Symbol/Edge/UsageContext path. Paths outside `repo_root` preserved.
- **`generate-concepts` scans Python source for programmatic concept emitters**: catches cases like `py.py` emitting `main_guard` from its AST walker. Ghost count 1 → 0.
- **`generate-concepts` detects variable-name and tuple-membership consumer patterns**: recognises `concept_type in (...)` / `{...}` / `[...]` and `not in`. 30 concepts flip inert → live; coverage moves 7/309/0 → 37/279/1 (live/inert/ghost).
- **`test-coverage` surfaces per-language false-negative caveats**: text output prints the ~20% recall gap and per-language blind spots (Java/Spring MockMvc, Kotlin PSI, Go YAML reflection, Scala macros, Ruby `described_class`, Python `parametrize`, JS/TS `describe.each`, C# `[Theory]`/Moq). JSON gains a structured `caveats` field.
- **Unified path argument across subcommands**: every subcommand accepts both `hypergumbo <cmd> /path` and `hypergumbo <cmd> --path /path`.
- **`routes` excludes test-file routes by default**: 14% of plausible's routes were from tests. `--include-tests` opts back in.

### Fixed

#### CI / build system

- **Argparse sentinel test dropped + nightly retry Node.js ordering**: (1) `test_discuss_rejects_ack_thread_before_message` deleted after Python 3.12/3.13 argparse backtracking changes. (2) `test-matrix-retry` in `nightly.yml` / `release.yml` had `actions/download-artifact@v3` before `Install Node.js`, firing full-suite on every matrix value when any primary failed.
- **`tree-sitter-c-sharp` pin tightened to `~=0.23.5`**: 0.23.5 flattened named-argument nodes and broke detection under the loose `~=0.23.1` pin; `csharp.py` named-arg handling updated.
- **`concurrency.cancel-in-progress` on tracker-ci.yml**: prevents stacked runs on retry (matches `ci.yml` block).
- **Top-level `tests/` drift**: three pre-existing failures surfaced on instrumentation. `test_committed_file_is_up_to_date` now passes `ANALYZER_SRC_DIRS` to `scan_producers`; two `TestLogTrainingExampleCohortMetadata` tests assert `pipeline_version == "v2"`.
- **`release-check` gitleaks noise quieted via `.gitleaks.toml`**: `gitleaks detect --no-git` walks the working tree regardless of `.gitignore`, so local-only agent state (transcripts, injection history, training data, rotation locks) was producing ~395 false positives per scan — mostly the 40-hex SCIP commit SHA matching the `sourcegraph-access-token` rule inside quoted transcript content. New config path-allowlists everything under `.agent/` except the committed subtrees (`agent_playbooks_protocols_sops_skills/`, `hooks/`, `tracker/`, `tracker-workspace/`, `cooldown_prompt.md`, `stop_reflect.md`) plus `__pycache__/`. Also drops scan time from ~1 min / 1.06 GB to ~8 s / 73 MB.
- **`hypergumbo-lang-rust-analyzer` added to `bump-version` and `release-check`**: the package's `pyproject.toml` and `__init__.py` are now bumped alongside the other main packages, and `release-check` includes it in the version-sync audit, build loop, and wheel-install check. Previously `prepare-release 2.7.0` left it pinned at 2.6.0.
- **`release-check` ruff gate cleared for top-level `tests/`**: 7 pre-existing violations (2 × RUF012 class-level fixture constants, 2 × F821 unresolved `"Any"` string annotations on `capsys`, 1 × RUF013 implicit `Optional`, 2 × RUF100 unused `noqa` targeting non-enabled annotation rules) were fixed in place or added to the test per-file-ignore list. `S607` and `RUF012` joined the existing test-scope ignore set; the per-file-ignore stanza now covers both `packages/*/tests/**/*.py` and the repo-root `tests/**/*.py`.
- **`release-check` pytest stage realigned with sibling full-suite runners**: previously ran `pytest --full --cov-fail-under=100 --quiet 2>/dev/null`, routing through the smart-test pytest wrapper — a dev-loop tool whose affected-only selection and targeted-manifest side effect are inappropriate for a release gate (ADR-0010). The three other authoritative full-suite runners (`full-suite.yml`, `nightly.yml`, `release.yml`) all call pytest directly. `release-check` now matches that pattern: `python -m pytest packages/*/tests/ "${COV_PATHS_ALL[@]}" -n auto --cov-fail-under=100`, stderr no longer redirected to `/dev/null`, output captured to a dedicated `.ci/release-check-pytest.log` named in the failure message. Coverage scope extended to `hypergumbo-tracker/src` so every package `bump-version` touches is gated.
- **New `scripts/lib/cov-paths.sh`**: single source of truth for the per-package `--cov=` args needed by authoritative full-suite runners. Sourced by `release-check`; intentionally not sourced by `smart-test` (dev-loop keeps its own coverage policy, e.g., excluding tracker). Adding a new released package now means appending one line to this file instead of editing every gate in parallel.
- **`release-check` no longer false-positive-fails on fresh release branches**: the "Check if up to date with remote" step captured `$(git rev-parse "origin/$CURRENT_BRANCH" 2>/dev/null || echo "none")`. Without `--verify`, `git rev-parse` echoes the input ref to stdout *and* returns non-zero on an unresolvable ref, so `REMOTE` ended up as the multi-line string `"origin/release/vX.Y.Z\nnone"` — failing both the `== "none"` warn branch and the `git merge-base --is-ancestor` warn branch — and blocking the release gate every time `prepare-release` created a brand-new branch. Switched to `git rev-parse --verify "origin/$CURRENT_BRANCH^{commit}"`, which emits nothing and exits non-zero on an unknown ref, so the fallback branch is the only thing the substitution captures.
- **`smart-test` and `prepare-release` no longer die with SIGPIPE (141) on release commits**: both scripts run `set -o pipefail` and both had a `find … | head -1` pipeline that `head` closes after one line, propagating SIGPIPE back to `find`/`sort` and killing the enclosing script with 141 before its real work completes. In `smart-test`'s VERSION_ONLY branch (scripts/smart-test:601, the "one test per affected package" selector for release-commit manifests) the failure meant the targeted manifest was never written, so `auto-pr`'s `elif smart-test --manifest` at scripts/auto-pr:1000 fell through and printed the misleading `⚠️  Manifest generation skipped (no stable hypergumbo?)`. CI's per-PR `pytest` job then rejected the resulting stale manifest with `❌ No valid manifest - cannot run tests`. Fixed: the smart-test site now uses `readarray` into a process-substitution so there is no outer pipeline for pipefail to kill; the prepare-release site (scripts/prepare-release:146, checking whether any tracker ops are pending) uses `find -print -quit` so there is no pipe at all.

#### auto-pr

- **`.ops` backup/restore no longer overwrites concurrent tracker writes**: new `_ops_union_restore_file` helper performs an order-preserving line-level union instead of `cp`-clobbering; restore loop enables `shopt -s dotglob` so dotfile `.ops` paths match.
- **Exit 2 (timeout) soft-retry**: the hung-run retry loop previously fired only on Exit 3. On Exit 2, `auto-pr` now re-polls once with a 300 s timeout and does one close-PR + repush before escalating to Scenario B.

#### Stop hook

- **`stop_hook_state.json` write discipline**: jq merge now starts from an explicit maintained-field extraction instead of `.`, so dropped keys from old migrations no longer linger. Recover-state playbook documents the field table.

#### Analyzers & edges

- **Solidity file-level `using X for Y;` applies inside contracts**: edge extractor now unions contract-scoped with file-level `using_libraries` set.
- **`test-coverage` recognises framework-tagged tests outside test paths**: any function with a `meta.concepts` entry starting `test` is treated as a test. Fixes shellcheck's Template-Haskell `$forAllProperties` case (2214 `prop_*` functions → 0% reported coverage before).
- **`dead-code-maybe` drops generated-file candidates**: any candidate with `supply_chain.is_generated_file=True` is filtered before ranking. Language-agnostic.
- **Django CBV routes expand per declared HTTP method**: `path("/foo/", FooView.as_view())` previously emitted a single `[GET]` route; new `expand_class_based_view_routes` post-pass emits one route per declared method. Out-of-repo view classes stay `[ANY]`.
- **Java `size`/`length`/`copy`/`find` no longer misclassified as `fs_read`**: added to io-boundary `ambiguous_names`.
- **Scala framework detection reads `project/*.scala` and `project/*.sbt`**: SBT meta-build convention keeps real coordinates in `project/Dependencies.scala`. Docspell's http4s imports are now visible to `profile.frameworks`.
- **Laravel `apiResource()` phantom routes eliminated; `.except()` / `.only()` honored**: 5 routes instead of 7 (index/store/show/update/destroy). Koel: ~40 phantom routes eliminated (~19% of 207).

#### CLI

- **`--config-extraction=embedding/hybrid` warns when sentence-transformers is missing**: both modes silently degraded to heuristic. Dispatcher now emits a one-shot stderr notice before falling back.
- **`verify-claims` surfaces languages with no taint-flow catalog**: trivially-passing claims against unanalyzed languages previously gave false security confidence. JSON schema unchanged.
- **`io-boundaries` distinguishes "no I/O" from "language unsupported"**: `IoBoundaryCatalog` gains `is_supported: bool`; JSON output adds `unsupported_languages: []`.
- **Subcommand parser cleanup**: (1) `hypergumbo foobar` prints a `Did you mean: …` via `difflib` instead of silently inserting `sketch`. (2) `--debug` stripped from argv in any position.
- **Embedding-model load quieted**: `_hf_noise.suppress_hf_noise()` runs at `sketch_embeddings` import (before `sentence_transformers` caches env) via `setdefault` so user overrides are preserved.
- **`-e/--exclude` glob normalization**: `ui/`, `ui/**`, `**/ui/**`, `**/ui` behave consistently with bare `ui`. Path-anchored patterns like `cmd/server.go` honored against the relative path.
- **README / markdown heading bleed**: ATX headings in rendered `.md`/`.mdx`/`.markdown`/`.rst` files demoted 2 levels so they don't compete with hypergumbo's H2 structural sections.
- **Token budget validation**: `-t 0` and negative values rejected by argparse on `sketch` and `explain`.
- **Single-file input exits cleanly**: `hypergumbo run` / `sketch` on a file prints a hint and `sys.exit(1)` instead of `NotADirectoryError`.
- **Quieter partial-linker warnings on polyglot repos**: suppress when the only met requirement is a language-file presence check. Alertmanager: 8 warnings → 1.
- **`--require-section` actually works**: fixes `max_tokens <= base_tokens` early-return bypassing section gates. Verified on alertmanager `-t 500`.

### Performance

- **Cached secret-scan results across warm sketch runs**: `scan_content_cached` keys gitleaks output by sha256 (8 entries). Warm `hypergumbo sketch` ≈ `--no-secret-scan` time (~7 s on alertmanager, was ~15 s). Cache invalidates on repo state change.

### Documentation

- **`docs/agent-supervisor.md` operator guide**: net-new user-facing doc covering first-time setup, daily operations, `status` JSON semantics, edge cases, and troubleshooting matrix. Linked from `README.md`.

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
