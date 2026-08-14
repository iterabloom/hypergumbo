# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-fafol tripwire: js/ts attribute sources anchor to the FILE, not the
callable, so they can never start a taint flow.

DISCLOSURE, NOT AN ASSERTION OF CORRECTNESS. This pins behaviour that is
WRONG, on purpose, because INV-potuf's fix (typescript now derives javascript's
83 sinks instead of zero) makes it REACHABLE for a second language and the gap
must not be discovered again from scratch.

WHAT WAS MEASURED. Same claim, same sink, same enclosing function; only the
SOURCE KIND differs:

    os.hostname()        -> fs.writeFileSync   rc 1  violated   (found)
    process.env.API_KEY  -> fs.writeFileSync   rc 0  confirmed  (MISSED)

The edges say why. The sink is anchored to the function, the source to the
file, so the two never share a caller and no flow can be constructed:

    calls            src=...:3-6:dump:function
    module_attr_ref  src=...:1-1:file:file

``emit_module_attribute_refs`` accepts a ``caller_symbol`` and its docstring
says the root may be "a function body for per-function emission" — the helper
supports the right thing. All five tree-sitter call sites (js_ts, go, java,
cpp, rust) pass the file root and a file pseudo-symbol. Python is unaffected
because its own WI-guhok helper anchors to the enclosing callable, which is why
the identical shape IS caught there.

SCALE: 21 of javascript's 50 derived taint sources are ``kind=attribute``,
including the whole ``process.env`` / ``process.argv`` family — the canonical
way a Node program reads a secret. go / java / cpp / rust are unmeasured on
this axis rather than known-clear.

WHEN INV-fafol LANDS THIS TEST GOES RED. That is the intent. Replace it then
with the positive assertion that the source anchors to ``dump``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.js_ts import analyze_javascript

FIXTURE = """import * as fs from "fs";

export function dump(): void {
    const key: string = process.env.API_KEY as string;
    fs.writeFileSync("/tmp/out", key);
}
"""


@pytest.fixture
def edges(tmp_path: Path):
    (tmp_path / "leak.ts").write_text(FIXTURE)
    return analyze_javascript(tmp_path).edges


def _one(edges, needle: str, edge_type: str):
    hits = [e for e in edges
            if e.edge_type == edge_type and needle in e.dst]
    assert len(hits) == 1, [e.dst for e in edges]
    return hits[0]


def test_the_sink_call_anchors_to_the_enclosing_function(edges) -> None:
    """The control. Without it, "both anchor to the file" would look like a
    deliberate file-level convention rather than an asymmetry."""
    assert _one(edges, "writeFileSync", "calls").src.endswith(":dump:function")


def test_the_attribute_source_anchors_to_the_FILE_and_should_not(
    edges,
) -> None:
    """INV-fafol. Pinned as the CURRENT state, which is a defect."""
    src = _one(edges, "process.env", "module_attr_ref").src
    assert src.endswith(":file:file"), (
        "the attribute source now anchors somewhere else — if it anchors to "
        "the enclosing callable, INV-fafol is fixed: delete this test and "
        "assert the flow is found end to end instead"
    )
    assert not src.endswith(":dump:function")
