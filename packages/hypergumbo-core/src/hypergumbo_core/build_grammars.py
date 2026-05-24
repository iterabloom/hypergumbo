# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Build tree-sitter grammars from source for languages not available on PyPI.

This module provides functionality to build tree-sitter-lean, tree-sitter-wolfram,
and tree-sitter-circom from their source repositories. These grammars are not
published to PyPI and must be compiled from source. The set must stay in sync
with `scripts/build-source-grammars` (the CI/dev path).

WI-fipab-kivoj: source is vendored under ``vendor/tree-sitter-{lean,
wolfram,circom}/`` (plain directory copy from upstream at a pinned commit).
Both this module and ``scripts/build-source-grammars`` read from the vendor
tree instead of cloning at build time, so the build is offline-deterministic
and independent of upstream git hygiene. The upstream URL and pinned commit
for each grammar live in ``vendor/tree-sitter-<name>/UPSTREAM``;
``GrammarSpec.repo_url`` is kept here as documentation metadata for the
re-sync procedure (see ``docs/grammars/vendor-sync.md``), not as a build-
time fetch target.

Requirements:
- A C/C++ compiler (gcc, clang, or MSVC)
- Python development headers

The build process:
1. Locates the vendored grammar source under ``vendor/tree-sitter-<name>/``
2. Generates Python binding code (C extension)
3. Builds and installs via pip
"""
from __future__ import annotations

import subprocess  # nosec B404 - required for pip commands
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .safety_zones import tmp_artifact_mkdir, tmp_artifact_rmtree, tmp_artifact_write


# Repo root resolution. This module lives at
# packages/hypergumbo-core/src/hypergumbo_core/build_grammars.py; the
# vendor/ tree is at the project root four levels up.
_REPO_ROOT = Path(__file__).resolve().parents[4]
VENDOR_ROOT = _REPO_ROOT / "vendor"


@dataclass
class GrammarSpec:
    """Specification for a tree-sitter grammar to build from source.

    ``repo_url`` is documentation metadata — the build path reads source
    from ``vendor/tree-sitter-<name>/`` (WI-fipab-kivoj), not from a fresh
    clone of ``repo_url``. The field is kept to support the re-sync
    procedure in ``docs/grammars/vendor-sync.md``.
    """

    name: str
    repo_url: str
    function_name: str
    scanner_type: Literal["none", "c", "cc"]


# Grammars that need to be built from source
SOURCE_GRAMMARS = [
    GrammarSpec(
        name="lean",
        repo_url="https://github.com/Julian/tree-sitter-lean.git",
        function_name="tree_sitter_lean",
        scanner_type="c",
    ),
    GrammarSpec(
        name="wolfram",
        repo_url="https://github.com/bostick/tree-sitter-wolfram.git",
        function_name="tree_sitter_wolfram",
        scanner_type="cc",
    ),
    GrammarSpec(
        name="circom",
        repo_url="https://github.com/Decurity/tree-sitter-circom.git",
        function_name="tree_sitter_circom",
        scanner_type="none",
    ),
]


def vendor_dir_for(name: str) -> Path:
    """Path to the vendored tree-sitter grammar source for ``name``."""
    return VENDOR_ROOT / f"tree-sitter-{name}"


def _generate_binding_c(function_name: str) -> str:
    """Generate the C binding code for a tree-sitter grammar."""
    return f'''#include <Python.h>

typedef struct TSLanguage TSLanguage;

TSLanguage *{function_name}(void);

static PyObject* _binding_language(PyObject *Py_UNUSED(self), PyObject *Py_UNUSED(args)) {{
    return PyCapsule_New({function_name}(), "tree_sitter.Language", NULL);
}}

static PyMethodDef methods[] = {{
    {{"language", _binding_language, METH_NOARGS,
     "Get the tree-sitter language for this grammar."}},
    {{NULL, NULL, 0, NULL}}
}};

static struct PyModuleDef module = {{
    .m_base = PyModuleDef_HEAD_INIT,
    .m_name = "_binding",
    .m_doc = NULL,
    .m_size = -1,
    .m_methods = methods
}};

PyMODINIT_FUNC PyInit__binding(void) {{
    return PyModule_Create(&module);
}}
'''


def _generate_init_py() -> str:
    """Generate the __init__.py for a tree-sitter grammar package."""
    return '''"""Grammar for tree-sitter."""

from ._binding import language

__all__ = ["language"]
'''


def _generate_setup_py(
    name: str,
    module_name: str,
    repo_dir: str,
    scanner_type: Literal["none", "c", "cc"],
) -> str:
    """Generate setup.py for building the grammar."""
    if scanner_type == "c":
        sources = (
            f"os.path.join(SRC_DIR, 'parser.c'), "
            f"os.path.join(SRC_DIR, 'scanner.c'), "
            f"'{module_name}/binding.c'"
        )
        extra_compile_args = "['-std=c11', '-Wno-unused-parameter']"
    elif scanner_type == "cc":
        sources = (
            f"os.path.join(SRC_DIR, 'parser.c'), "
            f"os.path.join(SRC_DIR, 'scanner.cc'), "
            f"'{module_name}/binding.c'"
        )
        extra_compile_args = "['-std=c++14', '-Wno-unused-parameter']"
    else:
        sources = f"os.path.join(SRC_DIR, 'parser.c'), '{module_name}/binding.c'"
        extra_compile_args = "['-std=c11', '-Wno-unused-parameter']"

    return f'''"""Setup for tree-sitter-{name} Python bindings."""
from setuptools import setup, Extension
import os

TREE_SITTER_DIR = {repo_dir!r}
SRC_DIR = os.path.join(TREE_SITTER_DIR, 'src')

ext_module = Extension(
    '{module_name}._binding',
    sources=[{sources}],
    include_dirs=[SRC_DIR],
    extra_compile_args={extra_compile_args},
)

setup(
    name='tree-sitter-{name}',
    version='0.1.0',
    description='{name} grammar for tree-sitter',
    packages=['{module_name}'],
    ext_modules=[ext_module],
    python_requires='>=3.10',
    install_requires=['tree-sitter>=0.21.0'],
)
'''


def _run_command(
    cmd: list[str],
    cwd: str | Path | None = None,
    quiet: bool = False,
) -> subprocess.CompletedProcess:
    """Run a command and handle errors."""
    kwargs: dict = {"cwd": cwd, "check": True}
    if quiet:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    return subprocess.run(cmd, **kwargs)  # noqa: S603  # nosec B603


def build_grammar(
    spec: GrammarSpec,
    build_dir: Path,
    quiet: bool = False,
) -> bool:
    """
    Build and install a single tree-sitter grammar from source.

    Args:
        spec: Grammar specification
        build_dir: Directory for cloning and building
        quiet: Suppress output if True

    Returns:
        True if successful, False otherwise
    """
    # WI-fipab-kivoj: read from the vendored grammar directory instead of
    # cloning the upstream repo. ``build_dir`` is used only for the
    # generated Python-binding scaffolding (setup.py, binding.c, __init__.py)
    # and pip's intermediate artifacts.
    repo_dir = vendor_dir_for(spec.name)
    pkg_dir = build_dir / f"tree-sitter-{spec.name}-py"
    module_name = f"tree_sitter_{spec.name}"

    if not quiet:  # pragma: no cover
        print(f"\n=== Building tree-sitter-{spec.name} ===")

    if not (repo_dir / "src").exists():
        if not quiet:  # pragma: no cover
            print(f"Error: vendored source not found at {repo_dir}/src")
            print("See docs/grammars/vendor-sync.md to restore the vendor tree.")
        return False

    # Create Python package directory
    if pkg_dir.exists():
        tmp_artifact_rmtree(pkg_dir)
    # INV-zudak: route scaffolding mkdir through the tmp_artifact wrapper.
    tmp_artifact_mkdir(pkg_dir, parents=True)
    tmp_artifact_mkdir(pkg_dir / module_name)

    # Generate source files
    tmp_artifact_write(
        pkg_dir / module_name / "__init__.py", _generate_init_py(),
    )
    tmp_artifact_write(
        pkg_dir / module_name / "binding.c",
        _generate_binding_c(spec.function_name),
    )
    tmp_artifact_write(
        pkg_dir / "setup.py",
        _generate_setup_py(spec.name, module_name, str(repo_dir), spec.scanner_type),
    )

    # Build and install
    if not quiet:  # pragma: no cover
        print("Building and installing...")
    try:
        cmd = [sys.executable, "-m", "pip", "install", str(pkg_dir)]
        if quiet:
            cmd.append("--quiet")
        _run_command(cmd)
    except subprocess.CalledProcessError as e:
        if not quiet:  # pragma: no cover
            print(f"Error installing package: {e}")
            print("Make sure you have a C/C++ compiler installed:")
            print("  - Linux: apt install build-essential")
            print("  - macOS: xcode-select --install")
            print("  - Windows: Install Visual Studio Build Tools")
        return False

    if not quiet:  # pragma: no cover
        print(f"Successfully installed tree-sitter-{spec.name}")
    return True


def build_all_grammars(
    build_dir: Path | None = None,
    quiet: bool = False,
) -> dict[str, bool]:
    """
    Build all source-only tree-sitter grammars.

    Args:
        build_dir: Directory for cloning and building (default: temp dir)
        quiet: Suppress output if True

    Returns:
        Dict mapping grammar name to success status
    """
    if build_dir is None:
        build_dir = Path(tempfile.gettempdir()) / "ts-grammar-build"

    # INV-zudak: route build-dir scaffolding through the tmp_artifact wrapper.
    tmp_artifact_mkdir(build_dir, parents=True, exist_ok=True)

    if not quiet:  # pragma: no cover
        print("Building tree-sitter grammars from source...")
        print(f"Build directory: {build_dir}")

    results = {}
    for spec in SOURCE_GRAMMARS:
        results[spec.name] = build_grammar(spec, build_dir, quiet=quiet)

    if not quiet:  # pragma: no cover
        print("\n=== Build Summary ===")
        for name, success in results.items():
            status = "✓" if success else "✗"
            print(f"  {status} tree-sitter-{name}")

        # Verify installation
        print("\nVerifying installation...")
        for spec in SOURCE_GRAMMARS:
            if results[spec.name]:
                try:
                    _run_command(
                        [
                            sys.executable,
                            "-c",
                            f"import tree_sitter_{spec.name}; "
                            f"print('tree-sitter-{spec.name}:', "
                            f"tree_sitter_{spec.name}.language())",
                        ]
                    )
                except subprocess.CalledProcessError:
                    print(f"  Warning: tree-sitter-{spec.name} import failed")

    return results


def check_grammar_availability() -> dict[str, bool]:
    """
    Check which source-only grammars are currently available.

    Returns:
        Dict mapping grammar name to availability status
    """
    results = {}
    for spec in SOURCE_GRAMMARS:
        module_name = f"tree_sitter_{spec.name}"
        try:
            __import__(module_name)
            results[spec.name] = True
        except ImportError:
            results[spec.name] = False
    return results
