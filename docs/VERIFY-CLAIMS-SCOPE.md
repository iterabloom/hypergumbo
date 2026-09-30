<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# What `verify-claims` can and cannot see

`hypergumbo verify-claims` answers security claims about a repository — *"no
environment value reaches a log sink"*, *"nothing read from the network reaches
a subprocess"* — and it is meant to be run in CI, where a passing exit code is
an assertion someone will rely on.

**This page is the scope of that assertion.** Everything here is a limit the
analysis knows about and declares. It is published for the reason AGENTS.md
requires of every status claim in this project: *explicit gaps over implicit
completeness.* If you are gating a pipeline on this tool, read the exit-code
contract and the caveat vocabulary; if you are reading a report, read *"What a
clean verdict does not mean"*.

## The verdict ladder, and the exit code you must not treat as success

| verdict | meaning |
|---|---|
| `confirmed` | The claim held everywhere the analysis could look. |
| `confirmed_with_caveats` | The claim held, **and something a reader has to see** qualified it. Every instance names its subject; the kinds are listed below. |
| `violated` | At least one flow or boundary chain contradicts the claim. |
| `inconclusive` | The analysis could not check the claim. **Not a pass.** |

Exit codes, in precedence order — the first that applies wins:

| code | condition |
|---:|---|
| `1` | any `violated` |
| `2` | any `inconclusive` (and no violation), **or the claims file failed validation** |
| `3` | any `confirmed_with_caveats` (and none of the above) |
| `0` | everything `confirmed`, no caveats |

**A gate written `verify-claims … || exit 1` fails on exit 3.** That is
deliberate and fail-closed. If you want caveated verdicts to pass, accept `3`
explicitly — do not widen the gate to "anything non-zero is fine", which would
also swallow `1` and `2`.

`2` is the one most often mistaken for success in a shell pipeline. It means
the tool did not check your claim.

## The caveat vocabulary

A `confirmed_with_caveats` verdict carries `caveats[]` in the JSON envelope,
each with a `kind`. These are the kinds the tool can emit:

| `kind` | what it tells you |
|---|---|
| `user_supplied_sanitizer` | A sanitizer **that did not ship with hypergumbo** — from `--taint-sanitizers`, the claims file's `extra_catalogs:`, or your `taint_sanitizers.d/` — is credited with removing a flow that would otherwise have been reported. The tool cannot check that assertion; it takes that file's word that the named function neutralises the taint. |
| `displaced_shipped_entry` | A catalogue entry hypergumbo ships was **replaced** by one the repository supplied, and the replaced entry is the kind that could have produced evidence for *this* claim. |
| `opaque_boundary` | The claim held everywhere the analysis could see, and control leaves the process at named call sites whose launched program is not in the edge set. |
| `untyped_receiver` | Named call sites reach a method the catalogue declares **for this boundary** through a receiver whose type could not be determined — e.g. `sock.sendall(payload)` where `sock` is an unannotated parameter. The flow could be neither constructed nor ruled out. |
| `unknown_receiver_scope` | The scope statement for the whole verdict: a clean result is **closed-world over the receivers the analysis could type**, reported with a count, a denominator and the distinct method names. Unlike the row above it is not boundary-scoped, because matching a method *name* against a catalogue is exactly what an untyped receiver makes meaningless. |
| `analyzer_method_call_blind` | The verdict rests on a language whose analyzer **cannot see external instance-method calls at all**. Declared, not inferred. |
| `analyzer_suppressed_methods` | The verdict rests on a language whose analyzer deliberately declines to model some method names, and the catalogue declares some of those names as I/O sinks. |
| `analyzer_construct_blind` | The verdict rests on a language whose catalogue declares rows that source reaches by a construct which is **not a call**, so the analyzer emits no edge for them at all. Declared and dated, and derived against the shipped catalogue at render time, so the rows named are the ones your catalogue actually carries. **No language declares one as of 2026-09-10** — the only entry there has ever been was javascript's handler assignment (`ws.onmessage = handler`), retired by WI-dosuh when the analyzer learned to emit the registration edge — so this caveat cannot currently appear on any verdict. It is documented because the shape recurs: the next analyzer to meet a catalogued row reached only by a construct it does not model declares it here rather than leaving the verdict silently closed-world. |
| `unreached_sink_shapes` | The claim's sink zone is `code_execution` or `dom_injection`, and in a language present in the repository some shapes of that zone emit no call edge — `new Function(s)`, a string `setTimeout`, an `innerHTML` assignment. The verdict holds for the sinks the analysis could see and names each shape it could not. (A language with **no** sink in the zone withholds the verdict instead: `inconclusive`.) |
| `higher_fidelity_available` | A higher-fidelity analyzer for a language in this repository is **installed on this machine and was not used** (e.g. rust-analyzer without `--backend rust-analyzer`). |

## What a clean verdict does not mean

### The catalogue is stdlib-scoped, on purpose

The shipped `io_primitives` catalogues cover **standard-library I/O only**, and
third-party libraries are **not** detected transitively. `requests`, `httpx`
and `urllib3` have zero rows in the Python catalogue. The reason is structural
rather than a backlog: hypergumbo analyses *your* repository, not
`site-packages`, so there are no edges into a third-party client's internals to
follow down to the stdlib call it eventually makes.

If your project's egress goes through a third-party client the tool ships no
rows for, a claim about the network boundary will be clean because the tool
never saw the call. Eight community overlays ship in the wheel and load by
default — Python HTTP clients and web/ORM, Go web frameworks and x/sys+gRPC,
Elixir web and DB, Haskell network and extras, SwiftNIO and logging — and every
run that uses them says so on stderr, because hypergumbo does **not** vouch for
those rows (ADR-0061; see [CATALOGUES.md](CATALOGUES.md)). They make third-party egress *visible*; they never
license a clean verdict, so a call they classify still counts as unexamined and
no claim is confirmed on their strength. For anything they do not cover, supply
your own overlay in `$XDG_CONFIG_HOME/hypergumbo/io_primitives.d/` or via
`--io-primitives`; `--no-default-overlays` omits the shipped ones.

### A language with no taint catalogue is not verified

Every run prints the languages it has no taint-flow catalogue for:

> *Note: no taint-flow catalog for language(s): … Claims touching these
> languages are NOT actually verified — taint-flow has no sources/sinks to
> trace. Treat 'confirmed' verdicts on these languages as inconclusive.*

Read it. A repository that is 90% one of those languages produces a clean
verdict that means almost nothing.

Such a language also **withholds** a clean verdict from every taint claim over
built-in labels (`untrusted_input`, `host_secret`, …), since any language can
produce that data. A label **your own source catalogue declares** is scoped
instead (WI-rusil): a no-catalogue language counts against it only if the
label is declared there, or code the flow can be in calls into it directly. A
verdict that set a language aside names it in its details. The scoping trusts
your catalogue's list of languages: a label declared only in python says the
flow cannot start anywhere else.

### A `module_completeness` grant in your overlay turns a gate OFF

An overlay entry marked `completeness: complete` is a **closed-world claim**:
it asserts that an unmatched call into that module is an examined negative, and
it disables the uncovered-module gate for it. It is the most powerful line you
can write in an overlay. Every grant is disclosed **by module name** in the
run's provenance output, not merely as the file it came in — check that list
against what you meant to claim.

The **shipped catalogues make the same claim** — python's `module_completeness`
block carries over a hundred dated audits — and a clean verdict that rests on
one says so: the provenance block's `load_bearing_grants` (and the
`NOTE: the coverage gate PASSED these modules on completeness grants` lines in
text mode) name, per language, the modules your code called into that no row
classified and a grant declared examined. Every confirmed verdict in the run
rests on them. It lists only what *this* run's gate passed on, shipped or
overlay, so it stays short; the full audit list, with dates, is the
catalogue's `module_completeness` block.

### A source outside any walked function is never data-flow adjudicated

The ADR-0017 §3a walk is **intraprocedural**. Its guard is "is the source's
enclosing symbol a function we built a CFG for", and a value read at **module
top level** has no enclosing function — its source anchor is the file itself.
Such a flow comes out `analysis_method: structural` no matter how good the
extractor is, and no per-language capability improvement can change that. It is
a property of the analysis, not a wiring gap.

It is not a rare shape. On the five-repository census in
[measurement 0005](measurements/0005-taint-precision-after-vocabulary-split.md)
(caddy, mitmproxy, poetry, express, apollo-server), **10 of 37 reported
situations (27.0%) and 17 of 170 rows (10.0%)** were anchored on a file rather
than a function, and **every one of them was labelled `structural`**. The
concentration is very uneven — express 3 of 3, caddy 0 of 16 — because
module-level configuration reads are idiomatic in JavaScript and Python and
rare in Go.

`dataflow_coverage` publishes capability per **language**. This limit is
per-**flow** and applies to every language equally.

### A partially parsed file is read from the parser's error recovery, and no verdict consults it

When tree-sitter cannot parse part of a file, the analysis carries on from its
error recovery and lists the file in `limits.failed_files` with a reason
starting `partial_parse:`. **No verdict reads that list.** A clean verdict does
not change, gain a caveat or lose its exit code because a file it depends on
parsed only partly. If your claim rests on particular files, check the list
yourself.

A caveat was considered (WI-vufur) and has not been added, because no rule we
measured tells a verdict that could be affected from one that could not:

- **In C and C++ a partial parse is common.** crun: 65 of 127 C files. sherpa-onnx:
  132 partially parsed files of all languages, even after WI-somod removed the
  rows that came from the wrong grammar reading a header. A caveat on "any partially parsed file" would sit on almost
  every verdict there.
- **Restricting it to the files a taint walk passes through does not narrow it
  enough.** On crun, the functions reachable from `host_secret` sources span 30
  of the 66 partially parsed files, and every built-in label on crun and on
  sherpa-onnx reaches at least one.
- **The size of the damage does not predict what was lost.** crun's
  `signals.c` and `mount_flags.c` and AFNetworking's `AFURLSessionManager.m`
  are wholly inside an error region, and every function and method in them is
  still extracted. Meanwhile AFNetworking's `AFHTTPSessionManager.h` loses all
  four of its `@property` declarations, and no error node surrounds them: the
  parser misread the `@interface` block and recovered the properties as
  top-level expressions.

What the list is reliable for: which files parsed only partly, and how many
damaged nodes each has. It is not a measure of what the analysis missed in
them.

The phoenix partial parses (112) are all generator templates: `.ex` files full
of `<%= %>` EEx markup, which are not Elixir.

### `analysis_method` is not a confidence score

The label records **how the flow was included**, not how likely it is to be
real:

- `ddg` — the walk confirmed a data dependence;
- `ddg_mixed` — reaching-definition data covered the source function, but the
  walk did not confirm. That covers three different facts, and the finer
  `walk_verdict` field (on every JSON evidence row, and in the text view's
  "§3a walk verdicts" line) says which: `not_attempted` (a guard above the walk
  failed, so it never ran), `escaped` (it ran and lost the value), or
  `unconfirmed` (it ran to exhaustion and found no dependence; such a flow is
  removed, so it never survives into a report). **Most `ddg_mixed` rows never
  ran a walk at all**: on measurement 0007's cohort re-run on 2026-09-22,
  `not_attempted` was 242 of 282 `ddg_mixed` rows (85.8%) by default and 551 of
  625 (88.2%) with `--include-non-production-sources`, two thirds of them
  stopped by `cross_function` (source and sink in different functions, which
  the intraprocedural walk cannot follow by construction);
- `structural` — no reaching-definition data was available.

Measured three times now, on three different populations, `ddg_mixed` scores
**below** `structural` for precision. Do not filter a report by
`analysis_method` expecting to keep the real findings: on measurement 0005,
keeping only `ddg` would have kept 3 flows and discarded **7 of the 8** true
positives.

## What a `violated` verdict is worth

Precision — how many reported violations are real — is measured on real
repositories and published, not asserted. The current figure is
[measurement 0005](measurements/0005-taint-precision-after-vocabulary-split.md):
**21.6% per situation, 10.0% per row** on a five-repository census, adjudicated
against source by four independent readers plus an adversarial pass.

Treat a `violated` verdict as **a place to look**, not as a finding. The
dominant false-positive mechanisms, in order, are: the sink is reached across
the call graph while nothing on the route carries the value; source and sink
merely co-located in one function; and the source value reaching a branch
condition that guards the sink rather than being one of its arguments.

Precision says nothing about **recall**. A repository with one reported flow
and forty real ones scores 100%.

Three caveat kinds ride a `violated` verdict rather than a clean one, because
they qualify a reported finding:

| `kind` | what it tells you |
|---|---|
| `choice_shaped_source` | Some evidence rows are rooted at a source whose value the far side *chose* from a constrained set rather than authored, so the finding can only be a selection, never an injected payload. |
| `withheld_community_sanitizer` | Some reported flows pass through a sanitizer from a **community** catalogue file — third-party rows hypergumbo ships without maintaining (ADR-0061) — which was not credited. Vouched for, it would have cleared them; the evidence rows name it under `withheld_sanitizers`. To vouch for one, copy its file into `$XDG_CONFIG_HOME/hypergumbo/taint_sanitizers.d/` and delete its `provenance: community` line: the flow is then cleared under `user_supplied_sanitizer`. |
| `withheld_community_summary` | This run's call graph calls function summaries from a **community** file that would *end* a taint branch (e.g. testify's `require.*`), and they were not allowed to: an unmaintained row may not remove a finding. Run-scoped, so it may name a summary no reported flow crossed. To vouch for one, copy its file into `$XDG_CONFIG_HOME/hypergumbo/function_summaries.d/` and delete its `provenance: community` line. |

## Related

- [`docs/hypergumbo-spec.md`](hypergumbo-spec.md) — the `verify-claims` design contract.
- [`docs/adr/0016-io-boundary-analysis.md`](adr/0016-io-boundary-analysis.md) — the boundary vocabulary and catalogue scope.
- [`docs/measurements/`](measurements/) — every published precision and coverage measurement.
- [`docs/adr/0047-catalogue-scope-and-user-visible-homes.md`](adr/0047-catalogue-scope-and-user-visible-homes.md) — what hypergumbo vouches for, and where a user's own catalogue rows live.
