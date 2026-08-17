# SPDX-License-Identifier: AGPL-3.0-or-later
"""The committed manifest and the local run set must stay separate variables.

WHY THIS IS NOT A STYLE RULE. ``.woodpecker/woodpecker.yml`` states that "the
committed .ci/affected-tests.txt manifest drives it" — the manifest smart-test
writes is what the MERGE-GATING CI runs. So any change to the variable that
feeds the manifest is a change to what gates merges, whether or not it was meant
to be.

THE COUPLING THAT MADE THAT DANGEROUS. Until 2026-08-16 the manifest body and
the pytest invocation read ONE variable, ``AFFECTED_TESTS``. Coverage-directed
selection is specified as local-only — WI-kuliv says "Remains local-only. CI
never invokes scripts/smart-test" — which is literally true and misleading in
effect: CI does not invoke the script, it consumes its output. Under one
variable, WI-kuliv (Phase 2, which UNIONS coverage-selected tests in) would have
silently widened what CI runs, and WI-bolot (Phase 3, which NARROWS) would have
silently narrowed the merge gate to whatever the selector believed. Neither
phase mentions CI, and neither would have failed a test.

THE DECISION THIS PINS (owner, 2026-08-16, option A): the manifest publishes the
static import-graph slice plus its declarative augmentations; the local run set
may be widened or narrowed afterwards for this machine only. Measured trade at
the time: per-PR CI ~3m40s against a local ~8m13s on the same 232-file
selection, so narrowing CI was the smaller half of the prize and not worth
coupling the merge gate to a selector still accruing evidence (10 of 30
commits).

THE TWO SETS ARE BYTE-IDENTICAL TODAY, which is exactly why the seam is cut now:
there is nothing yet to misuse it, so the split can be verified as a no-op.

WHY THE GUARD CARRIES POSITIVE CONTROLS. A lint over a shell script that matches
nothing passes just as green as one that matches everything. ``_audit`` is
therefore run over synthetic scripts that DO violate the rule — including the
exact shape a well-meaning "simplify these two variables back into one" edit
would produce.
"""
from __future__ import annotations

import re
from pathlib import Path

_SMART_TEST = (
    Path(__file__).resolve().parents[3] / "scripts" / "smart-test"
)

_RUN = "AFFECTED_TESTS"
_MAN = "MANIFEST_TESTS"


def _audit(text: str) -> list[str]:
    """Report ways the manifest could be driven by the local run set."""
    problems: list[str] = []

    assign = [
        i for i, line in enumerate(text.split("\n"))
        if re.match(rf'^\s*{_MAN}="\${_RUN}"\s*$', line)
    ]
    if len(assign) != 1:
        problems.append(
            f"expected exactly one {_MAN}=\"${_RUN}\" capture, found "
            f"{len(assign)}; the manifest set must be snapshotted from the run "
            f"set at one named seam"
        )

    # The manifest heredoc-style block: find the group that redirects to
    # MANIFEST_FILE and check what it publishes.
    for block in re.findall(r"\{(.*?)\}\s*>\s*\"\$MANIFEST_FILE\"", text,
                            re.DOTALL):
        if "=== SELECTED_TESTS ===" not in block:
            continue
        if re.search(rf'echo\s+"\${_RUN}"', block):
            problems.append(
                f"the manifest block publishes ${_RUN} (the LOCAL run set). "
                f"It must publish ${_MAN} — the committed manifest drives the "
                f"merge-gating CI, so a local narrowing would narrow it too."
            )

    # ORDERING, against the RIGHT write site. smart-test has several blocks
    # redirecting to $MANIFEST_FILE — the full-suite fallback, the diagnostic
    # path, and the SELECTED_TESTS early exit — and they publish other
    # variables. Anchoring on the first one found the early-exit block, which
    # legitimately precedes the capture, and reported a false violation. Anchor
    # on the block that actually publishes the manifest set.
    if assign and problems == []:
        publish = re.search(
            rf'echo\s+"\${_MAN}"\s*\n\}}\s*>\s*"\$MANIFEST_FILE"', text,
        )
        capture = text.find(f'{_MAN}="${_RUN}"')
        if publish and capture > publish.start():
            problems.append(
                f"{_MAN} is captured AFTER the manifest is written, so the "
                f"snapshot cannot have applied to it"
            )
    return problems


def test_live_script_keeps_the_two_sets_separate() -> None:
    """The shipped smart-test honours the split."""
    assert _audit(_SMART_TEST.read_text(encoding="utf-8")) == []


def test_local_run_still_uses_the_run_set() -> None:
    """The split must be a SEAM, not a rename.

    If every consumer moved to ``MANIFEST_TESTS`` the coupling would be intact
    under a new name, and the guard above would still pass.
    """
    text = _SMART_TEST.read_text(encoding="utf-8")
    assert re.search(rf'run_pytest\s+"\${_RUN}"', text), (
        "the local pytest invocation must read the run set, not the manifest set"
    )


def test_guard_fires_when_manifest_publishes_the_run_set() -> None:
    """POSITIVE CONTROL: the shape a 'simplify to one variable' edit produces."""
    src = f'''
{_MAN}="${_RUN}"
{{
    echo "# === SELECTED_TESTS ==="
    echo "${_RUN}"
}} > "$MANIFEST_FILE"
'''
    found = _audit(src)
    assert len(found) == 1 and "LOCAL run set" in found[0]


def test_guard_fires_when_the_capture_is_missing() -> None:
    """POSITIVE CONTROL: deleting the seam entirely."""
    src = '''
{
    echo "# === SELECTED_TESTS ==="
    echo "$MANIFEST_TESTS"
} > "$MANIFEST_FILE"
'''
    found = _audit(src)
    assert len(found) == 1 and "exactly one" in found[0]


def test_guard_fires_when_the_capture_comes_too_late() -> None:
    """POSITIVE CONTROL: a snapshot taken after the manifest is already written."""
    src = f'''
{{
    echo "# === SELECTED_TESTS ==="
    echo "${_MAN}"
}} > "$MANIFEST_FILE"
{_MAN}="${_RUN}"
'''
    found = _audit(src)
    assert len(found) == 1 and "AFTER the manifest" in found[0]


def test_guard_accepts_the_correct_shape() -> None:
    """NEGATIVE CONTROL: a minimal correct script reports nothing."""
    src = f'''
{_MAN}="${_RUN}"
{{
    echo "# === SELECTED_TESTS ==="
    echo "${_MAN}"
}} > "$MANIFEST_FILE"
run_pytest "${_RUN}" "${{ARGS[@]}}"
'''
    assert _audit(src) == []
