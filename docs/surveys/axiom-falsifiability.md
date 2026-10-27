<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Survey: Can Each Declared Axiom Be Falsified on Real Output?

- **Date:** 2026-10-27, on dev `7207fb0ecf`.
- **Tracker:** WI-ziruv.
- **Tests:** `packages/hypergumbo-lang-mainstream/tests/test_axiom_falsification.py`, `packages/hypergumbo-lang-extended1/tests/test_axiom_falsification_extended1.py` and `packages/hypergumbo-core/tests/test_medium_families.py`.
- **Informed:** WI-sukug, WI-gisip, WI-mupal, WI-kuzoh and WI-fanad (filed by this survey); WI-dofiz and WI-jofud (filed closing its recorded gaps, later on 2026-10-27); a note on INV-fuhin (the ecosystem half).
- **Strict xfails naming items filed before this survey:** WI-higik, INV-fuhin, WI-dagun, WI-savoh, WI-zakil, WI-kufos, WI-sofam, WI-vogos.

## Why this is a survey, not an audit-findings document

The verdict here is about an axiom's *testability*: mechanically testable, partly testable, or needing per-row judgment. It is not a CANONICAL / FOLD / DEPRECATE-NO-FOLD verdict on a value. [`docs/audits/README.md`](../audits/README.md) §"Scope" sends a different verdict shape here.

## Context

A declared axis ([ADR-0024](../adr/0024-axis-declaration-template.md)) has four artifacts: an axiom, a registry, a drift linter and a property test. Together they stop a new *spelling*, meaning an unregistered value written as a literal by a producer the linter can read. They do not stop the *meaning* from drifting. A registered value can be applied to the wrong thing, and every axis ADR has a one-sentence axiom that nothing ran against output.

Where verdicts stayed fixed, something tested meaning:
- INV-zivah: every boundary has a taint mapping.
- Audit 0018's type-family predicate.
- INV-suhob's activation drift test.

Where they flipped, nothing did:
- The ADR-0050 medium reading.
- The ADR-0041 project frame.
- Pony's computed kinds.

This survey takes each declared axiom and asks whether it can be falsified mechanically. If it can, a test tries to falsify it on real output. If it cannot, the survey says what a reviewer would sample instead.

## Methodology

1. **Enumerate the axioms.** That means every axis in `multi_value_field_axis._known_axes()`, plus the three axiom-bearing ADRs that are not registry axes: ADR-0039, ADR-0041 and ADR-0061. The verify-claims choice-shaped-source rule is included because it is stated as a rule over output.
2. **Classify each one.** An axiom whose sentence implies a property of the output the tool emits is *testable*. Where only a necessary condition can be checked, it is *partly testable*. Where applying it needs the language's documentation or the analyst's reading of a row, it *needs per-row judgment*.
3. **Test each testable consequence on real output.** Small fixtures, written into the test file, go through `run_behavior_map` (the `hypergumbo survey` pipeline) or `cmd_verify_claims`. The rules:
   - Each test first asserts that its fixture reached the thing it is about.
   - Each property is one pure checker, with a hand-built counterexample proving the checker can report one.
   - A property that fails on day one fails on a filed item and ships as `xfail(strict=True)` naming it. It XPASSes, and so fails the suite, when the item is fixed. It is then that item's closure evidence.
4. **Mutation controls.** Each live property was broken in its production code, in a detached worktree with a pinned `PYTHONPATH`, and its test went red. Rows 1–8 are from the survey; rows 9–15 from closing its recorded gaps, where a "fix" row applies the owning item's fix shape and shows its strict xfail flip:

   | # | Mutation | Tests that went red |
   |---|---|---|
   | 1 | Visibility default → `private` | Every default row; the WI-gisip rows XPASSed |
   | 2 | Every post-pass gets a silence reason | The silence test on all 16 surveys |
   | 3 | No `directness` stamp | Imported-externals stamp test |
   | 4 | The shared unresolved emitter says `ast_call` | java, php and typescript; the eight WI-sukug rows XPASSed. Python has its own emitter, so it stayed green |
   | 5 | `.ts` reads as `javascript` | The language test on both TypeScript surveys |
   | 6 | Java keys a lower-case receiver by its variable name | The Java module-key test |
   | 7 | Framework enrichment rewrites `kind` to `route` | Python and Java kind tests. TypeScript's concept comes from its analyzer, not enrichment, so it stayed green |
   | 8 | A command-line catalogue is listed twice | The tier-once test |
   | 9 | `all_boundaries_for` drops `env_read` | All 13 environment members of the medium families |
   | 10 | Fix WI-sofam: c `printf` / `putchar` rowed `logging` | Their two medium xfails XPASSed |
   | 11 | A manifest-declared CLI reads `main_function` | The CLI route twin |
   | 12 | Fix WI-dofiz's shape: the `application` concept emits nothing | The Flask `main_function` xfail XPASSed |
   | 13 | `taint_sinks.d/` not passed to the loader | The ruling 7 test |
   | 14 | Fix WI-kufos (2): the choice caveat reads the group's sources | Its xfail XPASSed |
   | 15 | Fix WI-kufos (1): INV-dudal reads the group's sinks | Its xfail XPASSed |

   The checker controls and the two verify-claims reach controls stayed green under every mutation.

## The classification

| Axiom (where stated) | Verdict | Test, and what it found |
|---|---|---|
| **edge-type** — "`edge_type` names the relationship that produced the edge" ([ADR-0023](../adr/0023-edge-type-relationship-not-endpoints.md) typing rule) | Partly testable | The relationship is not in the output, so only a necessary condition can be checked: one endpoint-kind partition should carry one edge type. **Existing:** the runtime-coherence ratchet in `test_validation_report_empty.py` (`runtime_coherence.py`, 7 allowlist entries). No new test. |
| **symbol-kind** — "`Symbol.kind` names the source-language syntactic construct that the symbol represents" ([ADR-0027](../adr/0027-symbol-kind-language-construct-only.md) §2) | Testable (two consequences) | **New:** a framework role (flask route, celery task, django Model, spring `@RestController` / `@GetMapping`, an express handler) does not move the kind. Holds on python, java and typescript. **New:** every emitted kind is on `language_construct`. Holds on python, java, typescript, zig, nim and gleam. **Pony fails ('actor', 'primitive'): strict xfail WI-higik.** **Candidate falsification, unruled:** route-marker nodes are `kind=function` whatever they stand for, filed as WI-mupal for a ruling. No test until it is ruled. |
| **evidence-type** — "names the inference pathway ... Properties of the dst's resolvedness ... are queried from siblings (`Edge.is_resolved`)" ([ADR-0028](../adr/0028-evidence-type-inference-pathway-only.md) §2) | Testable (the resolvedness clause); per-value judgment (the "pathway" clause, audits 0008 / 0012 / 0014) | **New, metamorphic:** two bare calls of one construct, one in-tree and one undefined, must carry one label. Holds on python, java, typescript and php. **Falsified on go, swift, kotlin, rust, ruby, csharp, scala and cpp:** the resolver arm emits `ast_call` and the shared unresolved emitter emits `ast_call_direct`. Strict xfails, filed as **WI-sukug**. This is the counterexample to WI-molit's KEEP premise ("set at detection time"). |
| **io-boundary** — "A row's primary boundary names the medium data crosses at this call site ... A `simultaneous` second boundary may record a reading" ([ADR-0050](../adr/0050-io-boundary-axis.md) §1) | Per-row judgment for the primary; partly testable for its consequences | The medium is read from each language's documentation (see "The judgment axioms" below). **Existing checks of consequences:** `direction` on every `data_crossing` value; `simultaneous_boundaries_for` rejects disagreeing rows; in-process cells and socket setup are not boundaries; every multi-boundary primitive has a reason; the URL rows are simultaneous. **Finding:** of the 39 primitives on the 35 shipped `simultaneous: true` rows, 33 pair two *crossings* (ipc_recv + subprocess: 23), which §1's second-boundary sentence does not cover. Filed as **WI-kuzoh** for a ruling. **New, cross-catalogue:** `test_medium_families.py` writes down 11 media (environment, a whole file, deleting a file, host name, host clock, DNS, connect, accept, server launch, own stdout, own stdin) with 99 members across 13 catalogues, and requires every member to carry its medium's boundary. 91 hold. **8 disagree:** c `printf` / `putchar` are `fs_write` (WI-sofam); javascript `http.createServer`, `net.createServer` and `Deno.listen` are `net_recv` where four languages are `net_listen` (WI-vogos); elixir `IO.gets` / `IO.read` lack the `ipc_recv` arm of the erlang rows they wrap, and `IO.inspect`, a stdout write, is `fs_read` (filed as **WI-jofud**). Strict xfails. |
| **module-key** — "names the static owner path of the called symbol ... not the receiver's variable name, not a set of candidates, and not a marker for the absence of an answer" ([ADR-0051](../adr/0051-module-key-axis.md) §1) | Partly testable | **New, metamorphic:** one method through two differently-named locals of one recovered type keeps one key, and the key is neither name. Holds on python, java, go, kotlin, rust and swift. The `swift.py:1223` fallback chain ADR-0051 cites is gone. "Not a set / not a sentinel" is the documented 7.8% non-conformance (WI-marok), not retested. Whether a key is an owner path is judgment. |
| **io-primitive-kind** — "`method` iff it is called on an instance of `module`; `function` iff it is called on `module` itself ...; `attribute` iff it is read" ([ADR-0059](../adr/0059-io-primitive-kind-axis.md) §1) | Testable on the catalogues | **Existing, the model:** the shrink-only `KNOWN_NONCONFORMING_ROWS` ledger (29 rows, WI-ziviv), its size pin, and the JVM-static and Swift-constructor checks in `test_io_primitive_kinds.py`. |
| **language** — "names the source language in which this Symbol's declaration appears, or `None` if the Symbol does not represent a declaration" ([ADR-0031](../adr/0031-symbol-language-reshape.md)) | Partly testable | **New:** a node in a source file that carries a language carries that file's language. Holds on all 16 fixture surveys. Whether a node *is* a declaration is judgment: route markers carry `language` (WI-mupal). **Existing:** `cross_field_coherence` (exactly one of language / protocol_origin). |
| **pass-id** — the registered analyzer and linker names plus the documented synthetic passes (`catalog.py`; not labelled an axiom) | Testable | **Existing:** the `origin_run_id` foreign-key predicate in the corpus ratchet, and `scripts/check-pass-id-agreement`. A probe on four fixtures found every node and edge's `origin` naming its run's pass. No new test. |
| **protocol-origin** — "names the protocol or framework family that the Symbol is a synthetic stand-in for" (ADR-0031) | Partly testable | **Existing:** `cross_field_coherence`. Whether a node is a stand-in is judgment (WI-mupal). |
| **qualified-name** — "a fully-qualified dotted/scoped identifier" (`qualified_name_axis.py`) | Partly testable | **Existing:** `_check_qualified_name_separator` (a value with another language's separator and not its own). |
| **callable-signature** — "preserves the declaration surface of a callable ... for display ... never parsed back out of this string" ([ADR-0058](../adr/0058-callable-signature-axis.md) §1) | Testable against consumers, not output | **Existing:** `test_no_undeclared_value_parser_exists` (static) and the closed backlog of 8 legacy parsers. |
| **entrypoint-kind** — "`kind` names WHAT the entrypoint is ... `evidence_type` names HOW it was detected" (`entrypoints.py`) | Partly testable | **Existing:** `test_entrypoint_detection_parity`. **New, metamorphic:** one entrypoint by two routes keeps one kind: a CLI by `@click.command` (`framework_pattern`) and by `[project.scripts]` (`manifest_declared`); a flask route by decorator and by `add_url_rule`; an express handler named and inline. Holds on all three. **New:** a kind whose definition names a callable (`main_function`, `cli_command`, `http_route`) targets a function or method. **A Flask `app = Flask(__name__)` variable is a `main_function` entrypoint:** the `application` concept (19 framework files) is mapped to `main_function`, the overload WI-tuvun removed for files. Strict xfail, filed as **WI-dofiz**. |
| **visibility** — "one computed canonical visibility level" (ir.py, INV-jusot) | Testable per language | **New:** 24 rows, each decided by the language's own rule. 19 hold (python, java public/private/protected, go, kotlin, csharp keywords, rust `pub`, typescript `public`). **5 falsified:** java package-private, csharp's default-private member and rust's non-`pub` fn all read `public`, and typescript `private` / `protected` read `public`. One language-agnostic default; filed as **WI-gisip**. |
| **pass-silence-reason** — "names WHY a pass produced no symbols and no edges" (`pass_silence.py`; ADR-0054 / ADR-0056) | Partly testable | **New:** a run that emitted output carries no reason, and `no_candidate_files` is claimed only by a run that analysed no file. Holds on all 16 surveys. Whether a given reason is the *true* why needs the truth, so it is judgment; WI-bubig (candidate-file presence) is still unreproduced. A first probe flagged `framework-patterns` (0 nodes, no reason). That was the instrument's error: an enrichment run that matched records `""` (not applicable) by design. |
| **confidence** — "the producer's evidence-derived estimate that the relationship exists" ([ADR-0039](../adr/0039-confidence-separation.md)) | Partly testable | **Existing:** bands via the `confidence_range` validator and `scripts/check-confidence-sites`. Ruling 6's interpolation was fixed by WI-sakum. Whether the estimate is calibrated needs ground truth ([ADR-0048](../adr/0048-taint-precision-benchmark-frame.md)'s frame). |
| **tier** — "`tier` names the supply-chain distance, from the project under analysis, of the code a node stands for" ([ADR-0041](../adr/0041-supply-tier-purity.md) §1) | Testable | **Existing, the model:** `test_in_tree_declaration_tier.py` (INV-kutub), where one package gets one tier across five ecosystems. INV-nuzas's workspace fixtures were re-run on 2026-10-27; its residuals are WI-homih, WI-halol and WI-silam. |
| **directness / ecosystem** — "records the declaration relationship between the project's manifests and an external dependency" / "names the provenance class of an external dependency's distribution channel" (ADR-0041) | Testable | **New:** every node `supply_chain_summary` counts carries both stamps. Imported externals hold. **Declarations carry neither key: strict xfail INV-fuhin** (the ecosystem half is noted there). |
| **catalogue tiers** — seven rulings ([ADR-0061](../adr/0061-catalogue-tiers-for-every-family.md)) | Testable per ruling | **Ruling 1** (a command-line file is `yours`): **new, strict xfail WI-dagun.** **Ruling 2** (never cleaner): the existing sanitizer test, plus **new, metamorphic:** a community sink file with `trust_level: trusted` vs `untrusted` must give one verdict. Its control is the same trust from your own file clearing the flow. **Strict xfail WI-savoh.** **Rulings 3, 4 and 6:** existing tests. **Ruling 5** (each file disclosed by tier): **new**, a command-line file is listed under exactly one tier; holds. **Ruling 7** (the persistent taint home): **existing**, `test_taint_channels_are_read.py` (WI-mimap: each of the three channels changes a verdict, is listed under `yours`, and has a no-file control). **New:** one `taint_sinks.d/` file is read, as `yours`, for two unrelated repositories that name nothing; holds. |
| **choice-shaped source** — a finding "rooted at a source whose value the far side CHOSE from a constrained set" (`verify_claims.py`, `CHOICE_SHAPED_SOURCE_NAMES`) | Testable | **New:** the `choice_shaped_source` caveat appears only on an `untrusted_input` claim. Its control: an `accept()` peer carries it. **`gethostname` (host_info_read) under a host_description claim carries it: strict xfail WI-zakil part 3.** The checker reads the claim, not a witness row. **New, metamorphic, WI-kufos's shape reproduced end to end:** a collapsed (`ddg_mixed`) row stands for a group, so swapping the order of two calls must not change what verify-claims says about it. (2) `recv` and `accept` reaching `subprocess` through a helper collapse into one row; the caveat appears only when `accept` is first. (1) A secret reaching a yours `dom_injection` sink and `document.write` through one helper: with `document.write` first, INV-dudal withdraws the qualification (`inconclusive`); with the yours sink first it does not (`confirmed_with_caveats`), the false-clean direction. Both strict xfails WI-kufos. |

## The judgment axioms: what a reviewer samples

- **ADR-0050, the primary boundary.**
  - **Sample:** per catalogue, sample rows from each boundary section. For each, read the primitive's documentation and ask what medium the data crosses at the call, and in which direction.
  - **The known counterexamples are medium cases:**
    - WI-kalol: a go `net.Conn` used over a Unix socket.
    - WI-foloh: python's `asyncio.StreamReader` fed by a pipe.
    - WI-zidam: argv, and `document.cookie`.
  - **The partial mechanical check** is "the same medium gets the same boundary across catalogues". Its medium map per primitive family is the judgment itself, so `test_medium_families.py` writes it down where it can be reviewed: each family names a medium, direction and boundary, and each member is a primitive whose documentation says it crosses that medium at the call, always or with default arguments. Primitives whose medium is set by a required argument or receiver (`unistd.write`, `fprintf`, `PrintStream.println`) are not members, nor are rows kept for a measured reason other than the medium (go `exec.Command`, rust `Command::new`, INV-dukam). `test_unix_listener_is_ipc_listen.py` pins one more family (Unix-socket listeners). Absence is not tested: a medium a catalogue does not row (java has no host-name row; swift none for stdin) is coverage, not a disagreement.
- **ADR-0028, whether a value is a pathway.** Per-value audits 0008, 0012 and 0014 did this; a new value goes through the same four leakage tests.
- **ADR-0051, whether a key is an owner path.** Sample `external_symbol` ids per language, and check each module slot against the language's import vocabulary. WI-marok holds the measured 7.8%.
- **ADR-0039, calibration.** The [ADR-0048](../adr/0048-taint-precision-benchmark-frame.md) benchmark frame is the instrument. A band check is not.
- **pass-silence, whether a reason is true.** Construct the missed case (WI-bubig needs a bare virtualenv) and check the reason against it.

## Outside both axes and this survey

These were named in the 2026-10-26 review as out of reach, and they still are:
- **Free-text field declarations shown false** (WI-fuzat). That is WI-kosup §C.
- **Consumer-side subsets of a vocabulary** (WI-sibup, WI-totam (c), WI-sojok). That is WI-kosup §D.
- **Consumers that disagree about one fact** (WI-votat, WI-fakuh, WI-bobuf).
- **Contracts never written, so there is nothing to falsify:** when anchored code runs, and what an `instantiates` edge carries (WI-fakuh, WI-tibib, WI-kakup).

Found in passing and filed: WI-fanad. Ruby drops a method call on a local typed to an external class (`http = Net::HTTP.new; http.get`).

## Related

- [ADR-0024](../adr/0024-axis-declaration-template.md), the axis template whose fourth artifact this extends from vocabulary to meaning.
- WI-kosup, the companion sweep for write-only fields, knobs and readers with no producer.
- [`test_in_tree_declaration_tier.py`](../../packages/hypergumbo-lang-mainstream/tests/test_in_tree_declaration_tier.py) and `io_primitive_kinds.KNOWN_NONCONFORMING_ROWS`, the two existing falsification tests that served as models.
