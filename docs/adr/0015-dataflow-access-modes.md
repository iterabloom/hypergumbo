<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0015: Dataflow Access Modes on Edges

Date: 2026-03-15
Status: Proposed

## Context

Hypergumbo's graph currently represents **structural relationships** — "function A calls function B", "class C inherits from D", "file E imports module F". Every edge is directional and typed (`calls`, `imports`, `inherits`, `ipc_calls`, etc.), but no edge carries information about **how** the connected symbols interact with shared state.

This matters because many of the most important couplings in real codebases are **data-mediated**, not call-mediated:

1. **CRDT observation** (PlazaFlow/Yjs): function A writes `yMap.set('cursor', pos)`, function B reads via `yMap.observeDeep(callback)`. There is no call edge between A and B. The coupling flows through shared mutable state.

2. **Pub/sub and event systems**: `emitter.emit('event')` and `emitter.on('event', handler)` are writes and reads to a named channel. The existing event sourcing linker creates edges between these, but doesn't distinguish the publisher (writer) from the subscriber (reader).

3. **Test isolation failures**: test A writes to a module-level global, test B reads it expecting clean state. The call graph shows both tests reach the global, but can't distinguish the polluter (writer) from the victim (reader).

4. **Slice precision**: a forward slice from symbol X currently includes everything reachable via any edge. With access modes, a forward slice from a *write* to X could follow only write-to-read chains, excluding code that also writes to X independently.

5. **Framework state**: middleware registration (write) vs middleware resolution (read), DI container binding (write) vs injection (read), config loading (write) vs config access (read).

### What exists today

The `Edge` dataclass already has a `meta: Optional[Dict[str, Any]]` field used by linkers for domain-specific metadata (`channel`, `wasm_export`, `package_name`). Some linkers implicitly encode dataflow direction — the event sourcing linker creates `event_publishes` and `event_subscribes` edge types — but this is ad-hoc and not queryable in a uniform way.

## Decision

### 1. Access mode vocabulary

Define four access modes as a controlled vocabulary on edges:

| Mode | Meaning | Example |
|------|---------|---------|
| `read` | Observe value without changing it | `x = config.get('key')`, `map.observe()` |
| `write` | Replace value entirely | `x = 42`, `map.set('key', value)` |
| `mutate` | Modify value in place (implies read + write) | `list.append(item)`, `counter += 1` |
| `delete` | Remove the binding / key / entry | `del x`, `map.delete('key')`, `DROP TABLE` |

Edges where access mode is not applicable (e.g., `inherits`, `imports`, `implements`) carry no access mode (`None`).

`mutate` is distinct from `write` because a mutate depends on the prior value (ordering between two mutators matters), while two independent writes do not (last writer wins). `delete` is distinct because it can cause subsequent reads to fail (KeyError, null reference) in ways that writes cannot.

### 2. Channel field

A `channel` field identifies the shared state being accessed. This is the join key for connecting writers to readers:

- For CRDT operations: the document/map key (`"awareness.cursor"`, `"yMap.items"`)
- For pub/sub: the topic or event name (`"user.created"`, `"order.shipped"`)
- For globals: the qualified variable name (`"sync_log._log_file_handle"`)
- For message queues: the queue/topic name (`"orders-topic"`)

When the channel is a literal string, confidence is high. When inferred from a variable, confidence is lower (matching the existing pattern in the event sourcing and message queue linkers).

### 3. Representation

Use the existing `meta` dict on `Edge`. No schema change required:

```python
Edge.create(
    src=writer_id,
    dst=reader_id,
    edge_type="data_flows_to",
    line=42,
    meta={
        "access_mode": "write",       # at source site
        "dest_access_mode": "read",   # at destination site
        "channel": "awareness.cursor",
    },
)
```

For existing edge types that carry implicit dataflow semantics, producers annotate them:

```python
# Event sourcing linker (already creates event_publishes edges)
Edge.create(
    ...,
    edge_type="event_publishes",
    meta={
        "access_mode": "write",
        "dest_access_mode": "read",
        "channel": event_name,
    },
)
```

The `Edge.create` factory gains optional `access_mode`, `dest_access_mode`, and `channel` kwargs that flow into `meta` as a convenience — one-line additions to the factory.

### 4. YAML-driven pattern classification

Rather than adding per-language bespoke code to classify reads and writes, define the patterns declaratively in YAML. The tree-sitter AST node types for assignments, calls, deletions, and attribute access are structurally similar across languages — they differ in node type names but not in shape.

#### Dataflow YAML format

```yaml
# dataflow/python.yaml
language: python

assignments:
  - node_type: assignment
    write: left
    read: right
  - node_type: augmented_assignment      # x += 1
    mutate: left
    read: right

calls:
  - node_type: call
    read: arguments                       # default: args are reads

deletions:
  - node_type: delete_statement
    delete: argument

# Library-specific patterns (optional, higher-level)
library_patterns:
  - match: "$map.set($key, $value)"
    access_mode: write
    channel_from: "$key"
  - match: "$map.observe($callback)"
    access_mode: read
    channel: "*"                          # observes all keys
  - match: "awareness.setLocalStateField($key, $value)"
    access_mode: write
    channel_from: "awareness.$key"
```

```yaml
# dataflow/rust.yaml
language: rust

assignments:
  - node_type: let_declaration
    write: pattern
    read: value
  - node_type: assignment_expression
    write: left
    read: right

borrows:
  - node_type: reference_expression
    mutate_if: mutable                    # &mut x
    read_if: immutable                    # &x
```

```yaml
# dataflow/javascript.yaml
language: javascript

assignments:
  - node_type: assignment_expression
    write: left
    read: right
  - node_type: augmented_assignment_expression
    mutate: left
    read: right
  - node_type: variable_declarator
    write: name
    read: value

deletions:
  - node_type: delete_expression          # delete obj.key
    delete: argument
```

#### Shared classification machinery

A single module (`dataflow.py`, estimated ~200-300 lines) provides:

1. **YAML loader**: reads `dataflow/*.yaml` files, builds a per-language lookup table of node types to access-mode rules.

2. **`classify_access(edge, ast_node, language) -> str | None`**: called by any edge producer. Looks up the AST node's type in the language's dataflow config, determines whether the edge's source/destination is on the read side or write side of the pattern, returns the access mode. Language analyzers call this when creating edges — one additional line at each `Edge.create` call site.

3. **`scan_library_patterns(file_content, language) -> List[DataflowSite]`**: matches library-specific patterns (the `library_patterns` section) against source text via regex, returning structured write/read sites with channels. This is the same approach used by the event sourcing, message queue, and WebSocket linkers — but driven by YAML instead of per-linker Python code.

#### Integration with existing producers

Each edge producer opts in by passing AST context to `classify_access`:

```python
# In a language analyzer's edge-creation code (e.g., rust.py)
from hypergumbo_core.dataflow import classify_access

mode = classify_access(call_node, language="rust")
edge = Edge.create(
    src=caller_id, dst=callee_id,
    edge_type="calls", line=line,
    access_mode=mode,
)
```

Existing edges that don't call `classify_access` continue to work — their `access_mode` is simply `None`. Adoption is incremental per-analyzer.

### 5. Slice integration

The slicer gains an optional `--dataflow` flag:

- **Without flag** (default): BFS traversal follows all edges, as today. No behavior change.
- **With `--dataflow`**: BFS only follows edges where a write/mutate at the source connects to a read at the destination. This produces tighter slices that represent actual data dependencies rather than structural reachability.

Implementation: ~10 lines added to the BFS loop in `slice.py` to check `edge.meta.get("access_mode")` before traversal.

### 6. Unification of existing linkers

Several existing linkers already detect dataflow patterns with bespoke Python code:

| Linker | Current edge type | Dataflow semantics |
|--------|------------------|-------------------|
| Event sourcing | `event_publishes` / `event_subscribes` | write / read on event channel |
| Message queue | `mq_publishes` / `mq_subscribes` | write / read on topic |
| WebSocket | `websocket_message` | write / read on event name |
| Yjs (planned) | `crdt_publishes` / `crdt_subscribes` | write / read on CRDT key |

With dataflow YAMLs, these patterns could be expressed declaratively in the `library_patterns` section of the relevant language's dataflow YAML — or in dedicated per-library YAMLs (e.g., `dataflow/yjs.yaml`, `dataflow/kafka.yaml`). The existing linkers would remain as-is for backward compatibility, but new pub/sub patterns could be added via YAML without writing Python code.

This also addresses the PlazaFlow team's annotation convention request (`@hg:publishes` / `@hg:subscribes`): annotations become a special case of dataflow patterns where the developer explicitly declares the access mode and channel via comments, parsed by the shared classification machinery.

## Consequences

### Positive

- **Uniform vocabulary**: all edge producers can express dataflow semantics without inventing ad-hoc edge types.
- **YAML-driven**: new languages and libraries can declare read/write patterns without writing Python analyzer code. Adding Yjs support, for example, is a YAML file — not a new linker.
- **Backward compatible**: `access_mode` is optional and carried in the existing `meta` dict. No schema version bump. No existing consumer breaks.
- **Incremental adoption**: analyzers opt in per-language. The Python analyzer could classify reads/writes first; others follow as needed.
- **Tighter slices**: `--dataflow` slices follow write-to-read chains, producing smaller and more relevant results.
- **Enables new analyses**: dead write detection (writes with no readers), shared mutable state enumeration (symbols with both writers and readers from different scopes), concurrency hazard candidates (concurrent writers without synchronization).

### Negative

- **Partial coverage**: until all analyzers adopt `classify_access`, the graph has mixed annotated and unannotated edges. Dataflow slices may miss paths through unannotated edges.
- **Pattern limitations**: tree-sitter AST patterns can classify direct assignments and simple call arguments, but cannot resolve aliased or indirect writes (e.g., `ref = obj; ref.x = 1` — the write to `obj.x` is invisible at the `ref.x` AST node without alias analysis).
- **YAML maintenance**: each new language needs a dataflow YAML file. Most are small (10-30 lines), but the total count grows with language coverage.

### Risks

- **False precision**: users may trust `--dataflow` slices as complete when they're actually missing edges through unannotated code. Mitigation: clearly label partial coverage in output, warn when unannotated edges are encountered during dataflow slicing.
- **Vocabulary creep**: teams may want custom access modes beyond the four defined here (e.g., "staged write" for transactions, "ownership transfer" for Rust move semantics). Mitigation: keep the core vocabulary small and use `meta` for domain-specific extensions.

## Relationship to other ADRs and work items

- **ADR-0012 (Pass Unification)**: dataflow classification is a pass annotation, not a separate pass. It piggybacks on existing analyzer passes.
- **ADR-0014 (Symbol Identity)**: `channel` fields on edges complement `stable_id` on symbols — together they identify what shared state is being accessed and by whom.
- **PlazaFlow work items**: the Yjs/CRDT linker (WI-zusig), annotation convention (WI-logok), and Tauri event direction (WI-vovaj) all become special cases of dataflow-annotated edges. The annotation convention (`@hg:publishes` / `@hg:subscribes`) maps directly to `access_mode: write` / `access_mode: read` with an explicit `channel`.
