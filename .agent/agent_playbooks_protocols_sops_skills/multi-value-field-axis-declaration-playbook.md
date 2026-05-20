<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Multi-Value Field Axis Declaration Playbook

This playbook explains the per-PR mechanical gate that every
`str`-typed field on a core dataclass (in `ir.py` or `datamodels.py`)
must declare its axis via a `# axis: <category>` trailing comment.
The gate is enforced by `scripts/check-multi-value-field-axis-declaration`
and the live-tree property test
`packages/hypergumbo-core/tests/test_multi_value_field_axis.py::test_live_tree_passes`;
the decision behind the gate is ADR-0024 §"Creation practice"; the
gate itself is the WI-busij invariant.

Complement: the **Fundamental Concept Audit** playbook (
`what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md`
) covers the audit case — "I suspect an existing field is doing the
wrong job." This playbook covers the creation case — "I'm sitting in
front of `ir.py` right now about to add or modify a field." Same
vocabulary, different procedure, different trigger.

## When this applies

You're editing `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
or `packages/hypergumbo-core/src/hypergumbo_core/datamodels.py`, and
your change either:

- Adds a new `@dataclass`-decorated class with one or more
  `str`/`Optional[str]`/`Literal[str, ...]` fields, OR
- Adds a new string-typed field to an existing `@dataclass`, OR
- Changes the type annotation of an existing field in a way that
  brings it into or out of the str-like family.

The linter checks every PR via the property test; you'll find out
within 5 seconds if you skipped the gate. The real value, though, is
*not* to use the linter as your reviewer — it's to think about the
axis question *before* writing the field declaration.

## The rule

Every str-typed field on a `@dataclass` in the configured core files
must carry a `# axis: <category>` trailing comment. Four allowed
categories:

| Category | When to use |
|---|---|
| `<known-axis-name>` | Field value comes from a fixed list backed by a registry. |
| `identity` | Field's role is to uniquely identify a record (id, hash, signature). |
| `bounded-enum` | Field value is from a small fixed list (≤5 conventional) documented in the dataclass docstring. |
| `free-text — <justification>` | Field value is open-ended payload; no consumer branches on it. Justification mandatory. |

If your field doesn't fit any of those, the answer is *not* to add
a fifth category — it's that your axis isn't declared yet. See "How
to add a new axis" below.

## Category 1: `<known-axis-name>` — named axes backed by a registry

Use when your field's value is enumerable and the enumeration is
written down in a registry. Five recognised axes today:

### Heavyweight (registry module + ADR)

| Axis name | Registry module | ADR | Used by |
|---|---|---|---|
| `edge-type` | `hypergumbo_core/edge_types.py` | [ADR-0023](../../docs/adr/0023-edge-type-relationship-not-endpoints.md) | `Edge.edge_type` |
| `symbol-kind` | `hypergumbo_core/symbol_kinds.py` | [ADR-0027](../../docs/adr/0027-symbol-kind-language-construct-only.md) | `Symbol.kind` |
| `evidence-type` | `hypergumbo_core/evidence_types.py` | [ADR-0028](../../docs/adr/0028-evidence-type-inference-pathway-only.md) | `Edge.evidence_type` |

These follow the full ADR-0024 four-part scaffolding: registry
module with `AXIS_*` constants, `*Spec` frozen dataclass per value,
`*_on_axis(axis)` accessor, drift linter, property test, by-axis
view.

### Lightweight (catalog-derived, ADR-0024 §4 carveout)

| Axis name | Resolver function | Used by |
|---|---|---|
| `language` | `hypergumbo_core.catalog.all_known_languages()` | `Symbol.language`, `Edge.evidence_lang`, `ExternalRef.lang` |
| `pass-id` | `hypergumbo_core.catalog.all_known_pass_ids()` | `AnalysisRun.pass_id`, `Symbol.origin`, `Edge.origin` |

These are *lightweight* in the ADR-0024 §4 sense: no separate
`*_types.py` module, no `*Spec` dataclass, no by-axis view. Just a
function that returns the legal set by walking
`_ANALYZER_REGISTRY` and `_LINKER_REGISTRY` (the single source of
truth — analyzer/linker call-site `languages=[...]` kwargs and
registration names). Build a new lightweight axis the same way when
the legal set is derivable from data we already have.

### Wiring a new axis into the linter

The linter resolves axis names through the `_known_axes()` table in
`packages/hypergumbo-core/src/hypergumbo_core/multi_value_field_axis.py`.
When you add a new axis registry (heavyweight or lightweight), wire
it into that table:

```python
def _known_axes() -> dict[str, Callable[[], Iterable[str]]]:
    from .your_module import all_your_axis_names

    return {
        ...,
        "your-axis-name": all_your_axis_names,
    }
```

Then field annotations can reference `# axis: your-axis-name`.

## Category 2: `identity` — unique-per-record

Use when the field's role is to identify the record uniquely. The
value is typically a hash, UUID, or composite key; two records
with different identities are intentionally distinct.

Examples in current code:

- `Edge.id`, `Edge.edge_key`, `Edge.src`, `Edge.dst`
- `Symbol.id`, `Symbol.stable_id`, `Symbol.shape_id`, `Symbol.fingerprint`
- `AnalysisRun.execution_id`, `AnalysisRun.run_signature`, `AnalysisRun.repo_fingerprint`, `AnalysisRun.config_fingerprint`, `AnalysisRun.pass_version`
- `UsageContext.id`, `UsageContext.symbol_ref`

### Subtle case: `Edge.edge_key`

`edge_key` is `identity` even though two `Edge` records can share
it (it's the dedup key — records with the same `edge_key` are
considered duplicate emissions of the same logical edge). The
relevant invariant is "uniquely identifies the logical edge across
producers," not "every record has a different value." `identity`
covers both shapes; the disambiguation lives in the dataclass
docstring.

## Category 3: `bounded-enum` — small fixed list

Use when the value is enumerable but the enumeration is small
(≤5 conventional values) and tractable to list in the dataclass
docstring rather than maintain in a separate registry. The
docstring is the source of truth for the legal values.

Example in current code:

- `UsageContext.kind: Literal["call", "data_value", "export", "macro"]` — four values, listed in the docstring with the re-evaluation triggers documented inline.

### When to upgrade `bounded-enum` → registry

The re-evaluation triggers are documented in each `bounded-enum`
field's docstring. Generic patterns:

- The enum grows past ~5 values.
- A consumer needs to filter values by axis programmatically (the
  `*_on_axis(axis)` shape).
- A bug surfaces where the single value is hiding a cross-axis
  signal a consumer needs.

When any trigger fires, follow ADR-0024's seven-step workflow to
declare a new axis (registry, linter, test, view); the linter
annotation flips from `# axis: bounded-enum` to
`# axis: <new-axis-name>`.

## Category 4: `free-text — <justification>` — open-ended payload

Use when the field's value isn't enumerable at all — natural
language, identifiers from source code, filesystem paths,
timestamps, signatures, error messages. The test: **no code in the
codebase branches on the field's value** via `if field == "literal"`,
`field in {...}`, or `match field: case "literal":`. If branching
happens, the field is enumerable and one of the other three
categories applies.

The justification (`— <reason>` after the tag) is **mandatory**.
This is the only category whose "this is the right call" claim
isn't anchored elsewhere:

- Named axes are anchored by their registry.
- `identity` is anchored by the uniqueness invariant.
- `bounded-enum` is anchored by the docstring value list.
- `free-text` has no other anchor — so the author writes one in.

Without the justification requirement, `free-text` would be the
natural can-kicker: any author wanting to dodge axis design could
slap `# axis: free-text` and ship. The justification raises the
friction enough to prevent that.

Examples in current code:

- `Symbol.docstring` — natural-language summary from source comment
- `Symbol.name` — language identifier from source
- `Symbol.path` — filesystem path
- `Symbol.signature` — callable signature string in source-language grammar
- `Symbol.supply_chain_reason` — natural-language explanation of assigned tier
- `Symbol.canonical_name` — fully-qualified name from source
- `AnalysisRun.started_at` — ISO-8601 UTC timestamp
- `ExternalRef.module_path` — module import path
- `ExternalRef.name` — symbol name at definition site
- `UsageContext.context_name` — name of the function call or export
- `UsageContext.position` — positional descriptor like "args[1]"

## Dropped categories and why

Two categories were considered and explicitly dropped. New
contributors periodically re-discover the impulse to add them;
this section exists so the rationale isn't re-litigated each time.

### Why no `# axis: pending WI-xxxxx`

The original ADR-0024 §3 open-question 2 framing proposed a
deferral hatch: "I know this field is multi-value-enumerable, I
haven't designed the axis yet, here's the tracker item where the
design will happen." Three problems:

1. **It becomes the easy way out.** The point of the creation-time
   gate is to force the axis design conversation at PR time. If
   `pending` is on the menu, the design conversation just doesn't
   happen — author stamps `pending`, PR ships, tracker item rots,
   structural fix never lands. The gate quietly defeats itself.
2. **Annotation rot.** A `pending WI-xxxxx` annotation in source
   code outlives the WI item. WI gets closed `wont_do` two years
   later; the annotation now lies in the source forever; nothing
   reconciles them.
3. **It duplicates `todo_hard`.** The structural-fix protocol
   already has a circuit breaker for "I'm not ready to make this
   decision now": file a `todo_hard` tracker item, hold the PR, do
   the design work, come back. The `pending` hatch reimplemented
   the same machinery in a worse way.

If you find yourself wanting `pending`, you actually want one of:

- Spend the design time NOW: declare the axis, then add the field.
- Revert the field addition: the PR isn't ready.
- File a `todo_hard` per the structural-fix protocol.

### Why `# axis: free-text` requires a justification

See Category 4 above. The TL;DR: without the justification
requirement, `free-text` becomes the natural can-kicker. Authors
who want to dodge the axis question stamp `free-text` and move on.
The justification requirement makes that dodge an explicit prose
defense, which raises the friction enough to deter drive-by use.

## Decision procedure: which category applies?

When adding a new str field, ask in order:

1. **Is this a hash, UUID, or composite key whose role is uniquely
   identifying the record?** → `identity`.
2. **Is the value drawn from a list that lives in (or could live in)
   a registry module?** → `<known-axis-name>`. If the registry
   doesn't exist yet, see "How to add a new axis" below.
3. **Is the value drawn from a small enum (≤5 values) listable in
   the dataclass docstring?** → `bounded-enum`. Document the values
   inline.
4. **Does any code in the codebase branch on this field's value
   (or will it soon)?** → If yes, it's enumerable; go back to (2).
   If no, it's payload → `free-text — <justification>`.

If you reach step 4 and you can't write an honest justification
for `free-text`, that's the linter's job done: you've discovered
the field IS multi-value-enumerable and you need a registry. See
"How to add a new axis."

## How to add a new axis

When none of the five existing axes fit your new field, you have
two paths depending on weight:

### Lightweight (catalog-derived)

If the legal set is derivable from data we already have (e.g.,
the union of registration kwargs, or a set of file paths matching
a glob), use the `all_*_known()` function pattern. Worked examples:
`catalog.all_known_languages()` and `catalog.all_known_pass_ids()`.

1. Write the resolver function in the natural home module
   (`catalog.py` for catalog-derived; elsewhere if better-anchored).
2. Wire it into `_known_axes()` in `multi_value_field_axis.py`.
3. Annotate your new field `# axis: <your-axis-name>`.
4. Add a test that the resolver returns a non-empty set.

No registry module needed. No ADR needed (per ADR-0024 §4 "use
judgment" carveout). The total addition is typically ~15 lines.

### Heavyweight (registry module + ADR)

If the legal set is *not* derivable from existing data — i.e.,
you're declaring a new vocabulary the codebase will maintain by
hand — follow ADR-0024's seven-step workflow:

1. Open a numbered ADR using ADR-0024's structure (axis name,
   axiom, consumer pattern, enforcement).
2. Land a registry module
   `packages/<pkg>/src/<module>/<field>_types.py`.
3. Land a drift linter `scripts/check-<field>-drift`.
4. Land a property test for registry invariants + drift.
5. Land a by-axis view in `docs/concept-axes.md`.
6. Update the Fundamental Concept Audit playbook to mention the
   new field as audited.
7. (Optional) Migrate pre-existing violators.

Heavyweight axes typically take a multi-PR campaign. Don't combine
the axis declaration with the field addition that motivated it —
the axis lands first, then the field uses it.

## The two known smuggling cases

The linter is mechanical; the data is sometimes messy. Two fields
have known violations that ride documented exceptions:

### `Symbol.origin` is `# axis: pass-id` but smuggles synthesis labels

`Symbol.origin` is annotated `# axis: pass-id`, but its runtime
values include `inheritance`, `orchestrator_file_symbol_synthesis`,
`scip` — synthesis-mechanism labels that aren't pass IDs. The
Symbol docstring documents this as a pending split into a sibling
`synthesis_mechanism` field. Until that lands, the smuggling is
documented; a future consumer-side check (a Phase 2 of WI-busij
that scans for `origin == "literal"` patterns) will surface these
as legitimate drift and force the split.

**Don't add new synthesis labels to `origin`.** If you find
yourself wanting to, file a tracker item for the
`synthesis_mechanism` split first.

### `UsageContext.kind` is `# axis: bounded-enum` at 4 values

`UsageContext.kind: Literal["call", "data_value", "export", "macro"]`
mixes two axes — syntactic construct (call, macro) and semantic
role (data_value, export). The docstring documents this and lists
the re-evaluation triggers (a 5th value that fits neither axis;
consumer needing per-axis filtering; cross-axis bug).

**Don't add a 5th value without splitting.** If a 5th value is
needed, follow ADR-0024's seven-step workflow to declare separate
`usage_construct` and `usage_role` axes; flip the annotations to
`# axis: usage-construct` and `# axis: usage-role`.

## How the linter runs

- **CLI**: `scripts/check-multi-value-field-axis-declaration`.
  Exit codes: 0 (clean), 1 (drift; offenders printed), 2 (import
  failure; don't block commits).
- **CI gate**: the property test
  `tests/test_multi_value_field_axis.py::test_live_tree_passes`
  runs against the live `ir.py` and `datamodels.py` and asserts
  agreement. Every PR's test run exercises this.
- **Pre-commit hook**: not yet wired (governance follow-up). When
  wired, the hook entry skips when no `ir.py` / `datamodels.py`
  files are staged.

## What this playbook does NOT cover

- **The audit case** — "I suspect an existing field is doing the
  wrong job." That's the Fundamental Concept Audit playbook at
  `what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md`.
- **The decision behind the four categories** — that's ADR-0024
  §"Creation practice" and §"Open questions" Q2.
- **The originating axis examples** — ADR-0023 (`edge-type`),
  ADR-0027 (`symbol-kind`), ADR-0028 (`evidence-type`) are the
  worked examples for heavyweight axis declarations.
- **Adding the field to non-core files** — the linter only scans
  `ir.py` and `datamodels.py` by default. A future sentinel-comment
  opt-in mechanism is reserved for other files if drift surfaces.

## Cross-references

- ADR-0024 §"Creation practice" — the decision.
- ADR-0024 §3 open question 2 — the originating defer (resolved
  by WI-busij).
- WI-busij tracker item — the implementation work.
- `packages/hypergumbo-core/src/hypergumbo_core/multi_value_field_axis.py`
  — the linter source (module docstring covers the implementation
  contract, not the practitioner guidance — that's this playbook).
- `packages/hypergumbo-core/tests/test_multi_value_field_axis.py`
  — the property tests + the live-tree CI gate.
- `scripts/check-multi-value-field-axis-declaration` — the CLI
  entry script.
- `.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md`
  — the audit-case complement to this playbook.
