# SPDX-License-Identifier: AGPL-3.0-or-later
"""release-check's gates that used to run only after the tag (WI-putib).

The 10.0.0 release took three attempts from "release-check passed" to
"published": the I/O manifest was first compared by the release workflow, on
the tag; the dependency audit judged this box's history instead of a fresh
install; and the wheel check warned on every run, so it could not report a
real failure. What is pinned, and what each would catch:

    the audit's fresh environment installs what release.yml's       the mirror
    security-audit job installs (packages and pins)                   claim
    a failed environment build fails the audit and never falls      no box
    back to this box's pip-audit; a built one is the one asked        fallback
    the wheels install with --find-links dist/; a failed install    a check that
    or a missing wheel fails, never warns                             can fail
    the default package set is release.yml's io-manifest set        the mirror
    the I/O check runs the installed set with the manifest's        before the
    arguments, fails on a difference, says so without strace          tag
    the handoff dispatches a dry run before tag-release             the order

Each executed test runs the block extracted from the real script, between
its ``# >>>`` / ``# <<<`` markers, under the script's ``set -euo pipefail``,
with ``pass`` / ``warn`` / ``fail`` stubbed to echoes and stub programs on
PATH: the gates' wiring is checked, not pip or strace.
"""
# covers: scripts/release-check, scripts/prepare-release

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE_CHECK = REPO_ROOT / "scripts" / "release-check"
RELEASE_YML = REPO_ROOT / ".github" / "workflows" / "release.yml"

STUBS = 'set -euo pipefail\nFAILED=0\npass() { echo "PASS: $1"; }\n' \
        'warn() { echo "WARN: $1"; }\nfail() { echo "FAIL: $1"; FAILED=$((FAILED + 1)); }\n' \
        'section() { echo "SECTION: $1"; }\n'


def _between(start: str, end: str) -> str:
    text = RELEASE_CHECK.read_text()
    i = text.index(start)
    return text[i:text.index(end, i) + len(end)]


def _job(name: str) -> str:
    """One job of release.yml: from ``  <name>:`` to the next top-level job."""
    text = RELEASE_YML.read_text()
    start = text.index(f"\n  {name}:\n")
    nxt = re.search(r"\n  [a-z][a-z-]*:\n", text[start + 1:])
    return text[start:start + 1 + nxt.start()] if nxt else text[start:]


def _install_spec(text: str) -> tuple[set[str], set[str], set[str]]:
    """(editable packages, quoted requirements, bare names) of every
    ``pip install`` command in ``text``: continuation lines joined, comments
    dropped."""
    editable: set[str] = set()
    quoted: set[str] = set()
    bare: set[str] = set()
    found = 0
    for line in re.sub(r"\\\n", " ", text).splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = re.search(r'pip"? install (.*)', line.split(" #", 1)[0])
        if not match:
            continue
        found += 1
        rest = match.group(1)
        editable |= {e.strip('"') for e in re.findall(r'-e\s+("[^"]+"|\S+)', rest)}
        rest = re.sub(r'-e\s+("[^"]+"|\S+)', "", rest)
        quoted |= set(re.findall(r'"([^"]+)"', rest))
        bare |= {w for w in re.sub(r'"[^"]+"', "", rest).split()
                 if re.fullmatch(r"[A-Za-z][\w.-]*", w)}
    assert found, "reach: no pip install line found"
    return editable, quoted, bare


def _run(script: str, tmp_path: Path, env: dict[str, str] | None = None
         ) -> subprocess.CompletedProcess[str]:
    harness = tmp_path / "harness.sh"
    harness.write_text(script)
    return subprocess.run(["/bin/bash", str(harness)], capture_output=True, text=True,
                          cwd=tmp_path, env=env or {"PATH": "/usr/bin:/bin",
                                                    "HOME": str(tmp_path)})


def _stub(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)
    return path


# ── the audit's fresh environment ───────────────────────────────────────────

def test_the_fresh_audit_environment_installs_what_the_release_audit_installs() -> None:
    ours = _install_spec(_between("# >>> fresh audit environment",
                                  "# <<< fresh audit environment"))
    editable, quoted, bare = _install_spec(_job("security-audit").split("Vulnerability scan")[0])
    # release.yml keeps a branch for the pre-package layout (`pip install -e .[dev]`),
    # never taken in this tree.
    assert ours == (editable - {".[dev]"}, quoted, bare)
    editable, quoted, bare = ours
    assert "packages/hypergumbo-core[dev]" in editable and "urllib3>=2.8.0" in quoted
    assert "pip-audit" in bare


AUDIT_BLOCK = ('AUDIT_SAVED_PATH=$PATH', 'rm -rf "$AUDIT_DIR"\n')


def test_a_failed_environment_fails_the_audit_and_never_asks_this_box(tmp_path: Path) -> None:
    box = _stub(tmp_path / "box" / "pip-audit", 'echo "BOX AUDIT"; exit 0\n')
    venv = _stub(tmp_path / "audit" / "venv" / "bin" / "pip-audit", 'echo "FRESH AUDIT"; exit 0\n')
    (tmp_path / "audit" / "setup.log").write_text("ERROR: no matching distribution\n")
    block = _between(*AUDIT_BLOCK)
    env = {"PATH": f"{box.parent}:/usr/bin:/bin", "HOME": str(tmp_path)}
    failed = _run(STUBS + f'AUDIT_DIR={tmp_path / "audit"}\nAUDIT_ENV_OK=0\n' + block
                  + 'echo "AFTER: $PATH"\n', tmp_path, env)
    assert "BOX AUDIT" not in failed.stdout and "FRESH AUDIT" not in failed.stdout
    assert "ERROR: no matching distribution" in failed.stdout
    assert "pip-audit not run: the fresh audit environment did not build" in failed.stdout
    assert "FAIL: Vulnerabilities detected (pip-audit exit 2)" in failed.stdout
    (tmp_path / "audit" / "venv" / "bin").mkdir(parents=True, exist_ok=True)
    venv = _stub(tmp_path / "audit" / "venv" / "bin" / "pip-audit", 'echo "FRESH AUDIT"; exit 0\n')
    ok = _run(STUBS + f'AUDIT_DIR={tmp_path / "audit"}\nAUDIT_ENV_OK=1\n' + block
              + 'echo "AFTER: $PATH"\n', tmp_path, env)
    assert "PASS: No known vulnerabilities in dependencies" in ok.stdout
    assert f"AFTER: {box.parent}:/usr/bin:/bin" in ok.stdout  # PATH restored
    assert not venv.exists()  # the environment is removed after the audit


# ── the wheels ──────────────────────────────────────────────────────────────

WHEEL_BLOCK = ("# >>> wheel install", "# <<< wheel install")
DEFAULT = ["hypergumbo_core", "hypergumbo_lang_mainstream", "hypergumbo_lang_common",
           "hypergumbo_lang_extended1", "hypergumbo"]
OTHERS = ["hypergumbo_lang_rust_analyzer", "hypergumbo_lang_scip_python", "hypergumbo_tracker"]


def _wheels(tmp_path: Path, names: list[str]) -> None:
    (tmp_path / "dist").mkdir(exist_ok=True)
    for name in names:
        (tmp_path / "dist" / f"{name}-10.1.0-py3-none-any.whl").write_text("")


def _fake_python(tmp_path: Path, pip_fails: str = "") -> Path:
    """``python -m venv DIR`` makes DIR/bin/pip, which logs its arguments and
    fails without --find-links dist/ (a release's own versions are not on
    the index), or when an argument matches ``pip_fails``."""
    return _stub(tmp_path / "bin" / "python3", f'''[[ "$1 $2" == "-m venv" ]] || exit 9
mkdir -p "$3/bin"
cat > "$3/bin/pip" <<'EOF'
#!/bin/bash
echo "pip $*" >> {tmp_path}/pip.calls
[[ " $* " == *" --find-links dist/ "* ]] || {{ echo "No matching distribution for hypergumbo-core==10.1.0"; exit 1; }}
[[ -n "{pip_fails}" && "$*" == *"{pip_fails}"* ]] && {{ echo "pip refused {pip_fails}"; exit 1; }}
exit 0
EOF
chmod +x "$3/bin/pip"
''')


def _wheel_run(tmp_path: Path, **kw: str) -> subprocess.CompletedProcess[str]:
    python = _fake_python(tmp_path, kw.get("pip_fails", ""))
    return _run(STUBS + f"WHEEL_PY={python}\n" + _between(*WHEEL_BLOCK)
                + '\necho "VENV: ${WHEEL_VENV:-none}"\n', tmp_path)


def test_the_wheels_install_from_dist_and_the_rest_resolve_against_them(tmp_path: Path) -> None:
    _wheels(tmp_path, DEFAULT + OTHERS)
    out = _wheel_run(tmp_path).stdout
    assert "PASS:   The default package set installs from the built wheels" in out
    for name in ("hypergumbo-lang-rust-analyzer", "hypergumbo-lang-scip-python",
                 "hypergumbo-tracker"):
        assert f"PASS:   {name} can be installed" in out
    assert "FAIL" not in out and "VENV: " in out and "VENV: none" not in out
    calls = (tmp_path / "pip.calls").read_text().splitlines()
    assert calls[0] == "pip install --find-links dist/ " + " ".join(
        f"dist/{n}-10.1.0-py3-none-any.whl" for n in DEFAULT)
    assert all("--dry-run --find-links dist/" in c for c in calls[1:]) and len(calls) == 4


def test_a_wheel_that_does_not_install_fails_and_a_missing_one_is_named(tmp_path: Path) -> None:
    _wheels(tmp_path, DEFAULT[:-1] + OTHERS)
    out = _wheel_run(tmp_path, pip_fails="hypergumbo_tracker").stdout
    assert "FAIL:   No wheel in dist/ for hypergumbo" in out
    assert "pip refused hypergumbo_tracker" in out
    assert "FAIL:   hypergumbo-tracker cannot be installed from the built wheels" in out
    assert not re.search(r"^\s*warn ", _between(*WHEEL_BLOCK), re.M)  # it fails, never warns
    broken = tmp_path / "broken"
    broken.mkdir()
    _wheels(broken, DEFAULT + OTHERS)
    out = _wheel_run(broken, pip_fails="hypergumbo_core").stdout
    assert "FAIL:   The default package set does not install from the built wheels" in out
    assert "VENV: none" in out
    # Nothing else is checked against an environment that did not install.
    assert "can be installed" not in out


def test_the_default_set_is_what_the_io_manifest_job_installs() -> None:
    block = _between(*WHEEL_BLOCK)
    ours = re.findall(r'"([^"]+)"', re.search(r"DEFAULT_SET=\(([^)]*)\)", block).group(1))
    theirs = re.findall(r"packages/(\S+)", _job("io-manifest").split("Trace the corpus")[0])
    assert ours == theirs and len(ours) == 5


# ── the I/O manifest ────────────────────────────────────────────────────────

IOM_BLOCK = ("# >>> io-manifest gate", "# <<< io-manifest gate")


def _iom(tmp_path: Path, *, strace: bool, venv: str, exit_code: int = 0,
         corpus_root: str = "") -> str:
    bindir = tmp_path / "bin"
    if strace:
        _stub(bindir / "strace", "exit 0\n")
    iom = _stub(tmp_path / "io-manifest", f'echo "io-manifest $*"\necho "+ write\t<repo>/x"\n'
                                          f"exit {exit_code}\n")
    # Without strace, PATH is the stub directory alone: this machine has /usr/bin/strace.
    path = f"{bindir}:/usr/bin:/bin" if strace else str(bindir)
    env = {"PATH": path, "HOME": str(tmp_path),
           "IO_MANIFEST": str(iom), "IO_MANIFEST_CORPUS_ROOT": corpus_root}
    return _run(STUBS + f"WHEEL_DIR={tmp_path}\nWHEEL_VENV={venv}\n" + _between(*IOM_BLOCK),
                tmp_path, env).stdout


def test_the_io_check_runs_the_installed_set_before_the_tag(tmp_path: Path) -> None:
    out = _iom(tmp_path, strace=True, venv="/v", corpus_root="/clones")
    assert "PASS: The observed I/O matches docs/io-manifest/expected.txt" in out
    log = (tmp_path / "io-manifest.log").read_text()
    assert ("io-manifest run --corpus docs/io-manifest/corpus.txt --expected "
            f"docs/io-manifest/expected.txt --out {tmp_path}/io-manifest --hypergumbo "
            "/v/bin/hypergumbo --path /usr/local/bin:/usr/bin:/bin --corpus-root /clones") in log
    plain = _iom(tmp_path / "a", strace=True, venv="/v")
    assert "PASS" in plain and "--corpus-root" not in (tmp_path / "a" / "io-manifest.log").read_text()


def test_a_difference_fails_and_no_strace_or_no_install_says_so(tmp_path: Path) -> None:
    differs = _iom(tmp_path, strace=True, venv="/v", exit_code=1)
    assert "+ write\t<repo>/x" in differs
    assert "FAIL: The observed I/O differs from docs/io-manifest/expected.txt" in differs
    assert "(io-manifest exit 1; above)" in differs
    absent = _iom(tmp_path / "b", strace=False, venv="/v")
    assert absent.startswith("WARN: strace is not installed") and "dry_run=true on main" in absent
    uninstalled = _iom(tmp_path / "c", strace=True, venv="")
    assert "FAIL: The I/O manifest was not compared: the default package set did not install" \
        in uninstalled
    assert not (tmp_path / "c" / "io-manifest.log").exists()


def test_the_handoff_dispatches_a_dry_run_before_tag_release() -> None:
    text = (REPO_ROOT / "scripts" / "prepare-release").read_text()
    handoff = text[text.index("HANDOFF TO HUMAN"):]
    assert handoff.index("dry_run=true") < handoff.index("./scripts/tag-release")
    header = text[:text.index("set -euo pipefail")]
    assert header.index("dry_run=true on main") < header.index("tag-release VERSION")
