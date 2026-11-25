<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0064: Shared call resolution: analyzers report, one pass decides; unresolved beats a guess

- Status: Proposed
- Date: 2026-11-25
- Supersedes (once accepted and built): ADR-0007 §"Decision" (per-analyzer path hints). Narrows ADR-0037 ruling 3. Extends ADR-0029. Completes what ADR-0006 withdrew on 2026-11-24.
- Rulings 1 to 8 below are the owner's, given in session on 2026-11-24 and quoted where words exist. The document as a whole, and the three decisions of §"Open for the owner", await acceptance. Nothing in §"The four pieces" is built.

## Context

### One question, answered about seventy times

Which declaration does this call reach? Each analyzer answers it for itself: about 72 analyzer files emit call edges, about 80 keep their own name tables, 16 construct one of the shared resolvers, 21 track variable types (text search, 2026-11-24). ADR-0006 gave each analyzer its own variable-type table; ADR-0007 gave each its own import map, passed to a shared lookup as a path hint. ADR-0029 took the other road for one sub-problem: analyzers stopped walking class hierarchies and one linker does it.

Three measurements, each made before this ADR was written, say what the per-analyzer road produced.

### Measurement 1: the fixture (PR #2394)

One program in 17 languages, a decoy for every callee, in two layouts (`test_call_resolution_matrix.py`; the tables are pinned there with the `resolved_by` mark behind every cell).

- Every class in one file: 28 of 170 cells bind a declaration the analyzer had no reason to prefer (the answer changes when two declarations swap places, or a call nothing in the repository answers is bound anyway).
- One class per file: 12. All twelve are in the imported-function and qualified-call columns. C#, Groovy and Objective-C make every one-file guess through a same-file name table; across files those calls are unresolved, and Objective-C resolves no method call across files at all.
- The call through a qualifier (`util.helper()`, `util::helper()`, `util.Util.helper()`) is right in 4 languages, a guess by file order in 4, unresolved in 4, has no edge in 1.
- With a third same-named candidate 19 cells move, all to unresolved or to no edge.

### Measurement 2: the census (51 repositories; 50 maps at dev b26c7dcf7, zstd at 66038a57a; `resolved_by` on every resolved call-family edge)

Resolved `calls` edges: 250,319. By what scoped the choice of the target (audit-findings 0030 defines the values):

| mark | edges | share |
|---|---|---|
| `name` (looked up among every declaration of the repository) | 73,243 | 29% |
| `receiver_type` | 71,244 | 28% |
| `own_type` | 24,873 | 10% |
| `same_file` | 20,733 | 8% |
| `qualified` | 14,995 | 6% |
| `import` | 13,604 | 5% |
| `same_module` | 12,241 | 5% |
| `lexical_scope` | 10,488 | 4% |
| `key_match` | 6,737 | 3% |
| `class_hint` | 2,036 | 1% |

The ten rows sum to 250,194; the other 125 are collapsed edges whose call sites disagreed. Resolved `instantiates` edges: 21,004, of which 17,636 are `name`. Share of a language's resolved calls marked `name`: C 100%, cuda 100%, php 69%, javascript 48%, rust 48%, typescript 42%, swift 33%, cpp 30%, go 13%, kotlin 9%, python 1%, java 0%.

### Measurement 3: the sample (ruling 4's "count, sample, then decide")

424 resolved `calls` edges drawn at random from the census maps, per language (C++, C, JavaScript, TypeScript, Swift, Rust, Go, Kotlin) and per mark, split by whether more than one declaring scope holds a same-named symbol. Eight judges, one per language, read the source and were told nothing about how the pairs were made: not the mark, not the candidate count, not that an analyzer produced them. Verdicts: the call invokes the target (`right`), invokes something else (`wrong`), cannot be told because the receiver is untyped (`plausible`), or there is no such call on the line (`no_call`). Files: `~/hypergumbo_lab_notebook/sample_11252026/` (`results.md`, the packets, the key, every judgment with its evidence).

| what scoped the choice | judged | right | wrong | no call / plausible | wrong of judged |
|---|---|---|---|---|---|
| `name`, more than one candidate in the repository | 120 | 56 | 57 | 7 | 48% |
| `name`, one candidate | 96 | 81 | 10 | 5 | 10% |
| `receiver_type`, the owner's name shared by several types | 36 | 19 | 17 | 0 | 47% |
| `receiver_type`, one such owner | 35 | 33 | 2 | 0 | 6% |
| `same_file` | 41 | 27 | 14 | 0 | 34% |
| `class_hint` | 15 | 9 | 6 | 0 | 40% |
| `qualified` | 17 | 12 | 4 | 1 | 24% |
| `own_type` | 24 | 24 | 0 | 0 | 0% |
| `import` | 20 | 20 | 0 | 0 | 0% |
| `lexical_scope` | 9 | 9 | 0 | 0 | 0% |
| `same_module` | 6 | 5 | 0 | 1 | 0% |

What the 110 wrong edges of these rows are wrong about, by the judges' own classification: 68 reach another declaration of the same repository (another type's method, another file's or package's function), 14 another overload of the right type (13 of them Swift), 27 something outside the repository (the standard library, a dependency, a built-in), 1 a target no call can invoke. Of the 10 wrong `name` edges with a single candidate, 9 are calls to something outside the repository whose name an in-repository declaration happens to share.

Read with these limits. Each (language, mark) cell holds 1 to 15 edges: the table separates "almost never wrong" from "wrong about half the time" and does not support a percentage to the digit. Eight languages, 51 repositories, one judge per language; two `wrong` verdicts were re-read against the source by the author and held. `key_match` is left out of the table: a protocol link (a `fetch` to a route) is not a language binding and the question does not fit it; the three JavaScript-to-Solidity links in the sample, bound by method name, were all wrong (WI-kabin).

### What the three say together

1. Where the analyzer used evidence the language itself uses (the enclosing type, an import, a lexical scope, the package), no sampled edge was wrong.
2. A name looked up among everything is wrong about half the time when the name has a second holder, and that is 19,823 edges in the eight sampled languages.
3. A receiver whose type is known is still bound wrongly about half the time when another type has the same NAME: the type was established and then looked up as a bare string.
4. A same-file table is not a scope: a third of its edges are wrong whether or not the file holds a second candidate.
5. A name with a single holder is wrong one time in ten, and nine of those ten wrongs are calls that leave the repository.

## Decision

### Rulings (owner, 2026-11-24, in session)

1. **One shared call-resolution pass.** "Build a shared pass: yes."
2. **Imports: each analyzer hands the shared pass a table of what its imports bind; the shared pass owns the lookup** ("let's go with C"). The per-analyzer path hint of ADR-0007 is replaced by this when a language adopts the pass.
3. **Unresolved beats a guess.** It replaces ADR-0006's "false positives (extra edges) are preferable to false negatives (missing edges)", which ADR-0006 withdrew on 2026-11-24 with the measurement that refuted it. When the pass cannot tell candidates apart, the call is unresolved and the candidates are kept.
4. **A name-only match keeps its number for now: first mark how every edge was resolved, count, sample, then decide.** The mark (PR #2389), the count and the sample are §Context. The decision is §"Open for the owner", O1.
5. **Variable types are scoped to the function that binds them.** Built (PR #2385, ten languages; ADR-0006 §"Scope Handling").
6. **Order.** Steps 1 to 4 agreed: variable types; measurement with no behaviour change; this ADR; the shared pieces with a pilot. Step 5 replanned as batches by mechanism (below).
7. **Declared names for Groovy, Scala, C++ and Swift go inside the next stable-id bump** (batch 5c), with the bump items already waiting; each bump item still needs its own ruling. In-place fixes for the guessing languages before their batch: "cheap ones only". No 11.0.0 before step 4.
8. **Test budget.** The full repository set (51 trees, about 20 minutes an arm) is run once before the first behaviour change (done: the census above is that run) and once after the last rollout PR. Every PR in between gets a pared set of about five minutes, one arm: the after-maps of one PR are the before-maps of the next.

### The mark (built, PR #2389 and #2393)

`Edge.meta["resolved_by"]` names what scoped the choice of a call-family edge's in-repository target. Its vocabulary, its rule ("name what scoped the search, not a check made on its result") and what it must not carry (a grade, a candidate count, locality, the call construct) are in `hypergumbo_core/resolved_by.py` and audit-findings 0030, and are adopted here. A call-family edge carries it if and only if it is resolved. The "only if" is by construction (finalize removes it from an edge that leaves the repository); the "if" is checked statically at creation sites and measured by census, and has no run-time gate yet (WI-pitok).

### The four pieces (to build; the contract the pilot refines)

Analyzers stop deciding and start reporting. For a call it cannot bind from its own syntax, an analyzer emits the call with facts; shared code turns facts into an edge or a refusal. This is ADR-0029's pattern widened from "walk the class hierarchy" to "type the receiver, resolve the name, find the method".

- **(a) Declaration index, shared.** Every symbol findable by its full declared name, by (file, name) and by owner (a type's methods, through the hierarchy `inherited_calls` already walks). One rule, in the one place it lives: a lookup returns one declaration, or none with the candidates kept. A per-language hook says when several declarations are one (a trait method and its implementations, `#[cfg]` alternatives, Go build-constraint variants).
- **(b) Import table, per file.** The analyzer fills local name to (module, name), with the kind (named, namespace, wildcard, aliased); `import_scope.py` is adopted for it (WI-tihup). Module to declarations is supplied by declared name for namespace and package languages and by path for path languages.
- **(c) Call facts, per call site, on the unresolved edge** (as ADR-0029's hints are today): the callee's name; the qualifier as written; the receiver's origin (constructed as T, annotated T, parameter of type T, result of calling F, field of the enclosing type, a chain rooted at one of those, unknown); the enclosing function and type.
- **(d) The resolver pass, a shared linker.** Types the receiver from its origin; resolves a type or function name through the file's import table to a DECLARATION, not to a string; finds the method on that type through the hierarchy; refuses ties; stamps `resolved_by`.

What the measurements require of these pieces, beyond the sketch: a type is identified by its declaration (finding 3: a bare type name is not an identity); evidence is read in the language's own order, qualifier and import before any repository-wide name (the shared lookup today answers on an exact key before it reads the import hint, WI-kanak, so the mark itself can differ by file order); a file's table of names is a lookup aid and never the reason for a bind (finding 4).

### Rollout (owner: "plan carefully how to reduce the number of PRs and economize on CI and local test time")

- **Step 4, the pilot: one language per family on one branch.** Rust (paths, traits), C# (declared names), JavaScript and TypeScript (paths, a module linker). The shared layer is what those three need in common: nothing enters it that fewer than two of them use, and nothing is written in two adapters that could be written once.
- **Step 5, batches**, one PR each, one commit per language: 5a java, kotlin, php; 5b python, ruby, go, lua, perl; 5c the stable-id bump (ruling 7); 5d groovy, scala, cpp, swift, objc; 5e lang-common; 5f lang-extended1 (5e and 5f move only the analyzers the probe lists show guessing: WI-bujih, WI-giroz).
- **Gates for every rollout PR.** Its languages' rows of the fixture in both layouts, cells and marks; the pared before/after on real repositories; a scoreboard in the PR description (the matrix, edges by mark, net lines per analyzer). A language's commit deletes what the pass replaced: a language that only adds is a duplication. A language that is not green is pulled from the batch. A rollout PR that needs a change in the shared core stops: that is a decision of its own.
- **Folding the older spellings** of "how was the target chosen" (audit 0030: receiver-flavoured `evidence_type` values, `resolution_quality`, the outcome half of `receiver`) happens per language in its batch (WI-gilip), under O3 below. Until then they are pinned shrink-only.

## What the pilot built and measured (step 4; rust, csharp, javascript/typescript)

Maps of 14 repositories before (dev 0d3c12ff4) and after (the pilot branch at e948d2ae3); every sample is of edges read in the source, drawn with one seed. The full scoreboard, with who read which sample, is in the pilot's pull request.

**The four pieces, as built.**
- **(b) the import table** is `ImportScope`, handed over per file (`AnalysisResult.import_scopes` to `LinkerContext.import_scopes`). It says which kind of module it holds: a NAMESPACE (C#: declarations are found by their qualified names; the table is the usings in scope in the file, global and MSBuild ones included) or a FILE (JavaScript, TypeScript, Rust: declarations are the ones in that file). It also records what a file passes on without declaring (`export ... from`; a Rust `pub use` is the file's own binding).
- **(a) the lookup** is `type_visibility`: which declarations a written name can mean from the caller's place, by the declared-name rule or the file rule; `declaring_files` follows a name through files that only re-export it. It holds no index of its own: the caller supplies candidates. A type is identified by its declaration and, where the language overloads types on it, by arity (`meta.type_arity`).
- **(c) the call facts** ride on the unresolved edge: `receiver_type_hint` (type arguments kept), `qualifier`, `enclosing_class`, `super_receiver`, `inherited_field_receiver`, `stdlib_member_name`. The receiver's ORIGIN, as sketched above, was not needed: analyzers reduce it to a type as written.
- **(d) the resolver is not one new linker.** `inherited_calls` already was the one place for "a method on a type" (ADR-0029); it now asks the lookup which type a name means, binds a function called by its imported name, and reads the new facts. `receiver_type_dispatch` (extension methods) and `method_call_recovery` are unchanged in what they do. One linker is new, and last: `single_holder_name`.

**The name step, and why the adapters could not simply delete name lookups.** The first JavaScript / TypeScript and Rust adapters deleted every bind by name, as the rollout rule "a language's commit deletes what the pass replaced" was first read. Resolved calls fell from 22,688 to 11,092 and from 11,561 to 4,768, and of 15 lost edges sampled in each language 13 and 10 were right. In 10,652 of the 11,805 lost JavaScript / TypeScript calls the name had exactly one holder. C# had hidden this: a typed language gives most receivers a type. So option (a) of O1 was built to be measured: a call nothing else resolved is bound to the ONE method (a call on a receiver) or function (a bare call) of its name, marked `name`, at 0.7; several holders, unresolved. It refuses on any fact that points elsewhere (a receiver type, an enclosing type, a qualifier that is not a binding to the repository, a bare name the imports do not bind, a stated module, a name the language's library defines). Three findings shaped it:
- It needs the analyzer's screen of library names. Without JavaScript's list (`push`, `then`, `forEach`), about 15 of 20 newly bound calls were wrong; C#, which has no such list, had 5 wrong of 15. The step therefore runs only for a file whose table says its analyzer screens (`ImportScope.library_names_screened`): JavaScript / TypeScript and Rust do, C# does not.
- Measured with the screen: JavaScript / TypeScript, 7,757 binds restored to their earlier target, 14 right of 15 read; 78 with no earlier bind of that name, 14 of 15. Rust, 1,549 restored, 13 of 15; 14 with no earlier bind, 5 right of 14 (standard-library names its list does not hold).
- A stub the import step already answered must not reach it (one edge per caller and target leaves a second call's stub open).

**Result on the 14 repositories, resolved `calls` before and after:** C# 5,572 and 7,621; JavaScript / TypeScript 23,102 and 23,401; Rust 11,561 and 9,533. By mark: in C# `name` 1,810 to 0 and `same_file` 100 to 0, `qualified` 957 to 3,429, `receiver_type` 973 to 1,748; in JavaScript / TypeScript `name` 14,581 to 7,835, `import` 52 to 3,964, `receiver_type` 788 to 2,024; in Rust `name` 5,616 to 2,512, `import` 229 to 1,850, `same_file` 961 to 314. Calls resolved before and unresolved now: 214, 659 and 2,409; in the samples of 15 of them, 6 (plus 2 of the right type and the wrong overload), 5 and 4 were right before. Of the edges newly resolved or retargeted that were sampled, none was wrong in C# (the right type with the wrong overload: 9 of 30, then 2 of 15) or in the second JavaScript / TypeScript and Rust rounds (15 of 15 each).

**What the rollout rules become.**
- "A language's commit deletes what the pass replaced" stands for binds on several holders, same-file tables and nearness. A bind on a name with one holder is not deleted: it moves to the name step, and the analyzer hands over its screen of library names with it. An analyzer without a screen keeps no name bind (C#).
- The hand sample of what MOVED, lost and gained, is a gate. The fixture went fully right for every adapter while the first two were losing mostly right edges; and the first measurement of the name step looked at what was lost and missed that it was newly binding builtins.
- One adapter needed several rounds (C# four, the others two), each sent back by its own sample. Budget a batch accordingly.
- "Nothing enters the shared layer that fewer than two of the three use" kept out, each filed with its count: overload selection; a base class written with type arguments; partial-type ancestors (two wrong edges remain on Dapper); a qualifier that is a member named like its type.

## Open for the owner

**O1. What a name-only bind becomes** (ruling 4's "then decide"). The sample: with several holders of the name, 57 wrong of 120; with one holder, 10 wrong of 96, nine of the ten being calls that leave the repository.
- (a) Several holders: unresolved, candidates kept. One holder: stays bound, marked `name`. *Recommended.* It removes the half-wrong population (19,823 edges in the eight sampled languages, about 11,000 of them wrong by the sample's rates) at the price of the right half of it, which the shared pass is then asked to win back with real evidence, language by language. It is ruling 3 applied where the measurement says a guess is being made.
- (b) As (a), and a single-holder bind is also refused when the call's own text points outside the repository (a receiver or qualifier bound by an import of an external module). This is where the nine-in-ten wrongs of the single-holder row live; it needs piece (b) and so arrives with each language's batch, not before.
- (c) Keep binding and lower the confidence of a `name` bind. Not recommended: the wrong edge stays in the graph, and a reader has to know to filter on a number to avoid following it. Which consumers (slice, dead-code, taint) filter on confidence today was not checked for this ADR.
- *Measured by the pilot (see the section above):* (a), with the part of (b) the call facts support, is what the pilot branch does for JavaScript / TypeScript and Rust; C# has no name bind at all. It is one linker (`single_holder_name`) to remove or to widen once this is decided.
- Whether (a) is applied in one PR across all analyzers (a refusal in the shared lookup; it moves about 20,000 edges at once, before any language has the pass to win them back) or per language in its batch is part of this decision. Recommended: per batch, so that each language's edges move once.

**O2. `resolved_by`: a field of `Edge`, or the `meta` key it is today.** ADR-0024's fold-residue rule 3 promotes a key written by two or more producer modules to a field; this one is written by about 90. `call_construct`, written by 25, was left in `meta`. Every creation site passes it as a keyword to `Edge.create`, so no producer changes either way; the schema does (a field is an addition: patch under the 0.x rule). Recommended: a field, in the step 4 PR, when the first reader (the magnet detector, WI-topit) starts to depend on it.

**O3. Folding the receiver-flavoured `evidence_type` values into `ast_call` plus the mark** (ADR-0028's own direction: `evidence_type` names the pathway by which the analyzer concluded the edge exists, and call variants collapse to `ast_call` with the residue in `meta`). Confidence is derived from the evidence type's band (ADR-0039), so a fold moves published confidences on every edge that carries one of the fifteen values. Recommended: fold per language in its batch, with the band chosen so that a `receiver_type` or `own_type` bind does not lose confidence and a `name` bind does not gain any; the per-value table is in audit-findings 0030.

## Consequences

- Call edges will be removed. Under O1(a) the largest single movement is the multi-holder `name` population. Every rollout PR reports resolved edges lost and gained, by mark.
- "Resolution certainty lives in `confidence` and `evidence_type`" (ADR-0037 ruling 3) becomes: the grade lives in `confidence`, what scoped the choice lives in `resolved_by`, and `evidence_type` keeps to the pathway (ADR-0028). ADR-0037's other rulings are untouched: `is_resolved` still means an in-repository target, decided once at finalize.
- ADR-0007's phases 1 and 2 (extract an alias map, pass a path hint) stay the implemented behaviour of every language until its batch lands; no new analyzer is written to that pattern.
- ADR-0029's linker is the precedent and a component: piece (a)'s owner lookup is its hierarchy walk.
- Analyzer files were expected to shrink (name tables, hint plumbing and later variable tables moving out), with net lines on the scoreboard so that this is checked and not assumed. Checked on the pilot: they did not (§"Not decided here, and not known", Effort).
- Languages outside the fixture's 17 get the mark (done) and move to the pass only where a probe shows guessing.

## Not decided here, and not known

- The cause of each guess outside Rust was read in the code during the stamping (the route lists are WI-kanak, WI-hubaf, WI-zibis, WI-mapas, WI-fatad, WI-suhol, WI-bujih, WI-giroz), not traced by experiment.
- Overloads. Thirteen of Swift's 22 wrong sampled edges are the right type and the wrong overload (WI-hinos). The pilot did not decide it: its three languages include one that overloads (C#: 9 of 30 sampled moved edges land on the wrong overload of the right type), and the two linkers that bind a typed call disagree (one keeps whichever overload its index held last, the other withholds on two). A call fact for it (the argument count at least) and one rule belong to the batch that brings Java, Kotlin and C++.
- Dynamic receivers. Where no origin types the receiver, the pass has no more to go on than the analyzer has today; O1 decides what is published for them.
- Linkers that bind by name across languages (the Lua FFI second phase, WI-patin; the Solidity ABI linker, WI-kabin) are outside the pass and are counted by the same mark.
- Effort. The pilot: 44 commits on one branch; eight worker rounds for three languages (C# four, the others two each) beside the shared layer; `csharp.py` 2,901 to 2,961 lines, `rust.py` 5,399 to 5,552, `js_ts.py` 9,287 to 9,601. The analyzers did not shrink: what they stopped doing (name tables, by-name routes) is smaller than what they now state (import tables, declared types, facts).
- A second judge per language, and languages outside the eight sampled.

## References

- Owner rulings and the plan: `~/hypergumbo_lab_notebook/shared_resolution_design_11242026.md`.
- Audit-findings 0030 (how a call's target was chosen); `hypergumbo_core/resolved_by.py`.
- The fixture: `packages/hypergumbo-lang-mainstream/tests/_call_matrix_fixture.py`, `test_call_resolution_matrix.py` (PR #2394).
- The census: `~/hypergumbo_lab_notebook/resolution_mark_11252026/census_full_final.md`. The sample: `~/hypergumbo_lab_notebook/sample_11252026/results.md`.
- ADR-0006 (variable types), ADR-0007 (import tracking), ADR-0024 (axis template, fold-residue rule 3), ADR-0028 (evidence type), ADR-0029 (inherited-call linker), ADR-0037 (edge resolution), ADR-0039 (confidence).
- INV-jutog (a resolved call's destination depends on declaration order), WI-pitok, WI-topit, WI-gilip, WI-tihup, WI-mobih.
