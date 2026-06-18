<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0022: Per-Language Configuration Surface and Language Profile Registry

Date: 2026-04-10
Status: Partially superseded by its own evolution — by-CATEGORY discoverability landed (yaml_catalogs.py + scripts/yaml-catalog-index, commit c00ec84615); by-LANGUAGE LanguageProfile half DEFERRED/UNIMPLEMENTED — see Status Update 2026-05-13

## Context

### The proliferating per-language config surface

hypergumbo has accumulated several per-language YAML configuration surfaces, each loaded by a different module:

| Config surface | Location | Consumers | Purpose |
|---|---|---|---|
| `dataflow_patterns/<lang>.yaml` | `hypergumbo-core/src/hypergumbo_core/dataflow_patterns/` | `dataflow.py`, analyzers | Access-mode rules (`read`/`write`/`mutate`) for library methods |
| `io_primitives/<lang>.yaml` | `hypergumbo-core/src/hypergumbo_core/io_primitives/` | `io_boundary.py`, `catalog.py` | IO primitive → boundary type mapping |
| `function_summaries/<lang>.yaml` | `hypergumbo-core/src/hypergumbo_core/function_summaries/` | `taint.py`, analyzers | Function taint/summary rules |
| `frameworks/<lang>.yaml` | `hypergumbo-core/src/hypergumbo_core/frameworks/` | `framework_patterns.py` | Framework detection patterns |

In addition, INV-dihos (ADR-0006 §Return-Type Registry Pre-Pass) adds a `signatures/<lang>.yaml` surface, and the proliferation raises the question of per-language discoverability for these rules.

Each analyzer currently reads its own YAML directly. This creates a distributed contract: when a contributor wants to "add a new rule for Kotlin", they must figure out which YAML file in which directory is the right one, then verify that the analyzer actually loads it.

### The discoverability problem

A developer adding a new language or extending an existing one has no single place to learn "what config surfaces apply to this language?". The rules are scattered across four directories with no index.

For cross-cutting work like INV-dihos Phase 1-5 (return-type registry rollout across 8+ languages), the scatter is especially painful — each phase touches a different analyzer, each reading its YAML in a slightly different way.

## Decision

DEFERRED design — not implemented; see Status Update (2026-05-13); original: git show 5163551892:docs/adr/0022-language-profile-registry.md

## Status Update (2026-05-13)

Commit c00ec84615 landed `hypergumbo_core.yaml_catalogs` + `scripts/yaml-catalog-index` + an auto-generated "YAML Catalogs" section in `docs/ARCHITECTURE.md`. This addresses the discoverability problem in §Context from a **perpendicular axis** to the original proposal:

- **Landed (by-category).** One `CatalogSpec` per directory (`frameworks/`, `dataflow_patterns/`, `io_primitives/`, `cfg_nodes/`, `taint_sources/`, `taint_sanitizers/`, `function_summaries/`), each naming its loader module and governing ADR. `scripts/yaml-catalog-index --check` exits non-zero on registry-vs-filesystem drift. The architecture document picks up new catalogs automatically via the same registry.
- **Still deferred (by-language).** The `LanguageProfile` dataclass, `load_profile(language)` registry, runtime analyzer migration, and `scripts/generate-language-reference` per-language markdown emitter described in §Decision and §Migration Path remain unimplemented.

The two halves are complementary, not duplicate. yaml_catalogs.py answers "what catalog directories exist; who loads each; what ADR governs each?". LanguageProfile would answer "what rules apply to language X?". They index the same data along orthogonal axes.

**Decision to defer (not decommission).** No analyzer currently has a concrete pain point that "merged per-language config object" would solve — each module loads its own YAML cleanly, and the by-category drift-detection covers the operational hazard the original ADR was responding to. The by-language registry remains a sensible addition if a future contributor onboarding workflow or analyzer integration concretely calls for it. Trigger conditions to revisit:

1. A contributor adding a new language stalls because they cannot find which YAML files to author.
2. An analyzer or other consumer wants a merged-config view that can't be cheaply assembled from the existing direct loaders.
3. INV-dihos Phase 1+ (return-type signatures across languages) reaches a scale where ad-hoc per-language loading is repetitive enough to motivate a unified surface.

Until one of those triggers fires, do not implement the runtime aggregation half — it would add a layer with no consumer demanding it.

## Consequences

### Positive

- **Single discovery point.** `load_profile("kotlin")` returns everything hypergumbo knows about Kotlin. The by-language discoverability concern is resolved.
- **Contribution surface is pure YAML.** External contributors can add rules without understanding Python.
- **Cross-cutting rollouts become tractable.** INV-dihos Phase 1-5 can be framed as "populate `profile.signatures` for each language" with one interface to implement per language.
- **Docs generation becomes mechanical.** Auto-generated per-language reference closes the contributor-onboarding gap independently of the runtime registry.

### Negative

- **Migration cost.** Each analyzer's YAML loading path must be refactored to consume `profile.<section>` instead. Because analyzers are independent, this can be incremental — start with one section (dataflow_patterns) and one language (Python), ship, measure, then expand.
- **One more abstraction layer.** The profile is a thin wrapper, but every new developer must learn it. Offset by: the thing it wraps is already confusing (four YAML directories), so a consolidating abstraction is a net win.
- **Schema drift risk.** If one section's YAML schema changes, the LanguageProfile dataclass must update too. Mitigated by YAML validation tests per section.

### Neutral

- **No runtime overhead.** Loading and merging YAML is a one-time startup cost, same as today.
- **Backward compatibility.** Analyzers can continue reading YAML directly during migration — the registry is additive, not breaking.

## Migration Path

DEFERRED design — not implemented; see Status Update (2026-05-13); original: git show 5163551892:docs/adr/0022-language-profile-registry.md

## Alternatives Considered

### Alternative 1: Status quo (scattered YAML)

Keep each section loading its own YAML. Add an index document (`docs/FRAMEWORKS.md`-style) that maps language → applicable sections.

Rejected because: the index document itself becomes a discoverability burden, and it does not solve the cross-cutting rollout problem (INV-dihos Phase 1-5).

### Alternative 2: One YAML file per language

Collapse all per-language config into a single `languages/<lang>.yaml` file containing all sections as subkeys.

Rejected because: the sections have different schemas and different ownership (IO primitives are ADR-0016, dataflow is ADR-0015, taint is ADR-0017). Merging them into one file blurs responsibility. The profile registry gives unified access without colocating the authoring.

### Alternative 3: Python modules per language (no YAML)

Implement per-language config as Python modules under `languages/<lang>.py`. Each module exports a `PROFILE` instance.

Rejected because: YAML is explicitly designed for external contribution without touching Python (AGENTS.md "contribution surface" principle). Python modules would gate contributions behind Python competency.

## Open Questions

DEFERRED design — not implemented; see Status Update (2026-05-13); original: git show 5163551892:docs/adr/0022-language-profile-registry.md

## References

- WI-rumim (this ADR)
- ADR-0006 (Variable Type Inference — §Return-Type Registry Pre-Pass is a future registry consumer)
- ADR-0015 (Dataflow Access Modes — defines `dataflow_patterns` section)
- ADR-0016 (IO Boundary Analysis — defines `io_primitives` section)
- ADR-0017 (Taint-Zone Dataflow — defines `function_summaries` section)
