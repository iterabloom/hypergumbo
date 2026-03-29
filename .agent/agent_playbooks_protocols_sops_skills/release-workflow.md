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
# 1. Review and merge the PR on Codeberg web UI

# 2. After PR merged, human runs:
./scripts/tag-release 0.8.0

# This script:
# 1. Switches to main and pulls latest
# 2. Verifies version matches
# 3. Creates GPG-signed tag: git tag -s v0.8.0
# 4. Pushes tag (triggers release workflow)
```

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
