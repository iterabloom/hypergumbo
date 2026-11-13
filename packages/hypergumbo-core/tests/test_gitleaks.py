# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for gitleaks integration.

This module tests the secret scanning functionality, including:
- Detection of gitleaks availability
- Scanning content for secrets
- Warning formatting
- CLI integration
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from hypergumbo_core import gitleaks
from hypergumbo_core.gitleaks import (
    SecretFinding,
    format_secret_warning,
    get_install_nag,
    get_gitleaks_path,
    is_gitleaks_available,
    install_gitleaks,
    uninstall_gitleaks,
    _scan,
    _get_platform_arch,
    _get_download_url,
)


class TestGitleaksAvailability:
    """Tests for checking gitleaks availability."""

    def test_is_gitleaks_available_when_in_path(self) -> None:
        """Returns True when gitleaks is in system PATH."""
        with patch("shutil.which", return_value="/usr/bin/gitleaks"):
            with patch.object(gitleaks, "GITLEAKS_PATH") as mock_path:
                mock_path.exists.return_value = False
                assert is_gitleaks_available() is True

    def test_is_gitleaks_available_when_installed_locally(self) -> None:
        """Returns True when gitleaks is in ~/.local/bin."""
        with patch.object(gitleaks, "GITLEAKS_PATH") as mock_path:
            mock_path.exists.return_value = True
            assert is_gitleaks_available() is True

    def test_is_gitleaks_available_when_not_found(self) -> None:
        """Returns False when gitleaks is not found anywhere."""
        with patch("shutil.which", return_value=None):
            with patch.object(gitleaks, "GITLEAKS_PATH") as mock_path:
                mock_path.exists.return_value = False
                assert is_gitleaks_available() is False

    def test_get_gitleaks_path_prefers_local(self) -> None:
        """Prefers local installation over system PATH."""
        with patch.object(gitleaks, "GITLEAKS_PATH") as mock_path:
            mock_path.exists.return_value = True
            result = get_gitleaks_path()
            assert result == mock_path

    def test_get_gitleaks_path_falls_back_to_system(self) -> None:
        """Falls back to system PATH when local not found."""
        with patch("shutil.which", return_value="/usr/bin/gitleaks"):
            with patch.object(gitleaks, "GITLEAKS_PATH") as mock_path:
                mock_path.exists.return_value = False
                result = get_gitleaks_path()
                assert result == Path("/usr/bin/gitleaks")

    def test_get_gitleaks_path_returns_none_when_not_found(self) -> None:
        """Returns None when gitleaks not found."""
        with patch("shutil.which", return_value=None):
            with patch.object(gitleaks, "GITLEAKS_PATH") as mock_path:
                mock_path.exists.return_value = False
                result = get_gitleaks_path()
                assert result is None


class TestPlatformDetection:
    """Tests for platform/architecture detection."""

    def test_get_platform_arch_linux_x64(self) -> None:
        """Detects Linux x86_64."""
        with patch("platform.system", return_value="Linux"):
            with patch("platform.machine", return_value="x86_64"):
                plat, arch = _get_platform_arch()
                assert plat == "linux"
                assert arch == "x64"

    def test_get_platform_arch_darwin_arm64(self) -> None:
        """Detects macOS arm64."""
        with patch("platform.system", return_value="Darwin"):
            with patch("platform.machine", return_value="arm64"):
                plat, arch = _get_platform_arch()
                assert plat == "darwin"
                assert arch == "arm64"

    def test_get_platform_arch_windows_x64(self) -> None:
        """Detects Windows x86_64."""
        with patch("platform.system", return_value="Windows"):
            with patch("platform.machine", return_value="AMD64"):
                plat, arch = _get_platform_arch()
                assert plat == "windows"
                assert arch == "x64"


class TestScanContent:
    """Tests for scanning content for secrets."""

    def test_scan_content_returns_empty_when_gitleaks_unavailable(self) -> None:
        """Not scanned (None) when gitleaks is not installed."""
        with patch.object(gitleaks, "get_gitleaks_path", return_value=None):
            result = _scan("some content with API_KEY=secret123")
            assert result is None

    def test_scan_content_parses_findings(self) -> None:
        """Parses gitleaks JSON output into findings."""
        mock_output = json.dumps([
            {
                "File": "config.py",
                "StartLine": 12,
                "RuleID": "generic-api-key",
                "Description": "Generic API Key",
                "Match": "API_KEY=secret123",
            }
        ])

        mock_result = MagicMock()
        mock_result.stdout = mock_output.encode()
        mock_result.returncode = 0

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", return_value=mock_result):
                findings = _scan("API_KEY=secret123")

        assert len(findings) == 1
        assert findings[0].file == "config.py"
        assert findings[0].line == 12
        assert findings[0].rule_id == "generic-api-key"

    def test_scan_content_handles_no_findings(self) -> None:
        """Returns empty list when no secrets found."""
        mock_result = MagicMock()
        mock_result.stdout = b"null"
        mock_result.returncode = 0

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", return_value=mock_result):
                findings = _scan("safe content")

        assert findings == []

    def test_scan_content_handles_empty_output(self) -> None:
        """Returns empty list when gitleaks outputs nothing."""
        mock_result = MagicMock()
        mock_result.stdout = b""
        mock_result.returncode = 0

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", return_value=mock_result):
                findings = _scan("safe content")

        assert findings == []

    def test_scan_content_handles_timeout(self) -> None:
        """Not scanned (None) on timeout."""
        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gitleaks", 30)):
                findings = _scan("content")

        assert findings is None

    def test_scan_content_handles_subprocess_error(self) -> None:
        """Not scanned (None) on a subprocess error."""
        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", side_effect=subprocess.SubprocessError("failed")):
                findings = _scan("content")

        assert findings is None

    def test_scan_content_handles_invalid_json(self) -> None:
        """Not scanned (None) on output that is not JSON."""
        mock_result = MagicMock()
        mock_result.stdout = b"not valid json"
        mock_result.returncode = 0

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", return_value=mock_result):
                findings = _scan("content")

        assert findings is None


class TestFormatting:
    """Tests for warning message formatting."""

    def test_format_secret_warning_single_finding(self) -> None:
        """Formats single finding correctly."""
        findings = [
            SecretFinding(
                file="config.py",
                line=12,
                rule_id="generic-api-key",
                description="Generic API Key",
                match="secret",
            )
        ]
        result = format_secret_warning(findings)

        assert "1 finding" in result
        assert "config.py:12" in result
        assert "generic-api-key" in result
        assert "best-effort" in result

    def test_format_secret_warning_multiple_findings(self) -> None:
        """Formats multiple findings correctly."""
        findings = [
            SecretFinding(file=f"file{i}.py", line=i, rule_id="rule", description="", match="")
            for i in range(3)
        ]
        result = format_secret_warning(findings)

        assert "3 findings" in result

    def test_format_secret_warning_truncates_long_list(self) -> None:
        """Truncates list when more than 10 findings."""
        findings = [
            SecretFinding(file=f"file{i}.py", line=i, rule_id="rule", description="", match="")
            for i in range(15)
        ]
        result = format_secret_warning(findings)

        assert "15 findings" in result
        assert "... and 5 more" in result

    def test_get_install_nag(self) -> None:
        """Returns install suggestion message."""
        result = get_install_nag()

        assert "hypergumbo install-gitleaks" in result
        assert "unavailable" in result.lower()


class TestDownloadUrl:
    """The download is pinned to one release and carries that release's hash."""

    def test_the_url_names_the_pinned_release_without_asking_the_network(self) -> None:
        """No "latest" lookup: the URL is built from the pinned version, so
        nothing is fetched before the archive itself."""
        with patch("hypergumbo_core.gitleaks.urlopen", side_effect=AssertionError("network")), \
                patch("platform.system", return_value="Linux"), \
                patch("platform.machine", return_value="x86_64"):
            url, filename, sha256 = _get_download_url()

        assert filename == "gitleaks_8.30.0_linux_x64.tar.gz"
        assert url == ("https://github.com/gitleaks/gitleaks/releases/download/"
                       "v8.30.0/gitleaks_8.30.0_linux_x64.tar.gz")
        # From gitleaks_8.30.0_checksums.txt, published with the release.
        assert sha256 == "79a3ab579b53f71efd634f3aaf7e04a0fa0cf206b7ed434638d1547a2470a66e"

    def test_a_platform_the_release_does_not_ship_is_refused(self) -> None:
        with patch("platform.system", return_value="Windows"), \
                patch("platform.machine", return_value="arm64"):
            with pytest.raises(RuntimeError, match=r"No gitleaks 8\.30\.0 release for windows/arm64"):
                _get_download_url()

    def test_every_hash_belongs_to_the_pinned_version(self) -> None:
        for name, digest in gitleaks.GITLEAKS_ARCHIVE_SHA256.items():
            assert name.startswith(f"gitleaks_{gitleaks.GITLEAKS_VERSION}_"), name
            assert len(digest) == 64 and int(digest, 16) >= 0, name


class TestInstallGitleaks:
    """Tests for gitleaks installation."""

    def test_install_handles_download_url_error(self) -> None:
        """Returns False when unable to get download URL."""
        with patch.object(gitleaks, "_get_download_url", side_effect=RuntimeError("network error")):
            result = install_gitleaks(quiet=True)

        assert result is False

    def test_install_handles_download_error(self) -> None:
        """Returns False when download fails."""
        with patch.object(gitleaks, "_get_download_url", return_value=("https://example.com/file.tar.gz", "file.tar.gz", "0" * 64)):
            with patch("hypergumbo_core.gitleaks.urlopen", side_effect=URLError("download failed")):
                result = install_gitleaks(quiet=True)

        assert result is False

    def test_each_install_write_lands_in_the_zone_its_wrapper_names(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """verify-claims reads a write's zone from the wrapper's NAME, never its path.

        So every wrapper the install calls must name the zone its path lands
        in: tmp_artifact_* inside the extraction dir, install_artifact_*
        inside the install dir. The downloaded archive lands in the
        extraction dir, so it must go through a tmp_artifact wrapper, not an
        install_artifact one.
        """
        import io
        import tarfile
        from contextlib import contextmanager

        payload = b"#!/bin/sh\n"
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo(gitleaks.GITLEAKS_BINARY_NAME)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        install_dir = tmp_path / ".local" / "bin"
        monkeypatch.setattr(gitleaks, "GITLEAKS_INSTALL_DIR", install_dir)
        monkeypatch.setattr(gitleaks, "GITLEAKS_PATH", install_dir / gitleaks.GITLEAKS_BINARY_NAME)

        calls: list[tuple[str, Path]] = []
        scratch: list[Path] = []
        real_dir = gitleaks.tmp_artifact_dir

        @contextmanager
        def recording_dir(**kwargs):
            with real_dir(**kwargs) as d:
                scratch.append(Path(d))
                yield d

        def recording(name, real):
            def wrapper(*args, **kwargs):
                calls.append((name, Path(args[1] if name == "install_artifact_copy" else args[0])))
                return real(*args, **kwargs)
            return wrapper

        monkeypatch.setattr(gitleaks, "tmp_artifact_dir", recording_dir)
        for name in dir(gitleaks):
            if name.startswith(("install_artifact_", "tmp_artifact_")) and name != "tmp_artifact_dir":
                monkeypatch.setattr(gitleaks, name, recording(name, getattr(gitleaks, name)))

        response = MagicMock()
        response.__enter__.return_value.read.return_value = buf.getvalue()
        digest = hashlib.sha256(buf.getvalue()).hexdigest()
        with patch.object(gitleaks, "_get_download_url",
                          return_value=("https://example.invalid/g.tar.gz",
                                        "gitleaks_8.30.0_linux_x64.tar.gz", digest)), \
                patch.object(gitleaks, "urlopen", return_value=response):
            assert install_gitleaks(quiet=True) is True

        assert (install_dir / gitleaks.GITLEAKS_BINARY_NAME).read_bytes() == payload
        assert len(scratch) == 1 and calls, "the install never reached the wrappers"
        for name, target in calls:
            root = scratch[0] if name.startswith("tmp_artifact_") else install_dir
            assert target == root or target.is_relative_to(root), (name, target, root)
        assert ("tmp_artifact_write_bytes", scratch[0] / "gitleaks_8.30.0_linux_x64.tar.gz") in calls


    def test_an_archive_whose_hash_does_not_match_is_never_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A tampered or truncated download stops before the first write: no
        archive in the tmp zone, nothing extracted, nothing installed."""
        install_dir = tmp_path / ".local" / "bin"
        monkeypatch.setattr(gitleaks, "GITLEAKS_INSTALL_DIR", install_dir)
        monkeypatch.setattr(gitleaks, "GITLEAKS_PATH", install_dir / gitleaks.GITLEAKS_BINARY_NAME)
        writes: list[str] = []
        for name in ("tmp_artifact_write_bytes", "tmp_artifact_extract",
                     "install_artifact_mkdir", "install_artifact_copy"):
            monkeypatch.setattr(gitleaks, name, lambda *a, _n=name, **k: writes.append(_n))
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"not the release"

        with patch.object(gitleaks, "_get_download_url",
                          return_value=("https://example.invalid/g.tar.gz",
                                        "gitleaks_8.30.0_linux_x64.tar.gz", "0" * 64)), \
                patch.object(gitleaks, "urlopen", return_value=response):
            assert install_gitleaks(quiet=True) is False

        assert writes == []
        assert not install_dir.exists()
        err = capsys.readouterr().err
        assert "SHA-256" in err and "0" * 64 in err


class TestPlatformArchEdgeCases:
    """Tests for platform/architecture edge cases."""

    def test_get_platform_arch_armv7(self) -> None:
        """Detects ARM v7."""
        with patch("platform.system", return_value="Linux"):
            with patch("platform.machine", return_value="armv7l"):
                plat, arch = _get_platform_arch()
                assert arch == "armv7"

    def test_get_platform_arch_x32(self) -> None:
        """Detects 32-bit x86."""
        with patch("platform.system", return_value="Linux"):
            with patch("platform.machine", return_value="i686"):
                plat, arch = _get_platform_arch()
                assert arch == "x32"

    def test_get_platform_arch_unknown_defaults_x64(self) -> None:
        """Defaults to x64 for unknown architecture."""
        with patch("platform.system", return_value="Linux"):
            with patch("platform.machine", return_value="unknown_arch"):
                plat, arch = _get_platform_arch()
                assert arch == "x64"


class TestScanContentEdgeCases:
    """Additional edge cases for content scanning."""

    def test_scan_content_handles_os_error(self) -> None:
        """Not scanned (None) on OSError."""
        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", side_effect=OSError("permission denied")):
                findings = _scan("content")

        assert findings is None

    def test_scan_content_handles_non_list_json(self) -> None:
        """Not scanned (None) when the JSON is not a list."""
        mock_result = MagicMock()
        mock_result.stdout = json.dumps({"not": "a list"}).encode()
        mock_result.returncode = 0

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", return_value=mock_result):
                findings = _scan("content")

        assert findings is None


class TestScanContentRealBinary:
    """Real-binary integration guard for the gitleaks invocation (INV-pirad).

    Line coverage of ``_scan`` is carried by the mocked
    ``TestScanContent`` suite. This class additionally runs the ACTUAL
    gitleaks binary to catch external-CLI *contract* drift that mocked
    ``subprocess.run`` tests structurally cannot — specifically the
    gitleaks 8.30+ regression where ``detect --pipe`` silently ignores
    stdin and scans the current working directory instead, turning
    the scan into a no-op that always returns ``[]`` (INV-pirad).

    A mocked-args assertion would pass even against the broken invocation;
    only feeding a known secret through the real binary proves the scan
    actually reads stdin and discriminates. Gated on availability because
    gitleaks is an opt-in extra (``add-extras``) that base CI does not
    provision — mirrors the optional-dependency gating in
    ``hypergumbo-tracker``'s ``test_embeddings.py``.
    """

    # AWS access-token test vector (gitleaks' own ``aws-access-token``
    # rule), assembled at runtime so this source file never contains a
    # contiguous detectable secret — keeps the repo's own secret-scanners
    # quiet without touching ``.gitleaksignore``.
    _AWS_VECTOR = "AKIA" + "LALEMEL33243OLIA"

    @pytest.mark.skipif(
        not is_gitleaks_available(),
        reason="gitleaks binary not installed (opt-in extra)",
    )
    def test_real_gitleaks_detects_known_secret(self) -> None:
        """The real binary must detect a known secret fed via _scan.

        An empty result here means the scan is not reading stdin (the
        ``detect --pipe`` contract described on the class; INV-pirad).
        """
        findings = _scan(f"aws_access_key_id = {self._AWS_VECTOR}\n")
        assert findings, (
            "real gitleaks returned no findings for a known AWS test "
            "vector — the secret scan is a no-op (INV-pirad regression: "
            "gitleaks is not reading stdin)"
        )
        assert findings[0].rule_id

    @pytest.mark.skipif(
        not is_gitleaks_available(),
        reason="gitleaks binary not installed (opt-in extra)",
    )
    def test_real_gitleaks_clean_input_returns_empty(self) -> None:
        """Secret-free content yields no findings — proves the scan ran and
        discriminated, rather than returning ``[]`` because it never read
        stdin."""
        findings = _scan("def hello():\n    return 'world'\n")
        assert findings == []


    @pytest.mark.skipif(
        not is_gitleaks_available(),
        reason="gitleaks binary not installed (opt-in extra)",
    )
    def test_real_gitleaks_ignores_a_config_in_the_working_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """INV-nihab: a ``.gitleaks.toml`` where hypergumbo runs (normally the
        analysed repository) cannot allowlist a finding away."""
        (tmp_path / ".gitleaks.toml").write_text(
            '[extend]\nuseDefault = true\n[allowlist]\nregexes = ["AKIA[A-Z0-9]+"]\n'
        )
        monkeypatch.chdir(tmp_path)
        findings = _scan(f"aws_access_key_id = {self._AWS_VECTOR}\n")
        assert findings, "the working directory's .gitleaks.toml hid the finding"

    @pytest.mark.skipif(
        not is_gitleaks_available(),
        reason="gitleaks binary not installed (opt-in extra)",
    )
    def test_real_gitleaks_that_fails_is_not_a_clean_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """WI-rapuh: a gitleaks that cannot load its config exits 1 with empty
        stdout. That is a scan that did not run, not a clean one."""
        from hypergumbo_core.gitleaks import scan_content_cached

        monkeypatch.setenv("GITLEAKS_CONFIG", str(tmp_path / "no-such-config.toml"))
        content = f"aws_access_key_id = {self._AWS_VECTOR}\n"
        assert scan_content_cached(content, tmp_path) is None
        assert not (tmp_path / "secret_scan_cache.json").exists()


class TestScanRunsInADirectoryOfItsOwn:
    """INV-nihab: gitleaks auto-discovers ``.gitleaks.toml`` / ``.gitleaksignore``
    in its working directory, and removes and recreates its report path. Run
    in the inherited cwd with ``--report-path /dev/stdout``, the analysed
    repository could configure (and empty) the scan, and gitleaks would try to
    unlink ``/dev/stdout`` -- which succeeds as root. So the scan reports on
    ``-`` and runs in an empty scratch directory of its own."""

    def _invoke(self, monkeypatch: pytest.MonkeyPatch, cwd: Path) -> dict:
        (cwd / ".gitleaks.toml").write_text('[allowlist]\nregexes = [".*"]\n')
        (cwd / ".gitleaksignore").write_text("<stdin>:generic-api-key:1\n")
        monkeypatch.chdir(cwd)
        seen: dict = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = list(argv)
            seen["cwd"] = kwargs.get("cwd")
            seen["cwd_entries"] = (
                sorted(p.name for p in Path(kwargs["cwd"]).iterdir())
                if kwargs.get("cwd") else None
            )
            result = MagicMock()
            result.stdout = b"[]"
            result.returncode = 0
            return result

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", side_effect=fake_run):
                assert _scan("API_KEY=secret123") == []
        assert "argv" in seen, "gitleaks was never invoked"  # reach
        return seen

    def test_the_report_goes_to_stdout_not_dev_stdout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        argv = self._invoke(monkeypatch, tmp_path)["argv"]
        assert argv[argv.index("--report-path") + 1] == "-"
        assert "/dev/stdout" not in argv

    def test_the_scan_runs_in_an_empty_directory_that_is_not_the_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen = self._invoke(monkeypatch, tmp_path)
        assert seen["cwd"] is not None, "gitleaks inherited the caller's cwd"
        assert Path(seen["cwd"]).resolve() != tmp_path.resolve()
        assert seen["cwd_entries"] == [], seen["cwd_entries"]
        assert not Path(seen["cwd"]).exists(), "the scratch directory was left behind"


class TestScanContentCached:
    """Tests for content-hash-keyed scan caching (WI-julir)."""

    @pytest.mark.parametrize("failure", [
        OSError(13, "Permission denied"),
        subprocess.TimeoutExpired("gitleaks", 30),
        "not json",
    ])
    def test_a_scan_that_did_not_run_is_not_cached_as_clean(
        self, tmp_path: Path, failure,
    ) -> None:
        """No result is not a clean result.

        A gitleaks that cannot run (a partial install), a scan that times out,
        or output that is not JSON must not return ``[]``: that would be cached
        as "no secrets" for that content, and sketch would keep saying "Secret
        scan complete" even after gitleaks was fixed. Instead the scan reports
        that it did not run (``None``) and nothing is cached.
        """
        from hypergumbo_core.gitleaks import scan_content_cached

        found = MagicMock()
        found.stdout = json.dumps([{"RuleID": "github-pat", "StartLine": 1}]).encode()
        found.returncode = 0
        broken = MagicMock()
        broken.stdout = b"not json"
        broken.returncode = 0
        first = {"side_effect": failure} if not isinstance(failure, str) else {"return_value": broken}

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", **first):
                assert scan_content_cached("ghp_secret", tmp_path) is None
            with patch("subprocess.run", return_value=found) as mock_run:
                again = scan_content_cached("ghp_secret", tmp_path)

        assert mock_run.call_count == 1, "the failed scan was served from the cache"
        assert [f.rule_id for f in again] == ["github-pat"]

    @pytest.mark.parametrize("stdout", [b"", b"null", b"[]"])
    def test_a_gitleaks_that_exits_non_zero_is_not_a_clean_result(
        self, tmp_path: Path, stdout: bytes,
    ) -> None:
        """A non-zero exit is a failed scan, whatever stdout says.

        The scan passes ``--exit-code 0``, so findings never make gitleaks
        exit non-zero; any other exit means it failed. Gitleaks 8.30 given a
        config it cannot load exits 1 with empty stdout; reading that as "no
        secrets" would cache it as clean (WI-rapuh).
        """
        from hypergumbo_core.gitleaks import scan_content_cached

        failed = MagicMock()
        failed.stdout = stdout
        failed.returncode = 1

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", return_value=failed):
                assert scan_content_cached("content", tmp_path) is None
        assert not (tmp_path / "secret_scan_cache.json").exists()

    def test_no_gitleaks_is_not_a_clean_result(self, tmp_path: Path) -> None:
        from hypergumbo_core.gitleaks import scan_content_cached

        with patch.object(gitleaks, "get_gitleaks_path", return_value=None):
            assert scan_content_cached("ghp_secret", tmp_path) is None
        assert not (tmp_path / "secret_scan_cache.json").exists()

    def test_cached_first_call_invokes_gitleaks(self, tmp_path: Path) -> None:
        """First call with a given content hash actually scans."""
        from hypergumbo_core.gitleaks import scan_content_cached

        mock_output = json.dumps([
            {
                "File": "<stdin>",
                "StartLine": 1,
                "RuleID": "test",
                "Description": "Test",
                "Match": "secret",
            }
        ])
        mock_result = MagicMock()
        mock_result.stdout = mock_output.encode()
        mock_result.returncode = 0

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", return_value=mock_result) as mock_run:
                findings = scan_content_cached("API_KEY=abc", tmp_path)

        assert mock_run.call_count == 1
        assert len(findings) == 1
        assert findings[0].rule_id == "test"

    def test_cached_second_call_skips_gitleaks(self, tmp_path: Path) -> None:
        """Second call with identical content returns cached result without subprocess."""
        from hypergumbo_core.gitleaks import scan_content_cached

        mock_output = json.dumps([
            {
                "File": "<stdin>",
                "StartLine": 1,
                "RuleID": "test",
                "Description": "Test",
                "Match": "secret",
            }
        ])
        mock_result = MagicMock()
        mock_result.stdout = mock_output.encode()
        mock_result.returncode = 0

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", return_value=mock_result) as mock_run:
                first = scan_content_cached("API_KEY=abc", tmp_path)
                second = scan_content_cached("API_KEY=abc", tmp_path)

        assert mock_run.call_count == 1, "second call must hit cache"
        assert [f.rule_id for f in first] == [f.rule_id for f in second]
        assert [f.line for f in first] == [f.line for f in second]

    def test_cached_different_content_misses_cache(self, tmp_path: Path) -> None:
        """Different content hash bypasses the cache and rescans."""
        from hypergumbo_core.gitleaks import scan_content_cached

        mock_result = MagicMock()
        mock_result.stdout = b"null"
        mock_result.returncode = 0

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", return_value=mock_result) as mock_run:
                scan_content_cached("content A", tmp_path)
                scan_content_cached("content B", tmp_path)

        assert mock_run.call_count == 2

    def test_cached_no_cache_dir_falls_back(self) -> None:
        """When cache_dir is None, behaves like uncached scan_content."""
        from hypergumbo_core.gitleaks import scan_content_cached

        mock_result = MagicMock()
        mock_result.stdout = b"null"
        mock_result.returncode = 0

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", return_value=mock_result) as mock_run:
                scan_content_cached("content", None)
                scan_content_cached("content", None)

        assert mock_run.call_count == 2  # no cache means no skip

    def test_cached_evicts_oldest_when_full(self, tmp_path: Path) -> None:
        """Cache is bounded; oldest entries are evicted past max_entries."""
        from hypergumbo_core.gitleaks import scan_content_cached

        mock_result = MagicMock()
        mock_result.stdout = b"null"
        mock_result.returncode = 0

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", return_value=mock_result):
                # Fill cache past the limit
                for i in range(5):
                    scan_content_cached(f"content {i}", tmp_path, max_entries=3)

        cache_file = tmp_path / "secret_scan_cache.json"
        cache = json.loads(cache_file.read_text())
        assert len(cache) == 3, "cache must respect max_entries"

    def test_cached_corrupt_cache_file_recovers(self, tmp_path: Path) -> None:
        """A corrupt cache file is treated as empty, scan proceeds."""
        from hypergumbo_core.gitleaks import scan_content_cached

        cache_file = tmp_path / "secret_scan_cache.json"
        cache_file.write_text("{not json")

        mock_result = MagicMock()
        mock_result.stdout = b"null"
        mock_result.returncode = 0

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", return_value=mock_result) as mock_run:
                findings = scan_content_cached("content", tmp_path)

        assert findings == []
        assert mock_run.call_count == 1

    def test_cached_round_trip_preserves_finding_fields(self, tmp_path: Path) -> None:
        """All SecretFinding fields survive the cache serialization round-trip."""
        from hypergumbo_core.gitleaks import scan_content_cached

        mock_output = json.dumps([
            {
                "File": "config.py",
                "StartLine": 42,
                "RuleID": "aws-key",
                "Description": "AWS Access Key",
                "Match": "AKIAIOSFODNN7EXAMPLE",
            }
        ])
        mock_result = MagicMock()
        mock_result.stdout = mock_output.encode()
        mock_result.returncode = 0

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", return_value=mock_result):
                scan_content_cached("AKIAIOSFODNN7EXAMPLE", tmp_path)
            # Now serve from cache (subprocess patched away → would error if called)
            cached = scan_content_cached("AKIAIOSFODNN7EXAMPLE", tmp_path)

        assert len(cached) == 1
        f = cached[0]
        assert f.file == "config.py"
        assert f.line == 42
        assert f.rule_id == "aws-key"
        assert f.description == "AWS Access Key"
        assert f.match == "AKIAIOSFODNN7EXAMPLE"


class TestSketchSecretScanning:
    """Tests for secret scanning integration in sketch command."""

    def test_sketch_shows_nag_when_gitleaks_unavailable(self, tmp_path: Path, capsys) -> None:
        """Sketch shows install nag when gitleaks not available."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_sketch

        # Create a minimal repo
        (tmp_path / "main.py").write_text("print('hello')")

        args = Namespace(
            path=str(tmp_path),
            tokens=100,
            exclude_tests=True,
            first_party_priority=True,
            extra_excludes=[],
            config_extraction_mode="heuristic",
            verbose=False,
            progress=False,
            readme_debug=False,
            max_config_files=5,
            fleximax_lines=50,
            max_chunk_chars=400,
            language_proportional=False,
            with_source=False,
            input=None,
            no_secret_scan=False,
        )

        with patch("hypergumbo_core.cli.is_gitleaks_available", return_value=False):
            cmd_sketch(args)

        captured = capsys.readouterr()
        assert "install-gitleaks" in captured.err

    def test_sketch_shows_scan_complete_when_no_secrets(self, tmp_path: Path, capsys) -> None:
        """Sketch shows 'scan complete' when gitleaks finds nothing."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_sketch

        # Create a minimal repo
        (tmp_path / "main.py").write_text("print('hello')")

        args = Namespace(
            path=str(tmp_path),
            tokens=100,
            exclude_tests=True,
            first_party_priority=True,
            extra_excludes=[],
            config_extraction_mode="heuristic",
            verbose=False,
            progress=False,
            readme_debug=False,
            max_config_files=5,
            fleximax_lines=50,
            max_chunk_chars=400,
            language_proportional=False,
            with_source=False,
            input=None,
            no_secret_scan=False,
        )

        with patch("hypergumbo_core.cli.is_gitleaks_available", return_value=True):
            with patch("hypergumbo_core.cli.scan_content_cached", return_value=[]):
                cmd_sketch(args)

        captured = capsys.readouterr()
        assert "best-effort" in captured.err

    def test_sketch_says_so_when_the_scan_did_not_run(self, tmp_path: Path, capsys) -> None:
        """A scan that did not run must not be reported as complete."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_sketch

        (tmp_path / "main.py").write_text("print('hello')")
        args = Namespace(
            path=str(tmp_path), tokens=100, exclude_tests=True,
            first_party_priority=True, extra_excludes=[],
            config_extraction_mode="heuristic", verbose=False, progress=False,
            readme_debug=False, max_config_files=5, fleximax_lines=50,
            max_chunk_chars=400, language_proportional=False, with_source=False,
            input=None, no_secret_scan=False,
        )

        with patch("hypergumbo_core.cli.is_gitleaks_available", return_value=True):
            with patch("hypergumbo_core.cli.scan_content_cached", return_value=None):
                cmd_sketch(args)

        err = capsys.readouterr().err
        assert "Secret scan complete" not in err
        assert "NOT checked for secrets" in err

    def test_sketch_shows_warning_when_secrets_found(self, tmp_path: Path, capsys) -> None:
        """Sketch shows warning when gitleaks finds secrets."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_sketch

        # Create a minimal repo
        (tmp_path / "main.py").write_text("print('hello')")

        args = Namespace(
            path=str(tmp_path),
            tokens=100,
            exclude_tests=True,
            first_party_priority=True,
            extra_excludes=[],
            config_extraction_mode="heuristic",
            verbose=False,
            progress=False,
            readme_debug=False,
            max_config_files=5,
            fleximax_lines=50,
            max_chunk_chars=400,
            language_proportional=False,
            with_source=False,
            input=None,
            no_secret_scan=False,
        )

        mock_findings = [
            SecretFinding(file="config.py", line=5, rule_id="api-key", description="", match="")
        ]

        with patch("hypergumbo_core.cli.is_gitleaks_available", return_value=True):
            with patch("hypergumbo_core.cli.scan_content_cached", return_value=mock_findings):
                cmd_sketch(args)

        captured = capsys.readouterr()
        assert "config.py:5" in captured.err
        assert "api-key" in captured.err

    def test_sketch_skips_scan_with_no_secret_scan_flag(self, tmp_path: Path, capsys) -> None:
        """Sketch skips secret scan when --no-secret-scan is set."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_sketch

        # Create a minimal repo
        (tmp_path / "main.py").write_text("print('hello')")

        args = Namespace(
            path=str(tmp_path),
            tokens=100,
            exclude_tests=True,
            first_party_priority=True,
            extra_excludes=[],
            config_extraction_mode="heuristic",
            verbose=False,
            progress=False,
            readme_debug=False,
            max_config_files=5,
            fleximax_lines=50,
            max_chunk_chars=400,
            language_proportional=False,
            with_source=False,
            input=None,
            no_secret_scan=True,
        )

        # Should not call any gitleaks functions
        with patch("hypergumbo_core.cli.is_gitleaks_available") as mock_check:
            cmd_sketch(args)
            mock_check.assert_not_called()

        captured = capsys.readouterr()
        # Should not mention gitleaks or secrets
        assert "secret" not in captured.err.lower()
        assert "gitleaks" not in captured.err.lower()


class TestCLIIntegration:
    """Tests for CLI command integration."""

    def test_cmd_install_gitleaks_check_when_available(self) -> None:
        """cmd_install_gitleaks --check returns 0 when installed."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_install_gitleaks

        args = Namespace(check=True, quiet=False)

        # Patch in cli module where it's imported
        with patch("hypergumbo_core.cli.is_gitleaks_available", return_value=True):
            result = cmd_install_gitleaks(args)

        assert result == 0

    def test_cmd_install_gitleaks_check_when_unavailable(self) -> None:
        """cmd_install_gitleaks --check returns 1 when not installed."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_install_gitleaks

        args = Namespace(check=True, quiet=False)

        with patch("hypergumbo_core.cli.is_gitleaks_available", return_value=False):
            result = cmd_install_gitleaks(args)

        assert result == 1

    def test_cmd_install_gitleaks_install_success(self) -> None:
        """cmd_install_gitleaks returns 0 on successful install."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_install_gitleaks

        args = Namespace(check=False, quiet=True)

        with patch("hypergumbo_core.cli.install_gitleaks", return_value=True):
            result = cmd_install_gitleaks(args)

        assert result == 0

    def test_cmd_install_gitleaks_install_failure(self) -> None:
        """cmd_install_gitleaks returns 1 on failed install."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_install_gitleaks

        args = Namespace(check=False, quiet=True)

        with patch("hypergumbo_core.cli.install_gitleaks", return_value=False):
            result = cmd_install_gitleaks(args)

        assert result == 1


class TestUninstallGitleaks:
    """Tests for uninstall_gitleaks function."""

    def test_uninstall_gitleaks_when_not_installed(self) -> None:
        """Returns True when gitleaks is not installed."""
        with patch.object(gitleaks, "GITLEAKS_PATH") as mock_path:
            mock_path.exists.return_value = False
            with patch("shutil.which", return_value=None):
                result = uninstall_gitleaks(quiet=True)
        assert result is True

    def test_uninstall_gitleaks_when_not_installed_shows_message(self, capsys) -> None:
        """Shows message when gitleaks is not installed."""
        with patch.object(gitleaks, "GITLEAKS_PATH") as mock_path:
            mock_path.exists.return_value = False
            with patch("shutil.which", return_value=None):
                uninstall_gitleaks(quiet=False)

        captured = capsys.readouterr()
        assert "not installed" in captured.out

    def test_uninstall_gitleaks_when_not_installed_notes_system(self, capsys) -> None:
        """Notes system gitleaks when local not installed."""
        with patch.object(gitleaks, "GITLEAKS_PATH") as mock_path:
            mock_path.exists.return_value = False
            with patch("shutil.which", return_value="/usr/bin/gitleaks"):
                uninstall_gitleaks(quiet=False)

        captured = capsys.readouterr()
        assert "/usr/bin/gitleaks" in captured.out

    def test_uninstall_gitleaks_success(self) -> None:
        """Successfully removes gitleaks binary."""
        with patch.object(gitleaks, "GITLEAKS_PATH") as mock_path:
            mock_path.exists.return_value = True
            result = uninstall_gitleaks(quiet=True)

        mock_path.unlink.assert_called_once()
        assert result is True

    def test_uninstall_gitleaks_shows_message_on_success(self, capsys) -> None:
        """Shows removal message on success."""
        with patch.object(gitleaks, "GITLEAKS_PATH") as mock_path:
            mock_path.exists.return_value = True
            uninstall_gitleaks(quiet=False)

        captured = capsys.readouterr()
        assert "Removed" in captured.out

    def test_uninstall_refuses_a_symlink_out_of_the_zone_without_crashing(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        """A ``~/.local/bin/gitleaks`` that links elsewhere is not ours to remove.

        The wrapper refuses it with ``SafetyZoneViolation``, which is not an
        ``OSError``, so uninstall-gitleaks must catch it explicitly or it exits
        1 with "internal error".
        """
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        bindir = tmp_path / ".local" / "bin"
        bindir.mkdir(parents=True)
        theirs = tmp_path / "go" / "bin" / "gitleaks"
        theirs.parent.mkdir(parents=True)
        theirs.write_bytes(b"theirs")
        link = bindir / "gitleaks"
        link.symlink_to(theirs)
        monkeypatch.setattr(gitleaks, "GITLEAKS_PATH", link)

        assert uninstall_gitleaks(quiet=True) is False

        assert link.is_symlink() and theirs.read_bytes() == b"theirs"
        assert "install_artifact" in capsys.readouterr().err

    def test_uninstall_gitleaks_failure(self) -> None:
        """Returns False when removal fails."""
        with patch.object(gitleaks, "GITLEAKS_PATH") as mock_path:
            mock_path.exists.return_value = True
            mock_path.unlink.side_effect = OSError("permission denied")
            result = uninstall_gitleaks(quiet=True)

        assert result is False


class TestUninstallGitleaksCLI:
    """Tests for cmd_uninstall_gitleaks."""

    def test_cmd_uninstall_gitleaks_success(self) -> None:
        """cmd_uninstall_gitleaks returns 0 on success."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_uninstall_gitleaks

        args = Namespace(quiet=True)

        with patch("hypergumbo_core.cli.uninstall_gitleaks", return_value=True):
            result = cmd_uninstall_gitleaks(args)

        assert result == 0

    def test_cmd_uninstall_gitleaks_failure(self) -> None:
        """cmd_uninstall_gitleaks returns 1 on failure."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_uninstall_gitleaks

        args = Namespace(quiet=True)

        with patch("hypergumbo_core.cli.uninstall_gitleaks", return_value=False):
            result = cmd_uninstall_gitleaks(args)

        assert result == 1


class TestInstallEmbeddingsCLI:
    """Tests for cmd_install_embeddings."""

    def test_cmd_install_embeddings_check_when_available(self) -> None:
        """cmd_install_embeddings --check returns 0 when installed."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_install_embeddings

        args = Namespace(check=True, quiet=False)

        with patch("hypergumbo_core.cli._is_embeddings_available", return_value=True):
            with patch("hypergumbo_core.cli._get_embeddings_version", return_value="5.7.0"), \
                    patch("hypergumbo_core.cli._embedding_models_cached", return_value=True):
                result = cmd_install_embeddings(args)

        assert result == 0

    def test_cmd_install_embeddings_check_when_unavailable(self) -> None:
        """cmd_install_embeddings --check returns 1 when not installed."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_install_embeddings

        args = Namespace(check=True, quiet=False)

        with patch("hypergumbo_core.cli._is_embeddings_available", return_value=False):
            result = cmd_install_embeddings(args)

        assert result == 1

    def test_cmd_install_embeddings_already_installed(self) -> None:
        """cmd_install_embeddings returns 0 when already installed."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_install_embeddings

        args = Namespace(check=False, quiet=True)

        with patch("hypergumbo_core.cli._is_embeddings_available", return_value=True):
            with patch("hypergumbo_core.cli._get_embeddings_version", return_value="5.7.0"), \
                    patch("hypergumbo_core.cli._embedding_models_cached", return_value=True):
                result = cmd_install_embeddings(args)

        assert result == 0

    def test_cmd_install_embeddings_install_success(self) -> None:
        """cmd_install_embeddings returns 0 on successful install."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_install_embeddings

        args = Namespace(check=False, quiet=True)

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("hypergumbo_core.cli._is_embeddings_available", return_value=False):
            with patch("subprocess.run", return_value=mock_result):
                result = cmd_install_embeddings(args)

        assert result == 0

    def test_cmd_install_embeddings_install_failure(self) -> None:
        """cmd_install_embeddings returns 1 on failed install."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_install_embeddings

        args = Namespace(check=False, quiet=True)

        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("hypergumbo_core.cli._is_embeddings_available", return_value=False):
            with patch("subprocess.run", return_value=mock_result):
                result = cmd_install_embeddings(args)

        assert result == 1

    def test_cmd_install_embeddings_subprocess_error(self) -> None:
        """cmd_install_embeddings returns 1 on subprocess error."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_install_embeddings

        args = Namespace(check=False, quiet=True)

        with patch("hypergumbo_core.cli._is_embeddings_available", return_value=False):
            with patch("subprocess.run", side_effect=subprocess.SubprocessError("error")):
                result = cmd_install_embeddings(args)

        assert result == 1


class TestUninstallEmbeddingsCLI:
    """Tests for cmd_uninstall_embeddings."""

    def test_cmd_uninstall_embeddings_not_installed(self) -> None:
        """cmd_uninstall_embeddings returns 0 when not installed."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_uninstall_embeddings

        args = Namespace(all=False, quiet=True)

        with patch("hypergumbo_core.cli._is_embeddings_available", return_value=False):
            result = cmd_uninstall_embeddings(args)

        assert result == 0

    def test_cmd_uninstall_embeddings_success(self) -> None:
        """cmd_uninstall_embeddings returns 0 on success."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_uninstall_embeddings

        args = Namespace(all=False, quiet=True)

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("hypergumbo_core.cli._is_embeddings_available", return_value=True):
            with patch("subprocess.run", return_value=mock_result):
                result = cmd_uninstall_embeddings(args)

        assert result == 0

    def test_cmd_uninstall_embeddings_failure(self) -> None:
        """cmd_uninstall_embeddings returns 1 on failure."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_uninstall_embeddings

        args = Namespace(all=False, quiet=True)

        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("hypergumbo_core.cli._is_embeddings_available", return_value=True):
            with patch("subprocess.run", return_value=mock_result):
                result = cmd_uninstall_embeddings(args)

        assert result == 1

    def test_cmd_uninstall_embeddings_with_all(self) -> None:
        """cmd_uninstall_embeddings --all also removes PyTorch."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_uninstall_embeddings

        args = Namespace(all=True, quiet=True)

        mock_result = MagicMock()
        mock_result.returncode = 0

        commands_run = []

        def capture_run(cmd, **kwargs):
            commands_run.append(cmd)
            return mock_result

        with patch("hypergumbo_core.cli._is_embeddings_available", return_value=True):
            with patch("subprocess.run", side_effect=capture_run):
                result = cmd_uninstall_embeddings(args)

        assert result == 0
        # Should have run pip uninstall for sentence-transformers, torch, torchvision, torchaudio
        assert len(commands_run) >= 2

    def test_cmd_uninstall_embeddings_subprocess_error(self) -> None:
        """cmd_uninstall_embeddings returns 1 on subprocess error."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_uninstall_embeddings

        args = Namespace(all=False, quiet=True)

        with patch("hypergumbo_core.cli._is_embeddings_available", return_value=True):
            with patch("subprocess.run", side_effect=subprocess.SubprocessError("error")):
                result = cmd_uninstall_embeddings(args)

        assert result == 1


class TestEmbeddingsAvailabilityHelpers:
    """Tests for embeddings helper functions."""

    def test_is_embeddings_available_returns_bool(self) -> None:
        """_is_embeddings_available returns a boolean based on package availability."""
        from hypergumbo_core.cli import _is_embeddings_available

        # Function should return bool (True if installed, False if not)
        result = _is_embeddings_available()
        assert isinstance(result, bool)

        # Verify it matches actual package availability
        try:
            import sentence_transformers

            expected = True
        except ImportError:
            expected = False

        assert result == expected

    def test_get_embeddings_version(self) -> None:
        """_get_embeddings_version returns version string."""
        from hypergumbo_core.cli import _get_embeddings_version

        result = _get_embeddings_version()
        assert isinstance(result, str)
        # Should be a version like "5.7.0" or "unknown"
        assert result



class TestVerboseOutput:
    """Tests for verbose output paths."""

    def test_cmd_install_embeddings_already_installed_verbose(self, capsys) -> None:
        """cmd_install_embeddings shows message when already installed (verbose)."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_install_embeddings

        args = Namespace(check=False, quiet=False)

        with patch("hypergumbo_core.cli._is_embeddings_available", return_value=True):
            with patch("hypergumbo_core.cli._get_embeddings_version", return_value="5.7.0"), \
                    patch("hypergumbo_core.cli._embedding_models_cached", return_value=True):
                result = cmd_install_embeddings(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "already installed" in captured.out
        assert "5.7.0" in captured.out

    def test_cmd_install_embeddings_install_verbose(self, capsys) -> None:
        """cmd_install_embeddings shows progress when installing (verbose)."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_install_embeddings

        args = Namespace(check=False, quiet=False)

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("hypergumbo_core.cli._is_embeddings_available", return_value=False):
            with patch("subprocess.run", return_value=mock_result):
                result = cmd_install_embeddings(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "Installing" in captured.out
        assert "PyTorch" in captured.out
        assert "features are now available" in captured.out

    def test_cmd_uninstall_embeddings_not_installed_verbose(self, capsys) -> None:
        """cmd_uninstall_embeddings shows message when not installed (verbose)."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_uninstall_embeddings

        args = Namespace(all=False, quiet=False)

        with patch("hypergumbo_core.cli._is_embeddings_available", return_value=False):
            result = cmd_uninstall_embeddings(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "not installed" in captured.out

    def test_cmd_uninstall_embeddings_uninstalling_verbose(self, capsys) -> None:
        """cmd_uninstall_embeddings shows progress when uninstalling (verbose)."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_uninstall_embeddings

        args = Namespace(all=False, quiet=False)

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("hypergumbo_core.cli._is_embeddings_available", return_value=True):
            with patch("subprocess.run", return_value=mock_result):
                result = cmd_uninstall_embeddings(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "Uninstalling" in captured.out
        assert "uninstalled" in captured.out.lower()

    def test_cmd_uninstall_embeddings_with_all_verbose(self, capsys) -> None:
        """cmd_uninstall_embeddings --all shows PyTorch removal message."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_uninstall_embeddings

        args = Namespace(all=True, quiet=False)

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("hypergumbo_core.cli._is_embeddings_available", return_value=True):
            with patch("subprocess.run", return_value=mock_result):
                result = cmd_uninstall_embeddings(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "PyTorch" in captured.out

    def test_cmd_uninstall_embeddings_with_all_handles_errors(self) -> None:
        """cmd_uninstall_embeddings --all ignores PyTorch removal errors."""
        from argparse import Namespace
        from hypergumbo_core.cli import cmd_uninstall_embeddings

        args = Namespace(all=True, quiet=True)

        mock_result = MagicMock()
        mock_result.returncode = 0

        call_count = [0]

        def mock_run(cmd, **kwargs):
            call_count[0] += 1
            # First call is sentence-transformers uninstall
            if call_count[0] == 1:
                return mock_result
            # Subsequent calls are PyTorch - raise errors
            raise subprocess.SubprocessError("mock error")

        with patch("hypergumbo_core.cli._is_embeddings_available", return_value=True):
            with patch("subprocess.run", side_effect=mock_run):
                result = cmd_uninstall_embeddings(args)

        # Should succeed despite PyTorch errors
        assert result == 0
