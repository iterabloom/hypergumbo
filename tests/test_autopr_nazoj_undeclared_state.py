# SPDX-License-Identifier: AGPL-3.0-or-later
"""auto-pr must never end a run with the terminal state still undeclared (WI-nazoj).

This is INV-rahib's residual class. The sibling class — a run that merged and
then exited nonzero — was closed by the post-merge boundary. What remains is
the run that ends with ``final_state`` still holding its initializer,
``unknown``: 14 of 485 recorded invocations, the most recent 2026-09-11, one of
them at exit 0. Owner ruling 2026-08-03 makes ``unknown`` *the* violation
signal rather than a state in the vocabulary, because it is never written by a
deliberate exit — reaching it means the run fell off the end.

Two mechanisms produce it, and they need different instruments:

**Deliberate exits that forgot to declare.** Ten ``exit`` statements in
``do_pr`` and ``flush_queue`` were reached with the state still unset — the
branch-ownership guards, the re-push failures inside stuck-CI recovery, the
unresolvable/non-linear vPR queue checks, the simulated outage. These are
static and enumerable, so ``test_no_action_path_exit_leaves_the_state_undeclared``
enumerates them. It is deliberately a *dominance* analysis rather than "is
there an assignment somewhere earlier in the function": the assignment at
auto-pr:2085 sits in the ``if _pre_scenario_b_gate`` arm while the exits at
2105/2114 sit in the ``else`` arm, so a proximity heuristic clears them. One
did — a 25-line distance threshold hid 2105 during this investigation, which is
the same defect class as a grep whose own filter omits the line being checked.

**`set -e` aborts before any declaration.** These are not statically
enumerable — any unguarded command in a 700-line function can do it. So the
run is made to attribute itself: an ERR trap records the file and line, and
``_autopr_finalize`` reports it and persists it to the sentinel and the
convergence ledger. That turns every future ``unknown`` row from "something
went wrong somewhere" into a line number.

**ABSENT IS NOT EMPTY, at two depths here.** A ``set -u`` unbound-variable
abort does *not* fire the ERR trap (verified, not assumed — bash treats it as
an expansion error, not a command failure), and neither does a bare ``exit``.
So ``abort_site`` can be null on a genuinely undeclared run, and a reader must
not take null to mean "no abort happened". The finalize banner says
``not recorded`` explicitly, and the audit script distinguishes a row that
carries no site from a row written before the instrumentation existed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AUTO_PR = REPO_ROOT / "scripts" / "auto-pr"


# ---------------------------------------------------------------------------
# Production shell flags, read from production
# ---------------------------------------------------------------------------


def production_shell_flags() -> str:
    """The `set` line auto-pr actually runs under.

    Read rather than copied. A harness that hardcodes its own flags is blind to
    whole defect classes: ``tests/test_autopr_branch_cleanup.py`` ran
    ``set -uo pipefail`` against a file whose defect only reproduces under
    ``-e``, and stayed green through six weeks of it. The same copy would go
    stale again the moment ``-E`` was added for the ERR trap.
    """
    for line in AUTO_PR.read_text(encoding="utf-8").splitlines()[:20]:
        if line.startswith("set -"):
            return line
    raise AssertionError("no `set -` line in the first 20 lines of auto-pr")


def test_errtrace_is_enabled_in_production() -> None:
    """`set -E` is what makes the ERR trap fire inside functions at all.

    Verified empirically before relying on it: without ``errtrace`` an ERR trap
    installed at the top level is NOT inherited by shell functions, and every
    line of auto-pr's action logic lives in one. The trap would then record
    nothing, on every run, forever — and a recorded-nothing looks exactly like
    a run that never aborted. Pinning the flag keeps that failure from being
    reintroduced silently.
    """
    flags = production_shell_flags()
    assert re.match(r"^set -[a-zA-Z]*E", flags), (
        f"auto-pr runs under `{flags}`, which lacks errtrace (-E); the ERR trap "
        f"that attributes an undeclared abort to its source line will never "
        f"fire inside do_pr/flush_queue, and every abort_site will be null"
    )
    for required in ("e", "u"):
        assert re.match(rf"^set -[a-zA-Z]*{required}", flags), (
            f"auto-pr lost `-{required}` from `{flags}`"
        )


# ---------------------------------------------------------------------------
# Static: no deliberate exit may leave the state undeclared
# ---------------------------------------------------------------------------

ACTION_FUNCS = ("do_pr", "flush_queue", "gov_cleanup", "_autopr_handle_already_merged")


def _indent(line: str) -> int:
    expanded = line.expandtabs(4)
    return len(expanded) - len(expanded.lstrip())


_EXIT_RE = re.compile(r"(^|;|\s|\|\|\s*|&&\s*)exit(\s+\d+)?\s*(\}|;|$)")
_BRANCH_RE = re.compile(r"^(else|elif)\b")


def undeclared_exits(lines: list[str], start: int, end: int) -> list[tuple[int, str]]:
    """Exits in ``lines[start:end]`` not dominated by a state declaration.

    Walks backwards from each ``exit``. ``cur`` tracks the indent of the block
    being scanned: an assignment at ``cur`` is sequentially earlier in the same
    block and therefore dominates. An ``else``/``elif`` at ``cur`` means the
    exit is in one arm of a conditional, so nothing above it in the sibling arm
    dominates — step out to the enclosing level and keep looking.

    Indent-based because bash has no parser here worth writing, and auto-pr is
    consistently tab-indented. It is validated in both directions by
    ``test_the_scanner_separates_a_declared_exit_from_an_undeclared_one``; a
    scanner that returned ``[]`` unconditionally would otherwise satisfy the
    caller below forever.
    """
    found: list[tuple[int, str]] = []
    for k in range(start, end + 1):
        raw = lines[k]
        stripped = raw.strip()
        if stripped.startswith("#") or not _EXIT_RE.search(raw):
            continue
        cur = _indent(raw)
        covered = False
        m = k - 1
        while m > start:
            line = lines[m]
            text = line.strip()
            if not text or text.startswith("#"):
                m -= 1
                continue
            level = _indent(line)
            if level > cur:
                m -= 1
                continue
            if "_autopr_state_final=" in line:
                covered = True
                break
            if level == cur:
                if _BRANCH_RE.match(text) or text == ";;" or text.startswith(")"):
                    cur = level - 1
            else:
                cur = level - 1 if _BRANCH_RE.match(text) else level
            m -= 1
        if not covered:
            found.append((k + 1, stripped))
    return found


def _func_bounds(lines: list[str], name: str) -> tuple[int, int]:
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(name)}\(\)\s*\{{\s*$", line):
            depth = 0
            for j in range(i, len(lines)):
                depth += lines[j].count("{") - lines[j].count("}")
                if depth == 0 and j > i:
                    return i, j
    raise AssertionError(f"{name}() not found in scripts/auto-pr")


def test_no_action_path_exit_leaves_the_state_undeclared() -> None:
    """Every deliberate exit declares what the run decided, before leaving.

    Ten did not when WI-nazoj was filed. Each wrote an ``unknown`` ledger row
    and told the caller only `exit 1`.
    """
    lines = AUTO_PR.read_text(encoding="utf-8").splitlines()
    offenders: list[str] = []
    for fn in ACTION_FUNCS:
        start, end = _func_bounds(lines, fn)
        for lineno, text in undeclared_exits(lines, start, end):
            offenders.append(f"  {fn}  auto-pr:{lineno}  {text}")
    assert not offenders, (
        "these exits are reached with _autopr_state_final still at its "
        "'unknown' initializer, so the run records no terminal state "
        "(WI-nazoj):\n" + "\n".join(offenders)
    )


def test_the_scanner_separates_a_declared_exit_from_an_undeclared_one() -> None:
    """Non-vacuity control for the scanner above.

    Both arms matter. Without the positive arm a scanner hard-wired to return
    ``[]`` passes the enumeration forever; without the negative arm one that
    flags everything looks equally green once the fix lands. The synthetic
    fixture reproduces the real shape that fooled a proximity heuristic: a
    declaration in the ``if`` arm and an exit in the ``else`` arm.
    """
    fixture = [
        "f() {",
        "\tif guard; then",
        '\t\t_autopr_state_final="declared_in_the_if_arm"',
        "\t\texit 1",
        "\telse",
        "\t\techo bad",
        "\t\texit 1",
        "\tfi",
        "}",
    ]
    hits = undeclared_exits(fixture, 0, len(fixture) - 1)
    assert [h[0] for h in hits] == [7], (
        f"expected only the else-arm exit on line 7 to be undeclared, got {hits}"
    )


# ---------------------------------------------------------------------------
# Dynamic: an abort attributes itself
# ---------------------------------------------------------------------------


def _extract_func(name: str) -> str:
    """Pull one function out of auto-pr, heredoc-aware.

    The obvious regex — start of function to the first `^}$` — silently
    TRUNCATES `_autopr_write_sentinel`, because the Python it heredocs closes a
    dict literal with a `}` in column 0. The harness then ran a half-function
    and wrote no sentinel at all, which reads as "the feature does not work"
    rather than "the test is lying". Tracking the heredoc is the fix.
    """
    lines = AUTO_PR.read_text(encoding="utf-8").splitlines()
    start = next(
        (i for i, l in enumerate(lines) if re.match(rf"^{re.escape(name)}\(\) \{{\s*$", l)),
        None,
    )
    assert start is not None, f"{name}() not found in scripts/auto-pr"
    terminator: str | None = None
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if terminator is not None:
            if line.strip() == terminator:
                terminator = None
            continue
        # Not anchored to end-of-line: auto-pr's is `python3 - <<'PY' 2>/dev/null
        # || true`, and an anchored pattern misses it, silently truncating the
        # function at the `}` that closes the heredoc'd dict literal. `<<<` is
        # a herestring, not a heredoc, so it must not match.
        m = re.search(r"<<-?(?!<)\s*([\"\']?)([A-Za-z_][A-Za-z0-9_]*)\1", line)
        if m:
            terminator = m.group(2)
            continue
        if line == "}":
            return "\n".join(lines[start : j + 1])
    raise AssertionError(f"{name}() has no closing brace in scripts/auto-pr")


def _extract_state_initializers() -> str:
    """The real ``_autopr_state_*`` defaults, lifted from production.

    Retyping them here would let the harness drift from the script it is
    supposed to be testing — which is the same mistake as retyping the shell
    flags.
    """
    lines = [
        line
        for line in AUTO_PR.read_text(encoding="utf-8").splitlines()
        if re.match(r"^_autopr_(state|sentinel)_\w+=", line)
    ]
    assert len(lines) >= 5, f"expected the state block, found {lines}"
    return "\n".join(lines)


def _run_action(tmp_path: Path, body: str) -> tuple[subprocess.CompletedProcess[str], dict | None]:
    """Run ``body`` as an action path with auto-pr's real finalize machinery.

    Real: the production ``set`` line, the real state initializers, the real
    ``_autopr_record_abort``/``_autopr_write_sentinel``/``_autopr_finalize``,
    and the real trap installation order. Synthetic: only the body, so a test
    can abort at a line it chooses.
    """
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    script = tmp_path / "act.sh"
    script.write_text(
        f"{production_shell_flags()}\n"
        f'REPO_ROOT="{repo}"\n'
        f'AUTOPR_RESULT_FILE="{repo}/.git/AUTOPR_LAST_RESULT.json"\n'
        f'AUTOPR_HISTORY_FILE="{repo}/.git/AUTOPR_HISTORY.jsonl"\n'
        f"{_extract_state_initializers()}\n"
        f"{_extract_func('_autopr_record_abort')}\n"
        f"{_extract_func('_autopr_write_sentinel')}\n"
        f"{_extract_func('_autopr_finalize')}\n"
        "action() {\n"
        "\ttrap _autopr_record_abort ERR\n"
        "\ttrap _autopr_finalize EXIT\n"
        f"{body}\n"
        "}\n"
        "action\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, timeout=60,
        cwd=str(repo), env={**os.environ, "LC_ALL": "C"},
    )
    sentinel = repo / ".git" / "AUTOPR_LAST_RESULT.json"
    payload = json.loads(sentinel.read_text()) if sentinel.exists() else None
    return proc, payload


def test_an_undeclared_abort_names_the_line_it_died_on(tmp_path: Path) -> None:
    """The load-bearing test: a silent abort becomes a line number.

    Before this, an undeclared run recorded ``final_state=unknown`` and
    nothing else — fourteen of them, spread over five weeks, with no way to
    tell whether they shared a cause.
    """
    proc, payload = _run_action(tmp_path, "\tgrep needle /definitely-absent-xyz\n\techo unreachable")
    assert proc.returncode != 0
    assert payload is not None
    assert payload["final_state"] == "unknown", "the fixture must really abort undeclared"
    assert payload["abort_site"] is not None, (
        "an abort before any declaration recorded no site, so the ledger row "
        "is as uninformative as the fourteen that prompted this work"
    )
    assert re.match(r"^act\.sh:\d+$", payload["abort_site"]), payload["abort_site"]
    assert payload["abort_command"] == "grep needle /definitely-absent-xyz"


def test_an_abort_inside_a_helper_function_is_attributed_too(tmp_path: Path) -> None:
    """The arm that actually depends on `-E`, and the reason it exists.

    A `trap ... ERR` set inside a function already covers commands in that same
    function WITHOUT errtrace — so the sibling test above kept passing with
    `-E` removed, and would have certified a flag it does not exercise. That is
    the harness contaminating its own control.

    Production's aborts are not in do_pr's own body; they are inside the
    helpers do_pr calls — `_assert_branch_owns_head`, `api_call`, `poll_ci`,
    `pr_web_url`. An ERR trap reaches those ONLY under errtrace. This fixture
    has that shape, so removing `-E` from auto-pr turns it red.
    """
    proc, payload = _run_action(
        tmp_path,
        "\thelper() { grep needle /definitely-absent-xyz; }\n\thelper",
    )
    assert proc.returncode != 0
    assert payload is not None
    assert payload["final_state"] == "unknown"
    assert payload["abort_site"] is not None, (
        "an abort one call-frame deep went unattributed; without errtrace the "
        "ERR trap is not inherited by shell functions, and every real auto-pr "
        "abort happens inside a helper"
    )
    assert payload["abort_command"] == "grep needle /definitely-absent-xyz"


def test_the_abort_banner_is_loud_and_names_the_violation(tmp_path: Path) -> None:
    """Criterion (b): the run must say so, not only write a row nobody reads.

    The ledger already recorded these; that is exactly how they accumulated
    unnoticed for five weeks. A convergence violation has to be visible in the
    output of the run that commits it.
    """
    proc, _ = _run_action(tmp_path, "\tgrep needle /definitely-absent-xyz")
    assert "CONVERGENCE VIOLATION" in proc.stderr, proc.stderr
    assert "WI-nazoj" in proc.stderr or "INV-rahib" in proc.stderr
    assert "act.sh:" in proc.stderr


def test_a_declared_run_prints_no_banner_and_records_no_site(tmp_path: Path) -> None:
    """The control. Without it, a banner printed unconditionally would satisfy
    the test above while making every successful run look like a violation."""
    proc, payload = _run_action(
        tmp_path, '\t_autopr_state_final="merged"\n\t_autopr_state_merged_sha="deadbee"\n\texit 0'
    )
    assert proc.returncode == 0, proc.stderr
    assert payload is not None
    assert payload["final_state"] == "merged"
    assert payload["abort_site"] is None, (
        f"a converged run recorded an abort site: {payload['abort_site']}"
    )
    assert payload["abort_command"] is None
    assert "CONVERGENCE VIOLATION" not in proc.stderr


def test_an_unbound_variable_abort_is_disclosed_rather_than_silently_unattributed(
    tmp_path: Path,
) -> None:
    """ABSENT IS NOT EMPTY, at the instrument's own boundary.

    A ``set -u`` violation kills the shell without firing the ERR trap —
    verified, because bash raises it as an expansion error rather than a
    command failure. So this run is undeclared AND unattributed, and the two
    facts must be reported separately. A banner that simply omitted the site
    would read as "aborted, no site" — indistinguishable from an instrument
    that is broken.
    """
    proc, payload = _run_action(tmp_path, '\techo "${THIS_NAME_IS_NOT_BOUND}"')
    assert proc.returncode != 0
    assert payload is not None
    assert payload["final_state"] == "unknown"
    assert payload["abort_site"] is None, "set -u is not supposed to reach the ERR trap"
    assert "CONVERGENCE VIOLATION" in proc.stderr
    assert "not recorded" in proc.stderr, (
        "the banner must say the site is missing, not leave it out:\n" + proc.stderr
    )


def test_a_recorded_command_never_expands_a_secret(tmp_path: Path) -> None:
    """The safety property that makes persisting the command text acceptable.

    ``BASH_COMMAND`` holds the command's source text, not its expansion, so a
    line carrying ``$HG_GITHUB_TOKEN`` records the NAME. That is what allows
    ``abort_command`` into a file on disk at all, and it is a property of bash
    rather than of our code — which is exactly why it is pinned here instead of
    assumed in a comment.
    """
    _, payload = _run_action(
        tmp_path,
        '\tSECRET="hunter2-do-not-persist"\n'
        '\tgrep "$SECRET" /definitely-absent-xyz',
    )
    assert payload is not None
    assert payload["abort_command"] == 'grep "$SECRET" /definitely-absent-xyz'
    assert "hunter2" not in json.dumps(payload), (
        "the recorded command expanded a variable; persisting it to the ledger "
        "would write whatever that variable held"
    )


def test_the_ledger_row_carries_the_site_too(tmp_path: Path) -> None:
    """The sentinel answers 'did my run succeed'; the ledger is what the audit
    reads. The site has to reach the artifact that outlives the run."""
    _, _ = _run_action(tmp_path, "\tgrep needle /definitely-absent-xyz")
    ledger = tmp_path / "repo" / ".git" / "AUTOPR_HISTORY.jsonl"
    rows = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["final_state"] == "unknown"
    assert re.match(r"^act\.sh:\d+$", rows[0]["abort_site"] or "")
