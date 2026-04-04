## Changelog Audit Playbook

Periodic audit of the `[Unreleased]` section of `CHANGELOG.md` to ensure it is complete, accurate, and well-organized. Run this before releases, after extended autonomous sessions, or whenever the Unreleased section feels stale.

### Phase 1: Completeness Check

**Goal:** Identify work that was merged to dev since the last release but is missing from the changelog.

**Step 1 — Read the Unreleased section.** Read `CHANGELOG.md` from the `## [Unreleased]` header down to the next `## [` header (the previous release). Build a mental inventory of what's covered.

**Step 2 — Gather the commit log.** The commit range is from the latest release tag to HEAD:

```bash
# Find the latest release tag
LAST_TAG=$(git tag --list 'v*' --sort=-v:refname | head -1)

# Get commit subjects and bodies, excluding tracker noise
git log "$LAST_TAG"..HEAD --format='--- %h %s%n%b' \
  -- ':!.agent/tracker/.ops' ':!.agent/tracker-workspace/.ops' \
  > /tmp/changelog-audit-commits.log
```

**Step 3 — Work through the log strategically.** The commit log may contain hundreds of entries. Do not try to hold it all in context at once. Instead:

1. Read the log in chunks (200-300 lines at a time).
2. For each chunk, note commits whose subject indicates user-visible work: `feat:`, `fix:`, `refactor:`, `test:`, `ci:`, `docs:`, `perf:`. Every category matters — infrastructure, CI, test improvements, and refactors are real work that belongs in the changelog.
3. For each noted commit, check whether its substance is already represented in the Unreleased section. "Represented" means the *effect* is documented, not necessarily that the exact commit is mentioned.
4. Collect a list of missing items. Group them by conventional-commit prefix.

**Step 4 — Add missing items.** For each missing item, write a concise changelog entry in the appropriate subsection (Added, Fixed, Changed, etc.). Match the style and detail level of surrounding entries. Place it under the most relevant existing heading, or create a new heading if none fits.

**What to include:** Features, bug fixes, refactors, CI/infrastructure improvements, test coverage expansions, documentation updates, dependency changes, performance improvements. If someone (human, machine, or other) did the work, it belongs in the record.

**What to exclude:** Tracker-only syncs (`tracker: sync N file(s)`), merge commits with no substance, and commits that are purely mechanical (version bumps that are part of the release process itself).

### Phase 2: Organization and Concision

**Goal:** Make the Unreleased section easier to scan and understand without losing information.

Run this phase after Phase 1 is complete (or independently if completeness is not a concern).

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

**Step 3 — Implement revisions.** Apply the enumerated revisions. Do not make additional changes beyond what you enumerated. If you notice more improvements while editing, write them down and do a second pass rather than expanding scope mid-edit.

**Budget:** Spend no more than 3 rounds of organization edits. If the section still feels messy after 3 rounds, it's good enough. Diminishing returns are real.

### Guard Rails

- **Do not remove information.** Concision means saying the same thing in fewer words, not saying less. If a bullet point documents real work, it stays — it may get reworded or merged with a related bullet, but it does not disappear.
- **Do not editorialize.** The changelog records what changed, not whether the change was important or impressive. A CI fix gets the same neutral tone as a new feature.
- **Do not rewrite history.** If the changelog attributes work to a specific PR or commit, keep that attribution. Traceability matters.
- **Watch for context window pressure.** If the Unreleased section is very long (100+ lines) and the commit log is very long (500+ commits), you will be tempted to skim. Resist — skim strategically (by chunk), not randomly. Missing a feature is worse than spending an extra 2 minutes reading.
