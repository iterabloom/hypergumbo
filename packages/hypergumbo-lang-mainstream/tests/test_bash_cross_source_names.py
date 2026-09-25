# SPDX-License-Identifier: AGPL-3.0-or-later
"""A name assigned in a script joined by ``source`` is not an environment read.

INV-pujob / WI-zovuz's third deliverable. bash.py's env-read discriminator is
"a name expanded but never assigned in THIS FILE came from the environment" --
the conservative rule available without cross-file analysis. It is wrong the
moment one script ``source``s another, because bash is DYNAMICALLY SCOPED: the
sourced script sees the sourcer's assignments and the sourcer sees the sourced
script's. guacamole's ``DESTINATION`` is assigned once in the whole tree, at
``build-guacamole.sh:58`` as ``$2``, and every ``build.d`` script that reads it
was reported as reading a host secret.

WHY RESOLUTION IS THE HARD HALF, measured over the cohort's 97 ``source``/``.``
statements: only 12 have a target the emitted edge can follow. 80 (82.5%) are a
variable or a command substitution. But they are dynamic in the PREFIX and
literal in the TAIL -- ``source $TOOL_LIB_PATH/gitlib.sh`` -- so matching the
literal trailing component against repository bash files lifts 12 to 81 of 97
with ZERO ambiguity on that cohort.

FAIL CLOSED IS THE WHOLE DISCIPLINE HERE. This removes taint SOURCES, which is
the false-all-clear direction: an unresolved target, or one whose tail matches
two files, must add no assignments and leave the name an environment read.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.discovery import FileIndex, get_file_index, set_file_index
from hypergumbo_lang_mainstream.bash import analyze_bash, find_bash_files

ENV_ATTR = "bash:env:0-0:env.environ:attribute"


def _env_names(root: Path) -> set[str]:
    """Every name this repo reports as an environment read."""
    names = set()
    for edge in analyze_bash(root).edges:
        if edge.dst != ENV_ATTR:
            continue
        meta = edge.meta or {}
        v = meta.get("env_var")
        if isinstance(v, str):
            names.add(v)
        for x in meta.get("env_var_values", []) or []:
            names.add(x)
    return names


class TestTheSourcingGraphCarriesAssignments:
    """An assignment reaches every file joined to it by ``source``."""

    def test_sourced_file_sees_the_sourcers_assignment(self, tmp_path):
        # guacamole's shape exactly: the sourcER assigns, the sourcED file
        # reads. Dynamic scoping makes this a binding, not an environment read.
        (tmp_path / "main.sh").write_text(
            'DESTINATION="$2"\n'
            'source ./lib.sh\n'
        )
        (tmp_path / "lib.sh").write_text(
            'echo "$DESTINATION" > /tmp/out\n'
        )
        assert "DESTINATION" not in _env_names(tmp_path)

    def test_sourcer_sees_the_sourced_files_assignment(self, tmp_path):
        # The other direction, which is the ordinary "library of settings"
        # idiom and the one cilium's contrib/backporting uses.
        (tmp_path / "lib.sh").write_text('GHCURL="curl -s"\n')
        (tmp_path / "main.sh").write_text(
            'source ./lib.sh\n'
            'echo "$GHCURL" > /tmp/out\n'
        )
        assert "GHCURL" not in _env_names(tmp_path)

    def test_a_name_nobody_assigns_stays_an_environment_read(self, tmp_path):
        # The control that costs something: if this ever passes vacuously the
        # gate above is deleting every source rather than the sourced ones.
        (tmp_path / "lib.sh").write_text('GHCURL="curl -s"\n')
        (tmp_path / "main.sh").write_text(
            'source ./lib.sh\n'
            'echo "$REAL_ENV_VAR" > /tmp/out\n'
        )
        assert "REAL_ENV_VAR" in _env_names(tmp_path)

    def test_an_unconnected_file_does_not_donate_assignments(self, tmp_path):
        # rabbitmq's refutation, pinned: a vendored `mvnw` reading $HOME must
        # not be silenced by an unrelated script that happens to assign HOME.
        (tmp_path / "unrelated.sh").write_text('HOME=/somewhere\n')
        (tmp_path / "mvnw").write_text(
            '#!/bin/sh\n'
            'echo "$HOME" > /tmp/out\n'
        )
        assert "HOME" in _env_names(tmp_path)


class TestResolvingADynamicTarget:
    """Dynamic in the prefix, literal in the tail."""

    def test_variable_prefix_with_literal_tail_resolves(self, tmp_path):
        # cilium: `source $TOOL_LIB_PATH/gitlib.sh`.
        (tmp_path / "gitlib.sh").write_text('GHCURL="curl -s"\n')
        (tmp_path / "main.sh").write_text(
            'source $TOOL_LIB_PATH/gitlib.sh\n'
            'echo "$GHCURL" > /tmp/out\n'
        )
        assert "GHCURL" not in _env_names(tmp_path)

    def test_command_substitution_prefix_with_literal_tail_resolves(self, tmp_path):
        # cilium: `source $(dirname $(readlink -ne $BASH_SOURCE))/k8s-common.sh`.
        (tmp_path / "k8s-common.sh").write_text('TOOL_LIB_PATH=/x\n')
        (tmp_path / "main.sh").write_text(
            'source $(dirname $(readlink -ne $BASH_SOURCE))/k8s-common.sh\n'
            'echo "$TOOL_LIB_PATH" > /tmp/out\n'
        )
        assert "TOOL_LIB_PATH" not in _env_names(tmp_path)

    def test_an_ambiguous_tail_declines(self, tmp_path):
        # Two `common.sh` in different directories: the rule must FAIL CLOSED
        # rather than pick one. This is the case the cohort happens not to
        # contain, which is exactly why it is pinned here.
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        (tmp_path / "a" / "common.sh").write_text('SHARED=1\n')
        (tmp_path / "b" / "common.sh").write_text('OTHER=2\n')
        (tmp_path / "main.sh").write_text(
            'source $SOMEDIR/common.sh\n'
            'echo "$SHARED" > /tmp/out\n'
        )
        assert "SHARED" in _env_names(tmp_path)

    def test_a_tail_naming_no_repo_file_declines(self, tmp_path):
        (tmp_path / "main.sh").write_text(
            'source $PREFIX/not-in-this-repo.sh\n'
            'echo "$WHATEVER" > /tmp/out\n'
        )
        assert "WHATEVER" in _env_names(tmp_path)


class TestTheGlobLoopShape:
    """``for X in DIR/*.sh; do source "$X"; done`` -- INV-pujob's own instance."""

    def test_loop_glob_over_a_repo_directory_resolves(self, tmp_path):
        (tmp_path / "build.d").mkdir()
        (tmp_path / "build.d" / "020-download.sh").write_text(
            'echo "$DESTINATION" > /tmp/out\n'
        )
        (tmp_path / "build-guacamole.sh").write_text(
            'DESTINATION="$2"\n'
            'for SCRIPT in ./build.d/*.sh; do\n'
            '    source "$SCRIPT"\n'
            'done\n'
        )
        assert "DESTINATION" not in _env_names(tmp_path)

    def test_container_path_resolves_by_directory_suffix(self, tmp_path):
        # guacamole VERBATIM: the glob names /opt/guacamole/build.d/, a path
        # that does not exist in the repository at all. The repo's own
        # `build.d` is the unique directory whose trailing component matches.
        (tmp_path / "guacamole-docker").mkdir()
        (tmp_path / "guacamole-docker" / "build.d").mkdir()
        (tmp_path / "guacamole-docker" / "build.d" / "020.sh").write_text(
            'echo "$DESTINATION" > /tmp/out\n'
        )
        (tmp_path / "guacamole-docker" / "bin").mkdir()
        (tmp_path / "guacamole-docker" / "bin" / "build-guacamole.sh").write_text(
            'DESTINATION="$2"\n'
            'for SCRIPT in /opt/guacamole/build.d/*.sh; do\n'
            '    source "$SCRIPT"\n'
            'done\n'
        )
        assert "DESTINATION" not in _env_names(tmp_path)

    def test_an_ambiguous_directory_suffix_declines(self, tmp_path):
        (tmp_path / "x").mkdir()
        (tmp_path / "x" / "build.d").mkdir()
        (tmp_path / "x" / "build.d" / "a.sh").write_text('echo "$V" > /tmp/o\n')
        (tmp_path / "y").mkdir()
        (tmp_path / "y" / "build.d").mkdir()
        (tmp_path / "y" / "build.d" / "b.sh").write_text('echo "$W" > /tmp/o\n')
        (tmp_path / "main.sh").write_text(
            'V=1\n'
            'for S in /opt/build.d/*.sh; do source "$S"; done\n'
        )
        assert "V" in _env_names(tmp_path)

    def test_a_bare_variable_with_no_binding_loop_declines(self, tmp_path):
        # rabbitmq's `source $ENV_FILE` -- nothing binds it to repo files.
        (tmp_path / "lib.sh").write_text('SETTING=1\n')
        (tmp_path / "main.sh").write_text(
            'source $ENV_FILE\n'
            'echo "$SETTING" > /tmp/out\n'
        )
        assert "SETTING" in _env_names(tmp_path)


class TestTransitivity:
    """Sourcing composes, and a cycle must terminate."""

    def test_assignments_reach_through_two_hops(self, tmp_path):
        (tmp_path / "a.sh").write_text('DEEP=1\n')
        (tmp_path / "b.sh").write_text('source ./a.sh\n')
        (tmp_path / "c.sh").write_text(
            'source ./b.sh\n'
            'echo "$DEEP" > /tmp/out\n'
        )
        assert "DEEP" not in _env_names(tmp_path)

    def test_a_sourcing_cycle_terminates(self, tmp_path):
        (tmp_path / "a.sh").write_text(
            'source ./b.sh\n'
            'AA=1\n'
            'echo "$BB" > /tmp/out\n'
        )
        (tmp_path / "b.sh").write_text(
            'source ./a.sh\n'
            'BB=1\n'
            'echo "$AA" > /tmp/out\n'
        )
        names = _env_names(tmp_path)
        assert "AA" not in names and "BB" not in names


class TestResolutionEdgesThatMustDecline:
    """Each of these is a way the resolver can be asked something it cannot
    answer. Every one must contribute nothing, because contributing a wrong
    assignment deletes a real taint source."""

    def test_a_direct_glob_argument_resolves(self, tmp_path):
        # `source ./inc/*.sh` with no loop variable in between.
        (tmp_path / "inc").mkdir()
        (tmp_path / "inc" / "settings.sh").write_text('TUNABLE=1\n')
        (tmp_path / "main.sh").write_text(
            'source ./inc/*.sh\n'
            'echo "$TUNABLE" > /tmp/out\n'
        )
        assert "TUNABLE" not in _env_names(tmp_path)

    def test_a_glob_with_no_directory_part_declines(self, tmp_path):
        # `source *.sh` names no directory to match on.
        (tmp_path / "lib.sh").write_text('LOOSE=1\n')
        (tmp_path / "main.sh").write_text(
            'source *.sh\n'
            'echo "$LOOSE" > /tmp/out\n'
        )
        assert "LOOSE" in _env_names(tmp_path)

    def test_a_glob_whose_directory_is_itself_dynamic_declines(self, tmp_path):
        (tmp_path / "lib.sh").write_text('DYNDIR=1\n')
        (tmp_path / "main.sh").write_text(
            'for S in $BASE/*.sh; do source "$S"; done\n'
            'echo "$DYNDIR" > /tmp/out\n'
        )
        assert "DYNDIR" in _env_names(tmp_path)

    def test_a_loop_word_that_is_not_a_glob_declines(self, tmp_path):
        # The loop iterates literal names, not a pattern; resolving those would
        # be a different rule and this one must not guess.
        (tmp_path / "lib.sh").write_text('LISTED=1\n')
        (tmp_path / "main.sh").write_text(
            'for S in one two; do source "$S"; done\n'
            'echo "$LISTED" > /tmp/out\n'
        )
        assert "LISTED" in _env_names(tmp_path)

    def test_a_relative_target_with_dotdot_resolves(self, tmp_path):
        # `source ../lib/common.sh` from a subdirectory: the join must collapse
        # `..` without touching the filesystem.
        (tmp_path / "lib").mkdir()
        (tmp_path / "lib" / "common.sh").write_text('UPWARD=1\n')
        (tmp_path / "bin").mkdir()
        (tmp_path / "bin" / "main.sh").write_text(
            'source ../lib/common.sh\n'
            'echo "$UPWARD" > /tmp/out\n'
        )
        assert "UPWARD" not in _env_names(tmp_path)

    def test_dotdot_above_the_root_does_not_escape(self, tmp_path):
        (tmp_path / "lib.sh").write_text('ESCAPED=1\n')
        (tmp_path / "main.sh").write_text(
            'source ../../lib.sh\n'
            'echo "$ESCAPED" > /tmp/out\n'
        )
        # Collapses to `lib.sh`, which IS this repo's file -- the point is that
        # it terminates rather than producing a path with leading `..`.
        assert "ESCAPED" not in _env_names(tmp_path)

    def test_source_with_no_argument_declines(self, tmp_path):
        (tmp_path / "lib.sh").write_text('NAKED=1\n')
        (tmp_path / "main.sh").write_text(
            'source\n'
            'echo "$NAKED" > /tmp/out\n'
        )
        assert "NAKED" in _env_names(tmp_path)

    def test_the_index_is_reused_across_files_of_one_repo(self, tmp_path):
        # Two readers in one repo: the second must hit the cached reachability
        # rather than recompute, and must get the same answer.
        (tmp_path / "lib.sh").write_text('SHARED_ONE=1\n')
        (tmp_path / "one.sh").write_text(
            'source ./lib.sh\n'
            'echo "$SHARED_ONE" > /tmp/a\n'
        )
        (tmp_path / "two.sh").write_text(
            'source ./lib.sh\n'
            'echo "$SHARED_ONE" > /tmp/b\n'
        )
        assert "SHARED_ONE" not in _env_names(tmp_path)

    def test_a_dynamic_trailing_component_declines(self, tmp_path):
        # `source $PREFIX/$NAME` -- dynamic in the tail as well as the prefix,
        # so there is no literal component to match on.
        (tmp_path / "lib.sh").write_text('BOTH_DYNAMIC=1\n')
        (tmp_path / "main.sh").write_text(
            'source $PREFIX/$NAME\n'
            'echo "$BOTH_DYNAMIC" > /tmp/out\n'
        )
        assert "BOTH_DYNAMIC" in _env_names(tmp_path)


class TestExtensionlessDiscoveryUnderAFileIndex:
    """``find_bash_files`` has two ways to reach an extensionless script, and
    the repo index this feature adds walks the tree through it.

    An extensionless shebang script is not a corner case for INV-pujob: the
    cohort's sourcing chains run through several (rabbitmq's
    ``selenium/bin/suite_template`` and ``selenium/bin/components/rabbitmq``,
    cilium's ``contrib/backporting/check-stable``), so if the FileIndex path
    missed them the sourcing graph would silently lose those edges and every
    name they carry would stay a false environment read.
    """

    def test_the_file_index_fast_path_finds_a_shebang_script(self, tmp_path):
        (tmp_path / "check-stable").write_text(
            "#!/bin/bash\n"
            'source ./gitlib.sh\n'
            'echo "$GHCURL" > /tmp/out\n'
        )
        (tmp_path / "gitlib.sh").write_text('GHCURL="curl -s"\n')
        set_file_index(FileIndex.build(tmp_path))
        try:
            assert get_file_index() is not None
            found = {p.name for p in find_bash_files(tmp_path)}
            assert "check-stable" in found
            # And the sourcing graph reaches through it, which is the reason
            # this path has to work rather than merely not crash.
            assert "GHCURL" not in _env_names(tmp_path)
        finally:
            set_file_index(None)

    def test_the_walk_path_agrees_with_the_index_path(self, tmp_path):
        # The same tree with no index registered must find the same script --
        # two discovery paths that disagree is how a repo-wide index ends up
        # depending on whether something else happened to build an index first.
        (tmp_path / "check-stable").write_text(
            "#!/bin/bash\n"
            'source ./gitlib.sh\n'
            'echo "$GHCURL" > /tmp/out\n'
        )
        (tmp_path / "gitlib.sh").write_text('GHCURL="curl -s"\n')
        # Set explicitly rather than asserting the global happens to be unset:
        # depending on another test's cleanup makes this fail for a reason that
        # has nothing to do with what it checks.
        previous = get_file_index()
        set_file_index(None)
        try:
            found = {p.name for p in find_bash_files(tmp_path)}
            assert "check-stable" in found
            assert "GHCURL" not in _env_names(tmp_path)
        finally:
            set_file_index(previous)
