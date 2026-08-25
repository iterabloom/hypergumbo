# SPDX-License-Identifier: AGPL-3.0-or-later
"""The stdlib climb: every row here was a measured hole in the shipped catalogue.

Under artifact scope the self-proof's blocker list stopped moving, and what
remained was 89 modules — every one a genuine dependency of the shipped tool.
The stdlib share of that list is not gate bookkeeping: ``os.open`` +
``os.write`` is the exact pair INV-zubuh measured writing a file while the
tool confirmed "never writes to the host filesystem". These tests pin the
classifications; the census numbers on the PR are the recall evidence.

PLACEMENT RULE (owner, 2026-08-15): stdlib rows and stdlib completeness live
in the SHIPPED CATALOGUE (``io_primitives/python.yaml``, ADR-0016 §27);
third-party lives in the project overlay; taint is written NOWHERE — both
taint halves auto-derive from these declarations (ADR-0017 §453), and a
hand-written taint entry beside them is the two-homes drift that produced
INV-potuf.

THE SLOT FAMILY RESOLVES INV-ponad BY ROWS, NOT BY A JOIN CHANGE. The filed
root-cause said teaching the (module, name) join about attributes was the fix
and rows would be "a second home for one fact". Implementing it showed the
opposite: the attribute row (``module: sys, attributes: [stdout]``) speaks
about REFERENCE edges — naming the stream is the boundary-relevant act the
tagger reads — while a method row (``module: sys.stdout, methods: [write]``)
speaks about CALL edges on the stream object. Two edge shapes, two facts, two
rows; a join change would have coupled ``classify_call`` to every catalogue's
attribute declarations to avoid writing the second fact down.
"""

from __future__ import annotations

import pytest

from hypergumbo_core.io_boundary import classify_call, load_catalog

CATALOGS = {"python": load_catalog("python")}


def _boundary(module: str, name: str) -> str | None:
    dst = f"python:{module}:0-0:{name}:external_symbol"
    primitive = classify_call(CATALOGS, dst, None)
    return primitive.boundary if primitive else None


class TestTheInvZubuhPairIsClosed:
    """The measured false-confirm: ``os.open`` + ``os.write`` wrote a file
    while the boundary claim confirmed. Both now classify."""

    def test_os_open_is_filesystem_io(self) -> None:
        assert _boundary("os", "open") in ("fs_read", "fs_write")

    def test_os_write_is_a_filesystem_write(self) -> None:
        assert _boundary("os", "write") == "fs_write"

    @pytest.mark.parametrize("name", ["fsync", "ftruncate", "truncate",
                                      "fchmod", "fchown", "sendfile"])
    def test_the_fd_write_surface_is_covered(self, name: str) -> None:
        assert _boundary("os", name) == "fs_write", name

    @pytest.mark.parametrize("name", ["read", "pread", "fdopen"])
    def test_the_fd_read_surface_is_covered(self, name: str) -> None:
        assert _boundary("os", name) == "fs_read", name

    @pytest.mark.parametrize("name", ["getuid", "getgid", "geteuid", "getcwd",
                                      "uname", "getlogin"])
    def test_host_identifying_reads_are_catalogued(self, name: str) -> None:
        """THE BOUNDARY MOVED, THE POINT DID NOT (INV-tutar). These are host and
        user IDENTIFYING reads, so they belong to ``host_info_read``; what this
        test exists to assert is that ``os``'s read surface is ENUMERATED, which
        is INV-zubuh's question and is unchanged.

        The docstring here used to explain why ``getpid`` / ``cpu_count`` /
        ``times`` were held OUT: "env_read rows auto-derive host_secret TAINT
        SOURCES and a pid is not a secret". That reasoning is what INV-tutar was
        filed about — the catalogue distorting its own membership to protect a
        downstream label — and it no longer applies, because these rows now
        derive ``host_description``. Adding those three is an ADDITION and was
        deliberately not folded into the split; the test below still pins their
        current absence rather than blessing it."""
        assert _boundary("os", name) == "host_info_read", name

    def test_a_csprng_read_is_not_an_environment_read_at_all(self) -> None:
        """``os.getrandom`` was in the list above and was REMOVED by INV-tutar's
        split rather than moved. It is not an environment read under any
        reading, and the same file already keeps ``os.urandom`` out because
        hypergumbo ships a HAND-WRITTEN taint source for it and an auto-derived
        twin DISPLACES that — measured tripping the INV-faput caveat on every
        python repo (rc 0 fixtures went rc 3)."""
        assert _boundary("os", "getrandom") is None
        assert _boundary("os", "urandom") is None
        assert CATALOGS["python"].module_io_is_enumerated("os")

    @pytest.mark.parametrize("name", ["getpid", "cpu_count"])
    def test_inert_process_state_is_not_a_taint_source(self, name: str) -> None:
        assert _boundary("os", name) is None, name
        assert CATALOGS["python"].module_io_is_enumerated("os")

    @pytest.mark.parametrize("name", ["umask", "chdir"])
    def test_process_state_mutation_is_env_write(self, name: str) -> None:
        assert _boundary("os", name) == "env_write", name

    def test_signalling_another_process_is_ipc(self) -> None:
        assert _boundary("os", "kill") == "ipc_send"


class TestTheSlotFamily:
    """INV-ponad: ``os.environ.get`` / ``sys.stdout.write`` carried the
    ATTRIBUTE as their module slot, so the parent's attribute row could not
    reach them and the most-catalogued names in the file read as blindness."""

    def test_reading_the_environment_through_its_mapping(self) -> None:
        assert _boundary("os.environ", "get") == "env_read"
        assert _boundary("os.environ", "keys") == "env_read"
        assert _boundary("os.environ", "copy") == "env_read"

    def test_mutating_the_environment_through_its_mapping(self) -> None:
        assert _boundary("os.environ", "setdefault") == "env_write"
        assert _boundary("os.environ", "pop") == "env_write"
        assert _boundary("os.environ", "update") == "env_write"

    def test_writing_the_standard_streams_is_logging(self) -> None:
        assert _boundary("sys.stdout", "write") == "logging"
        assert _boundary("sys.stderr", "write") == "logging"
        assert _boundary("sys.stdout", "flush") == "logging"

    def test_reading_stdin_is_ipc_recv(self) -> None:
        assert _boundary("sys.stdin", "read") == "ipc_recv"
        assert _boundary("sys.stdin", "readline") == "ipc_recv"


class TestTheFileObjectWrites:
    """WI-fuvuj's synthetic ``file`` module catalogued the READ half only, so
    ``f = open(p); f.write(secret)`` classified the open and lost the write."""

    @pytest.mark.parametrize("name", ["write", "writelines", "truncate"])
    def test_writes_through_a_typed_file_object(self, name: str) -> None:
        assert _boundary("file", name) == "fs_write", name

    def test_flush_is_deliberately_not_a_write_row(self) -> None:
        """flush commits writes ALREADY counted; a row for it made every bare
        .flush() in runtime code read as a direct unwrapped fs-write (the
        wrapper-discipline gate caught cli.py within the run)."""
        assert _boundary("file", "flush") is None


class TestPathlibPathSurface:
    def test_path_open_is_filesystem_io(self) -> None:
        assert _boundary("pathlib.Path", "open") in ("fs_read", "fs_write")

    @pytest.mark.parametrize("name", ["resolve", "readlink", "samefile",
                                      "lstat", "owner", "group", "walk"])
    def test_the_fs_inspection_surface(self, name: str) -> None:
        assert _boundary("pathlib.Path", name) == "fs_read", name

    @pytest.mark.parametrize("name", ["hardlink_to", "lchmod"])
    def test_the_fs_mutation_surface(self, name: str) -> None:
        assert _boundary("pathlib.Path", name) == "fs_write", name

    @pytest.mark.parametrize("name", ["cwd", "home", "expanduser"])
    def test_process_and_user_state_reads(self, name: str) -> None:
        """``host_info_read`` since INV-tutar: ``Path.cwd`` / ``Path.home`` /
        ``expanduser`` describe the host, they do not read configuration that
        could hold a credential. The assertion this test exists for — that the
        rows are CATALOGUED at all — is unchanged."""
        assert _boundary("pathlib.Path", name) == "host_info_read", name


class TestThePrinters:
    """``logging`` covers stdout/stderr in this vocabulary, so a module that
    prints does I/O — the rule that decided most refusals in the survey. These
    rows are what let those modules stop blocking HONESTLY."""

    def test_warnings_warn_reaches_stderr(self) -> None:
        assert _boundary("warnings", "warn") == "logging"

    def test_typing_reveal_type_reaches_stderr(self) -> None:
        """The probe's own catch: the one I/O surface in ``typing``."""
        assert _boundary("typing", "reveal_type") == "logging"

    def test_argparse_help_and_error_output(self) -> None:
        assert _boundary("argparse.ArgumentParser", "print_help") == "logging"
        assert _boundary("argparse.ArgumentParser", "error") == "logging"

    def test_argparse_parse_args_reads_argv(self) -> None:
        assert _boundary("argparse.ArgumentParser", "parse_args") == "env_read"

    def test_logging_module_level_emitters(self) -> None:
        assert _boundary("logging", "info") == "logging"
        assert _boundary("logging", "error") == "logging"
        assert _boundary("logging", "basicConfig") == "logging"


class TestArchivesAndImports:
    @pytest.mark.parametrize("module,name", [
        ("gzip", "open"), ("tarfile", "open"), ("zipfile", "ZipFile"),
    ])
    def test_archive_openers_are_filesystem_io(self, module, name) -> None:
        assert _boundary(module, name) in ("fs_read", "fs_write"), (module, name)

    @pytest.mark.parametrize("module,name", [
        ("importlib", "import_module"), ("importlib", "reload"),
        ("importlib.util", "find_spec"),
        ("importlib.util", "spec_from_file_location"),
        ("importlib.metadata", "version"), ("importlib.metadata", "metadata"),
        ("importlib.metadata", "distribution"),
        ("importlib.metadata", "entry_points"),
        ("inspect", "getsource"), ("inspect", "getsourcefile"),
        ("xml.etree.ElementTree", "parse"),
    ])
    def test_disk_reading_machinery(self, module, name) -> None:
        assert _boundary(module, name) == "fs_read", (module, name)

    @pytest.mark.parametrize("module,name", [
        ("pwd", "getpwuid"), ("pwd", "getpwnam"), ("grp", "getgrgid"),
        ("platform", "platform"),
    ])
    def test_host_databases_are_catalogued_as_host_info(self, module, name) -> None:
        """``pwd`` / ``grp`` / ``platform`` read the host's user and platform
        databases. INV-tutar moved them to ``host_info_read``: a username is
        identifying, which is a privacy question, and it is not the credential
        question ``host_secret`` names."""
        assert _boundary(module, name) == "host_info_read", (module, name)

    def test_fcntl_locks_and_fd_control(self) -> None:
        assert _boundary("fcntl", "flock") == "fs_write"
        assert _boundary("fcntl", "fcntl") == "fs_write"

    def test_asyncio_subprocess_surface(self) -> None:
        """asyncio carried net rows and NO subprocess rows — the same
        one-boundary-vouches-for-all shape as INV-zubuh's ``os``."""
        assert _boundary("asyncio", "create_subprocess_exec") == "subprocess"
        assert _boundary("asyncio", "create_subprocess_shell") == "subprocess"


class TestTheCompletenessTail:
    """Modules whose calls are pure compute at their slot: a dated
    closed-world entry, not rows. The probe from the survey ran over each."""

    @pytest.mark.parametrize("module", [
        "pathlib", "sys.modules", "urllib", "concurrent.futures",
        "secrets", "random", "uuid", "resource", "signal", "posixpath",
        "typing", "base64", "shlex", "contextlib", "warnings", "argparse",
        "io", "sqlite3.Connection",
    ])
    def test_is_enumerated(self, module: str) -> None:
        assert CATALOGS["python"].module_io_is_enumerated(module), module
