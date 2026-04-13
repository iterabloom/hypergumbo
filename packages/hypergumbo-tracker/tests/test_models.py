# SPDX-License-Identifier: MPL-2.0
"""Tests for hypergumbo_tracker.models.

Covers Op dataclass construction, frozen immutability, Tier enum,
config loading chain, config validation, actor resolution, FieldSchema,
KindConfig, CompiledItem, and dict_to_op conversion.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_tracker.models import (
    HUMAN_AUTHORITY_OPS,
    OP_TYPE_MAP,
    CompiledItem,
    ConfigValidationError,
    CreateOp,
    DemoteOp,
    DiscussClearOp,
    DiscussionEntry,
    DiscussOp,
    DiscussSummarizeOp,
    FieldSchema,
    KindConfig,
    LockOp,
    PromoteOp,
    ReconcileOp,
    StealthOp,
    Tier,
    TrackerConfig,
    UnlockOp,
    UnstealthOp,
    UpdateOp,
    _parse_config_dict,
    dict_to_op,
    load_config,
    resolve_actor,
)


# ---------------------------------------------------------------------------
# Tier enum
# ---------------------------------------------------------------------------


class TestTier:
    def test_canonical_value(self) -> None:
        assert Tier.CANONICAL.value == "canonical"

    def test_workspace_value(self) -> None:
        assert Tier.WORKSPACE.value == "workspace"

    def test_stealth_value(self) -> None:
        assert Tier.STEALTH.value == "stealth"

    def test_all_tiers(self) -> None:
        assert len(Tier) == 3


# ---------------------------------------------------------------------------
# Op dataclass construction and immutability
# ---------------------------------------------------------------------------


COMMON_FIELDS: dict[str, Any] = {
    "at": "2026-02-11T18:00:00Z",
    "by": "agent",
    "actor": "test_agent",
    "clock": 1,
    "nonce": "f7a2",
}


class TestCreateOp:
    def test_construction(self) -> None:
        op = CreateOp(op="create", **COMMON_FIELDS, data={"kind": "invariant", "title": "test"})
        assert op.op == "create"
        assert op.data["kind"] == "invariant"

    def test_wrong_op_type(self) -> None:
        with pytest.raises(ValueError, match="must be 'create'"):
            CreateOp(op="update", **COMMON_FIELDS, data={})

    def test_frozen(self) -> None:
        op = CreateOp(op="create", **COMMON_FIELDS, data={"kind": "invariant"})
        with pytest.raises(AttributeError):
            op.op = "update"  # type: ignore[misc]


class TestUpdateOp:
    def test_construction_with_set_only(self) -> None:
        op = UpdateOp(op="update", **COMMON_FIELDS, set={"status": "done"})
        assert op.set == {"status": "done"}
        assert op.add == {}
        assert op.remove == {}

    def test_construction_with_add_remove(self) -> None:
        op = UpdateOp(
            op="update",
            **COMMON_FIELDS,
            set={},
            add={"tags": ["ci_infrastructure"]},
            remove={"tags": ["analysis_quality"]},
        )
        assert op.add == {"tags": ["ci_infrastructure"]}
        assert op.remove == {"tags": ["analysis_quality"]}

    def test_wrong_op_type(self) -> None:
        with pytest.raises(ValueError, match="must be 'update'"):
            UpdateOp(op="create", **COMMON_FIELDS, set={})

    def test_frozen(self) -> None:
        op = UpdateOp(op="update", **COMMON_FIELDS, set={"status": "done"})
        with pytest.raises(AttributeError):
            op.clock = 99  # type: ignore[misc]


class TestDiscussOp:
    def test_construction(self) -> None:
        op = DiscussOp(op="discuss", **COMMON_FIELDS, message="hello")
        assert op.message == "hello"

    def test_wrong_op_type(self) -> None:
        with pytest.raises(ValueError, match="must be 'discuss'"):
            DiscussOp(op="create", **COMMON_FIELDS, message="")


class TestDiscussClearOp:
    def test_construction(self) -> None:
        op = DiscussClearOp(op="discuss_clear", **COMMON_FIELDS)
        assert op.op == "discuss_clear"

    def test_wrong_op_type(self) -> None:
        with pytest.raises(ValueError, match="must be 'discuss_clear'"):
            DiscussClearOp(op="create", **COMMON_FIELDS)


class TestDiscussSummarizeOp:
    def test_construction(self) -> None:
        op = DiscussSummarizeOp(op="discuss_summarize", **COMMON_FIELDS, message="summary")
        assert op.message == "summary"

    def test_wrong_op_type(self) -> None:
        with pytest.raises(ValueError, match="must be 'discuss_summarize'"):
            DiscussSummarizeOp(op="update", **COMMON_FIELDS, message="")


class TestLockOp:
    def test_construction(self) -> None:
        op = LockOp(op="lock", **COMMON_FIELDS, lock=["priority", "status"])
        assert op.lock == ["priority", "status"]

    def test_wrong_op_type(self) -> None:
        with pytest.raises(ValueError, match="must be 'lock'"):
            LockOp(op="update", **COMMON_FIELDS, lock=[])


class TestUnlockOp:
    def test_construction(self) -> None:
        op = UnlockOp(op="unlock", **COMMON_FIELDS, unlock=["priority"])
        assert op.unlock == ["priority"]

    def test_wrong_op_type(self) -> None:
        with pytest.raises(ValueError, match="must be 'unlock'"):
            UnlockOp(op="create", **COMMON_FIELDS, unlock=[])


class TestPromoteOp:
    def test_construction(self) -> None:
        op = PromoteOp(op="promote", **COMMON_FIELDS)
        assert op.op == "promote"

    def test_wrong_op_type(self) -> None:
        with pytest.raises(ValueError, match="must be 'promote'"):
            PromoteOp(op="demote", **COMMON_FIELDS)


class TestDemoteOp:
    def test_construction(self) -> None:
        op = DemoteOp(op="demote", **COMMON_FIELDS)
        assert op.op == "demote"

    def test_wrong_op_type(self) -> None:
        with pytest.raises(ValueError, match="must be 'demote'"):
            DemoteOp(op="promote", **COMMON_FIELDS)


class TestStealthOp:
    def test_construction(self) -> None:
        op = StealthOp(op="stealth", **COMMON_FIELDS)
        assert op.op == "stealth"

    def test_wrong_op_type(self) -> None:
        with pytest.raises(ValueError, match="must be 'stealth'"):
            StealthOp(op="update", **COMMON_FIELDS)


class TestUnstealthOp:
    def test_construction(self) -> None:
        op = UnstealthOp(op="unstealth", **COMMON_FIELDS)
        assert op.op == "unstealth"

    def test_wrong_op_type(self) -> None:
        with pytest.raises(ValueError, match="must be 'unstealth'"):
            UnstealthOp(op="update", **COMMON_FIELDS)


class TestReconcileOp:
    def test_construction(self) -> None:
        op = ReconcileOp(
            op="reconcile", **COMMON_FIELDS, from_tier="workspace", reason="duplicate"
        )
        assert op.from_tier == "workspace"
        assert op.reason == "duplicate"

    def test_wrong_op_type(self) -> None:
        with pytest.raises(ValueError, match="must be 'reconcile'"):
            ReconcileOp(op="create", **COMMON_FIELDS, from_tier="", reason="")


# ---------------------------------------------------------------------------
# OP_TYPE_MAP and HUMAN_AUTHORITY_OPS
# ---------------------------------------------------------------------------


class TestOpTypeMaps:
    def test_all_12_op_types_in_map(self) -> None:
        expected = {
            "create", "update", "discuss", "discuss_clear", "discuss_summarize",
            "lock", "unlock", "promote", "demote", "stealth", "unstealth", "reconcile",
        }
        assert set(OP_TYPE_MAP.keys()) == expected

    def test_human_authority_ops(self) -> None:
        assert HUMAN_AUTHORITY_OPS == frozenset({
            "lock", "unlock", "discuss_clear", "stealth", "unstealth",
        })


# ---------------------------------------------------------------------------
# dict_to_op
# ---------------------------------------------------------------------------


class TestDictToOp:
    def test_create_from_dict(self) -> None:
        d = {"op": "create", **COMMON_FIELDS, "data": {"kind": "invariant"}}
        op = dict_to_op(d)
        assert isinstance(op, CreateOp)
        assert op.data == {"kind": "invariant"}

    def test_update_from_dict(self) -> None:
        d = {"op": "update", **COMMON_FIELDS, "set": {"status": "done"}}
        op = dict_to_op(d)
        assert isinstance(op, UpdateOp)
        assert op.set == {"status": "done"}

    def test_discuss_from_dict(self) -> None:
        d = {"op": "discuss", **COMMON_FIELDS, "message": "hello"}
        op = dict_to_op(d)
        assert isinstance(op, DiscussOp)

    def test_lock_from_dict(self) -> None:
        d = {"op": "lock", **COMMON_FIELDS, "lock": ["priority"]}
        op = dict_to_op(d)
        assert isinstance(op, LockOp)

    def test_reconcile_from_dict(self) -> None:
        d = {"op": "reconcile", **COMMON_FIELDS, "from_tier": "canonical", "reason": "test"}
        op = dict_to_op(d)
        assert isinstance(op, ReconcileOp)

    def test_unknown_op_type(self) -> None:
        with pytest.raises(ValueError, match="Unknown op type"):
            dict_to_op({"op": "explode"})

    def test_extra_fields_ignored(self) -> None:
        d = {"op": "promote", **COMMON_FIELDS, "extra_field": "ignored"}
        op = dict_to_op(d)
        assert isinstance(op, PromoteOp)
        assert not hasattr(op, "extra_field")

    def test_missing_op_key(self) -> None:
        with pytest.raises(ValueError, match="Unknown op type"):
            dict_to_op({"at": "2026-01-01"})


# ---------------------------------------------------------------------------
# CompiledItem and DiscussionEntry
# ---------------------------------------------------------------------------


class TestCompiledItem:
    def test_construction_with_defaults(self) -> None:
        item = CompiledItem(id="INV-test", kind="invariant", title="Test", status="todo_hard")
        assert item.priority == 2
        assert item.parent is None
        assert item.tags == []
        assert item.isbefore == []
        assert item.duplicate_of == []
        assert item.not_duplicate_of == []
        assert item.pr_ref is None
        assert item.description == ""
        assert item.fields == {}
        assert item.locked_fields == set()
        assert item.discussion == []
        assert item.created_at == ""
        assert item.updated_at == ""
        assert item.cross_tier_conflict is False
        assert item.simhash is None
        assert item.tier is None

    def test_construction_with_tier(self) -> None:
        item = CompiledItem(
            id="INV-test", kind="invariant", title="Test",
            status="todo_hard", tier=Tier.CANONICAL,
        )
        assert item.tier is Tier.CANONICAL

    def test_mutable(self) -> None:
        item = CompiledItem(id="INV-test", kind="invariant", title="Test", status="todo_hard")
        item.status = "done"
        assert item.status == "done"
        item.tags.append("ci_infrastructure")
        assert item.tags == ["ci_infrastructure"]


class TestDiscussionEntry:
    def test_construction(self) -> None:
        entry = DiscussionEntry(
            by="human", actor="jgstern", at="2026-02-11T18:30:00Z",
            message="hello",
        )
        assert entry.is_summary is False

    def test_summary_entry(self) -> None:
        entry = DiscussionEntry(
            by="agent", actor="test_agent", at="2026-02-11T18:30:00Z",
            message="summary", is_summary=True,
        )
        assert entry.is_summary is True


# ---------------------------------------------------------------------------
# FieldSchema
# ---------------------------------------------------------------------------


class TestFieldSchema:
    def test_text_field(self) -> None:
        fs = FieldSchema(type="text", required=True, description="A text field")
        assert fs.type == "text"
        assert fs.required is True
        assert fs.min is None

    def test_integer_field_with_range(self) -> None:
        fs = FieldSchema(type="integer", min=0, max=100)
        assert fs.min == 0
        assert fs.max == 100

    def test_list_field(self) -> None:
        fs = FieldSchema(type="list")
        assert fs.type == "list"

    def test_boolean_field(self) -> None:
        fs = FieldSchema(type="boolean")
        assert fs.type == "boolean"

    def test_invalid_type(self) -> None:
        with pytest.raises(ValueError, match="must be one of"):
            FieldSchema(type="object")

    def test_min_max_on_non_integer(self) -> None:
        with pytest.raises(ValueError, match="min/max only valid for integer"):
            FieldSchema(type="text", min=0)


# ---------------------------------------------------------------------------
# KindConfig
# ---------------------------------------------------------------------------


class TestKindConfig:
    def test_construction(self) -> None:
        kc = KindConfig(
            prefix="INV",
            description="An invariant",
            fields_schema={"stmt": FieldSchema(type="text", required=True)},
        )
        assert kc.prefix == "INV"
        assert kc.fields_schema is not None
        assert "stmt" in kc.fields_schema

    def test_no_schema(self) -> None:
        kc = KindConfig(prefix="WI")
        assert kc.fields_schema is None
        assert kc.description == ""


# ---------------------------------------------------------------------------
# Config loading and validation
# ---------------------------------------------------------------------------


class TestConfigLoading:
    def test_load_from_config_yaml(self, config_yaml: Path) -> None:
        cfg = load_config(config_yaml.parent)
        assert "invariant" in cfg.kinds
        assert cfg.kinds["invariant"].prefix == "INV"
        assert cfg.statuses == [
            "todo_hard", "todo_soft", "in_progress",
            "needs_human_review", "holding", "satisfied",
            "pending_validation", "violated",
            "done", "wont_do", "deleted",
        ]
        assert cfg.blocking_statuses == [
            "todo_hard", "todo_soft", "violated", "pending_validation",
        ]
        assert cfg.resolved_statuses == [
            "done", "wont_do", "deleted", "satisfied", "holding",
        ]
        assert cfg.kinds["invariant"].deprecated_statuses == ["holding"]
        assert cfg.human_only_statuses == ["deleted"]
        assert cfg.agent_usernames == ["*_agent"]
        assert cfg.lamport_branches == ["dev", "main"]

    def test_load_from_template_fallback(self, tmp_path: Path) -> None:
        template = tmp_path / "config.yaml.template"
        template.write_text(textwrap.dedent("""\
            kinds:
              work_item:
                prefix: WI
            statuses: [todo_hard, done]
            stop_hook:
              blocking_statuses: [todo_hard]
              resolved_statuses: [done]
        """))
        cfg = load_config(tmp_path)
        assert "work_item" in cfg.kinds

    def test_load_builtin_defaults(self, tmp_path: Path) -> None:
        cfg = load_config(tmp_path)
        assert "invariant" in cfg.kinds
        assert "work_item" in cfg.kinds
        assert cfg.statuses == [
            "todo_hard", "todo_soft", "in_progress",
            "needs_human_review", "done", "wont_do", "deleted",
        ]
        assert cfg.human_only_statuses == ["deleted"]
        assert "deleted" in cfg.resolved_statuses

    def test_config_yaml_takes_priority_over_template(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml.template").write_text(textwrap.dedent("""\
            kinds:
              work_item:
                prefix: WI
            statuses: [todo_hard, done]
            stop_hook:
              blocking_statuses: [todo_hard]
              resolved_statuses: [done]
        """))
        (tmp_path / "config.yaml").write_text(textwrap.dedent("""\
            kinds:
              bug:
                prefix: BUG
            statuses: [open, closed]
            stop_hook:
              blocking_statuses: [open]
              resolved_statuses: [closed]
        """))
        cfg = load_config(tmp_path)
        assert "bug" in cfg.kinds
        assert "work_item" not in cfg.kinds

    def test_fields_schema_parsed(self, config_yaml: Path) -> None:
        cfg = load_config(config_yaml.parent)
        inv = cfg.kinds["invariant"]
        assert inv.fields_schema is not None
        assert "statement" in inv.fields_schema
        assert inv.fields_schema["statement"].type == "text"
        assert inv.fields_schema["statement"].required is True
        assert "progress_pct" in inv.fields_schema
        assert inv.fields_schema["progress_pct"].type == "integer"
        assert inv.fields_schema["progress_pct"].min == 0
        assert inv.fields_schema["progress_pct"].max == 100

    def test_kind_without_schema(self, config_yaml: Path) -> None:
        cfg = load_config(config_yaml.parent)
        wi = cfg.kinds["work_item"]
        assert wi.fields_schema is None


class TestConfigValidation:
    def _minimal_config(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "kinds": {"work_item": {"prefix": "WI"}},
            "statuses": ["todo_hard", "done"],
            "stop_hook": {
                "blocking_statuses": ["todo_hard"],
                "resolved_statuses": ["done"],
            },
        }
        base.update(overrides)
        return base

    def test_valid_config(self) -> None:
        cfg = _parse_config_dict(self._minimal_config())
        assert len(cfg.kinds) == 1

    def test_empty_statuses(self) -> None:
        with pytest.raises(ConfigValidationError, match="non-empty list"):
            _parse_config_dict(self._minimal_config(statuses=[]))

    def test_empty_blocking_statuses(self) -> None:
        raw = self._minimal_config()
        raw["stop_hook"]["blocking_statuses"] = []
        with pytest.raises(ConfigValidationError, match="non-empty list"):
            _parse_config_dict(raw)

    def test_blocking_resolved_overlap(self) -> None:
        raw = self._minimal_config()
        raw["stop_hook"]["resolved_statuses"] = ["todo_hard"]
        with pytest.raises(ConfigValidationError, match="overlap"):
            _parse_config_dict(raw)

    def test_unknown_blocking_status(self) -> None:
        raw = self._minimal_config()
        raw["stop_hook"]["blocking_statuses"] = ["nonexistent"]
        with pytest.raises(ConfigValidationError, match="unknown status 'nonexistent'"):
            _parse_config_dict(raw)

    def test_unknown_resolved_status(self) -> None:
        raw = self._minimal_config()
        raw["stop_hook"]["resolved_statuses"] = ["nonexistent"]
        with pytest.raises(ConfigValidationError, match="unknown status 'nonexistent'"):
            _parse_config_dict(raw)

    def test_empty_agent_usernames(self) -> None:
        raw = self._minimal_config()
        raw["actor_resolution"] = {"agent_usernames": []}
        with pytest.raises(ConfigValidationError, match="non-empty list"):
            _parse_config_dict(raw)

    def test_empty_lamport_branches(self) -> None:
        raw = self._minimal_config()
        raw["lamport_branches"] = []
        with pytest.raises(ConfigValidationError, match="non-empty list"):
            _parse_config_dict(raw)

    def test_invalid_scope(self) -> None:
        raw = self._minimal_config()
        raw["stop_hook"]["scope"] = "invalid"
        with pytest.raises(ConfigValidationError, match="must be 'all' or 'workspace'"):
            _parse_config_dict(raw)

    def test_statuses_not_list(self) -> None:
        with pytest.raises(ConfigValidationError, match="non-empty list"):
            _parse_config_dict(self._minimal_config(statuses="bad"))

    def test_resolved_not_list(self) -> None:
        raw = self._minimal_config()
        raw["stop_hook"]["resolved_statuses"] = "done"
        with pytest.raises(ConfigValidationError, match="must be a list"):
            _parse_config_dict(raw)

    def test_agent_usernames_not_list(self) -> None:
        raw = self._minimal_config()
        raw["actor_resolution"] = {"agent_usernames": "*_agent"}
        with pytest.raises(ConfigValidationError, match="non-empty list"):
            _parse_config_dict(raw)

    def test_deprecated_statuses_parsed(self) -> None:
        raw = self._minimal_config()
        raw["statuses"] = ["todo_hard", "done", "holding", "satisfied"]
        raw["kinds"] = {
            "invariant": {
                "prefix": "INV",
                "allowed_statuses": ["satisfied"],
                "deprecated_statuses": ["holding"],
            },
        }
        cfg = _parse_config_dict(raw)
        assert cfg.kinds["invariant"].deprecated_statuses == ["holding"]

    def test_deprecated_statuses_unknown_raises(self) -> None:
        raw = self._minimal_config()
        raw["kinds"] = {
            "invariant": {
                "prefix": "INV",
                "deprecated_statuses": ["nonexistent"],
            },
        }
        with pytest.raises(ConfigValidationError, match=r"deprecated_statuses.*unknown"):
            _parse_config_dict(raw)

    def test_deprecated_statuses_overlap_raises(self) -> None:
        raw = self._minimal_config()
        raw["kinds"] = {
            "invariant": {
                "prefix": "INV",
                "allowed_statuses": ["todo_hard"],
                "deprecated_statuses": ["todo_hard"],
            },
        }
        with pytest.raises(ConfigValidationError, match="overlap"):
            _parse_config_dict(raw)

    def test_deprecated_statuses_not_list_raises(self) -> None:
        raw = self._minimal_config()
        raw["kinds"] = {
            "invariant": {
                "prefix": "INV",
                "deprecated_statuses": "holding",
            },
        }
        with pytest.raises(ConfigValidationError, match="deprecated_statuses must be a list"):
            _parse_config_dict(raw)

    def test_kind_spec_not_dict(self) -> None:
        raw = self._minimal_config()
        raw["kinds"] = {"bad": "not a dict"}
        with pytest.raises(ConfigValidationError, match="must be a dict"):
            _parse_config_dict(raw)

    def test_field_schema_spec_not_dict(self) -> None:
        raw = self._minimal_config()
        raw["kinds"] = {"wi": {"prefix": "WI", "fields_schema": {"name": "not a dict"}}}
        with pytest.raises(ConfigValidationError, match="must be a dict"):
            _parse_config_dict(raw)

    def test_empty_config_yaml_raises(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text("")
        # Empty YAML → yaml.safe_load returns None → `or {}` → empty dict
        # → statuses defaults to [] → fails validation
        with pytest.raises(ConfigValidationError, match="non-empty list"):
            load_config(tmp_path)

    def test_scope_defaults(self) -> None:
        cfg = _parse_config_dict(self._minimal_config())
        assert cfg.scope == "all"

    def test_scope_workspace(self) -> None:
        raw = self._minimal_config()
        raw["stop_hook"]["scope"] = "workspace"
        cfg = _parse_config_dict(raw)
        assert cfg.scope == "workspace"

    def test_allowed_statuses_not_list(self) -> None:
        raw = self._minimal_config()
        raw["kinds"] = {"wi": {"prefix": "WI", "allowed_statuses": "bad"}}
        with pytest.raises(ConfigValidationError, match="allowed_statuses must be a list"):
            _parse_config_dict(raw)

    def test_allowed_statuses_unknown_status(self) -> None:
        raw = self._minimal_config()
        raw["kinds"] = {"wi": {"prefix": "WI", "allowed_statuses": ["nonexistent"]}}
        with pytest.raises(ConfigValidationError, match="allowed_statuses references unknown status 'nonexistent'"):
            _parse_config_dict(raw)

    def test_human_only_statuses_parsed(self) -> None:
        raw = self._minimal_config()
        raw["statuses"] = ["todo_hard", "done", "deleted"]
        raw["stop_hook"]["human_only_statuses"] = ["deleted"]
        raw["stop_hook"]["resolved_statuses"] = ["done", "deleted"]
        cfg = _parse_config_dict(raw)
        assert cfg.human_only_statuses == ["deleted"]

    def test_human_only_statuses_defaults_empty(self) -> None:
        cfg = _parse_config_dict(self._minimal_config())
        assert cfg.human_only_statuses == []

    def test_human_only_statuses_unknown_reference(self) -> None:
        raw = self._minimal_config()
        raw["stop_hook"]["human_only_statuses"] = ["nonexistent"]
        with pytest.raises(ConfigValidationError, match="unknown status 'nonexistent'"):
            _parse_config_dict(raw)

    def test_human_only_statuses_blocking_overlap(self) -> None:
        raw = self._minimal_config()
        raw["stop_hook"]["human_only_statuses"] = ["todo_hard"]
        with pytest.raises(ConfigValidationError, match="human_only_statuses and blocking_statuses overlap"):
            _parse_config_dict(raw)

    def test_human_only_statuses_not_list(self) -> None:
        raw = self._minimal_config()
        raw["stop_hook"]["human_only_statuses"] = "deleted"
        with pytest.raises(ConfigValidationError, match="must be a list"):
            _parse_config_dict(raw)


# ---------------------------------------------------------------------------
# Actor resolution
# ---------------------------------------------------------------------------


class TestActorResolution:
    def test_agent_resolution(self, mock_agent_uid: None) -> None:
        by, actor = resolve_actor(["*_agent"])
        assert by == "agent"
        assert actor == "test_agent"

    def test_human_resolution(self, mock_human_uid: None) -> None:
        by, actor = resolve_actor(["*_agent"])
        assert by == "human"
        assert actor == "jgstern"

    def test_default_pattern(self, mock_agent_uid: None) -> None:
        by, actor = resolve_actor()
        assert by == "agent"

    def test_custom_pattern(self, mock_human_uid: None) -> None:
        # jgstern doesn't match *_agent but does match jgstern
        by, actor = resolve_actor(["jgstern"])
        assert by == "agent"
        assert actor == "jgstern"

    def test_no_pattern_match(self, mock_human_uid: None) -> None:
        by, actor = resolve_actor(["bot_*"])
        assert by == "human"
        assert actor == "jgstern"

    def test_multiple_patterns(self, mock_agent_uid: None) -> None:
        by, actor = resolve_actor(["bot_*", "*_agent"])
        assert by == "agent"
