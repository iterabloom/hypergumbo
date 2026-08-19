# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ADR index must not be gentler about an ADR than the ADR itself is.

``docs/adr/README.md`` §"ADR lifecycle" makes the status line load-bearing:
"The truth lives at the top... a ``grep`` of the status line must not lie."
That rule is written about each ADR *file*, and the files comply. The index
table in the README is a second copy of the same claim, and it is the copy a
reader meets first — so a bare "Accepted" there, against a file that says a
decision only partly shipped, reproduces exactly the failure the rule exists
to prevent, one level up.

Two had drifted when this test was written. **ADR-0036** reads "Accepted" in
the index while its own status says the decision "shipped only partially" —
Ruling 2's round-trip is advisory and Rulings 1 and 3 never landed. **ADR-0024**
reads "Accepted" while its file records that open question 1 was partially
superseded by ADR-0038.

The check is deliberately one-directional and coarse. It does not require the
strings to match — the index cell is a summary and should stay short. It
requires only that the index not *drop a qualifier the file carries*:
if the file says superseded, the index must say superseded; if the file
qualifies how much landed, the index must qualify it too. An index that is
more detailed than the file is fine.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ADR_DIR = REPO_ROOT / "docs" / "adr"
README = ADR_DIR / "README.md"

#: Status wording that qualifies how completely a decision took effect. Losing
#: one of these on the way into the index is what turns a partial landing into
#: an apparent full one.
_QUALIFIERS = {
    "superseded": r"supersed",
    "partial": r"partial|only partly|not landed|in progress|deferred|pending",
}


def _flags(status: str) -> set[str]:
    low = status.lower()
    return {name for name, pat in _QUALIFIERS.items() if re.search(pat, low)}


def _file_statuses() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(ADR_DIR.glob("*.md")):
        if path.name == "README.md":
            continue
        head = "".join(path.read_text(encoding="utf-8").splitlines(keepends=True)[:15])
        m = re.search(r"^\s*[-*]?\s*\**Status\**\s*:(.*)$", head, re.M)
        assert m, f"{path.name} has no top-of-file Status line"
        out[path.name.split("-")[0]] = re.sub(r"\s+", " ", m.group(1)).strip()
    return out


def _index_statuses() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in README.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*\[([0-9a-z]+)\]\([^)]+\)\s*\|([^|]*)\|([^|]*)\|", line)
        if m:
            out[m.group(1)] = m.group(3).strip()
    return out


def test_every_adr_file_appears_in_the_index() -> None:
    """A reader scanning the index must not miss a live ADR."""
    files = _file_statuses()
    indexed = _index_statuses()
    # Reclassified stubs are redirects, not principles; the README documents
    # their absence from the table in prose immediately below it.
    live = {n for n, s in files.items() if "reclassified" not in s.lower()}
    missing = sorted(live - set(indexed))
    assert not missing, f"ADRs on disk but absent from the README index: {missing}"


def test_index_does_not_drop_a_qualifier_the_adr_carries() -> None:
    """The index may be shorter than the file; it may not be softer."""
    files = _file_statuses()
    indexed = _index_statuses()
    understated = []
    for num, file_status in sorted(files.items()):
        if num not in indexed:
            continue
        lost = _flags(file_status) - _flags(indexed[num])
        if lost:
            understated.append(
                f"ADR-{num} drops {sorted(lost)}\n"
                f"      index: {indexed[num][:100]}\n"
                f"      file : {file_status[:100]}"
            )
    assert not understated, (
        "the README index understates these ADRs' own status lines:\n  "
        + "\n  ".join(understated)
    )
