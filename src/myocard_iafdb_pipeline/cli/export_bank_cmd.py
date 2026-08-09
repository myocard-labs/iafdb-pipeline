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

    # IAFDB + ClassifierBank pair (all-healthy labels — see the warning
    # in that file; unlabeled is the default and the honest choice).
    iafdb-export-bank examples/iafdb_classifier.yaml

    # IAFDB + unlabeled ClassifierBank (no ground truth) for inference.
    iafdb-export-bank examples/iafdb_classifier_unlabeled.yaml
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
from myocard_iafdb_pipeline.export.activation_extract import ChannelTally
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

    Policies:

    - ``"all-healthy"`` — every trace labeled 0 ("healthy"). **Unproven and
      likely wrong for IAFDB, and opt-in for that reason.** It asserts that
      every selected trace is non-fibrotic, which is a restatement of the
      amplitude threshold rather than evidence: IAFDB carries no
      per-segment fibrosis truth, every patient is arrhythmic, and there is
      no independent labeling to check against. The approach came from a
      paper using a simple amplitude rule to separate healthy from
      unhealthy tissue; it does not transfer to the classification this
      project is doing.
    - ``"unlabeled"`` — **the default.** No ground truth: the label_fn returns ``None``, which
      egm-data's converter maps to every ``label_truth = None`` plus an empty
      labels dict. The honest policy for IAFDB; use it to build the inference
      bank an eval run turns into an unlabeled (``upred_``) predictions bank.

    The label_fn signature is fixed (a callable taking the Pydantic
    ``IafdbBank`` and returning ``(labels, labels_dict)`` *or* ``None``), so
    adding a policy is a config-key + a closure. [egm-data ``IAFDBLabelFn``]
    """
    if name == "all-healthy":

        def all_healthy(bank: Any) -> tuple[np.ndarray, dict[int, str]]:
            n = len(bank.traces.signal)
            return np.zeros(n, dtype=np.int64), {0: "healthy"}

        return all_healthy

    if name == "unlabeled":

        def unlabeled(bank: Any) -> None:
            # No per-segment ground truth. Returning None tells egm-data's
            # converter to leave every label_truth = None (labels dict empty).
            return None

        return unlabeled

    raise ConfigError(f"Unknown format.label_policy: {name!r}")


def _format_result(result: BankExportResult, *, threshold_descr: str) -> str:
    lines: list[str] = []
    lines.append(f"Wrote iafdb bank: {result.output_path}")
    if result.classifier_path is not None:
        lines.append(f"Wrote classifier bank: {result.classifier_path}")
    lines.append(f"  Bank id:              {result.bank_id}")
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


def _format_yield(tallies: tuple[ChannelTally, ...]) -> str:
    """Summarize activation-mode yield: what was detected, kept and dropped.

    Printed because the drop reasons are the operator's tuning signal. A
    bare segment count cannot distinguish "the detector found little" from
    "it found plenty and nearly all of it was multi-activation" — and on
    this dataset, where every patient is arrhythmic, the second is the
    likely failure. The two drop columns have different remedies, which is
    why they are reported apart rather than summed.
    """
    detected = sum(t.detected for t in tallies)
    kept = sum(t.kept for t in tallies)
    boundary = sum(t.dropped_boundary for t in tallies)
    multi = sum(t.dropped_multi_activation for t in tallies)
    kept_multi = sum(t.kept_multi_activation for t in tallies)
    degenerate = [t for t in tallies if t.degenerate]

    pct = f"{100.0 * kept / detected:.1f}%" if detected else "n/a"
    lines = [
        "  Activation yield:",
        f"    Detected:           {detected}",
        f"    Kept:               {kept} ({pct} of detected)",
        f"    Dropped, boundary:  {boundary}",
        f"    Dropped, multi:     {multi}",
    ]
    if kept_multi:
        lines.append(f"    (kept multi-activation: {kept_multi})")
    if degenerate:
        names = ", ".join(f"{t.record_name}/{t.channel}" for t in degenerate)
        lines.append(f"    Degenerate channels: {len(degenerate)} ({names})")
    if detected and kept == 0:
        lines.append(
            "    NOTE: activations were detected but every window was dropped. "
            "If most were multi-activation, the detection settings are not "
            "separating activations — set activation.keep_multi_activation: true "
            "to inspect them."
        )
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
            activation=cfg.activation,
            window_ms=cfg.window_ms,
            hop_ms=cfg.hop_ms,
            target_qrs_pp_mv=cfg.target_qrs_pp_mv,
            band_hz=cfg.band_hz,
            overwrite=args.overwrite,
            progress=not args.no_progress,
            output_format=cfg.output_format,
            label_fn=label_fn,
            classifier_path=cfg.classifier_output,
            bank_id=cfg.bank_id,
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

    if not result.written:
        # export_bank already warned with the reason; say plainly that no
        # file exists rather than printing a summary of a bank that isn't
        # there.
        print(f"No bank written to {result.output_path} — nothing survived.", file=sys.stderr)
        return 1

    threshold_descr = (
        "activation-anchored (no amplitude selection)"
        if cfg.activation is not None
        else "none (unfiltered)"
        if cfg.threshold_mode == "none"
        else f"{cfg.threshold_mode} {cfg.threshold_value}"
    )
    print(_format_result(result, threshold_descr=threshold_descr))
    if result.channel_tallies:
        print(_format_yield(result.channel_tallies))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
