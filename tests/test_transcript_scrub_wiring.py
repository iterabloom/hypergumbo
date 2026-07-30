# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end tests for the transcript scrubber's HOOK WIRING.

Why this file exists
--------------------
Before it, no test asserted anything about the wiring at all. Three adversarial
reviewers independently made the same observation: the five existing test files
that drive ``rotate-on-session-end.sh`` / ``launch-transcript-sync.sh`` create no
``.env`` and no secrets file in their temp repos, so ``collect_secrets()`` returns
empty and the scrubber takes its passthrough branch. **Every one of those tests
passes identically whether the scrubber redacts, does nothing, or has a broken
key regex.**

So this module asserts the two halves that actually matter, and they pull in
opposite directions:

1. **A secret placed in a transcript is gone from what rotation retains** --
   from the promoted ``.last_*`` slot AND from the archived ``.gz``.
2. **A broken scrubber loses no data.** This is the half that was wrong in
   production: the previous wiring turned any scrubber failure into a 20-byte
   empty gzip plus a deleted source, at exit 0 with no diagnostics. Four failure
   modes were demonstrated (missing script, lost ``+x``, no ``python3``, path is
   a directory) and a fifth -- dying mid-stream -- produced a gzip-valid,
   plausible, silently TRUNCATED archive.

Property 2 is tested by actively sabotaging the scrubber, because a wiring that
only works when everything works is exactly what shipped and broke.
"""
from __future__ import annotations

import gzip
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SHARED = _REPO / ".agent" / "hooks" / "_shared"

_TOKEN = "gho_WiringTest1234567890abcdefghijklmnop"
_SID = "wiring-test-session"

_SHARED_SCRIPTS = (
    "rotate-on-session-end.sh",
    "launch-transcript-sync.sh",
    "kill-transcript-sync.sh",
    "sync-transcript.sh",
    "poll-transcript-change.sh",
    "filter-transcript.py",
    "session_id_helpers.sh",
    "archive_scrubbed.sh",
    "scrub_secrets.py",
)


def _sandbox(tmp_path: Path, *, with_secret: bool = True) -> Path:
    """A repo-shaped sandbox with the shared hook scripts copied in."""
    repo = tmp_path / "repo"
    shared = repo / ".agent" / "hooks" / "_shared"
    shared.mkdir(parents=True)
    for name in _SHARED_SCRIPTS:
        src = _SHARED / name
        if src.is_file():
            shutil.copy2(src, shared / name)
    if with_secret:
        (repo / ".env").write_text(f"FORGEJO_TOKEN={_TOKEN}\n")
    return repo


def _rotate(repo: Path, sid: str = _SID) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", str(repo / ".agent" / "hooks" / "_shared" / "rotate-on-session-end.sh"),
         str(repo), sid],
        capture_output=True, text=True, cwd=str(repo),
        env={**os.environ, "REPO_ROOT": str(repo)},
    )


def _line(text: str) -> str:
    return f'{{"role":"tool","text":"{text}"}}\n'


# --- property 1: the secret is actually removed ---------------------------


def test_promoted_slot_is_scrubbed(tmp_path: Path) -> None:
    """The token must be gone from .last_session_transcript.jsonl after rotation."""
    repo = _sandbox(tmp_path)
    agent = repo / ".agent"
    (agent / f".current_session_transcript.{_SID}.jsonl").write_text(
        _line("start") + _line(f"https://u:{_TOKEN}@codeberg.org/x.git") + _line("end")
    )
    proc = _rotate(repo)
    assert proc.returncode == 0, proc.stderr
    promoted = (agent / ".last_session_transcript.jsonl").read_text()
    assert _TOKEN not in promoted, "credential survived into the promoted slot"
    assert "REDACTED-SECRET" in promoted
    assert "start" in promoted and "end" in promoted, "content was lost"


def test_archived_gz_is_scrubbed(tmp_path: Path) -> None:
    """The token must be gone from the archived .gz, not just the live slots."""
    repo = _sandbox(tmp_path)
    agent = repo / ".agent"
    (agent / ".second_to_last_transcript.jsonl").write_text(
        _line("old") + _line(f"token {_TOKEN} here")
    )
    (agent / f".current_session_transcript.{_SID}.jsonl").write_text(_line("new"))
    proc = _rotate(repo)
    assert proc.returncode == 0, proc.stderr
    archives = list((agent / ".archived-transcripts").glob("*/transcript.jsonl.gz"))
    assert len(archives) == 1, f"expected one archive, got {archives}"
    body = gzip.decompress(archives[0].read_bytes()).decode()
    assert _TOKEN not in body, "credential survived into the archive"
    assert "old" in body, "archive lost its content"


def test_demoted_slot_is_scrubbed(tmp_path: Path) -> None:
    """.second_to_last_* is scrubbed too — rotation-wide, not archive-only."""
    repo = _sandbox(tmp_path)
    agent = repo / ".agent"
    (agent / ".last_session_transcript.jsonl").write_text(_line(f"x {_TOKEN} y"))
    (agent / f".current_session_transcript.{_SID}.jsonl").write_text(_line("new"))
    assert _rotate(repo).returncode == 0
    assert _TOKEN not in (agent / ".second_to_last_transcript.jsonl").read_text()


# --- property 2: a broken scrubber must not lose data --------------------


@pytest.mark.parametrize(
    "sabotage",
    ["missing", "not_executable", "is_a_directory", "dies_midstream"],
)
def test_broken_scrubber_never_destroys_the_source(
    tmp_path: Path, sabotage: str
) -> None:
    """Every demonstrated failure mode must preserve the transcript.

    The old wiring destroyed it in all four: gzip had already written a valid
    empty stream to the destination before the guard could react, and the source
    was then deleted or clobbered.
    """
    repo = _sandbox(tmp_path)
    agent = repo / ".agent"
    scrubber = agent / "hooks" / "_shared" / "scrub_secrets.py"
    marker = "PRECIOUS-CONTENT-MUST-SURVIVE"
    (agent / ".second_to_last_transcript.jsonl").write_text(
        "".join(_line(f"{marker}-{i}") for i in range(200))
    )
    (agent / f".current_session_transcript.{_SID}.jsonl").write_text(_line("new"))

    if sabotage == "missing":
        scrubber.unlink()
    elif sabotage == "not_executable":
        scrubber.chmod(0o000)
    elif sabotage == "is_a_directory":
        scrubber.unlink()
        scrubber.mkdir()
    elif sabotage == "dies_midstream":
        scrubber.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "sys.stdout.write(open(sys.argv[-1]).read()[:200])\n"
            "sys.exit(1)\n"
        )
        scrubber.chmod(0o755)

    proc = _rotate(repo)
    # A session end must never be aborted by this.
    assert proc.returncode == 0, f"rotation aborted: {proc.stderr}"

    # The content must exist SOMEWHERE: a validated archive, or the rescue file,
    # or still in place. What must never happen is that it exists nowhere.
    found = []
    for p in agent.rglob("*"):
        if not p.is_file():
            continue
        try:
            body = (gzip.decompress(p.read_bytes()) if p.suffix == ".gz"
                    else p.read_bytes())
        except (OSError, EOFError):
            continue
        if marker.encode() in body:
            found.append((p.name, body.count(marker.encode())))
    assert found, (
        f"[{sabotage}] transcript content vanished entirely; stderr:\n{proc.stderr}"
    )
    # And all 200 lines, not a truncated prefix.
    assert max(n for _n, n in found) == 200, (
        f"[{sabotage}] content was TRUNCATED: {found}; stderr:\n{proc.stderr}"
    )


def test_truncated_archive_is_refused_not_published(tmp_path: Path) -> None:
    """A gzip-VALID but truncated archive must be rejected.

    `gzip -t` cannot detect this, which is why archive_scrubbed also compares
    uncompressed line counts. Without that check the archive looks perfect.
    """
    repo = _sandbox(tmp_path)
    agent = repo / ".agent"
    scrubber = agent / "hooks" / "_shared" / "scrub_secrets.py"
    scrubber.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdout.writelines(open(sys.argv[-1]).readlines()[:5])\n"
    )
    scrubber.chmod(0o755)
    (agent / ".second_to_last_transcript.jsonl").write_text(
        "".join(_line(f"line-{i}") for i in range(100))
    )
    (agent / f".current_session_transcript.{_SID}.jsonl").write_text(_line("new"))

    assert _rotate(repo).returncode == 0
    published = list((agent / ".archived-transcripts").glob("*/transcript.jsonl.gz"))
    assert not published, "published a truncated archive as if it were complete"
    rescued = list((agent / ".archived-transcripts").glob("*/*.rescued"))
    assert rescued, "no rescue copy either — the content would be lost"
    assert len(rescued[0].read_text().splitlines()) == 100


def test_no_partial_files_are_left_behind(tmp_path: Path) -> None:
    """A refused archive must not leave its .partial temp in the archive dir."""
    repo = _sandbox(tmp_path)
    agent = repo / ".agent"
    (agent / "hooks" / "_shared" / "scrub_secrets.py").unlink()
    (agent / ".second_to_last_transcript.jsonl").write_text(_line("content"))
    (agent / f".current_session_transcript.{_SID}.jsonl").write_text(_line("new"))
    _rotate(repo)
    assert not list(agent.rglob("*.partial.*")), "left a partial archive behind"


# --- the wiring must not be a silent no-op -------------------------------


def test_scrubber_diagnostics_are_not_suppressed(tmp_path: Path) -> None:
    """Warnings must reach stderr.

    The previous wiring sent the scrubber's stderr to /dev/null while its own
    comment claimed failures were "reported loudly" — so the one line that
    reports a configured secret being skipped was discarded.
    """
    repo = _sandbox(tmp_path)
    agent = repo / ".agent"
    (agent / ".second_to_last_transcript.jsonl").write_text(_line(f"x {_TOKEN}"))
    (agent / f".current_session_transcript.{_SID}.jsonl").write_text(_line("new"))
    proc = _rotate(repo)
    assert "redacting .env values for keys" in proc.stderr, (
        f"scrubber diagnostics were swallowed; stderr was:\n{proc.stderr}"
    )
    assert _TOKEN not in proc.stderr, "a diagnostic leaked the secret itself"


def test_rotation_without_any_secret_config_is_unaffected(tmp_path: Path) -> None:
    """No .env and no secrets file: rotation must behave exactly as before."""
    repo = _sandbox(tmp_path, with_secret=False)
    agent = repo / ".agent"
    body = _line("plain content") + _line("more")
    (agent / f".current_session_transcript.{_SID}.jsonl").write_text(body)
    assert _rotate(repo).returncode == 0
    assert (agent / ".last_session_transcript.jsonl").read_text() == body
