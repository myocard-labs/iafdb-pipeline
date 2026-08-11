"""Tests for the ``--report`` audit sidecar (B11b).

What is being protected here is *recoverability*: every field in the report
exists because it cannot be reconstructed from the bank afterwards. So the
assertions are mostly of the form "the report says something the bank does
not", and the most important ones cover the two cases where the report is
the only artifact there is — a record that contributed nothing, and a run
that produced no bank at all.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from myocard_egm_data.banks import read_iafdb_bank_hdf5
from myocard_egm_signal import NoThreshold

from myocard_iafdb_pipeline.exceptions import EmptyBankWarning
from myocard_iafdb_pipeline.export import export_bank
from myocard_iafdb_pipeline.export.report import REPORT_VERSION, relative_to_bank

# Reaching into the sibling test module for its activation fixtures rather
# than duplicating them: the pulse shape and the surface-lead/QRS scaffolding
# are subtle (see their docstrings — an impulse rings, and calibration fails
# without a surface lead), and a second divergent copy would be a liability.
from test_activation_extract import _config, _record_with_activations, _unwrap

from conftest import build_synthetic_record  # isort: skip


def _read(path: Path) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return parsed


def _export_activation(
    tmp_path: Path, *, report: bool = True, **kwargs: Any
) -> tuple[Any, Path, Path]:
    bank = tmp_path / "bank.h5"
    report_path = tmp_path / "bank_report.json"
    records = [
        _record_with_activations([800, 1600, 2400], name="iaf1_afw"),
        _record_with_activations([900, 1800, 2700], name="iaf2_afw"),
    ]
    result = export_bank(
        output_path=bank,
        records=iter(records),
        threshold=NoThreshold(),
        activation=kwargs.pop("config", _config()),
        calibration_method="r_wave_anchoring",
        target_qrs_pp_mv=1.0,
        band_hz=(30.0, 300.0),
        progress=False,
        report_path=report_path if report else None,
        **kwargs,
    )
    return result, bank, report_path


def test_report_is_written_only_when_asked(tmp_path: Path) -> None:
    """No ``--report``, no file, and no pointer on the bank.

    The sidecar is opt-in, and the schema treats an absent
    ``run_record_path`` as "no run record" — so the default path must not
    leave a pointer behind."""
    _, bank, report_path = _export_activation(tmp_path, report=False)

    assert not report_path.exists()
    loaded = read_iafdb_bank_hdf5(bank)
    assert getattr(loaded, "run_record_path", None) is None


def test_bank_points_at_the_report_relatively(tmp_path: Path) -> None:
    """The bank's pointer resolves against the bank's own directory.

    A relative pointer is what makes the pair survive being moved together,
    which is what phase storage does to them. Resolved from the bank's
    directory it must land on the file that was actually written."""
    _, bank, report_path = _export_activation(tmp_path)

    loaded = read_iafdb_bank_hdf5(bank)
    pointer = getattr(loaded, "run_record_path", None)
    assert pointer == "bank_report.json"
    assert (bank.parent / str(pointer)).resolve() == report_path.resolve()


def test_report_records_calibration_provenance_the_bank_discards(tmp_path: Path) -> None:
    """Which lead calibrated each record, and what it measured.

    The bank keeps only the applied scalar. Everything the scalar was
    derived from — the lead chosen by the priority walk, the measured QRS
    peak-to-peak, the beat count behind it — is consumed and dropped. That
    is the report's core job, so it is asserted field by field."""
    _, _, report_path = _export_activation(tmp_path)
    doc = _read(report_path)

    assert doc["report_version"] == REPORT_VERSION
    assert [r["record_name"] for r in doc["records"]] == ["iaf1_afw", "iaf2_afw"]
    for rec in doc["records"]:
        cal = rec["calibration"]
        assert cal["lead"] == "II"
        assert cal["measured_qrs_pp_mv"] > 0.0
        assert cal["n_beats"] >= 1
        assert cal["scalar"] > 0.0
        # The lead choice is only interpretable next to what was available.
        assert rec["surface_leads"] == ["II"]


def test_report_carries_the_settings_that_produced_the_run(tmp_path: Path) -> None:
    """Enough of the config to re-run it, in activation mode.

    Not a copy of the YAML — the point is the *effective* values, after
    defaulting and validation, which is what the config file cannot tell
    you."""
    _, _, report_path = _export_activation(tmp_path)
    run = _read(report_path)["run"]

    assert run["calibration_target_qrs_pp_mv"] == 1.0
    assert run["band_hz"] == [30.0, 300.0]
    assert run["trace_duration_ms"] == 192.0
    act = run["activation"]
    assert act["detection_curve"] == "rectified_derivative"
    assert act["threshold_rule"] == "median_mad"
    assert act["threshold_lam"] == 10.0
    assert act["position_low"] == 0.5
    assert act["position_high"] == 0.5
    assert act["keep_multi_activation"] is False
    # Sliding-only keys must not appear: they would read as settings that
    # applied to this run, and neither did.
    assert "window_ms" not in run
    assert "hop_ms" not in run


def test_report_splits_drops_by_reason_per_channel(tmp_path: Path) -> None:
    """The per-channel kept/dropped breakdown, with reasons kept apart.

    Boundary drops and multi-activation drops have different remedies, and
    the bank records neither — a dropped window leaves nothing behind. The
    totals must partition the detections."""
    _, _, report_path = _export_activation(tmp_path)
    doc = _read(report_path)

    for rec in doc["records"]:
        assert rec["channels"], "activation mode must report per-channel tallies"
        for ch in rec["channels"]:
            assert ch["activations"] == (
                ch["kept"] + ch["dropped_boundary"] + ch["dropped_multi_activation"]
            )

    totals = doc["totals"]
    assert totals["records_processed"] == 2
    assert totals["records_contributing"] == 2
    assert totals["segments_kept"] == sum(r["segments_kept"] for r in doc["records"])
    assert totals["activations"] > 0


def test_sliding_mode_reports_its_own_settings_and_no_tallies(tmp_path: Path) -> None:
    """The sliding path reports window/hop, and omits the drop columns.

    Sliding windowing has no per-window drop reasons to report — an empty
    ``channels`` list would imply it found nothing, so the key is absent
    instead."""
    bank = tmp_path / "sliding.h5"
    report_path = tmp_path / "sliding_report.json"
    export_bank(
        output_path=bank,
        records=iter([build_synthetic_record(name="iaf1_afw")]),
        threshold=NoThreshold(),
        window_ms=512.0,
        hop_ms=256.0,
        calibration_method="r_wave_anchoring",
        target_qrs_pp_mv=1.0,
        progress=False,
        report_path=report_path,
    )
    doc = _read(report_path)

    assert doc["windowing_mode"] == "sliding"
    assert doc["run"]["window_ms"] == 512.0
    assert doc["run"]["hop_ms"] == 256.0
    assert "activation" not in doc["run"]
    assert "channels" not in doc["records"][0]
    assert "activations" not in doc["totals"]
    # The pointer is stamped on the sliding path too — the report is a
    # property of the run, not of the windowing mode.
    assert getattr(read_iafdb_bank_hdf5(bank), "run_record_path", None) == "sliding_report.json"


def test_record_that_contributed_nothing_still_appears(tmp_path: Path) -> None:
    """A record absent from the bank is invisible; the report keeps it.

    This is the asymmetry the report exists to fix. A record whose windows
    were all dropped leaves no trace in the bank — not even its name in
    ``source_records`` — so "did it fail, or was it never processed?" is
    unanswerable without this."""
    bank = tmp_path / "bank.h5"
    report_path = tmp_path / "report.json"
    good = _record_with_activations([800, 1600, 2400], name="iaf1_afw")
    # Activations packed closer than one trace length: every window catches a
    # neighbour, so all of them drop as multi-activation.
    barren = _record_with_activations([800, 860, 920, 980], name="iaf2_afw")

    export_bank(
        output_path=bank,
        records=iter([good, barren]),
        threshold=NoThreshold(),
        activation=_config(refractory_ms=30.0),
        calibration_method="r_wave_anchoring",
        target_qrs_pp_mv=1.0,
        band_hz=(30.0, 300.0),
        progress=False,
        report_path=report_path,
    )
    doc = _read(report_path)

    names = [r["record_name"] for r in doc["records"]]
    assert names == ["iaf1_afw", "iaf2_afw"]
    barren_entry = next(r for r in doc["records"] if r["record_name"] == "iaf2_afw")
    assert barren_entry["segments_kept"] == 0
    # And it says *why*: detections happened, they were all multi-activation.
    assert sum(c["activations"] for c in barren_entry["channels"]) > 0
    assert sum(c["dropped_multi_activation"] for c in barren_entry["channels"]) > 0
    assert doc["totals"]["records_processed"] == 2
    assert doc["totals"]["records_contributing"] == 1
    # The bank cannot answer the same question.
    assert "iaf2_afw" not in [str(r) for r in read_iafdb_bank_hdf5(bank).source_records]


def test_report_survives_a_run_that_writes_no_bank(tmp_path: Path) -> None:
    """The empty run is the one the report matters most for.

    When nothing survives there is no bank to attach diagnostics to, and the
    operator's only question is which settings starved it. So the report is
    written anyway, flagged so nobody reads ``bank_file`` as a promise."""
    bank = tmp_path / "empty.h5"
    report_path = tmp_path / "empty_report.json"
    barren = _record_with_activations([800, 860, 920, 980], name="iaf1_afw")

    with pytest.warns(EmptyBankWarning):
        result = export_bank(
            output_path=bank,
            records=iter([barren]),
            threshold=NoThreshold(),
            activation=_config(refractory_ms=30.0),
            calibration_method="r_wave_anchoring",
            target_qrs_pp_mv=1.0,
            band_hz=(30.0, 300.0),
            progress=False,
            report_path=report_path,
        )

    assert not result.written
    assert not bank.exists()
    assert result.report_path == report_path
    doc = _read(report_path)
    assert doc["bank_written"] is False
    assert doc["totals"]["segments_kept"] == 0
    assert doc["totals"]["records_processed"] == 1
    assert sum(c["dropped_multi_activation"] for c in doc["records"][0]["channels"]) > 0


def test_report_will_not_clobber_without_overwrite(tmp_path: Path) -> None:
    """Same protection the bank gets, for the same reason.

    A report is the audit trail of a run; silently replacing it loses the
    only copy of settings that may no longer be reproducible."""
    _, _, report_path = _export_activation(tmp_path)
    assert report_path.exists()

    with pytest.raises(FileExistsError):
        _export_activation(tmp_path, overwrite=False)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _export_activation(tmp_path, overwrite=True)
    assert _read(report_path)["bank_written"] is True


def test_bank_file_is_a_name_not_a_path(tmp_path: Path) -> None:
    """Recorded as a bare filename, so the pair stays valid when moved."""
    _, bank, report_path = _export_activation(tmp_path)
    assert _read(report_path)["bank_file"] == bank.name


def test_pointer_falls_back_to_a_bare_name_across_roots() -> None:
    """A report outside the bank's tree gets a filename, never an abspath.

    An absolute path would encode *this machine* into a published artifact,
    which is worse than a relative path that needs a human to resolve."""
    assert relative_to_bank(Path("/tmp/elsewhere/r.json"), Path("/var/banks/b.h5")) == "r.json"
    assert relative_to_bank(Path("/var/banks/r.json"), Path("/var/banks/b.h5")) == "r.json"


# ---------------------------------------------------------------------------
# calibration.method: none — the uncalibrated path (CL-152/CL-153/CL-154)
# ---------------------------------------------------------------------------


def _export_uncalibrated(tmp_path: Path) -> tuple[Any, Path, Path]:
    bank = tmp_path / "raw.h5"
    report_path = tmp_path / "raw_report.json"
    result = export_bank(
        output_path=bank,
        records=iter([_record_with_activations([800, 1600, 2400], name="iaf1_afw")]),
        threshold=NoThreshold(),
        activation=_config(),
        calibration_method="none",
        band_hz=(30.0, 300.0),
        progress=False,
        report_path=report_path,
    )
    return result, bank, report_path


def test_uncalibrated_bank_records_the_method_it_actually_used(tmp_path: Path) -> None:
    """The bank says `none`, and says it at schema 1.4.

    This is the whole point of the enum widening: before it, an
    uncalibrated bank had to claim `r_wave_anchoring` — a signal-processing
    step that never ran — because that was the only legal value."""
    _, bank, _ = _export_uncalibrated(tmp_path)

    loaded = read_iafdb_bank_hdf5(bank)
    # These two are codegen'd Enums, not the RootModel wrappers `_unwrap`
    # handles — `.value` is the string the file actually carries.
    assert loaded.calibration_method.value == "none"
    assert loaded.schema_version.value == "1.4"


def test_uncalibrated_bank_uses_the_target_sentinel(tmp_path: Path) -> None:
    """No target applies, so the required field carries `+inf`.

    The schema requires `calibration_target_qrs_pp_mv` with
    `exclusiveMinimum: 0` and an uncalibrated run has no target. `+inf`
    reads as "not applicable" — no real target is infinite — matching the
    `peak_to_peak_mv` and `hop_ms` sentinels. The line being held: sentinel
    a field that would merely be *unused*, fix one that would be *untrue*
    (which is why `calibration_method` got a widened enum instead)."""
    _, bank, _ = _export_uncalibrated(tmp_path)

    target = _unwrap(read_iafdb_bank_hdf5(bank).calibration_target_qrs_pp_mv)
    assert target == float("inf")


def test_uncalibrated_traces_are_genuinely_unscaled(tmp_path: Path) -> None:
    """Unity scalars, and a signal that differs from the anchored one.

    Asserting the scalar column alone would pass even if the pipeline had
    quietly applied a scalar anyway, so this compares the emitted samples
    against the same records exported with anchoring. On this fixture the
    anchoring scalar is not 1.0, so the two must differ — that difference
    is the proof the step was actually skipped rather than merely
    reported as skipped."""
    _, raw_bank, _ = _export_uncalibrated(tmp_path)
    raw = read_iafdb_bank_hdf5(raw_bank)
    assert all(float(_unwrap(s)) == 1.0 for s in raw.traces.calibration_scalar)

    anchored_path = tmp_path / "anchored.h5"
    export_bank(
        output_path=anchored_path,
        records=iter([_record_with_activations([800, 1600, 2400], name="iaf1_afw")]),
        threshold=NoThreshold(),
        activation=_config(),
        calibration_method="r_wave_anchoring",
        target_qrs_pp_mv=1.0,
        band_hz=(30.0, 300.0),
        progress=False,
    )
    anchored = read_iafdb_bank_hdf5(anchored_path)

    scalar = float(_unwrap(anchored.traces.calibration_scalar[0]))
    assert scalar != 1.0, "fixture must anchor to something other than unity"

    raw_first = np.asarray(raw.traces.signal[0], dtype=float)
    anch_first = np.asarray(anchored.traces.signal[0], dtype=float)
    assert not np.allclose(raw_first, anch_first)
    # And the relationship is exactly the scalar — the only difference.
    assert np.allclose(anch_first, raw_first * scalar, rtol=1e-5, atol=1e-8)


def test_uncalibrated_report_nulls_calibration_rather_than_faking_unity(
    tmp_path: Path,
) -> None:
    """The sidecar reports absence, not a plausible-looking 1.0.

    A scalar of 1.0 in the report would be indistinguishable from an
    anchoring run that measured 1.0. Null is the only honest encoding, and
    the report exists precisely to say what happened."""
    _, _, report_path = _export_uncalibrated(tmp_path)
    doc = _read(report_path)

    assert doc["run"]["calibration_method"] == "none"
    # `null`, not the bank's +inf sentinel: `Infinity` is not valid JSON.
    assert doc["run"]["calibration_target_qrs_pp_mv"] is None
    cal = doc["records"][0]["calibration"]
    assert cal["scalar"] is None
    assert cal["lead"] is None
    assert cal["measured_qrs_pp_mv"] is None
    assert cal["n_beats"] is None
    # The yield accounting is still there — it is just as useful uncalibrated.
    assert doc["records"][0]["segments_kept"] > 0


def test_uncalibrated_report_is_strict_json(tmp_path: Path) -> None:
    """No `Infinity` anywhere in the sidecar.

    Python's json module emits bare `Infinity` for float('inf'), which is
    not valid JSON and is rejected by strict parsers — so leaking the
    bank's sentinel into the report would make it unreadable to exactly the
    tooling most likely to consume it."""
    _, _, report_path = _export_uncalibrated(tmp_path)
    text = report_path.read_text(encoding="utf-8")

    assert "Infinity" not in text and "NaN" not in text
    json.loads(text, parse_constant=_reject_constant)


def _reject_constant(name: str) -> Any:
    raise AssertionError(f"non-standard JSON constant in the report: {name}")


def test_records_without_a_usable_surface_lead_still_export_uncalibrated(
    tmp_path: Path,
) -> None:
    """`none` skips the calibration step, it does not calibrate by 1.0.

    A record with no QRS annotations makes `compute_calibration` raise. On
    the uncalibrated path that call must never happen — otherwise `none`
    would fail on exactly the records it is most useful for."""
    record = _record_with_activations([800, 1600, 2400], name="iaf1_afw")
    stripped = replace(record, qrs_samples=np.empty(0, dtype=np.int64))

    result = export_bank(
        output_path=tmp_path / "nolead.h5",
        records=iter([stripped]),
        threshold=NoThreshold(),
        activation=_config(),
        calibration_method="none",
        band_hz=(30.0, 300.0),
        progress=False,
    )
    assert result.written and result.n_segments > 0
