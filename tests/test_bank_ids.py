"""Stable cross-artifact id stamping (iafdb-pipeline v0.3.0).

The producer stamps an egm-contracts ArtifactId on every bank it writes:
auto-derived by default (role taken from the threshold), or an explicit
override. These tests pin the derive / override / validate behavior and the
propagation into the ClassifierBank converter + the noise run-record sidecar.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from myocard_egm_data.banks import (
    check_classifier_bank_id_matches_content,
    load_classifier_bank,
    read_iafdb_bank_hdf5,
)
from myocard_egm_data.records import load_noise_bank_run_record
from myocard_egm_signal import AbsoluteThreshold, NoThreshold, PercentileQuietThreshold

from myocard_iafdb_pipeline.export import export_bank, export_noise_bank
from myocard_iafdb_pipeline.ids import (
    derive_classifier_bank_id,
    derive_iafdb_bank_id,
    derive_noise_bank_id,
)
from myocard_iafdb_pipeline.records import IAFDBRecord

# egm-signal v0.3.0 (B22) removed the library default for the calibration
# target, so export_bank() now REQUIRES it. 1.0 mV mirrors the CLI config
# default in cli/_config.py, which is the single place the value is decided.
TARGET_QRS_PP_MV = 1.0

# ---------------------------------------------------------------------------
# iafdb_bank id
# ---------------------------------------------------------------------------


def test_bank_auto_derives_tbank_id(synthetic_record: IAFDBRecord, tmp_path: Path) -> None:
    """A thresholded export with no explicit id gets tbank_iafdb_<date>,
    and the id round-trips onto the HDF5 root attr the reader surfaces."""
    out = tmp_path / "bank.h5"
    result = export_bank(
        out,
        records=[synthetic_record],
        threshold=AbsoluteThreshold(0.1),
        calibration_method="r_wave_anchoring",
        target_qrs_pp_mv=TARGET_QRS_PP_MV,
        progress=False,
    )
    expected = derive_iafdb_bank_id("absolute")
    assert result.bank_id == expected
    assert expected.startswith("tbank_iafdb_")
    assert read_iafdb_bank_hdf5(out).bank_id == expected


def test_bank_none_threshold_derives_ptbank_id(
    synthetic_record: IAFDBRecord, tmp_path: Path
) -> None:
    """The unfiltered (NoThreshold) pretraining path uses the ptbank_ role
    prefix instead of tbank_."""
    out = tmp_path / "bank.h5"
    result = export_bank(
        out,
        records=[synthetic_record],
        threshold=NoThreshold(),
        calibration_method="r_wave_anchoring",
        target_qrs_pp_mv=TARGET_QRS_PP_MV,
        progress=False,
    )
    assert result.bank_id == derive_iafdb_bank_id("none")
    assert result.bank_id.startswith("ptbank_iafdb_")


def test_bank_explicit_id_is_used(synthetic_record: IAFDBRecord, tmp_path: Path) -> None:
    """An explicit, well-formed bank_id is stamped verbatim (overrides the
    auto-derived default)."""
    out = tmp_path / "bank.h5"
    explicit = "tbank_iafdb_healthy_kosiuk_2026-06-27"
    result = export_bank(
        out,
        records=[synthetic_record],
        threshold=AbsoluteThreshold(0.1),
        calibration_method="r_wave_anchoring",
        target_qrs_pp_mv=TARGET_QRS_PP_MV,
        progress=False,
        bank_id=explicit,
    )
    assert result.bank_id == explicit
    assert read_iafdb_bank_hdf5(out).bank_id == explicit


def test_bank_rejects_malformed_id(synthetic_record: IAFDBRecord, tmp_path: Path) -> None:
    """A malformed explicit id is rejected at the producer boundary, before
    any record processing or disk I/O."""
    with pytest.raises(ValueError, match="valid stable artifact id"):
        export_bank(
            tmp_path / "bank.h5",
            records=[synthetic_record],
            threshold=AbsoluteThreshold(0.1),
            calibration_method="r_wave_anchoring",
            target_qrs_pp_mv=TARGET_QRS_PP_MV,
            progress=False,
            bank_id="NOT VALID",
        )


def test_classifier_output_propagates_source_bank_id(
    synthetic_record: IAFDBRecord, tmp_path: Path
) -> None:
    """The ClassifierBank sibling keys its source-bank entry and every trace
    by the iafdb bank's stable id (the converter requires it)."""
    out = tmp_path / "bank.h5"

    def label_fn(bank: object) -> tuple[np.ndarray, dict[int, str]]:
        n = len(bank.traces.signal)  # type: ignore[attr-defined]
        return np.zeros(n, dtype=np.int64), {0: "healthy"}

    result = export_bank(
        out,
        records=[synthetic_record],
        threshold=AbsoluteThreshold(0.1),
        calibration_method="r_wave_anchoring",
        target_qrs_pp_mv=TARGET_QRS_PP_MV,
        progress=False,
        output_format="classifier",
        label_fn=label_fn,
    )
    assert result.classifier_path is not None
    cb = load_classifier_bank(result.classifier_path)
    assert cb.banks[0].bank_id == result.bank_id
    assert all(t.bank_id == result.bank_id for t in cb.traces)


def _unlabeled(bank: object) -> None:
    return None


def _all_healthy(bank: object) -> tuple[np.ndarray, dict[int, str]]:
    n = len(bank.traces.signal)  # type: ignore[attr-defined]
    return np.zeros(n, dtype=np.int64), {0: "healthy"}


def test_classifier_bank_carries_its_own_id(synthetic_record: IAFDBRecord, tmp_path: Path) -> None:
    """The ClassifierBank gets a root ``id`` of its own (CL-145).

    Before this it had none, and egm-studio refused to index the file —
    correctly, since inventing an id for someone else's artifact would
    manufacture provenance. Asserted on the loaded bank rather than on the
    return value, because the defect was that nothing reached the file."""
    out = tmp_path / "bank.h5"
    result = export_bank(
        out,
        records=[synthetic_record],
        threshold=NoThreshold(),
        calibration_method="none",
        progress=False,
        output_format="classifier",
        label_fn=_unlabeled,
    )
    assert result.classifier_path is not None
    cb = load_classifier_bank(result.classifier_path)
    assert cb.id == derive_classifier_bank_id(has_labels=False)
    assert str(cb.id).startswith("ptbank_")


def test_classifier_bank_role_follows_labels_not_the_source_bank(
    synthetic_record: IAFDBRecord, tmp_path: Path
) -> None:
    """A labeled ClassifierBank is a ``tbank_``, whatever the source is.

    The pair can legitimately disagree, which is the part that looks like a
    bug: here the source is unthresholded (``ptbank_``) while the derived
    bank carries labels (``tbank_``). The prefixes answer different
    questions — the source's is about selection, the classifier bank's is
    about what a consumer may do with it."""
    out = tmp_path / "bank.h5"
    result = export_bank(
        out,
        records=[synthetic_record],
        threshold=NoThreshold(),
        calibration_method="none",
        progress=False,
        output_format="classifier",
        label_fn=_all_healthy,
    )
    assert result.classifier_path is not None
    cb = load_classifier_bank(result.classifier_path)
    assert str(result.bank_id).startswith("ptbank_")
    assert str(cb.id).startswith("tbank_")


def test_classifier_bank_id_is_not_the_source_id(
    synthetic_record: IAFDBRecord, tmp_path: Path
) -> None:
    """The inverse disagreement, which is the case CL-145 actually reported.

    A thresholded source (``tbank_``) with no labels yields a ``ptbank_``
    classifier bank. Reusing the source id here would produce exactly the
    over-claiming ``tbank_``-with-no-labels that egm-data's id-content check
    exists to refuse."""
    out = tmp_path / "bank.h5"
    result = export_bank(
        out,
        records=[synthetic_record],
        threshold=AbsoluteThreshold(0.1),
        calibration_method="r_wave_anchoring",
        target_qrs_pp_mv=TARGET_QRS_PP_MV,
        progress=False,
        output_format="classifier",
        label_fn=_unlabeled,
    )
    assert result.classifier_path is not None
    cb = load_classifier_bank(result.classifier_path)
    assert str(result.bank_id).startswith("tbank_")
    assert str(cb.id).startswith("ptbank_")
    assert cb.id != result.bank_id


@pytest.mark.parametrize("label_fn", [_unlabeled, _all_healthy])
def test_classifier_bank_id_agrees_with_its_content(
    synthetic_record: IAFDBRecord, tmp_path: Path, label_fn: object
) -> None:
    """egm-data's own id-content check passes on what we stamp.

    The derivation and the check live in different repos and could drift
    apart; running the contract's checker over the produced artifact is what
    makes them one rule rather than two that happen to agree. Both label
    paths, because the check is enforced in both directions."""
    out = tmp_path / "bank.h5"
    result = export_bank(
        out,
        records=[synthetic_record],
        threshold=AbsoluteThreshold(0.1),
        calibration_method="r_wave_anchoring",
        target_qrs_pp_mv=TARGET_QRS_PP_MV,
        progress=False,
        output_format="classifier",
        label_fn=label_fn,  # type: ignore[arg-type]
    )
    assert result.classifier_path is not None
    check_classifier_bank_id_matches_content(load_classifier_bank(result.classifier_path))


# ---------------------------------------------------------------------------
# noise bank id (carried on the run-record sidecar)
# ---------------------------------------------------------------------------


def test_noise_auto_derives_nbank_id(synthetic_record: IAFDBRecord, tmp_path: Path) -> None:
    """A noise export with no explicit id gets nbank_iafdb_<date>, recorded
    on the run-record sidecar (the slim noise_bank HDF5 carries no id)."""
    bank_path = tmp_path / "noise.h5"
    result = export_noise_bank(
        bank_path,
        records=[synthetic_record],
        strategy=PercentileQuietThreshold(50.0),
        progress=False,
    )
    expected = derive_noise_bank_id()
    assert result.bank_id == expected
    assert expected.startswith("nbank_iafdb_")
    assert load_noise_bank_run_record(result.run_record_path).bank_id == expected


def test_noise_explicit_id_is_used(synthetic_record: IAFDBRecord, tmp_path: Path) -> None:
    bank_path = tmp_path / "noise.h5"
    explicit = "nbank_iafdb_quiet_p20_2026-06-27"
    result = export_noise_bank(
        bank_path,
        records=[synthetic_record],
        strategy=PercentileQuietThreshold(50.0),
        progress=False,
        bank_id=explicit,
    )
    assert result.bank_id == explicit
    assert load_noise_bank_run_record(result.run_record_path).bank_id == explicit


def test_noise_rejects_malformed_id(synthetic_record: IAFDBRecord, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="valid stable artifact id"):
        export_noise_bank(
            tmp_path / "noise.h5",
            records=[synthetic_record],
            strategy=PercentileQuietThreshold(50.0),
            progress=False,
            bank_id="bad id",
        )
