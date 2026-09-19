# SPDX-License-Identifier: AGPL-3.0-or-later
"""The scip-python opt-in gate: the ADR-0045 chain plus the binary probe."""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_scip_python.gate import ENV_VAR_NAME, should_use_scip_python_backend


def test_env_var_name_is_the_shared_one() -> None:
    assert ENV_VAR_NAME == "HYPERGUMBO_SCIP_PYTHON"


def test_silent_tiers_do_not_run_it() -> None:
    assert should_use_scip_python_backend(environ={}, is_available=lambda: True) is False


def test_opted_in_and_installed_runs_it() -> None:
    assert should_use_scip_python_backend(environ={ENV_VAR_NAME: "1"}, is_available=lambda: True) is True


def test_opted_in_but_not_installed_falls_through_silently() -> None:
    assert should_use_scip_python_backend(environ={ENV_VAR_NAME: "1"}, is_available=lambda: False) is False


def test_the_flag_outranks_the_environment() -> None:
    assert should_use_scip_python_backend(backend_flag="tree-sitter", environ={ENV_VAR_NAME: "1"}, is_available=lambda: True) is False
    assert should_use_scip_python_backend(backend_flag="scip-python", environ={ENV_VAR_NAME: "0"}, is_available=lambda: True) is True


def test_a_project_config_is_the_durable_opt_in(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".hypergumbo.toml").write_text("[backends]\nscip_python = true\n")
    env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    assert should_use_scip_python_backend(environ=env, repo_root=repo, is_available=lambda: True) is True
    assert should_use_scip_python_backend(environ=env, repo_root=tmp_path, is_available=lambda: True) is False


def test_the_default_probe_is_the_real_one(monkeypatch) -> None:
    import hypergumbo_core.scip_python_install as install

    monkeypatch.setattr(install, "is_scip_python_available", lambda: True)
    assert should_use_scip_python_backend(environ={ENV_VAR_NAME: "1"}) is True
