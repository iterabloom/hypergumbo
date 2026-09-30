# SPDX-License-Identifier: AGPL-3.0-or-later
"""A community sanitizer does not clear a real flow, end to end (WI-dikit).

``rotate`` decrypts a Fernet token and hands the plaintext to
``seal_and_store``, which re-encrypts it before writing. The encrypt call is a
sanitizer only in the COMMUNITY file ``taint_sanitizers/
encryption_community.yaml`` (the ``cryptography`` package is third-party), so
ADR-0061 ruling 2 withholds it: measured on a pinned dev a196aaddb5 the claim
"plaintext never reaches the host filesystem" read ``inconclusive`` rc 2 with
``sanitized_flows: 1`` -- the community row cleared the flow; with the change it
reads ``violated`` rc 1 and names the sanitizer it did not credit.

THE CONTROL is the act of vouching (ruling 3): the same community file copied
into ``taint_sanitizers.d`` with its ``provenance: community`` line deleted is
the operator's, and clears the flow again under the user-supplied caveat.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

import hypergumbo_core.taint as taint
from hypergumbo_core.cli import main

_VAULT = '''\
from cryptography.fernet import Fernet

KEY = b"0" * 44


def seal_and_store(data, path):
    blob = Fernet(KEY).encrypt(data)
    with open(path, "wb") as fh:
        fh.write(blob)


def rotate(token, path):
    plain = Fernet(KEY).decrypt(token)
    seal_and_store(plain, path)
'''


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    root = tmp_path / "repo"
    root.mkdir()
    (root / "vault.py").write_text(_VAULT)
    (tmp_path / "claims.yaml").write_text(
        "claims:\n  - id: PT-FS\n    text: t\n    constraint:\n      taint_flow:\n"
        "        source_taint: plaintext\n        prohibited_sink_zone: host_fs\n"
    )
    return root


def _verdict(repo: Path) -> tuple[int, dict]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        rc = main(["verify-claims", str(repo), "--claims",
                   str(repo.parent / "claims.yaml"), "--format", "json"])
    (verdict,) = json.loads(out.getvalue())["verdicts"]
    return rc, verdict


def test_the_community_sanitizer_is_named_not_credited(repo: Path) -> None:
    rc, verdict = _verdict(repo)
    assert (rc, verdict["verdict"]) == (1, "violated")
    assert verdict["sanitized_flows"] == 0
    (caveat,) = [c for c in verdict["caveats"]
                 if c["kind"] == "withheld_community_sanitizer"]
    assert "cryptography.fernet.Fernet.encrypt" in caveat["entries"]
    assert any("cryptography.fernet.Fernet.encrypt" in row["withheld_sanitizers"]
               for row in verdict["evidence"])


def test_vouching_for_the_file_credits_it_again(repo: Path, tmp_path: Path) -> None:
    shipped = Path(taint.__file__).parent / "taint_sanitizers" / \
        "encryption_community.yaml"
    channel = tmp_path / "config" / "hypergumbo" / "taint_sanitizers.d"
    channel.mkdir(parents=True)
    (channel / "encryption.yaml").write_text(
        shipped.read_text().replace("provenance: community\n", ""),
    )
    _rc, verdict = _verdict(repo)
    assert verdict["sanitized_flows"] == 1
    assert verdict["verdict"] != "violated"
    assert any(c["kind"] == "user_supplied_sanitizer" for c in verdict["caveats"])
