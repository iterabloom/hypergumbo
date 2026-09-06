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
| `user_supplied_sanitizer` | A sanitizer **the analysed repository declared** (via `extra_catalogs:` or `--taint-sanitizers`) is credited with removing a flow that would otherwise have been reported. The tool cannot check that assertion; it takes the repository's word that the named function neutralises the taint. |
| `displaced_shipped_entry` | A catalogue entry hypergumbo ships was **replaced** by one the repository supplied, and the replaced entry is the kind that could have produced evidence for *this* claim. |
| `opaque_boundary` | The claim held everywhere the analysis could see, and control leaves the process at named call sites whose launched program is not in the edge set. |
| `untyped_receiver` | Named call sites reach a method the catalogue declares **for this boundary** through a receiver whose type could not be determined — e.g. `sock.sendall(payload)` where `sock` is an unannotated parameter. The flow could be neither constructed nor ruled out. |
| `unknown_receiver_scope` | The scope statement for the whole verdict: a clean result is **closed-world over the receivers the analysis could type**, reported with a count, a denominator and the distinct method names. Unlike the row above it is not boundary-scoped, because matching a method *name* against a catalogue is exactly what an untyped receiver makes meaningless. |
| `analyzer_method_call_blind` | The verdict rests on a language whose analyzer **cannot see external instance-method calls at all**. Declared, not inferred. |
| `analyzer_suppressed_methods` | The verdict rests on a language whose analyzer deliberately declines to model some method names, and the catalogue declares some of those names as I/O sinks. |
| `analyzer_construct_blind` | The verdict rests on a language whose catalogue declares rows that source reaches by a construct which is **not a call** (`ws.onmessage = handler`), so the analyzer emits no edge for them at all. Declared and dated, and derived against the shipped catalogue at render time, so the rows named are the ones your catalogue actually carries. |
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
never saw the call. Two community overlays now ship in the wheel and load by
default — `python-http-clients.yaml` and `go-web-frameworks.yaml` — and every
run that uses them says so on stderr, because hypergumbo does **not** vouch for
those rows (ADR-0047). They make third-party egress *visible*; they never
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

### `analysis_method` is not a confidence score

The label records **how the flow was included**, not how likely it is to be
real:

- `ddg` — the walk confirmed a data dependence;
- `ddg_mixed` — the walk ran and did not confirm;
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

## Related

- [`docs/hypergumbo-spec.md`](hypergumbo-spec.md) — the `verify-claims` design contract.
- [`docs/adr/0016-io-boundary-analysis.md`](adr/0016-io-boundary-analysis.md) — the boundary vocabulary and catalogue scope.
- [`docs/measurements/`](measurements/) — every published precision and coverage measurement.
- [`docs/adr/0047-catalogue-scope-and-user-visible-homes.md`](adr/0047-catalogue-scope-and-user-visible-homes.md) — what hypergumbo vouches for, and where a user's own catalogue rows live.
