# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIP (Source Code Intelligence Protocol) → hypergumbo IR translation shim.

This subpackage hosts the language-agnostic translator that walks a SCIP
index and emits hypergumbo ``Symbol`` / ``Edge`` objects. SCIP is
Sourcegraph's protobuf-backed index format; rust-analyzer, scip-python,
scip-typescript, scip-java, and scip-clang all emit it. By anchoring
on SCIP rather than on a specific indexer we keep the bulk of the
translation code shared across language-backend packages
(hypergumbo-lang-rust-analyzer and friends, per the
``docs/hypergumbo-spec.md`` roadmap).

Why it lives in ``hypergumbo-core`` rather than in a per-language
package: the SCIP wire format is identical across indexers. Only the
descriptor-to-stable_id mapping and the interpretation of trait-
dispatch-style descriptor chains are language-specific, and those sit
behind helper hooks exposed by the per-language packages
(e.g. ``hypergumbo_lang_mainstream.rust_scip``). Keeping the translator
in core lets a single upgrade reach every SCIP-backed analyzer.

WI-mafut (this module) is Phase 1 of a multi-phase rollout; the current
surface is the ``parse_scip_symbol`` descriptor parser, which later
phases build on to emit Symbols/Edges from SCIP Documents/Occurrences.
"""
from .descriptor import (
    DescriptorKind,
    ScipDescriptor,
    ScipSymbol,
    parse_scip_symbol,
)

__all__ = [
    "DescriptorKind",
    "ScipDescriptor",
    "ScipSymbol",
    "parse_scip_symbol",
]
