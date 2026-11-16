<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# JS-module-linker supersession precision — 7-repo corpus

| | |
|---|---|
| **Axis** | ADR-0057 §14.1 supersession grant (`RegisteredLinker.supersedes_consumed_stub`) |
| **Date** | 2026-10-30 |
| **Artifact** | `hypergumbo survey` on knex, ioredis, pm2, nuclear, apollo-server, jotai and hls.js, on merged dev `9dbf7e2c30` |
| **Instrument** | full enumeration of every `js-module-linker` `imports` edge with the stub it names in `derived_from`; a mechanical check of every edge whose answer Node's rules fix; a hand read of a stratified sample of the rest (`~/hypergumbo_lab_notebook/jsmod_10302026/`) |
| **Outcome** | GRANTED, after three wrong routes the audit found were fixed first (WI-pulid, WI-tufob, WI-dunab). |

```yaml
kind: linker_supersession_precision
artifact: seven-js-repo-corpus-2026-10-30
date: 2026-10-30
passes:
  - pass: js-module-linker
    edges_emitted: 6792
    audited: 6213
    wrong_in_audit: 0
    structurally_consistent: 6163
    verdict: GRANTED
```

## What the pass consumes

The JS/TS analyzer emits every import as an `imports` edge to a `<spec>:0-0:module`
placeholder. This pass resolves the specifier — relative path, directory manifest,
workspace package entry, tsconfig / Vite alias, or an npm package node for a declared
third-party package — emits its own edge to that file or package, and names the
placeholder edge in `derived_from` (§14.1's stated relation). With the grant, finalize
stamps the placeholder `superseded_by` / `superseded_by_origin` and halves its
`rank_score`. Nothing is deleted. For a workspace sibling the placeholder was a
workspace-prefixed `external_symbol` beside the real file edge (WI-halol shape 5,
an INV-nuzas counterexample); demoted, it is INV-nuzas carve-out (c).

## The audit found three wrong routes first

The first enumeration (7,290 resolutions on dev `d4bac1048a`) was not grantable:

| route | wrong | cause | fixed |
|---|---:|---|---|
| `require('.')` / `require('..')` | 35 | `_is_relative_import` missed the bare dot specifiers; they became npm packages named `.` / `..` | WI-pulid (#1956) |
| a Node core module (`fs`, `path`, `http` …) | 540 | no core-module check before the npm route: `npm:fs:package`, "third-party dependency" | WI-tufob (#1957) |
| a dotted basename (`./Album.test-wrapper`) | 30 | `probe_file` used `Path.with_suffix`, which replaced the last segment: `Album.tsx` | WI-dunab (#1959) |

Each was found by this audit and each would, under a grant, have demoted the
analyzer's stub beneath a wrong answer — the reason `0020` refused method-call
recovery.

## The enumeration on the fixed tree

| route | edges | check | result |
|---|---:|---|---|
| relative | 3,957 | resolved again by Node's order (the path, the path with each extension appended, a JS specifier's TS twin, the directory's `index`) against the file tree | **3,877 / 3,877** agree |
| relative into a directory with `package.json` | (55 of the 3,957) | read: pm2 `../..` / `../../..` → root `main` `index.js` (51), knex `../../../..` → root `main` `knex.js` (4) | **55 / 55** correct |
| relative with a filtered source file | (25 of the 3,957) | source line unreadable (INV-saruf); targets not judged | not audited |
| npm package node | 2,231 | package name equals the specifier's, and it is not a Node core module | **2,231 / 2,231** |
| bare specifier → in-tree file | 604 | hand read: 30 drawn before the fixes, 20 after — workspace entries (`exports` / `main` / `src/index` fallback), jotai's tsconfig `paths` | **50 / 50** correct |

`audited` above is every edge judged, mechanically or by hand: 3,877 + 55 + 2,231 +
50 = 6,213, none wrong. `structurally_consistent` is the mechanically checked 3,877 +
2,231 plus the 55 directory manifests, 6,163.

With the grant, a survey of the same seven repositories demotes **6,792** import stubs
— 2,231 beside a package node, 4,561 beside a file — every `superseded_by` naming an
edge present in the graph. Import stubs no edge resolves (Node core modules, specifiers
with no in-tree target) keep their rank.

**Not asserted:** that the 554 bare-specifier edges not read are correct (they share
the routes of the 50 that were); that the 25 relative edges with a filtered source are
(their targets were correct in the earlier sample, their sources are INV-saruf's
defect); a precision for repositories with Vite aliases or `exports` subpath patterns,
of which this corpus has few.
