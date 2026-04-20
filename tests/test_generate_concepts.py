# SPDX-License-Identifier: AGPL-3.0-or-later
"""Smoke test for scripts/generate-concepts (WI-dajul)."""
from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "generate-concepts"


def _load_module():
    """Load the script as a module for direct function calls.

    The file is an executable script (no .py extension), so
    spec_from_file_location can't infer the loader — pass a
    SourceFileLoader explicitly.
    """
    loader = SourceFileLoader("generate_concepts", str(SCRIPT))
    spec = importlib.util.spec_from_loader("generate_concepts", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_script_produces_nonempty_registry(tmp_path: Path) -> None:
    """End-to-end: scan real YAMLs and linkers, produce a markdown file."""
    mod = _load_module()
    out = tmp_path / "CONCEPTS.md"
    mod.generate(out)
    text = out.read_text()
    # Basic shape assertions
    assert "# Concept Vocabulary Registry" in text
    assert "| Concept | Status | Producers (YAMLs) | Consumers" in text
    # The 'route' concept is always live (http.py and openapi.py consume it)
    assert "| `route` | live |" in text


def test_classify_live_inert_ghost() -> None:
    """classify() tags concepts correctly by producer/consumer presence."""
    mod = _load_module()
    producers = {"route": {"fastapi"}, "inert_one": {"demo"}}
    consumers = {"route": {"http.py"}, "ghost_one": {"somewhere.py"}}
    rows = mod.classify(producers, consumers)
    by_name = {name: status for name, _, _, status in rows}
    assert by_name["route"] == "live"
    assert by_name["inert_one"] == "inert"
    assert by_name["ghost_one"] == "ghost"


def test_producer_regex_handles_quoted_and_bare(tmp_path: Path) -> None:
    """_PRODUCER_RE matches `concept: name`, `concept: "name"`, and `concept: 'name'`."""
    mod = _load_module()
    yaml = tmp_path / "example.yaml"
    yaml.write_text(
        'patterns:\n'
        '  - concept: bare_name\n'
        '    decorator: "x"\n'
        '  - concept: "quoted_name"\n'
        '    decorator: "y"\n'
        "  - concept: 'single_quoted'\n"
        '    decorator: "z"\n'
    )
    producers = mod.scan_producers(tmp_path)
    assert "bare_name" in producers
    assert "quoted_name" in producers
    assert "single_quoted" in producers


def test_consumer_regex_finds_all_patterns(tmp_path: Path) -> None:
    """scan_consumers matches has_concept, get_concept, concept == 'x', and .get('concept') == 'x'."""
    mod = _load_module()
    src = tmp_path / "demo.py"
    src.write_text(
        'from foo import has_concept, get_concept\n'
        '\n'
        'def a(s):\n'
        '    return has_concept(s, "route")\n'
        '\n'
        'def b(s):\n'
        '    return get_concept(s, "model")\n'
        '\n'
        'def c(entry):\n'
        '    concept = entry.get("concept")\n'
        '    return concept == "middleware"\n'
        '\n'
        'def d(entry):\n'
        '    return entry.get("concept") == "command"\n'
    )
    consumers = mod.scan_consumers(tmp_path)
    assert "route" in consumers
    assert "model" in consumers
    assert "middleware" in consumers
    assert "command" in consumers


def test_check_mode_passes_when_file_matches(tmp_path: Path, monkeypatch) -> None:
    """--check returns 0 when the existing file matches the generated content."""
    mod = _load_module()
    out = tmp_path / "CONCEPTS.md"
    mod.generate(out)
    # main() with --check against the freshly written file should return 0
    rc = mod.main(["--check", "--out", str(out)])
    assert rc == 0


def test_check_mode_fails_when_file_missing(tmp_path: Path, capsys) -> None:
    """--check returns 1 when the target file doesn't exist."""
    mod = _load_module()
    out = tmp_path / "does_not_exist.md"
    rc = mod.main(["--check", "--out", str(out)])
    assert rc == 1


def test_check_mode_fails_when_file_stale(tmp_path: Path) -> None:
    """--check returns 1 when the target file's content differs."""
    mod = _load_module()
    out = tmp_path / "CONCEPTS.md"
    out.write_text("# Stale content\n")
    rc = mod.main(["--check", "--out", str(out)])
    assert rc == 1


def test_main_writes_and_reports(tmp_path: Path, capsys) -> None:
    """main() without --check writes the file and prints a summary line."""
    mod = _load_module()
    out = tmp_path / "CONCEPTS.md"
    rc = mod.main(["--out", str(out)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Wrote" in captured.out
    assert str(out) in captured.out
    assert out.exists()


def test_committed_file_is_up_to_date() -> None:
    """The checked-in docs/CONCEPTS.md must match what the script currently generates."""
    mod = _load_module()
    committed = REPO_ROOT / "docs" / "CONCEPTS.md"
    # Regenerate to an in-memory string and compare
    producers = mod.scan_producers(mod.FRAMEWORKS_DIR, mod.ANALYZER_SRC_DIRS)
    consumers = mod.scan_consumers(mod.CORE_SRC_DIR)
    rows = mod.classify(producers, consumers)
    expected = mod.format_md(rows)
    current = committed.read_text()
    if current != expected:
        # Surface a useful diff-hint rather than a raw equality failure
        raise AssertionError(
            f"{committed.relative_to(REPO_ROOT)} is out of date. "
            f"Re-run: ./scripts/generate-concepts"
        )
