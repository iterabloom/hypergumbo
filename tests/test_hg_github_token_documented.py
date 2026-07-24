# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rot-guard for the GitHub maintainer-PAT token model (PR-D).

``scripts/lib/forgejo-api.sh``'s ``resolve_forge_token`` reads a dedicated
local PAT, ``HG_GITHUB_TOKEN``, when the github backend is active (the dormant
Codeberg→GitHub migration credential). Its own comment asserts the token is
"provisioned + documented in PR-D". This test locks that code→doc contract so
the documentation can't silently drift away from the code that depends on it:

  1. the lib actually reads ``HG_GITHUB_TOKEN`` (the anchor — if this moves the
     rest of the contract is moot and the test's premise is re-examined);
  2. ``.env.template`` documents it (the machine-facing "provisioned" surface a
     maintainer copies to ``.env``);
  3. AGENTS.md's §Secrets exception list names it (the governance surface —
     scripts are only permitted to read secrets enumerated there).

Pure file reads; no source import, so it neither contributes nor consumes
package coverage. It runs in the normal suite as the rot-guard the deferred
forced-backend CI job would otherwise carry.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "scripts" / "lib" / "forgejo-api.sh"
ENV_TEMPLATE = REPO_ROOT / ".env.template"
AGENTS_MD = REPO_ROOT / "AGENTS.md"

TOKEN = "HG_GITHUB_TOKEN"


def test_lib_reads_the_github_pat() -> None:
    # Anchor: the whole contract exists because resolve_forge_token reads it.
    assert TOKEN in LIB.read_text(), (
        f"{LIB} no longer reads {TOKEN}; the token model changed — revisit "
        f"this rot-guard's premise before deleting the docs it protects."
    )


def test_env_template_documents_the_github_pat() -> None:
    assert TOKEN in ENV_TEMPLATE.read_text(), (
        f"{ENV_TEMPLATE} must document {TOKEN} — forgejo-api.sh's "
        f"resolve_forge_token reads it for the github backend."
    )


def test_agents_md_secrets_exception_lists_the_github_pat() -> None:
    # The §Secrets bullet is the authoritative allowlist of secrets scripts may
    # read. HG_GITHUB_TOKEN must appear on it (alongside FORGEJO_TOKEN).
    secrets_line = next(
        (
            ln
            for ln in AGENTS_MD.read_text().splitlines()
            if ln.startswith("- **Secrets:**")
        ),
        None,
    )
    assert secrets_line is not None, "AGENTS.md §Secrets bullet not found"
    assert TOKEN in secrets_line, (
        f"AGENTS.md §Secrets exception must name {TOKEN} — scripts may only "
        f"read secrets enumerated there."
    )
