# SPDX-License-Identifier: AGPL-3.0-or-later
"""An io_primitives catalogue or overlay refuses a key nothing reads (WI-hapon).

Reproduced by the WI-kosup census before this change: an overlay with a
section ``net_sendd:`` or a row key ``function:`` loaded with ZERO primitives
and no message, so its author believed in rows that classified nothing. The
shipped bash template carried exactly this: its ``fs_delete:`` section (rm,
shred) is no boundary, and both rows loaded as nothing.

The refusal goes through ``yaml_catalogs.reject_unknown_keys``, the helper the
taint loaders use since WI-rinaz, so the message and the near-miss hint read
the same in both families.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import (
    IoPrimitiveOverlayError,
    load_catalog,
    load_overlay_catalog,
)

_REPO = Path(__file__).resolve().parents[3]


def _load(tmp_path: Path, body: str) -> None:
    path = tmp_path / "overlay.yaml"
    path.write_text("language: python\n" + body)
    load_overlay_catalog(path)


@pytest.mark.parametrize("body, key, hint", [
    ("net_sendd:\n  - module: x\n    functions: [y]\n", "'net_sendd'", "net_send"),
    ("net_send:\n  - module: x\n    function: [y]\n", "'function'", "functions"),
    ("net_send:\n  - module: x\n    functions: [y]\n    simultanous: true\n",
     "'simultanous'", "simultaneous"),
    ("stdlib_other:\n  - module: x\n    funtions: [y]\n", "'funtions'", "functions"),
    ("module_completeness:\n  - module: x\n    completness: complete\n",
     "'completness'", "completeness"),
])
def test_an_unknown_key_is_refused_by_name_with_a_hint(
    tmp_path: Path, body: str, key: str, hint: str,
) -> None:
    with pytest.raises(IoPrimitiveOverlayError) as exc:
        _load(tmp_path, body)
    assert key in str(exc.value)
    assert f"Did you mean: {hint}" in str(exc.value)


def test_fs_delete_says_where_deletion_lives(tmp_path: Path) -> None:
    with pytest.raises(IoPrimitiveOverlayError, match="removing a file is fs_write"):
        _load(tmp_path, "fs_delete:\n  - module: rm\n    functions: [rm]\n")


def test_every_documented_key_still_loads(tmp_path: Path) -> None:
    """THE CONTROL: the keys an overlay may carry, at every level."""
    _load(tmp_path, (
        "provenance: community\nretrieved: \"2026-10-28\"\n"
        "ambiguous_names: [post]\n"
        "net_send:\n  - module: x\n    functions: [post]\n    notes: n\n"
        "    simultaneous: true\n"
        "net_recv:\n  - module: x\n    functions: [post]\n    simultaneous: true\n"
        "stdlib_other:\n  - module: x\n    functions: [len]\n    notes: n\n"
        "module_completeness:\n  - module: x\n    completeness: complete\n"
        "    retrieved: \"2026-10-28\"\n    notes: n\n"
    ))


@pytest.mark.parametrize("language", [
    "python", "javascript", "go", "rust", "java", "bash", "elixir", "erlang",
    "haskell", "swift", "objc", "c", "cpp", "scala", "kotlin",
])
def test_the_shipped_catalogues_and_default_overlays_load(language: str) -> None:
    load_catalog(language)


def test_the_bash_template_puts_its_destructive_rows_under_fs_write() -> None:
    catalog = load_overlay_catalog(
        _REPO / "docs/templates/bash-io-primitives.template.yaml",
    )
    rows = {(p.module, p.boundary) for p in catalog.primitives}
    assert {("rm", "fs_write"), ("shred", "fs_write")} <= rows
