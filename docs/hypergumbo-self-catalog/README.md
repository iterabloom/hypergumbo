<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# hypergumbo's in-repo self-audit catalogue

**What this directory IS:** taint catalogue entries used ONLY for
hypergumbo's own self-audit (`docs/hypergumbo.claims.yaml`). Loaded via
`extra_catalogs:` in the claims YAML.

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

## Where this sits among the catalogue tiers

Catalogue data comes in four tiers ([ADR-0061](../adr/0061-catalogue-tiers-for-every-family.md);
the user-facing page is [CATALOGUES.md](../CATALOGUES.md)):

1. **Built-in** — `packages/hypergumbo-core/src/hypergumbo_core/<family>/`, rows
   hypergumbo stands behind. Stdlib only, enforced for every family by the scope
   gate in `test_catalogue_scope_gates.py`.
2. **Community** — shipped third-party rows, `provenance: community`. They load
   by default and may ADD findings but never make a verdict cleaner;
   `--no-default-overlays` turns the I/O ones off.
3. **Yours** — your channel directories under `$XDG_CONFIG_HOME/hypergumbo/`
   and the files you name with `--io-primitives` / `--taint-*`.
4. **In-repo** — catalogue data inside the analysed repository. It loads only
   on opt-in; naming a claims file on the command line opts in that file's
   `extra_catalogs:`.

**This directory is in-repo data**, opted in by `--claims docs/hypergumbo.claims.yaml`:
one project (hypergumbo itself) using the mechanism every project has. Do NOT
promote entries here into the built-in catalogues — that would commit
hypergumbo-the-tool to those libraries for every user — and the community tier
is not the escape hatch for that rule either: it is for widely-used
third-party libraries, not for one project's own dependencies.

## Contents

- `entry_points.yaml` — declares each CLI subcommand handler
  (`cmd_sketch`, `cmd_run`, `cmd_install_gitleaks`, etc.) as a synthetic
  taint source with `start_at: callee`. These act as the entry-point
  anchors for the per-entry-point safety claims.
- `peer_zones.yaml` — declares hypergumbo's own peer sink zones
  (`user_cache`, `user_out`, `local_bin`, `site_packages`, `rustup_dir`,
  `hf_cache`, `tmp_build`, `subprocess`, `dev_zone`) by listing specific
  sink call sites within hypergumbo's own code.
  > Since WI-bibuk (2026-05-23), the `subprocess` boundary auto-derives
  > directly into a `subprocess` zone via `AUTO_SINK_ZONE_MAP`; no
  > per-project override file is needed for the zone split. The override
  > mechanism in `_merge_with_user_override` (taint.py) remains
  > available for finer-grained per-`(module, name, kind)` zone swaps.

## Entry rule

If hypergumbo's own source code starts using a new third-party library
that does observable IO, add it here (NOT to the built-in catalogues):

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
