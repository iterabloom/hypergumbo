# SPDX-License-Identifier: MPL-2.0
"""Forensic log for transient I/O races on tracker ``.ops`` files.

Tracker readers (TUI periodic refresh, CLI compile paths) can race
concurrent writers that replace an ``.ops`` file via atomic rename —
``stat()`` sees one inode, ``open()`` lands on another whose
permissions briefly differ, and the reader gets ``EACCES`` or
``ENOENT``. ``store._parse_ops_file`` retries a bounded number of
times to absorb the transient window.

Every retry (and every final re-raise) appends one JSONL record here.
Racing is expected to be rare, so the file grows slowly; no rotation.
The point is forensic: a future incident gives us timestamps, stat
snapshots, and process identity to correlate against writer logs.

Two-function interface to keep the dependency out of ``store.py``:

    configure_race_log(path)
        Set the log destination. Called once at ``TrackerSet`` init.
        Passing ``None`` disables logging (e.g. in tests).
    log_read_race(filepath, attempt, max_attempts, exc, *, final)
        Append one JSONL record. Swallows every error — the logger
        must never crash the caller.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

_log_path: Path | None = None


def configure_race_log(path: Path | None) -> None:
    """Install ``path`` as the race-log destination (or disable with ``None``)."""
    global _log_path
    _log_path = path


def get_race_log_path() -> Path | None:
    """Return the currently configured race-log path, or ``None`` if disabled."""
    return _log_path


def _write_record(record: dict) -> None:
    """Append one JSONL record. Swallows every error. Callers must
    gate on ``_log_path is None`` themselves — by the time we get here
    the path is expected to be configured."""
    try:
        _log_path.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        with open(_log_path, "a", encoding="utf-8") as f:  # type: ignore[arg-type]
            f.write(json.dumps(record) + "\n")
    except Exception:
        # Logger must never crash the caller.
        pass


def _snapshot_stat(filepath: Path) -> dict | None:
    try:
        st = os.stat(filepath)
        return {
            "st_mode": st.st_mode,
            "st_uid": st.st_uid,
            "st_gid": st.st_gid,
            "st_size": st.st_size,
            "st_mtime": st.st_mtime,
        }
    except OSError:
        return None


def log_read_race(
    filepath: Path,
    attempt: int,
    max_attempts: int,
    exc: BaseException,
    *,
    final: bool,
) -> None:
    """Append a JSONL record describing one transient-read event.

    Never raises. Logs the current stat of ``filepath`` alongside
    the exception so later audits can reconstruct the race window.
    """
    if _log_path is None:
        return
    _write_record({
        "event": "read_retry",
        "ts": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "euid": os.geteuid(),
        "path": str(filepath),
        "attempt": attempt,
        "max_attempts": max_attempts,
        "final": final,
        "errno": getattr(exc, "errno", None),
        "error": f"{type(exc).__name__}: {exc}",
        "stat": _snapshot_stat(filepath),
    })


def log_compile_suppression(filepath: Path, exc: BaseException) -> None:
    """Append a JSONL record when ``_compile_all`` / ``_compile_all_cached``
    catches an ``OSError`` and skips the file to keep the rest of the tier
    compiling.

    Records that are also logged by ``log_read_race`` (transient
    ``PermissionError`` / ``FileNotFoundError`` from ``_parse_ops_file``)
    will appear as both — the duplicate is intentional: it confirms the
    end-to-end path from first retry to final suppression. Other
    ``OSError`` subclasses that bypass the retry loop (e.g. raised by
    ``compile_ops`` or ``is_frozen``) are only visible here.
    """
    if _log_path is None:
        return
    _write_record({
        "event": "compile_suppressed",
        "ts": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "euid": os.geteuid(),
        "path": str(filepath),
        "errno": getattr(exc, "errno", None),
        "error": f"{type(exc).__name__}: {exc}",
        "stat": _snapshot_stat(filepath),
    })


def compute_default_race_log_path(tracker_root: Path) -> Path:
    """Return the default per-tracker-root log path under XDG cache.

    ``~/.cache/hypergumbo/tracker/<12-char sha256 of abs tracker_root>/race_log.jsonl``.
    Honors ``XDG_CACHE_HOME`` when set.
    """
    import hashlib

    cache_home = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    fingerprint = hashlib.sha256(
        str(tracker_root.resolve()).encode("utf-8"),
    ).hexdigest()[:12]
    return cache_home / "hypergumbo" / "tracker" / fingerprint / "race_log.jsonl"
