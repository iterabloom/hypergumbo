"""Tests for user configuration and interactive LLM setup.

This module tests the user configuration system that allows storing
API keys and preferences in ~/.config/hypergumbo/config.json.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


class TestUserConfigFile:
    """Tests for reading/writing user config file."""

    def test_get_config_dir_returns_xdg_path(self) -> None:
        """Config dir should be ~/.config/hypergumbo by default."""
        from hypergumbo.user_config import get_config_dir

        config_dir = get_config_dir()
        assert config_dir.name == "hypergumbo"
        assert config_dir.parent.name == ".config"

    def test_get_config_dir_respects_xdg_env(self, tmp_path: Path) -> None:
        """Config dir should respect XDG_CONFIG_HOME."""
        from hypergumbo.user_config import get_config_dir

        with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(tmp_path)}):
            config_dir = get_config_dir()
            assert config_dir == tmp_path / "hypergumbo"

    def test_load_config_returns_empty_when_missing(self, tmp_path: Path) -> None:
        """load_config returns empty dict when no config file exists."""
        from hypergumbo.user_config import load_config

        with patch("hypergumbo.user_config.get_config_dir", return_value=tmp_path):
            config = load_config()
            assert config == {}

    def test_load_config_reads_existing_file(self, tmp_path: Path) -> None:
        """load_config reads from existing config.json."""
        from hypergumbo.user_config import load_config

        config_file = tmp_path / "config.json"
        config_file.write_text('{"openrouter_api_key": "test-key"}')

        with patch("hypergumbo.user_config.get_config_dir", return_value=tmp_path):
            config = load_config()
            assert config["openrouter_api_key"] == "test-key"

    def test_load_config_handles_invalid_json(self, tmp_path: Path) -> None:
        """load_config returns empty dict for invalid JSON."""
        from hypergumbo.user_config import load_config

        config_file = tmp_path / "config.json"
        config_file.write_text("not valid json {{{")

        with patch("hypergumbo.user_config.get_config_dir", return_value=tmp_path):
            config = load_config()
            assert config == {}

    def test_save_config_creates_directory(self, tmp_path: Path) -> None:
        """save_config creates config directory if needed."""
        from hypergumbo.user_config import save_config

        config_dir = tmp_path / "new_dir"

        with patch("hypergumbo.user_config.get_config_dir", return_value=config_dir):
            save_config({"test": "value"})

        assert config_dir.exists()
        assert (config_dir / "config.json").exists()

    def test_save_config_writes_json(self, tmp_path: Path) -> None:
        """save_config writes valid JSON."""
        from hypergumbo.user_config import save_config

        with patch("hypergumbo.user_config.get_config_dir", return_value=tmp_path):
            save_config({"openrouter_api_key": "my-key"})

        config_file = tmp_path / "config.json"
        data = json.loads(config_file.read_text())
        assert data["openrouter_api_key"] == "my-key"


class TestGetApiKey:
    """Tests for API key retrieval from config + environment."""

    def test_get_api_key_prefers_env_var(self, tmp_path: Path) -> None:
        """Environment variable takes precedence over config file."""
        from hypergumbo.user_config import get_api_key

        config_file = tmp_path / "config.json"
        config_file.write_text('{"openrouter_api_key": "config-key"}')

        with patch("hypergumbo.user_config.get_config_dir", return_value=tmp_path):
            with patch.dict("os.environ", {"OPENROUTER_API_KEY": "env-key"}):
                key = get_api_key("openrouter")
                assert key == "env-key"

    def test_get_api_key_falls_back_to_config(self, tmp_path: Path) -> None:
        """Falls back to config file when env var not set."""
        from hypergumbo.user_config import get_api_key

        config_file = tmp_path / "config.json"
        config_file.write_text('{"openrouter_api_key": "config-key"}')

        with patch("hypergumbo.user_config.get_config_dir", return_value=tmp_path):
            with patch.dict("os.environ", {}, clear=True):
                # Clear the env var if it exists
                import os
                os.environ.pop("OPENROUTER_API_KEY", None)
                key = get_api_key("openrouter")
                assert key == "config-key"

    def test_get_api_key_returns_none_when_not_found(self, tmp_path: Path) -> None:
        """Returns None when key not in env or config."""
        from hypergumbo.user_config import get_api_key

        with patch("hypergumbo.user_config.get_config_dir", return_value=tmp_path):
            with patch.dict("os.environ", {}, clear=True):
                import os
                os.environ.pop("OPENROUTER_API_KEY", None)
                key = get_api_key("openrouter")
                assert key is None

    def test_get_api_key_supports_openai(self, tmp_path: Path) -> None:
        """get_api_key works for OpenAI."""
        from hypergumbo.user_config import get_api_key

        config_file = tmp_path / "config.json"
        config_file.write_text('{"openai_api_key": "openai-key"}')

        with patch("hypergumbo.user_config.get_config_dir", return_value=tmp_path):
            with patch.dict("os.environ", {}, clear=True):
                import os
                os.environ.pop("OPENAI_API_KEY", None)
                key = get_api_key("openai")
                assert key == "openai-key"

    def test_get_api_key_returns_none_for_unknown_provider(self) -> None:
        """get_api_key returns None for unknown provider."""
        from hypergumbo.user_config import get_api_key

        key = get_api_key("unknown_provider")
        assert key is None


class TestInteractiveSetup:
    """Tests for interactive LLM setup flow."""

    def test_prompt_for_llm_setup_returns_false_if_declined(self) -> None:
        """User can decline setup."""
        from hypergumbo.user_config import prompt_for_llm_setup

        with patch("builtins.input", return_value="n"):
            result = prompt_for_llm_setup()
            assert result is False

    def test_prompt_for_llm_setup_saves_openrouter_key(self, tmp_path: Path) -> None:
        """Interactive setup saves OpenRouter key to config."""
        from hypergumbo import user_config
        from hypergumbo.user_config import prompt_for_llm_setup

        # Simulate user choosing OpenRouter and entering a key
        inputs = iter(["y", "1", "sk-or-test-key"])

        with patch.object(user_config, "get_config_dir", return_value=tmp_path):
            with patch("builtins.input", lambda _: next(inputs)):
                result = prompt_for_llm_setup()

            # Read config using the patched path
            config_file = tmp_path / "config.json"
            config = json.loads(config_file.read_text())

        assert result is True
        assert config.get("openrouter_api_key") == "sk-or-test-key"

    def test_prompt_for_llm_setup_saves_openai_key(self, tmp_path: Path) -> None:
        """Interactive setup saves OpenAI key to config."""
        from hypergumbo import user_config
        from hypergumbo.user_config import prompt_for_llm_setup

        # Simulate user choosing OpenAI and entering a key
        inputs = iter(["y", "2", "sk-openai-test-key"])

        with patch.object(user_config, "get_config_dir", return_value=tmp_path):
            with patch("builtins.input", lambda _: next(inputs)):
                result = prompt_for_llm_setup()

            # Read config using the patched path
            config_file = tmp_path / "config.json"
            config = json.loads(config_file.read_text())

        assert result is True
        assert config.get("openai_api_key") == "sk-openai-test-key"

    def test_prompt_for_llm_setup_handles_local_models(self, tmp_path: Path) -> None:
        """Interactive setup handles local model selection."""
        from hypergumbo.user_config import prompt_for_llm_setup

        # Simulate user choosing local models
        inputs = iter(["y", "3"])

        with patch("hypergumbo.user_config.get_config_dir", return_value=tmp_path):
            with patch("builtins.input", lambda _: next(inputs)):
                result = prompt_for_llm_setup()

        # Should return True (setup "complete" - user chose local)
        assert result is True

    def test_prompt_for_llm_setup_handles_empty_openrouter_key(self, tmp_path: Path) -> None:
        """Interactive setup handles empty API key input."""
        from hypergumbo.user_config import prompt_for_llm_setup

        # Simulate user choosing OpenRouter but entering empty key
        inputs = iter(["y", "1", ""])

        with patch("hypergumbo.user_config.get_config_dir", return_value=tmp_path):
            with patch("builtins.input", lambda _: next(inputs)):
                result = prompt_for_llm_setup()

        assert result is False

    def test_prompt_for_llm_setup_handles_empty_openai_key(self, tmp_path: Path) -> None:
        """Interactive setup handles empty OpenAI API key input."""
        from hypergumbo.user_config import prompt_for_llm_setup

        # Simulate user choosing OpenAI but entering empty key
        inputs = iter(["y", "2", ""])

        with patch("hypergumbo.user_config.get_config_dir", return_value=tmp_path):
            with patch("builtins.input", lambda _: next(inputs)):
                result = prompt_for_llm_setup()

        assert result is False

    def test_prompt_for_llm_setup_handles_invalid_choice(self, tmp_path: Path) -> None:
        """Interactive setup handles invalid choice."""
        from hypergumbo.user_config import prompt_for_llm_setup

        # Simulate user entering invalid choice
        inputs = iter(["y", "9"])

        with patch("hypergumbo.user_config.get_config_dir", return_value=tmp_path):
            with patch("builtins.input", lambda _: next(inputs)):
                result = prompt_for_llm_setup()

        assert result is False


class TestLLMAssistIntegration:
    """Tests for integration with llm_assist module."""

    def test_detect_backend_checks_user_config(self, tmp_path: Path) -> None:
        """detect_backend should check user config file."""
        from hypergumbo import user_config
        from hypergumbo.llm_assist import detect_backend, LLMBackend

        config_file = tmp_path / "config.json"
        config_file.write_text('{"openrouter_api_key": "config-key"}')

        with patch.object(user_config, "get_config_dir", return_value=tmp_path):
            with patch.dict("os.environ", {}, clear=True):
                import os
                os.environ.pop("OPENROUTER_API_KEY", None)
                os.environ.pop("OPENAI_API_KEY", None)

                backend, config = detect_backend()

        assert backend == LLMBackend.OPENROUTER
        assert config.api_key == "config-key"


class TestCLIIntegration:
    """Tests for CLI integration with interactive setup."""

    def test_cmd_init_prompts_for_setup_when_no_backend(self, tmp_path: Path) -> None:
        """cmd_init offers interactive setup when --assistant llm but no backend."""
        from hypergumbo import user_config
        from hypergumbo.cli import cmd_init
        import argparse

        args = argparse.Namespace(
            path=str(tmp_path),
            capabilities="",
            assistant="llm",
            llm_input="tier0",
        )

        # Mock: no backend available, interactive terminal, user declines
        with patch.object(user_config, "get_config_dir", return_value=tmp_path):
            with patch.dict("os.environ", {}, clear=True):
                import os
                os.environ.pop("OPENROUTER_API_KEY", None)
                os.environ.pop("OPENAI_API_KEY", None)

                with patch("sys.stdin.isatty", return_value=True):
                    with patch("builtins.input", return_value="n"):
                        result = cmd_init(args)

        assert result == 0  # Should still succeed with template fallback

    def test_cmd_init_skips_prompt_when_not_tty(self, tmp_path: Path) -> None:
        """cmd_init doesn't prompt in non-interactive mode."""
        from hypergumbo import user_config
        from hypergumbo.cli import cmd_init
        import argparse

        args = argparse.Namespace(
            path=str(tmp_path),
            capabilities="",
            assistant="llm",
            llm_input="tier0",
        )

        # Mock: no backend available, NOT interactive terminal
        with patch.object(user_config, "get_config_dir", return_value=tmp_path):
            with patch.dict("os.environ", {}, clear=True):
                import os
                os.environ.pop("OPENROUTER_API_KEY", None)
                os.environ.pop("OPENAI_API_KEY", None)

                with patch("sys.stdin.isatty", return_value=False):
                    # Should NOT call input() since not a TTY
                    with patch("builtins.input", side_effect=AssertionError("Should not prompt")):
                        result = cmd_init(args)

        assert result == 0

    def test_cmd_init_redetects_after_successful_setup(self, tmp_path: Path) -> None:
        """cmd_init re-detects backend after user completes interactive setup."""
        from hypergumbo import user_config, cli
        from hypergumbo.cli import cmd_init
        from hypergumbo.plan import CapsulePlan
        import argparse

        args = argparse.Namespace(
            path=str(tmp_path),
            capabilities="",
            assistant="llm",
            llm_input="tier0",
        )

        # Simulate user completing OpenRouter setup
        inputs = iter(["y", "1", "sk-or-test-key"])

        # Mock generate_plan_with_fallback to avoid actual LLM calls
        mock_plan = CapsulePlan(passes=[], packs=[], rules=[])
        mock_llm_result = None

        with patch.object(user_config, "get_config_dir", return_value=tmp_path):
            with patch.dict("os.environ", {}, clear=True):
                import os
                os.environ.pop("OPENROUTER_API_KEY", None)
                os.environ.pop("OPENAI_API_KEY", None)

                with patch("sys.stdin.isatty", return_value=True):
                    with patch("builtins.input", lambda _: next(inputs)):
                        with patch.object(
                            cli,
                            "generate_plan_with_fallback",
                            return_value=(mock_plan, mock_llm_result),
                        ):
                            result = cmd_init(args)

        assert result == 0
        # Config should have been saved
        config_file = tmp_path / "config.json"
        assert config_file.exists()
