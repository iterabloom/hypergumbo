<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Self-Analysis Dogfooding Playbook

Use hypergumbo on itself to find bugs, validate Python analysis quality, and build intuition about the tool's output before modifying the codebase.

## When to Run

- **Before refactoring shared modules** — understand cross-package dependencies, high-centrality symbols, and call-graph structure before touching code that multiple packages import.
- **After adding or modifying analyzers** — verify the new analyzer produces expected edges, entrypoints, and framework patterns by running against hypergumbo itself (which uses many Python patterns: decorators, dataclasses, abstract classes, dynamic dispatch, entry points).
- **After changing linkers or IR** — run `hypergumbo run .` and diff the behavior map against a prior baseline to catch regressions in edge count, orphan rate, or confidence distribution.
- **When investigating bakeoff signals** — use `slice`, `explain`, or `io-boundaries` on hypergumbo's own code to trace how data flows through the analysis pipeline and understand what the tool sees vs. what it should see.
- **Periodically** — hypergumbo is a non-trivial Python project (~130K non-test LOC). Running it on itself is one of the cheapest smoke tests available.

## What Makes Hypergumbo a Good Self-Test Target

Hypergumbo's own codebase exercises many patterns that its Python analyzer should handle well:

- **Abstract base classes** (`analyze/base.py`) with concrete implementations per language
- **Dataclasses** (`ir.py`, `datamodels.py`) with complex field types
- **Dynamic dispatch** (analyzer registry, linker registration)
- **Entry points** (CLI via `cli.py`, `__main__.py`, setuptools console_scripts)
- **Decorators** (click commands, property decorators)
- **Cross-package imports** (core imports from lang-common, lang packages import from core)
- **IO boundaries** (file reads/writes in `io_boundary.py`, `discovery.py`, `paths.py`; subprocess calls in `build_grammars.py`; HTTP in tracker sync)
- **Framework patterns** (click CLI framework, pytest fixtures in tests)
- **Supply chain tiers** (first-party packages, vendored dependencies, third-party tree-sitter grammars)

If hypergumbo can't analyze itself well, it has a problem.

## Step 1: Generate Baseline Artifacts

Run hypergumbo on its own repo and save the outputs for inspection:

```bash
# Full behavior map
hypergumbo run . --out /tmp/hg-self-analysis.json

# Sketch (what an LLM would see)
hypergumbo . -t 8000 > /tmp/hg-self-sketch.md

# IO boundaries
hypergumbo io-boundaries --json > /tmp/hg-self-io.json

# Routes (should find click CLI commands)
hypergumbo routes > /tmp/hg-self-routes.txt

# Key symbols
hypergumbo symbols --limit 30 -x > /tmp/hg-self-symbols.txt
```

## Step 2: Inspect and Validate

Check the outputs against what you know about hypergumbo's architecture:

### Behavior map sanity checks

```bash
# How many nodes and edges?
python3 -c "
import json
with open('/tmp/hg-self-analysis.json') as f:
    data = json.load(f)
nodes = data.get('nodes', [])
edges = data.get('edges', [])
print(f'Nodes: {len(nodes)}')
print(f'Edges: {len(edges)}')

# Orphan rate (nodes with no edges)
node_ids = {n['id'] for n in nodes}
connected = set()
for e in edges:
    connected.add(e.get('src'))
    connected.add(e.get('dst'))
orphans = node_ids - connected
print(f'Orphans: {len(orphans)} / {len(nodes)} ({100*len(orphans)/max(len(nodes),1):.0f}%)')

# Edge type breakdown (on edges the field is 'type', NOT 'kind')
from collections import Counter
edge_types = Counter(e.get('type', 'MISSING') for e in edges)
print()
print('Edge types:')
for t, count in edge_types.most_common(15):
    print(f'  {t}: {count}')

# Node language breakdown (on nodes the field is 'language', NOT 'lang')
node_langs = Counter(n.get('language', 'MISSING') for n in nodes)
print()
print('Node languages:')
for lang, count in node_langs.most_common(15):
    print(f'  {lang}: {count}')

# Node kind breakdown ('kind' IS a node field — function, class, module, etc.)
node_kinds = Counter(n.get('kind', 'MISSING') for n in nodes)
print()
print('Node kinds:')
for k, count in node_kinds.most_common(15):
    print(f'  {k}: {count}')
"
```

**Node schema reference** — `Symbol.to_dict()` in
`packages/hypergumbo-core/src/hypergumbo_core/ir.py` is authoritative. If this
paragraph and the code disagree, the code wins: re-derive the field list from
`to_dict` rather than trusting this text.
Top-level fields are `id`, `name`, `kind`, `language`, `path`, `span`, `origin`,
`origin_run_id`, `stable_id`, `shape_id`, `fingerprint`, `quality`, `meta`,
`supply_chain` (nested: `tier`, `tier_name`, `reason`, `is_test_file`,
`is_example_file`, `is_config_file`, `is_generated_file`, `is_exported`),
`cyclomatic_complexity`, `line_span`, `signature`, `docstring`, `modifiers`,
`discovery_language`, `protocol_origin`, `display_label`, `qualified_name`.
The last four are the ADR-0031 / ADR-0032 axis-split siblings: `language` was
split into `discovery_language` (host source language) + `protocol_origin`
(protocol family such as `websocket` / `grpc` for synthetic linker stand-ins,
`null` for real-source symbols), and the former `canonical_name` field was split
into `display_label` (UI string — consumers display it, never branch on it) +
`qualified_name` (language-aware fully-qualified name). **`canonical_name` no
longer exists in output** (removed in ADR-0032): a probe reading
`n.get('canonical_name')` now always gets `None` — use `display_label` or
`qualified_name` instead.
Common mistakes: `lang` (use `language`), `type` on a node (use `kind`), `file`
(use `path`), `source` / `target` on an edge (use `src` / `dst`),
`canonical_name` (removed — use `display_label` / `qualified_name`).

**Tier label trap.** On a workspace package, `supply_chain.tier_name=internal_dep` is the default for everything *outside* `src/`/`lib/`/`app/` — including tests. On a self-analysis where the codebase is mostly first-party, a "78 % internal_dep" summary stat is not anomalous; it is the documented classification algorithm (`supply_chain.py` step 4: src/lib/app → tier 1, otherwise tier 2). Use the role flags (`is_test_file`, `is_example_file`, `is_config_file`, `is_generated_file`) to recover what kind of code a tier-2 node holds — not the tier label alone.

**Expected:** Orphan rate below 30%. If higher, the Python analyzer or linkers may be missing edges.
Edge type distribution should include `calls`, `contains`, `imports`, `instantiates`, etc. If most edges show `MISSING`, the behavior map schema may have changed.
Node language distribution should be dominated by `python` on a hypergumbo self-analysis; significant `MISSING` means nodes are missing the `language` field.

### Key symbols check

The top symbols should include core modules that everything depends on:
- `ir.py` symbols (Symbol, Edge, Span, AnalysisRun, etc.)
- `cli.py` (main entry point)
- `sketch.py`, `slice.py` (primary output generators)
- `discovery.py` (file discovery, used by all analyzers)

If these don't appear in the top 30, centrality ranking may have a bug.

### IO boundaries check

Expected IO boundaries for hypergumbo itself:
- **fs_read**: File reads in `discovery.py`, `paths.py`, all analyzers reading source files.
- **fs_write**: JSON output in `cli.py`, cache writes.
- **env_read**: `os.environ` / `os.getenv` / `sys.argv` reads — concentrated in `sketch_embeddings.py` and `cli.py`. Chains include attribute-kind primitives reached via `module_attr_ref` edges.
- **subprocess**: `build_grammars.py`, gitleaks integration, tracker sync helpers.
- **net_send**: Tracker sync (HTTP to Forgejo API) from the tracker helpers; HuggingFace Hub download from the `install-embeddings` extras subcommand. Those are the *only* legitimate network paths — see the caveat below about network during `run`.

If any of these categories are missing, the Python IO catalog or call-graph tracing has a gap.

**Network during `hypergumbo run` is a known defect, not an expected boundary.** The `runtime-cli-no-network` claim in `docs/hypergumbo.claims.yaml` (and the `SECURITY.md` generated from it) promises that analysis subcommands never send over the network — only `install-embeddings` may. In practice `hypergumbo run` still makes HTTPS calls to `huggingface.co` for model metadata on every invocation, even with the model fully cached locally and even when `install-embeddings` was not run this session: `local_files_only=True` is bypassed by HF Hub metadata calls plus a background `safetensors_conversion` thread. This is tracked as **INV-dasig (P0, violated)**. So if you observe HuggingFace traffic during `run` / sketch / any non-`install-embeddings` subcommand, treat it as a *confirmation of INV-dasig*, not as a normal net_send boundary. Caveat to the caveat: `HF_HUB_OFFLINE=1` masks the traffic, so an environment with that env var set may show zero net_send even though the bug is live.

The same chains feed `verify-claims`: every fs_write/net_send/subprocess primitive becomes a taint sink at `trust_level=untrusted` in zone `host_fs` / `network` / `host_fs` respectively, and every env_read primitive becomes a taint source at label `host_secret`. So a missing category here implies a missing taint-flow coverage too.

### Slice validation

Pick a known entry point and verify the slice captures its actual dependencies. `--entry` supports these forms (in precedence order; see `slice.py` docstring):

1. Exact node ID (most specific): `python:/abs/path/to/file.py:span:name:kind`
2. Exact file path
3. Path suffix match — `hypergumbo_core/cli.py` matches the absolute path ending with it
4. `module:name` shorthand — two tokens separated by a colon (e.g. `cli:main`) resolve to any symbol whose name equals the right side and whose file *stem* equals the left side; works across any extension and is the fastest way to disambiguate a short name like `main` that exists in many files (added by WI-hogun; see `slice.py` docstring)
5. Exact symbol name
6. Partial name match (contains)

If the form is ambiguous (e.g. `main` matches multiple symbols in different files) the error lists each candidate's full node ID so you can copy one.

```bash
# Slice the CLI via path-suffix match
hypergumbo slice --entry hypergumbo_core/cli.py
# → a few hundred nodes/edges (exact counts drift with the codebase; the
#   point is the slice is non-trivial and CLI-rooted, not a fixed number)

# Slice from a single named symbol
hypergumbo slice --entry link_middleware_chain

# Reverse slice: what calls Symbol?
hypergumbo slice --entry Symbol --reverse
```

The slice CLI prints `nodes: N · edges: N · limits_hit: [...]` and writes a JSON file alongside the analysis cache. **The slice JSON is referential, not standalone.** Its top-level keys are `feature`, `schema_version`, `view`; the slice contents live under `feature.node_ids[]` and `feature.edge_ids[]` as ID strings — not under `nodes` / `edges` like the behavior map. To answer "does the slice include file X?", join the IDs back against the parent behavior-map JSON:

```python
import json
with open(slice_path) as f:
    feature = json.load(f)["feature"]
with open(behavior_map_path) as f:
    node_by_id = {n["id"]: n for n in json.load(f)["nodes"]}

paths = {node_by_id[i]["path"] for i in feature["node_ids"] if i in node_by_id}
expected = {"sketch.py", "slice.py", "ir.py", "discovery.py", "paths.py",
            "io_boundary.py", "analyze/registry.py", "linkers/registry.py"}
missing = {e for e in expected if not any(p.endswith("/" + e) for p in paths)}
print(f"slice covers {len(paths)} files; missing: {missing}")
```

If `limits_hit` contains `file_limit` or `hub_pruned`, the slice was truncated — the missing-set check is then upper-bounded by the limit; expand with `--max-files` / `--max-hub-degree` if needed.

**Check:** Does the forward slice from `hypergumbo_core/cli.py` include `sketch.py`, `slice.py`, `ir.py`, and the analyzer modules? If major dependencies are missing *and* `limits_hit` is empty, the call graph has gaps.

### Explain validation

```bash
# Who calls Symbol? (should be many analyzers)
hypergumbo explain Symbol

# What does main call? (main is ambiguous, prompts for disambiguation)
hypergumbo explain main --with-source -x
```

## Step 3: Compare Against Prior Baseline

If you saved a prior analysis (from before your changes), diff the results:

```bash
# Quick metric comparison
python3 -c "
import json
for label, path in [('before', '/tmp/hg-self-analysis-before.json'),
                     ('after', '/tmp/hg-self-analysis.json')]:
    with open(path) as f:
        data = json.load(f)
    nodes = len(data.get('nodes', []))
    edges = len(data.get('edges', []))
    print(f'{label}: {nodes} nodes, {edges} edges')
"
```

**Regressions to watch for:**
- Node or edge count drops by more than 5% without explanation
- New orphan nodes that were previously connected
- Missing IO boundary categories that were previously detected
- Confidence scores dropping (check `provenance` fields)

## Step 4: Record Findings

- **Bugs found:** File tracker items immediately (`scripts/tracker add --kind invariant ...`).
- **Quality observations:** Record in the lab notebook under a "Self-Analysis" heading with the date.
- **Baseline snapshots:** Optionally save `/tmp/hg-self-analysis.json` to `~/hypergumbo_lab_notebook/self-analysis-baselines/` with a date suffix for future comparison.

## Anti-Patterns

- **Don't substitute self-analysis for bakeoff.** Hypergumbo is one Python project. It doesn't test Go, Rust, TypeScript, or cross-language linkers. Self-analysis validates Python analysis quality and provides architectural insight — it doesn't validate breadth.
- **Don't over-optimize for self-analysis results.** If you tune the tool to produce perfect output on itself, you may be overfitting to one codebase's patterns. Always validate changes against diverse repos via bakeoff.
- **Don't skip self-analysis because "it's just Python."** Hypergumbo's Python codebase uses enough real-world patterns (abstract classes, decorators, dataclasses, dynamic registry, CLI frameworks, multi-package imports) to be a meaningful quality signal.
- **Don't run self-analysis during a bakeoff.** The editable install means your in-progress changes affect all hypergumbo invocations. Run self-analysis on a clean `dev` branch or after your changes are committed.
