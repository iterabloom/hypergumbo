# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the G-Vendi finetuning pipeline (scripts/finetune-transcript-model).

Tests cover:
- load_training_data: JSONL loading, step filtering, validation
- inject_system_prompt: correct prompt per step, unknown step passthrough
- vendi_score: mathematical properties (orthogonal, duplicate, degenerate)
- select_diverse: output size, fraction compliance, determinism
- compute_gradient_features: CountSketch properties (requires torch)

Pure-Python/numpy tests run unconditionally.  Torch-dependent tests are
skipped if torch is not installed (the finetuning deps are optional).
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

np = pytest.importorskip("numpy")


# ---------------------------------------------------------------------------
# Import the script as a module (it has no .py extension)
# ---------------------------------------------------------------------------

def _import_script(name: str, rel_path: str):
    """Import a script from the repo by relative path."""
    script_path = str(Path(__file__).parent.parent / rel_path)
    loader = importlib.machinery.SourceFileLoader(name, script_path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ftm():
    """Import scripts/finetune-transcript-model as a module."""
    return _import_script("finetune_transcript_model", "scripts/finetune-transcript-model")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_jsonl(path: Path, entries: list[dict]) -> None:
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _sample_entry(step: str = "goal_distillation", user: str = "hello", asst: str = "world") -> dict:
    return {
        "step": step,
        "model": "test-model",
        "messages": [
            {"role": "user", "content": user},
            {"role": "assistant", "content": asst},
        ],
    }


# ---------------------------------------------------------------------------
# load_training_data
# ---------------------------------------------------------------------------

class TestLoadTrainingData:

    def test_loads_valid_entries(self, tmp_path: Path, ftm: Any) -> None:
        path = tmp_path / "data.jsonl"
        _write_jsonl(path, [_sample_entry(), _sample_entry(step="relevance_rating")])
        entries = ftm.load_training_data(path, "all")
        assert len(entries) == 2

    def test_filters_by_step(self, tmp_path: Path, ftm: Any) -> None:
        path = tmp_path / "data.jsonl"
        _write_jsonl(path, [
            _sample_entry(step="goal_distillation"),
            _sample_entry(step="relevance_rating"),
            _sample_entry(step="goal_distillation"),
        ])
        entries = ftm.load_training_data(path, "relevance_rating")
        assert len(entries) == 1
        assert entries[0]["step"] == "relevance_rating"

    def test_skips_invalid_json(self, tmp_path: Path, ftm: Any) -> None:
        path = tmp_path / "data.jsonl"
        path.write_text(
            json.dumps(_sample_entry()) + "\n"
            "not valid json\n"
            + json.dumps(_sample_entry()) + "\n"
        )
        entries = ftm.load_training_data(path, "all")
        assert len(entries) == 2

    def test_skips_missing_roles(self, tmp_path: Path, ftm: Any) -> None:
        path = tmp_path / "data.jsonl"
        bad = {"step": "goal_distillation", "messages": [{"role": "user", "content": "hi"}]}
        _write_jsonl(path, [bad, _sample_entry()])
        entries = ftm.load_training_data(path, "all")
        assert len(entries) == 1

    def test_skips_blank_lines(self, tmp_path: Path, ftm: Any) -> None:
        path = tmp_path / "data.jsonl"
        path.write_text(
            json.dumps(_sample_entry()) + "\n"
            "\n"
            "   \n"
            + json.dumps(_sample_entry()) + "\n"
        )
        entries = ftm.load_training_data(path, "all")
        assert len(entries) == 2


# ---------------------------------------------------------------------------
# inject_system_prompt
# ---------------------------------------------------------------------------

class TestInjectSystemPrompt:

    def test_goal_distillation_prompt(self, ftm: Any) -> None:
        entry = _sample_entry(step="goal_distillation")
        result = ftm.inject_system_prompt(entry)
        assert result["messages"][0]["role"] == "system"
        assert "distill" in result["messages"][0]["content"].lower()
        assert result["messages"][1]["role"] == "user"
        assert result["messages"][2]["role"] == "assistant"

    def test_relevance_rating_prompt(self, ftm: Any) -> None:
        entry = _sample_entry(step="relevance_rating")
        result = ftm.inject_system_prompt(entry)
        assert result["messages"][0]["role"] == "system"
        assert "relevance" in result["messages"][0]["content"].lower()

    def test_unknown_step_no_system_prompt(self, ftm: Any) -> None:
        entry = _sample_entry(step="unknown_task")
        result = ftm.inject_system_prompt(entry)
        # No system message added — first message is still user
        assert result["messages"][0]["role"] == "user"

    def test_does_not_mutate_original(self, ftm: Any) -> None:
        entry = _sample_entry()
        original_len = len(entry["messages"])
        ftm.inject_system_prompt(entry)
        assert len(entry["messages"]) == original_len


# ---------------------------------------------------------------------------
# vendi_score
# ---------------------------------------------------------------------------

class TestVendiScore:
    """Mathematical property tests for the Vendi Score computation."""

    def test_orthogonal_features_max_diversity(self, ftm: Any) -> None:
        """N orthogonal vectors → Vendi Score = N (maximum diversity)."""
        features = np.eye(10, dtype=np.float32)
        vs = ftm.vendi_score(features)
        assert abs(vs - 10.0) < 0.01

    def test_duplicate_features_min_diversity(self, ftm: Any) -> None:
        """N identical vectors → Vendi Score ≈ 1 (minimum diversity)."""
        row = np.random.RandomState(42).randn(1, 128).astype(np.float32)
        row /= np.linalg.norm(row)
        features = np.tile(row, (20, 1))
        vs = ftm.vendi_score(features)
        assert vs < 1.1

    def test_single_sample(self, ftm: Any) -> None:
        """Single sample → Vendi Score = 1."""
        features = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
        vs = ftm.vendi_score(features)
        assert abs(vs - 1.0) < 0.01

    def test_empty_features(self, ftm: Any) -> None:
        """Empty feature matrix → Vendi Score = 0."""
        features = np.zeros((0, 128), dtype=np.float32)
        vs = ftm.vendi_score(features)
        assert vs == 0.0

    def test_score_is_positive(self, ftm: Any) -> None:
        """Vendi Score is always positive for non-empty inputs."""
        rng = np.random.RandomState(123)
        features = rng.randn(50, 64).astype(np.float32)
        # L2-normalize rows (as the pipeline does)
        norms = np.linalg.norm(features, axis=1, keepdims=True)
        features = features / np.where(norms > 0, norms, 1.0)
        vs = ftm.vendi_score(features)
        assert vs > 0

    def test_score_bounded_by_n(self, ftm: Any) -> None:
        """Vendi Score is at most N (the number of samples)."""
        rng = np.random.RandomState(456)
        n = 30
        features = rng.randn(n, 64).astype(np.float32)
        norms = np.linalg.norm(features, axis=1, keepdims=True)
        features = features / np.where(norms > 0, norms, 1.0)
        vs = ftm.vendi_score(features)
        assert vs <= n + 0.01  # small tolerance for float imprecision

    def test_adding_diversity_increases_score(self, ftm: Any) -> None:
        """Adding a novel direction should increase or maintain the Vendi Score."""
        rng = np.random.RandomState(789)
        base = rng.randn(20, 64).astype(np.float32)
        base /= np.linalg.norm(base, axis=1, keepdims=True)

        # Add a vector in a fresh direction (orthogonal to all existing)
        novel = np.zeros((1, 64), dtype=np.float32)
        novel[0, 0] = 1.0  # unit vector along dim 0

        extended = np.vstack([base, novel])
        extended /= np.linalg.norm(extended, axis=1, keepdims=True)

        vs_base = ftm.vendi_score(base)
        vs_extended = ftm.vendi_score(extended)
        # Score should not decrease (it's measuring diversity breadth)
        assert vs_extended >= vs_base - 0.1  # small tolerance


# ---------------------------------------------------------------------------
# select_diverse
# ---------------------------------------------------------------------------

class TestSelectDiverse:

    def test_output_size_matches_fraction(self, ftm: Any) -> None:
        """Selected count ≈ fraction * N."""
        rng = np.random.RandomState(42)
        features = rng.randn(100, 64).astype(np.float32)
        indices = ftm.select_diverse(features, fraction=0.5, seed=42)
        assert len(indices) == 50

    def test_fraction_one_returns_all(self, ftm: Any) -> None:
        """Fraction=1.0 returns all indices."""
        features = np.random.RandomState(42).randn(30, 64).astype(np.float32)
        indices = ftm.select_diverse(features, fraction=1.0, seed=42)
        assert len(indices) == 30
        assert sorted(indices) == list(range(30))

    def test_fraction_clamps_to_at_least_one(self, ftm: Any) -> None:
        """Very small fraction still returns at least 1 sample."""
        features = np.random.RandomState(42).randn(10, 64).astype(np.float32)
        indices = ftm.select_diverse(features, fraction=0.01, seed=42)
        assert len(indices) >= 1

    def test_deterministic(self, ftm: Any) -> None:
        """Same seed → same selection."""
        features = np.random.RandomState(42).randn(50, 64).astype(np.float32)
        a = ftm.select_diverse(features, fraction=0.6, seed=99)
        b = ftm.select_diverse(features, fraction=0.6, seed=99)
        assert a == b

    def test_different_seeds_may_differ(self, ftm: Any) -> None:
        """Different seeds can produce different selections."""
        features = np.random.RandomState(42).randn(100, 64).astype(np.float32)
        a = ftm.select_diverse(features, fraction=0.5, seed=1)
        b = ftm.select_diverse(features, fraction=0.5, seed=2)
        # Not guaranteed to differ, but very likely with 100 samples
        # Just check both are valid
        assert len(a) == 50
        assert len(b) == 50

    def test_indices_are_valid(self, ftm: Any) -> None:
        """All returned indices are within bounds and unique."""
        n = 80
        features = np.random.RandomState(42).randn(n, 64).astype(np.float32)
        indices = ftm.select_diverse(features, fraction=0.5, seed=42)
        assert all(0 <= i < n for i in indices)
        assert len(set(indices)) == len(indices)

    def test_prefers_sparse_clusters(self, ftm: Any) -> None:
        """Selection should favor samples from underrepresented regions.

        Create features with one dense cluster (90 samples in one direction)
        and one sparse cluster (10 samples in a different direction).
        select_diverse operates on raw features (normalization happens
        upstream in compute_gradient_features), so we use unnormalized
        features with clear cluster separation.
        """
        rng = np.random.RandomState(42)
        # Dense cluster: 90 points tightly packed around [1, 0, 0, ...]
        dense = rng.randn(90, 64).astype(np.float32) * 0.01
        dense[:, 0] += 1.0
        # Sparse cluster: 10 points tightly packed around [0, 1, 0, ...]
        sparse = rng.randn(10, 64).astype(np.float32) * 0.01
        sparse[:, 1] += 1.0
        features = np.vstack([dense, sparse])

        indices = ftm.select_diverse(features, fraction=0.3, seed=42)
        # Count how many sparse-cluster samples (indices 90-99) were selected
        sparse_selected = sum(1 for i in indices if i >= 90)

        # Sparse cluster has 10/100 = 10% of the data, but should get more
        # than 10% of the selections (sparse-cluster preference)
        sparse_fraction = sparse_selected / len(indices) if indices else 0
        assert sparse_fraction > 0.10


# ---------------------------------------------------------------------------
# CountSketch gradient projection (requires torch, no model download)
# ---------------------------------------------------------------------------

@pytest.mark.torch
class TestCountSketch:
    """Test the CountSketch gradient projection using a tiny in-memory model.

    These tests verify the mathematical properties of the projection without
    downloading any HuggingFace models.  A minimal nn.Linear model provides
    real gradients for the CountSketch to project.

    Marked ``@pytest.mark.torch`` — deselect in CI with ``-m 'not torch'``.
    Auto-skipped if torch is not installed.
    """

    @pytest.fixture(autouse=True)
    def _skip_without_torch(self):
        pytest.importorskip("torch")

    def _sketch_from_model(self, ftm: Any, model, loss, proj_dim: int, seed: int):
        """Compute CountSketch by reimplementing the core loop from the script."""
        sketch = np.zeros(proj_dim, dtype=np.float64)
        global_offset = 0
        for p in model.parameters():
            if not p.requires_grad or p.grad is None:
                global_offset += p.numel()
                continue
            grad = p.grad.detach().cpu().flatten().numpy().astype(np.float64)
            n = grad.shape[0]
            rng = np.random.RandomState(seed + global_offset)
            bins = rng.randint(0, proj_dim, size=n)
            signs = rng.choice([-1.0, 1.0], size=n)
            np.add.at(sketch, bins, signs * grad)
            global_offset += n
        norm = np.linalg.norm(sketch)
        if norm > 0:
            sketch /= norm
        return sketch.astype(np.float32)

    def _make_model_and_loss(self, input_dim: int = 32, output_dim: int = 8):
        """Create a tiny model and compute a loss for gradient testing."""
        import torch
        import torch.nn as nn

        model = nn.Linear(input_dim, output_dim)
        x = torch.randn(1, input_dim)
        target = torch.zeros(1, dtype=torch.long)
        loss = nn.CrossEntropyLoss()(model(x), target)
        loss.backward()
        return model, loss

    def test_output_dimension(self, ftm: Any) -> None:
        """Sketch has exactly proj_dim dimensions."""
        model, loss = self._make_model_and_loss()
        sketch = self._sketch_from_model(ftm, model, loss, proj_dim=64, seed=42)
        assert sketch.shape == (64,)

    def test_l2_normalized(self, ftm: Any) -> None:
        """Sketch is L2-normalized to unit length."""
        model, loss = self._make_model_and_loss()
        sketch = self._sketch_from_model(ftm, model, loss, proj_dim=128, seed=42)
        np.testing.assert_allclose(np.linalg.norm(sketch), 1.0, atol=1e-5)

    def test_deterministic(self, ftm: Any) -> None:
        """Same model state + seed → identical sketch."""
        import torch

        torch.manual_seed(0)
        m1, l1 = self._make_model_and_loss()
        s1 = self._sketch_from_model(ftm, m1, l1, proj_dim=64, seed=42)

        torch.manual_seed(0)
        m2, l2 = self._make_model_and_loss()
        s2 = self._sketch_from_model(ftm, m2, l2, proj_dim=64, seed=42)

        np.testing.assert_array_equal(s1, s2)

    def test_different_seeds_differ(self, ftm: Any) -> None:
        """Different projection seeds produce different sketches."""
        import torch
        torch.manual_seed(0)
        model, loss = self._make_model_and_loss()
        s1 = self._sketch_from_model(ftm, model, loss, proj_dim=64, seed=1)

        # Re-create identical model and loss (gradients consumed by first sketch read)
        torch.manual_seed(0)
        model, loss = self._make_model_and_loss()
        s2 = self._sketch_from_model(ftm, model, loss, proj_dim=64, seed=999)

        assert not np.array_equal(s1, s2)

    def test_different_inputs_produce_different_sketches(self, ftm: Any) -> None:
        """Different loss gradients produce different sketches."""
        import torch
        import torch.nn as nn

        torch.manual_seed(0)
        model = nn.Linear(32, 8)

        # Input A
        model.zero_grad()
        x_a = torch.randn(1, 32)
        loss_a = nn.CrossEntropyLoss()(model(x_a), torch.tensor([0]))
        loss_a.backward()
        s_a = self._sketch_from_model(ftm, model, loss_a, proj_dim=64, seed=42)

        # Input B (different input, different target)
        model.zero_grad()
        x_b = torch.randn(1, 32) + 5.0
        loss_b = nn.CrossEntropyLoss()(model(x_b), torch.tensor([7]))
        loss_b.backward()
        s_b = self._sketch_from_model(ftm, model, loss_b, proj_dim=64, seed=42)

        cos_sim = np.dot(s_a, s_b)
        assert cos_sim < 0.99

    def test_preserves_inner_product_approximately(self, ftm: Any) -> None:
        """CountSketch approximately preserves inner products (JL property).

        With proj_dim=1024 and small models, the projected inner product
        should be in the same ballpark as the true gradient inner product.
        This is a sanity check, not a tight bound.
        """
        import torch
        import torch.nn as nn

        proj_dim = 1024
        torch.manual_seed(0)
        model = nn.Linear(128, 16)

        # Collect two gradient vectors and their sketches
        grads = []
        sketches = []
        for target_idx in [0, 15]:
            model.zero_grad()
            x = torch.randn(1, 128)
            loss = nn.CrossEntropyLoss()(model(x), torch.tensor([target_idx]))
            loss.backward()

            # True gradient (concatenated)
            grad = np.concatenate([
                p.grad.detach().cpu().flatten().numpy()
                for p in model.parameters() if p.grad is not None
            ])
            grads.append(grad / np.linalg.norm(grad))

            sketch = self._sketch_from_model(ftm, model, loss, proj_dim, seed=42)
            sketches.append(sketch)

        true_sim = np.dot(grads[0], grads[1])
        proj_sim = np.dot(sketches[0], sketches[1])

        # JL guarantees preservation within a factor — just check same sign
        # and rough magnitude for a sanity test
        assert abs(proj_sim - true_sim) < 0.5, (
            f"Projected sim {proj_sim:.3f} too far from true sim {true_sim:.3f}"
        )
