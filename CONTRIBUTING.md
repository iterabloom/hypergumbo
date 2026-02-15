# Contributing

We use the **Developer Certificate of Origin (DCO)** instead of a CLA.

## Sign-off required
All commits must include a `Signed-off-by:` line.
Use `git commit -s` to add this automatically.

## Pull Request Workflow

We use **Safe Trunk Based Development**. Direct pushes to `main` are blocked.

### External Contributors (Fork Workflow)

If you don't have write access to the repo, use the fork-based workflow:

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

Then for each contribution:

```bash
# Sync with upstream
git fetch upstream
git checkout dev
git merge upstream/dev

# Create feature branch
git checkout -b yourname/feat/description

# Make changes, commit with sign-off
git commit -s -m "feat: description"

# Create PR to upstream
./scripts/contribute
```

The `contribute` script pushes to your fork and creates a PR to upstream. Unlike `auto-pr`, it does **not** auto-merge—you'll need to wait for maintainer review.

| Aspect | Maintainer (`auto-pr`) | Contributor (`contribute`) |
|--------|------------------------|---------------------------|
| Push target | Upstream directly | Your fork |
| PR creation | refs/for/dev/branch | Fork → upstream/dev PR |
| CI polling | Waits and auto-merges | Exits after PR creation |
| Merge | Automatic on CI pass | Requires maintainer approval |

After your PR is merged, sync your fork:

```bash
git checkout dev
git fetch upstream
git merge upstream/dev
git push origin dev
```

### Tracker & Fork Workflow

The project uses a YAML-backed structured tracker (see [ADR-0013](docs/adr/0013-structured-tracker.md)) with three visibility tiers: **canonical** (shared with upstream), **workspace** (local to your fork), and **stealth** (never committed).

**Initial setup** (run once after cloning your fork):

```bash
# Set tracker scope to workspace so your agent isn't blocked by upstream items
scripts/tracker fork-setup
```

This sets `stop_hook.scope: workspace` in your local config, meaning the stop hook only counts your workspace items (not upstream's canonical items) when deciding whether the agent can stop.

**How it works:**

- Your agent writes tracker items to `.agent/tracker-workspace/` by default
- Upstream's canonical items in `.agent/tracker/` are read-only context
- `./scripts/contribute` automatically excludes workspace files from upstream PRs
- The pre-push hook warns if you accidentally push workspace files to upstream

**Promoting items to canonical:**

If your workspace item should become part of upstream's tracker (e.g., a discovered invariant that affects the whole project), promote it:

```bash
scripts/tracker promote <ITEM-ID>
```

This moves the item from workspace to canonical. Include the promoted item in a separate PR.

### Maintainers: The Automated Way (Recommended)
We provide a script that handles pushing, waiting for CI, and merging automatically.

```bash
./scripts/auto-pr "feat: my change title"
```

See [auto-pr Documentation](#auto-pr-documentation) below for details.

### Maintainers: The Manual "Pure Git" Way
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

## Licensing

This repository uses dual licensing:

- **AGPL-3.0** for the core hypergumbo tool (`packages/hypergumbo-core/`, `packages/hypergumbo-lang-*/`)
- **MPL-2.0** for the tracker package (`packages/hypergumbo-tracker/`)

**SPDX headers:** Every `.py` source file must include a license header as its first line:

```python
# SPDX-License-Identifier: AGPL-3.0-or-later
```

or for the tracker package:

```python
# SPDX-License-Identifier: MPL-2.0
```

**DCO covers both licenses.** Your `Signed-off-by` line certifies you have the right to submit under the applicable license. No CLA is required.

## Canonical forge
Codeberg is the source of truth for issues/PRs. GitHub is a mirror.

## Testing

### Smart Test Selection (ADR-0010)

When you run `pytest`, you automatically get **smart-test**, which runs only tests affected by your changes. This speeds up the development cycle.

```bash
pytest                    # Runs affected tests only (via smart-test)
pytest --full             # Runs complete test suite
pytest tests/test_foo.py  # Specific file (still goes through smart-test)
pytest --version          # Fast-path, no smart-test overhead
```

#### How It Works

```
pytest invocation
       │
       ▼
┌─────────────────────────────────────┐
│  .venv/bin/pytest (wrapper)         │
│  - Fast-path for --version, --help  │
│  - Checks SMART_TEST_ACTIVE env var │
│  - Delegates to scripts/smart-test  │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  scripts/smart-test                 │
│  - Sets SMART_TEST_ACTIVE=1         │
│  - Finds affected tests via slice   │
│  - Calls pytest with affected tests │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  .venv/bin/pytest (wrapper again)   │
│  - Sees SMART_TEST_ACTIVE=1         │
│  - Calls python -m pytest directly  │
└─────────────────────────────────────┘
```

#### Self-Healing

If `pip install pytest` overwrites the wrapper, it repairs automatically on the next pytest run:

```
pip install pytest  →  Overwrites wrapper with pip's entry point
       │
       ▼
pytest invocation
       │
       ▼
┌─────────────────────────────────────┐
│  conftest.py (pytest_configure)     │
│  - Detects wrapper is broken        │
│  - Runs: install-hooks --repair     │
│  - Prints: "wrapper repaired"       │
│  - Re-execs through fixed wrapper   │
└─────────────────────────────────────┘
```

#### Escape Hatches

```bash
python -m pytest ...              # Bypass wrapper completely
SMART_TEST_ACTIVE=1 pytest ...    # Skip smart-test, use real pytest
./scripts/install-hooks --repair-shims  # Manual repair
```

### Setup for Smart Test Selection

When you run `install-hooks`, you may see:

```
⚠️  No stable hypergumbo found
   smart-test will fall back to running full test suite
```

This means smart-test can't compute affected tests, so it runs everything (~10 min). To enable fast test selection (~30 sec for small changes), install a stable hypergumbo release **outside** your dev venv:

```bash
# Install stable hypergumbo via pipx (recommended)
pipx install hypergumbo

# Or via pip --user (if pipx not available)
python3 -m pip install --user hypergumbo
```

This installs the PyPI release to `~/.local/bin/hypergumbo`, which smart-test uses for `slice --files` analysis.

**Why a separate install?** This is a bootstrap safety measure: we use a known-good release to analyze the code under development, avoiding the "testing hypergumbo with itself" paradox. See [ADR-0010](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/adr/0010-modular-packages-and-smart-testing.md) for design rationale.

### Running Tests Locally

```bash
# Fast parallel run with coverage (goes through smart-test)
pytest -n auto --cov=src --cov-fail-under=100

# Sequential run (for debugging)
pytest --cov=src --cov-fail-under=100

# Bypass smart-test for full control
SMART_TEST_ACTIVE=1 pytest -n auto --cov=src --cov-fail-under=100
```

### Embedding Tests and sentence-transformers

Some tests require `sentence-transformers` (which depends on PyTorch) for testing embedding-based features like `sketch_precomputed`.

**CI behavior:** CI does **not** attempt to install sentence-transformers. It only checks if it's already available. If not installed, embedding-dependent tests are skipped. This keeps CI fast and avoids flaky installs on resource-constrained runners.

**Local development:** To run embedding tests locally, install sentence-transformers:

```bash
pip install sentence-transformers
```

This works on most development machines where PyTorch wheels are available (Linux x86_64, macOS, Windows).

**Test that requires embeddings:** `test_run_behavior_map_stores_sketch_precomputed` in `packages/hypergumbo-core/tests/test_cli_run_behavior_map.py` tests the `sketch_precomputed` feature which uses embeddings. Other tests pass `include_sketch_precomputed=False` to skip embedding-dependent code paths.

### Coverage Configuration

We maintain 100% test coverage. Two coverage configurations exist:

| Config | Used When | Omits |
|--------|-----------|-------|
| Default (`pyproject.toml`) | Local dev, CI with embeddings | Embedding-only modules |
| `.coveragerc.no-embeddings` | CI without embeddings (Python 3.10 matrix) | Embedding-only modules + `_embedding_data.py` |

**When to use `# pragma: no cover`:**

1. **Embedding-only code paths** — Code that only executes when sentence-transformers is available:
   ```python
   if embeddings_dir:  # pragma: no cover - only when embeddings available
       print(f"Embeddings cached: {embeddings_dir}")
   ```

2. **Python 3.11+ features** — CI tests on Python 3.10, so 3.11+ features aren't covered:
   ```python
   try:
       import tomllib  # pragma: no cover - Python 3.11+
       data = tomllib.loads(content)  # pragma: no cover
   except ImportError:
       # fallback for Python 3.10
   ```

3. **Optional dependencies** — Code paths using non-required packages:
   ```python
   try:
       import tomli  # pragma: no cover - optional dependency
       data = tomli.loads(content)  # pragma: no cover
   except ImportError:
       data = None  # fallback
   ```

**Release workflow note:** The release CI (`release.yml`) runs a test matrix on Python 3.10-3.13. The 3.10 jobs use `.coveragerc.no-embeddings` since sentence-transformers may not be available. If you add new embedding-related code, ensure it's either omitted or marked with pragma.

---

## auto-pr Documentation

The `scripts/auto-pr` script automates the entire PR lifecycle: push, CI polling, and merge.

### What it does

1. **Push** — Pushes your branch using Forgejo's AGit workflow (`refs/for/dev/<branch>`)
2. **Create PR** — The push automatically creates a PR on Codeberg
3. **Poll CI** — Waits for CI status checks to complete (polls every 10 seconds)
4. **Merge** — Uses **fast-forward merge** by default (preserves commit bodies + DCO)
5. **Cleanup** — Switches back to `dev`, pulls, and deletes the local feature branch

If the remote is unavailable (503 errors, network issues), the PR is **queued locally** and can be pushed later with `./scripts/auto-pr flush`.

### Merge Strategy

**Default: Fast-forward merge** — Your original commits are preserved exactly as-is, including:
- Full commit message bodies
- DCO `Signed-off-by` lines
- Commit SHAs remain unchanged

**If branch has diverged:** The script will prompt you to rebase first:
```bash
git fetch origin dev
git rebase origin/dev
./scripts/auto-pr
```

**Emergency fallback: `--squash`** — Only if fast-forward fails, rebasing fails, and it's an emergency:
```bash
./scripts/auto-pr --squash
```
This will:
- Warn and ask for confirmation
- Squash merge with `[from <sha>]` in subject for traceability
- Attach original commit body as a **git note** on the squash commit
- Push notes to remote

### Git Notes (for historical commits)

Some historical commits (Jan 9-22 2026) were squash-merged before fast-forward was the default.
Their original bodies are preserved as git notes.

```bash
# Fetch notes from remote
git fetch origin refs/notes/*:refs/notes/*

# View commits with notes
git log --show-notes

# Show note for specific commit
git notes show <sha>
```

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

# Force squash merge (emergency only - use only if fast-forward and rebasing both fail)
./scripts/auto-pr --squash

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

---

## AI Agent Governance Hooks

Hypergumbo includes a vendor-agnostic hook system for AI coding agents operating in autonomous mode. These hooks enforce reflection before stopping, helping prevent workarounds and ensuring structural fixes.

See [ADR-0008](docs/adr/0008-autonomous-governance-and-vendor-agnostic-hooks.md) for full design rationale.

### Hook Structure

```
.agent/
├── hooks/
│   ├── claude-code/stop.sh      # Claude Code Stop hook
│   ├── gemini-cli/after-agent.sh # Gemini CLI AfterAgent hook
│   ├── cursor/stop.sh           # Cursor stop hook
│   └── codex-cli/notify.sh      # Codex CLI notification (limited)
├── stop_reflect.md              # Reflection prompt (shared)
├── LOOP                         # Sentinel file (exists = continue)
└── invariant-ledger.md          # Tracks discovered invariants
```

### Setup by Tool

#### Claude Code

Add to `.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR\"/.agent/hooks/claude-code/stop.sh"
          }
        ]
      }
    ]
  }
}
```

#### Gemini CLI

Add to `.gemini/config.yaml`:

```yaml
hooks:
  AfterAgent:
    - type: command
      command: .agent/hooks/gemini-cli/after-agent.sh
```

#### Cursor

Add to `.cursor/hooks.json`:

```json
{
  "version": 1,
  "hooks": {
    "stop": [
      {
        "command": ".agent/hooks/cursor/stop.sh",
        "loop_limit": 10
      }
    ]
  }
}
```

#### Codex CLI

Add to `~/.codex/config.toml`:

```toml
notify = [".agent/hooks/codex-cli/notify.sh"]
```

**Note:** Codex CLI has limited hook support—it can only notify, not block or inject prompts.

### How It Works

1. **Activation:** Hooks only engage when `AUTONOMOUS_MODE.txt` contains "TRUE" and `.agent/LOOP` exists
2. **Reflection:** When the agent tries to stop, the hook injects `stop_reflect.md` as guidance
3. **Invariant tracking:** Discovered invariants are documented in `.agent/invariant-ledger.md`
4. **Structural fixes:** The reflection protocol encourages fixing root causes, not workarounds

### Disabling Hooks

To disable governance hooks temporarily, use the toggle script:

```bash
./scripts/loop-toggle off    # Disable autonomous mode
./scripts/loop-toggle on     # Re-enable autonomous mode
./scripts/loop-toggle status # Check current state
./scripts/loop-toggle        # Toggle current state
```

This manages both `AUTONOMOUS_MODE.txt` and the `.agent/LOOP` sentinel together.
