# SPDX-License-Identifier: MPL-2.0
"""Tests for hypergumbo_tracker.tag_catalog and the `tracker tags` subcommand.

Covers the catalog module (status derivation, IO round-trip, op-log
backfill, maintenance hooks) and the CLI surface (enumerate / --count /
--json / rename / describe / deprecate, plus the deprecation warning
hook in `tracker add --tag <deprecated>`).

The CLI tests run via the public ``main`` entry point so they exercise
parser wiring, dispatch, and the `_MUTATION_COMMANDS` gating in one shot.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

from hypergumbo_tracker import tag_catalog
from hypergumbo_tracker.cli import EXIT_SUCCESS, EXIT_USER_ERROR, main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_tracker(tmp_path: Path) -> Path:
    """Create the tracker dir layout the CLI expects."""
    tracker_root = tmp_path / ".agent"
    (tracker_root / "tracker" / ".ops").mkdir(parents=True)
    (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
    (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)
    return tracker_root


def _write_item_with_tags(
    ops_dir: Path,
    item_id: str,
    tags: list[str],
    *,
    at: str = "2026-01-01T00:00:00Z",
) -> None:
    """Write a minimal create-op file for an item with the given tags."""
    tags_yaml = (
        "[" + ", ".join(repr(t) for t in tags) + "]" if tags else "[]"
    )
    content = textwrap.dedent(f"""\
        - op: create
          at: "{at}"
          by: agent
          actor: test_agent
          clock: 1
          nonce: a1b2
          data:
            kind: work_item
            title: "Item {item_id}"
            status: todo_hard
            priority: 2
            tags: {tags_yaml}
    """)
    (ops_dir / f".{item_id}.ops").write_text(content)


def _append_update_op(
    op_path: Path,
    *,
    add_tags: list[str] | None = None,
    remove_tags: list[str] | None = None,
    set_tags: list[str] | None = None,
    at: str = "2026-02-01T00:00:00Z",
    clock: int = 2,
) -> None:
    """Append an update op that touches the tags list to an existing ops file."""
    parts: list[str] = [
        "",
        "- op: update",
        f'  at: "{at}"',
        "  by: agent",
        "  actor: test_agent",
        f"  clock: {clock}",
        "  nonce: c1d2",
    ]
    set_block = ""
    if set_tags is not None:
        tags_yaml = (
            "[" + ", ".join(repr(t) for t in set_tags) + "]"
            if set_tags else "[]"
        )
        set_block = f"\n  set:\n    tags: {tags_yaml}"
    else:
        parts.append("  set: {}")
    if add_tags:
        tags_yaml = "[" + ", ".join(repr(t) for t in add_tags) + "]"
        parts.append(f"  add:\n    tags: {tags_yaml}")
    if remove_tags:
        tags_yaml = "[" + ", ".join(repr(t) for t in remove_tags) + "]"
        parts.append(f"  remove:\n    tags: {tags_yaml}")
    text = "\n".join(parts)
    if set_block:
        # Replace the empty "set: {}" line we may have written above.
        text = text.replace("  set: {}", "  set:") + set_block
    with open(op_path, "a") as f:
        f.write(text + "\n")


# ---------------------------------------------------------------------------
# tag_status three-state model
# ---------------------------------------------------------------------------


class TestTagStatus:
    def test_active_when_count_positive(self) -> None:
        assert tag_catalog.tag_status(5, False) == "active"

    def test_inactive_when_count_zero_and_not_deprecated(self) -> None:
        assert tag_catalog.tag_status(0, False) == "inactive"

    def test_deprecated_wins_over_count(self) -> None:
        assert tag_catalog.tag_status(0, True) == "deprecated"
        assert tag_catalog.tag_status(5, True) == "deprecated"


# ---------------------------------------------------------------------------
# Catalog file IO
# ---------------------------------------------------------------------------


class TestCatalogIO:
    def test_load_missing_file_returns_empty(self, tmp_path: Path) -> None:
        catalog = tag_catalog.load_catalog(tmp_path / "nonexistent.yaml")
        assert catalog == {}

    def test_load_empty_file_returns_empty(self, tmp_path: Path) -> None:
        cat_file = tmp_path / "tag_catalog.yaml"
        cat_file.write_text("")
        assert tag_catalog.load_catalog(cat_file) == {}

    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        cat_file = tmp_path / "tag_catalog.yaml"
        original = {
            "developer_experience": tag_catalog.TagCatalogEntry(
                description="DX improvements.",
                created_on="2026-01-01T00:00:00Z",
                last_modified="2026-01-02T00:00:00Z",
                last_used="2026-01-03T00:00:00Z",
            ),
            "dx": tag_catalog.TagCatalogEntry(
                deprecated=True,
                in_favor_of="developer_experience",
                created_on="2026-01-01T00:00:00Z",
            ),
        }
        tag_catalog.save_catalog(cat_file, original)
        loaded = tag_catalog.load_catalog(cat_file)
        assert loaded["developer_experience"].description == "DX improvements."
        assert loaded["dx"].deprecated is True
        assert loaded["dx"].in_favor_of == "developer_experience"

    def test_save_drops_in_favor_of_when_not_deprecated(self, tmp_path: Path) -> None:
        cat_file = tmp_path / "tag_catalog.yaml"
        catalog = {
            "foo": tag_catalog.TagCatalogEntry(
                deprecated=False,
                in_favor_of="bar",
            ),
        }
        tag_catalog.save_catalog(cat_file, catalog)
        with open(cat_file) as f:
            raw = yaml.safe_load(f)
        # The catalog format always serializes the key (with null), but
        # callers should not see in_favor_of round-trip when not
        # deprecated.
        assert raw["tags"]["foo"]["in_favor_of"] is None

    def test_save_rejects_invalid_tag_name(self, tmp_path: Path) -> None:
        cat_file = tmp_path / "tag_catalog.yaml"
        catalog = {"BAD-NAME": tag_catalog.TagCatalogEntry()}
        with pytest.raises(ValueError, match="does not match"):
            tag_catalog.save_catalog(cat_file, catalog)

    def test_save_rejects_multiline_description(self, tmp_path: Path) -> None:
        cat_file = tmp_path / "tag_catalog.yaml"
        catalog = {"x": tag_catalog.TagCatalogEntry(description="line1\nline2")}
        with pytest.raises(ValueError, match="single-line"):
            tag_catalog.save_catalog(cat_file, catalog)

    def test_save_rejects_invalid_in_favor_of(self, tmp_path: Path) -> None:
        cat_file = tmp_path / "tag_catalog.yaml"
        catalog = {
            "x": tag_catalog.TagCatalogEntry(
                deprecated=True, in_favor_of="BAD-NAME",
            ),
        }
        with pytest.raises(ValueError, match="does not match"):
            tag_catalog.save_catalog(cat_file, catalog)

    def test_load_rejects_invalid_yaml(self, tmp_path: Path) -> None:
        cat_file = tmp_path / "tag_catalog.yaml"
        cat_file.write_text(": not valid yaml: at all: ::\n")
        with pytest.raises(ValueError, match="invalid YAML"):
            tag_catalog.load_catalog(cat_file)

    def test_load_rejects_non_mapping_top_level(self, tmp_path: Path) -> None:
        cat_file = tmp_path / "tag_catalog.yaml"
        cat_file.write_text("- not a mapping\n")
        with pytest.raises(ValueError, match="expected mapping"):
            tag_catalog.load_catalog(cat_file)

    def test_load_rejects_non_mapping_tags(self, tmp_path: Path) -> None:
        cat_file = tmp_path / "tag_catalog.yaml"
        cat_file.write_text("tags:\n  - not_a_mapping\n")
        with pytest.raises(ValueError, match="'tags' to be a mapping"):
            tag_catalog.load_catalog(cat_file)

    def test_load_rejects_invalid_tag_name(self, tmp_path: Path) -> None:
        cat_file = tmp_path / "tag_catalog.yaml"
        cat_file.write_text("tags:\n  BAD-NAME:\n    description: x\n")
        with pytest.raises(ValueError, match="does not match"):
            tag_catalog.load_catalog(cat_file)

    def test_load_rejects_non_mapping_entry(self, tmp_path: Path) -> None:
        cat_file = tmp_path / "tag_catalog.yaml"
        cat_file.write_text("tags:\n  foo: not_a_mapping\n")
        with pytest.raises(ValueError, match="must be a mapping"):
            tag_catalog.load_catalog(cat_file)

    def test_catalog_path_under_tracker_dir(self, tmp_path: Path) -> None:
        path = tag_catalog.catalog_path(tmp_path)
        assert path == tmp_path / "tracker" / "tag_catalog.yaml"

    def test_catalog_exists_distinguishes_missing(self, tmp_path: Path) -> None:
        assert tag_catalog.catalog_exists(tmp_path) is False
        (tmp_path / "tracker").mkdir()
        (tmp_path / "tracker" / "tag_catalog.yaml").write_text("")
        assert tag_catalog.catalog_exists(tmp_path) is True


# ---------------------------------------------------------------------------
# count_tags
# ---------------------------------------------------------------------------


class _Stub:
    def __init__(self, tags: list[str]) -> None:
        self.tags = tags


class TestCountTags:
    def test_empty(self) -> None:
        assert tag_catalog.count_tags([]) == {}

    def test_basic(self) -> None:
        items = [_Stub(["a", "b"]), _Stub(["b", "c"]), _Stub(["a"])]
        assert tag_catalog.count_tags(items) == {"a": 2, "b": 2, "c": 1}

    def test_handles_no_tags_attribute(self) -> None:
        class NoTags:
            pass
        assert tag_catalog.count_tags([NoTags()]) == {}


# ---------------------------------------------------------------------------
# Op-log backfill
# ---------------------------------------------------------------------------


class TestBackfill:
    def test_empty_dirs(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        assert tag_catalog.backfill_from_op_log(tracker_root) == {}

    def test_extracts_create_op_tags(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _write_item_with_tags(
            tracker_root / "tracker" / ".ops", "WI-aa",
            ["x", "y"], at="2026-01-01T10:00:00Z",
        )
        _write_item_with_tags(
            tracker_root / "tracker-workspace" / ".ops", "WI-bb",
            ["y", "z"], at="2026-02-01T10:00:00Z",
        )
        result = tag_catalog.backfill_from_op_log(tracker_root)
        assert result["x"]["created_on"] == "2026-01-01T10:00:00Z"
        assert result["y"]["created_on"] == "2026-01-01T10:00:00Z"
        assert result["y"]["last_used"] == "2026-02-01T10:00:00Z"
        assert result["z"]["created_on"] == "2026-02-01T10:00:00Z"

    def test_extracts_update_add_remove_tags(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _write_item_with_tags(
            tracker_root / "tracker" / ".ops", "WI-aa", [],
            at="2026-01-01T10:00:00Z",
        )
        op_path = tracker_root / "tracker" / ".ops" / ".WI-aa.ops"
        _append_update_op(
            op_path, add_tags=["new_tag"],
            at="2026-01-15T10:00:00Z", clock=2,
        )
        _append_update_op(
            op_path, remove_tags=["new_tag"],
            at="2026-01-20T10:00:00Z", clock=3,
        )
        result = tag_catalog.backfill_from_op_log(tracker_root)
        # Created: first appearance via add. Last_used: latest of either
        # add or remove.
        assert result["new_tag"]["created_on"] == "2026-01-15T10:00:00Z"
        assert result["new_tag"]["last_used"] == "2026-01-20T10:00:00Z"

    def test_extracts_update_set_tags(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _write_item_with_tags(
            tracker_root / "tracker" / ".ops", "WI-aa", [],
            at="2026-01-01T10:00:00Z",
        )
        op_path = tracker_root / "tracker" / ".ops" / ".WI-aa.ops"
        _append_update_op(
            op_path, set_tags=["wholesale"],
            at="2026-03-01T10:00:00Z", clock=2,
        )
        result = tag_catalog.backfill_from_op_log(tracker_root)
        assert result["wholesale"]["created_on"] == "2026-03-01T10:00:00Z"


# ---------------------------------------------------------------------------
# ensure_catalog
# ---------------------------------------------------------------------------


class TestEnsureCatalog:
    def test_creates_catalog_on_first_call(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _write_item_with_tags(
            tracker_root / "tracker" / ".ops", "WI-aa", ["foo"],
            at="2026-01-01T10:00:00Z",
        )
        assert tag_catalog.catalog_exists(tracker_root) is False
        catalog = tag_catalog.ensure_catalog(tracker_root)
        assert "foo" in catalog
        assert catalog["foo"].created_on == "2026-01-01T10:00:00Z"
        assert tag_catalog.catalog_exists(tracker_root) is True

    def test_subsequent_call_skips_backfill(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _write_item_with_tags(
            tracker_root / "tracker" / ".ops", "WI-aa", ["foo"],
        )
        tag_catalog.ensure_catalog(tracker_root)
        # Now add another item — the second ensure_catalog call should
        # NOT pick it up because the catalog file is already present.
        _write_item_with_tags(
            tracker_root / "tracker" / ".ops", "WI-bb", ["bar"],
        )
        catalog = tag_catalog.ensure_catalog(tracker_root)
        assert "bar" not in catalog

    def test_force_backfill_merges_without_clobbering(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        # Pre-existing catalog with editorial state.
        cat_file = tag_catalog.catalog_path(tracker_root)
        cat_file.parent.mkdir(parents=True, exist_ok=True)
        catalog = {
            "foo": tag_catalog.TagCatalogEntry(
                description="hand-written",
                deprecated=True,
                in_favor_of="bar",
            ),
        }
        tag_catalog.save_catalog(cat_file, catalog)
        _write_item_with_tags(
            tracker_root / "tracker" / ".ops", "WI-aa", ["foo"],
            at="2026-01-01T10:00:00Z",
        )
        result = tag_catalog.ensure_catalog(tracker_root, force_backfill=True)
        # Editorial state preserved.
        assert result["foo"].description == "hand-written"
        assert result["foo"].deprecated is True
        # Op-log data merged in.
        assert result["foo"].created_on == "2026-01-01T10:00:00Z"


# ---------------------------------------------------------------------------
# Maintenance hooks
# ---------------------------------------------------------------------------


class TestMaintenanceHooks:
    def test_touch_tags_creates_entry_on_first_sight(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        tag_catalog.touch_tags(tracker_root, ["new_tag"], when="2026-04-27T12:00:00Z")
        catalog = tag_catalog.load_catalog(tag_catalog.catalog_path(tracker_root))
        assert catalog["new_tag"].created_on == "2026-04-27T12:00:00Z"
        assert catalog["new_tag"].last_used == "2026-04-27T12:00:00Z"

    def test_touch_tags_advances_last_used(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        tag_catalog.touch_tags(tracker_root, ["x"], when="2026-01-01T00:00:00Z")
        tag_catalog.touch_tags(tracker_root, ["x"], when="2026-04-27T12:00:00Z")
        catalog = tag_catalog.load_catalog(tag_catalog.catalog_path(tracker_root))
        assert catalog["x"].created_on == "2026-01-01T00:00:00Z"
        assert catalog["x"].last_used == "2026-04-27T12:00:00Z"

    def test_touch_tags_never_advances_created_on(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        cat_file = tag_catalog.catalog_path(tracker_root)
        cat_file.parent.mkdir(parents=True, exist_ok=True)
        tag_catalog.save_catalog(cat_file, {
            "x": tag_catalog.TagCatalogEntry(created_on="2026-01-01T00:00:00Z"),
        })
        tag_catalog.touch_tags(tracker_root, ["x"], when="2026-04-27T12:00:00Z")
        catalog = tag_catalog.load_catalog(cat_file)
        assert catalog["x"].created_on == "2026-01-01T00:00:00Z"

    def test_touch_tags_no_op_for_empty(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        tag_catalog.touch_tags(tracker_root, [])
        assert not tag_catalog.catalog_exists(tracker_root)

    def test_bump_last_modified(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        tag_catalog.bump_last_modified(
            tracker_root, ["x"], when="2026-04-27T12:00:00Z",
        )
        catalog = tag_catalog.load_catalog(tag_catalog.catalog_path(tracker_root))
        assert catalog["x"].last_modified == "2026-04-27T12:00:00Z"

    def test_bump_last_modified_no_op_for_empty(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        tag_catalog.bump_last_modified(tracker_root, [])
        assert not tag_catalog.catalog_exists(tracker_root)

    def test_get_entry_returns_none_for_unknown(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        assert tag_catalog.get_entry(tracker_root, "missing") is None

    def test_get_entry_returns_known(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        tag_catalog.touch_tags(tracker_root, ["foo"], when="2026-04-27T12:00:00Z")
        entry = tag_catalog.get_entry(tracker_root, "foo")
        assert entry is not None
        assert entry.last_used == "2026-04-27T12:00:00Z"

    def test_upsert_entry_writes(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        tag_catalog.upsert_entry(
            tracker_root, "x",
            tag_catalog.TagCatalogEntry(description="hello"),
        )
        catalog = tag_catalog.load_catalog(tag_catalog.catalog_path(tracker_root))
        assert catalog["x"].description == "hello"

    def test_upsert_entry_rejects_invalid_name(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(ValueError, match="does not match"):
            tag_catalog.upsert_entry(
                tracker_root, "BAD-NAME", tag_catalog.TagCatalogEntry(),
            )


# ---------------------------------------------------------------------------
# now_utc
# ---------------------------------------------------------------------------


class TestNowUTC:
    def test_returns_rfc3339_z(self) -> None:
        ts = tag_catalog.now_utc()
        # Cheap shape check: no timezone offset suffix, just Z.
        assert ts.endswith("Z")
        assert len(ts) == 20  # YYYY-MM-DDTHH:MM:SSZ
        assert ts[10] == "T"


# ---------------------------------------------------------------------------
# _ts_min / _ts_max edge cases
# ---------------------------------------------------------------------------


class TestTimestampHelpers:
    def test_ts_min_b_none_returns_a(self) -> None:
        assert tag_catalog._ts_min("2026-01-01T00:00:00Z", None) == "2026-01-01T00:00:00Z"

    def test_ts_min_a_none_returns_b(self) -> None:
        assert tag_catalog._ts_min(None, "2026-01-01T00:00:00Z") == "2026-01-01T00:00:00Z"

    def test_ts_max_b_none_returns_a(self) -> None:
        assert tag_catalog._ts_max("2026-01-01T00:00:00Z", None) == "2026-01-01T00:00:00Z"

    def test_ts_max_a_none_returns_b(self) -> None:
        assert tag_catalog._ts_max(None, "2026-01-01T00:00:00Z") == "2026-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# Backfill / walk-ops edge cases
# ---------------------------------------------------------------------------


class TestBackfillEdgeCases:
    def test_walk_skips_missing_ops_dirs(self, tmp_path: Path) -> None:
        # No directories created at all.
        result = tag_catalog._walk_op_files(tmp_path)
        assert result == []

    def test_backfill_skips_op_without_at(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        # Hand-craft an ops file with one op missing `at`.
        op_path = tracker_root / "tracker" / ".ops" / ".WI-x.ops"
        op_path.write_text(textwrap.dedent("""\
            - op: create
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: x
                status: todo_hard
                priority: 2
                tags: [foo]
            - op: create
              at: "2026-01-01T10:00:00Z"
              by: agent
              actor: test_agent
              clock: 2
              nonce: c3d4
              data:
                kind: work_item
                title: y
                status: todo_hard
                priority: 2
                tags: [bar]
        """))
        result = tag_catalog.backfill_from_op_log(tracker_root)
        # `foo` came from the op without `at` — should be skipped.
        assert "foo" not in result
        assert "bar" in result


# ---------------------------------------------------------------------------
# save_catalog atomic-write failure cleanup
# ---------------------------------------------------------------------------


class TestSaveCatalogFailureCleanup:
    def test_unlinks_tmp_on_rename_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cat_file = tmp_path / "tag_catalog.yaml"
        catalog = {"x": tag_catalog.TagCatalogEntry()}

        real_rename = __import__("os").rename

        def boom(src: str, dst: str) -> None:
            # Trigger a failure path so the except branch unlinks the
            # tmpfile rather than leaving it behind.
            raise OSError("simulated rename failure")

        monkeypatch.setattr("os.rename", boom)
        with pytest.raises(OSError, match="simulated"):
            tag_catalog.save_catalog(cat_file, catalog)
        # tmp file from the failed write must not remain in the dir.
        leftovers = [
            p for p in tmp_path.iterdir()
            if p.name.startswith(".tag_catalog_")
        ]
        assert leftovers == [], f"leftover tmp files: {leftovers}"
        # Sanity: real os.rename still works after the test exits.
        _ = real_rename


# ---------------------------------------------------------------------------
# touch_tags: existing entry with None created_on
# ---------------------------------------------------------------------------


class TestTouchTagsCreatedOnFill:
    def test_fills_in_created_on_when_none(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        # Pre-seed an entry with description but NO timestamps — possible
        # if `tags describe` was the very first action on a tag, before
        # any item carried it.
        cat_file = tag_catalog.catalog_path(tracker_root)
        cat_file.parent.mkdir(parents=True, exist_ok=True)
        tag_catalog.save_catalog(cat_file, {
            "x": tag_catalog.TagCatalogEntry(description="pre-seeded"),
        })
        tag_catalog.touch_tags(
            tracker_root, ["x"], when="2026-04-27T12:00:00Z",
        )
        catalog = tag_catalog.load_catalog(cat_file)
        assert catalog["x"].created_on == "2026-04-27T12:00:00Z"
        assert catalog["x"].last_used == "2026-04-27T12:00:00Z"
        # Description preserved.
        assert catalog["x"].description == "pre-seeded"


# ---------------------------------------------------------------------------
# CLI: warn-deprecated-tags edge cases
# ---------------------------------------------------------------------------


class TestWarnDeprecatedTagsEdgeCases:
    def test_no_op_for_empty_tag_list(self, tmp_path: Path) -> None:
        # Direct call with empty list — early-return should not even
        # touch the catalog file.
        from hypergumbo_tracker.cli import _warn_deprecated_tags
        tracker_root = _setup_tracker(tmp_path)
        _warn_deprecated_tags(tracker_root, [])
        assert not tag_catalog.catalog_exists(tracker_root)


# ---------------------------------------------------------------------------
# CLI cache-dir resolution edge case
# ---------------------------------------------------------------------------


class TestGetCacheDirNoGit:
    def test_returns_none_when_no_git_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Covers the 'no .git ancestor' branch in `_get_cache_dir`.

        When the tracker root has no git ancestor, the cache-dir resolver
        returns None and the CLI runs without sqlite acceleration. The
        production stack-trace uncovered without this test is harmless
        (no caches are wired), but per-package coverage CI will flag it.
        """
        from hypergumbo_tracker import cli as cli_mod

        # Force the lookup to think there's no git by patching the
        # symbol the inline import resolves at call time.
        from hypergumbo_tracker import store as store_mod
        monkeypatch.setattr(store_mod, "_find_git_dir", lambda _: None)

        tracker_root = _setup_tracker(tmp_path)
        result = cli_mod._get_cache_dir(tracker_root)
        assert result is None


# ---------------------------------------------------------------------------
# CLI: enumerate (default + --count + --json)
# ---------------------------------------------------------------------------


class TestTagsEnumerate:
    def test_empty_corpus(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--no-auto-sync", "tags"])
        assert exc.value.code == EXIT_SUCCESS
        assert capsys.readouterr().out == ""

    def test_basic_enumeration_alphabetical(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        ops = tracker_root / "tracker" / ".ops"
        _write_item_with_tags(ops, "WI-aa", ["a", "b"])
        _write_item_with_tags(ops, "WI-bb", ["b", "c"])
        _write_item_with_tags(ops, "WI-cc", ["a"])
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--no-auto-sync", "tags"])
        assert exc.value.code == EXIT_SUCCESS
        assert capsys.readouterr().out == "a\nb\nc\n"

    def test_count_form(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        ops = tracker_root / "tracker" / ".ops"
        _write_item_with_tags(ops, "WI-aa", ["a", "b"])
        _write_item_with_tags(ops, "WI-bb", ["b", "c"])
        _write_item_with_tags(ops, "WI-cc", ["a"])
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "--count",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out.splitlines()
        # Sorted by count desc then alpha → a (2), b (2), c (1).
        assert out == ["a\t2\tactive", "b\t2\tactive", "c\t1\tactive"]

    def test_json_form_includes_metadata(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        ops = tracker_root / "tracker" / ".ops"
        _write_item_with_tags(ops, "WI-aa", ["foo"], at="2026-01-01T10:00:00Z")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "--json", "tags",
            ])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert data["foo"]["count"] == 1
        assert data["foo"]["status"] == "active"
        assert data["foo"]["created_on"] == "2026-01-01T10:00:00Z"
        assert data["foo"]["deprecated"] is False
        assert data["foo"]["in_favor_of"] is None


# ---------------------------------------------------------------------------
# CLI: rename
# ---------------------------------------------------------------------------


class TestTagsRename:
    def test_renames_across_items(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        ops = tracker_root / "tracker" / ".ops"
        _write_item_with_tags(ops, "WI-aa", ["dx"])
        _write_item_with_tags(ops, "WI-bb", ["dx", "other"])
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "rename", "dx", "developer_experience",
            ])
        assert exc.value.code == EXIT_SUCCESS
        # Re-enumerate to verify the actual state.
        capsys.readouterr()
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "--count",
            ])
        out_lines = capsys.readouterr().out.splitlines()
        out_text = "\n".join(out_lines)
        assert "developer_experience\t2\tactive" in out_text
        # `dx` is now in catalog with count==0 → status inactive.
        assert "dx\t0\tinactive" in out_text

    def test_rename_idempotent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        ops = tracker_root / "tracker" / ".ops"
        _write_item_with_tags(ops, "WI-aa", ["dx"])
        # Run twice.
        for _ in range(2):
            with pytest.raises(SystemExit) as exc:
                main([
                    "--tracker-root", str(tracker_root), "--no-auto-sync",
                    "tags", "rename", "dx", "developer_experience",
                ])
            assert exc.value.code == EXIT_SUCCESS
            capsys.readouterr()
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "--count",
            ])
        out = capsys.readouterr().out
        # No double-counted developer_experience entries.
        assert "developer_experience\t1\tactive" in out

    def test_rename_dedupes_when_both_present(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        ops = tracker_root / "tracker" / ".ops"
        _write_item_with_tags(ops, "WI-aa", ["dx", "developer_experience"])
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "rename", "dx", "developer_experience",
            ])
        capsys.readouterr()
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "--count",
            ])
        out = capsys.readouterr().out
        assert "developer_experience\t1\tactive" in out

    def test_rename_invalid_new_name(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "rename", "old", "BAD-NAME",
            ])
        assert exc.value.code == EXIT_USER_ERROR
        assert "does not match" in capsys.readouterr().err

    def test_rename_same_name_is_noop(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "rename", "x", "x",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_rename_json_output(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        ops = tracker_root / "tracker" / ".ops"
        _write_item_with_tags(ops, "WI-aa", ["dx"])
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "--json", "tags", "rename", "dx", "developer_experience",
            ])
        data = json.loads(capsys.readouterr().out)
        assert data["renamed"] == 1
        assert data["new"] == "developer_experience"


# ---------------------------------------------------------------------------
# CLI: describe
# ---------------------------------------------------------------------------


class TestTagsDescribe:
    def test_round_trip(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        # Set the description.
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "describe", "foo", "purpose statement",
            ])
        assert exc.value.code == EXIT_SUCCESS
        capsys.readouterr()
        # Read it back.
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "describe", "foo",
            ])
        assert exc.value.code == EXIT_SUCCESS
        assert capsys.readouterr().out.strip() == "purpose statement"

    def test_describe_unknown_returns_empty(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "describe", "missing",
            ])
        assert exc.value.code == EXIT_SUCCESS
        assert capsys.readouterr().out.strip() == ""

    def test_describe_unknown_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "--json", "tags", "describe", "missing",
            ])
        data = json.loads(capsys.readouterr().out)
        assert data == {"tag": "missing", "description": ""}

    def test_describe_known_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "describe", "foo", "hi",
            ])
        capsys.readouterr()
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "--json", "tags", "describe", "foo",
            ])
        data = json.loads(capsys.readouterr().out)
        assert data == {"tag": "foo", "description": "hi"}

    def test_describe_set_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "--json", "tags", "describe", "foo", "hi there",
            ])
        data = json.loads(capsys.readouterr().out)
        assert data == {"tag": "foo", "description": "hi there"}

    def test_describe_rejects_multiline(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "describe", "foo", "line1\nline2",
            ])
        assert exc.value.code == EXIT_USER_ERROR
        assert "single-line" in capsys.readouterr().err

    def test_describe_rejects_invalid_name(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "describe", "BAD-NAME", "x",
            ])
        assert exc.value.code == EXIT_USER_ERROR
        assert "does not match" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# CLI: deprecate + warning hook on add
# ---------------------------------------------------------------------------


class TestTagsDeprecate:
    def test_deprecate_flips_status(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "deprecate", "foo", "--in-favor-of", "bar",
            ])
        assert exc.value.code == EXIT_SUCCESS
        capsys.readouterr()
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "--json", "tags",
            ])
        data = json.loads(capsys.readouterr().out)
        assert data["foo"]["deprecated"] is True
        assert data["foo"]["in_favor_of"] == "bar"
        assert data["foo"]["status"] == "deprecated"

    def test_deprecate_without_in_favor_of(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "deprecate", "foo",
            ])
        capsys.readouterr()
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "--json", "tags",
            ])
        data = json.loads(capsys.readouterr().out)
        assert data["foo"]["deprecated"] is True
        assert data["foo"]["in_favor_of"] is None

    def test_deprecate_json_output(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "--json", "tags", "deprecate", "foo", "--in-favor-of", "bar",
            ])
        data = json.loads(capsys.readouterr().out)
        assert data == {"tag": "foo", "deprecated": True, "in_favor_of": "bar"}

    def test_deprecate_rejects_invalid_name(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "deprecate", "BAD-NAME",
            ])
        assert exc.value.code == EXIT_USER_ERROR
        assert "does not match" in capsys.readouterr().err

    def test_deprecate_rejects_invalid_in_favor_of(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "deprecate", "foo", "--in-favor-of", "BAD",
            ])
        assert exc.value.code == EXIT_USER_ERROR
        assert "does not match" in capsys.readouterr().err

    def test_add_with_deprecated_tag_warns(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        # Deprecate first.
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "deprecate", "dx", "--in-favor-of", "developer_experience",
            ])
        capsys.readouterr()
        # Now add an item using the deprecated tag.
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "add", "--kind", "work_item",
                "--title", "test", "--tag", "dx",
            ])
        # Add still succeeds — warning is non-blocking.
        assert exc.value.code == EXIT_SUCCESS
        captured = capsys.readouterr()
        assert "deprecated" in captured.err
        assert "developer_experience" in captured.err

    def test_add_with_non_deprecated_tag_does_not_warn(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "add", "--kind", "work_item",
                "--title", "test", "--tag", "fresh_tag",
            ])
        captured = capsys.readouterr()
        assert "deprecated" not in captured.err

    def test_update_add_tag_with_deprecated_warns(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        # Add a baseline item.
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "add", "--kind", "work_item",
                "--title", "test",
            ])
        out = capsys.readouterr().out.strip()
        item_id = out.split()[-1]  # last token is the ID
        # Deprecate a tag.
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "tags", "deprecate", "dx",
            ])
        capsys.readouterr()
        # Now apply the deprecated tag via update.
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "update", item_id, "--add-tag", "dx",
            ])
        captured = capsys.readouterr()
        assert "deprecated" in captured.err


# ---------------------------------------------------------------------------
# Catalog maintenance side effects (touch_tags from add / update)
# ---------------------------------------------------------------------------


class TestCLICatalogMaintenance:
    def test_add_records_last_used(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "add", "--kind", "work_item",
                "--title", "t", "--tag", "fresh",
            ])
        capsys.readouterr()
        catalog = tag_catalog.load_catalog(tag_catalog.catalog_path(tracker_root))
        assert "fresh" in catalog
        assert catalog["fresh"].last_used is not None

    def test_update_add_remove_tag_records_last_used(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "add", "--kind", "work_item", "--title", "t",
            ])
        out = capsys.readouterr().out.strip()
        item_id = out.split()[-1]
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "update", item_id, "--add-tag", "post_add",
            ])
        capsys.readouterr()
        catalog = tag_catalog.load_catalog(tag_catalog.catalog_path(tracker_root))
        assert "post_add" in catalog
        assert catalog["post_add"].last_used is not None
