# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for repo profile detection."""
import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def _all_detected_frameworks(data: dict) -> list[str]:
    """Return all detected frameworks (production + dev).

    Manifest detection tests care that the framework was *found*, not
    whether the import-based refinement classified it as production or
    dev-only.  Use this helper in tests that exercise manifest scanning.
    """
    profile = data["profile"]
    return profile.get("frameworks", []) + profile.get("dev_frameworks", [])


def test_detects_python_language(tmp_path: Path) -> None:
    """Should detect Python files and count them."""
    # Create some Python files
    (tmp_path / "app.py").write_text("def main():\n    pass\n")
    (tmp_path / "utils.py").write_text("def helper():\n    return 42\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    assert "profile" in data
    assert "languages" in data["profile"]
    assert "python" in data["profile"]["languages"]
    assert data["profile"]["languages"]["python"]["files"] == 2
    # run_behavior_map uses count_loc=True so LOC is populated
    assert data["profile"]["languages"]["python"]["loc"] == 4  # 2 lines per file


def test_detects_javascript_language(tmp_path: Path) -> None:
    """Should detect JavaScript files."""
    (tmp_path / "app.js").write_text("function main() {\n  console.log('hi');\n}\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    assert "javascript" in data["profile"]["languages"]
    assert data["profile"]["languages"]["javascript"]["files"] == 1


def test_detects_typescript_language(tmp_path: Path) -> None:
    """Should detect TypeScript files."""
    (tmp_path / "app.ts").write_text("const x: number = 42;\n")
    (tmp_path / "types.d.ts").write_text("declare const y: string;\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    assert "typescript" in data["profile"]["languages"]
    assert data["profile"]["languages"]["typescript"]["files"] == 2


def test_detects_html_language(tmp_path: Path) -> None:
    """Should detect HTML files."""
    (tmp_path / "index.html").write_text("<html>\n<body>Hello</body>\n</html>\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    assert "html" in data["profile"]["languages"]
    assert data["profile"]["languages"]["html"]["files"] == 1


def test_detects_blade_language(tmp_path: Path) -> None:
    """WI-navaf: .blade.php files should enrol both 'blade' and 'php' in
    detected languages.

    Pre-fix, FileIndex.match_pattern misclassified '*.blade.php' as a
    pure-extension lookup (keyed by Path.suffix which only retains '.php')
    so the 'blade' entry had zero files and was dropped, causing the
    blade analyzer to never enroll in the producer-routing layer.
    """
    (tmp_path / "resources").mkdir()
    (tmp_path / "resources" / "views").mkdir()
    (tmp_path / "resources" / "views" / "home.blade.php").write_text(
        "@extends('layouts.app')\n"
        "@section('content')\nHello\n@endsection\n"
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "Kernel.php").write_text("<?php\nnamespace App;\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    assert "blade" in data["profile"]["languages"], (
        f"Expected 'blade' in detected languages; got: "
        f"{sorted(data['profile']['languages'].keys())}"
    )
    assert data["profile"]["languages"]["blade"]["files"] == 1
    assert "php" in data["profile"]["languages"]
    # *.php matches both .blade.php and the plain .php file.
    assert data["profile"]["languages"]["php"]["files"] == 2


def test_detects_multiple_languages(tmp_path: Path) -> None:
    """Should detect all languages in a mixed repo."""
    (tmp_path / "app.py").write_text("print('hi')\n")
    (tmp_path / "index.js").write_text("console.log('hi');\n")
    (tmp_path / "page.html").write_text("<html></html>\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    languages = data["profile"]["languages"]
    assert "python" in languages
    assert "javascript" in languages
    assert "html" in languages


def test_inv_hokig_bash_includes_extensionless_shebang(tmp_path: Path) -> None:
    """INV-hokig: profile.languages.bash.files must agree with the analyzer.

    The bash analyzer detects extensionless executables by shebang line
    (e.g., .githooks/pre-commit, scripts/auto-pr). Before this fix, profile
    only counted *.sh / *.bash by extension and reported a lower number
    than the analyzer's files_analyzed — making
    profile.languages.bash.files unsafe to use for "how many bash files
    does this repo have?".

    Fixture: 1 .sh file + 1 extensionless shebang script. Both must
    surface under profile.languages.bash.files, matching the bash
    analyzer's enumeration.
    """
    (tmp_path / "script.sh").write_text("#!/bin/bash\necho hi\n")
    no_ext = tmp_path / "deploy"
    no_ext.write_text("#!/bin/bash\necho deploy\n")
    no_ext.chmod(0o755)

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    languages = data["profile"]["languages"]
    assert "bash" in languages
    assert languages["bash"]["files"] == 2, (
        f"profile counted {languages['bash']['files']} bash files; "
        f"expected 2 (script.sh + extensionless shebang 'deploy'). "
        f"Full profile: {languages}"
    )


def test_inv_tosum_bash_not_double_counted(tmp_path: Path) -> None:
    """INV-tosum: shell scripts must surface exactly once under the canonical 'bash' key.

    Pre-fix, alias keys (e.g., 'shell' → 'bash') were injected into
    LANGUAGE_EXTENSIONS, causing profile.detect_languages to enumerate
    the same .sh files twice and emit two entries with identical stats.
    """
    (tmp_path / "deploy.sh").write_text("#!/bin/bash\necho hi\n")
    (tmp_path / "test.sh").write_text("#!/bin/bash\necho test\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    languages = data["profile"]["languages"]

    assert "bash" in languages
    assert languages["bash"]["files"] == 2
    assert "shell" not in languages


def test_excludes_node_modules_from_profile(tmp_path: Path) -> None:
    """Should not count files in excluded directories."""
    (tmp_path / "app.py").write_text("print('hi')\n")

    # Create lots of JS in node_modules (should be ignored)
    node_modules = tmp_path / "node_modules" / "some-package"
    node_modules.mkdir(parents=True)
    (node_modules / "index.js").write_text("module.exports = {};\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    assert "frameworks" in data["profile"]
    assert "fastapi" in data["profile"]["frameworks"]


def test_detects_flask_framework(tmp_path: Path) -> None:
    """Should detect Flask framework from requirements.txt."""
    (tmp_path / "app.py").write_text("from flask import Flask\n")
    (tmp_path / "requirements.txt").write_text("flask==2.0.0\nrequests\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    assert "flask" in data["profile"]["frameworks"]


def test_detects_react_framework(tmp_path: Path) -> None:
    """Should detect React from package.json."""
    (tmp_path / "App.jsx").write_text("export default function App() { return <div/>; }\n")
    (tmp_path / "package.json").write_text(
        '{"name": "myapp", "dependencies": {"react": "^18.0.0"}}\n'
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    assert "react" in data["profile"]["frameworks"]


def test_detects_android_framework_from_build_gradle(tmp_path: Path) -> None:
    """Should detect Android from build.gradle with android {} block."""
    (tmp_path / "MainActivity.java").write_text(
        "package com.example;\nimport android.app.Activity;\n"
        "public class MainActivity extends Activity {}\n"
    )
    (tmp_path / "build.gradle").write_text(
        'plugins {\n    id "custom.android.application"\n}\n\n'
        "android {\n    namespace 'com.example'\n}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    assert "android" in data["profile"]["frameworks"]


def test_detects_android_framework_from_manifest(tmp_path: Path) -> None:
    """Should detect Android from AndroidManifest.xml presence."""
    (tmp_path / "MainActivity.java").write_text(
        "package com.example;\nimport android.app.Activity;\n"
        "public class MainActivity extends Activity {}\n"
    )
    # Create subdirectory structure like real Android projects
    src_dir = tmp_path / "app" / "src" / "main"
    src_dir.mkdir(parents=True)
    (src_dir / "AndroidManifest.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android"\n'
        '    package="com.example">\n'
        "    <application/>\n"
        "</manifest>\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    assert "android" in data["profile"]["frameworks"]


def test_detects_express_framework(tmp_path: Path) -> None:
    """Should detect Express.js from package.json."""
    (tmp_path / "server.js").write_text("const express = require('express');\n")
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"express": "^4.18.0"}}\n'
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    assert "express" in data["profile"]["frameworks"]


def test_detects_django_framework(tmp_path: Path) -> None:
    """Should detect Django from setup.py or pyproject.toml."""
    (tmp_path / "manage.py").write_text("#!/usr/bin/env python\nimport django\n")
    (tmp_path / "requirements.txt").write_text("Django>=4.0\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    assert "django" in data["profile"]["frameworks"]


# ============================================================================
# WI-himas: layered requirements/ directory + -r include resolution
# ============================================================================


def test_wi_himas_detects_django_from_requirements_subdir(tmp_path: Path) -> None:
    """WI-himas: requirements/base.txt is part of the manifest set even when no
    top-level requirements.txt exists (bakerydemo / many Django apps layout).
    """
    (tmp_path / "manage.py").write_text("#!/usr/bin/env python\nimport django\n")
    req_dir = tmp_path / "requirements"
    req_dir.mkdir()
    (req_dir / "base.txt").write_text("Django>=4.2\nwagtail==5.0\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "django" in data["profile"]["frameworks"]


def test_wi_himas_resolves_dash_r_include_chain(tmp_path: Path) -> None:
    """WI-himas: top-level requirements.txt with only `-r requirements/base.txt`
    must surface the base file's deps in the union.
    """
    (tmp_path / "manage.py").write_text("import django\n")
    (tmp_path / "requirements.txt").write_text("-r requirements/base.txt\n")
    req_dir = tmp_path / "requirements"
    req_dir.mkdir()
    (req_dir / "base.txt").write_text("Django>=4.2\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "django" in data["profile"]["frameworks"]


def test_wi_himas_dev_dot_txt_inherits_via_dash_r(tmp_path: Path) -> None:
    """WI-himas: requirements/dev.txt opens with `-r base.txt`; both files'
    deps must be detected (production.txt = base.txt + prod deps pattern).
    """
    (tmp_path / "manage.py").write_text("import django\n")
    req_dir = tmp_path / "requirements"
    req_dir.mkdir()
    (req_dir / "base.txt").write_text("Django>=4.2\n")
    (req_dir / "dev.txt").write_text("-r base.txt\npytest>=7.0\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # django (from base.txt, included via -r from dev.txt) must surface.
    assert "django" in data["profile"]["frameworks"]


def test_wi_himas_hyphen_requirements_file_is_part_of_manifest_set(tmp_path: Path) -> None:
    """WI-himas: requirements-prod.txt (hyphen suffix) is recognized as part
    of the pip-requirements manifest set, not just the literal name.
    """
    (tmp_path / "manage.py").write_text("import django\n")
    (tmp_path / "requirements-prod.txt").write_text("Django>=4.2\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "django" in data["profile"]["frameworks"]


def test_wi_himas_requirements_under_venv_is_skipped(tmp_path: Path) -> None:
    """WI-himas: requirements/*.txt nested under .venv/ / node_modules/ /
    vendor/ etc. are NOT part of the project manifest set. Shadowed deps in
    an installed virtualenv would otherwise create framework-detection FPs.
    """
    (tmp_path / "manage.py").write_text("import django\n")
    # Real project layout: no Django in any project-level manifest.
    venv_req = tmp_path / ".venv" / "site-packages" / "somepkg" / "requirements"
    venv_req.mkdir(parents=True)
    (venv_req / "base.txt").write_text("Django>=4.2\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # Django must NOT be detected — the .venv requirements file shouldn't
    # be in the project manifest set.
    assert "django" not in data["profile"]["frameworks"]


def test_wi_himas_dash_r_outside_repo_does_not_escape(tmp_path: Path) -> None:
    """WI-himas: -r references must not follow outside the repo root.
    Bounding the resolution at repo_root prevents directory traversal that
    would parse arbitrary host files.
    """
    (tmp_path / "manage.py").write_text("import django\n")
    # -r ../outside.txt — would escape repo if followed.
    (tmp_path / "requirements.txt").write_text("-r ../outside.txt\n")
    # Simulate a sibling file that should NOT be read.
    outside = tmp_path.parent / f"outside-{tmp_path.name}.txt"
    outside.write_text("Django>=4.2\n")

    out_path = tmp_path / "out.json"
    try:
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())
        # The outside file was NOT read, so django is not detected.
        assert "django" not in data["profile"]["frameworks"]
    finally:
        outside.unlink(missing_ok=True)


def test_profile_empty_when_no_source_files(tmp_path: Path) -> None:
    """Should return empty profile for repos with no recognized source files."""
    # Create a file with no recognized extension
    (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    assert data["profile"]["languages"] == {}
    assert data["profile"]["frameworks"] == []


def test_counts_lines_of_code_correctly(tmp_path: Path) -> None:
    """Behavior map has correct LOC (non-empty lines only)."""
    (tmp_path / "app.py").write_text("def main():\n    # comment\n    pass\n\n\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # LOC counts non-empty lines: "def main:", "# comment", "pass" = 3
    assert data["profile"]["languages"]["python"]["loc"] == 3
    assert data["profile"]["languages"]["python"]["files"] == 1


def test_handles_unreadable_dependency_file(tmp_path: Path) -> None:
    """Should gracefully handle unreadable dependency files."""
    (tmp_path / "app.py").write_text("print('hi')\n")

    # Create a directory named pyproject.toml (reading it will fail with IsADirectoryError)
    (tmp_path / "pyproject.toml").mkdir()

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Should still work, just not detect any frameworks
    assert "python" in data["profile"]["languages"]
    # No crash occurred


def test_handles_invalid_package_json(tmp_path: Path) -> None:
    """Should gracefully handle malformed package.json."""
    (tmp_path / "app.js").write_text("console.log('hi');\n")
    (tmp_path / "package.json").write_text("{ invalid json }")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Should still detect JavaScript, just not frameworks
    assert "javascript" in data["profile"]["languages"]
    assert data["profile"]["frameworks"] == []


def test_handles_non_dict_package_json(tmp_path: Path) -> None:
    """Should gracefully handle package.json with non-dict top-level value.

    Some repos have package.json files that are valid JSON but contain a
    string or array at the top level instead of an object. This was found
    in the grpc repo during bakeoff testing.
    """
    (tmp_path / "app.js").write_text("console.log('hi');\n")
    # Valid JSON, but a string instead of an object
    (tmp_path / "package.json").write_text('"this is a string, not an object"')

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Should still detect JavaScript, just not frameworks
    assert "javascript" in data["profile"]["languages"]
    assert data["profile"]["frameworks"] == []


def test_handles_array_package_json(tmp_path: Path) -> None:
    """Should gracefully handle package.json with array at top level."""
    (tmp_path / "app.js").write_text("console.log('hi');\n")
    # Valid JSON, but an array instead of an object
    (tmp_path / "package.json").write_text('["item1", "item2"]')

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Should still detect JavaScript, just not frameworks
    assert "javascript" in data["profile"]["languages"]
    assert data["profile"]["frameworks"] == []


def test_detects_pytorch_framework(tmp_path: Path) -> None:
    """Should detect PyTorch from dependencies."""
    (tmp_path / "train.py").write_text("import torch\n")
    (tmp_path / "requirements.txt").write_text("torch>=2.0\ntorchvision\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "pytorch" in data["profile"]["frameworks"]


def test_detects_tensorflow_framework(tmp_path: Path) -> None:
    """Should detect TensorFlow from dependencies."""
    (tmp_path / "model.py").write_text("import tensorflow as tf\n")
    (tmp_path / "requirements.txt").write_text("tensorflow>=2.0\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "tensorflow" in data["profile"]["frameworks"]


def test_detects_transformers_framework(tmp_path: Path) -> None:
    """Should detect HuggingFace Transformers from dependencies."""
    (tmp_path / "nlp.py").write_text("from transformers import pipeline\n")
    (tmp_path / "requirements.txt").write_text("transformers>=4.0\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "transformers" in data["profile"]["frameworks"]


def test_detects_langchain_framework(tmp_path: Path) -> None:
    """Should detect LangChain from dependencies."""
    (tmp_path / "agent.py").write_text("from langchain import LLMChain\n")
    (tmp_path / "requirements.txt").write_text("langchain>=0.1\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "langchain" in data["profile"]["frameworks"]


def test_detects_scikit_learn_framework(tmp_path: Path) -> None:
    """Should detect scikit-learn from dependencies."""
    (tmp_path / "ml.py").write_text("from sklearn.linear_model import LogisticRegression\n")
    (tmp_path / "requirements.txt").write_text("scikit-learn>=1.0\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "scikit-learn" in data["profile"]["frameworks"]


def test_detects_openai_framework(tmp_path: Path) -> None:
    """Should detect OpenAI client from dependencies."""
    (tmp_path / "chat.py").write_text("from openai import OpenAI\n")
    (tmp_path / "requirements.txt").write_text("openai>=1.0\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "openai" in data["profile"]["frameworks"]


def test_detects_anthropic_framework(tmp_path: Path) -> None:
    """Should detect Anthropic client from dependencies."""
    (tmp_path / "chat.py").write_text("from anthropic import Anthropic\n")
    (tmp_path / "requirements.txt").write_text("anthropic>=0.5\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "anthropic" in data["profile"]["frameworks"]


def test_detects_llamaindex_framework(tmp_path: Path) -> None:
    """Should detect LlamaIndex from dependencies."""
    (tmp_path / "rag.py").write_text("from llama_index import VectorStoreIndex\n")
    (tmp_path / "requirements.txt").write_text("llama-index>=0.9\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "llamaindex" in data["profile"]["frameworks"]


def test_detects_mlflow_framework(tmp_path: Path) -> None:
    """Should detect MLflow from dependencies."""
    (tmp_path / "experiment.py").write_text("import mlflow\n")
    (tmp_path / "requirements.txt").write_text("mlflow>=2.0\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "mlflow" in data["profile"]["frameworks"]


# Rust framework detection tests


def test_detects_rust_axum_framework(tmp_path: Path) -> None:
    """Should detect Axum web framework from Cargo.toml."""
    (tmp_path / "main.rs").write_text("fn main() {}\n")
    (tmp_path / "Cargo.toml").write_text('''
[package]
name = "myapp"
version = "0.1.0"

[dependencies]
axum = "0.7"
tokio = { version = "1", features = ["full"] }
''')

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "axum" in data["profile"]["frameworks"]
    assert "tokio" in data["profile"]["frameworks"]


def test_detects_rust_solana_framework(tmp_path: Path) -> None:
    """Should detect Solana SDK from Cargo.toml."""
    (tmp_path / "lib.rs").write_text("pub fn process() {}\n")
    (tmp_path / "Cargo.toml").write_text('''
[package]
name = "solana-program"
version = "0.1.0"

[dependencies]
solana-sdk = "1.17"
anchor-lang = "0.29"
''')

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "solana" in data["profile"]["frameworks"]
    assert "anchor" in data["profile"]["frameworks"]


def test_detects_rust_sp1_zkvm(tmp_path: Path) -> None:
    """Should detect SP1 zkVM from Cargo.toml."""
    (tmp_path / "main.rs").write_text("fn main() {}\n")
    (tmp_path / "Cargo.toml").write_text('''
[package]
name = "my-zkprogram"
version = "0.1.0"

[dependencies]
sp1-sdk = "1.0"
''')

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "sp1" in data["profile"]["frameworks"]


def test_detects_rust_arkworks(tmp_path: Path) -> None:
    """Should detect Arkworks ZKP library from Cargo.toml."""
    (tmp_path / "lib.rs").write_text("use ark_ff::Field;\n")
    (tmp_path / "Cargo.toml").write_text('''
[package]
name = "zk-circuit"
version = "0.1.0"

[dependencies]
ark-ff = "0.4"
ark-ec = "0.4"
ark-groth16 = "0.4"
''')

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "arkworks" in _all_detected_frameworks(data)
    assert "groth16" in _all_detected_frameworks(data)


def test_detects_rust_plonky2(tmp_path: Path) -> None:
    """Should detect Plonky2 proving system from Cargo.toml."""
    (tmp_path / "circuit.rs").write_text("use plonky2::field::types::Field;\n")
    (tmp_path / "Cargo.toml").write_text('''
[package]
name = "my-circuit"
version = "0.1.0"

[dependencies]
plonky2 = "0.2"
plonky2_field = "0.2"
''')

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "plonky2" in data["profile"]["frameworks"]


def test_detects_rust_halo2(tmp_path: Path) -> None:
    """Should detect Halo2 proving system from Cargo.toml."""
    (tmp_path / "lib.rs").write_text("use halo2_proofs::dev::MockProver;\n")
    (tmp_path / "Cargo.toml").write_text('''
[package]
name = "halo2-circuit"
version = "0.1.0"

[dependencies]
halo2_proofs = "0.3"
''')

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "halo2" in data["profile"]["frameworks"]


def test_detects_rust_substrate(tmp_path: Path) -> None:
    """Should detect Substrate blockchain framework from Cargo.toml."""
    (tmp_path / "lib.rs").write_text("use frame_support::pallet;\n")
    (tmp_path / "Cargo.toml").write_text('''
[package]
name = "my-pallet"
version = "0.1.0"

[dependencies]
frame-support = "4.0"
sp-core = "21.0"
sp-runtime = "24.0"
''')

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "substrate" in _all_detected_frameworks(data)


def test_detects_rust_ethers(tmp_path: Path) -> None:
    """Should detect ethers-rs Ethereum library from Cargo.toml."""
    (tmp_path / "main.rs").write_text("use ethers::prelude::*;\n")
    (tmp_path / "Cargo.toml").write_text('''
[package]
name = "eth-client"
version = "0.1.0"

[dependencies]
ethers = "2.0"
''')

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "ethers" in data["profile"]["frameworks"]


def test_detects_rust_risc0(tmp_path: Path) -> None:
    """Should detect RISC Zero zkVM from Cargo.toml."""
    (tmp_path / "main.rs").write_text("use risc0_zkvm::*;\n")
    (tmp_path / "Cargo.toml").write_text('''
[package]
name = "risc0-guest"
version = "0.1.0"

[dependencies]
risc0-zkvm = "0.20"
''')

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "risc0" in _all_detected_frameworks(data)


def test_detects_rust_zcash(tmp_path: Path) -> None:
    """Should detect Zcash libraries from Cargo.toml."""
    (tmp_path / "lib.rs").write_text("use zcash_primitives::*;\n")
    (tmp_path / "Cargo.toml").write_text('''
[package]
name = "privacy-wallet"
version = "0.1.0"

[dependencies]
zcash_primitives = "0.13"
orchard = "0.6"
''')

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "zcash" in data["profile"]["frameworks"]


def test_detects_rust_libp2p(tmp_path: Path) -> None:
    """Should detect libp2p networking from Cargo.toml."""
    (tmp_path / "main.rs").write_text("use libp2p::*;\n")
    (tmp_path / "Cargo.toml").write_text('''
[package]
name = "p2p-node"
version = "0.1.0"

[dependencies]
libp2p = "0.53"
''')

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "libp2p" in data["profile"]["frameworks"]


def test_handles_unreadable_cargo_toml(tmp_path: Path) -> None:
    """Should gracefully handle unreadable Cargo.toml."""
    (tmp_path / "main.rs").write_text("fn main() {}\n")
    # Create a directory named Cargo.toml (reading it will fail)
    (tmp_path / "Cargo.toml").mkdir()

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # Should still work, just not detect any Rust frameworks
    assert "rust" in data["profile"]["languages"]


# Go framework detection tests


def test_detects_go_gin_framework(tmp_path: Path) -> None:
    """Should detect Gin web framework from go.mod."""
    (tmp_path / "main.go").write_text("package main\n")
    (tmp_path / "go.mod").write_text("""module myapp

go 1.21

require (
    github.com/gin-gonic/gin v1.9.0
)
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "gin" in data["profile"]["frameworks"]


def test_detects_go_echo_framework(tmp_path: Path) -> None:
    """Should detect Echo web framework from go.mod."""
    (tmp_path / "main.go").write_text("package main\n")
    (tmp_path / "go.mod").write_text("""module myapp

go 1.21

require (
    github.com/labstack/echo v4.11.0
)
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "echo" in data["profile"]["frameworks"]


def test_detects_go_fiber_framework(tmp_path: Path) -> None:
    """Should detect Fiber web framework from go.mod."""
    (tmp_path / "main.go").write_text("package main\n")
    (tmp_path / "go.mod").write_text("""module myapp

go 1.21

require (
    github.com/gofiber/fiber v2.52.0
)
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "fiber" in data["profile"]["frameworks"]


def test_detects_go_xorm_framework(tmp_path: Path) -> None:
    """Should detect XORM ORM framework from go.mod."""
    (tmp_path / "main.go").write_text("package main\n")
    (tmp_path / "go.mod").write_text("""module code.gitea.io/gitea

go 1.21

require (
    xorm.io/xorm v1.3.4
)
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "xorm" in data["profile"]["frameworks"]


# PHP framework detection tests


def test_detects_php_laravel_framework(tmp_path: Path) -> None:
    """Should detect Laravel framework from composer.json."""
    (tmp_path / "index.php").write_text("<?php\n")
    (tmp_path / "composer.json").write_text("""{
    "require": {
        "laravel/framework": "^10.0"
    }
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "laravel" in data["profile"]["frameworks"]


def test_detects_php_symfony_framework(tmp_path: Path) -> None:
    """Should detect Symfony framework from composer.json."""
    (tmp_path / "index.php").write_text("<?php\n")
    (tmp_path / "composer.json").write_text("""{
    "require": {
        "symfony/framework-bundle": "^6.0"
    }
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "symfony" in data["profile"]["frameworks"]


def test_handles_invalid_composer_json(tmp_path: Path) -> None:
    """Should gracefully handle malformed composer.json."""
    (tmp_path / "index.php").write_text("<?php\n")
    (tmp_path / "composer.json").write_text("{ invalid json }")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # Should still detect PHP, just not frameworks
    assert "php" in data["profile"]["languages"]
    assert "laravel" not in data["profile"]["frameworks"]


def test_handles_non_dict_composer_json(tmp_path: Path) -> None:
    """Should gracefully handle composer.json with non-dict top-level value."""
    (tmp_path / "index.php").write_text("<?php\n")
    # Valid JSON, but a string instead of an object
    (tmp_path / "composer.json").write_text('"this is a string"')

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # Should still detect PHP, just not frameworks
    assert "php" in data["profile"]["languages"]
    assert "laravel" not in data["profile"]["frameworks"]


# Java/Kotlin framework detection tests


def test_detects_java_spring_boot_maven(tmp_path: Path) -> None:
    """Should detect Spring Boot from pom.xml."""
    (tmp_path / "Main.java").write_text("public class Main {}\n")
    (tmp_path / "pom.xml").write_text("""<?xml version="1.0"?>
<project>
    <dependencies>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter</artifactId>
        </dependency>
    </dependencies>
</project>""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "spring-boot" in data["profile"]["frameworks"]


def test_detects_java_spring_boot_gradle(tmp_path: Path) -> None:
    """Should detect Spring Boot from build.gradle."""
    (tmp_path / "Main.java").write_text("public class Main {}\n")
    (tmp_path / "build.gradle").write_text("""plugins {
    id 'org.springframework.boot' version '3.0.0'
}

dependencies {
    implementation 'org.springframework.boot:spring-boot-starter'
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "spring-boot" in data["profile"]["frameworks"]


def test_detects_dropwizard_framework_from_gradle(tmp_path: Path) -> None:
    """Should detect Dropwizard from dropwizard-core in build.gradle."""
    (tmp_path / "Main.java").write_text("public class Main {}\n")
    (tmp_path / "build.gradle").write_text("""dependencies {
    implementation 'io.dropwizard:dropwizard-core:4.0.0'
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "dropwizard" in data["profile"]["frameworks"]


def test_dropwizard_metrics_does_not_trigger_dropwizard(tmp_path: Path) -> None:
    """Dropwizard Metrics library should not trigger Dropwizard framework detection.

    io.dropwizard.metrics is a standalone metrics library used by many projects
    (Flink, Kafka, etc.) that has no relation to the Dropwizard REST framework.
    Projects like Apache Iceberg reference it as a transitive dependency.
    """
    (tmp_path / "Main.java").write_text("public class Main {}\n")
    (tmp_path / "build.gradle").write_text("""dependencies {
    implementation 'io.dropwizard.metrics:metrics-core:4.2.0'
    exclude group: 'io.dropwizard.metrics', module: 'metrics-core'
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "dropwizard" not in data["profile"]["frameworks"]


def test_detects_kotlin_ktor_framework(tmp_path: Path) -> None:
    """Should detect Ktor framework from build.gradle.kts."""
    (tmp_path / "Main.kt").write_text("fun main() {}\n")
    (tmp_path / "build.gradle.kts").write_text("""dependencies {
    implementation("io.ktor:ktor-server-core:2.3.0")
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "ktor" in data["profile"]["frameworks"]


def test_detects_jetpack_compose_framework(tmp_path: Path) -> None:
    """Should detect Jetpack Compose from build.gradle."""
    (tmp_path / "MainActivity.kt").write_text("import androidx.compose.ui\n")
    (tmp_path / "build.gradle").write_text("""android {
    buildFeatures {
        compose true
    }
}

dependencies {
    implementation 'androidx.compose.ui:ui:1.5.0'
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "jetpack-compose" in data["profile"]["frameworks"]


# Swift framework detection tests


def test_detects_swift_vapor_framework(tmp_path: Path) -> None:
    """Should detect Vapor framework from Package.swift."""
    (tmp_path / "main.swift").write_text("import Vapor\n")
    (tmp_path / "Package.swift").write_text("""// swift-tools-version:5.7
import PackageDescription

let package = Package(
    name: "myapp",
    dependencies: [
        .package(url: "https://github.com/vapor/vapor.git", from: "4.0.0")
    ]
)""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "vapor" in data["profile"]["frameworks"]


def test_detects_swift_hummingbird_framework(tmp_path: Path) -> None:
    """Should detect Hummingbird framework from Package.swift."""
    (tmp_path / "main.swift").write_text("import Hummingbird\n")
    (tmp_path / "Package.swift").write_text("""// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "myapp",
    dependencies: [
        .package(url: "https://github.com/hummingbird-project/hummingbird.git", from: "2.0.0")
    ]
)""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "hummingbird" in data["profile"]["frameworks"]


# Scala framework detection tests


def test_detects_scala_play_framework(tmp_path: Path) -> None:
    """Should detect Play Framework from build.sbt."""
    (tmp_path / "Main.scala").write_text("object Main extends App\n")
    (tmp_path / "build.sbt").write_text("""name := "myapp"
version := "1.0"

libraryDependencies += "com.typesafe.play" %% "play" % "2.9.0"
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "play" in data["profile"]["frameworks"]


def test_detects_scala_http4s_framework(tmp_path: Path) -> None:
    """Should detect http4s from build.sbt."""
    (tmp_path / "Main.scala").write_text("object Main extends App\n")
    (tmp_path / "build.sbt").write_text("""name := "myapp"
version := "1.0"

libraryDependencies += "org.http4s" %% "http4s-dsl" % "0.23.0"
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "http4s" in data["profile"]["frameworks"]


def test_detects_scala_http4s_from_project_dependencies(tmp_path: Path) -> None:
    """Should detect http4s when the dependency string lives in the
    standard SBT ``project/Dependencies.scala`` file rather than in the
    top-level ``build.sbt`` itself.

    Real-world SBT projects (docspell is the canonical example) put all
    library coordinates in ``project/Dependencies.scala`` and only
    reference scala variables in ``build.sbt``
    (e.g. ``Dependencies.http4sClient``). Before WI-piban the detector
    only scanned ``build.sbt`` and missed these cases.
    """
    # Production code imports http4s — required so framework-validation
    # (which moves non-imported candidates to dev_frameworks) keeps http4s
    # in the confirmed ``frameworks`` list.
    (tmp_path / "Main.scala").write_text(
        'import org.http4s._\nimport org.http4s.dsl.Http4sDsl\n'
        'object Main extends App\n'
    )
    (tmp_path / "build.sbt").write_text(
        'name := "myapp"\nversion := "1.0"\n'
        'libraryDependencies ++= Dependencies.http4sClient\n'
    )
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "Dependencies.scala").write_text(
        'import sbt._\nobject Dependencies {\n'
        '  val http4sClient = Seq(\n'
        '    "org.http4s" %% "http4s-ember-client" % "0.23.0",\n'
        '    "org.http4s" %% "http4s-dsl" % "0.23.0",\n'
        '  )\n'
        '}\n'
    )
    (project_dir / "build.properties").write_text("sbt.version=1.9.0\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "http4s" in data["profile"]["frameworks"]


def test_detects_scala_play_from_project_plugins(tmp_path: Path) -> None:
    """Play projects declare the sbt-play plugin in ``project/plugins.sbt``
    and the user's app code lives alongside a ``build.sbt`` that may not
    itself reference ``com.typesafe.play`` directly.
    """
    # Production import so framework-validation keeps Play confirmed
    # rather than demoting it to dev_frameworks.
    (tmp_path / "Main.scala").write_text(
        'import play.api._\nobject Main extends App\n'
    )
    (tmp_path / "build.sbt").write_text(
        'name := "play-app"\nversion := "1.0"\nenablePlugins(PlayScala)\n'
    )
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "plugins.sbt").write_text(
        'addSbtPlugin("com.typesafe.play" %% "sbt-plugin" % "2.9.0")\n'
    )
    (project_dir / "build.properties").write_text("sbt.version=1.9.0\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "play" in (
        data["profile"]["frameworks"] + data["profile"].get("dev_frameworks", [])
    )


# Ruby framework detection tests


def test_detects_ruby_rails_framework(tmp_path: Path) -> None:
    """Should detect Rails from Gemfile."""
    (tmp_path / "app.rb").write_text("require 'rails'\n")
    (tmp_path / "Gemfile").write_text("""source 'https://rubygems.org'

gem 'rails', '~> 7.0'
gem 'pg'
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "rails" in data["profile"]["frameworks"]


def test_detects_ruby_sinatra_framework(tmp_path: Path) -> None:
    """Should detect Sinatra from Gemfile."""
    (tmp_path / "app.rb").write_text("require 'sinatra'\n")
    (tmp_path / "Gemfile").write_text("""source 'https://rubygems.org'

gem 'sinatra'
gem 'puma'
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "sinatra" in data["profile"]["frameworks"]


def test_detects_ruby_grape_framework(tmp_path: Path) -> None:
    """Should detect Grape from Gemfile."""
    (tmp_path / "app.rb").write_text("require 'grape'\n")
    (tmp_path / "Gemfile").write_text("""source 'https://rubygems.org'

gem 'grape'
gem 'grape-entity'
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "grape" in data["profile"]["frameworks"]


# Elixir framework detection tests


def test_detects_elixir_phoenix_framework(tmp_path: Path) -> None:
    """Should detect Phoenix from mix.exs."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "app.ex").write_text("defmodule App do\nend\n")
    (tmp_path / "mix.exs").write_text("""defmodule App.MixProject do
  use Mix.Project

  defp deps do
    [
      {:phoenix, "~> 1.7"},
      {:phoenix_live_view, "~> 0.19"}
    ]
  end
end
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "phoenix" in _all_detected_frameworks(data)


def test_detects_elixir_ecto_framework(tmp_path: Path) -> None:
    """Should detect Ecto from mix.exs."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "app.ex").write_text("defmodule App do\nend\n")
    (tmp_path / "mix.exs").write_text("""defmodule App.MixProject do
  use Mix.Project

  defp deps do
    [
      {:ecto, "~> 3.10"},
      {:ecto_sql, "~> 3.10"}
    ]
  end
end
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "ecto" in _all_detected_frameworks(data)


# Dart/Flutter framework detection tests


def test_detects_dart_language(tmp_path: Path) -> None:
    """Should detect Dart files."""
    (tmp_path / "main.dart").write_text("void main() {}\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "dart" in data["profile"]["languages"]


def test_detects_flutter_framework(tmp_path: Path) -> None:
    """Should detect Flutter SDK from pubspec.yaml."""
    (tmp_path / "main.dart").write_text("import 'package:flutter/material.dart';\n")
    (tmp_path / "pubspec.yaml").write_text("""name: myapp
dependencies:
  flutter:
    sdk: flutter
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "flutter" in data["profile"]["frameworks"]


def test_detects_flutter_bloc_framework(tmp_path: Path) -> None:
    """Should detect Flutter Bloc state management from pubspec.yaml."""
    (tmp_path / "main.dart").write_text("import 'package:flutter_bloc/flutter_bloc.dart';\n")
    (tmp_path / "pubspec.yaml").write_text("""name: myapp
dependencies:
  flutter:
    sdk: flutter
  flutter_bloc: ^8.0.0
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "flutter" in data["profile"]["frameworks"]
    assert "flutter_bloc" in data["profile"]["frameworks"]


def test_handles_unreadable_pubspec(tmp_path: Path) -> None:
    """Should gracefully handle unreadable pubspec.yaml."""
    (tmp_path / "main.dart").write_text("void main() {}\n")
    # Create a directory named pubspec.yaml (reading it will fail)
    (tmp_path / "pubspec.yaml").mkdir()

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # Should still detect Dart, just not Flutter frameworks
    assert "dart" in data["profile"]["languages"]


# Mobile framework detection tests (React Native, Expo, etc.)


def test_detects_react_native_framework(tmp_path: Path) -> None:
    """Should detect React Native from package.json."""
    (tmp_path / "App.js").write_text("import { View } from 'react-native';\n")
    (tmp_path / "package.json").write_text("""{
    "dependencies": {
        "react": "^18.0.0",
        "react-native": "^0.72.0"
    }
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "react-native" in _all_detected_frameworks(data)
    assert "react" in _all_detected_frameworks(data)


def test_detects_expo_framework(tmp_path: Path) -> None:
    """Should detect Expo from package.json."""
    (tmp_path / "App.js").write_text("import { StatusBar } from 'expo-status-bar';\n")
    (tmp_path / "package.json").write_text("""{
    "dependencies": {
        "expo": "^49.0.0",
        "react-native": "^0.72.0"
    }
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "expo" in _all_detected_frameworks(data)


# Meta-framework detection tests


def test_detects_nextjs_framework(tmp_path: Path) -> None:
    """Should detect Next.js from package.json."""
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages/index.tsx").write_text("export default function Home() {}\n")
    (tmp_path / "package.json").write_text("""{
    "dependencies": {
        "next": "^14.0.0",
        "react": "^18.0.0"
    }
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "next" in data["profile"]["frameworks"]


def test_detects_astro_framework(tmp_path: Path) -> None:
    """Should detect Astro from package.json."""
    (tmp_path / "src" / "pages").mkdir(parents=True)
    (tmp_path / "src/pages/index.astro").write_text("---\n---\n<html></html>\n")
    (tmp_path / "package.json").write_text("""{
    "dependencies": {
        "astro": "^4.0.0"
    }
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "astro" in data["profile"]["frameworks"]


# Desktop framework detection tests


def test_detects_electron_framework(tmp_path: Path) -> None:
    """Should detect Electron from package.json."""
    (tmp_path / "main.js").write_text("const { app } = require('electron');\n")
    (tmp_path / "package.json").write_text("""{
    "devDependencies": {
        "electron": "^28.0.0"
    }
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "electron" in data["profile"]["frameworks"]


def test_detects_tauri_js_framework(tmp_path: Path) -> None:
    """Should detect Tauri from package.json."""
    (tmp_path / "App.tsx").write_text("import { invoke } from '@tauri-apps/api';\n")
    (tmp_path / "package.json").write_text("""{
    "dependencies": {
        "@tauri-apps/api": "^1.5.0"
    }
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "tauri" in data["profile"]["frameworks"]


# Blockchain/Web3 framework detection tests


def test_detects_hardhat_framework(tmp_path: Path) -> None:
    """Should detect Hardhat from package.json."""
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts/Token.sol").write_text("pragma solidity ^0.8.0;\n")
    (tmp_path / "package.json").write_text("""{
    "devDependencies": {
        "hardhat": "^2.19.0"
    }
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "hardhat" in data["profile"]["frameworks"]


def test_detects_ethersjs_framework(tmp_path: Path) -> None:
    """Should detect ethers.js from package.json."""
    (tmp_path / "app.js").write_text("const { ethers } = require('ethers');\n")
    (tmp_path / "package.json").write_text("""{
    "dependencies": {
        "ethers": "^6.0.0"
    }
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "ethers" in data["profile"]["frameworks"]


def test_extra_excludes_filters_files(tmp_path: Path) -> None:
    """Extra excludes should filter out files from language detection."""
    from hypergumbo_core.profile import detect_profile

    # Create Python files
    (tmp_path / "app.py").write_text("def main():\n    pass\n")
    (tmp_path / "generated.py").write_text("def generated():\n    pass\n")

    # Without extra excludes - should see 2 Python files
    profile = detect_profile(tmp_path)
    assert profile.languages.get("python", {}).files == 2

    # With extra excludes - should exclude generated.py
    profile = detect_profile(tmp_path, extra_excludes=["generated.py"])
    assert profile.languages.get("python", {}).files == 1


# Solidity framework detection tests


def test_detects_solidity_language(tmp_path: Path) -> None:
    """Should detect Solidity files."""
    (tmp_path / "Token.sol").write_text("pragma solidity ^0.8.0;\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "solidity" in data["profile"]["languages"]


def test_detects_foundry_framework(tmp_path: Path) -> None:
    """Should detect Foundry framework from foundry.toml."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src/Token.sol").write_text("pragma solidity ^0.8.0;\n")
    (tmp_path / "foundry.toml").write_text("""[profile.default]
src = "src"
out = "out"
libs = ["lib"]
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "foundry" in data["profile"]["frameworks"]


def test_detects_hardhat_framework_from_config_js(tmp_path: Path) -> None:
    """Should detect Hardhat framework from hardhat.config.js."""
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts/Token.sol").write_text("pragma solidity ^0.8.0;\n")
    (tmp_path / "hardhat.config.js").write_text("""module.exports = {
  solidity: "0.8.19",
};
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "hardhat" in _all_detected_frameworks(data)


def test_detects_hardhat_framework_from_config_ts(tmp_path: Path) -> None:
    """Should detect Hardhat framework from hardhat.config.ts."""
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts/Token.sol").write_text("pragma solidity ^0.8.0;\n")
    (tmp_path / "hardhat.config.ts").write_text("""import { HardhatUserConfig } from "hardhat/config";
const config: HardhatUserConfig = {
  solidity: "0.8.19",
};
export default config;
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "hardhat" in _all_detected_frameworks(data)


def test_detects_both_foundry_and_hardhat(tmp_path: Path) -> None:
    """Should detect both Foundry and Hardhat when both configs exist."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src/Token.sol").write_text("pragma solidity ^0.8.0;\n")
    (tmp_path / "foundry.toml").write_text('[profile.default]\nsrc = "src"\n')
    (tmp_path / "hardhat.config.js").write_text('module.exports = {};\n')

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "foundry" in data["profile"]["frameworks"]
    assert "hardhat" in data["profile"]["frameworks"]


def test_count_loc_with_max_file_size(tmp_path: Path) -> None:
    """Should skip files larger than max_file_size when specified."""
    from hypergumbo_core.profile import _count_loc

    # Create a small file - should be counted regardless
    small_file = tmp_path / "small.py"
    small_content = "x = 1\n" * 100  # 600 bytes
    small_file.write_text(small_content)
    assert _count_loc(small_file) == 100
    assert _count_loc(small_file, max_file_size=1000) == 100

    # Create a larger file
    large_file = tmp_path / "large.py"
    large_content = "x = 1\n" * 500  # 3000 bytes
    large_file.write_text(large_content)

    # Without max_file_size, counts all lines
    assert _count_loc(large_file) == 500
    # With max_file_size below file size, returns 0
    assert _count_loc(large_file, max_file_size=1000) == 0
    # With max_file_size above file size, counts all lines
    assert _count_loc(large_file, max_file_size=10000) == 500


def test_detect_languages_loc_counting(tmp_path: Path) -> None:
    """_detect_languages counts LOC when count_loc=True, returns 0 otherwise."""
    from hypergumbo_core.profile import _detect_languages

    # Create Python files
    (tmp_path / "small.py").write_text("print('hi')\n")
    (tmp_path / "large.py").write_text("x = 1\n" * 500)

    # Default: no LOC counting
    langs = _detect_languages(tmp_path)
    assert langs["python"].files == 2
    assert langs["python"].loc == 0

    # With count_loc=True: LOC is computed
    langs = _detect_languages(tmp_path, count_loc=True)
    assert langs["python"].files == 2
    assert langs["python"].loc == 501  # 1 + 500


def test_detect_profile_count_loc(tmp_path: Path) -> None:
    """detect_profile passes count_loc through to _detect_languages."""
    from hypergumbo_core.profile import detect_profile

    (tmp_path / "app.py").write_text("def main():\n    print('hello')\n")

    # Default: loc=0
    profile = detect_profile(tmp_path)
    assert profile.languages["python"].loc == 0

    # With count_loc=True: loc is computed
    profile = detect_profile(tmp_path, count_loc=True)
    assert profile.languages["python"].loc == 2


# Recursive manifest scanning tests


def test_detects_python_framework_in_subdirectory(tmp_path: Path) -> None:
    """Should detect FastAPI from pyproject.toml in a subdirectory."""
    # Simulate monorepo structure: backend/pyproject.toml
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "app.py").write_text("from fastapi import FastAPI\n")
    (backend / "pyproject.toml").write_text("""[project]
dependencies = ["fastapi"]
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "fastapi" in data["profile"]["frameworks"]


def test_detects_js_framework_in_subdirectory(tmp_path: Path) -> None:
    """Should detect React from package.json in a subdirectory."""
    # Simulate monorepo structure: frontend/package.json
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "app.js").write_text("import React from 'react';\n")
    (frontend / "package.json").write_text(json.dumps({
        "dependencies": {"react": "^18.0.0"}
    }))

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "react" in data["profile"]["frameworks"]


def test_detects_frameworks_from_multiple_subdirectories(tmp_path: Path) -> None:
    """Should detect frameworks from both backend and frontend subdirectories."""
    # Backend with FastAPI
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "app.py").write_text("from fastapi import FastAPI\n")
    (backend / "pyproject.toml").write_text("""[project]
dependencies = ["fastapi"]
""")

    # Frontend with React
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "app.js").write_text("import React from 'react';\n")
    (frontend / "package.json").write_text(json.dumps({
        "dependencies": {"react": "^18.0.0"}
    }))

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "fastapi" in data["profile"]["frameworks"]
    assert "react" in data["profile"]["frameworks"]


def test_recursive_scan_skips_node_modules(tmp_path: Path) -> None:
    """Should not scan package.json inside node_modules."""
    # Create main app
    (tmp_path / "app.js").write_text("console.log('app');\n")

    # Create package.json in node_modules (should be skipped)
    node_modules = tmp_path / "node_modules" / "some-package"
    node_modules.mkdir(parents=True)
    (node_modules / "package.json").write_text(json.dumps({
        "dependencies": {"react": "^18.0.0"}
    }))

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # React should NOT be detected since it's only in node_modules
    assert "react" not in data["profile"]["frameworks"]


def test_recursive_scan_skips_venv(tmp_path: Path) -> None:
    """Should not scan pyproject.toml inside venv directories."""
    # Create main app
    (tmp_path / "app.py").write_text("print('app')\n")

    # Create pyproject.toml in venv (should be skipped)
    venv = tmp_path / "venv" / "lib" / "python3.10"
    venv.mkdir(parents=True)
    (venv / "pyproject.toml").write_text("""[project]
dependencies = ["django"]
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # Django should NOT be detected since it's only in venv
    assert "django" not in data["profile"]["frameworks"]


def test_find_manifest_files_helper(tmp_path: Path) -> None:
    """Test the _find_manifest_files helper directly."""
    from hypergumbo_core.profile import _find_manifest_files

    # Create files at various depths
    (tmp_path / "pyproject.toml").write_text("root")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "pyproject.toml").write_text("backend")
    (tmp_path / "services" / "api").mkdir(parents=True)
    (tmp_path / "services" / "api" / "pyproject.toml").write_text("services/api")

    found = _find_manifest_files(tmp_path, "pyproject.toml")
    paths = [str(p.relative_to(tmp_path)) for p in found]

    assert "pyproject.toml" in paths
    assert "backend/pyproject.toml" in paths
    assert "services/api/pyproject.toml" in paths


def test_find_manifest_skips_test_fixtures(tmp_path: Path) -> None:
    """WI-sudug: manifest files inside test-fixture dirs are skipped.

    Prevents false-positive framework detection from test fixtures. E.g.,
    detekt is a Kotlin tool with no React; its test fixtures contain
    package.json files referencing react for testing purposes.
    """
    from hypergumbo_core.profile import _find_manifest_files

    # Real manifest at root
    (tmp_path / "package.json").write_text('{"dependencies": {"vue": "3.0"}}')

    # Test fixture manifest in various conventional locations
    (tmp_path / "testdata" / "fixture1").mkdir(parents=True)
    (tmp_path / "testdata" / "fixture1" / "package.json").write_text(
        '{"dependencies": {"react": "18.0"}}'
    )
    (tmp_path / "src" / "test" / "resources" / "bad").mkdir(parents=True)
    (tmp_path / "src" / "test" / "resources" / "bad" / "package.json").write_text(
        '{"dependencies": {"react": "18.0"}}'
    )

    found = _find_manifest_files(tmp_path, "package.json")
    paths = [str(p.relative_to(tmp_path)) for p in found]

    # Root package.json included
    assert "package.json" in paths
    # Test-fixture package.json files excluded
    assert not any("testdata" in p for p in paths)
    assert not any("test/resources" in p for p in paths)


def test_detect_js_frameworks_ignores_test_fixture_react(tmp_path: Path) -> None:
    """WI-sudug: React in a test-fixture package.json does not trigger detection."""
    from hypergumbo_core.profile import _detect_js_frameworks

    # Kotlin tool like detekt: no JS deps at root
    # But test fixtures have package.json with react
    (tmp_path / "testdata" / "fixture").mkdir(parents=True)
    (tmp_path / "testdata" / "fixture" / "package.json").write_text(
        '{"dependencies": {"react": "18.0"}}'
    )

    frameworks = _detect_js_frameworks(tmp_path)
    assert "react" not in frameworks


def test_detects_flutter_in_subdirectory(tmp_path: Path) -> None:
    """Should detect Flutter from pubspec.yaml in a subdirectory."""
    # Simulate monorepo with Flutter app in subdirectory
    mobile = tmp_path / "mobile"
    mobile.mkdir()
    (mobile / "lib").mkdir()
    (mobile / "lib" / "main.dart").write_text("void main() {}\n")
    (mobile / "pubspec.yaml").write_text("""name: myapp
dependencies:
  flutter:
    sdk: flutter
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "flutter" in data["profile"]["frameworks"]


# Haskell framework detection tests


def test_detects_haskell_servant_framework_from_cabal(tmp_path: Path) -> None:
    """Should detect Servant from *.cabal file."""
    (tmp_path / "Main.hs").write_text("main = putStrLn \"Hello\"\n")
    (tmp_path / "myapp.cabal").write_text("""name: myapp
version: 0.1.0.0
build-depends:
    base >=4.7 && <5,
    servant-server,
    warp
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "servant" in data["profile"]["frameworks"]


def test_detects_haskell_scotty_framework_from_stack_yaml(tmp_path: Path) -> None:
    """Should detect Scotty from stack.yaml."""
    (tmp_path / "Main.hs").write_text("main = putStrLn \"Hello\"\n")
    (tmp_path / "stack.yaml").write_text("""resolver: lts-21.0
packages:
  - .
extra-deps:
  - scotty-0.12.1
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "scotty" in data["profile"]["frameworks"]


def test_detects_haskell_servant_from_package_yaml(tmp_path: Path) -> None:
    """Should detect Servant from package.yaml (hpack)."""
    (tmp_path / "Main.hs").write_text("main = putStrLn \"Hello\"\n")
    (tmp_path / "package.yaml").write_text("""name: myapp
dependencies:
  - base >= 4.7 && < 5
  - servant
  - servant-server
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "servant" in data["profile"]["frameworks"]


def test_detects_haskell_yesod_framework_from_cabal(tmp_path: Path) -> None:
    """WI-vabiv: detect Yesod from *.cabal dependency.

    Yesod (UAT BUG-16 / haskellers) is the Rails-inspired Haskell web
    framework; detection wires it up like Servant / Scotty so the new
    yesod.yaml patterns are loaded when a repo declares the dependency.
    """
    (tmp_path / "Main.hs").write_text("main = putStrLn \"Hello\"\n")
    (tmp_path / "myapp.cabal").write_text("""name: myapp
version: 0.1.0.0
build-depends:
    base >=4.7 && <5,
    yesod,
    yesod-core,
    yesod-auth
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "yesod" in data["profile"]["frameworks"]


def test_detects_haskell_yesod_from_package_yaml(tmp_path: Path) -> None:
    """WI-vabiv: detect Yesod from package.yaml (hpack)."""
    (tmp_path / "Main.hs").write_text("main = putStrLn \"Hello\"\n")
    (tmp_path / "package.yaml").write_text("""name: myapp
dependencies:
  - base >= 4.7 && < 5
  - yesod
  - yesod-persistent
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "yesod" in data["profile"]["frameworks"]


def test_yesod_framework_yaml_loads(tmp_path: Path) -> None:
    """WI-vabiv: yesod.yaml loads via load_framework_patterns and declares
    the expected concepts (application, router, route, auth, model, etc.).
    Guards against typos or schema drift in the new file.
    """
    from hypergumbo_core.framework_patterns import load_framework_patterns

    pattern_def = load_framework_patterns("yesod")
    assert pattern_def is not None, "yesod.yaml must load"
    assert pattern_def.id == "yesod"
    assert pattern_def.language == "haskell"

    concepts = {p.concept for p in pattern_def.patterns}
    for required in ("application", "router", "route", "auth", "model"):
        assert required in concepts, (
            f"yesod pattern set missing concept: {required}"
        )


# Clojure framework detection tests


def test_detects_clojure_ring_framework_from_deps_edn(tmp_path: Path) -> None:
    """Should detect Ring/Compojure from deps.edn."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "core.clj").write_text("(ns myapp.core)\n")
    (tmp_path / "deps.edn").write_text("""{:deps
 {ring/ring-core {:mvn/version "1.10.0"}
  compojure/compojure {:mvn/version "1.7.0"}}}
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "ring-compojure" in data["profile"]["frameworks"]


def test_detects_clojure_pedestal_framework_from_project_clj(tmp_path: Path) -> None:
    """Should detect Pedestal from project.clj (Leiningen)."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "core.clj").write_text("(ns myapp.core)\n")
    (tmp_path / "project.clj").write_text("""(defproject myapp "0.1.0"
  :dependencies [[org.clojure/clojure "1.11.1"]
                 [io.pedestal/pedestal.service "0.6.0"]
                 [io.pedestal/pedestal.route "0.6.0"]])
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "pedestal" in data["profile"]["frameworks"]


# R framework detection tests


def test_detects_r_shiny_framework(tmp_path: Path) -> None:
    """Should detect Shiny from DESCRIPTION file."""
    (tmp_path / "app.R").write_text("library(shiny)\nshinyApp(ui, server)\n")
    (tmp_path / "DESCRIPTION").write_text("""Package: myapp
Title: My Shiny App
Version: 0.1.0
Imports:
    shiny,
    dplyr
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "shiny" in data["profile"]["frameworks"]


def test_detects_r_plumber_framework(tmp_path: Path) -> None:
    """Should detect Plumber from DESCRIPTION file."""
    (tmp_path / "api.R").write_text("#* @get /hello\nfunction() 'Hello'\n")
    (tmp_path / "DESCRIPTION").write_text("""Package: myapi
Title: My API
Version: 0.1.0
Imports:
    plumber
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "plumber" in data["profile"]["frameworks"]


# Lua framework detection tests


def test_detects_lua_openresty_framework(tmp_path: Path) -> None:
    """Should detect OpenResty from rockspec or nginx.conf."""
    (tmp_path / "app.lua").write_text("ngx.say('Hello')\n")
    (tmp_path / "nginx.conf").write_text("""
http {
    server {
        location / {
            content_by_lua_block {
                ngx.say("Hello from OpenResty")
            }
        }
    }
}
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "openresty" in data["profile"]["frameworks"]


def test_detects_lua_lapis_framework(tmp_path: Path) -> None:
    """Should detect Lapis from rockspec file."""
    (tmp_path / "app.lua").write_text("local lapis = require('lapis')\n")
    (tmp_path / "myapp-1.0-1.rockspec").write_text("""package = "myapp"
version = "1.0-1"
dependencies = {
    "lua >= 5.1",
    "lapis"
}
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "lapis" in data["profile"]["frameworks"]


# C++ framework detection tests


def test_detects_cpp_qt_framework_from_cmake(tmp_path: Path) -> None:
    """Should detect Qt from CMakeLists.txt."""
    (tmp_path / "main.cpp").write_text("#include <QApplication>\nint main() {}\n")
    (tmp_path / "CMakeLists.txt").write_text("""cmake_minimum_required(VERSION 3.16)
project(myapp)
find_package(Qt6 REQUIRED COMPONENTS Widgets)
add_executable(myapp main.cpp)
target_link_libraries(myapp Qt6::Widgets)
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "qt" in _all_detected_frameworks(data)


def test_detects_cpp_qt_framework_from_pro(tmp_path: Path) -> None:
    """Should detect Qt from .pro (qmake) file."""
    (tmp_path / "main.cpp").write_text("#include <QApplication>\nint main() {}\n")
    (tmp_path / "myapp.pro").write_text("""QT += widgets
SOURCES += main.cpp
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "qt" in _all_detected_frameworks(data)


# Erlang framework detection tests


def test_detects_erlang_cowboy_framework(tmp_path: Path) -> None:
    """Should detect Cowboy from rebar.config."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "myapp.erl").write_text("-module(myapp).\n")
    (tmp_path / "rebar.config").write_text("""{deps, [
    {cowboy, "2.10.0"}
]}.
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "cowboy" in data["profile"]["frameworks"]


# F# framework detection tests


def test_detects_fsharp_giraffe_framework(tmp_path: Path) -> None:
    """Should detect Giraffe from .fsproj file."""
    (tmp_path / "Program.fs").write_text("open Giraffe\n[<EntryPoint>]\nlet main _ = 0\n")
    (tmp_path / "myapp.fsproj").write_text("""<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Giraffe" Version="6.0.0" />
  </ItemGroup>
</Project>
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "giraffe" in data["profile"]["frameworks"]


def test_detects_fsharp_saturn_framework(tmp_path: Path) -> None:
    """Should detect Saturn from .fsproj file."""
    (tmp_path / "Program.fs").write_text("open Saturn\n[<EntryPoint>]\nlet main _ = 0\n")
    (tmp_path / "myapp.fsproj").write_text("""<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Saturn" Version="0.16.1" />
  </ItemGroup>
</Project>
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "saturn" in data["profile"]["frameworks"]


# Kotlin-specific framework detection tests


def test_detects_kotlin_ktor_from_gradle_kts(tmp_path: Path) -> None:
    """Should detect Ktor from build.gradle.kts."""
    (tmp_path / "Application.kt").write_text("fun main() {}\n")
    (tmp_path / "build.gradle.kts").write_text("""dependencies {
    implementation("io.ktor:ktor-server-core:2.3.0")
    implementation("io.ktor:ktor-server-netty:2.3.0")
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "ktor" in data["profile"]["frameworks"]


def test_detects_kotlin_exposed_framework(tmp_path: Path) -> None:
    """Should detect Exposed ORM from build.gradle.kts."""
    (tmp_path / "Database.kt").write_text("import org.jetbrains.exposed.sql.*\n")
    (tmp_path / "build.gradle.kts").write_text("""dependencies {
    implementation("org.jetbrains.exposed:exposed-core:0.44.0")
    implementation("org.jetbrains.exposed:exposed-dao:0.44.0")
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "exposed" in _all_detected_frameworks(data)


# C# framework detection tests


def test_detects_csharp_aspnetcore_framework(tmp_path: Path) -> None:
    """Should detect ASP.NET Core from .csproj file."""
    (tmp_path / "Program.cs").write_text("using Microsoft.AspNetCore;\n")
    (tmp_path / "myapp.csproj").write_text("""<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.AspNetCore.OpenApi" Version="8.0.0" />
  </ItemGroup>
</Project>
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "aspnetcore" in data["profile"]["frameworks"]


def test_detects_csharp_blazor_framework(tmp_path: Path) -> None:
    """Should detect Blazor from .csproj file."""
    (tmp_path / "App.razor").write_text("<Router AppAssembly=\"@typeof(App).Assembly\"/>\n")
    (tmp_path / "myapp.csproj").write_text("""<Project Sdk="Microsoft.NET.Sdk.BlazorWebAssembly">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.AspNetCore.Components.WebAssembly" Version="8.0.0" />
  </ItemGroup>
</Project>
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "blazor" in data["profile"]["frameworks"]


# Dart web framework detection tests


def test_detects_dart_shelf_framework(tmp_path: Path) -> None:
    """Should detect Shelf from pubspec.yaml."""
    (tmp_path / "bin" / "server.dart").parent.mkdir(parents=True)
    (tmp_path / "bin" / "server.dart").write_text("import 'package:shelf/shelf.dart';\n")
    (tmp_path / "pubspec.yaml").write_text("""name: myserver
dependencies:
  shelf: ^1.4.0
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "shelf" in _all_detected_frameworks(data)


def test_detects_dart_serverpod_framework(tmp_path: Path) -> None:
    """Should detect Serverpod from pubspec.yaml."""
    (tmp_path / "lib" / "server.dart").parent.mkdir(parents=True)
    (tmp_path / "lib" / "server.dart").write_text("import 'package:serverpod/serverpod.dart';\n")
    (tmp_path / "pubspec.yaml").write_text("""name: myserver
dependencies:
  serverpod: ^1.2.0
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "serverpod" in _all_detected_frameworks(data)


# Julia framework detection tests


def test_detects_julia_genie_framework(tmp_path: Path) -> None:
    """Should detect Genie from Project.toml."""
    (tmp_path / "src" / "app.jl").parent.mkdir(parents=True)
    (tmp_path / "src" / "app.jl").write_text("using Genie\n")
    (tmp_path / "Project.toml").write_text("""name = "MyApp"
[deps]
Genie = "c43c736e-a2d1-11e8-161f-af95117fbd1e"
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "genie" in data["profile"]["frameworks"]


def test_detects_julia_oxygen_framework(tmp_path: Path) -> None:
    """Should detect Oxygen from Project.toml."""
    (tmp_path / "src" / "app.jl").parent.mkdir(parents=True)
    (tmp_path / "src" / "app.jl").write_text("using Oxygen\n")
    (tmp_path / "Project.toml").write_text("""name = "MyApp"
[deps]
Oxygen = "c43c736e-a2d1-11e8-161f-af95117fbd1e"
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "oxygen" in data["profile"]["frameworks"]


# OCaml framework detection tests


def test_detects_ocaml_dream_framework(tmp_path: Path) -> None:
    """Should detect Dream from dune-project or .opam file."""
    (tmp_path / "main.ml").write_text("let () = Dream.run @@ Dream.router []\n")
    (tmp_path / "myapp.opam").write_text("""opam-version: "2.0"
depends: [
  "dream" {>= "1.0.0"}
]
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "dream" in data["profile"]["frameworks"]


def test_detects_ocaml_cohttp_framework(tmp_path: Path) -> None:
    """Should detect Cohttp from dune-project."""
    (tmp_path / "main.ml").write_text("open Cohttp_lwt_unix\n")
    (tmp_path / "dune-project").write_text("""(lang dune 3.0)
(name myapp)
(package (depends cohttp-lwt-unix))
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "cohttp" in _all_detected_frameworks(data)


# Nim framework detection tests


def test_detects_nim_jester_framework(tmp_path: Path) -> None:
    """Should detect Jester from .nimble file."""
    (tmp_path / "src" / "app.nim").parent.mkdir(parents=True)
    (tmp_path / "src" / "app.nim").write_text("import jester\n")
    (tmp_path / "myapp.nimble").write_text("""version = "0.1.0"
requires "jester >= 0.5.0"
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "jester" in _all_detected_frameworks(data)


def test_detects_nim_prologue_framework(tmp_path: Path) -> None:
    """Should detect Prologue from .nimble file."""
    (tmp_path / "src" / "app.nim").parent.mkdir(parents=True)
    (tmp_path / "src" / "app.nim").write_text("import prologue\n")
    (tmp_path / "myapp.nimble").write_text("""version = "0.1.0"
requires "prologue >= 0.6.0"
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "prologue" in _all_detected_frameworks(data)


# Zig framework detection tests


def test_detects_zig_zap_framework(tmp_path: Path) -> None:
    """Should detect zap from build.zig.zon."""
    (tmp_path / "src" / "main.zig").parent.mkdir(parents=True)
    (tmp_path / "src" / "main.zig").write_text("const zap = @import(\"zap\");\n")
    (tmp_path / "build.zig.zon").write_text(""".{
    .name = "myapp",
    .dependencies = .{
        .zap = .{
            .url = "https://github.com/zigzap/zap/archive/refs/tags/v0.0.1.tar.gz",
        },
    },
}
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "zap" in data["profile"]["frameworks"]


# D framework detection tests


def test_detects_d_vibed_framework(tmp_path: Path) -> None:
    """Should detect vibe.d from dub.json."""
    (tmp_path / "source" / "app.d").parent.mkdir(parents=True)
    (tmp_path / "source" / "app.d").write_text("import vibe.d;\n")
    (tmp_path / "dub.json").write_text("""{
    "name": "myapp",
    "dependencies": {
        "vibe-d": "~>0.9.0"
    }
}
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "vibe-d" in _all_detected_frameworks(data)


def test_detects_d_hunt_framework(tmp_path: Path) -> None:
    """Should detect Hunt from dub.sdl."""
    (tmp_path / "source" / "app.d").parent.mkdir(parents=True)
    (tmp_path / "source" / "app.d").write_text("import hunt.framework;\n")
    (tmp_path / "dub.sdl").write_text("""name "myapp"
dependency "hunt-framework" version="~>3.0.0"
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "hunt" in _all_detected_frameworks(data)


# Groovy framework detection tests


def test_detects_groovy_grails_framework(tmp_path: Path) -> None:
    """Should detect Grails from build.gradle."""
    (tmp_path / "grails-app" / "controllers").mkdir(parents=True)
    (tmp_path / "grails-app" / "controllers" / "HomeController.groovy").write_text(
        "class HomeController {}\n"
    )
    (tmp_path / "build.gradle").write_text("""plugins {
    id "org.grails.grails-web" version "5.3.0"
}

dependencies {
    implementation 'org.grails:grails-core'
}
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "grails" in data["profile"]["frameworks"]


def test_detects_groovy_ratpack_framework(tmp_path: Path) -> None:
    """Should detect Ratpack from build.gradle."""
    (tmp_path / "src" / "main" / "groovy").mkdir(parents=True)
    (tmp_path / "src" / "main" / "groovy" / "App.groovy").write_text(
        "import ratpack.groovy.Groovy\n"
    )
    (tmp_path / "build.gradle").write_text("""plugins {
    id 'io.ratpack.ratpack-groovy' version '1.9.0'
}

dependencies {
    implementation 'io.ratpack:ratpack-core'
}
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "ratpack" in _all_detected_frameworks(data)


def test_bottleneck_does_not_trigger_bottle_detection(tmp_path: Path) -> None:
    """The package 'bottleneck' should not cause 'bottle' framework detection.

    Substring matching on 'bottle' in 'bottleneck' caused false Bottle
    framework detection, which then applied Bottle's bare @patch/@get
    decorator patterns to non-Bottle repos (e.g., Superset).
    """
    (tmp_path / "app.py").write_text("import bottleneck\nx = bottleneck.nanmean([1])\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "myapp"\ndependencies = ["bottleneck", "pandas"]\n'
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "bottle" not in data["profile"]["frameworks"]


def test_bottle_detected_when_actual_dependency(tmp_path: Path) -> None:
    """The actual 'bottle' package should still be detected."""
    (tmp_path / "app.py").write_text("from bottle import route, run\n")
    (tmp_path / "requirements.txt").write_text("bottle==0.12.25\nrequests\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "bottle" in data["profile"]["frameworks"]


def test_detects_guice_from_maven(tmp_path: Path) -> None:
    """Should detect Google Guice from pom.xml."""
    (tmp_path / "Main.java").write_text("public class Main {}\n")
    (tmp_path / "pom.xml").write_text("""<?xml version="1.0"?>
<project>
    <dependencies>
        <dependency>
            <groupId>com.google.inject</groupId>
            <artifactId>guice</artifactId>
        </dependency>
    </dependencies>
</project>""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "guice" in data["profile"]["frameworks"]


def test_detects_jakarta_cdi_from_maven(tmp_path: Path) -> None:
    """Should detect Jakarta CDI from pom.xml with CDI context import."""
    (tmp_path / "Main.java").write_text("public class Main {}\n")
    (tmp_path / "pom.xml").write_text("""<?xml version="1.0"?>
<project>
    <dependencies>
        <dependency>
            <groupId>org.jboss.weld.se</groupId>
            <artifactId>weld-se-core</artifactId>
        </dependency>
    </dependencies>
</project>""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "jakarta-cdi" in data["profile"]["frameworks"]


def test_detects_guice_from_gradle(tmp_path: Path) -> None:
    """Should detect Google Guice from build.gradle."""
    (tmp_path / "Main.java").write_text("public class Main {}\n")
    (tmp_path / "build.gradle").write_text("""dependencies {
    implementation 'com.google.inject:guice:5.1.0'
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "guice" in data["profile"]["frameworks"]


def test_detects_jaxrs_from_auxiliary_gradle_file(tmp_path: Path) -> None:
    """Should detect JAX-RS from gradle/dependencies.gradle (auxiliary Gradle files).

    Multi-module Gradle projects like Apache Kafka declare dependencies in
    files like gradle/dependencies.gradle rather than in build.gradle directly.
    The framework detector must scan *.gradle files in the gradle/ directory.
    """
    (tmp_path / "Main.java").write_text("public class Main {}\n")
    # Root build.gradle exists but doesn't declare JAX-RS
    (tmp_path / "build.gradle").write_text("""plugins {
    id 'java'
}""")
    # JAX-RS dependency declared in gradle/dependencies.gradle (Kafka pattern)
    gradle_dir = tmp_path / "gradle"
    gradle_dir.mkdir()
    (gradle_dir / "dependencies.gradle").write_text("""
ext {
    versions = [jakartaRs: "3.1.0"]
    libs = [
        jakartaRsApi: "jakarta.ws.rs:jakarta.ws.rs-api:$versions.jakartaRs",
    ]
}
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "jax-rs" in data["profile"]["frameworks"]


def test_bare_graphql_package_does_not_trigger_framework(tmp_path: Path) -> None:
    """Bare 'graphql' npm package should NOT activate graphql framework (WI-rofiz).

    Many JS/TS projects install 'graphql' for type definitions or code
    generation without implementing a GraphQL server. Only server-specific
    packages like @apollo/server should trigger the framework.
    """
    (tmp_path / "app.ts").write_text("console.log('hello');\n")
    (tmp_path / "package.json").write_text("""{
    "dependencies": {
        "graphql": "^16.0.0",
        "express": "^4.18.0"
    }
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "graphql" not in data["profile"]["frameworks"], (
        "Bare 'graphql' npm package should not activate graphql framework"
    )


def test_apollo_server_triggers_graphql_framework(tmp_path: Path) -> None:
    """@apollo/server should activate graphql framework."""
    (tmp_path / "app.ts").write_text("console.log('hello');\n")
    (tmp_path / "package.json").write_text("""{
    "dependencies": {
        "@apollo/server": "^4.0.0",
        "graphql": "^16.0.0"
    }
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "graphql" in data["profile"]["frameworks"]


def test_detects_stapler_from_maven(tmp_path: Path) -> None:
    """Should detect Stapler framework from pom.xml."""
    (tmp_path / "Main.java").write_text("public class Main {}\n")
    (tmp_path / "pom.xml").write_text("""<?xml version="1.0"?>
<project>
    <dependencies>
        <dependency>
            <groupId>org.kohsuke.stapler</groupId>
            <artifactId>stapler-core</artifactId>
        </dependency>
    </dependencies>
</project>""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    assert "stapler" in data["profile"]["frameworks"]


def test_detects_protobuf_framework_from_proto_files(tmp_path: Path) -> None:
    """Protobuf framework detected when .proto files are present."""
    from hypergumbo_core.profile import _detect_protobuf

    (tmp_path / "user.proto").write_text(
        'syntax = "proto3";\nservice UserService { rpc GetUser(Req) returns (Resp); }\n'
    )

    detected = _detect_protobuf(tmp_path)
    assert detected == ["protobuf"]


def test_no_protobuf_without_proto_files(tmp_path: Path) -> None:
    """No protobuf framework when no .proto files exist."""
    from hypergumbo_core.profile import _detect_protobuf

    (tmp_path / "app.py").write_text("print('hello')\n")

    detected = _detect_protobuf(tmp_path)
    assert detected == []


def test_detects_grpc_go_framework(tmp_path: Path) -> None:
    """gRPC detected from Go go.mod dependency."""
    from hypergumbo_core.profile import _detect_go_frameworks

    (tmp_path / "go.mod").write_text(
        "module example.com/myapp\n\nrequire google.golang.org/grpc v1.60.0\n"
    )

    detected = _detect_go_frameworks(tmp_path)
    assert "grpc" in detected


def test_detects_grpc_python_framework(tmp_path: Path) -> None:
    """gRPC detected from Python requirements."""
    from hypergumbo_core.profile import _detect_python_frameworks

    (tmp_path / "requirements.txt").write_text("grpcio==1.60.0\n")

    detected = _detect_python_frameworks(tmp_path)
    assert "grpc" in detected


def test_detects_grpc_java_framework(tmp_path: Path) -> None:
    """gRPC detected from Java pom.xml dependency."""
    from hypergumbo_core.profile import _detect_java_frameworks

    (tmp_path / "pom.xml").write_text("""<?xml version="1.0"?>
<project>
    <dependencies>
        <dependency>
            <groupId>io.grpc</groupId>
            <artifactId>grpc-core</artifactId>
        </dependency>
    </dependencies>
</project>"""
    )

    detected = _detect_java_frameworks(tmp_path)
    assert "grpc" in detected


# ---------------------------------------------------------------------------
# refine_frameworks() tests
# ---------------------------------------------------------------------------


def _make_edge(src: str, dst: str, edge_type: str = "imports"):
    """Helper to create a minimal Edge for testing."""
    from hypergumbo_core.ir import Edge

    return Edge(id=f"e-{src}-{dst}", src=src, dst=dst, edge_type=edge_type, line=1, origin="test", origin_run_id="test")


def _make_symbol(sym_id: str, path: str, language: str = "python"):
    """Helper to create a minimal Symbol for testing."""
    from hypergumbo_core.ir import Span, Symbol

    return Symbol(
        id=sym_id,
        name="func",
        kind="function",
        language=language,
        path=path,
        span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
    )


def _make_profile(frameworks: list[str], mode: str = "auto"):
    """Helper to create a RepoProfile for testing."""
    from hypergumbo_core.profile import RepoProfile

    return RepoProfile(frameworks=frameworks, framework_mode=mode)


def test_refine_frameworks_prod_import_stays() -> None:
    """Framework imported in production code stays in frameworks."""
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile(["flask"])
    edges = [
        _make_edge(
            src="python:src/app.py:1-5:handler:function",
            dst="python:flask:0-0:module:module",
        ),
    ]
    symbols = [_make_symbol("python:src/app.py:1-5:handler:function", "src/app.py")]

    result = refine_frameworks(profile, edges, symbols)
    assert "flask" in result.frameworks
    assert "flask" not in result.dev_frameworks


def test_refine_frameworks_test_only_import_moves_to_dev() -> None:
    """Framework imported only in test code moves to dev_frameworks."""
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile(["pytest"])
    edges = [
        _make_edge(
            src="python:tests/test_app.py:1-5:test_foo:function",
            dst="python:pytest:0-0:module:module",
        ),
    ]
    symbols = [
        _make_symbol("python:tests/test_app.py:1-5:test_foo:function", "tests/test_app.py"),
    ]

    result = refine_frameworks(profile, edges, symbols)
    assert "pytest" not in result.frameworks
    assert "pytest" in result.dev_frameworks


def test_refine_frameworks_no_imports_moves_to_dev() -> None:
    """Framework with no matching imports moves to dev_frameworks."""
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile(["transformers"])
    # Edge exists but for a different module
    edges = [
        _make_edge(
            src="python:src/app.py:1-5:handler:function",
            dst="python:flask:0-0:module:module",
        ),
    ]
    symbols = [_make_symbol("python:src/app.py:1-5:handler:function", "src/app.py")]

    result = refine_frameworks(profile, edges, symbols)
    assert "transformers" not in result.frameworks
    assert "transformers" in result.dev_frameworks


def test_refine_frameworks_explicit_mode_unchanged() -> None:
    """Explicit mode skips refinement entirely."""
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile(["pytorch"], mode="explicit")
    # No edges at all — but mode is explicit, so no refinement
    result = refine_frameworks(profile, [], [])
    assert "pytorch" in result.frameworks
    assert result.dev_frameworks == []


def test_refine_frameworks_none_mode_unchanged() -> None:
    """None mode skips refinement entirely."""
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile([], mode="none")
    result = refine_frameworks(profile, [], [])
    assert result.frameworks == []
    assert result.dev_frameworks == []


def test_refine_frameworks_all_mode_unchanged() -> None:
    """All mode skips refinement entirely."""
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile(["flask", "django"], mode="all")
    result = refine_frameworks(profile, [], [])
    assert "flask" in result.frameworks
    assert "django" in result.frameworks
    assert result.dev_frameworks == []


def test_refine_frameworks_java_no_import_edges_stays() -> None:
    """Java frameworks stay when Java has no import edges (fallback)."""
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile(["spring-boot"])
    # No import edges at all — Java doesn't produce them
    edges = []
    symbols = []

    result = refine_frameworks(profile, edges, symbols)
    assert "spring-boot" in result.frameworks


def test_refine_frameworks_rails_autoloaded_stays_in_prod(tmp_path) -> None:
    """WI-lohok: Rails (Ruby) is loaded by Bundler at boot; production Ruby
    code never has explicit `require 'rails'` import edges. The previous
    behavior demoted Rails to dev_frameworks even on real Rails apps,
    which prevented rails.yaml from loading and starved the controller /
    route / form / serializer concept linkers of Rails inputs (observed
    on chatwoot in cohort cohort-001/iter-001). The fix exempts Rails
    from the import-edge demotion check so the framework-pattern dispatch
    actually fires on Rails apps.
    """
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile(["rails", "rspec"])
    # Realistic Rails repo: Ruby code has plenty of import edges (to
    # other gems and to internal modules) — so 'ruby' IS in
    # import_edge_langs — but no edge has dst starting with `ruby:rails`.
    # rspec IS imported in test files (so it should still get demoted).
    edges = [
        _make_edge(
            src="ruby:app/controllers/contacts_controller.rb:1-5:show:method",
            dst="ruby:devise:0-0:module:module",
        ),
        _make_edge(
            src="ruby:app/models/contact.rb:1-5:initialize:method",
            dst="ruby:active_record:0-0:module:module",
        ),
        _make_edge(
            src="ruby:spec/models/contact_spec.rb:1-5:test_validates:method",
            dst="ruby:rspec:0-0:module:module",
        ),
    ]
    symbols = [
        _make_symbol(
            "ruby:app/controllers/contacts_controller.rb:1-5:show:method",
            "app/controllers/contacts_controller.rb",
            "ruby",
        ),
        _make_symbol(
            "ruby:app/models/contact.rb:1-5:initialize:method",
            "app/models/contact.rb",
            "ruby",
        ),
        _make_symbol(
            "ruby:spec/models/contact_spec.rb:1-5:test_validates:method",
            "spec/models/contact_spec.rb",
            "ruby",
        ),
    ]

    result = refine_frameworks(profile, edges, symbols)
    assert "rails" in result.frameworks, (
        "Rails should stay in production frameworks (autoload exemption); "
        f"got frameworks={result.frameworks}, dev_frameworks={result.dev_frameworks}"
    )
    assert "rails" not in result.dev_frameworks
    # rspec should still get demoted — it IS imported, but only in spec/
    assert "rspec" not in result.frameworks
    assert "rspec" in result.dev_frameworks


def test_refine_frameworks_rails_kept_even_with_zero_ruby_import_edges() -> None:
    """The exemption applies even if Ruby happens to have zero import
    edges — should never get to the demotion check at all for Rails."""
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile(["rails"])
    # Edge case: trivial Ruby repo with no imports of anything yet.
    edges = []
    symbols = [
        _make_symbol(
            "ruby:app/controllers/application_controller.rb:1-5:foo:method",
            "app/controllers/application_controller.rb",
            "ruby",
        ),
    ]

    result = refine_frameworks(profile, edges, symbols)
    assert "rails" in result.frameworks
    assert "rails" not in result.dev_frameworks


def test_refine_frameworks_sinatra_not_exempted() -> None:
    """Counter-test: sinatra IS explicitly required in app code
    (`require 'sinatra'`) — should NOT be exempt from the demotion
    check. Without an import edge confirming production use, sinatra
    moves to dev_frameworks per the unmodified rule."""
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile(["sinatra"])
    # Ruby has import edges but none to sinatra
    edges = [
        _make_edge(
            src="ruby:app/foo.rb:1-5:bar:method",
            dst="ruby:json:0-0:module:module",
        ),
    ]
    symbols = [
        _make_symbol("ruby:app/foo.rb:1-5:bar:method", "app/foo.rb", "ruby"),
    ]

    result = refine_frameworks(profile, edges, symbols)
    # Sinatra IS required, so missing edge means demote — not exempt.
    assert "sinatra" not in result.frameworks
    assert "sinatra" in result.dev_frameworks


def test_refine_frameworks_import_override_pytorch() -> None:
    """IMPORT_OVERRIDES maps 'torch' manifest pattern to 'torch' import."""
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile(["pytorch"])
    edges = [
        _make_edge(
            src="python:src/model.py:1-5:train:function",
            dst="python:torch:0-0:module:module",
        ),
    ]
    symbols = [_make_symbol("python:src/model.py:1-5:train:function", "src/model.py")]

    result = refine_frameworks(profile, edges, symbols)
    assert "pytorch" in result.frameworks


def test_refine_frameworks_prefix_matching_submodule() -> None:
    """Submodule import (starlette.responses) matches framework (starlette)."""
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile(["starlette"])
    edges = [
        _make_edge(
            src="python:src/serve.py:1-5:lifespan:function",
            dst="python:starlette.responses:0-0:JSONResponse:symbol",
        ),
    ]
    symbols = [_make_symbol("python:src/serve.py:1-5:lifespan:function", "src/serve.py")]

    result = refine_frameworks(profile, edges, symbols)
    assert "starlette" in result.frameworks
    assert "starlette" not in result.dev_frameworks


def test_refine_frameworks_mixed_prod_and_dev() -> None:
    """Multiple frameworks: prod stays, dev-only moves."""
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile(["flask", "pytest"])
    edges = [
        _make_edge(
            src="python:src/app.py:1-5:handler:function",
            dst="python:flask:0-0:module:module",
        ),
        _make_edge(
            src="python:tests/test_app.py:1-5:test_foo:function",
            dst="python:pytest:0-0:module:module",
        ),
    ]
    symbols = [
        _make_symbol("python:src/app.py:1-5:handler:function", "src/app.py"),
        _make_symbol("python:tests/test_app.py:1-5:test_foo:function", "tests/test_app.py"),
    ]

    result = refine_frameworks(profile, edges, symbols)
    assert result.frameworks == ["flask"]
    assert result.dev_frameworks == ["pytest"]


# ---------------------------------------------------------------------------
# WI-palol / INV-rojip: promote-phase tests.
#
# refine_frameworks gains a second decision phase that promotes a framework
# into ``profile.frameworks`` when prod-non-test source files import a
# module matching the framework's registered import-module patterns, even
# if the manifest is silent. A specificity gate ("pattern is scoped, slash-
# compound, or dot-compound") protects against bare-name FPs (`react`,
# `flask`) that were the motivation for WI-rofiz. See INV-rojip for the
# full prior-occurrence history.
# ---------------------------------------------------------------------------


def test_is_specific_pattern_scoped() -> None:
    """Scoped packages (@apollo/server) are specific."""
    from hypergumbo_core.profile import _is_specific_pattern
    assert _is_specific_pattern("@apollo/server")


def test_is_specific_pattern_slash_compound() -> None:
    """Slash-compound paths (github.com/gin-gonic/gin) are specific."""
    from hypergumbo_core.profile import _is_specific_pattern
    assert _is_specific_pattern("github.com/gin-gonic/gin")


def test_is_specific_pattern_dot_compound() -> None:
    """Dot-compound Maven-style coords (org.springframework.boot) are specific."""
    from hypergumbo_core.profile import _is_specific_pattern
    assert _is_specific_pattern("org.springframework.boot")


def test_is_specific_pattern_bare_name_not_specific() -> None:
    """Bare names (react, flask, rails) are not specific — need manifest."""
    from hypergumbo_core.profile import _is_specific_pattern
    assert not _is_specific_pattern("react")
    assert not _is_specific_pattern("flask")
    assert not _is_specific_pattern("rails")


def test_refine_frameworks_promotes_apollo_from_workspace_import() -> None:
    """WI-palol motivating case: apollo-server smoke-test consumer.

    The local package.json declares only graphql + make-fetch-happen;
    Apollo is imported via npm workspace from @apollo/server/standalone.
    Manifest detection sees nothing apollo-related, but prod-non-test
    code imports @apollo/server. The promote phase should add the
    'apollo' framework (which has @apollo/server in its pattern set)
    to profile.frameworks.
    """
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile([])
    edges = [
        _make_edge(
            src="typescript:nodenext/src/smoke-test.ts:18-43:smokeTest:function",
            dst="typescript:@apollo/server/standalone:0-0:startStandaloneServer:symbol",
        ),
    ]
    symbols = [
        _make_symbol(
            "typescript:nodenext/src/smoke-test.ts:18-43:smokeTest:function",
            "nodenext/src/smoke-test.ts",
            language="typescript",
        ),
    ]

    result = refine_frameworks(profile, edges, symbols)
    assert "apollo" in result.frameworks
    # 'graphql' also has @apollo/server in its pattern set — both promote.
    assert "graphql" in result.frameworks


def test_refine_frameworks_does_not_promote_on_test_only_imports() -> None:
    """Test-only imports of a scoped package don't trigger promotion.

    If the only @apollo/server import is in a __tests__/ folder, the
    framework is not a production dep of this repo and should not be
    promoted.
    """
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile([])
    edges = [
        _make_edge(
            src="typescript:__tests__/integration.test.ts:1-5:test_starts:function",
            dst="typescript:@apollo/server/standalone:0-0:startStandaloneServer:symbol",
        ),
    ]
    symbols = [
        _make_symbol(
            "typescript:__tests__/integration.test.ts:1-5:test_starts:function",
            "__tests__/integration.test.ts",
            language="typescript",
        ),
    ]

    result = refine_frameworks(profile, edges, symbols)
    assert "apollo" not in result.frameworks
    assert "apollo" not in result.dev_frameworks


def test_refine_frameworks_does_not_promote_bare_name_react() -> None:
    """Specificity gate: bare-name imports do not promote.

    A prod file importing the bare 'react' module is too weak a signal
    on its own — many non-React tools depend on react for build/codegen.
    Without a manifest declaration, the framework must NOT be promoted.
    This mirrors the WI-rofiz lesson (bare 'graphql' was an FP trigger).
    """
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile([])
    edges = [
        _make_edge(
            src="typescript:src/App.tsx:1-5:App:function",
            dst="typescript:react:0-0:default:symbol",
        ),
    ]
    symbols = [
        _make_symbol(
            "typescript:src/App.tsx:1-5:App:function",
            "src/App.tsx",
            language="typescript",
        ),
    ]

    result = refine_frameworks(profile, edges, symbols)
    assert "react" not in result.frameworks


def test_refine_frameworks_promotes_go_compound_path() -> None:
    """Go imports are always full paths (github.com/owner/repo) and
    therefore always pass the specificity gate."""
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile([])
    edges = [
        _make_edge(
            src="go:cmd/server/main.go:1-5:main:function",
            dst="go:github.com/gin-gonic/gin:0-0:package:package",
        ),
    ]
    symbols = [
        _make_symbol(
            "go:cmd/server/main.go:1-5:main:function",
            "cmd/server/main.go",
            language="go",
        ),
    ]

    result = refine_frameworks(profile, edges, symbols)
    assert "gin" in result.frameworks


def test_refine_frameworks_promote_explicit_mode_skipped() -> None:
    """Explicit mode bypasses promotion (caller intent wins)."""
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile([], mode="explicit")
    edges = [
        _make_edge(
            src="typescript:src/a.ts:1-5:a:function",
            dst="typescript:@apollo/server:0-0:foo:symbol",
        ),
    ]
    symbols = [
        _make_symbol("typescript:src/a.ts:1-5:a:function", "src/a.ts", language="typescript"),
    ]

    result = refine_frameworks(profile, edges, symbols)
    assert "apollo" not in result.frameworks


def test_refine_frameworks_promote_skips_already_in_frameworks() -> None:
    """A framework already in profile.frameworks is not double-promoted.

    Combined with manifest detection: flask is in profile.frameworks
    from manifest scan; concurrent prod imports of @apollo/server still
    cause apollo to be promoted, while flask remains untouched.
    """
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile(["flask"])
    edges = [
        _make_edge(
            src="python:src/app.py:1-5:handler:function",
            dst="python:flask:0-0:module:module",
        ),
        _make_edge(
            src="typescript:src/server.ts:1-5:start:function",
            dst="typescript:@apollo/server:0-0:foo:symbol",
        ),
    ]
    symbols = [
        _make_symbol("python:src/app.py:1-5:handler:function", "src/app.py"),
        _make_symbol(
            "typescript:src/server.ts:1-5:start:function",
            "src/server.ts",
            language="typescript",
        ),
    ]

    result = refine_frameworks(profile, edges, symbols)
    assert "flask" in result.frameworks
    assert "apollo" in result.frameworks
    # flask appears only once in result.frameworks (no double-add).
    assert result.frameworks.count("flask") == 1


def test_refine_frameworks_promote_skips_already_in_dev_frameworks() -> None:
    """A framework already in profile.dev_frameworks (would only happen
    if a prior pass already classified it) is not re-promoted."""
    from hypergumbo_core.profile import refine_frameworks
    from hypergumbo_core.profile import RepoProfile

    profile = RepoProfile(
        frameworks=[],
        dev_frameworks=["apollo"],
        framework_mode="auto",
    )
    edges = [
        _make_edge(
            src="typescript:src/server.ts:1-5:start:function",
            dst="typescript:@apollo/server:0-0:foo:symbol",
        ),
    ]
    symbols = [
        _make_symbol(
            "typescript:src/server.ts:1-5:start:function",
            "src/server.ts",
            language="typescript",
        ),
    ]

    result = refine_frameworks(profile, edges, symbols)
    # apollo stays in dev — the promote phase respects the caller's
    # dev_frameworks classification.
    assert "apollo" not in result.frameworks
    assert "apollo" in result.dev_frameworks


def test_refine_frameworks_go_full_path_matching() -> None:
    """Go framework patterns use full import paths and match exactly."""
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile(["gin"])
    edges = [
        _make_edge(
            src="go:main.go:1-5:main:function",
            dst="go:github.com/gin-gonic/gin:0-0:package:package",
        ),
    ]
    symbols = [_make_symbol("go:main.go:1-5:main:function", "main.go", language="go")]

    result = refine_frameworks(profile, edges, symbols)
    assert "gin" in result.frameworks


def test_refine_frameworks_js_devdep_in_prod_code() -> None:
    """JS framework imported in non-test file is confirmed even if it was a devDep."""
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile(["lit"])
    edges = [
        _make_edge(
            src="typescript:src/components/app.ts:1-5:App:class",
            dst="typescript:lit:0-0:module:module",
        ),
    ]
    symbols = [
        _make_symbol(
            "typescript:src/components/app.ts:1-5:App:class",
            "src/components/app.ts",
            language="typescript",
        ),
    ]

    result = refine_frameworks(profile, edges, symbols)
    assert "lit" in result.frameworks


def test_refine_frameworks_import_override_scikit_learn() -> None:
    """scikit-learn manifest pattern maps to sklearn import."""
    from hypergumbo_core.profile import refine_frameworks

    profile = _make_profile(["scikit-learn"])
    edges = [
        _make_edge(
            src="python:src/ml.py:1-5:train:function",
            dst="python:sklearn.ensemble:0-0:RandomForestClassifier:symbol",
        ),
    ]
    symbols = [_make_symbol("python:src/ml.py:1-5:train:function", "src/ml.py")]

    result = refine_frameworks(profile, edges, symbols)
    assert "scikit-learn" in result.frameworks


def test_refine_frameworks_preserves_other_fields() -> None:
    """Refinement preserves framework_mode and other profile fields."""
    from hypergumbo_core.profile import LanguageStats, RepoProfile, refine_frameworks

    profile = RepoProfile(
        languages={"python": LanguageStats(files=5, loc=100)},
        frameworks=["flask"],
        framework_mode="auto",
        requested_frameworks=[],
    )
    edges = [
        _make_edge(
            src="python:src/app.py:1-5:handler:function",
            dst="python:flask:0-0:module:module",
        ),
    ]
    symbols = [_make_symbol("python:src/app.py:1-5:handler:function", "src/app.py")]

    result = refine_frameworks(profile, edges, symbols)
    assert result.languages == {"python": LanguageStats(files=5, loc=100)}
    assert result.framework_mode == "auto"


# ---------------------------------------------------------------------------
# INV-vunaf: structured manifest dep-name parsing must not produce false
# positives from substring matches in comments, marker names, or
# partial-substring collisions with unrelated packages.
# ---------------------------------------------------------------------------


class TestInvVunafPythonStructuredParsing:
    """INV-vunaf: Python framework detection rejects substring FPs."""

    def test_pytest_marker_named_torch_does_not_trigger_pytorch(
        self, tmp_path: Path
    ) -> None:
        """A pytest marker named ``torch`` must not flag pytorch as a dependency.

        Mirrors the hypergumbo self-analysis FP at ``pyproject.toml:9``::

            markers = ["torch: tests requiring PyTorch (deselect with -m 'not torch')"]
        """
        from hypergumbo_core.profile import _detect_python_frameworks

        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "myapp"\ndependencies = ["fastapi"]\n'
            "[tool.pytest.ini_options]\n"
            'markers = ["torch: tests requiring PyTorch (deselect with -m \'not torch\')"]\n'
        )

        detected = _detect_python_frameworks(tmp_path)
        assert "pytorch" not in detected, (
            f"pytest marker 'torch' must not trigger pytorch detection; got {detected}"
        )
        assert "fastapi" in detected

    def test_sentence_transformers_does_not_trigger_transformers(
        self, tmp_path: Path
    ) -> None:
        """Hyphenated package ``sentence-transformers`` must not flag transformers.

        Mirrors the hypergumbo self-analysis FP at
        ``packages/hypergumbo-core/pyproject.toml:46`` ::

            dependencies = ["sentence-transformers~=5.2.2"]
        """
        from hypergumbo_core.profile import _detect_python_frameworks

        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "myapp"\ndependencies = ["sentence-transformers~=5.2.2"]\n'
        )

        detected = _detect_python_frameworks(tmp_path)
        assert "transformers" not in detected, (
            f"sentence-transformers must not trigger transformers detection; got {detected}"
        )

    def test_comment_mentioning_torch_does_not_trigger_pytorch(
        self, tmp_path: Path
    ) -> None:
        """A comment mentioning torch in pyproject.toml must not trigger pytorch."""
        from hypergumbo_core.profile import _detect_python_frameworks

        (tmp_path / "pyproject.toml").write_text(
            "# We considered torch but went with jax instead.\n"
            '[project]\nname = "myapp"\ndependencies = ["jax"]\n'
        )

        detected = _detect_python_frameworks(tmp_path)
        assert "pytorch" not in detected, (
            f"comment mention of torch must not trigger pytorch; got {detected}"
        )
        assert "jax" in detected

    def test_real_torch_dep_still_detects_pytorch(self, tmp_path: Path) -> None:
        """Regression: a real torch dependency must still trigger pytorch."""
        from hypergumbo_core.profile import _detect_python_frameworks

        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "myapp"\ndependencies = ["torch>=2.0"]\n'
        )

        detected = _detect_python_frameworks(tmp_path)
        assert "pytorch" in detected

    def test_real_transformers_dep_still_detects_transformers(
        self, tmp_path: Path
    ) -> None:
        """Regression: a real transformers dependency must still trigger detection."""
        from hypergumbo_core.profile import _detect_python_frameworks

        (tmp_path / "requirements.txt").write_text("transformers>=4.0\n")

        detected = _detect_python_frameworks(tmp_path)
        assert "transformers" in detected

    def test_requirements_txt_comment_stripped(self, tmp_path: Path) -> None:
        """A commented-out package in requirements.txt must not be detected."""
        from hypergumbo_core.profile import _detect_python_frameworks

        (tmp_path / "requirements.txt").write_text(
            "# torch was removed in favor of jax\njax\n"
        )

        detected = _detect_python_frameworks(tmp_path)
        assert "pytorch" not in detected
        assert "jax" in detected

    def test_pipfile_extracts_packages_section(self, tmp_path: Path) -> None:
        """Pipfile [packages] section dep names are extracted; comments excluded."""
        from hypergumbo_core.profile import _detect_python_frameworks

        (tmp_path / "Pipfile").write_text(
            "# torch mentioned in comment only\n"
            "[packages]\n"
            'flask = "*"\n'
            'sentence-transformers = "*"\n'
        )

        detected = _detect_python_frameworks(tmp_path)
        assert "flask" in detected
        assert "pytorch" not in detected
        assert "transformers" not in detected

    def test_optional_and_dev_deps_in_pyproject_detected(
        self, tmp_path: Path
    ) -> None:
        """Optional/dev deps in pyproject.toml are detected as deps."""
        from hypergumbo_core.profile import _detect_python_frameworks

        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "myapp"\n'
            "dependencies = []\n"
            "[project.optional-dependencies]\n"
            'ml = ["torch>=2.0", "scikit-learn"]\n'
            "[dependency-groups]\n"
            'dev = ["pytest"]\n'
        )

        detected = _detect_python_frameworks(tmp_path)
        assert "pytorch" in detected
        assert "scikit-learn" in detected
        assert "pytest" in detected

    def test_setup_py_install_requires_detected(self, tmp_path: Path) -> None:
        """setup.py install_requires entries are parsed (and comments rejected)."""
        from hypergumbo_core.profile import _detect_python_frameworks

        (tmp_path / "setup.py").write_text(
            "# torch was considered\n"
            "from setuptools import setup\n"
            'setup(name="myapp", install_requires=["flask", "celery>=5.0"])\n'
        )

        detected = _detect_python_frameworks(tmp_path)
        assert "flask" in detected
        assert "celery" in detected
        assert "pytorch" not in detected


class TestInvVunafCrossLanguageFPs:
    """INV-vunaf: parallel FP cases for other detectors."""

    def test_rust_comment_does_not_trigger_actix(self, tmp_path: Path) -> None:
        """A Cargo.toml comment must not produce framework FPs."""
        from hypergumbo_core.profile import _detect_rust_frameworks

        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "myapp"\nversion = "0.1.0"\n'
            "# actix-web was rejected; using tokio + axum instead.\n"
            "[dependencies]\n"
            'tokio = "1.0"\n'
            'axum = "0.6"\n'
        )

        detected = _detect_rust_frameworks(tmp_path)
        assert "tokio" in detected
        assert "axum" in detected
        assert "actix-web" not in detected

    def test_go_vanity_path_collision_does_not_trigger(
        self, tmp_path: Path
    ) -> None:
        """A go.mod comment must not trigger gin via substring collision."""
        from hypergumbo_core.profile import _detect_go_frameworks

        (tmp_path / "go.mod").write_text(
            "module example.com/myapp\n\n"
            "go 1.21\n\n"
            "// github.com/gin-gonic/gin was tried; switched to echo.\n"
            "require (\n"
            "    github.com/labstack/echo/v4 v4.10.0\n"
            ")\n"
        )

        detected = _detect_go_frameworks(tmp_path)
        assert "echo" in detected
        assert "gin" not in detected

    def test_pom_xml_comment_does_not_trigger_spring(
        self, tmp_path: Path
    ) -> None:
        """An XML comment in pom.xml must not flag spring-boot."""
        from hypergumbo_core.profile import _detect_java_frameworks

        (tmp_path / "pom.xml").write_text(
            "<project>\n"
            "  <!-- We considered org.springframework.boot but use micronaut. -->\n"
            "  <dependencies>\n"
            "    <dependency>\n"
            "      <groupId>io.micronaut</groupId>\n"
            "      <artifactId>micronaut-http</artifactId>\n"
            "    </dependency>\n"
            "  </dependencies>\n"
            "</project>\n"
        )

        detected = _detect_java_frameworks(tmp_path)
        assert "micronaut" in detected
        assert "spring-boot" not in detected

    def test_gradle_comment_does_not_trigger_ktor(self, tmp_path: Path) -> None:
        """A Gradle comment must not flag Kotlin frameworks."""
        from hypergumbo_core.profile import _detect_kotlin_frameworks

        (tmp_path / "build.gradle.kts").write_text(
            "// io.ktor was evaluated; using spring-boot instead.\n"
            "dependencies {\n"
            '    implementation("org.springframework.boot:spring-boot-starter-web:3.0.0")\n'
            "}\n"
        )

        detected = _detect_kotlin_frameworks(tmp_path)
        assert "ktor" not in detected

    def test_gemfile_comment_does_not_trigger_rails(
        self, tmp_path: Path
    ) -> None:
        """A Gemfile comment must not flag rails."""
        from hypergumbo_core.profile import _detect_ruby_frameworks

        (tmp_path / "Gemfile").write_text(
            "# rails was migrated away from; using sinatra now.\n"
            'gem "sinatra"\n'
        )

        detected = _detect_ruby_frameworks(tmp_path)
        assert "sinatra" in detected
        assert "rails" not in detected

    def test_description_comment_does_not_trigger_shiny(
        self, tmp_path: Path
    ) -> None:
        """An R DESCRIPTION comment must not flag shiny."""
        from hypergumbo_core.profile import _detect_r_frameworks

        (tmp_path / "DESCRIPTION").write_text(
            "Package: myapp\n"
            "Title: My App\n"
            "Version: 1.0.0\n"
            "# shiny was considered\n"
            "Imports: plumber\n"
        )

        detected = _detect_r_frameworks(tmp_path)
        assert "plumber" in detected
        assert "shiny" not in detected

    def test_groovy_comment_does_not_trigger_grails(
        self, tmp_path: Path
    ) -> None:
        """A Groovy build.gradle comment must not flag grails."""
        from hypergumbo_core.profile import _detect_groovy_frameworks

        (tmp_path / "build.gradle").write_text(
            "// org.grails was evaluated; using ratpack-core instead.\n"
            "dependencies {\n"
            '    implementation "io.ratpack:ratpack-core:1.9.0"\n'
            "}\n"
        )

        detected = _detect_groovy_frameworks(tmp_path)
        assert "ratpack" in detected
        assert "grails" not in detected

    def test_julia_comment_does_not_trigger_genie(
        self, tmp_path: Path
    ) -> None:
        """A Project.toml comment must not flag a Julia framework."""
        from hypergumbo_core.profile import _detect_julia_frameworks

        (tmp_path / "Project.toml").write_text(
            'name = "MyApp"\n'
            "# Genie was considered\n"
            "[deps]\n"
            'HTTP = "cd3eb016-35fb-5094-929b-558a96fad6f3"\n'
        )

        detected = _detect_julia_frameworks(tmp_path)
        assert "http" in detected
        assert "genie" not in detected

    def test_dart_web_pubspec_partial_string_does_not_trigger_shelf(
        self, tmp_path: Path
    ) -> None:
        """A commented-out pubspec.yaml line must not flag shelf."""
        from hypergumbo_core.profile import _detect_dart_web_frameworks

        (tmp_path / "pubspec.yaml").write_text(
            "name: myapp\n"
            "# Removed: shelf: ^1.0.0 -- switched to serverpod\n"
            "dependencies:\n"
            "  serverpod: ^1.0.0\n"
        )

        detected = _detect_dart_web_frameworks(tmp_path)
        assert "serverpod" in detected
        assert "shelf" not in detected

    def test_scala_comment_does_not_trigger_play(
        self, tmp_path: Path
    ) -> None:
        """A Scala build.sbt comment must not flag play."""
        from hypergumbo_core.profile import _detect_scala_frameworks

        (tmp_path / "build.sbt").write_text(
            "// com.typesafe.play was considered; using http4s instead.\n"
            'libraryDependencies += "org.http4s" %% "http4s-blaze-server" % "0.23"\n'
        )

        detected = _detect_scala_frameworks(tmp_path)
        assert "http4s" in detected
        assert "play" not in detected

    def test_swift_package_path_dependency_is_extracted(
        self, tmp_path: Path
    ) -> None:
        """A ``.package(path: "...")`` declaration is also recognized."""
        from hypergumbo_core.profile import _parse_package_swift_deps

        content = (
            "let package = Package(\n"
            '    dependencies: [\n'
            '        .package(path: "../local-pkg/")\n'
            "    ]\n"
            ")\n"
        )
        deps = _parse_package_swift_deps(content)
        assert "local-pkg" in deps

    def test_swift_package_name_dependency_is_extracted(
        self, tmp_path: Path
    ) -> None:
        """A ``.package(name: "...")`` declaration is also recognized."""
        from hypergumbo_core.profile import _parse_package_swift_deps

        content = (
            "let package = Package(\n"
            '    dependencies: [\n'
            '        .package(name: "my-lib", url: "https://example.com/repo.git",\n'
            '                 from: "1.0.0")\n'
            "    ]\n"
            ")\n"
        )
        deps = _parse_package_swift_deps(content)
        assert "my-lib" in deps

    def test_swift_package_comment_does_not_trigger_vapor(
        self, tmp_path: Path
    ) -> None:
        """A Package.swift comment must not flag vapor."""
        from hypergumbo_core.profile import _detect_swift_frameworks

        (tmp_path / "Package.swift").write_text(
            '// swift-tools-version:5.7\n'
            "// vapor was considered; using hummingbird instead.\n"
            "import PackageDescription\n"
            "let package = Package(\n"
            '    name: "myapp",\n'
            "    dependencies: [\n"
            '        .package(url: "https://github.com/hummingbird-project/hummingbird", from: "1.0.0"),\n'
            "    ]\n"
            ")\n"
        )

        detected = _detect_swift_frameworks(tmp_path)
        assert "hummingbird" in detected
        assert "vapor" not in detected


class TestInvVunafParserUnits:
    """Direct unit tests for the structured manifest parsers (INV-vunaf)."""

    def test_load_toml_returns_none_on_malformed(self) -> None:
        from hypergumbo_core.profile import _load_toml
        assert _load_toml("this is = not valid toml [[[") is None

    def test_pep508_dist_name_skips_comment_line(self) -> None:
        from hypergumbo_core.profile import _pep508_dist_name
        assert _pep508_dist_name("# just a comment") is None
        assert _pep508_dist_name("") is None
        assert _pep508_dist_name("!!!") is None  # no valid name

    def test_parse_pyproject_deps_handles_non_dict(self) -> None:
        from hypergumbo_core.profile import _parse_pyproject_deps
        # TOML that parses to a non-dict (impossible for top-level TOML, but
        # _load_toml returning None should produce an empty set).
        assert _parse_pyproject_deps("definitely not [valid") == set()

    def test_parse_pyproject_deps_poetry_style(self) -> None:
        from hypergumbo_core.profile import _parse_pyproject_deps
        content = (
            "[tool.poetry]\nname = 'myapp'\n"
            "[tool.poetry.dependencies]\n"
            "python = '^3.10'\n"
            "fastapi = '^0.100'\n"
            "[tool.poetry.dev-dependencies]\n"
            "pytest = '^7.0'\n"
            "[tool.poetry.group.test.dependencies]\n"
            "httpx = '*'\n"
        )
        deps = _parse_pyproject_deps(content)
        assert "fastapi" in deps
        assert "pytest" in deps
        assert "httpx" in deps
        assert "python" not in deps  # python pin must be excluded

    def test_parse_requirements_txt_skips_pip_options(self) -> None:
        from hypergumbo_core.profile import _parse_requirements_txt_deps
        content = (
            "-r other-requirements.txt\n"
            "-e .\n"
            "fastapi>=0.100\n"
            "--editable git+https://github.com/foo/bar.git\n"
        )
        deps = _parse_requirements_txt_deps(content)
        assert deps == {"fastapi"}

    def test_parse_setup_py_extras_require(self) -> None:
        from hypergumbo_core.profile import _parse_setup_py_deps
        content = (
            "from setuptools import setup\n"
            "setup(name='myapp',\n"
            "    install_requires=['flask'],\n"
            "    extras_require={'ml': ['torch', 'scikit-learn']})\n"
        )
        deps = _parse_setup_py_deps(content)
        assert "flask" in deps
        assert "torch" in deps
        assert "scikit-learn" in deps

    def test_strip_python_line_comments_preserves_strings(self) -> None:
        from hypergumbo_core.profile import _strip_python_line_comments
        # The '#' inside the single-quoted string must not be stripped.
        assert _strip_python_line_comments("x = 'has # inside'  # tail") == (
            "x = 'has # inside'  "
        )
        # Double-quoted variant.
        assert _strip_python_line_comments('y = "has # inside"  # tail') == (
            'y = "has # inside"  '
        )
        # Triple-double quoted (docstring) preserves '#'.
        triple_double = '"""docstring with # inside"""\nx = 1  # tail\n'
        result = _strip_python_line_comments(triple_double)
        assert "# inside" in result
        assert "# tail" not in result
        # Triple-single quoted.
        triple_single = "'''docstring with # inside'''\nx = 1  # tail\n"
        result = _strip_python_line_comments(triple_single)
        assert "# inside" in result
        assert "# tail" not in result

    def test_parse_pipfile_dev_packages(self) -> None:
        from hypergumbo_core.profile import _parse_pipfile_deps
        # Malformed Pipfile yields empty set (covers _load_toml -> None branch).
        assert _parse_pipfile_deps("[[[ not toml") == set()
        # Valid Pipfile yields both packages and dev-packages.
        content = (
            "[packages]\n"
            'flask = "*"\n'
            "[dev-packages]\n"
            'pytest = "*"\n'
        )
        deps = _parse_pipfile_deps(content)
        assert "flask" in deps
        assert "pytest" in deps

    def test_parse_cargo_toml_target_and_workspace_deps(self) -> None:
        from hypergumbo_core.profile import _parse_cargo_toml_deps
        content = (
            '[target."cfg(unix)".dependencies]\n'
            'libc = "0.2"\n'
            "[workspace.dependencies]\n"
            'serde = "1.0"\n'
        )
        deps = _parse_cargo_toml_deps(content)
        assert "libc" in deps
        assert "serde" in deps

    def test_parse_cargo_toml_malformed_returns_empty(self) -> None:
        from hypergumbo_core.profile import _parse_cargo_toml_deps
        assert _parse_cargo_toml_deps("[[[ not toml") == set()

    def test_parse_go_mod_single_line_require(self) -> None:
        from hypergumbo_core.profile import _parse_go_mod_deps
        content = (
            "module example.com/myapp\n"
            "go 1.21\n"
            "require github.com/spf13/cobra v1.5.0\n"
        )
        deps = _parse_go_mod_deps(content)
        assert "github.com/spf13/cobra" in deps

    def test_parse_pom_xml_skips_comment(self) -> None:
        from hypergumbo_core.profile import _parse_pom_xml_deps
        content = (
            "<project>\n"
            "  <!-- <dependency><groupId>commented</groupId>"
            "<artifactId>out</artifactId></dependency> -->\n"
            "  <dependencies>\n"
            "    <dependency>\n"
            "      <groupId>com.example</groupId>\n"
            "      <artifactId>real-dep</artifactId>\n"
            "    </dependency>\n"
            "  </dependencies>\n"
            "</project>\n"
        )
        deps = _parse_pom_xml_deps(content)
        assert "com.example" in deps
        assert "real-dep" in deps
        assert "com.example:real-dep" in deps
        assert "commented" not in deps
        assert "out" not in deps

    def test_parse_gradle_deps_extracts_plugin_ids(self) -> None:
        from hypergumbo_core.profile import _parse_gradle_deps
        content = (
            "plugins {\n"
            "    id 'java'\n"
            '    id("com.android.application") version "8.0.0"\n'
            "}\n"
        )
        deps = _parse_gradle_deps(content)
        assert "java" in deps
        assert "com.android.application" in deps

    def test_parse_gradle_deps_extracts_maven_coord_in_helper_map(self) -> None:
        from hypergumbo_core.profile import _parse_gradle_deps
        content = (
            "ext {\n"
            '    libs = [foo: "io.example.lib:my-lib:1.0.0"]\n'
            "}\n"
        )
        deps = _parse_gradle_deps(content)
        assert "io.example.lib" in deps
        assert "my-lib" in deps
        assert "io.example.lib:my-lib" in deps

    def test_parse_gradle_deps_skips_single_token_implementation(self) -> None:
        from hypergumbo_core.profile import _parse_gradle_deps
        # A configuration with no coord (e.g., `implementation(project(":a"))`)
        # leaves an empty quoted match -- the parser must skip it cleanly.
        content = (
            "dependencies {\n"
            '    implementation ""\n'
            '    implementation "foo"\n'
            "}\n"
        )
        deps = _parse_gradle_deps(content)
        assert "foo" in deps

    def test_strip_cstyle_comments_respects_single_quoted(self) -> None:
        from hypergumbo_core.profile import _strip_cstyle_comments
        # '//' inside single-quoted string must survive.
        text = "let x = 'http://example.com'  // tail"
        stripped = _strip_cstyle_comments(text)
        assert "http://example.com" in stripped
        assert "// tail" not in stripped

    def test_strip_cstyle_comments_respects_block_comment(self) -> None:
        from hypergumbo_core.profile import _strip_cstyle_comments
        text = "before /* commented */ after"
        stripped = _strip_cstyle_comments(text)
        assert "commented" not in stripped
        assert "before" in stripped
        assert "after" in stripped

    def test_parse_sbt_deps_plugin(self) -> None:
        from hypergumbo_core.profile import _parse_sbt_deps
        content = 'addSbtPlugin("com.typesafe.play" % "sbt-plugin" % "2.8.0")\n'
        deps = _parse_sbt_deps(content)
        assert "com.typesafe.play" in deps
        assert "sbt-plugin" in deps

    def test_parse_description_imports_field(self) -> None:
        from hypergumbo_core.profile import _parse_description_deps
        content = (
            "Package: myapp\n"
            "Imports:\n"
            "    shiny (>= 1.0),\n"
            "    plumber\n"
            "Depends:\n"
            "    R (>= 4.0)\n"
        )
        deps = _parse_description_deps(content)
        assert "shiny" in deps
        assert "plumber" in deps
        # "R" itself is filtered.
        assert "r" not in deps

    def test_parse_pubspec_yaml_in_section(self) -> None:
        from hypergumbo_core.profile import _parse_pubspec_yaml_deps
        content = (
            "name: myapp\n"
            "dependencies:\n"
            "  shelf: ^1.0.0\n"
            "  serverpod: ^1.0.0\n"
            "dev_dependencies:\n"
            "  test: ^1.0.0\n"
        )
        deps = _parse_pubspec_yaml_deps(content)
        assert "shelf" in deps
        assert "serverpod" in deps
        assert "test" in deps

    def test_parse_msbuild_proj_reference_attribute(self) -> None:
        from hypergumbo_core.profile import _parse_msbuild_proj_deps
        content = (
            "<Project>\n"
            '  <ItemGroup>\n'
            '    <Reference Include="System.Web, Version=4.0.0.0, '
            'Culture=neutral" />\n'
            '    <PackageReference Include="Newtonsoft.Json" Version="13.0.0" />\n'
            "  </ItemGroup>\n"
            "</Project>\n"
        )
        deps = _parse_msbuild_proj_deps(content)
        assert "system.web" in deps
        assert "newtonsoft.json" in deps

    def test_parse_cabal_build_depends(self) -> None:
        from hypergumbo_core.profile import _parse_cabal_deps
        content = (
            "name:           myapp\n"
            "library\n"
            "  build-depends: base >= 4.7 && < 5,\n"
            "                 servant,\n"
            "                 scotty (>= 0.12)\n"
        )
        deps = _parse_cabal_deps(content)
        assert "servant" in deps
        assert "scotty" in deps

    def test_parse_haskell_yaml_deps(self) -> None:
        from hypergumbo_core.profile import _parse_haskell_yaml_deps
        content = (
            "name: myapp\n"
            "dependencies:\n"
            "  - base >= 4.7\n"
            "  - scotty\n"
            "  - servant-server-1.2.3@sha256:abc\n"
        )
        deps = _parse_haskell_yaml_deps(content)
        assert "scotty" in deps
        assert "servant-server-1.2.3" in deps

    def test_parse_clojure_deps_edn(self) -> None:
        from hypergumbo_core.profile import _parse_clojure_deps_edn
        content = (
            "{:deps {compojure/compojure {:mvn/version \"1.7.0\"}\n"
            "        ring/ring-core {:mvn/version \"1.9.0\"}}}\n"
        )
        deps = _parse_clojure_deps_edn(content)
        assert "compojure/compojure" in deps
        assert "compojure" in deps
        assert "ring/ring-core" in deps
        assert "ring" in deps

    def test_parse_clojure_project_clj(self) -> None:
        from hypergumbo_core.profile import _parse_clojure_project_clj
        content = (
            '(defproject myapp "0.1.0"\n'
            '  :dependencies [[compojure "1.7.0"]\n'
            '                 [ring/ring-core "1.9.0"]])\n'
        )
        deps = _parse_clojure_project_clj(content)
        assert "compojure" in deps
        assert "ring/ring-core" in deps
        assert "ring" in deps
        assert "ring-core" in deps

    def test_parse_rockspec_deps(self) -> None:
        from hypergumbo_core.profile import _parse_rockspec_deps
        content = (
            'package = "myapp"\n'
            "dependencies = {\n"
            '    "lua >= 5.1",\n'
            '    "lapis >= 1.10",\n'
            "}\n"
        )
        deps = _parse_rockspec_deps(content)
        assert "lapis" in deps

    def test_parse_cmake_find_package(self) -> None:
        from hypergumbo_core.profile import _parse_cmake_deps
        content = (
            "cmake_minimum_required(VERSION 3.10)\n"
            "# Qt6 was considered\n"
            "find_package(Qt5 REQUIRED COMPONENTS Core Widgets)\n"
        )
        deps = _parse_cmake_deps(content)
        assert "qt5" in deps
        assert "qt6" not in deps  # filtered as comment

    def test_parse_qmake_qt_modules(self) -> None:
        from hypergumbo_core.profile import _parse_qmake_deps
        content = "QT += core widgets sql\n# QT += notthis\n"
        deps = _parse_qmake_deps(content)
        assert "qtcore" in deps
        assert "qtwidgets" in deps
        assert "core" in deps
        assert "qtnotthis" not in deps

    def test_parse_vcpkg_deps(self) -> None:
        from hypergumbo_core.profile import _parse_vcpkg_deps
        content = (
            '{"name": "myapp", "dependencies": ["fmt", '
            '{"name": "qt5-base", "version>=": "5.15"}]}'
        )
        deps = _parse_vcpkg_deps(content)
        assert "fmt" in deps
        assert "qt5-base" in deps
        # Malformed JSON yields empty set.
        from hypergumbo_core.profile import _parse_vcpkg_deps as fn
        assert fn("not json") == set()

    def test_parse_rebar_config(self) -> None:
        from hypergumbo_core.profile import _parse_rebar_config_deps
        content = (
            "{deps, [\n"
            "    {cowboy, \"2.10.0\"},\n"
            "    {jsx, \"3.1.0\"}\n"
            "]}.\n"
        )
        deps = _parse_rebar_config_deps(content)
        assert "cowboy" in deps
        assert "jsx" in deps

    def test_parse_erlangmk_deps(self) -> None:
        from hypergumbo_core.profile import _parse_erlangmk_deps
        content = "DEPS = cowboy ranch\n"
        deps = _parse_erlangmk_deps(content)
        assert "cowboy" in deps
        assert "ranch" in deps

    def test_parse_nimble_requires(self) -> None:
        from hypergumbo_core.profile import _parse_nimble_deps
        content = (
            'version = "0.1.0"\n'
            'requires "jester >= 0.5.0"\n'
            'requires "prologue"\n'
            '# requires "skipthis"\n'
        )
        deps = _parse_nimble_deps(content)
        assert "jester" in deps
        assert "prologue" in deps
        assert "skipthis" not in deps

    def test_parse_zig_zon_deps(self) -> None:
        from hypergumbo_core.profile import _parse_zig_zon_deps
        content = (
            ".{\n"
            '    .name = "myapp",\n'
            "    .dependencies = .{\n"
            '        .zap = .{ .url = "https://example.com/zap.tar.gz" },\n'
            "    },\n"
            "}\n"
        )
        deps = _parse_zig_zon_deps(content)
        assert "zap" in deps

    def test_parse_zig_build_dependency_calls(self) -> None:
        from hypergumbo_core.profile import _parse_zig_build_deps
        content = (
            'const zap = b.dependency("zap", .{});\n'
            "// b.dependency(\"commented\", .{})\n"
        )
        deps = _parse_zig_build_deps(content)
        assert "zap" in deps
        assert "commented" not in deps

    def test_parse_dub_json_deps(self) -> None:
        from hypergumbo_core.profile import _parse_dub_json_deps
        content = '{"name": "myapp", "dependencies": {"vibe-d": "~>0.9.0"}}'
        deps = _parse_dub_json_deps(content)
        assert "vibe-d" in deps
        assert _parse_dub_json_deps("not json") == set()

    def test_parse_dub_sdl_deps(self) -> None:
        from hypergumbo_core.profile import _parse_dub_sdl_deps
        content = 'name "myapp"\ndependency "vibe-d" version="~>0.9.0"\n'
        deps = _parse_dub_sdl_deps(content)
        assert "vibe-d" in deps

    def test_parse_dune_project_depends(self) -> None:
        from hypergumbo_core.profile import _parse_dune_project_deps
        content = (
            "(lang dune 3.0)\n"
            "(name myapp)\n"
            "(package (depends cohttp-lwt-unix dream))\n"
        )
        deps = _parse_dune_project_deps(content)
        assert "cohttp-lwt-unix" in deps
        assert "dream" in deps

    def test_parse_opam_depends(self) -> None:
        from hypergumbo_core.profile import _parse_opam_deps
        content = (
            'opam-version: "2.0"\n'
            'depends: [\n'
            '    "ocaml" {>= "4.14"}\n'
            '    "dream"\n'
            ']\n'
        )
        deps = _parse_opam_deps(content)
        assert "dream" in deps
        assert "ocaml" in deps

    def test_pattern_matches_deps_module_path_suffix(self) -> None:
        """``github.com/labstack/echo`` matches ``github.com/labstack/echo/v4``."""
        from hypergumbo_core.profile import _pattern_matches_deps
        assert _pattern_matches_deps(
            "github.com/labstack/echo", {"github.com/labstack/echo/v4"}
        )

    def test_is_dsl_marker_recognizes_braces(self) -> None:
        from hypergumbo_core.profile import _is_dsl_marker
        assert _is_dsl_marker("android {")
        assert _is_dsl_marker("qt +=")
        assert not _is_dsl_marker("org.springframework.boot")

    def test_parse_gradle_deps_skips_whitespace_only_coord(self) -> None:
        from hypergumbo_core.profile import _parse_gradle_deps
        # ``[^'"]+`` matches the whitespace-only string, but ``coord.strip()``
        # is empty -- the parser must skip it cleanly.
        content = 'dependencies {\n    implementation " "\n}\n'
        deps = _parse_gradle_deps(content)
        assert deps == set()

    def test_parse_mix_exs_strips_hash_comment(self) -> None:
        from hypergumbo_core.profile import _parse_mix_exs_deps
        content = (
            "defp deps do\n"
            "  # {:notthis, \"~> 1.0\"}\n"
            "  [{:phoenix, \"~> 1.7\"}]\n"
            "end\n"
        )
        deps = _parse_mix_exs_deps(content)
        assert "phoenix" in deps
        assert "notthis" not in deps

    def test_parse_project_toml_malformed_returns_empty(self) -> None:
        from hypergumbo_core.profile import _parse_project_toml_deps
        assert _parse_project_toml_deps("[[[ not toml") == set()

    def test_pattern_matches_deps_coordinate_prefix(self) -> None:
        """``"org.springframework.boot"`` matches ``"org.springframework.boot:starter"``."""
        from hypergumbo_core.profile import _pattern_matches_deps
        assert _pattern_matches_deps(
            "org.springframework.boot",
            {"org.springframework.boot:spring-boot-starter"},
        )

    def test_detect_java_skips_already_detected_framework(
        self, tmp_path: Path
    ) -> None:
        """When the same framework appears in pom.xml AND build.gradle,
        the second loop branch hits ``if framework in detected_set: continue``."""
        from hypergumbo_core.profile import _detect_java_frameworks
        (tmp_path / "pom.xml").write_text(
            "<project><dependencies>\n"
            "<dependency><groupId>io.micronaut</groupId>"
            "<artifactId>micronaut-http</artifactId></dependency>\n"
            "</dependencies></project>\n"
        )
        (tmp_path / "build.gradle").write_text(
            "dependencies {\n"
            '    implementation "io.micronaut:micronaut-http:1.0"\n'
            "}\n"
        )
        detected = _detect_java_frameworks(tmp_path)
        assert detected.count("micronaut") == 1

    def test_detect_dart_empty_pubspec_continues(
        self, tmp_path: Path
    ) -> None:
        """A zero-byte pubspec.yaml triggers the ``if not text: continue`` branch."""
        from hypergumbo_core.profile import _detect_dart_frameworks
        (tmp_path / "pubspec.yaml").write_text("")
        # Should produce no detection and not crash.
        assert _detect_dart_frameworks(tmp_path) == []

    def test_detect_dart_already_detected_skips_repeat(
        self, tmp_path: Path
    ) -> None:
        """When two pubspec.yaml files both declare a Flutter framework, the
        second hits the ``if framework in detected_set: continue`` branch."""
        from hypergumbo_core.profile import _detect_dart_frameworks
        (tmp_path / "pubspec.yaml").write_text(
            "name: a\n"
            "dependencies:\n"
            "  flutter_bloc: ^8.0.0\n"
        )
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "pubspec.yaml").write_text(
            "name: b\n"
            "dependencies:\n"
            "  flutter_bloc: ^8.0.0\n"
        )
        detected = _detect_dart_frameworks(tmp_path)
        assert detected.count("flutter_bloc") == 1

    def test_parse_haskell_yaml_skips_blank_and_commented_lines(self) -> None:
        """Blank lines and ``- # commented`` entries are skipped."""
        from hypergumbo_core.profile import _parse_haskell_yaml_deps
        content = (
            "name: myapp\n"
            "\n"  # blank line
            "dependencies:\n"
            "  # comment-only line above\n"
            "  - servant\n"
            "  - # commented entry value\n"
        )
        deps = _parse_haskell_yaml_deps(content)
        assert "servant" in deps

    def test_parse_vcpkg_deps_non_dict_returns_empty(self) -> None:
        """A JSON array at top level is not a vcpkg manifest."""
        from hypergumbo_core.profile import _parse_vcpkg_deps
        assert _parse_vcpkg_deps('["just", "an", "array"]') == set()

    def test_parse_dub_json_deps_non_dict_returns_empty(self) -> None:
        from hypergumbo_core.profile import _parse_dub_json_deps
        assert _parse_dub_json_deps('"just-a-string"') == set()
