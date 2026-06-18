"""Dataset constants — patients, placements, channels.

Single source of truth for record names and channel layouts. See the IAFDB
description on PhysioNet: https://physionet.org/content/iafdb/1.0.0/
"""

from __future__ import annotations

PATIENTS: tuple[str, ...] = tuple(f"iaf{i}" for i in range(1, 9))
"""Patient identifiers: iaf1 … iaf8."""

PLACEMENTS: tuple[str, ...] = ("svc", "ivc", "tva", "afw")
"""Catheter placements per patient.

- ``svc``: distal tip near superior vena cava annulus.
- ``ivc``: proximal tip near inferior vena cava annulus.
- ``tva``: distal tip on tricuspid valve annulus.
- ``afw``: catheter against atrial free wall (longest recordings; include
  drug delivery and washout).
"""

RECORD_NAMES: tuple[str, ...] = tuple(f"{p}_{pl}" for p in PATIENTS for pl in PLACEMENTS)
"""32 canonical record names (8 patients x 4 placements)."""

BIPOLAR_CHANNELS: tuple[str, ...] = ("CS12", "CS34", "CS56", "CS78", "CS90")
"""Intracardiac bipolar channels in distal-to-proximal order."""

SURFACE_ECG_CHANNELS: tuple[str, ...] = (
    "I",
    "II",
    "III",
    "V1",
    "V5",
    "aVF",
    "aVL",
    "aVR",
)
"""Surface ECG channels that may appear in IAFDB headers (varies per record)."""

PHYSIONET_BASE_URL: str = "https://physionet.org/files/iafdb/1.0.0"
"""Base URL for the IAFDB files on PhysioNet."""

PHYSIONET_DOI: str = "10.13026/C23S33"
"""DOI for the IAFDB v1.0.0 release."""

SAMPLING_RATE_HZ: float = 1000.0
"""IAFDB sampling rate (Hz). All 32 records use this rate."""

DEFAULT_NOISE_WINDOW_MS: float = 200.0
"""Default sliding-window length in ms for noise extraction.

Shorter than the healthy-segment default (512 ms) on purpose: noise
windows feed the synthetic mixer, which tiles/crops a noise sample to
match the clean trace's length. Shorter source windows give the mixer
more variety per record without inflating the bank's row count too far.
"""

DEFAULT_NOISE_HOP_MS: float = 100.0
"""Default hop between adjacent noise windows in ms. 50% overlap of the 200 ms window."""
