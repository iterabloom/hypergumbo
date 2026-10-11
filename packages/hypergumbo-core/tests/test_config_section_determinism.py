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

INV-rikor: the embedding/hybrid path (the CLI default) had the same defect
one layer down. ``_collect_config_content`` iterated the frozenset
``_MONOREPO_MANIFEST_NAMES`` and unsorted globs, and its discovery caller a
``set``; ``_extract_config_embedding`` keeps the first ``max_config_files``
(15) candidates that yield chunks and its first-pass cross-file dedup keeps
whichever near-duplicate comes first, so on a monorepo with more than 15
manifests WHICH files the section shows followed the hash salt. The
monorepo fixture below has 32 manifests, so the cap is exercised; the
subprocess test runs the embedding extraction under three salts, and the
in-process tests pin the candidate order and the cap.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


_MANIFESTS = {
    "package.json": lambda n: (
        '{"name": "%s", "version": "1.0.0", "dependencies": {"express": "4"}}' % n
    ),
    "pyproject.toml": lambda n: (
        '[project]\nname = "%s"\nversion = "0.1.0"\ndependencies = ["flask"]\n' % n
    ),
    "Cargo.toml": lambda n: (
        '[package]\nname = "%s"\nversion = "0.1.0"\nedition = "2021"\n' % n
    ),
    "go.mod": lambda n: (
        "module example.com/%s\n\ngo 1.21\nrequire github.com/gin-gonic/gin v1\n" % n
    ),
}


def _monorepo(root: Path) -> Path:
    """32 depth-2 manifests (8 packages x 4 kinds): over the 15-file cap."""
    for i in range(8):
        for kind, body in _MANIFESTS.items():
            pkg = root / "packages" / f"pkg{i}{kind.split('.')[0].lower()}"
            pkg.mkdir(parents=True, exist_ok=True)
            (pkg / kind).write_text(body(pkg.name))
    (root / "main.py").write_text("print(1)\n")
    return root


def test_monorepo_manifest_names_keep_declaration_order() -> None:
    assert isinstance(sketch._MONOREPO_MANIFEST_NAMES, tuple)
    assert list(sketch._MONOREPO_MANIFEST_NAMES) == [
        f for f in sketch.CONFIG_FILES if "*" not in f
    ]


def test_monorepo_candidates_grouped_by_kind_then_sorted(tmp_path: Path) -> None:
    root = _monorepo(tmp_path)
    content, _seen = sketch._collect_config_content(root)
    sources = [source for source, _ in content]
    rank = {name: i for i, name in enumerate(sketch.CONFIG_FILES)}
    expected = sorted(
        sources, key=lambda s: (rank[Path(s).name], Path(s).parts)
    )
    assert sources == expected
    assert len(sources) == 32


def test_discovered_path_already_collected_is_not_appended_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery yields unresolved paths; seen_paths holds resolved ones."""
    monkeypatch.chdir(tmp_path)
    _monorepo(Path("repo"))
    rel_root = Path("repo")
    unresolved = rel_root / "packages" / "pkg0cargo" / "Cargo.toml"
    monkeypatch.setattr(
        sketch, "_discover_config_files_embedding", lambda _root: {unresolved}
    )
    content = sketch._collect_config_content_with_discovery(rel_root)
    sources = [source for source, _ in content]
    assert len(sources) == len(set(sources)) == 32


# A stand-in for the sentence-transformers model: a character histogram per
# text. Deterministic, str-hash-free and weight-free, so these tests measure
# the candidate ORDER and the cap, not the model (CI has no torch; numpy is
# still required by the code under test, hence importorskip).
class _HistogramModel:
    def encode(self, texts: list[str], convert_to_numpy: bool = True) -> object:
        import numpy as np

        out = np.zeros((len(texts), 64), dtype=np.float32)
        for row, text in enumerate(texts):
            for ch in text:
                out[row, ord(ch) % 64] += 1.0
        return out


# The same stand-in, installed in a fresh interpreter for the salted runs.
_FAKE_MODEL_SRC = (
    inspect.getsource(_HistogramModel)
    + "from hypergumbo_core import sketch_embeddings\n"
    + "sketch_embeddings._load_embedding_model = _HistogramModel\n"
)


def _salted_runs(code: str, root: Path) -> set[str]:
    outputs = set()
    for salt in ("0", "1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": salt}
        result = subprocess.run(
            [sys.executable, "-c", code, str(root)],
            capture_output=True, text=True, env=env, check=True,
        )
        outputs.add(result.stdout)
    return outputs


def test_embedding_extraction_stops_at_the_file_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("numpy")
    from hypergumbo_core import sketch_embeddings

    monkeypatch.setattr(sketch_embeddings, "_load_embedding_model", _HistogramModel)
    root = _monorepo(tmp_path)
    candidates = [
        s for s, _ in sketch._collect_config_content_with_discovery(root)
    ]
    assert len(candidates) > 15
    lines = sketch._extract_config_embedding(root, max_config_files=15)
    shown = {ln[1:-1] for ln in lines if ln.startswith("[") and ln.endswith("]")}
    assert shown
    assert shown <= set(candidates[:15])


def test_same_candidates_under_different_hash_salts(tmp_path: Path) -> None:
    """Behavioural evidence that needs no embedding stack at all."""
    code = (
        "import sys; from pathlib import Path;"
        "from hypergumbo_core.sketch import _collect_config_content_with_discovery;"
        "srcs = [s for s, _ in _collect_config_content_with_discovery("
        "Path(sys.argv[1]))];"
        "sys.stdout.write('\\n'.join(srcs))"
    )
    outputs = _salted_runs(code, _monorepo(tmp_path))
    assert len(outputs) == 1
    assert len(next(iter(outputs)).splitlines()) == 32


def test_same_embedding_section_under_different_hash_salts(tmp_path: Path) -> None:
    pytest.importorskip("numpy")
    code = _FAKE_MODEL_SRC + (
        "import sys\nfrom pathlib import Path\n"
        "from hypergumbo_core.sketch import _extract_config_embedding\n"
        "sys.stdout.write('\\n'.join(_extract_config_embedding("
        "Path(sys.argv[1]))))\n"
    )
    outputs = _salted_runs(code, _monorepo(tmp_path))
    assert len(outputs) == 1
    assert "[packages/" in next(iter(outputs))
