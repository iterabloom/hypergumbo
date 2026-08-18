# SPDX-License-Identifier: AGPL-3.0-or-later
"""The drift scan must disclose what dates a docstring, and must not guess.

``scripts/check-docstring-drift`` dates a docstring by ``git blame``-ing its
line range and taking the youngest commit. That answers "when did any byte in
this region last change", which is NOT "when were these claims last checked".
Three mass events in this repo's history decouple the two — the ADR-0010
monorepo reorg (blame follows the move), the Phase-3/4 TreeSitterAnalyzer
migrations (boilerplate stamped into ~106 analyzer docstrings), and the
ADR-3bbb subcategory sweep (ONE LINE prepended to 45 linker docstrings).

THE TEMPTING FIX IS WRONG, and these tests exist mostly to keep it from being
re-attempted. The obvious move is to detect such sweeps and "look past" them
to the last real edit. It cannot be done from blame. Measured on this corpus:

    3f29f139b1  ADR-3bbb convention stamp   median 1 line   2% of docstring
    c9406ccd54  staleness-audit fix         median 2 lines  4% of docstring

The first is a prefix nobody read; the second is the most careful reading
those files have ever had. Same size, similar file counts. Any rule that
demotes the stamp also demotes every prior audit's own fix commits — which
would make each audit blind the next one to exactly the files it just
verified, a self-defeating instrument.

So the scan reports three FACTS (floor sha, share of the docstring it owns,
how many other docstrings it floors) and leaves judgement to the semantic
step, which is where the playbook already puts judgement.

Separately, the day-based threshold decays. ``delta = ds_age - body_age`` with
a PINNED ds grows with wall clock alone: the ``ds=2026-02-19`` cohort could
not fire at ANY body age on 2026-05-15 (ds_age was 85, under the 90 minimum)
and fires for anything touched within 89 days by 2026-08-18. 28 flags became
87 with nothing having drifted. ``body_commits_since_ds`` counts commits
rather than days and so does not move with the calendar.
"""

import importlib.machinery
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load():
    loader = importlib.machinery.SourceFileLoader(
        "check_docstring_drift", str(SCRIPTS / "check-docstring-drift")
    )
    spec = importlib.util.spec_from_loader("check_docstring_drift", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


cdd = _load()

DAY = 86400


# --- floor_facts: report, never infer --------------------------------------


def test_floor_facts_reports_share_and_cofloor_count():
    """A one-line stamp on a 10-line docstring must be VISIBLE as one line."""
    blamed = [(900 * DAY, "stamp")] + [(100 * DAY, "old")] * 9
    f = cdd.floor_facts(blamed, {"stamp": 23})
    assert f["ds_floor_sha"] == "stamp"
    assert f["ds_ts"] == 900 * DAY, "the reported date is still the real floor"
    assert f["ds_floor_share"] == 0.1, "owns 1 of 10 lines"
    assert f["ds_floor_cofloors"] == 22, "and floors 22 OTHER docstrings"


def test_floor_facts_does_not_look_past_the_floor():
    """THE LOAD-BEARING TEST: no auto-skipping, because it cannot be justified.

    If this ever starts returning the older timestamp, the instrument has gone
    back to guessing intent from blame — and will silently demote every prior
    audit's fix commits along with the stamps.
    """
    blamed = [(900 * DAY, "stamp"), (100 * DAY, "realwork")]
    f = cdd.floor_facts(blamed, {"stamp": 45})
    assert f["ds_ts"] == 900 * DAY
    assert f["ds_floor_sha"] == "stamp"


def test_floor_facts_on_an_unshared_floor_reports_zero_cofloors():
    """POSITIVE CONTROL: an ordinary individual edit must look ordinary."""
    f = cdd.floor_facts([(500 * DAY, "solo")], {"solo": 1})
    assert f["ds_floor_cofloors"] == 0
    assert f["ds_floor_share"] == 1.0


def test_floor_facts_on_empty_blame_is_null_not_zero():
    """A file we could not blame has no date; it must not read as epoch."""
    f = cdd.floor_facts([], {})
    assert f["ds_ts"] is None and f["ds_floor_sha"] is None


# --- the non-decaying metric ----------------------------------------------


def _row(**kw):
    base = {"path": "x.py", "ds_ts": 100 * DAY, "body_ts": 395 * DAY,
            "ds_floor_sha": "sha", "ds_floor_share": 1.0,
            "ds_floor_cofloors": 0, "body_commits_since_ds": 0}
    base.update(kw)
    return base


def test_body_commit_count_flags_even_when_the_day_delta_is_short():
    """The clock-independent arm must be able to fire on its own.

    A docstring untouched across 40 commits is stale regardless of whether
    those commits span 90 days or 9.
    """
    rows = [_row(ds_ts=380 * DAY, body_commits_since_ds=40)]
    out = cdd.partition_rows(rows, window_days=120, min_delta_days=90,
                             min_body_commits=12, now_ts=400 * DAY)
    assert len(out["flagged"]) == 1, "delta is only 15d; commit count carries it"


def test_the_day_delta_arm_still_fires_on_its_own():
    """POSITIVE CONTROL: adding the new arm must not disable the old one."""
    rows = [_row(ds_ts=10 * DAY, body_commits_since_ds=0)]
    out = cdd.partition_rows(rows, window_days=120, min_delta_days=90,
                             min_body_commits=12, now_ts=400 * DAY)
    assert len(out["flagged"]) == 1


def test_neither_arm_means_no_flag():
    """POSITIVE CONTROL the other way: a fresh docstring stays unflagged."""
    rows = [_row(ds_ts=390 * DAY, body_commits_since_ds=1)]
    out = cdd.partition_rows(rows, window_days=120, min_delta_days=90,
                             min_body_commits=12, now_ts=400 * DAY)
    assert out["flagged"] == [] and out["pinned"] == []


def test_a_stale_file_nobody_touched_is_out_of_window():
    """The window still bounds the scan to code under active change."""
    rows = [_row(ds_ts=10 * DAY, body_ts=20 * DAY, body_commits_since_ds=99)]
    out = cdd.partition_rows(rows, window_days=120, min_delta_days=90,
                             min_body_commits=12, now_ts=400 * DAY)
    assert out["flagged"] == [] and out["pinned"] == []


# --- the pinned bucket -----------------------------------------------------


def test_a_stamp_pinned_row_is_separated_but_not_dropped():
    """Disclosed in its own bucket: unreviewed is not the same as clean."""
    rows = [_row(path="linker.py", ds_ts=10 * DAY, body_commits_since_ds=30,
                 ds_floor_share=0.02, ds_floor_cofloors=22)]
    out = cdd.partition_rows(rows, window_days=120, min_delta_days=90,
                             min_body_commits=12, now_ts=400 * DAY)
    assert [r["path"] for r in out["pinned"]] == ["linker.py"]
    assert out["flagged"] == []


def test_a_big_share_on_a_shared_floor_stays_flagged():
    """An audit fix floors many files but OWNS them — it must not be demoted.

    Regression guard for the self-blinding failure: a commit that rewrote 40%
    of a docstring reviewed it, however many files it touched.
    """
    rows = [_row(path="audited.py", ds_ts=10 * DAY, body_commits_since_ds=30,
                 ds_floor_share=0.40, ds_floor_cofloors=30)]
    out = cdd.partition_rows(rows, window_days=120, min_delta_days=90,
                             min_body_commits=12, now_ts=400 * DAY)
    assert [r["path"] for r in out["flagged"]] == ["audited.py"]
    assert out["pinned"] == []


def test_a_pinned_row_can_only_be_flagged_by_the_clock_independent_arm():
    """The recalibration, stated as a property.

    Pinning IS the decay mechanism — a ds that stops moving while the clock
    runs makes `delta` grow by itself, so on a pinned row `delta >= 90`
    degenerates into "was this touched recently". Such a row must therefore
    earn its flag from the commit count or not at all. Without this the scan
    re-inflates every year: 28 flags became 87 between 2026-05-15 and
    2026-08-18 with no docstring having drifted.
    """
    huge_delta_but_pinned = _row(path="pinned.py", ds_ts=1 * DAY,
                                 body_commits_since_ds=0,
                                 ds_floor_share=0.02, ds_floor_cofloors=22)
    out = cdd.partition_rows([huge_delta_but_pinned], window_days=120,
                             min_delta_days=90, min_body_commits=12,
                             now_ts=400 * DAY)
    assert out["flagged"] == [] and out["pinned"] == [], (
        "a 399-day delta on a pinned floor is a clock artifact, not evidence"
    )


def test_the_same_huge_delta_DOES_flag_when_the_floor_is_particular():
    """CONTROL for the test above: gating must key on pinning, not on delta.

    Without this, a rule that simply ignored large deltas would satisfy the
    previous test while deleting the day arm entirely.
    """
    unpinned = _row(path="real.py", ds_ts=1 * DAY, body_commits_since_ds=0,
                    ds_floor_share=1.0, ds_floor_cofloors=0)
    out = cdd.partition_rows([unpinned], window_days=120, min_delta_days=90,
                             min_body_commits=12, now_ts=400 * DAY)
    assert [r["path"] for r in out["flagged"]] == ["real.py"]
    assert out["flagged"][0]["flag_reason"] == "days"
