"""CLI: export bipolar EGM segments into an HDF5 bank.

Wired as the ``iafdb-export-bank`` console_script. All substantive
parameters live in the YAML config; only ``--overwrite`` and
``--no-progress`` remain as run-time flags. See
``examples/iafdb_*.yaml`` for ready-to-use config files.

Usage
-----
::

    iafdb-export-bank CONFIG.yaml [--overwrite] [--no-progress]

Examples
--------
::

    # Default Kosiuk AF-adjusted 0.2 mV healthy threshold.
    iafdb-export-bank examples/iafdb_healthy_default.yaml

    # Sánchez sinus-rhythm 0.5 mV threshold.
    iafdb-export-bank examples/iafdb_healthy_sanchez.yaml

    # Unfiltered pretraining bank.
    iafdb-export-bank examples/iafdb_pretrain.yaml

    # IAFDB + ClassifierBank pair.
    iafdb-export-bank examples/iafdb_classifier.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np
from myocard_egm_signal import (
    AbsoluteThreshold,
    NoThreshold,
    PercentileThreshold,
    ThresholdStrategy,
)

from myocard_iafdb_pipeline.cli._config import (
    BankExportConfig,
    ConfigError,
    build_bank_export_config,
    load_yaml,
)
from myocard_iafdb_pipeline.export.bank_export import BankExportResult, export_bank
from myocard_iafdb_pipeline.records import iter_records


def _build_threshold(cfg: BankExportConfig) -> ThresholdStrategy:
    if cfg.threshold_mode == "none":
        return NoThreshold()
    if cfg.threshold_mode == "absolute":
        return AbsoluteThreshold(cfg.threshold_value or 0.2)
    if cfg.threshold_mode == "percentile":
        return PercentileThreshold(cfg.threshold_value or 70.0)
    # _config.py validated the enum; defensive fallthrough.
    raise ConfigError(f"Unknown threshold.mode: {cfg.threshold_mode!r}")


def _label_policy(name: str) -> Any:
    """Map a config-supplied policy name to a label_fn callable.

    Today the only policy is ``"all-healthy"`` — every trace is labeled 0.
    Additional policies (e.g. patient-level AF status) can be added here
    without touching the producer signature.
    """
    if name == "all-healthy":

        def label_fn(bank: Any) -> tuple[np.ndarray, dict[int, str]]:
            n = len(bank.traces.signal)
            return np.zeros(n, dtype=np.int64), {0: "healthy"}

        return label_fn
    raise ConfigError(f"Unknown format.label_policy: {name!r}")


def _format_result(result: BankExportResult, *, threshold_descr: str) -> str:
    lines: list[str] = []
    lines.append(f"Wrote iafdb bank: {result.output_path}")
    if result.classifier_path is not None:
        lines.append(f"Wrote classifier bank: {result.classifier_path}")
    lines.append(f"  N segments:           {result.n_segments}")
    lines.append(f"  Records processed:    {result.n_records_processed}")
    lines.append(f"  Records contributing: {len(result.source_records)}")
    lines.append(f"  Threshold:            {threshold_descr}")
    if result.per_patient_counts:
        per_patient = ", ".join(f"{p}={n}" for p, n in result.per_patient_counts.items())
        lines.append(f"  By patient:           {per_patient}")
    if result.per_channel_counts:
        per_channel = ", ".join(f"{c}={n}" for c, n in result.per_channel_counts.items())
        lines.append(f"  By channel:           {per_channel}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="iafdb-export-bank",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "config",
        type=Path,
        help="Path to a YAML config file (see examples/iafdb_*.yaml).",
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
        cfg = build_bank_export_config(doc)
        threshold = _build_threshold(cfg)
        label_fn = _label_policy(cfg.label_policy) if cfg.output_format == "classifier" else None
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        records = iter_records(cfg.data_dir, skip_missing=True)
        result = export_bank(
            output_path=cfg.output,
            records=records,
            threshold=threshold,
            window_ms=cfg.window_ms,
            hop_ms=cfg.hop_ms,
            target_qrs_pp_mv=cfg.target_qrs_pp_mv,
            band_hz=cfg.band_hz,
            overwrite=args.overwrite,
            progress=not args.no_progress,
            output_format=cfg.output_format,
            label_fn=label_fn,
            classifier_path=cfg.classifier_output,
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

    threshold_descr = (
        "none (unfiltered)"
        if cfg.threshold_mode == "none"
        else f"{cfg.threshold_mode} {cfg.threshold_value}"
    )
    print(_format_result(result, threshold_descr=threshold_descr))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
