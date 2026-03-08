"""Tests for manifest_targets build-target extraction.

Covers all 11 manifest formats: Gradle, C#, Dart, Swift, Haskell,
Elixir, Ruby, Scala, OCaml, Zig, Nim.
"""

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.manifest_targets import (
    _analyze_manifest_targets,
    _class_to_path,
    _elixir_module_to_path,
    analyze_manifest_targets,
)


class TestHelpers:
    """Tests for shared helper functions."""

    def test_class_to_path_java(self) -> None:
        assert _class_to_path("com.example.Main", ".java") == "com/example/Main.java"

    def test_class_to_path_cs(self) -> None:
        assert _class_to_path("MyApp.Program", ".cs") == "MyApp/Program.cs"

    def test_class_to_path_scala(self) -> None:
        assert _class_to_path("com.example.Main", ".scala") == "com/example/Main.scala"

    def test_elixir_module_to_path(self) -> None:
        assert _elixir_module_to_path("MyApp.CLI") == "lib/my_app/cli.ex"

    def test_elixir_module_to_path_single(self) -> None:
        assert _elixir_module_to_path("MyApp") == "lib/my_app.ex"

    def test_elixir_module_to_path_deep(self) -> None:
        assert _elixir_module_to_path("MyApp.Web.Router") == "lib/my_app/web/router.ex"


class TestGradle:
    """Gradle build.gradle / build.gradle.kts build-target extraction."""

    def test_main_class_name_groovy(self, tmp_path: Path) -> None:
        f = tmp_path / "build.gradle"
        f.write_text('mainClassName = "com.example.Main"\n')
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "Main"
        assert result.symbols[0].kind == "binary"
        edges = [e for e in result.edges if e.edge_type == "defines_target"]
        assert len(edges) == 1
        assert edges[0].dst == "com/example/Main.java"

    def test_main_class_set_kotlin(self, tmp_path: Path) -> None:
        f = tmp_path / "build.gradle.kts"
        f.write_text("""
plugins { application }
application {
    mainClass.set("com.example.App")
}
""")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "App"
        edges = [e for e in result.edges if e.edge_type == "defines_target"]
        assert edges[0].dst == "com/example/App.java"

    def test_main_class_equals(self, tmp_path: Path) -> None:
        f = tmp_path / "build.gradle.kts"
        f.write_text('mainClass = "org.app.Server"\n')
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "Server"

    def test_no_main_class(self, tmp_path: Path) -> None:
        f = tmp_path / "build.gradle"
        f.write_text("apply plugin: 'java'\n")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 0


class TestCsproj:
    """C# .csproj StartupObject extraction."""

    def test_startup_object(self, tmp_path: Path) -> None:
        f = tmp_path / "MyApp.csproj"
        f.write_text("""<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <StartupObject>MyApp.Program</StartupObject>
  </PropertyGroup>
</Project>
""")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "Program"
        edges = [e for e in result.edges if e.edge_type == "defines_target"]
        assert edges[0].dst == "MyApp/Program.cs"

    def test_no_startup_object(self, tmp_path: Path) -> None:
        f = tmp_path / "MyApp.csproj"
        f.write_text('<Project Sdk="Microsoft.NET.Sdk"></Project>\n')
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 0


class TestDart:
    """Dart pubspec.yaml executables extraction."""

    def test_executables_with_script(self, tmp_path: Path) -> None:
        f = tmp_path / "pubspec.yaml"
        f.write_text("""name: my_pkg
executables:
  my_tool: my_tool_main
""")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "my_tool"
        edges = [e for e in result.edges if e.edge_type == "defines_target"]
        assert edges[0].dst == "bin/my_tool_main.dart"

    def test_executables_implicit_script(self, tmp_path: Path) -> None:
        f = tmp_path / "pubspec.yaml"
        f.write_text("""name: my_pkg
executables:
  my_cmd:
""")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 1
        edges = [e for e in result.edges if e.edge_type == "defines_target"]
        assert edges[0].dst == "bin/my_cmd.dart"

    def test_multiple_executables(self, tmp_path: Path) -> None:
        f = tmp_path / "pubspec.yaml"
        f.write_text("""name: tools
executables:
  tool_a: script_a
  tool_b: script_b
dependencies:
  http: ^1.0.0
""")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 2
        names = {s.name for s in result.symbols}
        assert names == {"tool_a", "tool_b"}

    def test_executables_stop_at_next_section(self, tmp_path: Path) -> None:
        """Entries after a non-indented line are ignored."""
        f = tmp_path / "pubspec.yaml"
        f.write_text("""executables:
  real_cmd: real_script
dev_dependencies:
  test: ^1.0.0
  fake_cmd: fake_script
""")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "real_cmd"

    def test_no_executables(self, tmp_path: Path) -> None:
        f = tmp_path / "pubspec.yaml"
        f.write_text("name: my_lib\nversion: 1.0.0\n")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 0


class TestSwift:
    """Swift Package.swift executableTarget extraction."""

    def test_executable_target(self, tmp_path: Path) -> None:
        f = tmp_path / "Package.swift"
        f.write_text("""
import PackageDescription
let package = Package(
    name: "MyApp",
    targets: [
        .executableTarget(name: "MyApp", dependencies: []),
    ]
)
""")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "MyApp"
        edges = [e for e in result.edges if e.edge_type == "defines_target"]
        assert edges[0].dst == "Sources/MyApp/main.swift"

    def test_multiple_targets(self, tmp_path: Path) -> None:
        f = tmp_path / "Package.swift"
        f.write_text("""
let package = Package(
    targets: [
        .executableTarget(name: "CLI"),
        .executableTarget(name: "Server"),
        .target(name: "Lib"),
    ]
)
""")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 2
        names = {s.name for s in result.symbols}
        assert names == {"CLI", "Server"}

    def test_no_executable(self, tmp_path: Path) -> None:
        f = tmp_path / "Package.swift"
        f.write_text('.target(name: "Lib")\n')
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 0


class TestHaskell:
    """Haskell .cabal executable stanza extraction."""

    def test_executable_with_src_dir(self, tmp_path: Path) -> None:
        f = tmp_path / "myapp.cabal"
        f.write_text("""name: myapp
version: 0.1.0

executable myapp
  main-is: Main.hs
  hs-source-dirs: app
  build-depends: base >= 4
""")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "myapp"
        edges = [e for e in result.edges if e.edge_type == "defines_target"]
        assert edges[0].dst == "app/Main.hs"

    def test_executable_no_src_dir(self, tmp_path: Path) -> None:
        f = tmp_path / "myapp.cabal"
        f.write_text("""executable myapp
  main-is: Main.hs
""")
        result = _analyze_manifest_targets(tmp_path)
        edges = [e for e in result.edges if e.edge_type == "defines_target"]
        assert edges[0].dst == "Main.hs"

    def test_multiple_executables(self, tmp_path: Path) -> None:
        f = tmp_path / "myapp.cabal"
        f.write_text("""executable server
  main-is: Server.hs
  hs-source-dirs: app

executable cli
  main-is: CLI.hs
  hs-source-dirs: tools
""")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 2
        targets = {e.dst for e in result.edges if e.edge_type == "defines_target"}
        assert targets == {"app/Server.hs", "tools/CLI.hs"}

    def test_no_executable(self, tmp_path: Path) -> None:
        f = tmp_path / "mylib.cabal"
        f.write_text("""name: mylib
library
  exposed-modules: MyLib
""")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 0

    def test_executable_no_main_is(self, tmp_path: Path) -> None:
        f = tmp_path / "myapp.cabal"
        f.write_text("""executable myapp
  build-depends: base
""")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 0


class TestElixir:
    """Elixir mix.exs escript extraction."""

    def test_escript_main_module(self, tmp_path: Path) -> None:
        f = tmp_path / "mix.exs"
        f.write_text("""
defmodule MyApp.MixProject do
  def project do
    [
      app: :my_app,
      escript: [main_module: MyApp.CLI]
    ]
  end
end
""")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "MyApp.CLI"
        edges = [e for e in result.edges if e.edge_type == "defines_target"]
        assert edges[0].dst == "lib/my_app/cli.ex"
        assert edges[0].meta == {"target_function": "main"}

    def test_no_escript(self, tmp_path: Path) -> None:
        f = tmp_path / "mix.exs"
        f.write_text("defmodule MyApp.MixProject do\nend\n")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 0


class TestRubyGemspec:
    """Ruby gemspec executables extraction."""

    def test_executables_array(self, tmp_path: Path) -> None:
        f = tmp_path / "mygem.gemspec"
        f.write_text("""
Gem::Specification.new do |spec|
  spec.name = "mygem"
  spec.executables = ["mygem", "mygem-helper"]
end
""")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 2
        names = {s.name for s in result.symbols}
        assert names == {"mygem", "mygem-helper"}
        targets = {e.dst for e in result.edges if e.edge_type == "defines_target"}
        assert targets == {"exe/mygem", "exe/mygem-helper"}

    def test_executables_percent_w(self, tmp_path: Path) -> None:
        f = tmp_path / "mygem.gemspec"
        f.write_text('  s.executables = %w[mygem tool]\n')
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 2
        names = {s.name for s in result.symbols}
        assert names == {"mygem", "tool"}

    def test_no_executables(self, tmp_path: Path) -> None:
        f = tmp_path / "mygem.gemspec"
        f.write_text('spec.name = "mygem"\n')
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 0


class TestScala:
    """Scala build.sbt mainClass extraction."""

    def test_main_class(self, tmp_path: Path) -> None:
        f = tmp_path / "build.sbt"
        f.write_text('mainClass := Some("com.example.Main")\n')
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "Main"
        edges = [e for e in result.edges if e.edge_type == "defines_target"]
        assert edges[0].dst == "com/example/Main.scala"

    def test_main_class_in_assembly(self, tmp_path: Path) -> None:
        f = tmp_path / "build.sbt"
        f.write_text('mainClass in assembly := Some("com.example.App")\n')
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "App"

    def test_main_class_slash_syntax(self, tmp_path: Path) -> None:
        f = tmp_path / "build.sbt"
        f.write_text('Compile / mainClass := Some("org.server.Boot")\n')
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "Boot"

    def test_no_main_class(self, tmp_path: Path) -> None:
        f = tmp_path / "build.sbt"
        f.write_text('name := "mylib"\n')
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 0


class TestOCaml:
    """OCaml dune executable extraction."""

    def test_single_executable(self, tmp_path: Path) -> None:
        f = tmp_path / "dune"
        f.write_text("(executable (name main) (public_name myapp))\n")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "main"
        edges = [e for e in result.edges if e.edge_type == "defines_target"]
        assert edges[0].dst == "main.ml"

    def test_executables_plural(self, tmp_path: Path) -> None:
        f = tmp_path / "dune"
        f.write_text("(executables (names server worker))\n")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 2
        names = {s.name for s in result.symbols}
        assert names == {"server", "worker"}
        targets = {e.dst for e in result.edges if e.edge_type == "defines_target"}
        assert targets == {"server.ml", "worker.ml"}

    def test_no_executable(self, tmp_path: Path) -> None:
        f = tmp_path / "dune"
        f.write_text("(library (name mylib))\n")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 0


class TestZig:
    """Zig build.zig addExecutable extraction."""

    def test_old_api(self, tmp_path: Path) -> None:
        f = tmp_path / "build.zig"
        f.write_text("""
const exe = b.addExecutable("myapp", "src/main.zig");
""")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "main"
        edges = [e for e in result.edges if e.edge_type == "defines_target"]
        assert edges[0].dst == "src/main.zig"

    def test_new_api(self, tmp_path: Path) -> None:
        f = tmp_path / "build.zig"
        f.write_text("""
const exe = b.addExecutable(.{
    .name = "myapp",
    .root_source_file = b.path("src/main.zig"),
});
""")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 1
        edges = [e for e in result.edges if e.edge_type == "defines_target"]
        assert edges[0].dst == "src/main.zig"

    def test_no_executable(self, tmp_path: Path) -> None:
        f = tmp_path / "build.zig"
        f.write_text("const lib = b.addStaticLibrary(.{});\n")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 0


class TestNim:
    """Nim .nimble bin field extraction."""

    def test_bin_single(self, tmp_path: Path) -> None:
        f = tmp_path / "myapp.nimble"
        f.write_text("""
version = "0.1.0"
bin = @["myapp"]
""")
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "myapp"
        edges = [e for e in result.edges if e.edge_type == "defines_target"]
        assert edges[0].dst == "src/myapp.nim"

    def test_bin_multiple(self, tmp_path: Path) -> None:
        f = tmp_path / "tools.nimble"
        f.write_text('bin = @["server", "worker"]\n')
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 2
        targets = {e.dst for e in result.edges if e.edge_type == "defines_target"}
        assert targets == {"src/server.nim", "src/worker.nim"}

    def test_no_bin(self, tmp_path: Path) -> None:
        f = tmp_path / "mylib.nimble"
        f.write_text('version = "1.0.0"\n')
        result = _analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 0


class TestAnalysisResult:
    """Tests for the AnalysisResult structure."""

    def test_result_has_run(self, tmp_path: Path) -> None:
        (tmp_path / "build.sbt").write_text('mainClass := Some("Main")\n')
        result = _analyze_manifest_targets(tmp_path)
        assert result.run is not None
        assert result.run.wall_time >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = _analyze_manifest_targets(tmp_path)
        assert result.symbols == []
        assert result.edges == []

    def test_symbol_metadata(self, tmp_path: Path) -> None:
        (tmp_path / "Package.swift").write_text(
            '.executableTarget(name: "CLI")\n'
        )
        result = _analyze_manifest_targets(tmp_path)
        sym = result.symbols[0]
        assert sym.meta["target_path"] == "Sources/CLI/main.swift"
        assert sym.origin == "manifest-targets-v1"
        assert sym.language == "swift"

    def test_registered_entry_point(self, tmp_path: Path) -> None:
        (tmp_path / "build.sbt").write_text('mainClass := Some("Main")\n')
        result = analyze_manifest_targets(tmp_path)
        assert len(result.symbols) == 1

    def test_edge_confidence(self, tmp_path: Path) -> None:
        (tmp_path / "myapp.cabal").write_text(
            "executable myapp\n  main-is: Main.hs\n"
        )
        result = _analyze_manifest_targets(tmp_path)
        edge = result.edges[0]
        assert edge.confidence == 1.0
        assert edge.edge_type == "defines_target"
