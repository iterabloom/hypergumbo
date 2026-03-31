<!-- SPDX-License-Identifier: MPL-2.0 -->
# ADR-0021: Tracker Federation

Date: 2026-03-30
Status: Proposed

## Context

### From single-node to multi-node

hypergumbo-tracker (ADR-0013) was designed for a single repository with one agent and one human. The append-only op-log, Lamport clock, nonce-on-every-line merge safety, and content-hash IDs were all motivated by concurrent git branches — but they are structurally identical to the primitives needed for multi-node replication over a network.

ADR-0019 introduces authenticated, encrypted, NAT-traversing connectivity between machines that can only dial out (Tor onion service + opportunistic WireGuard). Once that transport exists, the question becomes: can multiple htrac instances share state?

### Why federation matters

Concrete scenarios:

- **Multiple homelab VMs** running different agents (one per project, or one per language ecosystem). The human manages all of them from one phone via the web UI. Work items created on the phone propagate to the relevant agent's tracker. Status updates propagate back.
- **Distributed team** where each contributor runs their own agent. Canonical-tier items sync across nodes; workspace-tier items stay local to each contributor's fork.
- **Resilience.** If one node goes offline, the others continue operating. When it reconnects, ops catch up and `compile_ops()` produces the same result everywhere.

### Two orthogonal dimensions

Federation involves two independent design axes:

**Repo relationship** — what code are the nodes working on?

| Type | Shared code? | Shared items make sense? | Example |
|------|-------------|------------------------|---------|
| Same clone | Identical files | All items are about the same codebase | Two agents pairing on one repo (careful — editable install conflicts) |
| Different clone | Identical repo | All items are about the same codebase | CI agent + dev agent on different machines |
| Downstream fork | Overlapping | Upstream canonical items are relevant; fork-specific items are not | Contributor's fork |
| Different project | None | Only cross-project items are relevant (e.g., "wait for upstream library release") | Separate repos with a dependency relationship |

**Authority relationship** — who tells whom what to do?

| Type | Incoming items behave as... | Peer can change our items? | Mental model |
|------|---------------------------|---------------------------|--------------|
| Directive | Blocking work (show in `ready`, count in `count-todos`) | Yes — peer's updates are authoritative | Boss → worker |
| Advisory | Visible but non-blocking (show in `list`, not in `ready`) | No — read-only mirror | Colleague → colleague |
| Read-only | Visible, explicitly non-actionable, tagged `[remote]` | No | Observer |

These compose independently: a downstream fork might be advisory (you see their work but it doesn't block you), while a CI agent on a different clone of the same repo might be directive (its failure items become your `todo_hard`).

### Named identities

The current tracker has roles (`by: human`, `by: agent`) but not identities. `resolve_actor()` returns `("human", "jgstern")` or `("agent", "jgstern_agent")` based on `os.getuid()` — the username is recorded but not meaningful beyond distinguishing role. In a multi-human, multi-agent federation, "human" is insufficient — you need to know *which* human, *which* agent.

Federation requires every participant to have a **name** — a human-readable identifier that is unique within the federation, stable across sessions, and meaningful in discussion threads, assignee fields, authority configs, and `@` mentions.

### The missing assignee field

In a single-agent setup, there is no assignee — `ready` returns the top-priority item and the one agent works on it. With multiple agents, the question "which agent should work on this?" has no answer without an assignee field.

The current `CompiledItem` has no `assignee`. The existing set-valued fields (`tags`, `before`, `duplicate_of`, `not_duplicate_of`) use accumulation semantics via `add`/`remove` in update ops. Assignee fits the same pattern — an item may be assigned to multiple nodes/agents, and assignments are added/removed over time.

### What the current architecture already provides

The tracker's data model is closer to federation-ready than a typical application:

| Property | Current design | Federation implication |
|----------|---------------|----------------------|
| Append-only ops | Ops are never mutated or deleted | Each node's local history is durable and complete — ops stay on the origin |
| Lamport clock | Causal ordering across git branches | Orders ops within each node; not needed across nodes (compiled-view sync doesn't exchange ops) |
| Content-hash IDs | Same logical item produces same proquint ID everywhere | Natural deduplication — if two nodes independently create the same item, the IDs match and the collision is detectable |
| SimHash dedup | Near-duplicate detection on `add()` | Catches semantically similar items created on different nodes via the write-at-origin API |
| `compile_ops()` | Deterministic LWW fold over clock-ordered ops | Each node compiles its own ops; peers receive the compiled result, not the ops |
| Three-tier visibility | canonical / workspace / stealth | Stealth items never leave the node. Workspace items are exposed only to peers configured for workspace sync. Canonical items sync everywhere. |
| Lock enforcement | Human-locked fields reject agent writes | Locks are enforced at the origin on every write request — both local and remote |

### What is missing

The current architecture uses **git as the transport**: `git push`, `git fetch`, `merge=union`. Federation adds a network-based sync layer alongside git (which continues to handle single-repo branch merges). The key problems to solve:

1. **Peer discovery and identity.** Git has remotes. Federation needs a named peer registry with human and agent identities.
2. **Read sync protocol.** Peers need to see each other's compiled item state in real-time, without exchanging ops.
3. **Write routing.** Modifications to remote items must be sent to the origin node for validation and op-log append.
4. **Partition handling.** Reads degrade gracefully (stale cache). Writes queue for delivery on reconnect.
5. **Trust boundaries.** Authority is per-peer, per-scope, split by human/agent actor type, and non-totalizing.

## Decision

### Replication model: compiled-view sync with write-at-origin

Each htrac node maintains its own op-log files locally (unchanged from ADR-0013). **Raw ops — the append-only log with Lamport clocks, nonces, and causal ordering — never leave the node that created them.** Federation syncs **compiled item state**, not ops. Compiled state — including discussion thread text, field values, and metadata — is shared via the compiled-view feed. The security boundary is the raw op log (which contains structural information about branch merges, clock values, and nonce patterns), not the semantic content of items.

This is a deliberate choice. The alternative — op-log gossip with vector clocks — is more powerful (offline writes, full history replication, mathematical convergence guarantees) but adds complexity that doesn't match how agents are actually used. Agents are long-lived, accumulate expertise, and are the entity you want to interact with. Writes should go to the agent that owns the item, not to a local log for later merge. If the origin node is down, the agent's expertise is unavailable regardless — queuing an op locally doesn't give you the agent's judgment, just a deferred message.

**Read path (compiled-view feed):**

Each node exposes its compiled items as a read-only feed over WebSocket (via the ADR-0019 transport). Peers subscribe and receive typed events:

| Event type | Payload | When sent |
|------------|---------|-----------|
| `item_snapshot` | Full compiled item (all fields + full discussion thread + `event_seq`) | On connect, on reconnect, on full recompile, on gap repair |
| `item_update` | Item ID + changed scalar fields + `event_seq` (no discussion) | When scalar fields change (status, priority, assignee, etc.) |
| `item_discuss` | Item ID + single new `DiscussionEntry` + `event_seq` | When a new discussion entry is appended |

The receiver maintains a local cache. `item_update` patches the cached item's scalar fields. `item_discuss` appends to the cached discussion list. `item_snapshot` replaces the entire cached item (used for initial sync and recovery after reconnect). This avoids re-sending the full discussion thread on every field change — for active items with long discussions, the bandwidth savings are significant.

**Gap detection:** Each origin assigns a monotonically increasing `event_seq` per item. Incremental events (`item_update`, `item_discuss`) carry the item's current `event_seq`. If a receiver detects a gap (received seq N, last seen was N-2), it requests an `item_snapshot` for that item to repair the gap. This prevents silent data loss in discussion threads during network glitches.

The feed is filtered by the peer's `sync_tiers` and `item_filter` config. Stealth items are never exposed. Workspace items are exposed only to peers configured for workspace sync.

Receiving nodes store remote items in a local cache (keyed by origin node + item ID) for display in the TUI, web UI, and CLI. Remote items are clearly tagged with their origin. They are **not** written to the local ops directory — they exist only in the cache.

**Write path (API calls to origin):**

To modify a remote item (update status, add discussion, change assignee), the local node sends a write request to the originating node's API:

```
POST /api/items/{id}/update   → update fields
POST /api/items/{id}/discuss  → add discussion entry
POST /api/items/{id}/lock     → lock fields (human only)
```

The originating node validates the request (authority checks, lock enforcement, role checks), appends the op to its local log, compiles, and pushes the updated compiled view to all subscribed peers.

**Queued writes for offline peers:**

If the origin node is unreachable, the local node queues the write request. Each queued write carries a `based_on_version` field — the `item_version` from the compiled state the writer saw when making the decision.

When connectivity is restored, queued writes are delivered in order. The origin checks each write's `based_on_version` against the item's current `item_version`. If the item has been modified since the queued write was created (current `item_version` > `based_on_version`), the origin rejects the write with a `stale_write` error containing the current compiled state. The sender can then:
- **Retry** with updated state (re-evaluate the decision against current data),
- **Escalate** to the human (flag the conflict in `check-messages`), or
- **Drop** the write (the decision is no longer relevant).

This prevents stale queued writes from overwriting decisions made at the origin during the partition. For example, if an agent queues `status=done` while offline and the human sets `status=wont_do` at the origin during the same partition, the queued write is rejected rather than silently overwriting the human's decision.

**Wire format:** Compiled items are exchanged as JSON. The feed envelope carries a `protocol_version: int` field from day one — receivers ignore unknown fields but can detect incompatible schema changes. Retrofitting versioning after deployment is painful; adding it now is cheap. Each item carries: id, kind, title, status, priority, assignee, assignment_source, parent, tags, before, duplicate_of, not_duplicate_of, pr_ref, description, fields, locked_fields, discussion (in `item_snapshot` only — incremental events use `item_discuss`), created_at, updated_at, item_version, effective_actor, tier, origin_node. Discussion entries include by, actor, effective_actor, at, message, and is_summary.

`item_version` is a monotonic counter incremented on each op appended to the item's log. It serves two purposes: (1) queued writes reference `based_on_version` so the origin can reject stale writes, and (2) receivers can detect whether a compiled view is newer than their cached copy without comparing every field.

**What this model does not provide:**

- **Full history replication.** Peers see current state and discussion threads, not the sequence of all ops that produced that state. Status transitions, field change history, and who-changed-what-when are only available on the origin node.
- **Offline writes that merge automatically.** Writes require connectivity to the origin. If both the origin and a peer independently modify the same item while disconnected, the peer's queued write is rejected on reconnect (via `based_on_version` staleness check) rather than silently overwriting the origin's changes. The sender must retry, escalate, or drop the write.
- **Mathematical convergence guarantee.** With op-log sync, same ops → same compiled state everywhere. With compiled-view sync, you trust each node's `compile_ops()`. A bug in one node's compiler produces divergent state with no mechanism for peers to detect it. In practice, all nodes run the same htrac version, so this is unlikely but not impossible during rolling upgrades.

**Transport:** WebSocket connections established over the ADR-0019 transport (Tor or WireGuard). The feed uses a subscribe/push model: subscribe on connect, receive incremental updates in real-time, request full snapshot on reconnect.

### Assignee field

A new set-valued field `assignee` is added to the tracker data model:

**On `CompiledItem`:**
```python
assignee: list[str] = field(default_factory=list)
assignment_source: dict[str, dict[str, str]] = field(default_factory=dict)
# Maps assignee name -> {"by": "human"|"agent", "actor": "<name>", "at": "<ISO8601>"}
```

`assignment_source` tracks who assigned each node and when. It is updated by `compile_ops()` whenever an `add: {assignee: [...]}` op is processed: the op's `by`, `actor`, and `at` fields are recorded for each added assignee. When an assignee is removed, its entry is deleted from `assignment_source`. This metadata is available in the compiled-view feed, so remote nodes can resolve authority without op-log access.

**In `store.py` constants:**
- Added to `_SET_VALUED_FIELDS` (accumulation via `add`/`remove` in update ops)
- Added to `_UPDATABLE_FIELDS` (can be set via `update`)
- Added to `_LOCKABLE_FIELDS` (human can lock assignment)

**In `CreateOp` data dict:**
- `assignee` is an optional list of strings in the create op's data

**Assignee values** are peer names from the federation config (e.g., `"lab-vm-1"`, `"ci-agent"`) or the special value `"self"` which resolves to the local node's name. In a non-federated setup, assignee values are free-form strings — the field is useful even without federation (e.g., tagging items for different human team members).

**CLI:**
```bash
htrac add --kind work_item --title "Fix parser" --assignee lab-vm-1
htrac update INV-foo --add assignee=ci-agent
htrac update INV-foo --remove assignee=lab-vm-1
htrac ready                          # Shows items assigned to this node (or unassigned)
htrac ready --all                    # Shows all actionable items regardless of assignee
htrac list --assignee lab-vm-1       # Filter by assignee
```

**Interaction with `ready` and `count-todos`:**

**Assignee filtering is always active** — not gated on federation configuration. `ready` shows items that are either:
- Explicitly assigned to this node (matching `node.name` from config), or
- Unassigned (empty assignee list — available for any node to claim)

Items assigned to a different node are excluded from `ready` and `count-todos` — they don't block the local agent. This prevents a single `todo_hard` item assigned to Node A from blocking the agent on Node B.

**If no `node.name` is configured** (setup wizard not run), assignee filtering is disabled — all blocking items appear in `ready`, preserving current behavior. Once `htrac setup` establishes a node name, filtering activates. For free-form organizational tagging, use `tags` — `assignee` is always routing-significant.

**Self-assignment constraint:** Agents can always self-assign (claim unassigned work). Whether an agent can **self-unassign** depends on the authority of the assignment, looked up from `assignment_source[this_node]`: if the recorded actor had **directive** authority (per the scoped authority rules), the agent cannot remove itself — the assignment is an obligation. If the assigning actor had **advisory** or **read_only** authority, the agent can self-unassign — the assignment was a suggestion, not a directive. The agent's own human's assignments are always directive (hardcoded invariant) and therefore always irrevocable by the agent. Since `assignment_source` is available in the compiled-view feed, this constraint works for both local and remote items without op-log access.

### Node identity and the setup wizard

The setup wizard (`htrac setup`) establishes the identities for the node:

```yaml
node:
  name: "lab-vm-1"        # unique within the federation, stable across sessions
  human: "jake"           # the human who owns this node
  agent: "lab-vm-1"       # the agent identity (often same as node name)
```

**The wizard prompts:**
```
Node name: This identifies your tracker instance in the federation.
Other nodes will see items from this name and can assign work to it.
Enter a name for this node [default: hostname]:

Human name: Your identity in discussion threads and @mentions.
Enter your name [default: OS username]:
```

Defaulting node name to hostname and human name to OS username is reasonable — both are already locally unique and descriptive. The user can override with something more meaningful.

**How identity changes `resolve_actor()`:**

Currently returns `("human", "jgstern")` or `("agent", "jgstern_agent")` — a role plus an OS username. With named identity, it returns the configured names instead:

| Current | With named identity |
|---------|-------------------|
| `("human", "jgstern")` | `("human", "jake")` |
| `("agent", "jgstern_agent")` | `("agent", "lab-vm-1")` |

The `actor` field on every op becomes the configured name, not the OS username. This makes ops portable across machines and readable in discussion threads. The OS username is still used for the human-vs-agent role check (`os.getuid()` matching against `agent_usernames` patterns) — the name is for identity, the UID is for authorization.

**Uniqueness enforcement:** Within a federation, two nodes cannot share a name — assignee values and `@` mentions would be ambiguous. On `register-peer`, existing nodes reject a peer whose name collides with an existing peer or with themselves. Human names must also be unique within the federation's human set.

**Renaming and migration:** Discouraged, but supported. If a node or human is renamed, old ops retain their original `actor` field — the ops log is append-only and immutable, so `compile()` does not rewrite old actor values.

Aliases live in the **node config** (`config.yaml` under `node:`):

```yaml
node:
  name: "lab-vm-1"
  aliases: ["jgstern_agent"]     # old OS username from pre-federation ops
  human: "jake"
  human_aliases: ["jgstern"]     # old OS username
```

**Alias resolution in `compile_ops()`:**

`compile_ops()` accepts an optional `aliases` config (derived from `node.aliases` and `node.human_aliases` in config.yaml). During compilation, each op's `actor` field is resolved to an `effective_actor` using the alias mapping. This resolution happens at compile time, not at display time — downstream code uses `effective_actor` exclusively and never checks aliases directly.

**On `CompiledItem`:**
```python
effective_actor: str = ""   # resolved name of the last writer
```

**On `DiscussionEntry`:**
```python
effective_actor: str = ""   # resolved name of the discussion author
```

**How aliases flow through the system:**
- **`compile_ops()`:** Resolves `actor` → `effective_actor` for each op as it is folded. The raw `actor` field on ops is preserved (append-only log is immutable); aliasing is a compile-time concern.
- **Display (TUI, web UI, CLI `show`):** Uses `effective_actor` for all display. An op with `actor: "jgstern_agent"` renders as `lab-vm-1` because `compile_ops()` resolved the alias.
- **`ready` filtering:** Uses `effective_actor` (and the current node name) when matching assignee values.
- **`@` mention detection:** Uses the current name and all aliases when scanning discussion text for mentions (aliases are needed here because the raw text contains `@jgstern_agent`, not `@lab-vm-1`).
- **`check-messages`:** Uses `effective_actor` to determine who sent the last message.
- **Wire format (federation feed):** Sends `effective_actor` in compiled items and discussion entries. Remote nodes never see raw aliases — they see resolved names.

This centralizes alias resolution in one place (`compile_ops()`) rather than duplicating it across ~9 downstream call sites.

**Non-federated setups:** The node name is still useful — it shows up in discussion entries, `ready` output, and the TUI. A single-agent setup just has one node with one human. The wizard should always ask for names, not only when federation is configured.

### Peer identity and discovery

Each node has a **peer ID** derived from its Tor onion service address (a v3 `.onion` address is a public key — it's already a cryptographic identity). Peers are registered in `config.yaml`:

```yaml
federation:
  config_version: 1
  peers:
    - id: "abc...xyz.onion"
      name: "lab-vm-1"
      human: "jake"
      repo_relation: same_clone
      sync_tiers: [canonical]
      authority_rules:
        - scope: "*"                       # same human, same project — always listen
          from_human: directive
          from_agent: directive
    - id: "def...uvw.onion"
      name: "sarah-vm"
      human: "sarah"
      repo_relation: fork
      sync_tiers: [canonical, workspace]
      authority_rules:
        - scope: "tags:sarahproject"       # sarah is the expert here
          from_human: directive
          from_agent: advisory
        - scope: "*"                       # everything else is FYI
          from_human: advisory
          from_agent: read_only
    - id: "ghi...rst.onion"
      name: "upstream-lib"
      human: "alex"
      repo_relation: different_project
      sync_tiers: [canonical]
      item_filter: "tags:release"
      authority_rules:
        - scope: "*"
          from_human: read_only
          from_agent: read_only
```

**`config_version`** enables forward-compatible config evolution. `htrac` validates the federation config against the expected version on startup. Unknown version → error with migration guidance. Unknown fields within a known version are ignored (same policy as the wire `protocol_version`). The version is bumped when: `authority_rules` syntax changes, new required peer fields are added, or `item_filter` predicate syntax changes.

Discovery is manual (add peers to config via the human's phone app per ADR-0019). Automatic discovery is explicitly out of scope — federation is between known, trusted nodes, not an open mesh.

### Authority hierarchy

Authority is split by actor type — a peer's human and a peer's agent may have different authority levels over the local node. This reflects the natural hierarchy: you might trust another human's judgment (advisory) while treating their agent's output as informational only (read-only).

**Non-totalizing by construction:** Authority is **per-peer, per-scope**, not per-peer globally. The same two nodes can have reversed authority depending on which project or context is in play. No peer is ever globally "the boss."

This prevents the federation from collapsing into a hierarchy. ci-server might be directive to sarah-vm for the project it runs CI on, but sarah-vm might be directive to ci-server for a different project where sarah-vm is the expert. Authority is always contextual.

**Scoped authority rules:**

Each peer has an ordered list of `authority_rules`. Each rule has a `scope` predicate (same syntax as `item_filter`) and authority levels for that scope. Rules are evaluated top-to-bottom; the first matching rule wins. A `"*"` wildcard scope serves as the default fallback. **If no rule matches (e.g., no `*` fallback is configured), the default authority is `read_only` for both `from_human` and `from_agent`.** This ensures no unintended obligations from misconfigured rules.

**Validation:** `htrac validate` checks `authority_rules` ordering for each peer. If a rule's scope is strictly broader than a subsequent rule's scope (e.g., `"*"` preceding `"tags:critical"`), validation fails with an error identifying the shadowed rule. This prevents silent misconfiguration where specific rules never match. Rules must be ordered from most-specific to least-specific (same convention as firewall rules).

```yaml
federation:
  peers:
    - id: "abc...xyz.onion"
      name: "ci-server"
      human: "jake"
      authority_rules:
        - scope: "tags:hypergumbo"         # CI failures on this project block us
          from_human: directive
          from_agent: directive
        - scope: "tags:sarahproject"       # their items about this project are FYI
          from_human: advisory
          from_agent: read_only
        - scope: "*"                       # everything else
          from_human: advisory
          from_agent: read_only
```

And on ci-server's side, the reverse might be true:

```yaml
federation:
  peers:
    - id: "def...uvw.onion"
      name: "sarah-vm"
      human: "sarah"
      authority_rules:
        - scope: "tags:sarahproject"       # sarah is the expert on this project
          from_human: directive
          from_agent: directive
        - scope: "tags:hypergumbo"         # sarah's hypergumbo items are FYI to us
          from_human: advisory
          from_agent: read_only
        - scope: "*"
          from_human: advisory
          from_agent: read_only
```

This is the same relationship, but neither side is globally subordinate to the other. Authority flows from context, not from identity.

**Invariant rules (not overridable by scope):**

| Rule | Scope | Override? |
|------|-------|-----------|
| An agent always listens to its own human | All items | No — hardcoded, non-negotiable. This is what the YubiKey protects (ADR-0019). |
| An agent is never directive over any human | All items | No — agents cannot create obligations for humans. |

Everything else is configurable per-scope.

**Authority determines how incoming items interact with `ready` and `count-todos`:**

| Authority | `ready` | `count-todos` | Local agent can update? |
|-----------|---------|---------------|------------------------|
| `directive` | Included (if assigned to this node or unassigned) | Counts as blocking | Yes — peer's items are writable |
| `advisory` | Excluded | Does not block | No — items are read-only locally |
| `read_only` | Excluded | Does not block | No — items are displayed with `[remote]` tag |

The `count-todos` / `ready` pipeline becomes:

```
For each compiled item visible to this node:
  if item.origin == local:
    apply normal blocking/ready rules
  if item.origin == peer:
    if item.assignee does not contain this node AND item.assignee is not empty:
      skip — this item is someone else's problem
    identify the directing actor:
      if this node is in item.assignment_source:
        directing_actor = item.assignment_source[this_node]
        # {"by": "human"|"agent", "actor": "<name>", "at": "<timestamp>"}
      else:
        # item is relevant via item_filter, not explicit assignment
        directing_actor = {"by": item.effective_actor_role, "actor": item.effective_actor}
    resolve authority:
      1. match item against peer's authority_rules (first matching scope wins;
         if no rule matches, default to read_only)
      2. within the matched rule, select from_human or from_agent based on
         directing_actor["by"]
    if resolved authority == directive:
      apply normal blocking/ready rules
    if resolved authority in (advisory, read_only):
      never block, show in list only
```

**The directing actor, not the last writer:** Authority is resolved against whoever directed work to this node — not whoever last touched any field on the item. The directing actor is looked up from `assignment_source` — the structured record of who added this node to the assignee set. If sarah (human) creates an item and sarah_agent later assigns it to lab-vm-1, the directing actor is sarah_agent (recorded in `assignment_source["lab-vm-1"]`). The `from_agent` authority level applies, regardless of who created the item or who last updated its description. For items relevant only via `item_filter` (no explicit assignment), the directing actor falls back to `effective_actor` — the resolved name of whoever last modified the item.

**Being human does not grant automatic directive authority over someone else's agent.** Sarah's authority over lab-vm-1 depends on the scope — if she is the expert in the relevant domain (her "element"), `from_human` might be configured as `directive`. If she is not, it might be `advisory` or `read_only`. Only the node's own human (e.g., jgstern for jgstern_agent) is unconditionally directive — this is the hardcoded invariant the YubiKey protects.

**Item filter** (optional, for `different_project` peers) restricts which items are synced. Without a filter, all items in the synced tiers are exchanged. With a filter, only items matching the predicate are accepted. Supported predicates:
- `tags:<tag>` — item has the specified tag
- `kind:<kind>` — item is of the specified kind
- `status:<status>` — item has the specified status
- Predicates can be combined with `,` (AND)

**v1 limitation:** `item_filter` supports AND-only predicates (comma-separated). Negation (`NOT`) and disjunction (`OR`) are deferred to a future version. The syntax is designed for forward-compatible extension (e.g., `!tags:x` for negation, `|` for OR) without breaking existing configs.

### `@` mentions in discussion threads

Discussion entries already have `by` and `actor` fields. `@` mentions are a convention in the message text — not a new structured field — parsed by the TUI, web UI, and CLI tooling.

**Registered name set:** The set of names eligible for `@` mention highlighting is: the local `node.human`, `node.agent`, and all `name` and `human` values from `federation.peers`. If `node:` config has not been set up (no setup wizard run), the registered set is empty and no `@` mentions are highlighted — graceful degradation. Names are matched case-insensitively.

**Syntax:** `@name` in a discussion message, where `name` is any registered human name, agent name, or node name in the federation. Examples:

```bash
htrac discuss INV-foo "@lab-vm-1 please investigate the parser edge case"
htrac discuss INV-foo "@sarah FYI this might affect your fork too"
htrac discuss INV-foo "@ci-server what was the last failure on this?"
```

**The mention is stored as plain text** in the `discuss` op's `message` field. The ops log does not change — no new op type, no structured mention field. This keeps the data model simple and the ops log human-readable.

**Tooling parses mentions for:**

1. **Display.** TUI and web UI highlight `@name` in discussion text (bold, colored, or linked).

2. **Notification via `check-messages`.** Currently `check-messages` uses a heuristic: "last discussion entry is `by: human`" means the agent has an unread message. With named identities and `@` mentions, the heuristic extends:

    ```
    Unread for agent lab-vm-1:
      INV-foo: last message from jake (own human) — must respond
      WI-bar: @lab-vm-1 mention from sarah — advisory, respond if relevant
      WI-baz: last message from ci-server (directive peer) — should respond

    Unread for human jake:
      INV-foo: last message from lab-vm-1 (own agent) — review response
      WI-qux: @jake mention from sarah — read and optionally reply
    ```

    The unread heuristic becomes: "last discussion entry is from someone other than me, OR I'm `@`-mentioned in a recent entry I haven't replied to."

3. **Federation sync notification.** When a mention of a remote peer is detected in a newly synced discussion entry, the local node can flag it for priority delivery on the next sync cycle. This is a hint, not a guarantee — if the peer is offline, the mention waits.

**Mentioning across authority levels:**

An `@` mention does not override authority. If `sarah` mentions `@lab-vm-1` in a discussion entry, and sarah's authority over lab-vm-1 is `advisory`, the mention surfaces in `check-messages` but does not make the item blocking. The agent sees it, can respond, but is not obligated to act on it. Only `directive` authority creates obligations.

The one exception: **an `@` mention from the node's own human always surfaces as high-priority in `check-messages`**, regardless of which item or tier it appears in. The human-agent bond is unconditional.

### Tier-based sync boundaries

The three-tier model maps directly to federation visibility:

| Tier | Sync behavior |
|------|--------------|
| **Canonical** | Syncs to all peers by default. This is the "shared with upstream" tier. |
| **Workspace** | Syncs only to peers explicitly listed in `sync_tiers: [workspace]`. This is fork-local collaboration. |
| **Stealth** | Never syncs. Never leaves the node. This is the compartmentalization tier. |

A node can have different trust relationships with different peers — peer A gets canonical only, peer B gets canonical + workspace. Stealth is always local.

### Lock and freeze propagation

**Lock enforcement is origin-side only.** Locks are enforced at the origin node on every write request — both local and remote. When a peer's compiled-view feed includes `locked_fields`, the receiving node uses this for **display only** (showing which fields are locked in the TUI/web UI). The receiver does not locally enforce remote locks. Instead, it sends write requests to the origin, and the origin rejects writes that violate locks. This is consistent with the write-at-origin model: all validation happens at the origin, and the receiver trusts the origin's decision.

The same applies to freeze, discuss_clear, and other human-authority ops. The `by: human` / `by: agent` distinction is still determined by `os.getuid()` at the originating node, but the `actor` field now carries the configured name (e.g., `"jake"` or `"lab-vm-1"`) rather than the OS username. The compiled-view feed includes lock/freeze state so that receivers can display it, but enforcement is always at the origin.

### Partition recovery

When a node has been offline and reconnects:

1. **Reads recover immediately.** The reconnecting node requests a full compiled-view snapshot from each peer. Its cached remote items are replaced with current state. No vector clocks, no delta computation — just a fresh snapshot.
2. **Queued writes are delivered with staleness checks.** Any write requests that were queued while the origin was unreachable are sent in order. Each carries a `based_on_version` field. The origin validates each write — same authority checks, same lock enforcement — and additionally rejects writes whose `based_on_version` is behind the item's current `item_version` (stale write). Rejected writes surface in `check-messages` for human review.
3. **Content-hash collisions are detected.** If two nodes independently created items with the same content-hash ID while disconnected, the collision surfaces when one node tries to write to the other's item. The origin rejects the write with a conflict error. The human resolves by marking one as `duplicate_of` the other.

**Consistency model:** Each node is authoritative for its own items. Remote items are cached projections of the origin's state. There is no "global consistency" — each node's local items are always consistent (append-only ops, deterministic compile), and remote items are as fresh as the last sync. This is closer to a client-server model per item than to a peer-to-peer convergence model.

### Stop hook implications

`count-todos` currently counts blocking items across configured tiers. In a federated setup, the count is filtered by both **authority** and **assignee**:

- Items from `directive` peers that are assigned to this node (or unassigned) count as blocking.
- Items from `advisory` or `read_only` peers never block.
- Items assigned to a different node never block, regardless of authority.

This enables precise cross-node coordination: a human can create a `todo_hard` item on Node A, assign it to Node B, and Node B's agent is blocked from stopping until it's done. Meanwhile Node C (also syncing with Node A) is unaffected because the item isn't assigned to it.

The existing stop hook logic (`count-todos`, `hash-todos`) does not need structural changes — it already operates on compiled local state. The new filtering is a predicate applied before counting, not a change to the counting mechanism.

## Consequences

### Benefits

- **Unified governance across nodes.** One human manages multiple agents from one interface (phone via ADR-0019, or TUI on any node).
- **No single point of failure.** Any node can operate independently during partitions. No central server.
- **Existing data model works.** `compile_ops()`, content-hash IDs, lock enforcement — all work unchanged. Federation adds a sync layer on top; it does not modify the op-log or compile machinery.
- **Tier-based compartmentalization.** Stealth items are provably node-local. Workspace items sync only to trusted peers. Canonical items are globally visible.
- **Lock/freeze enforcement is natural.** Locks are enforced at the origin on every write — both local and remote. A remote human with directive authority can lock a field via the write API, and the origin enforces it. No special replication logic needed.
- **Assignee enables directed work.** Items can be routed to specific named agents. Combined with authority, this gives precise control: "lab-vm-1 must fix this; sarah-vm should know about it; upstream-lib doesn't need to see it."
- **Authority is receiver-decided and non-totalizing.** No negotiation protocol. Each node controls what blocks its own agent. Authority is scoped — the same two nodes can have reversed authority in different contexts. No peer is ever globally subordinate. A rogue peer claiming directive authority has no effect unless the receiver configures it that way.
- **Named identities make discussion social.** `@` mentions surface the right messages to the right participants. `check-messages` becomes identity-aware — "jake has an unread from lab-vm-1" rather than "a human message is pending."
- **Human-agent bond is non-negotiable.** The one hardcoded rule: an agent always listens to its own human. No config can override this. This is the invariant the YubiKey protects (ADR-0019).

### Costs

- **Writes require connectivity to origin.** You cannot modify a remote item while the origin node is offline. Queued writes are delivered on reconnect with `based_on_version` staleness checks — stale writes are rejected rather than silently applied. There is no offline merge. This is the fundamental tradeoff of compiled-view sync — simplicity and correctness at the cost of write availability.
- **No full history on remote nodes.** Peers see current compiled state and discussion threads, but not the op-level history (status transitions, field changes, who-changed-what-when). That history lives only on the origin. If you need to audit a remote item's history, you must query the origin node.
- **Peer management is manual.** Adding/removing peers requires the human's phone app (ADR-0019). There is no automatic peer discovery or key exchange ceremony.
- **Screenshots (ADR-0020) in federation.** Screenshots are tracked by git (per ADR-0020), so they are available to peers that share the same git repository. For compiled-view-only federation (no shared git), screenshots referenced in discussion threads would need to be served via the federation API or displayed as "[screenshot on remote node — connect to view]".
- **Testing distributed systems is hard.** Unit-testing the sync protocol requires simulating multi-node scenarios with partitions, clock skew, and concurrent writes. Property-based testing (Hypothesis) can help but the test harness is nontrivial.
- **Assignee (with `assignment_source`) adds fields to the data model.** Requires changes to `CompiledItem`, `_SET_VALUED_FIELDS`, `_UPDATABLE_FIELDS`, `_LOCKABLE_FIELDS`, `compile_ops()` (to populate `assignment_source`), `cache.py` (SQLite schema: `assignee` and `assignment_source` columns), `validation.py` (assignee value validation), `sync.py` (wire format), the CLI (`--assignee` flag), `ready` filtering, `count-todos` filtering, the TUI display, and `textconv_main()`. This is straightforward but touches 15+ files including tests.
- **Named identity is a migration.** Existing ops have OS usernames in `actor` fields. Old ops are not rewritten (append-only). `compile_ops()` resolves old-style actor names to current names via the `aliases` config, producing an `effective_actor` field on `CompiledItem` and `DiscussionEntry`. This centralizes the migration in `compile_ops()` rather than spreading alias checks across downstream code. The raw `actor` field on ops is preserved.
- **`@` mention parsing adds complexity to display code.** Every surface that renders discussion text (TUI detail panel, web UI, CLI `show` output) must parse and highlight mentions. False positives (e.g., email addresses containing `@`) need heuristic handling — only match against the known set of registered names.
- **Scoped authority adds config complexity.** Each peer has an ordered list of authority rules with scope predicates, each split by from_human and from_agent. This is powerful but harder to reason about than a flat per-peer setting. The human must understand: scope matching (first rule wins), the human/agent split within each rule, and how scopes interact with item_filter. Good defaults and `htrac federation status` (showing effective authority for each synced item) can mitigate this.

### Open questions

- **Deletion visibility.** The `deleted` status is a logical delete — the item still exists on the origin node's op log. Should deleted items be included in the compiled-view feed? If yes, peers see tombstones accumulate. If no, a peer that cached the item before deletion may show stale data until the next full snapshot. Recommendation: include deleted items in the feed with a `deleted: true` flag; let the receiving node decide whether to display or hide them.
- **Rate limiting across nodes.** The existing discussion rate limit (200K tokens/day per item) is per-node. With write-at-origin, the origin can enforce rate limits for all writers — both local and remote. Remote write requests that exceed the limit are rejected with a structured error.
- **Binary artifact sync.** Screenshots (ADR-0020) are tracked by git, so they sync naturally to peers sharing the same repo. For compiled-view-only federation, attachments and media need a separate mechanism. Options: content-addressed blob requests over the control channel (peer requests a blob by hash, origin serves it), or explicit "artifact unavailable" markers in the compiled view that the TUI renders as "[screenshot on remote node — connect to view]".
- **Human name uniqueness across independent federations.** Two humans named "jake" in separate federations that later merge would collide. Namespacing (e.g., `jake@lab-vm-1`) avoids this but is uglier. For v1, treat name collisions as a provisioning error caught by `register-peer`.
- **`@` mention of groups or roles.** Should `@humans` or `@agents` work as group mentions? Useful for "all agents should be aware of this." Adds parsing complexity. Probably defer to v2.
- **Discussion thread branching.** With multiple participants `@`-mentioning each other, discussion threads may become hard to follow. Threaded replies (reply-to a specific entry) would help but add a `reply_to` field to `DiscussOp`. Defer unless discussion threads become unreadable in practice.
- **Cross-human lock conflict.** If jake locks a field on a shared item and sarah also wants to lock the same field with a different intent, there is no conflict resolution — both lock ops coexist and the field is locked for agents regardless of which human locked it. Unlocking requires any human with directive authority. This is probably fine but could surprise users.

### Relationship to other ADRs

- **ADR-0013 (Structured Tracker):** Federation extends the op-log model from single-repo to multi-node. All ADR-0013 invariants (append-only, LWW-fold, content-hash IDs, lock enforcement) are preserved.
- **ADR-0019 (Remote Access Transport):** Provides the authenticated, encrypted transport over which federation sync operates. Tor onion addresses double as peer identities.
- **ADR-0020 (TUI Screenshot Annotation):** Screenshots are tracked by git (per ADR-0020). Peers sharing the same repo receive them via git. Compiled-view-only federation may need to serve screenshots via the federation API or mark them as unavailable.
