# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run one ``hypergumbo`` command in-process under the tracer (``check-allowlist-commands --trace``).

``HG_TRACE_ARGV``: the argv, JSON. ``HG_TRACE_STUB=1`` (the install / uninstall
commands, run with HOME in scratch): ``subprocess.run`` succeeds without
launching anything, and ``urlopen`` serves a fake gitleaks release archive --
a tar.gz holding a ``gitleaks`` file, whose hash replaces the pinned ones so
the install's SHA-256 check passes -- so the download, extraction and install
lines all run. ``HG_TRACE_PATCH="module:attr=True;..."`` replaces an
availability predicate first, to reach the other branch. Prints
``HG_TRACE_RC=<exit code>`` last on stderr.
"""
import importlib
import io
import json
import os
import subprocess  # nosec B404 - builds stub CompletedProcess results only
import sys
import tarfile

import hgtrace  # installed by sitecustomize

if os.environ.get("HG_TRACE_STUB") == "1":
    def _fake_run(args, *a, **k):
        empty = "" if (k.get("text") or k.get("universal_newlines") or k.get("encoding")) else b""
        return subprocess.CompletedProcess(args, 0, empty, empty)

    hgtrace.fake_run = _fake_run
    import urllib.request

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_archive() -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            data = b"#!/bin/sh\n"
            info = tarfile.TarInfo("gitleaks")
            info.size, info.mode = len(data), 0o755
            tf.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    _ARCHIVE = _fake_archive()

    def _fake_urlopen(url, *a, **k):
        return _Resp(_ARCHIVE)

    urllib.request.urlopen = _fake_urlopen

    # gitleaks binds urlopen at import, so it is imported after the swap; the
    # stand-in archive then takes the place of every pinned one.
    import hashlib

    import hypergumbo_core.gitleaks as _gitleaks
    _gitleaks.GITLEAKS_ARCHIVE_SHA256 = dict.fromkeys(
        _gitleaks.GITLEAKS_ARCHIVE_SHA256, hashlib.sha256(_ARCHIVE).hexdigest())

import hypergumbo_core.cli as cli

for spec in filter(None, os.environ.get("HG_TRACE_PATCH", "").split(";")):
    target, value = spec.split("=")
    module, attr = target.split(":")
    result = {"True": True, "False": False}[value]
    setattr(importlib.import_module(module), attr, (lambda v: (lambda *a, **k: v))(result))

try:
    rc = cli.main(json.loads(os.environ["HG_TRACE_ARGV"]))
except SystemExit as exc:
    rc = exc.code
print(f"HG_TRACE_RC={rc}", file=sys.stderr)
