# hypergumbo-tracker

Structured work tracker for AI agent governance. Append-only YAML op-logs
that are git-merge-safe, causally ordered, and support field-level access
control. Agents get structured task selection; humans get locks, tier control,
and a TUI.

## Setup

### 1. Install

Install the tracker into your project's virtual environment:

```bash
# As either user (whoever owns the venv)
pip install hypergumbo-tracker
```

### 2. Create an agent user (optional)

The tracker uses `os.getuid()` to distinguish agents from humans. For real
enforcement (not just convention), run the agent as a separate OS user:

```bash
# As the human user (needs sudo)
sudo useradd -m myproject_agent
sudo groupadd project-dev
sudo usermod -aG project-dev yourname
sudo usermod -aG project-dev myproject_agent
```

**Important:** Group membership doesn't take effect in existing shells. Either
log out and back in, or run `newgrp project-dev` in your current session.

If the repo lives under the agent's home directory, the human user also needs
traversal access:

```bash
# As the human user (needs sudo)
sudo chmod o+rx /home/myproject_agent
```

Single-user works fine — governance becomes a social contract instead of
OS-enforced. If your username matches an agent pattern (e.g. `*_agent`),
edit `actor_resolution.agent_usernames` in `config.yaml`.

### 3. Run the wizard

```bash
# As either user (from the repo root)
cd your-repo
htrac setup
```

This creates directories, configures git plumbing (merge=union, textconv),
copies the config template, sets file permissions, and reports anything that
needs attention. It's idempotent — run it again anytime to diagnose issues.

If you did step 2 (two-user setup), set group ownership on the directories
it created so both users can write to ops files:

```bash
# As the human user (needs sudo)
sudo chgrp -R project-dev .agent/tracker .agent/tracker-workspace
sudo chmod -R g+rws .agent/tracker/.ops .agent/tracker-workspace/.ops
```

Verify the human user can write:

```bash
# As the human user (must have run newgrp project-dev or started a new session)
touch .agent/tracker-workspace/.ops/test && rm .agent/tracker-workspace/.ops/test
```

If you get "Permission denied", check that `newgrp project-dev` was run (or
start a new login session).

**Note:** Git does not track file ownership or group permissions. After a fresh
`git clone`, you must re-run `htrac setup` and repeat the `chgrp`/`chmod`
commands above.

You now have:

- **`htrac`** — CLI for agents (`htrac ready`, `htrac add`, `htrac update`, ...)
- **`htrac tui`** — interactive terminal UI for humans
- **`htrac setup`** — re-runnable setup wizard

## Agent usage

These commands are run **as the agent user** (e.g. `myproject_agent`). The
tracker records `by: agent` on each operation, and human-only commands
(`lock`, `stealth`, `discuss --clear`) are blocked.

```bash
htrac ready                    # What should I work on?
htrac update :1 --status in_progress   # Claim the top item
htrac add --kind work_item --title "Add Dart analyzer" --priority 1
htrac update INV-lusab --status done --pr-ref "PR #42"
htrac show INV-lusab           # Full item details
htrac list --status todo_hard  # Filtered listing
htrac discuss INV-lusab "Root cause confirmed in parser.py"
```

Use `htrac <command> --help` for all options. Use `--json` on any command for
machine-readable output.

## Human usage

These commands are run **as the human user** (e.g. `yourname`). The tracker
records `by: human` on each operation, enabling human-only commands that
agents cannot access.

```bash
htrac tui                      # Interactive terminal UI
htrac lock INV-lusab status    # Prevent agent from changing status
htrac stealth WI-nipam         # Hide item from git (human-only)
htrac discuss INV-lusab --clear  # Clear discussion (human-only)
```

The TUI supports three layouts (compact/standard/wide) based on terminal size.
Keybindings: `q` quit, `f` filter, `d` discuss, `m` move tier, `n` new item,
`e` edit, `l` lock/unlock.

Both users can read all items — the access control only applies to writes.

## Core concepts

**Items** have an ID, kind, status (todo_hard → done), priority (0–4, lower =
higher), and optional fields, tags, and discussion.

**IDs** are content-hash proquints: `INV-lusab-bired`, `WI-nipam-fotil` —
deterministic, collision-resistant, pronounceable.

**Kinds** define item shape. Defaults: `invariant` (INV), `meta_invariant`
(META), `work_item` (WI). Add custom kinds in `config.yaml`.

**Tiers** control visibility:

| Tier | Path | Git-tracked | Purpose |
|------|------|-------------|---------|
| canonical | `.agent/tracker/.ops/` | Yes | Shared with upstream |
| workspace | `.agent/tracker-workspace/.ops/` | Yes | Fork-local |
| stealth | `.agent/tracker-workspace/stealth/` | No | Local-only |

Move items between tiers: `promote`, `demote`, `stealth`, `unstealth`.

**Dependencies:** `X.before = [Y]` means finish X before Y. The `ready`
command respects this.

## Stop hook integration

Three commands for autonomous-agent stop hooks:

```bash
# Called by the stop hook (runs as the agent user)
htrac count-todos              # Number of blocking items (0 = ok to stop)
htrac hash-todos               # SHA-256 fingerprint (circuit-breaker detection)
htrac guidance                 # Generate guidance file for the agent
```

Example hook snippet:

```bash
count=$(htrac count-todos 2>/dev/null || echo 0)
if [ "$count" -gt 0 ]; then
  echo "BLOCKED: $count items remain"
  exit 1
fi
```

## Pre-commit integration

```bash
# In .githooks/pre-commit:
staged_ops=$(git diff --cached --name-only -- '*.ops')
if [ -n "$staged_ops" ]; then
  htrac validate $staged_ops --check-locks --strict || exit 1
fi
```

`htrac setup` also configures the git textconv driver so `git diff` shows
compiled item state instead of raw YAML.

## Configuration

Config loading order: `config.yaml` (gitignored, yours to edit) →
`config.yaml.template` (tracked, shared) → built-in defaults.

`htrac setup` copies the template to `config.yaml` if it doesn't exist. See
the template file for the full schema. Key sections:

- **`kinds`** — item types with optional `fields_schema`
- **`statuses`** — lifecycle states
- **`stop_hook`** — which statuses block agent stopping, scope (all vs workspace)
- **`actor_resolution.agent_usernames`** — fnmatch patterns (default: `["*_agent"]`)
- **`lamport_branches`** — branches for clock sync and cross-branch locks

## Fork workflow

```bash
# As the agent user (in your fork)
htrac fork-setup               # Sets scope to workspace-only
```

This makes `count-todos` and `hash-todos` ignore upstream canonical items so
they don't block your agent. Items you create default to workspace; the
upstream maintainer can `promote` them after merge.

## License

[Mozilla Public License 2.0](LICENSE). Other hypergumbo packages are
AGPL-3.0-or-later. See the repository root LICENSE and CONTRIBUTING.md.
