# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-kuroj: every installed-CLI write goes through a declared zone wrapper.

Five runtime writes used raw ``Path.mkdir`` / ``write_text`` / ``chmod`` into
places no self-catalogue zone declared, so the self-proof saw them only as
``untyped_receiver`` caveat counts:

    backend_trust.record_decision                 $XDG_STATE_HOME/hypergumbo/trust.d/
    in_repo_catalogues.record_in_repo_decision    $XDG_STATE_HOME/hypergumbo/<store>/
    repo_tier_offer.record_offer_decision         $XDG_STATE_HOME/hypergumbo/offers.json
    repo_tier_offer.write_repo_tier_examples      ~/hypergumbo/.hypergumbo-examples/
    catalogue_home.materialize_catalogue_home     $XDG_CONFIG_HOME/hypergumbo/ or --home

They now go through ``user_state_*``, ``user_config_*`` and
``developer_checkout_*`` wrappers whose zones are declared in
``docs/hypergumbo-self-catalog/``, and the handlers that reach them -- six
``cmd_*`` functions no ``*_sources.yaml`` named, plus the repo-tier offer that
``main()`` runs after every subcommand -- are runtime taint sources.

THE USER-FACING HALF. Analysing ``~/hypergumbo`` itself and answering "y" wrote
three files into the analysed repository while the prompt said "Nothing is
written into any repository you analyse". The offer is now not made when the
analysed path and the developer checkout overlap, so the promise holds.
"""
from __future__ import annotations

import ast
import inspect
import os
import stat
from pathlib import Path
from typing import Any

import pytest
import yaml

from hypergumbo_core import safety_zones

_DOCS = Path(__file__).resolve().parents[3] / "docs"
_CATALOG = _DOCS / "hypergumbo-self-catalog"

#: zone -> its wrappers, as the zone files must declare them.
_ZONES = {
    "user_state": ["user_state_write"],
    "user_config": ["user_config_mkdir", "user_config_write"],
    "developer_checkout": ["developer_checkout_write"],
}
_WRAPPERS = [w for ws in _ZONES.values() for w in ws]


@pytest.mark.parametrize("wrapper", _WRAPPERS)
def test_the_wrapper_invokes_the_zone_barrier_once(wrapper: str) -> None:
    tree = ast.parse(inspect.getsource(getattr(safety_zones, wrapper)).lstrip())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "_safety_zone_barrier"]
    assert len(calls) == 1


def test_user_state_write_creates_parents_and_honours_private(tmp_path: Path) -> None:
    secret = tmp_path / "a" / "b" / "grant.json"
    safety_zones.user_state_write(secret, "{}", private=True)
    assert secret.read_text() == "{}"
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    plain = tmp_path / "c" / "offers.json"
    old_umask = os.umask(0o022)  # pinned, so "not forced to 0o600" is observable
    try:
        safety_zones.user_state_write(plain, "{}", private=False)
    finally:
        os.umask(old_umask)
    assert plain.read_text() == "{}"
    assert stat.S_IMODE(plain.stat().st_mode) == 0o644


def test_the_config_and_checkout_wrappers_write(tmp_path: Path) -> None:
    safety_zones.user_config_mkdir(tmp_path / "cfg" / "d")
    assert (tmp_path / "cfg" / "d").is_dir()
    safety_zones.user_config_write(tmp_path / "cfg" / "README.md", "x")
    assert (tmp_path / "cfg" / "README.md").read_text() == "x"
    safety_zones.developer_checkout_write(tmp_path / "co" / "e" / "f.yaml", "y")
    assert (tmp_path / "co" / "e" / "f.yaml").read_text() == "y"


@pytest.mark.parametrize("zone", sorted(_ZONES))
def test_the_self_catalogue_declares_the_zone(zone: str) -> None:
    doc = yaml.safe_load((_CATALOG / f"{zone}_sinks.yaml").read_text())
    assert doc["zone"] == zone
    (entry,) = doc["sinks"]["python"]
    assert entry["module"] == "hypergumbo_core.safety_zones"
    assert sorted(entry["functions"]) == sorted(_ZONES[zone])
    claims = yaml.safe_load((_DOCS / "hypergumbo.claims.yaml").read_text())
    assert f"hypergumbo-self-catalog/{zone}_sinks.yaml" in claims["extra_catalogs"]["sinks"]


def test_every_writing_handler_is_a_runtime_source() -> None:
    doc = yaml.safe_load((_CATALOG / "runtime_cli_sources.yaml").read_text())
    by_module = {e["module"]: set(e["functions"]) for e in doc["sources"]["python"]}
    assert {"cmd_trust_backend", "cmd_trust_catalogues", "cmd_init_catalogs",
            "cmd_backend_agreement", "cmd_repeat_finder",
            "cmd_catalog_inventory"} <= by_module["hypergumbo_core.cli"]
    assert "maybe_offer_repo_tier_examples" in by_module["hypergumbo_core.repo_tier_offer"]


def test_every_cmd_handler_is_catalogued_somewhere() -> None:
    """The enumeration that found the six, kept as a gate: a new ``cmd_*`` that
    no ``*_sources.yaml`` names is outside every claim."""
    from hypergumbo_core import cli

    named: set[str] = set()
    for f in _CATALOG.glob("*_sources.yaml"):
        for entry in yaml.safe_load(f.read_text())["sources"]["python"]:
            named |= set(entry["functions"])
    handlers = {n for n, obj in vars(cli).items()
                if n.startswith("cmd_") and inspect.isfunction(obj)}
    assert sorted(handlers - named) == []


# ------------------------------------------------- each site uses its zone ---

def _record(monkeypatch: pytest.MonkeyPatch, module: Any, name: str) -> "list[Path]":
    seen: "list[Path]" = []
    real = getattr(module, name)

    def spy(path: Path, *args: Any, **kwargs: Any) -> Any:
        seen.append(Path(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(module, name, spy)
    return seen


def test_a_trust_grant_is_a_private_user_state_write(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hypergumbo_core import backend_trust

    seen = _record(monkeypatch, backend_trust, "user_state_write")
    repo = tmp_path / "repo"
    repo.mkdir()
    backend_trust.record_decision(repo, "rust_analyzer", True,
                                  environ={"XDG_STATE_HOME": str(tmp_path / "st")})
    (path,) = seen
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_an_in_repo_catalogue_grant_is_a_user_state_write(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hypergumbo_core import in_repo_catalogues

    seen = _record(monkeypatch, in_repo_catalogues, "user_state_write")
    in_repo_catalogues.record_in_repo_decision(
        tmp_path, True, environ={"XDG_STATE_HOME": str(tmp_path / "st")})
    assert len(seen) == 1


def test_the_offer_decision_and_examples_use_their_zones(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hypergumbo_core import repo_tier_offer

    state = _record(monkeypatch, repo_tier_offer, "user_state_write")
    checkout = _record(monkeypatch, repo_tier_offer, "developer_checkout_write")
    home = tmp_path / "home"
    (home / "hypergumbo").mkdir(parents=True)
    repo_tier_offer.maybe_offer_repo_tier_examples(
        environ={"XDG_STATE_HOME": str(tmp_path / "st")}, home=home,
        interactive=True, ask=lambda _: "y", analysed=tmp_path / "elsewhere")
    assert len(state) == 1
    assert len(checkout) == 3


def test_init_catalogs_uses_the_user_config_zone(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hypergumbo_core import catalogue_home

    writes = _record(monkeypatch, catalogue_home, "user_config_write")
    dirs = _record(monkeypatch, catalogue_home, "user_config_mkdir")
    catalogue_home.materialize_catalogue_home(tmp_path / "cfg", version="0")
    assert writes and dirs


# ------------------------------------- the promise the prompt makes, kept ----

@pytest.fixture()
def home(tmp_path: Path) -> Path:
    (tmp_path / "home" / "hypergumbo" / "sub").mkdir(parents=True)
    return tmp_path / "home"


def _explode(_prompt: str) -> str:
    raise AssertionError("offered while analysing the checkout")


@pytest.mark.parametrize("rel", ["hypergumbo", "hypergumbo/sub", "."],
                         ids=["the-checkout", "inside-it", "its-parent"])
def test_no_offer_when_the_analysed_path_overlaps_the_checkout(
        tmp_path: Path, home: Path, rel: str) -> None:
    from hypergumbo_core import repo_tier_offer

    assert repo_tier_offer.maybe_offer_repo_tier_examples(
        environ={"XDG_STATE_HOME": str(tmp_path / "st")}, home=home,
        interactive=True, ask=_explode, analysed=home / rel) is None
    assert not repo_tier_offer.examples_destination(home).exists()


def test_an_unrelated_analysed_path_is_still_offered(tmp_path: Path, home: Path) -> None:
    from hypergumbo_core import repo_tier_offer

    other = tmp_path / "other"
    other.mkdir()
    assert repo_tier_offer.maybe_offer_repo_tier_examples(
        environ={"XDG_STATE_HOME": str(tmp_path / "st")}, home=home,
        interactive=True, ask=lambda _: "n", analysed=other) is False


def test_main_hands_the_analysed_path_to_the_offer(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hypergumbo_core import cli

    seen: "list[Any]" = []
    monkeypatch.setattr(cli, "maybe_offer_repo_tier_examples",
                        lambda **kw: seen.append(kw.get("analysed")))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    cli.main(["symbols", str(repo)])
    assert seen == [repo]
