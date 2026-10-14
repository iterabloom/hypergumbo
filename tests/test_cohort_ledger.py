# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``scripts/lib/cohort_ledger.py`` (WI-kovoj).

The defect under test: a multi-repo measurement instrument that loses a cohort
member -- a run that wrote no report, a behavior map truncated by a full disk,
a repo path that does not exist -- reported the survivors' numbers with exit 0,
and either DROPPED the member (``except Exception: continue``) or turned it
into a ZERO (``contextlib.suppress(SystemExit)`` then ``report = {}``). Either
way a shrunken denominator printed exactly like a full one. Reproduced on dev
62602239c6: ``measure-row-inflation.py CLAIMS flask-restful /nonexistent/kserve``
printed ``kserve ... EXCLUDED -- 0 cross_function findings (0 findings)`` and
exited 0.

The cure is a POSITIVE claim (LIVE.md section 5, absent is not empty): every
instrument states how many members it was GIVEN and how many it COVERED, names
each one it did not, and exits non-zero when those differ.

Three layers are pinned here:

* the ledger itself (given / covered / failed / not-attempted, disk-full
  flagging, the exit code);
* the member readers (``require_dir``, ``read_json_member``,
  ``run_cli_json``), each of which turns an ABSENT output into a raised
  ``MemberAbsent`` instead of an empty value -- including one run through the
  real ``hypergumbo verify-claims`` CLI on the exact reproduced case;
* a structural guard: every script under ``scripts/`` that takes a cohort of
  repos or maps as a positional argument must use the ledger, so the next
  instrument cannot reintroduce the silent shrink.
"""

# covers: scripts/lib/cohort_ledger.py
from __future__ import annotations

import errno
import io
import json
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_LIB = _ROOT / "scripts" / "lib"
sys.path.insert(0, str(_LIB))
import cohort_ledger  # noqa: E402
from cohort_ledger import CohortLedger, MemberAbsent  # noqa: E402


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------


def test_full_cohort_is_complete_and_says_so():
    ledger = CohortLedger(["a", "b"])
    for member in ledger:
        with ledger.attempt(member):
            pass
    claim = ledger.claim()
    assert claim["given"] == 2
    assert claim["covered"] == 2
    assert claim["failed"] == 0
    assert claim["not_attempted"] == 0
    assert claim["complete"] is True
    assert [m["status"] for m in claim["members"]] == ["covered", "covered"]
    assert ledger.exit_code() == 0
    out = io.StringIO()
    ledger.report(out)
    assert "covered 2 of 2 given repo(s)" in out.getvalue()
    assert "complete" in out.getvalue()


def test_failed_member_is_recorded_not_dropped_and_the_run_continues():
    ledger = CohortLedger(["pretix", "kserve", "meson"])
    reached = []
    for member in ledger:
        with ledger.attempt(member):
            if member == "kserve":
                raise ValueError("boom")
            reached.append(member)
    # The member after the failure still ran: one bad member does not make the
    # operator re-run with a shorter list, which is the silent shrink itself.
    assert reached == ["pretix", "meson"]
    claim = ledger.claim()
    assert (claim["given"], claim["covered"], claim["failed"]) == (3, 2, 1)
    assert claim["complete"] is False
    failed = [m for m in claim["members"] if m["status"] == "failed"]
    assert failed == [{
        "member": "kserve", "status": "failed",
        "error": "ValueError: boom", "disk_full": False,
    }]
    assert ledger.exit_code() == cohort_ledger.COHORT_INCOMPLETE_EXIT
    out = io.StringIO()
    ledger.report(out)
    text = out.getvalue()
    assert "covered 2 of 3 given repo(s)" in text
    assert "INCOMPLETE" in text
    assert "kserve" in text


def test_member_never_reached_is_not_attempted_not_covered():
    """A run that stops before the loop (a failed positive control) covered
    nothing, and must not read as a cohort that measured zero."""
    ledger = CohortLedger(["a", "b"], unit="map")
    claim = ledger.claim()
    assert (claim["covered"], claim["not_attempted"]) == (0, 2)
    assert claim["complete"] is False
    out = io.StringIO()
    ledger.report(out)
    assert "covered 0 of 2 given map(s)" in out.getvalue()
    assert "NOT ATTEMPTED" in out.getvalue()


def test_disk_full_is_flagged_through_the_exception_chain():
    ledger = CohortLedger(["kserve"])
    with ledger.attempt("kserve"):
        try:
            raise OSError(errno.ENOSPC, "No space left on device")
        except OSError as exc:
            raise RuntimeError("writing the behavior map failed") from exc
    member = ledger.claim()["members"][0]
    assert member["disk_full"] is True
    out = io.StringIO()
    ledger.report(out)
    assert "DISK FULL" in out.getvalue()


def test_is_disk_full_only_on_disk_full_errnos():
    assert cohort_ledger.is_disk_full(OSError(errno.ENOSPC, "x"))
    assert cohort_ledger.is_disk_full(OSError(errno.EDQUOT, "x"))
    assert not cohort_ledger.is_disk_full(OSError(errno.ENOENT, "x"))
    assert not cohort_ledger.is_disk_full(ValueError("x"))
    # __context__ (implicit chaining) is followed too, not only __cause__.
    try:
        try:
            raise OSError(errno.ENOSPC, "x")
        except OSError:
            raise KeyError("k")  # noqa: B904 - implicit chaining is the case under test
    except KeyError as exc:
        assert cohort_ledger.is_disk_full(exc)


def test_a_cycle_in_the_exception_chain_terminates():
    a = ValueError("a")
    b = ValueError("b")
    a.__context__ = b
    b.__context__ = a
    assert not cohort_ledger.is_disk_full(a)


def test_base_exceptions_are_not_swallowed():
    ledger = CohortLedger(["a"])
    with pytest.raises(KeyboardInterrupt):
        with ledger.attempt("a"):
            raise KeyboardInterrupt


def test_duplicate_members_are_refused():
    with pytest.raises(ValueError, match="twice"):
        CohortLedger(["a", "a"])


def test_unknown_member_and_double_attempt_are_programming_errors():
    ledger = CohortLedger(["a"])
    with pytest.raises(ValueError, match="not in the cohort"):
        with ledger.attempt("zzz"):
            pass
    with ledger.attempt("a"):
        pass
    with pytest.raises(ValueError, match="already"):
        with ledger.attempt("a"):
            pass


def test_member_absent_marks_failed():
    ledger = CohortLedger(["a"])
    with ledger.attempt("a"):
        raise MemberAbsent("no report on stdout")
    assert ledger.claim()["members"][0]["error"] == "MemberAbsent: no report on stdout"


def test_exit_code_keeps_an_instruments_own_failure_code():
    ledger = CohortLedger(["a"])
    assert ledger.exit_code(2) == 2      # not attempted, but the run already failed
    with ledger.attempt("a"):
        pass
    assert ledger.exit_code(0) == 0
    assert ledger.exit_code(2) == 2


def test_names_can_be_shortened_for_display_without_losing_identity():
    ledger = CohortLedger(["/r/x/pretix", "/r/y/pretix"], label=lambda m: Path(m).name)
    for member in ledger:
        with ledger.attempt(member):
            pass
    members = ledger.claim()["members"]
    assert [m["member"] for m in members] == ["/r/x/pretix", "/r/y/pretix"]
    assert ledger.label("/r/x/pretix") == "pretix"


# ---------------------------------------------------------------------------
# Member readers: absent output raises, it never returns an empty value
# ---------------------------------------------------------------------------


def test_require_dir(tmp_path):
    assert cohort_ledger.require_dir(str(tmp_path)) == tmp_path.resolve()
    with pytest.raises(MemberAbsent, match="not a directory"):
        cohort_ledger.require_dir(str(tmp_path / "missing"))


def test_read_json_member(tmp_path):
    good = tmp_path / "good.json"
    good.write_text('{"nodes": []}')
    assert cohort_ledger.read_json_member(good) == {"nodes": []}
    with pytest.raises(MemberAbsent, match="unreadable"):
        cohort_ledger.read_json_member(tmp_path / "missing.json")
    # A map cut off mid-write by a full disk is the WI-kovoj mechanism exactly.
    truncated = tmp_path / "truncated.json"
    truncated.write_text('{"nodes": [{"id": "py')
    with pytest.raises(MemberAbsent, match="not valid JSON"):
        cohort_ledger.read_json_member(truncated)
    empty = tmp_path / "empty.json"
    empty.write_text("")
    with pytest.raises(MemberAbsent, match="empty"):
        cohort_ledger.read_json_member(empty)


def _fake_main(stdout: str, code: int | None):
    def main():
        sys.stdout.write(stdout)
        if code is not None:
            raise SystemExit(code)
    return main


def test_run_cli_json_returns_the_report_whatever_the_verdict_exit_code():
    # verify-claims exit codes are VERDICTS (1 = violated), not health; a
    # report on stdout is the positive signal that the run happened.
    report = cohort_ledger.run_cli_json(
        ["hypergumbo", "verify-claims"],
        main=_fake_main('banner\n{"verdicts": []}\ntrailer', 1),
    )
    assert report == {"verdicts": []}


def test_run_cli_json_reports_a_returned_exit_code():
    """``cli.main`` returns its code rather than raising it; an internal error
    (seen live: a stale monkeypatch signature in an A/B instrument) comes back
    as a return value with nothing on stdout."""
    def main():
        return 1
    with pytest.raises(MemberAbsent, match="exit 1"):
        cohort_ledger.run_cli_json(["hypergumbo", "verify-claims"], main=main)


def test_run_cli_json_names_a_disk_full_error_the_cli_caught(capsys):
    """The CLI turns an uncaught OSError into ``internal error`` on stderr and
    returns 1 (observed live: ``verify-claims ... > /dev/full``). The ledger
    must still say DISK FULL, not "a bug"."""
    def main():
        sys.stderr.write("hypergumbo: internal error: OSError: [Errno 28] "
                         "No space left on device\nThis is a bug; re-run with --debug\n")
        return 1
    ledger = CohortLedger(["kserve"])
    with ledger.attempt("kserve"):
        cohort_ledger.run_cli_json(["hypergumbo", "verify-claims"], main=main)
    member = ledger.claim()["members"][0]
    assert member["disk_full"] is True
    assert "exit 1" in member["error"]
    assert "No space left on device" in member["error"]
    # stderr is teed, not swallowed: the operator still sees it live.
    assert "internal error" in capsys.readouterr().err


def test_run_cli_json_other_failures_are_not_disk_full():
    def main():
        sys.stderr.write("Error: /x: not a directory\n")
        return 2
    ledger = CohortLedger(["x"])
    with ledger.attempt("x"):
        cohort_ledger.run_cli_json(["hypergumbo", "verify-claims"], main=main)
    member = ledger.claim()["members"][0]
    assert member["disk_full"] is False
    assert "not a directory" in member["error"]


def test_tee_delegates_to_the_real_stream():
    real = io.StringIO()
    tee = cohort_ledger._Tee(real)
    tee.write("x")
    tee.flush()
    assert real.getvalue() == "x" and tee.copy.getvalue() == "x"
    assert tee.getvalue() == "x"   # an attribute only the real stream has
    assert tee.isatty() is real.isatty()


def test_run_cli_json_restores_argv():
    before = list(sys.argv)
    seen = []

    def main():
        seen.append(list(sys.argv))
        print("{}")

    cohort_ledger.run_cli_json(["hypergumbo", "x"], main=main)
    assert seen == [["hypergumbo", "x"]]
    assert sys.argv == before


def test_run_cli_json_raises_when_no_report_was_written():
    with pytest.raises(MemberAbsent, match="exit 2"):
        cohort_ledger.run_cli_json(
            ["hypergumbo", "verify-claims"],
            main=_fake_main("Error: not a directory\n", 2),
        )
    with pytest.raises(MemberAbsent, match="exit 0"):
        cohort_ledger.run_cli_json(["hypergumbo", "x"], main=_fake_main("", None))
    with pytest.raises(MemberAbsent, match="no JSON"):
        cohort_ledger.run_cli_json(["hypergumbo", "x"], main=_fake_main("{ not json", 0))


def test_run_cli_json_real_cli_missing_repo_is_absent_not_empty(tmp_path):
    """The reproduced case, through production: verify-claims on a path that
    does not exist used to come back as ``{}`` and be scored as zero findings."""
    claims = _ROOT / "docs" / "example-claims" / "generic-taint-claims.yaml"
    with pytest.raises(MemberAbsent, match="no JSON"):
        cohort_ledger.run_cli_json([
            "hypergumbo", "verify-claims", str(tmp_path / "kserve"),
            "--claims", str(claims), "--json",
        ])


# ---------------------------------------------------------------------------
# Structural guard: every cohort-taking script uses the ledger
# ---------------------------------------------------------------------------

#: A positional argument named ``repos`` or ``maps`` taking one-or-more /
#: zero-or-more values: that script measures a cohort.
_COHORT_ARG = re.compile(
    r"""add_argument\(\s*["'](?:repos|maps)["'][^)]*nargs\s*=\s*["'][+*]["']""",
    re.S,
)


def _cohort_scripts_without_ledger(scripts_dir: Path) -> list[str]:
    offenders = []
    for path in sorted(scripts_dir.iterdir()):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # pragma: no cover - a binary in scripts/
            continue
        if _COHORT_ARG.search(text) and "CohortLedger(" not in text:
            offenders.append(path.name)
    return offenders


def test_guard_detects_a_cohort_script_without_the_ledger(tmp_path):
    """The guard can fail: a synthetic cohort script without the ledger is
    reported, and one with it is not."""
    (tmp_path / "bad.py").write_text('ap.add_argument("repos", nargs="+")\n')
    (tmp_path / "maps.py").write_text(
        'parser.add_argument(\n    "maps", nargs="+", type=Path,\n)\n')
    (tmp_path / "good.py").write_text(
        'ap.add_argument("repos", nargs="*")\nledger = CohortLedger(args.repos)\n')
    (tmp_path / "single.py").write_text('ap.add_argument("repo")\n')
    (tmp_path / "sub").mkdir()
    assert _cohort_scripts_without_ledger(tmp_path) == ["bad.py", "maps.py"]


def test_every_cohort_script_states_its_cohort():
    scripts = _ROOT / "scripts"
    found = [
        p.name for p in sorted(scripts.iterdir())
        if p.is_file() and _COHORT_ARG.search(p.read_text(encoding="utf-8", errors="replace"))
    ]
    # Reach first: the guard is vacuous if the regex matches nothing.
    assert "measure-row-inflation.py" in found
    assert "measure-blind-language-signal.py" in found
    assert _cohort_scripts_without_ledger(scripts) == []


def test_claim_is_json_serialisable():
    ledger = CohortLedger(["a", "b"])
    with ledger.attempt("a"):
        raise OSError(errno.ENOSPC, "No space left on device")
    json.dumps(ledger.claim())
