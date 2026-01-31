import logging
import pytest

from hypergumbo import __version__
from hypergumbo_core.cli import build_parser, main


def test_version_flag_prints_version_and_exits(capsys):
    parser = build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])

    assert exc.value.code == 0

    out, err = capsys.readouterr()
    assert __version__ in out
    assert "hypergumbo" in out


def test_debug_flag_configures_logging(tmp_path, monkeypatch):
    """--debug flag enables DEBUG level logging."""
    # Create a minimal repo
    (tmp_path / "test.py").write_text("x = 1")

    # Track if basicConfig was called with DEBUG level
    original_basicConfig = logging.basicConfig
    config_calls = []

    def mock_basicConfig(**kwargs):
        config_calls.append(kwargs)
        # Don't actually configure logging (would affect other tests)

    monkeypatch.setattr(logging, "basicConfig", mock_basicConfig)

    # Run with --debug flag (will fail because no proper repo, but that's ok)
    # We just need to verify logging was configured
    try:
        main(["--debug", "sketch", str(tmp_path), "-t", "100"])
    except Exception:
        pass  # Expected - minimal repo won't fully work

    # Verify basicConfig was called with DEBUG level
    assert len(config_calls) == 1
    assert config_calls[0]["level"] == logging.DEBUG

