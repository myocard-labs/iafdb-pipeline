"""Round-trip tests for the v0.2.0 producer.

Three flavours of iafdb_bank export plus the noise_bank pair, all
validated by:

1. The contracts' file-level validator accepting what we wrote.
2. egm-data's reader loading the file back into the typed Pydantic model.

Schema versions and writer machinery live in myocard-egm-data + contracts
so this suite tests integration — the producer + writer + validator
agreeing on the same bytes.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from myocard_egm_contracts.schema_info import current_version
from myocard_egm_contracts.validators import (
    validate_iafdb_bank,
    validate_noise_bank,
    validate_noise_bank_run_record,
)
from myocard_egm_data.banks import load_classifier_bank, read_iafdb_bank_hdf5, read_noise_bank_hdf5
from myocard_egm_data.records import load_noise_bank_run_record
from myocard_egm_signal import (
    AbsoluteQuietThreshold,
    AbsoluteThreshold,
    NoThreshold,
    PercentileQuietThreshold,
    PercentileThreshold,
)

from myocard_iafdb_pipeline.export import export_bank, export_noise_bank
from myocard_iafdb_pipeline.records import IAFDBRecord

# ---------------------------------------------------------------------------
# iafdb_bank — all three threshold strategies
# ---------------------------------------------------------------------------


def test_export_bank_absolute_threshold_round_trips(
    synthetic_record: IAFDBRecord, tmp_path: Path
) -> None:
    """Smallest end-to-end: one synthetic record, absolute mV threshold.
    Producer writes -> contracts validator passes -> egm-data reader
    yields a Pydantic IafdbBank with the writer's stamped fields."""
    out = tmp_path / "bank.h5"
    result = export_bank(
        out,
        records=[synthetic_record],
        threshold=AbsoluteThreshold(0.1),
        progress=False,
    )
    assert result.output_path == out
    assert result.classifier_path is None
    assert result.n_segments > 0

    assert validate_iafdb_bank(out).ok

    bank = read_iafdb_bank_hdf5(out)
    assert bank.threshold_mode.value == "absolute"
    assert bank.threshold_value == 0.1
    # source_records is what contributed at least one segment; with one
    # input record that passed the threshold it's a 1-tuple.
    assert len(bank.source_records) == 1


def test_export_bank_percentile_threshold_round_trips(
    synthetic_record: IAFDBRecord, tmp_path: Path
) -> None:
    """The percentile path stamps mode='percentile' and a 0..100 value.
    Catches a regression where the percentile-value provenance is lost
    in the producer-to-writer hand-off."""
    out = tmp_path / "bank.h5"
    export_bank(
        out,
        records=[synthetic_record],
        threshold=PercentileThreshold(70.0),
        progress=False,
    )
    assert validate_iafdb_bank(out).ok

    bank = read_iafdb_bank_hdf5(out)
    assert bank.threshold_mode.value == "percentile"
    assert bank.threshold_value == 70.0


def test_export_bank_no_filter_writes_threshold_none(
    synthetic_record: IAFDBRecord, tmp_path: Path
) -> None:
    """v0.2.0 added NoThreshold for unfiltered pretraining banks. The
    producer should stamp threshold_mode='none' + threshold_value
    write through the writer as NaN (HDF5 has no native null; the
    schema validator accepts NaN as a number). Confirms the contract
    between NoThreshold -> iafdb_bank v1.1 'none' enum is wired."""
    out = tmp_path / "bank.h5"
    export_bank(
        out,
        records=[synthetic_record],
        threshold=NoThreshold(),
        progress=False,
    )
    assert validate_iafdb_bank(out).ok

    bank = read_iafdb_bank_hdf5(out)
    assert bank.threshold_mode.value == "none"
    # threshold_value is nullable per schema 1.1; the writer stamps NaN
    # to round-trip through HDF5. Pydantic reads it back as a float
    # (NaN) since the writer wrote NaN, not the literal Python None.
    val = bank.threshold_value
    assert val is None or (isinstance(val, float) and np.isnan(val))


def test_export_bank_classifier_format_writes_both(
    synthetic_record: IAFDBRecord, tmp_path: Path
) -> None:
    """When output_format='classifier', the producer also writes a
    sibling ClassifierBank.h5 via egm-data's converter. The supplied
    label_fn is the consumer's policy hook; here we label everything
    healthy. Confirms the integration with egm-data's converter."""
    out = tmp_path / "bank.h5"

    def label_fn(bank: object) -> tuple[np.ndarray, dict[int, str]]:
        signal = bank.traces.signal  # type: ignore[attr-defined]
        return np.zeros(len(signal), dtype=np.int64), {0: "healthy"}

    result = export_bank(
        out,
        records=[synthetic_record],
        threshold=AbsoluteThreshold(0.1),
        progress=False,
        output_format="classifier",
        label_fn=label_fn,
    )
    assert result.classifier_path is not None
    assert result.classifier_path.exists()

    # The classifier bank is consumable by egm-data's loader and labels
    # propagate through from label_fn.
    cb = load_classifier_bank(result.classifier_path)
    assert cb.labels == {0: "healthy"}
    assert all(t.label_truth == 0 for t in cb.traces)


def test_export_bank_classifier_format_requires_label_fn(
    synthetic_record: IAFDBRecord, tmp_path: Path
) -> None:
    """Producer shouldn't quietly default a label policy; output_format=
    'classifier' requires the caller to supply a label_fn. Labeling is
    consumer-side policy and the producer doesn't get to pick."""
    with pytest.raises(ValueError, match="requires label_fn"):
        export_bank(
            tmp_path / "bank.h5",
            records=[synthetic_record],
            threshold=AbsoluteThreshold(0.1),
            progress=False,
            output_format="classifier",
        )


def test_export_bank_overwrite_guard(synthetic_record: IAFDBRecord, tmp_path: Path) -> None:
    """Refuses to clobber an existing bank unless overwrite=True. Prevents
    producers from silently destroying a previous extraction they meant
    to keep."""
    out = tmp_path / "bank.h5"
    export_bank(out, records=[synthetic_record], threshold=AbsoluteThreshold(0.1), progress=False)
    with pytest.raises(FileExistsError):
        export_bank(
            out, records=[synthetic_record], threshold=AbsoluteThreshold(0.1), progress=False
        )
    # overwrite=True succeeds.
    export_bank(
        out,
        records=[synthetic_record],
        threshold=AbsoluteThreshold(0.1),
        progress=False,
        overwrite=True,
    )


# ---------------------------------------------------------------------------
# noise_bank — bank + sibling run record
# ---------------------------------------------------------------------------


def test_export_noise_bank_percentile_round_trips(
    synthetic_record: IAFDBRecord, tmp_path: Path
) -> None:
    """The noise producer writes the slim noise_bank.h5 and the JSON
    sidecar that captures extraction provenance. The bank passes the
    contracts' validator; the JSON passes its sibling validator.
    Confirms both halves of the v0.2.0 pair are wired correctly."""
    bank_path = tmp_path / "noise.h5"
    result = export_noise_bank(
        bank_path,
        records=[synthetic_record],
        strategy=PercentileQuietThreshold(50.0),
        progress=False,
    )
    assert result.bank_path == bank_path
    # Convention: sibling JSON with matching stem.
    assert result.run_record_path == tmp_path / "noise_run_record.json"

    assert validate_noise_bank(bank_path).ok
    assert validate_noise_bank_run_record(result.run_record_path).ok

    bank = read_noise_bank_hdf5(bank_path)
    assert bank.source == "iafdb v1.0.0"
    assert len(bank.traces.signal) == result.n_segments


def test_export_noise_bank_absolute_records_provenance(
    synthetic_record: IAFDBRecord, tmp_path: Path
) -> None:
    """The run-record sidecar captures calibration / threshold / windowing
    provenance with per-trace arrays aligned to the bank. Confirms the
    producer-side build of the run record matches what the validator
    accepts and what auditing tools downstream will read."""
    bank_path = tmp_path / "noise.h5"
    result = export_noise_bank(
        bank_path,
        records=[synthetic_record],
        strategy=AbsoluteQuietThreshold(0.5),
        description="round-trip provenance test",
        progress=False,
    )

    record = load_noise_bank_run_record(result.run_record_path)
    # As of egm-data v0.3.0, load_noise_bank_run_record returns a typed
    # NoiseBankRunRecord Pydantic model, so the assertions use attribute
    # access (the SchemaVersion + ThresholdMode enums expose .value).
    # The schema bumped to 1.1 in the linkage wave (+bank_id); assert
    # against current_version so a future bump doesn't silently rot this.
    assert record.schema_version.value == current_version("noise_bank_run_record")
    # Producer extracts from raw signal — calibration is honestly 'none'.
    assert record.calibration.method == "none"
    assert record.calibration.target_qrs_pp_mv is None
    assert record.selection.threshold_mode.value == "absolute"
    assert record.selection.threshold_value == 0.5
    # When the bank is non-empty the producer writes per_trace_provenance
    # — every paper-citable bank should have it.
    if result.n_segments > 0:
        assert record.per_trace_provenance is not None
        assert len(record.per_trace_provenance.patient_id) == result.n_segments


def test_export_noise_bank_overwrite_guard(synthetic_record: IAFDBRecord, tmp_path: Path) -> None:
    """Same overwrite guard as the iafdb bank — bank + sidecar are paired
    in one run, so destroying one without the other would leave an
    inconsistent on-disk state."""
    bank_path = tmp_path / "noise.h5"
    export_noise_bank(
        bank_path,
        records=[synthetic_record],
        strategy=PercentileQuietThreshold(50.0),
        progress=False,
    )
    with pytest.raises(FileExistsError):
        export_noise_bank(
            bank_path,
            records=[synthetic_record],
            strategy=PercentileQuietThreshold(50.0),
            progress=False,
        )
    export_noise_bank(
        bank_path,
        records=[synthetic_record],
        strategy=PercentileQuietThreshold(50.0),
        progress=False,
        overwrite=True,
    )
