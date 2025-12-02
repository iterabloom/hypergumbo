import argparse
from . import __version__

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="hypergumbo")
    p.add_argument("--version", action="store_true", help="Print version and exit")
    args = p.parse_args(argv)

    if args.version:
        print(__version__)
        return 0

    p.print_help()
    return 0
