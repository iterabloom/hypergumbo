# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gitleaks integration for secret scanning.

This module provides best-effort secret scanning of hypergumbo output before
users share it (e.g., pasting into LLM chat windows). It uses gitleaks, an
open-source (MIT licensed) secret scanner.

Key design decisions:
- Install is opt-in: user must run `hypergumbo install-gitleaks`
- Scan is opt-out: once installed, scans run by default (use --no-secret-scan to skip)
- Always nag: if not installed, every run suggests installing
- Always warn: even with gitleaks, disclaimer that it's best-effort
- Graceful degradation: if gitleaks fails, warn but don't block output

The goal is safety by default without requiring a PhD to configure.
"""

from __future__ import annotations

import json
import platform
import shutil
import stat
import subprocess  # nosec B404 - subprocess needed to run gitleaks
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.request import urlopen
from urllib.error import URLError

if TYPE_CHECKING:
    from typing import Optional

# Where we install gitleaks
GITLEAKS_INSTALL_DIR = Path.home() / ".local" / "bin"
GITLEAKS_BINARY_NAME = "gitleaks.exe" if sys.platform == "win32" else "gitleaks"
GITLEAKS_PATH = GITLEAKS_INSTALL_DIR / GITLEAKS_BINARY_NAME

# GitHub API for releases
GITLEAKS_RELEASES_URL = "https://api.github.com/repos/gitleaks/gitleaks/releases/latest"


@dataclass
class SecretFinding:
    """A potential secret found by gitleaks."""

    file: str
    line: int
    rule_id: str
    description: str
    match: str  # The actual matched text (redacted in output)


def is_gitleaks_available() -> bool:
    """Check if gitleaks is installed and runnable."""
    # Check our install location first
    if GITLEAKS_PATH.exists():
        return True
    # Check system PATH
    return shutil.which("gitleaks") is not None


def get_gitleaks_path() -> Optional[Path]:
    """Get the path to gitleaks binary, or None if not available."""
    if GITLEAKS_PATH.exists():
        return GITLEAKS_PATH
    system_path = shutil.which("gitleaks")
    if system_path:
        return Path(system_path)
    return None


def _get_platform_arch() -> tuple[str, str]:
    """Get platform and architecture for download URL."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    # Map to gitleaks naming convention
    if system == "darwin":
        plat = "darwin"
    elif system == "windows":
        plat = "windows"
    else:
        plat = "linux"

    # Map architecture
    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    elif machine in ("armv7l", "armv7"):
        arch = "armv7"
    elif machine in ("i386", "i686", "x86"):
        arch = "x32"
    else:
        arch = "x64"  # Default fallback

    return plat, arch


def _get_download_url() -> tuple[str, str]:
    """Get the download URL and filename for current platform.

    Returns:
        Tuple of (download_url, filename)

    Raises:
        RuntimeError: If unable to determine download URL
    """
    plat, arch = _get_platform_arch()

    try:
        with urlopen(GITLEAKS_RELEASES_URL, timeout=30) as response:  # noqa: S310  # nosec B310
            data = json.loads(response.read().decode())
    except (URLError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Failed to fetch gitleaks releases: {e}") from e

    version = data.get("tag_name", "").lstrip("v")
    if not version:
        raise RuntimeError("Could not determine gitleaks version from release")

    # Build expected filename
    ext = "zip" if plat == "windows" else "tar.gz"
    filename = f"gitleaks_{version}_{plat}_{arch}.{ext}"

    # Find matching asset
    for asset in data.get("assets", []):
        if asset.get("name") == filename:
            return asset["browser_download_url"], filename

    raise RuntimeError(
        f"No gitleaks release found for {plat}/{arch}. "
        f"Expected: {filename}"
    )


def install_gitleaks(quiet: bool = False) -> bool:
    """Download and install gitleaks binary.

    Args:
        quiet: Suppress progress messages

    Returns:
        True if installation succeeded, False otherwise
    """
    if not quiet:
        print("Installing gitleaks for secret scanning...")  # pragma: no cover

    try:
        url, filename = _get_download_url()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return False

    if not quiet:
        print(f"  Downloading {filename}...")  # pragma: no cover

    # Download to temp file
    try:
        with urlopen(url, timeout=60) as response:  # pragma: no cover  # noqa: S310  # nosec B310
            data = response.read()  # pragma: no cover
    except URLError as e:
        print(f"Error downloading gitleaks: {e}", file=sys.stderr)
        return False

    # Extract binary - all of this is network-dependent
    try:  # pragma: no cover
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            archive_path = tmppath / filename

            # Write archive
            archive_path.write_bytes(data)

            # Extract (from trusted GitHub release - nosec B202)
            if filename.endswith(".zip"):
                with zipfile.ZipFile(archive_path) as zf:
                    zf.extractall(tmppath)  # noqa: S202  # nosec B202
            else:
                with tarfile.open(archive_path, "r:gz") as tf:
                    tf.extractall(tmppath)  # noqa: S202  # nosec B202

            # Find the binary
            binary_name = "gitleaks.exe" if sys.platform == "win32" else "gitleaks"
            extracted_binary = tmppath / binary_name

            if not extracted_binary.exists():
                # Sometimes it's in a subdirectory
                for p in tmppath.rglob(binary_name):
                    extracted_binary = p
                    break

            if not extracted_binary.exists():
                print(f"Error: Could not find {binary_name} in archive", file=sys.stderr)
                return False

            # Ensure install directory exists
            GITLEAKS_INSTALL_DIR.mkdir(parents=True, exist_ok=True)

            # Copy binary
            shutil.copy2(extracted_binary, GITLEAKS_PATH)

            # Make executable on Unix
            if sys.platform != "win32":
                GITLEAKS_PATH.chmod(GITLEAKS_PATH.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    except (OSError, tarfile.TarError, zipfile.BadZipFile) as e:  # pragma: no cover
        print(f"Error extracting gitleaks: {e}", file=sys.stderr)  # pragma: no cover
        return False  # pragma: no cover

    if not quiet:  # pragma: no cover
        print(f"  Installed to {GITLEAKS_PATH}")
        print("  Done!")

    return True  # pragma: no cover


def scan_content(content: str) -> list[SecretFinding]:
    """Scan content for potential secrets using gitleaks.

    Args:
        content: The text content to scan (e.g., sketch output)

    Returns:
        List of findings. Empty list if gitleaks unavailable or no secrets found.
    """
    gitleaks = get_gitleaks_path()
    if not gitleaks:
        return []

    try:
        # Run gitleaks with stdin, JSON output (nosec B603 - known binary)
        result = subprocess.run(  # noqa: S603  # nosec B603
            [
                str(gitleaks),
                "detect",
                "--no-git",  # Don't treat as git repo
                "--pipe",  # Read from stdin
                "--report-format", "json",
                "--report-path", "/dev/stdout",  # Output to stdout
                "--exit-code", "0",  # Don't fail on findings
            ],
            input=content.encode("utf-8"),
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        # Scan took too long, skip
        return []
    except (subprocess.SubprocessError, OSError):
        # gitleaks failed to run, skip
        return []

    # Parse JSON output
    try:
        # gitleaks outputs JSON array of findings
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        if not stdout or stdout == "null":
            return []

        findings_data = json.loads(stdout)
        if not isinstance(findings_data, list):
            return []

        findings = []
        for item in findings_data:
            findings.append(
                SecretFinding(
                    file=item.get("File", "<stdin>"),
                    line=item.get("StartLine", 0),
                    rule_id=item.get("RuleID", "unknown"),
                    description=item.get("Description", ""),
                    match=item.get("Match", ""),
                )
            )
        return findings

    except json.JSONDecodeError:
        return []


def format_secret_warning(findings: list[SecretFinding]) -> str:
    """Format findings into a warning message for stderr.

    Args:
        findings: List of secret findings

    Returns:
        Formatted warning string
    """
    lines = [
        f"\u26a0\ufe0f  Potential secrets detected ({len(findings)} finding{'s' if len(findings) != 1 else ''}):"
    ]

    for f in findings[:10]:  # Limit to first 10
        # Don't show the actual secret, just location and type
        lines.append(f"    {f.file}:{f.line} - {f.rule_id}")

    if len(findings) > 10:
        lines.append(f"    ... and {len(findings) - 10} more")

    lines.append("   This scan is best-effort, not exhaustive. Review before sharing.")

    return "\n".join(lines)


def get_install_nag() -> str:
    """Get the message nagging user to install gitleaks."""
    return (
        "\u2139\ufe0f  Secret scanning unavailable. "
        "Run 'hypergumbo install-gitleaks' for safer sharing."
    )


def uninstall_gitleaks(quiet: bool = False) -> bool:
    """Remove gitleaks binary installed by hypergumbo.

    Args:
        quiet: Suppress progress messages

    Returns:
        True if uninstall succeeded (or wasn't needed), False otherwise
    """
    if not GITLEAKS_PATH.exists():
        if not quiet:
            print("gitleaks is not installed by hypergumbo.")
            system_path = shutil.which("gitleaks")
            if system_path:
                print(f"  Note: System gitleaks found at {system_path}")
        return True

    try:
        GITLEAKS_PATH.unlink()
        if not quiet:
            print(f"Removed gitleaks from {GITLEAKS_PATH}")
        return True
    except OSError as e:
        print(f"Error removing gitleaks: {e}", file=sys.stderr)
        return False
