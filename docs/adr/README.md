<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Architecture Decision Records

This directory contains the project's ADRs, documenting significant design decisions with context, rationale, and consequences.

## Index

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [0001](0001-portable-agent-instructions.md) | Portable Agent Instructions | Accepted | 2025-12-20 |
| [0002](0002-test-dependency-handling.md) | Test Dependency Handling | Accepted | 2025-12-27 |
| [0003](0003-architectural-analysis-and-revision-plan.md) | Architectural Analysis and Revision Plan | Accepted (§5 migration plan complete) | 2026-01-07 |
| [0003-ext](0003-usage-context-patterns.md) | Usage Context Patterns | Implemented | |
| [0003-ext](0003-linker-subcategory-restoration.md) | Linker Subcategory Restoration | Implemented | 2026-04-16 |
| [0004](0004-file-taxonomy.md) | File Taxonomy: Tier and Role Classification | Accepted | 2025-01-14 |
| [0005](0005-sketch-budget-allocation.md) | Sketch Budget Allocation and Section Composition | Accepted | 2025-01-15 |
| [0006](0006-variable-type-inference.md) | Variable Type Inference for Method Call Resolution | Accepted | 2025-01-21 |
| [0007](0007-import-tracking-for-call-resolution.md) | Import Tracking for Cross-File Call Resolution | Accepted | 2026-01-22 |
| [0008](0008-autonomous-governance-and-vendor-agnostic-hooks.md) | Autonomous Governance and Vendor-Agnostic Hooks | Accepted | 2026-01-24 |
| [0009](0009-feature-focused-bakeoff.md) | Feature-Focused Bakeoff Suite (DEEP mode) | Accepted | 2026-01-30 |
| [0010](0010-modular-packages-and-smart-testing.md) | Modular Packages and Smart Testing | Implemented | |
| [0011](0011-scoped-coverage-and-green-baseline.md) | Scoped Coverage and Green Baseline Tracking | Implemented | |
| [0012](0012-pass-unification-and-multi-fidelity.md) | Pass Unification and Multi-Fidelity Architecture | Partially implemented | |
| [0013](0013-structured-tracker.md) | Structured Tracker | Accepted | 2026-02-13 |
| [0014](0014-generalized-symbol-identity.md) | Generalized Symbol Identity (stable_id / shape_id) | Accepted — partially superseded by ADR-0035 (see amendment table) | 2026-02-20 |
| [0015](0015-dataflow-access-modes.md) | Dataflow Access Modes on Edges | Accepted — partially superseded by ADR-0038 (emission guidance only) | 2026-03-15 |
| [0016](0016-io-boundary-analysis.md) | I/O Boundary Analysis and Security Claim Verification | Accepted | 2026-03-18 |
| [0017](0017-taint-zone-dataflow.md) | Taint-Zone Dataflow Analysis | Accepted | 2026-03-22 |
| [0018](0018-transcript-sync-and-playbook-injection.md) | Vendor-Agnostic Transcript Sync and LLM-Driven Playbook Injection | Accepted | 2026-03-29 |
| [0019](0019-remote-access-transport.md) | Remote Access Transport | Proposed | 2026-03-30 |
| [0020](0020-tui-screenshot-annotation-and-inline-preview.md) | TUI Screenshot Annotation and Inline Preview | Proposed | 2026-03-30 |
| [0021](0021-tracker-federation.md) | Tracker Federation | Proposed | 2026-03-30 |
| [0022](0022-language-profile-registry.md) | Per-Language Configuration Surface and Language Profile Registry | Proposed (exploratory) | 2026-04-10 |
| [0023](0023-edge-type-relationship-not-endpoints.md) | Edge Type Names the Relationship, Not the Endpoints | Accepted (§6 migration in progress) | 2026-04-29 |
| [0024](0024-axis-declaration-template.md) | Axis Declaration Template for Multi-Value Fields | Accepted | 2026-04-30 (updated 2026-05-02) |
| [0027](0027-symbol-kind-language-construct-only.md) | Symbol.kind Names the Source-Language Syntactic Construct | Draft | 2026-05-02 |
| [0028](0028-evidence-type-inference-pathway-only.md) | Edge.evidence_type Names the Inference Pathway | Draft | 2026-05-02 |
| [0029](0029-cross-language-inherited-call-linker.md) | Cross-Language Inherited-Call Linker | Accepted | 2026-05-25 |
| [0030](0030-prov-vocabulary-mapping.md) | PROV Vocabulary Mapping for Behavior Map Provenance | Accepted | 2026-05-27 |
| [0031](0031-symbol-language-reshape.md) | Symbol.language Reshape — discovery_language and protocol_origin Typed Fields | Accepted | 2026-05-30 |
| [0032](0032-canonical-name-fingerprint-reshape.md) | canonical_name and fingerprint Reshape — display_label and qualified_name Typed Fields; Format 1 Fingerprint Demolition | Accepted | 2026-05-31 |
| [0033](0033-spec-vs-data-validator-stage.md) | Spec-vs-Data Validator Stage | Accepted | 2026-05-31 |
| [0034](0034-id-construction-discipline.md) | ID-Construction Discipline | Accepted | 2026-06-01 |
| [0035](0035-stable-id-v6-identity-contract.md) | stable_id v6 Identity Contract | Accepted | 2026-06-10 |
| [0036](0036-node-id-grammar-v2.md) | node.id Grammar v2 | Accepted | 2026-06-10 |
| [0037](0037-edge-resolution-semantics.md) | Edge Resolution Semantics | Accepted | 2026-06-10 |
| [0038](0038-access-mode-contract.md) | The access_mode Contract | Accepted | 2026-06-10 |
| [0039](0039-confidence-separation.md) | Confidence Separation | Accepted | 2026-06-10 |
| [0040](0040-evidence-field-descope.md) | Evidence-Field Descope | Accepted | 2026-06-10 |
| [0041](0041-supply-tier-purity.md) | Supply-Chain Tier Purity | Accepted | 2026-06-10 |
| [0042](0042-survey-rename.md) | Survey Rename | Accepted | 2026-06-10 |
| [0043](0043-stage-ordering-contract.md) | Stage-Ordering Contract for `run_behavior_map` | Accepted | 2026-06-12 |

> ADR numbers 0025 and 0026 were filed under the ADR series in error and have been **reclassified as audit-findings documents** (per-value verdicts under existing law from ADR-0023 and ADR-0024, not new architecture decisions). They now live at [`docs/audits/0001-dispatch-publish-family.md`](../audits/0001-dispatch-publish-family.md) and [`docs/audits/0002-ipc-family.md`](../audits/0002-ipc-family.md). Stubs at the old paths are kept for URL-level discoverability but are not principles. The bucket boundary is documented in the next section.

## When to write an ADR vs an audit-findings document vs a survey

Three buckets, each with its own home:

- **Bucket 1 — ADR (`docs/adr/<NN>-<topic>.md`).** A load-bearing decision document. May have a substantial Context section that includes analysis, surveys, or comparisons of alternatives — those serve the decision. Use even when Context is long, as long as the deliverable is fundamentally a decision. The decision is the deliverable; the analysis is in service of it. Examples in this directory: ADR-0023 (axis principle), ADR-0024 (axis-declaration template), ADR-0003 lineage (decisions with substantial Context).
- **Bucket 2 — Audit-findings (`docs/audits/<NN>-<topic>.md`).** A record of per-value verdicts produced by applying an existing methodology (typically the [Fundamental Concept Audit](../../.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md)) to a specific scope. Records case rulings under existing law, NOT new principles. Carries lifecycle states + structured YAML format per [`docs/audits/README.md`](../audits/README.md). Worked examples: [audit-findings 0001](../audits/0001-dispatch-publish-family.md), [audit-findings 0002](../audits/0002-ipc-family.md).
- **Bucket 3 — Survey/snapshot (`docs/surveys/<topic>.md` or `docs/architecture-snapshots/<date>.md`).** A catalog or point-in-time inventory with no associated decision. Pure descriptive — may *inform* a future ADR or per-value audit but isn't itself a decision or ruling. Worked example: [Symbol Emit-Site Coherence survey](../surveys/symbol-emit-coherence.md) (the audit that informed PR #3984, PR #3986, and ADR-0031). See [`docs/surveys/README.md`](../surveys/README.md) for the format.

**Bucket boundary**: *decision present?* → bucket 1, regardless of Context length. *No decision; per-value verdicts?* → bucket 2. *No decision; inventory/catalog?* → bucket 3.

**Bucket-1 self-justification guard.** Bucket 1 requires a load-bearing decision (a principle adopted, a template defined, an architectural change committed to). Context may be substantial in service of the decision, but the decision is the deliverable. If the deliverable is fundamentally a description with a "we should..." paragraph at the end, that's bucket 3 with weak decision content, not bucket 1.

## ADR lifecycle: status, supersession, and excision

ADRs here are **edited in place** as decisions evolve. Git history is the immutable record, so the *file* is kept current rather than accumulating superseded text behind banners. The governing goal: **no reader — human or LLM, full read or fragment — should be able to mistake superseded text for the current decision.** Traditional "ADRs are immutable, never edit" practice predates ubiquitous version control; here git provides the permanent trail, and the live file carries only what is true now plus pointers to where superseded pieces went.

### Status line — one form, at the top, greppable

Every ADR opens with a single `Status:` line right after the title, in one of these forms:

- `Status: Proposed` — proposed, not adopted.
- `Status: Accepted` — adopted (implementation may be partial).
- `Status: Implemented` — adopted and shipped.
- `Status: Superseded by ADR-NNNN (YYYY-MM-DD)` — fully replaced; tombstone the body (see below).
- `Status: Partially superseded by ADR-NNNN[, ADR-MMMM]` — some sections replaced; the rest remains authoritative.
- `Status: Mosaic — §A → ADR-X; §B → ADR-Y; §C in force; §D pending ADR-Z` — different slivers superseded by different successors (common for fast-churning topics like identity). The status line *is* the per-section map.
- `Status: Reclassified → <path>` — moved to another bucket; the file is a redirect stub.

The truth lives at the top. Never leave `Status: Accepted` up top with the real supersession buried in a mid-body amendment — a `grep` of the status line must not lie.

### Supersession is symmetric and by-number

Both endpoints name each other: the predecessor's `Status:` names the successor ADR number, and the successor carries a `Supersedes: ADR-NNNN (§sections)` line. The relationship must be greppable from *either* ADR. Do not record it only as one-sided prose ("amends ADR-XXXX").

### Excise the superseded body — don't banner it

Replace a superseded section's body with a one-line pointer to its successor — e.g. `> §2 (untyped-tier formula): superseded by ADR-0035 §1.` Do **not** retain the stale text under a "the following is superseded by…" banner: a fragment read ingests the stale text and never sees the banner. Still-current sections stay; pending ones keep a one-line "pending ADR-X" note. A mosaic ADR gets one pointer per superseded sliver, each naming its own successor.

### Tombstone vs delete

- **Tombstone** (default when an ADR is *fully* superseded): replace the whole body with the `Status:` line, a 2–4 sentence "what it decided / why replaced," and a `git show <sha>:<path>` pointer to the original. **Keep the file** — ADR numbers are stable addresses cited throughout the repo; deleting one dangles every reference.
- **Delete** (rare): only when an ADR is fully superseded *and* has zero inbound references repo-wide. Git preserves the content.

### Self-containment of the *decision*, not of all context

A successor must let a reader grasp the **current decision** without reading the superseded one. It need **not** duplicate durable, still-valid context (problem framing, alternatives) — reference it instead, provided the referenced content is itself current (not superseded). Before excising or tombstoning a predecessor, relocate any still-needed snippet a current doc points at; do not bloat the successor by absorbing the whole predecessor.

### One identifier per ADR

Each ADR has a unique identifier; number collisions are resolved by renaming the colliding files (git makes this safe) and rewriting their references, so a bare `ADR-NNNN` citation is never ambiguous. Disambiguating an ADR *file* this way is distinct from the cluster notation `<ADR-number><letter>` (e.g. `27A`) used for value sub-groups *within* an axis (see Common terms).

## Common terms

- **Axis** (or **concept-axis**) — a typing dimension along which every value of a multi-value field must be classified, governed by a one-sentence axiom. The hypergumbo IR has three (`Edge.edge_type`, `Symbol.kind`, `Edge.evidence_type`); the abstract framework for declaring one is [ADR-0024](0024-axis-declaration-template.md).
- **Cluster** — a sub-group of values within one axis, used to scope per-value audit and migration work. Identified as `<ADR-number><letter>` (e.g. `27A`, `28D`); the ADR-number prefix disambiguates clusters across axes (`Cluster 27D` and `Cluster 28D` are different things).
- **Audit-findings document** — a per-cluster verdict table filed at [`docs/audits/<NN>-<topic>.md`](../audits/), produced by applying the [Fundamental Concept Audit](../../.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md) methodology. Records case rulings under existing law, not new principles. See [`docs/audits/README.md`](../audits/README.md) §"Concepts" for the longer treatment.

## Thematic grouping

**Analysis pipeline:** 0003, 0004, 0005, 0006, 0007, 0012, 0014, 0015, 0016, 0017, 0022, 0023, 0024, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043

**Agent infrastructure and governance:** 0001, 0008, 0009, 0013, 0018, 0019, 0020, 0021

**Testing and packaging:** 0002, 0010, 0011
