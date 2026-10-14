# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the npm (package.json + lockfiles) dependency manifest reader (WI-juzaj)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_lang_mainstream.npm_deps import (
    parse_npm_dependencies,
    parse_package_json,
    parse_package_lock,
    parse_pnpm_lock,
    parse_yarn_lock,
)


class TestParsePackageJson:
    def test_all_sections_and_own_name(self) -> None:
        data = {
            "name": "web",
            "dependencies": {"leaflet": "^1.9", "@peertube/embed-api": "^0.0.6"},
            "devDependencies": {"vitest": "^1"},
            "peerDependencies": {"react": ">=18"},
            "optionalDependencies": {"fsevents": "*"},
            "bundledDependencies": ["ignored"],
        }
        direct, own = parse_package_json(data)
        assert direct == {"leaflet", "@peertube/embed-api", "vitest", "react", "fsevents"}
        assert own == "web"

    def test_local_references_are_not_dependencies(self) -> None:
        data = {"dependencies": {
            "a": "workspace:*", "b": "file:../b", "c": "link:../c", "d": "^1", "": "1",
        }}
        assert parse_package_json(data)[0] == {"d"}

    def test_malformed(self) -> None:
        assert parse_package_json(None) == (set(), None)
        assert parse_package_json({"name": " ", "dependencies": ["x"]}) == (set(), None)


class TestParsePackageLock:
    def test_v3_packages(self) -> None:
        data = {"packages": {
            "": {"name": "root"},
            "node_modules/ws": {},
            "node_modules/@artilleryio/int-commons": {},
            "node_modules/a/node_modules/b": {},
            "packages/local": {},
        }}
        assert parse_package_lock(data) == {"ws", "@artilleryio/int-commons", "b"}

    def test_v1_dependencies_recursive(self) -> None:
        data = {"dependencies": {"x": {"dependencies": {"y": {}}}, "z": "bad"}}
        assert parse_package_lock(data) == {"x", "y", "z"}

    def test_malformed(self) -> None:
        assert parse_package_lock(None) == set()
        assert parse_package_lock({"dependencies": []}) == set()


class TestParseYarnLock:
    def test_classic_and_berry(self) -> None:
        src = (
            "# yarn lockfile v1\n\n"
            '"@babel/core@^7.0.0", "@babel/core@^7.1.0":\n'
            '  version "7.2.0"\n'
            "lodash@^4.17.0:\n"
            '  version "4.17.21"\n'
            "__metadata:\n"
            "  version: 6\n"
            '"left-pad@npm:^1.3.0":\n'
            "  version: 1.3.0\n"
        )
        assert parse_yarn_lock(src) == {"@babel/core", "lodash", "left-pad"}


class TestParsePnpmLock:
    def test_generations(self) -> None:
        src = (
            "lockfileVersion: '9.0'\n"
            "importers:\n"
            "  .:\n"
            "    dependencies:\n"
            "      leaflet:\n"
            "packages:\n"
            "  /old-five/1.0.0:\n"
            "    resolution: {}\n"
            "  /@scope/five/2.0.0_react@18.0.0:\n"
            "  /six@1.0.0:\n"
            "  /@scope/six@1.0.0:\n"
            "  nine@1.0.0:\n"
            "  '@babel/core@7.0.0(supports-color@8.0.0)':\n"
            "snapshots:\n"
            "  snap@2.0.0:\n"
        )
        assert parse_pnpm_lock(src) == {
            "old-five", "@scope/five", "six", "@scope/six", "nine", "@babel/core", "snap",
        }


class TestParseNpmDependencies:
    def test_monorepo(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "name": "root", "devDependencies": {"ws": "^8"},
            "dependencies": {"web": "workspace:*"},
        }))
        (tmp_path / "package-lock.json").write_text(json.dumps({"packages": {
            "node_modules/ws": {}, "node_modules/artillery": {},
            "node_modules/web": {"link": True},
        }}))
        web = tmp_path / "web"
        web.mkdir()
        (web / "package.json").write_text(json.dumps({
            "name": "web", "dependencies": {"leaflet": "^1"},
        }))
        (web / "yarn.lock").write_text('leaflet@^1:\n  version "1.9.4"\nplyr@^3:\n')
        (web / "pnpm-lock.yaml").write_text("packages:\n  hls.js@1.0.0:\n")
        # node_modules manifests never count.
        nm = tmp_path / "node_modules" / "ws"
        nm.mkdir(parents=True)
        (nm / "package.json").write_text(json.dumps({"dependencies": {"evil": "1"}}))
        m = parse_npm_dependencies(tmp_path)
        assert m.scoped["npm"] == {
            "ws": {"direct": True},
            "leaflet": {"direct": True},
            "artillery": {"direct": False},
            "plyr": {"direct": False},
            "hls.js": {"direct": False},
        }

    def test_no_npm_project(self, tmp_path: Path) -> None:
        assert parse_npm_dependencies(tmp_path).scoped == {}

    def test_unreadable_files_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text('{"dependencies": {"a": "1"}}')
        (tmp_path / "yarn.lock").write_text("b@^1:\n")
        with patch.object(Path, "read_text", side_effect=OSError("denied")):
            assert parse_npm_dependencies(tmp_path).scoped == {}


class TestJsBoundaryDirectnessEndToEnd:
    def test_survey_stamps_js_boundaries(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "name": "app", "dependencies": {"leaflet": "^1", "@scope/kit": "^2"},
        }))
        (tmp_path / "package-lock.json").write_text(json.dumps({"packages": {
            "node_modules/leaflet": {}, "node_modules/@scope/kit": {},
            "node_modules/lodash": {},
        }}))
        (tmp_path / "app.js").write_text(
            "import { map } from 'leaflet';\n"
            "import { button } from '@scope/kit/ui';\n"
            "import { chunk } from 'lodash/fp';\n"
            "import { readFile } from 'node:fs';\n"
            "export function run() {\n"
            "  map(); button(); chunk(); readFile('x');\n"
            "}\n"
        )
        out = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out,
                         include_sketch_precomputed=False)
        data = json.loads(out.read_text())
        boundary = {
            n["display_label"].split(":")[1]: n.get("meta") or {}
            for n in data["nodes"]
            if n.get("language") == "javascript"
            and (n.get("meta") or {}).get("external_boundary")
        }
        assert boundary["leaflet"]["directness"] == "direct"
        assert boundary["leaflet"]["ecosystem"] == "third_party"
        assert boundary["@scope/kit/ui"]["directness"] == "direct"
        assert boundary["lodash/fp"]["directness"] == "transitive"
        assert "directness" not in boundary["fs"]
