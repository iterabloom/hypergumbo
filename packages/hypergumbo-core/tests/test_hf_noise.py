# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for HuggingFace noise suppression (WI-gatot).

Verifies the four env-var defaults that ``suppress_hf_noise()`` writes,
that user-set values are preserved (``setdefault`` semantics), and that
sketch_embeddings.py imports + calls the helper at module load — which is
what makes the env vars take effect before sentence_transformers and its
transitive imports cache them.

Real model loading is network-dependent and intentionally out of scope:
sketch_embeddings.py's loaders sit behind ``_has_sentence_transformers()``
and are exercised by integration runs, not unit tests.
"""
from __future__ import annotations

import importlib
import os

import pytest

from hypergumbo_core import _hf_noise


def test_default_table_has_all_four_hf_env_vars() -> None:
    """The known-noisy env vars are all in the defaults table."""
    names = {name for name, _ in _hf_noise._HF_NOISE_DEFAULTS}
    assert names == {
        "HF_HUB_DISABLE_PROGRESS_BARS",
        "TRANSFORMERS_VERBOSITY",
        "HF_HUB_DISABLE_SYMLINKS_WARNING",
        "TRANSFORMERS_NO_ADVISORY_WARNINGS",
    }


def test_progress_bars_default_is_off() -> None:
    """HF_HUB_DISABLE_PROGRESS_BARS defaults to '1' (off)."""
    defaults = dict(_hf_noise._HF_NOISE_DEFAULTS)
    assert defaults["HF_HUB_DISABLE_PROGRESS_BARS"] == "1"


def test_transformers_verbosity_default_is_error() -> None:
    """TRANSFORMERS_VERBOSITY defaults to 'error' (suppresses info/warning)."""
    defaults = dict(_hf_noise._HF_NOISE_DEFAULTS)
    assert defaults["TRANSFORMERS_VERBOSITY"] == "error"


def test_suppress_writes_all_defaults_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All env vars are set when none were defined before."""
    for name, _ in _hf_noise._HF_NOISE_DEFAULTS:
        monkeypatch.delenv(name, raising=False)

    _hf_noise.suppress_hf_noise()

    for name, value in _hf_noise._HF_NOISE_DEFAULTS:
        assert os.environ.get(name) == value


def test_suppress_respects_user_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User-set values survive (setdefault, not overwrite)."""
    monkeypatch.setenv("HF_HUB_DISABLE_PROGRESS_BARS", "0")
    monkeypatch.setenv("TRANSFORMERS_VERBOSITY", "info")
    # Leave the other two unset so we can also verify default-fill happens.
    monkeypatch.delenv("HF_HUB_DISABLE_SYMLINKS_WARNING", raising=False)
    monkeypatch.delenv("TRANSFORMERS_NO_ADVISORY_WARNINGS", raising=False)

    _hf_noise.suppress_hf_noise()

    # User overrides preserved
    assert os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] == "0"
    assert os.environ["TRANSFORMERS_VERBOSITY"] == "info"
    # Unset ones get the defaults
    assert os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] == "1"
    assert os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] == "1"


def test_suppress_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling twice doesn't change the result."""
    for name, _ in _hf_noise._HF_NOISE_DEFAULTS:
        monkeypatch.delenv(name, raising=False)

    _hf_noise.suppress_hf_noise()
    snapshot = {n: os.environ[n] for n, _ in _hf_noise._HF_NOISE_DEFAULTS}
    _hf_noise.suppress_hf_noise()
    after = {n: os.environ[n] for n, _ in _hf_noise._HF_NOISE_DEFAULTS}

    assert snapshot == after


def test_sketch_embeddings_invokes_suppress_at_import() -> None:
    """Importing sketch_embeddings.py applies the env defaults.

    This is the load-bearing test: the env vars must be set BEFORE
    sentence_transformers is imported anywhere downstream, so the
    sketch_embeddings module is responsible for calling suppress_hf_noise
    at its own module-init time.
    """
    # Force re-import to make sure module init runs in this test process.
    # importlib.reload preserves the existing module identity.
    import hypergumbo_core.sketch_embeddings as se
    importlib.reload(se)

    for name, value in _hf_noise._HF_NOISE_DEFAULTS:
        assert os.environ.get(name) == value, (
            f"sketch_embeddings import did not apply HF noise default for {name}"
        )
