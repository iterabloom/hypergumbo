# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/manifest_targets.py
"""A build target's id carries its own language in the language slot.

``manifest_targets`` mints one ``binary`` node per executable a manifest
declares, at the manifest's line, and stamps ``Symbol.language`` with the
language of the target it builds (WI-kunut: ``java`` for a Gradle
``mainClass``, ``haskell`` for a cabal ``executable``). Its id's language slot
was the literal ``manifest``, which is no language on the axis: ADR-0034 has a
producer pass the symbol's language to the id factory, and ADR-0036 Ruling 2
binds the slot to ``discovery_language or language``. The node is a declared
target with a language, so the slot is that language.

The stable id never hashed the slot (``pass_minted_stable_id`` hashes
``Symbol.language``), so it does not move; the node id does, and the
``defines_target`` edge leaves from the renamed node.

One fixture holds every manifest format the pass reads, so a format added
later is reached by the same assertions.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.catalog import all_known_languages
from hypergumbo_core.overload_arity import pass_minted_stable_id
from hypergumbo_lang_mainstream.manifest_targets import (
    PASS_ID,
    _analyze_manifest_targets,
)

#: manifest file -> (content, the target language WI-kunut stamps)
_MANIFESTS: dict[str, tuple[str, str]] = {
    "g/build.gradle": ('mainClassName = "com.example.Main"\n', "java"),
    "c/App.csproj": (
        "<Project><PropertyGroup><StartupObject>App.Program</StartupObject>"
        "</PropertyGroup></Project>\n", "csharp"),
    "d/pubspec.yaml": ("name: p\nexecutables:\n  tool: tool_main\n", "dart"),
    "s/Package.swift": ('.executableTarget(name: "Demo")\n', "swift"),
    "h/demo.cabal": (
        "name: demo\n\nexecutable greet\n  main-is: Main.hs\n"
        "  hs-source-dirs: app\n", "haskell"),
    "e/mix.exs": ("[escript: [main_module: MyApp.CLI]]\n", "elixir"),
    "r/x.gemspec": ('  s.executables = ["mygem"]\n', "ruby"),
    "sb/build.sbt": ('mainClass := Some("com.example.Main")\n', "scala"),
    "o/dune": ("(executable (name main))\n", "ocaml"),
    "z/build.zig": ('const exe = b.addExecutable("app", "src/main.zig");\n', "zig"),
    "n/app.nimble": ('bin = @["app"]\n', "nim"),
}


def _analyze(tmp_path: Path):
    for rel, (body, _) in _MANIFESTS.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return _analyze_manifest_targets(tmp_path)


def test_every_format_reaches_a_binary(tmp_path: Path) -> None:
    result = _analyze(tmp_path)
    assert {s.path for s in result.symbols} == set(_MANIFESTS)
    assert all(s.kind == "binary" for s in result.symbols)


def test_language_slot_is_the_symbols_language(tmp_path: Path) -> None:
    result = _analyze(tmp_path)
    known = all_known_languages()
    for sym in result.symbols:
        slot = sym.id.split(":", 1)[0]
        assert slot == sym.language, sym.id
        assert slot in known, sym.id
        assert sym.id.startswith(f"{sym.language}:{sym.path}:"), sym.id


def test_symbol_language_and_stable_id_are_unchanged(tmp_path: Path) -> None:
    """Control: the target language (WI-kunut) and the stable id stay."""
    result = _analyze(tmp_path)
    for sym in result.symbols:
        assert sym.language == _MANIFESTS[sym.path][1], sym.path
        assert sym.stable_id == pass_minted_stable_id(
            owner=PASS_ID, kind="binary", lang=sym.language, path=sym.path,
            name=sym.name, scope=(sym.meta["target_path"],),
        )


def test_defines_target_edge_leaves_the_renamed_node(tmp_path: Path) -> None:
    result = _analyze(tmp_path)
    ids = {s.id for s in result.symbols}
    edges = [e for e in result.edges if e.edge_type == "defines_target"]
    assert len(edges) == len(result.symbols)
    assert {e.src for e in edges} == ids
