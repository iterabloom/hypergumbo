# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-fafol LANDED: js/ts attribute sources anchor to the enclosing callable.

This file was a TRIPWIRE pinning the defect — it asserted the source anchored
to the FILE and said, in as many words, "when INV-fafol lands this test goes
red; replace it with the positive assertion". It went red on exactly the
change it was waiting for, and this is that replacement.

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

THE FIX. ``emit_module_attribute_refs`` now takes ``enclosing_symbols`` and
resolves the innermost callable whose line span contains the read, falling
back to the caller's pseudo-symbol when a read really is module-level. Span
containment is used rather than a walk to a function NODE so the rule needs no
per-language knowledge of callable node kinds and lives in one place; a parity
test (``test_attr_ref_anchor_parity.py``) asserts all five call sites pass it,
so the next analyzer fails a test instead of silently repeating this.

MEASURED END TO END on a JS file holding both shapes in sibling functions:
1 flow before, 2 after — ``process.env.API_KEY -> fs.writeFileSync`` is now
found alongside the ``os.hostname()`` control that always was.
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


def test_the_attribute_source_anchors_to_the_ENCLOSING_CALLABLE(edges) -> None:
    """INV-fafol, the positive assertion the tripwire asked for.

    21 of javascript's 50 derived taint sources are ``kind=attribute``,
    including the whole ``process.env`` / ``process.argv`` family — the
    canonical way a Node program reads a secret. None of them could start a
    flow while this anchored to the file.
    """
    src = _one(edges, "process.env", "module_attr_ref").src
    assert src.endswith(":dump:function"), (
        f"the attribute source must anchor to the callable that reads it; "
        f"got {src!r}"
    )
    assert not src.endswith(":file:file")


def test_source_and_sink_now_share_a_caller(edges) -> None:
    """The property that actually matters, stated directly.

    Propagation pairs a source and a sink that share a caller. Asserting each
    end's anchor separately would still pass if a later change moved BOTH to
    some third symbol, so assert the relationship rather than the two facts.
    """
    src_anchor = _one(edges, "process.env", "module_attr_ref").src
    sink_anchor = _one(edges, "writeFileSync", "calls").src
    assert src_anchor == sink_anchor
