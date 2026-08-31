# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-lipis: what a Go handle-wrapper WRAPS is a per-call-site fact.

``go.yaml`` files ``bufio.{NewScanner,NewReader}`` as ``ipc_recv`` on the note
*"When wrapping os.Stdin"* — a condition no catalogue row can enforce, because
the row sees the CALLEE and the answer is in the ARGUMENT. Same shape as bash's
redirect target (INV-nular): one row, a boundary that depends on the call site,
so the analyzer stamps the discriminator and the consumers read it. The key is
the same key, ``io_target_kind``, because it is the same question — what does
this call site actually touch?

MEASURED, on the ADR-0049 cohort's Go repositories, via hypergumbo's own
reaching-def solver. Of the 83 bare-local ``bufio.New*`` sites whose origin it
resolves: **63 wrap an ``os.Open`` handle** (``fs_read``, deliberately not a
taint source), 18 another RHS, 3 an HTTP body, 1 a buffer, and **zero wrap
``os.Stdin``**. The row's stated condition holds nowhere in that population.

ONLY THE PROVABLE CASES ARE STAMPED. A bare local — 64.8% of the measured
sites — stamps nothing, because recovering its origin is a dataflow question
and answering it syntactically would be guessing. Absence classifies exactly as
before.
"""

from pathlib import Path

from hypergumbo_lang_mainstream.go import analyze_go


def _kinds(tmp: Path, body: str) -> dict:
    """Stamped kind per WRAPPER call site, keyed on the callee.

    Keyed on ``dst``, not on the line: ``bufio.NewScanner(strings.NewReader(s))``
    emits an edge for BOTH calls on ONE line, so a line-keyed map silently kept
    whichever came last and reported the wrapper as unstamped. Keyed on the
    bufio destination the two are distinguishable, which is what the assertions
    below actually mean to compare.

    And restricted to ``calls``: the ``import "bufio"`` line also produces a
    ``go:bufio:`` destination, with no meta, which read as a second unstamped
    wrapper site.
    """
    (tmp / "m.go").write_text(body, encoding="utf-8")
    result = analyze_go(tmp)
    assert not result.skipped
    return {
        e.dst: (e.meta or {}).get("io_target_kind")
        for e in result.edges
        if e.edge_type == "calls" and e.dst.startswith("go:bufio:")
    }


def test_an_in_memory_reader_is_stamped_in_memory(tmp_path: Path) -> None:
    kinds = _kinds(tmp_path, '''package main

import (
	"bufio"
	"strings"
)

func f(s string) {
	sc := bufio.NewScanner(strings.NewReader(s))
	_ = sc
}
''')
    assert list(kinds.values()) == ["in_memory"]


def test_a_bytes_buffer_is_stamped_in_memory(tmp_path: Path) -> None:
    """The family, not one spelling — bytes.NewReader/NewBuffer are the same."""
    kinds = _kinds(tmp_path, '''package main

import (
	"bufio"
	"bytes"
)

func f(b []byte) {
	r := bufio.NewReader(bytes.NewReader(b))
	_ = r
}
''')
    assert list(kinds.values()) == ["in_memory"]


def test_stdin_is_stamped_std_stream(tmp_path: Path) -> None:
    """The row's own stated case, and it must stay distinguishable."""
    kinds = _kinds(tmp_path, '''package main

import (
	"bufio"
	"os"
)

func f() {
	sc := bufio.NewScanner(os.Stdin)
	_ = sc
}
''')
    assert list(kinds.values()) == ["std_stream"]


def test_a_bare_local_stamps_nothing(tmp_path: Path) -> None:
    """THE ABSTENTION, and it is 64.8% of the real population.

    ``f`` here is an ``os.Open`` handle, which the analyzer cannot see from the
    call site. Stamping a guess would be worse than stamping nothing: the
    consumers treat absence as "classify as before", which is the only safe
    default in the direction that removes findings.
    """
    kinds = _kinds(tmp_path, '''package main

import (
	"bufio"
	"os"
)

func f(p string) {
	h, _ := os.Open(p)
	sc := bufio.NewScanner(h)
	_ = sc
}
''')
    assert list(kinds.values()) == [None]


def test_an_unrelated_constructor_is_not_stamped(tmp_path: Path) -> None:
    """CONTROL on the wrapper set. ``bufio.NewWriter`` is not a read wrapper.

    It wraps an in-memory buffer here, so a stamp keyed on the ARGUMENT alone
    would mark it ``in_memory`` and a source-side gate would then be reasoning
    about a WRITER. The wrapper set is consulted first for exactly that reason.
    """
    kinds = _kinds(tmp_path, '''package main

import (
	"bufio"
	"bytes"
)

func f(b *bytes.Buffer) {
	w := bufio.NewWriter(b)
	_ = w
}
''')
    assert list(kinds.values()) == [None]
