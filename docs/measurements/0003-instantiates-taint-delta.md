<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0003: What the construction-edge widening did to taint precision

**Status:** Complete
**Date:** 2026-08-20
**Instrument:** [`scripts/measure-taint-precision.py`](../../scripts/measure-taint-precision.py) (unchanged; held constant across both arms)
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml)
**Tracker:** `INV-lalad` (the change under test), `WI-sivuz` / [0001](0001-taint-flow-precision.md) (the band this lands into)

## The question

`INV-lalad` added `instantiates` to `TAINT_CALL_EDGE_TYPES`, because the taint
walk could not traverse a construction edge and `subprocess.Popen(tainted)`
therefore verified CLEAN while `subprocess.run(tainted)` verified VIOLATED. That
is a **recall widening**, and [0001](0001-taint-flow-precision.md) had already
pre-registered the rule it lands into: precision is ≈41%, which is the `<50%`
band, whose stated consequence is *"precision is the headline problem and recall
work should stop until it is understood."*

So: **of the flows this change ADDS, how many are real?** Not "what is precision
now" — the marginal precision of the change itself, which is the number that
says whether the widening paid for itself.

## Why this is a delta, not a re-run of 0001

A straight re-run cannot answer it. 0001's census population was **60 flows**
on 2026-08-11; the same five repositories produce **298** at this measurement's
baseline commit, nine days later. Whatever added those 238 flows, it was not
this change, and pooling them would attribute someone else's drift to it.

The design instead holds everything constant except the code under test:

- **Baseline** `148fae52b3`, the parent of the taint fix, in an ephemeral clone.
- **Subject** `d8d415dd0d` (current `dev`).
- **Same instrument**, from the working tree, in both arms — the measuring
  device is not part of what varies.
- **Isolated analysis caches** (`XDG_CACHE_HOME` per arm), so each arm computes
  its own behaviour map with its own analyzer code. A shared cache would have
  silently served one arm's map to the other and hidden the analyzer half of
  the diff.
- **Flow identity** is production's own `_flow_identity` (source primitive,
  source symbol, sink primitive, sink symbol, full path) plus `claim_id`.

**The control was verified before any number was read**, because a control that
does not take is this project's most repeated failure. Under the baseline
`PYTHONPATH` the interpreter resolves `taint.py` to the clone and reports
`{calls, dispatches_to, module_attr_ref}`; unshadowed it reports the same set
plus `instantiates`.

## Population

Every distinct violating flow added by the change, across all six repositories
of 0001. A **census of the delta**, not a sample: 35 flows, all 35 adjudicated,
none unadjudicable.

| repo | base | new | added |
|---|---:|---:|---:|
| caddy | 219 | 219 | 0 |
| mitmproxy | 61 | 68 | **7** |
| poetry | 13 | 17 | **4** |
| express | 3 | 3 | 0 |
| apollo-server | 2 | 2 | 0 |
| pretix | 228 | 252 | **24** |
| **total** | **526** | **561** | **35** |

**Nothing was removed.** The change is purely additive in both arms, so it costs
no recall; the entire question is what the 35 are worth.

Every added flow has a **constructor-shaped sink** — `ZipFile`,
`TemporaryFile`, `NamedTemporaryFile`, `TemporaryDirectory`. That is the
mechanism working exactly as designed: PascalCase constructor calls are
`instantiates` edges, and they were previously invisible to the walk.

## Rubric

Unchanged from [0001](0001-taint-flow-precision.md) and reproduced there in
full: a TRUE POSITIVE requires the source value, or a value derived from it by
data flow, to be an **argument to the sink call or the receiver of it**, with
the carrying lines citable. Co-location, call-graph reachability with no value
passed, and control dependence only are FALSE POSITIVES.

## Headline

| population | added | TP | FP | precision |
|---|---:|---:|---:|---:|
| census (5 repos) | 11 | 1 | 10 | 9.1% |
| pretix | 24 | 0 | 24 | 0.0% |
| **combined** | **35** | **1** | **34** | **2.9%** |

**2.9% against a ≈41% baseline.** The widening landed an order of magnitude
below the layer it landed into.

## Verdict table

### The one true positive

`mitmproxy` — `expanduser` → `ZipFile`, `ddg_mixed`, 1 hop. Every link cites:

    onboardingapp/__init__.py:39   p = os.path.expanduser(p)
    onboardingapp/__init__.py:42   write_magisk_module(p)
    utils/magisk.py:95             def write_magisk_module(path: str):
    utils/magisk.py:100            with ZipFile(path, "w") as zipp:

The source value is the sink call's first argument. **This flow was invisible
before the change**, for the same reason `subprocess.Popen` was: the sink is
PascalCase, so the edge was `instantiates`.

### The thirty-four false positives

| n | repo | mechanism (0001 taxonomy) | what it is |
|---:|---|---|---|
| 24 | pretix | `content-not-argument` | The sink call takes **no tainted argument**. All 24 land in 10 distinct call sites, and every one is `TemporaryDirectory()` / `TemporaryFile()` / `NamedTemporaryFile()` with no arguments or a keyword literal, or `ZipFile` on a *generated* temp path (`os.path.join(d,'tmp.zip')`) or the temp-file object. The ORM data is written into the archive afterwards; it never reaches the constructor. |
| 6 | mitmproxy | `co-location-file` | Source symbol is a **file node** (`release/deploy-microsoft-store.py:1-1:file:file`). The analysis established nothing finer than "both appear in this file", and it multiplies: 3 source primitives × 2 constructor sinks = 6 "distinct" violations from one unanchored fact. |
| 2 | poetry | `co-location-function` | `shutil.get_terminal_size().columns` binds to `width` (`show.py:292-296`) and is used only for output truncation; the call that begins the chain is `find_latest_package(locked, root)`, which receives neither. |
| 2 | poetry | `content-not-argument` | `expanduser` genuinely flows six hops to `get_pep517_metadata(path)` — and the sink there is `TemporaryDirectory(ignore_cleanup_errors=True)`, whose only argument is a boolean literal. A real multi-hop data flow that still is not the reported finding. |

**These are not new mechanisms.** `co-location-function` (21) and
`content-not-argument` (8) were already 29 of 0001's 57 false positives. The
change did not introduce a defect; it fed a large new class of sinks into an
existing, already-documented weakness.

## The structural finding

**For constructor-shaped I/O sinks, the constructor is not where data crosses
the boundary.** `ZipFile(path,'w')` *opens*; `zipp.writestr(name, data)`
*writes*. Anchoring the sink on the constructor makes any tainted value
anywhere in the enclosing function produce a flow, because the constructor only
witnesses "an fs resource was created here".

The constructor is a legitimate sink when its **argument** is tainted — that is
exactly the mitmproxy true positive, where a secret-derived path decides where a
file is created. The defect is flagging it when the argument is not.

A **zero-argument constructor call cannot receive taint by argument**, and its
receiver is a module. That is a proof, not a heuristic, and it disposes of 8 of
the 10 pretix sites on its own. Filed for action rather than fixed here.

## Independent verification

**None. This is a single adjudication pass by the author of the change being
measured**, and 0001 used blind second passes specifically to control that bias.
Two things bound the exposure, neither of which substitutes for a second pass:

- The bias runs *against* the reported result. An author is motivated to find
  true positives; this pass found one in thirty-five.
- 24 of the 34 FP verdicts are **mechanical** — they turn on a call site having
  no arguments, which is checkable without tracing the source at all, and the
  check was run exhaustively over every constructor call site in all five files
  rather than only the reported ones.

The single TP is likewise mechanical in the other direction: `ZipFile(path,"w")`
with `path` bound from `expanduser` four lines earlier.

## What this does not support

- **This is marginal precision, not current precision.** It says what the change
  added is worth. It does not re-estimate the layer, and 0001's ≈41% is now
  nine days and 238 census flows stale — the baseline arm here reports 526 flows
  across the six repositories where 0001's population was 265.
- **It says nothing about the change's correctness.** Construction edges *should*
  carry taint; `subprocess.Popen(tainted)` verifying CLEAN was a real defect and
  is really fixed. A correct fix can still be a bad trade at the reporting layer,
  and separating those two claims is the point of measuring.
- **Six repositories are not a random sample.** pretix is 69% of the added flows
  and one framework shape.
- **No recall claim.** Nothing was removed in either arm, but this measurement
  never looked for flows the tool still misses.

## Consequences worth acting on

1. **The `<50%` band's rule was already the right call, and this is a second
   datapoint for it.** Recall work landing into this layer arrives at
   substantially worse than the layer's own precision, not merely at it.
2. **Sink-argument checking is the targeted fix**, not backing out the edge
   type. A no-argument sink call is provably incapable of carrying taint by
   argument; that removes 24 of these 34 with zero recall cost.
3. **Anchoring is still the cheapest large win**, as 0001 said. Six of the 34
   are one unanchored file node multiplied by the sink catalogue — the same
   *sources × sinks* amplification 0001 measured for unanchored placeholders,
   here with an anchored-but-useless file node.
