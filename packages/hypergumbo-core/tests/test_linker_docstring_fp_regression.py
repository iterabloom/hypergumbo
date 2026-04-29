# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression tests for the linker docstring/comment FP fix (WI-vavur).

Pattern detectors that match code shapes must not match against the contents
of comments or language-level docstrings. These tests instantiate
representative linkers against fixture files where the pattern appears
exclusively inside a docstring or comment, and assert zero detections.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.linkers.message_queue import link_message_queues
from hypergumbo_core.linkers.event_sourcing import link_events
from hypergumbo_core.linkers.subprocess_cli import _scan_python_file


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def test_message_queue_pattern_in_python_docstring_is_not_emitted(tmp_path: Path) -> None:
    body = (
        '"""Documentation block.\n\n'
        "Examples of patterns we detect:\n"
        "- producer.send('topic', msg)\n"
        "- consumer.subscribe(['topic'])\n"
        '"""\n'
        "x = 1\n"
    )
    _write(tmp_path / "lib.py", body)
    result = link_message_queues(tmp_path)
    # No symbols should be created from docstring text.
    assert all(
        s.path != str(tmp_path / "lib.py") for s in result.symbols
    ), [s for s in result.symbols if s.path == str(tmp_path / "lib.py")]


def test_message_queue_pattern_in_python_comment_is_not_emitted(tmp_path: Path) -> None:
    body = (
        "# Examples (must not match):\n"
        "# producer.send('topic', msg)\n"
        "# consumer.subscribe(['topic'])\n"
        "x = 1\n"
    )
    _write(tmp_path / "lib.py", body)
    result = link_message_queues(tmp_path)
    assert all(
        s.path != str(tmp_path / "lib.py") for s in result.symbols
    )


def test_message_queue_real_pattern_in_code_still_detected(tmp_path: Path) -> None:
    body = (
        "import kafka\n"
        "producer = kafka.KafkaProducer()\n"
        "producer.send('orders', b'msg')\n"
    )
    _write(tmp_path / "lib.py", body)
    result = link_message_queues(tmp_path)
    matched = [s for s in result.symbols if s.path == str(tmp_path / "lib.py")]
    assert matched, "real producer.send() call must still be detected"


def test_event_sourcing_pattern_in_js_comment_is_not_emitted(tmp_path: Path) -> None:
    body = (
        "// Documentation example:\n"
        "// emitter.emit('userCreated', user)\n"
        "const x = 1;\n"
    )
    _write(tmp_path / "x.js", body)
    result = link_events(tmp_path)
    assert all(
        s.path != str(tmp_path / "x.js") for s in result.symbols
    )


def test_subprocess_cli_pattern_in_python_docstring_is_not_emitted(tmp_path: Path) -> None:
    file_path = tmp_path / "lib.py"
    body = (
        '"""See `subprocess.run([\'git\', \'status\'])` for details."""\n'
        "y = 1\n"
    )
    _write(file_path, body)
    # _scan_python_file is the inner scanner; mask happens upstream in
    # the link_subprocess_cli driver. Use the driver to test the integrated path.
    from hypergumbo_core.linkers._text_filters import read_masked_source

    content = read_masked_source(file_path, encoding="utf-8", errors="ignore")
    calls = _scan_python_file(file_path, content)
    assert calls == []
