"""Tests for __main__.py module (python -m hypergumbo invocation)."""
import runpy
import sys

import pytest


def test_main_module_entry_point(tmp_path, monkeypatch):
    """Test running python -m hypergumbo via runpy."""
    # Set up argv to run a simple command
    out_file = tmp_path / "out.json"
    monkeypatch.setattr(
        sys, "argv", ["hypergumbo", "run", str(tmp_path), "--out", str(out_file)]
    )

    # runpy.run_module will execute __main__.py
    # It raises SystemExit on completion
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("hypergumbo", run_name="__main__")

    assert exc.value.code == 0
    assert out_file.exists()
