<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Re-syncing vendored tree-sitter grammars

Hypergumbo ships three tree-sitter grammars from source under
`vendor/tree-sitter-{lean,wolfram,circom}/`. They're vendored (WI-fipab-kivoj)
rather than cloned at build time, so the build is offline-deterministic and
independent of upstream git hygiene (a force-push of the upstream `main`
branch broke nightly's source-grammar build on 2026-05-20 when the previously
pinned commit became unreachable).

## When to re-sync

Re-sync when upstream ships a parser change you actually want — typically a
grammar fix or a language-version bump. There is no automated upgrade
cadence; treat each re-sync as a deliberate dependency bump.

## What to copy

The build paths (both `scripts/build-source-grammars` and
`hypergumbo_core.build_grammars`) only need the upstream `src/` directory
plus `LICENSE`. Specifically:

- `src/parser.c` — generated parser, large; do not edit by hand
- `src/scanner.c` or `src/scanner.cc` — only for grammars that have one
- `src/grammar.json` — upstream metadata
- `src/node-types.json` — upstream metadata
- `src/tree_sitter/` — `parser.h` and helper headers from the
  tree-sitter generator (required for compilation)
- `LICENSE` — upstream LICENSE file (currently MIT for all three)

Everything else (bindings, build scripts, CI config, README, tests) is
upstream-only and should NOT be vendored — we generate our own Python
bindings in the build paths.

## Procedure

```bash
# 1. Clone upstream to a temp dir
cd /tmp
git clone https://github.com/<upstream>/tree-sitter-<name>.git
cd tree-sitter-<name>

# 2. Check out the commit you want
git checkout <sha>

# 3. From the hypergumbo repo, replace the vendored src/ in place
cd /path/to/hypergumbo
rm -rf vendor/tree-sitter-<name>/src
cp -r /tmp/tree-sitter-<name>/src vendor/tree-sitter-<name>/
cp /tmp/tree-sitter-<name>/LICENSE vendor/tree-sitter-<name>/LICENSE

# 4. Update the UPSTREAM file in vendor/tree-sitter-<name>/UPSTREAM:
#    - Commit:    <new sha>
#    - Sync date: <YYYY-MM-DD>

# 5. Rebuild and verify
./scripts/build-source-grammars
python3 -c "import tree_sitter_<name>; print(tree_sitter_<name>.language())"

# 6. Bump SHAPE_ID_SCHEME if the parser produced different shape_id values
#    on a representative test corpus (ADR-0014 §6 — shape IDs depend on
#    CST structure; a grammar version change can shift them).
```

## License hygiene

All three vendored grammars are MIT. If a future upstream relicenses to a
non-permissive license, do not pull the new version — open a tracker
item and discuss alternatives. The vendored LICENSE file in
`vendor/tree-sitter-<name>/LICENSE` must always match the upstream
LICENSE at the synced commit.

## Why vendor rather than git-subtree

The tracker item (WI-fipab-kivoj) offered git-subtree as an alternative.
We chose plain directory copy because:

- Subtree merge would import each upstream's full git history (thousands
  of commits) into the hypergumbo repo, swamping the changelog signal.
- The build paths only need a static snapshot of `src/`. Subtree's
  history-preservation value is real but pays off mostly for code we'd
  want to upstream patches against, which is not our relationship to
  these grammars.
- Re-syncing is a low-frequency, supervised operation. The five-step
  procedure above is short enough that the lack of `git subtree pull`
  doesn't matter in practice.
