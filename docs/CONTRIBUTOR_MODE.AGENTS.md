<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Contributor Mode (Fork-Based Workflow)

For shared rules (TDD, coverage, testing, tracker, structural fix protocol, etc.), see the main `AGENTS.md` in the repository root.

This document covers the fork-based workflow for external contributors without write access.

## Setup
```bash
# 1. Fork the repo on Codeberg to your account

# 2. Clone YOUR fork (not upstream)
git clone https://codeberg.org/YOUR-USER/hypergumbo.git
cd hypergumbo

# 3. Add upstream remote
git remote add upstream https://codeberg.org/iterabloom/hypergumbo.git

# 4. Set credentials (in .env or exported)
export FORGEJO_USER=your-username
export FORGEJO_TOKEN=your-token
```

## Workflow
```bash
# 1. Sync with upstream
git fetch upstream
git checkout dev
git merge upstream/dev

# 2. Create feature branch (from dev)
git checkout -b yourname/feat/description

# 3. Do TDD work (same as maintainer workflow)
# ... write tests, write code, run pytest ...

# 4. Commit with sign-off
git commit -s -m "feat: description"

# 5. Create PR to upstream
./scripts/contribute
```

## Key Differences from Maintainer Workflow

| Aspect | Maintainer (`auto-pr`) | Contributor (`contribute`) |
|--------|------------------------|---------------------------|
| Push target | Upstream directly | Your fork |
| PR creation | refs/for/dev/branch | Fork → upstream/dev PR |
| CI polling | Waits and auto-merges | Exits after PR creation |
| Merge | Automatic on CI pass | Requires maintainer approval |

## Conflict Resolution: First Come, First Serve

If two contributors work on overlapping areas:
1. Whoever gets their PR merged first "wins"
2. The other contributor must rebase on the updated dev
3. No special coordination is expected or required
4. CI will fail on the second PR if there are conflicts

This is standard git workflow - small, focused PRs reduce conflict risk.

## After PR Merge

Once a maintainer merges your PR:
```bash
# Sync your fork with upstream
git checkout dev
git fetch upstream
git merge upstream/dev
git push origin dev

# Delete your feature branch
git branch -d yourname/feat/description
```
