# SPDX-License-Identifier: MPL-2.0
"""Always-on file logging for tracker sync operations.

Writes all sync diagnostic messages to daily log files in
``.agent/.sync-logs/sync-YYYY-MM-DD.log``.  Log files older than 30 days
are garbage-collected on each ``init_sync_log()`` call.

The log directory is dot-prefixed (invisible in normal directory listings)
and gitignored.  Logs are append-only, human-readable, and safe to delete
at any time — they're purely diagnostic.

Garbage collection safety:
- Only deletes files matching the exact ``sync-YYYY-MM-DD.log`` pattern.
- Validates the date portion parses to a real date (rejects malformed names).
- Never recurses into subdirectories.
- Never deletes the directory itself.
- Silently ignores permission errors or race conditions (file already gone).
"""

from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# How long to keep log files before garbage collection.
RETENTION_DAYS = 30

# Exact pattern for log filenames.  Only files matching this are eligible
# for GC — anything else in the directory is left alone.
_LOG_FILENAME_RE = re.compile(r"^sync-(\d{4}-\d{2}-\d{2})\.log$")

# Module-level state: the open log file handle and its path.
_log_file_handle: object | None = None  # typing as object to avoid IO type complexity
_log_dir: Path | None = None


def _parse_log_date(filename: str) -> datetime | None:
    """Extract and validate date from a log filename.

    Returns a timezone-aware UTC datetime for the date, or None if the
    filename doesn't match the expected pattern or the date is invalid
    (e.g. ``sync-2026-02-30.log``).
    """
    m = _LOG_FILENAME_RE.match(filename)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def gc_old_logs(log_dir: Path, now: datetime | None = None) -> list[str]:
    """Delete log files older than RETENTION_DAYS.

    Args:
        log_dir: Directory containing log files.
        now: Current time (injectable for testing).  Defaults to UTC now.

    Returns:
        List of deleted filenames (for diagnostics / testing).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    cutoff = now - timedelta(days=RETENTION_DAYS)
    deleted: list[str] = []

    if not log_dir.is_dir():
        return deleted

    # Only iterate direct children — never recurse.
    for entry in log_dir.iterdir():
        if not entry.is_file():
            continue
        log_date = _parse_log_date(entry.name)
        if log_date is None:
            # Not a recognized log file — leave it alone.
            continue
        if log_date < cutoff:
            try:
                entry.unlink()
                deleted.append(entry.name)
            except OSError:
                # Race condition (file already gone) or permission error.
                # Either way, best-effort — don't crash the sync workflow.
                pass

    return sorted(deleted)


def init_sync_log(repo_root: Path) -> Path | None:
    """Initialize the sync log for this session.

    Creates the log directory if needed, runs garbage collection, and opens
    today's log file in append mode.  Returns the log directory path, or
    None if initialization fails (e.g. read-only filesystem).

    Safe to call multiple times — subsequent calls are no-ops if the log
    file for today is already open.
    """
    global _log_file_handle, _log_dir
    log_dir = repo_root / ".agent" / ".sync-logs"
    _log_dir = log_dir

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    # Run GC before opening today's file.
    gc_old_logs(log_dir)

    today = time.strftime("%Y-%m-%d")
    log_path = log_dir / f"sync-{today}.log"

    # If we already have a handle open for this path, keep it.
    if (
        _log_file_handle is not None
        and hasattr(_log_file_handle, "name")
        and getattr(_log_file_handle, "name", None) == str(log_path)
        and not getattr(_log_file_handle, "closed", True)
    ):
        return log_dir

    # Close previous handle if open.
    _close_log()

    try:
        _log_file_handle = open(log_path, "a", encoding="utf-8")
    except OSError:
        _log_file_handle = None
        return None

    return log_dir


def write_log(msg: str) -> None:
    """Write a timestamped message to the sync log file.

    Also writes to stderr (preserving the original ``_log()`` behavior).
    If no log file is open, only writes to stderr.
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] sync: {msg}"

    # Always write to stderr (original behavior).
    print(f"sync: {msg}", file=sys.stderr)

    # Write to log file if available.
    if _log_file_handle is not None and not getattr(
        _log_file_handle, "closed", True
    ):
        try:
            _log_file_handle.write(formatted + "\n")  # type: ignore[union-attr]
            _log_file_handle.flush()  # type: ignore[union-attr]
        except OSError:
            pass  # Best-effort — don't crash sync because of log I/O.


def _close_log() -> None:
    """Close the current log file handle if open."""
    global _log_file_handle
    if _log_file_handle is not None:
        try:
            _log_file_handle.close()  # type: ignore[union-attr]
        except OSError:
            pass
        _log_file_handle = None


def get_log_dir() -> Path | None:
    """Return the current log directory path, or None if not initialized."""
    return _log_dir
