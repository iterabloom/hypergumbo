"""Tests for gitleaks integration.

This module tests the secret scanning functionality, including:
- Detection of gitleaks availability
- Scanning content for secrets
- Warning formatting
- CLI integration
"""

from __future__ import annotations

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
    scan_content,
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
        """Returns empty list when gitleaks not installed."""
        with patch.object(gitleaks, "get_gitleaks_path", return_value=None):
            result = scan_content("some content with API_KEY=secret123")
            assert result == []

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

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", return_value=mock_result):
                findings = scan_content("API_KEY=secret123")

        assert len(findings) == 1
        assert findings[0].file == "config.py"
        assert findings[0].line == 12
        assert findings[0].rule_id == "generic-api-key"

    def test_scan_content_handles_no_findings(self) -> None:
        """Returns empty list when no secrets found."""
        mock_result = MagicMock()
        mock_result.stdout = b"null"

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", return_value=mock_result):
                findings = scan_content("safe content")

        assert findings == []

    def test_scan_content_handles_empty_output(self) -> None:
        """Returns empty list when gitleaks outputs nothing."""
        mock_result = MagicMock()
        mock_result.stdout = b""

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", return_value=mock_result):
                findings = scan_content("safe content")

        assert findings == []

    def test_scan_content_handles_timeout(self) -> None:
        """Returns empty list on timeout."""
        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gitleaks", 30)):
                findings = scan_content("content")

        assert findings == []

    def test_scan_content_handles_subprocess_error(self) -> None:
        """Returns empty list on subprocess error."""
        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", side_effect=subprocess.SubprocessError("failed")):
                findings = scan_content("content")

        assert findings == []

    def test_scan_content_handles_invalid_json(self) -> None:
        """Returns empty list on invalid JSON output."""
        mock_result = MagicMock()
        mock_result.stdout = b"not valid json"

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", return_value=mock_result):
                findings = scan_content("content")

        assert findings == []


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
    """Tests for download URL determination."""

    def test_get_download_url_handles_network_error(self) -> None:
        """Raises RuntimeError on network failure."""
        with patch("hypergumbo_core.gitleaks.urlopen", side_effect=URLError("network error")):
            with pytest.raises(RuntimeError, match="Failed to fetch"):
                _get_download_url()

    def test_get_download_url_handles_invalid_json(self) -> None:
        """Raises RuntimeError on invalid JSON response."""
        mock_response = MagicMock()
        mock_response.read.return_value = b"not json"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("hypergumbo_core.gitleaks.urlopen", return_value=mock_response):
            with pytest.raises(RuntimeError, match="Failed to fetch"):
                _get_download_url()

    def test_get_download_url_handles_missing_version(self) -> None:
        """Raises RuntimeError when version not in response."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"tag_name": ""}).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("hypergumbo_core.gitleaks.urlopen", return_value=mock_response):
            with pytest.raises(RuntimeError, match="Could not determine"):
                _get_download_url()

    def test_get_download_url_handles_missing_asset(self) -> None:
        """Raises RuntimeError when expected asset not found."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "tag_name": "v8.30.0",
            "assets": [{"name": "wrong_file.tar.gz"}]
        }).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("hypergumbo_core.gitleaks.urlopen", return_value=mock_response):
            with pytest.raises(RuntimeError, match="No gitleaks release found"):
                _get_download_url()

    def test_get_download_url_success(self) -> None:
        """Returns URL and filename on success."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "tag_name": "v8.30.0",
            "assets": [{
                "name": "gitleaks_8.30.0_linux_x64.tar.gz",
                "browser_download_url": "https://example.com/download"
            }]
        }).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("hypergumbo_core.gitleaks.urlopen", return_value=mock_response):
            with patch("platform.system", return_value="Linux"):
                with patch("platform.machine", return_value="x86_64"):
                    url, filename = _get_download_url()

        assert url == "https://example.com/download"
        assert filename == "gitleaks_8.30.0_linux_x64.tar.gz"


class TestInstallGitleaks:
    """Tests for gitleaks installation."""

    def test_install_handles_download_url_error(self) -> None:
        """Returns False when unable to get download URL."""
        with patch.object(gitleaks, "_get_download_url", side_effect=RuntimeError("network error")):
            result = install_gitleaks(quiet=True)

        assert result is False

    def test_install_handles_download_error(self) -> None:
        """Returns False when download fails."""
        with patch.object(gitleaks, "_get_download_url", return_value=("https://example.com/file.tar.gz", "file.tar.gz")):
            with patch("hypergumbo_core.gitleaks.urlopen", side_effect=URLError("download failed")):
                result = install_gitleaks(quiet=True)

        assert result is False


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
        """Returns empty list on OSError."""
        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", side_effect=OSError("permission denied")):
                findings = scan_content("content")

        assert findings == []

    def test_scan_content_handles_non_list_json(self) -> None:
        """Returns empty list when JSON is not a list."""
        mock_result = MagicMock()
        mock_result.stdout = json.dumps({"not": "a list"}).encode()

        with patch.object(gitleaks, "get_gitleaks_path", return_value=Path("/usr/bin/gitleaks")):
            with patch("subprocess.run", return_value=mock_result):
                findings = scan_content("content")

        assert findings == []


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
            with patch("hypergumbo_core.cli.scan_content", return_value=[]):
                cmd_sketch(args)

        captured = capsys.readouterr()
        assert "best-effort" in captured.err

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
            with patch("hypergumbo_core.cli.scan_content", return_value=mock_findings):
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
