# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-kazob: the Python file-kind node carries the module docstring.

Every source file carries a substantive module docstring (a project
requirement), but the FILE/MODULE node never received it — 0 of 902
file-kind nodes carried a docstring. Analyzers attached docstrings to
callable/class symbols but never to the file node. This module verifies
the py analyzer now stamps the module's one-line docstring summary onto
the ``kind="file"`` Symbol at construction.

The fix also broadens the file-node emission condition: a file whose
only module-level statement is its docstring (no executable code) was
previously left to the language-agnostic orchestrator synthesizer, which
cannot read a Python docstring; py.py now emits the file node (carrying
the docstring) whenever the module has executable code OR a docstring,
relying on the existing INV-hojus dedup against the synthesizer.
"""

from pathlib import Path

from hypergumbo_lang_mainstream.py import analyze_python


def _write(tmp_path: Path, content: str, name: str = "m.py") -> None:
    (tmp_path / name).write_text(content)


def _file_node(result):
    files = [s for s in result.symbols if s.kind == "file"]
    return files[0] if files else None


class TestModuleDocstringOnFileNode:
    def test_docstring_with_module_code(self, tmp_path: Path) -> None:
        _write(tmp_path, '''"""Profiles a repo and emits a behavior map."""
import os

CONFIG = os.environ.get("X")
''')
        fn = _file_node(analyze_python(tmp_path))
        assert fn is not None
        assert fn.docstring == "Profiles a repo and emits a behavior map."

    def test_docstring_only_file_still_emits_file_node(self, tmp_path: Path) -> None:
        # No executable module-level code and no defs — only a docstring.
        # Before WI-kazob, py.py emitted no file node here (the synthesizer
        # made one, without the docstring); now py.py emits it with the docstring.
        _write(tmp_path, '"""Package marker module."""\n')
        fn = _file_node(analyze_python(tmp_path))
        assert fn is not None
        assert fn.docstring == "Package marker module."

    def test_library_module_imports_and_defs_only(self, tmp_path: Path) -> None:
        # The dominant real case: docstring + imports + a class/function but no
        # top-level executable statement (_has_module_level_code is False).
        _write(tmp_path, '''"""Linker registry and dispatch."""
import os


def register():
    return os.getcwd()
''')
        fn = _file_node(analyze_python(tmp_path))
        assert fn is not None
        assert fn.docstring == "Linker registry and dispatch."

    def test_multiline_docstring_first_line_truncated_to_80(self, tmp_path: Path) -> None:
        long_first = "A" * 100
        _write(tmp_path, f'"""{long_first}\n\nMore detail here.\n"""\nimport os\nos.getcwd()\n')
        fn = _file_node(analyze_python(tmp_path))
        assert fn is not None
        assert fn.docstring == "A" * 80

    def test_no_docstring_leaves_file_docstring_none(self, tmp_path: Path) -> None:
        _write(tmp_path, 'import os\nos.getcwd()\n')
        fn = _file_node(analyze_python(tmp_path))
        assert fn is not None  # has module-level code → file node emitted
        assert fn.docstring is None

    def test_no_docstring_no_code_emits_no_py_file_node(self, tmp_path: Path) -> None:
        # Neither docstring nor module-level code → py.py emits no file node
        # (the orchestrator synthesizer covers it). Regression guard that the
        # broadened condition didn't start emitting for empty-ish files.
        _write(tmp_path, 'import os\n\n\ndef f():\n    return os.getcwd()\n')
        fn = _file_node(analyze_python(tmp_path))
        assert fn is None
