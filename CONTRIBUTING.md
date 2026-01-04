# Contributing

We use the **Developer Certificate of Origin (DCO)** instead of a CLA.

## Sign-off required
All commits must include a `Signed-off-by:` line.
Use `git commit -s` to add this automatically.

## Pull Request Workflow

We use **Safe Trunk Based Development**. Direct pushes to `main` are blocked.

### Option 1: The Automated Agent Way (Recommended)
We provide a script that handles pushing, waiting for CI, and merging automatically.

```bash
./scripts/auto-pr "feat: my change title"
```

See [auto-pr Documentation](#auto-pr-documentation) below for details.

### Option 2: The Manual "Pure Git" Way
If you prefer manual control without installing CLI tools like `tea`:

1. **Commit changes** to a feature branch.
2. **Push via AGit:**
   ```bash
   # Replace 'feature-branch' with your actual branch name
   git push origin HEAD:refs/for/dev/feature-branch \
     -o title="feat: description" \
     -o description="Extended details..."
   ```
3. **Wait for CI** (Status check: `CI / pytest (pull_request)`).
4. **Merge** via the Codeberg web UI.

## Canonical forge
Codeberg is the source of truth for issues/PRs. GitHub is a mirror.

---

## auto-pr Documentation

The `scripts/auto-pr` script automates the entire PR lifecycle: push, CI polling, and merge.

### What it does

1. **Push** — Pushes your branch using Forgejo's AGit workflow (`refs/for/dev/<branch>`)
2. **Create PR** — The push automatically creates a PR on Codeberg
3. **Poll CI** — Waits for CI status checks to complete (polls every 10 seconds)
4. **Merge** — Automatically merges when CI passes
5. **Cleanup** — Switches back to `dev`, pulls, and deletes the local feature branch

If the remote is unavailable (503 errors, network issues), the PR is **queued locally** and can be pushed later with `./scripts/auto-pr flush`.

### Requirements

**Environment variables** (set in shell or `.env` file in repo root):

| Variable | Required | Description |
|----------|----------|-------------|
| `FORGEJO_USER` | Yes | Your Codeberg username |
| `FORGEJO_TOKEN` | Yes | Codeberg API token with repo write access |

You can create a `.env` file (git-ignored) in the repo root:
```bash
FORGEJO_USER=your_username
FORGEJO_TOKEN=your_token_here
```

**Prerequisites:**
- Must be on a feature branch (not `main` or `dev`)
- Branch must have commits ahead of `dev`
- `python3`, `curl`, and `git` must be available

### Usage

```bash
# Basic usage (uses last commit message as PR title)
./scripts/auto-pr

# Custom PR title
./scripts/auto-pr "feat: add new feature"

# Custom title and description
./scripts/auto-pr "feat: add new feature" "Detailed description here"

# Queue management (when remote is unavailable)
./scripts/auto-pr list    # Show queued PRs
./scripts/auto-pr flush   # Push queued PRs when remote is back
./scripts/auto-pr --help  # Show all options
```

### The PR_PENDING Gate (for AI agents)

When the script creates a PR, it writes the PR number to `.git/PR_PENDING`. This file signals to automated systems (like AI coding agents) that a PR is in flight and they should wait before starting new work.

**For agents:** Check for this file before starting new tasks:
```bash
test -f .git/PR_PENDING && echo "PR in progress, wait..."
```

The file is automatically removed when the PR is merged.

### Workflow Example

```bash
# 1. Start from dev
git checkout dev && git pull

# 2. Create a feature branch
git checkout -b my-feature

# 3. Make changes and commit
git add . && git commit -s -m "feat: my change"

# 4. Run auto-pr
./scripts/auto-pr

# 5. Script handles everything, you end up back on dev with changes merged
```

### Offline Resilience (vPR Queue)

When Codeberg is unavailable, `auto-pr` automatically:
1. Retries up to 3 times with 10-second delays
2. If still failing, queues as a **vPR (virtual PR)** in `.git/PR_QUEUE`
3. Exits cleanly so you can continue working

**vPR workflow:**
- vPRs form a linear chain (each branches from the previous)
- To add more changes: `git checkout -b new-branch $(./scripts/auto-pr status | grep tip | awk '{print $3}')`
- When Codeberg is back: `./scripts/auto-pr flush`
- Flush pushes ALL vPRs as a **single atomic PR** (individual commits preserved)

### Troubleshooting

| Error | Cause | Solution |
|-------|-------|----------|
| `FORGEJO_USER not set` | Missing env var | Add to `.env` or export in shell |
| `You are on 'main'` | Must use feature branch | `git checkout -b feature-name` |
| `Could not find open PR` | API timing issue | Wait and retry, or check Codeberg UI |
| `CI Failed` | Tests/linting failed | Fix issues, amend commit, re-run |
| `Merge failed` | Conflicts or permissions | Check PR on Codeberg for details |
| `PR queued locally` | Remote unavailable | Run `./scripts/auto-pr flush` when remote is back |

### How AGit Works

This script uses Forgejo's AGit flow, which creates PRs via specially-formatted push refs:

```bash
git push origin HEAD:refs/for/dev/<branch-name> -o title="..." -o description="..."
```

This is different from GitHub's flow where you push a branch and then create a PR separately. With AGit, the push *is* the PR creation.
