"""Loading IAFDB records from WFDB files into typed objects.

The :class:`IAFDBRecord` dataclass is the canonical in-memory representation
used by the rest of the package.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import wfdb

from myocard_iafdb_pipeline.constants import RECORD_NAMES


@dataclass(frozen=True)
class IAFDBRecord:
    """A single IAFDB record loaded into memory.

    Attributes
    ----------
    name
        Record name, e.g. ``"iaf1_afw"``.
    patient
        Patient identifier, e.g. ``"iaf1"``.
    placement
        Catheter placement, one of ``"svc"``, ``"ivc"``, ``"tva"``, ``"afw"``.
    fs
        Sampling frequency in Hz (1000 for IAFDB).
    signal
        Array of shape ``(n_samples, n_channels)`` in physical units (mV).
    channel_names
        Channel labels in the same order as ``signal`` columns.
    units
        Physical units per channel (typically all ``"mV"``).
    comments
        Free-form header comments (drug delivery notes, etc.).
    qrs_samples
        Sample indices of QRS annotations from PhysioNet's ``.qrs`` file
        (from ``sqrs``, uncorrected). Empty array if no annotation file
        present.
    """

    name: str
    patient: str
    placement: str
    fs: float
    signal: np.ndarray
    channel_names: tuple[str, ...]
    units: tuple[str, ...]
    comments: tuple[str, ...] = field(default_factory=tuple)
    qrs_samples: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))

    @property
    def duration_s(self) -> float:
        """Recording duration in seconds."""
        return float(self.signal.shape[0] / self.fs)

    def channel_index(self, name: str) -> int:
        """Return the column index of ``name`` in :attr:`signal`.

        Raises
        ------
        KeyError
            If ``name`` is not present in :attr:`channel_names`.
        """
        try:
            return self.channel_names.index(name)
        except ValueError as exc:
            raise KeyError(
                f"Channel {name!r} not in record {self.name} (available: {self.channel_names})"
            ) from exc


def load_record(name: str, data_dir: Path | str) -> IAFDBRecord:
    """Load a single IAFDB record from ``data_dir``.

    Parameters
    ----------
    name
        Record name (must be one of :data:`myocard_iafdb_pipeline.RECORD_NAMES`).
    data_dir
        Directory containing the WFDB files (``.dat``, ``.hea``, optionally
        ``.qrs``).

    Raises
    ------
    ValueError
        If ``name`` is not a known IAFDB record.
    FileNotFoundError
        If the ``.hea`` file is missing from ``data_dir``.
    """
    if name not in RECORD_NAMES:
        raise ValueError(f"Unknown record {name!r}. Expected one of {RECORD_NAMES}.")

    data_dir = Path(data_dir)
    record_path = data_dir / name
    if not record_path.with_suffix(".hea").is_file():
        raise FileNotFoundError(
            f"Header not found: {record_path.with_suffix('.hea')}. Did you run iafdb-download?"
        )

    rec = wfdb.rdrecord(str(record_path))
    signal = np.asarray(rec.p_signal, dtype=np.float64)
    channel_names = tuple(rec.sig_name)
    units = tuple(rec.units)
    comments = tuple(rec.comments or ())

    qrs_samples = np.empty(0, dtype=np.int64)
    if record_path.with_suffix(".qrs").is_file():
        try:
            ann = wfdb.rdann(str(record_path), "qrs")
            qrs_samples = np.asarray(ann.sample, dtype=np.int64)
        except Exception:
            # Non-fatal: annotation read can fail on edge cases. Caller can
            # always re-derive QRS via signal processing.
            pass

    patient, placement = name.split("_", 1)
    return IAFDBRecord(
        name=name,
        patient=patient,
        placement=placement,
        fs=float(rec.fs),
        signal=signal,
        channel_names=channel_names,
        units=units,
        comments=comments,
        qrs_samples=qrs_samples,
    )


def iter_records(
    data_dir: Path | str,
    records: Iterable[str] | None = None,
    skip_missing: bool = False,
) -> Iterator[IAFDBRecord]:
    """Iterate over IAFDB records present in ``data_dir``.

    Parameters
    ----------
    data_dir
        Directory containing the downloaded dataset.
    records
        Subset of record names to load. Defaults to all 32.
    skip_missing
        If True, silently skip records whose header is missing instead of
        raising :class:`FileNotFoundError`.
    """
    names = list(records) if records is not None else list(RECORD_NAMES)
    for name in names:
        try:
            yield load_record(name, data_dir)
        except FileNotFoundError:
            if not skip_missing:
                raise
