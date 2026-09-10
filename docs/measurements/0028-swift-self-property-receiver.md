<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0028: What typing a Swift `self.<property>` receiver reaches

**Status:** Complete
**Date:** 2026-09-10
**Instrument:** `~/hypergumbo_lab_notebook/dodop_swift_09102026/` — `PLAN.md` (pre-registration with an ADDENDUM appended after stage 1 rather than an edit, recording that stage 1 answered a different question than the item asks), `shapes2.py` (receiver shapes over ALL call sites, production `_extract_call_target`), `untyped.py` (the population the item is about, keyed on the `receiver_type_hint` stamp rather than on a name swift never stamps), `props.py` (would the slot fill?), `measure.py` + `ab.sh` (two arms, `swift.py` blob asserted at BEGIN and END of each), `prim.sh` (survey + `io-boundaries` per arm, `cache-clear` between), `readback.py` (moved rows read back against source), `RESULT.md`
**Tracker:** `WI-dodop` (the defect), `WI-garar` (objc's instance of the same class), `INV-kotob` (why a project type is refused), `INV-linub` (the class)

## Frame

Machine-readable per ADR-0048 §A3.

- unit: **the MODULE SLOT** — an external method-call edge whose `dst` module
  segment is not the `external` placeholder — with **io-boundary CHAINS** as the
  end-to-end unit beside it. The typed SHARE is deliberately NOT the unit:
  `receiver_type_hint` rises by 368 on vapor while the slot rises by 252,
  because a PROJECT receiver type is typed and then correctly refused
  (INV-kotob). Pricing on the share would credit a change for the refusals.
- allocation: CENSUS. Every swift file in each repository, both arms.
- seed: none for the arms (deterministic); `20260910` for the read-back sample.
- language_scope: swift only, confined to `swift.py`.
- cohort: vapor (the sized repo) plus **three independent repositories that
  sized nothing** — Alamofire, Kingfisher, hummingbird. There is no
  no-op control repo, so the internal control is the edge count: `method_edges`
  must be IDENTICAL in both arms in every repository, since this change re-keys
  a slot and must neither create nor destroy an edge.
- claim_set: `docs/example-claims/generic-taint-claims.yaml` is NOT used here;
  the end-to-end unit is io-boundary chains, which rest on the catalogue rather
  than on a claim.
- analyzer_sha: BEFORE = `swift.py` blob `8caae9bf…` (dev `297c1bc157`);
  AFTER = blob `f6c70a46…`. Both asserted by `git hash-object` at the BEGIN and
  at the END of each arm, because the editable install means a running arm
  silently picks up an edit; `ab.sh` and `prim.sh` abort on drift and the
  restore is verified against the AFTER blob.
- rubric: a slot is "filled" iff `dst.split(":")[1]` is neither empty nor
  `external`. A row is "correct" iff the property's declared type in the
  enclosing type (or a base) matches the module the slot names, read at source.

## The defect

    db.write(2)        -> name='write' hint='db'   has_recv=True
    self.db.write(1)   -> name='write' hint=None   has_recv=True
    self.save()        -> name='save'  hint=None   has_recv=True

`self` parses as `self_expression`, not `simple_identifier`. `_walk_nav`
collects only simple_identifiers into `receiver_parts`, so nothing lands there,
and the inner `navigation_suffix` — `db`, the token that IDENTIFIES the
receiver — is consumed as a `method_name` and then overwritten by the outer
suffix. **Writing `self.` in front of a property destroyed the receiver hint for
the identical call.** Corroborated from the other side: `hint == 'self'` occurs
**0** times across 1,913 classified vapor sites.

Third language with this shape: objc `WI-garar`, rust `WI-dizag A`.

## The population (vapor, 4,944 external method-call edges)

Reproduces the item's re-sizing exactly: typed 2,112 (42.7%), untyped 2,832
(57.3%). 1,913 sites classified; 525 on multi-call lines excluded and counted
(an edge carries a line, not a column); 21.5% dropped, under the pre-registered
25% cap.

|     n | share | shape |
|------:|------:|-------|
|   938 | 49.0% | hint present, `_type_of` ran and returned None |
|   393 | 20.5% | `self.<property>.<method>()` |
|   384 | 20.1% | `self.<method>()` |
|   112 |  5.9% | chain off a navigation_expression |
|    86 |  4.5% | literals, call() result, tuples, `super`, … |

**Pre-registered P4 was REFUTED.** It predicted self-shaped receivers would be
the largest SINGLE bucket; they are 777 (40.6%) across two, and the largest is
the 938 where `_type_of` ran and failed. Two comparable populations needing
different fixes — the same pooling this item was re-sized for, one level down.
Only the `self.<property>` half is addressed here.

## Result — the module slot

| repo | method edges | slot before | slot after | delta | sites LOST |
|---|---:|---:|---:|---:|---:|
| vapor | 4,944 | 807 | 1,059 | **+252** | 0 |
| hummingbird | 3,411 | 654 | 770 | **+116** | 0 |
| Kingfisher | 2,504 | 939 | 987 | **+48** | 0 |
| Alamofire | 3,233 | 478 | 504 | **+26** | 0 |

**+442 gained, 0 lost, across every module in every repository.** `method_edges`
is identical in both arms in all four, so this re-keys slots and creates no
edges. Top vapor gains: `NIOLockedValueBox` 41→181, `Logger` 22→49, `EventLoop`
18→35, `ByteBufferAllocator` 25→35, `EventLoopPromise` 11→18, `ByteBuffer`
19→26.

**Correctness:** 25 rows sampled from the 239 moved `self.<property>` sites and
read back against source — **25 correct, 0 wrong-type**, including an
optional-chained `self.promise?.fail(…)` → `EventLoopPromise` and
`self.allocator.buffer(…)` → `ByteBufferAllocator`.

## Result — what it REACHES (L5)

A filled slot is necessary, not sufficient: measurement 0025 moved 787 correct
objc names and recall by zero. Measured through `io-boundaries` on vapor,
`cache-clear` between arms:

| | before | after |
|---|---:|---:|
| io-boundary chains | 43 | **59** (+16) |
| distinct primitives reached | 16 | **17** |
| chains lost | — | **0** |

|  delta | boundary | primitive |
|-------:|----------|-----------|
|     +6 | logging | `Logger.debug` |
|     +6 | logging | `Logger.trace` |
|     +2 | logging | `Logger.warning` |
|     +1 | fs_read | `NonBlockingFileIO.readChunked` — **newly reachable** |
|     +1 | process_send | `EventLoopGroup.syncShutdownGracefully` |

**Stated rather than buried: 14 of the 16 new chains are `logging`**, a
low-severity boundary. The two that carry weight are the `fs_read` primitive,
which no chain reached in the before arm at all, and the `process_send` chain.
A reader pricing this change on severity should read it as "+1 new primitive and
2 substantive chains, plus 14 logging chains", not as "+16".

## Two instrument errors found in this measurement's own output

Recorded because both produced publishable-looking numbers.

1. **A `[:12]` truncation manufactured a regression.** The first arm comparison
   reported `Array 20 → 0` and `EventLoop 0 → 35`. Neither is real: the
   per-module table was truncated to the top 12, and the newly added modules
   pushed `Array` out while `EventLoop`'s true before-value (18) had been
   outside the cut. Caught by ARITHMETIC — the per-module deltas summed to +208
   against a +252 total, so the breakdown could not be complete. The instrument
   now emits every module.
2. **The stage-3 "59.3% external" figure is an ESTIMATE with a known bias and
   is not an acceptance criterion.** `props.py` keys declarations by property
   NAME repo-wide rather than by enclosing type, so a name used for two types
   resolves to whichever the walk saw first; `connection → Name` is a visible
   collision in its own output, and `NIOLock` reads "project" though it is
   swift-nio's. The A/B above is the real number.

## What this does not establish

- The 938 hint-present failures are untouched. They are the larger bucket.
- Bare `self.<method>()` (384 sites) is deliberately out of scope: its receiver
  is the enclosing PROJECT type, which can never fill the module slot, and it
  would stamp a `receiver_type_hint` that `method_call_recovery` step 3a acts on
  to REFUTE a class hint. That is a separate decision with its own measurement.
- Direction: more typed receivers feed sanitizer registration, and a registered
  barrier earns `sanitized` and DROPS a flow (ADR-0052). No chain was lost here,
  which is the both-directions reading for this change on this corpus.
