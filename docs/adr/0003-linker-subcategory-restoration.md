<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0003 Extension: Linker Subcategory Restoration

Date: 2026-04-16
Status: Proposed

## Supplements

This ADR supplements [ADR-0003 §2.4](0003-architectural-analysis-and-revision-plan.md). It does **not** supersede ADR-0003 as a whole — the analyzer-vs-linker / FRAMEWORK_PATTERNS architecture from that document remains authoritative. This extension addresses a specific drift in ADR-0003 §2.4's subcategory vocabulary and proposes a remediation.

## Context

### The taxonomy as originally specified

ADR-0003 §2.4 defined three linker subcategories:

| Subcategory | Definition | Original enumeration |
|---|---|---|
| **Protocol Linkers** | Framework-agnostic; match on protocol semantics regardless of framework | `http`, `websocket`, `database_query`, `message_queue`, `event_sourcing` |
| **Bridge Linkers** | Language-pair-specific; connect symbols across language boundaries via FFI conventions | `jni`, `swift_objc` |
| **Framework Linkers** | Framework-specific; implement framework conventions | `grpc`, `graphql`, `graphql_resolver`, `phoenix_ipc` |

The taxonomy was drafted with reference to the 14 linker files that existed on 2026-01-07, which we can confirm from the drafting notebook (`~/hypergumbo_lab_notebook/notebookjournal_01072026_1900_architecture_analysis_v2.md` line 794: "Linkers (14 total)…Protocol: http.py, websocket.py, message_queue.py, event_sourcing.py, ipc.py, database_query.py; Bridge: jni.py, swift_objc.py; Framework: grpc.py, graphql.py, graphql_resolver.py, phoenix_ipc.py; Other: dependency.py").

Crucially, the **Protocol Linkers** subcategory was designed to cover within-language framework-agnostic dispatch (WebSocket event handlers, IPC message channels, SQL queries, Pub/Sub topics). The author understood that "linker" is not a synonym for "cross-language" — §2.4's subcategorization proves it.

### What went wrong

Between 2026-01-07 and 2026-04-16, the linker count grew from 14 to **45** files under ~20 distinct introducing commits. During that growth the subcategory vocabulary was never used again in working diction:

- **Module docstrings:** 34 of 45 linker files self-describe as "linker" with no subcategory qualifier (`LINKER_UNQUALIFIED` in our G-doc audit). Only 2 files explicitly call themselves within-language (`di_resolution.py`, `type_hierarchy.py`). 10 files explicitly call themselves cross-language / FFI bridges. **Zero files reference ADR-0003 §2.4 or the Protocol / Bridge / Framework terms.**
- **Commit messages:** A stratified sample of 5 within-language-dispatch linker introducing commits (`f96300b2b` openapi, `b45f306c7` di_resolution, `9cc7d54a7` decorator_dispatch, `e825294d0` middleware_chain, `aabf1d945` method_call_recovery) — **zero engaged with the subcategory taxonomy**. Placement in `linkers/` was treated as the natural default. The FRAMEWORK_PATTERNS alternative home was never considered.
- **Restructuring discussions:** A full-history git-log pickaxe search plus tracker item sweep returned null findings. No commit, PR body, tracker item, or lab-notebook entry proposed restructuring `linkers/`, renaming the concept, or revising §2.4. The taxonomy was neither endorsed nor opposed — it was forgotten.
- **ADR-0003 itself:** 2 commits total touch the file — its creation (`66fd92c5c`) and a blanket SPDX license header addition (`e1433706c`). §2.4 has never received a content revision.
- **Downstream documents** (ADR-0010 directory comment, ADR-0012's "24 cross-language linkers" line, ADR-0015's repeated "cross-language linker" framing, `docs/LINKERS.md`'s title and opening sentence, `docs/hypergumbo-spec.md` §7, `README.md`, `docs/ARCHITECTURE.md`, `AGENTS.md` BROAD/DEEP priorities, three bakeoff playbooks) collectively treat "linker" as a synonym for "cross-language linker." The narrow framing pre-dates ADR-0003 (lab-notebook entry 2025-12-25 already used "cross-language linkers" as a casual category label for IPC and WebSocket) and was never corrected.

### Why this matters

Roadmap prioritization is biased toward novel cross-language pairs over higher-FP-volume within-language framework dispatch. The WI-tubot 2026-04-11 prospector run (92,218 dead-code candidates across 11 polyglot repos) categorized the volume distribution as:

| Category | Count | % of pool |
|---|---|---|
| Java JavaBean accessor (within-language framework dispatch) | 7,614 | 8.3% |
| Python Airflow framework (within-language) | 4,511 | 4.9% |
| Kafka Streams internal (within-language) | 2,386 | 2.6% |
| Python Django ORM (within-language) | 2,441 | 2.6% |
| Rust trait impl (within-language) | 3,004 | 3.3% |
| **Cross-language API (the category most linkers addressed historically)** | **1,113** | **1.2%** |

The within-language framework-dispatch categories dominate the cross-language category by roughly an order of magnitude. INV-nimuj (filed 2026-04-16) captures this as a prioritization heuristic: *"linker roadmap prioritizes dead-code-FP volume, not novelty of language pair."*

Three new tracker items (WI-gupah Jackson/JavaBean, WI-nutav Airflow framework, WI-lisov Kafka Streams) were filed to address the biggest gaps. Each was initially written as a "cross-language via X boundary" item; each had to be corrected mid-flight to clarify that the linker itself operates within-language and the cross-language consequence (JSON consumed by TS clients, cloud SDK calls past Hook.get_conn, Kafka records flowing to polyglot consumers) is downstream. The overselling was a direct symptom of the missing subcategory vocabulary.

## Decision

Restore the ADR-0003 §2.4 subcategory vocabulary as a first-class classification that every linker declares, every documented catalogue reports, and every bakeoff priority respects. Extend the taxonomy with a fourth subcategory to accommodate files that were already absorbed into `linkers/` but don't fit the original three.

### 1. Restore Protocol / Bridge / Framework as active terms

The three original subcategories retain their ADR-0003 §2.4 definitions:

- **Protocol Linker** — framework-agnostic; match on protocol semantics (URL pattern, SQL table name, message topic, event name). Typically activates always, regardless of detected frameworks.
- **Bridge Linker** — language-pair-specific; connect symbols across language boundaries via FFI or runtime-bridging conventions. Typically activates when both languages of the pair are present.
- **Framework Linker** — framework-specific; implement framework conventions for dispatch, routing, or concept resolution. Typically activates when the framework is detected.

### 2. Add a fourth subcategory: Infrastructure

Some files in `packages/hypergumbo-core/src/hypergumbo_core/linkers/` operate as general graph-structure utilities rather than dispatch recovery. They populate structural relationships (e.g., `contains`, `extends`, `depends_on_manifest`) that other linkers assume are present. These don't fit Protocol (they don't match on runtime-protocol semantics), Bridge (they don't cross language boundaries), or Framework (they're not framework-specific).

- **Infrastructure Linker** — graph-structural utilities run as Tier 2 passes but not doing dispatch recovery. Examples: `containment.py` (class→method `contains` edges), `inheritance.py` (base_classes → extends edges), `js_module.py` (import-path-to-file resolution), `build_target.py` (build manifest → entry point), `dependency.py` (import statement → manifest declaration).

The fourth subcategory is a documented **extension** of ADR-0003 §2.4, not a replacement.

### 3. Every linker module's docstring declares its subcategory

The opening sentence of every file in `packages/hypergumbo-core/src/hypergumbo_core/linkers/*.py` (excluding `__init__.py` and `registry.py`) must be of the form:

```python
"""[Protocol|Bridge|Framework|Infrastructure] linker: <one-line purpose>.
```

This is enforced by convention (not tooling for now), checked by spot audit at any linker-modifying PR's review phase.

### 4. Cataloguing documents use the subcategory vocabulary

- `docs/LINKERS.md` is renamed (title → "Linkers", not "Cross-Language Linkers") and its table gains a Subcategory column.
- `docs/hypergumbo-spec.md` §7 is renamed ("Linkers") and restructured around the subcategories. Its existing inclusive definition at line 197 ("*A linker is a Tier 2 pass that creates cross-language or cross-component relationships*") is amplified as the canonical one-sentence definition — it was always correct, it just wasn't propagated into the neighbouring text.

### 5. Every new linker PR includes a subcategory declaration

The subcategory label is part of the standard header for a new linker module. The PR body cites this ADR and includes a corresponding entry in `LINKERS.md`'s table. This is a convention-level commitment; no automated check is introduced today. Future automation (e.g., a pre-commit hook that scans `linkers/*.py` for the subcategory token) is deferred to a follow-up work item.

### 6. Bakeoff prioritization follows INV-nimuj

The existing BROAD/DEEP priority queues are updated to explicitly cite `INV-nimuj` as the prioritization heuristic: linker investment is ranked by expected false-positive-reduction volume on the current prospector corpus, not by novelty of language pair. Framework and Protocol subcategory work is ranked alongside Bridge subcategory work, not below it.

### 7. Tracker tag list is extended (not replaced)

The `trackerize` playbook's well-known-tags list adds:
- `protocol_linkers`
- `framework_dispatch_linkers`
- `infrastructure_linkers`

The pre-existing `cross_language_linkers` tag is retained for backwards-continuity with already-tagged items. New items use the finer-grained tags; no backfill is required. `cross_language_linkers` remains a reasonable tag for any item that genuinely spans a language boundary (typically Bridge-subcategory work and some Framework work).

### 8. ADR-0003 §2.4's original table is extended

§2.4's three-subcategory table (Protocol / Bridge / Framework) is extended to four (adding Infrastructure). The original three subcategories' definitions and activation-condition framing are preserved verbatim — only the enumerated examples are expanded to reflect the current 45-file catalogue. This is an **additive** revision; it does not contradict the original.

## Alternatives considered

**Alternative A — Amend ADR-0003 in place.** Rewrite §2.4 directly with the expanded tables and forward-looking commitment. Rejected: ADR-0003 is a sprawling document (~1000 lines) whose strongest contribution is the analyzer-vs-linker / FRAMEWORK_PATTERNS architecture. A multi-page §2.4 rehabilitation would dilute that focus. The extension-ADR pattern (already used by `0003-call-patterns-extension.md` and `0003-usage-context-patterns.md`) is a known idiom in this repo for scoped follow-ups.

**Alternative B — Abandon the subcategory vocabulary and commit to "just linkers."** Rejected: the Protocol / Bridge / Framework distinction has genuine prioritization value (different activation conditions, different FP profiles, different implementation styles). Collapsing the vocabulary would make the roadmap-bias problem (INV-nimuj) harder to reason about, not easier.

**Alternative C — Introduce a new vocabulary.** Rejected: ADR-0003's terms are already correct. The failure was vocabulary inertia (the terms were coined, then never repeated in practice), not vocabulary inadequacy. Introducing new terms would pay the introduction cost without solving the inertia problem.

**Alternative D — Defer the Infrastructure subcategory to a follow-up ADR.** Rejected: split creates more churn than it saves. The Infrastructure subcategory is a small additive clarification (covers ~5-6 files that are already in `linkers/`); addressing it alongside the main restoration keeps the effort in one ADR, one set of PRs, and one human-review cycle.

## Consequences

Implementation is sequenced across six PRs, five non-governance and one governance:

| PR | Surface | Governance | Depends on |
|---|---|---|---|
| 1 | This ADR (`docs/adr/0003-linker-subcategory-restoration.md`, new) | No | — |
| 2 | `docs/LINKERS.md` rewrite (rename + Subcategory column + enumerate all 45 files) | No | (can ship in parallel with PR 1) |
| 3 | `docs/hypergumbo-spec.md` in-place corrections | No | PR 1 (forward pointers) |
| 4 | ADR-0003 / 0010 / 0012 / 0015 in-place corrections | No | PR 1 (forward pointers) |
| 5 | Module docstring sweep + `README.md` + `ARCHITECTURE.md` + `CHANGELOG.md` | No | PR 1 Appendix B reviewed |
| 6 | `AGENTS.md` + three bakeoff playbooks + `trackerize` well-known-tags | **Yes** | PR 1 + `needs_human_review` tracker item approved |

Verification after all six land:

1. **Bias re-audit.** Re-run the G-doc survey (check module docstrings for the subcategory label). Target: ≥43 of 45 files declare a subcategory in their opening sentence.
2. **Bakeoff prioritization cross-check.** Re-run `./scripts/dead-code-prospector-run.py` aggregation against the 2026-04-11 corpus. The categorizer output is unchanged by this vocabulary work (expected and desired — the WI-vupin categorizer operates on candidate names, not linker docstrings) — but any follow-up tracker items filed after this ADR should tag themselves with the new finer-grained tags rather than the legacy `cross_language_linkers`.
3. **Retrospective notebook entry.** `~/hypergumbo_lab_notebook/linker_subcategory_restoration_retrospective_04162026.md` records before/after counts, unresolved judgment calls, and any residual risk.

## Appendix A: Investigative methodology

This ADR's evidence base was produced via a multi-phase audit with explicit bias-suppression controls. The methodology is documented here so that future architectural audits can replicate or extend it.

### A.1 Registered predictions

Before running any survey, seven testable predictions were written down. Each had a pre-specified verdict rubric (supported / refuted / insufficient evidence). The predictions are preserved verbatim in the session that produced this ADR — summarised:

- **P1.** ADR-0003 §2.4 has not been meaningfully expanded since authoring.
- **P2.** ≥ half of within-language framework-dispatch linkers were added after ADR-0003.
- **P3.** At least one introducing commit for a within-language linker does NOT reference ADR-0003 §2.4.
- **P4.** ADR-0012's "24 cross-language linkers" line was written when within-language linkers already existed.
- **P5.** No commit, PR, or tracker item proposed restructuring the linker taxonomy.
- **P6.** Within-language linkers were added in ≥3 distinct introducing commits, not one refactor.
- **P7.** Module docstrings don't surface the cross-language vs within-language distinction.

Final verdicts: P1 supported; P2 partially supported (revised finding — 8 pre-existed ADR, 16 post-date); P3 strongly supported (0/5 in the D-read sample); P4 strongly supported; P5 supported (null finding); P6 strongly supported (~20 commits over 4 months); P7 strongly supported (34/45 `LINKER_UNQUALIFIED`, 0 taxonomy refs).

### A.2 Subagent-enforced bias suppression

Categorical-judgment tasks were delegated to Explore subagents with fresh context windows so the registered predictions could not leak through. Four subagent tasks:

- **B-classify:** Blind-classify each of ~45 linker files into `CROSS_LANGUAGE_BOUNDARY` / `WITHIN_LANGUAGE_FRAMEWORK_DISPATCH` / `MIXED` / `OTHER_INFRASTRUCTURE` based on code and docstring, using a symmetrically-framed rubric.
- **D-read:** Read the introducing commits for a stratified sample of within-language linker additions (earliest, latest, median, two random) and score each on T (taxonomy awareness), P (placement reasoning), F (FRAMEWORK_PATTERNS consideration), L (language-scope framing). No leading questions.
- **F-counter:** Search full repo history (commits, tracker items, ADR edits) for ANY evidence of restructuring proposals. Required to report null findings explicitly.
- **G-doc:** Read every linker module's docstring and categorise how it self-describes: `CL_EXPLICIT` / `WL_EXPLICIT` / `BOTH` / `LINKER_UNQUALIFIED` / `NOT_SELF_LINKER`.

Each subagent prompt:
- Stated the task, not the hypothesis.
- Provided a symmetrically-framed rubric with "other / ambiguous / insufficient evidence" as a first-class category.
- Explicitly instructed the subagent not to use ratio-of-answers as a sanity check (that's bias leaking in).
- Did not mention the registered predictions.

### A.3 Mechanical git queries

Deterministic timeline anchoring: ADR introduction commits (`git log --diff-filter=A --reverse -- docs/adr/<NNNN>-*.md`), linker-file true first-commits (via `--follow` across the `src/hypergumbo/linkers/ → packages/hypergumbo-core/src/hypergumbo_core/linkers/` relocation), §2.4-adjacent ADR-0003 edit history (`git log -S 'cross-language linkers'`). The `--follow` step revealed a reorganisation (commit `55296f544`) that polluted a naïve `--diff-filter=A` query and would have shifted the first-commit dates by 5 weeks if uncaught.

### A.4 Lab-notebook survey

`~/hypergumbo_lab_notebook/*.md` (216 files) was surveyed for restructuring discussions. The 2026-01-07 architecture analysis v2 (the ADR-0003 drafting notebook) was the key find: it contained an explicit 14-file linker inventory classified into Protocol / Bridge / Framework / Other — confirming the taxonomy was informed by existing code, not aspirational. Post-ADR notebooks revert to casual "cross-language linker" phrasing; the subcategory vocabulary appears in **zero** entries after the ADR was committed.

### A.5 Prediction-by-prediction verdicting

After all subagents and mechanical queries completed, each registered prediction was scored against the evidence. Null findings were first-class. "Insufficient evidence" was a permissible verdict. Wrong predictions were acknowledged and the underlying story was revised, not retrofitted.

One substantive revision: the original "gradual drift" narrative did not survive the evidence. The ADR was authored with reference to existing code (not in ignorance of it); subsequent additions were deliberate features (not drift); the failure mode was *vocabulary inertia* — the subcategory terms were coined at authorship and never promoted into working diction by any subsequent commit or discussion. This reframing produced a different remediation: re-introduce the existing terms (cheaper and more durable) rather than invent new ones (more churn, same failure mode risk).

## Appendix B: Current-state linker inventory

**Status:** Draft. The subcategory assignments below are derived from the B-classify blind classification cross-referenced with ADR-0003 §2.4's original enumeration and the author's understanding of each file's code. Borderline cases are flagged. This table should be reviewed by a human before PR 5 (module docstring sweep) ships — individual entries may move between subcategories based on that review.

| File | Subcategory | Notes |
|---|---|---|
| `annotation_convention.py` | Protocol | Framework-agnostic annotation convention scanner (developer-provided `@hg:` directives). |
| `build_target.py` | Infrastructure | Build manifest → entry-point resolution; structural. |
| `cgo.py` | Bridge | Go↔C FFI (cgo `import "C"` pseudo-package). |
| `containment.py` | Infrastructure | Class→method `contains` edges; structural. |
| `crypto_flow.py` | Protocol | Framework-agnostic crypto-API pattern matching; data-mediated coupling. |
| `database_query.py` | Protocol | Per ADR-0003 §2.4 original. |
| `decorator_dispatch.py` | Framework | Decorator-registry patterns (`@register_analyzer`, `@register_linker`). Framework-specific in spirit (which decorator dialect). |
| `dependency.py` | Infrastructure | Manifest-dependency → code-import edges. |
| `di_resolution.py` | Framework | Spring/Guice/Inversify/etc. DI-binding resolution. |
| `event_sourcing.py` | Protocol | Per ADR-0003 §2.4 original. |
| `go_cobra.py` | Framework | Cobra command struct literal → RunE handler dispatch (Go-specific). |
| `go_memberlist.py` | Framework | Memberlist delegate-callback dispatch (Go-specific, HashiCorp memberlist). |
| `graphql.py` | Framework | Per ADR-0003 §2.4 original. |
| `graphql_resolver.py` | Framework | Per ADR-0003 §2.4 original. |
| `grpc.py` | Framework | Per ADR-0003 §2.4 original. Also Bridge-flavoured (cross-language RPC). |
| `http.py` | Protocol | Per ADR-0003 §2.4 original. |
| `inheritance.py` | Infrastructure | base_classes → extends/implements edges; structural cross-language convention-mapping. |
| `ipc.py` | Protocol | Per ADR-0003 §2.4 original (covers Electron IPC, Web Workers, postMessage). |
| `jni.py` | Bridge | Per ADR-0003 §2.4 original. |
| `js_module.py` | Infrastructure | Import-path → file resolution; structural. |
| `lua_ffi.py` | Bridge | LuaJIT FFI → C. |
| `message_dispatch.py` | Protocol | Typed wire-protocol message matching (JS/Rust). |
| `message_queue.py` | Protocol | Per ADR-0003 §2.4 original (Kafka/RabbitMQ/etc. topic matching). |
| `method_call_recovery.py` | Protocol | Language-agnostic chained-call recovery; framework-agnostic by design. |
| `middleware_chain.py` | Framework | Middleware ordering is framework-convention-specific (Flask, Django, Express, Go, Rails). |
| `napi.py` | Bridge | JS↔C/C++ via Node-API. |
| `openapi.py` | Framework | Per ADR-0003 §2.4-analogous (framework-tied to OpenAPI spec convention). |
| `orm.py` | Framework | Django/SQLAlchemy/ActiveRecord model reference conventions. |
| `otp.py` | Framework | Elixir/Erlang GenServer call/cast dispatch. |
| `phoenix_ipc.py` | Framework | Per ADR-0003 §2.4 original. |
| `pyffi.py` | Bridge | Python↔C via ctypes/cffi/PyO3. |
| `react_component.py` | Framework | JSX composition (React-specific). |
| `route_handler.py` | Framework | Route → handler via framework-specific metadata (Rails, Phoenix, Express, Django). |
| `ruby_ffi.py` | Bridge | Ruby↔C via FFI gem / C extensions. |
| `solidity_abi.py` | Bridge | TS/JS↔Solidity via ABI. |
| `subprocess_cli.py` | Protocol | Subprocess invocation → CLI entry-point matching (language-agnostic). |
| `swift_objc.py` | Bridge | Per ADR-0003 §2.4 original. |
| `tauri_ipc.py` | Bridge | TS/JS↔Rust via Tauri's typed IPC. |
| `type_hierarchy.py` | Framework | Polymorphic dispatch via interface/abstract-class hierarchy (cross-language but dispatch-mechanism-specific). |
| `view_template.py` | Framework | Rails controller-to-template by-convention rendering. |
| `vue_component.py` | Infrastructure | Vue import-path → `.vue` file resolution. |
| `vue_template_method.py` | Framework | Vue template event handler → script method (Vue-specific). |
| `wasm_bindgen.py` | Bridge | JS/TS↔Rust via wasm-bindgen. |
| `websocket.py` | Protocol | Per ADR-0003 §2.4 original. |
| `yjs_crdt.py` | Framework | Yjs shared-types reactive flow (Yjs-specific). |

**Summary:** Protocol 13 · Bridge 10 · Framework 16 · Infrastructure 6. Total 45 (excluding `__init__.py` and `registry.py`).

**Borderline cases flagged for reviewer judgment:**
- `grpc.py` — listed as Framework per ADR-0003 §2.4 original. Has Bridge-style cross-language RPC semantics too. Keeping Framework to match ADR-0003's original enumeration.
- `inheritance.py`, `type_hierarchy.py` — both address dispatch via class-hierarchy polymorphism. Inheritance edges (extends/implements) are structural (Infrastructure); polymorphic method dispatch is framework-specific-in-spirit (Framework). Split placements reflect this: inheritance.py → Infrastructure; type_hierarchy.py → Framework.
- `decorator_dispatch.py` — covers multiple decorator dialects (Flask, Click, register_analyzer, etc.). Placed in Framework because the decorator dialect is framework-specific; the mechanism (decorator registry) could arguably be Protocol.
- `annotation_convention.py` — developer-provided annotations, framework-agnostic. Placed in Protocol. Arguable because "annotation" itself is a framework-ish affordance.
- `method_call_recovery.py` — language-agnostic by design; recovers edges that analyzers already emit. Placed in Protocol as the framework-agnostic pattern recovery most closely matches §2.4's Protocol definition. Arguable whether it's more Infrastructure (purely AST-structural).

## Cross-references

- [ADR-0003 Architectural Analysis and Revision Plan](0003-architectural-analysis-and-revision-plan.md) — the supplemented document.
- [ADR-0010 Modular Packages and Smart Testing](0010-modular-packages-and-smart-testing.md) — directory comment at line 34 to be corrected in PR 4.
- [ADR-0012 Pass Unification and Multi-Fidelity](0012-pass-unification-and-multi-fidelity.md) — "24 cross-language linkers" count to be corrected in PR 4.
- [ADR-0015 Dataflow Access Modes on Edges](0015-dataflow-access-modes.md) — "cross-language linker" / "polyglot linker" framing to be corrected in PR 4.
- [`docs/LINKERS.md`](../LINKERS.md) — rewritten in PR 2.
- [`docs/hypergumbo-spec.md`](../hypergumbo-spec.md) — corrected in-place in PR 3.
- Tracker items: `INV-nimuj` (prioritization heuristic), `WI-gupah` (Jackson framework linker), `WI-nutav` (Airflow framework linker), `WI-lisov` (Kafka Streams framework linker) — cited as illustrative applications of the restored vocabulary.
