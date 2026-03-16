# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for QML analyzer."""
from pathlib import Path

from hypergumbo_lang_common.qml import analyze_qml


class TestAnalyzeQml:
    """Tests for QML analysis."""

    def test_detects_component(self, tmp_path: Path) -> None:
        (tmp_path / "main.qml").write_text("Rectangle {\n    width: 100\n}\n")
        result = analyze_qml(tmp_path)
        assert any(s.name == "Rectangle" and s.kind == "component" for s in result.symbols)

    def test_detects_property(self, tmp_path: Path) -> None:
        content = "Item {\n    property int myWidth: 100\n}\n"
        (tmp_path / "widget.qml").write_text(content)
        result = analyze_qml(tmp_path)
        assert any(s.name == "myWidth" and s.kind == "property" for s in result.symbols)

    def test_detects_signal(self, tmp_path: Path) -> None:
        content = "Item {\n    signal clicked(int x, int y)\n}\n"
        (tmp_path / "button.qml").write_text(content)
        result = analyze_qml(tmp_path)
        assert any(s.name == "clicked" and s.kind == "signal" for s in result.symbols)

    def test_detects_function(self, tmp_path: Path) -> None:
        content = "Item {\n    function doSomething() {\n        console.log('hi')\n    }\n}\n"
        (tmp_path / "logic.qml").write_text(content)
        result = analyze_qml(tmp_path)
        assert any(s.name == "doSomething" and s.kind == "function" for s in result.symbols)

    def test_detects_id(self, tmp_path: Path) -> None:
        content = "Rectangle {\n    id: myRect\n}\n"
        (tmp_path / "named.qml").write_text(content)
        result = analyze_qml(tmp_path)
        assert any(s.name == "myRect" and s.kind == "id" for s in result.symbols)

    def test_namespaced_component(self, tmp_path: Path) -> None:
        (tmp_path / "app.qml").write_text("QtQuick.Controls.Button {\n}\n")
        result = analyze_qml(tmp_path)
        assert any(s.name == "QtQuick.Controls.Button" for s in result.symbols)

    def test_property_alias(self, tmp_path: Path) -> None:
        content = "Item {\n    property alias text: label.text\n}\n"
        (tmp_path / "alias.qml").write_text(content)
        result = analyze_qml(tmp_path)
        assert any(s.name == "text" and s.kind == "property" for s in result.symbols)

    def test_symbols_have_qml_language(self, tmp_path: Path) -> None:
        (tmp_path / "test.qml").write_text("Item {\n    id: root\n}\n")
        result = analyze_qml(tmp_path)
        assert all(s.language == "qml" for s in result.symbols)

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        result = analyze_qml(tmp_path)
        assert result.symbols == []

    def test_multiple_constructs(self, tmp_path: Path) -> None:
        content = (
            "ApplicationWindow {\n"
            "    id: window\n"
            "    property string title: 'App'\n"
            "    signal closed()\n"
            "    function quit() {\n"
            "        Qt.quit()\n"
            "    }\n"
            "}\n"
        )
        (tmp_path / "app.qml").write_text(content)
        result = analyze_qml(tmp_path)
        kinds = {s.kind for s in result.symbols}
        assert "component" in kinds
        assert "id" in kinds
        assert "property" in kinds
        assert "signal" in kinds
        assert "function" in kinds

    def test_readonly_property(self, tmp_path: Path) -> None:
        content = "Item {\n    readonly property real ratio: 1.5\n}\n"
        (tmp_path / "ro.qml").write_text(content)
        result = analyze_qml(tmp_path)
        assert any(s.name == "ratio" and s.kind == "property" for s in result.symbols)
