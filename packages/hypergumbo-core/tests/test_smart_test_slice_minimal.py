# SPDX-License-Identifier: AGPL-3.0-or-later
"""smart-test's ``--minimal`` capability probe, executed rather than described.

WHY A PROBE AT ALL. smart-test's reverse slice answers "which test files depend
on these sources" from nodes+edges alone, so it has no use for the budget-tier
previews, per-handler slices, or sketch pre-computation that a cold analysis
otherwise writes — ~63s of one on this monorepo. ``--minimal`` declines them.

WHY IT IS NOT PASSED UNCONDITIONALLY, which is the whole reason this file exists.
The slice runs through ``$STABLE_HG``, the pipx-installed build, which lags the
working tree by design. An unrecognized argument makes hypergumbo exit non-zero,
and smart-test's slice-failure branch routes to ``run_full_suite`` — so adopting
the flag blindly would silently convert every local run into a ~90-minute full
suite until somebody reinstalled the stable, while still reporting success. That
is a silent-degradation mode, not a crash, so nothing would have pointed at the
cause. (The stable on the machine where this was written did NOT know the flag,
so the hazard was live rather than theoretical.)

WHAT IS TESTED, and why each case earns its place:

    advertised     -> the flag IS passed          the fast path actually engages
    not advertised -> the flag is NOT passed      the compatibility guard holds
    the two differ                                 POSITIVE CONTROL: a probe that
                                                   answered identically either way
                                                   would pass both cases above
                                                   while probing nothing
    the result is consumed                         a probe whose answer never
                                                   reaches the invocation is
                                                   inert (LIVE.md rule 8)

The block is lifted verbatim from ``scripts/smart-test`` rather than
reimplemented here — a copy would test the copy.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_SMART_TEST = _REPO / "scripts" / "smart-test"

_MARKER = "# === SLICE_MINIMAL ==="


def _probe_block() -> str:
    """The shipped probe: from its marker to the end of its ``fi``."""
    text = _SMART_TEST.read_text(encoding="utf-8")
    start = text.find(_MARKER)
    assert start != -1, (
        f"the {_MARKER} marker is gone from smart-test — if the probe was "
        "renamed, re-anchor this test rather than deleting it"
    )
    tail = text[start:]
    end = re.search(r"^fi$", tail, re.MULTILINE)
    assert end, "the probe block's closing fi is gone"
    return tail[: end.end()]


def _run_probe(tmp_path: Path, *, advertises: bool) -> str:
    """Execute the shipped probe against a stub hypergumbo; echo the array."""
    stub = tmp_path / "hg-stub"
    help_text = (
        "usage: hypergumbo slice\\n  --files FILES\\n  --minimal  skip extras\\n"
        if advertises
        else "usage: hypergumbo slice\\n  --files FILES\\n  --output OUT\\n"
    )
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "{help_text}"\n'
    )
    stub.chmod(0o755)

    script = tmp_path / "probe.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'STABLE_HG="{stub}"\n'
        + _probe_block()
        + "\n"
        # Print exactly what the real invocation would splice in.
        + 'printf "%s" "${SLICE_MINIMAL[@]+${SLICE_MINIMAL[@]}}"\n'
    )
    script.chmod(0o755)
    result = subprocess.run(
        ["bash", str(script)],  # noqa: S607
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (
        f"the probe block failed under `set -euo pipefail`: {result.stderr}"
    )
    return result.stdout


def test_flag_is_passed_when_the_binary_advertises_it(tmp_path: Path) -> None:
    assert _run_probe(tmp_path, advertises=True) == "--minimal"


def test_flag_is_withheld_when_the_binary_does_not_advertise_it(
    tmp_path: Path,
) -> None:
    """The compatibility guard: an older stable must not be handed the flag."""
    assert _run_probe(tmp_path, advertises=False) == ""


def test_the_probe_actually_discriminates(tmp_path: Path) -> None:
    """POSITIVE CONTROL.

    Without this, a probe hardcoded to one answer would satisfy exactly one of
    the two tests above and the other would look like a separate bug rather than
    proof the probe is inert.
    """
    assert _run_probe(tmp_path, advertises=True) != _run_probe(
        tmp_path, advertises=False
    )


def test_the_probe_result_reaches_the_slice_invocation() -> None:
    """A probe whose answer is never consumed is inert (LIVE.md rule 8).

    Static, because the real invocation cannot be executed here — it would run a
    whole analysis. Asserts the expansion appears on the command that actually
    runs the slice, not merely somewhere in the file (it is also spliced into
    ``SLICE_CMD``, the diagnostic string, which on its own would change nothing
    about what hypergumbo is asked to do).
    """
    text = _SMART_TEST.read_text(encoding="utf-8")
    invocation = re.search(
        r"^\s*\$STABLE_HG slice --files \"\$CHANGED_FILE_LIST\"(.*?)"
        r"--output \"\$SLICE_OUTPUT\"",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert invocation, "the slice invocation in smart-test no longer matches"
    assert "SLICE_MINIMAL" in invocation.group(1), (
        "the probe sets SLICE_MINIMAL but the slice invocation does not pass "
        "it — the flag is computed and discarded"
    )


def test_empty_array_expansion_survives_set_u() -> None:
    """`set -u` is on in smart-test, so the expansion must be the guarded form.

    A bare "${SLICE_MINIMAL[@]}" on an empty array is an unbound-variable error
    under `set -u` in some bash builds — and the failure would land on the slice
    command, i.e. exactly the path that silently falls back to the full suite.
    """
    text = _SMART_TEST.read_text(encoding="utf-8")
    assert 'SLICE_MINIMAL[@]+' in text, (
        "expected the ${a[@]+\"${a[@]}\"} guarded expansion for SLICE_MINIMAL"
    )
    assert not re.search(
        r'(?<!\+)"\$\{SLICE_MINIMAL\[@\]\}"', text.replace('[@]+"${SLICE_MINIMAL[@]}"', ''),
    ), "found an unguarded \"${SLICE_MINIMAL[@]}\" expansion"
