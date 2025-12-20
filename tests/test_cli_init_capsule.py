from pathlib import Path
import json
import subprocess
import sys


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

