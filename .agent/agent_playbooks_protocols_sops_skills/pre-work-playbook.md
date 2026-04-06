<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
## Pre-Work Playbook
Run these checks before starting any new feature or task:
```bash
# 1. Ensure no auto-pr is in flight (manual PRs don't create this file)
test -f .git/PR_PENDING && echo "STOP: auto-pr awaiting merge" && exit 1

# 2. Flush any queued vPRs if remote is available
./scripts/auto-pr list  # Check if any PRs are queued
./scripts/auto-pr flush # Push them if remote is back

# 3. Determine the authoritative remote (INV-bifud)
#    CRITICAL: During failover, origin (Codeberg) is stale. selfh is authoritative.
#    Branching from the wrong remote causes silent divergence and wasted CI cycles.
if [ -f .git/CI_FAILOVER_ACTIVE ]; then
  REMOTE=selfh
  echo "⚠️  Failover active — using selfh as authoritative remote"
else
  REMOTE=origin
fi

# 4. If you have stashed changes to restore, reset affected-tests.txt first
#    (smart-test regenerates it on every run, causing stash pop conflicts)
git checkout -- "$(git rev-parse --show-toplevel)/.ci/affected-tests.txt" 2>/dev/null

# 5. Sync with dev and main from the AUTHORITATIVE remote
git fetch "$REMOTE" dev
git checkout dev && git merge --ff-only "$REMOTE/dev"

# 6. Check current progress (at your careful discretion, use `head`, `tail`, `sed`, `grep`, etc, for efficient reading)
cat docs/hypergumbo-spec.md
cat CHANGELOG.md

# 7. Create feature branch (now based on the correct remote)
git checkout -b <author>/feat/<short-name>
```

**Why step 3 matters:** On 2026-04-01, the agent skipped failover detection, pulled
from origin (12 commits behind selfh), branched from stale origin/dev, and spent 45+
minutes debugging CI failures caused by base-branch divergence. Three stale PRs were
created before the root cause was identified. The authoritative remote check prevents
this class of error entirely.
