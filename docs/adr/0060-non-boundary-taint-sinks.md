<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0060: Built-in Taint Sinks That Are Not I/O Boundaries

Status: Accepted
Date: 2026-09-29
Related: [ADR-0017](0017-taint-zone-dataflow.md) (taint zones and the
auto-derived sink map), [ADR-0016](0016-io-boundary-analysis.md) (the I/O
catalogues sinks are derived from), [ADR-0047](0047-catalogue-scope-and-user-visible-homes.md)
(catalogue homes). Tracker: WI-nokab (and INV-dadu, which it unblocks).

Decided by the agent under the owner's standing autonomy ruling and flagged on
WI-nokab for owner review; reverting is deleting `taint_sinks/` and the loader's
two lines.

## Context

Every built-in taint sink is derived from `io_primitives/` through
`AUTO_SINK_ZONE_MAP` (commit 51e1d232f3). That commit retired a shipped
`taint_sinks/` directory for a specific reason: it listed ~9 Python I/O
primitives against io_primitives' 37 `fs_write` + 66 `net_send`, and a
hand-kept copy of a list another catalogue already enumerates drifts.

The derivation has a consequence nobody decided: a sink that is NOT an I/O
boundary cannot ship at all. Evaluating data as code (`eval`, `exec`) and
writing data into a page as markup (`document.write`) cross no I/O boundary --
python.yaml's builtins note says so on the record -- so the zones a code- or
DOM-injection claim needs do not exist, and `verify-claims` rejects
`prohibited_sink_zone: code_execution` as unknown. A project can already
declare such a sink (`--taint-sinks`), so this is about what SHIPS.

Two homes were considered and refused:

1. **A new I/O boundary** (`code_eval`) with an `AUTO_SINK_ZONE_MAP` entry.
   Re-opens the recorded "evaluation crosses no boundary" ruling and puts a
   non-crossing into `io-boundaries` output, where every consumer reads
   boundaries as crossings (ADR-0050's axis).
2. **Leave it to projects.** Every project would re-declare `eval`, which is
   the drift ADR-0047 exists to prevent, and a clean verdict over a repo that
   never declared it would read as coverage.

## Decision

1. `taint_sinks/` ships again, restricted to sinks with NO I/O-boundary
   counterpart. The loader reads it as it reads the shipped `taint_sources/`
   (which already ship non-boundary sources: `crypto`, `key_material`), and
   auto-derived I/O sinks still come only from `io_primitives/`.
2. Two zones: `code_execution` (evaluating data as code in the running
   process) and `dom_injection` (writing data into the document as HTML).
   They are sink ZONES, not boundaries; `io-boundaries` output is unchanged.
3. **The retired invariant is kept by a gate, not by prose.**
   `test_shipped_taint_sinks.py` refuses a shipped sink whose zone is one
   `AUTO_SINK_ZONE_MAP` derives, and a shipped sink that names a catalogued
   I/O primitive -- so `taint_sinks/` cannot regrow into the list 51e1d232f3
   retired.
4. Rows are limited to call edges the analyzers EMIT today. A sink nothing can
   reach is a coverage claim the tool cannot keep.

## Consequences

- Python `builtins.eval/exec/compile`, JS/TS `window.eval` and bare `eval`
  (newly emitted: `eval` joins `JS_KNOWN_GLOBAL_CALLS`), and
  `document.write/writeln` are sinks; a claim may name either zone.
- NOT covered, disclosed in the YAML headers: `el.innerHTML = s` /
  `outerHTML` (an assignment emits no edge, the WI-zumoz class),
  `insertAdjacentHTML` (emitted only on an untyped receiver), `new Function(s)`
  and a string `setTimeout(s)` (no call edge). Each is reach work, filed.
- INV-dadu's attacker-input half has a sink to reach. Its label question (the
  owner ruled `document.location` / `referrer` stay `host_secret`) is
  unchanged by this ADR.
