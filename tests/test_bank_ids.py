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
from myocard_egm_data.banks import load_classifier_bank, read_iafdb_bank_hdf5
from myocard_egm_data.records import load_noise_bank_run_record
from myocard_egm_signal import AbsoluteThreshold, NoThreshold, PercentileQuietThreshold

from myocard_iafdb_pipeline.export import export_bank, export_noise_bank
from myocard_iafdb_pipeline.ids import derive_iafdb_bank_id, derive_noise_bank_id
from myocard_iafdb_pipeline.records import IAFDBRecord

# ---------------------------------------------------------------------------
# iafdb_bank id
# ---------------------------------------------------------------------------


def test_bank_auto_derives_tbank_id(synthetic_record: IAFDBRecord, tmp_path: Path) -> None:
    """A thresholded export with no explicit id gets tbank_iafdb_<date>,
    and the id round-trips onto the HDF5 root attr the reader surfaces."""
    out = tmp_path / "bank.h5"
    result = export_bank(
        out, records=[synthetic_record], threshold=AbsoluteThreshold(0.1), progress=False
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
    result = export_bank(out, records=[synthetic_record], threshold=NoThreshold(), progress=False)
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
        progress=False,
        output_format="classifier",
        label_fn=label_fn,
    )
    assert result.classifier_path is not None
    cb = load_classifier_bank(result.classifier_path)
    assert cb.banks[0].bank_id == result.bank_id
    assert all(t.bank_id == result.bank_id for t in cb.traces)


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
