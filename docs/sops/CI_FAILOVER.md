# CI Failover SOP

When Codeberg is down and you have a self-hosted Forgejo with a runner.

## Quick Reference

```
CODEBERG DOWN                          CODEBERG BACK
─────────────                          ─────────────
./scripts/ci-failover engage           ./scripts/ci-failover disengage
  ↓                                      ↓
Work normally (auto-pr, etc.)          Review + merge PR on Codeberg
PRs go to self-hosted Forgejo            ↓
                                       ./scripts/ci-failover disengage-cleanup
```

## Prerequisites

In `.env`:
```bash
SELFHOSTED_FORGEJO_URL=http://forgejo-ci.internal:3000
SELFHOSTED_FORGEJO_TOKEN=<token>
SELFHOSTED_FORGEJO_REPO=user/hypergumbo    # repo slug on self-hosted instance
SELFHOSTED_FORGEJO_USER=<username>          # defaults to FORGEJO_USER if unset
```

The self-hosted Forgejo repo should be a **mirror** of the Codeberg repo (auto-converted to a regular repo during engage, restored on disengage).

## Engage (Codeberg is down)

```bash
./scripts/ci-failover engage
```

This will:
- Convert the mirror to a regular repo (so branches can be pushed)
- Add/update the `selfh` git remote with credentials
- Write `.git/CI_FAILOVER_ACTIVE` flag

After engage, `auto-pr`, `merge-pr`, and `ci-debug` automatically target self-hosted Forgejo. Work normally — create feature branches, run `auto-pr`, merge PRs on the self-hosted instance.

**Governance files** (AGENTS.md, scripts/auto-pr, etc.) still require human review. Use `auto-pr --gov` for those.

## Disengage (Codeberg is back)

Disengage is two steps because `dev` is branch-protected on Codeberg.

### Step 1: Create repatriation PR

```bash
./scripts/ci-failover disengage
```

This will:
- Create a branch with all failover-period commits
- Push it to Codeberg as a PR via AGit flow
- Poll Codeberg CI
- Print the PR link and stop

### Step 2: Human merges, then cleanup

1. **Review and merge** the PR on Codeberg (link printed by step 1)
2. Run:
   ```bash
   ./scripts/ci-failover disengage-cleanup
   ```

This will:
- Verify the PR was merged
- Sync local dev with Codeberg
- Restore the self-hosted Forgejo mirror
- Remove the failover flag

## Status Check

```bash
./scripts/ci-failover status
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `disengage` says PR already created | Merge the existing PR, then run `disengage-cleanup` |
| `disengage-cleanup` says PR not merged | Merge it on Codeberg first |
| `engage` says "already engaged" | Run `./scripts/ci-failover status` to check state |
| Push to selfh fails | Check credentials in `.env`, then `engage --force` to refresh remote URL |
| Working tree not clean | Commit or stash changes first (agent state files are auto-committed) |
