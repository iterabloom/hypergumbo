<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0063: The Entrypoint-Kind Axis

Status: Accepted
Date: 2026-11-21
Related: [ADR-0024](0024-axis-declaration-template.md) (the template this
instantiates), [ADR-0027](0027-symbol-kind-language-construct-only.md) (whose
"a kind names what the thing is" rule audit 0027 carried to entry kinds),
[ADR-0028](0028-evidence-type-inference-pathway-only.md) (`evidence_type` names
the inference pathway only, which is why a manifest's executables get a kind of
their own), [ADR-0039](0039-confidence-separation.md) (ruling 3's order among
ties reads the program-start role),
[audit-findings 0027](../audits/0027-entry-point-kinds.md) (the audit and the
owner rulings of 2026-11-21 this records)

## Context

`Entrypoint.kind` was a lightweight catalog-derived axis (WI-pupiz):
`all_known_entrypoint_kinds()` exposed the `EntrypointKind` enum as the schema
enum and the `_known_axes` resolver, under ADR-0024 §4's "use judgment"
carveout. That carveout holds only while no reader branches on groups of
values. Audit 0027 found five that did, each on a hand-written set, and the
sets disagreed:

- the tie order among entries of equal `rank_score` (`PROGRAM_START_KINDS`) and
  `slice --entry auto`'s 2x boost (the same set; the tie order also read
  `evidence_type` to put a manifest's executables first, the boost did not);
- the sketch's Entry Points groups (`electron_main` was a program start there
  and nowhere else; nine kinds fell through to "Other");
- request ingress (`REQUEST_INGRESS_KINDS`, read by `request_ingress`,
  verify-claims' unseeded-ingress withhold and taint's parameter seeds);
- the INV-hosuh main dedup tuple.

A sixth, the library-export demotion's `_SEMANTIC_KINDS`, had lost
`main_guard` when WI-tuvun split it from `main_function`. And the sets still
held 24 kinds nothing had produced since #331 (removed by WI-jilik). That is
ADR-0023's silent-drift bug, and ADR-0024's condition for a full axis.

## Decision

Declare the **entrypoint-kind axis** with ADR-0024's four artifacts:

- **Registry:** `hypergumbo_core.entrypoint_kinds` (`EntrypointKind` moved
  there; `entrypoints` re-exports it). `ENTRYPOINT_KINDS` gives every kind one
  role, whether the role was ruled or proposed, its ADR-0024 verdict, and why a
  framework- or mechanism-named kind keeps its name. `ENTRYPOINT_ROLES` gives
  each role its sketch heading.
- **By-axis view:** a section of `docs/concept-axes.md`, one subsection per
  role, rendered by `scripts/generate-concept-axes` from the registry.
- **Drift check:** `find_kind_set_drift` reports any hand-written group of
  kinds (a set, tuple, list or dict keys naming two or more members, or two or
  more kind-value strings) outside the registry; `scripts/check-symbol-kind-drift`
  runs it in the pre-commit step of the sibling kind axis (the ADR-0059
  precedent: a sibling axis rides an already wired step).
- **Property test:** `tests/test_entrypoint_kinds.py`.

### 1. Axiom

> An entrypoint's kind names what the entry IS; the framework that registered
> it and the mechanism that found it go in `meta`. Every kind has exactly one
> **role**: what starts or dispatches the code at that entry. A reader that
> treats a group of kinds alike asks for a role, never for a list of kinds.

### 2. Roles

| Role | Sketch heading | Readers that tell it apart |
|---|---|---|
| `program_start` | CLI & Scripts | first among ties (after a manifest's executable), `slice --entry auto` 2x boost and Go module-binary pick, the demotion |
| `command` | CLI & Scripts | the demotion |
| `hosted_application` | Hosted Applications | the demotion |
| `request_ingress` | Request Handlers | `request_ingress`, verify-claims' withhold, taint seeds, the demotion |
| `scheduled` | Event & Background Tasks | the demotion |
| `reflective` | Framework-Reflected Classes | none but the sketch |
| `library_api` | Library API | the export-specific boosts and demotion |
| `test` | (the Tests section) | excluded from Entry Points |
| `fallback` | Other | none but the sketch |

Eight are audit 0027's. `command` is a ninth, proposed here: a framework
subcommand (`cli_command`) is not a program start (the ruled list is closed),
receives no request (`REQUEST_INGRESS_KINDS` excludes a CLI), and is neither
reflective nor scheduled.

**Owner rulings 2026-11-21** (audit 0027, recorded on WI-holud), pinned one
by one in the property test: program start is a manifest's executable, the
convention main (`main_function`), `main_guard` and `electron_main`, and
nothing else; `shell_script` and `script_module` stay fallback;
`android_application` and the Verilog top module are hosted application; a
manifest's executable gets the program-start treatment in `slice --entry auto`;
server-hosted application objects get a kind of their own,
`hosted_application`, in the hosted-application role, "ranked with routes".

*Note 2026-11-21 (WI-fasij).* `hosted_application` is in the registry: the
`application` concept (a Flask / FastAPI `app` variable, a Tornado / aiohttp /
Roda / Padrino / Hummingbird application type; 15 YAML rows) and the Verilog
top module (`top_module`, which verilog.py had stamped as `entrypoint`, filed
under `main_function`) produce it. Its tie rank is 2, the routes' rank, because
its role is not program start; nothing chose a weight for it. The 7 framework
types the runtime starts as the program (IOApp, ZIOApp, SwiftUI `App`, the two
app delegates) moved to the `entrypoint` concept and stay `main_function`; the
two `ParsableCommand` rows emit `command` (`cli_command`); the 9 rows on start
calls and Template Haskell generators were deleted.
Every other role assignment is proposed and marked so in the registry;
`go_handler` is unruled (WI-hamiz) and keeps the role its behaviour implied.

### 3. Consumer pattern

`entrypoint_kinds_on_axis(role) -> tuple[EntrypointKindSpec, ...]` (ADR-0024's
shape), plus `kinds_with_role(*roles) -> frozenset[EntrypointKind]`, `role_of`
and `role_heading`. Each reader's set is one call:

| Reader | Set |
|---|---|
| tie order, slice boost, Go module-binary pick | `PROGRAM_START_KINDS = kinds_with_role(program_start)` |
| request ingress | `REQUEST_INGRESS_KINDS` from `kinds_with_role(request_ingress)` |
| library-export demotion | `APPLICATION_KINDS = kinds_with_role(*APPLICATION_ROLES)` |
| sketch Entry Points | one group per `role_heading`, in role order |

A reader that compares ONE kind (the build-wrapper demotion on `shell_script`,
the Go `cmd/` penalty on `main_function`, the INV-hosuh dedup, which drops a
`main_guard` beside a `main_function` on a function of its file and nothing
else (until WI-fasij it read `PROGRAM_START_KINDS` and kept only a file's
function-target starts, dropping any application object beside a `def main()`;
audit 0027 B), the connectivity-boost skips, the
route-label dedup's label scheme, taint's per-kind parameter contracts) asks
about that kind's own behaviour and is not a role question.
`NOT_A_ROLE_QUESTION` lists the one multi-kind collection that is not (the
route-marker pass's producer bookkeeping).

### 4. Enforcement, and the keep triggers it watches

Audit 0027 kept two pairs under a trigger and found that neither trigger had a
watcher (both were greps nobody ran). The property test is the watcher now:

| Audit trigger | Fires in |
|---|---|
| a reader branches on `main_guard` without `main_function` | a hand-written set: `find_kind_set_drift`; a single-kind branch: `find_watched_kind_reads` (ledger `KNOWN_WATCHED_READS`); a role split: `test_main_guard_shares_main_functions_role` |
| a reader branches on a mechanism-named kind alone | `find_watched_kind_reads` (the two existing reads, the build-wrapper demotion and the connectivity-boost skip, are listed with their reasons; so is the INV-hosuh dedup's read of the `main_guard` / `main_function` pair, the dedup the audit's trigger exempts) |
| a kind with no role / a role with no kind / a role no reader tells apart | `test_every_kind_has_exactly_one_role`, `test_every_role_has_a_kind`, `test_every_role_is_told_apart_by_some_reader` |
| a kind named by a framework or mechanism without a recorded verdict | `test_a_framework_or_mechanism_named_kind_records_why_the_name_stays` |
| a kind whose only producer is a concept nothing emits | `test_every_kind_has_a_live_producer` (ledger `PRODUCERLESS_KINDS`, empty) |

## Consequences

- The five sets agree by construction. Membership changed where they had
  disagreed: `electron_main` is a program start in the tie order, the slice
  boost and the dedup; the sketch files every kind under its role's heading,
  so `middleware_handler`, `error_handler`, `form`, `serializer` and
  `spa_bootstrap` leave "Other" and HTTP routes, controllers, GraphQL and
  WebSocket handlers share "Request Handlers" (within a group's share each
  kind's best entry is shown first, so no kind loses its last line to the
  merge: the pre-registered refutation fired on thc, whose one controller
  fell under 182 routes, before that rule); the demotion counts
  `main_guard`, middleware, error handlers and the hosted-application kinds.
  The measured effect is in WI-giron's evidence.
- `Entrypoint.kind` carries `# axis: entrypoint-kind`. The multi-value-field
  linter does not scan `entrypoints.py` (its scope must match the pre-commit
  hook's regex, a governance file), so the comment is a declaration, not a
  gate.
- Role is not an output field: it is derived from the kind, and no reader
  outside the process needs it.

## Not decided here

- Whether hosted applications show beside the routes in the sketch (audit 0027
  proposal A says "grouped with them"): under this ADR a heading is per role;
  two roles may share one, so that is a one-line change to the role table.
  WI-fasij left the heading as the registry gives it ("Hosted Applications"):
  the ruling's "ranked with routes" is about the entry list's order.
- `go_handler`'s fold target (WI-hamiz).
