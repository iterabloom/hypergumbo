<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0022: Per-Language Configuration Surface and Language Profile Registry

Date: 2026-04-10
Status: Proposed (exploratory — no implementation)

## Context

### The proliferating per-language config surface

hypergumbo has accumulated several per-language YAML configuration surfaces, each loaded by a different module:

| Config surface | Location | Consumers | Purpose |
|---|---|---|---|
| `dataflow_patterns/<lang>.yaml` | `hypergumbo-core/src/hypergumbo_core/dataflow_patterns/` | `dataflow.py`, analyzers | Access-mode rules (`read`/`write`/`mutate`) for library methods |
| `io_primitives/<lang>.yaml` | `hypergumbo-core/src/hypergumbo_core/io_primitives/` | `io_boundary.py`, `catalog.py` | IO primitive → boundary type mapping |
| `function_summaries/<lang>.yaml` | `hypergumbo-core/src/hypergumbo_core/function_summaries/` | `taint.py`, analyzers | Function taint/summary rules |
| `frameworks/<lang>.yaml` | `hypergumbo-core/src/hypergumbo_core/frameworks/` | `framework_patterns.py` | Framework detection patterns |

In addition, INV-dihos (ADR-0006 §Return-Type Registry Pre-Pass) adds a `signatures/<lang>.yaml` surface, and WI-sukav raised the question of per-language discoverability for these rules.

Each analyzer currently reads its own YAML directly. This creates a distributed contract: when a contributor wants to "add a new rule for Kotlin", they must figure out which YAML file in which directory is the right one, then verify that the analyzer actually loads it.

### The discoverability problem

From WI-sukav (the item that motivated this ADR): a developer adding a new language or extending an existing one has no single place to learn "what config surfaces apply to this language?". The rules are scattered across four directories with no index.

For cross-cutting work like INV-dihos Phase 1-5 (return-type registry rollout across 8+ languages), the scatter is especially painful — each phase touches a different analyzer, each reading its YAML in a slightly different way.

## Decision

Introduce a `LanguageProfile` dataclass and `language_profile.py` registry module. The registry loads, merges, and exposes all applicable per-language rules as a single object. Analyzers consume the registry instead of reading YAML directly.

### LanguageProfile dataclass

```python
@dataclass(frozen=True)
class LanguageProfile:
    """Unified per-language configuration surface.

    All rules that apply to a language are loaded here and exposed as
    a single object.  Analyzers request the profile for their language
    and consume whichever sections apply.
    """
    language: str
    dataflow_patterns: DataflowPatterns | None
    io_primitives: list[IOPrimitive]
    function_summaries: dict[str, FunctionSummary]
    framework_patterns: list[FrameworkPattern]
    signatures: SignatureConfig | None  # INV-dihos Phase 1+
    test_detection: TestDetectionRules | None
    type_hierarchy: TypeHierarchyConfig | None
```

### Registry API

```python
def load_profile(language: str) -> LanguageProfile:
    """Load all applicable config surfaces for a language.

    Missing sections default to empty.  Unknown languages produce a
    profile with all sections None/empty.
    """
```

The profile is computed once per analysis run and cached. Analyzers receive it via their existing `AnalyzerContext` or equivalent.

### Consumers

**Runtime consumers (analyzers):**
- `dataflow.py` reads `profile.dataflow_patterns` instead of loading YAML directly
- `io_boundary.py` reads `profile.io_primitives` instead of loading catalog directly
- `taint.py` reads `profile.function_summaries` instead of loading YAML directly
- `framework_patterns.py` reads `profile.framework_patterns`
- Analyzers consuming INV-dihos signatures read `profile.signatures`

**Build-time consumers (docs generation):**
- A new `scripts/generate-language-reference` script walks the registry and emits one markdown page per language: "What does hypergumbo know about <language>?". This subsumes the WI-sukav P2 auto-generated reference page concern.

**Contribution surface:**
- Contributors add rules by editing the appropriate YAML file under `<section>/<language>.yaml`. They do not need to touch Python.
- The ADR defines the schema for each section in a dedicated reference page (auto-generated from dataclass docstrings).

## Consequences

### Positive

- **Single discovery point.** `load_profile("kotlin")` returns everything hypergumbo knows about Kotlin. WI-sukav discoverability concern resolved.
- **Contribution surface is pure YAML.** External contributors can add rules without understanding Python.
- **Cross-cutting rollouts become tractable.** INV-dihos Phase 1-5 can be framed as "populate `profile.signatures` for each language" with one interface to implement per language.
- **Docs generation becomes mechanical.** Auto-generated per-language reference subsumes the P2 concern from WI-sukav.

### Negative

- **Migration cost.** Each analyzer's YAML loading path must be refactored to consume `profile.<section>` instead. Because analyzers are independent, this can be incremental — start with one section (dataflow_patterns) and one language (Python), ship, measure, then expand.
- **One more abstraction layer.** The profile is a thin wrapper, but every new developer must learn it. Offset by: the thing it wraps is already confusing (four YAML directories), so a consolidating abstraction is a net win.
- **Schema drift risk.** If one section's YAML schema changes, the LanguageProfile dataclass must update too. Mitigated by YAML validation tests per section.

### Neutral

- **No runtime overhead.** Loading and merging YAML is a one-time startup cost, same as today.
- **Backward compatibility.** Analyzers can continue reading YAML directly during migration — the registry is additive, not breaking.

## Migration Path

Phase 1: Implement `LanguageProfile` + `load_profile()` with only the `dataflow_patterns` section populated. Migrate one analyzer (Python `dataflow.py`) to consume it. Validate against bakeoff.

Phase 2: Populate the remaining sections (`io_primitives`, `function_summaries`, `framework_patterns`) in the dataclass and migrate their analyzers one at a time.

Phase 3: Integrate INV-dihos `signatures` as a LanguageProfile section. This unblocks Phase 5 of INV-dihos (cross-linker integration).

Phase 4: Write `scripts/generate-language-reference` to emit per-language markdown pages. Subsumes WI-sukav P2.

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

1. **Schema evolution.** How do we evolve the LanguageProfile dataclass without breaking analyzers that consume specific sections? Probably: version the dataclass and provide migration adapters.

2. **Profile composition for multi-language analyzers.** An analyzer that handles JS+TS may want `load_profile("javascript")` AND `load_profile("typescript")`. Should the registry support profile composition, or should the analyzer merge two profiles itself?

3. **Test detection rules.** Test-file detection is currently hardcoded in `taxonomy.py` rather than per-language YAML. Moving it to the profile would unify it but requires a migration from code to data.

4. **Performance.** Loading four+ YAML files per language times N languages at startup adds up. Is lazy per-language loading sufficient, or do we need eager caching?

## References

- WI-sukav (original motivation for per-language discoverability)
- WI-rumim (this ADR)
- ADR-0006 (Variable Type Inference — §Return-Type Registry Pre-Pass is a future registry consumer)
- ADR-0015 (Dataflow Access Modes — defines `dataflow_patterns` section)
- ADR-0016 (IO Boundary Analysis — defines `io_primitives` section)
- ADR-0017 (Taint-Zone Dataflow — defines `function_summaries` section)
