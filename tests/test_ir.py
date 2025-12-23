"""Tests for the internal representation (IR) layer."""
from pathlib import Path

from hypergumbo.ir import Symbol
from hypergumbo.analyze.py import analyze_python


def test_symbol_has_required_fields() -> None:
    """Symbol dataclass should have all required fields."""
    symbol = Symbol(
        id="python:test.py:1-2:greet:function",
        name="greet",
        kind="function",
        language="python",
        path="test.py",
        line=1,
        end_line=2,
    )

    assert symbol.id == "python:test.py:1-2:greet:function"
    assert symbol.name == "greet"
    assert symbol.kind == "function"
    assert symbol.language == "python"
    assert symbol.path == "test.py"
    assert symbol.line == 1
    assert symbol.end_line == 2


def test_analyze_python_returns_symbols(tmp_path: Path) -> None:
    """analyze_python should return AnalysisResult with Symbol objects."""
    py_file = tmp_path / "hello.py"
    py_file.write_text("def greet():\n    pass\n")

    result = analyze_python(tmp_path)

    assert len(result.symbols) == 1
    assert isinstance(result.symbols[0], Symbol)
    assert result.symbols[0].name == "greet"
    assert result.symbols[0].kind == "function"


def test_symbol_id_format(tmp_path: Path) -> None:
    """Symbol id should follow the spec format: {lang}:{file}:{start}-{end}:{name}:{kind}."""
    py_file = tmp_path / "models.py"
    py_file.write_text("class User:\n    pass\n")

    result = analyze_python(tmp_path)

    assert len(result.symbols) == 1
    symbol = result.symbols[0]
    # ID should contain all components
    assert symbol.language in symbol.id
    assert "models.py" in symbol.id
    assert symbol.name in symbol.id
    assert symbol.kind in symbol.id
