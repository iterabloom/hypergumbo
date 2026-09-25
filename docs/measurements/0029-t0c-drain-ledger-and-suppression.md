<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0029: What the ten-cell INV-linub drain moved, and what a declared sanitizer does to it

**Status:** Complete
**Date:** 2026-09-11
**Instrument:** `~/hypergumbo_lab_notebook/t0c_09112026/` — `PLAN.md` (pre-registration written before any number existed, plus two dated addenda appended rather than edited), `arm.sh` (one arm per process, each pinned to its own git worktree and reached through `PYTHONPATH`, `js_ts.py` blob asserted at BEGIN and END, exit 3 on mismatch), `diffprims.py` (two-way primitive diff that RAISES on an unknown schema key and cross-checks its own per-chain derivation against the tool's published `primitives_used`), `t0c_sanitizers.yaml` + `t0c_claims.yaml` (Arm A's declaration), `armA.json` / `armA_nodecl.json`, `RESULT.md`
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml), verbatim; Arm A appends an `extra_catalogs: sanitizers:` block and changes nothing above it
**Tracker:** `INV-linub` (the class this closes), the ten drain cells, `INV-busis` (the 2026-09-07 suppression worry this answers)

## Frame

Machine-readable per ADR-0048 §A3.

- unit: TWO, reported separately and never pooled. (a) the distinct
  `(language, boundary kind, primitive)` triple reached through io-boundaries;
  (b) `sanitized_flows` / `violated_claims` from verify-claims, for the
  suppression arm. Edges are NOT the unit anywhere in this record: an edge count
  is what INV-linub's own root_cause warns cannot be converted into a finding.
- allocation: CENSUS for the javascript arm (every chain in both arms, diffed
  two ways). The six inherited rows carry their own allocations in their own
  records and are marked INHERITED in the table rather than restated as if
  measured here.
- seed: `PYTHONHASHSEED=0` on every arm — mandatory rather than hygienic, since
  WI-jozap makes the elixir analyzer non-deterministic across processes.
- language_scope: seven languages (the drained set). The javascript row is
  measured here; the other six are inherited.
- cohort: `workadventure` (254 MB) as the javascript SUBJECT, `nextjs` (325 MB)
  as a second subject, and `modsecurity` (8 MB, zero `.js`/`.ts`/`.ex`/`.hs`) as
  the NO-LEAKAGE CONTROL. Arm A: pretix (the only repo of the pair that produces
  walks at all; apollo-server is reported as NO WALKS and excluded from the
  denominator rather than folded in as a zero).
- claim_set: the seven generic claims, verbatim.
- rubric: a primitive is REACHED if a chain names it; CORRECT if the source file
  the chain names really calls it.
- analyzer_sha: two arms, both whole trees pinned by SHA in a git worktree —
  OFF `fa20fe2048` (the parent of the first javascript drain commit), ON
  `e3b448b616`. Pinned by SHA rather than by the phrase "dev tip" so that
  landing later work does not silently redefine the arm.

## Why a whole-tree A/B is clean here, checked rather than assumed

Every non-tracker commit in `fa20fe2048..e3b448b616` is one of the three
javascript drain commits, one of the three elixir/haskell drain commits, a docs
commit, or one bakeoff path fix. No catalogue change, no core analyzer change
other than WI-dosuh's own. The elixir and haskell commits are structurally
unreachable from a javascript repository, so the window isolates exactly the
javascript cell — and the control repository proves it did.

## An ops fact this measurement established, worth more than its own result

`PYTHONPATH` pointing at a git worktree fully overrides an editable install:
with it set, `hypergumbo_core.cli.__file__` resolves inside the worktree, for
both `python -c` and the installed console script. The editable installs are
plain `.pth` path entries, not meta-path finders, so a `PYTHONPATH` entry wins.

An arm run this way therefore CANNOT pick up an edit made in the main working
tree while it runs, which retires — for arms run this way — the standing hazard
"the editable install means a running arm picks up your edits" and the
`git checkout <sha> -- <files>` dance that hazard forced.

## Result — Arm B, did the drain move user-visible answers?

    language     cell(s)                         primitive newly reached   verdict moved   provenance
    python       INV-mumov                       NO (kinds 16 -> 16)       no              inherited
    objc         WI-garar (the drain cell)       YES (+3, CocoaLumberjack) no              inherited (0025/0026)
    scala        WI-sigog, WI-pokam              YES (rows 0->13; 1->2)    no              inherited (0024)
    swift        WI-dodop                        YES (+1)                  no              inherited (0028)
    javascript   WI-vihop, WI-kikar, WI-dosuh    YES (+5)                  no (ev 4->7)    MEASURED HERE
    elixir       WI-kigub, WI-kafor              YES (+16, 4 new KINDS)    YES (2 flips)   inherited
    haskell      WI-lokun                        YES (+1)                  no              inherited

**Six of seven languages moved a primitive; elixir moved two VERDICTS and
javascript moved EVIDENCE within a standing one; ZERO primitives lost anywhere.** The two elixir verdicts moved from `inconclusive` to `violated`,
which is the strongest form the demand can take — not a number moving but a
question becoming answerable, because hypergumbo could not see `:ets` or
`:httpc` at all before and correctly said so.

The one row that did not move a primitive is python's, and it stays in the
record: INV-mumov added 2,943 correctly-resolved edges, moved `io_tag_rate`
31.1%, and reached no new boundary kind. That is exactly the case the
2026-09-10 drain ruling anticipated when it put the primitive demand on the
ledger rather than on each cell.

### The javascript arm (workadventure, 254 MB)

    total_io_edges                  1544 -> 1563
    distinct (lang,kind,primitive)    87 ->   92      GAINED 5, LOST 0
    boundary KINDS                    12 ->   12      no new kind
    VERDICT  host-description-no-network  violated ev=4 -> violated ev=7

All five gains are WI-kikar's (`process.cwd`, `process.cpuUsage`, `process.on`,
`performance.now` ×2 languages); WI-vihop shows as `fs.promises.readFile`
1 -> 2 chains; WI-dosuh emits registrations and surfaces no new primitive here.
Four of four distinct primitives read back against source and are CORRECT.

**One finding against the CATALOGUE, not the analyzer.** The `process.on` row's
own note reads `process.on('message', ...) for IPC from parent`, but the row
matches any event name and both live sites are `unhandledRejection` /
`uncaughtException` — error handlers, not IPC. The analyzer is right to emit;
the row over-matches, so one of the five reaches `ipc_recv` on evidence that
does not support it. Filed separately; it does not change the row's verdict.

### Controls

NO-LEAKAGE (P3): `modsecurity` — 0 `.js`/`.ts`, 0 `.ex`/`.exs`, 0 `.hs` — run
through both arms is BYTE-IDENTICAL (`io.json` md5 `0dd362492d…`, `vc.json` md5
`0fe07a57e7…` in both; two-way diff 0/0 over 30 keys). The arms differ by the
javascript cell and nothing else.

## Result — Arm A, what a declared sanitizer does to the drained set

    metric              A control   B ceiling     |  NO DECLARATION
    barrier walks / F        5/0         5/0      |       0/0
    sanitized flows          158         158      |         0
    violated claims            7           7      |         7
    evidence                 202         201      |       204
    instrument control  LIVE — 3 walk(s) left 'none'

**Two different things were being conflated under "suppression".**

1. A DECLARATION suppresses, and heavily: seven declared python sanitizer
   functions take pretix from 0 to 158 sanitized flows.
2. THE DRAIN DOES NOT: inside the declared arm, closing EVERY escape site at
   once moves `sanitized_flows` and `violated_claims` by 0, at a cost of one
   evidence item. Because arm B is a CEILING, that is a proof about the whole
   family — no INV-linub member can suppress a finding through this channel.

The 2026-09-07 worry that escape-closing work's "only reachable effect on
findings today is to delete them" is answered in the negative, and not by a
silent instrument: the same instrument measured 158 units of suppression one
column over.

**The misspelling trap the ruling warned about is CLOSED**, verified live rather
than restated: `taint_sanitizers:` now exits 2 with
`unknown extra_catalogs field(s) ... Did you mean: sanitizers?` (INV-sisod).

## What this does NOT establish

Not a precision claim (six rows report primitives REACHED; only javascript and
elixir adjudicated new rows against source in this session, 4/4 and 10/10).
`n_effective` per language row is one repository, two for scala and objc.
Arm A is pretix alone; apollo-server produced NO WALKS and is excluded from the
denominator rather than folded in as a zero. `nextjs` was launched as the
javascript subject and stopped by me at 26 minutes for resource contention —
recorded in the pre-registration's third addendum, not omitted.
