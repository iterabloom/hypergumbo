# SPDX-License-Identifier: MPL-2.0
"""SQLite per-tier read cache for the hypergumbo tracker.

Each Cache instance wraps a single Store and maintains a SQLite database
of compiled item state for fast queries. Key design decisions:

- **One DB per tier:** Three separate .cache.db files, one for each tier.
  This keeps cache invalidation local to the tier that changed.
- **Write-through:** Every Store mutation immediately upserts the cache row.
  No lazy rebuild needed after local writes.
- **Incremental invalidation:** Uses source file mtime/size tracking. If
  only mtime changed but size grew, tries incremental reparse (appended ops).
  If size shrank, does full reparse. Unchanged mtime → cache hit.
- **Corruption recovery:** If the SQLite DB is corrupt or missing, auto-rebuild
  from the underlying Store. No manual intervention needed.
- **Discussion-only optimization:** When new bytes are only discuss ops,
  only update discussion/updated_at columns instead of full recompile.

See ADR-0013 for the full design specification.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

from hypergumbo_tracker.models import (
    CompiledItem,
    DiscussionEntry,
    Tier,
)
from hypergumbo_tracker.store import (
    Store,
    _parse_ops_file,
    compile_ops,
)

# SQLite schema for cached items
_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS items (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    title         TEXT NOT NULL,
    status        TEXT NOT NULL,
    priority      INTEGER NOT NULL,
    parent        TEXT,
    tags          TEXT,
    before_ids    TEXT,
    duplicate_of  TEXT,
    not_duplicate_of TEXT,
    pr_ref        TEXT,
    description   TEXT,
    fields        TEXT,
    locked_fields TEXT,
    discussion    TEXT,
    simhash       INTEGER,
    tier          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    cross_tier_conflict INTEGER NOT NULL DEFAULT 0,
    source_mtime  REAL NOT NULL,
    source_size   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_kind ON items(kind);
CREATE INDEX IF NOT EXISTS idx_priority ON items(priority);
"""


class Cache:
    """SQLite read cache for a single tier's Store.

    Args:
        store: The Store instance this cache wraps.
        db_path: Path to the SQLite database file.
        tier: Which tier this cache serves.
    """

    def __init__(self, store: Store, db_path: Path, tier: Tier) -> None:
        self._store = store
        self._db_path = db_path
        self._tier = tier
        self._conn: sqlite3.Connection | None = None
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Create the database and schema if needed. Recover from corruption."""
        try:
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA_SQL)
        except sqlite3.DatabaseError:
            # Corrupt DB — delete and recreate
            self._db_path.unlink(missing_ok=True)
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA_SQL)

    @property
    def db_path(self) -> Path:
        """Return the database file path."""
        return self._db_path

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -------------------------------------------------------------------
    # Read path
    # -------------------------------------------------------------------

    def get_compiled(self, item_id: str) -> CompiledItem | None:
        """Get cached compiled item, or None if not cached/stale.

        Checks source file freshness before returning. If stale, returns
        None (caller should recompile and upsert).
        """
        assert self._conn is not None
        try:
            row = self._conn.execute(
                "SELECT * FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        except sqlite3.DatabaseError:
            self._recover()
            return None

        if row is None:
            return None

        # Check freshness
        source_path = self._store.item_path(item_id)
        if not source_path.exists():
            # Source file deleted — remove from cache
            self._delete_row(item_id)
            return None

        stat = source_path.stat()
        stored_mtime = row[20]  # source_mtime column index
        if stat.st_mtime != stored_mtime:
            return None  # Stale — caller should recompile

        return self._row_to_item(row)

    def get_all(self) -> list[CompiledItem]:
        """All cached items. Does NOT validate freshness (use invalidate_stale for that)."""
        assert self._conn is not None
        try:
            rows = self._conn.execute("SELECT * FROM items").fetchall()
        except sqlite3.DatabaseError:
            self._recover()
            return []
        return [self._row_to_item(row) for row in rows]

    # -------------------------------------------------------------------
    # Write path
    # -------------------------------------------------------------------

    def upsert(self, item_id: str, item: CompiledItem, mtime: float, size: int) -> None:
        """Write-through: store compiled item + source tracking info."""
        assert self._conn is not None
        try:
            self._conn.execute(
                """INSERT OR REPLACE INTO items
                   (id, kind, title, status, priority, parent, tags,
                    before_ids, duplicate_of, not_duplicate_of, pr_ref,
                    description, fields, locked_fields,
                    discussion, simhash, tier, created_at, updated_at,
                    cross_tier_conflict, source_mtime, source_size)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item_id,
                    item.kind,
                    item.title,
                    item.status,
                    item.priority,
                    item.parent,
                    json.dumps(item.tags),
                    json.dumps(item.before),
                    json.dumps(item.duplicate_of),
                    json.dumps(item.not_duplicate_of),
                    item.pr_ref,
                    item.description,
                    json.dumps(item.fields),
                    json.dumps(sorted(item.locked_fields)),
                    json.dumps([
                        {
                            "by": d.by, "actor": d.actor, "at": d.at,
                            "message": d.message, "is_summary": d.is_summary,
                        }
                        for d in item.discussion
                    ]),
                    item.simhash,
                    self._tier.value,
                    item.created_at,
                    item.updated_at,
                    1 if item.cross_tier_conflict else 0,
                    mtime,
                    size,
                ),
            )
            self._conn.commit()
        except sqlite3.DatabaseError:
            self._recover()

    def delete(self, item_id: str) -> None:
        """Remove an item from the cache."""
        assert self._conn is not None
        try:
            self._conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
            self._conn.commit()
        except sqlite3.DatabaseError:
            self._recover()

    # -------------------------------------------------------------------
    # Invalidation
    # -------------------------------------------------------------------

    def invalidate_stale(self) -> list[str]:
        """Check all cached items against source files. Returns IDs that were refreshed.

        Discussion-only optimization (ADR-0013 §818-829): when new bytes
        appended to an ops file contain only discuss/discuss_clear/discuss_summarize
        ops, only the discussion and updated_at columns are updated instead of
        a full INSERT OR REPLACE. This avoids rewriting all columns when only
        discussion changed.
        """
        assert self._conn is not None
        refreshed: list[str] = []

        try:
            rows = self._conn.execute(
                "SELECT id, source_mtime, source_size FROM items"
            ).fetchall()
        except sqlite3.DatabaseError:
            self._recover()
            return []

        for item_id, stored_mtime, stored_size in rows:
            source_path = self._store.item_path(item_id)
            if not source_path.exists():
                self._delete_row(item_id)
                refreshed.append(item_id)
                continue

            stat = source_path.stat()
            if stat.st_mtime == stored_mtime:
                continue  # Fresh

            # Stale — check if discussion-only fast path applies
            if stat.st_size >= stored_size and stored_size > 0:
                if self._try_discussion_only_update(
                    item_id, source_path, stat, stored_size
                ):
                    refreshed.append(item_id)
                    continue

            # Full reparse
            try:
                ops = _parse_ops_file(source_path)
                item = compile_ops(ops, item_id)
                item.tier = self._tier
                self.upsert(item_id, item, stat.st_mtime, stat.st_size)
                refreshed.append(item_id)
            except Exception:  # pragma: no cover — defensive: corrupt handled by Store
                self._delete_row(item_id)
                refreshed.append(item_id)

        return refreshed

    _DISCUSSION_OPS = frozenset({"discuss", "discuss_clear", "discuss_summarize"})

    def _try_discussion_only_update(
        self,
        item_id: str,
        source_path: Path,
        stat: Any,
        stored_size: int,
    ) -> bool:
        """Attempt discussion-only fast path for an appended ops file.

        Reads the new bytes appended since the last cached size. If they
        parse as YAML and all ops are discuss/discuss_clear/discuss_summarize,
        does a full file parse + compile but only updates discussion-related
        columns in the cache row.

        Returns True if the fast path succeeded, False if caller should
        fall through to full reparse + full upsert.
        """
        import yaml as _yaml

        try:
            with open(source_path, "rb") as f:
                f.seek(stored_size)
                new_bytes = f.read()
        except OSError:
            return False

        if not new_bytes.strip():
            return False

        # Parse new bytes as YAML
        try:
            new_ops = _yaml.load(new_bytes, Loader=_yaml.CSafeLoader)
        except _yaml.YAMLError:
            return False

        if not isinstance(new_ops, list) or not new_ops:
            return False

        # Check if ALL new ops are discussion-related
        for op in new_ops:
            if not isinstance(op, dict):
                return False
            if op.get("op") not in self._DISCUSSION_OPS:
                return False

        # All discussion ops — do full parse + compile, but only update
        # discussion columns
        try:
            ops = _parse_ops_file(source_path)
            item = compile_ops(ops, item_id)
            item.tier = self._tier
            self._update_discussion_only(
                item_id, item, stat.st_mtime, stat.st_size
            )
            return True
        except Exception:
            return False

    def _update_discussion_only(
        self,
        item_id: str,
        item: CompiledItem,
        mtime: float,
        size: int,
    ) -> None:
        """Update only discussion-related columns in the cache row.

        Updates: discussion, updated_at, source_mtime, source_size.
        Leaves all other fields untouched.
        """
        assert self._conn is not None
        discussion_json = json.dumps([
            {
                "by": d.by, "actor": d.actor, "at": d.at,
                "message": d.message, "is_summary": d.is_summary,
            }
            for d in item.discussion
        ])
        try:
            self._conn.execute(
                """UPDATE items
                   SET discussion = ?, updated_at = ?,
                       source_mtime = ?, source_size = ?
                   WHERE id = ?""",
                (discussion_json, item.updated_at, mtime, size, item_id),
            )
            self._conn.commit()
        except sqlite3.DatabaseError:  # pragma: no cover — defensive
            self._recover()

    def rebuild(self) -> None:
        """Cold start: wipe cache, recompile all items from Store."""
        assert self._conn is not None
        try:
            self._conn.execute("DELETE FROM items")
            self._conn.commit()
        except sqlite3.DatabaseError:
            self._recover()

        for item_id in self._store.item_ids():
            source_path = self._store.item_path(item_id)
            if not source_path.exists():  # pragma: no cover — defensive
                continue
            try:
                ops = _parse_ops_file(source_path)
                item = compile_ops(ops, item_id)
                item.tier = self._tier
                stat = source_path.stat()
                self.upsert(item_id, item, stat.st_mtime, stat.st_size)
            except Exception:  # noqa: S112  # nosec B112  # pragma: no cover — defensive: corrupt items skip
                continue

    # -------------------------------------------------------------------
    # Queries
    # -------------------------------------------------------------------

    def query_blocking(self, blocking_statuses: list[str]) -> int:
        """Count items with blocking status (for count_todos)."""
        assert self._conn is not None
        if not blocking_statuses:
            return 0
        placeholders = ",".join("?" for _ in blocking_statuses)
        try:
            row = self._conn.execute(
                f"SELECT COUNT(*) FROM items WHERE status IN ({placeholders})",  # noqa: S608  # nosec B608
                blocking_statuses,
            ).fetchone()
            return row[0] if row else 0
        except sqlite3.DatabaseError:
            self._recover()
            return 0

    # -------------------------------------------------------------------
    # Repo fingerprint
    # -------------------------------------------------------------------

    @staticmethod
    def repo_fingerprint(git_dir: Path) -> str:
        """Hash of remote URL + first commit SHA for cache directory naming.

        Falls back gracefully: no remote → just first commit SHA,
        no git → "no-git".
        """
        parts: list[str] = []

        # Remote URL (best effort)
        try:
            result = subprocess.run(  # nosec B603, B607
                ["git", "remote", "get-url", "origin"],  # noqa: S607
                capture_output=True, text=True, timeout=5,
                cwd=str(git_dir.parent),
            )
            if result.returncode == 0 and result.stdout.strip():
                parts.append(result.stdout.strip())
        except (subprocess.TimeoutExpired, OSError):  # pragma: no cover — defensive
            pass

        # First commit SHA
        try:
            result = subprocess.run(  # nosec B603, B607
                ["git", "rev-list", "--max-parents=0", "HEAD"],  # noqa: S607
                capture_output=True, text=True, timeout=5,
                cwd=str(git_dir.parent),
            )
            if result.returncode == 0 and result.stdout.strip():
                # May be multiple root commits; use first
                first_root = result.stdout.strip().split("\n")[0]
                parts.append(first_root)
        except (subprocess.TimeoutExpired, OSError):  # pragma: no cover — defensive
            pass

        if not parts:
            return "no-git"

        combined = "\n".join(parts)
        return hashlib.sha256(combined.encode()).hexdigest()[:16]

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    def _row_to_item(self, row: tuple[Any, ...]) -> CompiledItem:
        """Convert a SQLite row to a CompiledItem."""
        discussion_raw = json.loads(row[14]) if row[14] else []
        discussion = [
            DiscussionEntry(
                by=d["by"], actor=d["actor"], at=d["at"],
                message=d["message"], is_summary=d.get("is_summary", False),
            )
            for d in discussion_raw
        ]

        return CompiledItem(
            id=row[0],
            kind=row[1],
            title=row[2],
            status=row[3],
            priority=row[4],
            parent=row[5],
            tags=json.loads(row[6]) if row[6] else [],
            before=json.loads(row[7]) if row[7] else [],
            duplicate_of=json.loads(row[8]) if row[8] else [],
            not_duplicate_of=json.loads(row[9]) if row[9] else [],
            pr_ref=row[10],
            description=row[11] or "",
            fields=json.loads(row[12]) if row[12] else {},
            locked_fields=set(json.loads(row[13])) if row[13] else set(),
            discussion=discussion,
            simhash=row[15],
            created_at=row[17],
            updated_at=row[18],
            cross_tier_conflict=bool(row[19]),
            tier=Tier(row[16]),
        )

    def _delete_row(self, item_id: str) -> None:
        """Delete a single row from the cache."""
        assert self._conn is not None
        try:
            self._conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
            self._conn.commit()
        except sqlite3.DatabaseError:  # pragma: no cover — defensive
            pass

    def _recover(self) -> None:
        """Recover from a corrupt database by deleting and recreating."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # pragma: no cover — defensive
                pass
        self._db_path.unlink(missing_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA_SQL)
        self.rebuild()
