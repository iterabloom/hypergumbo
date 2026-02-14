# 13. Structured Tracker

Date: 2026-02-13
Status: Draft

## Context

The [hypergumbo](https://codeberg.org/iterabloom/hypergumbo) project uses AI agents for autonomous development. These agents are governed by a "stop hook" system (see [ADR-0008](0008-autonomous-governance-and-vendor-agnostic-hooks.md)) that decides whether an agent is allowed to stop working or must continue. The hook counts open work items and invariant violations to determine if the agent has finished.

Currently, the stop hook's input is two markdown files:

- **`.agent/invariant-ledger.md`** (455 lines) — Tracks discovered code invariants (e.g., "every `calls` edge has a non-null caller symbol"), their root causes, fixes, and whether they've been fully generalized across all languages.
- **`~/hypergumbo_lab_notebook/guidance_log/work_items.md`** (35 lines) — A categorized backlog of non-invariant work (developer experience, linkers, CI, etc.).

The stop hook (`stop_logic.sh`) reads these via grep patterns:
```bash
grep -c '^\s*- \*\*TODO!\*\*' "$LEDGER_FILE"    # hard TODOs (block stopping)
grep -c '^\s*- \*\*TODO\*\*[^!]' "$LEDGER_FILE"  # soft TODOs (block stopping)
```

This works but is fragile and limited:

1. **No schema enforcement** — Nothing prevents malformed entries, status typos ("FIXD" instead of "FIXED"), or vocabulary drift. Agents can write whatever they want.
2. **No deduplication** — Agents can create redundant items with no detection.
3. **No human interface** — Only agents read/write these files. Humans have no ergonomic way to browse, filter, triage, or override agent decisions.
4. **No access control** — The human cannot lock a field to prevent agent modification, nor can they have a private conversation with the agent about a specific item.
5. **Fragile parsing** — The grep patterns break if the markdown format drifts even slightly.

### Alternatives Evaluated and Rejected

- **SQLite as primary storage** — Binary file, opaque to git diffs, can't be meaningfully reviewed in PRs. (SQLite is used as a gitignored read cache — see [Read Cache](#read-cache-sqlite) — but the source of truth is always the append-only op log files.)
- **[git-bug](https://github.com/MichaelMure/git-bug)** — Mature (6+ years, ~100k LOC Go) but rigid: status is hardcoded to Open/Closed, no parent-child, extending requires Go compilation. Wrong language for a Python project. However, git-bug's core insight — storing immutable operations rather than mutable snapshots — directly inspired our operation-log storage model (see [Item Schema](#item-schema-operation-log)). git-bug's "entity ID = SHA-256 hash of the first operation" also inspired our hash-based ID scheme (see [Key Design Decisions](#key-design-decisions)).
- **[beads](https://github.com/steveyegge/beads)** — Feature-rich (~250k LOC Go) but overengineered for our needs. Beads' per-field resolution strategies (terminal-status-wins, timestamp tiebreakers) inspired the compile rules in [Compile Rules](#compile-rules-conflict-resolution). Beads' hash-based IDs (UUID → truncated SHA-256) validated the collision-free distributed ID approach we adopt in [Key Design Decisions](#key-design-decisions).
- **Separate git repo** — Unnecessary complexity. The YAML files are small and merge cleanly in the same repo.

## Decision

Replace both markdown files with a **YAML-backed structured tracker** that provides:

- **Schema-enforced controlled vocabulary** — Statuses, kinds, and other fields are validated against a config file. Invalid values are rejected.
- **CLI for agents** (`scripts/tracker`) — Replaces grep patterns. The stop hook calls `scripts/tracker count-todos --hard` instead of grepping markdown.
- **TUI for humans** (`scripts/tracker tui`) — A [Textual](https://textual.textualize.io/)-based terminal UI for browsing, editing, filtering, and discussing items.
- **Append-only operation log** — Each item's history is stored as a hidden YAML file (`.ops` extension, dotfile naming) in a dotdir, containing an ordered list of immutable operations. Current state is derived by replaying ops. The append-only format means git can always auto-merge concurrent edits — no custom merge driver needed. The op log files are deliberately hidden from agents via dotdir + dotfile + explicit AGENTS.md rules (see [Agent Context Protection](#agent-context-protection)).
- **Three-tier visibility** — Items live in one of three tiers: **canonical** (committed, shared with upstream), **workspace** (committed, backed up to fork remote, excluded from upstream PRs), or **stealth** (gitignored, never leaves the machine). Visibility only moves up via `promote` (workspace → canonical) or down via `demote` / `stealth`. This directory-level separation cleanly handles the fork workflow (see [Three-Tier Visibility](#three-tier-visibility)).
- **Fork-safe by design** — Contributors fork the repo and get upstream's canonical tracker as read-only context. Their agent writes to workspace (committed to the fork, backed up to the fork's remote). `scripts/contribute` automatically excludes workspace from upstream PRs. Canonical items can be promoted as separate, intentional PRs (see [Three-Tier Visibility](#three-tier-visibility)).
- **Field locking** — Humans can lock any field to prevent agent modification.
- **Discussion threads** — Each item has an async discussion field where human and agent exchange messages.
- **Parent-child relationships** — Items can form trees via an optional `parent` field.
- **Configurable kinds** — Item types are defined in `config.yaml`, not hardcoded. Users can add new kinds (with custom ID prefixes) without changing code.
- **SQLite read cache** — A gitignored SQLite database caches compiled snapshots for fast queries. The op log files remain the source of truth; the cache accelerates the read path (`list`, `ready`, `count-todos`) so agents can query the tracker on every task-selection cycle without parsing hundreds of op log files.

### Data Model

#### Design Principle: One Item Type, Configurable Kinds

Instead of hardcoding separate types like invariants, work items, issues, bugs, etc, all items share a single universal schema. The `kind` field determines what type of item it is, and valid kinds are defined in `config.yaml`. To add a new kind (say, "latke"), you edit the config — no code changes. Each kind can optionally declare a `fields_schema` that names the known fields for its `fields` dict, their types, and whether they're required (see [Key Design Decisions](#key-design-decisions)). Kinds without a schema have fully open-ended fields.

#### Storage Layout

```
.agent/
├── tracker/                          # canonical tier (committed, shared with upstream)
│   ├── config.yaml.template         # tracked governance rules (kinds, statuses, field schemas)
│   ├── config.yaml                  # gitignored, generated by `init`, human-owned (mode 644)
│   └── .ops/                        # committed op logs (dotdir — agents should not read)
│       ├── .INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit.ops
│       ├── .META-dabop-firuz-hadol-jikam-losib-mufad-nokap-pidul.ops
│       └── .WI-fodak-humit-kobap-linud-rasib-sufag-tohim-vukad.ops
└── tracker-workspace/                # workspace tier (committed, fork-local)
    ├── config.yaml.template         # tracked template for per-fork overrides
    ├── config.yaml                  # gitignored, generated by `init` or `fork-setup`
    ├── .ops/                        # committed op logs (backed up to fork remote)
    │   ├── .WI-gutob-kinap-sifad-tuhom-badol-fikam-gusib-hilap.ops
    │   └── .INV-hamoj-libud-mifog-nakip-rosab-sudol-tifag-vukim.ops
    └── stealth/                     # stealth tier (gitignored, never leaves machine)
        └── .WI-julad-mifog-vakob-zikap-bomud-diral-fusob-gihap.ops

$XDG_CACHE_HOME/hypergumbo-tracker/<repo-fingerprint>/
├── canonical.cache.db               # SQLite read cache for canonical tier
├── canonical.last_list              # positional alias stash for canonical
├── workspace.cache.db               # SQLite read cache for workspace tier
└── workspace.last_list              # positional alias stash for workspace
```

Two tracker directories, each containing op log files in a `.ops/` dotdir. One file per item, flat within each dotdir. Each file is a dotfile (`.INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit.ops`) containing an append-only operation log (see [Item Schema](#item-schema-operation-log)). The kind is inside the YAML content, not encoded in the path. The `.ops` extension and dotfile naming are deliberate — they prevent agents from casually reading the raw operation history (see [Agent Context Protection](#agent-context-protection)).

Cache and ephemeral files (`.cache.db`, `.last_list`) live outside the repo tree in `$XDG_CACHE_HOME/hypergumbo-tracker/<repo-fingerprint>/` (see [Read Cache](#read-cache-sqlite)). The repo-fingerprint key (hash of remote URL + first commit SHA) allows multiple checkouts of the same repo to share a cache, and avoids ownership/permission conflicts when two OS users share a checkout.

**Canonical** (`.agent/tracker/`) is the repo's institutional memory — committed, shared across forks, included in upstream PRs. **Workspace** (`.agent/tracker-workspace/`) is the agent's personal working memory — committed and pushed to the fork's remote for backup, but excluded from upstream PRs by `scripts/contribute`. **Stealth** (`.agent/tracker-workspace/stealth/`) is gitignored and never leaves the machine.

The store reads all three tiers transparently via `TrackerSet` — agents and humans see a unified merged view. Items are tagged `[C]`, `[W]`, or `[S]` in CLI output to indicate their tier. The agent writes to workspace by default; items are promoted to canonical explicitly (see [Three-Tier Visibility](#three-tier-visibility)). **Agents should never read op log files directly** — they use `scripts/tracker show <ID>` or `scripts/tracker show <ID> --json` to get compiled current state (see [Agent Context Protection](#agent-context-protection)).

#### Config File

The repo tracks a **template** (`config.yaml.template`). The actual config (`config.yaml`) is gitignored and human-owned via OS file permissions — the standard `.env.example` → `.env` pattern (see [Security Model](#security-model)).

`scripts/tracker init` (run by the human user) copies the template, lets the human configure per-deployment fields, and sets ownership: `chown <human_user> config.yaml && chmod 644 config.yaml`. The agent can read config (needs to, for validation) but cannot write it — the OS enforces this.

The validation code loads config from a chain: `config.yaml` if it exists, otherwise `config.yaml.template`. CI uses the template directly. `validate` warns when `config.yaml` contains kinds or statuses not present in `config.yaml.template` — this catches local-only additions that would fail in CI.

**`config.yaml.template`** (tracked, shared governance rules):

```yaml
kinds:
  invariant:
    prefix: INV
    description: "Discovered invariant with root cause analysis"
    fields_schema:
      statement:
        type: text
        required: true
        description: "The invariant being tracked"
      root_cause:
        type: text
        required: true
        description: "Why the invariant was violated"
      fix:
        type: text
        description: "How the root cause was addressed"
      verification:
        type: text
        description: "How the fix was verified across languages/constructs"
      regression_tests:
        type: list
        description: "Test cases that guard against recurrence"
      scope:
        type: text
        description: "Which languages/constructs are affected"
      progress_pct:
        type: integer
        min: 0
        max: 100
        description: "Percentage of affected scope addressed"

  meta_invariant:
    prefix: META
    description: "Cross-cutting invariant tracking multi-language coverage"
    fields_schema:
      statement:
        type: text
        required: true
        description: "The cross-cutting invariant"
      languages_done:
        type: list
        description: "Languages where the invariant holds"
      languages_remaining:
        type: list
        description: "Languages not yet checked or fixed"
      progress_pct:
        type: integer
        min: 0
        max: 100
        description: "Percentage of languages addressed"

  work_item:
    prefix: WI
    description: "Backlog item for non-invariant work"
    # No fields_schema — work items use title/description only.
    # Omitting fields_schema means: no known fields, no validation,
    # no warnings on arbitrary keys.

  # Add new kinds freely — no code changes needed:
  # latke:
  #   prefix: LTK
  #   description: "Jews for a free Palestine"
  #   fields_schema:
  #     filling:
  #       type: text
  #       required: true

# Status vocabulary. All statuses are config-defined — no Python enum.
# With OS-permission-protected config, this is harder for the agent to
# modify than source code (see [Security Model](#security-model)).
statuses:
  - todo_hard       # investigate deeply, assume structural
  - todo_soft       # address or defer freely
  - in_progress     # actively being worked on
  - done            # completed
  - deferred        # explicitly deferred (justification required)
  - wont_do         # decided against

# Stop hook semantics. blocking_statuses are what count-todos counts.
# resolved_statuses are what the `before` soft-blocking filter treats
# as "done" (predecessor resolved). These sets must not overlap, and
# blocking_statuses must be non-empty (otherwise the stop hook is toothless).
stop_hook:
  blocking_statuses: [todo_hard, todo_soft]
  resolved_statuses: [done, deferred, wont_do]

# Freeform tags for categorization. The config lists "well-known" tags for
# autocomplete in the TUI. Validation does NOT reject unknown tags (open vocabulary).
well_known_tags:
  - developer_experience
  - cross_language_linkers
  - analysis_quality
  - language_additions
  - ci_infrastructure
  - framework_patterns
```

**Per-deployment fields** (set during `init`, in `config.yaml` only — not in the template):

```yaml
# Actor resolution. Usernames matching these patterns are resolved as "agent".
# All other usernames resolve as "human". Default: ["*_agent"].
# See [Security Model](#security-model).
actor_resolution:
  agent_usernames: ["*_agent"]

# Stop hook scoping. Controls which tiers count-todos aggregates.
# On upstream repos, "all" counts canonical + workspace.
# On forks, "workspace" so the fork agent isn't blocked by
# upstream's canonical items (which it can read but not close).
# Detected automatically by scripts/tracker fork-setup.
stop_hook:
  scope: all                   # "all" | "workspace"
```

Validation enforces:
- All referenced statuses exist in the `statuses` list.
- `blocking_statuses` and `resolved_statuses` don't overlap.
- `blocking_statuses` is non-empty (otherwise the stop hook is toothless).
- `actor_resolution.agent_usernames` is a non-empty list of glob patterns.

#### Item Schema (Operation Log)

Each op log file (`.ops`) is an **append-only list of operations**. The store never mutates existing ops — it only appends new ones. Current state is derived by `compile()`, a pure function that replays all ops in Lamport clock order (see [Compile Rules](#compile-rules-conflict-resolution)).

```yaml
# .INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit.ops — append-only operation log (in .agent/tracker/.ops/)

- op: create  # f7a2
  at: "2026-02-11T18:00:00Z"  # f7a2
  by: agent  # f7a2
  clock: 1  # f7a2
  nonce: f7a2  # f7a2
  data:  # f7a2
    kind: invariant  # f7a2
    title: "Call Attribution Completeness"  # f7a2
    status: todo_hard  # f7a2
    priority: 2  # f7a2
    parent: null  # f7a2
    tags: [analysis_quality]  # f7a2
    before: []  # f7a2
    duplicate_of: []  # f7a2
    not_duplicate_of: []  # f7a2
    pr_ref: null  # f7a2
    justification: null  # f7a2
    description: ""  # f7a2
    fields:  # f7a2
      statement: "Every emitted `calls` edge has a non-null caller symbol"  # f7a2
      root_cause: "JS/TS arrow function early-return in _get_enclosing_function()"  # f7a2
      fix: "Position-based lookup for arrow functions"  # f7a2
      verification: "Kotlin and Scala lambdas work correctly..."  # f7a2
      regression_tests:  # f7a2
        - "test_js_ts.py::TestCallbackCallAttribution"  # f7a2
        - "test_kotlin.py::TestKotlinLambdaCallAttribution"  # f7a2
      scope: null  # f7a2
      progress_pct: null  # f7a2

- op: discuss  # b3c1
  at: "2026-02-11T18:30:00Z"  # b3c1
  by: human  # b3c1
  clock: 2  # b3c1
  nonce: b3c1  # b3c1
  message: "I think this should be higher priority because it affects CI."  # b3c1

- op: update  # d4e5
  at: "2026-02-11T18:31:00Z"  # d4e5
  by: agent  # d4e5
  clock: 3  # d4e5
  nonce: d4e5  # d4e5
  set:  # d4e5
    priority: 0  # d4e5

- op: discuss  # a1b2
  at: "2026-02-11T18:32:00Z"  # a1b2
  by: agent  # a1b2
  clock: 4  # a1b2
  nonce: a1b2  # a1b2
  message: "Agreed. Bumping to P0."  # a1b2

- op: lock  # c8d9
  at: "2026-02-11T18:33:00Z"  # c8d9
  by: human  # c8d9
  clock: 5  # c8d9
  nonce: c8d9  # c8d9
  lock: [priority]  # c8d9

- op: update  # e6f7
  at: "2026-02-11T19:30:00Z"  # e6f7
  by: agent  # e6f7
  clock: 6  # e6f7
  nonce: e6f7  # e6f7
  set:  # e6f7
    status: done  # e6f7
```

**Operation types:**

| Op type | Fields | Effect |
|---|---|---|
| `create` | `data: {kind, title, status, priority, ...}` | Initialize item with all fields |
| `update` | `set: {field: value, ...}` | Overwrite one or more fields |
| `discuss` | `message: "..."` | Append a discussion entry |
| `discuss_clear` | *(none)* | Clear all previous discussion entries |
| `discuss_summarize` | `message: "..."` | Replace discussion with a single summary |
| `lock` | `lock: [field, ...]` | Add fields to locked set |
| `unlock` | `unlock: [field, ...]` | Remove fields from locked set |
| `promote` | *(none)* | Record promotion (workspace → canonical; file also moves) |
| `demote` | *(none)* | Record demotion (canonical → workspace; file also moves) |
| `reconcile` | `from_tier: "...", reason: "..."` | Record automated cross-tier duplicate resolution (see [Self-Healing Reconciliation](#self-healing-reconciliation)) |

Every op carries `at` (ISO 8601 UTC timestamp), `by` (`agent` or `human`), `clock` (Lamport clock — monotonically increasing integer per op log file), and `nonce` (4 random hex chars). The nonce appears as an inline `# <nonce>` comment on **every line** of each op — not just the first line. This is load-bearing for `merge=union` correctness: it makes every line globally unique, preventing git's line-level union driver from deduplicating or stripping shared lines across ops. See [Compile Rules](#compile-rules-conflict-resolution).

**The operation log IS the audit trail.** There is no separate `audit_trail` field — the file itself is a complete, ordered record of every change. `scripts/tracker log <ID>` prints the raw ops; `scripts/tracker show <ID>` prints the compiled current state.

#### Key Design Decisions

**Append-only operation log.** Instead of storing a mutable snapshot (read-modify-write), each change appends an immutable op to the file. This is a simplified version of git-bug's operation-sourced model, adapted to plain YAML files instead of git objects. The key benefit: **concurrent edits to the same item never produce git conflicts**. Op log files are marked `merge=union` in `.gitattributes` (see [.gitattributes](#gitattributes)), which tells git to keep lines from both sides on conflict. Combined with the **nonce-on-every-line** serialization format (see [Compile Rules](#compile-rules-conflict-resolution)), this guarantees all ops are preserved as distinct YAML list items without conflict markers or data loss. The `compile()` function sorts ops by Lamport clock and applies them deterministically, regardless of the order they appear in the file (see [Compile Rules](#compile-rules-conflict-resolution)).

**Lamport clock for causal ordering.** Each op carries a `clock` field — an integer that captures causal ordering across branches. When appending an op, the store peeks at the op log file on a **scoped set of branches** (via `git cat-file --batch`), computes `max(clock)` across all of them, and sets the new op's clock to `max + 1`. This is a genuine Lamport clock: the cross-branch peek is the "message receive" step in the classic algorithm (`clock = max(local, received) + 1`). All reads are from the local git object store — no network calls, sub-millisecond per branch, works fully offline.

**Implementation: `git cat-file --batch`.** Rather than spawning one `git show <branch>:<path>` subprocess per branch (which costs ~1ms per call), the store pipes all `<branch>:<path>` refs into a single `git cat-file --batch` subprocess. Benchmarking shows this is ~14× faster than serial `git show`: 257 branches resolves in ~19ms (batch) vs. ~265ms (serial). This makes the Lamport clock negligible overhead at any realistic branch count — it is not a scaling bottleneck.

**Scoped branch set.** The peek only scans branches that could contain unmerged tracker ops: `dev`, `main`, and `HEAD` (the current working branch). Already-merged feature branches are redundant — their ops are already reachable via `dev`. In hypergumbo's sequential workflow (auto-pr blocks new work while CI runs), there's rarely even a second active feature branch. As a safety margin, branches with unmerged commits (`git branch --no-merged dev`) are also included, but this set is typically empty. Stale feature branches are excluded entirely — see "Branch hygiene" below.

This ensures causal ordering across the active branch frontier: if agent B can see agent A's ops on any active branch (even without merging), B's next op gets a strictly higher clock — correctly ordered regardless of wall-clock skew between machines. For truly concurrent ops (written on branches not locally visible to each other — e.g., one agent hasn't fetched the other's remote), both sides may produce the same clock value; the tiebreaker `(clock, timestamp, actor_rank)` resolves these deterministically. The guarantee boundary is honest: **causally ordered relative to everything locally visible, tiebreaker for everything else** — the strongest guarantee possible without a centralized server.

Inspired by git-bug's Lamport clock system (`util/lamport/`), but requiring no separate clock files or git object storage — just one integer field per op and a cross-branch `max()` call on append.

**Branch hygiene.** Stale feature branches (already merged into `dev`) are useless for the Lamport clock — they contain no ops that aren't already on `dev`. To prevent accumulation, `scripts/auto-pr` deletes feature branches (local and remote) after successful merge. For manual PRs, AGENTS.md documents the expectation: delete your feature branch after merge. This keeps the scoped branch set small (typically 2–3 branches) and eliminates the risk of degraded performance from branch accumulation.

**Rebase-safe by design.** The nonce-on-every-line serialization format (see [Compile Rules](#compile-rules-conflict-resolution)) makes `merge=union` safe under both merge and rebase. Because every line carries a unique `# <nonce>` suffix, git's line-level union driver cannot match or deduplicate lines across different ops — even when two ops share the same structure (e.g., both are `update` ops setting `status: done`). This was validated empirically: 9/9 adversarial scenarios (including identical ops, cascade diamonds, and 8-way concurrent ops) produce correct results with nonce-on-every-line, for both merge and rebase strategies. See `~/hypergumbo_lab_notebook/adr-0013-prototyping-scripts/rebase_nonce_every_line.py` for the reproduction scripts. The tracker imposes no constraints on git workflow — teams can freely use merge, rebase, squash-merge, or any combination.

**`status` field:**
- UNFIXED → `todo_hard`
- PARTIALLY ADDRESSED → `in_progress`
- FIXED → `done`
- TODO! → `todo_hard`
- TODO → `todo_soft`
- DONE → `done`
- DEFERRED → `deferred`
- WON'T DO → `wont_do`

The stop hook counts items whose status is in `blocking_statuses` (see [Config File](#config-file)). The `before` soft-blocking filter treats items whose status is in `resolved_statuses` as "done" (predecessor resolved).

**Integer priority tiers (0–4).** Priority is an integer:

| Value | Meaning |
|---|---|
| 0 | P0: critical / drop everything |
| 1 | P1: high |
| 2 | P2: medium (default) |
| 3 | P3: low |
| 4 | P4: backlog |

If `--priority` is omitted on `add`, the CLI assigns a default of 2 (P2). Items are sorted by `(priority, before-ordering, created_at)` — see below.

**`before` field for enforced ordering.** To express "this item should be worked on before that one" without changing priority tiers, an item can declare `before: [<ID>, ...]`:

```yaml
- op: update  # b2c3
  at: "2026-02-12T10:00:00Z"  # b2c3
  by: human  # b2c3
  clock: 7  # b2c3
  nonce: b2c3  # b2c3
  set:  # b2c3
    before: [INV-dabop-firuz-hadol-jikam-losib-mufad-nokap-pidul]  # b2c3
```

Unlike a display-order hint, `before` is **enforced as a soft-blocking relationship**. If item X has `before: [Y]`, then Y is not ready until X is resolved (status is in `resolved_statuses` — see [Config File](#config-file)). This is transitive: if X has `before: [Y]` and Y has `before: [Z]`, then Z is blocked until both X and Y are resolved. The `scripts/tracker ready` command (see [CLI](#cli)) returns only items that are actionable (`todo_hard` or `todo_soft`) **and** unblocked — this is what agents use for task selection.

Within the ready set, items are sorted by `(priority, created_at)`. For display purposes (`list`), all items are shown, sorted by `(priority, topological order of before links, created_at)`. Validation warns on (but does not reject) `before` links pointing to items in a different tier or to closed items — the ready filter simply ignores stale links. Cycles in `before` links are rejected by `validate` — a cycle would deadlock the agent.

**Timestamps.** `created_at` is the timestamp of the first op (`create`). "Last updated" is derived from the timestamp of the last op in the file — computed by `compile()`, never stored as a separate field. Both are ISO 8601 UTC. Staleness detection ("this todo_hard item hasn't been touched in 14 days") uses the last op's timestamp.

**Config-defined statuses.** Statuses, `blocking_statuses`, and `resolved_statuses` are all defined in `config.yaml` (see [Config File](#config-file)) — not hardcoded as a Python enum. "Hardcoded in code" provides no additional protection over "defined in config" — the agent can edit and auto-PR Python source just as easily as YAML. With the OS-permission-protected config model (see [Security Model](#security-model)), config-defined statuses are actually *harder* for the agent to modify than source code — the agent literally cannot `write()` to a file owned by the human user with mode 644. The Python code loads the status vocabulary from config at startup and validates ops against it. Adding a new status (e.g., `blocked_external`) is a config change made by the human — no code PR needed. `kind` is also validated against config at runtime — adding a new kind is purely a config change.

**Hash-based IDs with kind prefix and proquint encoding.** IDs are `<kind prefix>-<proquint>` (e.g., `INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit`), where the proquint suffix is a [proquint](https://github.com/dsw/proquint) encoding of the first 128 bits of the SHA-256 hash of the **canonicalized `create` op content** — specifically the `data` dict, serialized with sorted keys. The hash input excludes `at`, `by`, `clock`, and `nonce` — so the same logical item created at different times by different actors produces the same ID.

Proquint encoding maps 16 bits to a 5-letter pronounceable syllable (CVCVC pattern: 16 consonants × 4 vowels × 16 consonants × 4 vowels × 16 consonants = 2^16). Eight syllables encode 128 bits ≈ 3.4 × 10^38 values; birthday collision probability is negligible even at planetary scale (8 billion users × 256 agents × 10 items/day × 100 years ≈ 7.5 × 10^17 items yields <0.001 expected collisions). IDs are long but rarely typed in full — prefix matching means `INV-lusab` suffices in practice. The `proquint` Python package is pure Python with no dependencies (~30 lines of encode/decode logic; can be vendored).

This gives **natural deduplication**: if two agents independently discover the same invariant with the same title, description, and fields, they get the same ID. Inspired by both git-bug's entity ID scheme (SHA-256 of the first operation) and beads' hash-based short IDs (UUID → truncated SHA-256).

**Same-branch existence check.** When `add()` computes an ID, it checks whether a file with that ID already exists in the target tier. If the existing item has identical `data`, the item has already been created — `add()` refuses and reports the existing item: `"INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit already exists (title: 'Call Attribution Completeness'). Use 'update' to modify it."` This prevents silent overwrites when two agents on the same branch independently discover the same invariant. The agent learns the item exists and can `update` it instead (e.g., to add fields the first agent didn't fill in). If the existing item has *different* `data` (a hash collision), `add()` appends a salt to the hash input and recomputes — the item is created under a different ID transparently. **Cross-branch duplicate creation** (two agents on different branches creating the same-ID item before merging) is handled differently — see [Compile Rules](#compile-rules-conflict-resolution).

No sequential counter, no lockfile, no single-writer assumption.

**Prefix matching and positional aliases.** Full proquint IDs are pronounceable but long (`INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit`), so the CLI accepts any unambiguous prefix for convenience:

```bash
scripts/tracker update INV-lus --status done    # resolves if unique
scripts/tracker update lus --status done        # even without the kind prefix
scripts/tracker show INV-lusab                  # longer prefix if ambiguous
```

Ambiguous prefix → error listing matches:

```
error: INV-lus is ambiguous. Did you mean:
  INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit  Call Attribution Completeness
  INV-lusod-fikam-gobad-hilun-jomab-kifud-losip-murad  Symbol Resolution Consistency
```

The `list` and `ready` commands display the shortest unambiguous prefix per item (computed at render time, never stored). Additionally, output rows are numbered, and the CLI accepts positional aliases via `:N` syntax:

```bash
scripts/tracker ready
#  ID               Status     Title
1  INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit  todo_hard  Call Attribution Completeness
2  WI-fodak-humit-kobap-linud-rasib-sufag-tohim-vukad   todo_soft  Add Dart framework patterns

scripts/tracker update :1 --status done   # "item #1 from last list output"
```

The last-displayed ID list is stashed in `$XDG_CACHE_HOME/hypergumbo-tracker/<repo-fingerprint>/` (see [Storage Layout](#storage-layout)). The `:N` syntax is unambiguous (colon distinguishes it from an ID prefix). Positional aliases are ephemeral — never stored in op log files, just a CLI convenience.

**Advisory file locking (`flock()`) around appends.** Even append-only files can get corrupted by concurrent writes from multiple processes (agent CLI + human TUI, or two agent tasks running in parallel). The store wraps the critical section — compute Lamport clock, serialize op, append to file, fsync, update cache — in an advisory file lock:

```python
import fcntl

def _append_op(filepath: Path, op_bytes: bytes, cache: Cache) -> None:
    with open(filepath, "a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(op_bytes.decode())
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    cache.upsert_from_file(filepath)
```

Contention is expected to be rare (two processes appending to the *same item* at the *same instant*), but when it happens, correctness matters more than performance. The lock scope is per-file, so appends to different items never block each other. Note: `flock()` is advisory on Linux — a process that doesn't call it can still write to the file. This is fine because the store is the sole writer of op log files (see [YAML Serialization Rules](#yaml-serialization-rules)); the lock protects against concurrent *tracker* processes, not against arbitrary file writes.

**Two-tier near-duplicate detection.** Hash-based IDs catch verbatim duplicates but not semantic near-duplicates. Two agents discovering the same invariant but phrasing it differently ("Every `calls` edge has a non-null caller" vs. "All calls edges must have non-null callers") produce different hashes and different IDs. A two-tier similarity detection system addresses this:

**Tier 1 — SimHash (fast, always runs on `add`).** SimHash computes a locality-sensitive fingerprint over tokenized `title` + `description` + `fields` text. The algorithm is ~30 lines of pure Python (hash each token, accumulate bit-position votes, threshold), runs in microseconds, and requires no external dependencies. SimHash has a useful formal guarantee: for inputs with cosine similarity S, the probability of a k-bit fingerprint collision is `(1 - arccos(S)/π)^k`. At 40 bits, unrelated items (cosine similarity ~0) have a collision probability of ~10⁻¹² — identical to a random 40-bit hash. The "cost" of locality sensitivity appears only in the moderate-similarity zone (cosine ~0.5: ~10⁻⁸ collision probability at 40 bits), which is precisely where you *want* detection.

On `add`, the store computes the new item's SimHash and compares it (by Hamming distance) against existing items' cached SimHash fingerprints. If the distance is below a threshold (e.g., ≤5 bits out of 40), a warning is emitted:

```
WARNING: INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit is similar to existing INV-fodak-humit-kobap-linud-rasib-sufag-tohim-vukad
  (SimHash distance: 3 bits, title overlap: 82%)
  Creating anyway. Run `scripts/tracker show INV-fodak-humit-kobap-linud-rasib-sufag-tohim-vukad` to compare.
  To mark as duplicate: scripts/tracker update INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit --duplicate-of INV-fodak-humit-kobap-linud-rasib-sufag-tohim-vukad
  To suppress: scripts/tracker update INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit --not-duplicate-of INV-fodak-humit-kobap-linud-rasib-sufag-tohim-vukad
```

The item is created regardless — no blocking. `validate --similar` resurfaces unflagged pairs on demand.

**Tier 2 — Embedding-based LSH with semantic tags (lazy, on demand).** When the embedding model is available (optional dependency), `validate --deep-similar` computes dense embeddings for items, derives semantic tags from the embedding space (e.g., "call-graph-integrity", "symbol-resolution", "edge-attribution"), and applies a second LSH over the tag vectors. This tier discriminates between items that share vocabulary but are about different things — the zone where SimHash produces false positives.

**Model choice: `nomic-ai/modernbert-embed-base` (ONNX, `model_q4f16.onnx`, 140 MB).** ModernBERT is a recent embedding model with strong retrieval performance. The `q4f16` variant uses 4-bit weight quantization with fp16 activations — the smallest available variant (140 MB vs. 596 MB for fp32) with negligible quality loss for this coarse-grained task (distinguishing topics, not fine-grained ranking). CPU-friendly via ONNX Runtime, no GPU required, consistent with hypergumbo's local-first philosophy. The ONNX models are hosted at `https://huggingface.co/nomic-ai/modernbert-embed-base/tree/main/onnx`. Runtime dependencies: `onnxruntime` (CPU) + `tokenizers` (for the model's tokenizer). These are lighter than `sentence-transformers` + PyTorch and avoid pulling in a full deep learning framework.

The semantic tags provide an interpretability layer: when `validate --deep-similar` flags a pair, it explains *why* ("both tagged call-graph-integrity, edge-attribution") rather than just reporting a distance. The human or agent makes a faster triage decision.

This tier degrades gracefully: if `onnxruntime` isn't installed or the model hasn't been downloaded, `validate --deep-similar` emits a warning and falls back to SimHash-only results. No hard dependency.

**Human correction loop.** Two list fields on every item support dedup triage (see [Duplicate Detection](#duplicate-detection-and-correction)):
- `duplicate_of: [<ID>, ...]` — marks this item as a duplicate of one or more others. Items with non-empty `duplicate_of` are excluded from `ready` and `count-todos`.
- `not_duplicate_of: [<ID>, ...]` — records explicit "I've reviewed this pair, they're distinct" judgments. Suppresses future similarity warnings for those specific pairs.

Both are set via `scripts/tracker update` or from the TUI. The human can lock `duplicate_of` to prevent agent override. `validate --similar` skips pairs listed in `not_duplicate_of`.

**Per-kind `fields_schema` (open schema pattern).** The `fields` dict is an open-ended key-value store, but each kind can optionally declare a `fields_schema` in `config.yaml` that names the known fields, their types, and whether they're required. Three rules govern validation:

1. **Known fields are validated strictly.** If the schema declares `progress_pct` as `type: integer, min: 0, max: 100`, then `validate` rejects `progress_pct: "half done"`. Required fields (e.g., `statement` for invariants) must be present in the `create` op's `data.fields`.
2. **Unknown fields produce a warning, not an error.** An invariant with `fields.rout_cause` passes validation but emits: `WARNING: INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit has unknown field 'rout_cause' (did you mean 'root_cause'?)` — edit-distance suggestion against the declared field names. The pre-commit hook shows the warning; the agent or human can fix it or ignore it.
3. **No `fields_schema` means anything goes.** Work items have no structured fields — they use `title` and `description` only. Omitting `fields_schema` (or setting it to `{}`) disables field validation for that kind entirely.

Supported types are minimal: `text` (string), `integer` (with optional `min`/`max`), `list` (of strings), `boolean`. No nested objects, no foreign-key references. If a kind needs more complex validation, add it as a custom check in `validation.py` — don't extend the type system.

This gives the TUI concrete improvements: with a schema, the detail panel renders known fields in declared order with their `description` as tooltip/label, and unknown fields appear in a separate "Other" section below. The edit form presents known fields as named inputs with type-appropriate widgets (text area for `text`, spinner for `integer` with min/max, multi-line list editor for `list`). Without a schema, the TUI falls back to a generic key-value editor.

#### Parent-Child Relationships

Any item can point to a parent via `parent: <ID>`, forming a tree:

```
INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit (parent: null)                    ← root invariant
├── INV-hamoj-libud-mifog-nakip-rosab-sudol-tifag-vukim (parent: INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit)     ← child: a specific generalization
└── INV-kipod-nafug-posab-ridol-safim-tuhob-vikad-zulip (parent: INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit)     ← child: another generalization
```

This replaces the old `pending_generalizations` embedded list — each generalization becomes a first-class item with its own ID, status, priority, and history.

The store provides `children(id)` and `ancestors(id)` traversal methods. The TUI toggles between tree-view (indented by hierarchy) and flat table-view (filterable, sortable).

#### Field Locking

The human can lock any field (or the `discussion` channel) on any item to prevent agent modification. Locks are enforced at write time: the store compiles current state, checks `locked_fields`, and refuses to append `update` or `discuss` ops from agents that touch locked fields. The error message is clear: `"Field 'priority' on INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit is locked. Ask the human to unlock it."`

Locks are toggled from the TUI (`l` key) or via CLI (`scripts/tracker lock <ID> <field>`). The agent can always *read* locked fields — it just can't *write* them.

**Human-authority operations.** The following ops require human authority (see [Security Model](#security-model)) — the store refuses to append them when the resolved actor is `"agent"`: `lock`, `unlock`, `discuss_clear`, `stealth`, `unstealth`. These ops override agent behavior or control visibility, so they must come from the human. `promote`, `demote`, and `discuss_summarize` remain available to both actors — they are workflow operations the agent legitimately needs.

**Cross-branch lock enforcement.** Lock enforcement uses the same scoped cross-branch peek as the Lamport clock ([Key Design Decisions](#key-design-decisions)). Before appending an agent `update` op, the store runs `git show <branch>:<path>` on the scoped branch set (`dev`, `main`, `HEAD`, plus any unmerged branches), compiles each branch's version of the item, and unions their `locked_fields` sets. If the field being updated is locked on *any* branch in the scoped set, the write is rejected — even if the lock hasn't been merged into the current branch yet. This gives locks the **same guarantee boundary as the Lamport clock**: enforced against the active branch frontier, with `validate` warnings as a backstop for ops written on branches that weren't locally visible at write time (e.g., a remote branch not yet fetched). The cross-branch peek adds negligible overhead — it's the same `git show` calls the Lamport clock already makes, just extracting lock state from the compiled result.

**Residual edge case.** For truly concurrent ops on branches not yet fetched (neither side has visibility of the other), lock violations can survive a merge. `validate` detects these and emits a warning. The human corrects with a new op if needed. This is the same honest guarantee boundary documented for the Lamport clock — strongest possible without a central server.

#### Discussion Threads

Each item has an optional discussion, composed from `discuss`, `discuss_clear`, and `discuss_summarize` ops in the operation log. The discussion is **async**: the agent doesn't watch the tracker in a loop. It checks during stop hook reflection, between tasks, or when instructed. Discussions develop over hours or days as the agent works on other things. Many items can have active discussions simultaneously.

**The clear-then-lock pattern** gives the human a decisive override:
1. `discuss <ID> --clear` — appends a `discuss_clear` op (compile ignores all prior discussion). Human-authority only (see [Security Model](#security-model)).
2. `discuss <ID> "Priority stays at P0. Non-negotiable."` — appends a `discuss` op (actor resolved from `os.getuid()`, no `--as` flag)
3. `lock <ID> discussion` — appends a `lock` op so the agent can't respond

This doesn't remove the old discussion from the agent's context window (if it was already read), but it's an unambiguous signal in the persisted state: "I've decided, stop arguing."

**Soft cap and summarization.** The store emits a warning to stderr when a compiled discussion exceeds 20 entries. This is a soft cap — no hard limit is enforced, and humans can always override. To manage long-running discussions, use the `--summarize` flag:

```bash
scripts/tracker discuss <ID> --summarize "Summary text here"
```

This appends a `discuss_summarize` op. When compiled, all prior discussion entries are replaced with a single summary entry marked `is_summary: true`. The TUI shows a warning badge (e.g., `[20+ msgs]`) next to items with oversized discussions.

**Discussion rate limit (runaway loop guard).** The soft cap catches normal excess; a rate limit catches degenerate cases. The store tracks discussion volume per item per calendar day using a simple heuristic: `len(message) / 4.4` as token estimate (no tokenizer dependency). A generous daily cap — 200,000 tokens per item — is hardcoded in the store. Legitimate multi-day threads never hit this; an agent appending in a tight loop hits it within minutes. When the limit is reached:

```
warning: Discussion rate limit reached on INV-lusab (200,000 tokens today).
Further discussion deferred until tomorrow, or run --summarize to reset.
```

This is a circuit breaker, not a policy tool. It catches the degenerate case without ever restricting a human who wants a 60-entry thread on a thorny invariant. The limit is hardcoded (not in config) because it's a blast radius control, not a governance knob — see [Safety Model](#safety-model-three-layers).

**File readability.** Because discussion ops live in the same op log file as data ops, items with long discussions produce large files that are noisy in `git log -p` and `git diff`. Five mitigations: (1) op log files are in a dotdir (`.ops/`) with dotfile names, so they don't appear in casual browsing; (2) `linguist-generated` in `.gitattributes` collapses these in PR diffs; (3) a **textconv diff driver** ([textconv](#local-diff-declutter-textconv)) declutters local `git log -p` and `git diff` by showing compiled item state instead of raw ops; (4) `discuss_summarize` replaces accumulated entries with a single summary, which should be used proactively when discussions exceed the 20-entry soft cap; (5) the `tracker:` commit prefix convention ([Commit Convention](#commit-convention-and-git-history-hygiene)) lets developers filter tracker changes from git log entirely. If this proves insufficient, a future revision could split discussions into separate files — but this adds complexity to the merge story and is deferred unless needed.

#### Three-Tier Visibility

Items exist in one of three visibility tiers, determined by which directory the file physically lives in:

| Tier | Directory | In git? | Pushed to fork remote? | In upstream PRs? | Use case |
|---|---|---|---|---|---|
| **Canonical** | `.agent/tracker/.ops/` | yes | yes | yes | Repo's institutional memory |
| **Workspace** | `.agent/tracker-workspace/.ops/` | yes | yes | **no** | Agent's working memory, backed up |
| **Stealth** | `.agent/tracker-workspace/stealth/` | **no** | **no** | **no** | Truly private, local-only |

**Canonical** is the shared truth — committed, included in PRs, visible to everyone. This is where confirmed invariants, validated work items, and reviewed findings live.

**Workspace** is the agent's scratch space — committed to git and pushed to the fork's remote (for backup and continuity across machines), but excluded from upstream PRs by `scripts/contribute`. The agent writes here by default. Items are promoted to canonical explicitly when they're confirmed and worth sharing.

**Stealth** is fully private — gitignored, never leaves the machine. For draft items, sensitive priority overrides, or human-agent discussions the human doesn't want in any git history.

**Promotion and demotion.** Visibility moves between tiers via `promote` and `demote` commands. Each transition appends an op to the op log file (for audit trail) and physically moves the file between `.ops/` directories:

- `scripts/tracker promote <ID>` — workspace → canonical (file moves from `tracker-workspace/.ops/` to `tracker/.ops/`)
- `scripts/tracker demote <ID>` — canonical → workspace (reverse)
- `scripts/tracker stealth <ID>` — workspace → stealth (file moves to `tracker-workspace/stealth/`)
- `scripts/tracker unstealth <ID>` — stealth → workspace (reverse)
- TUI: `m` key opens a tier-move dialog on the selected item

The ID does not change on promotion/demotion — it's content-derived, not path-derived.

**Fork workflow.** When a contributor forks and clones, they get canonical (it's committed) and an empty workspace. Their agent:

1. **Reads** canonical items for context (upstream's priorities and institutional knowledge)
2. **Writes** new items to workspace (committed to the fork, backed up to the fork's remote)
3. **Never modifies** canonical directly (by convention — canonical is upstream's, not the fork's)

When the contributor runs `scripts/contribute`, workspace changes are automatically excluded from the PR. The PR contains only code changes. If the contributor discovers an invariant worth sharing with upstream:

1. `scripts/tracker promote INV-lusab` — moves item to canonical (prefix matching)
2. Creates a separate `tracker:` PR with just the promoted item
3. Upstream maintainer reviews it independently from the code PR

This separates "here's my code contribution" from "here's an invariant I discovered." The upstream maintainer can evaluate each on its own merits.

**Stop hook scoping on forks.** If `count-todos` aggregated all tiers, the fork agent would be permanently blocked by upstream's open canonical items (which it can read but not close). The fix: `config.yaml` has a `stop_hook.scope` field (see [Config File](#config-file)). On forks, this is set to `workspace` so the stop hook only counts the fork agent's own work. The `ready` command still shows canonical items (the fork agent should be *aware* of upstream's priorities), but canonical items don't *block* the fork agent from stopping.

| Context | `count-todos` scope | `ready` scope |
|---|---|---|
| Upstream | canonical + workspace (`all`) | canonical + workspace |
| Fork | workspace only | canonical + workspace |

Fork detection and scope configuration happen automatically during `scripts/fork-setup` (or first `scripts/contribute` run), which checks for the presence of an `upstream` remote and sets `stop_hook.scope: workspace` in the workspace `config.yaml` (gitignored, human-owned — see [Config File](#config-file)).

**Workspace starts empty on forks.** When a contributor forks and clones, workspace has no items. The fork's agent creates items as it works. It doesn't get copies of canonical items — that would create duplicates in the merged read view and diverge immediately. Canonical is read-only context, not a starting point to be cloned.

**Cross-tier references.** An item in workspace can reference a canonical item via `parent` or `before` — for example, a workspace work item tracking progress on fixing a canonical invariant. The `TrackerSet` merged read view resolves references across tiers transparently. Validation checks cross-tier refs against the full merged set.

**`scripts/contribute` workspace exclusion.** The `contribute` script already handles fork-specific PR creation. Adding workspace exclusion is ~15 lines:

```bash
# Before creating the PR branch, exclude workspace changes
WORKSPACE_CHANGES=$(git diff --name-only "$UPSTREAM_DEV"...HEAD -- '.agent/tracker-workspace/')
if [ -n "$WORKSPACE_CHANGES" ]; then
    echo "Excluding $(echo "$WORKSPACE_CHANGES" | wc -l) workspace tracker files from PR"
    # Create clean branch without workspace-only commits
fi
```

**Leak mitigation.** If a contributor uses raw `git push` instead of `contribute`, workspace items appear in the PR. Mitigations: (1) documented convention in AGENTS.md, (2) pre-push hook warns when workspace items are in a push to upstream, (3) `linguist-generated` collapses tracker diffs in the PR view so the noise is at least hidden. The consequence of a leak is just noise, not data loss — the maintainer can ignore workspace items in the diff.

#### Self-Healing Reconciliation

Cross-tier duplicates — the same item ID existing in multiple tier directories — can arise from interrupted tier moves (promote/demote appends the op but crashes before or after the file move) or merge artifacts (one branch promotes an item while another branch continues editing it in workspace). `TrackerSet` handles these automatically on the read path — the tracker's Python code self-heals without agent involvement (see [Safety Model](#safety-model-three-layers)).

**When tier-movement ops exist:** `TrackerSet` concatenates all ops from both files, compiles normally (Lamport clock ordering resolves everything), and looks at the last tier-movement op (`promote`, `demote`, `stealth`, `unstealth`) in the compiled history. That op determines the intended tier. The store merges the ops into a single file in the correct tier directory, deletes the other copy, and appends a `reconcile` op recording what happened (`from_tier`, `reason`). This is deterministic — no judgment needed, no agent involvement.

**When no tier-movement ops exist:** The item was independently created in different tiers on different branches (same content hash, different directories). This is genuinely ambiguous — the store cannot determine the correct tier. `TrackerSet` flags the item with a derived `cross_tier_conflict` field in the compiled snapshot (like `created_at` and `updated_at` — computed on read, not stored). The CLI surfaces this prominently:

```
⚠ INV-lusab exists in both canonical and workspace with no tier-movement history.
  Resolve: scripts/tracker promote INV-lusab    (workspace → canonical)
      or:  scripts/tracker demote INV-lusab     (canonical → workspace)
```

Until resolved, the item appears in `list` and `show` with a conflict indicator but is excluded from `ready` (the agent should not work on items in ambiguous state). `validate` warns on cross-tier duplicates.

**Self-healing is append-only.** The `reconcile` op is the audit trail — the store never silently deletes or rewrites ops. The reconciled file contains the full combined history from both tier copies.

**Self-healing attempt cap.** If the same item triggers reconciliation more than 3 times (tracked via `reconcile` op count in the compiled history), the store stops attempting automatic repair, flags the item with a persistent error in the compiled snapshot, and surfaces it to the human via `validate` and TUI. This prevents repair loops from degenerate merge scenarios.

#### Compile Rules (Conflict Resolution)

The `compile()` function is a pure function: it takes a list of ops from an op log file, sorts them by Lamport clock, and folds them into a snapshot. This is where concurrent edits are resolved — not in a merge driver, but in the read path.

**Sort order:** Ops are sorted by `(clock, timestamp, actor_rank)` where `human` ranks higher than `agent`. The Lamport clock captures causal ordering via cross-branch peek (see [Key Design Decisions](#key-design-decisions)): if agent B could see agent A's ops on any local branch before writing, B's clock is strictly higher. For truly concurrent ops (same clock value, from branches not locally visible to each other), timestamp breaks ties. For same-clock-same-timestamp ops, human wins. This ensures deterministic output regardless of the order ops appear in the file after a git merge.

**Duplicate `create` ops (cross-branch merge of independently-created items).** Content-hash IDs ([Key Design Decisions](#key-design-decisions)) mean that two agents on different branches who independently create the same item produce the same ID and write to the same `.ops` dotfile. On the same branch, `add()` detects the existing file and refuses (see [Key Design Decisions](#key-design-decisions)). But across branches, both files exist independently until merge. After `merge=union`, the merged file contains **two `create` ops** (with different nonces and clocks, since each agent generated its own). `compile()` handles this gracefully: it takes the `create` op with the **lowest clock** (the causally earliest creation) as the canonical creation event and **ignores subsequent `create` ops with identical `data`**. The `created_at` derived field uses the earliest `create` op's timestamp. All subsequent `update`, `discuss`, `lock`, etc. ops from both branches are folded normally — the item's compiled state reflects the combined work of both agents. `validate` emits an informational notice ("INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit has 2 create ops — likely independent creation on separate branches, merged cleanly") but does not error.

**Per-field resolution during fold:**

| Field / op type | Compile behavior |
|---|---|
| Scalar fields (`status`, `priority`, `title`, `parent`, `description`, `justification`, `pr_ref`) | Last write wins |
| `tags` | Last write wins (replaced wholesale by each `update` that sets it) |
| `before` | Last write wins (replaced wholesale) |
| `duplicate_of` | Last write wins (replaced wholesale) |
| `not_duplicate_of` | Last write wins (replaced wholesale) |
| `fields` (dict) | Per-key last write wins (merge, not replace — updating `fields.root_cause` does not clobber `fields.statement`) |
| `locked_fields` | Accumulated: `lock` ops add to the set, `unlock` ops remove |
| Discussion | Accumulated: `discuss` ops append, `discuss_clear` resets to empty, `discuss_summarize` replaces with single summary |

**Derived fields** (computed by `compile()`, never stored):
- `created_at`: timestamp of the `create` op
- `updated_at`: timestamp of the last op in the file

**Why `merge=union` and not git's default merge.** When two branches both append multi-line ops to the end of the same file, git's default (ort) merge strategy produces a **conflict** — even when the appended ops are completely different. Simulation confirms this: default merge conflicts in 7 of 8 tested scenarios, including the trivial case of appending one `update` op on branch A and one `discuss` op on branch B. Conflicts block the autonomous agent workflow (the agent can't resolve `<<<<<<<` markers in YAML), so default merge is not viable for concurrent append-only files.

`merge=union` eliminates these conflicts by keeping lines from both sides. However, `merge=union` operates at the **line level**, not the YAML-block level. This creates two hazards:

1. **Op fusion.** When two branches append ops that share the same first line (e.g., both start with `- op: update`), the union driver deduplicates that shared line, **fusing two ops into one malformed YAML mapping** with duplicate keys. Simulation confirms this: without mitigation, `merge=union` garbles the result in 4 of 8 tested scenarios (every case where both branches append the same op type).

2. **Line stripping.** Even when first lines differ (e.g., different nonces on the `- op:` line), ops that share identical *internal* lines (e.g., `  set:` / `    status: done`) can have those shared lines deduplicated by the union driver's diff algorithm. This silently strips payload from earlier ops, producing valid YAML with correct op counts but **empty op bodies** — silent data loss. Empirical testing showed this affects 10/12 adversarial scenarios when only the first line carries a nonce, including a merge-only control (proving this is a `merge=union` property, not rebase-specific). See `~/hypergumbo_lab_notebook/adr-0013-prototyping-scripts/rebase_duplication_v2.py`.

**Nonce-on-every-line: the fix.** Every line of every op carries the nonce as an inline `# <nonce>` comment:

```yaml
- op: update  # d4e5
  at: "2026-02-11T18:31:00Z"  # d4e5
  by: agent  # d4e5
  clock: 3  # d4e5
  nonce: d4e5  # d4e5
  set:  # d4e5
    priority: 0  # d4e5
- op: update  # e6f7
  at: "2026-02-11T19:30:00Z"  # e6f7
  by: agent  # e6f7
  clock: 6  # e6f7
  nonce: e6f7  # e6f7
  set:  # e6f7
    status: done  # e6f7
```

Since each op has a unique nonce and every line carries it, **every line in the file is globally unique**. The union driver cannot match or deduplicate any line across ops — neither first lines (preventing fusion) nor internal lines (preventing stripping). The nonce also appears as a regular field (`nonce: d4e5`) for programmatic access — the comments are purely for merge correctness and are stripped by YAML parsers. `ruamel.yaml`'s comment-preservation support handles nonce-on-every-line naturally on the write path.

**Simulation results.** With nonce-on-every-line, `merge=union` passes **all tested scenarios** — 9/9 adversarial cases including: identical ops (same `set:` block), cascade diamonds, 8-way concurrent ops, three-way merges with all-`update` ops, and post-rebase merges with pre-rebase lineages. Every scenario that failed with nonce-on-first-line (10/12 in the adversarial suite) passes cleanly with nonce-on-every-line. See `~/hypergumbo_lab_notebook/adr-0013-prototyping-scripts/rebase_nonce_every_line.py` for the reproduction scripts. The format is more verbose than nonce-on-first-line, but buys complete merge-strategy independence: the tracker is safe under merge, rebase, squash-merge, or any combination.

#### YAML Serialization Rules

YAML's implicit type coercion (`yes`→`true`, `3.0`→float, bare `null`) makes agent-written content error-prone. To prevent data corruption, all YAML I/O flows through the store using strict serialization conventions.

**Libraries.** The store uses two YAML libraries, each for a distinct purpose:
- **`ruamel.yaml`** (~0.18) for **writes** — round-trip-safe serialization with preserved quoting, comment retention, and canonical field ordering. All YAML output flows through ruamel.yaml.
- **`PyYAML`** with **`CSafeLoader`** for **reads** — wraps LibYAML (C extension), roughly 10× faster than ruamel.yaml's pure-Python parser. Since the read path doesn't need to preserve quoting or comments (it just needs Python dicts), the C loader is safe here. A dedicated test (`test_yaml_roundtrip.py`) verifies that `CSafeLoader` and `ruamel.yaml` produce identical parsed output for all op types, including adversarial inputs.

This split means the hot path — `compile()`, `list`, `ready`, `count-todos` — benefits from C-speed parsing, while the write path retains ruamel.yaml's strict serialization guarantees. The cache layer ([Read Cache](#read-cache-sqlite)) further reduces how often even the C loader is invoked.

**Benchmark-confirmed performance gap.** Testing with realistic op log files (180–12,000 ops) shows CSafeLoader is consistently 3–10× faster than both SafeLoader and ruamel.yaml's parser. At 3,000 ops (~750KB file), CSafeLoader parses in ~200ms vs. ~1,000ms for SafeLoader and ~1,170ms for ruamel.yaml. This dual-library split improves the hot path speed by 5×.

**Quoting rules:**
- String fields that could be misinterpreted are always double-quoted: `title`, `description`, `justification`, `message` (in discuss ops), all `fields.*` string values
- `op`, `status`, `kind`, `by` are unquoted (controlled vocabulary, known-safe values)
- Multiline strings use block scalar (`|`) style

**Canonical op field order.** Each op is serialized with fields in this order:
```
op (with nonce comment), at, by, clock, nonce, [op-specific fields: data/set/message/lock/unlock]
```

**Every line** of every op carries an inline `# <nonce>` comment that duplicates the `nonce` field value. This is load-bearing for `merge=union` correctness (see [Compile Rules](#compile-rules-conflict-resolution)): it makes every line globally unique, preventing the union driver from fusing same-type ops (first-line deduplication) or stripping shared internal lines (payload deduplication). The comments are invisible to YAML parsers but visible to git's line-level merge. `ruamel.yaml`'s comment-preservation support handles nonce-on-every-line naturally on the write path.

Within the `create` op's `data`, fields are ordered:
```
kind, title, status, priority, parent, tags, before, pr_ref, justification, description, fields
```

**Sole-writer invariant.** The store is the sole writer of op log files. Agents and humans use the CLI/TUI — they never edit `.ops` files directly. Agents should never *read* op log files either — they use `scripts/tracker show <ID>` for compiled state (see [Agent Context Protection](#agent-context-protection)).

**Enforcement.** The pre-commit hook ([Pre-Commit Validation](#pre-commit-validation)) validates all staged tracker files, catching malformed YAML regardless of how it was written. Additionally, `validate` checks for:
- Ops missing the `nonce` field (the CLI always generates one)
- Ops with lines missing the `# <nonce>` inline comment — nonce-on-every-line is load-bearing for `merge=union` correctness ([Compile Rules](#compile-rules-conflict-resolution))
- Nonce comments not matching the `nonce` field value on any line (detects copy-paste errors)
- Non-canonical field ordering (the CLI always serializes in canonical order)

These pre-commit hook checks make it difficult for invalid YAML to get committed.

**Round-trip invariant.** `load(dump(load(file))) == load(file)` — enforced by a dedicated test (`test_yaml_roundtrip.py`) with adversarial inputs (`"yes"`, `"null"`, `"3.0"`, `"*bold*"`, strings with colons, leading whitespace, emoji, etc.).

**Trailing newline.** The store always writes a trailing newline. This ensures that when two branches both append ops, git's merge sees clean line boundaries and can concatenate without garbling.

#### Op Log Compaction (Future Work)

Op log files grow monotonically (append-only). An item with 50 updates and 30 discussion entries has 80+ ops in a single `.ops` file. For the current scale (<500 items, <100 ops per item), this is fine — `compile()` is linear in op count and fast.

If file sizes become problematic, the compaction strategy is:

1. **Snapshot op**: A new `compact` op type containing the full compiled state at a point in time. When present, `compile()` starts from the snapshot and only replays ops with higher clocks.
2. **Op pruning**: A `scripts/tracker compact <ID>` command that (a) compiles current state, (b) rewrites the file as a single `compact` op followed by only the post-snapshot ops. This is a **destructive rewrite** (not append-only), so it requires human confirmation and produces a normal git diff (not a merge-safe append).
3. **Discussion summarization** already exists (`discuss_summarize`), which is the most likely source of file bloat.

This is explicitly deferred — the current design handles the expected scale. The compaction mechanism is documented here so the extension point is clear when needed.

#### Read Cache (SQLite)

The read path (`list`, `ready`, `count-todos`) must load and compile all items. Even with PyYAML's `CSafeLoader` ([YAML Serialization Rules](#yaml-serialization-rules)), parsing 500 op log files with 50+ ops each on every invocation adds latency that compounds when agents call `ready` frequently. A gitignored SQLite cache eliminates this cost for all read operations. The cache also means agents never need to read `.ops` files directly — `scripts/tracker show/list/ready` all query the cache (see [Agent Context Protection](#agent-context-protection)).

**Location:** `$XDG_CACHE_HOME/hypergumbo-tracker/<repo-fingerprint>/` (see [Storage Layout](#storage-layout)). One cache database per tier (`canonical.cache.db`, `workspace.cache.db`). The repo-fingerprint key (hash of remote URL + first commit SHA) allows multiple checkouts of the same repo to share a cache. Created automatically on first read; deleted and rebuilt by `scripts/tracker cache-rebuild`.

**Why XDG, not in-repo.** Two OS users sharing a checkout (see [Security Model](#security-model)) cannot share a single SQLite database safely — every write changes file ownership, causing permission errors for the other user. XDG gives each user their own cache directory (`/home/jgstern/.cache/...` vs. `/home/jgstern_agent/.cache/...`). This is consistent with hypergumbo's existing cache strategy (`~/.cache/hypergumbo/`). The `TRACKER_CACHE_DIR` environment variable overrides the default — useful when the cache directory is on a network filesystem where SQLite locking is unreliable (e.g., NFS).

**Schema:**
```sql
CREATE TABLE items (
    id          TEXT PRIMARY KEY,   -- e.g., "INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit"
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL,
    priority    INTEGER NOT NULL,
    parent      TEXT,
    tags        TEXT,               -- JSON array
    before      TEXT,               -- JSON array of IDs
    duplicate_of TEXT,              -- JSON array of IDs
    not_duplicate_of TEXT,          -- JSON array of IDs
    pr_ref      TEXT,
    justification TEXT,
    description TEXT,
    fields      TEXT,               -- JSON dict
    locked_fields TEXT,             -- JSON array
    discussion  TEXT,               -- JSON array of {by, message, is_summary}
    simhash     INTEGER,            -- 40-bit SimHash fingerprint (for fast similarity queries)
    tier        TEXT NOT NULL,       -- "canonical", "workspace", or "stealth"
    created_at  TEXT NOT NULL,      -- ISO 8601
    updated_at  TEXT NOT NULL,      -- ISO 8601
    source_mtime REAL NOT NULL,     -- file mtime at last cache update
    source_size  INTEGER NOT NULL   -- file size in bytes at last cache update
);

CREATE INDEX idx_status ON items(status);
CREATE INDEX idx_kind ON items(kind);
CREATE INDEX idx_priority ON items(priority);
```

**Cache invalidation: incremental via byte-offset tracking.** The naive approach — re-parse the entire file whenever mtime changes — is expensive when discussion is heavy: benchmarking shows that re-parsing 150 stale items with 1,000 ops each costs ~7.7 seconds, even though the data fields (`status`, `priority`, `tags`, etc.) haven't changed. The `count-todos` and `ready` queries don't need discussion content at all, so this is entirely wasted work.

The fix exploits the append-only invariant: old bytes are never modified, new ops are always appended at the end. The cache stores both `source_mtime` and `source_size` per item. On potential invalidation:

1. `stat()` the file — get current mtime and size.
2. If mtime unchanged → **cache hit**. Return cached data. (The common case.)
3. If mtime changed and current size ≥ stored `source_size` → **likely append-only**:
   a. Seek to `source_size`, read only the new bytes.
   b. Parse the new bytes as a YAML fragment (they are complete op list items because the store always writes trailing newlines — see [YAML Serialization Rules](#yaml-serialization-rules)).
   c. If **all** new ops are `discuss`, `discuss_clear`, or `discuss_summarize` → **discussion-only change**: update only the `discussion`, `updated_at`, `source_mtime`, and `source_size` columns in the cache. Skip data re-compile entirely.
   d. If any new ops are `update`, `lock`, `unlock`, `promote`, or `demote` → **data change**: full re-parse and re-compile.
4. If mtime changed and current size < stored `source_size` → **file was rewritten** (compaction, manual edit, or unusual merge): full re-parse and re-compile.

This works reliably after `merge=union` merges: when two branches both append ops, `merge=union` preserves the original bytes verbatim and appends both sides' new lines at the end. The first `source_size` bytes are unchanged, so seeking to `source_size` produces exactly the new ops from both branches.

**Benchmark impact:** With incremental invalidation, the cost of a discussion-only cache miss drops from ~50ms per item (full re-parse of a 1,000-op file) to <1ms (seek + parse one small YAML op). For the 150-stale-item scenario above, this reduces invalidation cost from ~7.7 seconds to ~50ms — a ~150× improvement. This eliminates the strongest performance argument for separating discussion into separate files, while preserving the structural simplicity of one file per item.

**Write-through updates:** When the store appends an op (via `add`, `update`, `discuss`, etc.), it re-compiles that single item and upserts the cache row (including updated `source_mtime` and `source_size`) immediately. This means the cache is always current after a local write — no deferred rebuild needed. Other processes on the same machine sharing the same `.cache.db` benefit from write-through done by any process: once process A appends a discuss op and updates the cache, process B's next read sees a fresh `source_mtime` and gets a cache hit. Redundant re-parsing only occurs during the narrow window when multiple processes simultaneously detect a stale mtime before any finishes updating the cache — this is idempotent and harmless (all processes compute the same result).

**Cold start:** On first run (or after `cache-rebuild`), the store parses all YAML files, compiles each, and populates the cache. Cold-start time scales with **total op volume**, not just item count: 200 items × 50 ops each (~1.6 MB total YAML) takes ~340ms; 500 items × 500 ops each (~39 MB) takes ~11 seconds; 500 items × 3,000 ops each (~234 MB) takes ~53 seconds. Proactive use of `discuss_summarize` and compaction ([Op Log Compaction](#op-log-compaction-future-work)) directly reduces cold-start time by keeping per-item op counts manageable. Subsequent reads after cold start are sub-millisecond (cache hit path).

**After `git pull` or `git merge`:** File mtimes change for any items modified on the incoming branch. The next read detects stale mtimes and applies incremental invalidation — for items where only discussion ops were appended (the common case when multiple agents are actively discussing), only the new bytes are parsed. Items with data changes get a full re-compile. This is the common case: a pull brings 5–10 changed items; the store incrementally processes 5–10 files, not all 500.

**Robustness.** The cache is strictly derived data — deleting `.cache.db` and re-running any command rebuilds it from the YAML source of truth. The cache is never consulted during writes (writes always go to YAML). If the cache is corrupt or out of date, the worst case is a one-time cold rebuild, not data loss.

**Why SQLite.** It's in Python's standard library (`sqlite3`), requires no additional dependency, supports indexed queries for filtered listing (`SELECT ... WHERE status IN (...blocking_statuses...) AND ... ORDER BY priority, created_at`), and handles concurrent reads safely (WAL mode). The `.cache.db` file lives outside the repo tree (XDG cache) and is disposable — it never enters the merge story.

**Why not `/tmp`.** The performance bottleneck is YAML parsing, not SQLite I/O. Cache hits already take ~0.02ms (a SQL query on indexed columns). Moving the database from XDG cache to a tmpfs-backed `/tmp/` would save microseconds on an operation that takes microseconds — not meaningful. XDG cache survives reboots (unlike `/tmp` on many distros), so cold starts only happen on first-ever run or after explicit `cache-rebuild`.

#### Duplicate Detection and Correction

Items carry two list fields for dedup triage:

- **`duplicate_of: [<ID>, ...]`** — Marks this item as a duplicate of one or more others. Set automatically by agent confirmation of a similarity warning, or manually by human. Items with non-empty `duplicate_of` are excluded from `ready` and `count-todos` but remain in the store for audit. Multiple IDs allow marking an item as a dupe of several others (e.g., three agents independently discovered the same invariant — pick one survivor, mark the other two).

- **`not_duplicate_of: [<ID>, ...]`** — Records explicit "I've reviewed this pair, they're distinct" judgments. Suppresses future similarity warnings between those specific pairs. Accumulated over time as the human or agent triages flagged pairs. `validate --similar` skips pairs where either item lists the other in `not_duplicate_of`.

Both fields are modified via `update` ops (last-write-wins, replaced wholesale — same as `tags` and `before`). The human can lock `duplicate_of` on an item to prevent agent override of a triage decision.

**Detection flow:**

1. **On `add`:** SimHash (tier 1) compares the new item against all cached fingerprints. If Hamming distance is below threshold, a warning is emitted with actionable commands. The item is created regardless — no blocking.
2. **On `validate --similar`:** SimHash comparison across all items, skipping `not_duplicate_of` pairs. Reports unflagged near-duplicate pairs.
3. **On `validate --deep-similar`:** Additionally runs embedding-based semantic tag LSH (tier 2) if `onnxruntime` is available. Discriminates between items that share vocabulary but are about different things. Falls back to SimHash-only if embeddings are unavailable.

**TUI integration (MVP):** Items with non-empty `duplicate_of` are displayed dimmed or struck-through in the table/tree view. **Post-MVP:** TUI groups items sharing a `duplicate_of` target, with a "merge" button that consolidates op logs, picks the surviving item's title/fields, and closes the others. Merge semantics are designed when real usage informs the needs.

#### Agent Context Protection

Op log files contain the full operation history for each item — every `create`, `update`, `discuss`, `lock`, and `unlock` op ever applied. An agent reading the raw op log wastes context window on historical intermediate states and may act on stale data (e.g., seeing `status: todo_hard` in the `create` op rather than the final compiled `status: done`). The compiled current state is what agents need; the op log is an implementation detail.

Three layers of defense prevent agents from reading op logs directly:

1. **Dotdir** (`.ops/`) — Agents are trained to skip dotdirs by convention. The op log directory is hidden from `ls`, file explorers, and casual glob patterns.
2. **Dotfile** (`.INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit.ops`) — Each op log file is itself a dotfile, doubly hidden. Even if an agent navigates into `.ops/`, the files don't appear in standard directory listings.
3. **Explicit instruction in AGENTS.md** — The following rules are added:
   - "Always use `scripts/tracker show <ID>` or `scripts/tracker show <ID> --json` to read tracker item state."
   - "Always refuse to read files ending in `.ops`. These are internal operation logs that will pollute your context window with historical data you don't need."

The `.ops` file extension (rather than `.yaml`) is also a signal — agents scanning for readable config or data files by extension will not match `.ops` files.

**Why not just use the CLI?** Agents with file access (Claude Code, Cursor, Copilot) default to reading files directly — it's faster than subprocess calls and is what they're trained to do. The dotdir/dotfile defense works with that behavior: agents will read files at obvious paths but skip hidden ones. If an agent is looking for tracker data, the most obvious path (`.agent/tracker/`) contains only `config.yaml` and hidden directories — nothing tempting to read.

**Comparison with alternatives:**
- **git-bug** stores data as git objects in `refs/bugs/*` — not files at all, so agents can't read them. This is the strongest protection but requires custom git plumbing and a Go toolchain.
- **beads** stores data in `.beads/issues.jsonl` — a visible file containing current state (not history), but as a single massive JSON line per issue that wastes context window differently.
- The tracker plan's approach is a pragmatic middle ground: the data is in files (for git portability and `merge=union`), but hidden behind conventions that agents respect.

**OS-level tripwire (inotifywait).** As a fourth layer, `scripts/install-hooks` can optionally start an `inotifywait` watcher on the `.ops/` directories:

```bash
inotifywait -m -r -e access,open .agent/tracker/.ops/ .agent/tracker-workspace/.ops/ \
  --format '%T %w%f %e' --timefmt '%H:%M:%S' >> ~/.ops-access.log &
```

The tracker CLI's access pattern is a predictable burst (many files opened in rapid succession during `compile()`). Any isolated `.ops` file read without a temporally correlated CLI invocation indicates a direct agent read. This is a monitoring/awareness mechanism — it does not block access, just logs it for the human to review. `inotifywait` requires no root privileges (it uses Linux's inotify kernel API) and is available in the `inotify-tools` package on all major distributions.

### Security Model

#### Threat Model

**What already went wrong.** The agent changed work item statuses in the markdown files using formatting that didn't match the stop hook's grep regex. Items silently fell out of governance view. The agent wasn't being adversarial — it was being helpful in a way that happened to be invisible to the tooling. This is the actual incident that motivated ADR-0013.

**The primary security win of the tracker is schema enforcement.** The agent cannot write a status that the governance tooling can't see, because `status` is validated against a controlled vocabulary on every write, and the pre-commit hook validates on every commit. There is no format to accidentally drift from.

**What hasn't gone wrong.** The agent has never attempted to forge human authority, bypass the stop hook, or deliberately evade governance. The agent's "motivation" comes from its system prompt. If you can't trust the system prompt to say "don't forge human ops," you can't trust it to say "don't `rm -rf .agent/tracker`" either.

**Layered recovery (the real backstop).** The deployment has multiple layers of recovery external to the tracker:

- **VM snapshots** — any mistake is reversible by rolling back the VM.
- **Limited forge permissions** — the agent has a Codeberg contributor account, not an owner account. It can create PRs but cannot force-push main, delete the repo, or change repository settings.
- **Read-only mirror** — the Codeberg repo is mirrored to GitHub, which the agent has no access to.

The tracker doesn't need to be a cryptographic fortress. It operates within a system where catastrophic mistakes are recoverable. Its job is to be rigid enough that the agent can't *accidentally* undermine governance — not to prevent a determined adversary with file-level write access.

#### Actor Resolution via `os.getuid()`

The deployment has two OS users:

| Account | UID | sudo | Role |
|---|---|---|---|
| `jgstern` | 1001 | yes | Human |
| `jgstern_agent` | 1002 | no | Agent |

Both are routinely logged in simultaneously. The agent runs Claude Code under `jgstern_agent`. The human SSHes in as `jgstern`.

```python
import os
import pwd

def _resolve_actor() -> str:
    username = pwd.getpwuid(os.getuid()).pw_name
    # Agent username patterns are configurable in config.yaml
    # Default: ["*_agent"]
    if _matches_agent_patterns(username):
        return "agent"
    return "human"
```

No env vars to sniff or unset. No keys to generate or lose. No signatures to verify. `os.getuid()` returns 1002 no matter what environment variables are set, what CLI flags are passed, or what AGENTS.md says. The agent cannot become UID 1001 without `su`/`sudo`, which it doesn't have.

The CLI resolves the actor internally; there is no `--as` flag. Human-authority operations (see [Field Locking](#field-locking)) check the resolved actor and refuse to execute as `"agent"`:

```
error: 'lock' requires human authority (current user: jgstern_agent).
Run this command as jgstern.
```

The human runs the TUI from their own account (`jgstern`). The TUI is the "human voice" because only UID 1001 can pass the actor check.

**Configurable agent username patterns.** The `username.endswith("_agent")` heuristic works for the current setup. For broader adoption, agent username patterns are configurable in `config.yaml` (see [Config File](#config-file)): `actor_resolution.agent_usernames: ["*_agent"]`. Override per-project.

#### Filesystem Access for Two-User Setup

The repo lives in `/home/jgstern_agent/hypergumbo/`. A shared group gives both users write access to tracker directories:

```bash
# One-time setup (as jgstern, who has sudo):
sudo groupadd hypergumbo-tracker
sudo usermod -aG hypergumbo-tracker jgstern
sudo usermod -aG hypergumbo-tracker jgstern_agent

# Set tracker dirs to shared group with setgid:
chgrp -R hypergumbo-tracker .agent/tracker .agent/tracker-workspace
chmod -R g+rwX .agent/tracker .agent/tracker-workspace
chmod g+s .agent/tracker/.ops
chmod g+s .agent/tracker-workspace/.ops
chmod g+s .agent/tracker-workspace/stealth
```

The `setgid` bit ensures new `.ops` files created by either user inherit the group. Both accounts can append ops. The `flock()` around appends (see [Key Design Decisions](#key-design-decisions)) handles concurrent writes.

#### Single-User Deployments

Contributors and standalone adopters likely run single-user. For them:

- Actor resolution returns `"human"` (no `_agent` suffix).
- The agent (if they run one) gets the same identity — there's no OS boundary on a single-user machine.
- Social controls (AGENTS.md rules, CLI conventions) are the enforcement layer, same as the existing design for lock/discuss ops.
- Config file permissions don't provide additional protection (same user owns everything).

This is an honest degradation, not a bug. Fork contributors don't need the same governance rigor as the upstream maintainer's autonomous agent.

**For adopters who want stronger guarantees**, creating a second OS user account is straightforward. The README should provide explicit steps for setting up a two-user deployment on a VM or container, and explain why it's a good idea. Running in a VM with snapshots (or at the very least a container) is also recommended — it's the real backstop for catastrophic mistakes, and it's cheap.

#### Config Protection

See [Config File](#config-file) for the template-based config design. The key security property: `config.yaml` is gitignored and owned by the human user with mode 644. The agent can read it (needs to, for validation) but cannot write it. The OS enforces this — UID 1002 cannot write a file owned by UID 1001 with mode 644. No CLI checks, no social contracts, no crypto. The agent cannot redefine governance rules (kinds, statuses, blocking semantics) without privilege escalation.

#### Safety Model (Three Layers)

**Layer 1: Tracker code self-heals deterministic issues.** The tracker's Python code handles structural inconsistencies automatically — no agent involvement. Cross-tier duplicates with clear tier-movement ops are reconciled on read (see [Self-Healing Reconciliation](#self-healing-reconciliation)). The agent never sees the inconsistency — it was fixed before the CLI returned output. Self-healing is always append-only (a new `reconcile` op, never rewriting or deleting existing ops) so the audit trail shows what happened.

**Layer 2: Agent uses CLI to resolve ambiguities on unlocked items.** The agent calls `scripts/tracker update` or `scripts/tracker promote` as a black-box tool — same as it uses `git` or `pytest`. It's making work-item decisions, not debugging the tracker. The CLI enforces locks, actor authority, schema validation, and rate limits. The agent can't do anything the CLI doesn't allow.

**Layer 3: Unanticipated errors surface to the human, with hardcoded blast radius limits.** For errors the tracker can't self-heal and the agent can't resolve through normal CLI usage, the system surfaces them through `validate` output, TUI error indicators, and derived conflict fields in compiled snapshots. The channel is impossible for the agent to dismiss or silence.

**Hardcoded blast radius limits** (in the store, not in config):

- **Never delete or rewrite ops.** Self-healing appends, always.
- **Refuse to write to items that can't compile.** If an op log is corrupted beyond what self-healing can fix, the store refuses to append rather than making it worse. The item is frozen until a human looks at it.
- **Cap self-healing attempts.** If the same item triggers reconciliation more than 3 times, stop trying and escalate (see [Self-Healing Reconciliation](#self-healing-reconciliation)). Prevents repair loops.
- **Discussion rate limit.** Token-based daily cap per item (see [Discussion Threads](#discussion-threads)). Prevents runaway agent loops from bloating op logs.

**The security guarantee, stated honestly:** On a two-user deployment, human authority over ops is enforced by the OS (getuid). Human authority over config is enforced by file permissions. The agent cannot forge human-authority ops or redefine governance rules without privilege escalation. On a single-user deployment, both are enforced by convention (AGENTS.md rules, CLI design). The tracker does not claim cryptographic non-repudiation. The real backstop for all deployments is external: VM snapshots, limited forge permissions, and a read-only mirror.

#### Rejected Security Proposals

| Proposal | Why not |
|---|---|
| Per-op cryptographic signatures | The OS user boundary already provides a non-spoofable actor identity. Crypto adds key management, performance cost, and contributor onboarding complexity for zero additional security in the actual deployment. |
| Hash-chain ops for tamper evidence | Append-only files with `merge=union` + nonce-on-every-line already make rewriting history a visible git operation (`git log -p` shows it). Hash chains add O(n) verification cost on every read and create fragile "chain broken" failure modes after legitimate git operations (rebase, cherry-pick). |
| Signed policy config (detached `.sig` or policy-as-ops) | Superseded by the gitignored-config-with-OS-permissions model, which provides stronger protection (OS-enforced, not crypto-enforced) with zero complexity. |
| `bootstrap-secure` script | The two-user setup is a shared-group `chmod` + `chown` away, folded into `scripts/tracker init`. No separate bootstrap needed. |
| Policy rollback prevention via `policy_hash` in every op | Config changes are rare. Adding a policy hash to every op's payload creates coupling between config edits and op validity. Not worth the complexity — and moot with OS-permission-protected config. |
| Hard discussion entry cap | A hard cap that blocks further discussion is unnecessarily restrictive. A generous rate limit (tokens/day) catches runaway loops without ever blocking legitimate long threads. |
| CLI `_resolve_actor()` check on config edits | Code theater. The agent can write files directly; a CLI check only works if the agent voluntarily uses the CLI. OS file permissions are the real enforcement. |

### Code Location

The tracker is a standalone Python package: `packages/hypergumbo-tracker/`. It lives alongside the other hypergumbo packages in the monorepo but has **no dependency on `hypergumbo-core`** — it doesn't need analyzers, IR, or tree-sitter. It's a standalone tool that happens to live in the same repo.

This keeps CI fast: tracker tests run in their own isolated job, not bloating the already-large core test suite.

```
packages/hypergumbo-tracker/
├── pyproject.toml                        # deps, console_scripts entry points, MPL-2.0 license
├── LICENSE                              # MPL-2.0 full text
├── README.md
├── src/
│   └── hypergumbo_tracker/
│       ├── __init__.py                   # Public API exports
│       ├── models.py                     # Dataclasses + op types + config loading + actor resolution
│       ├── store.py                      # YAML read/write, ID generation, compile(), list/filter, tree traversal
│       ├── trackerset.py                  # Multi-tier read: merges canonical + workspace + stealth stores
│       ├── cache.py                      # SQLite read cache: schema, invalidation, write-through, rebuild
│       ├── validation.py                 # Schema validation, enum enforcement, parent refs, cycle detection
│       ├── migration.py                  # One-time markdown → YAML converter
│       ├── cli.py                        # CLI (console_scripts: hypergumbo-tracker, hypergumbo-tracker-textconv)
│       ├── stop_hook.py                  # count_todos, hash_todos, generate_guidance (scope-aware)
│       └── tui.py                        # Textual TUI application
└── tests/
    ├── test_models.py
    ├── test_store.py                     # CRUD, compile(), concurrent-append scenarios
    ├── test_trackerset.py                 # Multi-tier merged reads, cross-tier refs, promote/demote
    ├── test_cache.py                     # SQLite cache: invalidation, write-through, cold start, corruption recovery
    ├── test_validation.py
    ├── test_migration.py
    ├── test_yaml_roundtrip.py            # Adversarial YAML serialization tests
    ├── test_compile_properties.py        # Property-based tests (hypothesis) for compile()
    ├── test_cli.py
    ├── test_stop_hook.py
    └── test_tui.py
```

The package is a dependency of the `hypergumbo` umbrella meta-package — `pip install hypergumbo` pulls it in alongside core and the lang packages. But it has no dependency on `hypergumbo-core` (no analyzers, IR, or tree-sitter), so it can also be installed standalone by projects that want the tracker without hypergumbo's analysis tooling:
```bash
pip install hypergumbo-tracker                       # standalone CLI
pip install hypergumbo-tracker[tui]                   # standalone CLI + TUI
pip install hypergumbo                               # gets tracker + everything else
```

#### Dependencies

Required:
- **ruamel.yaml** (~0.18) — Round-trip-safe YAML write with preserved quoting (write path only — see [YAML Serialization Rules](#yaml-serialization-rules))
- **PyYAML** (~6.0, with C extension) — Fast YAML read via `CSafeLoader` (read path only — see [YAML Serialization Rules](#yaml-serialization-rules))
- **proquint** (~0.1) — Proquint encoding/decoding for hash-based IDs (pure Python, no deps; ~30 lines — could be vendored if preferred)
- **rich** (~14.3.2) — CLI table formatting

Optional (`[tui]` extra):
- **textual** (~3.0) — TUI framework

Optional (`[dedup]` extra):
- **onnxruntime** (~1.17) — ONNX model inference for `validate --deep-similar` (tier 2 dedup). CPU-only, no GPU or PyTorch required.
- **tokenizers** (~0.21) — Fast tokenizer for `nomic-ai/modernbert-embed-base`. HuggingFace's Rust-backed tokenizer library.
- The ONNX model file (`model_q4f16.onnx`, 140 MB) is downloaded on first use and cached locally. Falls back gracefully if unavailable.

Dev:
- **pytest**, **pytest-cov**, **pytest-xdist** — testing (same versions as other packages)
- **pytest-asyncio** — async test support for Textual's `App.run_test()`/`Pilot` (configure `asyncio_mode=auto` in pyproject.toml)
- **pytest-textual-snapshot** — official Textual snapshot plugin for SVG-based visual regression testing
- **hypothesis** — property-based testing for `compile()` invariants (see [Verification](#verification))

#### Licensing

The tracker package is licensed under **MPL-2.0**, while the rest of hypergumbo is **AGPL-3.0-or-later**. This dual-license structure enables standalone adoption: projects that want structured agent governance can `pip install hypergumbo-tracker` without AGPL obligations on their own code. MPL-2.0's copyleft is file-level (modifications to tracker source files must be shared), not program-level (the tracker can be embedded in proprietary projects without infecting them). AGPL's copyleft protects hypergumbo's core analysis tooling from unreciprocated SaaS use.

**SPDX headers.** Every source file in the repo carries an SPDX license identifier:

```python
# SPDX-License-Identifier: MPL-2.0          # in packages/hypergumbo-tracker/
# SPDX-License-Identifier: AGPL-3.0-or-later  # everywhere else
```

```bash
# SPDX-License-Identifier: AGPL-3.0-or-later  # shell scripts outside the tracker package
```

The FSFE's [REUSE](https://reuse.software/) tool validates compliance in CI (`reuse lint`). Contributors see the applicable license at the top of the file they're editing — no need to reason about directory boundaries. The DCO sign-off (`git commit -s`) is license-agnostic (it defers to "the open source license indicated in the file"), so the existing sign-off process requires no changes.

**Integration glue.** This ADR modifies files outside `packages/hypergumbo-tracker/` (stop hook, pre-commit, CI workflows, AGENTS.md, `scripts/tracker` wrapper). Those files remain AGPL-3.0-or-later — they are hypergumbo-specific integration that is not useful standalone. MPL-2.0 is AGPL-3.0-compatible (MPL Section 3.3), so the AGPL host can depend on the MPL tracker without license conflict.

**Entry points as the license boundary for executables.** The tracker declares two `console_scripts` entry points in its `pyproject.toml`:

- **`hypergumbo-tracker`** — Main CLI (all subcommands)
- **`hypergumbo-tracker-textconv`** — Git textconv driver for `.ops` files (see [textconv](#local-diff-declutter-textconv))

Both are installed to `$PATH` by `pip install hypergumbo-tracker` and are MPL-2.0 as part of the tracker package. The repo's `scripts/tracker` is a thin AGPL-3.0 wrapper that delegates to the installed `hypergumbo-tracker` command — it exists for consistency with other repo scripts (`scripts/auto-pr`, `scripts/contribute`, etc.), not because the tracker needs it. Standalone users interact exclusively with the MPL entry points.

### CLI

The tracker package declares `console_scripts` entry points (`hypergumbo-tracker` and `hypergumbo-tracker-textconv`) in its `pyproject.toml`. `pip install hypergumbo-tracker` makes both available on `$PATH`. Within the hypergumbo repo, `scripts/tracker` is a thin wrapper that delegates to the installed `hypergumbo-tracker` command (or falls back to `python -m hypergumbo_tracker.cli`), maintaining consistency with other repo scripts. All subcommands except `tui` produce plain text (or `--json` for machine consumption). All `<ID>` arguments accept proquint prefix matching (e.g., `INV-lus` or just `lus`) and positional aliases (`:N` referring to the Nth item from the last `list`/`ready` output).

| Subcommand | Purpose | Primary Consumer |
|---|---|---|
| `init` | Create `.agent/tracker/` and `.agent/tracker-workspace/` dirs (with `.ops/` dotdirs), copy `config.yaml.template` → `config.yaml` (human-owned, mode 644), set up `.gitignore` entries (config.yaml, stealth/). See [Config File](#config-file), [Security Model](#security-model). | human |
| `count-todos [--hard\|--soft]` | Print integer count of blocking items (respects `stop_hook.scope` config, uses `blocking_statuses` from config) | stop_logic.sh |
| `hash-todos` | Print SHA256 of all TODO content (circuit breaker) | stop_logic.sh |
| `validate [FILE...] [--similar] [--deep-similar]` | Exit 0 if all op log files valid, exit 1 with errors/warnings. When called with file paths, validates only those files (but still checks cross-file constraints like duplicate IDs and dangling parent refs against the full set). No args = validate all. Warns on cross-tier duplicates. Warns when `config.yaml` has kinds/statuses not in `config.yaml.template` (CI fallback gap). `--similar`: surface near-duplicate pairs via SimHash (skips pairs in `not_duplicate_of`). `--deep-similar`: additionally uses embedding-based semantic tags for discrimination (requires `onnxruntime` + `tokenizers`; falls back to SimHash-only if unavailable). | pre-commit hook, CI |
| `add --kind <kind> --title "..." [--tier canonical\|workspace\|stealth]` | Create new item (appends `create` op; default tier: workspace). Computes SimHash on creation and warns if similar items exist (see [Key Design Decisions](#key-design-decisions)). Accepts prefix or positional alias (`:N`) for `--duplicate-of`/`--not-duplicate-of` flags. | agent |
| `update <ID> --status\|--priority\|...` | Update fields (appends `update` op; respects locked_fields and actor authority) | agent |
| `discuss <ID> "msg"` | Append `discuss` op (actor resolved from `os.getuid()` — no `--as` flag; see [Security Model](#security-model)) | both |
| `discuss <ID> --clear` | Append `discuss_clear` op (human-authority only) | human |
| `discuss <ID> --summarize "summary"` | Append `discuss_summarize` op | both |
| `lock <ID> <field> [<field>...]` | Append `lock` op (human-authority only) | human |
| `unlock <ID> <field> [<field>...]` | Append `unlock` op (human-authority only) | human |
| `promote <ID>` | Append `promote` op + move file workspace → canonical | both |
| `demote <ID>` | Append `demote` op + move file canonical → workspace | both |
| `stealth <ID>` | Move file workspace → stealth (human-authority only) | human |
| `unstealth <ID>` | Move file stealth → workspace (human-authority only) | human |
| `show <ID>` | Print compiled current state (formatted; includes cross-tier conflict indicator if applicable) | agent |
| `list [--status X] [--kind Y] [--tag Z] [--tier T]` | Filtered list (compact table, sorted by priority/before/created_at; shows tier indicator and conflict markers) | agent |
| `ready [--limit N]` | List actionable, unblocked items (respects `before` soft-blocking; respects `stop_hook.scope` for scoping; excludes items with cross-tier conflicts) | agent |
| `log <ID>` | Print raw operation log | both |
| `migrate` | Convert existing markdown → YAML (one-time, into canonical) | human/agent |
| `guidance` | Generate guidance markdown for stop hook (scope-aware) | stop_logic.sh |
| `fork-setup` | Detect fork (upstream remote), set workspace `stop_hook.scope: workspace` in config | human/agent |
| `cache-rebuild` | Delete and rebuild cache from YAML source of truth | human/agent |
| `textconv <FILE>` | Emit compiled one-line-per-field text representation of an op log file (used by git's textconv diff driver — see [textconv](#local-diff-declutter-textconv)) | git diff |
| `tui` | Launch Textual TUI (human-authority context — `os.getuid()` resolves as human) | human |

### TUI Design

A single-screen terminal application:

- **Header**: filter chips (kind, status, tag, tier), search bar
- **Left panel**: DataTable or TreeView (toggle with `t`). Tree groups by parent-child hierarchy. Table is flat and sortable. Each row shows a tier indicator (`[C]`/`[W]`/`[S]`) alongside the item ID.
- **Right panel**: Detail view showing compiled item state rendered with Rich markup. For kinds with a `fields_schema`, known fields are rendered in declared order with their `description` as a tooltip/label; unknown fields appear in a separate "Other" section below. For kinds without a schema, fields are rendered as a generic key-value list. Locked fields show a lock icon. The operation log is displayed at the bottom of the detail panel (scrollable). Items with discussions exceeding 20 entries show a warning badge (e.g., `[20+ msgs]`).
- **Footer**: keybinding hints

Key bindings:
| Key | Action |
|---|---|
| `q` | Quit |
| `t` | Toggle tree/table view |
| `f` | Open filter panel |
| `e` | Edit status/priority/tags on selected item |
| `n` | Create new item (in workspace by default) |
| `p` | Set/change parent |
| `b` | Set/change `before` ordering |
| `l` | Toggle lock on field under cursor |
| `m` | Move item between tiers (promote/demote/stealth/unstealth) |
| `d` | Open discussion panel (type message as "human") |
| `shift+d` | Clear discussion history |

All edits append ops to the YAML file (immediate persistence). When editing `fields` on a kind with a `fields_schema`, the TUI presents known fields as named inputs with type-appropriate widgets (text area for `text`, spinner for `integer` with min/max constraints, multi-line list editor for `list`). Unknown fields are editable via a generic key-value row. Items with unresolved cross-tier conflicts (see [Self-Healing Reconciliation](#self-healing-reconciliation)) show a conflict indicator with resolution options.

#### Testability

The TUI app accepts a dependency-injected `TrackerSet` (wrapping canonical and workspace `Store` instances) so tests can point at `tmp_path` fixtures without touching `.agent/tracker/` on the real filesystem. This also enables safe `pytest-xdist` parallelism. All test fixtures use deterministic `at` timestamps in ops (no `datetime.now()`) to avoid flaky snapshots — the compile path already derives `created_at`/`updated_at` from op timestamps, so freezing time at the op level is sufficient.

### Stop Hook Migration

#### Phase 1: Dual-mode (backward compatible)

`stop_logic.sh` gains a conditional that tries the tracker CLI first and falls back to the existing grep patterns:

```bash
if [[ -x "$REPO_ROOT/scripts/tracker" && -d "$REPO_ROOT/.agent/tracker" ]]; then
  # count-todos respects stop_hook.scope from config.yaml:
  # - "all" (default, upstream): counts canonical + workspace
  # - "workspace" (forks): counts workspace only
  TOTAL_HARD=$(scripts/tracker count-todos --hard)
  TOTAL_SOFT=$(scripts/tracker count-todos --soft)
  TOTAL_TODOS=$((TOTAL_HARD + TOTAL_SOFT))
  CURRENT_HASH=$(scripts/tracker hash-todos)
  # ... existing hash file / circuit breaker logic unchanged ...
else
  # Legacy grep patterns (existing code, no changes)
  HARD_TODO_COUNT=$(grep -c '^\s*- \*\*TODO!\*\*' "$LEDGER_FILE" 2>/dev/null) || HARD_TODO_COUNT=0
  # ... etc ...
fi
```

**Task selection vs. stopping.** `count-todos` answers "can I stop?" (total open work, scoped by `stop_hook.scope`). The separate `scripts/tracker ready` command answers "what should I work on next?" — it returns items from all tiers that are actionable and unblocked by `before` links (see [Key Design Decisions](#key-design-decisions)), so the agent is always aware of canonical items even on forks. The stop hook uses `count-todos`; the agent's task-selection logic (documented in AGENTS.md) uses `ready`.

#### Phase 2: Remove legacy grep

After the migration is confirmed stable, delete the grep fallback. The markdown files become read-only archives (kept for git history, no longer consumed by anything).

### Pre-Commit Validation

Added to `.githooks/pre-commit`, inserted **before** Ruff (fail fast — tracker validation takes ~100ms). Only staged `.ops` files are validated per-file; cross-file constraints (duplicate IDs, dangling parents) still check the full set but only load ID and parent fields, not full compilation:

```bash
# Run tracker validation (fast - only staged files)
echo -n "  Tracker (schema)... "
if [ -d ".agent/tracker" ] && command -v scripts/tracker &> /dev/null; then
    STAGED_TRACKER=$(git diff --cached --name-only -- '.agent/tracker/.ops/' '.agent/tracker-workspace/.ops/' '.agent/tracker-workspace/stealth/' || true)
    if [ -n "$STAGED_TRACKER" ]; then
        if scripts/tracker validate $STAGED_TRACKER 2>/dev/null; then
            echo -e "${GREEN}✓${NC}"
        else
            echo -e "${RED}✗${NC}"
            echo ""
            echo -e "${RED}Tracker validation failed. Fix YAML issues before committing.${NC}"
            exit 1
        fi
    else
        echo -e "${YELLOW}skipped (no tracker files staged)${NC}"
    fi
else
    echo -e "${YELLOW}skipped (no tracker)${NC}"
fi
```

This matches the existing pre-commit style in `.githooks/pre-commit` (color-coded pass/skip/fail, echo-then-check pattern). In CI, `scripts/tracker validate` (no args) validates all files.

Validation catches:
- Malformed YAML (not a valid list of ops)
- First op is not `create` or is missing required `data` fields
- Unknown `kind` (not in config.yaml)
- Invalid `status` (not in config.yaml statuses list)
- Unknown `op` type
- Missing required op fields (`op`, `at`, `by`, `clock`, `nonce`)
- Priority not an integer in range 0–4
- Timestamps not valid ISO 8601 UTC
- Duplicate IDs (across all tiers: canonical, workspace, stealth)
- Deferred items without justification (in compiled state)
- Dangling parent references (parent ID doesn't exist)
- ID prefix doesn't match kind's configured prefix
- Cycles in `before` links
- Required `fields` keys missing (per kind's `fields_schema`, if defined)
- `fields` values failing type/range checks (e.g., `progress_pct: "half done"` when schema says `type: integer`)
- **Warning** (non-blocking): unknown `fields` keys with edit-distance suggestion (e.g., `'rout_cause' — did you mean 'root_cause'?`), only for kinds with a `fields_schema`
- **Warning** (non-blocking): agent `update` ops touching fields that were locked at the time (by timestamp)

### Migration Script

`scripts/tracker migrate` performs one-time conversion of the existing markdown files:

1. Write default `config.yaml` with 3 kinds (invariant, meta_invariant, work_item)
2. Parse `.agent/invariant-ledger.md` — regex on `## INV-NNN:` headers and `- **Status:**` fields
3. Parse `~/hypergumbo_lab_notebook/guidance_log/work_items.md` — regex on category headers and `- **STATUS**` items
4. Map to unified status:
   - "FIXED" / "✅ FIXED" → `done`
   - "⬛ WON'T DO" → `wont_do`
   - "UNFIXED" → `todo_hard`
   - "PARTIALLY ADDRESSED" → `in_progress`
   - `**TODO!**` → `todo_hard`
   - `**TODO**` → `todo_soft`
   - `**DONE**` → `done`
   - `**DEFERRED**` → `deferred`
5. Assign priorities: old P1 → 1, P2 → 2, P3 → 3; invariants with todo_hard → 0, todo_soft → 1, done/deferred → 4
6. Generate hash-based 10-hex-char IDs by hashing each `create` op's canonicalized `data` dict (SHA-256, first 10 chars), with kind-appropriate prefixes
7. Convert `pending_generalizations` embedded lists into child items with `parent: <parent-ID>`
8. Map work item categories to tags (e.g., "Developer Experience" → tag `developer_experience`)
9. Write each item as an op log file in `.agent/tracker/.ops/` (canonical tier — migrated items are upstream's institutional memory), using dotfile naming (`.INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit.ops`)
10. Create empty `.agent/tracker-workspace/` with default config (including `.ops/` and `stealth/` dirs)
11. Validate all written files
12. Print summary: N items migrated (by kind), N parent-child links created

Migration is idempotent: re-running produces the same IDs (same content → same hash) and the same YAML output.

### Files Modified

| File | Change |
|---|---|
| `packages/hypergumbo-tracker/` (NEW) | Entire new package: pyproject.toml (with `console_scripts` entry points), LICENSE (MPL-2.0), 10 src modules (all with `SPDX-License-Identifier: MPL-2.0` headers), 11 test modules |
| `packages/hypergumbo-tracker/LICENSE` (NEW) | MPL-2.0 full text |
| `scripts/tracker` (NEW) | Thin AGPL-3.0 bash wrapper delegating to installed `hypergumbo-tracker` entry point (falls back to `python -m hypergumbo_tracker.cli`) |
| `scripts/check-package-coverage` | Add `tracker` to PACKAGES map for per-package CI isolation |
| `scripts/dev-install` | Add `pip install -e packages/hypergumbo-tracker[tui]` |
| `.agent/hooks/_shared/stop_logic.sh` | Add tracker-first path with grep fallback (scope-aware via config) |
| `scripts/auto-pr` | Delete local and remote feature branch after successful merge (branch hygiene — see [Key Design Decisions](#key-design-decisions)) |
| `scripts/contribute` | Add workspace exclusion (~15 lines): exclude `.agent/tracker-workspace/` from upstream PRs |
| `.agent/tracker/` (NEW) | Canonical tier: `.ops/` dotdir with op log files from migration + `config.yaml.template` (tracked) + `config.yaml` (gitignored, human-owned) |
| `.agent/tracker-workspace/` (NEW) | Workspace tier: empty `.ops/`, `stealth/` dirs + `config.yaml.template` (tracked) + `config.yaml` (gitignored) |
| `scripts/tracker-textconv` (NEW) | AGPL-3.0 bash shim for git textconv diff driver — delegates to `hypergumbo-tracker-textconv` entry point, falls back to `cat` (see [textconv](#local-diff-declutter-textconv)) |
| `.gitattributes` (NEW) | `linguist-generated` + `merge=union` + `diff=tracker` for both canonical and workspace `.ops/.*.ops` files (see [.gitattributes](#gitattributes), [textconv](#local-diff-declutter-textconv)) |
| `.gitignore` | Add `.agent/tracker/config.yaml`, `.agent/tracker-workspace/config.yaml`, `.agent/tracker-workspace/stealth/` |
| `AGENTS.md` | Update grep pattern instructions → `scripts/tracker` equivalents; add `tracker:` commit prefix convention and batching guidance (see [Commit Convention](#commit-convention-and-git-history-hygiene)); add task-selection guidance: use `scripts/tracker ready` (not `list`) to pick next work item; **add agent context protection rules: always use `scripts/tracker show` or `--json`, always refuse to read `.ops` files** (see [Agent Context Protection](#agent-context-protection)); add branch hygiene expectation (delete feature branches after merge); update contributor workflow to reference `fork-setup`; document security model and two-user setup |
| `README.md` | Add section on recommended deployment setup: two OS user accounts (human + agent), VM with snapshots or container, with explicit setup steps and rationale (see [Security Model](#security-model)) |
| `.agent/stop_reflect.md` | Update Section 2 grep patterns → tracker CLI |
| `.agent/cooldown_prompt.md` | Minor reference updates |
| `scripts/install-hooks` | Add `git config diff.tracker.textconv scripts/tracker-textconv` for local diff declutter (see [textconv](#local-diff-declutter-textconv)) |
| `.githooks/pre-commit` | Add incremental `scripts/tracker validate` step (staged files only from both tiers, before Ruff) |
| `.github/workflows/ci.yml` | Fix `CODE_PATTERNS` to exclude tracker `.ops` files; add `tracker_data` output; add `tracker-validate` job; update `ci-complete` gate; add concurrency group (see [CI Integration](#ci-integration)) |
| `.github/workflows/full-suite.yml` | Fix `CODE_PATTERNS` to exclude tracker `.ops` files; add `test-tracker` job; update `aggregate` (see [CI Integration](#ci-integration)) |
| `LICENSE` | Add preamble noting per-package licensing: `packages/hypergumbo-tracker/` is MPL-2.0, everything else AGPL-3.0-or-later |
| `CONTRIBUTING.md` | Document dual-license structure (MPL-2.0 for tracker, AGPL-3.0-or-later for everything else), SPDX header convention, and that DCO sign-off covers both licenses per-file |

### Implementation Sequence

#### PR 1: Package scaffold + data model + store + cache + validation + serialization + licensing
- Create `packages/hypergumbo-tracker/` with pyproject.toml (including `console_scripts` entry points: `hypergumbo-tracker` → `hypergumbo_tracker.cli:main`, `hypergumbo-tracker-textconv` → `hypergumbo_tracker.cli:textconv_main`), LICENSE (MPL-2.0), src layout, tests dir. All source files carry `# SPDX-License-Identifier: MPL-2.0` headers
- Update root `LICENSE` with preamble noting per-package licensing
- Update `CONTRIBUTING.md` to document dual-license structure (MPL-2.0 for tracker, AGPL-3.0-or-later for everything else), SPDX header convention, and that DCO sign-off covers both licenses per-file
- `models.py`: Op dataclasses (including `promote`/`demote`/`reconcile` op types), Tier enum (`canonical`/`workspace`/`stealth`), config loading from chain (`config.yaml` → `config.yaml.template` fallback, including `fields_schema` per kind — supported types: `text`, `integer` with optional `min`/`max`, `list`, `boolean`; `blocking_statuses`/`resolved_statuses`; `actor_resolution.agent_usernames` patterns). Status vocabulary loaded from config at startup (no Python enum). Actor resolution via `os.getuid()` + configurable agent username patterns (see [Security Model](#security-model))
- `store.py`: YAML write (ruamel.yaml) and read (PyYAML `CSafeLoader` — see [YAML Serialization Rules](#yaml-serialization-rules)), hash-based ID generation (SHA-256 of canonicalized `create` op `data` dict, first 128 bits proquint-encoded — see [Key Design Decisions](#key-design-decisions)), **same-branch existence check on `add()`** (refuse to create if file with computed ID already exists in the target tier — see [Key Design Decisions](#key-design-decisions)), SimHash computation on item content (40-bit fingerprint, cached in SQLite), prefix matching resolver (shortest unambiguous prefix), positional alias support (stash file in XDG cache dir), scoped cross-branch Lamport clock (peek `dev` + `main` + `HEAD` + unmerged branches via `git cat-file --batch` — see [Key Design Decisions](#key-design-decisions)), cross-branch lock enforcement (same scoped peek, union of `locked_fields`), human-authority enforcement via `_resolve_actor()` (see [Security Model](#security-model)), nonce generation (4 random hex chars per op, serialized as inline `# <nonce>` comment on every line for `merge=union` correctness — see [Compile Rules](#compile-rules-conflict-resolution)), **`flock()` around append-fsync-cache-update critical section** (per-file advisory lock — see [Key Design Decisions](#key-design-decisions)), **discussion rate limit** (token-based daily cap per item, `len(message) / 4.4` as estimate — see [Discussion Threads](#discussion-threads)), `compile()` function (**tolerates duplicate `create` ops from cross-branch merges** — lowest-clock `create` wins, subsequent identical-data `create` ops ignored — see [Compile Rules](#compile-rules-conflict-resolution)), list/filter, `ready()` filter (soft-blocking via `before` links, uses `resolved_statuses` from config), tree traversal (children/ancestors), canonical op field ordering, `before` topological sort, **refuse to write to items that can't compile** (frozen until human intervention — see [Safety Model](#safety-model-three-layers)). Store operates on a single directory (one tier) — multi-tier merging is handled by `TrackerSet`
- `trackerset.py`: Multi-tier wrapper that instantiates a `Store` per tier (canonical, workspace, stealth), merges reads transparently, resolves cross-tier `parent`/`before` references, routes writes to the correct tier, implements `promote()`/`demote()`/`stealth()`/`unstealth()` (append op + physical file move between directories), **self-healing cross-tier duplicate reconciliation** (follows last tier-movement op when deterministic, flags ambiguous cases with derived `cross_tier_conflict` field, caps reconciliation attempts at 3 per item — see [Self-Healing Reconciliation](#self-healing-reconciliation)), provides unified `ready()` (excludes items with cross-tier conflicts) and scope-aware `count_todos()` (respects `stop_hook.scope` and `blocking_statuses` from config)
- `cache.py`: SQLite read cache (see [Read Cache](#read-cache-sqlite)) — one cache database per tier in `$XDG_CACHE_HOME/hypergumbo-tracker/<repo-fingerprint>/`. Schema creation (including `source_size` and `tier` columns), incremental byte-offset invalidation (seek to stored `source_size`, parse only new bytes, skip data re-compile for discussion-only appends), write-through upsert on local ops, cold-start rebuild, `cache-rebuild` entry point, `TRACKER_CACHE_DIR` override. All read operations (`list`, `ready`, `count-todos`, `show`) query the cache; writes go to YAML and update the cache row in one step
- `validation.py`: schema checks, status validation against config (not a hardcoded enum), dedup (across all tiers), cross-tier duplicate detection, parent ref checks (cross-tier), `before` cycle detection (cross-tier), compiled-state checks (deferred justification, etc.), per-kind `fields_schema` validation (required fields present, type/range checks on known fields, edit-distance typo warnings for unknown fields), **config-vs-template divergence warning** (warn when `config.yaml` has kinds/statuses not in `config.yaml.template`). Must support optional file-path arguments from the start (for incremental pre-commit validation — see [Pre-Commit Validation](#pre-commit-validation))
- `__init__.py`: public API
- Create `.gitattributes` with `linguist-generated` and `merge=union` for both `.agent/tracker/.ops/.*.ops` and `.agent/tracker-workspace/.ops/.*.ops` (see [.gitattributes](#gitattributes))
- Add `.agent/tracker/config.yaml`, `.agent/tracker-workspace/config.yaml`, and `.agent/tracker-workspace/stealth/` to `.gitignore`
- Update `scripts/check-package-coverage` and `scripts/dev-install`
- Tests: model construction, validation pass/fail (including `fields_schema`: required field missing → error, wrong type → error, unknown field with close edit distance → warning with suggestion, unknown field on kind without schema → no warning), store CRUD (append ops), hash-based ID generation (same content → same ID, different content → different ID, IDs are valid proquint-encoded), proquint round-trip (encode → decode → encode produces same result), **`add()` same-branch existence check** (create item, attempt `add()` with identical content → `ItemExistsError` with existing item's title; verify the original file is not overwritten; verify different content producing a different ID succeeds normally; verify hash collision — create item, then `add()` with different content that produces the same ID via mocked hash → auto-salts and creates under a different ID), prefix matching (unique prefix resolves, ambiguous prefix errors with candidates, kind-prefix-less matching works), positional aliases (`:1` resolves to first item in last list, stale alias file warns), SimHash computation (identical text → identical fingerprint, similar text → low Hamming distance, unrelated text → high Hamming distance), SimHash similarity warning on `add` (mock store with existing items, verify warning emitted when distance below threshold, verify no warning when above threshold, verify `not_duplicate_of` suppresses warning), `duplicate_of` exclusion (items with non-empty `duplicate_of` excluded from `ready` and `count_todos`), scoped cross-branch Lamport clock (mock `git cat-file --batch` to simulate peek across `dev`/`main`/`HEAD`/unmerged branches, verify clock > max across scoped set, verify merged branches are excluded), cross-branch lock enforcement (mock `git cat-file --batch` to simulate lock on another branch in the scoped set, verify agent update rejected), nonce uniqueness (two ops with identical content/clock/timestamp produce byte-different serializations), `compile()` with interleaved ops from simulated concurrent branches (same clock values, clock-skewed timestamps), **`compile()` with duplicate `create` ops** (two `create` ops with same `data` but different nonces/clocks → lowest-clock `create` used for `created_at`, subsequent `create` ignored, all non-`create` ops from both branches folded normally; two `create` ops with same ID but different `data` → compile uses lowest-clock `create`, logs warning), tree traversal, `ready()` filter (items blocked by incomplete `before` predecessors excluded, transitive blocking, stale/cross-tier links ignored), `before` sorting, `before` cycle rejection
- `test_trackerset.py`: multi-tier merged reads (items from canonical + workspace + stealth appear in unified list with correct tier indicators), cross-tier `parent` resolution (workspace item with `parent` pointing to canonical item resolves correctly), cross-tier `before` resolution, `promote` (workspace → canonical: op appended, file physically moved, cache updated in both tiers, ID unchanged), `demote` (canonical → workspace: reverse), `stealth` (workspace → stealth: file moves to gitignored dir), `unstealth` (stealth → workspace), scope-aware `count_todos` (scope=`all` counts canonical + workspace; scope=`workspace` counts workspace only; stealth always counted in both modes; uses `blocking_statuses` from config), `ready` always shows all tiers regardless of scope, **self-healing reconciliation** (cross-tier duplicate with `promote` op → auto-reconciled to canonical with `reconcile` op appended; cross-tier duplicate with `demote` op → auto-reconciled to workspace; cross-tier duplicate with no tier-movement ops → `cross_tier_conflict` flag set, item excluded from `ready`; reconciliation attempt cap: item with 3+ prior `reconcile` ops → stops trying, surfaces persistent error; self-healing is append-only: verify no ops deleted or rewritten during reconciliation), **human-authority enforcement** (agent UID rejected for `lock`, `unlock`, `discuss_clear`, `stealth`, `unstealth`; agent UID accepted for `promote`, `demote`, `discuss_summarize`, `discuss`, `update`)
- `test_cache.py`: SQLite cache correctness — write-through (append op, verify cache row updated without re-parse, verify `source_size` updated), mtime invalidation (touch YAML file, verify re-parse on next read), cold start (delete `.cache.db`, verify rebuilt from YAML), corruption recovery (corrupt `.cache.db`, verify rebuilt transparently), stale cache (simulate `git pull` changing file mtimes, verify only changed items re-parsed), cache-vs-YAML consistency (compile from YAML and compare against cache row for all items), **incremental invalidation** (append discuss op to file, verify only new bytes parsed and data fields not re-compiled; append update op to file, verify full re-compile triggered; simulate `merge=union` by appending ops from two simulated branches, verify incremental parse finds all new ops; simulate file truncation/rewrite, verify fallback to full re-parse; verify `source_size` tracking is accurate across append/merge/rewrite scenarios)
- `test_compile_properties.py`: property-based tests using `hypothesis` — generate random op sequences (create followed by random update/discuss/lock/unlock ops with random clocks and timestamps) and verify: (1) idempotency (`compile(ops) == compile(ops)`), (2) permutation invariance (`compile(shuffle(ops)) == compile(ops)`), (3) terminal status consistency (compiled status = status from highest-clock update op that sets it), (4) **duplicate-create resilience** (generate op sequence with two `create` ops sharing the same `data` but different clocks/nonces, verify `compile()` produces the same result as with a single `create` op followed by the same non-`create` ops)
- `test_yaml_roundtrip.py`: adversarial inputs (`"yes"`, `"null"`, `"3.0"`, `"*bold*"`, strings with colons, leading whitespace, emoji), canonical field order verification, nonce field presence verification, **nonce-on-every-line verification** (every line of every serialized op carries a `# <nonce>` inline comment matching the `nonce` field value), **CSafeLoader/ruamel.yaml parity** (verify both parsers produce identical Python objects for all op types including adversarial inputs — note: CSafeLoader strips comments, so the nonce-on-every-line comments are not visible on the read path; comments are verified via raw string inspection of the serialized output, not via parsed data)

#### PR 2: Migration script
- `migration.py`: markdown parser, status normalizer, priority assigner (integer tiers), hash-based ID generator (SHA-256 of canonicalized `create` op `data` dict, first 128 bits proquint-encoded), writer
- Test against actual current content of both markdown files
- Creates `.agent/tracker/.ops/` (canonical tier) with migrated op log files (each dotfile containing a single `create` op)
- Creates `config.yaml.template` files for both tiers
- Creates empty `.agent/tracker-workspace/` with `.ops/`, `stealth/` dirs and template
- Tests: parse each markdown format, normalize all status variants, verify parent-child links

#### PR 3: CLI + textconv diff driver
- `cli.py`: argparse-based CLI with all subcommands (init, add, update, discuss, lock, unlock, promote, demote, stealth, unstealth, show, list, ready, log, validate, migrate, fork-setup, cache-rebuild, textconv). No `--as` flag — actor resolved internally via `os.getuid()` (see [Security Model](#security-model)). All `<ID>` arguments accept proquint prefix matching and positional aliases (`:N`). Human-authority ops (`lock`, `unlock`, `discuss --clear`, `stealth`, `unstealth`) refuse to execute under agent UIDs.
- `init`: creates both `.agent/tracker/` and `.agent/tracker-workspace/` with `.ops/` dotdirs, `stealth/` dir; copies `config.yaml.template` → `config.yaml` with human ownership (mode 644); sets up shared group if two-user deployment detected; sets up `.gitignore` entries
- `add --tier`: defaults to workspace; `--tier canonical` writes to canonical tier. Emits SimHash similarity warning if near-duplicates detected (see [Key Design Decisions](#key-design-decisions)). Supports `--duplicate-of` and `--not-duplicate-of` flags.
- `promote`/`demote`: delegates to `TrackerSet.promote()`/`demote()` (append op + file move)
- `stealth`/`unstealth`: delegates to `TrackerSet.stealth()`/`unstealth()` (workspace ↔ stealth)
- `fork-setup`: detects upstream remote, sets `stop_hook.scope: workspace` in workspace config
- `list --tier`: filter by tier; default shows all with `[C]`/`[W]`/`[S]` indicators
- `ready`: always shows all tiers (scope only affects `count-todos`)
- `discuss --summarize`: appends `discuss_summarize` op
- `log <ID>`: prints raw operation log
- `show <ID>`: prints compiled current state with tier indicator
- `textconv <FILE>`: emit compact one-line-per-field text representation for git textconv (see [textconv](#local-diff-declutter-textconv))
- `scripts/tracker`: thin AGPL-3.0 bash wrapper delegating to installed `hypergumbo-tracker` entry point (falls back to `python -m hypergumbo_tracker.cli`). Carries `SPDX-License-Identifier: AGPL-3.0-or-later` header
- `scripts/tracker-textconv`: thin AGPL-3.0 bash shim delegating to `hypergumbo-tracker-textconv` entry point, falls back to `cat` if tracker package not installed. Carries `SPDX-License-Identifier: AGPL-3.0-or-later` header
- Update `scripts/install-hooks`: add `git config diff.tracker.textconv scripts/tracker-textconv`
- `validate --similar`: runs SimHash comparison across all items, reports unflagged pairs, skips `not_duplicate_of` pairs
- `validate --deep-similar`: additionally runs embedding-based tier 2 if available, falls back gracefully
- `list` and `ready`: output numbered rows, stash ID list to `.last_list` for positional alias resolution
- Tests: each subcommand with fixture YAML files, locked field rejection, tier routing (add defaults to workspace, add --tier canonical routes to canonical), promote/demote (verify file moves between directories, op appended, ID unchanged), stealth/unstealth (verify file moves to/from gitignored dir), discussion summarization, discussion soft cap warning, `before` ordering in `list` output, `ready` excludes items with incomplete `before` predecessors and items with non-empty `duplicate_of`, `ready --limit` returns top N, `list --tier workspace` filters correctly, fork-setup detection and config write, textconv output format (verify one-line-per-field structure, verify diff of two compiled states is human-readable), textconv graceful fallback when file is malformed, prefix matching via CLI (unique prefix resolves, ambiguous prefix shows candidates), positional alias via CLI (`:1` after `list` resolves correctly, stale `.last_list` warns), `add` similarity warning (create item similar to existing, verify warning on stderr), `validate --similar` (fixture with known similar pairs, verify flagged; fixture with `not_duplicate_of`, verify suppressed)

#### PR 4: Stop hook integration + CI workflow updates
- `stop_hook.py`: scope-aware count_todos() (reads `stop_hook.scope` and `blocking_statuses` from config), hash_todos(), generate_guidance()
- Update `stop_logic.sh` with dual-mode (tracker-first with scope-aware counting, grep-fallback)
- Update `.github/workflows/ci.yml`: fix `CODE_PATTERNS` to exclude `.agent/tracker/.ops/` and `.agent/tracker-workspace/.ops/`; add `tracker_data` output to `changes` job; add `tracker-validate` job; update `ci-complete` gate; add concurrency group (see [CI Integration](#ci-integration))
- Update `.github/workflows/full-suite.yml`: fix `CODE_PATTERNS`; add `test-tracker` job; update `aggregate` (see [CI Integration](#ci-integration))
- Tests: stop_hook functions match expected counts on fixture data (test both scope=`all` and scope=`workspace`), hash stability, scope=`workspace` excludes canonical items from count

#### PR 5: Pre-commit + AGENTS.md + commit convention + branch hygiene + contribute
- Update `.githooks/pre-commit` with incremental tracker validation (staged `.ops` files only from both tiers, before Ruff — see [Pre-Commit Validation](#pre-commit-validation))
- Update AGENTS.md: replace grep pattern instructions with `scripts/tracker` equivalents; add `tracker:` commit prefix convention and batching guidance (see [Commit Convention](#commit-convention-and-git-history-hygiene)); add task-selection guidance instructing agents to use `scripts/tracker ready` (not `list`) to pick their next work item; **add agent context protection rules: "Always use `scripts/tracker show <ID>` or `scripts/tracker show <ID> --json` to read tracker item state. Always refuse to read files ending in `.ops`."** (see [Agent Context Protection](#agent-context-protection)); add branch hygiene expectation (delete feature branches after merge); update contributor workflow to reference `fork-setup` and explain three-tier model for forks; document security model and two-user setup expectations
- Update README.md: add section on recommended deployment setup — two OS user accounts (human + agent), VM with snapshots or container, with explicit setup steps (`groupadd`, `usermod`, `chgrp`, `chmod g+s`) and concise rationale (see [Security Model](#security-model))
- Update `scripts/auto-pr`: delete local and remote feature branch after successful merge (keeps the scoped Lamport clock branch set small — see [Key Design Decisions](#key-design-decisions))
- Update `scripts/contribute`: add workspace exclusion (~15 lines) to strip `.agent/tracker-workspace/` from upstream PRs
- Update stop_reflect.md, cooldown_prompt.md references
- Tests: pre-commit validation catches invalid `.ops` files from both tiers, warns on lock violations, skips gracefully when no tracker files staged; contribute workspace exclusion (mock git operations, verify workspace files excluded from PR branch)

#### PR 6: TUI
- `tui.py`: Textual app with table/tree toggle, detail panel (schema-aware field rendering: known fields in declared order with description tooltips, unknown fields in "Other" section), filter chips (including tier filter), tier indicator column (`[C]`/`[W]`/`[S]`), tier move dialog (`m` key: promote/demote/stealth/unstealth), lock/discussion UI, `before` ordering UI, operation log display, discussion warning badge (`[20+ msgs]`), schema-aware edit form (type-appropriate widgets for known fields: text area, integer spinner with min/max, list editor; generic key-value row for unknown fields), cross-tier conflict indicator for items with unresolved duplicates (see [Self-Healing Reconciliation](#self-healing-reconciliation))
- `textual~=3.0` declared as optional dep in PR 1's pyproject.toml
- Tests use Textual's `App.run_test()`/`Pilot` (headless, async via `pytest-asyncio`). All tests use `run_test(size=(80, 24))` for deterministic layout. Two test tiers:
  - **Pilot-driven flow tests:** Launch → table loads with tier indicators → selection updates detail pane; toggle table/tree (`t`) → selection stays consistent; open filter (`f`) → apply status filter → table updates; filter by tier → only matching items shown; edit (`e`) → change status → assert op appended to YAML + UI refreshes; lock toggle (`l`) → verify agent write rejected on locked field; discussion panel (`d`) → submit message → assert `discuss` op appended; `shift+d` → clear discussion; tier move (`m`) → promote item → verify file moves from workspace `.ops/` to canonical `.ops/` dir + op appended + tier indicator updates. Uses `run_before` callbacks to reach multi-keystroke states before assertions.
  - **Snapshot tests (visual regression, added once layout stabilizes):** `pytest-textual-snapshot` SVG baselines for: main screen at 80x24 and 120x40 (showing tier indicators); filter panel open; discussion panel with >20 entries badge; locked-field item with lock icon; tree view with parent-child hierarchy; detail panel for kind with `fields_schema` (known fields in order, "Other" section for unknowns) vs. kind without schema (flat key-value list); tier move dialog. Update with `pytest --snapshot-update`. Compatible with `pytest-xdist`.

#### PR 7: Deprecate markdown files
- Remove grep fallback from stop_logic.sh
- Add deprecation notice headers to the old markdown files
- Final cleanup

#### PR 8: Fork workflow hardening
- Add pre-push hook warning when workspace items are pushed to upstream remote
- End-to-end fork workflow test: fork-setup → workspace writes → contribute excludes workspace → promote → separate tracker PR
- Documentation: add fork workflow guide to README or CONTRIBUTING.md

### Verification

After each PR:
1. `pytest -n auto --cov-fail-under=100` — full test coverage (project requirement)
2. `scripts/tracker validate` — all YAML files pass schema validation (across both tiers)
3. `scripts/tracker count-todos --hard` + `--soft` — counts match expected values (test with both scope=`all` and scope=`workspace`)
4. `scripts/tracker cache-rebuild` — verify caches for both tiers rebuild cleanly and `list` output matches a full YAML-only compile
5. Incremental invalidation sanity check: append a `discuss` op to an op log file (outside the store, simulating another process), run `scripts/tracker list`, verify the item's data fields are unchanged in output and that `source_size` in `.cache.db` reflects the new file size

End-to-end after PR 4:
- Trigger stop hook, verify it uses the tracker CLI path (not the grep fallback)
- Verify circuit breaker hash from `hash-todos` matches the old grep-based hash (regression test for migration correctness)
- **Critical: end-to-end `merge=union` test.** In a temporary git repo: (1) create branches A and B from a common base with an existing op log file, (2) on branch A, append an `update` op via `scripts/tracker update`, (3) on branch B, append a *different* `update` op (same op type — the scenario that fails without nonce-on-every-line) to the same item, (4) merge B into A, (5) verify no conflict markers, both ops present as distinct YAML list items, `compile()` produces correct state reflecting both updates, (6) verify Lamport clocks are correctly ordered, (7) verify the `# <nonce>` comments on every line of each op survived the merge intact. Additionally test with identical `set:` blocks across ops (the scenario where nonce-on-first-line fails due to line stripping). This test validates the nonce-on-every-line + `merge=union` design. Add to `test_store.py` as an integration test using `subprocess` to run actual git commands in a temp repo.
- **End-to-end rebase safety test.** Same setup as the merge test, but (4a) rebase branch A onto B instead of merging, (4b) then merge the pre-rebase lineage with the post-rebase result. Verify no duplicate ops, no stripped fields, correct `compile()` output. This validates that nonce-on-every-line makes the tracker rebase-safe.
- **Cross-branch duplicate creation test.** In a temporary git repo: (1) create branches A and B from a common base with *no* op log file, (2) on branch A, `scripts/tracker add` with specific title/fields, (3) on branch B, `scripts/tracker add` with *identical* title/fields (producing the same content-hash ID and same filename), (4) on each branch, append a different `update` op to the item (A sets status to `in_progress`, B adds a field), (5) merge B into A, (6) verify no conflict markers, file contains two `create` ops and both `update` ops, (7) `compile()` produces correct state: `created_at` from the lowest-clock `create`, status and fields reflect both updates, (8) `validate` emits informational notice about duplicate `create` ops but does not error. This validates the duplicate-`create`-op tolerance described in [Compile Rules](#compile-rules-conflict-resolution).

End-to-end after PR 5:
- Simulate fork workflow: create item in workspace, verify `contribute` excludes it from PR, promote item to canonical, verify it appears in a separate commit
- Verify `fork-setup` detects upstream remote and sets `stop_hook.scope: workspace`

End-to-end after PR 6:
- `scripts/tracker tui` launches, displays all items from both tiers in table and tree views with tier indicators
- Status edits, field locking, discussion threads, tier moves (promote/demote/stealth/unstealth), and `before` ordering all persist correctly
- Discussion warning badges appear for items with >20 entries
- Operation log is visible and scrollable in detail panel
- All Pilot flow tests pass under `run_test(size=(80, 24))` with `tmp_path`-backed `TrackerSet` (no real tracker directory)
- Snapshot baselines committed and passing (once layout stabilizes)

End-to-end after PR 8:
- Full fork lifecycle: fork → clone → fork-setup → agent creates workspace items → contribute (workspace excluded) → promote item → tracker PR → upstream merge → sync fork

### CI Integration

Tracker op log files (`.ops`) are tracked on the main branch alongside code. Without careful CI configuration, every tracker change would trigger the full test suite unnecessarily, waste CI runner time, and potentially block the CI queue for real code changes. This section ensures tracker changes are smooth: no wasted CI, no blocked PRs, no noisy diffs.

**Key architectural advantage**: The existing CI already uses a `changes` job with job-level `if:` conditionals (not workflow-level `paths` filters), and a `ci-complete` gate as the sole required branch protection check. Jobs skipped via `if:` report as "skipped" which counts as passing for required checks in Forgejo Actions. This means we don't need structural changes — just surgical updates to the change detection logic and one new lightweight job.

#### Fix CODE_PATTERNS

The `changes` job in both `ci.yml` and `full-suite.yml` uses `CODE_PATTERNS` to decide whether expensive jobs run:

```bash
CODE_PATTERNS='\.py$|\.yaml$|\.yml$|\.json$|\.toml$|pyproject\.toml|scripts/|\.github/workflows/'
```

The `\.yaml$` pattern matches tracker config files, and `.ops` files could match other patterns. Fix by splitting detection to exclude tracker data:

```bash
CODE_PATTERNS='\.py$|\.json$|\.toml$|pyproject\.toml|scripts/|\.github/workflows/'
YAML_PATTERN='\.ya?ml$'
TRACKER_DATA='^\.agent/tracker(-workspace)?/'

CHANGED=$(git diff --name-only "$base" "$head")

has_code=false
# Non-YAML code files
echo "$CHANGED" | grep -qE "$CODE_PATTERNS" && has_code=true
# YAML files that aren't tracker data
echo "$CHANGED" | grep -E "$YAML_PATTERN" | grep -vqE "$TRACKER_DATA" && has_code=true

echo "code=$has_code" >> "$GITHUB_OUTPUT"
```

Apply this to both `.github/workflows/ci.yml` (line 52) and `.github/workflows/full-suite.yml` (line 60).

#### .gitattributes

No `.gitattributes` exists in the repo. Create one at the repo root:

```gitattributes
# Tracker op log files: machine-generated append-only operation logs.
# - linguist-generated: collapse in PR diffs, exclude from language stats
# - merge=union: on conflict, keep lines from both sides (matches append-only design)
# - diff=tracker: use textconv driver to show compiled state in diffs (see [textconv](#local-diff-declutter-textconv))
.agent/tracker/.ops/.*.ops             linguist-generated  merge=union  diff=tracker
.agent/tracker-workspace/.ops/.*.ops   linguist-generated  merge=union  diff=tracker
.agent/tracker/config.yaml.template             linguist-generated
.agent/tracker-workspace/config.yaml.template   linguist-generated
```

**`merge=union`** is the critical entry. It tells git that when a merge conflict occurs in these files, include all lines from both sides. This is exactly right for append-only operation logs: two branches that both appended ops will have all ops preserved without conflict markers. This upgrades the merge guarantee from "git usually handles appends correctly" to "git is explicitly told to keep everything from both sides."

**`linguist-generated`** causes Forgejo/Gitea to collapse these files in PR diffs by default and exclude them from language statistics, reducing review noise.

**`diff=tracker`** assigns a custom textconv diff driver ([textconv](#local-diff-declutter-textconv)) that shows compiled item state instead of raw operation logs in local `git log -p` and `git diff`. This is especially valuable now that op logs are in dotfiles — `git log -p` would otherwise show raw ops from hidden files, which is confusing. The textconv driver shows the compiled state instead.

#### Add tracker_data Output

Add a second output to the `changes` job so the new `tracker-validate` job knows when to run:

```yaml
outputs:
  code: ${{ steps.filter.outputs.code }}
  tracker_data: ${{ steps.filter.outputs.tracker_data }}
```

Detection logic (appended to the filter step):

```bash
if echo "$CHANGED" | grep -qE '^\.agent/tracker(-workspace)?/|^packages/hypergumbo-tracker/'; then
  echo "tracker_data=true" >> "$GITHUB_OUTPUT"
else
  echo "tracker_data=false" >> "$GITHUB_OUTPUT"
fi
```

Note: `packages/hypergumbo-tracker/` source changes also trigger `code=true` (because `.py` matches `CODE_PATTERNS`), so the full code CI runs too. The `tracker_data` output additionally triggers the lightweight validation job below.

#### Add tracker-validate Job

A fast job (~10 seconds) that validates tracker YAML schema. No tree-sitter grammars, no grammar wheel builds, no venv cache — just pip-install the tracker package and run `validate`:

```yaml
tracker-validate:
  needs: [changes, stop-the-line]
  if: >-
    always() &&
    needs.changes.outputs.tracker_data == 'true' &&
    needs.stop-the-line.result != 'failure'
  runs-on: codeberg-small-lazy
  steps:
    - uses: actions/checkout@v4
    - name: Set up Python
      uses: actions/setup-python@v5
      with:
        python-version: "3.11"
    - name: Install tracker
      run: pip install -e packages/hypergumbo-tracker
    - name: Validate
      run: scripts/tracker validate
```

#### Update ci-complete Gate

Add `tracker-validate` to the gate job's `needs` list and failure check:

```yaml
ci-complete:
  needs: [changes, stop-the-line, lint, audit, verify-generated, build-grammars, pytest, dco, tracker-validate]
  if: always()
  # ... existing steps, plus:
  #   [[ "${{ needs.tracker-validate.result }}" == "failure" ]]
  # in the failure check block
```

Since `ci-complete` is the sole required branch protection check, this ensures:
- Tracker-only PRs: code jobs skip ("skipped"), `tracker-validate` runs → `ci-complete` passes
- Code-only PRs: `tracker-validate` skips ("skipped"), code jobs run → `ci-complete` passes
- Mixed PRs: both run → `ci-complete` passes if both pass

#### Add Concurrency Group

Prevents CI queue congestion from rapid agent commits. Add at the top level of `ci.yml`, after `on:`:

```yaml
concurrency:
  group: ci-${{ github.head_ref || github.ref_name }}
  cancel-in-progress: true
```

When a new push arrives on a branch while CI is running for that branch, the in-progress run is cancelled and replaced. This is safe because `ci-complete` is the only required check — cancellation doesn't leave stale "pending" status checks. The `full-suite.yml` already has a singleton concurrency group with `cancel-in-progress: false` (correct — full suite should not be interrupted).

#### Commit Convention and Git History Hygiene

Document in `AGENTS.md`:

**Commit prefix.** Tracker-only changes use a `tracker:` conventional-commit prefix:
```
tracker: close INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit, update 3 work items
tracker: batch status updates for completed invariants
```

**Batching.** Agents should batch tracker operations into fewer commits rather than committing after every `scripts/tracker update` call. Perform all tracker updates for a logical unit of work, then commit once with a summary message.

**Filtering.** To view history without tracker noise:
```bash
git log --oneline -- ':!.agent/tracker/.ops' ':!.agent/tracker-workspace/.ops' ':!.agent/tracker-workspace/stealth'  # path-based (always works)
git log --oneline --invert-grep --grep='^tracker:' # prefix-based (requires convention)
```

#### Add test-tracker Job

Add a lightweight parallel test job alongside `test-core`, `test-mainstream`, `test-common`, `test-extended`:

```yaml
test-tracker:
  needs: [changes]
  if: needs.changes.outputs.code == 'true'
  runs-on: codeberg-small-lazy
  outputs:
    coverage: ${{ steps.tests.outputs.coverage }}
  steps:
    - uses: actions/checkout@v4
    - name: Set up Python
      uses: actions/setup-python@v5
      with:
        python-version: "3.11"
    - name: Install and test
      id: tests
      run: |
        pip install --upgrade pip
        pip install -e packages/hypergumbo-tracker[tui] pytest pytest-cov pytest-xdist
        pytest packages/hypergumbo-tracker/tests/ -n auto --tb=short \
          --cov=packages/hypergumbo-tracker/src --cov-report=term | tee coverage-output.txt
        COV=$(grep "^TOTAL" coverage-output.txt | awk '{print $NF}' | tr -d '%')
        echo "coverage=${COV:-0}" >> "$GITHUB_OUTPUT"
```

This job does **not** need the `prep` job, grammar wheels, or heavy deps — the tracker package has no tree-sitter dependency. Update the `aggregate` job to include `test-tracker` in its `needs` list and coverage reporting.

#### Smart Test Selection

`.agent/tracker/.ops/*.ops` data changes produce `code=false` from the `changes` job, so smart test selection (ADR-0010) is never invoked for tracker-only changes. No changes to `scripts/smart-test` or `.ci/affected-tests.txt` handling needed.

When `packages/hypergumbo-tracker/` code changes, `code=true` fires and the existing smart test selection runs normally. The `scripts/check-package-coverage` PACKAGES map needs a `tracker` entry to include the tracker package in per-package CI isolation (already noted in [Files Modified](#files-modified)).

#### Local Diff Declutter (textconv)

`linguist-generated` ([.gitattributes](#gitattributes)) collapses tracker diffs in Forgejo/Codeberg PR views, but doesn't help locally — `git log -p`, `git diff`, and `git show` still dump raw operation logs from the `.ops` dotfiles. Inspired by the smart-test pattern (wrap the tool, show a compact summary, keep the full output accessible), a **textconv diff driver** solves this transparently for all local git diff commands.

**How it works.** Git's `diff.<driver>.textconv` config points to an executable that converts a file to a text representation before diffing. Git runs the converter on both the old and new versions, then diffs the *text representations*. The `diff=tracker` attribute in `.gitattributes` ([.gitattributes](#gitattributes)) assigns this driver to all tracker `.ops` files.

**Setup** (added to `scripts/install-hooks`):
```bash
git config diff.tracker.textconv scripts/tracker-textconv
```

**`scripts/tracker-textconv`** — a thin AGPL-3.0 bash shim that delegates to the MPL-2.0 `hypergumbo-tracker-textconv` entry point, with graceful fallback:
```bash
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Git textconv driver for tracker op log files.
# Delegates to the MPL-2.0 entry point; falls back to raw YAML if not installed.
hypergumbo-tracker-textconv "$1" 2>/dev/null && exit 0
cat "$1"
```

**`scripts/tracker textconv <FILE>`** — CLI subcommand that compiles the item and emits a compact, one-line-per-field text representation designed for readable diffs:

```
INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit  Call Attribution Completeness
  status: todo_hard  priority: P0  tags: [analysis_quality]
  parent: null  before: []  pr_ref: null
  fields.statement: Every emitted `calls` edge has a non-null caller symbol
  fields.root_cause: JS/TS arrow function early-return in _get_enclosing_function()
  fields.fix: Position-based lookup for arrow functions
  discussion: 2 entries
  locked: [priority]
  ops: 6  updated: 2026-02-11T19:30:00Z
```

When a field changes, `git log -p` shows a clean diff of the compiled states:

```diff
 INV-lusab-bired-fomak-gunid-hasob-jikal-mofad-nukit  Call Attribution Completeness
-  status: todo_hard  priority: P0  tags: [analysis_quality]
+  status: done       priority: P0  tags: [analysis_quality]
   parent: null  before: []  pr_ref: null
   ...
-  ops: 6  updated: 2026-02-11T19:30:00Z
+  ops: 7  updated: 2026-02-12T10:00:00Z
```

Instead of the raw YAML op that was appended:

```diff
+- op: update  # e6f7
+  at: "2026-02-12T10:00:00Z"  # e6f7
+  by: agent  # e6f7
+  clock: 7  # e6f7
+  nonce: e6f7  # e6f7
+  set:  # e6f7
+    status: done  # e6f7
```

**Bypass.** `git log -p --no-textconv` (or `git diff --no-textconv`) shows the raw `.ops` file content when needed. This is the standard git escape hatch — no custom flags required.

**Bootstrapping.** On a fresh clone before `dev-install`, the `hypergumbo-tracker-textconv` entry point isn't available. The `cat "$1"` fallback in `scripts/tracker-textconv` ensures diffs still work — they just show raw ops until the package is installed. No broken state, just degraded display.

| | smart-test | tracker-textconv |
|---|---|---|
| Wraps | pytest | git diff rendering for `.ops` |
| Shows | ~20-line compact summary | compiled item state |
| Full output | `.ci/pytest-output.log` | raw `.ops` content (via `--no-textconv`) |
| License | AGPL-3.0 (part of hypergumbo) | Entry point is MPL-2.0; `scripts/tracker-textconv` shim is AGPL-3.0 |
| Setup | alias in `.venv/bin/pytest` | `diff=tracker` in `.gitattributes` + config in `install-hooks` |
| Transparent | yes (alias) | yes (git attribute) |

## Consequences

### Positive
- Schema enforcement eliminates vocabulary drift and malformed entries — the demonstrated threat that motivated this ADR
- OS-level actor resolution (`os.getuid()`) provides non-forgeable human authority without crypto key management
- Gitignored config with OS file permissions makes governance rules harder for the agent to modify than source code
- Three-layer safety model: tracker self-heals deterministic issues, agent uses CLI as black box, unanticipated errors surface to human with hardcoded blast radius limits
- Agents get structured task selection via `scripts/tracker ready` instead of fragile grep patterns
- Humans get a TUI for browsing, triage, field locking, and async discussion
- Fork-safe three-tier visibility enables contributor workflows without governance conflicts
- `merge=union` with nonce-on-every-line eliminates merge conflicts for concurrent agent edits and is safe under both merge and rebase
- Append-only operation log provides a complete audit trail with no additional infrastructure
- Self-healing cross-tier reconciliation handles interrupted tier moves and merge artifacts without agent involvement
- Reusable across projects — standalone MPL-2.0 package with no hypergumbo-core dependency; MPL's file-level copyleft removes the AGPL adoption barrier for projects that want agent governance without code analysis
- Content-hash IDs provide natural deduplication without coordination
- SQLite read cache (XDG-compliant, per-user) makes frequent agent queries (count-todos, ready) sub-millisecond
- Config-defined statuses and blocking semantics — governance changes are config changes, not code PRs

### Negative
- New package adds maintenance surface (~10 source modules, ~11 test modules)
- Nonce-on-every-line makes op log files more verbose (every line carries `# <nonce>` suffix)
- Two YAML libraries (ruamel.yaml for writes, PyYAML/CSafeLoader for reads) in the dependency tree
- Dual-license repo (AGPL + MPL) requires SPDX headers on every file and clear contributor documentation
- Migration is a one-way door — reverting to markdown after migration loses op-log history
- Cross-branch Lamport clock adds coupling between the tracker store and git internals
- Op log files grow monotonically; compaction is deferred to a future revision
- Two-user deployment requires OS-level setup (shared group, setgid); single-user deployments degrade to social controls for human authority enforcement

### Neutral
- The TUI depends on Textual (optional extra), keeping the core CLI lightweight
- Embedding-based dedup (tier 2) is optional and degrades gracefully when unavailable
- Stealth tier is gitignored — provides privacy but no backup
- `linguist-generated` collapses tracker diffs in PRs, reducing review noise at the cost of visibility
- `flock()` advisory locking protects against concurrent tracker processes but not arbitrary file writes — acceptable because the store is the sole writer

## References

- [ADR-0008: Autonomous Governance](0008-autonomous-governance-and-vendor-agnostic-hooks.md) — Stop hook system this replaces
- [ADR-0010: Modular Packages](0010-modular-packages-and-smart-testing.md) — Package structure pattern followed
- [git-bug](https://github.com/MichaelMure/git-bug) — Operation-sourced model inspiration
- [beads](https://github.com/steveyegge/beads) — Per-field resolution strategy inspiration
- [proquint](https://arxiv.org/html/0901.4016) — Pronounceable hash encoding
- `.agent/invariant-ledger.md` — Current invariant tracking (replaced by tracker)
- `~/hypergumbo_lab_notebook/guidance_log/work_items.md` — Current work item tracking (replaced by tracker)

