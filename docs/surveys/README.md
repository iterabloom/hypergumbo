<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Surveys

This directory holds **survey / snapshot documents**: descriptive inventories, point-in-time catalogs, or audit-shaped reports whose findings don't fit the strict per-value trichotomy of [`docs/audits/`](../audits/README.md).

## Bucket rubric

The three-bucket boundary is described in [`docs/adr/README.md`](../adr/README.md):

- **`docs/adr/`** — load-bearing decision documents.
- **`docs/audits/`** — per-value verdicts (CANONICAL / FOLD / DEPRECATE-NO-FOLD) under an existing axis-declaration ADR.
- **`docs/surveys/` (this directory)** — pure descriptive snapshots / inventories. No associated decision required; the survey may *inform* a future ADR or per-value audit, but the survey itself is a record of findings, not a ruling.

The `docs/audits/README.md` §"Scope" carve-out also points here for audits with different verdict shapes than the per-value trichotomy:

> "Audits with different verdict shapes (e.g., a single 'no leak found' finding, or a vocabulary audit whose conclusions don't slot into the trichotomy) should propose a sibling format rather than shoehorn into this one."

## File format

Each survey is one markdown file named `<topic>.md` (no numbering — the surveys series is a flat namespace, not a numbered sequence). Typical structure:

1. `# Survey: <Title>` — top-level heading.
2. Front-matter: Status (when relevant), Date, Methodology pointer, what the survey *informed* (PRs / ADRs / tracker items) versus what's still open.
3. `## Why this is a survey, not <other bucket>` — a short paragraph explaining the bucket choice. Especially useful for surveys that are audit-adjacent.
4. `## Context` — what the survey looked at and why.
5. `## Methodology` — pointer to the playbook or methodology document that produced it. Don't duplicate.
6. Per-dimension or per-section findings.
7. `## Related` — cross-references.

Surveys are **not subject to the audit-findings property test** that enforces strict trichotomy. They are free-form descriptive documents. If a survey's findings later need formal per-value verdicts, those would be filed as a separate audit-findings document under `docs/audits/`, with the survey cited as context.

## Index

| Topic | Date | Status | Informed |
|---|---|---|---|
| [Symbol Emit-Site Coherence](symbol-emit-coherence.md) | 2026-05-30 | Mixed — D1 mostly resolved, D2/D3 ongoing | PR #3984 (objc/ansible), PR #3986 (protobuf collapse), ADR-0031 (Draft, Symbol.language reshape) |

## Related

- [`docs/adr/README.md`](../adr/README.md) — the bucket rubric (ADR vs audit-findings vs survey).
- [`docs/audits/README.md`](../audits/README.md) — the audit-findings format; this directory is its acknowledged sibling for non-trichotomy findings.
