<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0030: PROV vocabulary mapping for behavior map provenance

- Status: Accepted
- Date: 2026-05-27
- Supersedes: —
- Superseded by: —
- Related: INV-dubam (META: PROV-compliant provenance), INV-rukor (Edge.derived_from), INV-jidat (origin scalar→list), INV-hujog (Pass.depends_on)

## Context

Behavior maps capture the results of analysis (symbols and edges) and which passes ran (analysis_runs), but until recently did not capture *causation* — how passes produced their results. Three distinct gaps existed:

1. **Derivation**: given an Edge, which Symbols did the linker consume to construct it? Answerable only by re-reading linker source code.
2. **Attribution**: when evidence for a Symbol or Edge is contributed by multiple passes, which passes contributed? `origin` was a scalar — only one pass got credit.
3. **Process model**: pass dependencies were implicit in orchestrator code, not declared. Disabling a prerequisite analyzer silently degraded output.

The W3C PROV Data Model (PROV-DM, 2013) defines a standard vocabulary for provenance that maps naturally onto these gaps. This ADR documents how hypergumbo's provenance fields correspond to PROV concepts, providing a conceptual anchor for the implementation and a reference for consumers of behavior maps.

## Decision

We adopt the following mapping between PROV-DM vocabulary and hypergumbo's data model. The mapping is *conceptual* — we use hypergumbo-native field names, not PROV URIs. The intent is that any PROV-aware consumer can mechanically translate a behavior map's provenance fields into valid PROV assertions.

### Entity → Symbol, Edge

PROV **Entity**: "a physical, digital, conceptual, or other kind of thing with some fixed aspects."

In hypergumbo, Symbols and Edges are the entities. Each has a stable `id`, fixed structural properties (kind, language, path, span for Symbols; src, dst, edge_type for Edges), and provenance metadata.

| PROV concept | hypergumbo field | Type | Location |
|---|---|---|---|
| Entity identifier | `Symbol.id`, `Edge.id` | `str` | `ir.py` |

### Activity → Pass execution

PROV **Activity**: "something that occurs over a period of time and acts upon or with entities."

Each pass execution (analyzer, linker, infrastructure pass) is an Activity. The `analysis_runs[]` array in the behavior map records which passes ran, in what order, and with what timing.

| PROV concept | hypergumbo field | Type | Location |
|---|---|---|---|
| Activity identifier | `Pass.id` / `analysis_runs[].pass_id` | `str` | `catalog.py`, output JSON |
| Activity timing | `analysis_runs[].duration_ms` | `int` | output JSON |

### Agent → hypergumbo (the tool)

PROV **Agent**: "something that bears some form of responsibility for an activity taking place."

The hypergumbo CLI is the Agent. The `tool_version` field in the behavior map header identifies which version ran. In multi-tool pipelines, each tool would be a distinct Agent; today there is only one.

| PROV concept | hypergumbo field | Type | Location |
|---|---|---|---|
| Agent identifier | `tool_version` | `str` | output JSON header |

### wasAttributedTo → Symbol.origin, Edge.origin

PROV **wasAttributedTo**: "ascribing an entity to an agent" (or, in our extended usage, to an activity).

`Symbol.origin` and `Edge.origin` are `list[str]` (INV-jidat, schema 0.10.0+). Each element is a pass ID that contributed evidence to the entity's existence. Order is chronological: the originating pass first, then each subsequent pass that refined or confirmed.

| PROV concept | hypergumbo field | Type | Semantics |
|---|---|---|---|
| wasAttributedTo | `Symbol.origin` | `list[str]` | Pass IDs that created/contributed to this Symbol |
| wasAttributedTo | `Edge.origin` | `list[str]` | Pass IDs that created/contributed to this Edge |

Single-element lists are valid and common (most entities are produced by exactly one pass). Multi-element lists arise when a linker extends or refines evidence from an analyzer.

### wasDerivedFrom → Edge.derived_from

PROV **wasDerivedFrom**: "a transformation of an entity into another, an update of an entity resulting in a new one, or the construction of a new entity based on a pre-existing entity."

`Edge.derived_from` is `Optional[list[str]]` (INV-rukor, schema 0.9.1+). When populated, it lists the Symbol IDs (or Edge IDs, for second-order linker chains) that the producing pass consumed as inputs to construct this Edge.

| PROV concept | hypergumbo field | Type | Semantics |
|---|---|---|---|
| wasDerivedFrom | `Edge.derived_from` | `Optional[list[str]]` | Symbol/Edge IDs consumed to construct this Edge |

Typical derivation inputs for a linker-produced edge:
- The source Symbol (`Edge.src`)
- The destination Symbol (`Edge.dst`)
- Any intermediate Symbol consulted during resolution (e.g., a file Symbol used for relative import path resolution)

Analyzer-originated edges have `derived_from = None` because the derivation is the source code itself, not other behavior-map entities.

### wasInformedBy → Pass.depends_on

PROV **wasInformedBy**: "communication of an entity from a completing activity to a started activity."

`Pass.depends_on` is `list[list[str]]` in Conjunctive Normal Form (INV-hujog, WI-dilab). Each inner list is a disjunctive clause: at least one pass in the clause must be active. All clauses must be satisfied for the pass to run.

| PROV concept | hypergumbo field | Type | Semantics |
|---|---|---|---|
| wasInformedBy | `Pass.depends_on` | `list[list[str]]` (CNF) | Pass-ID prerequisites — each clause is a disjunction, all clauses must hold |

Example: `depends_on=[["java"], ["c", "cpp", "rust"]]` means "requires java AND (c OR cpp OR rust)". `validate_pass_dependencies()` checks this at configuration time.

### Summary table

| PROV-DM concept | hypergumbo concept | Field(s) | Introduced in |
|---|---|---|---|
| Entity | Symbol, Edge | `id` | original |
| Activity | Pass execution | `Pass.id`, `analysis_runs[]` | original |
| Agent | hypergumbo tool | `tool_version` | original |
| wasAttributedTo | Multi-pass attribution | `Symbol.origin`, `Edge.origin` (`list[str]`) | INV-jidat (schema 0.10.0) |
| wasDerivedFrom | Derivation chain | `Edge.derived_from` (`Optional[list[str]]`) | INV-rukor (schema 0.9.1) |
| wasInformedBy | Pass dependencies | `Pass.depends_on` (`list[list[str]]` CNF) | INV-hujog (WI-hupaz/WI-dilab) |

### Surfacing in the CLI

`hypergumbo explain <symbol>` surfaces provenance:
- **Always**: `Origin:` line shows which passes created the queried symbol. Edge type annotations on caller/callee lines.
- **`--provenance`**: additionally shows `Derived from:` for each edge, resolving Symbol IDs to human-readable `name (kind)` pairs.

## Consequences

1. **Queryable derivation graph**: consumers can answer "why does this edge exist?" by walking `derived_from` to the contributing Symbols, without re-reading linker source code.
2. **Multi-pass credit**: `origin` as a list prevents lossy single-attribution when evidence crosses pass boundaries.
3. **Config-time validation**: `Pass.depends_on` in CNF enables `validate_pass_dependencies()` to catch missing prerequisites before analysis runs.
4. **PROV interop**: any tool that understands PROV-DM can mechanically translate a behavior map's provenance fields into valid PROV assertions using this mapping.
5. **No PROV serialization**: we do NOT emit PROV-N or PROV-JSON natively. The mapping is conceptual. A future `hypergumbo export --format prov-json` could use this ADR as its specification.
