# SPDX-License-Identifier: AGPL-3.0-or-later
"""``_extract_file_samples`` picks the same substrings in every process.

WI-vosag analogue. The sampler seeded its RNG with ``hash(str(file_path))``
under a comment calling that "deterministic seeding" — but ``str.__hash__``
is salted per process (PYTHONHASHSEED), so which three windows of a long
README were embedded, and therefore which files the embedding ranking
preferred, changed from run to run. Same defect class as the sketch's
test-framework sample built from an unsorted set of globs.

The in-process test pins the seed to a stable digest of the path; the
subprocess test is the behavioral evidence (two different hash salts, one
answer). Subprocess runs do not count toward coverage — the in-process test
carries that.
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
import zlib
from pathlib import Path

from hypergumbo_core.sketch_embeddings import _extract_file_samples


def _long_doc(tmp_path: Path) -> Path:
    path = tmp_path / "README.md"
    words = [f"word{i:05d}" for i in range(6000)]
    path.write_text(" ".join(words))
    return path


def test_seed_is_a_stable_digest_of_the_path(tmp_path: Path) -> None:
    path = _long_doc(tmp_path)
    got = _extract_file_samples(path, num_samples=3, sample_size=50)

    text = " ".join(path.read_text().split())[: len(path.read_text()) // 3]
    rng = random.Random(zlib.crc32(str(path).encode("utf-8")))  # noqa: S311
    samples, start_floor = [], 0
    for remaining in (2, 1, 0):
        start = rng.randint(start_floor, len(text) - 50 - 50 * remaining)
        samples.append(text[start : start + 50])
        start_floor = start + 50
    assert got == " ... ".join(samples)


def test_same_samples_under_different_hash_salts(tmp_path: Path) -> None:
    path = _long_doc(tmp_path)
    code = (
        "import sys; from pathlib import Path;"
        "from hypergumbo_core.sketch_embeddings import _extract_file_samples;"
        "sys.stdout.write(_extract_file_samples(Path(sys.argv[1])))"
    )
    outputs = set()
    for salt in ("1", "2", "3"):
        env = {**os.environ, "PYTHONHASHSEED": salt}
        result = subprocess.run(
            [sys.executable, "-c", code, str(path)],
            capture_output=True, text=True, env=env, check=True,
        )
        outputs.add(result.stdout)
    assert len(outputs) == 1
