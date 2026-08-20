<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
## Pre-Work Playbook
Run these checks before starting any new feature or task:
```bash
# 1. Ensure no auto-pr is in flight (manual PRs don't create this file)
test -f .git/PR_PENDING && echo "STOP: auto-pr awaiting merge" && exit 1

# 2. Flush any queued vPRs if remote is available
./scripts/auto-pr list  # Check if any PRs are queued
./scripts/auto-pr flush # Push them if remote is back

# 3. The authoritative remote is always origin (WI-hajif retired the
#    selfh CI failover; there is no second remote to choose between).
REMOTE=origin

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

**Why step 3 is now trivial (and the history worth keeping):** it used to be a real
decision. On 2026-04-01 the agent skipped failover detection, pulled from origin
(12 commits behind the failover remote), branched from a stale `origin/dev`, and
spent 45+ minutes debugging CI failures caused by base-branch divergence — three
stale PRs before the root cause was found. WI-hajif retired the CI failover, so
there is now exactly one authoritative remote and that class of error is gone by
construction rather than by vigilance. Recorded so nobody reintroduces a second
authoritative remote without knowing what it cost the first time.
