# SPDX-License-Identifier: AGPL-3.0-or-later
"""User and project configuration files (ADR-0045 rulings 1, 2, 3).

WHAT THIS IS FOR, AND WHAT IT DELIBERATELY REFUSES TO HOLD. hypergumbo had no
user configuration of any kind before this module: every preference was
expressed per-invocation or through the environment, so a project with
authored I/O overlays retyped ``--io-primitives`` on every run. This adds two
tiers of TOML file for preferences like that.

It also, and more importantly, refuses to hold one particular kind of setting.
The SCIP Rust backend indexes by **executing the analysed crate's ``build.rs``
and proc macros as the invoking user**, so enabling it is not a preference —
it is a grant of arbitrary code execution. ``$XDG_CONFIG_HOME`` is a directory
people deliberately sync between machines and commit to dotfiles repositories;
that portability is the entire point of it, and it is disqualifying here. A
``rust_analyzer = true`` line in a synced config would opt every Rust
repository on every machine into running build scripts — the same hazard as
exporting the environment variable globally (WI-sobig), reached by a nicer
route. Those settings live in a separate trust store outside this file, and
this module raises rather than accepting them.

TWO RULES THAT CURRENTLY FORBID THE SAME KEY, AND ARE STILL TWO RULES.
:data:`TRUST_ONLY_SETTINGS` (ruling 2) is refused in **every** tier because the
fact does not belong in config at all. :data:`PROJECT_FORBIDDEN_SETTINGS`
(ruling 3) is refused only from the project tier, because a repository must not
be able to make a security decision *about itself* — the inversion of
ADR-0016 §35 / ADR-0017 §370, where project-local overlays deliberately
outrank built-ins (correct for catalogs, exactly backwards for a gate). Today
both sets name the same single setting. They are kept separate because
collapsing them would silently widen whichever rule outlived the other.

UNKNOWN KEYS RAISE. A silently-dropped setting is how someone spends an
afternoon believing a preference is in effect; the same reasoning ADR-0045
ruling 2 gives for rejecting a trust key loudly rather than ignoring it. The
cost is forward compatibility — an older hypergumbo reading a newer config
refuses it instead of degrading — and that is the tradeoff ADR-0045 OQ4
(schema versioning) is open on. Loud is the safer of the two failure modes
while it stays open.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, Mapping, Optional

#: Backends whose activation executes code from the analysed repository, and
#: whose opt-in is therefore a trust grant rather than a preference (ADR-0045
#: ruling 5). The bit is declared per backend rather than hardcoded at the
#: deny-list, so a backend added later lands in the right store by describing
#: itself instead of by someone remembering to update a list. ``pyright``
#: performs pure static inference and executes nothing, so when it arrives
#: (WI-nanom) it belongs on the ``False`` side and is an ordinary preference.
BACKENDS_EXECUTING_ANALYSED_CODE: FrozenSet[str] = frozenset({"rust_analyzer"})

#: Settings no config tier may carry, at all (ruling 2).
TRUST_ONLY_SETTINGS: FrozenSet[str] = frozenset(
    f"backends.{name}" for name in BACKENDS_EXECUTING_ANALYSED_CODE
)

#: Settings the PROJECT tier may not carry (ruling 3). A superset of the
#: above by construction: anything that cannot live in config at all
#: certainly cannot live in a file the analysed repository ships.
PROJECT_FORBIDDEN_SETTINGS: FrozenSet[str] = TRUST_ONLY_SETTINGS

#: Recognised settings and the type each must have. Membership is the
#: allow-list; see the module docstring on why an unknown key raises.
_KNOWN_SETTINGS: Dict[str, type] = {"io_primitives": list}

_USER_CONFIG_BASENAME = "config.toml"
#: NOT ``.hypergumbo/config.toml``. ``.hypergumbo`` is already an OUTPUT
#: artifact name carried in discovery.py's ignore list, and configuration
#: living inside an ignored output directory invites a cleanup step to delete
#: it.
_PROJECT_CONFIG_BASENAME = ".hypergumbo.toml"


class ConfigError(Exception):
    """A configuration file exists but cannot be honoured.

    Always names the offending file, because two tiers mean "which file did
    this come from?" is the reader's first question.
    """


@dataclass
class LayeredConfig:
    """Merged settings, with list-valued settings in ASCENDING precedence.

    Ascending rather than descending because the one consumer today,
    :func:`hypergumbo_core.cli._resolve_io_overlays`, documents that a later
    path wins on qualified-name collision — so concatenation order *is*
    precedence order, and reversing here would invert it silently.
    """

    io_primitives: "list[Path]" = field(default_factory=list)


def user_config_path(
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    """``$XDG_CONFIG_HOME/hypergumbo/config.toml``, or the XDG default.

    ``environ`` and ``home`` are injected rather than read from the process so
    tests do not have to mutate global state — the same shape
    :mod:`hypergumbo_core.safety_zones` already uses for ``XDG_CACHE_HOME``.
    """
    import os

    env = os.environ if environ is None else environ
    base = env.get("XDG_CONFIG_HOME")
    root = Path(base) if base else (home or Path.home()) / ".config"
    return root / "hypergumbo" / _USER_CONFIG_BASENAME


def project_config_path(repo_root: Path) -> Path:
    """``<repo>/.hypergumbo.toml`` (see :data:`_PROJECT_CONFIG_BASENAME`)."""
    return Path(repo_root) / _PROJECT_CONFIG_BASENAME


def _flatten(raw: Mapping[str, Any], prefix: str = "") -> Dict[str, Any]:
    """Dotted-key view of a nested TOML table.

    The deny-lists are expressed as dotted names (``backends.rust_analyzer``)
    so that one string identifies a setting regardless of whether the file
    spells it as a table or inline, and so an error message can quote back
    exactly what the user must remove.
    """
    flat: Dict[str, Any] = {}
    for key, value in raw.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, prefix=f"{dotted}."))
        else:
            flat[dotted] = value
    return flat


def _read_toml(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            return dict(tomllib.load(handle))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: not valid TOML — {exc}") from exc
    except OSError as exc:  # pragma: no cover - unreadable file, OS-dependent
        raise ConfigError(f"{path}: could not be read — {exc}") from exc


def _validate(flat: Mapping[str, Any], path: Path, *, is_project: bool) -> None:
    """Enforce rulings 2 and 3, then the allow-list. Order matters.

    The trust checks run FIRST so that a file carrying both a trust key and a
    typo reports the trust key: that is the one with a security consequence,
    and it is the one whose fix (move it to the trust store) is not obvious
    from the message about the typo.
    """
    for setting in sorted(flat):
        if setting in TRUST_ONLY_SETTINGS:
            raise ConfigError(
                f"{path}: '{setting}' cannot be set in a configuration file. "
                f"Enabling this backend runs the analysed repository's build "
                f"scripts as you, so it is a per-repository TRUST decision "
                f"and is recorded in hypergumbo's trust store, not in config "
                f"— a config file is designed to be copied between machines "
                f"and a trust grant must not be.",
            )
        if is_project and setting in PROJECT_FORBIDDEN_SETTINGS:
            # Unreachable while the two sets are equal; kept because they are
            # two rulings, and whichever outlives the other must still hold.
            raise ConfigError(  # pragma: no cover
                f"{path}: '{setting}' cannot be set by the repository being "
                f"analysed.",
            )
    for setting, value in sorted(flat.items()):
        expected = _KNOWN_SETTINGS.get(setting)
        if expected is None:
            known = ", ".join(sorted(_KNOWN_SETTINGS))
            raise ConfigError(
                f"{path}: unknown setting '{setting}'. Known settings: "
                f"{known}.",
            )
        if not isinstance(value, expected):
            raise ConfigError(
                f"{path}: '{setting}' must be a {expected.__name__}, got "
                f"{type(value).__name__}.",
            )


def _paths_from(flat: Mapping[str, Any], config_path: Path) -> "list[Path]":
    """Resolve a setting's relative paths against ITS OWN config file.

    Not against the working directory: with two tiers in play, a single
    cwd-relative rule silently resolves a project-relative path against
    wherever the user happened to be standing.
    """
    entries = flat.get("io_primitives") or []
    base = config_path.parent
    return [
        Path(entry) if Path(entry).is_absolute() else base / entry
        for entry in entries
    ]


def load_layered_config(
    *,
    repo_root: Path,
    environ: Optional[Mapping[str, str]] = None,
) -> LayeredConfig:
    """Load both tiers, validate each, and merge in ascending precedence.

    A missing file at either tier is not an error — the overwhelmingly common
    case is that neither exists.
    """
    user_path = user_config_path(environ=environ)
    proj_path = project_config_path(repo_root)

    user_flat = _flatten(_read_toml(user_path))
    _validate(user_flat, user_path, is_project=False)
    proj_flat = _flatten(_read_toml(proj_path))
    _validate(proj_flat, proj_path, is_project=True)

    return LayeredConfig(
        io_primitives=(
            _paths_from(user_flat, user_path)
            + _paths_from(proj_flat, proj_path)
        ),
    )
