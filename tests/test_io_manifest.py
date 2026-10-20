# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for scripts/io-manifest, the observed-I/O release gate.

The gate runs the installed CLI over a pinned corpus under ``strace`` and
compares the reduced set of writes, deletes, renames, launches and connects
with ``docs/io-manifest/expected.txt``. These tests pin, without running
strace:

* the REDUCER -- which syscall lines become which manifest lines, read
  opens dropped, ``<unfinished>`` halves kept, resumed halves and signal
  lines ignored, and paths normalised into zone tokens with the longest
  root winning;
* the CHECK -- a new line fails, a missing line is a note, and the two hard
  rules (a write under ``<repo>/``, an ``AF_INET`` connect) fail even when
  the expected file lists them;
* the COMMITTED FILES -- the corpus parses with full pins, the expected
  manifest cannot bless either hard rule, and ``release.yml`` runs the gate
  and makes ``build-and-publish`` wait for it.
"""

# covers: scripts/io-manifest, docs/io-manifest/corpus.txt, docs/io-manifest/expected.txt, .github/workflows/release.yml
from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "io-manifest"
DOCS = REPO_ROOT / "docs" / "io-manifest"


def _load():
    loader = importlib.machinery.SourceFileLoader("io_manifest", str(SCRIPT))
    spec = importlib.util.spec_from_loader("io_manifest", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["io_manifest"] = mod
    loader.exec_module(mod)
    return mod


iom = _load()

OUT = "/scratch/iom"
ZONES = iom.Zones(
    repos={"alpha": "/corpus/alpha", "beta": "/corpus/beta"},
    out=OUT, cache=f"{OUT}/cache", tmp=f"{OUT}/tmp", home=f"{OUT}/home",
    python="/venv",
)


def _r(line: str) -> str | None:
    return iom.reduce_line(ZONES, line)


# --- corpus -----------------------------------------------------------------


def test_corpus_parses_pins_options_and_comments():
    text = (
        "# header\n"
        "alpha  o/alpha  " + "a" * 40 + "  slice=src/main.py  # py\n"
        "\n"
        "beta   o/beta   " + "b" * 40 + "\n"
    )
    entries = iom.parse_corpus(text)
    assert [e.name for e in entries] == ["alpha", "beta"]
    assert entries[0].slice_entry == "src/main.py"
    assert entries[1].slice_entry is None
    assert entries[0].url == "https://github.com/o/alpha.git"


@pytest.mark.parametrize("bad", [
    "alpha o/alpha",                       # too short
    "alpha o/alpha notasha",               # not hex
    "alpha o/alpha " + "a" * 8,            # too short a sha
    "alpha o/alpha " + "a" * 40 + " depth=1",  # unknown option
    "alpha o/alpha " + "a" * 40 + "\nalpha o/alpha2 " + "b" * 40,  # duplicate
])
def test_corpus_rejects_malformed_lines(bad):
    with pytest.raises(ValueError):
        iom.parse_corpus(bad)


# --- normalisation ------------------------------------------------------------


@pytest.mark.parametrize("path,expected", [
    ("/corpus/alpha/src/x.py", "<repo>/src/x.py"),
    ("/corpus/beta", "<repo>"),
    (f"{OUT}/cache/hypergumbo/f28d/results/abc/survey.json", "<cache>/hypergumbo/..."),
    (f"{OUT}/cache", "<cache>"),
    (f"{OUT}/tmp/tmpab12/x", "<tmp>/..."),
    (f"{OUT}/home/.local/state/hypergumbo/offers.json", "<home>/.local/state/hypergumbo/offers.json"),
    (f"{OUT}/work/alpha.survey.json", "<out>/..."),
    (OUT, "<out>"),
    ("/venv/lib/python3.11/site-packages/x/__pycache__/y.pyc",
     "<python>/lib/python3.11/site-packages/x/__pycache__/y.pyc"),
    ("/dev/shm/sem.4olWzl", "/dev/shm/sem.*"),
    ("/dev/null", "/dev/null"),
    ("relative/file", "<rel>/relative/file"),
    ("/etc/passwd", "/etc/passwd"),
    ("//corpus//alpha/./x", "<repo>/./x"),
])
def test_norm_tokenises_zones(path, expected):
    assert ZONES.norm(path) == expected


def test_norm_prefers_the_longest_root():
    """``cache`` lives under ``out``; it must not be reported as ``<out>``."""
    assert ZONES.norm(f"{OUT}/cache/hypergumbo/x").startswith("<cache>")
    assert ZONES.norm(f"{OUT}/home/x") == "<home>/x"


# --- reduction ----------------------------------------------------------------


def test_read_opens_are_dropped_and_write_opens_kept():
    assert _r('1 openat(AT_FDCWD, "/corpus/alpha/a.py", O_RDONLY|O_CLOEXEC) = 3') is None
    assert _r(f'1 openat(AT_FDCWD, "{OUT}/cache/hypergumbo/k/v", O_WRONLY|O_CREAT|O_TRUNC|O_CLOEXEC, 0666) = 4') == "write\t<cache>/hypergumbo/..."
    assert _r('1 openat(AT_FDCWD, "/dev/shm/sem.x", O_RDWR|O_CREAT|O_EXCL, 0600) = 3') == "write\t/dev/shm/sem.*"
    assert _r('1 creat("/corpus/beta/out", 0644) = 5') == "write\t<repo>/out"


def test_failed_attempts_are_still_recorded():
    line = '7 unlinkat(AT_FDCWD, "/dev/stdout", 0) = -1 EACCES (Permission denied)'
    assert _r(line) == "unlink\t/dev/stdout"


def test_directory_and_rename_ops():
    assert _r(f'1 mkdir("{OUT}/tmp/tmpq", 0700) = 0') == "mkdir\t<tmp>/..."
    assert _r('1 unlinkat(AT_FDCWD, "/corpus/alpha/d", AT_REMOVEDIR) = 0') == "rmdir\t<repo>/d"
    assert _r('1 unlink("/corpus/alpha/f") = 0') == "unlink\t<repo>/f"
    assert _r(f'1 rename("{OUT}/tmp/a", "{OUT}/cache/hypergumbo/b") = 0') == "rename\t<tmp>/... -> <cache>/hypergumbo/..."
    assert _r(f'1 renameat2(AT_FDCWD, "{OUT}/tmp/a", AT_FDCWD, "{OUT}/work/o.json", RENAME_NOREPLACE) = 0') == "rename\t<tmp>/... -> <out>/..."
    assert _r('1 symlinkat("/target", AT_FDCWD, "/corpus/alpha/link") = 0') == "symlink\t<repo>/link -> /target"
    assert _r('1 fchmodat(AT_FDCWD, "/corpus/alpha/x", 0755) = 0') == "chmod\t<repo>/x"
    assert _r('1 truncate("/corpus/alpha/x", 0) = 0') == "truncate\t<repo>/x"


def test_exec_shows_program_and_normalised_arguments():
    line = ('1 execve("/usr/bin/git", ["git", "-C", "/corpus/alpha", "rev-parse", "HEAD"], '
            '0x7ffd /* 12 vars */) = 0')
    assert _r(line) == "exec\tgit -C <repo> rev-parse HEAD"
    sha = "0a88cccd5188" + "0" * 28
    ten = '", "'.join(["cat-file", "-p", sha, "a", "b", "c", "d", "e", "f", "g"])
    assert _r(f'1 execve("/usr/bin/git", ["git", "{ten}"], 0x1 /* 1 var */) = 0') == "exec\tgit cat-file -p <sha> a b c d e"


def test_connect_families():
    assert _r('1 connect(3, {sa_family=AF_INET, sin_port=htons(443), sin_addr=inet_addr("140.82.112.3")}, 16) = 0') == "connect\tAF_INET <ip>:443"
    assert _r('1 connect(3, {sa_family=AF_INET6, sin6_port=htons(80), sin6_flowinfo=htonl(0), inet_pton(AF_INET6, "::1", &sin6_addr), sin6_scope_id=0}, 28) = 0') == "connect\tAF_INET6 <ip>:80"
    assert _r('1 connect(4, {sa_family=AF_UNIX, sun_path="/var/run/nscd/socket"}, 110) = -1 ENOENT (No such file)') == "connect\tAF_UNIX /var/run/nscd/socket"
    assert _r('1 connect(4, {sa_family=AF_UNIX, sun_path=@"abstract"}, 10) = 0') == "connect\tAF_UNIX @abstract"
    assert _r('1 connect(4, {sa_family=AF_NETLINK, nl_pid=0, nl_groups=0}, 12) = 0') == "connect\tAF_NETLINK"


def test_unfinished_half_counts_and_resumed_half_does_not():
    assert _r(f'1 openat(AT_FDCWD, "{OUT}/tmp/x", O_WRONLY|O_CREAT <unfinished ...>') == "write\t<tmp>/..."
    assert _r("1 <... openat resumed>) = 5") is None
    assert _r("1 +++ exited with 0 +++") is None
    assert _r("1 --- SIGCHLD {si_signo=SIGCHLD} ---") is None
    assert _r("") is None
    assert _r('[pid 12] mkdir("/corpus/alpha/n", 0777) = 0') == "mkdir\t<repo>/n"


def test_reduce_traces_groups_lines_by_run_and_renders_sorted():
    traces = {
        "alpha.sketch": f'1 mkdir("{OUT}/cache/hypergumbo/x", 0777) = 0\n1 openat(AT_FDCWD, "/corpus/alpha/a", O_RDONLY) = 3\n',
        "beta.sketch": f'2 mkdir("{OUT}/cache/hypergumbo/y", 0777) = 0\n2 execve("/usr/bin/git", ["git", "rev-parse", "HEAD"], 0x1 /* 1 var */) = 0\n',
    }
    observed = iom.reduce_traces(ZONES, traces)
    assert observed == {
        "mkdir\t<cache>/hypergumbo/...": {"alpha.sketch", "beta.sketch"},
        "exec\tgit rev-parse HEAD": {"beta.sketch"},
    }
    assert iom.render(observed) == "exec\tgit rev-parse HEAD\nmkdir\t<cache>/hypergumbo/...\n"
    assert "[alpha.sketch, beta.sketch]" in iom.render_by_run(observed)


# --- check ------------------------------------------------------------------


def test_new_line_fails_and_missing_line_is_only_a_note():
    expected = {"mkdir\t<cache>/hypergumbo/...", "write\t<tmp>/..."}
    result = iom.check({"mkdir\t<cache>/hypergumbo/...", "exec\tcurl http://x"}, expected)
    assert not result.ok
    assert result.new == ["exec\tcurl http://x"]
    assert result.missing == ["write\t<tmp>/..."]
    text = iom.format_check(result)
    assert "FAIL: observed I/O not in the expected manifest" in text
    assert "note: expected lines not observed" in text

    assert iom.check({"mkdir\t<cache>/hypergumbo/..."}, expected).ok


def test_repo_write_fails_even_when_expected_lists_it():
    for line in ("write\t<repo>/x", "rename\t<tmp>/... -> <repo>/y", "mkdir\t<repo>", "unlink\t<repo>/z"):
        result = iom.check({line}, {line})
        assert result.repo_writes == [line], line
        assert not result.ok
        assert "ADR-0047" in iom.format_check(result)


def test_network_connect_fails_even_when_expected_lists_it():
    for line in ("connect\tAF_INET <ip>:443", "connect\tAF_INET6 <ip>:443"):
        result = iom.check({line}, {line})
        assert result.network == [line]
        assert not result.ok
    unix = "connect\tAF_UNIX /var/run/nscd/socket"
    assert iom.check({unix}, {unix}).ok


def test_exec_mentioning_repo_is_not_a_repo_write():
    line = "exec\tgit -C <repo> rev-parse HEAD"
    assert iom.check({line}, {line}).ok


def test_parse_manifest_ignores_comments_and_blank_lines():
    assert iom.parse_manifest("# c\n\nwrite\t<tmp>/...\n  # d\n") == {"write\t<tmp>/..."}


# --- the committed files --------------------------------------------------------


def test_committed_corpus_has_full_pins_and_one_slice_target():
    entries = iom.parse_corpus((DOCS / "corpus.txt").read_text())
    assert len(entries) == 6
    assert all(len(e.sha) == 40 for e in entries), "pin every corpus repo by full sha"
    assert sum(1 for e in entries if e.slice_entry) >= 1


def test_committed_expected_manifest_cannot_bless_a_hard_rule_breach():
    expected = iom.parse_manifest((DOCS / "expected.txt").read_text())
    assert expected, "expected.txt is empty"
    result = iom.check(expected, expected)
    assert result.ok, iom.format_check(result)
    assert all("\t" in line for line in expected), "every line is op<TAB>target"


def test_release_workflow_runs_the_gate_before_publishing():
    workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert re.search(r"^  io-manifest:\n", workflow, re.M), "no io-manifest job"
    assert "scripts/io-manifest run" in workflow
    assert "docs/io-manifest/corpus.txt" in workflow
    assert "docs/io-manifest/expected.txt" in workflow
    needs = re.search(r"^  build-and-publish:\n(?:    .*\n)*?    needs: \[([^\]]*)\]", workflow, re.M)
    assert needs, "build-and-publish has no needs:"
    assert "io-manifest" in needs.group(1) and "security-audit" in needs.group(1)


# --- CLI ------------------------------------------------------------------------


def test_check_subcommand_exit_codes(tmp_path: Path, capsys):
    exp = tmp_path / "expected.txt"
    obs = tmp_path / "observed.txt"
    exp.write_text("mkdir\t<cache>/hypergumbo/...\n")
    obs.write_text("mkdir\t<cache>/hypergumbo/...\n")
    assert iom.main(["check", "--observed", str(obs), "--expected", str(exp)]) == 0
    obs.write_text("mkdir\t<cache>/hypergumbo/...\nconnect\tAF_INET <ip>:443\n")
    assert iom.main(["check", "--observed", str(obs), "--expected", str(exp)]) == 1
    assert "network connection" in capsys.readouterr().out


def test_reduce_subcommand_reads_saved_traces(tmp_path: Path, capsys):
    out = tmp_path / "iom"
    (out / "corpus" / "alpha").mkdir(parents=True)
    (out / "cache").mkdir()
    trace = out / "alpha.sketch.trace"
    trace.write_text(
        f'1 mkdir("{out}/cache/hypergumbo/x", 0777) = 0\n'
        f'1 creat("{out}/corpus/alpha/junk", 0644) = 3\n'
    )
    rc = iom.main(["reduce", "--out", str(out), "--hypergumbo", sys.executable, str(trace)])
    assert rc == 0
    assert capsys.readouterr().out == "mkdir\t<cache>/hypergumbo/...\nwrite\t<repo>/junk\n"


def test_reduce_can_rereduce_a_ci_artifact_without_local_paths(tmp_path: Path, capsys):
    """The runner's paths do not exist here; --corpus and --python supply the
    zone roots as strings, which is how expected.txt is regenerated from the
    uploaded artifact."""
    ci_out = "/home/runner/work/_temp/io-manifest"
    trace = tmp_path / "ripgrep.sketch.trace"
    trace.write_text(
        f'1 execve("/opt/hostedtoolcache/Python/3.11.17/x64/bin/hypergumbo", ["hypergumbo", "sketch", "{ci_out}/corpus/ripgrep"], 0x1 /* 1 var */) = 0\n'
        f'1 creat("{ci_out}/corpus/ripgrep/junk", 0644) = 3\n'
        f'1 mkdir("{ci_out}/home/.rustup", 0755) = 0\n'
        f'1 openat(AT_FDCWD, "/opt/hostedtoolcache/Python/3.11.17/x64/lib/x.pyc", O_WRONLY|O_CREAT, 0644) = 4\n'
    )
    rc = iom.main(["reduce", "--out", ci_out, "--corpus", str(DOCS / "corpus.txt"),
                   "--python", "/opt/hostedtoolcache/Python/3.11.17/x64", str(trace)])
    assert rc == 0
    assert capsys.readouterr().out == (
        "exec\thypergumbo sketch <repo>\n"
        "mkdir\t<home>/.rustup\n"
        "write\t<python>/lib/x.pyc\n"
        "write\t<repo>/junk\n"
    )


def test_run_refuses_without_strace(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(iom.shutil, "which", lambda name: None)
    rc = iom.main(["run", "--corpus", str(DOCS / "corpus.txt"), "--out", str(tmp_path)])
    assert rc == 2
    assert "strace" in capsys.readouterr().err


def test_run_refuses_without_the_executable(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(iom.shutil, "which", lambda name: "/usr/bin/strace" if name == "strace" else None)
    rc = iom.main(["run", "--corpus", str(DOCS / "corpus.txt"), "--out", str(tmp_path),
                   "--hypergumbo", "no-such-hypergumbo"])
    assert rc == 2
    assert "hypergumbo executable not found" in capsys.readouterr().err


def test_ensure_checkout_reuses_a_pinned_clone_and_rejects_a_drifted_one(tmp_path: Path):
    import subprocess

    repo = tmp_path / "alpha"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "x"], check=True, env=env)
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                         capture_output=True, text=True, env=env).stdout.strip()
    good = iom.CorpusEntry("alpha", "o/alpha", sha)
    assert iom.ensure_checkout(good, tmp_path) == repo
    drifted = iom.CorpusEntry("alpha", "o/alpha", "f" * 40)
    with pytest.raises(SystemExit):
        iom.ensure_checkout(drifted, tmp_path)
