<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
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

### Analyzer Identity (Changes with Toolchain)

The cache path also includes an **analyzer-identity segment** so that two hypergumbo installs running against the same source tree do not poison each other's cache. Without this segment, whichever process wrote first would win — a real problem for:

- **Stable + dev coexistence** — running released `hypergumbo` and an in-tree development build side by side.
- **RCT cross-arm comparisons** — bakeoff routines holding multiple wheel-pinned arms in parallel.
- **Partial lang-package upgrades** — bumping `hypergumbo-lang-mainstream` but not `hypergumbo-core` (the meta-version doesn't change, but the analyzer behavior does).

The analyzer-identity hash is a 16-character hex digest over:

1. `hypergumbo_core.__version__`
2. Per-package content hashes of every installed `hypergumbo_*` distribution (walked via `importlib.metadata.distributions()` and memoized for the process lifetime)

Cache path now reads:

```
~/.cache/hypergumbo/<repo_fingerprint>/results/<analyzer_identity>/<state_hash>/
```

**Out of scope:** eviction sizing for the larger key space, cross-machine sharing, and migration of pre-fix cache entries — those will fall through as misses on first access and be rewritten under the new path.

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

## Auto-Discovery

When running `hypergumbo sketch` without `--input`, hypergumbo:

1. Computes the repo fingerprint and state hash
2. Checks `~/.cache/hypergumbo/<fingerprint>/results/<state>/hypergumbo.results.json`
3. If found, uses the cached results for instant sketch generation
4. If not found, automatically runs `hypergumbo run` to populate the cache first

This means you can simply run `hypergumbo .` and get the benefits of caching without manual cache management.

## Staleness and Manual Input

When using `hypergumbo sketch --input <file>` with a manually-specified results file, hypergumbo checks if source files have been modified since the results file was generated. If so, a warning is displayed indicating the results may be stale.

The auto-discovered cache doesn't need staleness warnings because the state hash inherently captures file modifications—a changed file produces a different state hash, resulting in a cache miss that triggers fresh analysis.
