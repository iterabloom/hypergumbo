<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Trackerize Playbook

When the user says "trackerize", parse the plan under discussion into individual tracker items.

## Trigger

The user says "trackerize" — possibly with a file path, a topic reference, or nothing (meaning "what we were just discussing"). Examples:

- `trackerize`
- `trackerize docs/plan.md`
- `trackerize the omega plan`
- `trackerize the baldness cure discussion`

If it's ambiguous what to trackerize, ask the user to clarify before proceeding.

## Procedure

### 1. Gather context

- Read the plan source (file, conversation history, or both).
- Run `scripts/tracker list --limit 50` and `scripts/tracker ready` to understand what already exists. This serves two purposes: (a) avoid creating duplicates, and (b) inform priority assignment relative to existing items.

### 2. Decompose into items

Break the plan into **individual, self-contained items** — each one should be actionable on its own without re-reading the original plan. Prefer more items over fewer; the cost of an extra item is near zero, but the cost of accidentally skipping a step buried inside a larger item is real.

Guidelines:
- Each item's title should be specific enough to act on without context.
- Each item's description should include whatever detail is needed to execute — don't assume the agent will have the original plan in context.
- Reference the source plan file path in the description if one exists (e.g., "Per docs/plan.md §3").
- If a single plan step has multiple independently-verifiable parts, split them.

### 3. Choose kind

Use judgment:
- **`work_item`** — implementation tasks ("add X", "fix Y", "refactor Z").
- **`invariant`** — properties that must remain true ("ensure X never regresses", "Y must always satisfy Z"). These get validated by bakeoff rather than marked done.

### 4. Assign priority

Priority is 0–4 (0 = highest). Assign based on:
- The plan's own ordering and emphasis.
- How existing tracker items are prioritized (from the `list`/`ready` output in step 1).
- Dependencies — items that block many others are typically higher priority.

### 5. Set sequencing with `isbefore`

Use `--isbefore ITEM_ID` when one item must be completed before another can start. This is for real dependencies, not just preferred ordering — don't over-constrain. A linear chain of 8 items where each blocks the next is usually wrong; most plans have 2–3 dependency edges, not N-1.

### 6. Use parents sparingly

Only create parent-child relationships when there is a compelling structural reason (e.g., a multi-phase migration where phases are distinct but conceptually grouped). Flat lists with `isbefore` edges are typically clearer than nested hierarchies.

### 7. Apply tags

Use tags to make items filterable. Pick from well-known tags when they fit (`developer_experience`, `analysis_quality`, `cross_language_linkers`, `ci_infrastructure`, `bakeoff_infrastructure`, `language_additions`). Custom tags are fine when none of the well-known ones apply.

### 8. Check for duplicates

Before creating each item, check whether the tracker already has an item covering the same scope. If it does:
- If the existing item is a strict superset, skip the new one and note it.
- If there's partial overlap, ask the user whether to merge, skip, or create anyway.
- If the existing item is resolved (`done`/`satisfied`/`wont_do`), create the new one — the plan may intentionally revisit it.

### 9. Create the items

```bash
scripts/tracker add \
  --kind work_item \
  --title "Specific actionable title" \
  --priority 2 \
  --status todo_hard \
  --tag some_tag \
  --description "Full context needed to execute this item. Per docs/plan.md §3." \
  --isbefore WI-other-item-id
```

Create items in dependency order (blockers first) so that `--isbefore` references are valid.

### 10. Summarize

After creating all items, show the user a summary: item IDs, titles, priorities, and dependency edges. This lets them sanity-check the decomposition before work begins.

## Anti-patterns

- **One giant item** — defeats the purpose. If an item has "and" in the title, it's probably two items.
- **Over-sequencing** — a fully-linear chain of `isbefore` edges means nothing can be parallelized. Most real plans have some items that are independent.
- **Copying the plan verbatim** — item descriptions should be self-contained, not "see plan step 3". Include enough context that the item stands alone.
- **Skipping the duplicate check** — creating items that already exist clutters the tracker and confuses priority.
- **Defaulting everything to the same priority** — if all items are priority 2, the priority field carries no information. Differentiate.
