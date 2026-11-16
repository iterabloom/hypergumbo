<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Build-target-linker supersession precision — 12-repo corpus

| | |
|---|---|
| **Axis** | ADR-0057 §14.1 supersession grant (`RegisteredLinker.supersedes_consumed_stub`) |
| **Date** | 2026-10-30 |
| **Artifact** | `hypergumbo survey` on 12 repositories (below), on the WI-lipal branch tree |
| **Instrument** | full enumeration of every `build-target-linker` edge + a read of each against the manifest line it came from (`~/hypergumbo_lab_notebook/lipal_10302026/audit.py`, output `audit.txt`) |
| **Outcome** | GRANTED. |

```yaml
kind: linker_supersession_precision
artifact: twelve-repo-corpus-2026-10-30
date: 2026-10-30
passes:
  - pass: build-target-linker
    edges_emitted: 16
    audited: 16
    wrong_in_audit: 0
    structurally_consistent: 16
    verdict: GRANTED
```

## What the pass consumes

A manifest analyzer emits each build target (`[project.scripts]`, Cargo `[[bin]]` /
`[[example]]`, npm `bin`, a Gradle / Maven main, a Mix `escript`) as a
`defines_target` edge to a placeholder: a Python console script's is the dotted
`python:<module>:0-0:<func>:unresolved`, with the module made a file path and the
function in `meta`. The linker finds that function and emits a `calls` edge whose
`derived_from` names the `defines_target` edge — the §14.1 stated relation. With
the grant, finalize stamps the placeholder `superseded_by` / `superseded_by_origin`
and halves its `rank_score`. Nothing is deleted.

## Why the grant is safe here when it was refused to method-call recovery

`method-call-recovery-linker` was refused (`0020`) because it CHOOSES: more than one
class hint has a method of the stub's short name, and a line-proximity tiebreaker
picks one. This pass chooses nothing. An edge needs both:

- **the file**: the manifest-relative path, or for a Python module the manifest's
  `src/`, the package form `<module>/__init__.py`, else the one in-tree file whose
  path ends in the module's path by whole segments. Two such files name nothing.
- **the function**: the one the manifest names, exactly. A manifest that names a
  function the file does not define gets no edge; it no longer falls back to some
  other `main()` (that fallback was the one guess the pass made, and it was wrong
  for a re-export).

## The enumeration

| repository | `defines_target` | resolved | route | wrong |
|---|---:|---:|---|---:|
| hypergumbo | 4 | 4 | module (3 `src/` layout, 1 meta-package naming `hypergumbo_core.cli`) | 0 |
| poetry | 4 | 1 | module (`src/` layout); 3 are `tests/fixtures` scripts naming modules the fixture lacks | 0 |
| fastapi | 1 | 1 | manifest directory | 0 |
| firecracker | 8 | 8 | manifest directory (1 `[[bin]]`, 7 `[[example]]`) | 0 |
| ripgrep | 4 | 1 | manifest directory; 3 are `tests/` and `fuzz/` targets with no `main` | 0 |
| just | 3 | 1 | manifest directory; 2 are `tests/` and `fuzz/` targets with no `main` | 0 |
| pm2, knex, ioredis | 25 | 0 | npm `main` entries: a module, not a function, so no `main()` to reach | 0 |
| zellij, jedis, restic | 0 | 0 | — | — |
| **total** | **49** | **16** | 5 module, 11 manifest directory | **0** |

Every one of the 16 was read against its manifest line (`audit.txt`); none reaches a
function other than the one the entry names, in a file other than the one it names.
Before this change the pass resolved the 11 manifest-directory edges and none of the
5 module-route ones: on this repository the three console scripts' real functions had
no inbound edge from any manifest (WI-lipal).

**Not asserted:** a wrong rate. 0 of 16 bounds it at ≤19% at 95% (rule of three); the
corpus has few build targets, and the grant rests on the mechanism above — an exact
file identity and an exact name, ambiguity refused — not on the count. Gradle / Maven
mains and Mix escripts had no instance in these 12 repositories.
