import pytest

from hypergumbo import __version__
from hypergumbo.cli import build_parser


def test_version_flag_prints_version_and_exits(capsys):
    parser = build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])

    assert exc.value.code == 0

    out, err = capsys.readouterr()
    assert __version__ in out
    assert "hypergumbo" in out

