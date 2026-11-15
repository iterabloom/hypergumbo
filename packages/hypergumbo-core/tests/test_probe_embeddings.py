# SPDX-License-Identifier: AGPL-3.0-or-later
"""The probe texts are embedded once, at build time, not on every sketch.

The config extraction scores each chunk of a repository's config files against
three fixed lists of probe texts (41 answer patterns, 127 big-picture
questions, 12 negative patterns) and the README extraction against a fourth
(19). ``_embedding_data`` ships the embeddings of all four, computed by
``scripts/compute_probe_embeddings.py``. The config extraction used to encode
its three lists with the model on every call all the same: 180 texts per
sketch, 0.7 s on an idle machine and several times that in a loaded test
worker, while the shipped arrays for them had no reader.

What is pinned here:

* a shipped array is used only when it was computed from today's probe texts
  by today's model (a digest of both travels with the arrays) and has the
  dimension the model in hand produces; otherwise the model encodes the
  probes, as before, so a stale array costs time and never a wrong answer;
* the digests in the tree match the texts in the tree, so editing a probe
  list without re-running the script fails here and not silently at runtime;
* with the real model (where it is installed), each shipped array equals what
  the model computes now.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core import _embedding_data as data
from hypergumbo_core import sketch
from hypergumbo_core import sketch_embeddings as se

ALL_PROBES = {text for texts, _attr in se.PROBE_SETS.values() for text in texts}


@pytest.fixture(autouse=True)
def _fresh_probe_cache():
    """Decoded arrays are kept per process; a test that changes what is
    shipped must not see, or leave behind, another test's answer."""
    se._SHIPPED_PROBES.clear()
    yield
    se._SHIPPED_PROBES.clear()


class _Recorder:
    """A stand-in model: a character histogram per text, in ``dim`` columns,
    that records every text it is asked to encode."""

    def __init__(self, dim: int) -> None:
        self.dim = dim
        self.encoded: list[str] = []

    def encode(self, texts: list[str], convert_to_numpy: bool = True) -> object:
        import numpy as np

        self.encoded.extend(texts)
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for ch in text:
                out[row, ord(ch) % self.dim] += 1.0
        return out


def _repo(root: Path) -> Path:
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "1.2.3"\nlicense = "MIT"\n'
        'dependencies = ["requests", "click"]\n'
    )
    (root / "package.json").write_text('{"name": "demo", "version": "1.2.3"}\n')
    (root / "README.md").write_text(
        "# demo\n\nDemo is a command line tool that turns a repository into a map.\n"
        "It reads source files and prints what calls what.\n"
    )
    return root


def _extract_config(root: Path, model: _Recorder, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Run the config extraction with ``model`` and return the probe sets it
    asked for, in order. That list is the reach assertion: a stand-in model's
    scores are meaningless, so whether any line is SELECTED says nothing, but
    the three requests are made only once the chunks have been encoded."""
    asked: list[str] = []
    real = se.probe_embeddings

    def spy(name: str, model_: object, dim: int) -> object:
        asked.append(name)
        return real(name, model_, dim)

    monkeypatch.setattr(se, "_load_embedding_model", lambda: model)
    monkeypatch.setattr(se, "probe_embeddings", spy)
    monkeypatch.setattr(sketch, "_discover_config_files_embedding", lambda _root: set())
    sketch._extract_config_embedding(root)
    return asked


CONFIG_PROBES = ["answer", "bigpic", "negative"]


# --- the digest -------------------------------------------------------------


def test_the_shipped_digests_are_of_the_texts_in_the_tree() -> None:
    """Edit a probe list and this fails until
    ``python scripts/compute_probe_embeddings.py`` has been re-run."""
    assert data.PROBE_MODEL == se._EMBEDDING_MODEL
    assert set(data.PROBE_DIGESTS) == set(se.PROBE_SETS) == {"readme", "answer", "bigpic", "negative"}
    for name, (texts, attr) in se.PROBE_SETS.items():
        assert data.PROBE_DIGESTS[name] == se.probe_digest(se._EMBEDDING_MODEL, texts), name
        assert isinstance(getattr(data, attr), str) and getattr(data, attr), name


def test_the_digest_changes_with_the_model_a_text_and_the_order() -> None:
    base = se.probe_digest("m", ["a", "b"])
    assert base == se.probe_digest("m", ["a", "b"])
    assert len({base, se.probe_digest("n", ["a", "b"]), se.probe_digest("m", ["a", "c"]),
                se.probe_digest("m", ["b", "a"]), se.probe_digest("m", ["ab"]),
                se.probe_digest("m", ["a", "b", ""])}) == 6


def test_each_probe_list_lives_in_one_place() -> None:
    """``sketch`` used to keep its own copy of two of the lists, which the
    generator script did not read."""
    assert not hasattr(sketch, "ANSWER_PATTERNS")
    assert not hasattr(sketch, "BIG_PICTURE_QUESTIONS")


# --- which arrays a caller gets -----------------------------------------------


def test_shipped_arrays_are_unit_rows_of_the_model_dimension() -> None:
    np = pytest.importorskip("numpy")
    for name, (texts, _attr) in se.PROBE_SETS.items():
        arr = se._shipped_probe_embeddings(name)
        assert arr is not None and arr.shape == (len(texts), 768), name
        assert np.allclose(np.linalg.norm(arr, axis=1), 1.0, atol=1e-3), name


def test_the_config_extraction_does_not_encode_the_shipped_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("numpy")
    model = _Recorder(768)
    assert _extract_config(_repo(tmp_path), model, monkeypatch) == CONFIG_PROBES
    assert model.encoded, "the repository's own chunks are still encoded"
    assert not ALL_PROBES & set(model.encoded)


def test_a_model_of_another_dimension_encodes_the_probes_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shipped rows are 768 wide. A model that is not (every stand-in the
    other tests use) cannot be scored against them."""
    pytest.importorskip("numpy")
    model = _Recorder(64)
    assert _extract_config(_repo(tmp_path), model, monkeypatch) == CONFIG_PROBES
    encoded = set(model.encoded)
    for name in CONFIG_PROBES:
        assert set(se.PROBE_SETS[name][0]) <= encoded, name


def test_an_array_computed_from_other_texts_is_not_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("numpy")
    stale = dict(data.PROBE_DIGESTS, answer="0" * 64)
    monkeypatch.setattr(data, "PROBE_DIGESTS", stale)
    assert se._shipped_probe_embeddings("answer") is None
    assert se._shipped_probe_embeddings("bigpic") is not None
    model = _Recorder(768)
    assert _extract_config(_repo(tmp_path), model, monkeypatch) == CONFIG_PROBES
    encoded = set(model.encoded)
    assert set(se.PROBE_SETS["answer"][0]) <= encoded
    assert not set(se.PROBE_SETS["bigpic"][0]) & encoded
    assert not set(se.PROBE_SETS["negative"][0]) & encoded


def test_an_array_computed_by_another_model_is_not_used(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("numpy")
    monkeypatch.setattr(se, "_EMBEDDING_MODEL", "some/other-model")
    for name in se.PROBE_SETS:
        assert se._shipped_probe_embeddings(name) is None, name


def test_data_without_digests_is_not_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ``_embedding_data`` written by an older script says nothing about
    what its arrays were computed from."""
    pytest.importorskip("numpy")
    monkeypatch.delattr(data, "PROBE_DIGESTS")
    assert se._shipped_probe_embeddings("readme") is None


def test_the_readme_extraction_does_not_encode_its_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("numpy")
    model = _Recorder(768)
    monkeypatch.setattr(se, "_load_embedding_model", lambda: model)
    monkeypatch.setattr(se, "_has_sentence_transformers", lambda: True)
    _repo(tmp_path)
    se.extract_readme_description_embedding(tmp_path / "README.md")
    assert model.encoded, "the README's own lines are still encoded"
    assert not ALL_PROBES & set(model.encoded)


def test_the_readme_extraction_with_a_small_model_encodes_its_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("numpy")
    model = _Recorder(64)
    monkeypatch.setattr(se, "_load_embedding_model", lambda: model)
    monkeypatch.setattr(se, "_has_sentence_transformers", lambda: True)
    _repo(tmp_path)
    se.extract_readme_description_embedding(tmp_path / "README.md")
    assert set(se.PROBE_SETS["readme"][0]) <= set(model.encoded)


# --- against the real model, where it is installed ------------------------------


def test_each_shipped_array_is_what_the_model_computes_now() -> None:
    """Runs only where sentence-transformers and the cached weights are (not
    in CI). It is the one check that the arrays belong to the model."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("sentence_transformers")
    try:
        model = se._load_embedding_model()
    except se.EmbeddingsUnavailable:
        pytest.skip("the embedding model is not in the local cache")
    for name, (texts, _attr) in se.PROBE_SETS.items():
        shipped = se._shipped_probe_embeddings(name)
        assert shipped is not None, name
        live = model.encode(texts, convert_to_numpy=True)
        live = live / (np.linalg.norm(live, axis=1, keepdims=True) + 1e-8)
        cosine = (live * shipped).sum(axis=1)
        assert cosine.min() > 0.9999, (name, float(cosine.min()))
