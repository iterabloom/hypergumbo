<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0026: What typing a Objective-C `self.<property>` receiver reaches, and the parse failure that hides it

**Status:** Complete
**Date:** 2026-09-09
**Instrument:** `~/hypergumbo_lab_notebook/garar2_objc_09092026/` — `PLAN.md` (pre-registration, written before either arm ran, naming its own falsifier), `arm.sh` (blob asserted at BEGIN and END, tree restored), `diffarms.py` (four-level delta incl. caveat digest), `RESULT.md`, `RESULT_RAW.txt`
**Claims:** [`docs/example-claims/generic-taint-claims.yaml`](../example-claims/generic-taint-claims.yaml), verbatim
**Tracker:** `WI-garar` (stage 2 of 2), `WI-lafom` (the parse failure this refutation exposed), `INV-linub` (the class), `INV-tapat` / `INV-maluk` (the F3 gate this finally satisfies for objc), `INV-fazim` (the category error caught in this change's own first cut)

## Frame

Machine-readable per ADR-0048 §A3.

- unit: the catalogue PRIMITIVE reached through objc's own edges, with the
  module-slot population and the closed-world caveat reported alongside.
- allocation: CENSUS of every newly-claimed module slot and every gained
  primitive; the underlying typed-edge population is counted, not adjudicated.
- seed: none. Deterministic.
- language_scope: objc only, confined to `objc.py`.
- cohort: AFNetworking, CocoaLumberjack, fmdb, Mantle. **No control repository
  exists** — the objc cohort is entirely objc. Same limit as measurement 0025.
- claim_set: the seven generic taint claims, verbatim
- rubric: measurement 0001's, applied to the slot: CORRECT = the module names
  the class the source declares for that property.
- analyzer_sha: BEFORE = `objc.py` blob `e3278f70…` — byte-identical to
  measurement 0025's AFTER, so this isolates stage 2. AFTER = `009c1fbc…`.

## Result

    repo             primitives via objc   untypable receivers
    AFNetworking            12 -> 12       947/1808 -> 837/1810  (52.4% -> 46.3%)
    CocoaLumberjack         19 -> 22      1338/2346 -> 1319/2346
    fmdb                     1 ->  1
    Mantle                   0 ->  0

CocoaLumberjack gains three rows end to end —
`NSFileManager.contentsOfDirectoryAtPath:error:`,
`NSFileManager.setAttributes:ofItemAtPath:error:`,
`NSPersistentStoreCoordinator.removePersistentStore:error:` — which is the
mechanism working: a `self.<property>` receiver typed from its declaration
reaching a method-kind row the F3 gate previously refused for want of a module.

AFNetworking's untypable share falls 110 sites to **46.3%, below its 47.5% from
before stage 1**, so stage 1's disclosure regression is repaid with interest.

## The sharpest pre-registered prediction was REFUTED

`PLAN.md` said: "**`net_recv` on AFNetworking goes 0 → NON-ZERO** … If this
stays 0, the fix does not do what the item says it does." It stayed 0 — the
boundary is absent from AFNetworking's map in both arms. Honoured as written.

## Why — established, not guessed, and it is a different component

`AFURLSessionManager.h:96` declares `NSURLSession *session`. **tree-sitter-objc
fails to parse that header**: `has_error: True`, 29 ERROR nodes, and the
`@property` line becomes `@` plus a C `function_declarator` named `property`. No
`property_declaration` node exists, so stage 2 has nothing to read.

Counted two ways sharing no code — `@property` lines in TEXT vs
`property_declaration` nodes in the TREE:

    repo             files  w/ERROR   @property lines   prop nodes   reached
    AFNetworking        80       20              245          209     85.3%
    CocoaLumberjack    224       25              179          132     73.7%
    fmdb                30       11               51           45     88.2%
    Mantle              53       18               50           50    100.0%

**72 of 387 files (18.6%) carry a parse ERROR.** The map is not empty; `session`
is simply among the 14.7% of AFNetworking properties that never become nodes.
CocoaLumberjack demonstrates the mechanism; AFNetworking demonstrates that an
upstream parse failure can hide it entirely. Filed as `WI-lafom`.

## A guard that stops working exactly where the parse breaks

`AFURLSessionManager` appears as a module slot 3 → 43, all 40 new ones through
this change's `receiver_type_hint`. It is a PROJECT class, and the emit site's
`_declared not in project_classes` guard exists to keep those out. Checked:
**`AFURLSessionManager` has no class symbol in the survey at all**, because its
`@interface` is in one of the 20 failing files. The guard is not wrong; it is
silently defeated wherever the declaring header does not parse. Not harmful at
classification — nothing in the catalogue is named that — and arguably
ADR-0050/0051-conformant, since the slot names the static owner path.

## A false positive caught in this change's own first cut

`_extract_property_type` initially took the FIRST `type_identifier` of the
`struct_declaration`. `@property (strong) IBOutlet UIImageView *v;` parses to
TWO, so the MACRO went into the module slot as a class: newly claimed 1x on
AFNetworking, 3x on CocoaLumberjack. That is the same INV-fazim category error
measurement 0025 removed 20 instances of, re-introduced by the stage meant to
improve things, and caught only because the arm reports newly-claimed module
slots. Fixed to take the LAST `type_identifier`; the arm was re-run. Generic and
protocol-qualified types yield no bare `type_identifier` and correctly abstain.

## Direction

No claim verdict moved; `sanitized_flows` 0 in every repo and arm. The
pre-registered sanitizer hazard (0–2 new barriers, since filling the slot makes
`_register_sanitizer_callers` reachable) did not materialise.
