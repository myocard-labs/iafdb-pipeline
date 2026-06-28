"""CLI: export low-amplitude (quiet) bipolar EGM segments as a noise bank.

Wired as the ``iafdb-export-noise-bank`` console_script. All
substantive parameters live in the YAML config; only ``--overwrite``
and ``--no-progress`` remain as run-time flags. Produces a pair of
files: ``<output>.h5`` (the slim noise_bank schema) and
``<output>_run_record.json`` (the provenance sidecar).

Usage
-----
::

    iafdb-export-noise-bank CONFIG.yaml [--overwrite] [--no-progress]

Examples
--------
::

    # Default 20th-percentile quiet (scale-invariant), 200 ms windows.
    iafdb-export-noise-bank examples/iafdb_noise_percentile.yaml

    # Conservative absolute threshold (Sanders 2003 "electrically silent").
    iafdb-export-noise-bank examples/iafdb_noise_absolute.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from myocard_egm_signal import (
    AbsoluteQuietThreshold,
    NoiseSegmentStrategy,
    PercentileQuietThreshold,
)

from myocard_iafdb_pipeline.cli._config import (
    ConfigError,
    NoiseBankExportConfig,
    build_noise_bank_export_config,
    load_yaml,
)
from myocard_iafdb_pipeline.export.noise_export import (
    NoiseBankExportResult,
    export_noise_bank,
)
from myocard_iafdb_pipeline.records import iter_records


def _build_strategy(cfg: NoiseBankExportConfig) -> NoiseSegmentStrategy:
    if cfg.threshold_mode == "absolute":
        return AbsoluteQuietThreshold(cfg.threshold_value)
    if cfg.threshold_mode == "percentile":
        return PercentileQuietThreshold(cfg.threshold_value)
    # _config.py validated the enum; defensive fallthrough.
    raise ConfigError(f"Unknown threshold.mode: {cfg.threshold_mode!r}")


def _format_result(result: NoiseBankExportResult, *, threshold_descr: str) -> str:
    lines: list[str] = []
    lines.append(f"Wrote noise bank:     {result.bank_path}")
    lines.append(f"Wrote run record:     {result.run_record_path}")
    lines.append(f"  Bank id:            {result.bank_id}")
    lines.append(f"  N segments:         {result.n_segments}")
    lines.append(f"  Records processed:  {result.n_records_processed}")
    lines.append(f"  Records contributing: {len(result.source_records)}")
    lines.append(f"  Threshold:          {threshold_descr}")
    if result.per_patient_counts:
        per_patient = ", ".join(f"{p}={n}" for p, n in result.per_patient_counts.items())
        lines.append(f"  By patient:         {per_patient}")
    if result.per_channel_counts:
        per_channel = ", ".join(f"{c}={n}" for c, n in result.per_channel_counts.items())
        lines.append(f"  By channel:         {per_channel}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="iafdb-export-noise-bank",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "config",
        type=Path,
        help="Path to a YAML config file (see examples/iafdb_noise_*.yaml).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing outputs. Off by default to protect previous runs.",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Suppress the per-record tqdm progress bar.",
    )
    args = parser.parse_args(argv)

    try:
        doc = load_yaml(args.config)
        cfg = build_noise_bank_export_config(doc)
        strategy = _build_strategy(cfg)
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        records = iter_records(cfg.data_dir, skip_missing=True)
        result = export_noise_bank(
            bank_path=cfg.output,
            records=records,
            strategy=strategy,
            window_ms=cfg.window_ms,
            hop_ms=cfg.hop_ms,
            band_hz=cfg.band_hz,
            run_record_path=cfg.run_record_output,
            description=cfg.description,
            bank_id=cfg.bank_id,
            overwrite=args.overwrite,
            progress=not args.no_progress,
        )
    except FileExistsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Hint: pass --overwrite to replace.", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Hint: run `iafdb-download` first.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    threshold_descr = f"{cfg.threshold_mode} {cfg.threshold_value}"
    print(_format_result(result, threshold_descr=threshold_descr))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
