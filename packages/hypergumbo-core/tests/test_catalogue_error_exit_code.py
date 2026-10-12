# SPDX-License-Identifier: AGPL-3.0-or-later
"""A broken catalogue file is a clean exit-2 error naming the file.

The invariant, for every catalogue family a user path can supply: an
unparseable file, a document of the wrong shape (a top-level list where a
mapping belongs) or a section of the wrong type is ``Error: <...path...>`` on
stderr and exit 2 -- never an uncaught exception, and never exit 1, which
``verify-claims`` uses for ``violated``. A CI gate reading exit 1 takes a
crash for a violation, and an error message that does not name the file gives
the reader nothing to open.

The filed repro (row one): ``function_summaries.d/bad.yaml`` holding
``summaries: [unclosed`` crashed ``verify-claims`` with exit 1 and "This is a
bug; re-run with --debug". The taint arm already refused the same input with
exit 2; the summary loader parsed with no error handling, and the
provenance pre-reads (``overlay_declares_community``, the summary-file split
under ``--no-default-overlays``) parsed outside any handler at all, so a
broken I/O overlay crashed the same way before its real load could refuse it.

The structural half: every catalogue loader raises a subclass of
:class:`~hypergumbo_core.yaml_catalogs.CatalogueError`, and ``cli.main`` maps
that one type to exit 2, the way it maps ``SubstrateError``. The tests below
are parametrized over the families and channels so a new channel that forgets
the rule fails here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from hypergumbo_core.cli import main
from hypergumbo_core.function_summaries import (
    clear_summary_cache,
    load_function_summaries,
)
from hypergumbo_core.schema import SCHEMA_VERSION
from hypergumbo_core.yaml_catalogs import CatalogueError

_MAIN = "python:app.py:5-8:main:function"

SYNTAX = "key: [unclosed\n"
SHAPE = "- a\n- b\n"

#: family -> {mode: body}. ``syntax`` and ``shape`` for every family, plus the
#: family's own wrong-typed field.
_BODIES: "dict[str, dict[str, str]]" = {
    "function_summaries": {
        "syntax": "summaries: [unclosed\n",
        "shape": SHAPE,
        "field": "summaries: 5\n",
        "entry": "summaries:\n  - 5\n",
        "entry_field": "summaries:\n  - param_to_return: {}\n",
    },
    "taint_sources": {
        "syntax": SYNTAX, "shape": SHAPE, "field": "sources: 5\n"},
    "taint_sinks": {"syntax": SYNTAX, "shape": SHAPE, "field": "sinks: 5\n"},
    "taint_sanitizers": {
        "syntax": SYNTAX, "shape": SHAPE, "field": "transforms: 5\n"},
    "io_primitives": {
        "syntax": SYNTAX,
        "shape": SHAPE,
        "field": "language: python\nstatus: overlay\nnet_send: 5\n",
        "entry": "language: python\nstatus: overlay\nnet_send:\n  - 5\n",
        "dupkey": "language: python\nstatus: overlay\nstatus: overlay\n",
    },
}

#: (channel, family): how a verify-claims run is told about the file.
_CHANNELS = [
    ("xdg:function_summaries.d", "function_summaries"),
    ("xdg:taint_sources.d", "taint_sources"),
    ("xdg:taint_sinks.d", "taint_sinks"),
    ("xdg:taint_sanitizers.d", "taint_sanitizers"),
    ("flag:--taint-sources", "taint_sources"),
    ("flag:--taint-sinks", "taint_sinks"),
    ("flag:--taint-sanitizers", "taint_sanitizers"),
    ("extra:sources", "taint_sources"),
    ("extra:sinks", "taint_sinks"),
    ("extra:sanitizers", "taint_sanitizers"),
    ("extra:io_primitives", "io_primitives"),
    ("flag:--io-primitives", "io_primitives"),
    ("xdg:io_primitives.d", "io_primitives"),
    ("config", "io_primitives"),
    ("in_repo", "io_primitives"),
]

_CASES = [
    pytest.param(channel, family, mode, id=f"{channel}-{mode}")
    for channel, family in _CHANNELS
    for mode in _BODIES[family]
]


@pytest.fixture(autouse=True)
def _clean() -> "object":
    clear_summary_cache()
    yield
    clear_summary_cache()


def _bmap() -> dict:
    def ext(module: str, name: str, line: int) -> dict:
        return {"src": _MAIN, "dst": f"python:{module}:0-0:{name}:external_symbol",
                "type": "calls", "confidence": 0.5, "line": line,
                "is_resolved": False,
                "dst_ref": {"lang": "python", "module_path": module,
                            "name": name}}

    return {
        "schema_version": SCHEMA_VERSION,
        "nodes": [{"id": _MAIN, "name": "main", "kind": "function",
                   "language": "python", "path": "app.py",
                   "span": {"start_line": 5, "end_line": 8}}],
        "edges": [ext("os", "urandom", 6), ext("mylib", "swallow", 7),
                  ext("file", "write", 8), ext("builtins", "open", 8)],
    }


def _setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, channel: str, body: str,
) -> "tuple[Path, list[str], Path]":
    """Lay out a repo, a survey, a claims file and the broken catalogue file.

    Returns ``(repo, extra argv, path the error must name)``.
    """
    xdg = tmp_path / "xdg"
    (xdg / "hypergumbo").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "import os\nimport mylib\n\n\ndef main():\n    key = os.urandom(32)\n"
        "    mylib.swallow(key)\n"
        "    open(\"out.bin\", \"wb\").write(b\"constant\")\n"
    )
    (tmp_path / "hg.json").write_text(json.dumps(_bmap()))
    bad = tmp_path / "bad.yaml"
    argv: "list[str]" = []
    claims: "dict[str, object]" = {"claims": [{
        "id": "TF-1", "text": "key material never reaches the filesystem",
        "constraint": {"taint_flow": {"source_taint": "key_material",
                                      "prohibited_sink_zone": "host_fs"}},
    }]}
    if channel.startswith("xdg:"):
        bad = xdg / "hypergumbo" / channel[4:] / "bad.yaml"
        bad.parent.mkdir()
    elif channel.startswith("flag:"):
        argv += [channel[5:], str(bad)]
    elif channel.startswith("extra:"):
        claims["extra_catalogs"] = {channel[6:]: [str(bad)]}
    elif channel == "config":
        (xdg / "hypergumbo" / "config.toml").write_text(
            f"io_primitives = [{json.dumps(str(bad))}]\n")
    else:
        assert channel == "in_repo"
        bad = repo / "bad.yaml"
        (repo / ".hypergumbo.toml").write_text('io_primitives = ["bad.yaml"]\n')
        argv.append("--in-repo-catalogues")
    bad.write_text(body)
    (tmp_path / "claims.yaml").write_text(yaml.dump(claims))
    argv += ["--claims", str(tmp_path / "claims.yaml"),
             "--input", str(tmp_path / "hg.json")]
    return repo, argv, bad


def _assert_clean_refusal(rc: int, err: str, bad: Path) -> None:
    assert "This is a bug" not in err, err
    assert "internal error" not in err, err
    assert rc == 2, err
    refusal = [line for line in err.splitlines() if line.startswith("Error:")]
    assert refusal, err
    assert str(bad) in refusal[0], err


def test_the_filed_repro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Row one: ``summaries: [unclosed`` in function_summaries.d."""
    repo, argv, bad = _setup(tmp_path, monkeypatch, "xdg:function_summaries.d",
                             "summaries: [unclosed\n")
    rc = main(["verify-claims", str(repo), *argv])
    _assert_clean_refusal(rc, capsys.readouterr().err, bad)


@pytest.mark.parametrize("no_default_overlays", [False, True],
                         ids=["overlays", "no-default-overlays"])
@pytest.mark.parametrize(("channel", "family", "mode"), _CASES)
def test_verify_claims_refuses_a_broken_catalogue_with_exit_2(
    channel: str, family: str, mode: str, no_default_overlays: bool,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, argv, bad = _setup(tmp_path, monkeypatch, channel,
                             _BODIES[family][mode])
    if no_default_overlays:
        argv.append("--no-default-overlays")
    rc = main(["verify-claims", str(repo), *argv])
    _assert_clean_refusal(rc, capsys.readouterr().err, bad)


@pytest.mark.parametrize("no_default_overlays", [False, True],
                         ids=["overlays", "no-default-overlays"])
@pytest.mark.parametrize("mode", sorted(_BODIES["io_primitives"]))
@pytest.mark.parametrize("channel", ["flag:--io-primitives",
                                     "xdg:io_primitives.d", "config",
                                     "in_repo"])
def test_io_boundaries_refuses_a_broken_overlay_with_exit_2(
    channel: str, mode: str, no_default_overlays: bool,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """io-boundaries shares the overlay loaders and their pre-reads."""
    repo, argv, bad = _setup(tmp_path, monkeypatch, channel,
                             _BODIES["io_primitives"][mode])
    io_argv = [a for a in argv if a != "--claims"
               and not a.endswith("claims.yaml")]
    if no_default_overlays:
        io_argv.append("--no-default-overlays")
    rc = main(["io-boundaries", str(repo), *io_argv])
    _assert_clean_refusal(rc, capsys.readouterr().err, bad)


def test_the_control_a_sound_summary_file_still_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """REACH: the same layout with a sound file is a verdict, not a refusal."""
    repo, argv, _ = _setup(
        tmp_path, monkeypatch, "xdg:function_summaries.d",
        "summaries:\n  - function: mylib.other\n    param_to_return: {0: true}\n",
    )
    rc = main(["verify-claims", str(repo), *argv])
    err = capsys.readouterr().err
    assert rc == 1, err  # the baseline flow stands: violated
    assert "Error:" not in err


class TestTheSummaryLoader:
    """The loader names the file, whatever is wrong with it."""

    @pytest.mark.parametrize("mode", sorted(_BODIES["function_summaries"]))
    def test_it_raises_a_catalogue_error_naming_the_file(
        self, mode: str, tmp_path: Path,
    ) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text(_BODIES["function_summaries"][mode])
        with pytest.raises(CatalogueError, match=re.escape(str(bad))):
            load_function_summaries(tmp_path)

    def test_an_empty_file_and_a_file_without_summaries_are_not_errors(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "empty.yaml").write_text("")
        (tmp_path / "meta.yaml").write_text("provenance: community\n")
        assert load_function_summaries(tmp_path) == {}


# ---------------------------------------------------------------------------
# Analysis-stage families: read during analysis, not by verify-claims
# ---------------------------------------------------------------------------

def _load_framework() -> object:
    from hypergumbo_core.framework_patterns import (
        clear_pattern_cache,
        load_framework_patterns,
    )
    clear_pattern_cache()
    try:
        return load_framework_patterns("acmeweb")
    finally:
        clear_pattern_cache()


def _load_signatures() -> object:
    from hypergumbo_core.library_signatures import load_library_signatures
    load_library_signatures.cache_clear()
    try:
        return load_library_signatures("python")
    finally:
        load_library_signatures.cache_clear()


def _load_dataflow() -> object:
    from hypergumbo_core.dataflow import _config_cache, get_dataflow_config
    _config_cache.clear()
    try:
        return get_dataflow_config("python")
    finally:
        _config_cache.clear()


#: family -> (file stem the loader reads, loader, {mode: body}).
_ANALYSIS: "dict[str, tuple[str, object, dict[str, str]]]" = {
    "frameworks": ("acmeweb", _load_framework, {
        "syntax": SYNTAX, "shape": SHAPE,
        "field": "id: acmeweb\nlanguage: python\npatterns: 5\n",
        "entry": "id: acmeweb\nlanguage: python\npatterns:\n  - 5\n",
    }),
    "library_signatures": ("python", _load_signatures, {
        "syntax": SYNTAX, "shape": SHAPE,
        "field": "language: python\nsignatures: 5\n",
    }),
    "dataflow_patterns": ("python", _load_dataflow, {
        "syntax": SYNTAX, "shape": SHAPE,
        "field": "library_patterns: 5\n",
        "entry": "library_patterns:\n  - 5\n",
    }),
}


@pytest.mark.parametrize(("family", "mode"), [
    pytest.param(family, mode, id=f"{family}-{mode}")
    for family, (_, _, bodies) in _ANALYSIS.items() for mode in bodies
])
def test_an_analysis_stage_loader_refuses_a_broken_user_file(
    family: str, mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refused naming the file, never a raw parser exception (frameworks.d
    crashed ``survey`` with exit 1) and never skipped in silence (a broken
    dataflow_patterns.d file was read as "no extra rows")."""
    stem, load, bodies = _ANALYSIS[family]
    channel = tmp_path / "hypergumbo" / f"{family}.d"
    channel.mkdir(parents=True)
    bad = channel / f"{stem}.yaml"
    bad.write_text(bodies[mode])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with pytest.raises(CatalogueError, match=re.escape(str(bad))):
        load()  # type: ignore[operator]


def test_main_maps_a_catalogue_error_from_any_command_to_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The chokepoint itself: a command that does not catch its own catalogue
    error (here io-boundaries meeting an unparseable overlay in its provenance
    pre-read) is exit 2 with the message, not an internal error."""
    repo, argv, bad = _setup(tmp_path, monkeypatch, "flag:--io-primitives",
                             SYNTAX)
    rc = main(["io-boundaries", str(repo), "--io-primitives", str(bad),
               "--input", str(tmp_path / "hg.json")])
    _assert_clean_refusal(rc, capsys.readouterr().err, bad)


def test_sketch_does_not_swallow_a_catalogue_error_from_its_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``sketch`` runs the analysis itself on a cold cache and swallowed any
    exception from it: a broken frameworks.d file gave a sketch of no analysis
    at exit 0. A catalogue error now reaches the CLI."""
    import hypergumbo_core.cli as cli_mod
    from hypergumbo_core.sketch import generate_sketch

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def f():\n    return 1\n")

    def _broken(*_args: object, **_kwargs: object) -> None:
        raise CatalogueError("framework pattern file /x/flask.yaml: bad")

    monkeypatch.setattr(cli_mod, "run_behavior_map", _broken)
    with pytest.raises(CatalogueError, match=r"/x/flask\.yaml"):
        generate_sketch(repo)
