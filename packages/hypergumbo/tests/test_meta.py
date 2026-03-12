# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the hypergumbo meta-package."""


def test_version_reexport():
    """Verify the meta-package re-exports __version__ from core."""
    from hypergumbo import __version__

    assert isinstance(__version__, str)
    assert __version__  # non-empty
