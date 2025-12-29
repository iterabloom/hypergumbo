"""User configuration management for hypergumbo.

This module handles persistent user configuration stored in
~/.config/hypergumbo/config.json (or $XDG_CONFIG_HOME/hypergumbo/config.json).

Configuration is used for:
- API keys (OpenRouter, OpenAI) so users don't need to set env vars
- User preferences

How It Works
------------
1. get_config_dir() returns the config directory path
2. load_config() reads config.json if it exists
3. save_config() writes config.json
4. get_api_key() checks env vars first, then falls back to config file

Interactive Setup
-----------------
When a user runs `hypergumbo init --assistant llm` without API keys configured,
prompt_for_llm_setup() offers to walk them through configuration interactively.

Why This Design
---------------
- XDG Base Directory compliance for cross-platform consistency
- Environment variables take precedence (12-factor app style)
- Interactive setup reduces friction for new users
- Config file avoids need to edit shell profiles
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional


def get_config_dir() -> Path:
    """Get the hypergumbo configuration directory.

    Follows XDG Base Directory specification:
    - Uses $XDG_CONFIG_HOME/hypergumbo if XDG_CONFIG_HOME is set
    - Falls back to ~/.config/hypergumbo otherwise

    Returns:
        Path to the configuration directory
    """
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        base = Path(xdg_config)
    else:
        base = Path.home() / ".config"
    return base / "hypergumbo"


def load_config() -> dict[str, Any]:
    """Load user configuration from config.json.

    Returns:
        Configuration dict, or empty dict if file doesn't exist
    """
    config_file = get_config_dir() / "config.json"
    if not config_file.exists():
        return {}

    try:
        return json.loads(config_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(config: dict[str, Any]) -> None:
    """Save user configuration to config.json.

    Creates the config directory if it doesn't exist.

    Args:
        config: Configuration dict to save
    """
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps(config, indent=2))


def get_api_key(provider: str) -> Optional[str]:
    """Get API key for a provider, checking env vars then config file.

    Args:
        provider: Provider name ("openrouter" or "openai")

    Returns:
        API key string, or None if not found
    """
    # Map provider to env var and config key
    env_vars = {
        "openrouter": "OPENROUTER_API_KEY",
        "openai": "OPENAI_API_KEY",
    }
    config_keys = {
        "openrouter": "openrouter_api_key",
        "openai": "openai_api_key",
    }

    env_var = env_vars.get(provider)
    config_key = config_keys.get(provider)

    if not env_var or not config_key:
        return None

    # Check environment first (takes precedence)
    env_value = os.environ.get(env_var)
    if env_value:
        return env_value

    # Fall back to config file
    config = load_config()
    return config.get(config_key)


def prompt_for_llm_setup() -> bool:
    """Interactively prompt user to set up LLM configuration.

    Guides user through choosing an LLM provider and entering their API key.
    Saves the configuration to the config file.

    Returns:
        True if setup completed, False if user declined
    """
    print("\n" + "=" * 60)
    print("LLM Backend Setup")
    print("=" * 60)
    print("\nNo LLM backend is configured. Would you like to set one up?")
    print("This enables AI-assisted analysis plan generation.\n")

    response = input("Set up LLM backend now? [y/N]: ").strip().lower()
    if response not in ("y", "yes"):
        print("\nSkipping LLM setup. You can configure it later by setting")
        print("environment variables or running this setup again.")
        return False

    print("\nChoose your LLM provider:\n")
    print("  1. OpenRouter (recommended - free tier available)")
    print("     Get a free API key at: https://openrouter.ai/keys")
    print()
    print("  2. OpenAI")
    print("     Requires an API key from: https://platform.openai.com/api-keys")
    print()
    print("  3. Local models (via 'llm' package)")
    print("     Requires: pip install hypergumbo[llm-local]")
    print()

    choice = input("Enter choice [1-3]: ").strip()

    config = load_config()

    if choice == "1":
        print("\nEnter your OpenRouter API key:")
        print("(Get one free at https://openrouter.ai/keys)")
        api_key = input("API key: ").strip()
        if api_key:
            config["openrouter_api_key"] = api_key
            save_config(config)
            print("\n✓ OpenRouter API key saved to ~/.config/hypergumbo/config.json")
            return True
        else:
            print("\nNo API key entered. Setup cancelled.")
            return False

    elif choice == "2":
        print("\nEnter your OpenAI API key:")
        api_key = input("API key: ").strip()
        if api_key:
            config["openai_api_key"] = api_key
            save_config(config)
            print("\n✓ OpenAI API key saved to ~/.config/hypergumbo/config.json")
            return True
        else:
            print("\nNo API key entered. Setup cancelled.")
            return False

    elif choice == "3":
        print("\nTo use local models, install the llm package:")
        print("  pip install hypergumbo[llm-local]")
        print("\nThen configure your preferred model with:")
        print("  llm models")
        print("  llm keys set <model>")
        return True

    else:
        print("\nInvalid choice. Setup cancelled.")
        return False
