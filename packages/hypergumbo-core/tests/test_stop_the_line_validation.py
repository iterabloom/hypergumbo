"""TEMPORARY: ADR-0010 validation test - DELETE AFTER VALIDATION COMPLETE.

This test intentionally fails in the full-suite workflow to verify the
stop-the-line protocol works correctly. See ADR-0010 Validation Plan.

The test passes locally and in fast-CI, but fails in full-suite.yml.
"""
import os


def test_intentional_failure_for_adr0010_validation():
    """Intentional failure to validate stop-the-line protocol.

    TEMPORARY: Remove this file after ADR-0010 validation is complete.

    This test checks if we're running in the "Full Test Suite" workflow.
    If so, it fails intentionally to trigger stop-the-line.
    """
    if os.environ.get("GITHUB_ACTIONS") and os.environ.get("GITHUB_WORKFLOW") == "Full Test Suite":
        raise AssertionError(
            "ADR-0010 validation: intentional full-suite failure. "
            "This is expected - see ADR-0010 validation plan."
        )
