# SPDX-License-Identifier: AGPL-3.0-or-later
"""Recognising the files a coding-agent tool loads as instructions.

Three properties, each with its edge decisions:

- the shipped catalogue is well-formed, keyed by tool, and recognises the
  files the owner ruling names (AGENTS.md, CLAUDE.md, GEMINI.md,
  .github/copilot-instructions.md, .cursorrules, .cursor/rules/*), at the
  depth each tool reads them and nowhere else; a malformed catalogue is
  refused naming the file;
- ``@path`` imports are followed transitively from every instruction file,
  outside code, and the decided edges hold: a cycle ends, a missing target,
  a directory and a target outside the repository are not listed, an
  absolute path inside the repository is;
- the walk skips what every walker skips (``.git``) and what the VCS
  ignores, and the listing line names its files with their reasons.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from hypergumbo_core.agent_instructions import (
    CATALOGUE_PATH,
    LISTED_FILES,
    InstructionFile,
    find_instruction_files,
    format_instruction_files_disclosure,
    import_references,
    load_catalogue,
    resolve_import,
    tools_reading,
)
from hypergumbo_core.yaml_catalogs import YAML_CATALOGS, CatalogueError


def _write(path: Path, text: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestTheCatalogue:
    def test_it_is_a_registered_family(self) -> None:
        spec = next(s for s in YAML_CATALOGS if s.directory == "agent_instructions")
        assert spec.loader == "hypergumbo_core.agent_instructions"
        assert CATALOGUE_PATH.parent.name == spec.directory

    def test_every_row_names_a_tool_and_its_files(self) -> None:
        rows = load_catalogue()
        assert len({row.tool for row in rows}) == len(rows)
        assert all(row.tool and row.patterns for row in rows)

    @pytest.mark.parametrize("path, tool", [
        ("AGENTS.md", "OpenAI Codex"),
        ("CLAUDE.md", "Claude Code"),
        ("GEMINI.md", "Gemini CLI"),
        (".github/copilot-instructions.md", "GitHub Copilot"),
        (".cursorrules", "Cursor"),
        (".cursor/rules/style.mdc", "Cursor"),
        (".cursor/rules/nested/deeper.mdc", "Cursor"),
        ("pkg/.cursor/rules/local.mdc", "Cursor"),
        ("packages/core/CLAUDE.md", "Claude Code"),
        ("src/AGENTS.md", "OpenAI Codex"),
        (".github/instructions/TESTS.instructions.md", "GitHub Copilot"),
        (".claude/commands/review.md", "Claude Code"),
    ])
    def test_the_ruling_s_files_are_recognised(self, path: str, tool: str) -> None:
        assert tool in tools_reading(path)

    @pytest.mark.parametrize("path", [
        "README.md",
        "claude.md",                       # the tools' lookups are case-sensitive
        "docs/AGENTS.md.txt",
        "src/.cursorrules",                # a root-only file, not at the root
        ".github/other.md",
        ".github/copilot-instructions.md/x",
        "CONVENTIONS.md",                  # Aider reads it only when told to
    ])
    def test_other_files_are_not(self, path: str) -> None:
        assert tools_reading(path) == ()

    def test_a_file_several_tools_read_names_each(self) -> None:
        tools = tools_reading("AGENTS.md")
        assert {"OpenAI Codex", "GitHub Copilot", "Cursor", "Amp"} <= set(tools)


class TestAMalformedCatalogueIsRefused:
    @pytest.mark.parametrize("text, fragment", [
        ("tools: []\n", "non-empty list"),
        ("tools: [x]\n", "must be a mapping"),
        ("tools:\n  - tool: T\n", "lists no 'files'"),
        ("tools:\n  - tool: ''\n    files: [A.md]\n", "must name the tool"),
        ("tools:\n  - tool: T\n    files: ['/abs.md']\n", "relative to the repository root"),
        ("tools:\n  - tool: T\n    files: ['a/../b.md']\n", "relative to the repository root"),
        ("tools:\n  - tool: T\n    files: ['a//b.md']\n", "relative to the repository root"),
        ("tools:\n  - tool: T\n    files: [3]\n", "non-empty string"),
        ("tools:\n  - tool: T\n    file: [A.md]\n", "unknown tools row key"),
        ("tool: T\n", "unknown top-level key"),
    ])
    def test_naming_the_file(self, tmp_path: Path, text: str, fragment: str) -> None:
        path = _write(tmp_path / "bad.yaml", text)
        with pytest.raises(CatalogueError, match=fragment) as excinfo:
            load_catalogue(path)
        assert str(path) in str(excinfo.value)


class TestImportReferences:
    def test_references_at_a_line_start_and_after_whitespace(self) -> None:
        assert import_references("@AGENTS.md\nsee @docs/a.md now") == ["AGENTS.md", "docs/a.md"]

    def test_an_email_address_is_not_one(self) -> None:
        assert import_references("mail me@example.com") == []

    def test_trailing_punctuation_is_not_part_of_the_path(self) -> None:
        assert import_references("Read @AGENTS.md. Then @b.md), @c.md;") == [
            "AGENTS.md", "b.md", "c.md"]

    def test_an_at_sign_after_punctuation_is_not_one(self) -> None:
        """Only a line start or whitespace opens a reference, the rule Claude
        Code applies; ``(@x.md)`` is not followed."""
        assert import_references("see (@x.md) and [@y.md]") == []

    def test_code_blocks_and_spans_are_skipped(self) -> None:
        text = (
            "```python\n@decorator\n```\n"
            "~~~~\n@tilde.md\n~~~\n@still_in.md\n~~~~\n"
            "use `see @inline.md` or ``x @double.md`` but @real.md\n"
        )
        assert import_references(text) == ["real.md"]

    def test_a_shorter_fence_does_not_close_a_longer_one(self) -> None:
        assert import_references("````\n```\n@inside.md\n````\n@after.md") == ["after.md"]


class TestImportsAreFollowed:
    def test_claude_md_importing_agents_md(self, tmp_path: Path) -> None:
        """The ruling's example, with AGENTS.md also read by name."""
        _write(tmp_path / "CLAUDE.md", "@AGENTS.md\n")
        _write(tmp_path / "AGENTS.md", "rules\n")
        found = find_instruction_files(tmp_path)
        assert found["AGENTS.md"].imported_by == ("CLAUDE.md",)
        assert "OpenAI Codex" in found["AGENTS.md"].tools

    def test_transitively_and_relative_to_the_importer(self, tmp_path: Path) -> None:
        _write(tmp_path / "CLAUDE.md", "@docs/a.md\n")
        _write(tmp_path / "docs/a.md", "then @b.md and @../top.md\n")
        _write(tmp_path / "docs/b.md")
        _write(tmp_path / "top.md")
        found = find_instruction_files(tmp_path)
        assert found["docs/a.md"].imported_by == ("CLAUDE.md",)
        assert found["docs/b.md"].imported_by == ("docs/a.md",)
        assert found["top.md"].imported_by == ("docs/a.md",)
        assert found["docs/b.md"].tools == ()

    def test_a_cycle_ends(self, tmp_path: Path) -> None:
        _write(tmp_path / "CLAUDE.md", "@a.md\n")
        _write(tmp_path / "a.md", "@b.md\n")
        _write(tmp_path / "b.md", "@a.md @CLAUDE.md\n")
        found = find_instruction_files(tmp_path)
        assert set(found) == {"CLAUDE.md", "a.md", "b.md"}
        assert found["a.md"].imported_by == ("CLAUDE.md", "b.md")
        assert found["CLAUDE.md"].imported_by == ("b.md",)

    def test_a_file_importing_itself_records_nothing(self, tmp_path: Path) -> None:
        _write(tmp_path / "CLAUDE.md", "@CLAUDE.md @./CLAUDE.md\n")
        assert find_instruction_files(tmp_path)["CLAUDE.md"].imported_by == ()

    def test_a_missing_target_and_a_directory_are_not_listed(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        _write(tmp_path / "CLAUDE.md", "@missing.md @docs\n")
        assert set(find_instruction_files(tmp_path)) == {"CLAUDE.md"}

    def test_a_target_outside_the_repository_is_not_listed(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        outside = _write(tmp_path / "outside.md")
        _write(repo / "CLAUDE.md", f"@../outside.md @{outside} @~/.claude/x.md @/etc/hosts\n")
        assert set(find_instruction_files(repo)) == {"CLAUDE.md"}

    def test_an_absolute_path_inside_the_repository_is(self, tmp_path: Path) -> None:
        inside = _write(tmp_path / "docs/a.md")
        _write(tmp_path / "CLAUDE.md", f"@{inside}\n")
        assert find_instruction_files(tmp_path)["docs/a.md"].imported_by == ("CLAUDE.md",)

    def test_a_symlinked_checkout_resolves_both_spellings(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        inside = _write(real / "docs/a.md")
        link = tmp_path / "link"
        link.symlink_to(real)
        _write(real / "CLAUDE.md", f"@{inside}\n")
        assert "docs/a.md" in find_instruction_files(link)

    def test_resolve_import_directly(self, tmp_path: Path) -> None:
        _write(tmp_path / "a/b.md")
        assert resolve_import(tmp_path, "a/CLAUDE.md", "b.md") == "a/b.md"
        assert resolve_import(tmp_path, "CLAUDE.md", "~/x") is None
        assert resolve_import(tmp_path, "CLAUDE.md", "..") is None
        assert resolve_import(tmp_path, "CLAUDE.md", ".") is None


class TestTheWalk:
    def test_default_excludes_are_pruned(self, tmp_path: Path) -> None:
        _write(tmp_path / ".git/CLAUDE.md")
        _write(tmp_path / "__pycache__/AGENTS.md")
        _write(tmp_path / "AGENTS.md")
        assert set(find_instruction_files(tmp_path)) == {"AGENTS.md"}

    def test_dot_directories_are_walked(self, tmp_path: Path) -> None:
        _write(tmp_path / ".github/copilot-instructions.md")
        _write(tmp_path / ".claude/agents/reviewer.md")
        assert set(find_instruction_files(tmp_path)) == {
            ".github/copilot-instructions.md", ".claude/agents/reviewer.md"}

    def test_a_file_the_vcs_ignores_is_left_out(self, tmp_path: Path) -> None:
        """``CLAUDE.local.md`` is the usual case: personal, git-ignored."""
        git = shutil.which("git")
        assert git is not None
        subprocess.run([git, "init", "-q", str(tmp_path)], check=True)
        _write(tmp_path / ".gitignore", "CLAUDE.local.md\nignored.md\n")
        _write(tmp_path / "CLAUDE.local.md")
        _write(tmp_path / "CLAUDE.md", "@ignored.md\n")
        _write(tmp_path / "ignored.md")
        assert set(find_instruction_files(tmp_path)) == {"CLAUDE.md"}

    def test_shallowest_first(self, tmp_path: Path) -> None:
        for rel in ("z/AGENTS.md", "AGENTS.md", "a/b/AGENTS.md", "GEMINI.md"):
            _write(tmp_path / rel)
        assert list(find_instruction_files(tmp_path)) == [
            "AGENTS.md", "GEMINI.md", "z/AGENTS.md", "a/b/AGENTS.md"]


class TestTheListingLine:
    def test_nothing_to_list(self) -> None:
        assert format_instruction_files_disclosure({}) is None

    def test_reasons(self) -> None:
        files = {
            "CLAUDE.md": InstructionFile("CLAUDE.md", ("Claude Code",)),
            "AGENTS.md": InstructionFile("AGENTS.md", ("OpenAI Codex", "Amp"), ("CLAUDE.md",)),
            "x.md": InstructionFile("x.md", (), ("AGENTS.md", "CLAUDE.md")),
        }
        line = format_instruction_files_disclosure(files)
        assert line == (
            "Agent instruction files, listed and not quoted because a coding-agent "
            "tool loads them as instructions: `CLAUDE.md` (Claude Code), "
            "`AGENTS.md` (OpenAI Codex, Amp; imported by `CLAUDE.md`), "
            "`x.md` (imported by `AGENTS.md`, `CLAUDE.md`)."
        )

    def test_a_long_list_is_counted(self) -> None:
        files = {
            f"d{i}/AGENTS.md": InstructionFile(f"d{i}/AGENTS.md", ("OpenAI Codex",))
            for i in range(LISTED_FILES + 3)
        }
        line = format_instruction_files_disclosure(files)
        assert line is not None
        assert line.count("`") == 2 * LISTED_FILES
        assert line.endswith(", and 3 more.")
