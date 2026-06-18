"""HDF5 bank exports for IAFDB-derived data.

Two producers ship here, both delegating bank-writing to
``myocard-egm-data``:

- :func:`export_bank` — write an ``iafdb_bank.h5``. Optionally also
  emit a paired ``ClassifierBank.h5`` via the egm-data converter
  when ``output_format="classifier"``.
- :func:`export_noise_bank` — write a (slim) ``noise_bank.h5`` plus
  its sidecar ``noise_bank_run_record.json`` (the provenance pair
  defined in egm-contracts v0.2.0).
"""

from __future__ import annotations

from myocard_iafdb_pipeline.constants import (
    DEFAULT_NOISE_HOP_MS,
    DEFAULT_NOISE_WINDOW_MS,
)

from .bank_export import (
    DEFAULT_HOP_MS,
    DEFAULT_WINDOW_MS,
    BankExportResult,
    export_bank,
)
from .noise_export import (
    NoiseBankExportResult,
    export_noise_bank,
)

__all__ = [
    "DEFAULT_HOP_MS",
    "DEFAULT_NOISE_HOP_MS",
    "DEFAULT_NOISE_WINDOW_MS",
    "DEFAULT_WINDOW_MS",
    "BankExportResult",
    "NoiseBankExportResult",
    "export_bank",
    "export_noise_bank",
]
