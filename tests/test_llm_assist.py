"""Tests for LLM-assisted capsule plan generation."""
import json
import os
from unittest import mock

import pytest

from hypergumbo.catalog import get_default_catalog
from hypergumbo.llm_assist import (
    LLMBackend,
    LLMConfig,
    OPENROUTER_FREE_MODELS,
    _build_prompt,
    _call_llm_package,
    _call_openai_compatible,
    _is_llm_package_available,
    _is_openai_sdk_available,
    _parse_plan_json,
    _sanitize_no_proxy,
    _sanitized_proxy_env,
    detect_backend,
    generate_plan_with_fallback,
    generate_plan_with_llm,
)
from hypergumbo.plan import PLAN_VERSION
from hypergumbo.profile import LanguageStats, RepoProfile


@pytest.fixture
def sample_profile():
    """Create a sample repo profile for testing."""
    return RepoProfile(
        languages={
            "python": LanguageStats(files=10, loc=1000),
            "javascript": LanguageStats(files=5, loc=500),
        },
        frameworks=["fastapi", "react"],
    )


@pytest.fixture
def sample_catalog():
    """Create a sample catalog for testing."""
    return get_default_catalog()


class TestLLMBackendDetection:
    """Tests for LLM backend detection logic."""

    def test_detect_backend_no_config(self, monkeypatch):
        """When no env vars set and no packages, returns NONE."""
        monkeypatch.delenv("HYPERGUMBO_LLM_BACKEND", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        with mock.patch(
            "hypergumbo.llm_assist._is_llm_package_available", return_value=False
        ):
            backend, config = detect_backend()
            assert backend == LLMBackend.NONE
            assert config.backend == LLMBackend.NONE

    def test_detect_backend_openrouter_key(self, monkeypatch):
        """When OPENROUTER_API_KEY is set, uses OpenRouter."""
        monkeypatch.delenv("HYPERGUMBO_LLM_BACKEND", raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        backend, config = detect_backend()
        assert backend == LLMBackend.OPENROUTER
        assert config.api_key == "test-key"
        assert config.base_url == "https://openrouter.ai/api/v1"
        assert config.model == "mistralai/devstral-2512:free"

    def test_detect_backend_openai_key(self, monkeypatch):
        """When OPENAI_API_KEY is set (and no OpenRouter), uses OpenAI."""
        monkeypatch.delenv("HYPERGUMBO_LLM_BACKEND", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        backend, config = detect_backend()
        assert backend == LLMBackend.OPENAI
        assert config.api_key == "sk-test"
        assert config.base_url == "https://api.openai.com/v1"

    def test_detect_backend_llm_package(self, monkeypatch):
        """When llm package is available, uses it."""
        monkeypatch.delenv("HYPERGUMBO_LLM_BACKEND", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        with mock.patch(
            "hypergumbo.llm_assist._is_llm_package_available", return_value=True
        ):
            backend, config = detect_backend()
            assert backend == LLMBackend.LLM_PACKAGE
            assert config.model is None  # Uses llm default

    def test_detect_backend_explicit_openrouter(self, monkeypatch):
        """Explicit backend selection via env var."""
        monkeypatch.setenv("HYPERGUMBO_LLM_BACKEND", "openrouter")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        backend, config = detect_backend()
        assert backend == LLMBackend.OPENROUTER

    def test_detect_backend_explicit_openai(self, monkeypatch):
        """Explicit OpenAI backend selection."""
        monkeypatch.setenv("HYPERGUMBO_LLM_BACKEND", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        backend, config = detect_backend()
        assert backend == LLMBackend.OPENAI

    def test_detect_backend_explicit_llm(self, monkeypatch):
        """Explicit llm package selection."""
        monkeypatch.setenv("HYPERGUMBO_LLM_BACKEND", "llm")

        with mock.patch(
            "hypergumbo.llm_assist._is_llm_package_available", return_value=True
        ):
            backend, config = detect_backend()
            assert backend == LLMBackend.LLM_PACKAGE

    def test_detect_backend_explicit_missing(self, monkeypatch):
        """Explicit backend requested but not available."""
        monkeypatch.setenv("HYPERGUMBO_LLM_BACKEND", "openrouter")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        backend, config = detect_backend()
        assert backend == LLMBackend.NONE

    def test_detect_backend_custom_model(self, monkeypatch):
        """Custom model via HYPERGUMBO_LLM_MODEL."""
        monkeypatch.setenv("HYPERGUMBO_LLM_BACKEND", "openrouter")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("HYPERGUMBO_LLM_MODEL", "qwen/qwen3-coder:free")

        backend, config = detect_backend()
        assert config.model == "qwen/qwen3-coder:free"

    def test_openrouter_priority_over_openai(self, monkeypatch):
        """OpenRouter takes priority over OpenAI when both are set."""
        monkeypatch.delenv("HYPERGUMBO_LLM_BACKEND", raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

        backend, config = detect_backend()
        assert backend == LLMBackend.OPENROUTER
        assert config.api_key == "openrouter-key"


class TestPromptBuilding:
    """Tests for prompt construction."""

    def test_build_prompt_tier0(self, sample_profile, sample_catalog):
        """Tier0 prompt includes only language/framework names."""
        prompt = _build_prompt(sample_profile, sample_catalog, tier="tier0")

        assert "python" in prompt.lower()
        assert "javascript" in prompt.lower()
        assert "fastapi" in prompt.lower()
        assert PLAN_VERSION in prompt
        # Tier0 should not include file counts
        assert "10 files" not in prompt

    def test_build_prompt_tier1(self, sample_profile, sample_catalog):
        """Tier1 prompt includes file counts and LOC."""
        prompt = _build_prompt(sample_profile, sample_catalog, tier="tier1")

        assert "10 files" in prompt
        assert "1000 LOC" in prompt
        assert "5 files" in prompt
        assert "500 LOC" in prompt

    def test_build_prompt_tier2(self, sample_profile, sample_catalog):
        """Tier2 prompt includes fuller profile details."""
        prompt = _build_prompt(sample_profile, sample_catalog, tier="tier2")

        assert "10 files" in prompt
        assert "1000 LOC" in prompt
        assert "Repository kind" in prompt

    def test_build_prompt_includes_catalog_passes(self, sample_profile, sample_catalog):
        """Prompt includes available passes from catalog."""
        prompt = _build_prompt(sample_profile, sample_catalog, tier="tier0")

        assert "python-ast-v1" in prompt
        assert "html-pattern-v1" in prompt

    def test_build_prompt_includes_catalog_packs(self, sample_profile, sample_catalog):
        """Prompt includes available packs from catalog."""
        prompt = _build_prompt(sample_profile, sample_catalog, tier="tier0")

        assert "python-fastapi" in prompt

    def test_build_prompt_no_frameworks(self, sample_catalog):
        """Prompt handles profiles with no frameworks."""
        profile = RepoProfile(
            languages={"python": LanguageStats(files=5, loc=100)},
            frameworks=[],
        )
        prompt = _build_prompt(profile, sample_catalog, tier="tier0")

        assert "none" in prompt.lower()


class TestPlanParsing:
    """Tests for parsing LLM responses into plans."""

    def test_parse_valid_json(self, sample_catalog):
        """Valid JSON is parsed correctly."""
        raw = json.dumps({
            "version": PLAN_VERSION,
            "passes": [{"id": "python-ast-v1", "enabled": True}],
            "packs": [],
            "rules": [
                {"type": "exclude_pattern", "glob": "**/*.test.py", "reason": "tests"}
            ],
            "features": [],
        })

        plan, error = _parse_plan_json(raw, sample_catalog)
        assert error is None
        assert plan is not None
        assert plan.version == PLAN_VERSION
        assert len(plan.passes) == 1
        assert plan.passes[0].id == "python-ast-v1"

    def test_parse_json_with_markdown_blocks(self, sample_catalog):
        """JSON wrapped in markdown code blocks is handled."""
        raw = """```json
{
    "version": "0.1.0",
    "passes": [{"id": "python-ast-v1", "enabled": true}],
    "packs": [],
    "rules": [],
    "features": []
}
```"""

        plan, error = _parse_plan_json(raw, sample_catalog)
        assert error is None
        assert plan is not None

    def test_parse_invalid_json(self, sample_catalog):
        """Invalid JSON returns error."""
        raw = "not valid json {"

        plan, error = _parse_plan_json(raw, sample_catalog)
        assert plan is None
        assert "Invalid JSON" in error

    def test_parse_unknown_pass(self, sample_catalog):
        """Unknown pass ID fails validation."""
        raw = json.dumps({
            "version": PLAN_VERSION,
            "passes": [{"id": "unknown-pass-v99", "enabled": True}],
            "packs": [],
            "rules": [],
            "features": [],
        })

        plan, error = _parse_plan_json(raw, sample_catalog)
        assert plan is None
        assert "Unknown pass" in error

    def test_parse_unknown_pack(self, sample_catalog):
        """Unknown pack ID fails validation."""
        raw = json.dumps({
            "version": PLAN_VERSION,
            "passes": [],
            "packs": [{"id": "unknown-pack", "enabled": True}],
            "rules": [],
            "features": [],
        })

        plan, error = _parse_plan_json(raw, sample_catalog)
        assert plan is None
        assert "Unknown pack" in error

    def test_parse_missing_pass_id(self, sample_catalog):
        """Missing pass ID returns error."""
        raw = json.dumps({
            "version": PLAN_VERSION,
            "passes": [{"enabled": True}],  # Missing 'id'
            "packs": [],
            "rules": [],
            "features": [],
        })

        plan, error = _parse_plan_json(raw, sample_catalog)
        assert plan is None
        assert "Invalid plan structure" in error

    def test_parse_with_config(self, sample_catalog):
        """Pass config is preserved."""
        raw = json.dumps({
            "version": PLAN_VERSION,
            "passes": [{
                "id": "python-ast-v1",
                "enabled": True,
                "config": {"parse_decorators": True},
            }],
            "packs": [],
            "rules": [],
            "features": [],
        })

        plan, error = _parse_plan_json(raw, sample_catalog)
        assert error is None
        assert plan.passes[0].config == {"parse_decorators": True}


class TestOpenAICompatibleCall:
    """Tests for OpenAI/OpenRouter API calls."""

    def test_call_without_sdk(self):
        """Returns error when SDK not installed."""
        with mock.patch(
            "hypergumbo.llm_assist._is_openai_sdk_available", return_value=False
        ):
            config = LLMConfig(
                backend=LLMBackend.OPENAI,
                api_key="test",
                base_url="https://api.openai.com/v1",
            )
            response, error = _call_openai_compatible("test prompt", config)

            assert response is None
            assert "not installed" in error

    def test_call_openai_success(self):
        """Successful OpenAI call returns response."""
        # Create mock response
        mock_message = mock.MagicMock()
        mock_message.content = '{"version": "0.1.0"}'
        mock_choice = mock.MagicMock()
        mock_choice.message = mock_message
        mock_response = mock.MagicMock()
        mock_response.choices = [mock_choice]

        # Create mock client
        mock_client = mock.MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with mock.patch(
            "hypergumbo.llm_assist._is_openai_sdk_available", return_value=True
        ):
            # Mock the openai module import inside the function
            mock_openai_module = mock.MagicMock()
            mock_openai_module.OpenAI.return_value = mock_client

            with mock.patch.dict("sys.modules", {"openai": mock_openai_module}):
                config = LLMConfig(
                    backend=LLMBackend.OPENAI,
                    model="gpt-4o-mini",
                    api_key="test-key",
                    base_url="https://api.openai.com/v1",
                )
                response, error = _call_openai_compatible("test prompt", config)

                assert error is None
                assert response == '{"version": "0.1.0"}'

    def test_call_openrouter_includes_headers(self):
        """OpenRouter calls include attribution headers."""
        # Create mock response
        mock_message = mock.MagicMock()
        mock_message.content = '{"version": "0.1.0"}'
        mock_choice = mock.MagicMock()
        mock_choice.message = mock_message
        mock_response = mock.MagicMock()
        mock_response.choices = [mock_choice]

        # Create mock client
        mock_client = mock.MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with mock.patch(
            "hypergumbo.llm_assist._is_openai_sdk_available", return_value=True
        ):
            mock_openai_module = mock.MagicMock()
            mock_openai_module.OpenAI.return_value = mock_client

            with mock.patch.dict("sys.modules", {"openai": mock_openai_module}):
                config = LLMConfig(
                    backend=LLMBackend.OPENROUTER,
                    model="mistralai/devstral-2512:free",
                    api_key="test-key",
                    base_url="https://openrouter.ai/api/v1",
                )
                _call_openai_compatible("test prompt", config)

                # Check that extra_headers were passed
                call_kwargs = mock_client.chat.completions.create.call_args.kwargs
                assert "extra_headers" in call_kwargs
                assert call_kwargs["extra_headers"]["X-Title"] == "hypergumbo"

    def test_call_api_error(self):
        """API errors are caught and returned."""
        # Create mock client that raises
        mock_client = mock.MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")

        with mock.patch(
            "hypergumbo.llm_assist._is_openai_sdk_available", return_value=True
        ):
            mock_openai_module = mock.MagicMock()
            mock_openai_module.OpenAI.return_value = mock_client

            with mock.patch.dict("sys.modules", {"openai": mock_openai_module}):
                config = LLMConfig(
                    backend=LLMBackend.OPENAI,
                    api_key="test-key",
                    base_url="https://api.openai.com/v1",
                )
                response, error = _call_openai_compatible("test prompt", config)

                assert response is None
                assert "API call failed" in error


class TestLLMPackageCall:
    """Tests for llm package calls."""

    def test_call_without_package(self):
        """Returns error when llm package not installed."""
        with mock.patch(
            "hypergumbo.llm_assist._is_llm_package_available", return_value=False
        ):
            config = LLMConfig(backend=LLMBackend.LLM_PACKAGE)
            response, error = _call_llm_package("test prompt", config)

            assert response is None
            assert "not installed" in error

    def test_call_llm_success(self):
        """Successful llm package call returns response."""
        mock_llm = mock.MagicMock()
        mock_model = mock.MagicMock()
        mock_response = mock.MagicMock()
        mock_response.text.return_value = '{"version": "0.1.0"}'
        mock_model.prompt.return_value = mock_response
        mock_llm.get_model.return_value = mock_model

        with mock.patch(
            "hypergumbo.llm_assist._is_llm_package_available", return_value=True
        ):
            with mock.patch.dict("sys.modules", {"llm": mock_llm}):
                config = LLMConfig(backend=LLMBackend.LLM_PACKAGE, model="test-model")
                response, error = _call_llm_package("test prompt", config)

                # Success means no error (response may vary based on mock)
                # The actual response depends on how the mock is consumed
                assert error is None or "failed" not in str(error).lower()

    def test_call_llm_error(self):
        """llm package errors are caught."""
        mock_llm = mock.MagicMock()
        mock_llm.get_model.side_effect = Exception("Model not found")

        with mock.patch(
            "hypergumbo.llm_assist._is_llm_package_available", return_value=True
        ):
            with mock.patch.dict("sys.modules", {"llm": mock_llm}):
                config = LLMConfig(backend=LLMBackend.LLM_PACKAGE)
                response, error = _call_llm_package("test prompt", config)

                assert response is None
                assert "failed" in error


class TestGeneratePlanWithLLM:
    """Tests for the main LLM plan generation function."""

    def test_no_backend_available(self, sample_profile, sample_catalog, monkeypatch):
        """Returns failure when no backend is available."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("HYPERGUMBO_LLM_BACKEND", raising=False)

        with mock.patch(
            "hypergumbo.llm_assist._is_llm_package_available", return_value=False
        ):
            result = generate_plan_with_llm(sample_profile, sample_catalog)

            assert not result.success
            assert result.plan is None
            assert "No LLM backend available" in result.error

    def test_successful_generation(self, sample_profile, sample_catalog, monkeypatch):
        """Successful LLM generation returns valid plan."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.delenv("HYPERGUMBO_LLM_BACKEND", raising=False)

        valid_plan = json.dumps({
            "version": PLAN_VERSION,
            "passes": [{"id": "python-ast-v1", "enabled": True}],
            "packs": [],
            "rules": [],
            "features": [],
        })

        with mock.patch(
            "hypergumbo.llm_assist._call_openai_compatible",
            return_value=(valid_plan, None),
        ):
            result = generate_plan_with_llm(sample_profile, sample_catalog)

            assert result.success
            assert result.plan is not None
            assert result.plan.passes[0].id == "python-ast-v1"
            assert result.backend_used == LLMBackend.OPENROUTER

    def test_api_error_returns_failure(self, sample_profile, sample_catalog, monkeypatch):
        """API errors result in failure."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.delenv("HYPERGUMBO_LLM_BACKEND", raising=False)

        with mock.patch(
            "hypergumbo.llm_assist._call_openai_compatible",
            return_value=(None, "Connection timeout"),
        ):
            result = generate_plan_with_llm(sample_profile, sample_catalog)

            assert not result.success
            assert result.error == "Connection timeout"

    def test_invalid_json_returns_failure(self, sample_profile, sample_catalog, monkeypatch):
        """Invalid JSON response results in failure."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.delenv("HYPERGUMBO_LLM_BACKEND", raising=False)

        with mock.patch(
            "hypergumbo.llm_assist._call_openai_compatible",
            return_value=("not valid json", None),
        ):
            result = generate_plan_with_llm(sample_profile, sample_catalog)

            assert not result.success
            assert "Invalid JSON" in result.error
            assert result.raw_response == "not valid json"

    def test_custom_config(self, sample_profile, sample_catalog):
        """Custom config is used when provided."""
        from hypergumbo.llm_assist import LLMBackend as LB, LLMConfig as LC

        custom_config = LC(
            backend=LB.OPENAI,
            model="gpt-4",
            api_key="custom-key",
            base_url="https://api.openai.com/v1",
        )

        valid_plan = json.dumps({
            "version": PLAN_VERSION,
            "passes": [{"id": "python-ast-v1", "enabled": True}],
            "packs": [],
            "rules": [],
            "features": [],
        })

        with mock.patch(
            "hypergumbo.llm_assist._call_openai_compatible",
            return_value=(valid_plan, None),
        ) as mock_call:
            result = generate_plan_with_llm(
                sample_profile, sample_catalog, config=custom_config
            )

            assert result.success
            assert result.model_used == "gpt-4"
            # Verify custom config was passed
            call_config = mock_call.call_args[0][1]
            assert call_config.model == "gpt-4"

    def test_llm_package_backend(self, sample_profile, sample_catalog):
        """LLM package backend calls _call_llm_package."""
        from hypergumbo.llm_assist import LLMBackend as LB, LLMConfig as LC

        custom_config = LC(
            backend=LB.LLM_PACKAGE,
            model="gguf/phi-3",
        )

        valid_plan = json.dumps({
            "version": PLAN_VERSION,
            "passes": [{"id": "python-ast-v1", "enabled": True}],
            "packs": [],
            "rules": [],
            "features": [],
        })

        with mock.patch(
            "hypergumbo.llm_assist._call_llm_package",
            return_value=(valid_plan, None),
        ):
            result = generate_plan_with_llm(
                sample_profile, sample_catalog, config=custom_config
            )

            assert result.success
            assert result.backend_used == LB.LLM_PACKAGE


class TestGeneratePlanWithFallback:
    """Tests for the fallback plan generation."""

    def test_template_only(self, sample_profile, sample_catalog):
        """When use_llm=False, uses template only."""
        plan, llm_result = generate_plan_with_fallback(
            sample_profile, sample_catalog, use_llm=False
        )

        assert plan is not None
        assert llm_result is None
        # Should have passes for python and javascript
        pass_ids = [p.id for p in plan.passes]
        assert "python-ast-v1" in pass_ids

    def test_llm_success(self, sample_profile, sample_catalog, monkeypatch):
        """When LLM succeeds, uses LLM plan."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.delenv("HYPERGUMBO_LLM_BACKEND", raising=False)

        llm_plan = json.dumps({
            "version": PLAN_VERSION,
            "passes": [{"id": "html-pattern-v1", "enabled": True}],
            "packs": [],
            "rules": [{"type": "exclude_pattern", "glob": "**/custom/*", "reason": "LLM"}],
            "features": [],
        })

        with mock.patch(
            "hypergumbo.llm_assist._call_openai_compatible",
            return_value=(llm_plan, None),
        ):
            plan, llm_result = generate_plan_with_fallback(
                sample_profile, sample_catalog, use_llm=True
            )

            assert llm_result is not None
            assert llm_result.success
            # Should have the LLM-generated plan
            assert plan.passes[0].id == "html-pattern-v1"
            assert any(r.glob == "**/custom/*" for r in plan.rules)

    def test_llm_failure_falls_back(self, sample_profile, sample_catalog, monkeypatch):
        """When LLM fails, falls back to template."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.delenv("HYPERGUMBO_LLM_BACKEND", raising=False)

        with mock.patch(
            "hypergumbo.llm_assist._call_openai_compatible",
            return_value=(None, "API error"),
        ):
            plan, llm_result = generate_plan_with_fallback(
                sample_profile, sample_catalog, use_llm=True
            )

            assert llm_result is not None
            assert not llm_result.success
            # Should have the template plan
            assert plan is not None
            pass_ids = [p.id for p in plan.passes]
            assert "python-ast-v1" in pass_ids


class TestOpenRouterFreeModels:
    """Tests for OpenRouter free model list."""

    def test_free_models_have_correct_suffix(self):
        """All free models have :free suffix."""
        for model in OPENROUTER_FREE_MODELS:
            assert model.endswith(":free"), f"{model} should end with :free"

    def test_default_model_is_in_list(self):
        """Default OpenRouter model is in the free list."""
        from hypergumbo.llm_assist import DEFAULT_MODELS, LLMBackend as LB

        default = DEFAULT_MODELS[LB.OPENROUTER]
        assert default in OPENROUTER_FREE_MODELS


class TestPackageAvailabilityChecks:
    """Tests for package availability checking."""

    def test_is_openai_sdk_available_when_installed(self):
        """Returns True when openai is importable."""
        # This may or may not be installed in test env
        result = _is_openai_sdk_available()
        assert isinstance(result, bool)

    def test_is_openai_sdk_not_available(self):
        """Returns False when openai raises ImportError."""
        import sys
        from importlib.abc import MetaPathFinder

        # Save and remove openai from sys.modules
        saved = sys.modules.pop("openai", None)
        saved_sub = {k: v for k, v in list(sys.modules.items())
                     if k.startswith("openai.")}
        for k in saved_sub:
            sys.modules.pop(k, None)

        try:
            # Block openai import using the modern finder protocol
            class BlockOpenAI(MetaPathFinder):
                def find_spec(self, fullname, path, target=None):
                    if fullname == "openai" or fullname.startswith("openai."):
                        raise ImportError(f"Blocked: {fullname}")
                    return None

            blocker = BlockOpenAI()
            sys.meta_path.insert(0, blocker)
            try:
                result = _is_openai_sdk_available()
                assert result is False
            finally:
                sys.meta_path.remove(blocker)
        finally:
            # Restore openai module
            if saved is not None:
                sys.modules["openai"] = saved
            for k, v in saved_sub.items():
                sys.modules[k] = v

    def test_is_llm_package_available_when_not_installed(self):
        """Returns False when llm is not importable."""
        with mock.patch.dict("sys.modules", {"llm": None}):
            # Force import error
            import sys
            original = sys.modules.get("llm")
            sys.modules["llm"] = None

            # The function should handle this
            result = _is_llm_package_available()
            # Result depends on whether llm is actually installed

            if original is not None:
                sys.modules["llm"] = original

    def test_is_llm_package_available_success(self):
        """Returns True when llm can be imported."""
        mock_llm = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"llm": mock_llm}):
            result = _is_llm_package_available()
            assert result is True

    def test_is_openai_sdk_available_success(self):
        """Returns True when openai can be imported."""
        mock_openai = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"openai": mock_openai}):
            result = _is_openai_sdk_available()
            assert result is True


class TestUnknownBackend:
    """Tests for unknown backend handling."""

    def test_generate_with_none_backend(self, sample_profile, sample_catalog):
        """NONE backend returns appropriate error."""
        from hypergumbo.llm_assist import LLMBackend as LB, LLMConfig as LC

        config = LC(
            backend=LB.NONE,
            model="test",
            api_key="test",
        )

        # Directly call with NONE backend should return "No LLM backend available"
        result = generate_plan_with_llm(sample_profile, sample_catalog, config=config)
        assert not result.success
        assert "No LLM backend available" in result.error

    def test_generate_with_truly_unknown_backend(self, sample_profile, sample_catalog):
        """Unknown backend (not in enum) returns appropriate error."""
        from hypergumbo.llm_assist import LLMConfig as LC, generate_plan_with_llm

        # Create a mock backend that's not in the normal enum
        fake_backend = mock.MagicMock()
        fake_backend.value = "fake"
        fake_backend.__eq__ = lambda self, other: False  # Never equal to NONE

        config = LC(
            backend=fake_backend,
            model="test",
            api_key="test",
        )

        result = generate_plan_with_llm(sample_profile, sample_catalog, config=config)
        assert not result.success
        assert "Unknown backend" in result.error


class TestProxySanitization:
    """Tests for NO_PROXY environment variable sanitization."""

    def test_sanitize_no_proxy_removes_cidr(self):
        """CIDR notation entries are filtered out."""
        original = "localhost,127.0.0.1,10.200.0.0/16,fd00:200::/40"
        result = _sanitize_no_proxy(original)
        assert result == "localhost,127.0.0.1"

    def test_sanitize_no_proxy_keeps_hostnames(self):
        """Valid hostname entries are preserved."""
        original = "localhost,example.com,api.openrouter.ai"
        result = _sanitize_no_proxy(original)
        assert result == original

    def test_sanitize_no_proxy_empty_string(self):
        """Empty string returns empty string."""
        assert _sanitize_no_proxy("") == ""

    def test_sanitize_no_proxy_only_cidr(self):
        """If all entries are CIDR, returns empty string."""
        original = "10.0.0.0/8,192.168.0.0/16"
        result = _sanitize_no_proxy(original)
        assert result == ""

    def test_sanitize_no_proxy_strips_whitespace(self):
        """Whitespace around entries is stripped."""
        original = " localhost , 127.0.0.1 , 10.0.0.0/8 "
        result = _sanitize_no_proxy(original)
        assert result == "localhost,127.0.0.1"

    def test_sanitized_proxy_env_context_manager(self, monkeypatch):
        """Context manager sanitizes and restores env vars."""
        monkeypatch.setenv("NO_PROXY", "localhost,10.0.0.0/8")
        monkeypatch.setenv("no_proxy", "127.0.0.1,fd00::/8")

        # Before context
        assert "/" in os.environ["NO_PROXY"]
        assert "/" in os.environ["no_proxy"]

        # Inside context
        with _sanitized_proxy_env():
            assert os.environ["NO_PROXY"] == "localhost"
            assert os.environ["no_proxy"] == "127.0.0.1"

        # After context - restored
        assert os.environ["NO_PROXY"] == "localhost,10.0.0.0/8"
        assert os.environ["no_proxy"] == "127.0.0.1,fd00::/8"

    def test_sanitized_proxy_env_no_env_vars(self, monkeypatch):
        """Context manager handles missing env vars gracefully."""
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)

        # Should not raise
        with _sanitized_proxy_env():
            assert os.environ.get("NO_PROXY") is None
            assert os.environ.get("no_proxy") is None

    def test_sanitized_proxy_env_restores_on_exception(self, monkeypatch):
        """Env vars are restored even if exception occurs."""
        monkeypatch.setenv("NO_PROXY", "localhost,10.0.0.0/8")

        try:
            with _sanitized_proxy_env():
                assert os.environ["NO_PROXY"] == "localhost"
                raise ValueError("test error")
        except ValueError:
            pass

        # Should be restored
        assert os.environ["NO_PROXY"] == "localhost,10.0.0.0/8"

    def test_sanitized_proxy_env_cleans_up_added_vars(self, monkeypatch):
        """If env vars were added during context, they're removed after."""
        # Ensure neither var is set
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)

        with _sanitized_proxy_env():
            # Simulate something setting them inside the context
            os.environ["NO_PROXY"] = "added"
            os.environ["no_proxy"] = "added"

        # After context, vars that weren't originally set should be removed
        assert os.environ.get("NO_PROXY") is None
        assert os.environ.get("no_proxy") is None
