# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gitleaks integration for secret scanning.

This module provides best-effort secret scanning of ``hypergumbo sketch``
output before users share it (e.g., pasting into LLM chat windows). It uses gitleaks, an
open-source (MIT licensed) secret scanner.

Key design decisions:
- Install is opt-in: user must run ``hypergumbo install-gitleaks`` or
  ``hypergumbo add-extras`` (the umbrella that also installs grammars,
  embeddings, and rust-analyzer).
- Scan is opt-out: once installed, scans run by default (use
  ``--no-secret-scan`` to skip).
- Always nag: if not installed, every ``sketch`` run suggests installing.
- Always warn: even with gitleaks, disclaimer that it's best-effort.
- Graceful degradation: if gitleaks fails, warn but don't block output.

Public surface
--------------
- ``install_gitleaks`` / ``uninstall_gitleaks``: download / remove the
  binary from the hypergumbo-managed install location. The latter is
  symmetric with the former — components installed outside this module
  (system package, hand-copied binary) are left alone. The download is the
  pinned :data:`GITLEAKS_VERSION`, and its archive must match the SHA-256
  that release published (:data:`GITLEAKS_ARCHIVE_SHA256`) before anything
  is written. Until this pin it was whatever ``releases/latest`` named,
  checked against nothing but HTTPS -- for a binary that is then handed the
  sketch's text on every run.
- ``scan_content`` / ``scan_content_cached``: scan a string of output
  for secrets. The cached path (WI-julir, UAT 2026-04-13 BUG-20) keys
  on a content hash and short-circuits repeated scans of identical
  output; useful when ``hypergumbo`` is invoked in tight CI loops.
- ``is_gitleaks_available``: existence check only (our install location,
  then ``PATH``). Nothing is executed.
- ``get_install_nag``: the message printed when the scan would run but
  the binary is missing.
- ``get_gitleaks_path``: resolve the binary, ours first then ``PATH``.
- ``format_secret_warning``: render findings for the user.
- ``scan_content_cached``: the scan, cached by content hash; ``None`` when
  no scan happened, which is never cached and never reads as clean. It is
  the only entry point: ``scan_content``, which returned ``[]`` for both
  "clean" and "not scanned", lost its last production caller in a473cd2bba
  and was retired (WI-jovuf).
- ``SecretFinding``: the per-finding record the scan returns, consumed by
  ``format_secret_warning``.

Filesystem discipline
---------------------
All fs-write operations route through ``hypergumbo_core.safety_zones``
wrappers (``install_artifact_chmod`` / ``install_artifact_unlink`` /
``install_artifact_copy`` / ``install_artifact_mkdir`` / ``cache_write`` /
``cache_mkdir`` / ``tmp_artifact_dir`` / ``tmp_artifact_write_bytes`` /
``tmp_artifact_extract``): the downloaded archive and its extraction stay in
the tmp zone, and only the binary copied out of it is an install write. The wrappers tag each write with its trust zone
(``install_artifact`` / ``user_cache`` / ``tmp_artifact``) so the
per-entry-point taint catalog can verify zone reachability claims without
short-name sink overapproximation (Unreleased CHANGELOG / safety-claims work).

Not a write, and the widest-reading thing this module does: the scan itself
runs through ``repo_inspect_scan`` (WI-fasuv), the ``repo_inspection``
SUBPROCESS zone rather than a filesystem one. It reads every byte of output
handed to it on stdin (``gitleaks stdin``), so it is binned separately from
the writes above and is the sink that matters most when reasoning about
what this module can reach. It runs in an empty ``tmp_artifact_dir`` and
reports on ``-`` (INV-nihab): gitleaks reads its config and ignore files
from its working directory, which would otherwise be the analysed
repository, and it unlinks a report PATH before writing it.

The goal is safety by default without requiring a PhD to configure.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import stat
import subprocess  # nosec B404 - subprocess needed to run gitleaks
import sys
import tarfile
import zipfile

from .safety_zones import (
    SafetyZoneViolation,
    tmp_artifact_dir,
    tmp_artifact_extract,
    tmp_artifact_write_bytes,
    cache_mkdir,
    cache_write,
    install_artifact_chmod,
    install_artifact_copy,
    install_artifact_mkdir,
    install_artifact_unlink,
    repo_inspect_scan,
)
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

#: The one gitleaks release ``install-gitleaks`` installs. Pinned rather than
#: "latest": the scan hands gitleaks the sketch's text, which quotes the
#: user's source, so the binary is the one a reviewer chose, not whatever was
#: published most recently. Moving to a newer release means replacing the
#: version and every hash below in one reviewed change.
GITLEAKS_VERSION = "8.30.0"
GITLEAKS_DOWNLOAD_BASE = (
    f"https://github.com/gitleaks/gitleaks/releases/download/v{GITLEAKS_VERSION}"
)

#: SHA-256 of each archive in that release, copied from the
#: ``gitleaks_8.30.0_checksums.txt`` published beside it. gitleaks signs
#: nothing, so this table is the only check that the bytes downloaded are the
#: bytes released; ``~/.local/bin/gitleaks`` installed on 2025-11-26 was
#: confirmed identical to the binary in the linux_x64 archive.
GITLEAKS_ARCHIVE_SHA256 = {
    "gitleaks_8.30.0_darwin_arm64.tar.gz": "b251ab2bcd4cd8ba9e56ff37698c033ebf38582b477d21ebd86586d927cf87e7",
    "gitleaks_8.30.0_darwin_x64.tar.gz": "ca221d012d247080c2f6f61f4b7a83bffa2453806b0c195c795bbe9a8c775ed5",
    "gitleaks_8.30.0_linux_arm64.tar.gz": "b4cbbb6ddf7d1b2a603088cd03a4e3f7ce48ee7fd449b51f7de6ee2906f5fa2f",
    "gitleaks_8.30.0_linux_armv6.tar.gz": "193d2e4580e3871f66a5c74d4a6098264d2dd0a50a49b53f7fb58081ab174742",
    "gitleaks_8.30.0_linux_armv7.tar.gz": "affb600da323925f775df81709c9878cda3f276f30c8e9c5ebef45d66b60834d",
    "gitleaks_8.30.0_linux_x32.tar.gz": "9da53611213a9d95d9352941ae5ebd4992ac0684eaf76e135eee13ee73c25085",
    "gitleaks_8.30.0_linux_x64.tar.gz": "79a3ab579b53f71efd634f3aaf7e04a0fa0cf206b7ed434638d1547a2470a66e",
    "gitleaks_8.30.0_windows_armv6.zip": "bb1c0e78f785cbf7c88a967d304372a5e36c3703b22a769fa44fd8bc514db8f6",
    "gitleaks_8.30.0_windows_armv7.zip": "92f1dff6a9d75c027e8379edc38e79d1e8d4e9af5789f123f7ea394f416703e0",
    "gitleaks_8.30.0_windows_x32.zip": "35b08db8fc11f4cec66fe54227a9bbd8ecfd7456941d31837f9137edb77ee60e",
    "gitleaks_8.30.0_windows_x64.zip": "54fe94f644b832dd08e8c3a5915efb3bfa862386d59fb27ca0792cb687a83573",
}


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


def _get_download_url() -> tuple[str, str, str]:
    """The pinned release's archive for this platform, and its expected hash.

    Nothing is fetched: the URL is built from :data:`GITLEAKS_VERSION`, so the
    archive is the only download.

    Returns:
        Tuple of (download_url, filename, sha256)

    Raises:
        RuntimeError: If the pinned release ships no archive for this platform
    """
    plat, arch = _get_platform_arch()
    ext = "zip" if plat == "windows" else "tar.gz"
    filename = f"gitleaks_{GITLEAKS_VERSION}_{plat}_{arch}.{ext}"
    sha256 = GITLEAKS_ARCHIVE_SHA256.get(filename)
    if sha256 is None:
        raise RuntimeError(
            f"No gitleaks {GITLEAKS_VERSION} release for {plat}/{arch}. "
            f"Expected: {filename}"
        )
    return f"{GITLEAKS_DOWNLOAD_BASE}/{filename}", filename, sha256


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
        url, filename, expected_sha256 = _get_download_url()
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

    # Before the first write: a download that is not the released archive
    # (tampered, truncated, or a different release) never reaches the disk.
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if actual_sha256 != expected_sha256:
        print(
            f"Error: {filename} has SHA-256 {actual_sha256}, but gitleaks "
            f"{GITLEAKS_VERSION} published {expected_sha256}; not installing it.",
            file=sys.stderr,
        )
        return False

    # Extract binary - all of this is network-dependent
    try:  # pragma: no cover
        with tmp_artifact_dir() as tmpdir:
            tmppath = Path(tmpdir)
            archive_path = tmppath / filename

            # Write archive
            tmp_artifact_write_bytes(archive_path, data)

            # Extract through the zone wrapper, which validates EVERY member
            # path against the tmp_artifact root. The suppressions this
            # replaced justified the hazard by PROVENANCE ("from trusted
            # GitHub release"); the claim this code is under asserts a
            # WRAPPER discipline, and provenance is not that. See
            # safety_zones.tmp_artifact_extract.
            tmp_artifact_extract(archive_path, tmppath)

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

            # Ensure install directory exists (INV-zudak: route through wrapper).
            install_artifact_mkdir(GITLEAKS_INSTALL_DIR, parents=True, exist_ok=True)

            # Copy binary
            install_artifact_copy(extracted_binary, GITLEAKS_PATH)

            # Make executable on Unix
            if sys.platform != "win32":
                install_artifact_chmod(
                    GITLEAKS_PATH,
                    GITLEAKS_PATH.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
                )

    except (
        OSError,
        SafetyZoneViolation,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as e:  # pragma: no cover
        # SafetyZoneViolation is caught here rather than allowed to escape: a
        # refused member means a release archive tried to write outside the
        # tmp zone, and a refused target means ~/.local/bin/gitleaks is a
        # symlink to a binary hypergumbo does not own. Either is a REFUSAL TO
        # INSTALL, not a crash. It returns False like every other failure, and
        # the message names the path.
        print(f"Error installing gitleaks: {e}", file=sys.stderr)  # pragma: no cover
        return False  # pragma: no cover

    if not quiet:  # pragma: no cover
        print(f"  Installed to {GITLEAKS_PATH}")
        print("  Done!")

    return True  # pragma: no cover


def _scan(content: str) -> list[SecretFinding] | None:
    """The scan itself: findings, or ``None`` when it did not happen.

    ``None`` covers no gitleaks, a gitleaks that cannot run (a partial
    install), the 30 s timeout, a non-zero exit, and output that is not a
    JSON list. None of
    those is a clean result, and :func:`scan_content_cached` must not record
    one as clean.
    """
    gitleaks = get_gitleaks_path()
    if not gitleaks:
        return None

    try:
        # Run gitleaks against stdin, JSON output (nosec B603 - known binary).
        #
        # Use the `stdin` subcommand, NOT `detect --pipe`: gitleaks 8.30+
        # removed the `detect` subcommand (replaced by `git`/`dir`/`stdin`)
        # and `--pipe` no longer reads stdin — it silently scans the current
        # working directory instead, turning this scan into a no-op that
        # always returns ``[]`` regardless of the piped content (INV-pirad).
        # Do NOT revert to `detect --pipe`; the real-binary guard in
        # test_gitleaks.py (TestScanContentRealBinary) exists to catch that.
        # WI-fasuv: repo_inspection zone. This is the widest-reading member --
        # it sees every byte of content handed to it on stdin.
        #
        # INV-nihab: gitleaks reads `.gitleaks.toml` and `.gitleaksignore` from
        # its working directory, and the inherited one is normally the analysed
        # repository, which could then allowlist its own secrets out of this
        # scan. So it runs in an empty scratch directory of its own; an
        # operator who wants a config passes it through gitleaks' own
        # GITLEAKS_CONFIG / GITLEAKS_CONFIG_TOML, which are theirs, not the
        # repository's. And it reports on `-`: given `/dev/stdout` as a report
        # PATH, gitleaks unlinks it before writing, which succeeds as root (the
        # usual user in a container).
        with tmp_artifact_dir(prefix="hypergumbo-gitleaks-") as scan_cwd:
            result = repo_inspect_scan(
                [
                    str(gitleaks),
                    "stdin",  # Read the content to scan from stdin
                    "--report-format", "json",
                    "--report-path", "-",  # Findings on stdout
                    "--exit-code", "0",  # Don't exit non-zero on findings
                    "--no-banner",  # Keep the ASCII logo out of stderr
                ],
                input=content.encode("utf-8"),
                capture_output=True,
                timeout=30,
                cwd=scan_cwd,
            )
    except subprocess.TimeoutExpired:
        # Scan took too long: not scanned
        return None
    except (subprocess.SubprocessError, OSError):
        # gitleaks failed to run: not scanned
        return None

    # `--exit-code 0` means findings never exit non-zero, so any non-zero
    # exit is a failure: an unloadable GITLEAKS_CONFIG exits 1 with empty
    # stdout, which read as "no secrets" and was cached clean (WI-rapuh).
    if result.returncode != 0:
        return None

    # Parse JSON output
    try:
        # gitleaks outputs JSON array of findings
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        if not stdout or stdout == "null":
            return []

        findings_data = json.loads(stdout)
        if not isinstance(findings_data, list):
            return None

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
        return None


def scan_content_cached(
    content: str,
    cache_dir: "Optional[Path]",
    max_entries: int = 8,
) -> list[SecretFinding] | None:
    """Cached wrapper around ``_scan``; ``None`` when no scan happened.

    Why this exists: gitleaks invocation costs ~8s on a small repo and
    dominates wall-clock for warm sketch runs that reuse cached analysis
    (UAT 2026-04-13 BUG-20 / WI-julir). The sketch text itself is the
    only input that affects the scan, so a sha256 of the content is a
    sufficient cache key. The cache lives next to the per-state results
    cache, so source-file changes naturally invalidate it (the state
    hash directory changes, taking the cache file with it).

    The cap of 8 entries comfortably accommodates the 1-3 sketches that
    a single ``hypergumbo sketch`` call may produce (main + 4x + 16x
    budgets, though only the main sketch is scanned today).

    Failure modes are silent — a missing/unwritable cache_dir, a
    corrupt cache file, or an OSError on write all degrade to the
    uncached path without surfacing an error to the user.

    A SCAN THAT DID NOT HAPPEN IS RETURNED AS ``None`` AND NEVER CACHED. It
    was returned as ``[]`` and cached as "no secrets" for that content, so a
    gitleaks that could not run, or one timeout, made sketch report "Secret
    scan complete" for that sketch from then on.
    """
    if cache_dir is None:
        return _scan(content)

    import hashlib
    import time
    from dataclasses import asdict

    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    cache_file = cache_dir / "secret_scan_cache.json"

    cache: dict = {}
    if cache_file.exists():
        try:
            raw = json.loads(cache_file.read_text())
            if isinstance(raw, dict):
                cache = raw
        except (OSError, json.JSONDecodeError):
            cache = {}

    entry = cache.get(content_hash)
    if isinstance(entry, dict) and isinstance(entry.get("findings"), list):
        try:
            return [SecretFinding(**f) for f in entry["findings"]]
        except TypeError:  # pragma: no cover - defensive against schema drift
            pass

    findings = _scan(content)
    if findings is None:
        return None

    cache[content_hash] = {
        "findings": [asdict(f) for f in findings],
        "ts": time.time(),
    }
    if len(cache) > max_entries:
        sorted_keys = sorted(
            cache.keys(),
            key=lambda k: cache[k].get("ts", 0) if isinstance(cache.get(k), dict) else 0,
        )
        for k in sorted_keys[: len(cache) - max_entries]:
            del cache[k]

    try:
        # INV-zudak: route directory creation through the user_cache wrapper
        # so verify-claims doesn't see a raw host_fs mkdir flow.
        cache_mkdir(cache_dir, parents=True, exist_ok=True)
        cache_write(cache_file, json.dumps(cache, indent=2))
    except OSError:  # pragma: no cover - cache write failure must not break scan
        pass

    return findings


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
        install_artifact_unlink(GITLEAKS_PATH)
        if not quiet:
            print(f"Removed gitleaks from {GITLEAKS_PATH}")
        return True
    except (OSError, SafetyZoneViolation) as e:
        # A refusal is a SafetyZoneViolation (a RuntimeError): the path is a
        # symlink to a gitleaks outside ~/.local/bin, which hypergumbo did not
        # install. Report it like any other failure, not as an internal error.
        print(f"Error removing gitleaks: {e}", file=sys.stderr)
        return False
