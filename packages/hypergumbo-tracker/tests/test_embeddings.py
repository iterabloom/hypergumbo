# SPDX-License-Identifier: MPL-2.0
"""Tests for hypergumbo_tracker.embeddings.

Covers pure-function utilities (cosine similarity, semantic tags, item text
extraction), dependency detection, model management (download, directory
discovery), and — when dedup deps are installed — real embedding computation
and duplicate detection.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hypergumbo_tracker.embeddings import (
    EmbeddingDuplicateResult,
    EmbeddingModel,
    SEMANTIC_PROBES,
    _COSINE_SIMILARITY_THRESHOLD,
    _TAG_SIMILARITY_THRESHOLD,
    _cosine_similarity,
    _get_model_dir,
    _item_text_for_embedding,
    check_embedding_duplicates,
    derive_semantic_tags,
    download_model,
    is_dedup_available,
    is_model_downloaded,
)
from hypergumbo_tracker.models import CompiledItem


def _has_dedup_deps() -> bool:
    """Check if onnxruntime and tokenizers are importable."""
    try:
        import onnxruntime
        import tokenizers

        del onnxruntime, tokenizers
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Cosine similarity (pure Python, always runs)
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors(self) -> None:
        v = [1.0, 0.0, 0.0]
        assert _cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self) -> None:
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert _cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self) -> None:
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert _cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_zero_vector_a(self) -> None:
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_zero_vector_b(self) -> None:
        a = [1.0, 2.0, 3.0]
        b = [0.0, 0.0, 0.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_both_zero(self) -> None:
        assert _cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0

    def test_arbitrary_vectors(self) -> None:
        a = [1.0, 2.0, 3.0]
        b = [4.0, 5.0, 6.0]
        dot = 1 * 4 + 2 * 5 + 3 * 6
        norm_a = math.sqrt(1 + 4 + 9)
        norm_b = math.sqrt(16 + 25 + 36)
        expected = dot / (norm_a * norm_b)
        assert _cosine_similarity(a, b) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Semantic tags (pure function, synthetic vectors)
# ---------------------------------------------------------------------------


class TestDeriveSemanticTags:
    def test_high_similarity_included(self) -> None:
        # Create a unit vector and probes that are very similar
        embedding = [1.0, 0.0, 0.0]
        probes = {
            "call_graph": [0.9, 0.1, 0.0],  # high similarity
            "test_coverage": [0.0, 0.0, 1.0],  # orthogonal
        }
        tags = derive_semantic_tags(embedding, probes)
        assert "call_graph" in tags
        assert "test_coverage" not in tags

    def test_low_similarity_excluded(self) -> None:
        embedding = [1.0, 0.0, 0.0]
        probes = {
            "performance": [0.0, 1.0, 0.0],  # orthogonal
            "documentation": [0.0, 0.0, 1.0],  # orthogonal
        }
        tags = derive_semantic_tags(embedding, probes)
        assert tags == []

    def test_sorted_output(self) -> None:
        # Both probes similar to embedding
        embedding = [1.0, 1.0, 0.0]
        probes = {
            "zebra": [1.0, 0.0, 0.0],
            "alpha": [0.0, 1.0, 0.0],
        }
        tags = derive_semantic_tags(embedding, probes)
        assert tags == sorted(tags)

    def test_threshold_boundary(self) -> None:
        # Construct vectors where cosine sim is exactly at threshold
        # cos(theta) = 0.3 → embed = [0.3, sqrt(1-0.09)], probe = [1, 0]
        embedding = [_TAG_SIMILARITY_THRESHOLD, math.sqrt(1 - _TAG_SIMILARITY_THRESHOLD**2)]
        probes = {"boundary": [1.0, 0.0]}
        tags = derive_semantic_tags(embedding, probes)
        assert "boundary" in tags

    def test_empty_probes(self) -> None:
        embedding = [1.0, 0.0, 0.0]
        tags = derive_semantic_tags(embedding, {})
        assert tags == []


# ---------------------------------------------------------------------------
# Item text extraction
# ---------------------------------------------------------------------------


class TestItemTextForEmbedding:
    def test_basic_extraction(self) -> None:
        item = CompiledItem(
            id="WI-a", kind="work_item", title="Fix the bug",
            status="todo_hard", description="Auth module has a bug",
        )
        text = _item_text_for_embedding(item)
        assert "Fix the bug" in text
        assert "Auth module has a bug" in text

    def test_fields_included(self) -> None:
        item = CompiledItem(
            id="WI-a", kind="work_item", title="Title",
            status="todo_hard", description="Desc",
            fields={"statement": "invariant text", "tags_list": ["tag1", "tag2"]},
        )
        text = _item_text_for_embedding(item)
        assert "invariant text" in text
        assert "tag1" in text
        assert "tag2" in text

    def test_empty_fields(self) -> None:
        item = CompiledItem(
            id="WI-a", kind="work_item", title="Title",
            status="todo_hard",
        )
        text = _item_text_for_embedding(item)
        assert "Title" in text

    def test_fields_not_dict(self) -> None:
        item = CompiledItem(
            id="WI-a", kind="work_item", title="Title",
            status="todo_hard",
        )
        # Force fields to a non-dict value for edge case
        object.__setattr__(item, "fields", "not a dict")
        text = _item_text_for_embedding(item)
        assert "Title" in text


# ---------------------------------------------------------------------------
# Dependency detection
# ---------------------------------------------------------------------------


class TestIsDedupAvailable:
    def test_available_when_deps_exist(self) -> None:
        with patch.dict("sys.modules", {"onnxruntime": MagicMock(), "tokenizers": MagicMock()}):
            # Need to reload since function uses try/import
            from hypergumbo_tracker import embeddings
            result = embeddings.is_dedup_available()
            assert result is True

    def test_unavailable_when_deps_missing(self) -> None:
        import sys
        with patch.dict(sys.modules, {"onnxruntime": None}):
            from hypergumbo_tracker import embeddings
            result = embeddings.is_dedup_available()
            assert result is False


# ---------------------------------------------------------------------------
# Model directory
# ---------------------------------------------------------------------------


class TestGetModelDir:
    def test_xdg_cache_home_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        d = _get_model_dir()
        assert d == tmp_path / "hypergumbo-tracker" / "models" / "modernbert-embed-base"

    def test_default_home_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        d = _get_model_dir()
        expected = Path.home() / ".cache" / "hypergumbo-tracker" / "models" / "modernbert-embed-base"
        assert d == expected


# ---------------------------------------------------------------------------
# Model download state
# ---------------------------------------------------------------------------


class TestIsModelDownloaded:
    def test_both_files_present(self, tmp_path: Path) -> None:
        (tmp_path / "model_q4f16.onnx").touch()
        (tmp_path / "tokenizer.json").touch()
        assert is_model_downloaded(tmp_path) is True

    def test_model_missing(self, tmp_path: Path) -> None:
        (tmp_path / "tokenizer.json").touch()
        assert is_model_downloaded(tmp_path) is False

    def test_tokenizer_missing(self, tmp_path: Path) -> None:
        (tmp_path / "model_q4f16.onnx").touch()
        assert is_model_downloaded(tmp_path) is False

    def test_empty_directory(self, tmp_path: Path) -> None:
        assert is_model_downloaded(tmp_path) is False


# ---------------------------------------------------------------------------
# Model download (mocked urllib)
# ---------------------------------------------------------------------------


class TestDownloadModel:
    def test_downloads_files(self, tmp_path: Path) -> None:
        model_dir = tmp_path / "models"

        def fake_urlretrieve(url: str, dest: str) -> tuple[str, Any]:
            Path(dest).write_text("fake data")
            return dest, None

        with patch("hypergumbo_tracker.embeddings.urllib.request.urlretrieve", side_effect=fake_urlretrieve):
            result = download_model(model_dir=model_dir)

        assert result == model_dir
        assert (model_dir / "model_q4f16.onnx").is_file()
        assert (model_dir / "tokenizer.json").is_file()

    def test_skips_existing(self, tmp_path: Path) -> None:
        model_dir = tmp_path / "models"
        model_dir.mkdir(parents=True)
        (model_dir / "model_q4f16.onnx").write_text("existing")
        (model_dir / "tokenizer.json").write_text("existing")

        with patch("hypergumbo_tracker.embeddings.urllib.request.urlretrieve") as mock_retrieve:
            download_model(model_dir=model_dir)
            mock_retrieve.assert_not_called()

    def test_force_redownloads(self, tmp_path: Path) -> None:
        model_dir = tmp_path / "models"
        model_dir.mkdir(parents=True)
        (model_dir / "model_q4f16.onnx").write_text("old")
        (model_dir / "tokenizer.json").write_text("old")

        def fake_urlretrieve(url: str, dest: str) -> tuple[str, Any]:
            Path(dest).write_text("new data")
            return dest, None

        with patch("hypergumbo_tracker.embeddings.urllib.request.urlretrieve", side_effect=fake_urlretrieve):
            download_model(model_dir=model_dir, force=True)

        assert (model_dir / "model_q4f16.onnx").read_text() == "new data"
        assert (model_dir / "tokenizer.json").read_text() == "new data"

    def test_atomic_rename(self, tmp_path: Path) -> None:
        """Verify download uses .tmp then renames (no partial files)."""
        model_dir = tmp_path / "models"
        rename_targets: list[str] = []

        def fake_urlretrieve(url: str, dest: str) -> tuple[str, Any]:
            Path(dest).write_text("data")
            rename_targets.append(dest)
            return dest, None

        with patch("hypergumbo_tracker.embeddings.urllib.request.urlretrieve", side_effect=fake_urlretrieve):
            download_model(model_dir=model_dir)

        # Both downloads went to .tmp files
        assert all(t.endswith(".tmp") for t in rename_targets)


# ---------------------------------------------------------------------------
# EmbeddingModel (mocked at ONNX/tokenizer level)
# ---------------------------------------------------------------------------


class TestEmbeddingModelFileNotFound:
    @pytest.mark.skipif(not _has_dedup_deps(), reason="onnxruntime/tokenizers not installed")
    def test_raises_when_no_files(self, tmp_path: Path) -> None:
        model = EmbeddingModel(model_dir=tmp_path)
        with pytest.raises(FileNotFoundError, match="Model files not found"):
            model._load()


class TestEmbeddingModelMocked:
    """Tests for EmbeddingModel — some always runnable, some need deps."""

    def test_lazy_load(self, tmp_path: Path) -> None:
        model = EmbeddingModel(model_dir=tmp_path)
        assert model._session is None
        assert model._tokenizer is None

    @pytest.mark.skipif(not _has_dedup_deps(), reason="onnxruntime/tokenizers not installed")
    def test_embed_returns_correct_shape(self, tmp_path: Path) -> None:
        """Directly inject mock session/tokenizer to verify embed() pipeline."""
        import numpy as np

        model = EmbeddingModel(model_dir=tmp_path)

        # Mock tokenizer
        mock_encoding = MagicMock()
        mock_encoding.ids = [1, 2, 3]
        mock_encoding.attention_mask = [1, 1, 1]

        mock_tokenizer = MagicMock()
        mock_tokenizer.encode.return_value = mock_encoding

        # Mock ONNX session — return random 768-dim embeddings
        mock_session = MagicMock()
        fake_output = np.random.randn(1, 3, 768).astype(np.float32)
        mock_session.run.return_value = [fake_output]

        # Directly inject mocks (bypass _load)
        model._session = mock_session
        model._tokenizer = mock_tokenizer

        result = model.embed(["test text"])

        assert len(result) == 1
        assert len(result[0]) == 768

        # Verify L2 normalization (should be unit vector)
        norm = math.sqrt(sum(x * x for x in result[0]))
        assert norm == pytest.approx(1.0, abs=1e-5)


# ---------------------------------------------------------------------------
# check_embedding_duplicates (mocked — always runnable)
# ---------------------------------------------------------------------------


class TestCheckEmbeddingDuplicatesMocked:
    def test_returns_empty_when_deps_unavailable(self) -> None:
        items: dict[str, tuple[str, Any]] = {
            "WI-a": ("canonical", CompiledItem(
                id="WI-a", kind="work_item", title="Test",
                status="todo_hard",
            )),
        }
        with patch("hypergumbo_tracker.embeddings.is_dedup_available", return_value=False):
            with pytest.warns(UserWarning, match="Embedding-based dedup unavailable"):
                result = check_embedding_duplicates(items)
        assert result == []

    def test_empty_items(self) -> None:
        with patch("hypergumbo_tracker.embeddings.is_dedup_available", return_value=True):
            result = check_embedding_duplicates({})
        assert result == []

    def test_identical_items_flagged(self) -> None:
        items: dict[str, tuple[str, Any]] = {
            "WI-a": ("canonical", CompiledItem(
                id="WI-a", kind="work_item",
                title="Fix the login bug in authentication",
                status="todo_hard",
                description="The auth module login flow has a bug",
            )),
            "WI-b": ("canonical", CompiledItem(
                id="WI-b", kind="work_item",
                title="Fix the login bug in authentication",
                status="todo_hard",
                description="The auth module login flow has a bug",
            )),
        }

        # Mock model to return identical embeddings
        mock_model = MagicMock(spec=EmbeddingModel)
        # Return identical unit vectors for items, and arbitrary for probes
        unit_vec = [1.0] + [0.0] * 767

        def embed_side_effect(texts: list[str]) -> list[list[float]]:
            return [unit_vec for _ in texts]

        mock_model.embed.side_effect = embed_side_effect

        with patch("hypergumbo_tracker.embeddings.is_dedup_available", return_value=True):
            result = check_embedding_duplicates(items, model=mock_model)

        assert len(result) == 1
        assert result[0].id_a == "WI-a"
        assert result[0].id_b == "WI-b"
        assert result[0].cosine_sim == pytest.approx(1.0)

    def test_different_items_not_flagged(self) -> None:
        items: dict[str, tuple[str, Any]] = {
            "WI-a": ("canonical", CompiledItem(
                id="WI-a", kind="work_item", title="Fix auth",
                status="todo_hard",
            )),
            "WI-b": ("canonical", CompiledItem(
                id="WI-b", kind="work_item", title="Add database migration",
                status="todo_hard",
            )),
        }

        # Mock model to return orthogonal embeddings
        mock_model = MagicMock(spec=EmbeddingModel)
        call_count = [0]

        def embed_side_effect(texts: list[str]) -> list[list[float]]:
            result = []
            for _ in texts:
                vec = [0.0] * 768
                vec[call_count[0] % 768] = 1.0
                call_count[0] += 1
                result.append(vec)
            return result

        mock_model.embed.side_effect = embed_side_effect

        with patch("hypergumbo_tracker.embeddings.is_dedup_available", return_value=True):
            result = check_embedding_duplicates(items, model=mock_model)

        assert result == []

    def test_not_duplicate_of_respected(self) -> None:
        items: dict[str, tuple[str, Any]] = {
            "WI-a": ("canonical", CompiledItem(
                id="WI-a", kind="work_item", title="Same title",
                status="todo_hard", not_duplicate_of=["WI-b"],
            )),
            "WI-b": ("canonical", CompiledItem(
                id="WI-b", kind="work_item", title="Same title",
                status="todo_hard",
            )),
        }

        mock_model = MagicMock(spec=EmbeddingModel)
        unit_vec = [1.0] + [0.0] * 767
        mock_model.embed.side_effect = lambda texts: [unit_vec for _ in texts]

        with patch("hypergumbo_tracker.embeddings.is_dedup_available", return_value=True):
            result = check_embedding_duplicates(items, model=mock_model)

        assert result == []

    def test_duplicate_of_respected(self) -> None:
        items: dict[str, tuple[str, Any]] = {
            "WI-a": ("canonical", CompiledItem(
                id="WI-a", kind="work_item", title="Same title",
                status="todo_hard", duplicate_of=["WI-b"],
            )),
            "WI-b": ("canonical", CompiledItem(
                id="WI-b", kind="work_item", title="Same title",
                status="todo_hard",
            )),
        }

        mock_model = MagicMock(spec=EmbeddingModel)
        unit_vec = [1.0] + [0.0] * 767
        mock_model.embed.side_effect = lambda texts: [unit_vec for _ in texts]

        with patch("hypergumbo_tracker.embeddings.is_dedup_available", return_value=True):
            result = check_embedding_duplicates(items, model=mock_model)

        assert result == []

    def test_shared_tags_reported(self) -> None:
        items: dict[str, tuple[str, Any]] = {
            "WI-a": ("canonical", CompiledItem(
                id="WI-a", kind="work_item", title="Same",
                status="todo_hard",
            )),
            "WI-b": ("canonical", CompiledItem(
                id="WI-b", kind="work_item", title="Same",
                status="todo_hard",
            )),
        }

        mock_model = MagicMock(spec=EmbeddingModel)
        # Return identical unit vectors — will have identical tags
        unit_vec = [1.0] + [0.0] * 767
        mock_model.embed.side_effect = lambda texts: [unit_vec for _ in texts]

        with patch("hypergumbo_tracker.embeddings.is_dedup_available", return_value=True):
            result = check_embedding_duplicates(items, model=mock_model)

        assert len(result) == 1
        assert isinstance(result[0].shared_tags, list)

    def test_auto_downloads_model(self) -> None:
        """When model is not provided and not downloaded, auto-download."""
        items: dict[str, tuple[str, Any]] = {
            "WI-a": ("canonical", CompiledItem(
                id="WI-a", kind="work_item", title="Test",
                status="todo_hard",
            )),
        }

        mock_model_instance = MagicMock(spec=EmbeddingModel)
        unit_vec = [1.0] + [0.0] * 767
        mock_model_instance.embed.side_effect = lambda texts: [unit_vec for _ in texts]

        with patch("hypergumbo_tracker.embeddings.is_dedup_available", return_value=True), \
             patch("hypergumbo_tracker.embeddings.is_model_downloaded", return_value=False), \
             patch("hypergumbo_tracker.embeddings.download_model") as mock_download, \
             patch("hypergumbo_tracker.embeddings.EmbeddingModel", return_value=mock_model_instance):
            check_embedding_duplicates(items)
            mock_download.assert_called_once()


# ---------------------------------------------------------------------------
# EmbeddingDuplicateResult dataclass
# ---------------------------------------------------------------------------


class TestEmbeddingDuplicateResult:
    def test_fields(self) -> None:
        r = EmbeddingDuplicateResult(
            id_a="WI-a", id_b="WI-b",
            cosine_sim=0.92, shared_tags=["call_graph", "performance"],
        )
        assert r.id_a == "WI-a"
        assert r.id_b == "WI-b"
        assert r.cosine_sim == 0.92
        assert r.shared_tags == ["call_graph", "performance"]


# ---------------------------------------------------------------------------
# SEMANTIC_PROBES constant
# ---------------------------------------------------------------------------


class TestSemanticProbes:
    def test_has_expected_count(self) -> None:
        assert len(SEMANTIC_PROBES) == 10

    def test_all_values_are_strings(self) -> None:
        for key, val in SEMANTIC_PROBES.items():
            assert isinstance(key, str)
            assert isinstance(val, str)
            assert len(val) > 0
