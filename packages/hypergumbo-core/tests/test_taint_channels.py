# SPDX-License-Identifier: AGPL-3.0-or-later
"""The operator's taint model has one persistent home, and it is read (WI-mimap).

ADR-0061 ruling 7: ``taint_sources.d/``, ``taint_sanitizers.d/`` and
``taint_sinks.d/`` under ``$XDG_CONFIG_HOME/hypergumbo/`` are the ``yours``
channels for the taint families, all of them read. Before this the registry
declared the first two and ``init-catalogs`` created them while nothing scanned
either, and the third did not exist: a file dropped there had no effect and
raised no error.

The rules pinned here:
* a channel file is a USER layer BELOW the claims file and the flag (a scanned
  directory is the least specific statement of intent), and its sanitizers are
  stamped ``user_supplied`` like the others;
* a channel file that still declares ``provenance: community`` is not the
  operator's: it joins the COMMUNITY layer, which only adds -- as do the
  shipped ``*_community.yaml`` files -- and ``--no-default-overlays`` omits it.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.taint import load_builtin_taint_catalog, load_full_taint_catalog


def _source_file(path: Path, *, label: str, module: str, fn: str,
                 community: bool = False) -> Path:
    path.write_text(
        ("provenance: community\nretrieved: 2026-01-01\n" if community else "")
        + f"taint_label: {label}\nsources:\n  python:\n"
        f"    - module: {module}\n      functions: [{fn}]\n"
    )
    return path


def _sanitizer_file(path: Path, qualified: str, *, community: bool = False) -> Path:
    path.write_text(
        ("provenance: community\nretrieved: 2026-01-01\n" if community else "")
        + "transforms:\n  - input_taint: host_secret\n    output_taint: redacted\n"
        f"    functions:\n      python:\n        - {qualified}\n"
    )
    return path


def _labels(catalog, module: str, name: str) -> set[str]:
    return {s.taint_label for s in catalog._sources.get("python", [])
            if s.module == module and s.name == name}


class TestTheChannelIsAUserLayer:
    def test_a_channel_source_is_read(self, tmp_path: Path) -> None:
        mine = _source_file(tmp_path / "s.yaml", label="pii", module="crm",
                            fn="lookup")
        catalog = load_full_taint_catalog(channel_source_paths=[mine])
        assert _labels(catalog, "crm", "lookup") == {"pii"}

    def test_it_sits_below_the_claims_file_and_the_flag(self, tmp_path: Path) -> None:
        channel = _source_file(tmp_path / "c.yaml", label="from_channel",
                               module="crm", fn="lookup")
        claims = _source_file(tmp_path / "k.yaml", label="from_claims",
                              module="crm", fn="lookup")
        flag = _source_file(tmp_path / "f.yaml", label="from_flag",
                            module="crm", fn="lookup")
        assert _labels(load_full_taint_catalog(
            extra_source_paths=[claims], channel_source_paths=[channel],
        ), "crm", "lookup") == {"from_claims"}
        assert _labels(load_full_taint_catalog(
            extra_source_paths=[claims], cli_source_paths=[flag],
            channel_source_paths=[channel],
        ), "crm", "lookup") == {"from_flag"}

    def test_it_displaces_a_shipped_row_like_any_user_row(self, tmp_path: Path) -> None:
        """os.getenv derives host_secret from env_read; a user row re-labels
        it, and the displacement is recorded (INV-faput) -- the channel is the
        operator's word, with the operator's disclosure."""
        mine = _source_file(tmp_path / "s.yaml", label="config_value",
                            module="os", fn="getenv")
        catalog = load_full_taint_catalog(channel_source_paths=[mine])
        assert _labels(catalog, "os", "getenv") == {"config_value"}
        assert any(s.name == "getenv"
                   for s in catalog._displaced_sources.get("python", []))

    def test_a_channel_sanitizer_is_stamped_user_supplied(self, tmp_path: Path) -> None:
        mine = _sanitizer_file(tmp_path / "z.yaml", "crm.redact")
        catalog = load_full_taint_catalog(channel_sanitizer_paths=[mine])
        (san,) = [s for s in catalog._sanitizers["python"]
                  if s.qualified_name == "crm.redact"]
        assert san.user_supplied is True

    def test_a_channel_directory_is_read_like_a_flag_directory(
        self, tmp_path: Path,
    ) -> None:
        d = tmp_path / "taint_sources.d"
        d.mkdir()
        _source_file(d / "a.yaml", label="pii", module="crm", fn="lookup")
        catalog = load_full_taint_catalog(channel_source_paths=[d])
        assert _labels(catalog, "crm", "lookup") == {"pii"}


class TestACommunityFileOnlyAdds:
    def test_it_never_displaces_a_vouched_row(self, tmp_path: Path) -> None:
        community = _source_file(tmp_path / "c.yaml", label="vendor",
                                 module="os", fn="getenv", community=True)
        catalog = load_full_taint_catalog(channel_source_paths=[community])
        assert _labels(catalog, "os", "getenv") == {"host_secret"}
        assert not catalog._displaced_sources.get("python")

    def test_it_still_adds_and_its_sanitizer_is_not_the_operators(
        self, tmp_path: Path,
    ) -> None:
        source = _source_file(tmp_path / "c.yaml", label="vendor",
                              module="vendorlib", fn="read", community=True)
        sanitizer = _sanitizer_file(tmp_path / "z.yaml", "vendorlib.scrub",
                                    community=True)
        catalog = load_full_taint_catalog(
            channel_source_paths=[source], channel_sanitizer_paths=[sanitizer],
        )
        assert _labels(catalog, "vendorlib", "read") == {"vendor"}
        (san,) = [s for s in catalog._sanitizers["python"]
                  if s.qualified_name == "vendorlib.scrub"]
        assert san.user_supplied is False

    def test_no_default_overlays_omits_it(self, tmp_path: Path) -> None:
        source = _source_file(tmp_path / "c.yaml", label="vendor",
                              module="vendorlib", fn="read", community=True)
        catalog = load_full_taint_catalog(
            channel_source_paths=[source], include_community=False,
        )
        assert _labels(catalog, "vendorlib", "read") == set()

    def test_the_shipped_community_files_only_add_too(self) -> None:
        """THE CONTROL on the shipped tree: the community crypto sources still
        load, beside the built-in ones."""
        catalog = load_builtin_taint_catalog()
        assert _labels(catalog, "cryptography.fernet", "Fernet.decrypt") == {
            "plaintext",
        }
        assert any(s.module == "crypto/aes" for s in catalog._sources["go"])
