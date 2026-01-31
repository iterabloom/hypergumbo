"""Allow running hypergumbo as a module: python -m hypergumbo."""
from hypergumbo_core.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
