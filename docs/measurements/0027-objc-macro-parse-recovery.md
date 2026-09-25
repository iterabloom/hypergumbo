<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Measurement 0027: Which macros break the Objective-C parse, and what recovering them reaches

**Status:** Complete
**Date:** 2026-09-09
**Instrument:** `~/hypergumbo_lab_notebook/lafom_objc_parse_09092026/` — `PLAN.md` (pre-registration, written before the production fix existed, naming a falsifier per prediction, with an ADDENDUM recording where it was wrong and never an edit to the original), `probe.py` (reproduces the filed measurement by two routes sharing no code), `snippets.py` (each suspected construct IN ISOLATION), `variants.py` (each rewrite rule priced cumulatively), `parse_table.py` (the shipped table — it IMPORTS the production function rather than re-implementing it, because the prototypes here drifted the moment a rule was re-spelled), `arm.sh` / `arm.py` / `diffarms.py` (two arms through `analyze_objc`, `objc.py` blob asserted at BEGIN and END, whole-artifact equality before any field is picked), `readback.py`, `RESULT.md`, `RESULT_RAW.txt`
**Tracker:** `WI-lafom` (the defect), `WI-garar` (whose stage-2 prediction this refutation explained), `INV-bisok` (swift's instance of the same class, whose `parse_source` hook this reuses), `INV-linub` (the class)

## Frame

Machine-readable per ADR-0048 §A3.

- unit: **the ERROR NODE**, with the whole-FILE count reported beside it and
  the `property_declaration` / `class_interface` populations beside both. The
  file count was the unit this measurement started with and it is the wrong one:
  it hid the availability rule twice (39 → 38 files while ERROR nodes fell
  1094 → 265), because a file with two ERROR nodes in one corner has all its
  other method bodies back. Node counts are reported so a recovered file that
  yields nothing stays visible as such.
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
- analyzer_sha: BEFORE = `objc.py` blob `009c1fbc…` — byte-identical to
  measurement 0026's AFTER, so this isolates exactly this change. AFTER =
  `c9d881dc…`. Asserted at BEGIN and END of each arm, the arm exiting 2 on a
  mismatch, because the editable install means a running arm picks up edits.

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
| `FOUNDATION_EXPORT DDName const DDX;` | 0 |
| `@property (class, nonatomic, readonly) A *shared;` | 0 |
| **`… AF_API_AVAILABLE(ios(10), macosx(10.12));`** | **1 (820 in its file)** |
| **bare `DD_SENDABLE` before an `@interface`** | **2 (residual, filed)** |
| **`QuickSpecBegin(S)`** | **3 (residual, filed)** |

The item named `NS_ASSUME_NONNULL_BEGIN`, `nullable` and generics together and
called the trigger "narrower than modern ObjC". Only the first is a trigger, it
is one **only in the wrapping position**, and the item named neither `NS_ENUM`
(the larger of the two by files recovered) nor the availability attributes (much
the largest by nodes). The two rows that parse CLEAN and were on the suspect list
anyway — `FOUNDATION_EXPORT` and the `(class, …)` property attribute — are there
because error recovery in `DDLog.h` points at the `@property (class, …)` line,
which is the same trap this section exists to avoid.

**The grammar-bump option is closed, not skipped.** `pip index versions
tree-sitter-objc` returns 3.0.0 / 3.0.1 / **3.0.2**, and 3.0.2 is installed.
Upstream's last substantive commit is 2024-12-16 (one LICENSE commit since),
and `tree-sitter-grammars/tree-sitter-objc#21`, "bug: does not recognize
NS_OPTIONS", has been open since 2025-02-25. So the fix is INV-bisok's route.

## Which rules ship, and the one whose spelling was wrong

Priced cumulatively on the four-repository ObjC corpus, each variant the previous
plus one rule:

| variant | ERR files | ERR nodes | `property_declaration` | `class_interface` |
|---|---|---|---|---|
| none | 74 | 1687 | 436 | 322 |
| `+NS_ASSUME_NONNULL_*` | 49 | 1126 | 486 | 346 |
| `+NS_ENUM/OPTIONS/CLOSED_ENUM` | 39 | 1094 | 486 | 346 |
| `+availability attributes, Apple names EXACTLY` | 39 | 1078 | 486 | 346 |
| `+generic SHOUTY(...) before a ;` | 37 | **349** | **469** | 347 |
| **`+availability attributes, PREFIX-TOLERANT`** | **38** | **265** | **491** | **349** |

**The availability rule was dropped once, on a real number, and the number was
not measuring the rule.** Listing Apple's names exactly — `API_AVAILABLE`,
`NS_DEPRECATED`, eighteen of them — recovered ZERO files, and that verdict was
recorded and shipped. AFNetworking spells its own wrapper `AF_API_AVAILABLE`, so
the pattern never matched it: **a name search over an incomplete vocabulary does
not error, it returns clean.** Matching `(?:<PREFIX>_)?(?:API|NS)_(AVAILABLE|
UNAVAILABLE|DEPRECATED)…` instead takes `AFURLSessionManager.m` from **822 ERROR
nodes to 2**, and that file is where every `[self.session dataTaskWithRequest:…]`
call site lives.

**The file count was also the wrong unit**, and it is what made the rule look
inert twice over: it moves 39 → 38 while ERROR nodes fall 1094 → 265. A file with
two ERROR nodes in one corner has all its other method bodies back. Rules are
priced on ERROR nodes here for that reason.

The generic `SHOUTY(...)` rule stays **rejected**: it sheds 17 real properties,
and a count bought by shrinking the denominator is pure loss. The prefix-tolerant
rule reaches a lower ERROR-node count than it *and* sheds nothing.

**Byte length is not the whole invariant, and the second draft nearly shipped
the gap.** INV-bisok's hook contract says the rewrite must preserve byte length
so every span still points at real source. That is necessary and not sufficient:
tree-sitter derives a node's ROW from the newlines before it, so blanking
`API_DEPRECATED("msg",\n  ios(9, 13))` to spaces preserves every byte offset and
still shifts every following line number up by one. Caught by asserting the
NEWLINE COUNT alongside the length; the shipped blanker leaves `\n` bytes in
place, and the `NS_ENUM` padding puts back the newlines it consumes. The corpus
numbers are unchanged by the correction — which is exactly why a byte-length
assertion alone would never have found it.

**A defect the first draft of the `NS_ENUM` rule shipped.** Rewriting
`typedef NS_ENUM(…, S)` to `typedef enum S` is a typedef with no declarator and
yields a MISSING `type_identifier` — a new parse failure inside the fix for parse
failures. Caught only by counting MISSING separately from ERROR; `has_error` is
True for a MISSING node with zero ERROR nodes. The shipped rule consumes the
`typedef`.

## Result, at the parse

    repo             files    ERR files      ERR nodes     MISSING   property_decl   class_iface
    AFNetworking        80      20 ->  5      996 ->   8    113 ->  4    209 -> 244     84 ->  95
    CocoaLumberjack    224      25 -> 14      296 -> 218     89 -> 72    132 -> 146    161 -> 172
    fmdb                30      11 ->  2      356 ->   3     82 ->  2     45 ->  51     25 ->  30
    Mantle              53      18 -> 17       39 ->  36     21 -> 20     50 ->  50     52 ->  52
    TOTAL              387      74 -> 38     1687 -> 265    305 -> 98    436 -> 491    322 -> 349

**19.1% → 9.8% of files carry a parse ERROR, and ERROR nodes fall 84%.** No count
falls anywhere, which is prediction 2's falsifier held.

## Result, end to end — the case the item was filed about

WI-lafom's first consequence is that measurement 0026's pre-registered prediction
was refuted by this defect: AFNetworking's `net_recv` rows, all `NSURLSession`,
all reached through `self.session`, did not move because
`AFURLSessionManager.h` would not parse. Through `survey` → `io-boundaries`:

    AFNetworking                      before      after
    primitives reached via objc         12         21
    net_recv                             0          2
    net_send                             4          8
    ipc_send                             0          7
    ipc_recv                            11         14

Six `NSURLSession` rows reach for the first time — `dataTaskWithRequest:`,
`dataTaskWithURL:`, `downloadTaskWithRequest:`,
`uploadTaskWithRequest:fromData:`, `uploadTaskWithRequest:fromFile:`,
`uploadTaskWithStreamedRequest:`. **This is 0026's refuted prediction resolved
against the input rather than against the fix**, which is what a refuted
prediction is worth only if someone goes and finds out which.

Recovering the HEADER alone did not do it, and the intermediate arm proves it:
with rules 1 and 2 only, `session` becomes a `property_declaration` and
`net_recv` is still **0**, because the calls that use it live in
`AFURLSessionManager.m` — 822 ERROR nodes, none of them the two macros fixed so
far. The blocker moved from the `.h` to the `.m` and had to be found again.

## Result, through `analyze_objc`

    repo              symbols        classes          edges     typed slots
    AFNetworking     1158 -> 1291   133 -> 145   2786 -> 2973   1189 -> 1243
    CocoaLumberjack  2117 -> 2176   267 -> 276   4087 -> 4087   1370 -> 1371
    fmdb              584 ->  744    34 ->  40   1808 -> 2060    389 ->  474
    Mantle            386 ->  386    68 ->  68     568 ->  568    212 ->  212

Mantle is **byte-identical on every field**, predicted in advance: its trigger is
`QuickSpecBegin`, a Quick/Nimble test macro, not an Apple SDK macro. Reported
here because a repository where nothing moved is part of the result.

Symbol kinds: `property` +37 / +14 / +6, `method` +80 / +34 / +147, `class`
+14 / +11 / +7, `protocol` +2. CocoaLumberjack gains no edges because what it
recovers is HEADERS, which declare rather than call.

### The one count that fell, attributed

In the intermediate arm (rules 1 and 2 only) AFNetworking's typed module slots
DROPPED 1189 → 1148. Every moved site was read back: **41 re-keys, 0 sites lost,
0 gained**, and all 41 are `AFURLSessionManager → external` — a **first-party**
class leaving the module slot of calls to AFNetworking's own API, at the count
the item predicted ("40 edges on AFNetworking"):

> `@interface AFURLSessionManager` produces NO class symbol, so
> `_project_classes` does not contain it, so the emit site's
> `_declared not in project_classes` guard … is silently defeated for exactly
> the classes whose headers do not parse.

`AFURLSessionManager` is in `_project_classes` for the first time (`False →
True`), the guard fires, and 41 false external attributions are withdrawn. **A
precision gain that reads as a count drop.** In the shipped arm the same 41 are
still withdrawn and 228 correct sites arrive alongside them, so the visible net
is +54.

fmdb's movements read back the same way: 20 declaration sites corrected
`FMDatabase.h → FMDatabase.m`, and 7 `sqliteHandle` sites that were
`objc:external:0-0:sqliteHandle:unresolved` now resolve to the first-party
`FMDatabase.m:159-161:FMDatabase.sqliteHandle:method`. CocoaLumberjack's single
move is `isEqualToString:` `external → NSString`. **Zero real losses corpus-wide.**

### The 85 newly typed fmdb slots, censused

Every newly typed non-sentinel slot in fmdb was read back against source (the
full list is in `RESULT_RAW.txt`): `NSString.characterAtIndex:`,
`NSNumber.numberWithLongLong:`, `NSMutableString.appendString:`,
`NSDictionary.objectForKey:`, `NSError.errorWithDomain:code:userInfo:`,
`NSTimeZone.timeZoneForSecondsFromGMT:` and so on — **85 of 85 correct** under
measurement 0026's rubric.

## What this does not close

**9.8% of objc files still fail to parse**, so the item's third consequence —
an objc census under-counting by an unknown per-repository amount — is reduced,
not closed. The residual is one characterised family, isolated the same way the
shipped triggers were: a **bare, argument-less project macro in a
declaration-attribute position**. `DD_SENDABLE` before an `@interface` (2 ERROR
nodes alone), `NS_STRING_ENUM` after a typedef (1), and `QuickSpecBegin` (3, and
17 of Mantle's files). `FOUNDATION_EXPORT` and a `(class, …)` property attribute
both parse CLEAN — the `@property (class, …)` line that error recovery pointed at
in `DDLog.h` is fallout, not a trigger, which is the same trap this measurement
opened by naming. Blanking a bare identifier is a much wider blast radius than
blanking a parenthesised call, so it is filed rather than chased.

The DISCLOSURE half — `has_error` read by nothing — is deliberately not in this
change, and is cross-language: swift carries 5 residual files under INV-bisok by
the same mechanism.

**A pre-existing defect this change made measurable.** The emit site guards the
module slot against first-party classes on the *declared-receiver* branch only;
the *uppercase-receiver* branch (`[FMStatement alloc]`) takes the receiver name
verbatim with no guard. Now that the symbol table is complete enough to tell the
two apart, **583 of 3,300 typed slots (17.7%) name a class the repository itself
declares**, plus 89 naming `__auto_type`, a type-inference keyword and not a
class at all — **20.4% of the typed population between them, 29.0% on
CocoaLumberjack**. Present in both arms, so not caused by this change and only
visible because of it. It means every published objc typed-share figure — 0025's
and 0026's included, and this record's own — is inflated by that much, and fixing
it will make those numbers FALL as a correction rather than a regression.
