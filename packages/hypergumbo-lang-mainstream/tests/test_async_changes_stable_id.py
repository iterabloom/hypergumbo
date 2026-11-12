# SPDX-License-Identifier: AGPL-3.0-or-later
"""Making a function async (or Kotlin ``suspend``) changes its stable_id.

``async`` changes the calling contract: the caller gets a coroutine or future
and must await it. A stable_id keeps an interface's identity across body
edits, not across a contract change, so the async marker is a hash input in
every language (ADR-0035 §1), not only where the language happens to spell it
into the declared return type.

Each case below differs ONLY in the keyword: same path, name, parameters and
declared return type. The sync ids are pinned to the values the previous
scheme produced, so adding the marker is shown to leave every non-async id
unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.csharp import analyze_csharp
from hypergumbo_lang_mainstream.js_ts import analyze_javascript
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin
from hypergumbo_lang_mainstream.py import analyze_python
from hypergumbo_lang_mainstream.rust import analyze_rust
from hypergumbo_lang_mainstream.swift import analyze_swift

# (case, analyzer, file name, sync source, async source, symbol name, pinned sync id)
CASES = [
    ("python", analyze_python, "m.py",
     "def handle(req: int, resp: int) -> int:\n    return req\n",
     "async def handle(req: int, resp: int) -> int:\n    return req\n",
     "handle", "sha256:45a2db3bd372ef70"),
    ("python-untyped", analyze_python, "m.py",
     "def handle(req, resp):\n    return req\n",
     "async def handle(req, resp):\n    return req\n",
     "handle", "sha256:b38e87cca9435fcd"),
    ("python-method", analyze_python, "m.py",
     "class C:\n    def handle(self, req: int) -> int:\n        return req\n",
     "class C:\n    async def handle(self, req: int) -> int:\n        return req\n",
     "handle", "sha256:8c2a6601147231cf"),
    ("javascript", analyze_javascript, "m.js",
     "function handle(req, resp) { return req; }\n",
     "async function handle(req, resp) { return req; }\n",
     "handle", "sha256:1f342ef1c162c103"),
    ("javascript-method", analyze_javascript, "m.js",
     "class C { handle(req) { return req; } }\n",
     "class C { async handle(req) { return req; } }\n",
     "handle", "sha256:0703b337efc48c1c"),
    ("javascript-arrow", analyze_javascript, "m.js",
     "const handle = (req) => req;\n",
     "const handle = async (req) => req;\n",
     "handle", "sha256:9483d6772de92490"),
    ("typescript", analyze_javascript, "m.ts",
     "export function handle(req: number): number { return req; }\n",
     "export async function handle(req: number): number { return req; }\n",
     "handle", "sha256:ce5699b7ecafdc28"),
    ("rust", analyze_rust, "m.rs",
     "pub fn handle(req: i32) -> i32 { req }\n",
     "pub async fn handle(req: i32) -> i32 { req }\n",
     "handle", "sha256:47deb794247d1f60"),
    ("rust-method", analyze_rust, "m.rs",
     "struct S;\nimpl S { pub fn handle(&self, req: i32) -> i32 { req } }\n",
     "struct S;\nimpl S { pub async fn handle(&self, req: i32) -> i32 { req } }\n",
     "handle", "sha256:a2b529b7d77591b1"),
    ("csharp", analyze_csharp, "m.cs",
     "public class C { public Task<int> Handle(int req) { return Task.FromResult(req); } }\n",
     "public class C { public async Task<int> Handle(int req) { return req; } }\n",
     "Handle", "sha256:6aa58f10860ddf03"),
    ("kotlin", analyze_kotlin, "m.kt",
     "fun handle(req: Int): Int { return req }\n",
     "suspend fun handle(req: Int): Int { return req }\n",
     "handle", "sha256:44bcdf8eeda34284"),
    ("swift", analyze_swift, "m.swift",
     "func handle(req: Int) -> Int { return req }\n",
     "func handle(req: Int) async -> Int { return req }\n",
     "handle", "sha256:1f29aa0526ec7a45"),
]


def _stable_ids(analyze, tmp: Path, fname: str, src: str, name: str) -> list[str]:
    tmp.mkdir()
    (tmp / fname).write_text(src)
    result = analyze(tmp)
    return [s.stable_id for s in result.symbols
            if s.name.replace("::", ".").split(".")[-1] == name
            and s.kind in ("function", "method")]


@pytest.mark.parametrize(
    "case,analyze,fname,sync_src,async_src,name,pinned",
    CASES, ids=[c[0] for c in CASES])
def test_async_changes_the_stable_id_and_sync_keeps_its_own(
    tmp_path: Path, case, analyze, fname, sync_src, async_src, name, pinned,
) -> None:
    sync_ids = _stable_ids(analyze, tmp_path / "sync", fname, sync_src, name)
    async_ids = _stable_ids(analyze, tmp_path / "async", fname, async_src, name)
    assert sync_ids == [pinned], f"{case}: the non-async id moved"
    assert len(async_ids) == 1 and async_ids[0] is not None
    assert async_ids[0] != pinned, f"{case}: async did not change the id"
