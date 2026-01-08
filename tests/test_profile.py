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


def test_detects_pytorch_framework(tmp_path: Path) -> None:
    """Should detect PyTorch from dependencies."""
    (tmp_path / "train.py").write_text("import torch\n")
    (tmp_path / "requirements.txt").write_text("torch>=2.0\ntorchvision\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "pytorch" in data["profile"]["frameworks"]


def test_detects_tensorflow_framework(tmp_path: Path) -> None:
    """Should detect TensorFlow from dependencies."""
    (tmp_path / "model.py").write_text("import tensorflow as tf\n")
    (tmp_path / "requirements.txt").write_text("tensorflow>=2.0\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "tensorflow" in data["profile"]["frameworks"]


def test_detects_transformers_framework(tmp_path: Path) -> None:
    """Should detect HuggingFace Transformers from dependencies."""
    (tmp_path / "nlp.py").write_text("from transformers import pipeline\n")
    (tmp_path / "requirements.txt").write_text("transformers>=4.0\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "transformers" in data["profile"]["frameworks"]


def test_detects_langchain_framework(tmp_path: Path) -> None:
    """Should detect LangChain from dependencies."""
    (tmp_path / "agent.py").write_text("from langchain import LLMChain\n")
    (tmp_path / "requirements.txt").write_text("langchain>=0.1\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "langchain" in data["profile"]["frameworks"]


def test_detects_scikit_learn_framework(tmp_path: Path) -> None:
    """Should detect scikit-learn from dependencies."""
    (tmp_path / "ml.py").write_text("from sklearn.linear_model import LogisticRegression\n")
    (tmp_path / "requirements.txt").write_text("scikit-learn>=1.0\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "scikit-learn" in data["profile"]["frameworks"]


def test_detects_openai_framework(tmp_path: Path) -> None:
    """Should detect OpenAI client from dependencies."""
    (tmp_path / "chat.py").write_text("from openai import OpenAI\n")
    (tmp_path / "requirements.txt").write_text("openai>=1.0\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "openai" in data["profile"]["frameworks"]


def test_detects_anthropic_framework(tmp_path: Path) -> None:
    """Should detect Anthropic client from dependencies."""
    (tmp_path / "chat.py").write_text("from anthropic import Anthropic\n")
    (tmp_path / "requirements.txt").write_text("anthropic>=0.5\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "anthropic" in data["profile"]["frameworks"]


def test_detects_llamaindex_framework(tmp_path: Path) -> None:
    """Should detect LlamaIndex from dependencies."""
    (tmp_path / "rag.py").write_text("from llama_index import VectorStoreIndex\n")
    (tmp_path / "requirements.txt").write_text("llama-index>=0.9\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "llamaindex" in data["profile"]["frameworks"]


def test_detects_mlflow_framework(tmp_path: Path) -> None:
    """Should detect MLflow from dependencies."""
    (tmp_path / "experiment.py").write_text("import mlflow\n")
    (tmp_path / "requirements.txt").write_text("mlflow>=2.0\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "arkworks" in data["profile"]["frameworks"]
    assert "groth16" in data["profile"]["frameworks"]


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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "substrate" in data["profile"]["frameworks"]


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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "risc0" in data["profile"]["frameworks"]


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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "libp2p" in data["profile"]["frameworks"]


def test_handles_unreadable_cargo_toml(tmp_path: Path) -> None:
    """Should gracefully handle unreadable Cargo.toml."""
    (tmp_path / "main.rs").write_text("fn main() {}\n")
    # Create a directory named Cargo.toml (reading it will fail)
    (tmp_path / "Cargo.toml").mkdir()

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "fiber" in data["profile"]["frameworks"]


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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "symfony" in data["profile"]["frameworks"]


def test_handles_invalid_composer_json(tmp_path: Path) -> None:
    """Should gracefully handle malformed composer.json."""
    (tmp_path / "index.php").write_text("<?php\n")
    (tmp_path / "composer.json").write_text("{ invalid json }")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "spring-boot" in data["profile"]["frameworks"]


def test_detects_kotlin_ktor_framework(tmp_path: Path) -> None:
    """Should detect Ktor framework from build.gradle.kts."""
    (tmp_path / "Main.kt").write_text("fun main() {}\n")
    (tmp_path / "build.gradle.kts").write_text("""dependencies {
    implementation("io.ktor:ktor-server-core:2.3.0")
}""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "vapor" in data["profile"]["frameworks"]


# Scala framework detection tests


def test_detects_scala_play_framework(tmp_path: Path) -> None:
    """Should detect Play Framework from build.sbt."""
    (tmp_path / "Main.scala").write_text("object Main extends App\n")
    (tmp_path / "build.sbt").write_text("""name := "myapp"
version := "1.0"

libraryDependencies += "com.typesafe.play" %% "play" % "2.9.0"
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "http4s" in data["profile"]["frameworks"]


# Dart/Flutter framework detection tests


def test_detects_dart_language(tmp_path: Path) -> None:
    """Should detect Dart files."""
    (tmp_path / "main.dart").write_text("void main() {}\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "flutter" in data["profile"]["frameworks"]
    assert "flutter_bloc" in data["profile"]["frameworks"]


def test_handles_unreadable_pubspec(tmp_path: Path) -> None:
    """Should gracefully handle unreadable pubspec.yaml."""
    (tmp_path / "main.dart").write_text("void main() {}\n")
    # Create a directory named pubspec.yaml (reading it will fail)
    (tmp_path / "pubspec.yaml").mkdir()

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "react-native" in data["profile"]["frameworks"]
    assert "react" in data["profile"]["frameworks"]


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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "expo" in data["profile"]["frameworks"]


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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())
    assert "ethers" in data["profile"]["frameworks"]


def test_extra_excludes_filters_files(tmp_path: Path) -> None:
    """Extra excludes should filter out files from language detection."""
    from hypergumbo.profile import detect_profile

    # Create Python files
    (tmp_path / "app.py").write_text("def main():\n    pass\n")
    (tmp_path / "generated.py").write_text("def generated():\n    pass\n")

    # Without extra excludes - should see 2 Python files
    profile = detect_profile(tmp_path)
    assert profile.languages.get("python", {}).files == 2

    # With extra excludes - should exclude generated.py
    profile = detect_profile(tmp_path, extra_excludes=["generated.py"])
    assert profile.languages.get("python", {}).files == 1
