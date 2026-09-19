# SPDX-License-Identifier: AGPL-3.0-or-later
"""Recorded producer-shaped output of rust-analyzer 1.94.0 (ADR-0057 §12, WI-romuh).

rust-analyzer executes the analysed crate's ``build.rs``, is opt-in, and
never runs in CI, so every cross-backend test in this repository consumes
RECORDED output of the real producer rather than something derived from the
tree-sitter arm's own records (a control that cannot fail is not a control —
INV-dolud was exactly that, and stood for months). This module is the one
place those recordings are exposed; it lives under the rust-analyzer
package's ``recorded/`` directory (owned by the producer's package, not
shipped in its wheel) and is on pytest's ``pythonpath`` so core, mainstream
and rust-analyzer tests import it by its bare name. Feeding a test from
anywhere else is what ``scripts/check-recorded-producer-input`` refuses.

TWO RECORDINGS, ONE PRODUCER: ``rust-analyzer 1.94.0 (4a4ef49 2026-03-02)``.

1. THE SAMPLE CRATE — :data:`SAMPLE_SOURCE` / :data:`DEFINITIONS`, a 33-line
   ``src/lib.rs`` small enough to list every Definition inline. Invoked as
   ``rust-analyzer scip .`` on 2026-09-18 in a scratch crate whose
   ``Cargo.toml`` is::

    [package]
    name = "ra_sample"
    version = "0.1.0"
    edition = "2021"

    [lib]
    path = "src/lib.rs"

and whose ``src/lib.rs`` is :data:`SAMPLE_SOURCE` byte for byte. The
emitted ``index.scip`` was parsed with ``hypergumbo_core.scip._generated
.scip_pb2``; every global (non-``local``) Definition-role Occurrence is
listed in :data:`DEFINITIONS` exactly as emitted:

* ``range`` and ``enclosing_range`` are SCIP's 0-based ``[start_line,
  start_col, end_line, end_col]`` arrays, in the 3-element form
  ``[line, start_col, end_col]`` when the range is single-line — rust-analyzer
  uses the short form for every identifier token;
* ``kind`` is the last descriptor's ``DescriptorKind`` name (what
  ``scip/index.py`` maps onto ``Symbol.kind``); the name is that descriptor's
  name, which is what the SCIP arm emits as ``Symbol.name``.

Two facts these records pin, which the merge anchor declarations rely on:
every callable's ``range`` is the identifier TOKEN (single-line), so the SCIP
arm's ``span_role`` is ``token`` (INV-lodum); and every ``enclosing_range``
is the ITEM extent that the tree-sitter arm spans the same declaration over,
which is what makes an item-against-item comparison possible once the fold
lands (WI-kokiz).

Re-record (3 s) rather than edit: create the crate above, run
``rust-analyzer scip .``, parse ``index.scip`` and regenerate the table.

2. AARDVARK-DNS — the crate every ADR-0057 measurement was taken on, committed
   under ``aardvark_dns/crate/`` (upstream containers/aardvark-dns at commit
   ``4444d90fee``, Apache-2.0, ``LICENSE`` retained; ``src/``, ``build.rs``,
   ``Cargo.toml`` and ``Cargo.lock`` only) beside the ``index.scip`` that
   ``rust-analyzer scip <crate>`` emitted for it on 2026-09-18 in 6.3 s.
   The index is committed RAW (protobuf, 428 KB) because that is the byte
   stream production hands to ``translate_scip_to_hg``; the one edit is
   ``metadata.project_root``, rewritten from the recording machine's absolute
   path to ``file:///recorded/aardvark_dns/crate`` (nothing in the tree reads
   it). :func:`aardvark_dns_index_bytes` returns those bytes,
   :func:`aardvark_dns_crate_root` the crate, and :data:`AARDVARK_DNS_COUNTS`
   what the index contains — 16 documents, 169 global Definitions, 500 local
   Definitions (the ones WI-jikok drops), 3,543 reference occurrences — so a
   test that gets a different count is looking at a different recording.
   The producer's own log during the recording said ``Duplicate symbol:
   rust-analyzer cargo aardvark-dns 2.0.0-dev crate/`` — INV-kutid's
   per-target moniker collision, visible at the source.

   Under the ADR-0057 §10 anchors as declared on 2026-09-18 (tree-sitter:
   last ``::`` segment over the item span; SCIP: name as emitted over the
   identifier token), the live tree-sitter arm run on ``crate/`` pairs
   **148 of the 169** SCIP definitions with exactly one record each and
   none ambiguously; the 21 unpaired are the 18 module namespaces (no
   tree-sitter counterpart by design), 2 ``type`` aliases (``AardvarkResult``,
   ``ThreadHandleMap``; SCIP kind ``type_alias`` since WI-gapup) and 1
   ``static`` item (``DNSBACKEND``, ``variable``) — three constructs the
   tree-sitter arm emits no Symbol for at all (WI-bamar), an incumbent gap,
   not an anchor defect — :data:`AARDVARK_DNS_PAIRING` pins it, and
   :data:`AARDVARK_DNS_AGREEMENT_TALLY` pins that the paired records agree on
   ``kind`` 133 times out of 148. ADR-0057 §3 cites 138 of 169 from
   a scratch-script measurement on 2026-09-18; the committed rule and the
   committed input give 148, and the committed number is the one the merge
   pass (WI-kokiz) must reproduce.

Re-record either fixture rather than editing it: the crate is at
``aardvark_dns/crate``; run ``rust-analyzer scip <crate>`` from an empty
directory, normalise ``project_root``, replace ``index.scip``, and update the
counts here and the producer version above.
"""
from __future__ import annotations

from pathlib import Path
from typing import Final, TypedDict

PRODUCER_VERSION: Final = "rust-analyzer 1.94.0 (4a4ef49 2026-03-02)"

_HERE: Final = Path(__file__).resolve().parent

SAMPLE_SOURCE: Final = """\
pub fn top_level_add(x: i32, y: i32) -> i32 {
    x + y
}

fn private_helper(name: &str) -> String {
    name.to_string()
}

pub struct Counter {
    value: i32,
}

impl Counter {
    pub fn increment(&mut self, by: i32) -> i32 {
        self.value += by;
        self.value
    }

    fn reset(&mut self) {
        self.value = 0;
    }
}

pub trait Greeter {
    fn greet(&self, name: &str) -> String;
}

impl Greeter for Counter {
    fn greet(&self, name: &str) -> String {
        format!("hi {}", name)
    }
}
"""


class RecordedDefinition(TypedDict):
    symbol: str
    name: str
    kind: str
    range: list[int]
    enclosing_range: list[int]


DEFINITIONS: Final[list[RecordedDefinition]] = [
    {"symbol": "rust-analyzer cargo ra_sample 0.1.0 crate/",
     "name": "crate", "kind": "NAMESPACE",
     "range": [0, 0, 32, 0], "enclosing_range": [0, 0, 32, 0]},
    {"symbol": "rust-analyzer cargo ra_sample 0.1.0 top_level_add().",
     "name": "top_level_add", "kind": "METHOD",
     "range": [0, 7, 20], "enclosing_range": [0, 0, 2, 1]},
    {"symbol": "rust-analyzer cargo ra_sample 0.1.0 private_helper().",
     "name": "private_helper", "kind": "METHOD",
     "range": [4, 3, 17], "enclosing_range": [4, 0, 6, 1]},
    {"symbol": "rust-analyzer cargo ra_sample 0.1.0 Counter#",
     "name": "Counter", "kind": "TYPE",
     "range": [8, 11, 18], "enclosing_range": [8, 0, 10, 1]},
    {"symbol": "rust-analyzer cargo ra_sample 0.1.0 Counter#value.",
     "name": "value", "kind": "TERM",
     "range": [9, 4, 9], "enclosing_range": [9, 4, 14]},
    {"symbol": "rust-analyzer cargo ra_sample 0.1.0 impl#[Counter]increment().",
     "name": "increment", "kind": "METHOD",
     "range": [13, 11, 20], "enclosing_range": [13, 4, 16, 5]},
    {"symbol": "rust-analyzer cargo ra_sample 0.1.0 impl#[Counter]reset().",
     "name": "reset", "kind": "METHOD",
     "range": [18, 7, 12], "enclosing_range": [18, 4, 20, 5]},
    {"symbol": "rust-analyzer cargo ra_sample 0.1.0 Greeter#",
     "name": "Greeter", "kind": "TYPE",
     "range": [23, 10, 17], "enclosing_range": [23, 0, 25, 1]},
    {"symbol": "rust-analyzer cargo ra_sample 0.1.0 Greeter#greet().",
     "name": "greet", "kind": "METHOD",
     "range": [24, 7, 12], "enclosing_range": [24, 4, 42]},
    {"symbol": "rust-analyzer cargo ra_sample 0.1.0 impl#[Counter][Greeter]greet().",
     "name": "greet", "kind": "METHOD",
     "range": [28, 7, 12], "enclosing_range": [28, 4, 30, 5]},
]


def lines_of(scip_range: list[int]) -> tuple[int, int]:
    """1-based ``(start_line, end_line)`` of a SCIP range, either form."""
    if len(scip_range) == 3:
        return scip_range[0] + 1, scip_range[0] + 1
    return scip_range[0] + 1, scip_range[2] + 1


def callable_item_to_token_lines() -> dict[tuple[int, int], tuple[int, int]]:
    """``{item (start, end): token (start, end)}`` for every recorded callable.

    The shape the ``rust_scip`` parity test consumes: keys are the ITEM
    spans (``enclosing_range``, what rust.py spans a callable over), values
    the identifier-token spans rust-analyzer puts in the Definition
    ``range`` — what production actually feeds the parity helper.
    """
    return {
        lines_of(d["enclosing_range"]): lines_of(d["range"])
        for d in DEFINITIONS
        if d["kind"] == "METHOD"
    }


# ---------------------------------------------------------------------------
# aardvark-dns
# ---------------------------------------------------------------------------

AARDVARK_DNS_UPSTREAM_COMMIT: Final = "4444d90fee"

AARDVARK_DNS_COUNTS: Final[dict[str, int]] = {
    "documents": 16,
    "global_definitions": 169,
    "local_definitions": 500,
    "reference_occurrences": 3543,
}

#: What the declared anchors pair on the committed input (see docstring).
AARDVARK_DNS_PAIRING: Final[dict[str, int]] = {
    "scip_definitions": 169,
    "paired": 148,
    "ambiguous": 0,
    "unpaired_namespace": 18,
    "unpaired_type_alias": 2,
    "unpaired_variable": 1,
}

#: On the 148 paired records, how often the two arms say the same ``kind``.
#: (Named without the word "kind": the axis-drift collector reads any
#: identifier containing it as a Symbol.kind value set, and these are counters.)
#: (WI-gapup: the SCIP arm reads the producer's declared kind). The 15
#: disagreements are every ``const`` item: SCIP declares ``Constant`` and the
#: importer maps it to the registry's ``constant``; the tree-sitter arm emits
#: ``variable`` for module-level ``const`` and ``static`` alike. The SCIP side
#: is the more precise one, so the contest is left to arbitration rather than
#: shaped away. Statics agree (``variable`` on both).
AARDVARK_DNS_AGREEMENT_TALLY: Final[dict[str, int]] = {
    "paired": 148,
    "agree": 133,
    "disagree": 15,
    "disagree_constant_vs_variable": 15,
    # WI-binis, the provenance slot on the 148 merged records: ``span`` is
    # contested (token vs item) on all but the 2 unit enum variants, whose
    # tree-sitter span IS the bare name; ``stable_id`` is contested (moniker
    # hash vs rust.py's) on all but the 14 single-line items for which the
    # parity helper already reaches rust.py's id (INV-dolud's one case).
    "span_alternatives": 146,
    "stable_id_alternatives": 134,
}

#: The SCIP arm's occurrence edges on the recording and their tree-sitter
#: twins (WI-zapuk). A "twin" is a tree-sitter edge on the same (src, dst)
#: after both endpoints are paired through the declared anchors. The nine
#: twins that still differ are tuple-struct / variant CONSTRUCTION sites the
#: tree-sitter arm labels ``calls`` while the SCIP target is a struct or
#: variant. (Named without "edge type": see AARDVARK_DNS_AGREEMENT_TALLY.)
AARDVARK_DNS_CALL_SITE_TALLY: Final[dict[str, int]] = {
    "scip_edges": 498,
    "calls": 130,
    "references": 368,
    "field_target_references": 113,
    "twins": 130,
    "twins_calls_calls": 121,
    "twins_references_calls": 9,
    # After the merge pass rewires endpoints (WI-kokiz), the 121 calls/calls
    # twins fall onto 93 DISTINCT (src, dst, edge_type) keys — SCIP emits one
    # edge per call occurrence and within-arm dedup already folds repeats —
    # so deduplicate_edges removes exactly 93 cross-arm duplicates.
    "shared_call_keys_after_fold": 93,
    # Edges the fold absorbs into those 93 survivors (WI-binis): the 121 SCIP
    # twins plus each arm's own repeats of the same key — a producer's repeated
    # call sites are absorbed the same way, their lines unioned into call_lines.
    "edges_absorbed_by_fold": 149,
    # ADR-0057 §14 (WI-lihis), after the fold and the resolution verdict: of
    # the tree-sitter call edges that end at an external stub, how many share
    # a src, a call line, the edge type AND the declared callee name with a
    # resolved edge from a producer they lack (superseded — every one is a
    # `wrap` SCIP resolves to `Result::wrap`), and how many share the site and
    # type but name a DIFFERENT callee (two calls on one line: never demoted).
    "stub_calls": 244,
    "superseded_stubs": 3,
    "co_located_same_type_not_superseded": 24,
}

# What `hypergumbo backend-agreement` (ADR-0057 §5, WI-dajif) reads off the
# two-arm artifact — the merge pass, edge dedup, the resolution verdict and
# supersession, as `_two_arm_artifact()` in test_recorded_fixtures builds it.
# These are the numbers in docs/audits/0019-backend-agreement-rust-aardvark-dns.md.
# Pairing per Symbol category: every one of the 148 paired records has both
# producers; the 21 SCIP-only records are the namespaces, the two `type`
# aliases and the one `static` (WI-bamar) the tree-sitter arm emits nothing for.
AARDVARK_DNS_PAIRED_PER_CATEGORY = {
    "enum": 3, "field": 38, "function": 52, "method": 27, "struct": 10, "trait": 1, "variable": 17,
}
AARDVARK_DNS_SCIP_ONLY_PER_CATEGORY = {"namespace": 18, "type_alias": 2, "variable": 1}
# Per attribute over the 148 paired records: (agree, disagree, rust only, rust_analyzer only).
# `name`: the 38 fields and 27 methods tree-sitter qualifies as `Type::member`
# and SCIP emits bare — the anchors' name_key folds them, the carried scalar
# keeps the incumbent's form. `is_exported`: one-sided to the incumbent on all
# 148 — it computes exportedness (`"pub" in modifiers`) and the SCIP arm
# declares it computes none (`observes=()`), so the dataclass default it would
# otherwise have contributed on every record is not a candidate (INV-huboz). `span`: the
# item/token role split of §10, by construction. `stable_id`: derived from the
# attributes above it; the 14 one-sided are the struct/enum/trait records the
# SCIP arm emits without one. The one-sided rust-only columns are attributes
# the SCIP translation never fills (signature, docstring, modifiers, qualified_name).
AARDVARK_DNS_ATTRIBUTE_AGREEMENT = {
    "docstring": (0, 0, 63, 0),
    "is_exported": (0, 0, 148, 0),
    "kind": (133, 15, 0, 0),
    "modifiers": (0, 0, 35, 0),
    "name": (83, 65, 0, 0),
    "qualified_name": (0, 0, 148, 0),
    "signature": (0, 0, 125, 0),
    "span": (2, 146, 0, 0),
    "stable_id": (0, 134, 0, 14),
}
# Per (edge type, resolved) after the fold: (both producers, rust only, rust_analyzer only).
# The 93 "both" calls are the corroborated edges; the incumbent emits no
# `references` edge and the SCIP arm no edge to an external stub.
AARDVARK_DNS_EDGE_OVERLAP = {
    ("calls", True): (93, 18, 7),
    ("calls", False): (0, 244, 0),
    ("decorated_by", False): (0, 1, 0),
    ("implements", False): (0, 2, 0),
    ("module_attr_ref", False): (0, 26, 0),
    ("references", True): (0, 0, 269),
}


def aardvark_dns_crate_root() -> Path:
    """The committed aardvark-dns crate the index was recorded on."""
    return _HERE / "aardvark_dns" / "crate"


def aardvark_dns_index_bytes() -> bytes:
    """The recorded ``index.scip``, exactly as ``translate_scip_to_hg`` reads it."""
    return (_HERE / "aardvark_dns" / "index.scip").read_bytes()
