"""IAFDB noise-bank producer.

Walks every IAFDB record, extracts low-amplitude windows via a
:class:`~myocard_egm_signal.NoiseSegmentStrategy`,
accumulates the segments, and serializes via ``myocard-egm-data``'s
:func:`write_noise_bank` (HDF5) and
:func:`write_noise_bank_run_record` (JSON sidecar with extraction
provenance).

The HDF5 bank schema (egm-contracts ``noise_bank/1.0``) is intentionally
minimal — only the fields the synthetic mixer consumes (signal,
source_record, source_channel per trace; schema_version / created_utc /
source / fs_hz at root). Extraction provenance (calibration, threshold,
windowing, optional per-trace audit) is paired with the bank as a
sibling ``noise_bank_run_record.json`` file with a matching name stem.

Calibration policy: this producer does NOT pre-calibrate the input
signals — the percentile strategy is scale-invariant and works on raw
signal, and absolute mV strategies require the *caller* to feed a
calibrated source. The run record records ``calibration_method="none"``
to be honest about what the bank's amplitudes mean. A future revision
may add an optional calibration step here mirroring the healthy-segment
export; until then, downstream consumers reading the noise bank should
not assume the signal is in mV.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from myocard_egm_contracts import noise_bank as _noise_bank_models
from myocard_egm_contracts.schema_info import current_version
from myocard_egm_data.banks import write_noise_bank
from myocard_egm_data.records import (
    build_noise_bank_run_record,
    write_noise_bank_run_record,
)
from myocard_egm_signal import (
    DEFAULT_BIPOLAR_BAND_HZ,
    AbsoluteQuietThreshold,
    NoiseSegment,
    NoiseSegmentStrategy,
    PercentileQuietThreshold,
    extract_noise_segments,
)

from myocard_iafdb_pipeline.constants import (
    BIPOLAR_CHANNELS,
    DEFAULT_NOISE_HOP_MS,
    DEFAULT_NOISE_WINDOW_MS,
    SAMPLING_RATE_HZ,
)
from myocard_iafdb_pipeline.records import IAFDBRecord

BANK_SOURCE: str = "iafdb v1.0.0"
"""Provenance tag identifying the upstream dataset."""


@dataclass(frozen=True)
class NoiseBankExportResult:
    """Summary of a single noise-bank-export run."""

    bank_path: Path
    run_record_path: Path
    n_segments: int
    source_records: tuple[str, ...]
    n_records_processed: int
    per_patient_counts: dict[str, int]
    per_channel_counts: dict[str, int]


def _threshold_provenance(strategy: NoiseSegmentStrategy) -> tuple[str, float]:
    if isinstance(strategy, AbsoluteQuietThreshold):
        return "absolute", float(strategy.value)
    if isinstance(strategy, PercentileQuietThreshold):
        return "percentile", float(strategy.percentile)
    return str(strategy.name), float("nan")


def export_noise_bank(
    bank_path: Path | str,
    *,
    records: Iterable[IAFDBRecord],
    strategy: NoiseSegmentStrategy,
    window_ms: float = DEFAULT_NOISE_WINDOW_MS,
    hop_ms: float = DEFAULT_NOISE_HOP_MS,
    band_hz: tuple[float, float] = DEFAULT_BIPOLAR_BAND_HZ,
    run_record_path: Path | str | None = None,
    description: str = "",
    overwrite: bool = False,
    progress: bool = False,
) -> NoiseBankExportResult:
    """Run the noise-bank-export pipeline.

    Pipeline per record: call :func:`extract_noise_segments` →
    accumulate. At the end, build a Pydantic
    :class:`myocard_egm_contracts.noise_bank.NoiseBank` and hand it to
    :func:`myocard_egm_data.banks.write_noise_bank`. Also build the
    provenance sidecar via
    :func:`myocard_egm_data.records.build_noise_bank_run_record` and
    write it next to the bank.

    Parameters
    ----------
    bank_path
        Destination noise_bank.h5 path. Parent directory is created if
        missing.
    records
        Iterable of records to process.
    strategy
        Noise threshold strategy.
    window_ms, hop_ms
        Sliding-window length and stride in milliseconds. Defaults match
        :data:`DEFAULT_NOISE_WINDOW_MS` / :data:`DEFAULT_NOISE_HOP_MS`.
    band_hz
        Bipolar band-pass edges (Hz). Defaults to the clinical 30-300 Hz
        band.
    run_record_path
        Optional explicit path for the JSON sidecar. Defaults to
        ``bank_path`` with ``"_run_record.json"`` appended to the stem
        (e.g. ``foo_noise.h5`` → ``foo_noise_run_record.json``).
    description
        Free-form note recorded into the run record.
    overwrite
        Replace existing outputs if True.
    progress
        Wrap the records iterator with ``tqdm`` if available.

    Returns
    -------
    NoiseBankExportResult
        Run summary including counts and produced paths.
    """
    if window_ms <= 0 or hop_ms <= 0:
        raise ValueError("window_ms and hop_ms must be positive.")

    bank_path = Path(bank_path)
    if bank_path.exists() and not overwrite:
        raise FileExistsError(f"{bank_path} already exists; pass overwrite=True to replace.")
    bank_path.parent.mkdir(parents=True, exist_ok=True)

    # Convention: matching stem + "_run_record.json".
    if run_record_path is None:
        run_record_path = bank_path.with_name(bank_path.stem + "_run_record.json")
    else:
        run_record_path = Path(run_record_path)
    if run_record_path.exists() and not overwrite:
        raise FileExistsError(f"{run_record_path} already exists; pass overwrite=True to replace.")

    window_samples = round(window_ms * 1e-3 * SAMPLING_RATE_HZ)

    iterator: Iterable[IAFDBRecord] = records
    if progress:
        try:
            from tqdm import tqdm
        except ImportError:  # pragma: no cover — tqdm is a hard runtime dep
            pass
        else:
            iterator = tqdm(records, desc="Records")

    all_segments: list[NoiseSegment] = []
    contributing_records: list[str] = []
    n_records_processed = 0

    for record in iterator:
        n_records_processed += 1
        if float(record.fs) != float(SAMPLING_RATE_HZ):
            raise ValueError(
                f"Record {record.name} has fs={record.fs}, expected "
                f"{SAMPLING_RATE_HZ} (IAFDB canonical)."
            )
        record_segments = extract_noise_segments(
            record,
            strategy,
            channels=BIPOLAR_CHANNELS,
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

    # Build the (slim) Pydantic bank model.
    pyd_bank = _build_noise_bank_model(all_segments)
    write_noise_bank(pyd_bank, bank_path, overwrite=overwrite)

    # Build + write the provenance sidecar with the optional per-trace
    # block (we have it; producers always should when they can).
    threshold_mode, threshold_value = _threshold_provenance(strategy)
    run_record = build_noise_bank_run_record(
        source=BANK_SOURCE,
        fs_hz=float(SAMPLING_RATE_HZ),
        window_ms=float(window_ms),
        window_samples=int(window_samples),
        hop_ms=float(hop_ms),
        band_hz=[float(band_hz[0]), float(band_hz[1])],
        calibration_method="none",
        calibration_target_qrs_pp_mv=None,
        threshold_mode=threshold_mode,
        threshold_value=threshold_value,
        source_records=list(contributing_records),
        description=description,
        per_trace_provenance=(
            {
                "patient_id": [s.patient for s in all_segments],
                "start_sample": [int(s.start_sample) for s in all_segments],
                "peak_to_peak_mv": [float(s.peak_to_peak_mv) for s in all_segments],
                # Calibration is method='none' so no per-trace scaling was
                # applied; write 1.0 per the schema's producer contract so
                # downstream consumers don't accidentally rescale.
                "calibration_scalar": [1.0] * len(all_segments),
            }
            if all_segments
            else None
        ),
    )
    write_noise_bank_run_record(run_record_path, run_record)

    per_patient: dict[str, int] = {}
    per_channel: dict[str, int] = {}
    for seg in all_segments:
        per_patient[seg.patient] = per_patient.get(seg.patient, 0) + 1
        per_channel[seg.channel] = per_channel.get(seg.channel, 0) + 1

    return NoiseBankExportResult(
        bank_path=bank_path,
        run_record_path=run_record_path,
        n_segments=len(all_segments),
        source_records=tuple(contributing_records),
        n_records_processed=n_records_processed,
        per_patient_counts=dict(sorted(per_patient.items())),
        per_channel_counts=dict(sorted(per_channel.items())),
    )


# ---------------------------------------------------------------------------
# Pydantic model construction (private)
# ---------------------------------------------------------------------------


def _build_noise_bank_model(segments: list[NoiseSegment]) -> _noise_bank_models.NoiseBank:
    """Assemble accumulator state into a Pydantic NoiseBank (slim) model."""
    signal_list = [seg.signal.astype(np.float32, copy=False).tolist() for seg in segments]
    doc: dict[str, Any] = {
        "schema_version": current_version("noise_bank"),
        "created_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "source": BANK_SOURCE,
        "fs_hz": float(SAMPLING_RATE_HZ),
        "traces": {
            "signal": signal_list,
            "source_record": [s.record_name for s in segments],
            "source_channel": [s.channel for s in segments],
        },
    }
    return _noise_bank_models.NoiseBank.model_validate(doc)
