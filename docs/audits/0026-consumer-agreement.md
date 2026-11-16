<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# 0026 — Consumer agreement: facts two consumers derive separately, and where they disagree

```yaml
kind: consumer_agreement
date: 2026-10-30
pinned_tree: a17367f254
trigger: WI-borib (input filed for the next Fundamental Concept Audit, from the 2026-10-25 critique of WI-kosup's method)
outcome: confirmed
```

## Suspicion

hypergumbo's views each derive the facts they report — is this test code, is it reached, how many files, which boundary
does this call cross — from their own reading of the IR, with their own filters and defaults, so two consumers can give
the same user two answers to one question. Every earlier instance (WI-jufih, WI-zulij, INV-hokig, INV-pazur, WI-punod,
WI-bobuf) was found by its symptom and fixed alone.

Falsifiable: each fact could have had one deriving helper, or separate derivations that agree on real output.

Out of scope, as WI-borib states: agreement between two fields of one record (spec_validator's cross-field class), a
declared axis's axiom on output (WI-ziruv), and hand-written subsets of a registered vocabulary (WI-tilav).

## Method

Four read-only sub-agents inventoried one family each on the pinned tree — reachability, ranking and scope, counts and
pass disclosure, classification and tier — finding derivations by AST walk over call sites and field assignments, not
grep alone, and recording for each consumer its file:line, whether it calls a shared helper, and its inputs, filters and
defaults. They reproduced disagreements on constructed maps and the schema-coverage fixtures, including non-default
configurations (`--max-file-bytes`, `--first-party-only`, user overlays, `--no-default-overlays`).

The orchestrator measured the separate pairs on one default survey per language family — mitmproxy (Python), knex
(JavaScript), logrus (Go), ripgrep (Rust), jedis (Java) — read every disagreement it counted against source, re-checked
the highest-impact code claims in the pinned tree (slice's catalogue load, dead-code's Symbol rebuild, `rank_files`'
dampeners, `derive_silence_reason`, knex's duplicate ids), and re-took WI-vosop's live-vs-harness measurement. Per-family
inventories, scripts and raw output: `~/hypergumbo_lab_notebook/borib_consumer_agreement_10302026/` (A_reachability.md,
B_ranking_scope.md, C_counts_disclosure.md, D_classification_tier.md, M/NOTES.md, vosop/FINDINGS.md).

Verdicts, per WI-borib: SINGLE-SOURCE (route every consumer through one helper) · PIN (keep the derivations, add a
property test keyed on the contradiction, as `verify_claims.classified_yet_starved` is) · RENAME (they answer different
questions; each output says which) · KEEP WITH A TRIGGER THAT CAN FIRE · OK.

## Verdicts

17 facts: 12 derived separately, 5 through one helper (three of those with side paths that disagree). Disagreement was
measured on real output for 7 of them; the rest are reproduced on constructed maps or fixtures, or read from code, as
each row says.

| Fact | Derivations | Disagreement found | Verdict | Item |
|---|---|---|---|---|
| Is node N test code | 5 predicates: `is_test_file`, `is_test_path`, `is_test_node` (annotations), a path-or-concept rule written twice, and the narrow `supply_chain.is_test_file`; scripts carry private regexes | The path predicates agree on every path of all 5 repos. Annotations split them: ripgrep's 496 `#[test]` nodes in `src/` are test to slice, io-boundaries and ranking, and production to dead-code (434 of its 817 candidates) and test-coverage (464 cold spots; 493 of its 2,680 "production functions") | SINGLE-SOURCE | WI-dibuf |
| Is F reached by tests | dead-code walks calls, instantiates, `dispatches_to`, wraps; test-coverage and sketch walk calls; the ratchet one hop | 12 functions test-only to dead-code with 0 tests to test-coverage (mitmproxy 4, knex 4, ripgrep 4), every one reached only by `dispatches_to`; 0 the other way | SINGLE-SOURCE the edge set, PIN the contradiction | WI-mofaz |
| Is X dead | `cmd_dead_code_maybe` only | inherits the rows above and below | OK | — |
| Is X an entry point | `detect_entrypoints` shared; dead-code feeds it hand-rebuilt Symbols without `supply_chain` / `modifiers`; compact and the tiered view order by detection `confidence`, the rest by `rank_score` | constructed map: a tier-3 export is a dead-code seed only | SINGLE-SOURCE | WI-misin, WI-zuroj |
| The top-N symbols | `rank_symbols`, `key_symbols` + `compute_dampened_centrality`, `rank_files`, the tiered views, compact's seed count, script rankers | `rank_files` lacks two of seven dampeners under a "same pipeline" comment; tiered views credit test-sourced edges; three views rank three populations and none says which (constructed maps only) | SINGLE-SOURCE; RENAME the populations | WI-zuroj, WI-kojof |
| A symbol's degree | `symbols` filters on `confidence` and orders on `rank_score`; explain keeps all; centrality weights in- but not out-degree | fixture: `symbols` Out 0, `explain` 1 call | SINGLE-SOURCE | WI-zuroj |
| Files of language L | profile extension globs; each analyzer's finder; the sketch Overview's own walk; two bakeoff scripts' extension maps | mitmproxy css 62 profiled / 30 analysed; logrus go 47 / 46; the sketch Overview's test-file count 355 vs `profile.test_files` 392 (offline reproduction); Wolfram `.m` never analysed | SINGLE-SOURCE | WI-firaf, WI-siruf |
| Symbols / edges of L | `metrics` recomputed from each view's arrays (shared); tiered `nodes_summary` pinned (INV-pazur) | compact's `metrics.languages` counts the compacted subset under the survey's name (ripgrep 100 vs 5,163 nodes) and loses `discovery_language` (mitmproxy `unknown: 1`); knex 3 file-node ids emitted twice and counted twice | RENAME; SINGLE-SOURCE the projection and the ids | WI-kojof, WI-firaf |
| Did pass P run, and why not | `pass_silence` shared for the vocabulary and stderr; four readers re-derive | a pass whose every file the size cap dropped reads `no_candidate_files`; a false "llvm_ir analyzer not installed"; two config-language lists disagree; `files_skipped` has four meanings (logrus flagged partial for one symbol-less file) | SINGLE-SOURCE | WI-nifog |
| Is language L present | profile taxonomy; analyzer dispatch; verify-claims' node census | analysed pony, scss and gitignore have no profile key; verify-claims' two censuses agree on 4 surveys | SINGLE-SOURCE (with file counts) | WI-firaf |
| Which boundary call C crosses | `named_lookup_arm` shared by io-boundaries and taint (INV-foda holds); taint differs around it; slice assembles its own catalogue | 18 of 1,488 edges: taint ignores `io_target_kind` (knex `>` to `/dev/null`, logrus `Fprintf` into a buffer), matches a resolved in-tree `writeFile` by name, and reads knex's `process.on('exit')` handlers as `untrusted_input`; os.urandom / Fernet.decrypt taint-only labels are a different question. Fixtures: slice ignores user overlays; verify-claims builds no `external_potential` | PIN, fix what it finds; SINGLE-SOURCE the catalogue | WI-zavot, WI-tobit |
| Is module M classified / starved / untraced | io-boundaries `untraced_modules` vs verify-claims `_adjudicate_external_modules`; `classified_yet_starved` already pins one pair (WI-bobuf) | with an overlay: io counts a community row as classified, verify-claims does not; caller- vs callee-language keying | SINGLE-SOURCE; RENAME where the questions differ | WI-tobit |
| Does a zone have sinks for L | `taint.builtin_sink_zones` + `_sink_zone_gaps`, shared | community sinks count as coverage: Haskell `network` is covered by 28 community rows only, so default and `--no-default-overlays` judge one claim differently | decide against ADR-0061 | WI-mizog |
| A node's tier | `supply_chain.classify_file`, shared; side paths | a `--first-party-only` map holds 5 tier-3 boundary nodes; profile `own_files` counts files the map calls tier 4 | PIN | WI-nolud |
| Stdlib / third-party / in-tree | `module_ecosystem` shared (WI-pogar); the node stamp falls back to the manifest, verify-claims does not; only `python.yaml` lists stdlib modules | code: Go/JS dependencies `third_party` in io-boundaries while verify-claims' untraced-dependency clause never fires; a pyproject `dataclasses` declaration `third_party`, its import `stdlib` | SINGLE-SOURCE | WI-tobit |
| Which frameworks the repo uses | one refined `profile.frameworks` | linker activation names four frameworks detection never emits (`spring`, `spring-mvc`, `openapi`, `protobuf`) | OK for the fact; the activation is a reader keyed to nothing | WI-vigap |
| Backend agreement's edge rows | the recorded harness (0019) vs a live survey | harness 93 / 19 / 7 vs live 92 / 17 / 8 on calls/in-repo, every edge named: a self-loop dedup flag the harness lacked, the survey's file nodes, and an order-dependent Rust binding | SINGLE-SOURCE the dedup; state the rest in 0019 | WI-vosop (closed by this audit's PR), WI-lirik |

## Silent bugs found on the way

- The Rust analyzer binds a call to a `use`-imported function by discovery order: under a survey's sorted order,
  aardvark-dns `serve.rs:287` reaches a `#[cfg(test)]` helper, not `config/mod.rs`; and receiver or path calls bind to
  the enclosing same-named function, which the survey then drops as a self-loop — WI-lirik.
- Wolfram `.m` files are never analysed — WI-siruf.
- 0019's pairing and edge rows went unguarded and stale (269 `references`, a pre-WI-bamar `type_alias` row); its guard
  now checks every row against the instrument (this audit's PR).

## Not determined

Real-repo magnitudes for every row marked "constructed map", "fixture" or "code" above; configurations beyond the
fixtures' (overlays, `--max-tier`, claims files) on real repos; a Haskell repo for the zone gate; the internals of
sketch's two-phase selection, slice traversal and the taint walks; linkers and the language packages' own test
detection (ruby.py, jvm_deps.py); whether knex's duplicate ids recur elsewhere; the opt-in scip-python backend.
