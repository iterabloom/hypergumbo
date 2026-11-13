# SPDX-License-Identifier: AGPL-3.0-or-later
"""The committed manifest and the local run set must stay separate variables.

WHY THIS IS NOT A STYLE RULE. ``.woodpecker/woodpecker.yml`` states that "the
committed .ci/affected-tests.txt manifest drives it" — the manifest smart-test
writes is what the MERGE-GATING CI runs. So any change to the variable that
feeds the manifest is a change to what gates merges, whether or not it was meant
to be.

THE HAZARD. Coverage-directed selection is local-only: CI never invokes
scripts/smart-test, but it does consume the manifest smart-test writes. If the
manifest body and the pytest invocation read ONE variable, Phase 2 (WI-kuliv,
which UNIONS coverage-selected tests into the run set) would silently widen
what CI runs, and Phase 3 (WI-bolot, which NARROWS it) would silently narrow
the merge gate to whatever the selector believes. Neither would fail a test.

THE RULE THIS PINS. The manifest (``MANIFEST_TESTS``) publishes the static
import-graph slice plus its declarative augmentations; the local run set
(``AFFECTED_TESTS``) may be widened or narrowed afterwards, for this machine
only. Per-PR CI already runs much faster than the local suite on the same
selection, so narrowing CI is the smaller saving and not worth coupling the
merge gate to a heuristic selector. ``_audit_union`` below pins that Phase 2
lands on the local side.

WHY THE GUARD CARRIES POSITIVE CONTROLS. A lint over a shell script that matches
nothing passes just as green as one that matches everything. Both audits are
therefore run over synthetic scripts that DO violate the rule — including the
exact shape a well-meaning "simplify these two variables back into one" edit
would produce, and the exact shape of a Phase 2 union placed one block too early.
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

    # The manifest block: everything from the SELECTED_TESTS marker to the
    # redirect that closes the group.
    #
    # ANCHOR ON THE MARKER, NOT ON A BRACE. Matching
    # `\{(.*?)\}\s*>\s*"\$MANIFEST_FILE"` reads "the nearest brace group
    # ending in the redirect" — and `{` occurs in ordinary shell all over this
    # script (`${BASELINE:-HEAD}`, `${ARGS[@]}`). Any block containing one
    # above the manifest would start the match there and swallow its
    # `echo "$AFFECTED_TESTS"`, reporting a violation in code that has nothing
    # to do with the manifest. Phase 2's union block is exactly that shape.
    for marker in re.finditer(r'echo "# === SELECTED_TESTS ==="', text):
        tail = text[marker.end():]
        close = re.search(r'\}\s*>\s*"\$MANIFEST_FILE"', tail)
        block = tail[:close.start()] if close else tail
        if re.search(rf'echo\s+"\${_RUN}"', block):
            problems.append(
                f"the manifest block publishes ${_RUN} (the LOCAL run set). "
                f"It must publish ${_MAN} — the committed manifest drives the "
                f"merge-gating CI, so a local narrowing would narrow it too."
            )

    # ORDERING, against the RIGHT write site. smart-test has several blocks
    # redirecting to $MANIFEST_FILE — the full-suite fallback, the diagnostic
    # path, and the SELECTED_TESTS early exit — and they publish other
    # variables. Anchoring on the first one would find the early-exit block,
    # which legitimately precedes the capture, and report a false violation.
    # So anchor on the block that actually publishes the manifest set.
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
    # WI-fipuf: the run reads RUN_ORDER, which is the run set REORDERED
    # (failure-first). So the seam is checked one step back: pytest gets
    # RUN_ORDER, and RUN_ORDER is built from the run set and never from the
    # manifest set.
    assert re.search(r'run_pytest\s+"\$RUN_ORDER"', text), (
        "the local pytest invocation must read the (reordered) run set"
    )
    start = text.index("RUN_ORDER=$( {")
    block = text[start:text.index("awk 'NF && !seen[$0]++')", start)]
    assert f'echo "${_RUN}"' in block, "RUN_ORDER must be built from the run set"
    assert _MAN not in block, "RUN_ORDER must not read the manifest set"


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


# ── Phase 2: the coverage-directed union must land BELOW the seam ────────────
#
# The seam only helps if its consumers respect it. Phase 2 (WI-kuliv) unions
# coverage-selected tests into the local run set; placed above the capture it
# would add those test files to the COMMITTED manifest, and therefore to the
# merge-gating CI. Nothing about that failure is visible:
# the union still works, the local run is still correct, and the per-PR gate
# just gets quietly slower. Only position distinguishes the two, so position is
# what gets pinned.

_SELECT_CALL = re.compile(r"coverage-select\"?\s*\\?\s*\n?[^\n]*\bselect\b")


def _audit_union(text: str) -> list[str]:
    """Report a coverage-directed union that could reach the manifest."""
    problems: list[str] = []

    calls = [m.start() for m in _SELECT_CALL.finditer(text)]
    if len(calls) != 1:
        problems.append(
            f"expected exactly one `coverage-select … select` invocation, "
            f"found {len(calls)}; the union has one site, below the seam"
        )
        return problems

    capture = text.find(f'{_MAN}="${_RUN}"')
    if capture == -1:
        problems.append("the seam capture is missing entirely")
    elif calls[0] < capture:
        problems.append(
            "the coverage-directed union runs BEFORE the manifest set is "
            "snapshotted, so its additions reach the committed manifest and "
            "therefore the merge-gating CI. It must run below the seam."
        )
    return problems


def test_live_script_unions_below_the_seam() -> None:
    """The shipped smart-test keeps Phase 2 local."""
    assert _audit_union(_SMART_TEST.read_text(encoding="utf-8")) == []


def test_the_union_is_actually_wired() -> None:
    """A position guard passes vacuously if the union was never added.

    Without this, deleting the whole Phase 2 block leaves ``_audit_union``
    reporting one problem — which the test above would catch — but a future
    refactor that renames the subcommand would silently satisfy neither.
    """
    text = _SMART_TEST.read_text(encoding="utf-8")
    assert "SELECT_UNION" in text, "the Phase 2 switch is gone"
    assert re.search(r'--no-select-union\)\s*SELECT_UNION=false', text), (
        "the union must remain individually disablable"
    )
    assert re.search(r'SELECT_UNION=false', text[:text.find("for arg in")]), (
        "the CI guard must also switch the union off"
    )


def test_union_guard_fires_when_placed_above_the_seam() -> None:
    """POSITIVE CONTROL: the exact shape of a union one block too early."""
    src = f'''
ADDS=$("$REPO_ROOT/scripts/coverage-select" --repo-root "$REPO_ROOT" select \\
    --changed-files "$f")
{_RUN}=$(printf '%s\\n%s\\n' "${_RUN}" "$ADDS" | sort -u)
{_MAN}="${_RUN}"
'''
    found = _audit_union(src)
    assert len(found) == 1 and "BEFORE the manifest set" in found[0]


def test_union_guard_fires_when_the_union_is_missing() -> None:
    """POSITIVE CONTROL: no select call at all."""
    found = _audit_union(f'{_MAN}="${_RUN}"\n')
    assert len(found) == 1 and "exactly one" in found[0]


def test_union_guard_accepts_the_correct_shape() -> None:
    """NEGATIVE CONTROL: below the seam reports nothing."""
    src = f'''
{_MAN}="${_RUN}"
ADDS=$("$REPO_ROOT/scripts/coverage-select" --repo-root "$REPO_ROOT" select \\
    --changed-files "$f")
{_RUN}=$(printf '%s\\n%s\\n' "${_RUN}" "$ADDS" | sort -u)
'''
    assert _audit_union(src) == []
