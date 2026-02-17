# hypergumbo-tracker

A structured work tracker for AI agent governance. Replaces fragile grep-based
markdown tracking (scanning for `**TODO**` markers in ledger files) with
append-only YAML op-logs that are git-merge-safe, causally ordered via Lamport
clocks, and support field-level access control. Agents get structured task
selection; humans get locks, tier control, and a TUI.

## Install

```bash
pip install hypergumbo-tracker
```

This gives you:

- **`hypergumbo-tracker`** — CLI with 24 subcommands (query, write, governance)
- **`hypergumbo-tracker tui`** — interactive terminal UI (requires the `tui` extra: `pip install hypergumbo-tracker[tui]`)
- **`hypergumbo-tracker-textconv`** — git textconv driver for readable diffs on `.ops` files

What you do **not** get: repo-specific hooks (pre-commit, stop hook). Wire those
up yourself — see [Stop Hook Integration](#stop-hook-integration) and
[Pre-commit Integration](#pre-commit-integration).

## Deployment: Two-User Setup (Recommended)

The tracker distinguishes agents from humans using `os.getuid()` — a
non-forgeable UNIX identity. This matters because certain operations (locking
fields, stealthing items, clearing discussion) are human-only.

### Why two users?

- **Non-forgeable identity.** An agent process cannot fake `os.getuid()` without
  sudo (which it should not have).
- **File permissions protect config.** `config.yaml` is owned by the human user;
  the agent can read it but not modify it.
- **Locks have teeth.** When a human locks a field, the agent physically cannot
  write an unlock op (the CLI raises `HumanAuthorityError`).

### How to set it up

```bash
# 1. Create an agent user
sudo useradd -m jgstern_agent

# 2. Create a shared group
sudo groupadd project-dev
sudo usermod -aG project-dev jgstern        # human
sudo usermod -aG project-dev jgstern_agent  # agent

# 3. Set group ownership + setgid on .ops dirs
#    (so new files inherit group ownership)
sudo chgrp -R project-dev .agent/tracker .agent/tracker-workspace
sudo chmod -R g+rws .agent/tracker/.ops .agent/tracker-workspace/.ops
```

### Configuring agent detection

In `config.yaml`, the `actor_resolution.agent_usernames` field controls which
usernames are treated as agents. It uses `fnmatch` glob patterns:

```yaml
actor_resolution:
  agent_usernames:
    - "*_agent"      # matches jgstern_agent, deploy_agent, etc.
    - "bot_*"        # matches bot_ci, bot_review, etc.
```

Default: `["*_agent"]`.

### Single-user degradation

Everything still works with one user, but governance becomes a social contract:

- Locks are enforced by the CLI, not the OS — the agent could bypass them by
  writing raw YAML (but a well-behaved agent won't).
- `config.yaml` protection is moot — same user owns everything.
- Human-only operations (`lock`, `stealth`, `discuss --clear`) are unavailable
  unless you override actor resolution.

## Setup

```bash
# Initialize tracker structure in your repo
hypergumbo-tracker init
```

This creates:

```
.agent/
├── tracker/                        # Canonical tier (shared with upstream)
│   ├── .ops/                       # Op-log files (merge=union via .gitattributes)
│   ├── config.yaml.template        # Tracked governance config (shared)
│   └── config.yaml                 # Gitignored local override (human-owned)
└── tracker-workspace/              # Workspace tier (fork-local)
    ├── .ops/                       # Op-log files
    └── stealth/                    # Stealth tier (gitignored, local-only)
```

If `config.yaml.template` exists, `init` copies it to `config.yaml`. Edit
`config.yaml` to customize behavior for your environment (it is gitignored).

## Core Concepts

### Items

Every tracked thing — an invariant violation, a work item, a meta-invariant — is
an **item**. Items have an ID, kind, status, priority, title, and optional fields
like description, tags, dependencies, and discussion.

### IDs (Proquints)

Item IDs are content-hash proquints: `INV-lusab-bired`, `WI-nipam-fotil`.
They're deterministic (same content → same ID), collision-resistant (SHA-256),
and human-pronounceable (unlike hex UUIDs).

### Kinds

Kinds define the shape of an item. The default config provides three:

| Kind | Prefix | Purpose |
|------|--------|---------|
| `invariant` | `INV` | Discovered invariant with root cause analysis |
| `meta_invariant` | `META` | Cross-language coverage tracking |
| `work_item` | `WI` | General work item |

Each kind can define a `fields_schema` with typed fields (`text`, `integer`,
`list`, `boolean`). Add custom kinds in `config.yaml`.

### Statuses

Default statuses, in lifecycle order:

| Status | Meaning |
|--------|---------|
| `todo_hard` | Hard-blocking: investigate deeply, assume structural |
| `todo_soft` | Soft-blocking: address or defer freely |
| `in_progress` | Work in progress |
| `done` | Completed |
| `deferred` | Deferred for later |
| `wont_do` | Will not do |

The `stop_hook` config section controls which statuses are **blocking**
(prevent agent from stopping) and which are **resolved** (work is done):

```yaml
stop_hook:
  blocking_statuses: [todo_hard, todo_soft]
  resolved_statuses: [done, deferred, wont_do]
```

### Tiers

Items live in one of three tiers:

| Tier | Storage | Git-tracked | Purpose |
|------|---------|-------------|---------|
| **canonical** | `.agent/tracker/.ops/` | Yes | Shared with upstream |
| **workspace** | `.agent/tracker-workspace/.ops/` | Yes | Fork-local |
| **stealth** | `.agent/tracker-workspace/stealth/` | No (gitignored) | Local-only, invisible to git |

Movement between tiers: `promote` (workspace→canonical), `demote`
(canonical→workspace), `stealth` (workspace→stealth, human-only), `unstealth`
(stealth→workspace, human-only).

### Priorities

Integer 0–4. Lower = higher priority. Default: 2.

### Dependencies (`before` links)

`X.before = [Y]` means "finish X before starting Y." The `ready` command
respects this: Y won't appear until X is resolved.

## Agent Workflows

### Add an item

```bash
hypergumbo-tracker add --kind work_item --title "Add Dart analyzer" --priority 1
hypergumbo-tracker add --kind invariant --title "Parser crash on nested generics" \
  --priority 0 --field statement="Nested generics cause infinite loop" \
  --field root_cause="Recursive descent doesn't track depth" \
  --tag analysis_quality
```

Options: `--status`, `--priority`, `--parent`, `--tag` (repeatable),
`--before` (repeatable), `--description`, `--pr-ref`, `--justification`,
`--field key=value` (repeatable), `--tier {canonical,workspace,stealth}`,
`--json`.

Default tier is `workspace`. Default status is the first blocking status
(`todo_hard`).

### Pick your next task

```bash
hypergumbo-tracker ready --limit 5
```

Returns unblocked, non-duplicate items in blocking statuses, sorted by priority
then creation time. Example output:

```
  1  INV-lusab-bired  todo_hard  P0  [canonical]   Fix parser crash on nested generics
  2  META-kojot-zukot todo_hard  P0  [workspace]   Extend to all C-family languages
  3  WI-nipam-fotil   todo_soft  P1  [workspace]   Add Dart analyzer
```

Use positional aliases from the last `ready` output:

```bash
hypergumbo-tracker update :1 --status in_progress
```

### Update an item

```bash
hypergumbo-tracker update INV-lusab-bired --status done --pr-ref "PR #42"
hypergumbo-tracker update WI-nipam-fotil --add-tag language_additions --priority 0
```

Options: `--status`, `--priority`, `--title`, `--parent`, `--pr-ref`,
`--justification`, `--description`, `--add-tag`/`--remove-tag`,
`--add-before`/`--remove-before`,
`--add-duplicate-of`/`--remove-duplicate-of`,
`--add-not-duplicate-of`/`--remove-not-duplicate-of`,
`--field key=value`, `--json`.

### Show item details

```bash
hypergumbo-tracker show INV-lusab-bired
hypergumbo-tracker show INV-lusab-bired --json   # machine-readable
```

### List items with filters

```bash
hypergumbo-tracker list --status todo_hard --kind invariant --tag analysis_quality
hypergumbo-tracker list --tier canonical --limit 10 --json
```

### Add discussion

```bash
hypergumbo-tracker discuss INV-lusab-bired "Confirmed: affects Java and Kotlin generics"
```

Discussion entries are rate-limited (200K tokens/day per item, soft cap at 20
entries with a warning suggesting `--summarize`).

## Human Workflows

### Interactive TUI

```bash
hypergumbo-tracker tui
```

Responsive terminal interface with three layout tiers based on terminal size:

- **Compact** (40×16+): full-width table, Enter/Esc to toggle detail view
- **Standard** (60×20+): two-pane layout (list + detail panel)
- **Wide** (120×38+): enhanced standard with extra columns and activity log

Keybindings: `q` quit, `t` toggle tree/table, `f` filter, `d` discuss,
`D` clear discussion, `m` move tier, `n` new item, `e` edit, `p` set parent,
`b` edit dependencies, `l` lock/unlock.

### Lock fields

Prevent agents from modifying specific fields:

```bash
hypergumbo-tracker lock INV-lusab-bired status priority
hypergumbo-tracker unlock INV-lusab-bired status
```

Lockable fields: `status`, `priority`, `title`, `parent`, `description`,
`justification`, `pr_ref`, `tags`, `before`, `duplicate_of`,
`not_duplicate_of`, `discussion`, and any custom field.

Locks are checked cross-branch (via `git cat-file --batch` on configured
`lamport_branches`). If a field is locked on any branch, agents cannot modify it
on any branch.

### Manage discussion

```bash
# Clear all discussion (human-only)
hypergumbo-tracker discuss INV-lusab-bired --clear

# Replace discussion with a summary
hypergumbo-tracker discuss INV-lusab-bired --summarize "Root cause confirmed in parser.py:L42. Fix PR #42 merged. Monitoring for regression."
```

### Move between tiers

```bash
hypergumbo-tracker promote WI-nipam-fotil    # workspace → canonical
hypergumbo-tracker demote INV-lusab-bired    # canonical → workspace
hypergumbo-tracker stealth WI-nipam-fotil    # workspace → stealth (human-only)
hypergumbo-tracker unstealth WI-nipam-fotil  # stealth → workspace (human-only)
```

## Stop Hook Integration

The tracker provides three commands for integrating with autonomous-agent stop
hooks (the mechanism that prevents an agent from stopping work while blocking
items remain):

### count-todos

```bash
hypergumbo-tracker count-todos          # all blocking items
hypergumbo-tracker count-todos --hard   # only todo_hard
hypergumbo-tracker count-todos --soft   # only todo_soft
```

Returns the count of items in blocking statuses. Your stop hook calls this and
blocks stopping if count > 0.

Respects `stop_hook.scope` config:
- `scope: all` (default) — counts canonical + workspace + stealth
- `scope: workspace` — counts workspace + stealth only (for forks)

### hash-todos

```bash
hypergumbo-tracker hash-todos
```

Returns a SHA-256 fingerprint of all blocking items (sorted by ID, status,
title). Use this for **circuit-breaker detection**: if the hash hasn't changed
between consecutive stop attempts, the agent has made no progress and should be
allowed to stop (prevents infinite loops).

### guidance

```bash
hypergumbo-tracker guidance [--guidance-dir ~/my/guidance/]
```

Generates a markdown file listing blocking items sorted by priority. Default
directory: `~/hypergumbo_lab_notebook/guidance_log/`. Returns the file path so
your stop hook can point the agent to it.

### Example stop hook integration

```bash
#!/usr/bin/env bash
# In your pre-commit or stop hook:

count=$(hypergumbo-tracker count-todos 2>/dev/null || echo 0)
if [ "$count" -gt 0 ]; then
  guidance_path=$(hypergumbo-tracker guidance --json | jq -r .path)
  echo "BLOCKED: $count items remain. See: $guidance_path"
  exit 1
fi

# Circuit breaker: allow stopping after 5 identical hashes
current_hash=$(hypergumbo-tracker hash-todos)
# ... compare with previous hash, increment counter, etc.
```

## Configuration Reference

The full `config.yaml.template` structure:

```yaml
# Item kinds — define custom kinds with typed field schemas
kinds:
  invariant:
    prefix: INV
    description: "Discovered invariant with root cause analysis"
    fields_schema:
      statement: { type: text, required: true }
      root_cause: { type: text, required: true }
      fix: { type: text }
      verification: { type: text }
      regression_tests: { type: list }
      scope: { type: text }
      progress_pct: { type: integer, min: 0, max: 100 }
  meta_invariant:
    prefix: META
    description: "Cross-language coverage tracking"
    fields_schema:
      statement: { type: text, required: true }
      languages_done: { type: list }
      languages_remaining: { type: list }
      progress_pct: { type: integer, min: 0, max: 100 }
  work_item:
    prefix: WI
    description: "A work item"

# Status lifecycle
statuses:
  - todo_hard
  - todo_soft
  - in_progress
  - done
  - deferred
  - wont_do

# Stop hook behavior
stop_hook:
  blocking_statuses: [todo_hard, todo_soft]
  resolved_statuses: [done, deferred, wont_do]
  # scope: all | workspace
  #   all (default): count canonical + workspace + stealth
  #   workspace: count workspace + stealth only (for forks)

# Tags for filtering and categorization
well_known_tags:
  - developer_experience
  - cross_language_linkers
  - analysis_quality
  # ... add your own

# Agent detection (fnmatch patterns on UNIX username)
actor_resolution:
  agent_usernames:
    - "*_agent"

# Branches checked for Lamport clock synchronization and cross-branch locks
lamport_branches:
  - dev
  - main
```

**Config loading order:** `config.yaml` (gitignored, human-owned) →
`config.yaml.template` (tracked, shared) → built-in defaults.

## Fork Workflow

For contributors working on a fork who don't want upstream canonical items
blocking their stop hook:

```bash
# 1. Initialize tracker (if not already done)
hypergumbo-tracker init

# 2. Set workspace scope (human-only)
hypergumbo-tracker fork-setup
```

`fork-setup` writes `stop_hook.scope: workspace` to `config.yaml`, so
`count-todos` and `hash-todos` only count workspace and stealth items — upstream
canonical items won't block your agent.

Items you create default to the workspace tier. When your PR is merged, the
upstream maintainer can `promote` them to canonical.

## Pre-commit Integration

The tracker ships a `validate` command for use in pre-commit hooks:

```bash
#!/usr/bin/env bash
# .githooks/pre-commit (or .git/hooks/pre-commit)

# Validate tracker data on staged .ops files
staged_ops=$(git diff --cached --name-only -- '*.ops')
if [ -n "$staged_ops" ]; then
  hypergumbo-tracker validate $staged_ops --check-locks --strict
  if [ $? -ne 0 ]; then
    echo "Tracker validation failed. Fix issues before committing."
    exit 1
  fi
fi
```

Validation checks:
- YAML parse errors in ops files
- Schema violations (required fields, type constraints)
- Lock violations (`--check-locks`): detects ops that modify locked fields
- Near-duplicate detection (`--similar` for SimHash, `--deep-similar` for
  embedding-based)
- `--strict` treats warnings as errors

### Git textconv for readable diffs

```bash
# .git/config or ~/.gitconfig
[diff "tracker-ops"]
    textconv = hypergumbo-tracker-textconv

# .gitattributes (created by init)
*.ops diff=tracker-ops
```

This shows compiled item state in diffs instead of raw YAML op-logs.

## License

This package is licensed under the [Mozilla Public License 2.0](LICENSE).
Other hypergumbo packages are licensed under AGPL-3.0-or-later.
See the repository root LICENSE and CONTRIBUTING.md for details.
