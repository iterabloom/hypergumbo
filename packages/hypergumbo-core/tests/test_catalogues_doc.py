# SPDX-License-Identifier: AGPL-3.0-or-later
"""``docs/CATALOGUES.md`` is generated, complete, and its examples load (WI-lidof).

ADR-0061 ruling 6: one user-facing page covers the four tiers, where each
family's files live, how to add a row to each family, and what each tier can
do to a verdict -- with its registry facts generated so they cannot drift.

Three things are pinned here:
* the committed page is exactly what ``catalogues_doc`` renders (regenerate
  with ``./scripts/generate-catalogues-doc``);
* every family the registry declares extensible has a how-to example, and no
  example names a family that is not extensible;
* every example is a row its family's own loader accepts -- an example that
  does not load teaches a shape the tool refuses.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hypergumbo_core.catalogues_doc import HOW_TO_ADD, render_catalogues_markdown
from hypergumbo_core.yaml_catalogs import YAML_CATALOGS

_DOC = Path(__file__).resolve().parents[3] / "docs" / "CATALOGUES.md"
_EXTENSIBLE = {s.directory for s in YAML_CATALOGS if s.user_channel}


def test_the_committed_page_is_fresh() -> None:
    assert _DOC.read_text(encoding="utf-8") == render_catalogues_markdown(), (
        "docs/CATALOGUES.md is stale: run ./scripts/generate-catalogues-doc"
    )


def test_every_extensible_family_has_exactly_one_example() -> None:
    assert set(HOW_TO_ADD) == _EXTENSIBLE


def test_the_page_names_every_family_and_every_tier() -> None:
    text = render_catalogues_markdown()
    for spec in YAML_CATALOGS:
        assert f"`{spec.directory}`" in text
    for tier in ("built-in", "community", "yours", "in-repo"):
        assert f"**{tier}**" in text


def test_a_declared_but_unread_channel_is_called_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hypergumbo_core.catalogues_doc as doc

    monkeypatch.setattr(doc, "WIRED_CHANNELS",
                        doc.WIRED_CHANNELS - {"taint_sinks.d"})
    assert "`taint_sinks.d/` (declared, NOT read yet)" in \
        doc.render_catalogues_markdown()


@pytest.mark.parametrize("family", sorted(HOW_TO_ADD))
def test_every_example_loads_through_its_family(
    family: str, tmp_path: Path,
) -> None:
    text = HOW_TO_ADD[family]
    data = yaml.safe_load(text)
    assert isinstance(data, dict)
    path = tmp_path / "example.yaml"
    path.write_text(text)
    if family == "io_primitives":
        from hypergumbo_core.io_boundary import load_overlay_catalog
        assert load_overlay_catalog(path).primitives
    elif family == "frameworks":
        from hypergumbo_core.framework_patterns import FrameworkPatternDef
        assert FrameworkPatternDef.from_dict(data).patterns
    elif family == "dataflow_patterns":
        assert data["library_patterns"][0]["access_mode"]
    elif family == "function_summaries":
        from hypergumbo_core.function_summaries import _parse_summary
        assert _parse_summary(data["summaries"][0]).side_effect
    elif family == "library_signatures":
        from hypergumbo_core.library_signatures import _rows_from
        assert _rows_from(path)
    else:
        from hypergumbo_core.taint import load_taint_catalog
        paths = {"taint_sources": ([path], [], []),
                 "taint_sinks": ([], [path], []),
                 "taint_sanitizers": ([], [], [path])}[family]
        catalog = load_taint_catalog(*paths)
        assert catalog._sources or catalog._sinks or catalog._sanitizers
