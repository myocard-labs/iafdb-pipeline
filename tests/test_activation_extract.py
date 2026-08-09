"""Tests for activation-anchored extraction (IAF1 / S3).

These build records whose activation train is **known by construction** —
spikes placed at chosen samples — so each assertion pins a specific behavior
rather than just checking that something came out. The window geometry itself
is egm-signal's and tested there; what is tested here is this repo's response:
which windows are kept, how they are counted, and what is carried forward.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from myocard_egm_contracts import iafdb_bank as _iafdb_bank_models
from myocard_egm_signal.extraction.activation_based import UniformPositionGenerator
from pydantic import ValidationError

from myocard_iafdb_pipeline.cli._config import (
    ActivationConfig,
    DetectionConfig,
    PositionBandConfig,
)
from myocard_iafdb_pipeline.constants import BIPOLAR_CHANNELS
from myocard_iafdb_pipeline.export.activation_extract import (
    UNUSED_PEAK_TO_PEAK_MV,
    ActivationSegment,
    ChannelTally,
    build_position_generator,
    extract_activation_segments,
)
from myocard_iafdb_pipeline.records import IAFDBRecord

FS = 1000.0
WINDOW_MS = 192.0
WINDOW_SAMPLES = 192


def _config(
    *,
    low: float = 0.5,
    high: float = 0.5,
    refractory_ms: float = 50.0,
    curve: str = "rectified_derivative",
    seed: int | None = 0,
    keep_multi_activation: bool = False,
) -> ActivationConfig:
    """A detection config with the position band collapsed to a point.

    A fixed position makes the geometry assertions exact: with `low == high`
    every window sits at the same place, so a mis-anchored window is a failure
    rather than a draw from the band. Tests that care about the band widen it
    explicitly.

    `lam=10` rather than a lower multiplier: on a record that is mostly
    baseline the median/MAD threshold sits low, and a permissive multiplier
    lets ordinary noise peaks through as activations. Measured on this
    fixture, 5.0 finds 16 activations where 3 were planted and 10.0 finds
    exactly 3.
    """
    return ActivationConfig(
        trace_duration_ms=WINDOW_MS,
        detection=DetectionConfig(
            curve=curve,  # type: ignore[arg-type]
            botteron_band_hz=(40.0, 250.0) if curve == "botteron_envelope" else None,
            botteron_lowpass_hz=20.0 if curve == "botteron_envelope" else None,
            threshold_rule="median_mad",
            threshold_c=1.0,
            threshold_lam=10.0,
            threshold_q=None,
            min_prominence=None,
            refractory_ms=refractory_ms,
            refine_curve=None,
            refine_radius_ms=None,
        ),
        position=PositionBandConfig(low=low, high=high, seed=seed),
        keep_multi_activation=keep_multi_activation,
    )


def _activation_pulse(*, width_ms: float = 14.0, amplitude: float = 1.0) -> np.ndarray:
    """A smooth biphasic activation — the derivative of a Gaussian.

    Deliberately not a one-sample impulse. The detection curve is a
    derivative and the record is band-passed at 30-300 Hz, so an impulse
    rings: its side lobes clear an adaptive threshold and the detector
    reports activations that were never planted. A ~14 ms biphasic pulse is
    both what a real activation looks like and band-limited enough that the
    only peaks are the ones put there.
    """
    n = round(width_ms * 1e-3 * FS)
    n += (n + 1) % 2  # odd, so the pulse has a centre sample
    t = np.linspace(-3.0, 3.0, n)
    wave = -t * np.exp(-(t**2) / 2.0)
    scaled: np.ndarray = amplitude * wave / np.abs(wave).max()
    return scaled


def _record_with_activations(
    activation_samples: list[int],
    *,
    n_samples: int = 4000,
    name: str = "iaf1_afw",
    amplitude: float = 1.0,
    noise_sd: float = 0.01,
    dead_channels: tuple[str, ...] = (),
) -> IAFDBRecord:
    """A record whose bipolar channels carry activations at known samples.

    Baseline noise is present and non-zero on purpose: the median/MAD
    threshold is computed from the signal itself, so a perfectly clean
    channel has no scale to measure against.
    """
    channels = list(BIPOLAR_CHANNELS)
    rng = np.random.default_rng(0)
    signal = rng.normal(0.0, noise_sd, (n_samples, len(channels)))
    pulse = _activation_pulse(amplitude=amplitude)
    half = len(pulse) // 2

    for ci, ch in enumerate(channels):
        if ch in dead_channels:
            signal[:, ci] = 0.0  # perfectly constant -> DegenerateSignalError
            continue
        for t in activation_samples:
            signal[t - half : t + half + 1, ci] += pulse

    return IAFDBRecord(
        name=name,
        patient=name.split("_", 1)[0],
        placement=name.split("_", 1)[1],
        fs=FS,
        signal=signal,
        channel_names=tuple(channels),
        units=tuple("mV" for _ in channels),
        comments=("synthetic activation record",),
        qrs_samples=np.array([], dtype=np.int64),
    )


def _extract(
    record: IAFDBRecord, config: ActivationConfig
) -> tuple[list[ActivationSegment], list[ChannelTally]]:
    return extract_activation_segments(
        record,
        config,
        calibration_scalar=1.0,
        band_hz=(30.0, 300.0),
        position_generator=build_position_generator(config),
    )


def test_one_window_per_well_separated_activation() -> None:
    """The base case: activations far apart, none near an edge.

    Every activation should yield exactly one kept window on every channel,
    with nothing dropped."""
    activations = [800, 1600, 2400, 3200]
    record = _record_with_activations(activations)

    segments, tallies = _extract(record, _config())

    assert len(tallies) == len(BIPOLAR_CHANNELS)
    for tally in tallies:
        assert tally.detected == len(activations)
        assert tally.kept == len(activations)
        assert tally.dropped_boundary == 0
        assert tally.dropped_multi_activation == 0
        assert tally.candidates == tally.detected
    assert len(segments) == len(activations) * len(BIPOLAR_CHANNELS)
    assert all(seg.signal.shape == (WINDOW_SAMPLES,) for seg in segments)


def test_activation_lands_at_the_requested_position() -> None:
    """A window is anchored where it was asked to be.

    With the band collapsed to 0.5 the activation must sit mid-window, within
    the half-sample the fraction-to-index rounding allows. This is the
    property the whole mode exists to provide, so it is asserted on the
    emitted signal rather than trusted from the metadata."""
    activations = [800, 1600, 2400]
    record = _record_with_activations(activations)

    segments, _ = _extract(record, _config(low=0.5, high=0.5))

    for seg in segments:
        # Measure with the same derivative convention the detector uses,
        # g[i] = |x[i] - x[i-1]|. numpy's diff indexes as x[i+1] - x[i], which
        # is off by one against it — worth being exact about, because with the
        # conventions aligned the activation lands on precisely the sample
        # `realized_position` claims, not merely near it.
        peak = int(np.argmax(np.abs(np.diff(seg.signal, prepend=seg.signal[0]))))
        assert peak == round(seg.activation_position * (WINDOW_SAMPLES - 1))
        # And the realized position is the requested one to within the
        # half-sample the fraction-to-index rounding can cost.
        assert abs(seg.activation_position - 0.5) <= 0.5 / (WINDOW_SAMPLES - 1)


def test_activations_near_the_record_ends_are_dropped_as_boundary() -> None:
    """A window that would run off either end of the record is dropped.

    Not padded, not shifted inward: a shifted window would put the activation
    somewhere other than the position drawn for it, quietly biasing the very
    distribution this mode exists to control."""
    # 20 samples from the start and end: a centred 192-sample window cannot fit.
    activations = [20, 1600, 3980]
    record = _record_with_activations(activations, n_samples=4000)

    segments, tallies = _extract(record, _config(low=0.5, high=0.5))

    for tally in tallies:
        assert tally.detected == 3
        assert tally.dropped_boundary == 2
        assert tally.kept == 1
    assert len(segments) == len(BIPOLAR_CHANNELS)
    assert all(seg.signal is not None for seg in segments)


def test_neighbouring_activation_inside_a_window_is_dropped_as_multi() -> None:
    """Two activations closer together than the window are both dropped.

    Multi-activation windows are dropped rather than kept because the whole
    corpus contract is one-activation-per-trace; keeping them would put a
    different *kind* of trace into a bank the classifier reads as uniform.
    They are counted separately from boundary drops because the two have
    different remedies — a shorter T fixes this one."""
    # 60 samples apart: inside a 192-sample window, but outside the 50 ms
    # refractory so both are genuinely detected rather than suppressed.
    activations = [1000, 1060, 2500]
    record = _record_with_activations(activations)

    segments, tallies = _extract(record, _config(low=0.5, high=0.5))

    for tally in tallies:
        assert tally.detected == 3
        assert tally.dropped_multi_activation == 2
        assert tally.dropped_boundary == 0
        assert tally.kept == 1
    assert len(segments) == len(BIPOLAR_CHANNELS)


def test_dead_channel_is_counted_and_does_not_abort_the_record() -> None:
    """A flat channel raises inside egm-signal; the sweep must survive it.

    egm-signal deliberately raises rather than returning an empty result, so
    that a caller has to decide. This producer's decision is: count it, log
    it, keep going — one dead channel is a recording fault, not a reason to
    lose the other four."""
    activations = [800, 1600, 2400]
    record = _record_with_activations(activations, dead_channels=("CS12", "CS90"))

    segments, tallies = _extract(record, _config())

    degenerate = [t for t in tallies if t.degenerate]
    healthy = [t for t in tallies if not t.degenerate]
    assert {t.channel for t in degenerate} == {"CS12", "CS90"}
    assert all(t.detected == 0 and t.kept == 0 for t in degenerate)
    assert len(healthy) == len(BIPOLAR_CHANNELS) - 2
    assert all(t.kept == len(activations) for t in healthy)
    assert {seg.source_channel for seg in segments} == set(BIPOLAR_CHANNELS) - {"CS12", "CS90"}


def test_drop_counts_partition_the_candidates() -> None:
    """kept + boundary + multi == one candidate per detected activation.

    The tallies feed §8.1's yield analysis and the paper's methods section, so
    an accounting leak would misstate how much real data survived."""
    record = _record_with_activations([20, 1000, 1060, 2500, 3980])

    _, tallies = _extract(record, _config(low=0.5, high=0.5))

    for tally in tallies:
        assert tally.candidates == tally.detected


def test_positions_vary_across_the_band_and_are_recorded() -> None:
    """With a real band the positions spread, and each window records its own.

    A single shared position would defeat the purpose — the point of varying
    the anchor is that the classifier cannot key on it."""
    record = _record_with_activations([600, 1200, 1800, 2400, 3000])

    segments, _ = _extract(record, _config(low=0.3, high=0.7, seed=11))

    positions = np.array([seg.activation_position for seg in segments])
    assert positions.min() >= 0.3 - 1e-9
    assert positions.max() <= 0.7 + 1e-9
    assert len(np.unique(positions)) > 1


def test_one_generator_spans_records_so_positions_are_not_repeated() -> None:
    """The position stream is corpus-wide, not per record.

    Rebuilding the generator per record would restart the same seeded stream
    for every record, making the corpus position distribution an artifact of
    how many records there are rather than of the band."""
    config = _config(low=0.2, high=0.8, seed=5)
    generator = UniformPositionGenerator(0.2, 0.8, seed=5)

    per_record_positions = []
    for name in ("iaf1_afw", "iaf2_afw"):
        record = _record_with_activations([800, 1600, 2400], name=name)
        segments, _ = extract_activation_segments(
            record,
            config,
            calibration_scalar=1.0,
            band_hz=(30.0, 300.0),
            position_generator=generator,
        )
        per_record_positions.append([s.activation_position for s in segments])

    assert per_record_positions[0] != per_record_positions[1]


def test_calibration_scalar_is_applied_and_recorded_per_trace() -> None:
    """The scalar is applied before filtering, and carried on every segment.

    Band-pass is linear, so a scalar factors straight through it — doubling
    the calibration doubles the emitted signal exactly. It is recorded per
    trace because `iafdb_bank`'s contract has a `calibration_scalar` column:
    a consumer has to be able to get back to raw amplitudes."""
    record = _record_with_activations([800, 1600, 2400])

    plain, _ = _extract(record, _config())
    scaled, _ = extract_activation_segments(
        record,
        _config(),
        calibration_scalar=2.0,
        band_hz=(30.0, 300.0),
        position_generator=build_position_generator(_config()),
    )

    assert len(plain) == len(scaled)
    assert all(s.calibration_scalar == 1.0 for s in plain)
    assert all(s.calibration_scalar == 2.0 for s in scaled)
    for a, b in zip(plain, scaled, strict=True):
        np.testing.assert_allclose(b.signal, 2.0 * a.signal, rtol=1e-9)


def test_multi_activation_windows_can_be_kept_on_purpose() -> None:
    """Opt in and multi-activation windows survive; out-of-bounds never do.

    This is a debugging affordance with teeth. Every IAFDB patient is
    arrhythmic, so a mistuned detector leaves a neighbour inside nearly every
    window — and with multi-activation windows dropped, that is
    indistinguishable from "found nothing": an empty bank either way. Keeping
    them turns a silent empty result into an inspectable one."""
    # 1000/1060 sit inside one window of each other; 20 runs off the front.
    record = _record_with_activations([20, 1000, 1060, 2500])

    dropped, drop_tallies = _extract(record, _config(low=0.5, high=0.5))
    kept, keep_tallies = _extract(record, _config(low=0.5, high=0.5, keep_multi_activation=True))

    for tally in drop_tallies:
        assert tally.dropped_multi_activation == 2
        assert tally.kept_multi_activation == 0
        assert tally.kept == 1

    for tally in keep_tallies:
        # Still counted as detected, no longer dropped for being multi.
        assert tally.dropped_multi_activation == 0
        assert tally.kept_multi_activation == 2
        assert tally.kept == 3
        # Out-of-bounds is not a policy choice — those have no signal at all.
        assert tally.dropped_boundary == 1
        assert tally.candidates == tally.detected

    assert len(kept) > len(dropped)
    assert all(seg.signal.shape == (WINDOW_SAMPLES,) for seg in kept)


def test_kept_multi_is_a_subset_of_kept_not_a_fourth_bucket() -> None:
    """`kept_multi_activation` must not double-count against the partition."""
    record = _record_with_activations([20, 1000, 1060, 2500])

    _, tallies = _extract(record, _config(low=0.5, high=0.5, keep_multi_activation=True))

    for tally in tallies:
        assert tally.candidates == tally.detected
        assert tally.kept_multi_activation <= tally.kept


def test_channel_absent_from_the_record_is_skipped_silently() -> None:
    """IAFDB's bipolar set is complete in all 32 records, but the loop must
    not assume it — a record missing a pair should yield the others, not
    raise."""
    record = _record_with_activations([800, 1600])
    trimmed = IAFDBRecord(
        name=record.name,
        patient=record.patient,
        placement=record.placement,
        fs=record.fs,
        signal=record.signal[:, :3],
        channel_names=record.channel_names[:3],
        units=record.units[:3],
        comments=record.comments,
        qrs_samples=record.qrs_samples,
    )

    segments, tallies = _extract(trimmed, _config())

    assert {t.channel for t in tallies} == set(BIPOLAR_CHANNELS[:3])
    assert {s.source_channel for s in segments} == set(BIPOLAR_CHANNELS[:3])


def test_unused_peak_to_peak_sentinel_survives_the_binding_gate() -> None:
    """The sentinel must clear the gate the producer actually passes through.

    `iafdb_bank.traces.peak_to_peak_mv` is required and constrained
    `minimum: 0`, and the two validators in this stack disagree about NaN:
    jsonschema accepts it (its test is `value < minimum`, False for NaN)
    while the codegen'd Pydantic field is `ge=0.0`, and `nan >= 0` is also
    False, so it *rejects*. The producer builds the Pydantic model before
    anything reaches a file, so Pydantic is binding — a NaN sentinel would
    have looked fine against the file schema and failed at write time.

    Asserted here against the codegen'd `PeakToPeakMvItem` directly, so the test exercises
    the binding constraint rather than the permissive one."""
    assert math.isinf(UNUSED_PEAK_TO_PEAK_MV)
    assert (
        _iafdb_bank_models.PeakToPeakMvItem(UNUSED_PEAK_TO_PEAK_MV).root == UNUSED_PEAK_TO_PEAK_MV
    )

    # Recorded so the choice is not quietly "simplified" later:
    # NaN reads as the natural not-applicable value but does not validate...
    with pytest.raises(ValidationError):
        _iafdb_bank_models.PeakToPeakMvItem(float("nan"))
    # ...and a negative sentinel fails for the ordinary reason.
    with pytest.raises(ValidationError):
        _iafdb_bank_models.PeakToPeakMvItem(-1.0)
    # 0.0 *would* validate — it is rejected on meaning, not on validity:
    # a flat trace genuinely measures 0.0, so it is indistinguishable from
    # a real reading.
    assert _iafdb_bank_models.PeakToPeakMvItem(0.0).root == 0.0


def test_sentinel_round_trips_through_the_columns_float32_storage() -> None:
    """The column is stored float32; the sentinel must survive the narrowing."""
    stored = float(np.asarray([UNUSED_PEAK_TO_PEAK_MV], dtype=np.float32)[0])
    assert stored == UNUSED_PEAK_TO_PEAK_MV
