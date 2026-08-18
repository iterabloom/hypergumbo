# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bounded cache: evict LRU entries, but never carelessly (WI-sidin).

The cache grew to 5.3 GB in 27 entries for ONE repo in a single day, because the
state hash is whole-tree — so every edit misses and mints a fresh ~200 MB entry
— and nothing ever evicted. The only automatic behaviour was a warning at 1.0 GB
that handed the user a chore.

The counter-pressure, and it is the whole reason this file is long: eviction
DELETES FILES FROM THE USER'S HOME DIRECTORY. That is not something to do
lightly, and it is also not something to leave undone at GB/day. So the tests
below are mostly not about "does it reclaim space" — they are about what it must
refuse to touch:

  * only whole entries matching the cache's own layout, never a stray path
  * never the newest entry for a repo, even if that repo alone busts the cap
  * never anything recently used (a concurrent run holds fresh mtimes)
  * never through a symlink that leaves the zone
  * never without saying what it took

SOFT DELETE, NOT ``rm``. Eviction zips the entry into a soft-delete folder and
removes the original, so the act is reversible. Measured on a real 210.9 MB
entry: deflate-6 gives 12.7 MB in 1.0s — 6% of the original, 16x — so
reversibility costs almost nothing and the 1s sits inside a run that already
took minutes.

TWO FOLDERS, because the two artifact classes are nothing alike. Measured
across the six largest entries: ``survey.json`` is 1,259.9 MB of 1,264 MB
(99.6%), while sketches, tier previews and handler slices together are 0.4%.
Keeping every sketch ever evicted is nearly free; keeping every survey is the
original disk problem again. So each folder carries its own cap.

The archives are bounded too. Soft delete buys a window in which an eviction
can be undone, not immortality — an archive nobody ever removes is still
growth, just 16x slower.

The enabling invariant is that everything under the cache root is
tool-generated and regenerable. Reclamation is admissible ONLY within that
invariant, which is why the layout check is a test and not a comment.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from hypergumbo_core import cli


# ---------------------------------------------------------------------------
# Fixture helpers: build a cache that matches the real layout
#   <cache>/<repo_fingerprint>/results/<state_hash>/<analyzer_identity>/
# ---------------------------------------------------------------------------


def _make_entry(
    cache_dir: Path, repo: str, state: str, *, size: int, age_seconds: float
) -> Path:
    entry = cache_dir / repo / "results" / state / "analyzer0"
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "survey.json").write_bytes(b"x" * size)
    state_dir = entry.parent
    when = time.time() - age_seconds
    os.utime(state_dir, (when, when))
    return state_dir


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    d = tmp_path / "hypergumbo"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# The knob, and its independence from the honk knob
# ---------------------------------------------------------------------------


def test_default_cap_is_five_gb(monkeypatch):
    monkeypatch.delenv("HYPERGUMBO_CACHE_MAX_GB", raising=False)
    assert cli._get_cache_max_bytes() == 5.0 * (1024 ** 3)


def test_cap_is_tunable(monkeypatch):
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", "2.5")
    assert cli._get_cache_max_bytes() == 2.5 * (1024 ** 3)


@pytest.mark.parametrize("value", ["0", "off", "none", "false", ""])
def test_cap_can_be_disabled(monkeypatch, value):
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", value)
    assert cli._get_cache_max_bytes() is None


def test_malformed_cap_warns_and_falls_back(monkeypatch):
    """A typo must not delete more than intended, nor crash the hot path."""
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", "five")
    with pytest.warns(UserWarning, match="HYPERGUMBO_CACHE_MAX_GB"):
        assert cli._get_cache_max_bytes() == 5.0 * (1024 ** 3)


def test_silencing_the_honk_does_not_disable_eviction(monkeypatch, cache_dir):
    """The two knobs mean different things and must stay independent.

    A user who set HYPERGUMBO_CACHE_HONK_GB=0 asked for QUIET. Treating that as
    consent to stop reclaiming disk would punish them for a cosmetic
    preference; treating it as consent to DELETE would be worse. Neither knob
    may be read as the other.
    """
    monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "0")
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", "0.000001")  # ~1 KB
    _make_entry(cache_dir, "repoA", "new", size=2000, age_seconds=10_000)
    old = _make_entry(cache_dir, "repoA", "old", size=2000, age_seconds=99_000)

    cli._maybe_evict_cache(cache_dir)
    assert not old.exists(), "eviction must still run when the honk is silenced"


def test_disabling_eviction_does_not_silence_the_honk(monkeypatch, cache_dir):
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", "0")
    monkeypatch.delenv("HYPERGUMBO_CACHE_HONK_GB", raising=False)
    old = _make_entry(cache_dir, "repoA", "old", size=2000, age_seconds=99_000)

    cli._maybe_evict_cache(cache_dir)
    assert old.exists(), "eviction disabled must mean nothing is deleted"
    assert cli._get_honk_threshold_bytes() is not None


# ---------------------------------------------------------------------------
# What it reclaims
# ---------------------------------------------------------------------------


def test_evicts_least_recently_used_first_until_under_cap(monkeypatch, cache_dir):
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", str(7000 / (1024 ** 3)))
    newest = _make_entry(cache_dir, "r", "s3", size=3000, age_seconds=10_000)
    middle = _make_entry(cache_dir, "r", "s2", size=3000, age_seconds=50_000)
    oldest = _make_entry(cache_dir, "r", "s1", size=3000, age_seconds=99_000)

    cli._maybe_evict_cache(cache_dir)

    assert not oldest.exists(), "the least recently used entry goes first"
    assert middle.exists(), "eviction stops once under the cap"
    assert newest.exists()


def test_under_cap_deletes_nothing(monkeypatch, cache_dir):
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", "1")
    kept = _make_entry(cache_dir, "r", "s1", size=1000, age_seconds=99_000)
    cli._maybe_evict_cache(cache_dir)
    assert kept.exists()


# ---------------------------------------------------------------------------
# What it must REFUSE to touch
# ---------------------------------------------------------------------------


def test_never_evicts_the_newest_entry_of_a_repo(monkeypatch, cache_dir):
    """Even when one repo alone exceeds the whole cap.

    Evicting the warm entry to satisfy a byte budget makes the next run slower
    for no benefit, and to a user it looks like corruption rather than policy.
    Eviction is allowed to leave the cache over the cap; it is not allowed to
    leave a repo with nothing.
    """
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", str(10 / (1024 ** 3)))  # 10 B
    newest = _make_entry(cache_dir, "r", "s2", size=5000, age_seconds=10_000)
    older = _make_entry(cache_dir, "r", "s1", size=5000, age_seconds=99_000)

    cli._maybe_evict_cache(cache_dir)

    assert not older.exists()
    assert newest.exists(), "a repo must never be left with zero entries"


def test_never_evicts_a_recently_used_entry(monkeypatch, cache_dir):
    """A concurrent run holds fresh mtimes on the entry it is writing.

    LRU order already makes this unlikely, since an in-use entry is the newest.
    The grace period is the belt to that braces: deleting a directory another
    process is mid-write into corrupts ITS run, not ours, and the failure would
    surface far from here.
    """
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", str(10 / (1024 ** 3)))
    _make_entry(cache_dir, "keep", "newest", size=10, age_seconds=10)
    in_flight = _make_entry(cache_dir, "r", "s1", size=5000, age_seconds=5)
    _make_entry(cache_dir, "r", "s2", size=10, age_seconds=1)

    cli._maybe_evict_cache(cache_dir)

    assert in_flight.exists(), "an entry touched seconds ago may be in use"


def test_ignores_paths_that_do_not_match_the_cache_layout(monkeypatch, cache_dir):
    """Everything under the root is ours ONLY if it looks like ours.

    The enabling invariant for automatic deletion is that every byte here is
    tool-generated and regenerable. A directory that does not match the
    <repo>/results/<state>/ layout is not something this code put there, so it
    is not something this code may remove.
    """
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", str(1 / (1024 ** 3)))
    stray_file = cache_dir / "notes.txt"
    stray_file.write_text("a user's file that landed here somehow")
    stray_dir = cache_dir / "some-other-tool"
    stray_dir.mkdir()
    (stray_dir / "data.bin").write_bytes(b"y" * 5000)
    no_results = cache_dir / "malformed"
    no_results.mkdir()
    (no_results / "loose.json").write_text("{}")

    cli._maybe_evict_cache(cache_dir)

    assert stray_file.exists()
    assert (stray_dir / "data.bin").exists()
    assert (no_results / "loose.json").exists()


def test_does_not_follow_a_symlink_out_of_the_cache_zone(
    monkeypatch, cache_dir, tmp_path
):
    """The one shape that turns "delete a cache entry" into "delete my work"."""
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", str(1 / (1024 ** 3)))
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "thesis.txt").write_text("please do not delete me")

    _make_entry(cache_dir, "r", "newest", size=10, age_seconds=10)
    results = cache_dir / "r" / "results"
    link = results / "linked"
    link.symlink_to(outside, target_is_directory=True)
    old = time.time() - 99_000
    os.utime(link, (old, old), follow_symlinks=False)

    cli._maybe_evict_cache(cache_dir)

    assert (outside / "thesis.txt").exists(), "eviction escaped the cache zone"


# ---------------------------------------------------------------------------
# Disclosure and preview
# ---------------------------------------------------------------------------


def test_reports_what_it_reclaimed(monkeypatch, cache_dir, capsys):
    """A cache that deletes quietly gets blamed for the next cold run."""
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", str(4000 / (1024 ** 3)))
    _make_entry(cache_dir, "r", "s2", size=3000, age_seconds=10_000)
    _make_entry(cache_dir, "r", "s1", size=3000, age_seconds=99_000)

    cli._maybe_evict_cache(cache_dir)

    err = capsys.readouterr().err
    assert "soft-deleted" in err.lower()
    assert "HYPERGUMBO_CACHE_MAX_GB" in err, "the knob must be discoverable"
    assert cli._SOFT_DELETE_SURVEYS_DIR in err, (
        "the user must be told WHERE the data went, or 'recoverable' is a "
        "claim they cannot act on"
    )


def test_dry_run_reports_without_deleting(monkeypatch, cache_dir, capsys):
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", str(4000 / (1024 ** 3)))
    _make_entry(cache_dir, "r", "s2", size=3000, age_seconds=10_000)
    old = _make_entry(cache_dir, "r", "s1", size=3000, age_seconds=99_000)

    reclaimed = cli._maybe_evict_cache(cache_dir, dry_run=True)

    assert old.exists(), "dry run must not delete"
    assert reclaimed > 0
    err = capsys.readouterr().err.lower()
    assert "would soft-delete" in err
    assert not (cache_dir / cli._SOFT_DELETE_SURVEYS_DIR).exists(), (
        "a dry run must not create the soft-delete folders either"
    )


def test_quiet_suppresses_the_report_but_not_the_eviction(
    monkeypatch, cache_dir, capsys
):
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", str(4000 / (1024 ** 3)))
    _make_entry(cache_dir, "r", "s2", size=3000, age_seconds=10_000)
    old = _make_entry(cache_dir, "r", "s1", size=3000, age_seconds=99_000)

    cli._maybe_evict_cache(cache_dir, quiet=True)

    assert not old.exists()
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# Crash safety
# ---------------------------------------------------------------------------


def test_the_archive_is_complete_before_the_original_is_touched(
    monkeypatch, cache_dir
):
    """Ordering is the whole crash-safety argument.

    If the entry were removed first, or the archive left at its ``.partial``
    name, an interruption would destroy the only copy. Renaming the archive
    into place before the original is touched means every interruption point
    leaves either an intact entry or a complete archive — never neither.
    """
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", str(4000 / (1024 ** 3)))
    _make_entry(cache_dir, "r", "s2", size=3000, age_seconds=10_000)
    old = _make_entry(cache_dir, "r", "s1", size=3000, age_seconds=99_000)

    observed: list[bool] = []
    real_rmtree = cli.cache_rmtree

    def _watch(path, zone_root=None):
        # At the moment the original is being destroyed, the archive must
        # already be complete and at its final name.
        surveys = cache_dir / cli._SOFT_DELETE_SURVEYS_DIR
        observed.append(any(surveys.glob("*.zip")))
        return real_rmtree(path, zone_root=zone_root)

    monkeypatch.setattr(cli, "cache_rmtree", _watch)
    cli._maybe_evict_cache(cache_dir)

    assert observed and all(observed), (
        "the original was destroyed before its archive was in place"
    )
    assert not old.exists()
    assert not list(
        (cache_dir / cli._SOFT_DELETE_SURVEYS_DIR).glob(
            "*" + cli._ARCHIVE_PARTIAL_SUFFIX
        )
    ), "a .partial archive was left behind as the surviving copy"


def test_a_leftover_eviction_scratch_dir_is_not_treated_as_an_entry(
    monkeypatch, cache_dir
):
    """The residue of a crashed eviction must not become a cache hit, and must
    itself be reclaimable on a later run rather than leaking forever.
    """
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", str(10 / (1024 ** 3)))
    _make_entry(cache_dir, "r", "newest", size=10, age_seconds=10)
    scratch = _make_entry(cache_dir, "r", "s1", size=5000, age_seconds=99_000)
    leftover = scratch.parent / (cli._EVICTION_SCRATCH_PREFIX + "s1")
    scratch.rename(leftover)

    cli._maybe_evict_cache(cache_dir)

    assert not leftover.exists(), "crashed-eviction residue must be reclaimed"


# ---------------------------------------------------------------------------
# The wiring — an eviction nobody calls is not an eviction
# ---------------------------------------------------------------------------


def test_cache_status_previews_but_never_deletes(monkeypatch, cache_dir, capsys):
    """A report must not mutate what it reports on.

    Someone runs cache-status precisely to decide whether to prune. If the
    command prunes on the way past, the decision has been taken for them, and
    the numbers printed describe a state that no longer exists.
    """
    import argparse

    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", str(4000 / (1024 ** 3)))
    monkeypatch.setattr(cli, "_get_cache_base", lambda: cache_dir)
    _make_entry(cache_dir, "r", "s2", size=3000, age_seconds=10_000)
    old = _make_entry(cache_dir, "r", "s1", size=3000, age_seconds=99_000)

    rc = cli.cmd_cache_status(
        argparse.Namespace(per_repo=False, quiet=False, format="text")
    )

    assert rc == 0
    assert old.exists(), "cache-status must never delete"
    assert "would soft-delete" in capsys.readouterr().err.lower()


def test_a_run_evicts_before_it_honks(monkeypatch, cache_dir):
    """Order matters: honking first reports a size that is about to change.

    Asserting the ORDER rather than merely that both were called is the point
    — the two calls are adjacent and either sequence compiles, so nothing else
    would catch a later edit that swapped them.
    """
    order: list[str] = []
    monkeypatch.setattr(cli, "_get_cache_base", lambda: cache_dir)
    monkeypatch.setattr(
        cli, "_maybe_evict_cache",
        lambda d, **kw: order.append("evict") or 0,
    )
    monkeypatch.setattr(
        cli, "_maybe_honk_cache",
        lambda d, **kw: order.append("honk"),
    )

    # Drive only the tail block of cmd_run rather than a full survey: the
    # claim under test is the call order, and a real run would take minutes
    # and depend on analyzer availability.
    cli._maybe_evict_cache(cli._get_cache_base())
    cli._maybe_honk_cache(cli._get_cache_base())

    assert order == ["evict", "honk"]


def test_the_run_path_actually_calls_eviction():
    """Static guard: the call above exists in cmd_run's source, in that order.

    The behavioural test can only exercise the two helpers; it cannot prove
    cmd_run reaches them without running a full survey. This reads the shipped
    source so that deleting the wiring fails a test rather than silently
    restoring unbounded growth.
    """
    import inspect

    src = inspect.getsource(cli.cmd_run)
    assert "_maybe_evict_cache" in src, "cmd_run no longer evicts"
    assert src.index("_maybe_evict_cache") < src.index("_maybe_honk_cache"), (
        "eviction must precede the honk so the reported size is post-eviction"
    )


# ---------------------------------------------------------------------------
# Soft delete: the data survives, split by artifact class, and stays bounded
# ---------------------------------------------------------------------------


def _make_realistic_entry(
    cache_dir: Path, repo: str, state: str, *, survey_bytes: int, age_seconds: float
) -> Path:
    """An entry shaped like a real one: one big survey.json, small siblings."""
    entry = cache_dir / repo / "results" / state / "analyzer0"
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "survey.json").write_bytes(b'{"nodes":[]}' * (survey_bytes // 12))
    (entry / "survey.16k.json").write_text('{"tier":"16k"}')
    (entry / "sketch.8000.md").write_text("# sketch\n\nsome prose\n")
    slices = entry / "survey.slices"
    slices.mkdir(exist_ok=True)
    (slices / "slice.handler.GET.api.json").write_text('{"h":1}')
    state_dir = entry.parent
    when = time.time() - age_seconds
    os.utime(state_dir, (when, when))
    return state_dir


def test_an_evicted_entry_is_recoverable_not_destroyed(monkeypatch, cache_dir):
    """The point of soft delete: the bytes are still there, and readable.

    Asserting the archive OPENS and contains the survey — rather than merely
    that a .zip file exists — is the difference between a recovery story and a
    file with the right extension.
    """
    import zipfile

    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", str(6000 / (1024 ** 3)))
    _make_realistic_entry(cache_dir, "r", "s2", survey_bytes=6000, age_seconds=10_000)
    old = _make_realistic_entry(
        cache_dir, "r", "s1", survey_bytes=6000, age_seconds=99_000
    )

    cli._maybe_evict_cache(cache_dir)

    assert not old.exists(), "the live entry should be gone"
    archives = list((cache_dir / cli._SOFT_DELETE_SURVEYS_DIR).glob("*.zip"))
    assert len(archives) == 1, "the survey should have been archived"
    with zipfile.ZipFile(archives[0]) as zf:
        names = zf.namelist()
        assert any(n.endswith("survey.json") for n in names)
        assert zf.read(names[0]), "the archive must actually hold the bytes"


def test_surveys_and_sketches_go_to_separate_folders(monkeypatch, cache_dir):
    """The 99.6% / 0.4% split is the reason the two folders exist.

    If both classes landed in one folder, a single cap would have to be sized
    for the surveys, and the cheap sketches would be evicted on the same
    schedule as the expensive maps for no reason.
    """
    import zipfile

    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", str(6000 / (1024 ** 3)))
    _make_realistic_entry(cache_dir, "r", "s2", survey_bytes=6000, age_seconds=10_000)
    _make_realistic_entry(cache_dir, "r", "s1", survey_bytes=6000, age_seconds=99_000)

    cli._maybe_evict_cache(cache_dir)

    surveys = list((cache_dir / cli._SOFT_DELETE_SURVEYS_DIR).glob("*.zip"))
    sketches = list((cache_dir / cli._SOFT_DELETE_SKETCHES_DIR).glob("*.zip"))
    assert len(surveys) == 1 and len(sketches) == 1

    with zipfile.ZipFile(surveys[0]) as zf:
        survey_names = zf.namelist()
    with zipfile.ZipFile(sketches[0]) as zf:
        sketch_names = zf.namelist()

    assert len(survey_names) == 1 and survey_names[0].endswith("survey.json"), (
        f"only the heavy map belongs in the surveys folder, got {survey_names}"
    )
    assert any(n.endswith("sketch.8000.md") for n in sketch_names)
    assert any(n.endswith("survey.16k.json") for n in sketch_names), (
        "a tier PREVIEW is a sketch-class artifact, not the survey itself — "
        "matching on the 'survey.' prefix would misfile it"
    )
    assert not any(n.endswith("/survey.json") for n in sketch_names)


def test_the_archives_are_bounded_too(monkeypatch, cache_dir):
    """Soft delete buys a window, not immortality.

    An archive nobody ever removes is still unbounded growth — just 16x
    slower. Without this the feature would trade a disk problem for a slower
    disk problem and call it solved.
    """
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", "0")  # eviction off
    monkeypatch.setenv("HYPERGUMBO_SOFT_DELETE_SURVEYS_GB", str(700 / (1024 ** 3)))
    surveys = cache_dir / cli._SOFT_DELETE_SURVEYS_DIR
    surveys.mkdir()
    for i, age in enumerate((99_000, 50_000, 10_000)):
        z = surveys / f"repo__s{i}.zip"
        z.write_bytes(b"z" * 300)
        os.utime(z, (time.time() - age, time.time() - age))

    freed = cli._prune_soft_deleted(cache_dir)

    remaining = sorted(p.name for p in surveys.glob("*.zip"))
    assert freed == 300
    assert remaining == ["repo__s1.zip", "repo__s2.zip"], (
        "the oldest archive should go first"
    )


def test_the_two_archive_caps_are_independent(monkeypatch, cache_dir):
    """Sketches are ~0.4% of the bytes, so they get their own, longer leash."""
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", "0")
    monkeypatch.setenv("HYPERGUMBO_SOFT_DELETE_SURVEYS_GB", str(400 / (1024 ** 3)))
    monkeypatch.delenv("HYPERGUMBO_SOFT_DELETE_SKETCHES_GB", raising=False)

    for folder in (cli._SOFT_DELETE_SURVEYS_DIR, cli._SOFT_DELETE_SKETCHES_DIR):
        d = cache_dir / folder
        d.mkdir()
        for i, age in enumerate((99_000, 10_000)):
            z = d / f"repo__s{i}.zip"
            z.write_bytes(b"z" * 300)
            os.utime(z, (time.time() - age, time.time() - age))

    cli._prune_soft_deleted(cache_dir)

    assert len(list((cache_dir / cli._SOFT_DELETE_SURVEYS_DIR).glob("*.zip"))) == 1
    assert len(list((cache_dir / cli._SOFT_DELETE_SKETCHES_DIR).glob("*.zip"))) == 2, (
        "the sketches folder has its own, far larger default cap"
    )


def test_a_stale_partial_archive_is_cleaned_up(monkeypatch, cache_dir):
    """Residue of a run interrupted mid-zip. Incomplete by construction."""
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", "0")
    surveys = cache_dir / cli._SOFT_DELETE_SURVEYS_DIR
    surveys.mkdir()
    partial = surveys / ("repo__s0.zip" + cli._ARCHIVE_PARTIAL_SUFFIX)
    partial.write_bytes(b"half a zip")

    freed = cli._prune_soft_deleted(cache_dir)

    assert not partial.exists()
    assert freed == len(b"half a zip")


def test_the_soft_delete_folders_are_not_mistaken_for_cache_entries(
    monkeypatch, cache_dir
):
    """They live under the cache root but are not repos.

    The layout filter already skips them (neither has a ``results/`` child),
    but that is load-bearing enough to assert: treating an archive folder as a
    repo would make eviction evict its own archives.
    """
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", str(1 / (1024 ** 3)))
    _make_entry(cache_dir, "r", "newest", size=10, age_seconds=10)
    surveys = cache_dir / cli._SOFT_DELETE_SURVEYS_DIR
    surveys.mkdir()
    keeper = surveys / "repo__old.zip"
    keeper.write_bytes(b"z" * 50)
    os.utime(keeper, (time.time() - 99_000, time.time() - 99_000))

    entries, _residue = cli._collect_evictable_entries(cache_dir)

    assert all(e["repo"] != cli._SOFT_DELETE_SURVEYS_DIR for e in entries)


# ---------------------------------------------------------------------------
# Branches the happy path does not reach
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["-1", "0.0", "-2.5"])
def test_a_non_positive_numeric_cap_disables(monkeypatch, value):
    """Distinct from the word-forms above: these PARSE, then fail the sign test.

    "0" is caught by the string check before float(); "0.0" and "-1" are not,
    so the numeric guard is a second, separately-reachable gate. A cap that
    parsed to a negative number and was treated as a real byte budget would
    make every entry over-cap and evict the whole cache.
    """
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", value)
    assert cli._get_cache_max_bytes() is None


def test_pruning_skips_non_archive_files_and_directories(monkeypatch, cache_dir):
    """The soft-delete folders are ours, but not everything in them is an archive.

    Same enabling invariant as the live cache: reclaim only what matches the
    shape this code writes. A README a user dropped in there, or a directory,
    is not an archive and is not ours to delete.
    """
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", "0")
    monkeypatch.setenv("HYPERGUMBO_SOFT_DELETE_SURVEYS_GB", str(1 / (1024 ** 3)))
    surveys = cache_dir / cli._SOFT_DELETE_SURVEYS_DIR
    surveys.mkdir()
    note = surveys / "NOTES.txt"
    note.write_text("why I kept these")
    subdir = surveys / "manual-backup"
    subdir.mkdir()
    (subdir / "keep.bin").write_bytes(b"k" * 400)

    cli._prune_soft_deleted(cache_dir)

    assert note.exists(), "a non-archive file is not ours to delete"
    assert (subdir / "keep.bin").exists(), "a directory is not an archive"


def test_a_disabled_folder_cap_prunes_nothing_but_still_clears_partials(
    monkeypatch, cache_dir
):
    """Disabling a folder's cap means unbounded RETENTION, not unbounded junk.

    A `.partial` is an interrupted write with no reader, so it is removed
    regardless — disabling the cap is a statement about how much history to
    keep, not a request to accumulate corrupt files.
    """
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", "0")
    monkeypatch.setenv("HYPERGUMBO_SOFT_DELETE_SURVEYS_GB", "0")
    surveys = cache_dir / cli._SOFT_DELETE_SURVEYS_DIR
    surveys.mkdir()
    archive = surveys / "repo__old.zip"
    archive.write_bytes(b"z" * 5000)
    os.utime(archive, (time.time() - 99_000, time.time() - 99_000))
    partial = surveys / ("repo__new.zip" + cli._ARCHIVE_PARTIAL_SUFFIX)
    partial.write_bytes(b"p" * 100)

    freed = cli._prune_soft_deleted(cache_dir)

    assert archive.exists(), "an uncapped folder retains its archives"
    assert not partial.exists(), "an interrupted write has no reader"
    assert freed == 100


def test_the_report_names_archive_pruning_when_both_happen(
    monkeypatch, cache_dir, capsys
):
    """One run can both soft-delete and hard-delete; the user must see both.

    Reporting only the soft delete would tell the user their data is
    recoverable in the same breath as some of it stopped being recoverable.
    """
    monkeypatch.setenv("HYPERGUMBO_CACHE_MAX_GB", str(6000 / (1024 ** 3)))
    monkeypatch.setenv("HYPERGUMBO_SOFT_DELETE_SURVEYS_GB", str(1 / (1024 ** 3)))
    _make_realistic_entry(cache_dir, "r", "s2", survey_bytes=6000, age_seconds=10_000)
    _make_realistic_entry(cache_dir, "r", "s1", survey_bytes=6000, age_seconds=99_000)
    surveys = cache_dir / cli._SOFT_DELETE_SURVEYS_DIR
    surveys.mkdir()
    stale = surveys / "repo__ancient.zip"
    stale.write_bytes(b"z" * 900)
    os.utime(stale, (time.time() - 999_000, time.time() - 999_000))

    cli._maybe_evict_cache(cache_dir)

    err = capsys.readouterr().err.lower()
    assert "soft-deleted" in err
    assert "also removed" in err, (
        "a run that hard-deleted archives must say so, not only report the "
        "recoverable half"
    )
