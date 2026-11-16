<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# 0023 — Reader census: write-only fields, keys, knobs and functions, producerless readers, vocabulary subsets, and keep-with-trigger verdicts

```yaml
kind: reader_census
date: 2026-10-28
pinned_tree: 02a25bba56
trigger: WI-kosup (the input filed for the next Fundamental Concept Audit, scope widened by the owner on 2026-10-25)
outcome: confirmed
```

## Suspicion

hypergumbo carries fields, meta keys, knobs and functions that nothing reads, readers keyed to values nothing
produces, and hand-written subsets of registered vocabularies that drift. Every one of the 19 recorded concept
audits found instances of these only in passing; none swept for them.

Falsifiable: the inventories could have come back with every item read by a production consumer.

## Method

Six read-only sub-agents worked on one pinned worktree (no tracker, git writes, smart-test or corpus runs), one per
scope: dataclass fields, meta keys, knobs and catalogue keys, functions, consumer-side vocabulary subsets, and the
keep-with-trigger verdicts of every recorded audit. Each used an AST walk over the six Step-4.5 emit/read shapes, not
grep alone, and classified each reader as DISPLAY / PRESENCE / BRANCH. A serialised field counted as read only if
something consumes the serialised output. The orchestrator re-checked the highest-impact claims in the pinned tree
(the SYNTHETIC_FRAMEWORK_ROLES drift, the overlay stdlib_other hole, _COVERAGE_CALL_EDGE_TYPES, the field
verdicts it spot-checked). The full per-item rows, scripts and raw output are in the lab notebook,
`~/hypergumbo_lab_notebook/kosup_reader_census_10282026/` (A_fields.md, B_meta.md, C_knobs.md, D_functions.md,
E_subsets.md, F_triggers.md); this document is the verdict summary.

Verdicts: DELETE (no reader, none intended) · WIRE (a reader is needed and missing) · DECLARE EXTERNAL (written for
an outside consumer) · KEEP WITH A TRIGGER THAT CAN FIRE · OK.

## Headline numbers

| Scope | Inventoried | Non-OK | Main finding |
|---|---|---|---|
| Dataclass fields (24 classes) | 229 fields | 9 DELETE, 4 WIRE, 33 DECLARE EXTERNAL | 8 free-text axis declarations are false (a consumer branches) |
| Meta keys | 203 registered, 348 emitted | 115 registered keys with no production reader; 157 emitted unregistered | readers keyed to keys/values nothing writes (6 sites) |
| Knobs | 193 CLI flags, 16 env vars, 2 config files, 9 YAML loaders | 21 (K1-K21) | 5 catalogue loaders silently ignore unknown keys; an overlay's stdlib_other is honoured |
| Functions | 5,039 module-level | 11 uncalled, 7 collision-hidden, 44 behind test-only roots | the ratchet matches by bare name |
| Vocabulary subsets | 269 hand-written literals | none declared complete | ~20 core ones look wrong today |
| Keep-with-trigger verdicts | 44 | 9 fired and not acted on, 19 unwatched, 4 unfireable | nothing evaluates a trigger |

## Verdicts and actions

Every action names its tracker item.

### Fields

- DELETE: Symbol.quality; AnalysisRun.skipped_passes; Limits.test_files_excluded; SupplyChainLimits.ambiguous_paths
  and AmbiguousPath's three fields; IoBoundaryCatalog._by_qualified and .stdlib_provenance; and 25 run.duration_ms
  writes onto runs that are then discarded — **WI-givar**.
- WIRE: Edge.evidence_lang (ADR-0040's confidence input), Symbol.display_label (cmd_explain), AnalysisRun.started_at
  (left "" by 10 analyzers), AnalysisRun.cpu_ms — **WI-popoj**.
- False free-text declarations (a BRANCH reader exists): UsageContext.position, IoChain.primitive,
  UsageContext.context_name, Symbol.name, Symbol.path and three more — **WI-supak**.
- DECLARE EXTERNAL (33): the serialised `limits` object except skipped_passes, Symbol.stable_id / fingerprint /
  is_example_file / is_config_file, the boundary roll-ups (whose docstring wrongly says taint reasons with them); 7
  already say so in code. Rows in A_fields.md.
- Prior instances, current state: origin_run_signature and analysis_tiers removed; run_signature and linker
  pass_version stamped, read externally only; directness now read (supply-chain summary) and stamped on declarations
  (INV-fuhin); shape_id read by the repeat-finder view; limits.skipped_languages produced but unread (its "dead field"
  docstring at verify_claims.py is stale); TaintSink.trust_level read via TaintSink.trusted.

### Meta keys

- DECISION, replacing the 2026-10-21 unfireable keep: Edge.meta `call_type` and `method` — DELETE; every writer's
  value is already on the edge or its source symbol — **WI-dikum** (with `export_scope`, the cobol meta clobber, and a
  read-side gate pinning the 115 write-only keys as the fireable form of their DECLARE EXTERNAL verdict).
- Readers keyed to keys or values nothing writes: SYNTHETIC_FRAMEWORK_ROLES lacks the IPC linkers' f-string
  ipc_send / ipc_receive — **WI-pokuz**; datamodels parent_bases, grpc extends, entrypoints imports, the CLI's
  pre-fold call-kind counts, bakeoff-broad's framework filter — **WI-tovoz**.
- WI-mujug: `receiver` OK (11 values, readers match producers, partition-tested); `resolution_quality` WIRE (declare
  its 7-value set with a partition test; three "typed" spellings no reader distinguishes) — recorded on **WI-mujug**.
- 33 keys written on both Symbol.meta and Edge.meta, registered on one axis — input recorded on **WI-kuhoh**.

### Knobs

- Unknown keys silently ignored by the io_primitives, function_summaries, library_signatures, frameworks and
  dataflow_patterns loaders; frameworks `name_pattern` / `usage_call` / `detect` / `linkers` accepted but unread
  (the two patterns match nothing) — **WI-hapon**.
- An overlay's `stdlib_other` is honoured and suppresses external_potential chains, though overlays are stripped of
  stdlib_modules on purpose (ADR-0061 ruling 2 risk; verdict effect not measured) — **WI-pofal**.
- The rest (taint-loader language keys and string rows, a transforms string row exiting rc 1, allowed_sanitizers
  unread, function-summary fields unread, unconstructed config classes, routes -x, undocumented env vars,
  --budgets) — **WI-ditom**. config.toml / .hypergumbo.toml: OK (unknown keys refused, every key read).

### Functions

- 11 uncalled, 7 hidden by bare-name collisions, 44 reached only through test-only roots, the textconv_main false
  positive, and two hand-copied sketch tables — **WI-nitug**.
- WI-fomif: DELETE (bakeoff-deep's claims_* fields have no branching reader and their input was never produced) —
  recorded on **WI-fomif**.
- verify_claims.classified_yet_starved (added 2026-10-28, WI-bobuf) had no production caller; wired into the
  starvation reason in the PR that carries this document.

### Vocabulary subsets (scope D)

- The gate, a declared complete partition for every consumer-side subset of a registered vocabulary — **WI-tilav**.
- The concrete probable misses (tag_io_boundaries' default edge types, _COVERAGE_CALL_EDGE_TYPES without
  instantiates, inherited_calls' kinds, owner_ancestry's KeyError, ABSTRACT_KIND_LANGUAGES stale, five silent
  defaults, the _merge_caveat comment) — **WI-hogon**.
- Known instances, current state: WI-sibup, WI-punod, WI-zakil, WI-totam fixed; WI-sojok's all-zones gate lands in
  the same PR as this document.

### Keep-with-trigger verdicts

- Nine fired and not acted on (the `redirect` sentinel, UsageContext.kind's fifth value, supply-chain tier 4,
  Edge.confidence_source, ADR-0024's refactor trigger, pass-silence Pair B, io catalogue status, and two now handled
  elsewhere) — **WI-daruz**.
- Nothing evaluates a trigger; 19 are unwatched, the cited watchers do not watch, and the playbook's Step 6 still
  offers the unfireable "a new consumer arriving" form — **WI-vihul**.

## Not determined

Outside consumers of the 33 DECLARE EXTERNAL fields and the write-only meta keys; whether the stdlib_other hole moves
a verdict; corpus-dependent triggers (ADR-0052, ADR-0054 T1/T2, ADR-0055 T1/T2, ADR-0059, the is_test volume
threshold); methods and nested functions (only module-level functions were censused); the cfg_nodes / url_folding
loaders, the trust store, runtime_coherence's allowlist and the tracker's config.yaml.
