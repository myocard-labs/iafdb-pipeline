"""Shared fixtures.

The synthetic-record fixtures here let us exercise the preprocessing pipeline
without touching the network or the downloaded dataset. Signals are
constructed with known QRS amplitudes and known bipolar peak-to-peak values
so we can write assertions that catch real regressions, not just smoke
errors.
"""

from __future__ import annotations

import numpy as np
import pytest

from myocard_iafdb_pipeline.records import IAFDBRecord


def _synthetic_qrs_complex(fs: float, peak_to_peak: float, duration_ms: float = 80.0) -> np.ndarray:
    """Crude triangular Q-R-S shape with the requested peak-to-peak amplitude."""
    n = max(3, round(duration_ms * 1e-3 * fs))
    # Triangular up-down-up around a baseline of 0.
    t = np.linspace(0, 1, n)
    # Asymmetric shape: small Q dip, large R peak, small S dip.
    q_amp = -0.15 * peak_to_peak
    r_amp = 0.85 * peak_to_peak
    s_amp = -0.15 * peak_to_peak
    # Three control points: at t=0.25 (Q), t=0.5 (R), t=0.75 (S); interpolate.
    xp = [0.0, 0.25, 0.5, 0.75, 1.0]
    fp = [0.0, q_amp, r_amp, s_amp, 0.0]
    return np.interp(t, xp, fp)


def build_synthetic_record(
    *,
    name: str = "iaf1_afw",
    fs: float = 1000.0,
    duration_s: float = 4.0,
    qrs_period_s: float = 0.8,
    qrs_pp_mv: float = 1.0,
    bipolar_pp_mv: tuple[float, float, float, float, float] = (
        0.3,
        0.4,
        0.6,
        0.8,
        0.2,
    ),
    seed: int = 0,
    include_qrs_annotations: bool = True,
    surface_leads: tuple[str, ...] = ("II", "V1", "aVF"),
) -> IAFDBRecord:
    """Build a synthetic IAFDBRecord with known properties.

    Channels are ordered: ``surface_leads`` then ``CS12, CS34, CS56, CS78, CS90``.
    Each bipolar channel's signal is band-limited noise with a sinusoidal
    envelope scaled so its peak-to-peak amplitude matches ``bipolar_pp_mv``.
    The surface ECG channels carry repeated synthetic QRS complexes with
    peak-to-peak ``qrs_pp_mv`` (scaled per lead — II largest, V1/aVF smaller).
    """
    rng = np.random.default_rng(seed)
    n = round(duration_s * fs)
    n_bipolar = len(bipolar_pp_mv)
    n_chan = len(surface_leads) + n_bipolar
    signal = np.zeros((n, n_chan), dtype=np.float64)

    # Surface ECG with periodic QRS complexes
    qrs_samples: list[int] = []
    qrs_template = _synthetic_qrs_complex(fs, peak_to_peak=qrs_pp_mv)
    qrs_n = qrs_template.size
    half = qrs_n // 2
    step = round(qrs_period_s * fs)
    # Per-lead scale: II at 1.0, V1 at 0.6, others at 0.5
    lead_scale = {
        "II": 1.0,
        "I": 0.9,
        "III": 0.4,
        "V1": 0.6,
        "V5": 0.8,
        "aVF": 0.5,
        "aVL": 0.4,
        "aVR": 0.3,
    }
    for sample in range(step, n - qrs_n, step):
        qrs_samples.append(sample)
        for li, lead in enumerate(surface_leads):
            scale = lead_scale.get(lead, 0.5)
            lo = sample - half
            hi = lo + qrs_n
            signal[lo:hi, li] += qrs_template * scale

    # Add gentle baseline noise to all surface channels
    for li in range(len(surface_leads)):
        signal[:, li] += rng.normal(scale=0.02 * qrs_pp_mv, size=n)

    # Bipolar EGM channels: band-limited noise with controlled peak-to-peak.
    # Sample white noise, then dial the amplitude so peak-to-peak hits target.
    bipolar_labels = ("CS12", "CS34", "CS56", "CS78", "CS90")
    for ci, (label, target_pp) in enumerate(zip(bipolar_labels, bipolar_pp_mv, strict=True)):
        col = len(surface_leads) + ci
        # Place strong "EGM activations" at periodic intervals to ensure
        # peak-to-peak in some windows exceeds the target.
        x = rng.normal(scale=0.05 * target_pp, size=n)
        for sample in range(int(0.2 * fs), n - int(0.1 * fs), int(0.3 * fs)):
            # Bipolar deflection: sharp biphasic pulse
            pulse_n = max(3, int(0.05 * fs))
            t = np.linspace(-1, 1, pulse_n)
            pulse = (target_pp / 2.0) * np.sin(np.pi * t) * np.exp(-((t * 2) ** 2))
            lo = sample
            hi = min(n, sample + pulse_n)
            x[lo:hi] += pulse[: hi - lo]
        signal[:, col] = x
        # Tag the label
        _ = label

    channel_names = tuple(surface_leads) + bipolar_labels
    units = ("mV",) * n_chan
    patient, placement = name.split("_", 1)
    qrs_arr = (
        np.array(qrs_samples, dtype=np.int64)
        if include_qrs_annotations
        else np.empty(0, dtype=np.int64)
    )

    return IAFDBRecord(
        name=name,
        patient=patient,
        placement=placement,
        fs=float(fs),
        signal=signal,
        channel_names=channel_names,
        units=units,
        comments=("synthetic record for testing",),
        qrs_samples=qrs_arr,
    )


@pytest.fixture
def synthetic_record() -> IAFDBRecord:
    """A default synthetic record: 4 s @ 1 kHz, 1.0 mV QRS on lead II."""
    return build_synthetic_record()


@pytest.fixture
def synthetic_record_no_qrs() -> IAFDBRecord:
    """A synthetic record without QRS annotations (for error-path testing)."""
    return build_synthetic_record(include_qrs_annotations=False)


@pytest.fixture
def synthetic_record_factory():
    """Factory that lets a test parametrize the synthetic record's properties."""
    return build_synthetic_record
