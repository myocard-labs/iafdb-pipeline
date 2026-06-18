"""CLI: download the IAFDB dataset from PhysioNet.

Wired as the ``iafdb-download`` console_script.

Examples
--------
::

    iafdb-download
    iafdb-download --dest /path/to/iafdb
    iafdb-download --records iaf1_afw iaf2_svc
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from myocard_iafdb_pipeline.constants import RECORD_NAMES
from myocard_iafdb_pipeline.download import download

DEFAULT_DEST = Path.cwd() / "data" / "iafdb"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="iafdb-download",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_DEST,
        help=f"Destination directory (default: {DEFAULT_DEST})",
    )
    parser.add_argument(
        "--records",
        nargs="+",
        choices=RECORD_NAMES,
        metavar="RECORD",
        help="Subset of records to download (default: all 32)",
    )
    args = parser.parse_args(argv)
    try:
        download(args.dest, args.records)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
