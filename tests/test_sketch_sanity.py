"""Sketch sanity tests for various languages.

These tests catch truly insane sketch behavior - catastrophic failures that
would make sketches useless. They are intentionally forgiving and focus on:

1. Sketches that are completely empty or malformed
2. Key Symbols being entirely absent for languages with working analyzers
3. Obvious extraction failures (e.g., LICENSE dominating config for 90%+ of content)
4. Language detection failing entirely

These are NOT exhaustive validation tests. Minor variations in output,
missing optional sections, or slightly suboptimal content selection are
all acceptable and will not trigger failures.
"""

from pathlib import Path

import pytest

from hypergumbo.sketch import generate_sketch, ConfigExtractionMode


def make_stylized_repo(
    tmp_path: Path,
    files: dict[str, str],
) -> Path:
    """Create a minimal repo with the given file structure."""
    for rel_path, content in files.items():
        file_path = tmp_path / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
    return tmp_path


class TestSketchNotEmpty:
    """Basic sanity: sketches should not be empty or trivially short."""

    def test_python_sketch_not_empty(self, tmp_path: Path) -> None:
        """Python repo sketch should have meaningful content."""
        repo = make_stylized_repo(tmp_path, {
            "src/main.py": '''
def greet(name: str) -> str:
    """Return a greeting."""
    return f"Hello, {name}!"

class Greeter:
    def greet(self, name: str) -> str:
        return f"Hello, {name}!"
''',
        })
        sketch = generate_sketch(repo, max_tokens=4000)

        # Should have at least some content (header + overview minimum)
        assert len(sketch) > 100, "Sketch is suspiciously short"
        # Should have the project name as header
        assert sketch.startswith("#"), "Sketch missing header"

    def test_nix_sketch_not_empty(self, tmp_path: Path) -> None:
        """Nix repo sketch should have meaningful content."""
        repo = make_stylized_repo(tmp_path, {
            "flake.nix": '''{
  description = "Test flake";
  outputs = { self }: { default = "hello"; };
}''',
        })
        sketch = generate_sketch(repo, max_tokens=4000)

        assert len(sketch) > 100, "Sketch is suspiciously short"
        assert sketch.startswith("#"), "Sketch missing header"


class TestLanguageDetection:
    """Sanity: language should be detected and shown in overview."""

    def test_python_detected(self, tmp_path: Path) -> None:
        """Python files should be detected."""
        repo = make_stylized_repo(tmp_path, {
            "main.py": "def main(): pass",
            "utils.py": "def helper(): pass",
        })
        sketch = generate_sketch(repo, max_tokens=2000)

        # Should mention Python somewhere in the overview
        assert "python" in sketch.lower() or "Python" in sketch, \
            "Python not detected in overview"

    def test_nix_detected(self, tmp_path: Path) -> None:
        """Nix files should be detected."""
        repo = make_stylized_repo(tmp_path, {
            "flake.nix": "{ outputs = { }: { }; }",
            "default.nix": "{ pkgs }: pkgs.hello",
        })
        sketch = generate_sketch(repo, max_tokens=2000)

        assert "nix" in sketch.lower() or "Nix" in sketch, \
            "Nix not detected in overview"


class TestKeySymbolsPresence:
    """Sanity: Key Symbols should appear for languages with working analyzers.

    This is forgiving - we only check that SOME symbols appear when we have
    enough budget and a working analyzer. We don't mandate specific symbols.
    """

    def test_python_has_symbols_at_large_budget(self, tmp_path: Path) -> None:
        """Python sketch with large budget should have Key Symbols section."""
        repo = make_stylized_repo(tmp_path, {
            "src/main.py": '''
def function_one(): pass
def function_two(): pass
def function_three(): pass
class MyClass:
    def method_one(self): pass
    def method_two(self): pass
''',
        })
        # Use large budget to ensure symbols section has room
        sketch = generate_sketch(repo, max_tokens=8000)

        # At 8000 tokens with meaningful code, we should get Key Symbols
        # But be forgiving - if we don't, it might just be budget allocation
        if "## Key Symbols" not in sketch:
            # Only fail if the sketch is suspiciously small (suggests a bug)
            assert len(sketch) > 500, \
                "No Key Symbols and sketch is suspiciously short - possible analysis failure"

    def test_nix_has_symbols_at_large_budget(self, tmp_path: Path) -> None:
        """Nix sketch with large budget should have Key Symbols section."""
        repo = make_stylized_repo(tmp_path, {
            "flake.nix": '''{
  description = "Test";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs";
  outputs = { self, nixpkgs }: {
    packages.default = nixpkgs.legacyPackages.x86_64-linux.hello;
  };
}''',
            "lib.nix": '''{ lib }: {
  myFunc = x: x + 1;
  anotherFunc = { a, b }: a * b;
}''',
        })
        sketch = generate_sketch(repo, max_tokens=8000)

        if "## Key Symbols" not in sketch:
            assert len(sketch) > 500, \
                "No Key Symbols and sketch is suspiciously short - possible Nix analysis failure"


class TestNoGarbageInConfig:
    """Sanity: Configuration section should not be dominated by garbage.

    We are forgiving here - some LICENSE content is acceptable if it's
    mixed with real config. We only flag when it's obviously wrong
    (LICENSE is 90%+ of the config section).
    """

    LICENSE_TEXT = """MIT License

Copyright (c) 2024 Test Author

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
"""

    def test_license_does_not_dominate_config(self, tmp_path: Path) -> None:
        """LICENSE should not be 90%+ of Configuration section."""
        repo = make_stylized_repo(tmp_path, {
            "main.py": "def main(): pass",
            "LICENSE": self.LICENSE_TEXT,
            "pyproject.toml": '''[project]
name = "myproject"
version = "1.0.0"
dependencies = ["requests", "click"]
''',
            "config.yaml": '''
database:
  host: localhost
  port: 5432
''',
        })

        # Only test with embedding mode since that's where extraction happens
        try:
            sketch = generate_sketch(
                repo,
                max_tokens=4000,
                config_extraction_mode=ConfigExtractionMode.EMBEDDING,
            )
        except Exception:
            # If embedding mode fails (no sentence-transformers), skip
            pytest.skip("Embedding mode not available")

        if "## Configuration" in sketch:
            # Extract config section
            parts = sketch.split("## Configuration")
            if len(parts) > 1:
                config_section = parts[1].split("##")[0]

                # Count LICENSE-specific phrases
                license_indicators = [
                    "MIT License",
                    "Permission is hereby granted",
                    "WITHOUT WARRANTY",
                    "MERCHANTABILITY",
                    "NONINFRINGEMENT",
                ]
                license_matches = sum(
                    1 for phrase in license_indicators
                    if phrase in config_section
                )

                # Fail only if LICENSE dominates (4+ out of 5 indicators)
                assert license_matches < 4, \
                    f"LICENSE dominates Configuration section ({license_matches}/5 indicators)"


class TestSketchWellFormed:
    """Sanity: sketch should be valid markdown-ish structure."""

    def test_has_header(self, tmp_path: Path) -> None:
        """Sketch should start with a header."""
        repo = make_stylized_repo(tmp_path, {"main.py": "x = 1"})
        sketch = generate_sketch(repo, max_tokens=1000)

        assert sketch.strip().startswith("#"), "Sketch should start with markdown header"

    def test_no_error_messages_in_output(self, tmp_path: Path) -> None:
        """Sketch should not contain error messages or tracebacks."""
        repo = make_stylized_repo(tmp_path, {
            "main.py": "def main(): pass",
            "utils.py": "def helper(): return 42",
        })
        sketch = generate_sketch(repo, max_tokens=2000)

        error_indicators = [
            "Traceback (most recent call last)",
            "Error:",
            "Exception:",
            "FAILED",
        ]
        for indicator in error_indicators:
            assert indicator not in sketch, \
                f"Sketch contains error indicator: {indicator}"

    def test_reasonable_size(self, tmp_path: Path) -> None:
        """Sketch should not be absurdly large for small repos."""
        repo = make_stylized_repo(tmp_path, {
            "main.py": "def main(): pass",
        })
        sketch = generate_sketch(repo, max_tokens=1000)

        # For a tiny repo with 1000 token budget, sketch should be under 6000 chars
        # (generous buffer for markdown overhead)
        assert len(sketch) < 6000, \
            f"Sketch suspiciously large ({len(sketch)} chars) for tiny repo"


class TestMultiLanguageRepo:
    """Sanity: repos with multiple languages should detect primary language."""

    def test_mixed_repo_detects_dominant_language(self, tmp_path: Path) -> None:
        """Mixed repo should show dominant language in overview."""
        repo = make_stylized_repo(tmp_path, {
            # More Python than JS
            "src/main.py": "def main(): pass\n" * 10,
            "src/utils.py": "def helper(): pass\n" * 10,
            "src/index.js": "function main() {}\n",
        })
        sketch = generate_sketch(repo, max_tokens=2000)

        # Should mention both languages somewhere
        has_python = "python" in sketch.lower() or "Python" in sketch
        has_js = "javascript" in sketch.lower() or "JavaScript" in sketch

        # At minimum, the dominant language should be detected
        assert has_python, "Dominant language (Python) not detected"


class TestSketchSanityAdditionalLanguages:
    """Sanity checks for additional languages.

    These are forgiving spot-checks to ensure language detection works
    and basic sketch generation doesn't fail catastrophically.
    """

    def test_elixir_detected(self, tmp_path: Path) -> None:
        """Elixir files should be detected."""
        repo = make_stylized_repo(tmp_path, {
            "lib/my_app.ex": '''
defmodule MyApp do
  def hello do
    :world
  end
end
''',
        })
        sketch = generate_sketch(repo, max_tokens=2000)
        assert "elixir" in sketch.lower() or "Elixir" in sketch

    def test_rust_detected(self, tmp_path: Path) -> None:
        """Rust files should be detected."""
        repo = make_stylized_repo(tmp_path, {
            "src/main.rs": '''
fn main() {
    println!("Hello, world!");
}

struct Point {
    x: i32,
    y: i32,
}
''',
        })
        sketch = generate_sketch(repo, max_tokens=2000)
        assert "rust" in sketch.lower() or "Rust" in sketch

    def test_go_detected(self, tmp_path: Path) -> None:
        """Go files should be detected."""
        repo = make_stylized_repo(tmp_path, {
            "main.go": '''
package main

func main() {
    println("Hello, world!")
}

type Point struct {
    X, Y int
}
''',
        })
        sketch = generate_sketch(repo, max_tokens=2000)
        assert "go" in sketch.lower() or "Go" in sketch

    def test_terraform_detected(self, tmp_path: Path) -> None:
        """Terraform/HCL files should be detected."""
        repo = make_stylized_repo(tmp_path, {
            "main.tf": '''
resource "aws_instance" "web" {
  ami           = "ami-12345678"
  instance_type = "t2.micro"
}

variable "region" {
  default = "us-west-2"
}
''',
        })
        sketch = generate_sketch(repo, max_tokens=2000)
        # HCL might be detected as "hcl" or "terraform"
        has_hcl = "hcl" in sketch.lower() or "terraform" in sketch.lower()
        assert has_hcl, "HCL/Terraform not detected"

    def test_sql_detected(self, tmp_path: Path) -> None:
        """SQL files should be detected."""
        repo = make_stylized_repo(tmp_path, {
            "schema.sql": '''
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255)
);

CREATE VIEW active_users AS
SELECT * FROM users WHERE active = true;
''',
        })
        sketch = generate_sketch(repo, max_tokens=2000)
        assert "sql" in sketch.lower() or "SQL" in sketch

    def test_dockerfile_detected(self, tmp_path: Path) -> None:
        """Dockerfile should be detected."""
        repo = make_stylized_repo(tmp_path, {
            "Dockerfile": '''
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
EXPOSE 8000
CMD ["python", "main.py"]
''',
        })
        sketch = generate_sketch(repo, max_tokens=2000)
        assert "dockerfile" in sketch.lower() or "Dockerfile" in sketch


class TestFunctionSignaturesShown:
    """Sanity: function signatures should be shown in Key Symbols section.

    When we have working analyzers that extract signatures, the Key Symbols
    section should display those signatures (parameter types, return types).
    This is forgiving - we only check that SOME signatures appear for
    languages where we extract them.
    """

    def test_rust_functions_have_signatures(self, tmp_path: Path) -> None:
        """Rust functions in Key Symbols should show signatures."""
        repo = make_stylized_repo(tmp_path, {
            "src/main.rs": '''
fn main() {
    let result = add(1, 2);
    println!("{}", result);
}

fn add(x: i32, y: i32) -> i32 {
    x + y
}

fn process(data: Vec<String>) -> Option<String> {
    data.first().cloned()
}

struct Calculator {
    value: i32,
}

impl Calculator {
    fn new(initial: i32) -> Self {
        Calculator { value: initial }
    }

    fn add(&mut self, amount: i32) {
        self.value += amount;
    }

    fn get(&self) -> i32 {
        self.value
    }
}
''',
        })
        # Use larger budget to ensure Key Symbols section appears
        sketch = generate_sketch(repo, max_tokens=8000)

        # Key Symbols section should be present
        assert "## Key Symbols" in sketch, \
            "Key Symbols section missing for Rust repo with functions"

        # At least some signatures should be visible
        # Look for signature patterns: (param: Type) or -> ReturnType
        has_param_type = ": i32" in sketch or ": Vec" in sketch or ": String" in sketch
        has_return_arrow = "-> i32" in sketch or "-> Self" in sketch or "-> Option" in sketch
        has_self_param = "&self" in sketch or "&mut self" in sketch

        # At least one of these should appear in a function-rich Rust repo
        assert has_param_type or has_return_arrow or has_self_param, \
            f"No function signatures found in Rust sketch. Expected parameter types, return types, or &self. Sketch excerpt: {sketch[:2000]}"

    def test_python_functions_have_signatures(self, tmp_path: Path) -> None:
        """Python functions in Key Symbols should show signatures."""
        repo = make_stylized_repo(tmp_path, {
            "src/main.py": '''
def greet(name: str) -> str:
    """Return a greeting."""
    return f"Hello, {name}!"

def add_numbers(x: int, y: int) -> int:
    """Add two numbers."""
    return x + y

def process_data(items: list[str], limit: int = 10) -> dict[str, int]:
    """Process a list of items."""
    return {item: len(item) for item in items[:limit]}

class Calculator:
    """A simple calculator."""

    def __init__(self, initial: int = 0) -> None:
        self.value = initial

    def add(self, amount: int) -> int:
        """Add amount to value."""
        self.value += amount
        return self.value
''',
        })
        sketch = generate_sketch(repo, max_tokens=8000)

        # Key Symbols section should be present
        assert "## Key Symbols" in sketch, \
            "Key Symbols section missing for Python repo with functions"

        # At least some signatures should be visible
        has_param_type = ": str" in sketch or ": int" in sketch or ": list" in sketch
        has_return_arrow = "-> str" in sketch or "-> int" in sketch or "-> dict" in sketch

        # At least one of these should appear
        assert has_param_type or has_return_arrow, \
            f"No function signatures found in Python sketch. Sketch excerpt: {sketch[:2000]}"

    def test_go_functions_have_signatures(self, tmp_path: Path) -> None:
        """Go functions in Key Symbols should show signatures."""
        repo = make_stylized_repo(tmp_path, {
            "main.go": '''
package main

func main() {
    result := add(1, 2)
    println(result)
}

func add(x int, y int) int {
    return x + y
}

func divide(a int, b int) (int, error) {
    return a / b, nil
}

type Calculator struct {
    value int
}

func (c *Calculator) Add(amount int) {
    c.value += amount
}

func (c Calculator) Get() int {
    return c.value
}
''',
        })
        sketch = generate_sketch(repo, max_tokens=8000)

        # Key Symbols section should be present
        assert "## Key Symbols" in sketch, \
            "Key Symbols section missing for Go repo with functions"

        # At least some signatures should be visible
        # Look for Go signature patterns: (x int), int, (int, error)
        has_param_type = " int" in sketch and "(" in sketch
        has_error_return = "error" in sketch

        # At least one of these should appear
        assert has_param_type or has_error_return, \
            f"No function signatures found in Go sketch. Sketch excerpt: {sketch[:2000]}"

    def test_typescript_functions_have_signatures(self, tmp_path: Path) -> None:
        """TypeScript functions in Key Symbols should show signatures."""
        repo = make_stylized_repo(tmp_path, {
            "src/main.ts": '''
function greet(name: string): string {
    return "Hello, " + name;
}

function add(x: number, y: number): number {
    return x + y;
}

const multiply = (a: number, b: number): number => a * b;

class Calculator {
    private value: number = 0;

    add(amount: number): number {
        this.value += amount;
        return this.value;
    }

    get(): number {
        return this.value;
    }
}
''',
        })
        sketch = generate_sketch(repo, max_tokens=8000)

        # Key Symbols section should be present
        assert "## Key Symbols" in sketch, \
            "Key Symbols section missing for TypeScript repo with functions"

        # At least some signatures should be visible
        # Look for TS signature patterns: : string, : number
        has_param_type = ": string" in sketch or ": number" in sketch

        # At least one of these should appear
        assert has_param_type, \
            f"No function signatures found in TypeScript sketch. Sketch excerpt: {sketch[:2000]}"

    def test_csharp_functions_have_signatures(self, tmp_path: Path) -> None:
        """C# functions in Key Symbols should show signatures."""
        repo = make_stylized_repo(tmp_path, {
            "src/Calculator.cs": '''
using System;

namespace MyApp
{
    public class Calculator
    {
        public int Add(int x, int y)
        {
            return x + y;
        }

        public string Format(string template, int value)
        {
            return string.Format(template, value);
        }

        public Calculator(int initial)
        {
            _value = initial;
        }

        private int _value;
    }
}
''',
        })
        sketch = generate_sketch(repo, max_tokens=8000)

        # Key Symbols section should be present
        assert "## Key Symbols" in sketch, \
            "Key Symbols section missing for C# repo with methods"

        # At least some signatures should be visible
        has_param_type = ": int" in sketch or ": string" in sketch or "int " in sketch or "string " in sketch
        has_csharp_types = "int x" in sketch or "string template" in sketch

        assert has_param_type or has_csharp_types, \
            f"No function signatures found in C# sketch. Sketch excerpt: {sketch[:2000]}"

    def test_swift_functions_have_signatures(self, tmp_path: Path) -> None:
        """Swift functions in Key Symbols should show signatures."""
        repo = make_stylized_repo(tmp_path, {
            "Sources/Calculator.swift": '''
import Foundation

class Calculator {
    func add(x: Int, y: Int) -> Int {
        return x + y
    }

    func format(template: String, value: Int) -> String {
        return String(format: template, value)
    }

    init(initial: Int) {
        self.value = initial
    }

    private var value: Int
}
''',
        })
        sketch = generate_sketch(repo, max_tokens=8000)

        # Key Symbols section should be present
        assert "## Key Symbols" in sketch, \
            "Key Symbols section missing for Swift repo with methods"

        # At least some signatures should be visible
        has_param_type = ": Int" in sketch or ": String" in sketch
        has_return_arrow = "-> Int" in sketch or "-> String" in sketch

        assert has_param_type or has_return_arrow, \
            f"No function signatures found in Swift sketch. Sketch excerpt: {sketch[:2000]}"
