# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ending the CALLING process is not launching a subprocess (INV-mulul).

WHAT WAS WRONG. rust.yaml rowed ``std::process`` [exit, abort] under
``subprocess``. Neither spawns, signals or talks to another process: rust-src
``abort()`` is one call to ``crate::sys::abort_internal()`` (``libc::abort()``,
SIGABRT on the CALLING process) and ``exit()`` runs ``crate::rt::cleanup()``
then ``crate::sys::os::exit(code)``. The rows were attributed by MODULE --
``std::process`` also holds ``Command`` and ``Child``, which genuinely are
subprocess primitives. ``subprocess`` is the one boundary that is both an
auto-derived taint SINK (zone ``subprocess``) and OPAQUE (it withholds a clean
verdict on every other boundary), so the rows did three false things at once:
``process::exit(code)`` with ``code`` read from the environment violated
``host_secret -> subprocess``; "never launches a subprocess" read violated; and
every other ``must_not_exist`` claim on the repo was withheld with the sentence
"the analysis launches an external program at N call site(s)
(std::process.exit)".

THE SAME MECHANISM, SWEPT (LIVE rule 3). elixir.yaml rowed ``System.halt``
under ``subprocess`` with a note that says it "ends the VM; it launches nothing
and returns nothing" -- the row contradicted its own note. Both are removed.
The precedent for the cure is shipped twice already: javascript.yaml's
``process`` completeness grant ("Declared NOT I/O: exit/reallyExit/abort ...
process control, no boundary in this vocabulary") and haskell.yaml's
``System.Exit`` grant (INV-dozuj). python ``sys.exit`` / ``os._exit`` /
``os.abort``, go ``os.Exit`` and java ``System.exit`` have no rows.

NOT SWEPT HERE, and listed rather than silently skipped: swift.yaml rows
``fatalError`` / ``preconditionFailure`` / ``assertionFailure`` under
``process_send`` (an ``ipc`` taint sink) -- the same "ends this process" shape,
but ``fatalError`` also prints its message to stderr, so its right home is a
``logging``-vs-nothing decision, not a deletion; and erlang's ``exit`` row is
``erlang:exit/2`` (an exit SIGNAL to another process, correctly
``process_send``) sharing a name with ``exit/1``.

WHAT THE REMOVAL DOES NOT BUY. No completeness grant is added for
``std::process``: the module also exports ``id()`` (the pid), which
``test_rust_process_env_surface`` pins UNROWED by python precedent while c and
javascript row a pid as ``host_info_read``. Granting the module would declare
``id`` not-I/O, which is a ruling this item does not own. So an ``exit`` call
is now an UNCLASSIFIED call into ``std::process``: a ``must_not_exist`` claim
over it reads ``inconclusive``, not ``confirmed``. That is the honest reading,
and it is what the behavioural test below pins -- not violated, and no longer
described as a launch.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

import hypergumbo_core.io_boundary as iob
from hypergumbo_core.cli import main
from hypergumbo_core.io_boundary import (
    HIGH_RISK_EXEMPTIONS_SUBPROCESS,
    load_catalog,
)
from hypergumbo_core.taint import _derive_auto_imports_from_io_primitives

_CATALOG_DIR = Path(iob.__file__).parent / "io_primitives"


def _boundaries(lang: str, module: str, name: str) -> set[str]:
    catalog = load_catalog(lang)
    assert catalog is not None
    return {
        p.boundary for p in catalog.primitives
        if p.module == module and p.name == name
    }


@pytest.mark.parametrize("lang,module,name", [
    ("rust", "std::process", "exit"),
    ("rust", "std::process", "abort"),
    ("elixir", "System", "halt"),
])
def test_ending_this_process_is_not_rowed(lang: str, module: str, name: str) -> None:
    assert _boundaries(lang, module, name) == set()


@pytest.mark.parametrize("lang,module,name", [
    ("rust", "std::process::Command", "spawn"),
    ("rust", "std::process::Command", "new"),
    ("elixir", "System", "cmd"),
])
def test_control_the_real_launches_in_the_same_module_still_classify(
    lang: str, module: str, name: str,
) -> None:
    """Without this, the tests above pass by the catalogue going blind."""
    assert "subprocess" in _boundaries(lang, module, name)


def test_no_subprocess_sink_is_derived_for_a_process_exit() -> None:
    """The taint consequence, at the layer the taint arm consumes."""
    _sources, sinks, _amb = _derive_auto_imports_from_io_primitives(_CATALOG_DIR)
    derived = {
        (lang, s.module, s.name)
        for lang, rows in sinks.items() for s in rows
        if s.zone == "subprocess"
    }
    assert ("rust", "std::process::Command", "spawn") in derived  # reach
    for gone in (("rust", "std::process", "exit"),
                 ("rust", "std::process", "abort"),
                 ("elixir", "System", "halt")):
        assert gone not in derived


def test_every_subprocess_exemption_names_a_live_subprocess_row() -> None:
    """The reverse of TestHighRiskPrimitivesDriftGuard's Part 2.

    That guard requires every ``subprocess`` row to be classified; nothing
    required every EXEMPTION to still name one, so removing a row would leave
    a stale exemption behind -- an entry that documents a classification the
    catalogue no longer makes.
    """
    live: set[str] = set()
    for path in sorted(_CATALOG_DIR.glob("*.yaml")):
        catalog = load_catalog(path.stem)
        if catalog is None:  # pragma: no cover - every shipped file loads
            continue
        live |= {p.qualified_name for p in catalog.primitives
                 if p.boundary == "subprocess"}
    assert "subprocess.Popen.wait" in live  # reach
    stale = sorted(HIGH_RISK_EXEMPTIONS_SUBPROCESS - live)
    assert stale == [], stale


_RUST = '''use std::env;
use std::process;

fn main() {
    let code = env::var("X").unwrap_or_default().len() as i32;
    if code > 3 {
        process::abort();
    }
    process::exit(code);
}
'''

_ELIXIR = '''defmodule App do
  def main do
    code = String.length(System.get_env("X") || "")
    System.halt(code)
  end
end
'''

_CLAIMS = '''claims:
  - id: NO-SUBPROCESS
    text: This program never launches a subprocess.
    constraint:
      boundary: subprocess
      must_not_exist: true
  - id: SECRET-NO-SUBPROCESS
    text: No environment value reaches a subprocess.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: subprocess
'''


def _run(tmp_path: Path, files: dict[str, str], argv: list[str]) -> str:
    repo = tmp_path / "repo"
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    (tmp_path / "claims.yaml").write_text(_CLAIMS)
    mp = pytest.MonkeyPatch()
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), \
                contextlib.redirect_stderr(io.StringIO()):
            main([a.replace("{repo}", str(repo))
                  .replace("{claims}", str(tmp_path / "claims.yaml"))
                  for a in argv])
    finally:
        mp.undo()
    return buf.getvalue()


@pytest.mark.parametrize("files,env_read", [
    ({"Cargo.toml": '[package]\nname = "x"\nversion = "0.1.0"\n',
      "src/main.rs": _RUST}, "std::env.var"),
    ({"lib/app.ex": _ELIXIR}, "System.get_env"),
], ids=["rust", "elixir"])
def test_an_exit_fed_from_the_environment_is_not_a_launch(
    tmp_path: Path, files: dict[str, str], env_read: str,
) -> None:
    """At the FINDING level, through the shipped CLI (ADR-0049 ruling 3)."""
    iob_report = json.loads(_run(
        tmp_path, files,
        ["io-boundaries", "{repo}", "--format", "json", "--include-tests"],
    ))
    chains = {b: {c["primitive"] for c in v["chains"]}
              for b, v in iob_report["boundaries"].items()}
    # Reach: the source the flow would start from IS seen.
    assert env_read in chains.get("env_read", set())
    assert "subprocess" not in chains, chains.get("subprocess")

    out = _run(tmp_path, files,
               ["verify-claims", "{repo}", "--claims", "{claims}",
                "--format", "json"])
    verdicts = {v["claim_id"]: v for v in json.loads(out)["verdicts"]}
    for claim_id in ("NO-SUBPROCESS", "SECRET-NO-SUBPROCESS"):
        verdict = verdicts[claim_id]
        assert verdict["verdict"] != "violated", verdict["details"]
        assert "launches an external program" not in verdict["details"]
