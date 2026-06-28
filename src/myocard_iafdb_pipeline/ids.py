"""Stable cross-artifact id helpers for IAFDB-pipeline outputs.

Every bank this producer writes carries a stable id (an egm-contracts
``ArtifactId``) that downstream phase manifests + the provenance graph key
on. The producer either accepts an explicit, hand-curated id or derives a
sensible default at write time:

- iafdb_bank: ``{role}_iafdb_{date}`` — ``tbank_`` for a thresholded
  training bank, ``ptbank_`` for the unfiltered (``threshold=none``)
  pretraining path.
- noise bank: ``nbank_iafdb_{date}`` (recorded on the run-record sidecar;
  the slim noise_bank HDF5 carries no id).

The id *pattern* is single-sourced in egm-contracts' ``common.ArtifactId``;
this module only composes candidate strings and validates them against it.
"""

from __future__ import annotations

import datetime as _dt

from myocard_egm_contracts import common as _contracts_common
from pydantic import ValidationError

DATASET_TAG = "iafdb"
"""Descriptor segment identifying the upstream dataset in derived ids."""


def _today_utc() -> str:
    """Today's date (UTC) as ``YYYY-MM-DD`` for the id's date segment."""
    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


def validate_artifact_id(value: str) -> str:
    """Validate ``value`` against the egm-contracts ArtifactId pattern.

    Returns the value unchanged on success; raises ``ValueError`` with a
    producer-friendly message on a malformed id. Mirrors the validation
    egm-data applies to ClassifierBank ids (``common.ArtifactId``), so an
    explicit override fails fast at the producer boundary rather than deep
    inside the writer.
    """
    try:
        _contracts_common.ArtifactId(value)
    except ValidationError as exc:
        raise ValueError(
            f"bank_id {value!r} is not a valid stable artifact id "
            "(egm-contracts ArtifactId pattern, e.g. 'tbank_iafdb_2026-06-27'): "
            "a lowercase role prefix, a descriptor, and an ISO date."
        ) from exc
    return value


def derive_iafdb_bank_id(threshold_mode: str, *, today: str | None = None) -> str:
    """Default stable id for an exported iafdb_bank.

    The role prefix follows the bank's purpose: an unfiltered export
    (``threshold_mode == "none"``) is an unsupervised pretraining bank
    (``ptbank_``); any thresholded export is a training bank (``tbank_``).
    """
    role = "ptbank" if threshold_mode == "none" else "tbank"
    return f"{role}_{DATASET_TAG}_{today or _today_utc()}"


def derive_noise_bank_id(*, today: str | None = None) -> str:
    """Default stable id for an exported noise bank (recorded on the sidecar)."""
    return f"nbank_{DATASET_TAG}_{today or _today_utc()}"
