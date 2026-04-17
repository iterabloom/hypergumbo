<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Architecture Decision Records

This directory contains the project's ADRs, documenting significant design decisions with context, rationale, and consequences.

## Index

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [0001](0001-portable-agent-instructions.md) | Portable Agent Instructions | Accepted | 2025-12-20 |
| [0002](0002-test-dependency-handling.md) | Test Dependency Handling | Accepted | 2025-12-27 |
| [0003](0003-architectural-analysis-and-revision-plan.md) | Architectural Analysis and Revision Plan | Accepted (§5 migration plan complete) | 2026-01-07 |
| [0003-ext](0003-usage-context-patterns.md) | Usage Context Patterns | Proposed | |
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
| [0014](0014-generalized-symbol-identity.md) | Generalized Symbol Identity (stable_id / shape_id) | Accepted | 2026-02-20 |
| [0015](0015-dataflow-access-modes.md) | Dataflow Access Modes on Edges | Accepted | 2026-03-15 |
| [0016](0016-io-boundary-analysis.md) | I/O Boundary Analysis and Security Claim Verification | Accepted | 2026-03-18 |
| [0017](0017-taint-zone-dataflow.md) | Taint-Zone Dataflow Analysis | Accepted | 2026-03-22 |
| [0018](0018-transcript-sync-and-playbook-injection.md) | Vendor-Agnostic Transcript Sync and LLM-Driven Playbook Injection | Accepted | 2026-03-29 |

## Thematic grouping

**Analysis pipeline:** 0003, 0004, 0005, 0006, 0007, 0012, 0014, 0015, 0016, 0017

**Agent infrastructure and governance:** 0001, 0008, 0009, 0013, 0018

**Testing and packaging:** 0002, 0010, 0011
