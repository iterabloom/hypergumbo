"""Pytest configuration for hypergumbo test suite.

This conftest.py ensures proper initialization of the test environment,
particularly for entry_points-based analyzer discovery.
"""
import sys
import pytest


def pytest_configure(config):
    """Called after command line options have been parsed and all plugins loaded.

    This hook runs before test collection, ensuring analyzer discovery
    has a chance to complete before any tests import analyzer-related code.
    """
    print(f"\n[conftest.py] Python version: {sys.version}", file=sys.stderr)

    # Check entry_points discovery
    try:
        import importlib.metadata
        eps = list(importlib.metadata.entry_points(group="hypergumbo.analyzers"))
        print(f"[conftest.py] Entry points for 'hypergumbo.analyzers': {len(eps)}", file=sys.stderr)
        for ep in eps:
            print(f"[conftest.py]   - {ep.name}: {ep.value}", file=sys.stderr)
    except Exception as e:
        print(f"[conftest.py] ERROR checking entry_points: {e}", file=sys.stderr)

    # Clear and re-discover analyzers to ensure fresh state for each test run
    try:
        from hypergumbo_core.analyze.all_analyzers import clear_analyzer_cache, get_analyzers
        clear_analyzer_cache()
        # Force discovery by calling get_analyzers
        analyzers = get_analyzers()
        print(f"[conftest.py] Discovered {len(analyzers)} analyzers", file=sys.stderr)
        # Verify we found the expected analyzers
        if len(analyzers) < 100:
            print(f"[conftest.py] WARNING: Expected at least 100 analyzers, found {len(analyzers)}", file=sys.stderr)
            # List first 10 to help debug
            for a in analyzers[:10]:
                print(f"[conftest.py]   - {a.name}: {a.module_path}", file=sys.stderr)
    except ImportError as e:
        print(f"[conftest.py] WARNING: Could not import hypergumbo_core: {e}", file=sys.stderr)

    # Check installed packages
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list"],
            capture_output=True,
            text=True,
            timeout=30
        )
        lines = result.stdout.strip().split("\n")
        hg_packages = [l for l in lines if "hypergumbo" in l.lower()]
        print("[conftest.py] Installed hypergumbo packages:", file=sys.stderr)
        for pkg in hg_packages:
            print(f"[conftest.py]   {pkg}", file=sys.stderr)
    except Exception as e:
        print(f"[conftest.py] ERROR listing packages: {e}", file=sys.stderr)
