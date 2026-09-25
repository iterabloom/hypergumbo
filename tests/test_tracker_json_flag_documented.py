# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rot-guard for the tracker's ``--json`` flag position (WI-dolaf).

``hypergumbo-tracker`` registers ``--json`` on the TOP-LEVEL parser, before
``add_subparsers``, so it is a global flag: ``tracker --json show <ID>`` works
and ``tracker show <ID> --json`` exits 2. That asymmetry is ordinary argparse,
but its failure mode is not ordinary — argparse writes the usage error to
STDERR and leaves STDOUT empty, so a caller that pipes stdout into a JSON
parser and discards stderr cannot distinguish "you invoked the CLI wrong" from
"that item does not exist". Two consecutive agent sessions produced full
columns of false UNRESOLVED that way, because AGENTS.md — the always-loaded
governance text — documented the trailing form.

So the docs are the mitigation, and this locks them. It asserts:

  1. the ANCHOR: ``--json`` is still registered exactly once, on the top-level
     parser, ahead of ``add_subparsers``. If that stops being true — notably if
     WI-dolaf's structural half lands and the subparsers accept ``--json`` too
     — the trailing form becomes valid, this guard's premise is void, and this
     test is the thing that says so instead of failing mysteriously;
  2. no documentation surface shows the trailing form in a code span;
  3. AGENTS.md positively shows the working form (a guard that only forbids
     can be satisfied by deleting the documentation).

Scanning is per BACKTICK SPAN, not per line: prose that discusses the hazard
legitimately mentions both orders in one sentence, and a line-level regex
cannot tell that apart from an actual bad invocation.

Pure file reads; no source import, so it neither contributes nor consumes
package coverage — same shape as ``test_hg_github_token_documented.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACKER_CLI = (
    REPO_ROOT
    / "packages"
    / "hypergumbo-tracker"
    / "src"
    / "hypergumbo_tracker"
    / "cli.py"
)
AGENTS_MD = REPO_ROOT / "AGENTS.md"

# Subcommands a reader might plausibly write a trailing --json after. Not the
# full set: this guard is about the documented reading commands, and a name
# that never appears beside --json in prose adds only false-positive surface.
SUBCOMMANDS = (
    "show",
    "list",
    "ready",
    "log",
    "count-todos",
    "check-messages",
    "validate",
    "clusters",
)

# Directories with no documentation-for-agents role. `.git` is not tracked;
# the tracker package's own tests invoke main([...]) with argv LISTS, where
# ordering is exercised deliberately rather than modelled for a reader.
SKIP_PARTS = (".git", "node_modules", ".venv", "__pycache__")

CODE_SPAN = re.compile(r"`([^`\n]+)`")


def _doc_files() -> list[Path]:
    """Every markdown surface, plus the two .py files that carry agent-facing
    prose about tracker invocations in docstrings/constants."""
    files = [
        p
        for p in REPO_ROOT.rglob("*.md")
        if not any(part in SKIP_PARTS for part in p.parts)
    ]
    files.append(
        REPO_ROOT
        / "packages"
        / "hypergumbo-tracker"
        / "src"
        / "hypergumbo_tracker"
        / "setup.py"
    )
    files.append(REPO_ROOT / ".agent" / "hooks" / "_shared" / "on_transcript_change.py")
    return [p for p in files if p.is_file()]


def _trailing_json_spans(text: str) -> list[str]:
    """Code spans that put --json AFTER a tracker subcommand."""
    bad = []
    for span in CODE_SPAN.findall(text):
        if "--json" not in span:
            continue
        tokens = span.split()
        try:
            json_at = tokens.index("--json")
        except ValueError:
            continue  # --json appears glued to another token, not as an arg
        for name in SUBCOMMANDS:
            if name in tokens and tokens.index(name) < json_at:
                bad.append(span)
                break
    return bad


def test_json_is_registered_once_on_the_top_level_parser() -> None:
    """ANCHOR. The whole doc contract exists because --json is global-only."""
    source = TRACKER_CLI.read_text()
    registrations = source.count('"--json", action="store_true"')
    assert registrations == 1, (
        f"expected exactly 1 --json registration in {TRACKER_CLI}, found "
        f"{registrations}. If the subparsers now accept --json (WI-dolaf's "
        f"structural half), the trailing form is valid and this rot-guard "
        f"should be RELAXED, not worked around."
    )
    json_at = source.index('"--json", action="store_true"')
    subparsers_at = source.index("add_subparsers(")
    assert json_at < subparsers_at, (
        "--json is no longer registered ahead of add_subparsers; it may no "
        "longer be a global-only flag. Re-examine this guard's premise."
    )


def test_no_doc_surface_shows_the_trailing_form() -> None:
    offenders: list[str] = []
    for path in _doc_files():
        for span in _trailing_json_spans(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(REPO_ROOT)}: `{span}`")
    assert not offenders, (
        "these code spans put --json AFTER the subcommand, which exits 2 with "
        "empty stdout — a JSON consumer misreads it as a missing item "
        "(WI-dolaf). Write `tracker --json <subcommand>`:\n  "
        + "\n  ".join(offenders)
    )


def test_agents_md_shows_the_working_form() -> None:
    """A forbid-only guard is satisfiable by deleting the documentation."""
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert "`scripts/tracker --json show <ID>`" in text, (
        "AGENTS.md must positively document the working global-flag-first "
        "form; it is the always-loaded surface every agent reads."
    )
