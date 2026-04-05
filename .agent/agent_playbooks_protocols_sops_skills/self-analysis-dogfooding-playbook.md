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
"
```

**Expected:** Orphan rate below 30%. If higher, the Python analyzer or linkers may be missing edges.

### Key symbols check

The top symbols should include core modules that everything depends on:
- `ir.py` symbols (IRNode, IREdge, etc.)
- `cli.py` (main entry point)
- `sketch.py`, `slice.py` (primary output generators)
- `discovery.py` (file discovery, used by all analyzers)

If these don't appear in the top 30, centrality ranking may have a bug.

### IO boundaries check

Expected IO boundaries for hypergumbo itself:
- **fs_read**: File reads in `discovery.py`, `paths.py`, all analyzers reading source files
- **fs_write**: JSON output in `cli.py`, cache writes
- **subprocess**: `build_grammars.py`, gitleaks integration
- **net_send**: Tracker sync (HTTP to Forgejo API), OpenRouter calls in hooks

If any of these categories are missing, the Python IO catalog or call-graph tracing has a gap.

### Slice validation

Pick a known entry point and verify the slice captures its actual dependencies:

```bash
# Slice from the main CLI entry point
hypergumbo slice --entry cli:main

# Slice from a specific analyzer
hypergumbo slice --entry py:analyze

# Reverse slice: what calls ir.IRNode?
hypergumbo slice --entry ir:IRNode --reverse
```

**Check:** Does the forward slice from `cli:main` include `sketch.py`, `slice.py`, `ir.py`, and the analyzer modules? If major dependencies are missing, the call graph has gaps.

### Explain validation

```bash
# Who calls IRNode? (should be many analyzers)
hypergumbo explain IRNode

# What does cli.main call? (should be run/sketch/slice commands)
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
