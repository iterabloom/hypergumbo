# SPDX-License-Identifier: AGPL-3.0-or-later
"""The copy-paste install steps for the SCIP backends' binaries.

Each renderer reads the machine through injected lookups, so every state is
reached without touching the real ``PATH``: what is missing decides which
steps appear, and a step the machine does not need must not appear.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from hypergumbo_core.install_steps import (
    NVM_REFERENCE,
    NVM_VERSION,
    RUSTUP_REFERENCE,
    SCIP_PYTHON_REFERENCE,
    rust_analyzer_install_steps,
    scip_python_install_steps,
)

HOME = Path("/home/u")
LOCAL_BIN_ON_PATH = {"PATH": "/usr/bin:/home/u/.local/bin", "SHELL": "/bin/bash"}
LOCAL_BIN_OFF_PATH = {"PATH": "/usr/bin", "SHELL": "/bin/bash"}


def _which(*present: str):
    def resolve(name: str) -> Optional[str]:
        return f"/usr/bin/{name}" if name in present else None
    return resolve


def _commands(text: str) -> list[str]:
    """The pasteable lines, comments dropped."""
    return [line.strip() for line in text.splitlines()
            if line.startswith("  ") and not line.strip().startswith("#")]


class TestScipPython:
    def test_with_npm_and_local_bin_on_path_one_install_line_and_a_check(self) -> None:
        text = scip_python_install_steps(which=_which("npm"), environ=LOCAL_BIN_ON_PATH,
                                         home=HOME, platform="linux")
        assert _commands(text) == [
            'npm install -g --prefix "$HOME/.local" @sourcegraph/scip-python@0.6.6',
            "scip-python --version",
        ]
        assert "not installed, or not on PATH" in text
        assert f"Reference: {SCIP_PYTHON_REFERENCE}" in text
        assert NVM_REFERENCE not in text

    def test_local_bin_off_path_adds_it_now_and_in_the_shell_profile(self) -> None:
        text = scip_python_install_steps(which=_which("npm"), environ=LOCAL_BIN_OFF_PATH,
                                         home=HOME, platform="linux")
        assert _commands(text)[1:3] == [
            'export PATH="$HOME/.local/bin:$PATH"',
            "echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> ~/.bashrc",
        ]

    def test_a_zsh_user_gets_the_zsh_profile(self) -> None:
        text = scip_python_install_steps(which=_which("npm"), home=HOME, platform="linux",
                                         environ={"PATH": "/usr/bin", "SHELL": "/usr/bin/zsh"})
        assert ">> ~/.zshrc" in text

    def test_a_tilde_path_entry_counts_as_on_path(self) -> None:
        text = scip_python_install_steps(which=_which("npm"), home=Path.home(), platform="linux",
                                         environ={"PATH": "~/.local/bin:/usr/bin"})
        assert "export PATH" not in text

    def test_without_npm_node_comes_first_from_a_pinned_nvm(self) -> None:
        text = scip_python_install_steps(which=_which(), environ=LOCAL_BIN_ON_PATH,
                                         home=HOME, platform="linux")
        assert _commands(text)[:4] == [
            f"curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/{NVM_VERSION}/install.sh | bash",
            '. "$HOME/.nvm/nvm.sh"',
            "nvm install --lts",
            'npm install -g --prefix "$HOME/.local" @sourcegraph/scip-python@0.6.6',
        ]
        assert f"Reference: {NVM_REFERENCE}" in text

    def test_nvm_under_xdg_config_home_is_loaded_from_there(self) -> None:
        text = scip_python_install_steps(which=_which(), home=HOME, platform="linux",
                                         environ={**LOCAL_BIN_ON_PATH, "XDG_CONFIG_HOME": "/home/u/.config"})
        assert '. "$XDG_CONFIG_HOME/nvm/nvm.sh"' in _commands(text)

    def test_a_binary_that_resolves_but_fails_is_named(self) -> None:
        text = scip_python_install_steps(which=_which("npm", "scip-python"), environ=LOCAL_BIN_ON_PATH,
                                         home=HOME, platform="linux")
        assert text.startswith("/usr/bin/scip-python was found, but `scip-python --version` failed.")

    def test_windows_is_told_the_commands_are_posix(self) -> None:
        text = scip_python_install_steps(which=_which("npm"), environ=LOCAL_BIN_ON_PATH,
                                         home=HOME, platform="win32")
        assert "These commands are for Linux and macOS" in text

    def test_defaults_read_the_real_machine(self) -> None:
        assert "scip-python --version" in scip_python_install_steps()


class TestRustAnalyzer:
    def test_with_rustup_the_component_and_a_check(self) -> None:
        text = rust_analyzer_install_steps(which=_which("rustup"), environ=LOCAL_BIN_ON_PATH,
                                           home=HOME, platform="linux", is_file=lambda _p: False)
        assert _commands(text) == ["rustup component add rust-analyzer", "rust-analyzer --version"]
        assert f"Reference: {RUSTUP_REFERENCE}" in text

    def test_without_rustup_the_official_installer_comes_first(self) -> None:
        text = rust_analyzer_install_steps(which=_which(), environ=LOCAL_BIN_ON_PATH,
                                           home=HOME, platform="linux", is_file=lambda _p: False)
        assert _commands(text)[:3] == [
            "curl --proto '=https' --tlsv1.2 https://sh.rustup.rs -sSf | sh",
            '. "$HOME/.cargo/env"',
            "rustup component add rust-analyzer",
        ]

    def test_rustup_installed_off_path_is_put_on_path_not_reinstalled(self) -> None:
        seen: list[Path] = []

        def is_file(p: Path) -> bool:
            seen.append(p)
            return True

        text = rust_analyzer_install_steps(which=_which(), environ=LOCAL_BIN_OFF_PATH,
                                           home=HOME, platform="linux", is_file=is_file)
        assert seen == [HOME / ".cargo" / "bin" / "rustup"]
        assert "sh.rustup.rs" not in text
        assert _commands(text)[:2] == ['. "$HOME/.cargo/env"', "echo '. \"$HOME/.cargo/env\"' >> ~/.bashrc"]

    def test_cargo_home_is_honoured(self) -> None:
        seen: list[Path] = []
        text = rust_analyzer_install_steps(which=_which(), home=HOME, platform="linux",
                                           environ={"PATH": "/usr/bin", "CARGO_HOME": "/opt/cargo"},
                                           is_file=lambda p: seen.append(p) or False)
        assert seen == [Path("/opt/cargo/bin/rustup")]
        assert '. "$CARGO_HOME/env"' in _commands(text)

    def test_a_binary_that_resolves_but_fails_is_named(self) -> None:
        text = rust_analyzer_install_steps(which=_which("rustup", "rust-analyzer"), environ=LOCAL_BIN_ON_PATH,
                                           home=HOME, platform="linux")
        assert text.startswith("/usr/bin/rust-analyzer was found, but `rust-analyzer --version` failed.")

    def test_defaults_read_the_real_machine(self) -> None:
        assert "rust-analyzer --version" in rust_analyzer_install_steps()
