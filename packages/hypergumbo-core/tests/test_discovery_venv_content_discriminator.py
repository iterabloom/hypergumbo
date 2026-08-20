# SPDX-License-Identifier: AGPL-3.0-or-later
"""A directory is a virtualenv because of what it CONTAINS, not what it is called.

WHAT WAS BROKEN. ``DEFAULT_EXCLUDES`` carried the bare names ``env`` / ``venv`` /
``.venv``, and ``_is_excluded_classified`` matches an exact name against **every
component of the relative path at any depth**. So every directory named ``env``
anywhere in any repository was deleted from the analysis — including first-party
source packages that merely share the name.

MEASURED, across 467 corpus repositories:

    427 source files, in 77 directories, across 39 repositories (8.4%)
    425 of 427 (99.5%) classified FIRST_PARTY by supply_chain.classify_file
    0 of the 110 venv-NAMED directories in the corpus carry any virtualenv marker

The two worst instances are whole subsystems, not stray files: ``rocksdb/env/``
(``env_posix.cc``, ``fs_posix.cc``, ``io_posix.cc``, ``file_system.cc``) is RocksDB's
entire POSIX filesystem/IO abstraction layer, and ``qemu-u-boot/env/`` (``env.c``,
``ext4.c``, ``fat.c``, ``mmc.c``, ``nand.c``, ``flash.c``) is U-Boot's environment
storage subsystem. On poetry, the deleted package is its **virtualenv manager** —
re-including it raises unique I/O boundary chains 70 → 107 (+52.9%) with zero chains
lost. In every case the deleted code is unusually I/O-dense, which is the opposite of
what an exclusion list should be removing from a boundary analysis.

WHY THE CONTENT TEST RATHER THAN ROOT-ANCHORING. Anchoring the pattern to the repo
root (``env/*``, which the existing matcher already supports) recovers 379 of 427
(88.8%) and **structurally cannot** recover the two worst cases: rocksdb's and
U-Boot's ``env/`` both sit AT the repo root, exactly where a virtualenv would. Only a
content test separates them. PEP 405 specifies ``pyvenv.cfg`` at a virtualenv root;
older tooling wrote only ``bin/activate``, and Windows writes ``Scripts/activate``.

DIRECTION, STATED. This ADDS analyzable source, so it can add findings and add
analysis time. It cannot delete a finding.

THE REGRESSION TO FEAR is the mirror image — re-including a real virtualenv drags in
thousands of dependency files. ``test_a_real_virtualenv_is_still_excluded`` and
``test_hypergumbo_own_venv_is_still_excluded`` pin that door shut; the latter runs
against this repository's actual ``.venv`` rather than a fixture, because the
corpus that supplied the 0-of-110 figure contains **no installed virtualenvs at
all** (467 bare clones, never pip-installed), so that zero is partly a property of
the corpus and cannot be the only evidence.

HONEST GAP: the ``Scripts/activate`` (Windows) arm has no real-world positive
control — zero corpus hits and no Windows virtualenv on the machine this was
measured on. It is pinned by fixture below and is not claimed to be validated.
"""

from pathlib import Path

import pytest

from hypergumbo_core.discovery import DEFAULT_EXCLUDES, is_excluded


def _venv(root: Path, name: str, marker: str) -> Path:
    """Build a directory that looks like a virtualenv via ``marker``."""
    d = root / name
    (d / "lib" / "python3.11" / "site-packages").mkdir(parents=True)
    (d / "lib" / "python3.11" / "site-packages" / "dep.py").write_text("x = 1\n")
    target = d / marker
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("home = /usr\n")
    return d


def _source_pkg(root: Path, rel: str) -> Path:
    """Build a first-party package at ``rel`` with no virtualenv marker."""
    d = root / rel
    d.mkdir(parents=True)
    (d / "__init__.py").write_text("")
    (d / "base.py").write_text("import os\n")
    return d


class TestFirstPartyPackagesNamedEnvAreAnalyzed:
    """The 427 files. Each shape below is a real corpus instance."""

    def test_nested_source_package_named_env(self, tmp_path: Path) -> None:
        """poetry: ``src/poetry/utils/env/`` — its virtualenv MANAGER, not a venv."""
        pkg = _source_pkg(tmp_path, "src/poetry/utils/env")
        assert is_excluded(pkg / "base.py", tmp_path) is False

    def test_root_level_source_directory_named_env(self, tmp_path: Path) -> None:
        """rocksdb ``env/`` and qemu-u-boot ``env/`` sit at depth 0 — the two cases
        root-anchoring structurally cannot recover."""
        pkg = _source_pkg(tmp_path, "env")
        assert is_excluded(pkg / "base.py", tmp_path) is False

    def test_deeply_nested_source_package_named_env(self, tmp_path: Path) -> None:
        """spring-boot: ``core/spring-boot/src/main/java/org/springframework/boot/env``."""
        pkg = _source_pkg(tmp_path, "a/b/c/d/e/f/env")
        assert is_excluded(pkg / "base.py", tmp_path) is False

    def test_the_directory_itself_is_not_excluded(self, tmp_path: Path) -> None:
        """Discovery prunes directories, so the directory must clear the check too —
        excluding it would delete the files without ever testing them."""
        pkg = _source_pkg(tmp_path, "src/env")
        assert is_excluded(pkg, tmp_path) is False


class TestRealVirtualenvsStayExcluded:
    """The regression this fix must not cause. A virtualenv holds thousands of
    dependency files; pulling them into analysis is worse than the bug being fixed."""

    @pytest.mark.parametrize("marker", ["pyvenv.cfg", "bin/activate"])
    @pytest.mark.parametrize("name", ["env", "venv", ".venv"])
    def test_a_real_virtualenv_is_still_excluded(
        self, tmp_path: Path, name: str, marker: str,
    ) -> None:
        v = _venv(tmp_path, name, marker)
        assert is_excluded(v, tmp_path) is True
        assert is_excluded(
            v / "lib" / "python3.11" / "site-packages" / "dep.py", tmp_path,
        ) is True

    def test_windows_scripts_activate_marker(self, tmp_path: Path) -> None:
        """PINNED WITHOUT A REAL-WORLD CONTROL. Zero corpus hits and no Windows
        virtualenv was available to measure against, so this arm is asserted by
        fixture only and is NOT claimed to be validated."""
        v = _venv(tmp_path, "venv", "Scripts/activate")
        assert is_excluded(v, tmp_path) is True

    def test_hypergumbo_own_venv_is_still_excluded(self) -> None:
        """Runs against this repository's REAL .venv, not a fixture.

        Load-bearing because the corpus that produced "0 of 110 venv-named dirs carry
        a marker" contains no installed virtualenvs at all — 467 bare clones — so that
        zero is partly a property of the corpus. This is a live positive control on
        the arm that protects against a 26,882-file regression."""
        repo_root = Path(__file__).resolve().parents[3]
        venv = repo_root / ".venv"
        if not venv.is_dir():  # pragma: no cover - dev machines without a local venv
            pytest.skip("no .venv in this checkout")
        assert is_excluded(venv, repo_root) is True

    def test_a_venv_nested_below_the_root_is_still_excluded(
        self, tmp_path: Path,
    ) -> None:
        """A virtualenv is not always at depth 0 (``backend/venv`` is idiomatic), so
        the content test stays depth-agnostic — that is the whole reason it beats
        root-anchoring."""
        v = _venv(tmp_path / "backend", "venv", "pyvenv.cfg")
        assert is_excluded(v / "lib" / "python3.11" / "site-packages" / "dep.py",
                           tmp_path) is True


class TestTheRestOfTheExcludeListIsUntouched:
    """Blast radius control. Only the venv-named entries change behaviour; the other
    53 exact names keep matching at any depth. ``vendor`` is called out because it was
    measured: 19,251 of its files sit BELOW the root and are genuine dependencies, so
    it must stay unanchored and unconditioned."""

    @pytest.mark.parametrize("name", ["node_modules", "vendor", "__pycache__", ".git"])
    def test_other_excludes_still_match_at_any_depth(
        self, tmp_path: Path, name: str,
    ) -> None:
        d = _source_pkg(tmp_path, f"src/deep/{name}")
        assert is_excluded(d / "base.py", tmp_path) is True

    def test_vendor_below_root_stays_excluded_without_a_content_test(
        self, tmp_path: Path,
    ) -> None:
        """kata-containers ``src/runtime/vendor`` — 3,282 genuine dependency files.
        No marker required, and none must be demanded."""
        d = _source_pkg(tmp_path, "src/runtime/vendor")
        assert is_excluded(d / "base.py", tmp_path) is True

    def test_venv_names_are_no_longer_bare_entries_in_the_default_list(self) -> None:
        """They moved from name-matching to content-conditioned matching. If a later
        edit puts them back as bare names, the 427 files vanish again silently."""
        assert "env" not in DEFAULT_EXCLUDES
        assert "venv" not in DEFAULT_EXCLUDES
        assert ".venv" not in DEFAULT_EXCLUDES

    def test_an_unrelated_name_containing_env_is_unaffected(
        self, tmp_path: Path,
    ) -> None:
        """``myenv`` and ``environment.py`` never matched and still must not."""
        d = _source_pkg(tmp_path, "myenv")
        assert is_excluded(d / "base.py", tmp_path) is False
        (tmp_path / "environment.py").write_text("")
        assert is_excluded(tmp_path / "environment.py", tmp_path) is False


class TestAllWalkersAgree:
    """PARITY. This module has two independent tree walkers, and adding the venv rule
    to only one of them produced exactly the split it was written to prevent:
    ``find_files`` excluded a virtualenv correctly while a full ``run_behavior_map``
    still analysed it — same input, two answers. Caught by the suite, not by review.

    Asserted BEHAVIOURALLY rather than structurally: a test that greps for a call to
    the shared helper can be satisfied by a fourth copy that merely looks right.
    These run the actual entry points and compare their answers."""

    @pytest.mark.parametrize(
        "marker,expected_excluded",
        [(None, False), ("pyvenv.cfg", True)],
        ids=["source-package", "real-virtualenv"],
    )
    def test_all_three_walkers_agree(
        self, tmp_path: Path, marker: str | None, expected_excluded: bool,
    ) -> None:
        from hypergumbo_core.discovery import FileIndex, find_files

        (tmp_path / "app.py").write_text("def main(): pass\n")
        d = tmp_path / "env"
        d.mkdir()
        (d / "mod.py").write_text("def inner(): pass\n")
        if marker is not None:
            (d / marker).write_text("home = /usr\n")
        target = d / "mod.py"

        by_is_excluded = is_excluded(target, tmp_path)
        by_find_files = target not in set(find_files(tmp_path, ["*.py"]))
        by_index = target not in set(FileIndex.build(tmp_path).all_files())

        assert by_is_excluded is expected_excluded
        assert by_find_files is expected_excluded
        assert by_index is expected_excluded
        assert by_is_excluded == by_find_files == by_index


class TestUserSuppliedExcludesKeepNameSemantics:
    """An explicit ``--exclude env`` is a direct instruction and is obeyed literally.
    The content test exists to fix a DEFAULT the user never asked for; silently
    second-guessing an explicit flag would be a different bug."""

    def test_explicit_exclude_of_env_is_obeyed_without_a_marker(
        self, tmp_path: Path,
    ) -> None:
        pkg = _source_pkg(tmp_path, "src/env")
        assert is_excluded(pkg / "base.py", tmp_path, excludes=["env"]) is True
