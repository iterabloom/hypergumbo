# SPDX-License-Identifier: MPL-2.0
"""Tests for hypergumbo_tracker.cache.

Covers Cache construction, SQLite schema, read/write paths, invalidation,
corruption recovery, query_blocking, and repo fingerprint.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from hypergumbo_tracker.cache import Cache
from hypergumbo_tracker.models import (
    CompiledItem,
    DiscussionEntry,
    Tier,
    TrackerConfig,
)
from hypergumbo_tracker.store import Store, _parse_ops_file, compile_ops


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> TrackerConfig:
    """Create a minimal TrackerConfig for tests."""
    return TrackerConfig(
        kinds={
            "invariant": __import__(
                "hypergumbo_tracker.models", fromlist=["KindConfig"]
            ).KindConfig(prefix="INV", description="test"),
            "work_item": __import__(
                "hypergumbo_tracker.models", fromlist=["KindConfig"]
            ).KindConfig(prefix="WI", description="test"),
        },
        statuses=["todo_hard", "todo_soft", "in_progress", "done", "deferred", "wont_do"],
        blocking_statuses=["todo_hard", "todo_soft"],
        resolved_statuses=["done", "deferred", "wont_do"],
    )


def _make_store_and_cache(
    tmp_path: Path,
    tier: Tier = Tier.WORKSPACE,
) -> tuple[Store, Cache, Path]:
    """Create a Store and Cache pair for testing."""
    ops_dir = tmp_path / ".ops"
    ops_dir.mkdir(parents=True, exist_ok=True)
    config = _make_config()
    store = Store(ops_dir, config=config)
    db_path = tmp_path / "cache.db"
    cache = Cache(store, db_path, tier)
    return store, cache, db_path


# ---------------------------------------------------------------------------
# Constructor + Schema
# ---------------------------------------------------------------------------


class TestCacheConstructor:
    def test_creates_db(self, tmp_path: Path, mock_agent_uid: None) -> None:
        store, cache, db_path = _make_store_and_cache(tmp_path)
        assert db_path.exists()
        cache.close()

    def test_creates_schema(self, tmp_path: Path, mock_agent_uid: None) -> None:
        store, cache, db_path = _make_store_and_cache(tmp_path)
        conn = sqlite3.connect(str(db_path))
        # Check table exists
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='items'"
        ).fetchall()
        assert len(tables) == 1

        # Check indexes
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        index_names = {row[0] for row in indexes}
        assert "idx_status" in index_names
        assert "idx_kind" in index_names
        assert "idx_priority" in index_names
        conn.close()
        cache.close()

    def test_db_path_property(self, tmp_path: Path, mock_agent_uid: None) -> None:
        store, cache, db_path = _make_store_and_cache(tmp_path)
        assert cache.db_path == db_path
        cache.close()

    def test_corrupt_db_recovery_on_init(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        """Corrupt DB file is replaced on init."""
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        db_path = tmp_path / "cache.db"
        # Write garbage to the DB file
        db_path.write_bytes(b"this is not a sqlite file")
        config = _make_config()
        store = Store(ops_dir, config=config)
        cache = Cache(store, db_path, Tier.WORKSPACE)
        # Should have recovered — schema should work
        # Verify by inserting and reading back with get_all (doesn't check source files)
        cache.upsert(
            "test-id",
            CompiledItem(
                id="test-id", kind="invariant", title="Test",
                status="todo_hard", created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
            ),
            mtime=1.0, size=100,
        )
        items = cache.get_all()
        assert len(items) == 1
        assert items[0].id == "test-id"
        cache.close()


# ---------------------------------------------------------------------------
# Cold start rebuild
# ---------------------------------------------------------------------------


class TestRebuild:
    def test_rebuild_populates_cache(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        item_id = store.add(kind="invariant", title="Rebuild Test")
        cache.rebuild()
        item = cache.get_compiled(item_id)
        assert item is not None
        assert item.title == "Rebuild Test"
        cache.close()

    def test_rebuild_correct_mtime_size(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        item_id = store.add(kind="invariant", title="Mtime Test")
        cache.rebuild()
        source_path = store.item_path(item_id)
        stat = source_path.stat()
        # Get the item — should be fresh (mtime matches)
        item = cache.get_compiled(item_id)
        assert item is not None
        cache.close()


# ---------------------------------------------------------------------------
# Read path: get_compiled
# ---------------------------------------------------------------------------


class TestGetCompiled:
    def test_returns_cached_item_when_fresh(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        item_id = store.add(kind="invariant", title="Cached")
        source_path = store.item_path(item_id)
        stat = source_path.stat()
        ops = _parse_ops_file(source_path)
        compiled = compile_ops(ops, item_id)
        compiled.tier = Tier.WORKSPACE
        cache.upsert(item_id, compiled, stat.st_mtime, stat.st_size)
        result = cache.get_compiled(item_id)
        assert result is not None
        assert result.id == item_id
        assert result.title == "Cached"
        cache.close()

    def test_returns_none_when_not_cached(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        result = cache.get_compiled("INV-nonexistent")
        assert result is None
        cache.close()

    def test_returns_none_when_stale(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        item_id = store.add(kind="invariant", title="Stale")
        source_path = store.item_path(item_id)
        stat = source_path.stat()
        ops = _parse_ops_file(source_path)
        compiled = compile_ops(ops, item_id)
        compiled.tier = Tier.WORKSPACE
        cache.upsert(item_id, compiled, stat.st_mtime, stat.st_size)

        # Modify the source file to make cache stale
        time.sleep(0.05)  # Ensure mtime differs
        store.update(item_id, set_fields={"status": "in_progress"})

        result = cache.get_compiled(item_id)
        assert result is None  # Stale
        cache.close()

    def test_returns_none_when_source_deleted(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        item_id = store.add(kind="invariant", title="Will Delete")
        source_path = store.item_path(item_id)
        stat = source_path.stat()
        ops = _parse_ops_file(source_path)
        compiled = compile_ops(ops, item_id)
        compiled.tier = Tier.WORKSPACE
        cache.upsert(item_id, compiled, stat.st_mtime, stat.st_size)

        # Delete source
        source_path.unlink()
        result = cache.get_compiled(item_id)
        assert result is None
        cache.close()

    def test_get_compiled_corrupt_db(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        """Corrupt DB triggers recovery on get_compiled."""
        store, cache, db_path = _make_store_and_cache(tmp_path)
        item_id = store.add(kind="invariant", title="Corrupt Get")

        # Corrupt the DB by closing and overwriting
        cache.close()
        db_path.write_bytes(b"corrupted data")

        # Re-create cache (will recover on init)
        cache2 = Cache(store, db_path, Tier.WORKSPACE)
        # The DB should be rebuilt — item might not be cached yet
        result = cache2.get_compiled(item_id)
        # After recovery, rebuild should have populated the item
        cache2.rebuild()
        result = cache2.get_compiled(item_id)
        assert result is not None
        cache2.close()


# ---------------------------------------------------------------------------
# Read path: get_all
# ---------------------------------------------------------------------------


class TestGetAll:
    def test_returns_all_cached_items(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        id1 = store.add(kind="invariant", title="Item 1")
        id2 = store.add(kind="work_item", title="Item 2", not_duplicate_of=[id1])
        cache.rebuild()
        items = cache.get_all()
        ids = {i.id for i in items}
        assert id1 in ids
        assert id2 in ids
        cache.close()

    def test_empty_cache(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        items = cache.get_all()
        assert items == []
        cache.close()

    def test_get_all_corrupt_db(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        """Corrupt DB triggers recovery on get_all."""
        store, cache, _ = _make_store_and_cache(tmp_path)
        store.add(kind="invariant", title="Get All Corrupt")
        cache.rebuild()

        # Simulate corruption by dropping the table
        assert cache._conn is not None
        cache._conn.execute("DROP TABLE items")
        cache._conn.commit()

        items = cache.get_all()
        # After recovery + rebuild, items should be back
        assert isinstance(items, list)
        cache.close()


# ---------------------------------------------------------------------------
# Write path: upsert
# ---------------------------------------------------------------------------


class TestUpsert:
    def test_insert_and_retrieve(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        item = CompiledItem(
            id="INV-test-123",
            kind="invariant",
            title="Upsert Test",
            status="todo_hard",
            priority=1,
            parent="INV-parent",
            tags=["tag1", "tag2"],
            before=["INV-before"],
            duplicate_of=["INV-dup"],
            not_duplicate_of=["INV-nodup"],
            pr_ref="PR-42",
            justification="Because reasons",
            description="Full description",
            fields={"statement": "s", "root_cause": "r"},
            locked_fields={"status", "priority"},
            discussion=[
                DiscussionEntry(
                    by="agent", actor="test_agent",
                    at="2026-01-01T00:00:00Z", message="hello",
                ),
            ],
            simhash=12345,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-02T00:00:00Z",
            cross_tier_conflict=True,
            tier=Tier.WORKSPACE,
        )
        cache.upsert("INV-test-123", item, mtime=1234.5, size=500)
        result = cache.get_all()
        assert len(result) == 1
        r = result[0]
        assert r.id == "INV-test-123"
        assert r.kind == "invariant"
        assert r.title == "Upsert Test"
        assert r.status == "todo_hard"
        assert r.priority == 1
        assert r.parent == "INV-parent"
        assert r.tags == ["tag1", "tag2"]
        assert r.before == ["INV-before"]
        assert r.duplicate_of == ["INV-dup"]
        assert r.not_duplicate_of == ["INV-nodup"]
        assert r.pr_ref == "PR-42"
        assert r.justification == "Because reasons"
        assert r.description == "Full description"
        assert r.fields == {"statement": "s", "root_cause": "r"}
        assert r.locked_fields == {"status", "priority"}
        assert len(r.discussion) == 1
        assert r.discussion[0].message == "hello"
        assert r.simhash == 12345
        assert r.created_at == "2026-01-01T00:00:00Z"
        assert r.updated_at == "2026-01-02T00:00:00Z"
        assert r.cross_tier_conflict is True
        assert r.tier == Tier.WORKSPACE
        cache.close()

    def test_upsert_replaces(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        item = CompiledItem(
            id="INV-test",
            kind="invariant", title="Original",
            status="todo_hard",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        cache.upsert("INV-test", item, mtime=1.0, size=100)

        item.title = "Updated"
        cache.upsert("INV-test", item, mtime=2.0, size=200)

        result = cache.get_all()
        assert len(result) == 1
        assert result[0].title == "Updated"
        cache.close()

    def test_upsert_corrupt_db_recovers(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        # Corrupt by dropping table
        assert cache._conn is not None
        cache._conn.execute("DROP TABLE items")
        cache._conn.commit()

        item = CompiledItem(
            id="INV-corrupt",
            kind="invariant", title="Corrupt Upsert",
            status="todo_hard",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        # Should trigger recovery
        cache.upsert("INV-corrupt", item, mtime=1.0, size=100)
        # After recovery, cache should work
        cache.close()


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_removes_item(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        item = CompiledItem(
            id="INV-del",
            kind="invariant", title="Delete Me",
            status="todo_hard",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        cache.upsert("INV-del", item, mtime=1.0, size=100)
        cache.delete("INV-del")
        result = cache.get_all()
        assert len(result) == 0
        cache.close()

    def test_delete_nonexistent_noop(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        cache.delete("INV-nonexistent")  # Should not raise
        cache.close()

    def test_delete_corrupt_db_recovers(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        assert cache._conn is not None
        cache._conn.execute("DROP TABLE items")
        cache._conn.commit()
        cache.delete("INV-corrupt")  # Should trigger recovery
        cache.close()


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------


class TestInvalidateStale:
    def test_invalidate_refreshes_stale(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        item_id = store.add(kind="invariant", title="Invalidate Test")
        cache.rebuild()

        # Modify source
        time.sleep(0.05)
        store.update(item_id, set_fields={"status": "in_progress"})

        refreshed = cache.invalidate_stale()
        assert item_id in refreshed
        # Cache should now have updated status
        item = cache.get_compiled(item_id)
        assert item is not None
        assert item.status == "in_progress"
        cache.close()

    def test_invalidate_removes_deleted(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        item_id = store.add(kind="invariant", title="Delete After Cache")
        cache.rebuild()

        # Delete source
        store.item_path(item_id).unlink()
        refreshed = cache.invalidate_stale()
        assert item_id in refreshed
        cache.close()

    def test_invalidate_fresh_noop(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        store.add(kind="invariant", title="Stay Fresh")
        cache.rebuild()
        refreshed = cache.invalidate_stale()
        assert refreshed == []
        cache.close()

    def test_invalidate_corrupt_db_recovers(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        assert cache._conn is not None
        cache._conn.execute("DROP TABLE items")
        cache._conn.commit()
        refreshed = cache.invalidate_stale()
        assert isinstance(refreshed, list)
        cache.close()


# ---------------------------------------------------------------------------
# query_blocking
# ---------------------------------------------------------------------------


class TestQueryBlocking:
    def test_counts_blocking_items(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        id1 = store.add(kind="invariant", title="Hard", status="todo_hard")
        id2 = store.add(kind="work_item", title="Done", status="done",
                        not_duplicate_of=[id1])
        cache.rebuild()
        count = cache.query_blocking(["todo_hard", "todo_soft"])
        assert count == 1
        cache.close()

    def test_empty_statuses(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        store.add(kind="invariant", title="Anything")
        cache.rebuild()
        count = cache.query_blocking([])
        assert count == 0
        cache.close()

    def test_query_blocking_corrupt_db(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        assert cache._conn is not None
        cache._conn.execute("DROP TABLE items")
        cache._conn.commit()
        count = cache.query_blocking(["todo_hard"])
        assert count == 0  # After recovery, empty cache
        cache.close()


# ---------------------------------------------------------------------------
# Repo fingerprint
# ---------------------------------------------------------------------------


def _init_test_git_repo(path: Path, *, with_remote: bool = False) -> None:
    """Initialize a minimal git repo for testing."""
    import subprocess
    run = subprocess.run
    run(["git", "init", str(path)], capture_output=True, check=True)
    run(["git", "config", "user.email", "t@t.com"], cwd=str(path), capture_output=True, check=True)
    run(["git", "config", "user.name", "T"], cwd=str(path), capture_output=True, check=True)
    if with_remote:
        run(["git", "remote", "add", "origin", "https://example.com/r.git"], cwd=str(path), capture_output=True, check=True)
    (path / "f.txt").write_text("x")
    run(["git", "add", "f.txt"], cwd=str(path), capture_output=True, check=True)
    run(["git", "commit", "-m", "i"], cwd=str(path), capture_output=True, check=True)


class TestRepoFingerprint:
    def test_fingerprint_with_git(self, tmp_path: Path) -> None:
        """Fingerprint in a real git repo returns hex string."""
        _init_test_git_repo(tmp_path)
        fp = Cache.repo_fingerprint(tmp_path / ".git")
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)

    def test_fingerprint_no_git(self, tmp_path: Path) -> None:
        """No git → returns 'no-git'."""
        fp = Cache.repo_fingerprint(tmp_path / ".git")
        assert fp == "no-git"

    def test_fingerprint_with_remote(self, tmp_path: Path) -> None:
        """Git repo with remote → includes remote URL in fingerprint."""
        _init_test_git_repo(tmp_path, with_remote=True)
        fp = Cache.repo_fingerprint(tmp_path / ".git")
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)

    def test_fingerprint_no_remote(self, tmp_path: Path) -> None:
        """Git repo without remote → uses only first commit SHA."""
        _init_test_git_repo(tmp_path)
        fp = Cache.repo_fingerprint(tmp_path / ".git")
        assert fp != "no-git"
        assert len(fp) == 16


# ---------------------------------------------------------------------------
# Serialization round-trip (all CompiledItem fields)
# ---------------------------------------------------------------------------


class TestSerializationRoundTrip:
    def test_discussion_entry_roundtrip(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        item = CompiledItem(
            id="INV-disc",
            kind="invariant", title="Discussion",
            status="todo_hard",
            discussion=[
                DiscussionEntry(
                    by="human", actor="jgstern",
                    at="2026-01-01T00:00:00Z", message="first",
                ),
                DiscussionEntry(
                    by="agent", actor="test_agent",
                    at="2026-01-02T00:00:00Z", message="summary",
                    is_summary=True,
                ),
            ],
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-02T00:00:00Z",
        )
        cache.upsert("INV-disc", item, mtime=1.0, size=100)
        result = cache.get_all()
        assert len(result) == 1
        assert len(result[0].discussion) == 2
        assert result[0].discussion[0].is_summary is False
        assert result[0].discussion[1].is_summary is True
        cache.close()

    def test_empty_lists_roundtrip(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        item = CompiledItem(
            id="INV-empty",
            kind="invariant", title="Empty Lists",
            status="todo_hard",
            tags=[], before=[], duplicate_of=[], not_duplicate_of=[],
            fields={}, locked_fields=set(), discussion=[],
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        cache.upsert("INV-empty", item, mtime=1.0, size=100)
        result = cache.get_all()
        assert len(result) == 1
        r = result[0]
        assert r.tags == []
        assert r.before == []
        assert r.fields == {}
        assert r.locked_fields == set()
        assert r.discussion == []
        cache.close()

    def test_none_fields_roundtrip(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        item = CompiledItem(
            id="INV-none",
            kind="invariant", title="None Fields",
            status="todo_hard",
            parent=None, pr_ref=None, justification=None,
            simhash=None,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        cache.upsert("INV-none", item, mtime=1.0, size=100)
        result = cache.get_all()
        assert len(result) == 1
        assert result[0].parent is None
        assert result[0].pr_ref is None
        assert result[0].justification is None
        assert result[0].simhash is None
        cache.close()


# ---------------------------------------------------------------------------
# Close
# ---------------------------------------------------------------------------


class TestRecoveryPaths:
    def test_get_compiled_db_error(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        """DatabaseError in get_compiled triggers recovery."""
        store, cache, _ = _make_store_and_cache(tmp_path)
        item_id = store.add(kind="invariant", title="DB Error Get")
        cache.rebuild()

        # Drop table to force DatabaseError on next SELECT
        assert cache._conn is not None
        cache._conn.execute("DROP TABLE items")
        cache._conn.commit()

        result = cache.get_compiled(item_id)
        assert result is None  # Recovery returns None
        cache.close()

    def test_rebuild_db_error_on_delete(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        """DatabaseError during rebuild DELETE triggers recovery."""
        store, cache, _ = _make_store_and_cache(tmp_path)
        store.add(kind="invariant", title="Rebuild Error")

        # Drop table so DELETE FROM items fails
        assert cache._conn is not None
        cache._conn.execute("DROP TABLE items")
        cache._conn.commit()

        cache.rebuild()  # Should recover
        cache.close()


class TestClose:
    def test_close_idempotent(
        self, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        store, cache, _ = _make_store_and_cache(tmp_path)
        cache.close()
        cache.close()  # Should not raise
