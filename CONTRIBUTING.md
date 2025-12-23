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
   git push origin HEAD:refs/for/main/feature-branch \
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

1. **Push** — Pushes your branch using Forgejo's AGit workflow (`refs/for/main/<branch>`)
2. **Create PR** — The push automatically creates a PR on Codeberg
3. **Poll CI** — Waits for CI status checks to complete (polls every 10 seconds)
4. **Merge** — Automatically merges when CI passes
5. **Cleanup** — Switches back to `main`, pulls, and deletes the local feature branch

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
- Must be on a feature branch (not `main`)
- Branch must have commits ahead of `main`
- `python3`, `curl`, and `git` must be available

### Usage

```bash
# Basic usage (uses last commit message as PR title)
./scripts/auto-pr

# Custom PR title
./scripts/auto-pr "feat: add new feature"

# Custom title and description
./scripts/auto-pr "feat: add new feature" "Detailed description here"
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
# 1. Make changes on main
git add . && git commit -s -m "feat: my change"

# 2. Create a feature branch (auto-pr requires this)
git checkout -b my-feature

# 3. Run auto-pr
./scripts/auto-pr

# 4. Script handles everything, you end up back on main with changes merged
```

### Troubleshooting

| Error | Cause | Solution |
|-------|-------|----------|
| `FORGEJO_USER not set` | Missing env var | Add to `.env` or export in shell |
| `You are on 'main'` | Must use feature branch | `git checkout -b feature-name` |
| `Could not find open PR` | API timing issue | Wait and retry, or check Codeberg UI |
| `CI Failed` | Tests/linting failed | Fix issues, amend commit, re-run |
| `Merge failed` | Conflicts or permissions | Check PR on Codeberg for details |

### How AGit Works

This script uses Forgejo's AGit flow, which creates PRs via specially-formatted push refs:

```bash
git push origin HEAD:refs/for/main/<branch-name> -o title="..." -o description="..."
```

This is different from GitHub's flow where you push a branch and then create a PR separately. With AGit, the push *is* the PR creation.
