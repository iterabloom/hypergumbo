<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# hypergumbo-lang-rust-analyzer

SCIP-backed Rust analyzer for hypergumbo.

This optional package integrates `rust-analyzer scip` output into hypergumbo,
providing precise type-resolved symbols and call edges for Rust workspaces
beyond what the tree-sitter `rust.py` analyzer can recover. It is designed
as an opt-in alternative to `rust.py`, not a replacement.

## Status

Shipped end-to-end. Activate with the `--backend rust-analyzer` root flag
(also settable via `HYPERGUMBO_RUST_ANALYZER=1`). Install with
`pipx install 'hypergumbo[rust-analyzer]'`, or
`pipx inject hypergumbo hypergumbo-lang-rust-analyzer` when `hypergumbo` is
already installed.

When `--backend rust-analyzer` is requested but the integration package or
the `rust-analyzer` binary is missing/broken (the binary is smoke-tested
with `rust-analyzer --version`), the CLI exits non-zero with a pointer to
the install or to `rustup component add rust-analyzer`, rather than
silently falling through to the tree-sitter `rust.py` analyzer.

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

## What is not emitted

- **Function-local bindings.** rust-analyzer emits a SCIP `local <n>` symbol
  for every `let`, parameter and pattern binding, numbered per document. None
  becomes a hypergumbo `Symbol` and no edge may point at one (WI-jikok /
  INV-kukiz, ruled 2026-09-18). The id is a document-scoped index, so `local 0`
  is a different binding in every file: minted, they were 75% of this backend's
  nodes at a 39.7% `stable_id` collision rate, and 329 of the 455 edges pointing
  at them resolved across files. Every other hypergumbo backend leaves locals
  out of the map for the same reason — a binding no other file can name is not
  part of a behavior map.
- **Calls into dependencies and the standard library.** A SCIP symbol with no
  in-workspace definition resolves to `None` and the edge is dropped rather
  than dangling into an `external_symbol` boundary node (WI-gojum
  sub-component 1, parked).
