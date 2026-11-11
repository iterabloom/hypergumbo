# SPDX-License-Identifier: AGPL-3.0-or-later
"""The adjudication packet builder, and the defects it is pinned against.

``scripts/measure-taint-precision.py`` renders one packet per sampled taint
flow: the flow's facts and the source behind them, which a human adjudicator
reads to label the flow. A packet that prints an empty listing where the site
is really present shows the adjudicator evidence of absence that the
instrument manufactured, so each way the builder can do that is pinned here
as a test. The defects keep the numbers measurement 0006 §F gave them, which
the builder's own docstrings also use:

  1. sink names matched as bare SUBSTRINGS (``inFORMATion`` matching
     ``format``; ``-> `` inside a quoted pattern matching ``>``);
  2. shell ``$VAR`` matches inside COMMENTS and on locally-assigned names;
  3. literal call sites missed;
  4. sink sites searched only inside the SOURCE symbol's span, which makes
     the sink listing structurally empty for every multi-hop situation.

Defect 4 is the load-bearing one. In the ``ArkLib#3`` shape the path is
``main`` (201-255) then ``generate_dot_graph`` (110-137), and ``open`` is
called in the SECOND, so a search of the source span alone prints "(none
found in span)" for a situation whose sink is plainly there.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts/measure-taint-precision.py"
)
_spec = importlib.util.spec_from_file_location("mtp", _SCRIPT)
assert _spec and _spec.loader
mtp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mtp)


class TestScrub:
    """Defect 1 and 2: a match inside a comment or a string is not a call."""

    def test_a_hash_comment_is_blanked(self) -> None:
        assert "environ" not in mtp.scrub("# reads $environ here", "bash")

    def test_a_slash_comment_is_blanked(self) -> None:
        assert "Write" not in mtp.scrub("x := 1 // conn.Write(buf)", "go")

    def test_a_haskell_comment_is_blanked(self) -> None:
        assert "readFile" not in mtp.scrub("y = 1 -- readFile x", "haskell")

    def test_a_string_literal_is_blanked(self) -> None:
        """``-> `` inside a quoted pattern must not match the bash redirect ``>``."""
        assert ">" not in mtp.scrub('grep "a -> b" file', "bash")

    def test_code_outside_the_string_survives(self) -> None:
        """Non-vacuity floor: scrubbing must not blank the whole line."""
        out = mtp.scrub('echo "hello" > file', "bash")
        assert ">" in out
        assert "echo" in out

    def test_a_hash_inside_a_string_is_not_a_comment(self) -> None:
        out = mtp.scrub('echo "a # b" > f', "bash")
        assert ">" in out

    def test_scrub_preserves_line_length(self) -> None:
        """Columns must still line up, so a caller can report a position."""
        line = 'x = "abcd"  # note'
        assert len(mtp.scrub(line, "python")) == len(line)

    def test_an_unknown_language_still_scrubs_strings(self) -> None:
        assert "z" not in mtp.scrub("'z'", "brainfuck")


class TestIdentifierBoundary:
    """Defect 1: ``format`` must not match inside ``inFORMATion``."""

    def test_a_substring_does_not_match(self) -> None:
        found = mtp.find_sites(["    print(information)"], ["format"], 1, 1, "python")
        assert found == []

    def test_the_whole_identifier_matches(self) -> None:
        found = mtp.find_sites(["    format(x)"], ["format"], 1, 1, "python")
        assert [n for n, _ in found] == [1]

    def test_an_attribute_call_matches(self) -> None:
        """Defect 3: a literal call site spelled through a receiver."""
        found = mtp.find_sites(
            ["    path.write_text(data)"], ["write_text"], 1, 1, "python",
        )
        assert [n for n, _ in found] == [1]

    def test_a_dotted_primitive_matches_on_its_last_component(self) -> None:
        found = mtp.find_sites(
            ["    os.makedirs(d)"], ["os.makedirs"], 1, 1, "python",
        )
        assert [n for n, _ in found] == [1]

    def test_a_non_identifier_name_matches_literally(self) -> None:
        """bash's sink name is the operator ``>`` — no identifier boundary."""
        found = mtp.find_sites(['echo 1 > "$f"'], [">"], 1, 1, "bash")
        assert [n for n, _ in found] == [1]

    def test_the_span_bounds_the_search(self) -> None:
        lines = ["format(a)", "format(b)", "format(c)"]
        found = mtp.find_sites(lines, ["format"], 2, 2, "python")
        assert [n for n, _ in found] == [2]

    def test_a_comment_line_inside_the_span_is_not_a_site(self) -> None:
        """Defect 2, end to end."""
        found = mtp.find_sites(["# format(a)"], ["format"], 1, 1, "python")
        assert found == []


class TestSinkSearchSpans:
    """Defect 4: every symbol on the PATH, not just the source's span."""

    def test_every_first_party_path_symbol_is_searched(self) -> None:
        flow = {
            "path": [
                "python:a.py:201-255:main:function",
                "python:a.py:110-137:generate_dot_graph:function",
            ],
            "source_symbol": "python:a.py:201-255:main:function",
        }
        spans = mtp.sink_search_spans(flow)
        assert ("a.py", 110, 137, "generate_dot_graph") in spans
        assert ("a.py", 201, 255, "main") in spans

    def test_the_arklib3_shape_reaches_the_second_symbol(self) -> None:
        """The defect-4 shape: the sink lives in the SECOND path symbol."""
        flow = {
            "path": [
                "python:s/g.py:201-255:main:function",
                "python:s/g.py:110-137:generate_dot_graph:function",
            ],
            "source_symbol": "python:s/g.py:201-255:main:function",
        }
        assert len(mtp.sink_search_spans(flow)) == 2

    def test_an_external_path_symbol_is_skipped(self) -> None:
        """An external symbol names no file and cannot be read against source."""
        flow = {
            "path": [
                "python:a.py:1-9:f:function",
                "python:builtins:0-0:open:external_symbol",
            ],
            "source_symbol": "python:a.py:1-9:f:function",
        }
        assert mtp.sink_search_spans(flow) == [("a.py", 1, 9, "f")]

    def test_a_symbol_with_no_span_is_skipped(self) -> None:
        """A missing span must never be read as line 0 — an excerpt at the top
        of a file reads as evidence and is not."""
        flow = {
            "path": ["python:a.py:f:function"],
            "source_symbol": "python:a.py:f:function",
        }
        assert mtp.sink_search_spans(flow) == []

    def test_a_flow_with_no_path_falls_back_to_the_source_symbol(self) -> None:
        flow = {"path": [], "source_symbol": "python:a.py:1-9:f:function"}
        assert mtp.sink_search_spans(flow) == [("a.py", 1, 9, "f")]

    def test_duplicate_path_symbols_are_searched_once(self) -> None:
        sym = "python:a.py:1-9:f:function"
        flow = {"path": [sym, sym], "source_symbol": sym}
        assert len(mtp.sink_search_spans(flow)) == 1


class TestSinkNames:
    """Defect 3: a collapsed situation stands for SEVERAL primitives."""

    def test_every_collapsed_primitive_is_searched(self) -> None:
        """ArkLib#3 collapses ``open``, ``file.write`` and ``json.dump``;
        searching only the headline ``open`` can report nothing found."""
        flow = {
            "sink_name": "open",
            "sink_primitives": ["builtins.open", "file.write", "json.dump"],
        }
        assert mtp.sink_names(flow) == ["dump", "open", "write"]

    def test_the_bare_sink_name_is_used_when_no_primitives_are_listed(self) -> None:
        assert mtp.sink_names({"sink_name": "write", "sink_primitives": []}) == ["write"]


class TestRenderedPacket:
    """The whole packet, on a fixture with the ArkLib#3 shape."""

    @pytest.fixture()
    def repo(self, tmp_path: Path) -> Path:
        (tmp_path / "s").mkdir()
        (tmp_path / "s/g.py").write_text(
            "\n".join([
                "def generate_dot_graph(out):",      # 1
                "    with open(out, 'w') as f:",     # 2
                "        f.write('x')",              # 3
                "",                                  # 4
                "def main():",                       # 5
                "    args = parser.parse_args()",    # 6
                "    generate_dot_graph(args.out)",  # 7
            ]) + "\n",
            encoding="utf-8",
        )
        return tmp_path

    def _flow(self) -> dict:
        return {
            "flow_id": "F#3", "repo": "R", "claim_id": "c",
            "analysis_method": "ddg_mixed", "collapsed_flow_count": 6,
            "hops": 1,
            "path": [
                "python:s/g.py:5-7:main:function",
                "python:s/g.py:1-3:generate_dot_graph:function",
            ],
            "source_symbol": "python:s/g.py:5-7:main:function",
            "source_file": "s/g.py", "source_lines": [5, 7],
            "source_primitive": "parse_args", "source_boundary": "env_read",
            "sink_symbol": "python:builtins:0-0:open:external_symbol",
            "sink_name": "open", "sink_module": "builtins",
            "sink_primitives": ["builtins.open", "file.write"],
        }

    def test_the_sink_is_found_in_the_callee_not_the_source(self, repo: Path) -> None:
        """The defect-4 situation, end to end: the listing is not empty."""
        text = mtp.render_packet(self._flow(), repo)
        assert "none found" not in text
        assert "open(out" in text
        assert "generate_dot_graph" in text

    def test_the_source_site_is_still_listed(self, repo: Path) -> None:
        text = mtp.render_packet(self._flow(), repo)
        assert "parse_args()" in text

    def test_a_genuinely_absent_sink_says_so_with_its_scope(self, repo: Path) -> None:
        """An honest empty must name WHAT was searched, or a reader cannot
        tell "not there" from "not looked for" — without that distinction an
        empty listing is misleading rather than merely thin."""
        flow = {**self._flow(), "sink_name": "socket", "sink_primitives": []}
        text = mtp.render_packet(flow, repo)
        assert "none found" in text
        assert "searched" in text.lower()

    def test_a_missing_file_is_reported_not_crashed(self, tmp_path: Path) -> None:
        text = mtp.render_packet(self._flow(), tmp_path)
        assert "unreadable" in text.lower() or "none found" in text.lower()

    def test_the_packet_carries_no_verdict(self, repo: Path) -> None:
        """Blindness: the packet is facts + source, never a label."""
        text = mtp.render_packet(self._flow(), repo).lower()
        for leak in ("true positive", "false positive", "verdict", "precision"):
            assert leak not in text


class TestFileAnchoredSymbols:
    """A file-anchored symbol covers the whole file, not its declared span.

    ``bash:scripts/lintWhitespace.sh:1-1:file:file`` declares span 1-1 for a
    26-line script. Honouring that span literally would search ONE line and
    report "none found" for a redirect on line 10 — the same manufactured
    absence as defect 4, arriving by another route. Function-kind symbols,
    whose declared span is right, cannot exercise this, so these tests use
    file-kind ones.
    """

    def test_a_file_symbol_covers_the_whole_file(self) -> None:
        parsed = mtp.parse_symbol_id("bash:s.sh:1-1:file:file")
        assert mtp.effective_span(parsed, 26) == (1, 26)

    def test_a_function_symbol_keeps_its_declared_span(self) -> None:
        parsed = mtp.parse_symbol_id("python:a.py:10-20:f:function")
        assert mtp.effective_span(parsed, 500) == (10, 20)

    def test_a_file_symbol_in_an_empty_file_yields_nothing(self) -> None:
        parsed = mtp.parse_symbol_id("bash:s.sh:1-1:file:file")
        assert mtp.effective_span(parsed, 0) is None

    def test_a_spanless_symbol_yields_nothing(self) -> None:
        assert mtp.effective_span(mtp.parse_symbol_id("python:a.py:f:function"), 9) is None


class TestShellEnvSites:
    """Defect 2's other half: "match any `$VAR` including comments and locals".

    A bash ``environ`` source names no literal token — there is no ``environ``
    in the script — so name matching finds nothing and an adjudicator is shown
    an empty listing for a source that is really there. The evidence for an
    ambient read is the EXPANSION, and the two things that must not count as
    one are comments and locally-assigned names.

    Strings are deliberately NOT excluded here, unlike everywhere else: the
    shell expands inside double quotes, so ``"$HOME/x"`` is a real read.
    """

    def test_an_expansion_is_a_site(self) -> None:
        assert mtp.shell_env_sites(['echo "$HOME"']) == [(1, 'echo "$HOME"')]

    def test_a_braced_expansion_is_a_site(self) -> None:
        assert [n for n, _ in mtp.shell_env_sites(["echo ${PATH}"])] == [1]

    def test_a_locally_assigned_name_is_not_an_environment_read(self) -> None:
        lines = ["tmpfile=$(mktemp)", 'echo 1 > "$tmpfile"']
        assert mtp.shell_env_sites(lines) == []

    def test_a_comment_is_not_a_site(self) -> None:
        assert mtp.shell_env_sites(["# uses $HOME here"]) == []

    def test_a_positional_parameter_is_not_an_environment_read(self) -> None:
        assert mtp.shell_env_sites(['echo "$1" "$@" "$?"']) == []

    def test_a_local_assignment_does_not_hide_a_real_read_elsewhere(self) -> None:
        """Non-vacuity floor: exclusion must be per-NAME, not per-file."""
        lines = ["tmpfile=$(mktemp)", 'echo "$HOME" > "$tmpfile"']
        assert [n for n, _ in mtp.shell_env_sites(lines)] == [2]

    def test_an_export_is_still_an_assignment(self) -> None:
        assert mtp.shell_env_sites(["export FOO=1", 'echo "$FOO"']) == []

    def test_a_read_bound_name_is_a_local(self) -> None:
        """A name bound by ``read`` is a local, not an environment read --
        ArkLib's lintWhitespace.sh binds ``file`` with ``read -r file`` and
        then expands ``$file``."""
        lines = ["while IFS=: read -r line_num line; do", '    echo "$line"']
        assert mtp.shell_env_sites(lines) == []

    def test_a_for_loop_variable_is_a_local(self) -> None:
        assert mtp.shell_env_sites(["for f in *; do", '  echo "$f"']) == []

    def test_read_binding_does_not_swallow_a_real_read(self) -> None:
        """Vacuity guard: the binder must not blanket-disable the line."""
        lines = ["read -r name", 'echo "$HOME$name"']
        assert [n for n, _ in mtp.shell_env_sites(lines)] == [2]


class TestExtensionlessScripts:
    """Defect 5: an extensionless script is still read as its language.

    ``shellcheck`` carries two bash scripts named ``.github_deploy`` and
    ``.multi_arch_docker``. ``Path(name).suffix`` is ``""`` for both — a
    leading dot is not an extension — so by suffix alone the builder would
    call them an unknown language, skip the bash branch that lists ambient
    parameter expansions, and print

        (none found; searched .github_deploy 1-28)

    for a file whose line 11 reads ``for tag in $TAGS``. That is defect 2's
    failure mode through a different door: the listing is empty not because
    the read is absent but because the instrument never looked, and an
    adjudicator is handed manufactured evidence of absence pointing at FP.

    The shebang supplies the fact the extension is missing, and both files
    carry ``#!/bin/bash`` on line 1.
    """

    def test_a_suffixless_path_alone_is_unknown(self) -> None:
        """Without content there is nothing to go on, and that is honest."""
        assert mtp._language_of(".github_deploy") == ""

    def test_a_shebang_names_the_language(self) -> None:
        assert mtp._language_of(".github_deploy", "#!/bin/bash") == "bash"

    def test_the_env_form_of_a_shebang_is_read(self) -> None:
        assert mtp._language_of("script", "#!/usr/bin/env bash") == "bash"

    @pytest.mark.parametrize(
        ("shebang", "language"),
        [("#!/bin/sh", "bash"), ("#!/bin/dash", "bash"), ("#!/bin/zsh", "bash"),
         ("#!/usr/bin/python3", "python"), ("#!/usr/bin/env ruby", "ruby")],
    )
    def test_known_interpreters_map_to_their_language(
        self, shebang: str, language: str,
    ) -> None:
        assert mtp._language_of("script", shebang) == language

    def test_an_unknown_interpreter_stays_unknown(self) -> None:
        assert mtp._language_of("script", "#!/usr/bin/env tclsh") == ""

    def test_a_first_line_that_is_not_a_shebang_is_ignored(self) -> None:
        assert mtp._language_of("script", "echo hello") == ""

    def test_a_bare_shebang_with_no_interpreter_is_ignored(self) -> None:
        assert mtp._language_of("script", "#!") == ""

    def test_the_suffix_wins_over_the_shebang(self) -> None:
        """A ``.py`` file opening with a shell shebang is still Python.

        The extension is the author's declaration about the whole file; the
        shebang only says how one entry point is executed. Consulting it
        FIRST would let a wrapper's interpreter line relabel real source.
        """
        assert mtp._language_of("a.py", "#!/bin/bash") == "python"

    def test_the_packet_lists_the_expansion_in_a_suffixless_script(
        self, tmp_path: Path,
    ) -> None:
        """Defect 5 end to end, in the shellcheck shape."""
        (tmp_path / ".github_deploy").write_text(
            "\n".join([
                "#!/bin/bash",          # 1
                "# $COMMENTED is not a read",  # 2
                "for tag in $TAGS",     # 3
                "do",                   # 4
                "  echo x > out",       # 5
                "done",                 # 6
            ]) + "\n",
            encoding="utf-8",
        )
        flow = {
            "flow_id": "S#0", "repo": "shellcheck", "claim_id": "c",
            "analysis_method": "structural", "collapsed_flow_count": 1,
            "hops": 0,
            "path": ["bash:.github_deploy:1-6:file:file"],
            "source_symbol": "bash:.github_deploy:1-6:file:file",
            "source_file": ".github_deploy", "source_lines": [1, 6],
            "source_primitive": "environ", "source_boundary": "env_read",
            "sink_symbol": "bash:redirect:0-0:>:external_symbol",
            "sink_name": ">", "sink_module": "redirect",
            "sink_primitives": ["redirect.>"],
        }
        text = mtp.render_packet(flow, tmp_path)
        source_block = text.split("-- SINK")[0]
        assert "for tag in $TAGS" in source_block
        assert "none found" not in source_block
        # Defect 2 still holds here: the commented expansion is not a site,
        # which is the property the bash branch exists to provide.
        assert "COMMENTED" not in source_block

    def test_a_suffixless_sink_file_is_scrubbed_as_its_language(
        self, tmp_path: Path,
    ) -> None:
        """The same blindness on the SINK side: an unknown language scrubs no
        comment, so a redirect inside a shell comment reads as a call site."""
        (tmp_path / "deploy").write_text(
            "\n".join([
                "#!/bin/sh",              # 1
                "# writes with > here",   # 2
                "echo x > real",          # 3
            ]) + "\n",
            encoding="utf-8",
        )
        flow = {
            "flow_id": "S#1", "repo": "r", "claim_id": "c",
            "analysis_method": "structural", "collapsed_flow_count": 1,
            "hops": 0,
            "path": ["bash:deploy:1-3:file:file"],
            "source_symbol": "bash:deploy:1-3:file:file",
            "source_file": "deploy", "source_lines": [1, 3],
            "source_primitive": "environ", "source_boundary": "env_read",
            "sink_symbol": "bash:redirect:0-0:>:external_symbol",
            "sink_name": ">", "sink_module": "redirect",
            "sink_primitives": ["redirect.>"],
        }
        sink_block = mtp.render_packet(flow, tmp_path).split("-- SINK")[1]
        assert "echo x > real" in sink_block
        assert "writes with" not in sink_block

    def test_a_bare_env_shebang_names_no_interpreter(self) -> None:
        """``#!/usr/bin/env`` with nothing after it is not a language claim."""
        assert mtp._language_of("script", "#!/usr/bin/env") == ""

    def test_an_empty_file_on_the_path_is_not_a_shebang_lookup(
        self, tmp_path: Path,
    ) -> None:
        """A zero-line file has no first line to consult, and reading one
        would be an IndexError on a shape that occurs (a truncated or
        generated file on a flow's path)."""
        (tmp_path / "empty.txt").write_text("", encoding="utf-8")
        flow = {
            "flow_id": "E#0", "repo": "r", "claim_id": "c",
            "analysis_method": "structural", "collapsed_flow_count": 1,
            "hops": 0,
            "path": ["bash:empty.txt:1-1:file:file"],
            "source_symbol": "bash:empty.txt:1-1:file:file",
            "source_file": "empty.txt", "source_lines": [1, 1],
            "source_primitive": "environ", "source_boundary": "env_read",
            "sink_symbol": "bash:redirect:0-0:>:external_symbol",
            "sink_name": ">", "sink_module": "redirect",
            "sink_primitives": ["redirect.>"],
        }
        text = mtp.render_packet(flow, tmp_path)
        assert "none found" in text


class TestOverlappingPathSpans:
    """A site is listed once, under the narrowest path symbol (WI-binod).

    Path symbols can NEST -- a closure and its enclosing function both on the
    path -- and a whole-file pseudo-symbol is the widest instance. In the
    ArkLib :5 shape, the ``open()`` sites in ``revert_nav`` also lie inside
    the module-level symbol's widened whole-file span, but only TEXTUALLY:
    module-level execution never reaches them. Listing a site under every
    span that contains it would show it twice, once as though module level
    called it. Each site is therefore listed ONCE, under the narrowest path
    symbol whose span contains it.

    The module-level entry is labelled "at module level" rather than by the
    pseudo-symbol's name ``file``, which reads like a claim about a variable
    called ``file`` when the script has one (ArkLib :3 does).
    """

    @pytest.fixture()
    def repo(self, tmp_path: Path) -> Path:
        (tmp_path / "s").mkdir()
        (tmp_path / "s/nav.py").write_text(
            "\n".join([
                "import sys",                         # 1
                "",                                   # 2
                "def revert_nav(d):",                 # 3
                "    with open(d, 'w') as f:",        # 4
                "        f.write('x')",               # 5
                "",                                   # 6
                "def helper(p):",                     # 7
                "    with open(p, 'w') as g:",        # 8
                "        g.write('y')",               # 9
                "",                                   # 10
                "LOG = open('log.txt', 'a')",         # 11
                "revert_nav(sys.argv[1])",            # 12
            ]) + "\n",
            encoding="utf-8",
        )
        return tmp_path

    def _flow(self, path: list[str]) -> dict:
        return {
            "flow_id": "A#5", "repo": "R", "claim_id": "c",
            "analysis_method": "structural", "collapsed_flow_count": 2,
            "hops": 1,
            "path": path,
            "source_symbol": path[0],
            "source_file": "s/nav.py", "source_lines": [1, 1],
            "source_primitive": "argv", "source_boundary": "env_read",
            "sink_symbol": "python:builtins:0-0:open:external_symbol",
            "sink_name": "open", "sink_module": "builtins",
            "sink_primitives": ["builtins.open", "file.write"],
        }

    def _sink_block(self, repo: Path, path: list[str]) -> str:
        return mtp.render_packet(self._flow(path), repo).split("-- SINK")[1]

    _ARKLIB5 = (
        "python:s/nav.py:1-1:file:file",
        "python:s/nav.py:3-5:revert_nav:function",
    )

    def test_a_site_inside_a_path_function_is_listed_once(self, repo: Path) -> None:
        """The ArkLib :5 shape: one listing per site."""
        block = self._sink_block(repo, list(self._ARKLIB5))
        assert block.count("with open(d, 'w') as f:") == 1
        assert block.count("f.write('x')") == 1

    def test_the_site_is_listed_under_the_function_not_the_file(
        self, repo: Path,
    ) -> None:
        block = self._sink_block(repo, list(self._ARKLIB5))
        under_function = block.split("in revert_nav")[1]
        assert "with open(d, 'w') as f:" in under_function

    def test_a_module_level_site_is_still_listed(self, repo: Path) -> None:
        """Non-vacuity floor: de-duplication must not drop the module-level
        listing itself -- a module-level call is exactly what it is for."""
        block = self._sink_block(repo, list(self._ARKLIB5))
        assert "LOG = open('log.txt', 'a')" in block

    def test_a_site_in_a_function_off_the_path_stays_listed(
        self, repo: Path,
    ) -> None:
        """Stated limit, pinned so a change to it is a decision: the builder
        knows only the PATH's spans, not the file's symbol table, so a site in
        a function the path does not name stays under the module-level entry.
        That errs toward showing the adjudicator too much, the direction this
        builder takes throughout."""
        block = self._sink_block(repo, list(self._ARKLIB5))
        assert block.count("with open(p, 'w') as g:") == 1

    def test_the_file_symbol_is_labelled_module_level_not_by_its_name(
        self, repo: Path,
    ) -> None:
        """``in file (...)`` would read as a claim about a variable named
        ``file``."""
        block = self._sink_block(repo, list(self._ARKLIB5))
        assert "in file (" not in block
        assert "at module level (s/nav.py 1-12)" in block

    def test_nested_path_symbols_attribute_to_the_innermost(
        self, repo: Path,
    ) -> None:
        """The same mechanism without a file symbol: an enclosing function and
        a narrower one both on the path."""
        block = self._sink_block(repo, [
            "python:s/nav.py:3-9:outer:function",
            "python:s/nav.py:7-9:helper:function",
        ])
        assert block.count("with open(p, 'w') as g:") == 1
        assert "with open(p, 'w') as g:" in block.split("in helper")[1]
        assert "with open(d, 'w') as f:" in block.split("in helper")[0]

    def test_an_empty_search_names_the_widened_file_span(
        self, repo: Path,
    ) -> None:
        """The empty-result scope names the WIDENED file span, never the
        unwidened ``1--1`` sentinel."""
        flow = {**self._flow(list(self._ARKLIB5)), "sink_name": "socket",
                "sink_primitives": []}
        text = mtp.render_packet(flow, repo)
        assert "1--1" not in text
        assert "module level s/nav.py 1-12" in text

    def test_an_unreadable_file_symbol_names_no_range(
        self, tmp_path: Path,
    ) -> None:
        """A file-anchored symbol whose file cannot be read has no line count
        to widen to; its scope must not print the ``1--1`` sentinel."""
        flow = self._flow(["python:gone.py:1-1:file:file"])
        text = mtp.render_packet(flow, tmp_path)
        assert "module level (gone.py): (unreadable)" in text
        assert "searched module level gone.py)" in text
        assert "1--1" not in text

    def test_innermost_span_prefers_the_narrower(self) -> None:
        spans = [("a.py", 1, 100, "file"), ("a.py", 10, 20, "f")]
        assert mtp.innermost_span(15, "a.py", spans) == 1
        assert mtp.innermost_span(50, "a.py", spans) == 0

    def test_innermost_span_ignores_other_files_and_misses(self) -> None:
        spans = [("a.py", 1, 100, "file"), ("b.py", 10, 20, "f")]
        assert mtp.innermost_span(15, "a.py", spans) == 0
        assert mtp.innermost_span(150, "a.py", spans) is None

    def test_innermost_span_first_wins_a_tie(self) -> None:
        spans = [("a.py", 10, 20, "f"), ("a.py", 10, 20, "g")]
        assert mtp.innermost_span(15, "a.py", spans) == 0


class TestShellParametersBashAssigns:
    """Names bash assigns itself are not environment reads (WI-binod).

    bash sets ``BASH_SOURCE`` itself; nothing inherits it, so cert-manager's
    checkhash.sh:21 (``SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )"
    ...)"``) is not a SOURCE site of an ``environ`` source. hypergumbo's bash
    analyzer does not emit it as an env read (INV-nular), and a builder
    keeping its own list of such names could drift from the tool it measures.
    So the fact has ONE home: the builder reads bash.py's own sets, and these
    tests enumerate them rather than sample them.
    """

    _CHECKHASH = (
        'SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null '
        '2>&1 && pwd )"'
    )

    def test_bash_source_is_not_an_environment_read(self) -> None:
        """cert-manager's line yields no environment site."""
        assert mtp.shell_env_sites([self._CHECKHASH]) == []

    def test_a_real_read_on_the_same_line_is_still_a_site(self) -> None:
        """Non-vacuity floor: the exclusion is per NAME, not per line."""
        line = 'cp "${BASH_SOURCE[0]}" "$HOME/x"'
        assert [n for n, _ in mtp.shell_env_sites([line])] == [1]

    def test_every_bash_assigned_name_is_excluded(self) -> None:
        """Enumerated over bash.py's set, so a name added there is covered
        here with no second edit -- which a second, hand-kept list would
        not give."""
        from hypergumbo_lang_mainstream import bash

        assert "BASH_SOURCE" in bash._SHELL_STATE_NAMES
        leaked = [
            name for name in sorted(bash._SHELL_STATE_NAMES)
            if mtp.shell_env_sites([f'echo "${{{name}}}"'])
        ]
        assert leaked == []

    def test_every_host_description_name_is_not_an_environment_read(
        self,
    ) -> None:
        """bash.py routes these to ``shell.hostinfo`` (INV-tutar), not to
        ``env.environ``, so an ``environ`` listing that showed them would be
        the BASH_SOURCE defect again for a different set."""
        from hypergumbo_lang_mainstream import bash

        assert "HOSTNAME" in bash._HOST_DESCRIPTION_NAMES
        leaked = [
            name for name in sorted(bash._HOST_DESCRIPTION_NAMES)
            if mtp.shell_env_sites([f'echo "${name}"'])
        ]
        assert leaked == []

    def test_a_hostinfo_source_lists_host_description_expansions(self) -> None:
        """The other half of the split. A ``hostinfo`` source names no literal
        token either, so name matching would print "(none found)" for a
        ``$HOSTNAME`` read sitting in the span."""
        lines = ['echo "$HOSTNAME" > h', 'echo "$API_KEY" > k',
                 'echo "${BASH_SOURCE[0]}"']
        assert mtp.shell_env_sites(lines, "hostinfo") == [
            (1, 'echo "$HOSTNAME" > h'),
        ]

    def test_a_locally_assigned_host_name_is_not_a_hostinfo_read(self) -> None:
        lines = ["HOSTNAME=box", 'echo "$HOSTNAME"']
        assert mtp.shell_env_sites(lines, "hostinfo") == []

    def test_the_packet_lists_a_hostinfo_source(self, tmp_path: Path) -> None:
        (tmp_path / "run.sh").write_text(
            "\n".join([
                "#!/bin/bash",                       # 1
                'echo "$HOSTNAME" > host.txt',       # 2
            ]) + "\n",
            encoding="utf-8",
        )
        flow = {
            "flow_id": "H#0", "repo": "r", "claim_id": "c",
            "analysis_method": "structural", "collapsed_flow_count": 1,
            "hops": 0,
            "path": ["bash:run.sh:1-1:file:file"],
            "source_symbol": "bash:run.sh:1-1:file:file",
            "source_file": "run.sh", "source_lines": [1, 1],
            "source_primitive": "hostinfo",
            "source_boundary": "host_info_read",
            "sink_symbol": "bash:redirect:0-0:>:external_symbol",
            "sink_name": ">", "sink_module": "redirect",
            "sink_primitives": ["redirect.>"],
        }
        source_block = mtp.render_packet(flow, tmp_path).split("-- SINK")[0]
        assert 'echo "$HOSTNAME" > host.txt' in source_block
        assert "none found" not in source_block
