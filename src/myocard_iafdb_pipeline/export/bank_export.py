"""IAFDB iafdb_bank.h5 producer (+ optional ClassifierBank output).

Walks every IAFDB record, computes a per-record calibration, slices out
windows according to a :class:`ThresholdStrategy` (healthy / all / custom),
accumulates the segments, and serializes via ``myocard-egm-data``'s
:func:`write_iafdb_bank`. As of v0.2.0 the producer no longer owns any
HDF5-writing code — the bank's schema is the contract, and the writer
that satisfies that contract lives in egm-data.

Two output formats are supported, selected by the ``output_format`` kwarg
(or the matching CLI flag):

- ``"iafdb"`` (default) — write the iafdb_bank.h5 only. This is the
  producer's primary artifact.
- ``"classifier"`` — additionally convert the iafdb_bank to a labeled
  ClassifierBank via ``myocard_egm_data.banks.iafdb_bank_to_classifier``
  and write the resulting ClassifierBank.h5 next to the bank. Label
  policy is consumer-side; the caller supplies a ``label_fn`` (see
  ``--label-policy`` in the CLI).

Threshold strategies (from :mod:`~myocard_egm_signal.thresholds`):

- :class:`AbsoluteThreshold` — fixed mV cutoff (e.g. Sánchez 0.5,
  Kosiuk-adjusted 0.2)
- :class:`PercentileThreshold` — per-record percentile of the calibrated
  p-p distribution
- :class:`NoThreshold` — pass-through, emit every windowed segment
  (corresponds to iafdb_bank schema 1.1's ``threshold_mode="none"``;
  used for pretraining banks where label semantics don't gate selection)
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from myocard_egm_contracts import iafdb_bank as _iafdb_bank_models
from myocard_egm_contracts.schema_info import current_version
from myocard_egm_data.banks import (
    iafdb_bank_to_classifier,
    write_classifier_bank,
    write_iafdb_bank,
)
from myocard_egm_signal import (
    DEFAULT_BIPOLAR_BAND_HZ,
    DEFAULT_TARGET_QRS_PP_MV,
    AbsoluteThreshold,
    HealthySegment,
    NoThreshold,
    PercentileThreshold,
    ThresholdStrategy,
    compute_calibration,
    extract_healthy_segments,
)

from myocard_iafdb_pipeline.constants import BIPOLAR_CHANNELS, SAMPLING_RATE_HZ
from myocard_iafdb_pipeline.ids import derive_iafdb_bank_id, validate_artifact_id
from myocard_iafdb_pipeline.records import IAFDBRecord

BANK_SOURCE: str = "iafdb v1.0.0"
"""Provenance tag identifying the upstream dataset."""

DEFAULT_WINDOW_MS: float = 512.0
"""Segment length in ms — matches the classifier's T=512 input at 1 kHz."""

DEFAULT_HOP_MS: float = 256.0
"""Default hop between adjacent windows in ms. 50% overlap of the 512 ms window."""

OutputFormat = Literal["iafdb", "classifier"]


@dataclass(frozen=True)
class BankExportResult:
    """Summary of a single bank-export run.

    ``classifier_path`` is set only when ``output_format="classifier"``;
    None otherwise.
    """

    output_path: Path
    bank_id: str
    classifier_path: Path | None
    n_segments: int
    source_records: tuple[str, ...]
    n_records_processed: int
    per_patient_counts: dict[str, int]
    per_channel_counts: dict[str, int]


def _threshold_provenance(threshold: ThresholdStrategy) -> tuple[str, float | None]:
    """Return ``(mode, value)`` for the bank's root attrs.

    ``NoThreshold`` produces ``("none", None)`` — the iafdb_bank schema
    accepts None for ``threshold_value`` since v1.1.
    """
    if isinstance(threshold, AbsoluteThreshold):
        return "absolute", float(threshold.value)
    if isinstance(threshold, PercentileThreshold):
        return "percentile", float(threshold.percentile)
    if isinstance(threshold, NoThreshold):
        return "none", None
    return str(threshold.name), float("nan")


def export_bank(
    output_path: Path | str,
    *,
    records: Iterable[IAFDBRecord],
    threshold: ThresholdStrategy,
    window_ms: float = DEFAULT_WINDOW_MS,
    hop_ms: float = DEFAULT_HOP_MS,
    target_qrs_pp_mv: float = DEFAULT_TARGET_QRS_PP_MV,
    band_hz: tuple[float, float] = DEFAULT_BIPOLAR_BAND_HZ,
    overwrite: bool = False,
    progress: bool = False,
    output_format: OutputFormat = "iafdb",
    label_fn: Callable[[Any], Any] | None = None,
    classifier_path: Path | str | None = None,
    bank_id: str | None = None,
) -> BankExportResult:
    """Run the bank-export pipeline.

    Pipeline per record: compute :class:`Calibration` via R-wave
    anchoring → call :func:`extract_healthy_segments` with the calibration
    and threshold → accumulate. At the end, build a Pydantic
    :class:`myocard_egm_contracts.iafdb_bank.IafdbBank` and hand it to
    :func:`myocard_egm_data.banks.write_iafdb_bank`.

    When ``output_format="classifier"``, the pipeline additionally
    converts the iafdb bank to a labeled
    :class:`~myocard_egm_data.banks.ClassifierBank` (via the egm-data
    converter, applying ``label_fn``) and writes it next to the iafdb
    bank.

    Parameters
    ----------
    output_path
        Destination iafdb_bank.h5 path. Parent directory is created if
        missing.
    records
        Iterable of records to process.
    threshold
        Threshold strategy. Operates on the calibrated, band-passed
        peak-to-peak distribution. Use :class:`NoThreshold` to emit every
        window (pretraining banks).
    window_ms, hop_ms
        Sliding-window length and stride in milliseconds.
    target_qrs_pp_mv
        Calibration target. Passed through to R-wave anchoring.
    band_hz
        Bipolar band-pass edges (Hz). Defaults to the clinical 30-300 Hz
        band.
    overwrite
        Replace existing outputs if True.
    progress
        Wrap the records iterator with ``tqdm`` if available.
    output_format
        ``"iafdb"`` (default) writes only the iafdb_bank.h5;
        ``"classifier"`` also writes a sibling ClassifierBank.h5.
    label_fn
        Required when ``output_format="classifier"``. Forwarded to
        :func:`iafdb_bank_to_classifier` from egm-data.
    classifier_path
        Optional explicit path for the ClassifierBank output. Defaults
        to ``output_path.with_suffix(".classifier.h5")`` when omitted.
    bank_id
        Optional explicit stable cross-artifact id to stamp on the bank.
        When omitted, a default is derived at write time:
        ``tbank_iafdb_<date>`` for a thresholded bank, or
        ``ptbank_iafdb_<date>`` for an unfiltered (``NoThreshold``)
        pretraining bank. An explicit id is validated against the
        egm-contracts ArtifactId pattern.

    Returns
    -------
    BankExportResult
        Run summary including counts and produced paths.
    """
    if window_ms <= 0 or hop_ms <= 0:
        raise ValueError("window_ms and hop_ms must be positive.")
    if output_format == "classifier" and label_fn is None:
        raise ValueError(
            "output_format='classifier' requires label_fn — labeling is a "
            "consumer-side policy decision; this producer does not pick one."
        )

    output_path = Path(output_path)
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"{output_path} already exists; pass overwrite=True to replace.")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Resolve the stable cross-artifact id up front so a malformed explicit
    # id fails before the (potentially long) record sweep. The role prefix
    # follows the threshold: an unfiltered export is a pretraining bank
    # (ptbank_), a thresholded export is a training bank (tbank_).
    threshold_mode, threshold_value = _threshold_provenance(threshold)
    resolved_bank_id = (
        validate_artifact_id(bank_id)
        if bank_id is not None
        else derive_iafdb_bank_id(threshold_mode)
    )

    window_samples = round(window_ms * 1e-3 * SAMPLING_RATE_HZ)

    iterator: Iterable[IAFDBRecord] = records
    if progress:
        try:
            from tqdm import tqdm
        except ImportError:  # pragma: no cover — tqdm is a hard runtime dep
            pass
        else:
            iterator = tqdm(records, desc="Records")

    all_segments: list[HealthySegment] = []
    all_scalars: list[float] = []
    contributing_records: list[str] = []
    n_records_processed = 0

    for record in iterator:
        n_records_processed += 1
        if float(record.fs) != float(SAMPLING_RATE_HZ):
            raise ValueError(
                f"Record {record.name} has fs={record.fs}, expected "
                f"{SAMPLING_RATE_HZ} (IAFDB canonical). Resampling is not "
                "currently supported in the bank export."
            )

        cal = compute_calibration(record, target_qrs_pp_mv=target_qrs_pp_mv)
        record_segments = extract_healthy_segments(
            record,
            threshold=threshold,
            channels=BIPOLAR_CHANNELS,
            calibration=cal,
            window_ms=window_ms,
            hop_ms=hop_ms,
            band_hz=band_hz,
        )
        if record_segments:
            contributing_records.append(record.name)
            for seg in record_segments:
                if seg.signal.size != window_samples:
                    raise ValueError(
                        f"Segment from {seg.record_name} ch {seg.channel} has "
                        f"length {seg.signal.size}, expected {window_samples}."
                    )
                all_segments.append(seg)
                all_scalars.append(cal.scalar)

    # Build the Pydantic model — the hand-off to egm-data.
    pyd_bank = _build_iafdb_bank_model(
        segments=all_segments,
        scalars=all_scalars,
        source_records=tuple(contributing_records),
        bank_id=resolved_bank_id,
        threshold_mode=threshold_mode,
        threshold_value=threshold_value,
        target_qrs_pp_mv=target_qrs_pp_mv,
        window_ms=window_ms,
        window_samples=window_samples,
        hop_ms=hop_ms,
        band_hz=band_hz,
    )
    write_iafdb_bank(pyd_bank, output_path, overwrite=overwrite)

    # Optional classifier-format output via egm-data converter.
    cb_path: Path | None = None
    if output_format == "classifier":
        assert label_fn is not None  # guarded above
        cb_path = (
            Path(classifier_path)
            if classifier_path is not None
            else output_path.with_suffix(".classifier.h5")
        )
        if cb_path.exists() and not overwrite:
            raise FileExistsError(f"{cb_path} already exists; pass overwrite=True to replace.")
        cb = iafdb_bank_to_classifier(
            pyd_bank,
            bank_path=output_path,
            label_fn=label_fn,
        )
        write_classifier_bank(cb, cb_path, overwrite=overwrite)

    per_patient: dict[str, int] = {}
    per_channel: dict[str, int] = {}
    for seg in all_segments:
        per_patient[seg.patient] = per_patient.get(seg.patient, 0) + 1
        per_channel[seg.channel] = per_channel.get(seg.channel, 0) + 1

    return BankExportResult(
        output_path=output_path,
        bank_id=resolved_bank_id,
        classifier_path=cb_path,
        n_segments=len(all_segments),
        source_records=tuple(contributing_records),
        n_records_processed=n_records_processed,
        per_patient_counts=dict(sorted(per_patient.items())),
        per_channel_counts=dict(sorted(per_channel.items())),
    )


# ---------------------------------------------------------------------------
# Pydantic model construction (private)
# ---------------------------------------------------------------------------


def _build_iafdb_bank_model(
    *,
    segments: list[HealthySegment],
    scalars: list[float],
    source_records: tuple[str, ...],
    bank_id: str,
    threshold_mode: str,
    threshold_value: float | None,
    target_qrs_pp_mv: float,
    window_ms: float,
    window_samples: int,
    hop_ms: float,
    band_hz: tuple[float, float],
) -> _iafdb_bank_models.IafdbBank:
    """Assemble accumulator state into a Pydantic IafdbBank model.

    The Pydantic model is the contract with egm-data's writer — building
    it here means schema-shaped validation happens before any disk I/O.
    """
    n = len(segments)
    signal_list = [seg.signal.astype(np.float32, copy=False).tolist() for seg in segments]

    doc: dict[str, Any] = {
        "schema_version": current_version("iafdb_bank"),
        "created_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "bank_id": bank_id,
        "source": BANK_SOURCE,
        "fs_hz": float(SAMPLING_RATE_HZ),
        "trace_duration_ms": float(window_ms),
        "calibration_method": "r_wave_anchoring",
        "calibration_target_qrs_pp_mv": float(target_qrs_pp_mv),
        "threshold_mode": threshold_mode,
        "threshold_value": threshold_value,
        "band_hz": [float(band_hz[0]), float(band_hz[1])],
        "window_ms": float(window_ms),
        "window_samples": int(window_samples),
        "hop_ms": float(hop_ms),
        "source_records": list(source_records),
        "traces": {
            "signal": signal_list,
            "patient_id": [s.patient for s in segments],
            "source_record": [s.record_name for s in segments],
            "source_channel": [s.channel for s in segments],
            "start_sample": [int(s.start_sample) for s in segments],
            "peak_to_peak_mv": [float(s.peak_to_peak_mv) for s in segments],
            "calibration_scalar": [float(c) for c in scalars],
        },
    }
    # Empty banks are allowed — the schema accepts n=0; the writer
    # serializes an empty traces group. This handles the case where no
    # record contributed a segment under the chosen threshold.
    if n == 0:
        doc["traces"]["signal"] = []
    return _iafdb_bank_models.IafdbBank.model_validate(doc)
