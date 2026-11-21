<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# 0027 — Entry-point kinds: one kind carries three concepts, and the sets that read it

```yaml
kind: concept_leak_pairs
date: 2026-11-20
revised: 2026-11-21
pinned_tree: fafaa4a8f
trigger: WI-holud (owner ruling 2026-11-20 - the domain of the overdue Fundamental Concept Audit)
outcome: confirmed
reviewed_against: [ADR-0024, ADR-0027, ADR-0028, ADR-0039, ADR-0056]
owner_rulings: 2026-11-21
filed: [WI-jilik, WI-giron, WI-fasij, WI-rafop]
referenced: [WI-duzif]
build_order: WI-jilik -> WI-giron -> WI-fasij (one PR, one schema bump to 0.22.0)
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
so it gives the best position an entry could reach in rank 2. Four constructed probes (the fourth, `probe4_electron`,
added on 2026-11-21 for the electron.yaml check). Scripts, raw output and probes:
`~/hypergumbo_lab_notebook/fast_smart_test_build_11172026/audit/` (RESULTS.md first).

Two verdict vocabularies, both recorded. Per pair, the playbook's Step 6: DEPRECATE (split, delete or rename) ·
DOCUMENT · KEEP WITH A TRIGGER THAT CAN FIRE. T1-T4 are the four leakage tests (property derivability, apex/peer,
construct vs relationship, mechanism vs category). Per kind value, ADR-0024's trichotomy (CANONICAL / FOLD /
DEPRECATE-NO-FOLD, §"Family-audit verdict methodology"), with ADR-0027's rule carried over from `Symbol.kind`: a
kind names what a thing is, and the framework and the mechanism go in `meta`.

Revision of 2026-11-21: the 2026-11-20 draft proposed items A-G. A review against ADR-0024, ADR-0027, ADR-0028,
ADR-0039 and ADR-0056 refined them, the owner ruled the same day, and the items were filed. This revision records
the refined proposals, the per-value verdicts, the rulings and the filed ids. The inventory and the measurements
are unchanged.

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

### Per pair (playbook Step 6)

| Pair | T1 derivable | T2 apex/peer | T3 construct/relationship | T4 mechanism/category | Verdict | Item |
|---|---|---|---|---|---|---|
| `main_function`: convention main × `application` | Fires the wrong way. No category field separates them, only `evidence_type`, a mechanism, and the free-text label. | Fires. Apex "a program start" (`PROGRAM_START_KINDS` docstring, sketch group, slice boost); peer "a language's `main()`" (label `<Lang> main()`, per-language confidence, Go `cmd/` penalty, WI-tuvun's "kind disambiguates the target type"). Targets are function, class and variable alike. | Fires. A function named `main` is a construct; the object a server hosts is a role. | Fires on the remedy. PR 4's narrower alternative (rank 1 only for `evidence_type == language_convention`) would read a mechanism field as a category. | **DEPRECATE**: split. Ruled 2026-11-21: a new kind `hosted_application`, named by role, takes the 15 server-hosted rows; the other 18 rows split four ways (see A below). | WI-fasij |
| `main_function`: convention main × `entrypoint` concept | Not reached: 6 of 6 non-electron rows never fire, and 0 entries were found in 23 + 6 lists. | Fires. The concept name is the vocabulary's apex ("an entry point") used as one peer concept. | No | Fires. "The CLI framework's parse call" names how a start is recognised, not what starts. | **DEPRECATE**: delete the 6 dead rows; correct the `PROGRAM_START_KINDS` docstring and the spec, which claim the parse call is detected. The electron rows already go to `electron_main`; the Verilog top module takes the hosted-application role (ruled). | WI-jilik, WI-fasij |
| `main_function` × `main_guard` | Fires. Function vs file is derivable from the target `Symbol.kind`, which the INV-hosuh dedup reads instead of the entry kind. | No | No. Both are constructs. | Fires. Two ways of recognising one category. | **KEEP WITH A TRIGGER**. No consumer branches on the difference: every set holds both. WI-tuvun's rationale is already false for `main_function`, whose targets include class and variable. | — (watcher: WI-giron) |
| `cli_main` × the rest | — | — | — | — | **DEPRECATE (delete)**: zero producers since #331. It is a phantom in `PROGRAM_START_KINDS`, `_SEMANTIC_KINDS`, the sketch group, and the spec (§8 auto-slice formula, §9 Sorting). ADR-0024: DEPRECATE-NO-FOLD. | WI-jilik |
| `cli_command`: manifest bin × framework subcommand × `cmd_<name>` | Fires. Separable only by `evidence_type`, which is how rank 0 reads it. | Fires. A Cargo `[[bin]]` / pyproject script is the program; a click / cobra command is a subcommand inside it. | No | Fires. Rank 0 is keyed on a mechanism field because the kind cannot say "program". ADR-0028: `evidence_type` names the inference pathway, so reading it as a category is the leak. | **DOCUMENT now** (the `entry_kind_tie_rank` docstring states why it reads `evidence_type`). Resolve through the role registry. Ruled: a manifest bin is a program start, in `slice --entry auto` too. | WI-giron |
| "The framework's app start": `application` (as `main_function`) × `electron_main` × `android_application` × `spa_bootstrap` | Fires. The difference is the framework, which the concept already carries. | Fires the other way: one thing under four names. | No | No | **DOCUMENT**, decided with A and F. Ruled roles: the 15 hosted rows and `android_application` are hosted application; `electron_main` is a program start. `spa_bootstrap`'s role is not ruled. | WI-fasij, WI-giron |
| 21 framework-flavoured kinds × `http_route` / `controller` | Fires. Framework is derivable from the concept. | No | No | No | **DEPRECATE (remove the values)**. ADR-0024: FOLD into `http_route` / `controller` with the framework in `meta` (what analyzers already emit; no data to migrate). `electron_renderer` folds too; `electron_preload` is kept and gets a producer (see C+D+E below). | WI-jilik |
| Mechanism-named kinds (`connectivity_based`, `script_module`, `shell_script`, `html_entry`) × category kinds | Fires for `connectivity_based`: it restates `evidence_type = connectivity_heuristic`. | Fires for `shell_script` / `script_module`: each is a file run as a program, like `main_guard`, yet sits in rank 2. | No | Fires. Named by how they are found. | **KEEP WITH A TRIGGER**. Their program-start membership is ruled: `shell_script` and `script_module` stay fallback. | — (watcher: WI-giron) |
| `confidence` × rank | — | Fires. Base literals are set as ranking tiers ("Lower than routes, similar to main()", "Naming heuristic - lowest tier"), and `rank_score` starts from them. Only `main_function`'s values (11 languages) are measured. | — | Fires. A detection probability used as an ordering seed (ADR-0039 ruling 1: `confidence` is detection reliability only). | Confirmed; **already filed as WI-duzif** (Go main 0.95 → 0.90 reordered 4 of 23 lists). Not re-filed. Here the unmeasured 0.90 of `application` decides where the narrow rule would put it. | WI-duzif |

### Per kind value (ADR-0024)

"(ruled)" marks a verdict or role the owner ruled on 2026-11-21; "(audit)" marks this audit's verdict, which no
ruling changed. ADR-0024's trichotomy has no "keep under a trigger" verdict: for the four mechanism-named kinds,
CANONICAL means "stays", and the pair trigger above is what reopens it. Role is filled only where ruled; WI-giron
assigns the rest from the eight roles (program start, hosted application, request ingress, scheduled, reflective,
library API, test, fallback). Producer sites are AST counts on the pinned tree (`kind_sets.txt`). The per-value
split of the 21 framework kinds between `http_route` and `controller` follows their sketch group today; the ruling
names the two targets, not the split.

| Kind | Producer sites | ADR-0024 verdict | Target or note | Role | Item |
|---|---|---|---|---|---|
| `http_route` | 3 | CANONICAL (audit) | receives 13 folds | — | WI-jilik |
| `cli_main` | 0 | DEPRECATE-NO-FOLD (ruled) | "this main runs a CLI parser" is a property of that main, not a kind of its own; precedent: ADR-0039's retirement of `confidence_source=composite`, which no producer ever stamped (WI-daruz) | — | WI-jilik |
| `cli_command` | 3 | CANONICAL (audit) | receives the 2 `ParsableCommand` rows; a manifest bin is told apart from a subcommand only by `evidence_type` | program start, for a manifest bin (ruled) | WI-giron, WI-fasij |
| `main_function` | 3 | CANONICAL (audit) | keeps the 21 convention rows and the 7 program-start-by-construction rows; 15 rows leave for `hosted_application`; 11 other `application` rows, the 6 `entrypoint` rows and verilog.py's top-module concept stop producing it | program start (ruled) | WI-fasij, WI-jilik |
| `main_guard` | 1 | CANONICAL (audit) | KEEP WITH A TRIGGER against `main_function` | program start (ruled) | — |
| `electron_main` | 1 | CANONICAL (audit) | the name carries the framework (ADR-0027); no framework-neutral kind names Electron's main-process start | program start (ruled) | WI-giron |
| `electron_preload` | 0 | CANONICAL (ruled: keep, wire a producer) | a real role: Electron loads the preload script when it creates a window. The electron.yaml row the ruling cites cannot fire (see C+D+E) | — | WI-jilik |
| `electron_renderer` | 0 | FOLD (ruled) | `html_entry` / `script_module` | — | WI-jilik |
| `django_view` | 0 | FOLD (ruled) | `http_route`, framework in `meta` | — | WI-jilik |
| `express_route` | 0 | FOLD (ruled) | `http_route` | — | WI-jilik |
| `nestjs_controller` | 0 | FOLD (ruled) | `controller` | — | WI-jilik |
| `spring_controller` | 0 | FOLD (ruled) | `controller` | — | WI-jilik |
| `rails_controller` | 0 | FOLD (ruled) | `controller` | — | WI-jilik |
| `phoenix_controller` | 0 | FOLD (ruled) | `controller` | — | WI-jilik |
| `go_handler` | 1 | not ruled | language-named with a live producer (entrypoints.py:1338, Go `ServeHTTP`); `taint.py:4630` and `:4651` read the value. A FOLD candidate under ADR-0027, target not decided: a `ServeHTTP` method has no route path | — | not filed |
| `laravel_controller` | 0 | FOLD (ruled) | `controller` | — | WI-jilik |
| `rust_handler` | 0 | FOLD (ruled) | `http_route` | — | WI-jilik |
| `aspnet_controller` | 0 | FOLD (ruled) | `controller` | — | WI-jilik |
| `sinatra_route` | 0 | FOLD (ruled) | `http_route` | — | WI-jilik |
| `ktor_route` | 0 | FOLD (ruled) | `http_route` | — | WI-jilik |
| `vapor_route` | 0 | FOLD (ruled) | `http_route` | — | WI-jilik |
| `plug_route` | 0 | FOLD (ruled) | `http_route` | — | WI-jilik |
| `hapi_route` | 0 | FOLD (ruled) | `http_route` | — | WI-jilik |
| `fastify_route` | 0 | FOLD (ruled) | `http_route` | — | WI-jilik |
| `koa_route` | 0 | FOLD (ruled) | `http_route` | — | WI-jilik |
| `grape_api` | 0 | FOLD (ruled) | `controller` | — | WI-jilik |
| `tornado_handler` | 0 | FOLD (ruled) | `http_route` | — | WI-jilik |
| `aiohttp_view` | 0 | FOLD (ruled) | `http_route` | — | WI-jilik |
| `slim_route` | 0 | FOLD (ruled) | `http_route` | — | WI-jilik |
| `micronaut_controller` | 0 | FOLD (ruled) | `controller` | — | WI-jilik |
| `graphql_server` | 1 | CANONICAL (audit) | named by protocol, as `http_route` is | — | — |
| `android_activity` | 1 | CANONICAL (audit) | the name carries the platform; not ruled | — | — |
| `android_application` | 1 | CANONICAL (audit) | the name carries the platform | hosted application (ruled) | WI-giron |
| `controller` | 7 | CANONICAL (audit) | receives 8 folds | — | WI-jilik |
| `background_task` | 1 | CANONICAL (audit) | | — | — |
| `websocket_handler` | 3 | CANONICAL (audit) | | — | — |
| `middleware_handler` | 1 | CANONICAL (audit) | in no sketch group today | — | WI-giron |
| `event_handler` | 2 | CANONICAL (audit) | | — | — |
| `error_handler` | 1 | CANONICAL (audit) | in no sketch group today | — | WI-giron |
| `form` | 1 | CANONICAL (audit) | in no sketch group today | — | WI-giron |
| `serializer` | 1 | CANONICAL (audit) | in no sketch group today | — | WI-giron |
| `scheduled_task` | 1 | CANONICAL (audit) | | — | — |
| `library_export` | 2 | CANONICAL (audit) | | — | — |
| `shell_script` | 1 | CANONICAL (audit), kept under a trigger | mechanism-named | fallback (ruled) | — |
| `html_entry` | 1 | CANONICAL (audit), kept under a trigger | mechanism-named; receives `electron_renderer` | — | WI-jilik |
| `script_module` | 1 | CANONICAL (audit), kept under a trigger | mechanism-named; receives `electron_renderer` | fallback (ruled) | WI-jilik |
| `test_function` | 1 | CANONICAL (audit) | in no sketch group today | — | WI-giron |
| `spa_bootstrap` | 1 | CANONICAL (audit) | in no sketch group today; role not ruled | — | WI-giron |
| `connectivity_based` | 1 | CANONICAL (audit), kept under a trigger | restates `evidence_type = connectivity_heuristic` | — | — |
| `hosted_application` (new) | — | CANONICAL (ruled) | named by role, framework in `meta` (ADR-0027); the 15 server-hosted rows, and the Verilog top module, whose ruled role is hosted application | hosted application (ruled) | WI-fasij |

Totals over the 49 existing values: 25 CANONICAL, 22 FOLD, 1 DEPRECATE-NO-FOLD, 1 not ruled (`go_handler`); plus
the new `hosted_application`.

ADR-0056 applied to the 24 producerless values: they are not one omission. Read one by one, they are 1 + 21 + 1 +
1. `cli_main` names a property of another kind's target (DEPRECATE-NO-FOLD); the 21 framework kinds name concepts
already produced under `http_route` / `controller` (FOLD); `electron_renderer` names a concept already produced
under `html_entry` / `script_module` (FOLD); `electron_preload` names a real concept whose producer is missing
(keep, wire).

### Keep triggers

- `main_function` × `main_guard`: fires when a consumer branches on `main_guard` without `main_function`
  (`grep -n "EntrypointKind.MAIN_GUARD" packages/*/src` outside the producer arm, the dedup tuple,
  `PROGRAM_START_KINDS` and the sketch group), or when WI-fasij lands. WI-fasij changes what the dedup tuple holds,
  and the tuple must then be re-derived.
- Mechanism-named kinds: fires when any consumer other than the sketch's fall-through branches on
  `connectivity_based`, `script_module`, `shell_script` or `html_entry` alone.

**Watcher.** A keep-with-trigger has to be able to fire and has to have a watcher. WI-giron's drift test is the
watcher for both triggers: once each kind set is derived from the role registry, a set that branches on
`main_guard` alone or on a mechanism-named kind alone is a hand-written set the drift test rejects. The grep
triggers are re-pointed onto that test, not struck (ADR-0056, "re-point, don't strike"). **Until WI-giron lands,
neither trigger has a watcher.** Nothing runs the greps; they fire only if someone runs them by hand.

## Silent bugs found on the way

- **The INV-hosuh main dedup drops application entries** (entrypoints.py:1968-2003). It groups kinds
  {`main_function`, `main_guard`} by file and keeps only entries whose symbol is a `function`. The probe has an
  identical Tornado `Application` and Flask `app` in two files. In `other.py` both are entries; in `server.py`,
  which also has `def main()`, both are silently gone. The kind smuggles them into a set written for the
  guard-vs-main pair. Real-repo magnitude was not measured. → WI-fasij.
- **`cli_main` is a phantom value** in `PROGRAM_START_KINDS`, which the spec documents as one of three program
  starts. → WI-jilik.
- **Six `entrypoint` rows can never fire** while the code documents them as detected (the `PROGRAM_START_KINDS`
  docstring: "a CLI framework's top-level parse call"). → WI-jilik.
- **`ParsableCommand` is filed under SwiftUI's `application`.** It only loads when SwiftUI is detected, so it
  fires on 0 of 4,756 nodes of swift-argument-parser. Where it does load, every subcommand conformer becomes a
  rank-1 "program start". The second half was read from code and not reproduced. → WI-fasij (the 2 rows go to
  `cli_command`).
- **The electron.yaml `api_bridge` row can never fire** (found while checking the ruling's premise, 2026-11-21;
  see C+D+E). → WI-jilik.
- Adjacent, outside the kind field: COBOL's `program_entrypoint` concept (language-conventions.yaml:74) and
  micronaut's `scheduled` (micronaut.yaml:87) reach no arm of the detector. The concept vocabulary has no
  registry, so a near-miss name produces no entry point and no error. → WI-rafop.

Code read, not reproduced: within one symbol, `added_kinds` lets only the first concept of a kind through. A symbol
carrying both `main_function` and `application` would take its label and confidence (0.95 vs 0.90) from concept
order, against the INV-liraj docstring's "the chosen kind does not depend on detector or concept order". No case
reached it: the Javalin and scotty usage rows attached nothing on probe2. WI-fasij moves the 15 hosted rows off
`main_function`, which removes that pairing for them.

## Refined proposals

The 2026-11-20 draft's items A-G, as refined by the review and then ruled. Each names its filed item.

### F. Entrypoint-kind becomes a full ADR-0024 axis → WI-giron

Entrypoint-kind is today a lightweight catalog-derived axis: `all_known_entrypoint_kinds()` is the single source
of the schema enum and is wired into `multi_value_field_axis._known_axes()` (spec §9, WI-pupiz). That is ADR-0024
§4's "use judgment" carveout, and it holds only while no consumer branches on groups of values. Five readers do,
each on a hand-written set: the tie order and `slice --entry auto`'s 2× boost (both `PROGRAM_START_KINDS`; the
manifest-bin disagreement is that the tie order also reads `evidence_type` and the boost does not), request ingress
(`REQUEST_INGRESS_KINDS`), the sketch groups, and the INV-hosuh dedup tuple. The library-export demotion's
`_SEMANTIC_KINDS` is one more hand-written set. They disagree (Inventory). Readers branching on hand-written groups is ADR-0024's condition for the full axis, and
ADR-0023's silent-drift bug is what it prevents: here, 21 dead values in `REQUEST_INGRESS_KINDS` and a phantom
`cli_main` in `PROGRAM_START_KINDS`.

The full axis is ADR-0024's four artifacts: a registry module declaring a role per value, with an
`*_on_axis(role)` accessor; the by-axis view in `docs/concept-axes.md` (a section in `scripts/generate-concept-axes`);
a drift check over hand-written `*_KINDS` sets; a property test. The kind sets are derived from the registry.

Roles: program start, hosted application, request ingress, scheduled, reflective, library API, test, fallback.

Ruled program-start set: a manifest bin, the convention main (`main_function`), `main_guard`, `electron_main`, and
nothing else. `shell_script` and `script_module` stay fallback. `android_application` and the Verilog top module
are hosted application. A manifest bin gets the program-start treatment in `slice --entry auto` too, which ends
the tie order / slice disagreement.

Repro: `audit/kind_sets.py` prints the five sets and their disagreements.

### A. Server-hosted application objects get their own kind, `hosted_application` → WI-fasij (with B)

The kind is named by role, not by framework: the framework goes in `meta` (ADR-0027). It ranks with routes (rank 2)
and is grouped with them in the sketch. The 15 server-hosted rows go to it; the other 18 `application` rows split
four ways (the ruling's count), so the 33 have five destinations:

| Rows | What they are | Where they go |
|---|---|---|
| 15 | server-hosted application objects (aiohttp, FastAPI, Flask, Tornado, Roda, Padrino, lapis, Hummingbird `ApplicationProtocol`, servant, Yesod ×2, giraffe, plumber ×2, shiny) | `hosted_application` |
| 7 | program starts by construction (`IOApp` / `IOApp.Simple`, `ZIOApp` / `ZIOAppDefault`, SwiftUI `App`, `UIApplicationDelegate`, `NSApplicationDelegate`) | stay `main_function` |
| 6 | start calls made inside a `main` (Javalin, `QApplication`, scotty ×3, Yesod `warp` / `toWaiApp`) | no entry of their own (at most a `meta` tag on the main) |
| 2 | `ParsableCommand`, `AsyncParsableCommand` | `cli_command` |
| 3 | Yesod Template Haskell dispatch generators (`mkYesod`, `mkYesodData`, `mkYesodSub*`) | not entries (their rows produce routes or are removed) |

The narrower rule PR 4 offered (read `evidence_type`) is the T4 failure above, and ADR-0028 rules it out: that
field names the inference pathway.

**Ship condition (owner).** An A/B across every reader of the kind: the tie order, `slice --entry auto`'s 2×
boost, the sketch groups, request ingress and the compact seeds, on mitmproxy, full-stack-fastapi-template and a
Flask repository. A service with no `main` still auto-slices from its application object or from a route that
covers it. If that fails, the work stops and reports.

Repro: survey mitmproxy; the "Tornado application" is at position 3, before 24 controllers of equal `rank_score`,
and in the sketch's "CLI & Scripts".

**B, built with A: the INV-hosuh dedup drops non-function entries of kind `main_function`.** Key the dedup on the
guard / main pair it was written for, not on the kind set. Repro: `probe3/` (two files with one Tornado
`Application` and one Flask `app` each, one also with `def main()` and a guard). Both appear for `other.py` and
neither for `server.py`.

### C, D, E. Dead rows and dead kinds → WI-jilik

D and E no longer share one verdict.

- **D. `cli_main` → DEPRECATE-NO-FOLD.** That a main runs a CLI parser is a property of that main, not a kind of
  its own, and nothing has produced the value since #331. Precedent: ADR-0039 ruling 2's retirement of
  `confidence_source=composite`, a value no producer ever stamped, removed as no longer legal (WI-daruz). Repro:
  the AST producer count is 0; the member appears in `PROGRAM_START_KINDS`, `_SEMANTIC_KINDS`, sketch
  "CLI & Scripts" and spec §8 and §9.
- **E. The 21 framework-named kinds → FOLD** into `http_route` / `controller`, the framework in `meta`. The
  analyzers already emit exactly that, so there is no data to migrate; the values are removed from the enum, the
  kind sets, the schema and the spec. `electron_renderer` → FOLD into `html_entry` / `script_module`, which already
  cover a renderer's page and scripts. `electron_preload` → KEEP, and wire a producer: Electron loads the preload
  script when it creates a window, a real role (ADR-0056: check each producerless value for a real concept with a
  missing producer). Repro: `kind_sets.py` lists the 24 zero-producer kinds; `git log -S` places their last
  producers in `1b5d12767`.
- **C. The six `decorator:` rows of the `entrypoint` concept are deleted in the same change** (cli.yaml argparse /
  fire, cli-go.yaml kong, cli-ruby.yaml ×2, akka-http.yaml), and the `PROGRAM_START_KINDS` docstring and the spec
  stop claiming the parse call is detected. Repro: the Go probe with `kong.Parse` in `main` (cli-go loaded)
  carries no `entrypoint` concept, and `go.py` / `ruby.py` never write `meta["decorators"]`.

Schema: `SCHEMA_VERSION` 0.21.3 → 0.22.0 (schema 0.x: a breaking change bumps the minor). Readers of the removed
names outside this repository: not checked.

**electron.yaml, checked 2026-11-21.** The ruling's premise is that electron.yaml already detects
`contextBridge.exposeInMainWorld` as `api_bridge`. The row exists (`concept: api_bridge`,
`decorator: "^contextBridge\\.exposeInMainWorld$"`) and cannot fire, for the reason the six `entrypoint` rows
cannot. The matcher (framework_patterns.py:432) reads `meta["decorators"]`; the JS/TS analyzer writes that key only
from `@decorator` syntax on class and member declarations (js_ts.py `_extract_decorators`, :4191), never from a
call. Nothing outside the YAML reads `api_bridge` either (zero references in `packages/*/src` besides the row).
Reproduced on `audit/probe4_electron/` (`main.js` with `app.whenReady()` and a `preload:` path, `preload.js` with
`contextBridge.exposeInMainWorld('api', { ping: () => ipcRenderer.invoke('ping') })`, `renderer.js` calling
`window.api.ping()`, an `electron` dependency): electron is detected; the entries are `electron_main` (main.js),
`script_module` (preload.js) and `script_module` (renderer.js); no node carries `api_bridge`, and the only concept
on a `.js` node is `entrypoint` on main.js. What does see the bridge is the IPC linker (linkers/ipc.py, a regex
scan): one `calls` edge with `meta.bridge_kind = context_bridge` from renderer.js `api.ping` to the send site in
preload.js. So WI-jilik's producer comes from a usage-keyed row (the shape of electron.yaml's two
`app.whenReady` / `app.on` rows, which do fire) or from the IPC linker's `exposeInMainWorld` detection, not from
the `api_bridge` row as written. electron.yaml's other `decorator:` rows on calls (`ipcMain.*`, `new BrowserWindow`,
`app.on` for `lifecycle`, and the rest) have the same shape; the probe shows `ipc_handler`, `window` and `lifecycle`
absent on its nodes, and the remaining rows were read, not probed.

### G. Concept names no detector arm reads → WI-rafop

A concept-name registry with a load-time check (an unknown concept is an error), then a decision per orphan
(wire or delete). Repro: `yaml_inventory.py` lists `program_entrypoint` (COBOL) and `scheduled` (micronaut) as
mapping to nothing.

### Confidence × rank → WI-duzif

Stands as filed. Its question to the owner: does the entry ranking start from kind rank and structural signals
instead of detection confidence? ADR-0039 ruling 1 makes `confidence` detection reliability only; `rank_score`
still starts from it.

## Owner rulings 2026-11-21

Recorded on WI-holud, quoted:

> (1) program start = manifest bin, convention main, main_guard, electron_main only; shell_script/script_module
> stay fallback; android_application and the Verilog top module are hosted application. (2) server-hosted app
> objects (15 rows) move to a new kind hosted_application ranked with routes; the other 18 application rows split
> four ways; ships only if a service with no main still auto-slices from its app or a covering route (A/B). (3)
> delete cli_main, the 21 framework-named kinds and electron_renderer; add a producer for electron_preload;
> SCHEMA_VERSION 0.22.0. Built as one PR, in order: WI-jilik (dead kinds) -> WI-giron (entrypoint-kind axis
> registry, roles) -> WI-fasij (hosted_application + dedup fix). Residual: WI-rafop (concept names no detector
> reads). Ranking question 1 of this item is answered by rulings 1-2; the concept question 2 by the audit.

The 18 are the rows that do not become `hosted_application`: 7 stay `main_function`, 6 become no entry, 2 go to
`cli_command`, 3 stop being entries. WI-fasij's description calls the whole of the 33 a "four-way split" while it
lists five destinations; the ruling's own count is the one above (15 to the new kind, 18 split four ways).

## Filed items and build order

| Draft item | Filed | Status | What |
|---|---|---|---|
| C + D + E | WI-jilik | todo_hard | remove `cli_main`, the 21 framework-named kinds and `electron_renderer`; wire `electron_preload`; delete the six dead `entrypoint` rows; correct the docstring and the spec |
| F | WI-giron | todo_hard | entrypoint-kind becomes a full ADR-0024 axis: role registry, by-axis view, drift check, property test; the kind sets derive from it |
| A + B | WI-fasij | todo_hard | `hosted_application` and the four-way split; the INV-hosuh dedup keyed on the guard / main pair |
| G | WI-rafop | todo_soft | concept-name registry with a load-time check |
| confidence × rank | WI-duzif | todo_soft | referenced, not re-filed |

Build order: WI-jilik → WI-giron → WI-fasij, in one PR, with one schema bump to 0.22.0. WI-jilik first, so that
the registry WI-giron builds declares only values that exist; WI-giron before WI-fasij, so that
`hosted_application` lands as a registry row with its role, not as a new hand-written set member. WI-rafop is
residual and outside that PR.

## Why this document is not an `audit_verdicts` table yet

The per-value verdicts above are ADR-0024's trichotomy, but the standard `audit_verdicts` format cannot carry them
today. `hypergumbo_core.audit_findings` binds exactly three axes in `_REGISTRIES` (`Edge.edge_type`,
`Symbol.kind`, `Edge.evidence_type`), and `validate_against_registry` rejects any other declared axis ("has no
registry mapping"); each row's status predicate checks the value against that axis's registry module. Entrypoint-kind
is a lightweight catalog-derived axis with no registry module and no canonical section until WI-giron lands, so
there is nothing to check a row against. The per-pair verdicts and the four leakage-test columns are outside that
format in any case. The document therefore keeps the `concept_leak_pairs` sibling format; no lint is changed.
When WI-giron lands a registry, the per-kind table converts to `audit_verdicts` rows, and binding the axis in
`_REGISTRIES` is part of that change.

## Not checked

- the real-repo magnitude of the INV-hosuh dedup drop (B);
- a repository with SwiftUI and `ParsableCommand` subcommands;
- the http4s, zio, javalin, scotty and Qt rows on real code (probe2 was inconclusive: the frameworks were detected
  but nothing attached, and Scala `extends IOApp.Simple` left `base_classes` empty);
- Verilog top modules on a real repository;
- whether tools outside this repository read the removed kind values from the schema enum;
- configurations other than the default survey;
- `go_handler`: no ruling and no item; its fold target is open (above);
- how WI-giron's registry expresses a manifest bin, which today is a `cli_command` row told apart only by
  `evidence_type` (a role per kind cannot say it without a kind of its own or a role accessor that reads
  `evidence_type`, the ADR-0028 leak);
- the roles of the kinds the rulings did not name (`spa_bootstrap`, `android_activity`, `electron_preload`,
  `cli_command`'s framework subcommands, and the rest marked "—" above);
- electron.yaml's other call-keyed `decorator:` rows, beyond the three concepts absent on probe4.
