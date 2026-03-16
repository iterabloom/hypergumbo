# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Mermaid diagram analyzer."""
from pathlib import Path

from hypergumbo_lang_extended1.mermaid import analyze_mermaid


class TestAnalyzeMermaid:
    """Tests for Mermaid diagram analysis."""

    def test_detects_flowchart(self, tmp_path: Path) -> None:
        (tmp_path / "flow.mmd").write_text("flowchart LR\n    A[Start] --> B[End]\n")
        result = analyze_mermaid(tmp_path)
        assert any(s.name == "flowchart" and s.kind == "diagram" for s in result.symbols)

    def test_detects_nodes(self, tmp_path: Path) -> None:
        (tmp_path / "flow.mmd").write_text("graph TD\n    A[Start]\n    B(Process)\n")
        result = analyze_mermaid(tmp_path)
        names = [s.name for s in result.symbols if s.kind == "node"]
        assert "A" in names
        assert "B" in names

    def test_detects_sequence_participants(self, tmp_path: Path) -> None:
        content = "sequenceDiagram\n    participant Alice\n    participant Bob\n"
        (tmp_path / "seq.mmd").write_text(content)
        result = analyze_mermaid(tmp_path)
        names = [s.name for s in result.symbols if s.kind == "participant"]
        assert "Alice" in names
        assert "Bob" in names

    def test_detects_class_diagram(self, tmp_path: Path) -> None:
        content = "classDiagram\n    class Animal\n    class Dog\n"
        (tmp_path / "cls.mmd").write_text(content)
        result = analyze_mermaid(tmp_path)
        names = [s.name for s in result.symbols if s.kind == "class"]
        assert "Animal" in names
        assert "Dog" in names

    def test_detects_state_diagram(self, tmp_path: Path) -> None:
        content = 'stateDiagram\n    state "Still" as s1\n'
        (tmp_path / "state.mermaid").write_text(content)
        result = analyze_mermaid(tmp_path)
        assert any(s.name == "s1" and s.kind == "state" for s in result.symbols)

    def test_skips_keywords_as_nodes(self, tmp_path: Path) -> None:
        content = "graph TD\n    A[Real]\n    subgraph sub1\n    end\n"
        (tmp_path / "flow.mmd").write_text(content)
        result = analyze_mermaid(tmp_path)
        names = [s.name for s in result.symbols if s.kind == "node"]
        assert "A" in names
        assert "subgraph" not in names
        assert "end" not in names

    def test_symbols_have_mermaid_language(self, tmp_path: Path) -> None:
        (tmp_path / "test.mmd").write_text("graph TD\n    A[Node]\n")
        result = analyze_mermaid(tmp_path)
        assert all(s.language == "mermaid" for s in result.symbols)

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        result = analyze_mermaid(tmp_path)
        assert result.symbols == []

    def test_mermaid_extension(self, tmp_path: Path) -> None:
        (tmp_path / "diagram.mermaid").write_text("pie\n")
        result = analyze_mermaid(tmp_path)
        assert any(s.name == "pie" and s.kind == "diagram" for s in result.symbols)

    def test_gantt_diagram_type(self, tmp_path: Path) -> None:
        (tmp_path / "gantt.mmd").write_text("gantt\n    title Project\n")
        result = analyze_mermaid(tmp_path)
        assert any(s.name == "gantt" and s.kind == "diagram" for s in result.symbols)
