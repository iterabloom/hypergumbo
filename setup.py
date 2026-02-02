raise SystemExit(
    "\n"
    "ERROR: This repository is a monorepo. The root is not an installable package.\n"
    "\n"
    "For development setup:\n"
    "  python3 -m venv .venv && source .venv/bin/activate\n"
    "  ./scripts/dev-install\n"
    "  ./scripts/install-hooks\n"
    "\n"
    "For end-user installation:\n"
    "  pip install hypergumbo\n"
)