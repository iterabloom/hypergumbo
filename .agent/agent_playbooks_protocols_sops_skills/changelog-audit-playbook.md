<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
## Changelog Audit Playbook

Periodic audit of the `[Unreleased]` sections of both `CHANGELOG.md` (main tool) and `packages/hypergumbo-tracker/CHANGELOG.md` (tracker package) to ensure they are complete, accurate, well-organized, and that entries live in the correct document. Run this before releases, after extended autonomous sessions, or whenever the Unreleased sections feel stale.

### Scope: Two Changelogs

The project maintains two changelogs:

- **`CHANGELOG.md`** — the main hypergumbo tool (core, language analyzers, linkers, CLI, CI, infrastructure).
- **`packages/hypergumbo-tracker/CHANGELOG.md`** — the `hypergumbo-tracker` package (tracker CLI, TUI, web server, store, sync, governance).

Each changelog tracks its own package's releases independently. The audit procedure below applies to both documents, run sequentially. Phase 0 (relocation) runs once before auditing either document.

### Phase 0: Relocation

**Goal:** Ensure tracker entries are in the tracker changelog and main entries are in the main changelog.

**Step 1 — Read both Unreleased sections.** Read the `## [Unreleased]` section of both changelogs.

**Step 2 — Identify misplaced entries in the main changelog.** For each entry in the main changelog's Unreleased section, check whether the underlying work only touched files under `packages/hypergumbo-tracker/`. Use path-based identification: if the commits behind an entry exclusively modified tracker package files, the entry belongs in the tracker changelog, not the main one.

When in doubt, check the commit(s) behind the entry:
```bash
git log --oneline --name-only "$RANGE_START"..HEAD -- packages/hypergumbo-tracker/
```

**Step 3 — Relocate misplaced entries.** For each misplaced entry:
- If the work is tracker-only: **move** the entry from the main changelog to the tracker changelog's Unreleased section. Remove it from the main changelog.
- If the work genuinely spans both packages (e.g., a core change that required a coordinated tracker change): **copy** the entry to the tracker changelog and keep it in the main changelog. This should be uncommon.

**Step 4 — Check the reverse direction.** Scan the tracker changelog for entries that belong in the main changelog (less common, but possible). Apply the same move-or-copy logic.

Do relocation before the completeness and organization phases. Otherwise you'd audit a changelog, find it "complete," then move entries out and leave gaps.

### Phase 1: Completeness Check

Run this phase for each changelog in turn: first the main changelog, then the tracker changelog.

**Goal:** Identify work that was merged to dev since the last release but is missing from the changelog.

**Step 1 — Read the Unreleased section.** Read the changelog's `## [Unreleased]` header down to the next `## [` header (the previous release). Build a mental inventory of what's covered.

**Step 2 — Check for recent prior audits.** Before reading the full commit log, check whether a recent audit already covered most of the range:

```bash
# Find the latest release tag (main tool or tracker, as appropriate)
LAST_TAG=$(git tag --list 'v*' --sort=-v:refname | head -1)

# Check if a prior audit exists since the last tag
git log "$LAST_TAG"..HEAD --oneline --grep='changelog.*audit\|audit.*changelog' | head -5
```

If a recent audit commit exists (e.g., `docs(changelog): audit Unreleased section`), only check commits *since that audit* for missing items. Reading 2000+ lines of commits that were already audited is pure waste. Set the log range to `<audit-commit>..HEAD` instead of `<last-tag>..HEAD`.

**Step 3 — Gather and scan the commit log.**

For the **main changelog**, filter out tracker-only changes:
```bash
git log "$RANGE_START"..HEAD --format='--- %h %s%n%b' \
  -- ':!.agent/tracker/.ops' ':!.agent/tracker-workspace/.ops' \
  ':!packages/hypergumbo-tracker/' \
  > /tmp/changelog-audit-commits-main.log
```

For the **tracker changelog**, filter to tracker-only changes:
```bash
git log "$RANGE_START"..HEAD --format='--- %h %s%n%b' \
  -- 'packages/hypergumbo-tracker/' \
  > /tmp/changelog-audit-commits-tracker.log
```

Read the log in chunks (200-300 lines). For each chunk, note commits whose subject indicates user-visible work (`feat:`, `fix:`, `refactor:`, `test:`, `ci:`, `docs:`, `perf:`). Check whether each is already represented in the Unreleased section. "Represented" means the *effect* is documented, not necessarily that the exact commit is mentioned. Collect missing items grouped by conventional-commit prefix.

**Critical:** Commits that only touch `packages/hypergumbo-tracker/` are not "missing" from the main changelog — they belong in the tracker changelog. Do not add them to the main changelog. Conversely, commits that only touch non-tracker paths are not "missing" from the tracker changelog.

**Time box:** If the prior audit was recent and the range has <20 commits, Phase 1 should take under 5 minutes per changelog. If you're spending 15+ minutes on Phase 1 and finding nothing missing, stop — the section is complete.

**Step 4 — Add missing items.** For each missing item, write a concise changelog entry in the appropriate subsection (Added, Fixed, Changed, etc.). Match the style and detail level of surrounding entries. Place it under the most relevant existing heading, or create a new heading if none fits.

**What to include:** Features, bug fixes, refactors, CI/infrastructure improvements, test coverage expansions, documentation updates, dependency changes, performance improvements. If someone (human, machine, or other) did the work, it belongs in the record.

**What to exclude:** Tracker-only syncs (`tracker: sync N file(s)`), merge commits with no substance, and commits that are purely mechanical (version bumps that are part of the release process itself).

### Phase 2: Organization and Concision

Run this phase for each changelog in turn, after Phase 1 is complete for that changelog.

**Goal:** Make the Unreleased section easier to scan and understand. The Unreleased section's level of detail should be comparable to that of the released per-version sections — and no more.

**Step 0 — Calibrate against released sections.** Before touching the Unreleased section, read the most recent 2-3 released version sections (e.g., `## [2.4.0]`, `## [2.3.0]` for the main changelog; `## [0.2.0]` etc. for the tracker changelog). These are the style and detail-level target. The Unreleased section should feel like it belongs in the same document at the same granularity. If a released section describes a feature in one bullet, the Unreleased section should not describe a comparable feature in five bullets with implementation sub-details. Completeness is valued but so is conciseness — sometimes it is appropriate to remove detail and make the view more "birds' eye". A single concise bullet that captures the *effect* of a feature is better than a multi-paragraph breakdown of its internal architecture. Save implementation details for commit messages and ADRs.

**Step 1 — Re-read the Unreleased section in full.** Ask these specific questions:

- **Duplicates:** Are there entries describing the same feature from different PRs? (Common when a feature lands across multiple commits.) If so, merge them into a single entry that captures the full picture.
- **Granularity mismatch:** Are some features described at implementation detail level ("Added `_type_identifier_from_node` helper") while others are at feature level ("Go qualified-type parameter tracking")? Normalize toward feature-level descriptions. Implementation details can remain as sub-bullets if they add genuine context.
- **Grouping:** Are related entries scattered across the section? Group them under a shared subheading. For example, five separate IO boundary catalog entries across different languages belong under one "I/O boundary catalogs" heading.
- **Ordering:** Within each subsection, are the most significant changes listed first? Re-order if a minor fix is buried between two major features.

**Step 2 — Enumerate specific revisions.** Before editing, write down (in a scratchpad or message to yourself) the exact revisions you plan to make. For example:

- "Merge the three Go IO catalog bullets into one paragraph under the existing heading"
- "Move the CI workflow entry from Added to Changed"
- "Combine the two `htrac serve` WebSocket entries (from PR #132 and #135)"

This prevents drift — if you start editing without a plan, you risk reorganizing endlessly.

**Step 3 — Implement revisions subsection by subsection.** Do NOT attempt to rewrite the entire Unreleased section in one edit. Work top to bottom:

1. Pick the next subsection heading (e.g., `#### Go qualified-type tracking`).
2. Apply your planned revision for that subsection as one `Edit` call.
3. Move to the next subsection.

This prevents context-window overload from holding a 200+ line section in working memory. Each edit is small, self-contained, and verifiable. If you need to move content between subsections (e.g., merging IO catalog items), do the deletion and insertion as two sequential edits.

**Budget:** Spend no more than 3 rounds of organization edits per changelog. If the section still feels messy after 3 rounds, it's good enough. Diminishing returns are real. Phase 2 should take 10-15 minutes per changelog — if you've been editing for 20+ minutes on one, stop.

### Guard Rails

- **Prefer conciseness over exhaustive detail.** Every piece of real work should be represented, but it's fine to condense multiple implementation-level bullets into a single higher-level entry. The goal is a useful birds'-eye summary, not an exhaustive log — that's what `git log` is for.
- **Do not editorialize.** The changelog records what changed, not whether the change was important or impressive. A CI fix gets the same neutral tone as a new feature.
- **Do not rewrite history.** If the changelog attributes work to a specific PR or commit, keep that attribution. Traceability matters.
- **Watch for context window pressure.** If the Unreleased section is very long (100+ lines) and the commit log is very long (500+ commits), you will be tempted to skim. Resist — skim strategically (by chunk), not randomly. Missing a feature is worse than spending an extra 2 minutes reading.
- **Respect changelog boundaries.** Tracker work goes in the tracker changelog. Main tool work goes in the main changelog. When relocating, move — don't delete. The entry's information is preserved, just in the correct document.
