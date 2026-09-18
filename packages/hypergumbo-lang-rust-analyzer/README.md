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

## Stable-ID parity: a helper exists; parity does not hold in production

`reassign_rust_stable_ids` asks
`hypergumbo_lang_mainstream.rust_scip.compute_rust_stable_id_from_source` for
the `rust.py` id of each SCIP function, and the helper reproduces `rust.py`'s
id byte for byte **when given the item's span**. Production never gives it
that: rust-analyzer's Definition occurrence is the identifier token (line 14
for a method whose body runs 14–17), the helper requires both endpoints to
match, so it abstains for every multi-line item and the SCIP symbol keeps its
`sha256(moniker)` id. Measured on aardvark-dns (INV-dolud, 2026-09-18): **0 of
52** functions emitted by both arms share a `stable_id`. The two arms carry
independent identities today, and a consumer joining on `stable_id` sees two
records for one function. Whether they should share one — and what a dedup
would then do — is WI-gojum's parked question (ADR-0012 Steps 2–3); until it
is ruled, the helper is readiness, not a contract. An earlier version of this
section said "so cross-pass dedup works"; it did not, and no cross-pass dedup
has ever been written.

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
