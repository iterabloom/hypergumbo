# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the transcript secret scrubber (.agent/hooks/_shared/scrub_secrets.py).

The risk profile is asymmetric and drives what these tests emphasise. A MISS
leaves one credential readable in a gitignored file. A FALSE POSITIVE rewrites
every occurrence of an ordinary string across a ~2GB corpus, irreversibly. So the
false-positive guards and the "bytes unchanged when there is nothing to redact"
invariant are the load-bearing tests, not the happy path.

Every test named ``test_regression_*`` pins a defect an adversarial review
DEMONSTRATED against the first implementation. They are grouped and labelled so a
future reader can see that these are not hypotheticals:

* text mode with ``errors="replace"`` silently mutated non-UTF-8 bytes and
  collapsed CRLF, on a destructive in-place rewrite, with no secret present;
* line-based scrubbing let a secret straddling a ``\\r`` evade redaction *and*
  rewrote the ``\\r``, making the credential permanently immune;
* ``*_KEY`` / ``*_PAT`` key matching redacted ordinary config -- a plausible
  ``CACHE_KEY`` would have replaced 376,928 occurrences across 265 files;
* an inline ``# comment`` in .env folded into the secret literal, so the real
  token was never matched while verification still reported clean;
* equal-length overlapping secrets left a different fragment per run because the
  ordering depended on set iteration order.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / ".agent" / "hooks" / "_shared" / "scrub_secrets.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("scrub_secrets", _MODULE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


scrub = _load()

# gitleaks:allow - synthetic bait; this file tests the secret scrubber, so the
# fixture has to be shaped like the thing the scrubber removes.
_TOKEN = "gho_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"  # gitleaks:allow
_OTHER = "sk-or-v1-zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"


def _repo(tmp_path: Path, *, env: str = "", secrets: str = "") -> Path:
    if env:
        (tmp_path / ".env").write_text(env)
    if secrets:
        (tmp_path / scrub.SECRETS_FILENAME).write_text(secrets)
    return tmp_path


# --- regressions from the adversarial review ------------------------------


def test_regression_bytes_unchanged_when_nothing_to_redact(tmp_path: Path) -> None:
    """The single most important invariant: a no-op scrub must not touch bytes.

    The first version read text with ``errors="replace"`` and universal newlines,
    so a non-UTF-8 byte became U+FFFD and ``\\r\\n`` became ``\\n`` -- silently,
    on the only copy, with zero secrets present and exit 0.
    """
    raw = b'{"a":"ok"}\n{"b":"\xff\xfe bad utf8"}\n{"c":"crlf"}\r\n{"d":"no newline"}'
    target = tmp_path / "t.jsonl"
    target.write_bytes(raw)
    repo = _repo(tmp_path, secrets=f"{_TOKEN}\n")
    scrub.scrub_in_place(target, scrub.collect_secrets(repo))
    assert target.read_bytes() == raw


def test_regression_secret_straddling_a_carriage_return(tmp_path: Path) -> None:
    """A secret split by a raw CR must still be redacted.

    Line-based scrubbing never saw the halves together, so the credential
    survived AND its CR was rewritten to LF — making it immune to re-scrubbing.
    """
    straddled = _TOKEN[:20] + "\r" + _TOKEN[20:]
    target = tmp_path / "cr.jsonl"
    target.write_bytes(b'{"x":"' + straddled.encode() + b'"}\n')
    repo = _repo(tmp_path, secrets=straddled + "\n")
    scrub.scrub_in_place(target, scrub.collect_secrets(repo))
    assert straddled.encode() not in target.read_bytes()


def test_regression_secret_straddling_a_chunk_boundary(tmp_path: Path) -> None:
    """Chunked reading must carry enough bytes forward to catch a split secret.

    This risk is INTRODUCED by chunking, so it needs its own guard: place the
    token so it spans the 1 MiB boundary.
    """
    target = tmp_path / "big.jsonl"
    target.write_bytes(b"x" * (scrub.CHUNK_SIZE - 10) + _TOKEN.encode() + b"\n")
    repo = _repo(tmp_path, secrets=f"{_TOKEN}\n")
    count, clean = scrub.scrub_in_place(target, scrub.collect_secrets(repo))
    assert _TOKEN.encode() not in target.read_bytes()
    assert count == 1 and clean


def test_regression_ordinary_config_under_secretish_keys_is_kept(tmp_path: Path) -> None:
    """The six keys the review weaponised must all be refused.

    Redacting ``packages/hypergumbo-core`` would have rewritten 376,928
    occurrences across 265 files. Both guards are needed: the key regex no longer
    accepts a bare ``*_KEY``/``*_PAT``, and the value must be credential-shaped.
    """
    repo = _repo(tmp_path, env=(
        "CACHE_KEY=packages/hypergumbo-core\n"
        "SORT_KEY=rank_score descending\n"
        "GPG_SIGNING_KEY=josh@iterabloom.com\n"
        "IGNORE_PAT=.agent/tracker/.ops\n"
        "EOS_TOKEN=<|reserved_special_token_0|>\n"
        "SSH_KEY=/home/u/.ssh/id_ed25519\n"
        f"FORGEJO_TOKEN={_TOKEN}\n"
    ))
    assert scrub.collect_secrets(repo) == [_TOKEN]


def test_regression_inline_env_comment_does_not_defeat_redaction(tmp_path: Path) -> None:
    """``KEY=<token> # note`` must yield the token, not token-plus-comment.

    Folding the comment in meant the real token never matched, while the tool
    reported success — false assurance, the worst failure mode for a privacy tool.
    """
    repo = _repo(tmp_path, env=f"MY_TOKEN={_TOKEN} # prod credential\n")
    assert scrub.collect_secrets(repo) == [_TOKEN]


def test_regression_secret_ordering_is_deterministic(tmp_path: Path) -> None:
    """Ordering must not depend on set iteration order.

    Two equal-length secrets sharing a core previously left a different fragment
    on different runs depending on PYTHONHASHSEED.
    """
    a, b = "AAAA" + "c" * 18, "BBBB" + "c" * 18
    repo = _repo(tmp_path, secrets=f"{a}\n{b}\n")
    first = scrub.collect_secrets(repo)
    out = subprocess.run(
        [sys.executable, "-c",
         "import importlib.util,sys;"
         f"spec=importlib.util.spec_from_file_location('s',{str(_MODULE_PATH)!r});"
         "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
         f"print(m.collect_secrets(__import__('pathlib').Path({str(repo)!r})))"],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONHASHSEED": "7"},
    )
    assert str(first) == out.stdout.strip()
    assert first == sorted(first, key=lambda s: (-len(s), s))


# --- false-positive and safety guards -------------------------------------


def test_short_values_are_ignored_from_both_sources(tmp_path: Path) -> None:
    repo = _repo(tmp_path, env="API_KEY=abc\n", secrets="q\nzz7yq\n")
    assert scrub.collect_secrets(repo) == []


def test_short_explicit_entries_warn_without_leaking_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, secrets="q\nzz7yq\n" + _TOKEN + "\n")
    assert scrub.collect_secrets(repo) == [_TOKEN]
    err = capsys.readouterr().err
    assert "too short" in err
    assert "zz7yq" not in err


def test_diagnostics_never_print_a_secret(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, env=f"OPENROUTER_API_KEY={_TOKEN}\n")
    target = tmp_path / "t.jsonl"
    target.write_text(json.dumps({"cmd": f"curl -H 'Bearer {_TOKEN}'"}) + "\n")
    scrub.main([str(target), "--in-place", "--repo-root", str(repo)])
    cap = capsys.readouterr()
    assert _TOKEN not in cap.err and _TOKEN not in cap.out
    assert "OPENROUTER_API_KEY" in cap.err


def test_paths_and_shapes_are_rejected() -> None:
    assert not scrub._value_looks_like_a_credential("/etc/ssl/private/key.pem")
    assert not scrub._value_looks_like_a_credential("~/.ssh/id_ed25519")
    assert not scrub._value_looks_like_a_credential("./relative/path/value")
    assert not scrub._value_looks_like_a_credential("../up/one/level/value")
    assert not scrub._value_looks_like_a_credential("has spaces in it here")
    assert not scrub._value_looks_like_a_credential("short")
    assert scrub._value_looks_like_a_credential(_TOKEN)


def test_world_readable_secrets_file_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / scrub.SECRETS_FILENAME
    path.write_text(f"{_TOKEN}\n")
    path.chmod(0o644)
    scrub.collect_secrets(tmp_path)
    assert "group/world accessible" in capsys.readouterr().err


def test_unreadable_env_is_tolerated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unreadable .env must not abort the caller mid-rotation."""
    env = tmp_path / ".env"
    env.write_text(f"A_TOKEN={_TOKEN}\n")
    env.chmod(0o000)
    try:
        if os.access(env, os.R_OK):  # pragma: no cover - running as root
            pytest.skip("running as root; cannot make a file unreadable")
        assert scrub.collect_secrets(tmp_path) == []
        assert "could not read .env" in capsys.readouterr().err
    finally:
        env.chmod(0o644)


# --- redaction behaviour ---------------------------------------------------


def test_in_place_redacts_and_keeps_jsonl_parseable(tmp_path: Path) -> None:
    repo = _repo(tmp_path, secrets=f"{_TOKEN}\n")
    target = tmp_path / "t.jsonl"
    rows = [
        {"role": "user", "text": "hello"},
        {"role": "tool", "text": f"https://u:{_TOKEN}@codeberg.org/x.git"},
        {"role": "assistant", "text": "done"},
    ]
    target.write_text("".join(json.dumps(r) + "\n" for r in rows))
    count, clean = scrub.scrub_in_place(target, scrub.collect_secrets(repo))
    assert (count, clean) == (1, True)
    text = target.read_text()
    assert _TOKEN not in text
    parsed = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert len(parsed) == 3 and parsed[0]["text"] == "hello"
    assert parsed[1]["text"].endswith("@codeberg.org/x.git")


def test_stdout_mode_streams_and_leaves_source_untouched(
    tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    repo = _repo(tmp_path, secrets=f"{_TOKEN}\n")
    target = tmp_path / "t.jsonl"
    target.write_text(f'{{"t":"{_TOKEN}"}}\n')
    assert scrub.main([str(target), "--repo-root", str(repo)]) == 0
    out = capsysbinary.readouterr().out
    assert _TOKEN.encode() not in out
    assert scrub.PLACEHOLDER in out
    assert _TOKEN in target.read_text()


def test_stdout_mode_is_fail_safe_and_emits_original_bytes(
    tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The property that makes future hook wiring safe.

    Callers pipe stdout into gzip. A non-zero exit or short write would yield a
    truncated archive — and in the wiring reviewers examined, a DESTROYED source.
    So any internal failure must fall back to the original bytes and exit 0.
    """
    repo = _repo(tmp_path, secrets=f"{_TOKEN}\n")
    target = tmp_path / "t.jsonl"
    original = f'{{"t":"{_TOKEN}"}}\n'
    target.write_text(original)

    def boom(*_a, **_k):
        raise RuntimeError("simulated scrub failure")

    monkeypatch.setattr(scrub, "scrub_stream", boom)
    assert scrub.main([str(target), "--repo-root", str(repo)]) == 0
    cap = capsysbinary.readouterr()
    assert cap.out == original.encode(), "did not fall back to original bytes"
    assert b"ORIGINAL" in cap.err


def test_multiple_secrets_all_redacted(tmp_path: Path) -> None:
    repo = _repo(tmp_path, env=f"A_TOKEN={_TOKEN}\nB_API_KEY={_OTHER}\n")
    target = tmp_path / "t.jsonl"
    target.write_text(f'{{"a":"{_TOKEN}","b":"{_OTHER}"}}\n')
    scrub.scrub_in_place(target, scrub.collect_secrets(repo))
    text = target.read_text()
    assert _TOKEN not in text and _OTHER not in text


def test_nested_secrets_leave_no_fragment(tmp_path: Path) -> None:
    inner, outer = _TOKEN, f"user:{_TOKEN}"
    repo = _repo(tmp_path, secrets=f"{inner}\n{outer}\n")
    target = tmp_path / "t.jsonl"
    target.write_text(f'{{"t":"https://{outer}@host"}}\n')
    scrub.scrub_in_place(target, scrub.collect_secrets(repo))
    text = target.read_text()
    assert inner not in text and "user:" not in text


def test_scrubbing_is_idempotent_byte_for_byte(tmp_path: Path) -> None:
    repo = _repo(tmp_path, secrets=f"{_TOKEN}\n")
    target = tmp_path / "t.jsonl"
    target.write_text(f'{{"t":"{_TOKEN}"}}\n')
    secrets = scrub.collect_secrets(repo)
    scrub.scrub_in_place(target, secrets)
    first = target.read_bytes()
    count, _ = scrub.scrub_in_place(target, secrets)
    assert target.read_bytes() == first and count == 0


def test_no_secrets_configured_is_a_byte_exact_passthrough(
    tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    target = tmp_path / "t.jsonl"
    raw = b'{"t":"plain"}\n\xff\xfe\n'
    target.write_bytes(raw)
    assert scrub.main([str(target), "--repo-root", str(tmp_path)]) == 0
    assert capsysbinary.readouterr().out == raw
    assert scrub.main([str(target), "--in-place", "--repo-root", str(tmp_path)]) == 0
    assert target.read_bytes() == raw


# --- metadata and filesystem behaviour ------------------------------------


def test_mtime_preserved_to_the_nanosecond(tmp_path: Path) -> None:
    """Rotation shows session-end time via mtime; float seconds lose the low bits."""
    repo = _repo(tmp_path, secrets=f"{_TOKEN}\n")
    target = tmp_path / "t.jsonl"
    target.write_text(f'{{"t":"{_TOKEN}"}}\n')
    ns = 1_751_234_567_123_456_789
    os.utime(target, ns=(ns, ns))
    scrub.scrub_in_place(target, scrub.collect_secrets(repo))
    assert target.stat().st_mtime_ns == ns


def test_mode_is_preserved(tmp_path: Path) -> None:
    repo = _repo(tmp_path, secrets=f"{_TOKEN}\n")
    target = tmp_path / "t.jsonl"
    target.write_text(f'{{"t":"{_TOKEN}"}}\n')
    target.chmod(0o600)
    scrub.scrub_in_place(target, scrub.collect_secrets(repo))
    assert target.stat().st_mode & 0o777 == 0o600


def test_symlink_is_followed_so_the_real_target_is_scrubbed(tmp_path: Path) -> None:
    """Rewriting through a link would replace the link and leave the target dirty."""
    repo = _repo(tmp_path, secrets=f"{_TOKEN}\n")
    real = tmp_path / "real.jsonl"
    real.write_text(f'{{"t":"{_TOKEN}"}}\n')
    link = tmp_path / "link.jsonl"
    link.symlink_to(real)
    scrub.scrub_in_place(link, scrub.collect_secrets(repo))
    assert link.is_symlink(), "symlink was replaced by a regular file"
    assert _TOKEN not in real.read_text()


def test_hard_link_warns_that_a_copy_survives(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, secrets=f"{_TOKEN}\n")
    target = tmp_path / "t.jsonl"
    target.write_text(f'{{"t":"{_TOKEN}"}}\n')
    os.link(target, tmp_path / "copy.jsonl")
    scrub.scrub_in_place(target, scrub.collect_secrets(repo))
    assert "hard link" in capsys.readouterr().err


def test_failure_leaves_the_original_intact_and_no_temp_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial write must never replace a good file."""
    repo = _repo(tmp_path, secrets=f"{_TOKEN}\n")
    target = tmp_path / "t.jsonl"
    original = f'{{"t":"{_TOKEN}"}}\n'
    target.write_text(original)

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(scrub, "scrub_stream", boom)
    with pytest.raises(OSError):
        scrub.scrub_in_place(target, scrub.collect_secrets(repo))
    assert target.read_text() == original
    assert not list(tmp_path.glob(".scrub-*")), "left a temp file behind"


def test_preserve_group_tolerates_chown_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-root cannot chown; that must not fail the scrub."""
    target = tmp_path / "t.jsonl"
    target.write_text("x\n")
    st = target.stat()
    monkeypatch.setattr(
        scrub.os, "chown",
        lambda *_a, **_k: (_ for _ in ()).throw(PermissionError("nope")),
    )
    scrub._preserve_group(target, st, target.name)  # must not raise


# --- CLI surface ----------------------------------------------------------


def test_missing_file_reports_failure(tmp_path: Path) -> None:
    assert scrub.main([str(tmp_path / "nope.jsonl"), "--repo-root", str(tmp_path)]) == 1


def test_absent_config_files_are_tolerated(tmp_path: Path) -> None:
    assert scrub.collect_secrets(tmp_path) == []


def test_the_shipped_example_file_yields_no_secrets(tmp_path: Path) -> None:
    """The committed .example is all comments; copying it verbatim must be inert.

    This is the comment/blank-line skip path, and the example is exactly what a
    user copies into place first — so an inert copy must not arm anything.
    """
    example = Path(__file__).resolve().parents[1] / (
        ".secrets_for_scrubbing_upon_archive.txt.example"
    )
    assert example.is_file(), "the committed .example went missing"
    (tmp_path / scrub.SECRETS_FILENAME).write_text(example.read_text())
    assert scrub.collect_secrets(tmp_path) == []


def test_env_comments_blank_lines_and_bare_words_ignored(tmp_path: Path) -> None:
    repo = _repo(tmp_path, env=(
        f"# API_KEY=commented\n\nNOT_AN_ASSIGNMENT\nREAL_TOKEN={_TOKEN}\n"
    ))
    assert scrub.collect_secrets(repo) == [_TOKEN]


@pytest.mark.parametrize("quote", ['"', "'"])
def test_quoted_env_values_are_unwrapped(tmp_path: Path, quote: str) -> None:
    repo = _repo(tmp_path, env=f"MY_TOKEN={quote}{_TOKEN}{quote}\n")
    assert scrub.collect_secrets(repo) == [_TOKEN]


def test_in_place_reports_a_count_when_it_redacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, secrets=f"{_TOKEN}\n")
    target = tmp_path / "t.jsonl"
    target.write_text(f'{{"a":"{_TOKEN}","b":"{_TOKEN}"}}\n')
    assert scrub.main([str(target), "--in-place", "--repo-root", str(repo)]) == 0
    assert "redacted 2 occurrence(s)" in capsys.readouterr().err


def test_in_place_is_silent_when_there_is_nothing_to_redact(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, secrets=f"{_TOKEN}\n")
    target = tmp_path / "t.jsonl"
    target.write_text('{"t":"clean"}\n')
    capsys.readouterr()
    assert scrub.main([str(target), "--in-place", "--repo-root", str(repo)]) == 0
    assert "redacted" not in capsys.readouterr().err


def test_stdout_mode_reports_a_count(
    tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    repo = _repo(tmp_path, secrets=f"{_TOKEN}\n")
    target = tmp_path / "t.jsonl"
    target.write_text(f'{{"t":"{_TOKEN}"}}\n')
    scrub.main([str(target), "--repo-root", str(repo)])
    assert b"redacted 1 occurrence(s)" in capsysbinary.readouterr().err


def test_repo_root_defaults_to_the_scripts_own_repo(tmp_path: Path) -> None:
    """Documented fallback. Callers should pass --repo-root; this pins parents[3].

    An off-by-one here would silently find no secrets while reporting success.
    """
    assert _MODULE_PATH.resolve().parents[3] == Path(__file__).resolve().parents[1]
