<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0015: Dataflow Access Modes on Edges

Date: 2026-03-15
Updated: 2026-04-11
Status: Accepted

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

A single module (`dataflow.py`, ~380 lines) provides:

1. **YAML loader**: reads `dataflow/*.yaml` files, builds a per-language lookup table of node types to access-mode rules.

2. **`annotate_dataflow(edges, tree, source, language) -> List[Edge]`**: takes a batch of edges produced by a language analyzer, the parsed AST tree, and the source bytes. For each edge, locates the AST node at the edge's line, looks up the node type in the language's dataflow config, and stamps `access_mode` into `meta`. Returns the same edges with annotations added. Edges that already have `access_mode` set (by a linker — see below) are skipped.

3. **`scan_library_patterns(file_content, language) -> List[DataflowSite]`**: matches library-specific patterns (the `library_patterns` section) against source text via regex, returning structured write/read sites with channels. This is the same approach used by the event sourcing, message queue, and WebSocket linkers — but driven by YAML instead of per-linker Python code.

### 5. Two-tier integration model

Dataflow annotation applies to two distinct populations of edges, with different integration strategies for each:

#### Tier 1: Intra-language edges (automatic)

Edges created by language analyzers (calls, assignments, attribute access) go through the base class orchestrator in `analyze/base.py`. The orchestrator already has the AST tree and source bytes at the point where edges are created. One line is added after `extract_edges_from_file`:

```python
# In analyze/base.py, after line 1768 (existing edge extraction)
edges = self.extract_edges_from_file(
    tree, source, source_file, rel_path,
    analysis.symbol_by_name, global_symbols, run,
    import_aliases, resolver,
)
edges = annotate_dataflow(edges, tree, source, self.lang)  # NEW
all_edges.extend(edges)
```

This is **one integration point** in the base class. Every language analyzer that uses the base class gets dataflow annotation automatically — zero changes to any individual analyzer. The `annotate_dataflow` function matches each edge's line number back to the AST node at that position, looks up the dataflow YAML for the language, and stamps `access_mode` into `meta`.

#### Tier 2: Cross-language edges (explicit)

Edges created by linkers (IPC, pub/sub, wasm bridges, event sourcing) have no AST context — linkers work from symbol metadata and regex scans, not tree-sitter nodes. Automatic classification is impossible here because the dataflow semantics come from the pattern match, not the AST structure. Only the linker knows that `emitter.emit('x')` is a write and `emitter.on('x', handler)` is a read.

Linkers set `access_mode` and `channel` explicitly at edge creation time, using the same `meta` dict they already use for domain-specific metadata:

```python
# In event_sourcing.py (linker knows the semantics)
Edge.create(
    src=publisher_id, dst=subscriber_id,
    edge_type="event_publishes",
    meta={
        "access_mode": "write",
        "dest_access_mode": "read",
        "channel": event_name,
    },
)
```

This is not new work — linkers already set `meta` fields like `channel`, `wasm_export`, and `package_name`. Adding `access_mode` is one additional key.

#### Precedence rule

When both tiers could apply (e.g., a language analyzer creates a `calls` edge to `emitter.emit`, and then the event sourcing linker creates a separate `event_publishes` edge from the same call site), **explicit annotations take precedence over automatic ones**. The `annotate_dataflow` function skips any edge that already has `access_mode` set. This prevents double-counting: each call site gets at most one dataflow annotation, from whichever producer knows the semantics best.

#### Why two tiers, not one

A single-tier design was considered and rejected:

- **All-automatic** (post-hoc pass over all edges): doesn't work for cross-language linker edges because there's no AST context. The event sourcing linker knows `emit` is a write, but an AST-level scan sees only a method call.

- **All-explicit** (every edge producer calls `classify_access` manually): works correctly but requires touching every `Edge.create` call site across every analyzer. Error-prone, easy to forget, and creates N integration points instead of one.

The two-tier design matches the natural boundary: language analyzers have AST context (automatic), linkers have domain knowledge but no AST (explicit).

#### Coverage: analyzers outside the base class

104 of 118 language analyzers subclass `TreeSitterAnalyzer` and get automatic annotation. 11 do not (3 added since the original count of 114):

| Analyzer | Approach | Dataflow action needed |
|----------|----------|----------------------|
| **py.py** | Python `ast` module | Uses `annotate_dataflow_ast()` — classifies `ast.Assign` as write, `ast.AugAssign` as mutate, `ast.Delete` as delete, `ast.Return`/`ast.Yield` as read. |
| **jupyter.py** | `ast` + JSON | Shares py.py's `ast`-based classifier after cell extraction. |
| **html.py** | Regex | No action needed — only creates `script_src` edges (file-level references, no read/write semantics). |
| **manifest_targets.py** | Regex | No action needed — build target declarations only. |
| **play_routes.py** | Regex | No action needed — Play Framework route declarations only. |
| **handlebars.py, blade.py** | Regex | Low priority — template partial/directive references. Could eventually classify `@yield` as read and `@section` as write to model template inheritance dataflow. |
| **just.py, qml.py, gnuplot.py, mermaid.py** | Regex | No action needed — primarily declaration extraction with minimal call edges. |

### 6. Slice integration

The slicer gains an optional `--dataflow` flag:

- **Without flag** (default): BFS traversal follows all edges, as today. No behavior change.
- **With `--dataflow`**: BFS only follows edges where a write/mutate at the source connects to a read at the destination. This produces tighter slices that represent actual data dependencies rather than structural reachability.

Implementation: ~10 lines added to the BFS loop in `slice.py` to check `edge.meta.get("access_mode")` before traversal.

#### Forward-slice admission rule (WI-saful option 1)

The forward-dataflow BFS admission rule is:

1. **Writer source**: admit edges where `access_mode in {write, mutate}`. This is the primary chain — "follow what this symbol writes to, transitively".
2. **One-hop downstream read**: after visiting a writer node `W`, admit each of `W`'s outgoing `read` edges as a **terminal** (the edge and destination enter the slice, but the destination is NOT enqueued for further BFS). This captures "data flows OUT: write site → downstream reads of what was written" without exploding the slice into unbounded reader chains.
3. **Graceful degradation**: edges with no `access_mode` annotation are admitted (graceful fallback for incomplete linker coverage).

This is the "option 1" shipped in WI-saful. Rejected on evidence (see §6.1 below): **option 2** (symmetric `dst_mode` OR-check) and **option 3** (writer-chain BFS state).

#### 6.1 Option 2 (dst_mode OR-check): evaluated and deferred (WI-hukoh)

WI-hukoh-bakob-gidij-nibag-puvaz-fadil-kizor-kitan proposed extending the admission rule to "admit if `src_mode in {write, mutate}` OR `dst_mode in {read, mutate}`". The rationale was schema-correctness: the `dest_access_mode` field already exists on the `Edge` schema (see §1), and forward slicing should be symmetric with respect to it. WI-hukoh Phase A added `SliceResult.admission_stats` telemetry with a predictive counter `would_admit_dst_reader` that measures exactly how many edges option 2 would ADDITIONALLY admit beyond option 1's rules, **without implementing the behavior change**.

On 4 sampled repos (alertmanager, buildkit, apollo-server, wasmtime, ~188k total edges, ~55k annotated), the result is unambiguous:

| repo         | total edges | annotated | `write→-` | `mutate→-` | `read→-` | `delete→-` | `write→read` | other |
|--------------|-------------|-----------|-----------|------------|----------|-----------|--------------|-------|
| alertmanager | 12,430      | 4,809     | 266       | 0          | 4,447    | 0         | 96           | 0     |
| buildkit     | 42,792      | 21,683    | 826       | 0          | 20,186   | 0         | 671          | 0     |
| apollo-server| 7,710       | 320       | 10        | 0          | 310      | 0         | 0            | 0     |
| wasmtime     | 125,152     | ~25,405   | 2,163     | 498        | 22,376   | 46        | 322          | 0     |

**Critical pattern**: every single edge with `dest_access_mode` populated ALSO has `access_mode=write`. There are ZERO edges where src is `read`, `mutate`, `delete`, or `-` with dst as `read` or `mutate`. All 16 polyglot linkers that populate `dest_access_mode` always ALSO populate `access_mode=write` at the same call site. Empirically, option 2's unique contribution over option 1 is **zero edges on any tested repo**.

**Decision**: defer option 2 indefinitely. Ship option 1 (already in place) as the canonical forward-slice admission rule. Do NOT add the `dst_mode` OR-check as dormant future-proofing — speculative complexity that must be maintained, reviewed, and tested carries non-zero cost for zero measured benefit, and the maintenance risks documented in the WI-hukoh thread (cognitive overhead of multi-branch admission rule, state-space doubling for option 3, deprecation cliff uncertainty) remain real even for dormant code.

**Re-evaluation trigger**: the `SliceResult.admission_stats.would_admit_dst_reader` telemetry counter stays in place as a live monitor. Re-evaluate this decision **when** INV-dihos Phase 5 (cross-linker signature-registry integration) or any future linker begins populating `dest_access_mode` WITHOUT `access_mode=write` on ≥1% of annotated edges on any repo. At that point, re-run the Phase A telemetry: if `would_admit_dst_reader > 0`, option 2 becomes justified and this ADR section should be revised to match.

**Option 3 (writer-chain BFS state)** was not separately evaluated on real data because option 2 had to be proven insufficient first — which it was not, because no edges in current behavior maps would exercise the dst-mode path at all. Option 3 is also deferred. If option 2 ever becomes active and multi-hop `write→passthrough→read` chains turn out to be a meaningful missed-case, option 3 can be evaluated then.

**INV-forim prerequisite**: Phase A baseline data collection exposed INV-forim, a structural bug where 4 cross-language linkers (`event_sourcing`, `ipc`, `websocket`, `message_queue`) silently destroyed `access_mode`/`dest_access_mode` annotations by reassigning `edge.meta = {...}` after `Edge.create` had merged them. Fixed in PR #2925 before the Phase C decision data was collected — without that fix, the `would_admit_dst_reader` counter was reading zero on every repo for a different (structural) reason, which would have falsely validated the "option 2 is useless" conclusion for the wrong reason.

### 7. Unification of existing linkers

Several existing linkers already detect dataflow patterns with bespoke Python code:

| Linker | Current edge type | Dataflow semantics |
|--------|------------------|-------------------|
| Event sourcing | `event_publishes` / `event_subscribes` | write / read on event channel |
| Message queue | `mq_publishes` / `mq_subscribes` | write / read on topic |
| WebSocket | `websocket_message` | write / read on event name |
| Yjs CRDT | `crdt_publishes` / `crdt_subscribes` | write / read on CRDT key |

With dataflow YAMLs, these patterns could be expressed declaratively in the `library_patterns` section of the relevant language's dataflow YAML — or in dedicated per-library YAMLs (e.g., `dataflow/yjs.yaml`, `dataflow/kafka.yaml`). The existing linkers would remain as-is for backward compatibility, but new pub/sub patterns could be added via YAML without writing Python code.

This also addresses the PlazaFlow team's annotation convention request (`@hg:publishes` / `@hg:subscribes`): annotations become a special case of dataflow patterns where the developer explicitly declares the access mode and channel via comments, parsed by the shared classification machinery.

## Consequences

### Positive

- **Uniform vocabulary**: all edge producers express dataflow semantics using the same four access modes, without inventing ad-hoc edge types.
- **YAML-driven**: new languages and libraries can declare read/write patterns without writing Python analyzer code. Adding Yjs support, for example, is a YAML file — not a new linker.
- **Automatic for intra-language edges**: one line in `base.py` gives every language analyzer dataflow annotation for free. No per-analyzer code changes. No opt-in to forget.
- **Backward compatible**: `access_mode` is optional and carried in the existing `meta` dict. No schema version bump. No existing consumer breaks.
- **No double-counting**: the precedence rule (explicit beats automatic) ensures each edge gets at most one dataflow annotation from whichever producer knows the semantics best.
- **Tighter slices**: `--dataflow` slices follow write-to-read chains, producing smaller and more relevant results.
- **Enables new analyses**: dead write detection (writes with no readers), shared mutable state enumeration (symbols with both writers and readers from different scopes), concurrency hazard candidates (concurrent writers without synchronization).

### Negative

- **Partial coverage for cross-language edges**: linkers must explicitly set `access_mode`. Until all linkers adopt the vocabulary, some cross-language edges lack dataflow annotation. Intra-language edges are covered automatically via the base class.
- **Pattern limitations**: tree-sitter AST patterns can classify direct assignments and simple call arguments, but cannot resolve aliased or indirect writes (e.g., `ref = obj; ref.x = 1` — the write to `obj.x` is invisible at the `ref.x` AST node without alias analysis).
- **YAML maintenance**: each new language needs a dataflow YAML file. Most are small (10-30 lines), but the total count grows with language coverage.

### Risks

- **False precision**: users may trust `--dataflow` slices as complete when they're actually missing edges through unannotated code. Mitigation: clearly label partial coverage in output, warn when unannotated edges are encountered during dataflow slicing.
- **Vocabulary creep**: teams may want custom access modes beyond the four defined here (e.g., "staged write" for transactions, "ownership transfer" for Rust move semantics). Mitigation: keep the core vocabulary small and use `meta` for domain-specific extensions.
- **Precedence edge cases**: a linker and the automatic pass could disagree about access mode for the same symbol (e.g., a method call that the AST classifies as `read` but the linker knows is `write` because of framework semantics). The precedence rule (explicit wins) is correct, but only if the linker creates an edge for that specific call site. If the linker creates a *separate* edge (different `edge_type`) for the same call site, both annotations survive — which is the right behavior, since they represent different relationships (structural call vs. dataflow channel).

## Relationship to other ADRs and work items

- **ADR-0012 (Pass Unification)**: dataflow classification piggybacks on existing analyzer passes. Tier 1 annotation runs inside the base class orchestrator's Pass 2 loop; it is not a separate pass.
- **ADR-0014 (Symbol Identity)**: `channel` fields on edges complement `stable_id` on symbols — together they identify what shared state is being accessed and by whom.
- **PlazaFlow work items**: the Yjs/CRDT linker (WI-zusig), annotation convention (WI-logok), and Tauri event direction (WI-vovaj) all become special cases of dataflow-annotated edges. The annotation convention (`@hg:publishes` / `@hg:subscribes`) maps directly to `access_mode: write` / `access_mode: read` with an explicit `channel`.
- **Test isolation analysis**: the shared mutable state detection enabled by this ADR directly addresses the Textual Pilot test interaction failures observed in hypergumbo's own test suite — module-level globals that are written by one test and read by another can be enumerated by querying for symbols with both `write` and `read` edges from different test scopes.
- **WI-saful**: shipped "option 1" forward-slice admission rule (writer source + one-hop downstream read + graceful degradation). See §6.
- **WI-hukoh**: evaluated "option 2" (dst_mode OR-check) and "option 3" (writer-chain BFS state) on real data; both deferred indefinitely because no edges in any sampled repo would exercise the dst-mode path. See §6.1 for the data and the re-evaluation trigger.
- **INV-forim**: structural bug where 4 cross-language linkers destroyed dataflow annotations via `edge.meta = {...}` reassignment after `Edge.create`. Discovered during WI-hukoh Phase A baseline data collection, fixed in PR #2925. Was a prerequisite for WI-hukoh Phase C: without the fix, the telemetry was falsely reporting zero for a different (structural) reason.
- **INV-dihos Phase 5**: cross-linker signature-registry integration. If this ever begins populating `dest_access_mode` on edges without `access_mode=write`, the WI-hukoh decision to skip option 2 must be revisited. The `SliceResult.admission_stats.would_admit_dst_reader` counter is the trigger.
