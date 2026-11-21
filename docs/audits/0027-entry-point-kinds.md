<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# 0027 — Entry-point kinds: one kind carries three concepts, and the sets that read it

```yaml
kind: concept_leak_pairs
date: 2026-11-20
pinned_tree: fafaa4a8f
trigger: WI-holud (owner ruling 2026-11-20 - the domain of the overdue Fundamental Concept Audit)
outcome: confirmed
```

## Suspicion

The entry-point kind `main_function` carries three concepts: a language's convention `main`, the `application`
concept (a framework's application object: Flask(), FastAPI(), a Tornado `Application` subclass, IOApp, ZIOApp,
SwiftUI `App`...) and the `entrypoint` concept (argparse, fire, kong, OptionParser, akka `newServerAt`). Every
consumer that keys on the kind treats all three as "a program start". The tie order of WI-fupap (rank 0 manifest
script, rank 1 `PROGRAM_START_KINDS`, rank 2 the rest; `entry_kind_tie_rank`) is the visible one: on mitmproxy a
"Tornado application" sorts before the Tornado controllers.

Falsifiable: `application` / `entrypoint` could have had no live producer, could have landed only where a program
starts, or no consumer could have depended on the kind alone.

Scope: the whole `EntrypointKind` vocabulary (49 values) and the framework YAMLs' concept -> kind mapping, with
`main_function` as the lead suspect; `cli_main`, `main_guard`, `manifest_declared`, the framework-flavoured kinds;
`confidence` vs rank (already filed as WI-duzif, referenced here, not re-filed).

## Method

Inventory (playbook Steps 2 and 4.5): every pattern of the 111 framework YAMLs, mapped through the concept ->
kind table transcribed from every arm of `entrypoints._detect_from_concepts`; every kind's producers and its
membership in each hand-written kind set, by AST walk (`kind=` keyword, `target_kind =` assignment, positional
construction; no kind is ever built from a string: zero `EntrypointKind(`, `EntrypointKind[`, `getattr`,
`__members__` in `packages/*/src` or `scripts`, which closes shapes 4-6).

Measurement: PR 4's 23 entry lists (historical, PR 4's tree), and fresh surveys on the pinned tree, one at a time:
httpx, alertmanager, poetry, mitmproxy from PR 4's corpus, and full-stack-fastapi-template and
swift-argument-parser from outside it (they hold `application` rows the corpus barely exercises). Each entry's
concept is read from its own fields (kind, `meta.evidence_type`, the label its detector arm writes). The
counterfactual "narrow rule" (rank 1 only for the convention main, `main_guard`, `cli_main`) is a stable re-sort,
so it gives the best position an entry could reach in rank 2. Three constructed probes. Scripts, raw output and
probes: `~/hypergumbo_lab_notebook/fast_smart_test_build_11172026/audit/` (RESULTS.md first).

Per-pair verdicts use the playbook's Step 6: DEPRECATE (split, delete or rename) · DOCUMENT · KEEP WITH A TRIGGER
THAT CAN FIRE. T1-T4 are the four leakage tests (property derivability, apex/peer, construct vs relationship,
mechanism vs category).

## Inventory

- **Who maps to `main_function`.** 54 YAML rows + 1 analyzer. `main_function` concept: 21 rows, all in
  main-functions.yaml. `application` concept: 33 rows in 19 files. Read one by one:
  - 7 rows are a program start by construction: http4s `IOApp` / `IOApp.Simple`, zio `ZIOApp` /
    `ZIOAppDefault`, SwiftUI `App`, `UIApplicationDelegate`, `NSApplicationDelegate`;
  - 2 are CLI command types, every subcommand included: `ParsableCommand`, `AsyncParsableCommand` (in
    swiftui.yaml, which loads only when SwiftUI is detected);
  - 15 are hosted application objects that a server or runtime runs: aiohttp, FastAPI, Flask, Tornado, Roda,
    Padrino, lapis, Hummingbird `ApplicationProtocol`, servant, Yesod ×2, giraffe, plumber ×2, shiny;
  - 6 are start calls made inside a `main`: Javalin, `QApplication`, scotty ×3, Yesod `warp` / `toWaiApp`;
  - 3 are Template Haskell dispatch generation: `mkYesod`, `mkYesodData`, `mkYesodSub*`.

  `entrypoint` concept: 8 rows. 2 are electron, which go to `electron_main`. The other 6 (argparse, fire, kong,
  OptionParser ×2, akka `Http().newServerAt`) are `decorator:` keys on calls. The matcher reads only
  `meta["decorators"]`; `go.py` and `ruby.py` never write it, and none of the six is a decorator in Python or
  an annotation in Scala. One analyzer emits the concept: verilog.py stamps every module that no instantiation
  names (`_TOP_MODULE_CONCEPT`), which becomes `main_function`, "Verilog entrypoint".
- **Who reads `main_function`.** It feeds seven consumer sites:
  - the tie rank (`PROGRAM_START_KINDS`, entrypoints.py:385);
  - `slice --entry auto`'s 2× `kind_boost` (cli.py:2111, :2143);
  - the Go module-binary pick (cli.py:1973);
  - the Go `cmd/` penalty (entrypoints.py:2138);
  - the INV-hosuh main dedup (entrypoints.py:1968-2003);
  - the library-export demotion's `_SEMANTIC_KINDS` (entrypoints.py:2159);
  - the sketch's "CLI & Scripts" group (sketch.py:5967).

  The per-language confidence table (`MAIN_FUNCTION_CONFIDENCE`) was measured on the `main_function` concept
  only. `application` and `entrypoint` take an unmeasured 0.90.
- **Dead vocabulary.** 24 of 49 kinds have no producer: `cli_main`, `electron_preload`, `electron_renderer`, and
  21 framework-flavoured kinds (`django_view`, `express_route`, …, `micronaut_controller`). All their producers
  were removed by `1b5d12767` (#331, "remove 26 legacy _detect_* functions"). They remain in the consumer sets:
  21 of `REQUEST_INGRESS_KINDS`' 28, 22 of `_SEMANTIC_KINDS`' 35, 1 of `PROGRAM_START_KINDS`' 3, every sketch
  group, and the schema enum (`all_known_entrypoint_kinds`).
- **Five hand-written sets over one enum, and they disagree:**
  - `electron_main` is a program start to the sketch ("CLI & Scripts") and to nobody else;
  - a manifest-declared bin is rank 0 to the tie order but gets no 2× boost in `slice --entry auto`;
  - `middleware_handler`, `error_handler`, `form`, `serializer`, `shell_script`, `html_entry`,
    `script_module`, `test_function` and `spa_bootstrap` are in no sketch group and fall through to "Other".

## Measurements

| Where | Convention main, rank 1 | `main_guard`, rank 1 | `application`, rank 1 | `entrypoint` concept | `cli_main` | Manifest, rank 0 |
|---|---|---|---|---|---|---|
| 23 lists, PR 4 tree | 99 | 68 | 3 (mitmproxy 2, kserve 1) | 0 | 0 | 20 |
| httpx / alertmanager / poetry, pinned | 1 / 4 / 1 | 0 / 0 / 1 | 0 | 0 | 0 | 1 / 0 / 1 |
| mitmproxy, pinned (143 entries) | 1 | 16 | 2 | 0 | 0 | 3 |
| full-stack-fastapi-template, pinned (50) | 3 | 0 | 1 | 0 | 0 | 0 |
| swift-argument-parser, pinned | 0 | 0 | 0 on 4,756 nodes (swiftui.yaml not loaded) | 0 | 0 | 0 |

What the `application` entries change, on mitmproxy:

- the "Tornado application" (a class, score 1.0) sits at position 3. It precedes 36 rank-2 entries of equal
  score: 24 controllers, 5 routes, 3 exports, 2 framework commands and 2 others. Under the narrow rule it moves
  to position 49 at best, and 47 of 143 positions change;
- the sketch files it under "CLI & Scripts";
- the Flask `app` (score 0.9) ties only with two routes of equal confidence and stays at 71 to 73.

Elsewhere, kserve's and full-stack-fastapi-template's FastAPI `app` (score 0.9) jump nothing. `slice --entry auto`
on mitmproxy picks a `main_guard`, not the application (PR 6 run).

## Verdicts

| Pair | T1 derivable | T2 apex/peer | T3 construct/relationship | T4 mechanism/category | Verdict | Item |
|---|---|---|---|---|---|---|
| `main_function`: convention main × `application` | Fires the wrong way. No category field separates them, only `evidence_type`, a mechanism, and the free-text label. | Fires. Apex "a program start" (`PROGRAM_START_KINDS` docstring, sketch group, slice boost); peer "a language's `main()`" (label `<Lang> main()`, per-language confidence, Go `cmd/` penalty, WI-tuvun's "kind disambiguates the target type"). Targets are function, class and variable alike. | Fires. A function named `main` is a construct; the object a server hosts is a role. | Fires on the remedy. PR 4's narrower alternative (rank 1 only for `evidence_type == language_convention`) would read a mechanism field as a category. | **DEPRECATE**: split `application` into its own kind. Its rank and the 7 program-start-by-construction rows are the owner's call. | A, B |
| `main_function`: convention main × `entrypoint` concept | Not reached: 6 of 6 non-electron rows never fire, and 0 entries were found in 23 + 6 lists. | Fires. The concept name is the vocabulary's apex ("an entry point") used as one peer concept. | No | Fires. "The CLI framework's parse call" names how a start is recognised, not what starts. | **DEPRECATE**: delete the 6 dead rows. Give the Verilog top module and electron concepts specific names. Correct the `PROGRAM_START_KINDS` docstring and the spec, which claim the parse call is detected. | C |
| `main_function` × `main_guard` | Fires. Function vs file is derivable from the target `Symbol.kind`, which the INV-hosuh dedup reads instead of the entry kind. | No | No. Both are constructs. | Fires. Two ways of recognising one category. | **KEEP WITH A TRIGGER**. No consumer branches on the difference: every set holds both. WI-tuvun's rationale is already false for `main_function`, whose targets include class and variable. | — |
| `cli_main` × the rest | — | — | — | — | **DEPRECATE (delete)**: zero producers since #331. It is a phantom in `PROGRAM_START_KINDS`, `_SEMANTIC_KINDS`, the sketch group, and the spec (§8 auto-slice formula, §9 Sorting). | D |
| `cli_command`: manifest bin × framework subcommand × `cmd_<name>` | Fires. Separable only by `evidence_type`, which is how rank 0 reads it. | Fires. A Cargo `[[bin]]` / pyproject script is the program; a click / cobra command is a subcommand inside it. | No | Fires. Rank 0 is keyed on a mechanism field because the kind cannot say "program". | **DOCUMENT now** (the `entry_kind_tie_rank` docstring states why it reads `evidence_type`). Resolve through the role registry. The tie rank and slice auto already disagree on whether a manifest bin is a program start. | F |
| "The framework's app start": `application` (as `main_function`) × `electron_main` × `android_application` × `spa_bootstrap` | Fires. The difference is the framework, which the concept already carries. | Fires the other way: one thing under four names. | No | No | **DOCUMENT**, decided with A: four kinds, three tie ranks, three sketch groups for one concept. | A, F |
| 21 framework-flavoured kinds × `http_route` / `controller` | Fires. Framework is derivable from the concept. | No | No | No | **DEPRECATE (delete)**, together with `electron_preload` / `electron_renderer`: dead vocabulary since #331. The schema enum lists them, so removing them is the owner's call. | E |
| Mechanism-named kinds (`connectivity_based`, `script_module`, `shell_script`, `html_entry`) × category kinds | Fires for `connectivity_based`: it restates `evidence_type = connectivity_heuristic`. | Fires for `shell_script` / `script_module`: each is a file run as a program, like `main_guard`, yet sits in rank 2. | No | Fires. Named by how they are found. | **KEEP WITH A TRIGGER**, except their program-start membership, which goes to F. | F |
| `confidence` × rank | — | Fires. Base literals are set as ranking tiers ("Lower than routes, similar to main()", "Naming heuristic - lowest tier"), and `rank_score` starts from them. Only `main_function`'s values (11 languages) are measured. | — | Fires. A detection probability used as an ordering seed. | Confirmed; **already filed as WI-duzif** (Go main 0.95 → 0.90 reordered 4 of 23 lists). Not re-filed. Here the unmeasured 0.90 of `application` decides where the narrow rule would put it. | WI-duzif |

### Keep triggers

- `main_function` × `main_guard`: fires when a consumer branches on `main_guard` without `main_function`
  (`grep -n "EntrypointKind.MAIN_GUARD" packages/*/src` outside the producer arm, the dedup tuple,
  `PROGRAM_START_KINDS` and the sketch group), or when item A lands. A changes what the dedup tuple holds, and the
  tuple must then be re-derived.
- Mechanism-named kinds: fires when any consumer other than the sketch's fall-through branches on
  `connectivity_based`, `script_module`, `shell_script` or `html_entry` alone.

## Silent bugs found on the way

- **The INV-hosuh main dedup drops application entries** (entrypoints.py:1968-2003). It groups kinds
  {`main_function`, `main_guard`} by file and keeps only entries whose symbol is a `function`. The probe has an
  identical Tornado `Application` and Flask `app` in two files. In `other.py` both are entries; in `server.py`,
  which also has `def main()`, both are silently gone. The kind smuggles them into a set written for the
  guard-vs-main pair. Real-repo magnitude was not measured. → B.
- **`cli_main` is a phantom value** in `PROGRAM_START_KINDS`, which the spec documents as one of three program
  starts. → D.
- **Six `entrypoint` rows can never fire** while the code documents them as detected (the `PROGRAM_START_KINDS`
  docstring: "a CLI framework's top-level parse call"). → C.
- **`ParsableCommand` is filed under SwiftUI's `application`.** It only loads when SwiftUI is detected, so it
  fires on 0 of 4,756 nodes of swift-argument-parser. Where it does load, every subcommand conformer becomes a
  rank-1 "program start". The second half was read from code and not reproduced. → A.
- Adjacent, outside the kind field: COBOL's `program_entrypoint` concept (language-conventions.yaml:74) and
  micronaut's `scheduled` (micronaut.yaml:87) reach no arm of the detector. The concept vocabulary has no
  registry, so a near-miss name produces no entry point and no error. → G.

Code read, not reproduced: within one symbol, `added_kinds` lets only the first concept of a kind through. A symbol
carrying both `main_function` and `application` would take its label and confidence (0.95 vs 0.90) from concept
order, against the INV-liraj docstring's "the chosen kind does not depend on detector or concept order". No case
reached it: the Javalin and scotty usage rows attached nothing on probe2.

## Proposed items (the owner files and decides)

- **A. Give `application` its own entry kind.** *Owner decision.* Options:
  1. new kind `application`, ranked with routes (rank 2) and grouped with them in the sketch;
  2. the same, but the 7 program-start-by-construction rows (`IOApp`, `ZIOApp`, SwiftUI `App`, the app
     delegates) keep `main_function`;
  3. keep it as is and document the kind as "a program start or a framework application object".

  The narrower rule PR 4 offered (read `evidence_type`) is the T4 failure above. Repro: survey mitmproxy; the
  "Tornado application" is at position 3, before 24 controllers of equal `rank_score`, and in the sketch's
  "CLI & Scripts".
- **B. The INV-hosuh dedup drops non-function entries of kind `main_function`.** Key the dedup on the guard / main
  pair it was written for, not on the kind set. Not blocked on A. Repro: `probe3/` (two files with one Tornado
  `Application` and one Flask `app` each, one also with `def main()` and a guard). Both appear for `other.py` and
  neither for `server.py`.
- **C. Delete the six `decorator:` rows of the `entrypoint` concept, and give its live producers specific
  concepts.** The rows: cli.yaml argparse / fire, cli-go.yaml kong, cli-ruby.yaml ×2, akka-http.yaml. The live
  producers: Verilog top module, electron. Correct the `PROGRAM_START_KINDS` docstring and the spec. Repro: the Go
  probe with `kong.Parse` in `main` (cli-go loaded) carries no `entrypoint` concept, and `go.py` / `ruby.py` never
  write `meta["decorators"]`.
- **D. Remove `cli_main`.** *Owner decision* (schema enum). Repro: the AST producer count is 0. The member appears
  in `PROGRAM_START_KINDS`, `_SEMANTIC_KINDS`, sketch "CLI & Scripts" and spec §8 and §9.
- **E. Remove the 23 other zero-producer kinds.** *Owner decision* (schema enum; external readers not checked).
  Repro: `kind_sets.py` lists 24 zero-producer kinds; `git log -S` places their last producers in `1b5d12767`.
- **F. Declare each entry kind's role in one registry** (program start / request ingress / scheduled / reflective /
  library API / test / fallback). Derive `PROGRAM_START_KINDS`, `REQUEST_INGRESS_KINDS`, `_SEMANTIC_KINDS`, the
  sketch groups and the dedup tuple from it, with a drift test (playbook Step 6, "value-set in code"). *Owner
  decision* on membership:
  - is a manifest bin a program start to `slice --entry auto` too;
  - are `shell_script`, `script_module`, `electron_main`, `android_application` and a Verilog top module program
    starts.

  Repro: `kind_sets.py`, where `electron_main` is "CLI & Scripts" in the sketch and rank 2 in the tie order.
- **G. Concept names no detector arm reads.** Either a concept registry or a load-time warning. Repro:
  `yaml_inventory.py` lists `program_entrypoint` (COBOL) and `scheduled` (micronaut) as mapping to nothing.
- WI-duzif stands as filed.

## Not determined

- the real-repo magnitude of B;
- a repository with SwiftUI and `ParsableCommand` subcommands;
- the http4s, zio, javalin, scotty and Qt rows on real code (probe2 was inconclusive: the frameworks were detected
  but nothing attached, and Scala `extends IOApp.Simple` left `base_classes` empty);
- Verilog top modules on a real repository;
- whether tools outside this repository read the dead kind values from the schema enum;
- configurations other than the default survey.
