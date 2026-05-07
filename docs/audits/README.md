<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-Findings Documents

This directory holds **audit-findings documents**: per-value verdict
tables produced by applying an existing methodology (typically the
[Fundamental Concept Audit
playbook](../../.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md))
to a specific scope.

Audit-findings documents are **filed separately from ADRs** because
they record **case rulings under existing law** (the law being a
prior axis-declaration ADR), not new principles. The boundary is
described in [`docs/adr/README.md`](../adr/README.md) under "When to
write an ADR vs an audit-findings document."

> **Scope.** This format is shaped for **axis-conformance audits**:
> per-value verdicts from the CANONICAL / FOLD / DEPRECATE-NO-FOLD
> trichotomy applied to values on a declared axis. Audits with
> different verdict shapes (e.g., a single "no leak found" finding,
> or a vocabulary audit whose conclusions don't slot into the
> trichotomy) should propose a sibling format rather than shoehorn
> into this one.

## Index

| ID | Title | Axis | Status |
|----|-------|------|--------|
| [0001](0001-dispatch-publish-family.md) | Dispatch and Publish Family Classifications | `Edge.edge_type` | All RESOLVED |
| [0002](0002-ipc-family.md) | IPC Family Classifications | `Edge.edge_type` | All RESOLVED |
| [0003](0003-symbol-kind-cluster-a-language-constructs.md) | Symbol.kind Cluster A — Canonical Language Constructs | `Symbol.kind` | All RESOLVED |
| [0004](0004-evidence-type-cluster-a-canonical-inference.md) | Edge.evidence_type Cluster A — Canonical Inference Pathways | `Edge.evidence_type` | All RESOLVED |
| [0005](0005-symbol-kind-cluster-b-file-shape.md) | Symbol.kind Cluster B — File-Shape and Package-Shape Entities | `Symbol.kind` | Mixed (6 RESOLVED, 11 PRELIM_RESOLVED) |
| [0006](0006-symbol-kind-cluster-g-build-config-shape.md) | Symbol.kind Cluster G — Build / Config-Shape Entities | `Symbol.kind` | Mixed (15 RESOLVED, 9 PRELIM_RESOLVED) |
| [0007](0007-symbol-kind-cluster-h-long-tail.md) | Symbol.kind Cluster H — Domain-Specific Long Tail | `Symbol.kind` | Mixed (56 RESOLVED, 2 PRELIM_RESOLVED, 2 UNRESOLVED) |
| [0008](0008-evidence-type-cluster-b-resolution-status.md) | Edge.evidence_type Cluster B — Resolution-Status Leakage | `Edge.evidence_type` | All PRELIM_RESOLVED |
| [0009](0009-symbol-kind-cluster-c-apex-peer.md) | Symbol.kind Cluster C — Apex/Peer Overloads | `Symbol.kind` | All PRELIM_RESOLVED |
| [0010](0010-symbol-kind-cluster-e-edge-label-kinds.md) | Symbol.kind Cluster E — Edge Labels Masquerading as Kinds | `Symbol.kind` | Mixed (9 PRELIM_RESOLVED, 3 UNRESOLVED) |
| [0011](0011-symbol-kind-cluster-f-component-refs.md) | Symbol.kind Cluster F — Component References | `Symbol.kind` | Mixed |
| [0012](0012-evidence-type-cluster-d-call-construct.md) | Edge.evidence_type Cluster D — Apex/Peer Call-Construct Overloads | `Edge.evidence_type` | All PRELIM_RESOLVED |
| [0013](0013-symbol-kind-cluster-d-framework-roles.md) | Symbol.kind Cluster D — Framework Roles | `Symbol.kind` | Mixed (most UNRESOLVED, 8 PRELIM_RESOLVED registry placeholders) |
| [0014](0014-evidence-type-cluster-c-framework-dispatch.md) | Edge.evidence_type Cluster C — Framework-Specific Dispatch | `Edge.evidence_type` | All UNRESOLVED |

## File format

Each audit-findings document is one markdown file named
`<NN>-<topic>.md` with `NN` starting at `0001`. Numbering is
independent of ADR numbering (the audit-findings series is its own
sequence).

The document's structure:

1. `# Audit-findings <NN>: <Title>` — top-level heading.
2. Front-matter: Status, Date, Closes (tracker items), Methodology
   (pointer to the parent ADR).
3. `## Context` — what the audit looked at and why.
4. `## Methodology` — one short paragraph pointing to the parent
   ADR's methodology section. Don't duplicate the methodology here.
5. `## Verdicts` — required heading immediately followed by a fenced
   YAML block (next section).
6. `## Diagnostic findings` (optional) — patterns worth naming.
7. `## Migration impact` (optional) — what code changed and why.
8. `## Related` — cross-references.

### The verdicts block

The `## Verdicts` section is followed by a fenced YAML block. **Both
the heading and the in-block `kind: audit_verdicts` key are
required** so the parser can locate the block from either direction;
missing either is a lint error. The block's schema:

```yaml
kind: audit_verdicts
axis: Edge.edge_type        # which declared axis this audit covers
verdicts:
  - value: <string>         # the value being judged
    verdict: CANONICAL | FOLD | DEPRECATE-NO-FOLD
    fold_target: <string|null>   # required when verdict is FOLD; null otherwise
    status: UNRESOLVED | PRELIM_RESOLVED | RESOLVED
    diagnostic_test:
      cmd: <shell command>       # what to run to verify the row
      expect: empty | nonempty | exit_code:N
    rationale: <string>           # one or two sentences
  - value: ...
    ...
```

The verdict scheme (CANONICAL / FOLD / DEPRECATE-NO-FOLD) is defined
in
[ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md).

### The three-state lifecycle

Each verdict row carries a `status` from the lifecycle below. The
property test (`packages/hypergumbo-core/tests/test_audit_findings.py`)
enforces the mechanical-check predicate matching each status against
the live registry.

| Status            | Meaning                                                           | Mechanical-check predicate (per axis registry)                                              |
|-------------------|-------------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| `UNRESOLVED`      | Verdict recorded; producers still emit the value.                 | Value is present in the registry (any axis).                                                |
| `PRELIM_RESOLVED` | Producer migration shipped; value remains in registry through the deprecation window. | Value is present in the registry on `endpoint_shape`.                              |
| `RESOLVED`        | Registry pruned; consumer-side enumerations cleaned up.           | For verdict CANONICAL: value present on `relationship`. For FOLD/DEPRECATE-NO-FOLD: absent.  |

For DEPRECATE-NO-FOLD verdicts the lifecycle compresses (the producer
drop is the migration; PRELIM_RESOLVED → RESOLVED happens when the
registry entry is pruned), but the same three states apply.

### The `diagnostic_test` field

Each row's `diagnostic_test` is a structured `{cmd, expect}` pair.
`cmd` is the shell command that verifies the row's claim; `expect` is
one of:

- `empty` — the command's stdout is expected to be empty.
- `nonempty` — the command's stdout is expected to be non-empty.
- `exit_code:N` — the command is expected to exit with status `N`.

The property test currently validates the **structural shape** of
`diagnostic_test` (cmd present, expect well-formed). Execution is
deferred to a future iteration. Authoring the field as machine-
readable now keeps that future cheap.

### Maintenance

- **Hand-edit-with-validation.** Edit the YAML block directly; the
  property test catches schema and lifecycle drift on pre-commit.
- **When the registry changes**, the property test will fail loudly
  if any row's status no longer agrees with the registry — at which
  point the row's status (or the registry) needs updating.
- **When a new audit is filed**, give it the next sequential number
  and add it to the index above. The bucket-rubric in
  [`docs/adr/README.md`](../adr/README.md) governs whether something
  belongs here (per-value verdicts under existing law) versus in
  `docs/adr/` (a load-bearing decision document).

## Bakeoff validation is orthogonal

This format does **not** track bakeoff validation of claimed metric
movements. Quantitative claims like "this fix reduces dead-code
false positives by N%" are tracked separately on the migration's
**tracker item** via the `awaits_bakeoff_validation` tag. Single
source of truth: `scripts/tracker list --tag
awaits_bakeoff_validation`. Don't duplicate that signal here.

## Related

- [ADR-0023: Edge Type Names the Relationship, Not the Endpoints](../adr/0023-edge-type-relationship-not-endpoints.md) — the originating axis declaration whose dispatch/publish/IPC families this directory's first two docs catalogue.
- [ADR-0024: Axis Declaration Template](../adr/0024-axis-declaration-template.md) — the template that names this directory as the canonical home for per-family audit-findings outputs (§"Family-audit verdict methodology" + §"Enforcement").
- [`docs/adr/README.md`](../adr/README.md) — the bucket rubric (ADR vs audit-findings vs survey).
- [Fundamental Concept Audit playbook](../../.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md) — Step 6 names this directory as the filing path for axis-conformance audit outputs.
