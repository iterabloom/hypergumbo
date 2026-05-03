# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression test: ``scripts/prepare-release`` must not pass multi-line values
to ``git push -o ...``.

Background
----------
On 2026-05-03 during the v4.0.0 release prep, Step 7 ("Create PR to main")
silently failed: the script emits ``-o description="..."`` whose quoted value
spanned multiple lines (a markdown checklist), and git rejects any push option
containing a newline (``fatal: push options must not have new line
characters``). The push exited 128, but the script's ``2>&1) || true`` swallowed
the failure and the URL-extraction regex returned empty, so Step 7 reported
``PR branch pushed (check Codeberg for PR)`` and exited 0 even though no PR was
created. The dev->main release PR had to be opened by hand.

This test asserts the structural invariant by AST-light parsing of the script
source: every ``-o (title|description)=`` push option must be bound to a
single-line value.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PREPARE_RELEASE = REPO_ROOT / "scripts" / "prepare-release"

# Match `-o NAME="..."` where the quoted value may contain anything but
# unescaped double-quotes (so the regex stops at the closing quote even when
# the value spans multiple lines).
PUSH_OPTION_RE = re.compile(
    r'-o\s+(title|description)=("(?:[^"\\]|\\.)*")',
    flags=re.DOTALL,
)


def _line_no(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def test_push_options_have_no_newlines() -> None:
    text = PREPARE_RELEASE.read_text()
    bad: list[tuple[int, str, str]] = []
    for match in PUSH_OPTION_RE.finditer(text):
        opt_name = match.group(1)
        quoted_value = match.group(2)
        if "\n" in quoted_value:
            bad.append((_line_no(text, match.start()), opt_name, quoted_value[:60]))
    assert not bad, (
        "scripts/prepare-release passes multi-line value(s) to `git push -o`; "
        "git rejects newlines in push options. Offenders (line, option, head): "
        f"{bad}"
    )


def test_push_description_variables_are_single_line() -> None:
    """The variables interpolated into ``-o description=$VAR`` must themselves
    be single-line, otherwise the static check above is bypassed by indirection.

    Currently only ``PR_DESCRIPTION`` is interpolated; if more are added, list
    them here.
    """
    text = PREPARE_RELEASE.read_text()
    interpolated_into_description = ["PR_DESCRIPTION"]
    # Match `VAR="..."` assignments at the start of a line (after optional
    # whitespace), capturing the quoted value with multi-line tolerance.
    bad: list[tuple[str, int]] = []
    for var in interpolated_into_description:
        assign_re = re.compile(
            rf'^\s*{re.escape(var)}=("(?:[^"\\]|\\.)*")',
            flags=re.MULTILINE | re.DOTALL,
        )
        for match in assign_re.finditer(text):
            if "\n" in match.group(1):
                bad.append((var, _line_no(text, match.start())))
    assert not bad, (
        "Variable(s) interpolated into `-o description=` push options have "
        "multi-line assignments; this defeats the single-line invariant. "
        f"Offenders (var, line): {bad}"
    )
