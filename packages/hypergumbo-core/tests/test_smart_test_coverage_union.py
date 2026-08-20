# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Phase 2 union block, executed rather than pattern-matched.

WHY THIS EXISTS ALONGSIDE THE POSITION GUARD. ``test_smart_test_manifest_split``
pins WHERE the union sits — below the seam, so it cannot reach the committed
manifest. That is the dangerous property, but it is not the only one: a union
that never adds anything would satisfy every position check ever written while
quietly making Phase 2 a no-op. The two failure modes are opposite and need
opposite tests, so this one RUNS the shipped lines.

WHY THE BLOCK IS EXTRACTED RATHER THAN REIMPLEMENTED. Copying the four lines
into a fixture would test the copy. The block is lifted verbatim out of
``scripts/smart-test`` between its own ``if``/``fi``, given the variables the
surrounding script would have set, and executed against a STUB selector. What
runs here is the text that ships.

WHY A STUB SELECTOR RATHER THAN THE REAL ONE. The real selector's answer depends
on a persistent out-of-repo index and on what happens to have changed in the
working tree — on the day this was written the static slice already contained
every file coverage picked, so a live run added zero and would have "passed"
whether the block worked or not. A stub makes the interesting case reachable on
demand, which is the only way the ADD path can be shown to fire.

THE FOUR CASES, and what each would catch:

    adds a file the run set lacks   the block does its job at all
    adds nothing already present    it is not double-counting overlap
    honours the off switch          --no-select-union / the CI guard work
    survives a broken selector      a failing selector cannot redden the run
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_SMART_TEST = _REPO / "scripts" / "smart-test"

_STUB = """#!/usr/bin/env bash
{body}
"""


def _block(var: str) -> str:
    """The shipped block guarded by ``$var``, from its ``if`` to its ``fi``."""
    text = _SMART_TEST.read_text(encoding="utf-8")
    start = re.search(rf'^if \[\[ "\${var}" == "true" \]\]', text, re.MULTILINE)
    assert start, f"the block guarded by ${var} is gone from smart-test"
    end = re.search(r'^fi$', text[start.start():], re.MULTILINE)
    assert end, f"the ${var} block's closing fi is gone"
    return text[start.start():start.start() + end.end()]


def _union_block() -> str:
    return _block("SELECT_UNION")


def _run(tmp_path: Path, *, stub: str, affected: str,
         select_union: str = "true", changed: str = "a.py") -> str:
    """Execute the shipped block with a stubbed selector; return the run set."""
    scripts = tmp_path / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    sel = scripts / "coverage-select"
    sel.write_text(_STUB.format(body=stub))
    sel.chmod(0o755)

    script = "\n".join([
        "set -uo pipefail",
        f'REPO_ROOT="{tmp_path}"',
        f'SELECT_UNION="{select_union}"',
        f'CHANGED_FILES="{changed}"',
        'BASELINE="HEAD"',
        "log() { :; }",
        f"AFFECTED_TESTS='{affected}'",
        _union_block(),
        'echo "RESULT:"',
        'echo "$AFFECTED_TESTS"',
    ])
    done = subprocess.run(
        ["bash", "-c", script],  # noqa: S607
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert done.returncode == 0, done.stderr
    body = done.stdout.split("RESULT:\n", 1)[1]
    return body


def test_a_file_the_run_set_lacks_is_added(tmp_path) -> None:
    """THE POINT OF PHASE 2: coverage sees a dependency the slice missed.

    Modelled on the real case — a change to ``edge_types.find_edge_type``
    selects ``test_cli_explain.py``, because ``explain`` looks edge types up at
    runtime and imports nothing that the static slice could follow.
    """
    out = _run(
        tmp_path,
        stub='echo "packages/hypergumbo-core/tests/test_cli_explain.py"',
        affected="packages/hypergumbo-core/tests/test_edge_types.py",
    )
    got = set(out.split())
    assert got == {
        "packages/hypergumbo-core/tests/test_edge_types.py",
        "packages/hypergumbo-core/tests/test_cli_explain.py",
    }


def test_overlap_is_not_double_counted(tmp_path) -> None:
    """NEGATIVE CONTROL: the common case, where the slice already had it.

    This is what a live run produced on the day the phase landed, so a test
    suite containing only this case would prove nothing.
    """
    out = _run(
        tmp_path,
        stub='echo "packages/hypergumbo-core/tests/test_edge_types.py"',
        affected="packages/hypergumbo-core/tests/test_edge_types.py",
    )
    assert out.split() == ["packages/hypergumbo-core/tests/test_edge_types.py"]


def test_the_union_never_drops_a_test(tmp_path) -> None:
    """The safety property Phase 2 rests on: additive, never subtractive.

    The selector answers with a set DISJOINT from the run set — if the block
    ever assigned instead of unioning, this is where it would show.
    """
    out = _run(
        tmp_path,
        stub='echo "packages/hypergumbo-core/tests/test_cli_explain.py"',
        affected="a/test_one.py\nb/test_two.py",
    )
    got = set(out.split())
    assert {"a/test_one.py", "b/test_two.py"} <= got


def test_the_off_switch_is_honoured(tmp_path) -> None:
    """``--no-select-union`` and the CI guard both work by setting this."""
    out = _run(
        tmp_path,
        stub='echo "packages/hypergumbo-core/tests/test_cli_explain.py"',
        affected="a/test_one.py",
        select_union="false",
    )
    assert out.split() == ["a/test_one.py"]


def test_no_changed_files_selects_nothing(tmp_path) -> None:
    out = _run(tmp_path, stub='echo "x/test_y.py"', affected="a/test_one.py",
               changed="")
    assert out.split() == ["a/test_one.py"]


def test_a_broken_selector_cannot_redden_the_run(tmp_path) -> None:
    """A selector that fails must degrade to silence, not to a failed run.

    ``set -uo pipefail`` is in force above, so a non-zero exit that was not
    swallowed would surface as a non-zero return from the block.
    """
    out = _run(
        tmp_path,
        stub='echo "boom" >&2; exit 1',
        affected="a/test_one.py",
    )
    assert out.split() == ["a/test_one.py"]


def test_a_selector_that_prints_junk_to_stderr_is_ignored(tmp_path) -> None:
    """Only stdout is the interface; diagnostics must not enter the run set."""
    out = _run(
        tmp_path,
        stub='echo "[coverage-select] 3 files" >&2; echo "x/test_y.py"',
        affected="a/test_one.py",
    )
    assert set(out.split()) == {"a/test_one.py", "x/test_y.py"}


# ── The shadow must not grade the union against its own output ───────────────
#
# Found by reading a live run, not by reasoning: with the union wired, the
# shadow reported `actually selected: 90 / EXTRA_FROM_COVERAGE: 0`, and that 0
# is unfalsifiable — the union has already folded coverage's picks into
# AFFECTED_TESTS by the time the shadow reads it. EXTRA_FROM_COVERAGE is the
# metric WI-kuliv is justified BY, so a permanent structural 0 would have read
# as "coverage stopped finding anything the slice missed" for as long as anyone
# kept looking. Same shape as this project's other measurement tautologies:
# the number is real, and it is measuring the instrument.


def test_the_shadow_compares_against_the_pre_union_set() -> None:
    text = _SMART_TEST.read_text(encoding="utf-8")
    block = re.search(
        r'if \[\[ "\$SELECT_SHADOW" == "true" \]\].*?\nfi$',
        text, re.DOTALL | re.MULTILINE,
    )
    assert block, "the shadow block is gone from smart-test"
    body = block.group(0)
    assert re.search(r'echo "\$MANIFEST_TESTS" \| sed .* > "\$_sel_actual"',
                     body), (
        "the shadow must write MANIFEST_TESTS (the pre-union set) as "
        "--actual-tests"
    )
    assert not re.search(r'echo "\$AFFECTED_TESTS"[^\n]*> "\$_sel_actual"',
                         body), (
        "the shadow is comparing coverage's selection against a run set that "
        "already contains it; EXTRA_FROM_COVERAGE is then 0 by construction"
    )


def test_the_pre_union_set_exists_before_the_shadow_reads_it() -> None:
    """``set -euo pipefail`` is in force: an unset read aborts the run."""
    text = _SMART_TEST.read_text(encoding="utf-8")
    assign = text.find('MANIFEST_TESTS="$AFFECTED_TESTS"')
    use = text.find('echo "$MANIFEST_TESTS" | sed')
    assert assign != -1 and use != -1
    assert assign < use, (
        "MANIFEST_TESTS is read by the shadow before it is assigned"
    )


# ── Phase 3: the narrow block, also executed ─────────────────────────────────
#
# Narrowing is the first operation here that can REMOVE a test, so the shipped
# block gets the same treatment as the union: run it, don't grep it. The cases
# that matter are the failure ones — a helper that dies or refuses must leave
# the run set standing, because a narrowing to nothing makes pytest exit 5,
# which is GREEN.


def _run_narrow(tmp_path: Path, *, stub: str, affected: str,
                select_narrow: str = "true", changed: str = "a.py") -> str:
    scripts = tmp_path / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    sel = scripts / "coverage-select"
    sel.write_text(_STUB.format(body=stub))
    sel.chmod(0o755)
    script = "\n".join([
        "set -uo pipefail",
        f'REPO_ROOT="{tmp_path}"',
        f'SELECT_NARROW="{select_narrow}"',
        f'CHANGED_FILES="{changed}"',
        'BASELINE="HEAD"',
        'VERBOSE=""',
        "log() { :; }",
        f"AFFECTED_TESTS='{affected}'",
        _block("SELECT_NARROW"),
        'echo "RESULT:"',
        'echo "$AFFECTED_TESTS"',
    ])
    done = subprocess.run(
        ["bash", "-c", script],  # noqa: S607
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.split("RESULT:\n", 1)[1]


def test_narrowing_removes_what_the_helper_drops(tmp_path) -> None:
    out = _run_narrow(
        tmp_path,
        stub='echo "a/test_one.py"',
        affected="a/test_one.py\nb/test_two.py\nc/test_three.py",
    )
    assert out.split() == ["a/test_one.py"]


def test_a_dead_helper_leaves_the_run_set_standing(tmp_path) -> None:
    """THE DANGEROUS CASE. pytest exits 5 — GREEN — on an empty collection.

    A narrowing that silently collapsed the run set to nothing would report a
    passing run that executed no tests at all.
    """
    out = _run_narrow(tmp_path, stub='exit 1',
                      affected="a/test_one.py\nb/test_two.py")
    assert set(out.split()) == {"a/test_one.py", "b/test_two.py"}


def test_a_helper_that_prints_nothing_leaves_the_run_set_standing(
    tmp_path,
) -> None:
    out = _run_narrow(tmp_path, stub='exit 0',
                      affected="a/test_one.py\nb/test_two.py")
    assert set(out.split()) == {"a/test_one.py", "b/test_two.py"}


def test_narrowing_is_off_unless_asked_for(tmp_path) -> None:
    out = _run_narrow(tmp_path, stub='echo "a/test_one.py"',
                      affected="a/test_one.py\nb/test_two.py",
                      select_narrow="false")
    assert set(out.split()) == {"a/test_one.py", "b/test_two.py"}


def test_narrowing_defaults_off_and_ci_forces_it_off_after_the_parse() -> None:
    """Order is load-bearing: ``--narrow`` is parsed AFTER the shadow guards.

    A CI guard placed beside the shadow's would be overridden by the very flag
    it exists to override, so it has to sit below the argument loop.
    """
    text = _SMART_TEST.read_text(encoding="utf-8")
    assert re.search(r'^SELECT_NARROW=false$', text, re.MULTILINE), (
        "narrowing must default OFF"
    )
    assert re.search(r'--narrow\)\s*SELECT_NARROW=true', text)
    parse_end = text.index("\n    esac\ndone\n")
    guard = re.search(r'if \[\[ -n "\$\{CI:-\}" \]\]; then\n'
                      r'    SELECT_NARROW=false', text)
    assert guard and guard.start() > parse_end, (
        "the CI guard for narrowing must come after argument parsing"
    )
