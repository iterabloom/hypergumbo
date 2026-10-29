# SPDX-License-Identifier: AGPL-3.0-or-later
"""The frameworks, library_signatures, function_summaries and dataflow_patterns
loaders refuse a key nothing reads (WI-hapon, after taint in WI-rinaz and
io_primitives in part 1).

A key nothing reads is a write-only knob: the file reports loaded and the key
does nothing. The census found it live in shipped files -- ``linkers:`` in 101
framework files and ``detect:`` in 3 (no reader), ``module_completeness:
partial`` in 5 library-signature files (no reader), and three framework rules
spelled with ``name_pattern`` / ``usage_call``, which the parser never read, so
they matched nothing. Those keys are deleted; every loader now refuses an
unknown key through ``yaml_catalogs.reject_unknown_keys``, naming it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from hypergumbo_core.dataflow import load_dataflow_config
from hypergumbo_core.framework_patterns import (
    FrameworkPatternDef,
    clear_pattern_cache,
    load_framework_patterns,
)
from hypergumbo_core.yaml_catalogs import CatalogueError

_CORE = Path(__file__).resolve().parents[1] / "src" / "hypergumbo_core"


# --- frameworks --------------------------------------------------------------

@pytest.mark.parametrize("body, key", [
    ("id: fw\nlanguage: python\nlinkers: [http]\npatterns: []\n", "'linkers'"),
    ("id: fw\nlanguage: python\npatterns:\n  - concept: x\n    name_pattern: '^a'\n",
     "'name_pattern'"),
    ("id: fw\nlanguage: python\npatterns:\n  - concept: x\n    usage:\n"
     "      kind: '^call$'\n      nme: '^a$'\n", "'nme'"),
])
def test_a_framework_file_refuses_an_unread_key(
    tmp_path: Path, body: str, key: str,
) -> None:
    (tmp_path / "fw.yaml").write_text(body)
    clear_pattern_cache()
    with patch(
        "hypergumbo_core.framework_patterns.get_frameworks_dir",
        return_value=tmp_path,
    ), pytest.raises(CatalogueError, match=key) as exc:
        load_framework_patterns("fw")
    assert str(tmp_path / "fw.yaml") in str(exc.value)
    clear_pattern_cache()


def test_no_shipped_framework_file_carries_a_key_nothing_reads() -> None:
    for path in sorted((_CORE / "frameworks").glob("*.yaml")):
        FrameworkPatternDef.from_dict(yaml.safe_load(path.read_text()))


def test_the_definition_has_no_linkers_field() -> None:
    assert not hasattr(FrameworkPatternDef.from_dict({}), "linkers")


# --- library_signatures ------------------------------------------------------

def test_a_signature_file_refuses_an_unread_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hypergumbo_core.library_signatures import load_library_signatures

    channel = tmp_path / "hypergumbo" / "library_signatures.d"
    channel.mkdir(parents=True)
    (channel / "go.yaml").write_text(
        "language: go\nsignature:\n  inhouse.Open: inhouse.Handle\n"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    load_library_signatures.cache_clear()
    try:
        with pytest.raises(CatalogueError, match=r"'signature'.*Did you mean: signatures"):
            load_library_signatures("go")
    finally:
        load_library_signatures.cache_clear()


def test_no_shipped_signature_file_declares_module_completeness() -> None:
    for path in sorted((_CORE / "library_signatures").glob("*.yaml")):
        assert "module_completeness" not in yaml.safe_load(path.read_text()), path


# --- function_summaries ------------------------------------------------------

@pytest.mark.parametrize("body, key", [
    ("summary:\n  - function: f\n", "'summary'"),
    ("summaries:\n  - function: f\n    side_efect: true\n", "'side_efect'"),
    ("summaries:\n  - function: f\n    callback:\n      param_index: 0\n"
     "      caller_args: {}\n", "'caller_args'"),
    ("summaries:\n  - function: f\n    sanitizes:\n      '0': {from: a, too: b}\n",
     "'too'"),
])
def test_a_summary_file_refuses_an_unread_key(
    tmp_path: Path, body: str, key: str,
) -> None:
    from hypergumbo_core.function_summaries import _read_summary_file

    path = tmp_path / "python.yaml"
    path.write_text(body)
    with pytest.raises(CatalogueError, match=key):
        _read_summary_file(path)


def test_every_shipped_summary_file_loads() -> None:
    from hypergumbo_core.function_summaries import _read_summary_file

    for path in sorted((_CORE / "function_summaries").glob("*.yaml")):
        _read_summary_file(path)


# --- dataflow_patterns -------------------------------------------------------

@pytest.mark.parametrize("body, key", [
    ("language: python\nassignmnets: []\n", "'assignmnets'"),
    ("language: python\nlibrary_patterns:\n  - match: x\n    acess_mode: write\n",
     "'acess_mode'"),
])
def test_a_dataflow_file_refuses_an_unread_key(
    tmp_path: Path, body: str, key: str,
) -> None:
    path = tmp_path / "python.yaml"
    path.write_text(body)
    with pytest.raises(ValueError, match=key):
        load_dataflow_config(path)


def test_every_shipped_dataflow_file_loads() -> None:
    for path in sorted((_CORE / "dataflow_patterns").glob("*.yaml")):
        load_dataflow_config(path)
