# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-gujus: no runtime subcommand downloads an embedding model.

``runtime-cli-no-network`` says embeddings downloads happen only through the
install path, never from analysis. It was false: ``install-embeddings`` (and
``add-extras``, and ``pip install 'hypergumbo[embeddings]'``) installed only the
library, and ``_load_st_model_offline_first`` fell back to a network-allowed
``SentenceTransformer(name)`` when the model was not cached, so the first
``sketch`` fetched ``microsoft/unixcoder-base`` and
``nomic-ai/modernbert-embed-base`` from huggingface.co. Measured in the
2026-10-25 premise check with a stub ``sentence_transformers``: every load was a
``local_files_only=True`` attempt FOLLOWED BY a download-allowed retry.

The claim is kept, not restated: the runtime loader is cache-only and degrades
with the remedy named, and the install path fetches the weights. A pip extra
cannot fetch weights, so for that path the degradation warning is the remedy.
"""
from __future__ import annotations

import argparse
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from hypergumbo_core import cli, sketch_embeddings
from hypergumbo_core.sketch_embeddings import (
    EMBEDDING_MODELS,
    EmbeddingsUnavailable,
    _load_st_model_offline_first,
    fetch_embedding_models,
)


def test_the_runtime_loader_never_retries_with_the_network() -> None:
    st_cls = MagicMock(side_effect=OSError("not cached"))
    with pytest.raises(EmbeddingsUnavailable, match="install-embeddings"):
        _load_st_model_offline_first(st_cls, "m", truncate_dim=8)
    assert st_cls.call_count == 1
    assert st_cls.call_args.kwargs.get("local_files_only") is True


def test_a_value_error_cache_miss_also_degrades() -> None:
    st_cls = MagicMock(side_effect=ValueError("no local entry"))
    with pytest.raises(EmbeddingsUnavailable):
        _load_st_model_offline_first(st_cls, "m")
    assert st_cls.call_count == 1


def test_a_failed_cached_load_restores_the_callers_offline_setting(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    with pytest.raises(EmbeddingsUnavailable):
        _load_st_model_offline_first(MagicMock(side_effect=OSError()), "m")
    assert "HF_HUB_OFFLINE" not in __import__("os").environ


def test_the_warning_names_a_remedy_that_fetches_the_weights() -> None:
    sketch_embeddings._EMBEDDINGS_UNAVAILABLE_WARNED = False
    try:
        with pytest.warns(UserWarning) as rec:
            sketch_embeddings._warn_embeddings_unavailable(OSError("x"))
    finally:
        sketch_embeddings._EMBEDDINGS_UNAVAILABLE_WARNED = False
    text = str(rec[0].message)
    assert "hypergumbo install-embeddings" in text
    assert "downloads" in text and "never" in text


def test_the_install_path_fetches_both_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    seen: list[tuple[str, dict, object]] = []

    def st_cls(name: str, **kw: object) -> object:
        import os
        seen.append((name, dict(kw), os.environ.get("HF_HUB_OFFLINE")))
        return object()

    monkeypatch.setattr(sketch_embeddings, "_hf_model_is_cached", lambda name: False)
    fetch_embedding_models(st_cls=st_cls)
    assert [s[0] for s in seen] == list(EMBEDDING_MODELS)
    assert all("local_files_only" not in s[1] and s[2] is None for s in seen)


def test_a_cached_model_is_not_fetched_again(monkeypatch: pytest.MonkeyPatch) -> None:
    st_cls = MagicMock()
    monkeypatch.setattr(sketch_embeddings, "_hf_model_is_cached", lambda name: True)
    fetch_embedding_models(st_cls=st_cls)
    st_cls.assert_not_called()


def _args(**kw: bool) -> argparse.Namespace:
    return argparse.Namespace(check=kw.get("check", False), quiet=kw.get("quiet", True))


def _fetch_calls(run: MagicMock) -> list[list[str]]:
    return [c.args[0] for c in run.call_args_list
            if "fetch_embedding_models" in " ".join(c.args[0])]


def test_install_embeddings_fetches_the_weights_after_pip() -> None:
    ok = MagicMock(returncode=0)
    with patch.object(cli, "_is_embeddings_available", return_value=False), \
            patch.object(cli, "_embedding_models_cached", return_value=False), \
            patch.object(subprocess, "run", return_value=ok) as run:
        assert cli.cmd_install_embeddings(_args()) == 0
    assert len(_fetch_calls(run)) == 1


def test_an_already_installed_library_still_gets_its_weights() -> None:
    ok = MagicMock(returncode=0)
    with patch.object(cli, "_is_embeddings_available", return_value=True), \
            patch.object(cli, "_get_embeddings_version", return_value="5.7.0"), \
            patch.object(cli, "_embedding_models_cached", return_value=False), \
            patch.object(subprocess, "run", return_value=ok) as run:
        assert cli.cmd_install_embeddings(_args()) == 0
    (cmd,) = [c.args[0] for c in run.call_args_list]
    assert "fetch_embedding_models" in " ".join(cmd)


def test_a_failed_fetch_fails_the_install(capsys: pytest.CaptureFixture[str]) -> None:
    def run(cmd, **kw):
        return MagicMock(returncode=1 if "fetch_embedding_models" in " ".join(cmd) else 0)

    with patch.object(cli, "_is_embeddings_available", return_value=False), \
            patch.object(cli, "_embedding_models_cached", return_value=False), \
            patch.object(subprocess, "run", side_effect=run):
        assert cli.cmd_install_embeddings(_args()) == 1
    assert "model" in capsys.readouterr().err


def test_add_extras_embeddings_also_fetches_the_weights() -> None:
    ok = MagicMock(returncode=0)
    with patch.object(cli, "_embedding_models_cached", return_value=False), \
            patch.object(subprocess, "run", return_value=ok) as run:
        assert cli._install_embeddings_impl(quiet=True) is True
    assert len(_fetch_calls(run)) == 1


def test_a_fetch_that_cannot_start_is_a_failure(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(cli, "_embedding_models_cached", return_value=False), \
            patch.object(subprocess, "run", side_effect=OSError("no interpreter")):
        assert cli._fetch_embedding_models(quiet=False) is False
    captured = capsys.readouterr()
    assert "Downloading the embedding model weights" in captured.out
    assert "no interpreter" in captured.err


def test_the_cache_probe_asks_about_every_model(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []
    monkeypatch.setattr(sketch_embeddings, "_hf_model_is_cached",
                        lambda name: asked.append(name) or True)
    assert cli._embedding_models_cached() is True
    assert asked == list(EMBEDDING_MODELS)


def test_add_extras_fails_when_the_weights_cannot_be_fetched() -> None:
    ok = MagicMock(returncode=0)
    with patch.object(cli, "_fetch_embedding_models", return_value=False), \
            patch.object(subprocess, "run", return_value=ok):
        assert cli._install_embeddings_impl(quiet=True) is False


def test_check_fails_when_the_library_is_there_but_the_weights_are_not(
        capsys: pytest.CaptureFixture[str]) -> None:
    """``--check`` reports a working install, and without the weights analysis
    now degrades, so the library alone is not one."""
    with patch.object(cli, "_is_embeddings_available", return_value=True), \
            patch.object(cli, "_get_embeddings_version", return_value="5.7.0"), \
            patch.object(cli, "_embedding_models_cached", return_value=False):
        assert cli.cmd_install_embeddings(_args(check=True)) == 1
    assert "weights have not been downloaded" in capsys.readouterr().out


def test_the_child_imports_the_fetcher_by_its_own_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``-c`` program is built from the function object, so it names the
    function that exists rather than a string that can drift from it; and
    the reference is what lets the test-only-reachability gate see that the
    install path reaches ``fetch_embedding_models`` (it went red on the
    string-only spelling). Run the generated program against a recorder."""
    ok = MagicMock(returncode=0)
    with patch.object(cli, "_embedding_models_cached", return_value=False), \
            patch.object(subprocess, "run", return_value=ok) as run:
        assert cli._fetch_embedding_models(quiet=True) is True
    (cmd,) = [c.args[0] for c in run.call_args_list]
    assert cmd[1] == "-c"
    # Built from the real function; run it against a recorder in its place.
    calls: list[str] = []
    monkeypatch.setattr(
        sketch_embeddings, "fetch_embedding_models", lambda: calls.append("fetched"),
    )
    exec(cmd[2], {})  # noqa: S102 - the program the child would run
    assert calls == ["fetched"]
