# SPDX-License-Identifier: AGPL-3.0-or-later
"""The embedding model is an ENHANCEMENT, so its absence must not end the run.

INV-rupid. ``hypergumbo .`` -- the documented quick-start -- exited 1 with an
uncaught ``OSError`` naming ``huggingface.co`` and wrote ZERO bytes of sketch
whenever ``sentence-transformers`` was installed but the model WEIGHTS were not
on disk. Reproduced offline with ``HF_HUB_OFFLINE=1``, so no network was even
attempted; ``hypergumbo survey`` on the same tree under the same environment
exited 0 and wrote its full map.

Two facts about the old shape explain why the existing degradation branches
never fired, and both are pinned below:

1. ``_has_sentence_transformers()`` catches only ``ImportError``, so it answers
   "is the library importable" when the requirement is "are the weights
   available". Library present + weights absent passed the probe.
2. ``_load_st_model_offline_first`` caught the *cached* load's failure and fell
   back to a network-allowing constructor that was itself UNWRAPPED, so an
   offline ``OSError`` propagated out through six unguarded load sites.

The fix keeps the probe honest about what it measures and routes a LOAD failure
into the same "unavailable" answers the ``_has_sentence_transformers()`` guards
already return -- so the degradation is the one the module already knew how to
produce, not a new second opinion.

The positive controls are load-bearing here. A stub whose every construction
raises would make "raises EmbeddingsUnavailable" pass against an implementation
that raises unconditionally, and a test that only asserts degradation would pass
against an implementation that never loads a model at all. Each contract below
is paired with a control proving the two outcomes are distinguishable.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core import sketch as sketch_mod
from hypergumbo_core import sketch_embeddings as se
from hypergumbo_core.sketch_embeddings import EmbeddingsUnavailable


class _StubModel:
    """Stand-in for a SentenceTransformer that can encode."""

    def __init__(self, name: str, **kwargs: object) -> None:
        self.name = name
        self.kwargs = kwargs

    def encode(self, texts, convert_to_numpy=True):
        import numpy as np

        n = 1 if isinstance(texts, str) else len(texts)
        return np.ones((n, 8), dtype="float32")


def _stub_cls(*, cached_ok: bool, download_ok: bool):
    """Build a SentenceTransformer-shaped class with controllable outcomes.

    ``cached_ok`` decides the ``local_files_only=True`` construction;
    ``download_ok`` decides the network-allowing fallback.
    """

    def factory(name, **kwargs):
        if kwargs.get("local_files_only"):
            if not cached_ok:
                raise OSError("no local entry for " + name)
            return _StubModel(name, **kwargs)
        if not download_ok:
            raise OSError(
                "We couldn't connect to 'https://huggingface.co' to load the "
                "files, and couldn't find them in the cached files."
            )
        return _StubModel(name, **kwargs)

    return factory


@pytest.fixture(autouse=True)
def _reset_model_cache():
    """``_load_embedding_model`` memoizes a singleton; tests must not share it."""
    se._cached_embedding_model = None
    yield
    se._cached_embedding_model = None


# ---------------------------------------------------------------------------
# The loader contract, with both controls
# ---------------------------------------------------------------------------


def test_offline_and_uncached_raises_typed_unavailable_not_bare_oserror():
    """The filed crash: neither the cache nor the network can supply weights."""
    with pytest.raises(EmbeddingsUnavailable) as excinfo:
        se._load_st_model_offline_first(
            _stub_cls(cached_ok=False, download_ok=False), "some/model"
        )
    # The original failure is preserved for the operator, not swallowed.
    assert isinstance(excinfo.value.__cause__, OSError)
    assert "huggingface.co" in str(excinfo.value.__cause__)


def test_control_cached_weights_load_without_raising():
    """POSITIVE CONTROL: the normal path still returns a model."""
    model = se._load_st_model_offline_first(
        _stub_cls(cached_ok=True, download_ok=False), "some/model"
    )
    assert isinstance(model, _StubModel)
    assert model.kwargs.get("local_files_only") is True


def test_control_first_install_download_is_still_reached():
    """POSITIVE CONTROL: the one sanctioned runtime fetch must survive the fix.

    Weights absent but network available is the first run after
    ``hypergumbo install-embeddings``. A fix that made the probe refuse whenever
    weights are missing would break exactly this and no other test would notice.
    """
    model = se._load_st_model_offline_first(
        _stub_cls(cached_ok=False, download_ok=True), "some/model"
    )
    assert isinstance(model, _StubModel)
    assert not model.kwargs.get("local_files_only")


# ---------------------------------------------------------------------------
# Every consumer degrades to the answer its own unavailable-branch returns
# ---------------------------------------------------------------------------


def _raise_unavailable(*args: object, **kwargs: object):
    raise EmbeddingsUnavailable("weights unavailable")


def test_readme_extraction_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "_load_embedding_model", _raise_unavailable)
    readme = tmp_path / "README.md"
    readme.write_text("# Proj\n\nProj is a tool that does a thing.\n" * 5)
    assert se.extract_readme_description_embedding(readme) is None


def test_readme_extraction_debug_shape_is_preserved(tmp_path, monkeypatch):
    """debug=True has its own unavailable shape; degradation must honour it."""
    monkeypatch.setattr(se, "_load_embedding_model", _raise_unavailable)
    readme = tmp_path / "README.md"
    readme.write_text("# Proj\n\nProj is a tool that does a thing.\n" * 5)
    result = se.extract_readme_description_embedding(readme, debug=True)
    assert isinstance(result, se.ReadmeExtractionDebug)
    assert result.description is None


def test_embed_file_for_semantic_ranking_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "_load_modernbert_model", _raise_unavailable)
    f = tmp_path / "a.py"
    f.write_text("def f():\n    return 1\n")
    assert se.embed_file_for_semantic_ranking(f) is None


def test_batch_embed_files_returns_none_per_path(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "_load_modernbert_model", _raise_unavailable)
    paths = []
    for i in range(3):
        f = tmp_path / f"m{i}.py"
        f.write_text(f"def f{i}():\n    return {i}\n")
        paths.append(f)
    result = se.batch_embed_files(paths)
    assert set(result) == set(paths)
    assert all(v is None for v in result.values())


def test_additional_files_probes_fall_back_to_zeros(monkeypatch):
    """The runtime-compute branch is reached only when the pre-computed
    constant is absent, so the test has to remove it — patching the model
    alone would never enter the branch under test.
    """
    import numpy as np

    from hypergumbo_core import _embedding_data

    monkeypatch.setattr(se, "_load_modernbert_model", _raise_unavailable)
    monkeypatch.setattr(se, "_ADDITIONAL_FILES_PROBE_EMBEDDINGS", None)
    monkeypatch.delattr(_embedding_data, "ADDITIONAL_FILES_PROBES_B64")
    arr = se._get_additional_files_probe_embeddings()
    assert arr.shape == (
        len(se.ADDITIONAL_FILES_PROBES),
        se._MODERNBERT_TRUNCATE_DIM,
    )
    assert not np.any(arr)


# Near-miss config names: close enough to a known name to clear the 0.15 n-gram
# pre-filter, but not already known — which is what it takes to reach the model
# load at all. A plain "weird.conf" fixture returns set() from an EARLIER exit
# and never touches the loader, so it would pass against no fix whatsoever.
_NEAR_MISS_CONFIG_NAMES = [
    "pyproject.tomlx",
    "requirements.lock",
    "docker-compose.override.yaml",
    "setup.cfg.bak",
    "package.jsonc",
    "tsconfig.base.json",
    "Cargo.toml.orig",
]


def test_config_discovery_returns_empty_set(tmp_path, monkeypatch):
    calls: list[str] = []

    def _counting_raise(*args: object, **kwargs: object):
        calls.append("load")
        raise EmbeddingsUnavailable("weights unavailable")

    monkeypatch.setattr(se, "_load_embedding_model", _counting_raise)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("def f():\n    return 1\n")
    for name in _NEAR_MISS_CONFIG_NAMES:
        (tmp_path / name).write_text("k=v\n")

    assert sketch_mod._discover_config_files_embedding(tmp_path) == set()
    assert calls, (
        "fixture never reached the model load — this assertion would hold "
        "against an unfixed tree"
    )


def test_config_extraction_falls_back_to_the_heuristic_extractor(
    tmp_path, monkeypatch
):
    """NOT an empty list — this function's ImportError arm already prefers the
    heuristic extractor, and degrading to [] would silently drop config
    metadata that pattern matching recovers with no model at all.
    """
    monkeypatch.setattr(se, "_load_embedding_model", _raise_unavailable)
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\nversion='1.0.0'\n"
    )
    result = sketch_mod._extract_config_embedding(tmp_path)
    assert result == sketch_mod._extract_config_heuristic(tmp_path)[:30]
    assert result, "the heuristic arm should still find pyproject metadata"


# ---------------------------------------------------------------------------
# The user-facing contract the crash actually broke
# ---------------------------------------------------------------------------


def test_sketch_still_renders_when_weights_are_unavailable(tmp_path, monkeypatch):
    """INV-rupid's own repro, at the production entry point.

    Before the fix this raised OSError out of ``generate_sketch`` and the CLI
    wrote a zero-byte file. HYBRID is the CLI's default config-extraction mode,
    so this is the shape a real ``hypergumbo .`` takes.
    """
    monkeypatch.setattr(se, "_load_embedding_model", _raise_unavailable)
    monkeypatch.setattr(se, "_load_modernbert_model", _raise_unavailable)

    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'tiny'\nversion = '0.1.0'\n"
    )
    (tmp_path / "README.md").write_text("# tiny\n\ntiny does a small thing.\n")
    pkg = tmp_path / "tiny"
    pkg.mkdir()
    (pkg / "mod.py").write_text(
        "import os\n\n\ndef read_conf(path):\n"
        "    with open(path) as fh:\n        return fh.read()\n"
    )

    sketch = sketch_mod.generate_sketch(
        tmp_path,
        max_tokens=2000,
        config_extraction_mode=sketch_mod.ConfigExtractionMode.HYBRID,
    )
    assert isinstance(sketch, str)
    assert sketch.strip(), "sketch must not be empty when embeddings are absent"
    assert "tiny" in sketch


def test_control_the_sketch_degradation_path_is_actually_reached(
    tmp_path, monkeypatch
):
    """POSITIVE CONTROL for the test above, and the one that matters.

    The risk is not that ``generate_sketch`` returns something — it is that it
    returns something because the embedding path was never entered on this
    fixture, in which case the degradation assertion proves nothing about the
    fix. Counting loader calls settles it directly: if the count is zero the
    preceding test is vacuous regardless of how green it looks.
    """
    calls: list[str] = []

    def _counting_raise(*args: object, **kwargs: object):
        calls.append("load")
        raise EmbeddingsUnavailable("weights unavailable")

    monkeypatch.setattr(se, "_load_embedding_model", _counting_raise)
    monkeypatch.setattr(se, "_load_modernbert_model", _counting_raise)

    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'tiny'\nversion = '0.1.0'\n"
    )
    (tmp_path / "README.md").write_text("# tiny\n\ntiny does a small thing.\n")
    pkg = tmp_path / "tiny"
    pkg.mkdir()
    (pkg / "mod.py").write_text("def f():\n    return 1\n")

    sketch = sketch_mod.generate_sketch(
        tmp_path,
        max_tokens=2000,
        config_extraction_mode=sketch_mod.ConfigExtractionMode.HYBRID,
    )
    assert calls, (
        "no model load was attempted — the degradation test above never "
        "exercised the code path it claims to cover"
    )
    assert sketch.strip()


# ---------------------------------------------------------------------------
# The disclosure itself is a contract, not a courtesy
# ---------------------------------------------------------------------------


def test_warns_once_per_process_naming_cause_and_remedy(monkeypatch):
    """A silent degradation is its own defect.

    The user asked for a sketch and got one with a section missing; if nothing
    says why, the omission is indistinguishable from the tool having found
    nothing to say. The warning must therefore name the CAUSE (so the failure
    is diagnosable) and the REMEDY (so it is fixable) — and fire once, because
    six consumers degrade independently and six copies would bury the output
    they are attached to.
    """
    monkeypatch.setattr(se, "_EMBEDDINGS_UNAVAILABLE_WARNED", False)

    with pytest.warns(UserWarning) as record:
        se._warn_embeddings_unavailable(OSError("could not reach huggingface.co"))
        se._warn_embeddings_unavailable(OSError("could not reach huggingface.co"))

    assert len(record) == 1, "the warning must not repeat per consumer"
    message = str(record[0].message)
    assert "could not reach huggingface.co" in message, "cause not named"
    assert "install-embeddings" in message, "remedy not named"


def test_control_the_once_flag_is_what_suppresses_the_second_warning(
    monkeypatch,
):
    """POSITIVE CONTROL: prove the single warning above is deduplication and
    not simply a code path that warns at most once by construction.
    """
    monkeypatch.setattr(se, "_EMBEDDINGS_UNAVAILABLE_WARNED", False)
    with pytest.warns(UserWarning) as first:
        se._warn_embeddings_unavailable(OSError("boom"))
    monkeypatch.setattr(se, "_EMBEDDINGS_UNAVAILABLE_WARNED", False)
    with pytest.warns(UserWarning) as second:
        se._warn_embeddings_unavailable(OSError("boom"))
    assert len(first) == 1 and len(second) == 1
