# Contributing

We use the **Developer Certificate of Origin (DCO)** instead of a CLA.

## Sign-off required
All commits must include a `Signed-off-by:` line.
Use `git commit -s` to add this automatically.

## Pull Request Workflow

We use **Safe Trunk Based Development**. Direct pushes to `main` are blocked.

### Option 1: The Automated Agent Way (Recommended)
We provide a script that handles pushing, waiting for CI, and merging automatically.
Requires `FORGEJO_TOKEN` in your environment.

```bash
export FORGEJO_TOKEN="your_token_here"
./scripts/auto-pr "feat: my change title"
```

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
