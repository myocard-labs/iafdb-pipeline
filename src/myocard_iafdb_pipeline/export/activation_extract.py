"""Activation-anchored segment extraction (IAF1).

The second of this producer's two windowing modes. Where the sliding path
steps a fixed window across a record and keeps whatever clears an amplitude
threshold, this one detects the **activation train** per channel and cuts one
window per activation, anchored at a fractional position drawn from a band.

Why the corpus wants it: a synthetic trace is a single clean activation at a
known position, while an IAFDB sliding window is whatever happened to fall
inside it — often several activations, with the deflection anywhere. Two
corpora that differ in *framing* differ for reasons that have nothing to do
with tissue, and a classifier will happily learn the difference. Anchoring
both on an activation removes that, and *varying* the anchor position stops
position itself becoming the shortcut.

**This module owns the response, not the geometry.** Detection, window
placement and the in-bounds / single-activation classification are all
egm-signal's (SIG1) — shared with the synthetic producer precisely so the two
cannot drift. What lives here is IAFDB-specific: per-record conditioning
(calibrate, then filter once), the keep/drop policy over SIG1's flags, the
per-channel accounting, and turning survivors into the segment shape the bank
writer already consumes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from myocard_egm_signal import bandpass
from myocard_egm_signal.exceptions import DegenerateSignalError
from myocard_egm_signal.extraction.activation_based import (
    BotteronEnvelope,
    DetectionPreprocessor,
    GreedyHeightSuppressor,
    LocalMaximaSelector,
    RectifiedDerivative,
    TeagerKaiser,
    TwoStageRefiner,
    UniformPositionGenerator,
    detect_activation_train,
    window_train,
)
from myocard_egm_signal.thresholds import (
    MedianMadThreshold,
    PercentileSignalThreshold,
    SignalThreshold,
)

from myocard_iafdb_pipeline.cli._config import ActivationConfig, DetectionConfig
from myocard_iafdb_pipeline.constants import BIPOLAR_CHANNELS
from myocard_iafdb_pipeline.records import IAFDBRecord

logger = logging.getLogger(__name__)

UNUSED_PEAK_TO_PEAK_MV: float = float("inf")
"""What to write into ``iafdb_bank``'s required ``peak_to_peak_mv`` column
for a trace this mode did not select on amplitude.

The schema requires the column on every trace and describes it as "the
calibrated peak-to-peak amplitude **that passed the threshold check**" —
the *selection statistic*. Activation windows are selected by where an
activation is, so no threshold check happened and there is no honest value
to write. Until the schema stops requiring it (FB-30), something must go
there.

**Infinity, and the reason is not obvious.** The column is constrained
``minimum: 0``, and the two validators in this stack **disagree about NaN**:

- ``jsonschema`` accepts it — its check is ``value < minimum``, and
  ``nan < 0`` is False, so nothing fires.
- egm-contracts' codegen'd Pydantic model **rejects** it — the field is
  ``Field(ge=0.0)``, and ``nan >= 0`` is also False, so the constraint fails.

The producer builds the Pydantic model *before* anything reaches a file, so
Pydantic is the binding gate. NaN — the intuitive choice, and this schema's
idiom for ``threshold_value`` — would therefore fail at write time.

That leaves ``+inf``, which clears both gates and is the better sentinel on
its merits anyway: no window has infinite peak-to-peak, so unlike ``0.0``
(a legitimate flat-trace reading) it cannot be mistaken for a measurement,
and it poisons any mean or histogram loudly instead of quietly biasing it.
It round-trips through the column's float32 storage unchanged.

Verified against both validators rather than reasoned about; see
``test_unused_peak_to_peak_sentinel_survives_the_binding_gate``.
"""


@dataclass(frozen=True)
class ActivationSegment:
    """One kept window, named after the artifact it becomes.

    Field names mirror the **`iafdb_bank` `traces/` columns** — the
    cross-repo contract in egm-contracts — rather than any in-memory type
    from a sibling library. Contracts are the structures that are versioned
    and coordinated; an internal dataclass is not, so modelling on one means
    inheriting a shape that can be renamed out from under this producer.

    Two consequences of that choice, both deliberate:

    - No ``fs`` or ``end_sample``. The sampling rate is a bank root attr,
      not a per-trace column, and the end is ``start_sample + T``. Neither
      is part of the contract, so neither is carried.
    - **No ``peak_to_peak_mv``.** The schema requires it and describes it as
      "the calibrated peak-to-peak amplitude **that passed the threshold
      check**" — i.e. the *selection statistic*. In activation mode nothing
      passed a threshold check; windows are selected by where an activation
      is, not by how large the window is. Emitting a number there would
      assert a selection that never happened. See the module note for what
      this leaves open for the bank-writing step.
    """

    signal: np.ndarray
    patient_id: str
    source_record: str
    source_channel: str
    start_sample: int
    calibration_scalar: float
    activation_position: float


@dataclass(frozen=True)
class ChannelTally:
    """Per-channel accounting for one record's extraction.

    Yield is not a diagnostic afterthought here — study §8.1 chooses `T` and
    the position band by trading single-beat yield against morphology, and the
    methods section has to state how much real data survived. Reconstructing
    that later means re-running everything, so it is counted as we go.

    ``detected`` is activations found; ``kept``, ``dropped_boundary`` and
    ``dropped_multi_activation`` partition the candidate windows, one per
    detected activation. ``kept_multi_activation`` is a **subset of** ``kept``,
    not a fourth bucket: it is how many surviving windows hold more than one
    activation, which is zero unless multi-activation windows were allowed
    through.
    """

    record_name: str
    channel: str
    detected: int
    kept: int
    dropped_boundary: int
    dropped_multi_activation: int
    kept_multi_activation: int = 0
    degenerate: bool = False

    @property
    def candidates(self) -> int:
        return self.kept + self.dropped_boundary + self.dropped_multi_activation


def build_preprocessor(cfg: DetectionConfig, *, fs_hz: float) -> DetectionPreprocessor:
    """Map the config's curve name onto an egm-signal preprocessor."""
    if cfg.curve == "rectified_derivative":
        return RectifiedDerivative()
    if cfg.curve == "teager_kaiser":
        return TeagerKaiser()
    if cfg.curve == "botteron_envelope":
        # Config validation guarantees these are set for this curve.
        assert cfg.botteron_band_hz is not None
        assert cfg.botteron_lowpass_hz is not None
        return BotteronEnvelope(
            fs=fs_hz,
            band_hz=cfg.botteron_band_hz,
            lowpass_hz=cfg.botteron_lowpass_hz,
        )
    raise ValueError(f"Unknown detection curve: {cfg.curve!r}")


def build_threshold(cfg: DetectionConfig) -> SignalThreshold:
    """Map the config's threshold rule onto an egm-signal threshold."""
    if cfg.threshold_rule == "median_mad":
        assert cfg.threshold_c is not None
        assert cfg.threshold_lam is not None
        return MedianMadThreshold(c=cfg.threshold_c, lam=cfg.threshold_lam)
    if cfg.threshold_rule == "percentile":
        assert cfg.threshold_q is not None
        return PercentileSignalThreshold(q=cfg.threshold_q)
    raise ValueError(f"Unknown detection threshold rule: {cfg.threshold_rule!r}")


def _ms_to_samples(ms: float, fs_hz: float) -> int:
    return round(ms * 1e-3 * fs_hz)


def extract_activation_segments(
    record: IAFDBRecord,
    config: ActivationConfig,
    *,
    calibration_scalar: float,
    band_hz: tuple[float, float],
    position_generator: UniformPositionGenerator,
) -> tuple[list[ActivationSegment], list[ChannelTally]]:
    """Cut one window per detected activation, for every bipolar channel.

    Per record: apply the calibration scalar, band-pass **once over the whole
    record**, then per channel detect the train and window it. Filtering
    whole-record rather than per-window is deliberate — band-pass ringing at a
    window's edges would corrupt exactly the margins the position band exists
    to protect, and a filter applied after cutting sees an edge at every cut.

    ``position_generator`` is passed in rather than built here so one random
    stream spans the whole corpus: built per record, every record would draw
    the same positions from a fresh seed and the position distribution would
    be an artifact of the record count.
    """
    segments: list[ActivationSegment] = []
    tallies: list[ChannelTally] = []

    preprocessor = build_preprocessor(config.detection, fs_hz=record.fs)
    threshold = build_threshold(config.detection)
    selector = LocalMaximaSelector(min_prominence=config.detection.min_prominence)
    suppressor = GreedyHeightSuppressor(
        refractory_interval_samples=_ms_to_samples(config.detection.refractory_ms, record.fs)
    )
    refiner: TwoStageRefiner | None = None
    if config.detection.refine_curve is not None:
        assert config.detection.refine_radius_ms is not None
        refine_cfg = DetectionConfig(
            curve=config.detection.refine_curve,
            botteron_band_hz=config.detection.botteron_band_hz,
            botteron_lowpass_hz=config.detection.botteron_lowpass_hz,
            threshold_rule=config.detection.threshold_rule,
            threshold_c=config.detection.threshold_c,
            threshold_lam=config.detection.threshold_lam,
            threshold_q=config.detection.threshold_q,
            min_prominence=None,
            refractory_ms=config.detection.refractory_ms,
            refine_curve=None,
            refine_radius_ms=None,
        )
        refiner = TwoStageRefiner(
            preprocessor=build_preprocessor(refine_cfg, fs_hz=record.fs),
            radius_samples=_ms_to_samples(config.detection.refine_radius_ms, record.fs),
        )

    window_length = _ms_to_samples(config.trace_duration_ms, record.fs)

    for channel in BIPOLAR_CHANNELS:
        if channel not in record.channel_names:
            continue
        raw = record.signal[:, record.channel_index(channel)]
        conditioned = bandpass(calibration_scalar * raw, record.fs, band_hz[0], band_hz[1])

        try:
            train = detect_activation_train(
                conditioned,
                preprocessor=preprocessor,
                threshold=threshold,
                selector=selector,
                suppressor=suppressor,
                refiner=refiner,
            )
        except DegenerateSignalError:
            # A dead or rail-clipped channel. egm-signal raises rather than
            # returning an empty result, precisely so a corpus sweep has to
            # decide: we count it and carry on, because one flat channel is
            # not a reason to abandon a record — but it is worth knowing
            # about, since a channel that flatlines is a recording fault.
            logger.warning(
                "Degenerate channel %s in %s — no activations detectable; skipping.",
                channel,
                record.name,
            )
            tallies.append(
                ChannelTally(
                    record_name=record.name,
                    channel=channel,
                    detected=0,
                    kept=0,
                    dropped_boundary=0,
                    dropped_multi_activation=0,
                    degenerate=True,
                )
            )
            continue

        windows = window_train(
            conditioned,
            train,
            position_generator=position_generator,
            window_length_samples=window_length,
        )

        in_bounds = windows.in_bounds_mask
        single = windows.single_activation_mask

        # Out-of-bounds windows are dropped unconditionally — there is no
        # signal to keep, the field is literally None, so this is not a
        # policy choice. Multi-activation windows *are* a policy choice, and
        # the config decides.
        #
        # Order matters here regardless: the in-bounds filter has to come
        # first, or a None signal reaches the loop below.
        keep_mask = in_bounds if config.keep_multi_activation else (in_bounds & single)
        dropped_boundary = int(np.count_nonzero(~in_bounds))
        dropped_multi = (
            0 if config.keep_multi_activation else int(np.count_nonzero(in_bounds & ~single))
        )
        kept_multi = int(np.count_nonzero(keep_mask & ~single))
        kept_windows = windows.select(keep_mask)

        for w in kept_windows.windows:
            signal = w.signal
            assert signal is not None  # guaranteed by the in_bounds filter
            segments.append(
                ActivationSegment(
                    signal=signal,
                    patient_id=record.patient,
                    source_record=record.name,
                    source_channel=channel,
                    start_sample=int(w.start_index),
                    calibration_scalar=float(calibration_scalar),
                    activation_position=float(w.realized_position),
                )
            )

        tallies.append(
            ChannelTally(
                record_name=record.name,
                channel=channel,
                detected=int(train.size),
                kept=len(kept_windows.windows),
                dropped_boundary=dropped_boundary,
                dropped_multi_activation=dropped_multi,
                kept_multi_activation=kept_multi,
            )
        )

    return segments, tallies


def build_position_generator(config: ActivationConfig) -> UniformPositionGenerator:
    """One generator for a whole corpus run — see ``extract_activation_segments``."""
    return UniformPositionGenerator(
        config.position.low, config.position.high, seed=config.position.seed
    )


__all__ = [
    "UNUSED_PEAK_TO_PEAK_MV",
    "ActivationSegment",
    "ChannelTally",
    "build_position_generator",
    "build_preprocessor",
    "build_threshold",
    "extract_activation_segments",
]
