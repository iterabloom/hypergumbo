# SPDX-License-Identifier: AGPL-3.0-or-later
"""``command_line_read``: the program's own command line (WI-zidam).

OWNER RULINGS. 2026-10-22: argv counts as ``untrusted_input`` as well as
``host_secret`` -- the launcher chose it, as it chose stdin. 2026-10-28, on the
measured delta (10 repos x 7 generic claims: 6 untrusted-input verdicts flipped
to ``violated``, 10/10 sampled flows true and 0/10 useful, each a CLI doing with
its arguments what it exists to do): ship it, with argv's flows SET ASIDE AND
COUNTED, the ``resource_naming_flows`` shape, and countable again on a claim
that says ``include_command_line: true``.

WHY A BOUNDARY AND NOT A FLAG. A finding already carries its
``source_boundary``, and the boundary is already in the situation-collapse key
(``collapse_unadjudicated_flows``), so the exclusion reads a field that cannot
be half-true of a collapsed row (WI-gajir). WHY NOT ``ipc_recv``, which the
measured patch used: nothing is received from another process at the call
site, and an ``ipc_recv`` boundary claim would have counted every argv read.
An argument may also carry a credential, so each row stays ``simultaneous``
with ``env_read`` -- the ``navigation_read`` shape (ADR-0050 section 1) -- and
``env_read``, declared first, stays the scalar ``io_boundary``.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

import hypergumbo_core.io_boundary as iob
from hypergumbo_core.cli import main
from hypergumbo_core.io_boundary import CATALOG_BOUNDARY_TYPES, load_catalog
from hypergumbo_core.io_boundary_types import (
    AXIS_DATA_CROSSING,
    DIRECTION_INBOUND,
    find_io_boundary,
)
from hypergumbo_core.taint import (
    AUTO_SOURCE_LABEL_MAP,
    _derive_auto_imports_from_io_primitives,
)
from hypergumbo_core.verify_claims import ClaimsFileError, load_claims

_CATALOG_DIR = Path(iob.__file__).parent / "io_primitives"

# Every argv spelling the shipped catalogues row, by language. go, c/cpp, the
# JVM languages and objc have no argv row (a stated recall gap, not this item's).
_ARGV_ROWS = [
    ("python", "sys", "argv"),
    ("python", "argparse.ArgumentParser", "parse_args"),
    ("python", "argparse.ArgumentParser", "parse_known_args"),
    ("javascript", "process", "argv"),
    ("rust", "std::env", "args"),
    ("rust", "std::env", "args_os"),
    ("elixir", "System", "argv"),
    ("haskell", "System.Environment", "getArgs"),
    ("erlang", "init", "get_argument"),
    ("erlang", "init", "get_arguments"),
    ("erlang", "init", "get_plain_arguments"),
    ("swift", "CommandLine", "arguments"),
]


def _boundary_by_label(lang: str, module: str, name: str) -> dict[str, str]:
    sources, _sinks, _amb = _derive_auto_imports_from_io_primitives(_CATALOG_DIR)
    return {
        s.taint_label: s.source_boundary for s in sources.get(lang, ())
        if s.module == module and s.name == name
    }


def test_the_boundary_is_an_inbound_data_crossing_declared_last() -> None:
    """Declared LAST so first-declared-wins keeps every argv row's scalar
    ``io_boundary`` at ``env_read``, which it was before."""
    spec = find_io_boundary("command_line_read")
    assert spec is not None
    assert spec.axis == AXIS_DATA_CROSSING
    assert spec.direction == DIRECTION_INBOUND
    assert spec.catalog_declarable and spec.counts_in_headline
    assert CATALOG_BOUNDARY_TYPES[-1] == "command_line_read"


def test_it_derives_untrusted_input() -> None:
    assert AUTO_SOURCE_LABEL_MAP["command_line_read"] == "untrusted_input"


@pytest.mark.parametrize("lang, module, name", _ARGV_ROWS)
def test_an_argv_read_derives_both_labels_each_from_its_own_boundary(
    lang: str, module: str, name: str,
) -> None:
    """The untrusted half carries ``command_line_read`` -- what verify-claims
    excludes on -- and the credential half keeps ``env_read``, so no
    host_secret claim changes."""
    assert _boundary_by_label(lang, module, name) == {
        "host_secret": "env_read", "untrusted_input": "command_line_read",
    }


@pytest.mark.parametrize("lang", sorted({lang for lang, _, _ in _ARGV_ROWS}))
def test_every_command_line_row_is_simultaneous_with_env_read(lang: str) -> None:
    """The section holds argv rows and nothing else: each one is the second
    boundary of an ``env_read`` row, never ``ipc_recv``."""
    catalog = load_catalog(lang)
    rowed = [p for p in catalog.primitives if p.boundary == "command_line_read"]
    assert rowed
    for prim in rowed:
        assert catalog.simultaneous_boundaries_for(prim.qualified_name) == {
            "env_read", "command_line_read",
        }, prim.qualified_name


def test_the_cookie_is_browser_storage_and_keeps_its_secret_label() -> None:
    """The other half of the item: ``document.cookie``'s medium is browser
    storage, and it may carry a session token, so it stays ``host_secret``
    and mints nothing else (browser_storage_read derives no label)."""
    catalog = load_catalog("javascript")
    assert catalog.simultaneous_boundaries_for("document.cookie") == {
        "browser_storage_read", "env_read",
    }
    assert _boundary_by_label("javascript", "document", "cookie") == {
        "host_secret": "env_read",
    }


# ---------------------------------------------------------------------------
# The production path: verify-claims over a one-file repo.
# ---------------------------------------------------------------------------

_ARGV_APP = '''import subprocess
import sys


def go():
    subprocess.run(sys.argv[1], shell=True)
'''

_STDIN_APP = '''import subprocess
import sys


def go():
    subprocess.run(sys.stdin.read(), shell=True)
'''


def _claims(label: str, include: str | None) -> str:
    extra = f"        include_command_line: {include}\n" if include is not None else ""
    return (
        "claims:\n"
        "  - id: C\n"
        "    text: Nothing reaches a launched command.\n"
        "    constraint:\n"
        "      taint_flow:\n"
        f"        source_taint: {label}\n"
        "        prohibited_sink_zone: subprocess\n"
        f"{extra}"
    )


def _verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app: str,
    label: str = "untrusted_input", include: str | None = None,
) -> dict:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(app)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_claims(label, include))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict


def test_an_argv_flow_is_set_aside_and_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdict = _verdict(tmp_path, monkeypatch, _ARGV_APP)
    assert verdict["verdict"] != "violated"
    assert verdict["evidence_count"] == 0
    assert verdict["command_line_flows"] == 1
    assert "command line" in verdict["details"]


def test_a_claim_can_count_argv_flows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdict = _verdict(tmp_path, monkeypatch, _ARGV_APP, include="true")
    assert verdict["verdict"] == "violated"
    assert verdict["evidence_count"] == 1
    assert verdict["evidence"][0]["source_boundary"] == "command_line_read"
    assert verdict["command_line_flows"] == 0


def test_argv_still_counts_as_a_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exclusion is of the LAUNCHER-CHOSEN reading only: argv's
    credential reading is unchanged."""
    verdict = _verdict(tmp_path, monkeypatch, _ARGV_APP, label="host_secret")
    assert verdict["verdict"] == "violated"
    assert verdict["command_line_flows"] == 0


def test_stdin_is_not_set_aside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE CONTROL: the launcher chose stdin too, but it is ``ipc_recv``, a
    different boundary, and the ruling covered the command line only."""
    verdict = _verdict(tmp_path, monkeypatch, _STDIN_APP)
    assert verdict["verdict"] == "violated"
    assert verdict["command_line_flows"] == 0


@pytest.mark.parametrize("value", ["yes-please", "1", "[true]"])
def test_include_command_line_must_be_a_boolean(tmp_path: Path, value: str) -> None:
    path = tmp_path / "claims.yaml"
    path.write_text(_claims("untrusted_input", value))
    with pytest.raises(ClaimsFileError, match="include_command_line"):
        load_claims(path)
