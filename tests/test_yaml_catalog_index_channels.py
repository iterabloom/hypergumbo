# SPDX-License-Identifier: AGPL-3.0-or-later
"""The catalog index surfaces each family's ADR-0047 ruling-7 answer.

Ruling 7 makes ``scripts/yaml-catalog-index --check`` the gate that "refuses a
family that declares a channel it does not have". The registry holds the
answer; this script is where an operator reads it and where CI enforces it, so
both the rendering and the exit code are pinned here.
"""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "yaml-catalog-index"


def _load():
    """Import the extensionless script as a module.

    ``spec_from_file_location`` cannot infer a loader for a file with no
    ``.py`` suffix, so the loader is named explicitly.
    """
    loader = SourceFileLoader("yaml_catalog_index", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class _Spec:
    def __init__(self, user_channel, channel_scope=None, channel_gated=None):
        self.user_channel = user_channel
        self.channel_scope = channel_scope
        self.channel_gated = channel_gated


def test_channel_cell_renders_each_shape():
    cell = _load()._channel_cell
    assert cell(_Spec(None)) == "internal"
    assert cell(_Spec("io_primitives.d")) == "io_primitives.d"
    assert cell(_Spec("dataflow_patterns.d", channel_scope="library_patterns")) \
        == "dataflow_patterns.d [library_patterns only]"
    assert cell(_Spec("function_summaries.d", channel_gated="CAVEAT_X")) \
        == "function_summaries.d (gated)"


def test_check_mode_passes_on_the_shipped_tree():
    assert _load().main(["--check"]) == 0


def test_json_mode_carries_the_channel_fields(capsys):
    import json

    assert _load().main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    by_dir = {row["directory"]: row for row in payload}
    assert by_dir["io_primitives"]["user_channel"] == "io_primitives.d"
    assert by_dir["cfg_nodes"]["user_channel"] is None
    assert by_dir["cfg_nodes"]["no_channel_reason"]
    assert by_dir["dataflow_patterns"]["channel_scope"] == "library_patterns"
    assert by_dir["function_summaries"]["channel_gated"] == (
        "CAVEAT_USER_SUPPLIED_SANITIZER"
    )


def test_table_mode_names_the_scoped_and_gated_families(capsys):
    assert _load().main([]) == 0
    out = capsys.readouterr().out
    assert "User channel: 6 of 9 families" in out
    assert "internal: io_primitives_overlays, cfg_nodes, url_folding" in out
    assert "limited to the 'library_patterns' section" in out
    assert "ride CAVEAT_USER_SUPPLIED_SANITIZER" in out


def test_check_mode_fails_when_a_family_contradicts_itself(monkeypatch):
    """The gate ruling 7 relies on actually refuses, rather than warning."""
    mod = _load()
    from hypergumbo_core.yaml_catalogs import CatalogSpec, validate_registry

    bad = (CatalogSpec(
        directory="x", purpose="p", loader="hypergumbo_core.cli", adr=None,
        user_channel="x.d", no_channel_reason="also internal",
    ),)
    monkeypatch.setattr(
        mod, "validate_registry", lambda: validate_registry(catalogs=bad),
    )
    assert mod.main(["--check"]) == 1


@pytest.mark.parametrize("directory", ["cfg_nodes", "url_folding"])
def test_internal_families_state_a_reason_a_human_can_read(directory):
    from hypergumbo_core.yaml_catalogs import YAML_CATALOGS

    spec = next(s for s in YAML_CATALOGS if s.directory == directory)
    assert spec.user_channel is None
    # Not merely truthy: a one-word reason is not an answer.
    assert len(spec.no_channel_reason.split()) >= 10
