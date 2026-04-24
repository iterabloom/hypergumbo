<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Migration Guide: I/O catalog refactor

This release refactors hypergumbo's I/O catalog system. It changes
what `hypergumbo io-boundaries` reports — and, indirectly, what
`hypergumbo verify-claims` will accept — for every repo that uses
third-party I/O wrappers.

This guide explains the user-visible shifts, the JSON shape changes,
and what to do if you depend on the old behavior.

## TL;DR

- `net_send` / `fs_read` / `fs_write` / `db_*` / `logging` chain counts
  drop on most real codebases.
- A new `external_potential` boundary section appears alongside the
  classical buckets.
- The JSON output gains a `boundaries.external_potential` key and a
  `dst_classification_unreliable` field on every chain.
- Catalog YAML files now require a `status` declaration plus, for
  `status: complete`, a `stdlib_provenance` block whose URL hostname
  suffix-matches an allowlist of official documentation hosts.

## Why the change

Previously, the I/O catalog grandfathered popular third-party
wrappers (`requests`, `axios`, `okhttp3.*`, `tokio::fs`, akka,
`huggingface_hub`, ...) so they would show up under `net_send` /
`fs_read` / etc. instead of being silently invisible. That carve-out
was a slippery slope: every popular library wanted its own entry, and
there was no principled stopping point.

The structural answer is to stop trying to enumerate every wrapper
and instead expose the **shape** of untrusted-territory reach as its
own signal. That is what the new `external_potential` bucket is.

The catalog now enumerates **only** stdlib symbols, with a per-
language `status` declaration (`complete` or `in_progress`) so
absence-from-the-catalog has a clear meaning: for `status: complete`
languages, "not in the catalog" = "not stdlib, probably third-party";
for `status: in_progress` languages, the absence is flagged as
not-yet-authoritative.

## What the output looks like now

### Text mode

The classical sections (`net_send`, `fs_read`, ...) still render the
way they always did, but a typical Python repo using `requests` will
no longer show those `requests.get` / `requests.post` calls there.
Instead, a new section appears:

```
  external_potential: 12 call(s)
    requests.get (8)
      <- fetch_user (src/api.py:42)  [tier-3 external_dep]
      <- ...
    huggingface_hub.snapshot_download (4)
      <- load_model (src/embed.py:17)  [tier-3 external_dep]
      <- ...
```

For source languages whose catalog is `status: in_progress`, every
chain in `external_potential` carries an `[unreliable]` marker:

```
  external_potential: 3 call(s)
    github.com/some/wrapper.Fetch (3)
      <- run (src/main.go:12)  [tier-3 external_dep]  [unreliable]
```

The `[unreliable]` marker means: "this language's stdlib catalog
hasn't been audited end-to-end yet, so the absence-of-catalog-hit
that caused this chain to land in `external_potential` isn't
authoritative — after the catalog is promoted to `status: complete`,
the chain may either stay here or move into a classical bucket."

### JSON mode

```diff
 {
   "boundaries": {
     "net_send": {
       "chains": [
         {
           "boundary": "net_send",
           "primitive": "urllib.request.urlopen",
           "io_edge_src": "...",
           "io_edge_dst": "...",
           "entry_points": [...],
           "high_risk": true,
           "dst_tier": 3,
           "dst_tier_name": "external_dep",
           "dst_external_boundary": true,
+          "dst_classification_unreliable": false
         }
       ]
     },
+    "external_potential": {
+      "chains": [
+        {
+          "boundary": "external_potential",
+          "primitive": "huggingface_hub.snapshot_download",
+          "io_edge_src": "...",
+          "io_edge_dst": "...",
+          "entry_points": [...],
+          "high_risk": false,
+          "dst_tier": 3,
+          "dst_tier_name": "external_dep",
+          "dst_external_boundary": true,
+          "dst_classification_unreliable": false
+        }
+      ]
+    }
   }
 }
```

`dst_classification_unreliable` is `false` by default and is set to
`true` only on `external_potential` chains whose source language's
catalog declares `status: in_progress`.

## What to do if you...

### ...used `net_send` chain counts as a "does this repo make network calls?" indicator

You were probably already wrong on third-party-wrapper-only repos
(some popular wrappers were never in the catalog), but the answer is
**now decisively wrong** for any repo using `requests` / `axios` /
`okhttp` / etc.

Use the union of `net_send` + `external_potential` chains where
`dst_tier == 3`. The new bucket is not just "things we don't
understand" — it's "things hypergumbo did not analyze the body of,"
which is exactly the population that may be making network calls.

For `verify-claims`, the same logic applies. A `must_not_exist
net_send` claim that previously failed on a `requests.get` call now
passes spuriously — but the `external_potential` bucket exposes the
reach so you can write a claim against it directly. Tier-aware
`verify-claims` semantics (a claim that surfaces tier-3 reach as
informational rather than a hard fail) are deferred to a focused
follow-up; for now you can read both buckets in your claim's
post-processing.

### ...pinned the JSON shape in downstream tooling

Two additive changes:

1. `boundaries.external_potential` is a new top-level key. If your
   tooling iterates `boundaries.keys()`, it picks up the new bucket
   automatically. If it switches on a hardcoded set of boundary
   names, add `external_potential` to that set.
2. Every `boundaries.<type>.chains[]` dict now contains a
   `dst_classification_unreliable: bool` field. JSON deserializers
   that error on unknown fields will need to allow this one.

Total chain counts will change. If you compute health metrics off
chain counts, recalibrate against the post-cull numbers.

### ...maintain a project-local taint catalog (`--taint-sources` / `--taint-sinks` / `--taint-sanitizers`)

No change. Project-local catalogs continue to override the built-in
catalog the same way they did before. If a third-party wrapper your
project cares about is no longer in the global catalog, you can
re-add it for your repo via a project-local catalog without
affecting anyone else.

### ...are a catalog contributor for a non-Python language

Every catalog YAML (`io_primitives/<lang>.yaml`) now has new
top-level fields:

```yaml
language: <lang>

# REQUIRED. "complete" means the catalog enumerates the entire
# stdlib of the language and declares stdlib_provenance.
# "in_progress" means the catalog is partial; external_potential
# chains in this language are flagged unreliable.
status: complete | in_progress

# REQUIRED for status: complete. Optional for status: in_progress.
# Cited at load time; URL hostname must suffix-match an allowlist
# of official-stdlib documentation hosts declared in
# io_boundary.py.
stdlib_provenance:
  source_url: https://docs.<authority>/<version>/library/index.html
  version: "<stdlib release>"
  retrieved: "YYYY-MM-DD"
  notes: |
    What you cross-referenced (e.g. `sys.stdlib_module_names`,
    a language's "list all modules" page, etc.).

# OPTIONAL. Stdlib symbols that are NOT I/O primitives.
# Used by the external_potential filter to drop "first-party calls
# a stdlib non-IO symbol" — math.sqrt, collections.deque, etc. —
# from the bucket. Empty until you populate it.
stdlib_other:
  - module: math
    functions: [sqrt, sin, cos, ...]
```

Promoting a language from `in_progress` to `complete` is a regular
PR: audit the catalog against the language's official stdlib
documentation, add `stdlib_provenance`, and flip `status` to
`complete`. Adding a hostname to the provenance allowlist is a
governance change requiring PR review (same shape as
`ALLOWED_WEBSITES.md`).

### ...care about the strict-stdlib rule itself

The catalog principle is now: **catalog membership = stdlib
(language ships it); absence = probably third-party, not certain**.
A load-time validator hard-errors on any catalog declaring
`status: complete` without provenance, so completeness claims are
auditable. The hostname allowlist defends against typos and
unofficial sources.

Project-local catalogs remain the escape hatch for "my project
depends on a wrapper not in the global catalog and I want it
classified" — they always take precedence over built-in entries.

## Reference

Per-change detail — down to the API-level and YAML-level shape of
each subsystem covered above — lives in the `[Unreleased]` section
of `CHANGELOG.md`, under the subsections for the strict-stdlib cull,
the catalog schema additions, and the `external_potential` bucket.
