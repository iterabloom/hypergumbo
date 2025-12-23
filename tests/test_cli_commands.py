"""Tests for CLI commands to achieve 100% coverage."""
import json
from pathlib import Path

from hypergumbo.cli import (
    cmd_init,
    cmd_run,
    cmd_slice,
    cmd_catalog,
    cmd_export_capsule,
    main,
    build_parser,
)


class FakeArgs:
    """Minimal namespace for testing command functions."""
    pass


def test_cmd_init_creates_capsule(tmp_path: Path, capsys) -> None:
    args = FakeArgs()
    args.path = str(tmp_path)
    args.capabilities = "python,javascript"
    args.assistant = "template"
    args.llm_input = "tier1"

    result = cmd_init(args)

    assert result == 0

    capsule_path = tmp_path / ".hypergumbo" / "capsule.json"
    assert capsule_path.exists()

    data = json.loads(capsule_path.read_text())
    assert data["capabilities"] == ["python", "javascript"]
    assert data["assistant"] == "template"
    assert data["llm_input"] == "tier1"

    out, _ = capsys.readouterr()
    assert "[hypergumbo init]" in out


def test_cmd_init_empty_capabilities(tmp_path: Path) -> None:
    args = FakeArgs()
    args.path = str(tmp_path)
    args.capabilities = ""
    args.assistant = "template"
    args.llm_input = "tier0"

    result = cmd_init(args)

    assert result == 0

    capsule_path = tmp_path / ".hypergumbo" / "capsule.json"
    data = json.loads(capsule_path.read_text())
    assert data["capabilities"] == []


def test_cmd_run_creates_behavior_map(tmp_path: Path) -> None:
    args = FakeArgs()
    args.path = str(tmp_path)
    args.out = str(tmp_path / "results.json")

    result = cmd_run(args)

    assert result == 0

    out_path = tmp_path / "results.json"
    assert out_path.exists()

    data = json.loads(out_path.read_text())
    assert data["schema_version"] == "0.1.0"


def test_cmd_slice_stub(capsys) -> None:
    args = FakeArgs()
    args.entry = "my_function"
    args.out = "slice.json"

    result = cmd_slice(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "[hypergumbo slice]" in out
    assert "my_function" in out


def test_cmd_catalog_stub(capsys) -> None:
    args = FakeArgs()
    args.show_all = True

    result = cmd_catalog(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "[hypergumbo catalog]" in out
    assert "True" in out


def test_cmd_catalog_stub_default(capsys) -> None:
    args = FakeArgs()
    args.show_all = False

    result = cmd_catalog(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "False" in out


def test_cmd_export_capsule_stub(capsys) -> None:
    args = FakeArgs()
    args.shareable = True
    args.out = "capsule.tar.gz"

    result = cmd_export_capsule(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "[hypergumbo export-capsule]" in out
    assert "shareable=True" in out


def test_main_with_init(tmp_path: Path) -> None:
    result = main(["init", str(tmp_path)])
    assert result == 0

    capsule_path = tmp_path / ".hypergumbo" / "capsule.json"
    assert capsule_path.exists()


def test_main_with_run(tmp_path: Path) -> None:
    out_file = tmp_path / "output.json"
    result = main(["run", str(tmp_path), "--out", str(out_file)])
    assert result == 0
    assert out_file.exists()


def test_main_with_slice() -> None:
    result = main(["slice", "--entry", "foo"])
    assert result == 0


def test_main_with_catalog() -> None:
    result = main(["catalog"])
    assert result == 0


def test_main_with_export_capsule() -> None:
    result = main(["export-capsule"])
    assert result == 0
