<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Backend agreement — aardvark-dns-4444d90fee-two-arm-recorded.json

| | |
|---|---|
| **Axis** | backend agreement (ADR-0057 §5, §10 — the only evidence that may change an arbitration default or license an `authoritative_for` declaration) |
| **Date** | 2026-09-19 |
| **Artifact** | `aardvark-dns-4444d90fee-two-arm-recorded.json` |
| **Instrument** | `hypergumbo backend-agreement` (`hypergumbo_core.backend_agreement`) |
| **Outcome** | Measurement, not a verdict: agreement says a value is uncontested, not that it is right. |

```yaml
kind: backend_agreement
artifact: aardvark-dns-4444d90fee-two-arm-recorded.json
date: 2026-09-19
languages:
  - language: rust
    producers: [rust, rust_analyzer]
    nodes: 169
    merged_nodes: 148
    corroborated_edges: 93
    superseded_edges: 3
```

## rust: `rust` (incumbent) vs `rust_analyzer`

169 records from these producers, 148 folded by the merge pass.

### Pairing by kind

| kind | paired | `rust` only | `rust_analyzer` only |
|---|---|---|---|
| `enum` | 3 | 0 | 0 |
| `field` | 38 | 0 | 0 |
| `function` | 52 | 0 | 0 |
| `method` | 27 | 0 | 0 |
| `namespace` | 0 | 0 | 18 |
| `struct` | 10 | 0 | 0 |
| `trait` | 1 | 0 | 0 |
| `type_alias` | 0 | 0 | 2 |
| `variable` | 17 | 0 | 1 |

### Per-attribute agreement on paired records

| attribute | agree | disagree | `rust` only | `rust_analyzer` only | disagreement shapes (carried ← alternative x count) |
|---|---|---|---|---|---|
| `docstring` | 0 | 0 | 63 | 0 | — |
| `is_exported` | 107 | 41 | 0 | 0 | `null` ← `false` x41 |
| `kind` | 133 | 15 | 0 | 0 | `variable` ← `constant` x15 |
| `modifiers` | 0 | 0 | 35 | 0 | — |
| `name` | 83 | 65 | 0 | 0 | `AardvarkError::from` ← `from` x3; `Opts::config` ← `config` x1; `Opts::port` ← `port` x1; `Opts::filter_search_domain` ← `filter_search_domain` x1; `Opts::subcmd` ← `subcmd` x1; `SubCommand::Run` ← `Run` x1; `SubCommand::Version` ← `Version` x1; `AardvarkError::Message` ← `Message` x1 |
| `qualified_name` | 0 | 0 | 148 | 0 | — |
| `signature` | 0 | 0 | 125 | 0 | — |
| `span` | 2 | 146 | 0 | 0 | `5:0-30:1` ← `5:3-5:7` x1; `12:0-25:1` ← `12:7-12:11` x1; `15:4-15:26` ← `15:4-15:10` x1; `18:4-18:21` ← `18:4-18:8` x1; `21:4-21:40` ← `21:4-21:24` x1; `24:4-24:22` ← `24:4-24:10` x1; `28:0-33:1` ← `28:5-28:15` x1; `30:4-30:17` ← `30:4-30:7` x1 |
| `stable_id` | 0 | 134 | 0 | 14 | (hash derived from the attributes above) |

### Edge overlap

| edge type | target | both | `rust` only | `rust_analyzer` only |
|---|---|---|---|---|
| `calls` | in-repo | 93 | 18 | 7 |
| `calls` | external | 0 | 244 | 0 |
| `decorated_by` | external | 0 | 1 | 0 |
| `implements` | external | 0 | 2 | 0 |
| `module_attr_ref` | external | 0 | 26 | 0 |
| `references` | in-repo | 0 | 0 | 269 |

Corroborated edges (two distinct pathways, ADR-0057 §13): 93. Superseded external stubs (§14): 3.

## Reading

Written by the agent on 2026-09-19 from the tables above; the numbers are the instrument's, this section is a reading of them and is preserved verbatim by `scripts/regenerate-backend-agreement-table`. Nothing here changes a default: ADR-0057 §5 is changed by citing this table in a later decision, not by this document.

- **Pairing.** All 148 pairs are two-sided; nothing is `rust`-only. The 21 `rust_analyzer`-only records are the 18 module namespaces, two `type` aliases and one `static` the tree-sitter arm emits no Symbol for (WI-bamar). An `authoritative_for` claim is only meaningful on paired records, so the SCIP arm's coverage advantage here is a *pairing* fact, not an agreement one.
- **`kind`: 133 agree, 15 disagree, every disagreement `variable` ← `constant`.** The alternative is right (a Rust `const` is a constant) and the carried value is the WI-bamar producer gap in `rust.py`. Fix the producer (§7), not the default: after WI-bamar this row should read 148 / 0 with no exception declared.
- **`name`: 83 agree, 65 disagree, every one `Type::member` ← `member`** (38 fields, 27 methods). Both forms are true; the anchors' `name_key` folds them for pairing and incumbent-first keeps the qualified form as the scalar. Whether `name` should carry the leaf and `qualified_name` the qualified form is a vocabulary question for the owner, not a precedence exception.
- **`is_exported`: 107 agree, 41 disagree, every one `null` ← `false`.** The incumbent supplied no value and precedence carried its `None` over a concrete `False`. That is not a case for a per-attribute exception; it is the absent-versus-empty rule: an absent value is not a candidate. Filed as INV-huboz against the merge pass; after it lands this row should read 107 agree, 0 disagree, 41 one-sided to `rust_analyzer`.
- **`span`: 2 agree, 146 disagree, by construction** — the §10 role split (item vs identifier token); the two that agree are single-token items. Not a disagreement about the declaration.
- **`stable_id`: 134 disagree, 14 one-sided to `rust`.** The 134 follow from `span` and `name` (the id hashes them). The 14 one-sided are the 10 structs, 3 enums and 1 trait the SCIP arm emits without a `stable_id` at all — filed as WI-paluk against the translation.
- **One-sided attributes.** `qualified_name` (148), `signature` (125), `docstring` (63) and `modifiers` (35) come from the syntax arm only: the SCIP translation fills none of them although `SymbolInformation` carries signature documentation. No agreement can be measured on them until it does.
- **Edges.** The 93 in-repo `calls` both arms emit are exactly the corroborated edges (§13); 18 in-repo calls only tree-sitter sees, 7 only SCIP. The 269 `references` edges are SCIP-only (the syntax arm emits none), and every edge to an external stub — 244 `calls`, 26 `module_attr_ref`, 2 `implements`, 1 `decorated_by` — is tree-sitter-only, since the recorded index resolves nothing outside the crate. Three of the 244 stubs are superseded (§14).
