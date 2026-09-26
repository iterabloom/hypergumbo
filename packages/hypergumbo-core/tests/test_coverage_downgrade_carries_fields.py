# SPDX-License-Identifier: AGPL-3.0-or-later
"""A verdict the coverage gate downgrades keeps every field it had.

Found by WI-simiv (2026-09-26). ``_require_coverage_to_confirm`` rebuilt the
verdict field by field, and the rebuild omitted fields. The ``inconclusive``
branch dropped ``sanitized_flows``, ``excluded_flows``,
``resource_naming_flows``, ``flow_origins``, ``analysis_methods`` and
``analysis_fidelity``, and the qualified branch dropped ``analysis_fidelity``.
On the production path a Fernet decrypt -> encrypt -> write program printed
"1 flow(s) reach that zone but pass through a sanitizer on every route" in
``details`` and ``"sanitized_flows": 0`` beside it: the sentence and the
machine field disagreed, on exactly the verdicts least certain.

The test builds a verdict with every field set to a non-default value, so a
field the gate forgets is caught by name rather than by a count.
"""

from __future__ import annotations

import dataclasses

import pytest

from hypergumbo_core.verify_claims import ClaimVerdict, _require_coverage_to_confirm


def _full() -> ClaimVerdict:
    return ClaimVerdict(
        claim_id="C", claim_text="t", verdict="confirmed", evidence_count=2,
        details="clean.", evidence=[{"x": 1}], excluded_flows={"test_sourced": 3},
        flow_origins={"ddg": 1}, analysis_methods={"ddg": 1},
        resource_naming_flows=4, sanitized_flows=5,
        caveats=[{"kind": "untyped_receiver", "entries": ["a"]}],
        analysis_fidelity={"python": ["python-ast-v1"]},
    )


_CHANGED = {"verdict", "details", "caveats"}


@pytest.mark.parametrize("opaque", [None, ["subprocess.run"]], ids=["withheld", "qualified"])
def test_every_field_survives_the_downgrade(opaque: list[str] | None) -> None:
    before = _full()
    after = _require_coverage_to_confirm(before, "a reason", opaque)
    assert after.verdict != "confirmed"
    lost = [
        f.name for f in dataclasses.fields(ClaimVerdict)
        if f.name not in _CHANGED and getattr(after, f.name) != getattr(before, f.name)
    ]
    assert lost == []
