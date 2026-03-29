## Pre-Commit Playbook
Run these checks before every commit:
```bash
# 1. Verify git identity is configured
git config user.name && git config user.email

# 2. Run tests with coverage (must be 100%)
pytest -n auto --cov-fail-under=100

# 3. If feature status changed: Update CHANGELOG.md. Update emoji indicators in `docs/hypergumbo-spec.md`.

# 4. If fixing a bakeoff signal: Check tracker for open items
./scripts/tracker count-todos
# If count > 0, review with `./scripts/tracker ready` and decide whether to address before committing

# 5. Commit with sign-off
git commit -s -m "feat: description"
```
