# SPDX-License-Identifier: AGPL-3.0-or-later
"""Copy-paste install steps for the opt-in backends' external binaries.

The two SCIP backends run programs hypergumbo does not ship: ``scip-python``
(an npm package) and ``rust-analyzer`` (a rustup component). hypergumbo does
not install either for the user — a global npm prefix and a Rust toolchain
are machine-level choices — so when one is missing, the next best thing is
the exact commands that install it, in order, ready to paste into a
terminal, plus a reference page for the reader who wants the background.

"Exact" is the point, and it is why the steps are computed rather than
fixed. A single canned line was wrong for real machines: ``npm install -g``
fails without root wherever npm's global prefix is ``/usr``; ``rustup
component add`` cannot help a user without rustup; and a binary installed
into a folder that is not on ``PATH`` looks exactly like a binary that is
not installed, because every probe resolves it through ``shutil.which``.
So each renderer looks at the machine first — which of ``npm`` / ``rustup``
resolve, whether ``~/.local/bin`` is on ``PATH``, whether rustup sits in
``~/.cargo/bin`` unseen — and emits only the steps that machine still needs.
The looking is lookups only (``which``, an environment read, one
``Path.is_file``): rendering advice runs no program.

Choices the steps make, and why:

* scip-python goes into ``~/.local`` with ``npm install -g --prefix``, which
  needs no root and puts the binary in ``~/.local/bin`` — the folder pipx
  already uses for hypergumbo itself, so it is usually on ``PATH`` already.
* Node.js, when absent, comes from nvm at a pinned release: one route that
  needs no root on Linux and macOS and yields a Node new enough for
  scip-python (which requires Node 16 or newer).
* rustup comes from its official installer one-liner, as the Rust book
  gives it.

The commands are POSIX-shell; on Windows the renderers say so and point at
the references instead of pretending.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Callable, Mapping, Optional

from .scip_python_install import (
    SCIP_PYTHON_BINARY,
    SCIP_PYTHON_NPM_PACKAGE,
    SCIP_PYTHON_PINNED_VERSION,
)

#: The nvm release the Node.js steps pin (its README's install one-liner).
NVM_VERSION = "v0.41.0"

SCIP_PYTHON_REFERENCE = "https://github.com/sourcegraph/scip-python#readme"
NVM_REFERENCE = "https://github.com/nvm-sh/nvm#install--update-script"
RUSTUP_REFERENCE = "https://doc.rust-lang.org/book/ch01-01-installation.html"

Which = Callable[[str], Optional[str]]


def _on_path(folder: Path, environ: Mapping[str, str]) -> bool:
    """Whether *folder* is one of the entries of ``environ['PATH']``."""
    target = os.path.normpath(folder)
    return any(
        os.path.normpath(os.path.expanduser(entry)) == target
        for entry in environ.get("PATH", "").split(os.pathsep) if entry
    )


def _profile(environ: Mapping[str, str]) -> str:
    """The startup file of the user's shell: zsh's, else bash's."""
    return "~/.zshrc" if Path(environ.get("SHELL", "")).name == "zsh" else "~/.bashrc"


def _render(intro: str, commands: list[str], references: list[str], platform: str) -> str:
    lines = [intro, "", "Paste these commands into a terminal:", ""]
    lines += [f"  {c}" for c in commands]
    if platform == "win32":
        lines += ["", "These commands are for Linux and macOS; on Windows, follow the references."]
    lines += [""] + [f"Reference: {url}" for url in references]
    return "\n".join(lines)


def scip_python_install_steps(
    *,
    which: Optional[Which] = None,
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
    platform: Optional[str] = None,
) -> str:
    """The steps that get a working ``scip-python`` onto ``PATH`` on this machine."""
    resolve = which if which is not None else shutil.which
    env = environ if environ is not None else os.environ
    home_dir = home if home is not None else Path.home()
    found = resolve(SCIP_PYTHON_BINARY)
    package = f"{SCIP_PYTHON_NPM_PACKAGE}@{SCIP_PYTHON_PINNED_VERSION}"

    if found is None:
        intro = f"scip-python {SCIP_PYTHON_PINNED_VERSION} is not installed, or not on PATH."
    else:
        intro = (f"{found} was found, but `scip-python --version` failed. It runs on "
                 "Node.js 16 or newer; reinstalling it fixes a broken install.")
    commands: list[str] = []
    references = [SCIP_PYTHON_REFERENCE]
    if resolve("npm") is None:
        nvm_dir = "$XDG_CONFIG_HOME/nvm" if env.get("XDG_CONFIG_HOME") else "$HOME/.nvm"
        commands += [
            "# Node.js and npm (nvm installs them without root):",
            f"curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/{NVM_VERSION}/install.sh | bash",
            f'. "{nvm_dir}/nvm.sh"',
            "nvm install --lts",
        ]
        references.append(NVM_REFERENCE)
    commands += [
        "# scip-python, into ~/.local/bin (no root needed):",
        f'npm install -g --prefix "$HOME/.local" {package}',
    ]
    if not _on_path(home_dir / ".local" / "bin", env):
        commands += [
            "# put ~/.local/bin on PATH, in this terminal and in new ones:",
            'export PATH="$HOME/.local/bin:$PATH"',
            f"echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> {_profile(env)}",
        ]
    commands += [
        f"# check: this prints {SCIP_PYTHON_PINNED_VERSION}",
        "scip-python --version",
    ]
    return _render(intro, commands, references, platform if platform is not None else sys.platform)


def rust_analyzer_install_steps(
    *,
    which: Optional[Which] = None,
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
    platform: Optional[str] = None,
    is_file: Optional[Callable[[Path], bool]] = None,
) -> str:
    """The steps that get a working ``rust-analyzer`` onto ``PATH`` on this machine."""
    resolve = which if which is not None else shutil.which
    env = environ if environ is not None else os.environ
    home_dir = home if home is not None else Path.home()
    exists = is_file if is_file is not None else Path.is_file
    found = resolve("rust-analyzer")

    if found is None:
        intro = "rust-analyzer is not installed, or not on PATH."
    else:
        intro = (f"{found} was found, but `rust-analyzer --version` failed. A rustup "
                 "proxy whose rust-analyzer component is not installed does this.")
    cargo_home = env.get("CARGO_HOME")
    cargo_env = '"$CARGO_HOME/env"' if cargo_home else '"$HOME/.cargo/env"'
    rustup_dir = Path(cargo_home) if cargo_home else home_dir / ".cargo"
    commands: list[str] = []
    if resolve("rustup") is None:
        if exists(rustup_dir / "bin" / "rustup"):
            commands += [
                f"# rustup is installed in {rustup_dir / 'bin'}, which is not on PATH;",
                "# add it, in this terminal and in new ones:",
                f". {cargo_env}",
                f"echo '. {cargo_env}' >> {_profile(env)}",
            ]
        else:
            commands += [
                "# rustup, the Rust toolchain installer (it asks before changing anything):",
                "curl --proto '=https' --tlsv1.2 https://sh.rustup.rs -sSf | sh",
                f". {cargo_env}",
            ]
    commands += [
        "# the rust-analyzer component (or: hypergumbo install-rust-analyzer):",
        "rustup component add rust-analyzer",
        "# check: this prints a version",
        "rust-analyzer --version",
    ]
    return _render(intro, commands, [RUSTUP_REFERENCE], platform if platform is not None else sys.platform)
