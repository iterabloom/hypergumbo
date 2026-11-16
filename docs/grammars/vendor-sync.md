<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Re-syncing vendored tree-sitter grammars

Hypergumbo ships three tree-sitter grammars from source under
`packages/hypergumbo-core/src/hypergumbo_core/vendor/tree-sitter-{lean,wolfram,circom}/`
(written `$VENDOR` below). They're vendored (WI-fipab-kivoj)
rather than cloned at build time, so the build is offline-deterministic and
independent of upstream git hygiene (a force-push of the upstream `main`
branch broke nightly's source-grammar build on 2026-05-20 when the previously
pinned commit became unreachable).

The tree is package data of `hypergumbo_core` (INV-bazoz), so the
`hypergumbo-core` wheel carries it, together with each grammar's `LICENSE`
and `UPSTREAM` file, and `hypergumbo build-grammars` works from a PyPI
install. It used to sit at the repo root (`vendor/`), which no wheel
contained. Keep it inside the package: anything outside
`src/hypergumbo_core` is not in the wheel.

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
VENDOR=packages/hypergumbo-core/src/hypergumbo_core/vendor
rm -rf $VENDOR/tree-sitter-<name>/src
cp -r /tmp/tree-sitter-<name>/src $VENDOR/tree-sitter-<name>/
cp /tmp/tree-sitter-<name>/LICENSE $VENDOR/tree-sitter-<name>/LICENSE

# 4. Update the UPSTREAM file in $VENDOR/tree-sitter-<name>/UPSTREAM:
#    - Commit:    <new sha>
#    - Sync date: <YYYY-MM-DD>

# 5. Rebuild and verify
./scripts/build-source-grammars
python3 -c "import tree_sitter_<name>; print(tree_sitter_<name>.language())"

# 6. Bump SHAPE_ID_SCHEME if the parser produced different shape_id values
#    on a representative test corpus (shape IDs depend on CST structure; a
#    grammar version change can shift them: see "shape_id and grammar
#    versions" below).
```

## shape_id and grammar versions

A tree-sitter `shape_id` is a hash of the grammar's CST
(`TreeSitterAnalyzer.compute_shape_id`), which gives the contract below. The
spec states it for consumers (`docs/hypergumbo-spec.md` §6, "Identity and
provenance scheme versioning").

- **Every grammar is pinned.** The three vendored grammars are pinned by
  commit, recorded in each `$VENDOR/tree-sitter-<name>/UPSTREAM`. The grammars
  installed from PyPI are pinned in each package's `pyproject.toml` with a
  compatible-release constraint (`~=X.Y.Z`).
- **`shape_id` is stable for a pinned grammar version.** Upgrading a grammar
  may change hashes.
- **`SHAPE_ID_SCHEME` names the algorithm, not the grammars.** It bumps when
  the hashing algorithm changes computed values, and step 6 above bumps it
  when a re-sync of a vendored grammar moves values. A patch release of a PyPI
  grammar inside its constraint reaches a user with no change here, so it
  moves values without a bump.
- **The grammar versions are in the output.** A run records the grammars it
  used and the hypergumbo version in `reproducibility_context.captured`
  (`grammars`, `hypergumbo_version`). A consumer comparing `shape_id` values
  across outputs treats `shape_id_scheme` together with those versions as
  the identity context, not `shape_id` alone.
- **No cross-version stability tests.** Hash identity across grammar versions
  is not tested: it is impractical to maintain, and the scheme identifier and
  the recorded versions are the mechanism a consumer has.

## License hygiene

All three vendored grammars are MIT. If a future upstream relicenses to a
non-permissive license, do not pull the new version — open a tracker
item and discuss alternatives. The vendored LICENSE file in
`$VENDOR/tree-sitter-<name>/LICENSE` must always match the upstream
LICENSE at the synced commit. It is redistributed in the
`hypergumbo-core` wheel and sdist next to the sources it covers.

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
