# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/profile.py
"""Two JavaScript frameworks whose rows had no way to load (WI-movur).

* Socket.IO: its handler rows lived in express.yaml, which loads only when
  Express is detected, so a Socket.IO server without Express loaded none.
  ``socket.io`` in a manifest now detects ``socketio`` (socketio.yaml).
* gluegun: a gluegun CLI detected no framework, so cli-js.yaml (its
  ``GluegunCommand`` row among them) never loaded on one.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.profile import _detect_js_frameworks


def test_socket_io_is_detected_on_its_own(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"dependencies": {"socket.io": "^4.7.5"}}')
    frameworks = _detect_js_frameworks(tmp_path)
    assert "socketio" in frameworks
    assert "express" not in frameworks


def test_gluegun_detects_cli_js(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"dependencies": {"gluegun": "^5.1.6"}}')
    assert "cli-js" in _detect_js_frameworks(tmp_path)


def test_neither_without_the_dependency(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"dependencies": {"socket.io-client": "^4"}}')
    frameworks = _detect_js_frameworks(tmp_path)
    assert "socketio" not in frameworks and "cli-js" not in frameworks
