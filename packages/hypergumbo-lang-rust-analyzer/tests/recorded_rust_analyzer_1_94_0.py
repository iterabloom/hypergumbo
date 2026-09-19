# SPDX-License-Identifier: AGPL-3.0-or-later
"""Recorded producer-shaped output of rust-analyzer 1.94.0 (ADR-0057 §12).

rust-analyzer executes the analysed crate's ``build.rs``, is opt-in, and
never runs in CI, so every cross-backend test in this package consumes
RECORDED output of the real producer rather than something derived from the
tree-sitter arm's own records (a control that cannot fail is not a control —
INV-dolud was exactly that, and stood for months).

PRODUCER: ``rust-analyzer 1.94.0 (4a4ef49 2026-03-02)``, invoked as
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
"""
from __future__ import annotations

from typing import Final, TypedDict

PRODUCER_VERSION: Final = "rust-analyzer 1.94.0 (4a4ef49 2026-03-02)"

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
