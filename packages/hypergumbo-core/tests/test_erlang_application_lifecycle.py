# SPDX-License-Identifier: AGPL-3.0-or-later
"""Erlang's application lifecycle calls are messages to a process (INV-butiv).

erlang.yaml rowed ``application`` [set_env, unset_env, load, start, stop,
ensure_started, ensure_all_started] under ``env_write``. ``set_env`` and
``unset_env`` belong there under the 2026-09-06 INV-nular ruling (application
config is where credentials live). The other five were swept in with them, the
WI-jupaf module-sweep defect two lines below the row where it was fixed.

What they do at the call site, read in OTP's kernel source
(``application.erl`` / ``application_controller.erl``):

* ``load/1`` -> ``application_controller:load_application/1`` ->
  ``call({load_application, App}, infinity)``, a gen_server call.
* ``start/1,2`` -> ``ensure_loaded`` (the load above) then
  ``application_controller:start_application/2`` -> ``call(...)``.
* ``ensure_started`` is ``start``; ``ensure_all_started`` starts each
  dependency the same way.
* ``stop/1`` -> ``application_controller:stop_application/1`` -> ``call(...)``.

So each is a request to the application controller PROCESS: ``process_send``,
the class erlang.yaml already gives ``gen_server:call`` / ``start`` / ``stop``.
The ``.app`` resource file is read inside the controller, not at the call site,
so it is not rowed here (a ``gen_server:call`` to a file server is not an
``fs_read`` either).
"""

from __future__ import annotations

import contextlib
import io
import json

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.io_boundary import load_catalog

_LIFECYCLE = ("load", "start", "stop", "ensure_started", "ensure_all_started")


@pytest.fixture(scope="module")
def erlang():
    return load_catalog("erlang")


def _boundaries(catalog, name: str) -> set[str]:
    return {p.boundary for p in catalog.primitives
            if p.module == "application" and p.name == name}


@pytest.mark.parametrize("name", _LIFECYCLE)
def test_a_lifecycle_call_is_a_process_send(erlang, name: str) -> None:
    assert _boundaries(erlang, name) == {"process_send"}


@pytest.mark.parametrize("name", ["set_env", "unset_env"])
def test_the_config_writers_stay_env_write(erlang, name: str) -> None:
    """Control: the two rows the INV-nular ruling covers."""
    assert _boundaries(erlang, name) == {"env_write"}


_PROGRAM = '''-module(app).
-export([boot/0, cfg/1]).

boot() ->
    ok = application:load(myapp),
    {ok, _} = application:ensure_all_started(myapp),
    ok = application:ensure_started(crypto),
    application:start(ssl),
    application:stop(ssl).

cfg(V) ->
    application:set_env(myapp, key, V).
'''


def test_the_shipped_command_reports_them_as_process_sends(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.erl").write_text(_PROGRAM)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        rc = main(["io-boundaries", str(repo), "--format", "json", "--include-tests"])
    assert rc == 0
    boundaries = json.loads(buf.getvalue())["boundaries"]
    chains = {b: {c["primitive"] for c in v["chains"]} for b, v in boundaries.items()}
    assert chains["env_write"] == {"application.set_env"}
    assert chains["process_send"] >= {f"application.{n}" for n in _LIFECYCLE}
