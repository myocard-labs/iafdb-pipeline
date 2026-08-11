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


def derive_classifier_bank_id(*, has_labels: bool, today: str | None = None) -> str:
    """Default stable id for the paired ClassifierBank export (CL-145).

    **Deliberately not the source `iafdb_bank`'s id.** These are two
    artifacts and each carries its own identity; reusing the source id
    would manufacture provenance, and — more concretely — the two can
    legitimately disagree about their role. A `threshold.mode: absolute`
    export with `label_policy: unlabeled` produces a `tbank_` iafdb bank
    (thresholded) sitting next to a `ptbank_` ClassifierBank (no labels).
    That looks like an inconsistency and is not one: the prefixes answer
    different questions.

    The role here follows **label presence**, because that is what the
    prefix promises a consumer. egm-data's
    :func:`check_classifier_bank_id_matches_content` enforces the same
    mapping in both directions at write time — a `ptbank_` bank carrying
    labels is refused, and so is a `tbank_` bank without them — so a
    derivation that disagreed with the content would not merely be
    confusing, it would fail the write.

    Derived from the **produced bank** rather than the configured
    `label_policy` string, so the two cannot drift: `export_bank` takes a
    caller-supplied ``label_fn``, and a policy name is only the CLI's way
    of choosing one. What ends up in the file is the authority.

    Note this is only ``tbank_`` / ``ptbank_``. The prediction roles
    (``lpred_`` / ``upred_``) belong to whatever *evaluates* the bank —
    this producer never writes predictions, and eval writes a new bank
    rather than mutating this one.

    Neither prefix is a great fit for what these banks are actually used
    for — feature comparison against a synthetic bank in egm-studio, or
    unlabeled inference input to egm-classifier. ``ptbank_`` is the least
    wrong of the existing vocabulary; sharpening it is **FB-19** (the
    role-vocabulary study), which already tracks three other artifacts
    with the same problem.
    """
    role = "tbank" if has_labels else "ptbank"
    return f"{role}_{DATASET_TAG}_{today or _today_utc()}"


def derive_noise_bank_id(*, today: str | None = None) -> str:
    """Default stable id for an exported noise bank (recorded on the sidecar)."""
    return f"nbank_{DATASET_TAG}_{today or _today_utc()}"
