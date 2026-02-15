# SPDX-License-Identifier: MPL-2.0
"""Tests for migration from markdown governance files to YAML tracker ops.

Tests cover four phases:
1. Pure functions: normalize_status, assign_priority, category_to_tag, write_config_template
2. Parsers: parse_invariant_ledger, parse_work_items
3. Writer + pipeline: build_create_op, migrate()
4. Integration: real governance files → validated output

Each test exercises a specific invariant of the migration process.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

from hypergumbo_tracker.migration import (
    MigrationResult,
    ParsedItem,
    assign_priority,
    build_create_op,
    category_to_tag,
    migrate,
    normalize_status,
    parse_invariant_ledger,
    parse_work_items,
    write_config_template,
)
from hypergumbo_tracker.models import load_config
from hypergumbo_tracker.store import _parse_ops_file, compile_ops


# ============================================================================
# Phase 1: Pure functions
# ============================================================================


class TestNormalizeStatus:
    """Test normalize_status() — maps raw markdown statuses to tracker statuses."""

    def test_fixed(self) -> None:
        assert normalize_status("FIXED") == "done"

    def test_fixed_with_emoji(self) -> None:
        assert normalize_status("✅ FIXED") == "done"

    def test_fixed_with_emoji_and_parens(self) -> None:
        assert normalize_status("✅ FIXED (line coverage at 100%, enforced in CI)") == "done"

    def test_fixed_with_parens(self) -> None:
        assert normalize_status("FIXED (with full RESTful expansion)") == "done"

    def test_wont_do(self) -> None:
        assert normalize_status("⬛ WON'T DO") == "wont_do"

    def test_unfixed(self) -> None:
        assert normalize_status("UNFIXED") == "todo_hard"

    def test_partially_addressed(self) -> None:
        assert normalize_status("PARTIALLY ADDRESSED") == "in_progress"

    def test_100_percent(self) -> None:
        assert normalize_status("100%") == "done"

    def test_below_100_percent(self) -> None:
        assert normalize_status("80%") == "in_progress"

    def test_tbd(self) -> None:
        assert normalize_status("TBD") == "todo_hard"

    def test_done(self) -> None:
        assert normalize_status("DONE") == "done"

    def test_deferred(self) -> None:
        assert normalize_status("DEFERRED") == "deferred"

    def test_todo_hard(self) -> None:
        assert normalize_status("TODO!") == "todo_hard"

    def test_todo_soft(self) -> None:
        assert normalize_status("TODO") == "todo_soft"

    def test_unknown_passes_through(self) -> None:
        assert normalize_status("SOME_UNKNOWN") == "todo_hard"

    def test_fixed_various_parens(self) -> None:
        assert normalize_status("FIXED (Python, TypeScript, Java, C#, Rust)") == "done"

    def test_whitespace_stripped(self) -> None:
        assert normalize_status("  FIXED  ") == "done"


class TestAssignPriority:
    """Test assign_priority() — maps normalized status to priority level."""

    def test_todo_hard(self) -> None:
        assert assign_priority("todo_hard") == 0

    def test_todo_soft(self) -> None:
        assert assign_priority("todo_soft") == 1

    def test_in_progress(self) -> None:
        assert assign_priority("in_progress") == 1

    def test_done(self) -> None:
        assert assign_priority("done") == 4

    def test_deferred(self) -> None:
        assert assign_priority("deferred") == 4

    def test_wont_do(self) -> None:
        assert assign_priority("wont_do") == 4


class TestCategoryToTag:
    """Test category_to_tag() — converts section headers to normalized tags."""

    def test_developer_experience(self) -> None:
        assert category_to_tag("Developer Experience") == "developer_experience"

    def test_cross_language_linkers(self) -> None:
        assert category_to_tag("Cross-Language Linkers") == "cross_language_linkers"

    def test_extends_edge_disambiguation(self) -> None:
        assert category_to_tag("Extends Edge Disambiguation") == "extends_edge_disambiguation"

    def test_bakeoff_infrastructure(self) -> None:
        assert category_to_tag("Bakeoff Infrastructure") == "bakeoff_infrastructure"

    def test_analysis_quality(self) -> None:
        assert category_to_tag("Analysis Quality") == "analysis_quality"

    def test_language_additions(self) -> None:
        assert category_to_tag("Language Additions") == "language_additions"

    def test_ci_infrastructure(self) -> None:
        assert category_to_tag("CI Infrastructure") == "ci_infrastructure"

    def test_from_inv_with_parens(self) -> None:
        assert category_to_tag("From INV-011 (migrated from invariant ledger)") == "from_inv_011"

    def test_empty(self) -> None:
        assert category_to_tag("") == ""

    def test_multiple_spaces(self) -> None:
        assert category_to_tag("Some  Multi   Spaced") == "some_multi_spaced"


class TestWriteConfigTemplate:
    """Test write_config_template() — creates valid config files."""

    def test_creates_config_files(self, tmp_path: Path) -> None:
        tracker_dir = tmp_path / "tracker"
        tracker_dir.mkdir()
        write_config_template(tracker_dir)

        assert (tracker_dir / "config.yaml.template").exists()
        assert (tracker_dir / "config.yaml").exists()

    def test_config_is_loadable(self, tmp_path: Path) -> None:
        tracker_dir = tmp_path / "tracker"
        tracker_dir.mkdir()
        write_config_template(tracker_dir)

        config = load_config(tracker_dir)
        assert "invariant" in config.kinds
        assert "meta_invariant" in config.kinds
        assert "work_item" in config.kinds

    def test_config_has_correct_statuses(self, tmp_path: Path) -> None:
        tracker_dir = tmp_path / "tracker"
        tracker_dir.mkdir()
        write_config_template(tracker_dir)

        config = load_config(tracker_dir)
        assert "todo_hard" in config.statuses
        assert "todo_soft" in config.statuses
        assert "in_progress" in config.statuses
        assert "done" in config.statuses
        assert "deferred" in config.statuses
        assert "wont_do" in config.statuses

    def test_config_has_correct_prefixes(self, tmp_path: Path) -> None:
        tracker_dir = tmp_path / "tracker"
        tracker_dir.mkdir()
        write_config_template(tracker_dir)

        config = load_config(tracker_dir)
        assert config.kinds["invariant"].prefix == "INV"
        assert config.kinds["meta_invariant"].prefix == "META"
        assert config.kinds["work_item"].prefix == "WI"

    def test_does_not_overwrite_existing_template(self, tmp_path: Path) -> None:
        tracker_dir = tmp_path / "tracker"
        tracker_dir.mkdir()
        (tracker_dir / "config.yaml.template").write_text("existing: true\n")
        write_config_template(tracker_dir)
        content = (tracker_dir / "config.yaml.template").read_text()
        assert "existing: true" in content


# ============================================================================
# Phase 2: Parsers
# ============================================================================


class TestParseInvariantLedger:
    """Test parse_invariant_ledger() — parses INV and META entries."""

    def test_parses_simple_inv(self) -> None:
        content = textwrap.dedent("""\
            # Invariant Ledger

            ## INV-001: Call Attribution Completeness
            - **Statement:** Every emitted `calls` edge has a non-null caller symbol
            - **Status:** FIXED
            - **Root cause:** JS/TS arrow function special-case
            - **Fix:** Position-based lookup
            - **Verification:** Kotlin and Scala lambdas work correctly
            - **Regression tests:**
              - `test_js_ts.py::TestCallbackCallAttribution`
              - `test_kotlin.py::TestKotlinLambdaCallAttribution` (4 tests)
            - **Pending Generalizations:** None
        """)
        items = parse_invariant_ledger(content)
        assert len(items) == 1
        item = items[0]
        assert item.source_id == "INV-001"
        assert item.kind == "invariant"
        assert item.title == "Call Attribution Completeness"
        assert item.status == "done"
        assert item.priority == 4
        assert item.fields["statement"] == "Every emitted `calls` edge has a non-null caller symbol"
        assert item.fields["root_cause"] == "JS/TS arrow function special-case"
        assert item.fields["fix"] == "Position-based lookup"
        assert item.fields["verification"] == "Kotlin and Scala lambdas work correctly"
        assert len(item.fields["regression_tests"]) == 2

    def test_parses_meta_invariant(self) -> None:
        content = textwrap.dedent("""\
            ### META-001: Metadata Must Become Graph Structure
            > "Semantic relationships expressed in metadata must become traversable graph structure."

            - **Status:** 100%
            - **Notes:**
              - **DONE (create edges from base_classes):** Java, JS/TS, Python, Ruby
            - **Pending Generalizations:** None
        """)
        items = parse_invariant_ledger(content)
        assert len(items) == 1
        item = items[0]
        assert item.source_id == "META-001"
        assert item.kind == "meta_invariant"
        assert item.title == "Metadata Must Become Graph Structure"
        assert item.status == "done"
        assert item.fields["statement"] == (
            "Semantic relationships expressed in metadata must become traversable graph structure."
        )

    def test_skips_template_entries(self) -> None:
        content = textwrap.dedent("""\
            ## INV-XXX: Template for New Invariants
            - **Statement:** [What must always be true]
            - **Status:** [UNFIXED | PARTIALLY ADDRESSED | FIXED | TBD]

            ## META-00X: Template for New Meta-Invariants
            > "[Broad principle statement]"
            - **Status:** [100% | <100% (e.g., 80%) | TBD]
        """)
        items = parse_invariant_ledger(content)
        assert len(items) == 0

    def test_parses_emoji_status(self) -> None:
        content = textwrap.dedent("""\
            ## INV-007: Go Import Path Resolution
            - **Statement:** When multiple Go files define the same symbol
            - **Status:** ✅ FIXED
            - **Root cause:** symbol_resolution.py
            - **Fix:** Enhanced lookup
            - **Pending Generalizations:** None
        """)
        items = parse_invariant_ledger(content)
        assert len(items) == 1
        assert items[0].status == "done"

    def test_parses_wont_do_status(self) -> None:
        content = textwrap.dedent("""\
            ## INV-011: Branch Coverage
            - **Statement:** Branch coverage should be tracked
            - **Status:** ⬛ WON'T DO
            - **Pending Generalizations:** None
        """)
        items = parse_invariant_ledger(content)
        assert len(items) == 1
        assert items[0].status == "wont_do"

    def test_parses_meta_with_hash_heading(self) -> None:
        content = textwrap.dedent("""\
            ## META-004: Testing Discipline
            > "Tests must exercise all code paths to verify correctness and prevent regressions."

            - **Status:** ✅ FIXED (line coverage at 100%, enforced in CI)
            - **Notes:**
              - Line coverage: 100%
            - **Pending Generalizations:** None
        """)
        items = parse_invariant_ledger(content)
        assert len(items) == 1
        assert items[0].source_id == "META-004"
        assert items[0].status == "done"

    def test_parses_multiline_fields(self) -> None:
        content = textwrap.dedent("""\
            ## INV-002: Usage-to-Concept Flow
            - **Statement:** Usage patterns extracted by analyzers become concepts on nodes
            - **Status:** FIXED
            - **Root cause:** `symbol_ref` gate prevented UsageContexts with `symbol_ref=None` from
              enriching symbols. This happens when analyzers extract string-based handler references.
            - **Fix:** Added deferred resolution phase
            - **Pending Generalizations:** None
        """)
        items = parse_invariant_ledger(content)
        assert len(items) == 1
        assert "symbol_ref" in items[0].fields["root_cause"]
        assert "enriching symbols" in items[0].fields["root_cause"]

    def test_horizontal_rule_finalizes(self) -> None:
        """Horizontal rule (---) finalizes the current entry."""
        content = textwrap.dedent("""\
            ## INV-001: First Item
            - **Statement:** Something
            - **Status:** FIXED
            - **Root cause:** Bug
            - **Fix:** Fixed
            - **Pending Generalizations:** None

            ---

            ## INV-002: Second Item
            - **Statement:** Another thing
            - **Status:** UNFIXED
            - **Root cause:** Different bug
            - **Fix:** Pending
            - **Pending Generalizations:** None
        """)
        items = parse_invariant_ledger(content)
        assert len(items) == 2
        assert items[0].source_id == "INV-001"
        assert items[1].source_id == "INV-002"

    def test_empty_file(self) -> None:
        items = parse_invariant_ledger("")
        assert items == []

    def test_real_ledger_count(self) -> None:
        """Parse the actual invariant ledger and verify item count."""
        ledger_path = Path("/home/jgstern_agent/hypergumbo/.agent/invariant-ledger.md")
        if not ledger_path.exists():
            pytest.skip("Real ledger not available")
        content = ledger_path.read_text()
        items = parse_invariant_ledger(content)
        inv_items = [i for i in items if i.kind == "invariant"]
        meta_items = [i for i in items if i.kind == "meta_invariant"]
        assert len(inv_items) == 15, f"Expected 15 INV, got {len(inv_items)}"
        assert len(meta_items) == 4, f"Expected 4 META, got {len(meta_items)}"

    def test_fixed_with_parens_description(self) -> None:
        content = textwrap.dedent("""\
            ## INV-006: Rails Resource Route Handler Resolution
            - **Statement:** Rails resource routes should have handler metadata
            - **Status:** FIXED (with full RESTful expansion)
            - **Root cause:** Ruby analyzer only extracted explicit syntax
            - **Fix:** Full RESTful route expansion
            - **Pending Generalizations:** None
        """)
        items = parse_invariant_ledger(content)
        assert len(items) == 1
        assert items[0].status == "done"

    def test_description_fields_folded(self) -> None:
        """Architecture decision, Impact etc. go into description."""
        content = textwrap.dedent("""\
            ## INV-002: Usage-to-Concept Flow
            - **Statement:** Usage patterns
            - **Status:** FIXED
            - **Root cause:** Gate prevented
            - **Fix:** Deferred resolution
            - **Architecture decision:** Chose deferred resolution (Option 2)
            - **Impact:** Feature bakeoff showed improvement
            - **Pending Generalizations:** None
        """)
        items = parse_invariant_ledger(content)
        assert len(items) == 1
        assert "deferred resolution" in items[0].description.lower()
        assert "improvement" in items[0].description.lower()

    def test_unified_by_as_list_item(self) -> None:
        """Unified by as a list field with value gets folded into description."""
        content = textwrap.dedent("""\
            ### META-005: Test Meta
            > "Test principle."
            - **Status:** 100%
            - **Unified by:** INV-001 and INV-002
            - **Implication:** Must create edges
            - **Pending Generalizations:** None
        """)
        items = parse_invariant_ledger(content)
        assert len(items) == 1
        assert "Unified by: INV-001 and INV-002" in items[0].description
        assert "Implication: Must create edges" in items[0].description

    def test_notes_with_value(self) -> None:
        """Notes field with inline value gets into description."""
        content = textwrap.dedent("""\
            ### META-005: Test Meta
            > "Test principle."
            - **Status:** 100%
            - **Notes:** Some inline note text
            - **Pending Generalizations:** None
        """)
        items = parse_invariant_ledger(content)
        assert len(items) == 1
        assert "Some inline note text" in items[0].description

    def test_unknown_field_no_value(self) -> None:
        """Unknown field with empty value still sets current_field_key."""
        content = textwrap.dedent("""\
            ## INV-099: Edge Case
            - **Statement:** Something
            - **Status:** FIXED
            - **Root cause:** Bug
            - **Fix:** Fixed
            - **CustomField:**
              Detail on next line
            - **Pending Generalizations:** None
        """)
        items = parse_invariant_ledger(content)
        assert len(items) == 1


class TestParseWorkItems:
    """Test parse_work_items() — parses work item entries by category."""

    def test_parses_done_item_with_pr(self) -> None:
        content = textwrap.dedent("""\
            ## Developer Experience
            - **DONE** (PR#922) Make `check-package-coverage` run faster.
        """)
        items = parse_work_items(content)
        assert len(items) == 1
        item = items[0]
        assert item.status == "done"
        assert item.pr_ref == "PR#922"
        assert "check-package-coverage" in item.title
        assert "developer_experience" in item.tags

    def test_parses_deferred_item(self) -> None:
        content = textwrap.dedent("""\
            ## Cross-Language Linkers
            - **DEFERRED** Target: Node.js N-API linker — description here. Deferred: reason.
        """)
        items = parse_work_items(content)
        assert len(items) == 1
        assert items[0].status == "deferred"
        assert "cross_language_linkers" in items[0].tags

    def test_parses_multiple_categories(self) -> None:
        content = textwrap.dedent("""\
            ## Developer Experience
            - **DONE** (PR#922) First item.

            ## Cross-Language Linkers
            - **DONE** (PR#1003) Second item.
            - **DEFERRED** Third item. Deferred: reason.
        """)
        items = parse_work_items(content)
        assert len(items) == 3
        assert items[0].tags == ["developer_experience"]
        assert items[1].tags == ["cross_language_linkers"]
        assert items[2].tags == ["cross_language_linkers"]

    def test_parses_pr_ref_with_space(self) -> None:
        content = textwrap.dedent("""\
            ## Analysis Quality
            - **DONE** (PR #1016) Target: Java something.
        """)
        items = parse_work_items(content)
        assert len(items) == 1
        assert items[0].pr_ref == "PR #1016"

    def test_parses_pr_ref_pending(self) -> None:
        content = textwrap.dedent("""\
            ## Analysis Quality
            - **DONE** (PR pending) Something.
        """)
        items = parse_work_items(content)
        assert len(items) == 1
        assert items[0].pr_ref == "PR pending"

    def test_parses_pr_ref_with_hash_number(self) -> None:
        content = textwrap.dedent("""\
            ## Analysis Quality
            - **DONE** (PR#1037 pending) Something about headers.
        """)
        items = parse_work_items(content)
        assert len(items) == 1
        assert items[0].pr_ref == "PR#1037 pending"

    def test_empty_file(self) -> None:
        items = parse_work_items("")
        assert items == []

    def test_file_with_only_header(self) -> None:
        content = textwrap.dedent("""\
            # Work Items

            Lightweight backlog for non-invariant work.
        """)
        items = parse_work_items(content)
        assert items == []

    def test_real_work_items_count(self) -> None:
        """Parse the actual work items file and verify item count."""
        wi_path = Path(
            "/home/jgstern_agent/hypergumbo_lab_notebook/guidance_log/work_items.md"
        )
        if not wi_path.exists():
            pytest.skip("Real work items not available")
        content = wi_path.read_text()
        items = parse_work_items(content)
        done_items = [i for i in items if i.status == "done"]
        deferred_items = [i for i in items if i.status == "deferred"]
        assert len(items) == 24, f"Expected 24 items, got {len(items)}"
        assert len(done_items) == 7, f"Expected 7 DONE, got {len(done_items)}"
        assert len(deferred_items) == 17, f"Expected 17 DEFERRED, got {len(deferred_items)}"

    def test_source_ids_sequential_per_category(self) -> None:
        content = textwrap.dedent("""\
            ## Developer Experience
            - **DONE** (PR#1) First item.
            - **DEFERRED** Second item.

            ## CI Infrastructure
            - **DEFERRED** Third item.
        """)
        items = parse_work_items(content)
        assert items[0].source_id == "wi-developer-experience-0"
        assert items[1].source_id == "wi-developer-experience-1"
        assert items[2].source_id == "wi-ci-infrastructure-0"

    def test_item_without_pr_ref(self) -> None:
        content = textwrap.dedent("""\
            ## Language Additions
            - **DEFERRED** Add Assembly analyzer. Grammar: tree-sitter-asm.
        """)
        items = parse_work_items(content)
        assert len(items) == 1
        assert items[0].pr_ref is None
        assert "Assembly" in items[0].title

    def test_justification_for_deferred(self) -> None:
        content = textwrap.dedent("""\
            ## Language Additions
            - **DEFERRED** Add Assembly analyzer. Lower priority than linker work.
        """)
        items = parse_work_items(content)
        assert len(items) == 1
        assert items[0].justification is not None
        assert "priority" in items[0].justification.lower()


# ============================================================================
# Phase 3: Writer + pipeline
# ============================================================================


class TestBuildCreateOp:
    """Test build_create_op() — deterministic op dict construction."""

    def test_builds_valid_op(self, tmp_path: Path) -> None:
        tracker_dir = tmp_path / "tracker"
        tracker_dir.mkdir()
        write_config_template(tracker_dir)
        config = load_config(tracker_dir)

        item = ParsedItem(
            source_id="INV-001",
            kind="invariant",
            title="Test Invariant",
            status="done",
            priority=4,
            description="Some description",
            fields={"statement": "must be true", "root_cause": "bug"},
            tags=["analysis_quality"],
            pr_ref=None,
            justification=None,
            parent_source_id=None,
        )
        op_dict, item_id = build_create_op(item, config)
        assert op_dict["op"] == "create"
        assert op_dict["by"] == "agent"
        assert op_dict["actor"] == "migration"
        assert op_dict["clock"] == 1
        assert item_id.startswith("INV-")

    def test_idempotent_ids(self, tmp_path: Path) -> None:
        """Same input produces same ID."""
        tracker_dir = tmp_path / "tracker"
        tracker_dir.mkdir()
        write_config_template(tracker_dir)
        config = load_config(tracker_dir)

        item = ParsedItem(
            source_id="INV-001",
            kind="invariant",
            title="Test Invariant",
            status="done",
            priority=4,
            description="",
            fields={},
            tags=[],
            pr_ref=None,
            justification=None,
            parent_source_id=None,
        )
        _, id1 = build_create_op(item, config)
        _, id2 = build_create_op(item, config)
        assert id1 == id2


class TestMigrate:
    """Test migrate() — full pipeline."""

    def test_creates_directories(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        ledger = tmp_path / "ledger.md"
        ledger.write_text(textwrap.dedent("""\
            ## INV-001: Test Item
            - **Statement:** Something
            - **Status:** FIXED
            - **Root cause:** Bug
            - **Fix:** Fixed it
            - **Pending Generalizations:** None
        """))
        work_items = tmp_path / "work_items.md"
        work_items.write_text("")

        result = migrate(ledger, work_items, tracker_root)

        assert (tracker_root / "tracker" / ".ops").is_dir()
        assert (tracker_root / "tracker-workspace" / ".ops").is_dir()
        assert (tracker_root / "tracker" / "config.yaml.template").exists()
        assert result.items_created >= 1
        assert result.errors == []

    def test_creates_ops_files(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        ledger = tmp_path / "ledger.md"
        ledger.write_text(textwrap.dedent("""\
            ## INV-001: Test Item
            - **Statement:** Something
            - **Status:** FIXED
            - **Root cause:** Bug
            - **Fix:** Fixed it
            - **Pending Generalizations:** None
        """))
        work_items = tmp_path / "work_items.md"
        work_items.write_text(textwrap.dedent("""\
            ## Dev Tools
            - **DEFERRED** Some work item. Reason for deferral.
        """))

        result = migrate(ledger, work_items, tracker_root)

        # INV → canonical tier
        canonical_ops = list((tracker_root / "tracker" / ".ops").glob("*.ops"))
        assert len(canonical_ops) == 1

        # WI → workspace tier
        workspace_ops = list((tracker_root / "tracker-workspace" / ".ops").glob("*.ops"))
        assert len(workspace_ops) == 1

        assert result.items_created == 2

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        """Re-running migration skips existing items."""
        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        ledger = tmp_path / "ledger.md"
        ledger.write_text(textwrap.dedent("""\
            ## INV-001: Test Item
            - **Statement:** Something
            - **Status:** FIXED
            - **Root cause:** Bug
            - **Fix:** Fixed it
            - **Pending Generalizations:** None
        """))
        work_items = tmp_path / "work_items.md"
        work_items.write_text("")

        result1 = migrate(ledger, work_items, tracker_root)
        assert result1.items_created == 1
        assert result1.items_skipped == 0

        result2 = migrate(ledger, work_items, tracker_root)
        assert result2.items_created == 0
        assert result2.items_skipped == 1

    def test_dry_run(self, tmp_path: Path) -> None:
        """Dry run reports what would happen without writing."""
        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        ledger = tmp_path / "ledger.md"
        ledger.write_text(textwrap.dedent("""\
            ## INV-001: Test Item
            - **Statement:** Something
            - **Status:** FIXED
            - **Root cause:** Bug
            - **Fix:** Fixed it
            - **Pending Generalizations:** None
        """))
        work_items = tmp_path / "work_items.md"
        work_items.write_text("")

        result = migrate(ledger, work_items, tracker_root, dry_run=True)

        assert result.items_created == 1
        # No files should actually be written in ops dirs
        canonical_ops = list((tracker_root / "tracker" / ".ops").glob("*.ops"))
        assert len(canonical_ops) == 0

    def test_ops_files_are_valid_yaml(self, tmp_path: Path) -> None:
        """Every generated ops file must be parseable and compilable."""
        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        ledger = tmp_path / "ledger.md"
        ledger.write_text(textwrap.dedent("""\
            ## INV-001: Test Item
            - **Statement:** Something true
            - **Status:** FIXED
            - **Root cause:** Bug in code
            - **Fix:** Fixed the bug
            - **Pending Generalizations:** None

            ### META-001: Some Meta
            > "Meta principle here."
            - **Status:** 100%
            - **Pending Generalizations:** None
        """))
        work_items = tmp_path / "work_items.md"
        work_items.write_text(textwrap.dedent("""\
            ## Dev Tools
            - **DONE** (PR#1) A work item done.
        """))

        result = migrate(ledger, work_items, tracker_root)
        assert result.items_created == 3
        assert result.errors == []

        # Validate all ops files
        for ops_path in (tracker_root / "tracker" / ".ops").glob("*.ops"):
            ops = _parse_ops_file(ops_path)
            assert len(ops) == 1
            item_id = ops_path.name[1:-4]  # strip leading . and trailing .ops
            compiled = compile_ops(ops, item_id)
            assert compiled.id == item_id
            assert compiled.kind in ("invariant", "meta_invariant")

        for ops_path in (tracker_root / "tracker-workspace" / ".ops").glob("*.ops"):
            ops = _parse_ops_file(ops_path)
            assert len(ops) == 1
            item_id = ops_path.name[1:-4]
            compiled = compile_ops(ops, item_id)
            assert compiled.id == item_id
            assert compiled.kind == "work_item"

    def test_gitattributes_created(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        ledger = tmp_path / "ledger.md"
        ledger.write_text("")
        work_items = tmp_path / "work_items.md"
        work_items.write_text("")

        migrate(ledger, work_items, tracker_root)

        ga = tracker_root / "tracker" / ".ops" / ".gitattributes"
        assert ga.exists()
        assert "merge=union" in ga.read_text()

    def test_stealth_gitignore_created(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        ledger = tmp_path / "ledger.md"
        ledger.write_text("")
        work_items = tmp_path / "work_items.md"
        work_items.write_text("")

        migrate(ledger, work_items, tracker_root)

        gi = tracker_root / "tracker-workspace" / "stealth" / ".gitignore"
        assert gi.exists()
        assert "*.ops" in gi.read_text()

    def test_result_id_map(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        ledger = tmp_path / "ledger.md"
        ledger.write_text(textwrap.dedent("""\
            ## INV-001: Test Item
            - **Statement:** Something
            - **Status:** FIXED
            - **Root cause:** Bug
            - **Fix:** Fixed it
            - **Pending Generalizations:** None
        """))
        work_items = tmp_path / "work_items.md"
        work_items.write_text("")

        result = migrate(ledger, work_items, tracker_root)
        assert "INV-001" in result.id_map
        assert result.id_map["INV-001"].startswith("INV-")

    def test_result_items_by_kind(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        ledger = tmp_path / "ledger.md"
        ledger.write_text(textwrap.dedent("""\
            ## INV-001: Test Item
            - **Statement:** Something
            - **Status:** FIXED
            - **Root cause:** Bug
            - **Fix:** Fixed it
            - **Pending Generalizations:** None
        """))
        work_items = tmp_path / "work_items.md"
        work_items.write_text(textwrap.dedent("""\
            ## Dev Tools
            - **DEFERRED** Item one. Reason.
            - **DONE** (PR#1) Item two.
        """))

        result = migrate(ledger, work_items, tracker_root)
        assert result.items_by_kind["invariant"] == 1
        assert result.items_by_kind["work_item"] == 2

    def test_nonexistent_ledger_handled(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        work_items = tmp_path / "work_items.md"
        work_items.write_text("")

        result = migrate(
            tmp_path / "nonexistent.md",
            work_items,
            tracker_root,
        )
        assert result.items_created == 0
        assert result.errors == []

    def test_nonexistent_work_items_handled(self, tmp_path: Path) -> None:
        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        ledger = tmp_path / "ledger.md"
        ledger.write_text("")

        result = migrate(
            ledger,
            tmp_path / "nonexistent.md",
            tracker_root,
        )
        assert result.items_created == 0
        assert result.errors == []

    def test_build_op_error_recorded(self, tmp_path: Path) -> None:
        """Errors from build_create_op are recorded, not raised."""
        from unittest.mock import patch

        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        ledger = tmp_path / "ledger.md"
        ledger.write_text(textwrap.dedent("""\
            ## INV-001: Test Item
            - **Statement:** Something
            - **Status:** FIXED
            - **Root cause:** Bug
            - **Fix:** Fixed it
            - **Pending Generalizations:** None
        """))
        work_items = tmp_path / "work_items.md"
        work_items.write_text("")

        with patch(
            "hypergumbo_tracker.migration.build_create_op",
            side_effect=KeyError("bad_kind"),
        ):
            result = migrate(ledger, work_items, tracker_root)

        assert result.items_created == 0
        assert len(result.errors) == 1
        assert "INV-001" in result.errors[0]


class TestIntegrationRealFiles:
    """Integration tests with the actual governance files."""

    @pytest.fixture()
    def real_ledger(self) -> Path:
        p = Path("/home/jgstern_agent/hypergumbo/.agent/invariant-ledger.md")
        if not p.exists():
            pytest.skip("Real ledger not available")
        return p

    @pytest.fixture()
    def real_work_items(self) -> Path:
        p = Path(
            "/home/jgstern_agent/hypergumbo_lab_notebook/guidance_log/work_items.md"
        )
        if not p.exists():
            pytest.skip("Real work items not available")
        return p

    def test_full_migration(
        self,
        tmp_path: Path,
        real_ledger: Path,
        real_work_items: Path,
    ) -> None:
        """Full migration of real governance files."""
        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()

        result = migrate(real_ledger, real_work_items, tracker_root)

        # 15 INV + 4 META + 24 WI = 43 total
        assert result.items_created == 43, (
            f"Expected 43 items, got {result.items_created}. "
            f"By kind: {result.items_by_kind}. Errors: {result.errors}"
        )
        assert result.errors == [], f"Errors: {result.errors}"
        assert result.items_by_kind["invariant"] == 15
        assert result.items_by_kind["meta_invariant"] == 4
        assert result.items_by_kind["work_item"] == 24

        # Verify every ops file is valid
        for tier_dir in ["tracker", "tracker-workspace"]:
            ops_dir = tracker_root / tier_dir / ".ops"
            for ops_path in ops_dir.glob("*.ops"):
                ops = _parse_ops_file(ops_path)
                assert len(ops) == 1, f"Expected 1 op in {ops_path}, got {len(ops)}"
                item_id = ops_path.name[1:-4]
                compiled = compile_ops(ops, item_id)
                assert compiled.title, f"Empty title for {item_id}"
                assert compiled.status in (
                    "todo_hard", "todo_soft", "in_progress",
                    "done", "deferred", "wont_do",
                ), f"Bad status '{compiled.status}' for {item_id}"

    def test_full_migration_idempotent(
        self,
        tmp_path: Path,
        real_ledger: Path,
        real_work_items: Path,
    ) -> None:
        """Re-running full migration skips all existing items."""
        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()

        result1 = migrate(real_ledger, real_work_items, tracker_root)
        result2 = migrate(real_ledger, real_work_items, tracker_root)

        assert result2.items_created == 0
        assert result2.items_skipped == result1.items_created


# ============================================================================
# Phase 4: CLI
# ============================================================================


class TestCLIMigrate:
    """Test the CLI migrate subcommand."""

    def test_cli_migrate_basic(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from hypergumbo_tracker.cli import main

        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        ledger = tmp_path / "ledger.md"
        ledger.write_text(textwrap.dedent("""\
            ## INV-001: Test Item
            - **Statement:** Something
            - **Status:** FIXED
            - **Root cause:** Bug
            - **Fix:** Fixed it
            - **Pending Generalizations:** None
        """))
        work_items = tmp_path / "work_items.md"
        work_items.write_text("")

        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "migrate",
                "--ledger", str(ledger),
                "--work-items", str(work_items),
            ])
        assert exc.value.code == 0

        captured = capsys.readouterr()
        assert "created 1 items" in captured.out

    def test_cli_migrate_dry_run(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from hypergumbo_tracker.cli import main

        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        ledger = tmp_path / "ledger.md"
        ledger.write_text(textwrap.dedent("""\
            ## INV-001: Test Item
            - **Statement:** Something
            - **Status:** FIXED
            - **Root cause:** Bug
            - **Fix:** Fixed it
            - **Pending Generalizations:** None
        """))
        work_items = tmp_path / "work_items.md"
        work_items.write_text("")

        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "migrate",
                "--ledger", str(ledger),
                "--work-items", str(work_items),
                "--dry-run",
            ])
        assert exc.value.code == 0

        captured = capsys.readouterr()
        assert "would create 1 items" in captured.out

    def test_cli_migrate_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from hypergumbo_tracker.cli import main

        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        ledger = tmp_path / "ledger.md"
        ledger.write_text(textwrap.dedent("""\
            ## INV-001: Test Item
            - **Statement:** Something
            - **Status:** FIXED
            - **Root cause:** Bug
            - **Fix:** Fixed it
            - **Pending Generalizations:** None
        """))
        work_items = tmp_path / "work_items.md"
        work_items.write_text("")

        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "--json",
                "migrate",
                "--ledger", str(ledger),
                "--work-items", str(work_items),
            ])
        assert exc.value.code == 0

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["items_created"] == 1
        assert data["items_skipped"] == 0
        assert "invariant" in data["items_by_kind"]

    def test_cli_migrate_with_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from unittest.mock import patch

        from hypergumbo_tracker.cli import main

        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        ledger = tmp_path / "ledger.md"
        ledger.write_text(textwrap.dedent("""\
            ## INV-001: Test Item
            - **Statement:** Something
            - **Status:** FIXED
            - **Root cause:** Bug
            - **Fix:** Fixed it
            - **Pending Generalizations:** None
        """))
        work_items = tmp_path / "work_items.md"
        work_items.write_text("")

        with patch(
            "hypergumbo_tracker.migration.build_create_op",
            side_effect=KeyError("bad_kind"),
        ):
            with pytest.raises(SystemExit) as exc:
                main([
                    "--tracker-root", str(tracker_root),
                    "migrate",
                    "--ledger", str(ledger),
                    "--work-items", str(work_items),
                ])
            assert exc.value.code == 1

        captured = capsys.readouterr()
        assert "ERROR:" in captured.err
