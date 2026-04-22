# SPDX-License-Identifier: AGPL-3.0-or-later
"""Drift-guard between ``io_primitives/*.yaml`` write-side categories and the
``taint_sinks/*.yaml`` trust-zone catalogs (WI-hizik).

Background
----------
ADR-0017 intentionally separates the two catalogs: io_primitives classifies
syscall-level IO by boundary type (fs_read, fs_write, net_send, ...), while
taint_sinks classifies *trust zones* and is designed to be extended per-project
with local YAML.  The design rationale holds, but it leaves the built-in
first-party catalogs free to drift apart — a new primitive added to
``io_primitives/python.yaml#fs_write`` will be seen by ``io-boundaries`` but
silently missed by ``verify-claims`` if nobody remembers to also list it under
``taint_sinks/host_filesystem.yaml``.

This test pins the current alignment.  For each of the 5 languages that
``taint_sinks`` already declares coverage for, every entry in the io_primitives
write-side catalog must either appear in the matching taint_sinks file or be
grandfathered in ``_taint_sink_drift_baseline.yaml``.

Scope
-----
- Categories: ``fs_write`` (vs ``host_filesystem``) and ``net_send`` (vs
  ``network_send``).
- Languages: the 5 that appear in taint_sinks today — python, rust, go, java,
  and typescript (which uses the shared io_primitives/javascript catalog via
  the ADR-0016 TS→JS catalog alias).
- Direction: io_primitives ⊆ taint_sinks.  The reverse direction (entries in
  taint_sinks without an io_primitives counterpart) is not guarded today; a
  separate item covers that.

Failure modes
-------------
The test fails when current drift grows beyond the baseline.  That happens in
two shapes:

1. Someone adds an entry to ``io_primitives/<lang>.yaml`` for fs_write or
   net_send and forgets to mirror it in taint_sinks.  Fix: add the entry to
   the matching taint_sinks section (preferred), or append it to the baseline
   with a rationale comment.

2. Someone removes an entry from taint_sinks that was matching an
   io_primitives entry.  Fix: put the taint_sinks entry back, or move its
   io_primitives counterpart into the baseline if the removal was deliberate.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

PKG_ROOT = Path(__file__).resolve().parent.parent / "src" / "hypergumbo_core"
IO_DIR = PKG_ROOT / "io_primitives"
SINK_DIR = PKG_ROOT / "taint_sinks"
BASELINE_PATH = PKG_ROOT / "_taint_sink_drift_baseline.yaml"


# (io_primitives category) -> (taint_sinks yaml filename stem)
CATEGORY_TO_SINK_FILE = {
    "fs_write": "host_filesystem",
    "net_send": "network_send",
}

# io_primitives language filename stem -> taint_sinks sinks-section key.
# TypeScript inherits the shared io_primitives/javascript catalog via the
# ADR-0016 catalog alias; taint_sinks uses "typescript" as its section key.
IO_LANG_TO_SINK_LANG = {
    "python": "python",
    "rust": "rust",
    "go": "go",
    "java": "java",
    "javascript": "typescript",
}


def _extract_primitives(entries: list | None) -> set[tuple[str, str, str]]:
    """Flatten YAML entries to ``{(module, name, kind)}``."""
    out: set[tuple[str, str, str]] = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        module = entry.get("module", "")
        for func in entry.get("functions", []) or []:
            out.add((module, func, "function"))
        for method in entry.get("methods", []) or []:
            out.add((module, method, "method"))
        for attr in entry.get("attributes", []) or []:
            out.add((module, attr, "attribute"))
    return out


def _load_io_primitives(io_lang: str, category: str) -> set[tuple[str, str, str]]:
    yaml_path = IO_DIR / f"{io_lang}.yaml"
    data = yaml.safe_load(yaml_path.read_text()) or {}
    return _extract_primitives(data.get(category, []))


def _load_taint_sinks(
    category: str, sink_lang: str,
) -> set[tuple[str, str, str]]:
    sink_filename = CATEGORY_TO_SINK_FILE[category]
    yaml_path = SINK_DIR / f"{sink_filename}.yaml"
    data = yaml.safe_load(yaml_path.read_text()) or {}
    return _extract_primitives(data.get("sinks", {}).get(sink_lang, []))


def _load_baseline() -> dict[str, dict[str, set[tuple[str, str, str]]]]:
    """Return baseline[category][sink_lang] = set of (module, name, kind)."""
    data = yaml.safe_load(BASELINE_PATH.read_text()) or {}
    out: dict[str, dict[str, set[tuple[str, str, str]]]] = {}
    for category, per_lang in data.items():
        out[category] = {}
        for sink_lang, records in (per_lang or {}).items():
            out[category][sink_lang] = {
                (r["module"], r["name"], r["kind"]) for r in (records or [])
            }
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def baseline() -> dict[str, dict[str, set[tuple[str, str, str]]]]:
    return _load_baseline()


@pytest.mark.parametrize("category", sorted(CATEGORY_TO_SINK_FILE))
@pytest.mark.parametrize("io_lang", sorted(IO_LANG_TO_SINK_LANG))
def test_io_primitives_not_in_taint_sinks_stays_within_baseline(
    category: str,
    io_lang: str,
    baseline: dict[str, dict[str, set[tuple[str, str, str]]]],
) -> None:
    """For each guarded (category, language) pair the set of io_primitives
    entries missing from taint_sinks must not exceed the baseline.  Growth
    beyond the baseline means someone added to io_primitives without also
    adding to taint_sinks — fix the taint_sinks entry or append to baseline.
    """
    sink_lang = IO_LANG_TO_SINK_LANG[io_lang]
    io_set = _load_io_primitives(io_lang, category)
    sink_set = _load_taint_sinks(category, sink_lang)
    current_drift = io_set - sink_set
    baseline_drift = baseline.get(category, {}).get(sink_lang, set())
    new_drift = current_drift - baseline_drift

    if new_drift:
        lines = [
            f"Drift grew beyond baseline for {category}[{io_lang} -> "
            f"taint_sinks:{sink_lang}]:",
            f"  {len(new_drift)} new missing entr(y|ies):",
        ]
        for module, name, kind in sorted(new_drift):
            lines.append(f"    - {module}.{name} ({kind})")
        lines.extend([
            "",
            "Fix options (preferred → last resort):",
            f"  1. Add the missing entr(y|ies) to taint_sinks/"
            f"{CATEGORY_TO_SINK_FILE[category]}.yaml#sinks.{sink_lang}.",
            "  2. If the entr(y|ies) are intentionally not taint sinks, "
            "append to _taint_sink_drift_baseline.yaml with a rationale "
            "comment.",
        ])
        raise AssertionError("\n".join(lines))


def test_baseline_file_exists_and_is_parseable() -> None:
    """Fail early with a clear message if the baseline YAML is missing or
    malformed, rather than a cascade of NoneType errors in the parametrized
    tests above.
    """
    assert BASELINE_PATH.is_file(), (
        f"Drift baseline YAML is missing: {BASELINE_PATH}"
    )
    data = yaml.safe_load(BASELINE_PATH.read_text())
    assert isinstance(data, dict), (
        f"Drift baseline root must be a mapping, got {type(data).__name__}"
    )
    for category, per_lang in data.items():
        assert category in CATEGORY_TO_SINK_FILE, (
            f"Unknown category {category!r} in baseline; expected one of "
            f"{sorted(CATEGORY_TO_SINK_FILE)}"
        )
        assert isinstance(per_lang, dict), (
            f"baseline[{category!r}] must be a mapping"
        )


def test_drift_guard_catches_novel_io_primitive(tmp_path: Path) -> None:
    """Negative test: confirm the drift-guard actually catches new misalignment.

    Simulates a scenario where someone adds a new primitive to io_primitives
    without updating taint_sinks, and asserts the guard flags it.  Uses
    in-memory sets so we don't have to mutate the real YAML files.
    """
    # Baseline + current sink set + io set simulating a new unaligned primitive
    baseline_drift: set[tuple[str, str, str]] = {
        ("os", "rmdir", "function"),  # grandfather entry
    }
    sink_set: set[tuple[str, str, str]] = {
        ("builtins", "open", "function"),
    }
    io_set: set[tuple[str, str, str]] = {
        ("os", "rmdir", "function"),            # grandfathered → OK
        ("builtins", "open", "function"),       # aligned → OK
        ("pathlib.Path", "NEW_WRITE", "method"),  # novel drift → should fail
    }

    current_drift = io_set - sink_set
    new_drift = current_drift - baseline_drift
    assert new_drift == {("pathlib.Path", "NEW_WRITE", "method")}, (
        "Expected the novel primitive to show up as new drift beyond the "
        "baseline."
    )
