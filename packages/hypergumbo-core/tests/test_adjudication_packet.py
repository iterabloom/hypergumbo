# SPDX-License-Identifier: AGPL-3.0-or-later
"""The adjudication packet builder, and the four defects 0006 filed against it.

WHY THIS FILE EXISTS AT ALL. Measurement 0006 adjudicated 112 situations and
published 33.9% correctness / 24.1% useful precision. Its own section F names
~12 reports against **the measurement's own packet builder, NOT hypergumbo**,
and ends "Fix before reuse." The builder was a session artifact and was never
committed, so the defects could not be fixed, only re-encountered. This is the
builder, in-repo, with each named defect pinned as a test.

THE FOUR DEFECTS, quoted from 0006 §F and reproduced from the real packets in
``measurement_0006_08252026/packets2/``:

  1. "candidate-line listings match sink names as bare SUBSTRINGS
     (`inFORMATion` matched `format`; `-> ` inside a quoted pattern matched
     `>`)"
  2. "match any `$VAR` including shell COMMENTS and locals"
  3. "miss literal call sites"
  4. "search for sink sites only inside the SOURCE symbol's span — which makes
     the sink listing structurally empty for every multi-hop situation"

Defect 4 is the load-bearing one. ``ArkLib#3``'s path is ``main`` (201-255)
then ``generate_dot_graph`` (110-137); ``open`` is called in the SECOND, so a
search of the source span printed "(none found in span)" for a situation whose
sink is plainly there. An adjudicator reading that packet is being shown
evidence of absence that the instrument manufactured.
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
        """``-> `` inside a quoted pattern matched the bash redirect ``>``."""
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
        """The exact situation that printed "(none found in span)"."""
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
        """ArkLib#3 collapses ``open``, ``file.write`` and ``json.dump``; the
        old packet searched only ``open`` and reported nothing found."""
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
        """THE REGRESSION. This is the situation 0006 printed as empty."""
        text = mtp.render_packet(self._flow(), repo)
        assert "none found" not in text
        assert "open(out" in text
        assert "generate_dot_graph" in text

    def test_the_source_site_is_still_listed(self, repo: Path) -> None:
        text = mtp.render_packet(self._flow(), repo)
        assert "parse_args()" in text

    def test_a_genuinely_absent_sink_says_so_with_its_scope(self, repo: Path) -> None:
        """An honest empty must name WHAT was searched, or a reader cannot
        tell "not there" from "not looked for" — the distinction that made
        the original listing misleading rather than merely thin."""
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
    """Found by running the fixed builder on 0006's own ArkLib flows.

    ``bash:scripts/lintWhitespace.sh:1-1:file:file`` declares span 1-1 for a
    26-line script. Honouring that span literally searched ONE line and
    reported "none found" for a redirect on line 10 — the same manufactured
    absence as defect 4, arriving by another route. The unit fixtures all used
    function-kind symbols, where the declared span is right, so no test caught
    it; reading real output back against source did.
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
    ambient read is the EXPANSION, and the two things the old builder got
    wrong are comments and locally-assigned names.

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
        """ArkLib's lintWhitespace.sh binds ``file`` with ``read -r file``;
        ``$file`` was listed as an environment read on four lines. Found by
        reading the rendered packet back against source, not by a fixture."""
        lines = ["while IFS=: read -r line_num line; do", '    echo "$line"']
        assert mtp.shell_env_sites(lines) == []

    def test_a_for_loop_variable_is_a_local(self) -> None:
        assert mtp.shell_env_sites(["for f in *; do", '  echo "$f"']) == []

    def test_read_binding_does_not_swallow_a_real_read(self) -> None:
        """Vacuity guard: the binder must not blanket-disable the line."""
        lines = ["read -r name", 'echo "$HOME$name"']
        assert [n for n, _ in mtp.shell_env_sites(lines)] == [2]


class TestExtensionlessScripts:
    """Defect 5, found by running the FIXED builder on the 0007 draw.

    ``shellcheck`` carries two bash scripts named ``.github_deploy`` and
    ``.multi_arch_docker``. ``Path(name).suffix`` is ``""`` for both — a
    leading dot is not an extension — so the builder called them an unknown
    language, skipped the bash branch that lists ambient parameter
    expansions, and printed

        (none found; searched .github_deploy 1-28)

    for a file whose line 11 reads ``for tag in $TAGS``. That is defect 2's
    failure mode returning through a different door: the listing is empty not
    because the read is absent but because the instrument never looked, and
    an adjudicator is handed manufactured evidence of absence pointing at FP.

    The shebang is the fact the extension is missing, and both files carry
    ``#!/bin/bash`` on line 1.
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
        """THE REGRESSION, in the shellcheck shape."""
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
        # Defect 2 must not return with it: the commented expansion is not a
        # site, which is the property the bash branch exists to provide.
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
