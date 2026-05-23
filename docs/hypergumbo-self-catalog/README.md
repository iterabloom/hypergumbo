<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# hypergumbo's project-local self-audit catalog

**What this directory IS:** project-local taint catalog entries used ONLY
for hypergumbo's own self-audit (`docs/hypergumbo.claims.yaml`). Loaded
via `extra_catalogs:` in the claims YAML.

**What this directory is NOT:** part of hypergumbo-the-tool's built-in
catalogs. Users installing hypergumbo from PyPI do NOT inherit these
declarations. They can use the same `--taint-sources` / `--taint-sinks` /
`extra_catalogs:` mechanism for their own dependencies.

**When to add to this directory:** if hypergumbo's own source code starts
using a new third-party library that does observable IO (net / fs /
subprocess), declare it here so the self-audit picks it up. The
built-in catalogs at `packages/hypergumbo-core/src/hypergumbo_core/io_primitives/`
are NEVER the right place — that would commit hypergumbo-the-tool to
maintaining catalog entries for that library across all users, violating
the stdlib-only scope.

## Catalog layering, refresher

There are three layers (per AGENTS.md "Catalog layering" section):

1. **Built-in stdlib catalogs** at `packages/hypergumbo-core/src/hypergumbo_core/io_primitives/<lang>.yaml`.
   Stdlib only. Hypergumbo-the-tool's responsibility, shipped to PyPI users.
2. **Project-local catalogs supplied by users.** The `--taint-sources` /
   `--taint-sinks` / `--taint-sanitizers` CLI flags + `extra_catalogs:`
   key in claims YAML. Each user can declare per-project rules for the
   libraries THEY use.
3. **Hypergumbo's OWN project-local catalog (this directory).** A specific
   *use* of layer 2's customization point, targeting this repo's audit.

The firewall: built-in (layer 1) is hypergumbo-the-tool's ongoing
commitment. This directory (layer 3) is one project (hypergumbo itself)
using the layer-2 mechanism. Do NOT promote entries here into layer 1.

## Contents

- `entry_points.yaml` — declares each CLI subcommand handler
  (`cmd_sketch`, `cmd_run`, `cmd_install_gitleaks`, etc.) as a synthetic
  taint source with `start_at: callee`. These act as the entry-point
  anchors for the per-entry-point safety claims.
- `peer_zones.yaml` — declares the project-local peer sink zones
  (`user_cache`, `user_out`, `local_bin`, `site_packages`, `rustup_dir`,
  `hf_cache`, `tmp_build`, `subprocess`, `dev_zone`) by listing specific
  sink call sites within hypergumbo's own code.
  > Since WI-bibuk (2026-05-23), the `subprocess` boundary auto-derives
  > directly into a `subprocess` zone via `AUTO_SINK_ZONE_MAP`; no
  > per-project override file is needed for the zone split. The override
  > mechanism in `_merge_with_user_override` (taint.py) remains
  > available for finer-grained per-`(module, name, kind)` zone swaps.

## Layer-3 entry rule

If hypergumbo's own source code starts using a new third-party library
that does observable IO, add it here (NOT to layer 1):

```yaml
# In an appropriate file in this directory:
taint_label: ...
sources:
  python:
    - module: huggingface_hub
      functions: [snapshot_download, hf_hub_download]
```

Then reference the file from `docs/hypergumbo.claims.yaml`'s
`extra_catalogs:` key so the self-audit picks it up.
