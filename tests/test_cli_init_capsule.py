from pathlib import Path
import json
import subprocess
import sys
from unittest import mock


from hypergumbo.cli import cmd_init
from hypergumbo.llm_assist import LLMBackend, LLMResult
from hypergumbo.plan import CapsulePlan, PassConfig, PLAN_VERSION


def test_init_creates_capsule_config(tmp_path: Path) -> None:
    project_root = tmp_path

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hypergumbo",
            "init",
            str(project_root),
            "--capabilities",
            "python,javascript",
            "--assistant",
            "template",
            "--llm-input",
            "tier0",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
    )

    # Help debug if init exits non-zero
    assert result.returncode == 0, f"stderr was:\n{result.stderr}"

    capsule_dir = project_root / ".hypergumbo"
    capsule_path = capsule_dir / "capsule.json"

    assert capsule_path.exists(), "capsule.json was not created by init"

    data = json.loads(capsule_path.read_text())
    assert data["assistant"] == "template"
    assert data["llm_input"] == "tier0"
    assert data["capabilities"] == ["python", "javascript"]


def test_init_with_llm_success(tmp_path: Path) -> None:
    """Test init with --assistant llm when LLM succeeds."""
    import argparse

    # Create a successful LLM result
    llm_plan = CapsulePlan(
        version=PLAN_VERSION,
        passes=[PassConfig(id="python-ast-v1", enabled=True)],
        packs=[],
        rules=[],
        features=[],
    )
    llm_result = LLMResult(
        success=True,
        plan=llm_plan,
        raw_response='{"version": "0.1.0"}',
        backend_used=LLMBackend.OPENROUTER,
        model_used="mistralai/devstral-2512:free",
    )

    with mock.patch(
        "hypergumbo.cli.generate_plan_with_fallback",
        return_value=(llm_plan, llm_result),
    ):
        args = argparse.Namespace(
            path=str(tmp_path),
            capabilities="",
            assistant="llm",
            llm_input="tier0",
        )
        result = cmd_init(args)

    assert result == 0

    capsule_path = tmp_path / ".hypergumbo" / "capsule.json"
    assert capsule_path.exists()

    data = json.loads(capsule_path.read_text())
    assert data["assistant"] == "llm"
    assert "generator" in data
    assert data["generator"]["mode"] == "llm_assisted"
    assert data["generator"]["backend"] == "openrouter"
    assert data["generator"]["model"] == "mistralai/devstral-2512:free"


def test_init_with_llm_fallback(tmp_path: Path) -> None:
    """Test init with --assistant llm when LLM fails and falls back."""
    import argparse

    from hypergumbo.plan import generate_plan
    from hypergumbo.profile import detect_profile
    from hypergumbo.catalog import get_default_catalog

    # Create a failed LLM result
    llm_result = LLMResult(
        success=False,
        plan=None,
        error="API error: connection timeout",
        backend_used=LLMBackend.OPENROUTER,
        model_used="mistralai/devstral-2512:free",
    )

    # The fallback plan
    profile = detect_profile(tmp_path)
    catalog = get_default_catalog()
    template_plan = generate_plan(profile, catalog)

    with mock.patch(
        "hypergumbo.cli.generate_plan_with_fallback",
        return_value=(template_plan, llm_result),
    ):
        args = argparse.Namespace(
            path=str(tmp_path),
            capabilities="",
            assistant="llm",
            llm_input="tier0",
        )
        result = cmd_init(args)

    assert result == 0

    capsule_path = tmp_path / ".hypergumbo" / "capsule.json"
    assert capsule_path.exists()

    data = json.loads(capsule_path.read_text())
    assert data["assistant"] == "llm"
    assert "generator" in data
    assert data["generator"]["mode"] == "template_fallback"
    assert data["generator"]["fallback_reason"] == "API error: connection timeout"

