"""Tests for repo profile detection."""
import json
from pathlib import Path

from hypergumbo.cli import run_behavior_map


def test_detects_python_language(tmp_path: Path) -> None:
    """Should detect Python files and count them."""
    # Create some Python files
    (tmp_path / "app.py").write_text("def main():\n    pass\n")
    (tmp_path / "utils.py").write_text("def helper():\n    return 42\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    assert "profile" in data
    assert "languages" in data["profile"]
    assert "python" in data["profile"]["languages"]
    assert data["profile"]["languages"]["python"]["files"] == 2
    assert data["profile"]["languages"]["python"]["loc"] > 0


def test_detects_javascript_language(tmp_path: Path) -> None:
    """Should detect JavaScript files."""
    (tmp_path / "app.js").write_text("function main() {\n  console.log('hi');\n}\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    assert "javascript" in data["profile"]["languages"]
    assert data["profile"]["languages"]["javascript"]["files"] == 1


def test_detects_typescript_language(tmp_path: Path) -> None:
    """Should detect TypeScript files."""
    (tmp_path / "app.ts").write_text("const x: number = 42;\n")
    (tmp_path / "types.d.ts").write_text("declare const y: string;\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    assert "typescript" in data["profile"]["languages"]
    assert data["profile"]["languages"]["typescript"]["files"] == 2


def test_detects_html_language(tmp_path: Path) -> None:
    """Should detect HTML files."""
    (tmp_path / "index.html").write_text("<html>\n<body>Hello</body>\n</html>\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    assert "html" in data["profile"]["languages"]
    assert data["profile"]["languages"]["html"]["files"] == 1


def test_detects_multiple_languages(tmp_path: Path) -> None:
    """Should detect all languages in a mixed repo."""
    (tmp_path / "app.py").write_text("print('hi')\n")
    (tmp_path / "index.js").write_text("console.log('hi');\n")
    (tmp_path / "page.html").write_text("<html></html>\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    languages = data["profile"]["languages"]
    assert "python" in languages
    assert "javascript" in languages
    assert "html" in languages


def test_excludes_node_modules_from_profile(tmp_path: Path) -> None:
    """Should not count files in excluded directories."""
    (tmp_path / "app.py").write_text("print('hi')\n")

    # Create lots of JS in node_modules (should be ignored)
    node_modules = tmp_path / "node_modules" / "some-package"
    node_modules.mkdir(parents=True)
    (node_modules / "index.js").write_text("module.exports = {};\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    # Should only have Python, not JavaScript
    assert "python" in data["profile"]["languages"]
    assert "javascript" not in data["profile"]["languages"]


def test_detects_fastapi_framework(tmp_path: Path) -> None:
    """Should detect FastAPI framework from pyproject.toml."""
    (tmp_path / "app.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "myapp"\ndependencies = ["fastapi", "uvicorn"]\n'
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    assert "frameworks" in data["profile"]
    assert "fastapi" in data["profile"]["frameworks"]


def test_detects_flask_framework(tmp_path: Path) -> None:
    """Should detect Flask framework from requirements.txt."""
    (tmp_path / "app.py").write_text("from flask import Flask\n")
    (tmp_path / "requirements.txt").write_text("flask==2.0.0\nrequests\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    assert "flask" in data["profile"]["frameworks"]


def test_detects_react_framework(tmp_path: Path) -> None:
    """Should detect React from package.json."""
    (tmp_path / "App.jsx").write_text("export default function App() { return <div/>; }\n")
    (tmp_path / "package.json").write_text(
        '{"name": "myapp", "dependencies": {"react": "^18.0.0"}}\n'
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    assert "react" in data["profile"]["frameworks"]


def test_detects_express_framework(tmp_path: Path) -> None:
    """Should detect Express.js from package.json."""
    (tmp_path / "server.js").write_text("const express = require('express');\n")
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"express": "^4.18.0"}}\n'
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    assert "express" in data["profile"]["frameworks"]


def test_detects_django_framework(tmp_path: Path) -> None:
    """Should detect Django from setup.py or pyproject.toml."""
    (tmp_path / "manage.py").write_text("#!/usr/bin/env python\nimport django\n")
    (tmp_path / "requirements.txt").write_text("Django>=4.0\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    assert "django" in data["profile"]["frameworks"]


def test_profile_empty_when_no_source_files(tmp_path: Path) -> None:
    """Should return empty profile for repos with no recognized source files."""
    # Create a file with no recognized extension
    (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    assert data["profile"]["languages"] == {}
    assert data["profile"]["frameworks"] == []


def test_counts_lines_of_code_correctly(tmp_path: Path) -> None:
    """Should count non-empty lines as LOC."""
    (tmp_path / "app.py").write_text("def main():\n    # comment\n    pass\n\n\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    # 3 non-empty lines (def, comment, pass)
    assert data["profile"]["languages"]["python"]["loc"] == 3


def test_handles_unreadable_dependency_file(tmp_path: Path) -> None:
    """Should gracefully handle unreadable dependency files."""
    (tmp_path / "app.py").write_text("print('hi')\n")

    # Create a directory named pyproject.toml (reading it will fail with IsADirectoryError)
    (tmp_path / "pyproject.toml").mkdir()

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    # Should still work, just not detect any frameworks
    assert "python" in data["profile"]["languages"]
    # No crash occurred


def test_handles_invalid_package_json(tmp_path: Path) -> None:
    """Should gracefully handle malformed package.json."""
    (tmp_path / "app.js").write_text("console.log('hi');\n")
    (tmp_path / "package.json").write_text("{ invalid json }")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    # Should still detect JavaScript, just not frameworks
    assert "javascript" in data["profile"]["languages"]
    assert data["profile"]["frameworks"] == []
