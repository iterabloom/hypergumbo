# SPDX-License-Identifier: AGPL-3.0-or-later
"""Permission contract for the transcript pipeline (INV-todig).

A secret's copy is never more readable than its origin. ``.env`` is mode
0600; transcripts republish command output (``git remote -v`` was the live
case, 138 credential occurrences measured 2026-07-29), so every
transcript-content file must be 0600 and every archive directory 0700 —
regardless of the process umask (002 in this environment, which is exactly
how the 664 files happened).

The contract has one home per language layer:

* shell — ``.agent/hooks/_shared/transcript_perms.sh`` (umask + idempotent
  hardening helpers), sourced by every shell writer;
* python — ``os.chmod(..., 0o600)`` at the two python write sites
  (``filter-transcript.py`` dest, ``on_transcript_change.py`` injection
  history), self-healing on every write so files created 664 by the
  pre-fix pipeline tighten on next touch.

Tests drive the REAL scripts under ``umask 002`` (the failure condition),
never a mocked one. Subprocess-driven like the sibling hook tests
(``test_touch_heartbeat.py``); these do not contribute to coverage and do
not need to — the scripts under test are not package code.
"""
from __future__ import annotations

import gzip
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT_REAL = Path(__file__).parent.parent
SHARED = REPO_ROOT_REAL / ".agent" / "hooks" / "_shared"
FILTER_SCRIPT = SHARED / "filter-transcript.py"
ROTATE_SCRIPT = SHARED / "rotate-on-session-end.sh"
PERMS_HELPER = SHARED / "transcript_perms.sh"

SID = "11111111-2222-3333-4444-555555555555"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _transcript_line() -> str:
    """One minimal row the filter keeps (an assistant text message)."""
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "hello"}],
            },
        }
    )


def _run_filter(src: Path, dest: Path, state: Path) -> None:
    """Run filter-transcript.py exactly as sync-transcript.sh does, but
    under the permissive umask that produced the 664 files."""
    subprocess.run(
        [
            "bash",
            "-c",
            f'umask 002; exec "{sys.executable}" "{FILTER_SCRIPT}" '
            f'"{src}" "{dest}" "{state}"',
        ],
        check=True,
        capture_output=True,
        text=True,
    )


class TestFilterTranscriptCreatesOwnerOnly:
    def test_fresh_dest_is_0600_despite_permissive_umask(self, tmp_path: Path):
        src = tmp_path / "vendor.jsonl"
        src.write_text(_transcript_line() + "\n")
        dest = tmp_path / f".current_session_transcript.{SID}.jsonl"
        state = tmp_path / "state.json"
        _run_filter(src, dest, state)
        assert dest.exists() and dest.stat().st_size > 0
        assert _mode(dest) == 0o600, (
            f"per-session transcript born {oct(_mode(dest))}; the whole "
            f"defect is inheriting the process umask"
        )

    def test_preexisting_664_dest_is_healed_on_next_write(self, tmp_path: Path):
        """Files created by the pre-fix pipeline are 664 on disk today; the
        writer must tighten them rather than faithfully preserve the
        downgrade (the scrubber's mode-preservation made this the only
        self-healing layer available)."""
        src = tmp_path / "vendor.jsonl"
        src.write_text(_transcript_line() + "\n")
        dest = tmp_path / f".current_session_transcript.{SID}.jsonl"
        dest.write_text("")
        os.chmod(dest, 0o664)
        state = tmp_path / "state.json"
        _run_filter(src, dest, state)
        assert _mode(dest) == 0o600


class TestRotationHardensSlotsAndArchive:
    @pytest.fixture()
    def seeded_root(self, tmp_path: Path) -> Path:
        """A fake REPO_ROOT whose .agent/ holds a full 664-era population:
        a current pair to promote, a last pair to demote, and a
        second-to-last pair that rotation must archive."""
        agent = tmp_path / ".agent"
        agent.mkdir()
        line = _transcript_line() + "\n"
        for name in (
            f".current_session_transcript.{SID}.jsonl",
            f".current_injection_history.{SID}.jsonl",
            ".last_session_transcript.jsonl",
            ".last_injection_history.jsonl",
            ".second_to_last_transcript.jsonl",
            ".second_to_last_injection_history.jsonl",
        ):
            p = agent / name
            p.write_text(line)
            os.chmod(p, 0o664)
        return tmp_path

    def test_legacy_setgid_archive_root_is_fully_stripped(self, seeded_root: Path):
        """The live archive dir was created 2775 (setgid). GNU chmod keeps
        setgid on directories under numeric modes, so a plain chmod 700
        leaves 2700 — group propagation intact for every future subdir.
        Rotation must strip it explicitly."""
        archive_root = seeded_root / ".agent" / ".archived-transcripts"
        archive_root.mkdir()
        # S103 is correct about the mode and beside the point: 2775 is the
        # hazard this test reproduces so it can assert rotation strips it.
        os.chmod(archive_root, 0o2775)  # noqa: S103
        subprocess.run(
            [
                "bash",
                "-c",
                f'umask 002; exec bash "{ROTATE_SCRIPT}" "{seeded_root}" "{SID}"',
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert _mode(archive_root) == 0o700, (
            f"archive root is {oct(_mode(archive_root))}; setgid survived"
        )

    def test_rotation_leaves_no_group_readable_transcript(self, seeded_root: Path):
        proc = subprocess.run(
            [
                "bash",
                "-c",
                f'umask 002; exec bash "{ROTATE_SCRIPT}" "{seeded_root}" "{SID}"',
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        agent = seeded_root / ".agent"

        for name in (
            ".last_session_transcript.jsonl",
            ".last_injection_history.jsonl",
            ".second_to_last_transcript.jsonl",
            ".second_to_last_injection_history.jsonl",
        ):
            slot = agent / name
            assert slot.exists(), f"{name} missing after rotation"
            assert _mode(slot) == 0o600, (
                f"{name} is {oct(_mode(slot))} after rotation; mv preserves "
                f"the 664 the pre-fix pipeline created, so rotation must "
                f"harden the global slots explicitly"
            )

        archive_root = agent / ".archived-transcripts"
        assert archive_root.is_dir()
        assert _mode(archive_root) == 0o700, (
            f"archive root is {oct(_mode(archive_root))}; group access on "
            f"the directory defeats 0600 on the files inside"
        )
        subdirs = [p for p in archive_root.iterdir() if p.is_dir()]
        assert subdirs, "rotation archived nothing despite a seeded pair"
        for sub in subdirs:
            assert _mode(sub) == 0o700, f"{sub.name} is {oct(_mode(sub))}"
            for f in sub.iterdir():
                assert _mode(f) == 0o600, f"{f.name} is {oct(_mode(f))}"

    def test_archived_content_survives_hardening(self, seeded_root: Path):
        """Permission hardening must not disturb the fail-safe archive
        contract — the gz still decompresses to the source's line count."""
        subprocess.run(
            [
                "bash",
                "-c",
                f'umask 002; exec bash "{ROTATE_SCRIPT}" "{seeded_root}" "{SID}"',
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        archive_root = seeded_root / ".agent" / ".archived-transcripts"
        gz_files = list(archive_root.glob("*/transcript.jsonl.gz"))
        assert gz_files, "no archived transcript produced"
        with gzip.open(gz_files[0], "rt") as f:
            assert len(f.readlines()) == 1


class TestArchiveScrubbedDest0600:
    def test_dest_gz_is_0600_despite_permissive_umask(self, tmp_path: Path):
        src = tmp_path / "t.jsonl"
        src.write_text(_transcript_line() + "\n")
        dest = tmp_path / "t.jsonl.gz"
        proc = subprocess.run(
            [
                "bash",
                "-c",
                f'umask 002; source "{SHARED}/archive_scrubbed.sh"; '
                f'archive_scrubbed "{src}" "{dest}" "{tmp_path}"',
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert _mode(dest) == 0o600


class TestInjectionHistory0600:
    def test_log_injection_history_creates_0600(self, tmp_path: Path):
        script = (
            "import sys, os\n"
            f"sys.path.insert(0, {str(SHARED)!r})\n"
            "os.umask(0o002)\n"
            "otc = __import__('on_transcript_change')\n"
            f"otc.log_injection_history({str(tmp_path)!r}, "
            "transcript_offset=0, agent_goals='', selected=[], injected=[], "
            f"skipped_dedup=[], event_id='evt', session_id={SID!r})\n"
        )
        subprocess.run(
            [sys.executable, "-c", script], check=True, capture_output=True
        )
        log = tmp_path / ".agent" / f".current_injection_history.{SID}.jsonl"
        assert log.exists() and log.stat().st_size > 0
        assert _mode(log) == 0o600


class TestTrainingLog0600:
    def test_log_training_example_creates_0600(self, tmp_path: Path):
        """The training log quotes transcript content and is explicitly on
        a path into model weights — same contract as the transcripts."""
        log = tmp_path / "training.jsonl"
        script = (
            "import sys, os\n"
            f"sys.path.insert(0, {str(SHARED)!r})\n"
            "os.umask(0o002)\n"
            "otc = __import__('on_transcript_change')\n"
            f"otc.TRAINING_LOG = {str(log)!r}\n"
            "otc.log_training_example('r', 'step', 'p', 'r', 'm')\n"
        )
        subprocess.run(
            [sys.executable, "-c", script], check=True, capture_output=True
        )
        assert log.exists() and log.stat().st_size > 0
        assert _mode(log) == 0o600


class TestEveryShellWriterIsWiredToTheOneHome:
    """Parity guard in the style of test_touch_heartbeat: the permission
    contract lives in transcript_perms.sh, and every shell script that
    creates transcript-content files must source it and set the umask.
    A writer added without this wiring recreates INV-todig silently."""

    WRITERS = (
        "sync-transcript.sh",
        "rotate-on-session-end.sh",
        "launch-transcript-sync.sh",
    )

    def test_helper_exists(self):
        assert PERMS_HELPER.is_file(), (
            "transcript_perms.sh is the single home for the transcript "
            "permission contract; it does not exist"
        )

    @pytest.mark.parametrize("writer", WRITERS)
    def test_writer_sources_helper_and_sets_umask(self, writer: str):
        text = (SHARED / writer).read_text()
        assert "transcript_perms.sh" in text, f"{writer} never sources the helper"
        assert "harden_transcript_umask" in text, (
            f"{writer} sources the helper but never sets the umask; new "
            f"files it creates still inherit the process umask"
        )
