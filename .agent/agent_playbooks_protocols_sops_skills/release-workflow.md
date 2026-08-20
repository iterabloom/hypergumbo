<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
## Release Workflow (Agent + Human)

Releases use a two-step workflow that separates agent preparation from human authorization.

### Agent Preparation
```bash
# Agent runs this to prepare everything
./scripts/prepare-release 0.8.0

# This script:
# 1. Bumps version in pyproject.toml and __init__.py
# 2. Updates CHANGELOG.md ([Unreleased] → [0.8.0])
# 3. Commits: "chore: release 0.8.0"
# 4. Runs ./scripts/release-check (all validations)
# 5. Pushes to dev
# 6. Creates PR: dev → main
# 7. Outputs handoff instructions
```

### Human Actions (Required)
```bash
# 1. Review and merge the PR on the GitHub web UI
#    (origin is github.com; "Codeberg" in older docs is stale)

# 2. After PR merged, human runs:
./scripts/tag-release 0.8.0

# This script:
# 1. Switches to main and pulls latest
# 2. Verifies version matches
# 3. Refuses to clobber an existing tag without consent
# 4. Verifies it can authenticate to origin, running `gh auth login` +
#    `gh auth setup-git` when an https origin has no credential helper
# 5. Creates GPG-signed tag: git tag -s v0.8.0
# 6. Pushes tag (triggers release workflow)
```

**Step 4 runs before step 5 deliberately.** GitHub removed password auth for Git
in 2021, so an `https` origin with no credential helper prompts for a password
that can never work. Tagging v8.0.0 hit precisely that — the tag signed, the push
failed with `Password authentication is not supported for Git operations`, and
the release was left half-done: a signed local tag, nothing on the remote, and a
script that could not just be re-run, because step 3 offers to delete and
recreate the very tag it had signed a moment earlier. Authenticating first makes
that failure cost nothing.

**Which account signs and pushes.** The org pays for two GitHub seats —
`josh-iterabloom` (human) and `jgstern-agent` (agent). Step 4 prints the
authenticated login and warns when it is neither. One trap worth knowing: `gh`
prefers `GH_TOKEN` / `GITHUB_TOKEN` from the environment over its own stored
login, so a human who has run `gh auth login` as themselves can still push as
the agent seat; step 4 warns whenever either variable is set. This repo's own
`HG_GITHUB_TOKEN` is *not* a name `gh` reads and does not trigger the warning.

### Why Two Steps?
- **Branch protection:** main branch cannot be pushed to directly
- **GPG signing:** Tag must be signed with human's GPG key
- **Authorization:** Human explicitly approves the release

### Scripts Reference
| Script | Who | Purpose |
|--------|-----|---------|
| `./scripts/prepare-release VERSION` | Agent | Prepare everything, create PR |
| `./scripts/tag-release VERSION` | Human | Sign and push tag after PR merge |
| `./scripts/release VERSION` | Either | Legacy single-step (detects protection) |
| `./scripts/release-check` | Either | Validate release readiness |
| `./scripts/bump-version VERSION` | Either | Just bump version (part of prepare-release) |
