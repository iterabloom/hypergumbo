# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for scripts/audit-closure-evidence (WI-dafun closure-evidence guard).

The detector is heuristic and advisory; these tests pin its LOGIC on synthetic
cases (the INV-nufob proxy-closure pattern that motivated it, plus the
behavioral-evidence and non-behavioral-statement counter-cases), not the live
corpus count.
"""

import importlib.machinery
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load():
    loader = importlib.machinery.SourceFileLoader(
        "audit_closure_evidence", str(SCRIPTS / "audit-closure-evidence")
    )
    spec = importlib.util.spec_from_loader("audit_closure_evidence", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ace = _load()


# --- signal detectors ------------------------------------------------------


def test_has_proxy_detects_validator_metrics():
    assert ace.has_proxy("self-analysis at 0 verdict_enum violations")
    assert ace.has_proxy("spec_validator reports zero violations")
    assert ace.has_proxy("18 schema violations remain")
    assert not ace.has_proxy("ran verify-claims and saw rc=2 on stderr")


def test_has_behavioral_detects_repro_and_subcommands():
    assert ace.has_behavioral("verify-claims --taint-sources bad.json → rc=2")
    assert ace.has_behavioral("live repro: exit 2 with a traceback on stderr")
    assert ace.has_behavioral("dead-code-maybe now emits the entry")
    assert ace.has_behavioral("```\n$ run --out x\n```")
    assert ace.has_behavioral("4170/5886 -> 0/5886 chains")
    assert not ace.has_behavioral("validator class reports 0 enum violations")


def test_is_behavioral_statement():
    assert ace.is_behavioral_statement(
        "the --taint loader must error, not silently confirm, on bad input"
    )
    assert ace.is_behavioral_statement("CLI must exit 2 on an unknown flag")
    assert ace.is_behavioral_statement("routes writes uninvited artifacts to stdout")
    # A pure schema/structure statement is not behavioral.
    assert not ace.is_behavioral_statement(
        "FrameworkPatternDef is an orphan schema $def with 0 $ref"
    )


# --- flagged_entries / audit ----------------------------------------------


def _item(status="satisfied", statement="", description="", discussion=None):
    return {
        "id": "INV-test-aaaaa-bbbbb-ccccc-ddddd-eeeee-fffff-ggggg",
        "status": status,
        "title": "t",
        "fields": {"statement": statement},
        "description": description,
        "discussion": discussion or [],
    }


def _agent(msg):
    return {"by": "agent", "actor": "a", "at": "2026-06-01T00:00:00Z", "message": msg}


def test_proxy_only_closure_on_behavioral_item_is_flagged():
    """The INV-nufob pattern: behavioral statement + proxy-only closure."""
    item = _item(
        statement="verify-claims --taint-* loader must error, not silently confirm, on bad input",
        discussion=[_agent("Closed: self-analysis at 0 verdict_enum violations; closed by INV-mofih's fix")],
    )
    flagged = ace.flagged_entries(item)
    assert len(flagged) == 1


def test_behavioral_evidence_closure_is_not_flagged():
    item = _item(
        statement="verify-claims --taint-* loader must error, not silently confirm, on bad input",
        discussion=[_agent("Repro: verify-claims --taint-sources bad.json now exits rc=2 with an error on stderr (PR #4152)")],
    )
    assert ace.flagged_entries(item) == []


def test_non_behavioral_statement_with_proxy_is_not_flagged():
    item = _item(
        statement="FrameworkPatternDef is an orphan schema $def with 0 $ref",
        discussion=[_agent("Closed: spec_validator reports zero violations")],
    )
    assert ace.flagged_entries(item) == []


def test_unresolved_item_is_not_flagged():
    item = _item(
        status="violated",
        statement="CLI must exit 2 on bad input",
        discussion=[_agent("self-analysis at 0 verdict_enum violations")],
    )
    assert ace.flagged_entries(item) == []


def test_human_proxy_entry_is_not_flagged():
    item = _item(
        statement="CLI must exit 2 on bad input",
        discussion=[{"by": "human", "actor": "h", "at": "T", "message": "spec_validator at 0 violations looks fine"}],
    )
    assert ace.flagged_entries(item) == []


def test_audit_collects_only_candidates():
    good = _item(
        statement="CLI must exit 2 on bad input",
        discussion=[_agent("repro: rc=2 on stderr")],
    )
    bad = _item(
        statement="run must error on a missing --input file",
        discussion=[_agent("Closed: validation_report clean, 0 enum violations")],
    )
    flags = ace.audit([good, bad])
    assert len(flags) == 1
    assert flags[0][0] is bad
