# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0047 ruling 10 (WI-sofov) — the frameworks and dataflow user channels.

RULING 10'S TEST IS "DOES THE FAMILY DESCRIBE THE USER'S WORLD OR THE
LANGUAGE'S", and these two answer it differently, which is why they are wired
differently rather than by one shared rule:

``frameworks`` describes conventions, including in-house ones, and a
``FrameworkPatternDef`` is a COHERENT WHOLE — its detectors, route extraction
and usage patterns are written to agree with each other. So a user file
REPLACES the shipped definition for that framework id rather than merging into
it: half-merging two definitions yields one neither author wrote. That is not
an inconsistency with the io_primitives channel, where rows are keyed by
qualified name and "later wins per row" is well defined.

``dataflow_patterns`` is MIXED and its channel is SECTION-SCOPED. Grammar rows
(node types, field names) are internal by the ``cfg_nodes`` argument — a user
cannot know better than the grammar, and a wrong row silently breaks the CFG,
which silently breaks the taint walk. ``library_patterns`` rows are regexes over
call syntax matched by method name regardless of receiver type, which a user
with an in-house collection type can legitimately extend. So only that section
is read, and the grammar keys in a user file are IGNORED rather than refused —
a user file that also carries them must not break the run, and a test pins that
they have no effect.

BOTH CHANNELS ONLY WIDEN RECOGNITION. Neither can silence a finding, which is
why they ship ahead of the ``function_summaries`` channel: a user-supplied
TERMINATING summary deletes a real finding and needs the
``CAVEAT_USER_SUPPLIED_SANITIZER`` gate before its channel can exist at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.catalogue_home import user_channel_files


@pytest.fixture(autouse=True)
def _clean_caches():
    """Both loaders cache, so a test that writes a user file after another test
    already loaded the language would otherwise read the earlier answer."""
    from hypergumbo_core.dataflow import _config_cache
    from hypergumbo_core.framework_patterns import clear_pattern_cache
    _config_cache.clear()
    clear_pattern_cache()
    yield
    _config_cache.clear()
    clear_pattern_cache()


# ------------------------------------------------------- the shared primitive

def test_the_channel_name_comes_from_the_registry(tmp_path) -> None:
    """One join, not four. A family whose channel is renamed cannot leave one
    loader reading the old directory."""
    env = {"XDG_CONFIG_HOME": str(tmp_path)}
    channel = tmp_path / "hypergumbo" / "frameworks.d"
    channel.mkdir(parents=True)
    (channel / "acme.yaml").write_text("id: acme\n")
    assert [p.name for p in user_channel_files("frameworks", env)] == ["acme.yaml"]


def test_a_family_with_no_channel_answers_no_rather_than_raising(tmp_path) -> None:
    """Asking "has the user extended cfg_nodes" is a fair question with the
    answer "no"; raising would make every caller guard the call."""
    assert user_channel_files("cfg_nodes", {"XDG_CONFIG_HOME": str(tmp_path)}) == []


# ------------------------------------------------------------------ frameworks

def test_an_in_house_framework_is_loaded_from_the_user_channel(
    tmp_path, monkeypatch,
) -> None:
    """THE CASE THIS CHANNEL EXISTS FOR: a convention hypergumbo has never
    heard of."""
    from hypergumbo_core.framework_patterns import load_framework_patterns
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    channel = tmp_path / "hypergumbo" / "frameworks.d"
    channel.mkdir(parents=True)
    (channel / "acmeweb.yaml").write_text(
        "id: acmeweb\nname: Acme Web\nlanguage: python\n"
        "symbol_patterns:\n"
        "  - concept: route\n"
        "    decorators: ['acme.route']\n"
    )
    assert load_framework_patterns("acmeweb") is not None


def test_a_user_definition_replaces_the_shipped_one_whole(
    tmp_path, monkeypatch,
) -> None:
    """Replacement, not merge — and asserted, because the alternative is a
    definition neither author wrote."""
    from hypergumbo_core.framework_patterns import load_framework_patterns
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    shipped = load_framework_patterns("fastapi")
    assert shipped is not None, "fixture wrong: fastapi is not shipped"

    channel = tmp_path / "hypergumbo" / "frameworks.d"
    channel.mkdir(parents=True)
    (channel / "fastapi.yaml").write_text(
        "id: fastapi\nlanguage: ruby\npatterns: []\n"
    )
    from hypergumbo_core.framework_patterns import clear_pattern_cache
    clear_pattern_cache()
    mine = load_framework_patterns("fastapi")
    assert mine is not None
    # Wholesale: the user's file decides BOTH fields, so nothing of the
    # shipped definition survives to be half-merged with it.
    assert mine.language == "ruby"
    assert mine.patterns == []
    assert shipped.patterns, "fixture wrong: shipped fastapi had no patterns"


def test_an_unknown_framework_is_still_none(tmp_path, monkeypatch) -> None:
    """CONTROL. The channel must not turn every unknown id into a hit."""
    from hypergumbo_core.framework_patterns import load_framework_patterns
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert load_framework_patterns("no-such-framework-anywhere") is None


def test_shipped_frameworks_still_load_with_no_user_channel(
    tmp_path, monkeypatch,
) -> None:
    """CONTROL, and the one that would catch the worst regression: an empty or
    absent channel must not shadow the 107 shipped definitions."""
    from hypergumbo_core.framework_patterns import load_framework_patterns
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    assert load_framework_patterns("fastapi") is not None


# ------------------------------------------------------------------- dataflow

def _write_user_dataflow(tmp_path: Path, body: str) -> None:
    channel = tmp_path / "hypergumbo" / "dataflow_patterns.d"
    channel.mkdir(parents=True, exist_ok=True)
    (channel / "python.yaml").write_text(body)


def test_user_library_patterns_are_appended(tmp_path, monkeypatch) -> None:
    from hypergumbo_core.dataflow import get_dataflow_config
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    baseline = len(get_dataflow_config("python").library_patterns)

    from hypergumbo_core.dataflow import _config_cache
    _config_cache.clear()
    _write_user_dataflow(tmp_path, (
        "library_patterns:\n"
        "  - pattern: '\\\\.acme_push\\\\('\n"
        "    access_mode: mutate\n"
    ))
    widened = get_dataflow_config("python").library_patterns
    assert len(widened) == baseline + 1
    assert widened[-1]["access_mode"] == "mutate", "user row must be LAST"


def test_the_grammar_section_of_a_user_file_is_ignored(
    tmp_path, monkeypatch,
) -> None:
    """THE SCOPING, ASSERTED. A user file carrying grammar keys must not have
    them take effect — that is the half of this family that stays internal,
    because a wrong node type silently empties the CFG and therefore the taint
    walk. Ignored rather than refused, so such a file does not break the run."""
    from hypergumbo_core.dataflow import get_dataflow_config
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    shipped = get_dataflow_config("python")
    shipped_assignments = list(shipped.assignments)

    from hypergumbo_core.dataflow import _config_cache
    _config_cache.clear()
    _write_user_dataflow(tmp_path, (
        "assignments:\n"
        "  - node_type: totally_made_up_node\n"
        "    field: nonsense\n"
        "library_patterns:\n"
        "  - pattern: '\\\\.acme_push\\\\('\n"
        "    access_mode: mutate\n"
    ))
    after = get_dataflow_config("python")
    assert after.assignments == shipped_assignments, (
        "a user file's grammar rows took effect; the channel is scoped to "
        "library_patterns and granting the whole file hands over the CFG"
    )
    assert len(after.library_patterns) == len(shipped.library_patterns) + 1


def test_a_user_file_for_another_language_is_not_applied(
    tmp_path, monkeypatch,
) -> None:
    """CONTROL. The channel is per-language by filename; a rust file must not
    widen python."""
    from hypergumbo_core.dataflow import _config_cache, get_dataflow_config
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    baseline = len(get_dataflow_config("python").library_patterns)
    _config_cache.clear()
    channel = tmp_path / "hypergumbo" / "dataflow_patterns.d"
    channel.mkdir(parents=True)
    (channel / "rust.yaml").write_text(
        "library_patterns:\n  - pattern: 'x'\n    access_mode: mutate\n")
    assert len(get_dataflow_config("python").library_patterns) == baseline


def test_a_language_with_no_shipped_config_stays_none(
    tmp_path, monkeypatch,
) -> None:
    """CONTROL on the branch order: the user channel widens an existing config
    and does not manufacture one for a language hypergumbo has no rules for."""
    from hypergumbo_core.dataflow import get_dataflow_config
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert get_dataflow_config("not-a-language") is None
