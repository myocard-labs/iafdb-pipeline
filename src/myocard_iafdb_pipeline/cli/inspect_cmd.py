"""CLI: print metadata and basic stats for an IAFDB record.

Wired as the ``iafdb-inspect`` console_script.

Examples
--------
::

    iafdb-inspect iaf1_afw
    iafdb-inspect iaf1_afw --data-dir /custom/path
    iafdb-inspect --all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from myocard_iafdb_pipeline.constants import RECORD_NAMES
from myocard_iafdb_pipeline.records import IAFDBRecord, iter_records, load_record

DEFAULT_DATA_DIR = Path.cwd() / "data" / "iafdb"


def _format_record(rec: IAFDBRecord) -> str:
    lines: list[str] = []
    lines.append(f"Record:      {rec.name}")
    lines.append(f"Patient:     {rec.patient}")
    lines.append(f"Placement:   {rec.placement}")
    lines.append(f"fs:          {rec.fs:.1f} Hz")
    lines.append(f"Duration:    {rec.duration_s:.2f} s ({rec.signal.shape[0]} samples)")
    lines.append(f"Channels:    {len(rec.channel_names)}")
    lines.append(f"  names:     {', '.join(rec.channel_names)}")
    lines.append(f"  units:     {', '.join(rec.units)}")
    lines.append(f"QRS anns:    {rec.qrs_samples.size}")
    if rec.comments:
        lines.append("Comments:")
        for c in rec.comments:
            lines.append(f"  {c}")

    lines.append("Per-channel stats (mV):")
    lines.append(f"  {'channel':<8} {'min':>10} {'max':>10} {'mean':>10} {'std':>10} {'pk-pk':>10}")
    for i, name in enumerate(rec.channel_names):
        col = rec.signal[:, i]
        if np.all(np.isnan(col)):
            lines.append(f"  {name:<8} (all NaN)")
            continue
        lines.append(
            f"  {name:<8} {np.nanmin(col):>10.3f} {np.nanmax(col):>10.3f} "
            f"{np.nanmean(col):>10.3f} {np.nanstd(col):>10.3f} "
            f"{np.nanmax(col) - np.nanmin(col):>10.3f}"
        )

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="iafdb-inspect",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "record",
        nargs="?",
        choices=RECORD_NAMES,
        metavar="RECORD",
        help="Record name to inspect (e.g. iaf1_afw)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Inspect every downloaded record; missing records are skipped",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Dataset directory (default: {DEFAULT_DATA_DIR})",
    )
    args = parser.parse_args(argv)

    if args.all == bool(args.record):
        parser.error("Provide a record name OR --all (exactly one).")

    try:
        if args.all:
            first = True
            for rec in iter_records(args.data_dir, skip_missing=True):
                if not first:
                    print("\n" + "-" * 72 + "\n")
                first = False
                print(_format_record(rec))
        else:
            rec = load_record(args.record, args.data_dir)
            print(_format_record(rec))
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Hint: run `iafdb-download` first.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
