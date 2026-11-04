# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every taint label the self-catalogue declares has a zone-barrier transform.

WHY THIS GATE EXISTS (WI-pakag, 2026-11-04). The barrier sanitizer
``safety_zones._safety_zone_barrier`` is declared in
``docs/hypergumbo-self-catalog/zone_barrier_sanitizers.yaml`` once PER TAINT
LABEL (``input_taint``), and the sanitizer schema has no wildcard. So a label a
new ``*_sources.yaml`` introduces walks straight through every ``safety_zones``
wrapper into its stdlib call, and the self-audit reports ``cache_write``'s
``Path.write_text`` as a host_fs violation and ``repo_inspect_git``'s
``subprocess.run`` as a subprocess violation. Measured when the
``analysed_repo_content`` label was first added without its transform: 5
host_fs flows and 100 subprocess flows, every one a wrapper internal the
entry-label claims sanitize (lab ``pakag_11042026/v1_prohibited.json``). A
violated-for-the-wrong-reason verdict is the mirror of INV-hotib's
withheld-for-the-wrong-reason one, and nothing paired the two files before
this test.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_DOCS = Path(__file__).resolve().parents[3] / "docs"
_CATALOG = _DOCS / "hypergumbo-self-catalog"
_BARRIER = "hypergumbo_core.safety_zones._safety_zone_barrier"


def _declared_labels() -> set[str]:
    labels: set[str] = set()
    for f in sorted(_CATALOG.glob("*_sources.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        labels.add(str(data["taint_label"]))
    return labels


def _barrier_labels() -> set[str]:
    data = yaml.safe_load(
        (_CATALOG / "zone_barrier_sanitizers.yaml").read_text(encoding="utf-8")
    )
    out: set[str] = set()
    for transform in data["transforms"]:
        if _BARRIER in transform.get("functions", {}).get("python", []):
            out.add(str(transform["input_taint"]))
    return out


def test_every_self_catalogue_label_has_a_zone_barrier_transform() -> None:
    declared = _declared_labels()
    assert declared, "no *_sources.yaml declares a taint_label; the glob is wrong"
    missing = sorted(declared - _barrier_labels())
    assert missing == [], (
        f"taint label(s) without a _safety_zone_barrier transform: {missing}. "
        "Add an input_taint transform to zone_barrier_sanitizers.yaml, or the "
        "label's claims report every wrapper's internal stdlib call as a violation."
    )


def test_no_barrier_transform_names_a_label_nothing_declares() -> None:
    """The reverse: a transform for a label no source declares is dead text
    that would hide a typo in a label's spelling."""
    stale = sorted(_barrier_labels() - _declared_labels())
    assert stale == []
