<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0060: Built-in Taint Sinks That Are Not I/O Boundaries

Status: Accepted by the agent under the owner's standing autonomy ruling; owner review requested
Date: 2026-09-29
Related: [ADR-0016](0016-io-boundary-analysis.md), [ADR-0017](0017-taint-zone-dataflow.md), [ADR-0061](0061-catalogue-tiers-for-every-family.md)

## Context

- **I/O sinks come from one list.** Built-in taint sinks are derived from
  `io_primitives/` through `AUTO_SINK_ZONE_MAP` (ADR-0017 §2b), so no I/O
  function is written down in two places that could drift apart.
- **That leaves no home for a sink that is not I/O.** Evaluating data as code
  (`eval`, `exec`) and writing data into a page as markup (`document.write`)
  cross no I/O boundary, so no `io_primitives` row can carry them.
- **So verify-claims could not ask the injection questions.** Without such a
  sink, the zone a code-injection or DOM-injection claim needs does not exist,
  and `verify-claims` refused `prohibited_sink_zone: code_execution` as unknown.
- **This is about what ships, not what can be expressed.** An operator could
  already declare such a sink in their own catalogue.

## Decision

**1. `taint_sinks/` ships sinks that have no I/O-boundary counterpart, and only
those.**

- **Every I/O sink is still derived** from `io_primitives/`.
- **The shipped rows are standard-library and platform functions.** That puts
  them in ADR-0061's **built-in** tier.

**2. Two zones, `code_execution` and `dom_injection`.**

- **`code_execution`:** evaluating data as code in the running process.
- **`dom_injection`:** writing data into the document as HTML.
- **They are sink zones, not I/O boundaries.** `io-boundaries` output does not
  list them.

**3. A gate keeps the directory from becoming a second list of I/O sinks.**
`test_shipped_taint_sinks.py` refuses any shipped row that:

- sits in a zone `AUTO_SINK_ZONE_MAP` derives; or
- names a catalogued I/O primitive.

It also pins the set of shipped zones.

**4. Rows cover only call edges the analyzers emit.** A sink that no call edge
can reach would claim coverage the tool cannot deliver.

## Consequences

- **What ships:**

  | zone | languages | sinks |
  |---|---|---|
  | `code_execution` | Python | `builtins.eval`, `exec`, `compile` |
  | `code_execution` | JavaScript/TypeScript | `window.eval`, bare `eval` |
  | `dom_injection` | JavaScript/TypeScript | `document.write`, `document.writeln` |

- **Not covered.** No call edge reaches these shapes today, and the YAML
  headers list them:
  - `el.innerHTML = s` and `outerHTML` (assignments emit no edge);
  - `insertAdjacentHTML` on a receiver the analysis cannot type;
  - `new Function(s)`;
  - `setTimeout` or `setInterval` given a string.
- **Known gap: claims on these zones can report a false `confirmed`.** A claim
  naming one of the zones reads `confirmed`, with no caveat, when:
  - the only flow uses one of the shapes above; or
  - the repository's language has no sink in that zone.

  Before this ADR the same claim was refused as naming an unknown zone. Until a
  verdict discloses the zone's coverage for the languages present, a clean
  verdict on these zones asserts only that no *listed* sink was reached.
- **Where the operator's own sinks go.** Non-I/O sinks the operator adds belong
  in their own catalogue (ADR-0061 ruling 7: `taint_sinks.d/`, `--taint-sinks`
  or a claims file). I/O sinks belong in `io_primitives`.

## Alternatives Considered

- **A1 — A new I/O boundary for evaluation, with an `AUTO_SINK_ZONE_MAP`
  entry.** Rejected: it would put a non-crossing into `io-boundaries` output,
  where every consumer reads a boundary as a crossing.
- **A2 — Leave these sinks to each operator.** Rejected: every operator would
  have to re-declare `eval`.
