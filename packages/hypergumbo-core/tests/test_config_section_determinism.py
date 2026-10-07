# SPDX-License-Identifier: AGPL-3.0-or-later
"""The sketch's Configuration section is the same in every process.

WI-vosag analogue, found by diffing two cold thc sketches: the root
``package.json`` and ``mix.exs`` lines swapped places between runs. Four
loops in ``sketch._extract_config_heuristic`` iterated a ``set`` — the
root-manifest list (``CONFIG_FILES = list({...})``), package.json's
``INTERESTING_DEPS``, go.mod's and the Gemfile's interesting-dependency
sets — so their order followed the per-process str-hash salt, and because
the section is cut to ``max_lines`` WHICH manifests survive the cut varied
too. The subprocess test is the behavioral evidence (three hash salts, one
answer); the in-process tests pin the ordering contracts.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from hypergumbo_core import sketch


def _fixture(root: Path) -> Path:
    (root / "package.json").write_text(
        '{"name": "x", "dependencies": {"react": "1", "express": "2", '
        '"vue": "3", "next": "4", "prisma": "5", "redis": "6"}}'
    )
    (root / "mix.exs").write_text('def project, do: [app: :x, version: "0.1.0"]\n')
    (root / "go.mod").write_text(
        "module example.com/x\n\ngo 1.22\n\nrequire (\n"
        "\tgithub.com/gin-gonic/gin v1\n\tgithub.com/gorilla/mux v1\n"
        "\tgithub.com/lib/pq v1\n\tgithub.com/jackc/pgx v5\n)\n"
    )
    (root / "Gemfile").write_text(
        "gem 'rails'\ngem 'puma'\ngem 'pg'\ngem 'sidekiq'\ngem 'redis'\n"
    )
    (root / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "0.1.0"\n')
    (root / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1"\n')
    for sub in ("a", "b", "c"):
        (root / sub).mkdir()
        (root / sub / "LICENSE").write_text("MIT License\n")
    return root


def test_config_files_keep_declaration_order() -> None:
    expected = list(
        dict.fromkeys(
            f for files in sketch.CONFIG_FILES_BY_LANG.values() for f in files
        )
    )
    assert sketch.CONFIG_FILES == expected


def test_dependency_lines_are_sorted(tmp_path: Path) -> None:
    lines = sketch._extract_config_heuristic(_fixture(tmp_path))
    by_file = {line.split(":", 1)[0]: line for line in lines}
    gem = by_file["Gemfile"].split(": ", 1)[1].split("; ")
    assert gem == sorted(gem)
    go = [p for p in by_file["go.mod"].split(": ", 1)[1].split("; ") if ":" not in p]
    assert go == sorted(go)
    pkg = by_file["package.json"].split("; ")[1:]
    dep_names = [p.split(":")[0] for p in pkg if not p.startswith(("version", "license"))]
    assert dep_names == sorted(dep_names)


def test_same_section_under_different_hash_salts(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    code = (
        "import sys; from pathlib import Path;"
        "from hypergumbo_core.sketch import _extract_config_heuristic;"
        "sys.stdout.write('\\n'.join(_extract_config_heuristic(Path(sys.argv[1]))))"
    )
    outputs = set()
    for salt in ("1", "2", "3", "4"):
        env = {**os.environ, "PYTHONHASHSEED": salt}
        result = subprocess.run(
            [sys.executable, "-c", code, str(root)],
            capture_output=True, text=True, env=env, check=True,
        )
        outputs.add(result.stdout)
    assert len(outputs) == 1
