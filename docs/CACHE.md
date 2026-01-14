# Caching Architecture

Hypergumbo uses a two-tier caching system that balances efficiency with correctness. The cache lives in the XDG-compliant location `~/.cache/hypergumbo/`.

## Directory Structure

```
~/.cache/hypergumbo/
└── <repo_fingerprint>/              # Stable identifier for the repository
    ├── embeddings/                  # Shared across all states (by content hash)
    │   └── embed_<file_hash>.npy
    └── results/                     # Per-state snapshots
        └── <state_hash>/
            └── hypergumbo.results.json
```

## Design Principles

### Repo Fingerprint (Stable)

The repo fingerprint identifies a repository and **never changes** when files are modified. For git repositories, it's derived from:

1. The git remote URL (normalized)
2. The first commit SHA (the "root" of the repo)

This combination uniquely identifies a repository across clones, renames, and moves. For non-git directories, a hash of the absolute path is used.

### State Hash (Changes with Edits)

The state hash captures the **current state** of the repository at a point in time. It changes whenever:

- A tracked file is modified (detected via `git diff`)
- New untracked files are added (detected via mtime)
- Files are staged or committed

For git repositories, the state hash combines:

1. HEAD commit SHA
2. Hash of `git diff` output (staged and unstaged changes)
3. Modification times of untracked files

This provides fast detection of changes without reading file contents.

### Why Two Tiers?

**Embeddings are expensive but content-stable.** A function's embedding depends only on its source code, not on the overall repository state. If you modify `file_a.py`, the embedding for `file_b.py` doesn't need to be recomputed.

Embeddings are stored by file content hash:
- Same content = same embedding (cache hit)
- Modified content = new embedding (cache miss, but doesn't invalidate others)

**Results depend on the full repository state.** The behavior map (`hypergumbo.results.json`) includes cross-file relationships, call graphs, and framework detection. Changing one file can affect edges throughout the codebase.

Results are stored per state hash:
- Exact same state = reuse cached results
- Any change = fresh analysis (but embeddings are still reused)

## Workflow Example

1. First run on a fresh clone:
   - Cache miss everywhere
   - Computes all embeddings, stores in `embeddings/`
   - Generates results, stores in `results/<state_hash>/`

2. Modify one file and re-run:
   - State hash changes (different results cache)
   - Most embeddings are cache hits (unchanged files)
   - Only modified file needs re-embedding
   - Fresh results generated for new state

3. Checkout a previous commit:
   - State hash matches previous state
   - Full cache hit on both embeddings and results
   - Nearly instant sketch generation

## Cache Location

The cache respects the XDG Base Directory Specification:

| Variable | Default | Result |
|----------|---------|--------|
| `$XDG_CACHE_HOME` set | Uses value | `$XDG_CACHE_HOME/hypergumbo/` |
| `$XDG_CACHE_HOME` unset | `~/.cache` | `~/.cache/hypergumbo/` |

## Cache Invalidation

The cache is self-invalidating through content and state hashing. There's no need for manual cache management in normal use.

To force a fresh analysis, you can:

```bash
# Delete cache for a specific repo
rm -rf ~/.cache/hypergumbo/<fingerprint>/

# Delete all hypergumbo caches
rm -rf ~/.cache/hypergumbo/
```

## Staleness Warnings

When using `hypergumbo sketch --input hypergumbo.results.json`, hypergumbo checks if source files have been modified since the results file was generated. If so, a warning is displayed indicating the results may be stale.
