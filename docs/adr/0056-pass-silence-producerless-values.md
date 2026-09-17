<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0056: The four producerless pass-silence values — three are mis-hosted, one is gated

Date: 2026-09-16
Status: Accepted

## Decision

Four values on the `pass-silence-reason` axis have no producer. They are **not four instances of one omission**; they are **3 + 1**, with a strict internal order:

| value | verdict |
|---|---|
| `dependency_unavailable` | **RE-HOST** — the value is real, the axis is right, the host dataclass is wrong |
| `backend_disabled` | **RE-HOST** |
| `pass_crashed` | **RE-HOST** (called speculative here on a zero twin count; it has six real producers — see Corrections) |
| `prerequisite_absent` | **KEEP, GATED** — correctly declared and correctly hosted; producer deferred behind the other three. **Built in PR #1018**; it was not merely unproduced, it was being replaced by a false `no_candidate_files` — see Corrections |

Concretely: **do not** wire the `depends_on`-CNF producer INV-hujog asks for; **keep** `prerequisite_absent` declared; **restate** INV-hujog's trigger onto `limits.skipped_passes[].reason`.

**Decision rule:** LIVE.md §2 — *"a deferral's re-open trigger must be fireable by something other than the deferred work"* — plus §1.2, *"re-point, don't strike."* LIVE.md §1.7 (*"a vocabulary value with no producer is the same defect"*) pushes toward wiring a producer and is **not** satisfied by restating the trigger alone; it is satisfied here by making the value **falsifiable in a constructible environment**, rather than by shipping a producer that is wrong.

## The semantic ruling

**A pass that RAN and found nothing DOES satisfy a `depends_on` clause naming it.** This is not ambiguous: `catalog.py` builds `active_ids = {p.id for p in active_passes}` and tests `any(literal in active_ids for literal in clause)`. Membership in a list of passes; no counter is read. The docstring confirms it — *"`depends_on=[]` … vacuously satisfied"* — and the vocabulary throughout is "active", never "productive".

**That ruling does not rescue the proposed producer, because the interesting question is one level down: WHY is a pass not in the active set?** Two families, already separated by the pipeline. `_filter_by_file_presence` drops an analyzer *precisely because the repo has zero files of its language*, recording `reason: "no files matched"`. Everything else — grammar missing, backend off, crash — removes a pass for a reason unrelated to the repo's content.

Measured across every survey on disk (3,560 surveys, 229,541 `skipped_passes` records):

| reason | count | share |
|---|---:|---:|
| `no files matched` | 227,098 | **98.94%** |
| `rust-analyzer backend not enabled` | 2,383 | 1.04% |
| `tree-sitter-circom grammar not available` | 60 | 0.026% |

So on the default pipeline, "conjunct unsatisfied" is very nearly synonymous with **"the repo lacks that language"** — State A, a correct no-op. `prerequisite_absent` is declared as *"A declared upstream pass did not run. State C — the ordering defect."* Stamping it there would make the field assert an ordering defect about a repo that simply has no Rust in it: the axis's founding sin, committed inside the axis built to cure it.

**The discriminator is therefore not "is the conjunct satisfied" but "was the missing literal skipped for a FILE reason or a TOOLCHAIN reason."** That datum exists today, in free text, in the field the axis docstring says a consumer *"cannot ask … without matching ten spellings."*

## Why three of the four are mis-hosted, not missing a producer

A pass that is `dependency_unavailable`, `backend_disabled` or `pass_crashed` **has no `AnalysisRun`**:

- `all_analyzers.py` — `result.run is None` → append to `limits.skipped_passes`, return. No run.
- `all_analyzers.py` — `if is_skipped:` appends a skip; the sole `analysis_runs.append` is in the `else` branch. A skipped analyzer that *did* build a run object has it **discarded**.
- `registry.py` — a crashing linker goes to `_record_linker_crash(limits, …)` then `continue`. No result, no run.

`silence_reason` is a field **on `AnalysisRun`**. These three describe a pass that *did not run*, so there is no carrier in existence to stamp. Their correct home is `limits.skipped_passes[].reason`, where their free-text twins already live — as a structured companion key, not as values on `AnalysisRun`.

`prerequisite_absent` is the odd one out and the only well-hosted one: the pass that suffers it *did* run (a linker executed, found no upstream output, emitted nothing). It is also **second-order** — the propagation of the other three to a downstream pass — so it cannot be stamped *correctly* until the three first-order values have structured producers. That ordering was never written down and is the substance of this ADR.

**Reachability, against the "it can never fire" reading:** 40 of the 69 `depends_on` conjuncts consist entirely of `availability="extra"` (grammar-gated) passes — `tauri-ipc-linker ['rust']`, `jni-linker ['java']`, `cgo-linker ['go']`, and 37 more. On a machine without `tree-sitter-language-pack`, a JS+Rust repo gives `tauri-ipc-linker` a genuinely unsatisfied `["rust"]` *with Rust files present*. That is a real State C, it is actionable ("install the grammar"), and `no_candidate_files` is the wrong answer for it. **Keep the value.**

## Free-text twin disposition

Three of the four are a **migration**, not a new signal. `prerequisite_absent` has no twin — a grep over `packages/*/src/` for `prerequisite` / `upstream` / `did not run` / `required pass` in string literals returns only unrelated hits.

| value | twin | live count |
|---|---|---:|
| `dependency_unavailable` | two `TreeSitterAnalyzer` f-string templates (~50 spellings, one with a trailing space) plus stragglers | 60 |
| `backend_disabled` | `"rust-analyzer backend not enabled"` | 2,383 |
| `pass_crashed` | `f"crashed: {type(exc).__name__}: {exc}"` | **0** |

**Disposition: add a structured companion key, then CUT OVER the consumers. Do not dual-write.** Add `skip_reason_code` to `skipped_passes` entries on the existing axis; the prose `reason` stays as human-readable detail (it carries the pip command, the exception message — payload a code cannot). Cut over every consumer that today string-matches spellings. ~~Only ~4 write sites need touching, because the ~50 grammar spellings come from two f-string templates, not 29 hand-written strings.~~ **Measured at implementation: 49 sites across 38 files** — the f-string is copy-pasted into thirty analyzers, not inherited. See Corrections.

Dual-write is specifically wrong: two channels for one concept is the defect WI-finij named. One channel, two fields.

`pass_crashed` is **not** a migration — its twin has never fired in 229,541 records. It ships on a synthetic-crash test, and this ADR says so rather than bundling it silently.

## The work, in dependency order

**W1 — re-point `validate_pass_dependencies` from gate to falsification detector.** Independently valuable; fires today. Add `find_falsified_dependencies(active_passes, runs)` reporting passes that **emitted output** despite an unsatisfied conjunct. Keep `validate_pass_dependencies` as-is so its 11 tests stay green. **Do not wire it as a gate:** raising is the withholding direction (LIVE.md §2), and a gate would abort a survey on any repo whose declarations are stale. The declaration set has never been enforced and gets its first real audit from this.

**W2 — `skip_reason_code` on `skipped_passes`.** Schema bump. ~4 producer sites. Plus a summarizer mirroring `emit_silence_summary`, because `skipped_passes` is serialized today and **nothing summarizes it** — landing a code without a reader would repeat the defect this ADR is about.

**W3 — the `prerequisite_absent` producer.** Only after W2:

> Stamp `prerequisite_absent` on a silent run **iff** some conjunct of its `depends_on` contains no active literal **AND** at least one literal in that conjunct was skipped with a `skip_reason_code` other than `no_candidate_files`. Otherwise fall through to `derive_silence_reason`.

The plumbing exists — `run_all_linkers(ctx, limits)` already takes the `Limits` sink.

## The unmeasurability objection, and why it fails

The objection was that every tree-sitter grammar is installed here, so a grammar-driven `prerequisite_absent` could never fire and the feature would ship on reasoning alone. **Both halves are false.** Sixty `tree-sitter-circom grammar not available` records sit in the corpus: the state has occurred on this machine and nothing noticed. And the blindness is a venv, not a law.

**Named acceptance test, runnable before W3 and requiring no production code:**

> ~~Build a venv with `hypergumbo` and **without** `tree-sitter-language-pack`. Survey a JS+Rust repo (`aardvark-dns`, 16 `.rs` files, fast).~~ **This test as written returns empty and would have deprecated the value** — `aardvark-dns` is Rust-only and `rust` is not language-pack-gated. Corrected form: uninstall **`tree-sitter-rust`** and survey a genuine JS+Rust repo (`component-model-demo`, `robyn`). Assert `limits.skipped_passes` contains `rust` with a non-`no files matched` reason, **and** `tauri-ipc-linker` / `wasm-bindgen-linker` appear in `analysis_runs` silent. See Corrections.

The axis docstring's *"the drift bites a user in a bare environment, which is exactly who cannot report it to us"* is true, and is why the corpus is blind. It is not a reason the team must be blind: **construct the bare environment.**

## The restated trigger

Replacing INV-hujog's unfireable *"wire the validator when a reason-bearing corpus produces a `prerequisite_absent`"* — whose observable was emittable only by the wire-up being deferred.

> **T-hujog.** A survey's `limits.skipped_passes` contains an entry whose reason is **neither** `"no files matched"` nor another repo-content skip, **for a pass that appears as a literal in some `depends_on` clause of a pass that did run.**
>
> **T-hujog-b (falsification twin, checked in the same sweep).** A pass that **emitted output** has an unsatisfied `depends_on` conjunct. This re-opens the *declaration set*, not the wire-up, and is what W1 reports.

**Independently fireable:** it reads only fields already written (229,541 such records were extracted from disk without touching production code); its first half has already fired 2,443 times; it never mentions `silence_reason`, `prerequisite_absent` or `validate_pass_dependencies`; and it is satisfiable by a user in a bare environment, a CI matrix without the language pack, or anyone running the acceptance test above — three fireers, none of them INV-hujog. It can also return the other answer: the self-survey satisfies its first half (rust-analyzer) and fails its second (no dependent clause names it), so it currently reads NO.

## Corrections this ADR makes to the record

- **`pass_silence.py`'s own sizing is superseded.** Its docstring cites *"490 surveys on disk carrying 39,757 skip records, only TWO strings"*; the corpus now holds **3,560 surveys, 229,541 records, three** strings. The conclusion survives; the number does not, and it is the number the "unmeasurable here" argument rested on.
- **One supporting instance does not reproduce.** The analysis that produced this ADR cited `database-query-linker` falsifying its `[["sql"], …]` declaration — reading 353 files and emitting 15 nodes while `sql` was skipped — and concluded that wiring the validator as a gate *would have aborted the hypergumbo self-survey*. That rests on the Sep-15 cached artifact. On the current tree `sql` runs (one file), the clause is satisfied, and re-running the simulation yields **zero** falsified declarations. **The mechanism generalises** — on any repo with no SQL files that linker still reads ~400 files and emits nodes with its clause unsatisfied — but the specific instance is stale and is recorded here as such rather than as live evidence.

## Filing note

Bucket 1. This was scoped as an audit-findings document (`docs/audits/0020`) and **the format cannot carry it**: `audit_findings._REGISTRIES` binds exactly three registry-backed axes and `VALID_VERDICTS` is a closed frozenset admitting neither RE-HOST nor any sibling. See ADR-0054, which records the general rule. Decisions are also plainly present — four per-value verdicts, a work ordering, a restated trigger.

**Not pre-minted:** if the owner later adopts *"a silence reason for a pass that did not run belongs on `skipped_passes`, never on `AnalysisRun`"* as a standing rule binding future values, that is a separate ADR. This one rules on four values.

## What would change this decision

1. **A pass-level selector.** The ruling rests on "unsatisfied ≈ the repo lacks the language", which holds because file-presence filtering is the dominant remover (98.94%). A `--skip-pass`-style selector would break that equivalence and make the naive producer correct. `--skip` today takes sketch components, not passes; if that changes, **re-decide**.
2. **The `depends_on` set surviving W1 on a polyglot corpus.** One hit across 20 repos means the declarations are trustworthier than expected and W3 could precede W2. Ten hits means declaration repair, not W2, is the blocker.
3. **The bare-venv acceptance test coming back empty.** Then `prerequisite_absent` is unreachable in practice as well as in this corpus, and the verdict flips to **DEPRECATE**. Run it *before* W3.
4. **`pass_crashed` firing once.** Zero instances in 229,541 records; one real crash record moves it from speculative re-host to evidenced migration.
5. **An owner ruling that `AnalysisRun` should be emitted for passes that did not run.** That dissolves the re-hosting finding entirely and collapses 3+1 to a flat 4. This ADR judges it wrong — an `AnalysisRun` for a pass that never ran is ABSENT ≠ EMPTY one level up — but it is the owner's call, and it is the single assumption the class analysis rests on.

## Corrections from implementation (2026-09-17, PRs #1015 / #1016 / #1018)

All three work items landed. Four of this ADR's own claims did not survive the work, and they are corrected here rather than left for the next reader to rediscover.

**1. The named acceptance test does not test what it says, and run literally it would have DEPRECATED a value that is firing.** Two independent faults. `~/repos/aardvark-dns` does not exist — the repo is under `~/whole_bunch_of_repos/` — and it is **Rust-only**: 16 `.rs` files and zero `.js`, so it is not the "JS+Rust repo" the test calls it, and `tauri-ipc-linker` / `wasm-bindgen-linker` are activation-gated off there and produce no `AnalysisRun` at all. Separately, **removing `tree-sitter-language-pack` does not remove `rust`**; the mainstream grammars load from dedicated `tree-sitter-<lang>` wheels and the pack is the long tail's fallback. Run as written, the test returns empty — and §"What would change this decision" item 3 says an empty result flips the verdict to DEPRECATE.

Re-aimed (uninstall `tree-sitter-rust`, survey a genuine JS+Rust repo) it is **decisive, and the finding is stronger than this ADR anticipated**: `tauri-ipc-linker` and `rust-trait-dispatch-linker` were claiming `no_candidate_files` — declared to mean *"nothing to find, and no ordering or declaration mechanism would change it"* — while Rust files sat in the tree. The value's absence was not a gap; it was making the axis **assert something false**. Verdict: KEEP AND BUILD.

**2. `Pass.requires` is wrong for 25 of 26 grammar-gated literals, and that is what aimed the test at the wrong package.** Every `extra` language literal declares `requires="tree-sitter-language-pack"`; almost none of them use it. Nothing but a human reads the field — `is_available` only substring-matches `"tree-sitter"` — so nothing catches it. Filed as **WI-pakof**. An instrument aimed by a wrong declaration exits 0 with a plausible answer.

**3. W2 was sized at "~4 producer sites … two f-string templates". It is 49 sites across 38 files.** The grammar-missing f-string is *copy-pasted* into thirty analyzer modules, not inherited from a template. The estimate was checked before being designed against.

**4. `pass_crashed` is not speculative.** This ADR called it so on zero instances in 229,541 records — which measured the free-text *twin*, not the state. Six analyzer sites catch a parser **constructor** exception (`"Failed to load Go parser: {e}"` and kin): the grammar is installed and initialisation raised. That is not `dependency_unavailable`, because "install the package" is the wrong advice; it is `pass_crashed`, whose declaration says nothing about *who* contained the raise. The value has real producers.

**One thing W1 changed about the work ordering, per this ADR's own criterion.** §"What would change this decision" item 2 says *"ten hits means declaration repair, not W2, is the blocker."* W1's first audit found **261 of 893 surveys (29.2%)** carrying a falsified declaration, six distinct `(pass, clause)` pairs across five passes. The criterion is met; declaration repair is filed as **WI-rasal** and is the precondition for any future gate.

**One rule W3 needed that this ADR did not state.** The producer fills **only a field the pass body left empty**. Measured on the same repository, `pyffi-linker` is silent with the *same* blocked conjunct and truthfully claims `no_candidate_construct` — it scanned the Python side and there are genuinely no FFI call sites, so the missing Rust grammar is irrelevant to *its* silence. Overruling it would reverse the ADR-0054/PR-#1008 guard and replace a true claim with a plausible one. `prerequisite_absent` therefore **under-reports by design**, and sees only *analyzer* prerequisites, since linkers are deliberately not enumerated in `skipped_passes`.

## Related

- [ADR-0054](0054-pass-silence-candidates-unresolved.md) — the sibling split on the same axis, and the filing constraint.
- [ADR-0055](0055-linker-activation-tightening-refused.md) — why a closed activation gate is **not** `prerequisite_absent`'s missing producer.
