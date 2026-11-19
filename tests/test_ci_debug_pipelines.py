# SPDX-License-Identifier: AGPL-3.0-or-later
"""``ci-debug pipelines``: Woodpecker's pipeline records, to a local file (WI-rubak).

The cadence of the scheduled runs is decided from measurements: how long a
full run and a nightly take, how long the runner sits idle, how long a PR's CI
spends before its first test. Woodpecker keeps those times in its pipeline
records; this command pages through them (GETs only) and writes one JSON object
a line, and, for the newest few, the full record with each step's times. What
is pinned: the pages are read until an empty one, every record lands in the
file, the full records are fetched for the newest, a refused page stops the
command with its HTTP code, and missing credentials are named, not guessed.
"""

# covers: scripts/ci-debug
from __future__ import annotations

import json
from pathlib import Path

from _forge_github_harness import bindir_with_fakes, calls, fake_repo, run_script

_GH = {"HYPERGUMBO_FORGE_BACKEND": "github"}
_WP = {
    "WOODPECKER_SERVER": "https://wp.example",
    "WOODPECKER_TOKEN": "wtok",
    "CF_ACCESS_CLIENT_ID": "cfid",
    "CF_ACCESS_CLIENT_SECRET": "cfsecret",
}
_LIST = "GET https://wp.example/api/repos/1/pipelines?page="


def _rows(*numbers: int) -> str:
    return json.dumps([{"number": n, "event": "cron", "created": n, "started": n,
                        "finished": n + 5} for n in numbers])


def _run(tmp_path: Path, fixtures: list, argv: tuple[str, ...], env: dict | None = None):
    repo = fake_repo(tmp_path, "https://github.com/o/r.git")
    return run_script("ci-debug", repo, ("pipelines", *argv), fixtures=fixtures,
                      env=env if env is not None else {**_GH, **_WP},
                      bindir=bindir_with_fakes(tmp_path))


def test_every_page_until_an_empty_one_and_the_newest_in_full(tmp_path: Path) -> None:
    out = tmp_path / "p.jsonl"
    (r, logs) = _run(tmp_path, [
        {"match": _LIST + "1&", "code": 200, "body": _rows(30, 29)},
        {"match": _LIST + "2&", "code": 200, "body": _rows(28)},
        {"match": _LIST + "3&", "code": 200, "body": "[]"},
        {"match": "GET https://wp.example/api/repos/1/pipelines/30", "code": 200,
         "body": json.dumps({"number": 30, "workflows": [{"children": [{"name": "pytest"}]}]})},
        {"match": "GET https://wp.example/api/repos/1/pipelines/29", "code": 200,
         "body": json.dumps({"number": 29, "workflows": []})},
    ], (str(out), "5", "2"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert [json.loads(line)["number"] for line in out.read_text().splitlines()] == [30, 29, 28]
    full = [json.loads(line) for line in (tmp_path / "p.details.jsonl").read_text().splitlines()]
    assert [d["number"] for d in full] == [30, 29]
    assert full[0]["workflows"][0]["children"][0]["name"] == "pytest"
    assert f"3 pipeline record(s) in {out}" in r.stdout
    urls = [c["url"] for c in calls(logs["curl"])]
    assert all(u.startswith("https://wp.example/api/repos/1/pipelines") for u in urls)
    assert not any("page=4" in u for u in urls), "read past the empty page"
    assert all(c["method"] == "GET" for c in calls(logs["curl"]))


def test_a_refused_page_stops_with_its_code(tmp_path: Path) -> None:
    out = tmp_path / "p.jsonl"
    (r, _) = _run(tmp_path, [{"match": _LIST, "code": 403, "body": "{}"}], (str(out),))
    assert r.returncode == 1
    assert "page 1: HTTP 403; stopped" in r.stderr


def test_missing_credentials_are_named(tmp_path: Path) -> None:
    (r, logs) = _run(tmp_path, [], (str(tmp_path / "p.jsonl"),), env=_GH)
    assert r.returncode == 1
    assert "WOODPECKER_SERVER, WOODPECKER_TOKEN" in r.stderr
    assert calls(logs["curl"]) == []


def test_no_output_file_is_a_usage_error(tmp_path: Path) -> None:
    (r, _) = _run(tmp_path, [], ())
    assert r.returncode == 1 and "usage:" in r.stderr
