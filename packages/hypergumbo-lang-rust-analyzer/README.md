<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# hypergumbo-lang-rust-analyzer

SCIP-backed Rust analyzer for hypergumbo.

This optional package integrates `rust-analyzer scip` output into hypergumbo,
providing precise type-resolved symbols and call edges for Rust workspaces
beyond what the tree-sitter `rust.py` analyzer can recover. It is designed
as an opt-in alternative to `rust.py`, not a replacement.

## Status

Slice A: pure-Python translation surface from parsed SCIP `Index` bytes to
`(symbols, edges)`. No live `rust-analyzer` invocation yet — that arrives
in Slice B alongside analyzer-registry wiring and the opt-in flag
(`HYPERGUMBO_RUST_ANALYZER` env var or `--backend rust-analyzer` CLI flag).

## Why SCIP, not LSP

Rust-analyzer emits SCIP with a single shot of `rust-analyzer scip` instead
of requiring a long-lived LSP session. SCIP is slower than tree-sitter
(~10× at every realistic size per WI-zakub), so this backend is opt-in and
falls through to `rust.py` when unavailable or not requested.

## Stable-ID parity

`rust.py` and this analyzer both produce `stable_id`s via
`hypergumbo_lang_mainstream.rust_scip.compute_rust_stable_id_from_source`,
so cross-pass dedup works. Shared symbols carry the same `stable_id` under
both backends; rust-analyzer-only symbols (e.g. trait-resolved method
dispatch) extend the id space with SCIP-only suffixes.

## Upstream shim

Symbol / edge emission builds on `hypergumbo_core.scip.*`:

- `scip_index_to_symbols` — `Document` walk → `Symbol` objects.
- `scip_index_to_edges` — `SymbolInformation.relationships` → `Edge`s.
- `scip_index_to_call_edges` — non-Definition `Occurrence` → calls / references
  edges via span-enclosure resolution.

Rust-analyzer's `Relationship` set is empty (per WI-zakub), so the primary
edge source here is `scip_index_to_call_edges`.
