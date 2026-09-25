# SPDX-License-Identifier: MPL-2.0
"""The custom-field-key gate: what may be stored, and where the rule lives.

WHY THESE TESTS EXERCISE THE STORE AND NOT ONLY THE PURE FUNCTION. INV-linan's
first layer is that ``Store.update`` runs no schema check at all and never looks
inside ``set_fields["fields"]`` -- which is exactly where a custom key goes. A
gate that only the CLI enforces is bypassed by every other writer, so the
store-level tests below are the ones that pin the invariant; the pure-function
tests pin the classification.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_tracker.field_keys import (
    CORE_ATTRIBUTE_NAMES,
    FieldKeyError,
    check_field_key,
    flag_for,
    normalise,
)
from hypergumbo_tracker.models import load_config
from hypergumbo_tracker.store import Store


class TestTheClassification:
    """Three tiers, keyed on normalisation."""

    @pytest.mark.parametrize("spelling", ["status", "Status", "STATUS", " status "])
    def test_a_core_attribute_is_refused_however_it_is_spelled(
        self, spelling: str,
    ) -> None:
        with pytest.raises(FieldKeyError) as exc:
            check_field_key(spelling)
        assert exc.value.tier == 1
        assert exc.value.forceable is False
        assert "--status" in str(exc.value)

    def test_tier_one_is_not_forceable(self) -> None:
        # The escape hatch is what produced eighteen of these in the corpus.
        with pytest.raises(FieldKeyError) as exc:
            check_field_key("priority", force=True)
        assert exc.value.tier == 1

    @pytest.mark.parametrize("spelling", ["Root_Cause", "root-cause", "ROOT CAUSE"])
    def test_a_declared_field_spelled_differently_is_tier_two(
        self, spelling: str,
    ) -> None:
        with pytest.raises(FieldKeyError) as exc:
            check_field_key(spelling, declared=["root_cause"])
        assert exc.value.tier == 2
        assert exc.value.forceable is True
        assert "root_cause" in str(exc.value)

    def test_tier_two_is_forceable(self) -> None:
        check_field_key("Root_Cause", declared=["root_cause"], force=True)

    def test_the_declared_spelling_itself_is_allowed(self) -> None:
        check_field_key("root_cause", declared=["root_cause"])

    def test_an_undeclared_distinct_key_is_allowed(self) -> None:
        # `severity` sits on 17 invariants and no schema names it. The open
        # dict is genuinely used; `validate --strict` is where it surfaces.
        check_field_key("severity", declared=["statement", "root_cause"])

    def test_a_near_miss_that_does_not_NORMALISE_is_tier_three(self) -> None:
        # The tiers are keyed on normalisation, which is narrower than "looks
        # similar" on purpose: an edit-distance rule would start refusing
        # legitimately distinct keys that happen to sit near a declared one.
        check_field_key("root_causes", declared=["root_cause"])

    def test_a_kind_with_no_schema_can_still_take_custom_keys(self) -> None:
        check_field_key("dogfood_anon_id", declared=())


class TestTheHelpers:
    def test_normalise_folds_case_and_separators(self) -> None:
        assert normalise(" Pr-Ref ") == "pr_ref"

    def test_flag_for_names_the_cli_flag(self) -> None:
        assert flag_for("pr_ref") == "--pr-ref"


def _store(ops_dir: Path, config_yaml: Path) -> Store:
    return Store(ops_dir, config=load_config(config_yaml))


class TestTheStoreIsWhereTheGateLives:
    """A gate only the CLI enforces is bypassed by every other writer."""

    def test_add_refuses_a_core_name(
        self, ops_dir: Path, config_yaml: Path, mock_agent_uid: None,
    ) -> None:
        store = _store(ops_dir, config_yaml)
        with pytest.raises(FieldKeyError):
            store.add(kind="work_item", title="t", fields={"status": "done"})

    def test_add_allows_an_undeclared_distinct_key(
        self, ops_dir: Path, config_yaml: Path, mock_agent_uid: None,
    ) -> None:
        store = _store(ops_dir, config_yaml)
        item_id = store.add(
            kind="work_item", title="t", fields={"severity": "high"},
        )
        assert store.get(item_id).fields["severity"] == "high"

    def test_update_refuses_a_core_name_INSIDE_the_fields_dict(
        self, ops_dir: Path, config_yaml: Path, mock_agent_uid: None,
    ) -> None:
        """THE LAYER-1 DEFECT. ``update``'s field-name loop validates the
        TOP-LEVEL keys only; nothing looked inside ``set_fields["fields"]``,
        which is how ``fields.status`` reached eleven items under a top-level
        ``status`` that disagreed with it."""
        store = _store(ops_dir, config_yaml)
        item_id = store.add(kind="work_item", title="t")
        with pytest.raises(FieldKeyError) as exc:
            store.update(item_id, set_fields={"fields": {"status": "violated"}})
        assert exc.value.tier == 1

    def test_update_allows_an_undeclared_distinct_key(
        self, ops_dir: Path, config_yaml: Path, mock_agent_uid: None,
    ) -> None:
        store = _store(ops_dir, config_yaml)
        item_id = store.add(kind="work_item", title="t")
        store.update(item_id, set_fields={"fields": {"rationale": "because"}})
        assert store.get(item_id).fields["rationale"] == "because"

    def test_removing_an_offending_key_is_NOT_refused(
        self, ops_dir: Path, config_yaml: Path, mock_agent_uid: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Deleting the offending key is the REMEDY. A gate that refused it
        would freeze the eleven corpus items in their broken state for good —
        the one shape where the rule must let the write through.

        The key is planted with the check disabled, which is exactly how the
        corpus acquired its eighteen: they were written before the gate
        existed.
        """
        import hypergumbo_tracker.store as store_mod

        store = _store(ops_dir, config_yaml)
        item_id = store.add(kind="work_item", title="t")
        monkeypatch.setattr(store_mod, "check_field_key", lambda *a, **k: None)
        store.update(item_id, set_fields={"fields": {"status": "violated"}})
        monkeypatch.undo()
        assert store.get(item_id).fields["status"] == "violated"

        # With the gate live again, the REMOVAL must go through.
        store.update(item_id, set_fields={"fields": {"status": None}})
        assert "status" not in store.get(item_id).fields

    def test_update_still_moves_the_REAL_status(
        self, ops_dir: Path, config_yaml: Path, mock_agent_uid: None,
    ) -> None:
        # The gate must not touch the core attribute's own path.
        store = _store(ops_dir, config_yaml)
        item_id = store.add(kind="work_item", title="t")
        store.update(item_id, set_fields={"status": "done"})
        assert store.get(item_id).status == "done"


class TestTheCoreSetDoesNotDriftFromTheCli:
    def test_every_core_name_has_a_cli_flag_or_is_read_only(self) -> None:
        """The core names live in ``field_keys`` because the store cannot
        import the CLI parser without a cycle. This is the test that fails if
        the two drift."""
        from hypergumbo_tracker.cli import _build_parser

        parser = _build_parser()
        flags: set[str] = set()
        for action in parser._subparsers._group_actions[0].choices.values():  # type: ignore[union-attr]
            for act in action._actions:
                flags.update(act.option_strings)
        # Attributes the tracker computes rather than accepts as a flag.
        read_only = {"id", "discussion", "created", "updated", "kind"}
        for name in CORE_ATTRIBUTE_NAMES - read_only:
            assert flag_for(name) in flags, (
                f"core attribute {name!r} resolves to {flag_for(name)!r}, which "
                f"is not a flag any subcommand accepts — the tier-1 message "
                f"would name a flag that does not exist"
            )


class TestTheCliFlag:
    """`--force-field-key` on `add`, which is the tier-2 escape hatch."""

    def test_add_accepts_force_field_key_for_a_spelling_twin(
        self, tmp_path: Path, mock_agent_uid: None,
    ) -> None:
        from hypergumbo_tracker.cli import EXIT_SUCCESS, main

        tracker_root = tmp_path / ".agent"
        (tracker_root / "tracker" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)

        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "add",
                "--kind", "work_item", "--title", "t",
                "--field", "Rationale=because", "--force-field-key",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_add_still_refuses_a_core_name_even_with_the_flag(
        self, tmp_path: Path, mock_agent_uid: None,
    ) -> None:
        """Tier 1 has no escape hatch, and the flag must not become one."""
        from hypergumbo_tracker.cli import main

        tracker_root = tmp_path / ".agent"
        (tracker_root / "tracker" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)

        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "add",
                "--kind", "work_item", "--title", "t",
                "--field", "status=done", "--force-field-key",
            ])
        assert exc.value.code != 0
