"""myocard_iafdb_pipeline — IAFDB download + bank-export producer.

This package owns the IAFDB-specific download + load logic plus the
CLIs that orchestrate the bank exports. Generic signal-processing
primitives (filters, threshold strategies, calibration, segment
extractors) live in :mod:`myocard_egm_signal` (since v0.2.0; previously
inlined here). Bank-writing is delegated to :mod:`myocard_egm_data`.
Schemas live in :mod:`myocard_egm_contracts`.

Subpackages
-----------
- :mod:`myocard_iafdb_pipeline.constants` — dataset constants (patients,
  placements, channels, sampling rate, noise defaults).
- :mod:`myocard_iafdb_pipeline.records` — load IAFDB records into typed
  :class:`IAFDBRecord` objects (satisfy the egm-signal ``Record``
  Protocol structurally).
- :mod:`myocard_iafdb_pipeline.download` — fetch records from PhysioNet.
- :mod:`myocard_iafdb_pipeline.export` — :func:`export_bank` (iafdb_bank
  + optional ClassifierBank) and :func:`export_noise_bank` (noise_bank
  + sibling run record). Both orchestrate egm-signal primitives + the
  egm-data writers.
- :mod:`myocard_iafdb_pipeline.cli` — command-line entry points
  (``iafdb-download``, ``iafdb-inspect``, ``iafdb-export-bank``,
  ``iafdb-export-noise-bank``).
"""

from __future__ import annotations

from importlib import metadata

from myocard_iafdb_pipeline.constants import (
    BIPOLAR_CHANNELS,
    DEFAULT_NOISE_HOP_MS,
    DEFAULT_NOISE_WINDOW_MS,
    PATIENTS,
    PLACEMENTS,
    RECORD_NAMES,
    SAMPLING_RATE_HZ,
    SURFACE_ECG_CHANNELS,
)
from myocard_iafdb_pipeline.export import (
    BankExportResult,
    NoiseBankExportResult,
    export_bank,
    export_noise_bank,
)
from myocard_iafdb_pipeline.records import IAFDBRecord, iter_records, load_record

try:
    __version__ = metadata.version("myocard-iafdb-pipeline")
except metadata.PackageNotFoundError:  # pragma: no cover — editable install without metadata
    __version__ = "0.0.0+unknown"


__all__ = [
    "BIPOLAR_CHANNELS",
    "DEFAULT_NOISE_HOP_MS",
    "DEFAULT_NOISE_WINDOW_MS",
    "PATIENTS",
    "PLACEMENTS",
    "RECORD_NAMES",
    "SAMPLING_RATE_HZ",
    "SURFACE_ECG_CHANNELS",
    "BankExportResult",
    "IAFDBRecord",
    "NoiseBankExportResult",
    "__version__",
    "export_bank",
    "export_noise_bank",
    "iter_records",
    "load_record",
]
