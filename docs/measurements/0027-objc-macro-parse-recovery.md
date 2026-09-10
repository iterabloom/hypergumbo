<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0027: Which macros break the Objective-C parse, and what recovering them reaches

**Status:** Complete
**Date:** 2026-09-09
**Instrument:** `~/hypergumbo_lab_notebook/lafom_objc_parse_09092026/` — `PLAN.md` (pre-registration, written before the production fix existed, naming a falsifier per prediction), `probe.py` (reproduces the filed measurement by two routes sharing no code), `snippets.py` (each suspected construct IN ISOLATION), `variants.py` (each rewrite rule priced cumulatively), `arm.py` / `diffarms.py` (through `analyze_objc`, whole-artifact equality before any field is picked), `final_probe.py`, `RESULT.md`
**Tracker:** `WI-lafom` (the defect), `WI-garar` (whose stage-2 prediction this refutation explained), `INV-bisok` (swift's instance of the same class, whose `parse_source` hook this reuses), `INV-linub` (the class)

## Frame

Machine-readable per ADR-0048 §A3.

- unit: the source FILE that parses without an ERROR node, with the
  `property_declaration` and `class_interface` node populations counted
  alongside so a recovered file that yields nothing is visible as such.
- allocation: CENSUS. Every objc file in the cohort is parsed in both arms;
  nothing is sampled.
- seed: none. Deterministic.
- language_scope: objc only, confined to `objc.py`.
- cohort: AFNetworking, CocoaLumberjack, fmdb, Mantle (387 files). **No control
  repository exists** — the objc cohort is entirely objc — so the control is
  INTERNAL: every file that parses clean in arm A must be byte-identical
  through the hook in arm B, which guard 1 makes structural.
- claim_set: n/a — this measurement prices a parse, not a verdict.
- rubric: a file "parses" iff `tree.root_node.has_error` is False. ERROR and
  MISSING nodes are counted SEPARATELY, because `has_error` is True for a
  MISSING node with zero ERROR nodes, and the first draft of this fix
  introduced exactly that.
- analyzer_sha: recorded in `RESULT.md`.

## The question the item asked first

WI-lafom filed the symptom — 74 of 387 real ObjC files carry a parse ERROR, the
grammar silently falls back to C, and `@property` never becomes a node — and
required the trigger to be established before any fix:

> establish WHICH construct breaks the parse … Bisect a failing header down to a
> minimal repro before deciding whether this is a grammar-version bump, a
> preprocessing step, or an accepted limit that must at least be DISCLOSED.

That order matters because INV-bisok's residual half recorded a **refuted** root
cause by naming the construct at the first ERROR node's line. That line is where
error recovery re-parented TO. So nothing is called a trigger here unless a
minimal snippet containing it errors and the same snippet without it does not.

## What breaks the parse

Each construct parsed alone, `snippets.py`:

| construct | ERROR nodes in isolation |
|---|---|
| `@property (nonatomic, strong) NSURLSession *session;` | 0 |
| the same with `nullable` | 0 |
| `NSArray<NSString *> *` generics | 0 |
| `NS_DESIGNATED_INITIALIZER`, `NS_SWIFT_NAME`, block property | 0 |
| `NS_ASSUME_NONNULL_BEGIN` **alone** | 0 |
| **`NS_ASSUME_NONNULL_BEGIN` WRAPPING a declaration** | **4** |
| **`typedef NS_ENUM(NSInteger, S) { … };`** | **3** |
| **`typedef NS_OPTIONS(NSUInteger, O) { … };`** | **3** |
| plain `typedef enum { … } S;` | 0 |

The item named `NS_ASSUME_NONNULL_BEGIN`, `nullable` and generics together and
called the trigger "narrower than modern ObjC". Only the first is a trigger, it
is one **only in the wrapping position**, and the item did not name `NS_ENUM`
at all — which is the larger of the two by files recovered.

**The grammar-bump option is closed, not skipped.** `pip index versions
tree-sitter-objc` returns 3.0.0 / 3.0.1 / **3.0.2**, and 3.0.2 is installed.
Upstream's last substantive commit is 2024-12-16 (one LICENSE commit since),
and `tree-sitter-grammars/tree-sitter-objc#21`, "bug: does not recognize
NS_OPTIONS", has been open since 2025-02-25. So the fix is INV-bisok's route.

## Which rules ship, and why not more

Priced cumulatively, each variant the previous plus one rule (`variants.py`):

| variant | ERR files | ERR nodes | `property_declaration` | `class_interface` |
|---|---|---|---|---|
| none | 74 | 1687 | 436 | 322 |
| `+NS_ASSUME_NONNULL_*` | 49 | 1126 | 486 | 346 |
| **`+NS_ENUM/OPTIONS/CLOSED_ENUM`** | **39** | **1094** | **486** | **346** |
| `+Apple availability by name` | 39 | 1078 | 486 | 346 |
| `+generic SHOUTY attribute` | 37 | 349 | **469** | 347 |

Rule 3 is **dropped**: it recovers zero files and moves zero nodes any consumer
reads. Rule 4 is **rejected**: it sheds 17 real properties. A count bought by
shrinking the denominator is pure loss.

**A defect the first draft of rule 2 shipped.** Rewriting `typedef NS_ENUM(…, S)`
to `typedef enum S` is a typedef with no declarator and yields a MISSING
`type_identifier` — a new parse failure inside the fix for parse failures.
Caught only by counting MISSING separately from ERROR; `has_error` is True for
a MISSING node with zero ERROR nodes. The shipped rule consumes the `typedef`.

## Result, at the parse

    repo             files   ERROR files    ERROR nodes   property_decl   class_iface
    AFNetworking        80      20 ->  6    996 -> 831      209 -> 239      84 ->  92
    CocoaLumberjack    224      25 -> 14    296 -> 224      132 -> 146     161 -> 172
    fmdb                30      11 ->  2    356 ->   3       45 ->  51      25 ->  30
    Mantle              53      18 -> 17     39 ->  36       50 ->  50      52 ->  52
    TOTAL              387      74 -> 39   1687 -> 1094      436 -> 486     322 -> 346

**19.1% → 10.1% of files carry a parse ERROR.** No count falls anywhere, which
is prediction 2's falsifier held.

## Result, through `analyze_objc`

    repo              symbols        classes          edges     typed slots
    AFNetworking     1158 -> 1267   133 -> 139   2786 -> 2786   1189 -> 1148
    CocoaLumberjack  2117 -> 2176   267 -> 276   4087 -> 4087   1370 -> 1371
    fmdb              584 ->  744    34 ->  40   1808 -> 2060    389 ->  474
    Mantle            386 ->  386    68 ->  68     568 ->  568    212 ->  212

Mantle is **byte-identical on every field**, predicted in advance: its trigger
is `QuickSpecBegin`, a Quick/Nimble test macro, not an Apple SDK macro. Reported
here because a repository where nothing moved is part of the result.

Symbol kinds moved as `property` +30 / +14 / +6, `method` +69 / +34 / +147,
`class` +8 / +11 / +7, `protocol` +2. AFNetworking and CocoaLumberjack gain no
edges because what they recover is HEADERS, which declare rather than call;
fmdb's +252 edges are `FMDatabase.m` (356 ERROR nodes → 3).

### The one count that fell, attributed

AFNetworking's typed module slots drop **1189 → 1148**. Every moved site was
read back: **41 re-keys, 0 sites lost, 0 sites gained**, and all 41 are
`AFURLSessionManager → external` — a **first-party** class leaving the module
slot of calls to AFNetworking's own API. That is consequence 2 of the item
closing, at the count the item predicted ("40 edges on AFNetworking"):

> `@interface AFURLSessionManager` produces NO class symbol, so
> `_project_classes` does not contain it, so the emit site's
> `_declared not in project_classes` guard … is silently defeated for exactly
> the classes whose headers do not parse.

`AFURLSessionManager` is in `_project_classes` for the first time (`False →
True`), the guard fires, and 41 false external attributions are withdrawn. **A
precision gain that reads as a count drop.**

fmdb's other movements read back the same way: 20 declaration sites corrected
`FMDatabase.h → FMDatabase.m`, and 7 `sqliteHandle` sites that were
`objc:external:0-0:sqliteHandle:unresolved` now resolve to the first-party
`FMDatabase.m:159-161:FMDatabase.sqliteHandle:method`. CocoaLumberjack's single
move is `isEqualToString:` `external → NSString`. **Zero real losses corpus-wide.**

### The 85 newly typed slots, censused

Every newly typed non-sentinel slot in fmdb was read back against source (the
full list is in `RESULT_RAW.txt`): `NSString.characterAtIndex:`,
`NSNumber.numberWithLongLong:`, `NSMutableString.appendString:`,
`NSDictionary.objectForKey:`, `NSError.errorWithDomain:code:userInfo:`,
`NSTimeZone.timeZoneForSecondsFromGMT:` and so on — **85 of 85 correct** under
measurement 0026's rubric.

## What this does not close

**10.1% of objc files still fail to parse**, so the item's third consequence —
an objc census under-counting by an unknown per-repository amount — is halved,
not closed. Residual triggers, identified rather than guessed: `QuickSpecBegin`
/ `QuickSpecEnd` (17 Mantle files), a project-local availability macro on a
block typedef (`AF_API_AVAILABLE(ios(10), …)`), `#if` / `#define` bodies in
vendored C, and generic-parameter fragments. The DISCLOSURE half — `has_error`
being read by nothing — is deliberately not in this change.

**A pre-existing defect this change made measurable.** The emit site guards the
module slot against first-party classes on the *declared-receiver* branch only;
the *uppercase-receiver* branch (`[FMStatement alloc]`) takes the receiver name
verbatim with no guard. Now that the symbol table is complete enough to tell the
two apart, **577 of 3,205 typed slots (18.0%) name a class the repository itself
declares**, plus 89 naming `__auto_type`, which is a type-inference keyword and
not a class at all. Present in both arms — not caused by this change, only
visible because of it.
