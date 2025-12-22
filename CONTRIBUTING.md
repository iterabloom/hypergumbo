# Contributing

We use the **Developer Certificate of Origin (DCO)** instead of a CLA.

## Sign-off required
All commits must include a `Signed-off-by:` line.

Use:
```bash
git commit -s -m "your message"
```

This adds your sign-off automatically. By signing off, you agree to the terms in `DCO`.

## Canonical forge

Codeberg is the source of truth for issues/PRs. GitHub is a mirror.
## Pull Request Workflow (AGit)

We use **Safe Trunk Based Development**. Direct pushes to `main` are blocked.
Instead, use the "Pure Git" workflow to open Pull Requests directly from your terminal:

```bash
# 1. Commit your changes
git commit -s -m "feat: my cool change"

# 2. Push to open a PR (Codeberg/Forgejo specific)
#    This pushes your HEAD to a magic ref that creates a PR targeting 'main'
git push origin HEAD:refs/for/main -o title="feat: my cool change" -o description="Detailed description here..."
```

- The output will provide a link to the PR.
- Wait for CI to pass.
- Merge via the web UI or API.
