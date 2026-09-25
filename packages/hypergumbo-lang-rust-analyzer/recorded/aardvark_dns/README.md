<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Recorded rust-analyzer output for aardvark-dns (ADR-0057 §12, WI-romuh)

This directory is a **recorded producer fixture**: the crate every ADR-0057
measurement was taken on, beside the SCIP index rust-analyzer emitted for it.
Tests import it through `recorded_rust_analyzer_1_94_0.py` one level up; that
module's docstring is the authoritative description (producer version, counts,
the pairing the declared anchors give, and how to re-record).

| item | provenance |
| --- | --- |
| `crate/` | [containers/aardvark-dns](https://github.com/containers/aardvark-dns) at commit `4444d90fee`, **Apache-2.0** (`crate/LICENSE` is the upstream licence, unmodified). Only `src/`, `build.rs`, `Cargo.toml` and `Cargo.lock` are kept — the files rust-analyzer needs to index and the ones the tree-sitter arm reads. |
| `index.scip` | `rust-analyzer 1.94.0 (4a4ef49 2026-03-02)`, `rust-analyzer scip <crate>` run from an empty directory on 2026-09-18 (6.3 s). Committed raw (protobuf), because that is the byte stream `translate_scip_to_hg` reads in production. One field was rewritten after recording: `metadata.project_root`, from the recording machine's absolute path to `file:///recorded/aardvark_dns/crate`. Nothing in the tree reads it. |

**Why the source is committed too.** A pairing count needs both arms: the
recorded SCIP arm and the tree-sitter arm run *live* on the same source. CI has
no corpus checkout, so the crate travels with its recording. Recording the
tree-sitter arm's output instead would tie the fixture to hypergumbo's own id
format and stop exercising the incumbent.

**Do not edit either file by hand.** Re-record: run `rust-analyzer scip` on
`crate/` from an empty directory, normalise `project_root`, replace
`index.scip`, and update the counts and producer version in the module.
