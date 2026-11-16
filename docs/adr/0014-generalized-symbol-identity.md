<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0014: Generalized Symbol Identity (stable_id / shape_id) — RETIRED

Status: Superseded by ADR-0035 (2026-11-15); stub, its other rules are in the spec and `vendor-sync.md` (table below)
Date: 2026-02-20

> **RETIRED — nothing in this file is a rule.** ADR-0014 decided that every
> analyzer computes `stable_id` and `shape_id` through shared machinery: a
> generic tree-sitter structure hash, an untyped and a typed `stable_id` tier,
> route and entry-point formulas, per-kind backstop factories, and a
> grammar-version stability contract. Its `stable_id` contract ("survives
> renames and file moves") and its hash formulas were replaced by
> [ADR-0035](0035-stable-id-v6-identity-contract.md); the rest now lives in
> the documents below, stated as the code implements it.

| ADR-0014 section | Where the rule is |
|---|---|
| Status line (coverage census) | [`docs/hypergumbo-spec.md`](../hypergumbo-spec.md) §6 "Identity field semantics": the typed-tier and `shape_id` Coverage bullets |
| §1 `shape_id` (tree-sitter walker; Python `ast` hash) | spec §6 "Identity field semantics", `shape_id`; `TreeSitterAnalyzer.compute_shape_id` docstring |
| §2 `stable_id` untyped tier | [ADR-0035](0035-stable-id-v6-identity-contract.md) §1; formula in spec §6 and `analyze/base.py::assemble_stable_id` |
| §3 `stable_id` typed tier, language-scoped normalization | spec §6 "Identity field semantics", typed tier; ADR-0035 §1 for the `name` / `qualified_name` inputs |
| §4 route and entry-point formulas | ADR-0035 §3 |
| §5 containing-scope recursion | ADR-0035 §1 |
| §5a `class_body_sig` | ADR-0035 §2 |
| §5b kind factories and the backstop's precedence | spec §6 "Identity field semantics", backstop; ADR-0035 §1 |
| §6 grammar version stability | [`docs/grammars/vendor-sync.md`](../grammars/vendor-sync.md) "shape_id and grammar versions"; spec §6 "Identity and provenance scheme versioning" |
| Scheme bumps v1 → v5 | spec Appendix C "`stable_id_scheme` version history" |

Original text: `git show 79c203145b:docs/adr/0014-generalized-symbol-identity.md`.

This stub is permanent so that references to "ADR-0014" stay resolvable.
