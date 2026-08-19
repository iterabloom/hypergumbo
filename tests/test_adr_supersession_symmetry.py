# SPDX-License-Identifier: AGPL-3.0-or-later
"""Supersession between ADRs must be greppable from both endpoints.

``docs/adr/README.md`` §"ADR lifecycle" makes this load-bearing: "Both
endpoints name each other: the predecessor's ``Status:`` names the
successor ADR number, and the successor carries a ``Supersedes:
ADR-NNNN (§sections)`` line... Do not record it only as one-sided prose."

The failure this guards against is quiet. A one-sided record still reads
correctly from the side that has it, so nothing surfaces until a reader
arrives from the other side — at which point a superseded decision looks
current. Three pairs had drifted when this test was written
(ADR-0024/0038 and both ADR-3aaa extensions).

**Parentheticals are stripped before ADR numbers are extracted**, and
that is not a detail. A ``Supersedes:`` line routinely *cites* an
unrelated ADR to explain why something is dead — ADR-0035's line names
ADR-0032 only inside "(dead since ADR-0032 removed ``canonical_name``)".
Reading numbers out of the raw line reports that citation as a
supersession claim, which is exactly the false positive that motivated
the strip. A citation is not a claim.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ADR_DIR = REPO_ROOT / "docs" / "adr"

_PAREN = re.compile(r"\([^()]*\)")
_ADR_NUM = re.compile(r"ADR[-\s]?(\d{4}|3[a-z]{3})")


def _strip_parentheticals(text: str) -> str:
    """Drop parenthesised spans, which carry citations rather than claims."""
    prev = None
    while prev != text:
        prev, text = text, _PAREN.sub(" ", text)
    return text


def _field(head: str, name: str) -> str:
    match = re.search(
        rf"^\s*(?:[-*]\s*)?\*{{0,2}}{name}\*{{0,2}}\s*:\s*(.*)$", head, re.M
    )
    return match.group(1) if match else ""


def _adr_records() -> dict[str, dict[str, set[str]]]:
    records = {}
    for path in sorted(ADR_DIR.glob("*.md")):
        if path.name == "README.md":
            continue
        head = path.read_text(encoding="utf-8")[:6000]
        status = _field(head, "Status")
        _, _, after = status.partition("uperseded by")
        claimed_by = set(_ADR_NUM.findall(_strip_parentheticals(after)))
        claimed_by |= set(
            _ADR_NUM.findall(_strip_parentheticals(_field(head, "Superseded by")))
        )
        records[path.name.split("-")[0]] = {
            "supersedes": set(
                _ADR_NUM.findall(_strip_parentheticals(_field(head, "Supersedes")))
            ),
            "superseded_by": claimed_by,
        }
    return records


def test_supersession_is_symmetric() -> None:
    """Every supersession claim is recorded at both endpoints."""
    records = _adr_records()
    breaks = []
    for num, rec in records.items():
        for target in rec["supersedes"]:
            other = records.get(target)
            if other is None:
                breaks.append(f"ADR-{num} supersedes ADR-{target}, which has no file")
            elif num not in other["superseded_by"]:
                breaks.append(
                    f"ADR-{num} claims to supersede ADR-{target}, but ADR-{target} "
                    f"names {sorted(other['superseded_by']) or 'nobody'} as superseding it"
                )
        for source in rec["superseded_by"]:
            other = records.get(source)
            if other is None:
                breaks.append(f"ADR-{num} names ADR-{source}, which has no file")
            elif num not in other["supersedes"]:
                breaks.append(
                    f"ADR-{num} says ADR-{source} supersedes it, but ADR-{source} "
                    f"supersedes {sorted(other['supersedes']) or 'nothing'}"
                )
    assert not breaks, "Asymmetric supersession records:\n  " + "\n  ".join(
        sorted(breaks)
    )


def test_citations_inside_parentheses_are_not_supersession_claims() -> None:
    """Positive control: the stripper actually changes the verdict.

    Without this, a stripper that silently matched nothing would leave
    the symmetry test passing for the wrong reason.
    """
    line = (
        "ADR-0014's contract; the policy "
        "(dead since ADR-0032 removed `canonical_name`)"
    )
    assert set(_ADR_NUM.findall(line)) == {"0014", "0032"}
    assert set(_ADR_NUM.findall(_strip_parentheticals(line))) == {"0014"}
