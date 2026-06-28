"""YAML config loading + per-CLI typed config builders.

The two export CLIs (``iafdb-export-bank`` and
``iafdb-export-noise-bank``) take a YAML config file as the only
positional argument plus a handful of run-time flags (``--overwrite``,
``--no-progress``). All substantive parameters — paths, thresholds,
windowing, calibration target, output format, label policy — live in
the YAML.

Two typed config dataclasses (``BankExportConfig``,
``NoiseBankExportConfig``) capture what each CLI needs after the
YAML is parsed and validated. The orchestrators (``export_bank``,
``export_noise_bank``) keep their function signatures; the CLI's job
is to translate YAML to function kwargs.

Schema documented in detail in the example YAML files under
``examples/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from myocard_egm_signal import DEFAULT_BIPOLAR_BAND_HZ


class ConfigError(ValueError):
    """Raised when a config file is malformed or missing required keys."""


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def load_yaml(path: Path | str) -> dict[str, Any]:
    """Load a YAML file into a dict.

    Resolves the path's parent into the returned dict under the
    ``_config_dir`` key so per-config-file relative paths in
    subsequent fields can be resolved against the YAML's directory
    (the same convention egm-classifier uses).
    """
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"Config file not found: {p}")
    with p.open(encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    if not isinstance(loaded, dict):
        raise ConfigError(f"Config {p} did not parse as a YAML mapping at the top level.")
    loaded["_config_dir"] = p.parent.resolve()
    return loaded


def _required(doc: dict[str, Any], *path: str) -> Any:
    """Walk ``path`` into the nested dict; raise on any missing key."""
    node: Any = doc
    for k in path:
        if not isinstance(node, dict) or k not in node:
            dotted = ".".join(path)
            raise ConfigError(f"Required config field missing: {dotted}")
        node = node[k]
    return node


def _optional(doc: dict[str, Any], *path: str, default: Any = None) -> Any:
    """Walk ``path`` into the nested dict; return ``default`` if missing."""
    node: Any = doc
    for k in path:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node


def _resolve_path(value: str | None, config_dir: Path) -> Path | None:
    """Resolve a YAML-supplied path string against the config file's dir.

    ``None`` or empty string returns ``None`` so the caller can fall
    back to a CLI-side default. Absolute paths pass through
    untouched.
    """
    if value is None or value == "":
        return None
    p = Path(value)
    return p if p.is_absolute() else (config_dir / p).resolve()


# ---------------------------------------------------------------------------
# Bank-export (healthy / pretraining / classifier-format) config
# ---------------------------------------------------------------------------


OutputFormat = Literal["iafdb", "classifier"]
ThresholdMode = Literal["absolute", "percentile", "none"]


@dataclass(frozen=True)
class BankExportConfig:
    """Typed config for the ``iafdb-export-bank`` CLI."""

    # data
    data_dir: Path
    output: Path
    bank_id: str | None

    # format
    output_format: OutputFormat
    label_policy: str
    classifier_output: Path | None

    # threshold
    threshold_mode: ThresholdMode
    threshold_value: float | None

    # windowing
    window_ms: float
    hop_ms: float
    band_hz: tuple[float, float]

    # calibration
    target_qrs_pp_mv: float


def build_bank_export_config(doc: dict[str, Any]) -> BankExportConfig:
    """Translate a parsed YAML dict into a typed bank-export config.

    Layered defaults match the previous CLI's argparse defaults so
    omitting a field reproduces the prior behavior.
    """
    cfg_dir: Path = doc["_config_dir"]

    data_dir = _resolve_path(_required(doc, "data", "data_dir"), cfg_dir)
    output = _resolve_path(_required(doc, "data", "output"), cfg_dir)
    if data_dir is None or output is None:
        raise ConfigError("data.data_dir and data.output must both be set.")

    # Optional explicit stable id; None -> the producer derives a default.
    bank_id_raw = _optional(doc, "data", "bank_id", default=None)
    bank_id = str(bank_id_raw) if bank_id_raw is not None else None

    output_format_raw = _optional(doc, "format", "type", default="iafdb")
    if output_format_raw not in ("iafdb", "classifier"):
        raise ConfigError(f"format.type must be 'iafdb' or 'classifier'; got {output_format_raw!r}")

    label_policy = str(_optional(doc, "format", "label_policy", default="all-healthy"))
    classifier_output = _resolve_path(
        _optional(doc, "format", "classifier_output", default=None), cfg_dir
    )

    threshold_mode_raw = _optional(doc, "threshold", "mode", default="absolute")
    if threshold_mode_raw not in ("absolute", "percentile", "none"):
        raise ConfigError(
            f"threshold.mode must be 'absolute' / 'percentile' / 'none'; got {threshold_mode_raw!r}"
        )
    threshold_value_raw = _optional(doc, "threshold", "value", default=None)
    threshold_value = None if threshold_mode_raw == "none" else float(threshold_value_raw or 0.2)

    window_ms = float(_optional(doc, "windowing", "window_ms", default=512.0))
    hop_ms = float(_optional(doc, "windowing", "hop_ms", default=256.0))
    band_hz_raw = _optional(doc, "windowing", "band_hz", default=list(DEFAULT_BIPOLAR_BAND_HZ))
    if not (isinstance(band_hz_raw, list) and len(band_hz_raw) == 2):
        raise ConfigError("windowing.band_hz must be a two-element list [low, high].")
    band_hz = (float(band_hz_raw[0]), float(band_hz_raw[1]))

    target_qrs_pp_mv = float(_optional(doc, "calibration", "target_qrs_pp_mv", default=1.0))

    return BankExportConfig(
        data_dir=data_dir,
        output=output,
        bank_id=bank_id,
        output_format=output_format_raw,
        label_policy=label_policy,
        classifier_output=classifier_output,
        threshold_mode=threshold_mode_raw,
        threshold_value=threshold_value,
        window_ms=window_ms,
        hop_ms=hop_ms,
        band_hz=band_hz,
        target_qrs_pp_mv=target_qrs_pp_mv,
    )


# ---------------------------------------------------------------------------
# Noise-bank-export config
# ---------------------------------------------------------------------------


NoiseThresholdMode = Literal["absolute", "percentile"]


@dataclass(frozen=True)
class NoiseBankExportConfig:
    """Typed config for the ``iafdb-export-noise-bank`` CLI."""

    # data
    data_dir: Path
    output: Path
    run_record_output: Path | None
    bank_id: str | None

    # threshold
    threshold_mode: NoiseThresholdMode
    threshold_value: float

    # windowing
    window_ms: float
    hop_ms: float
    band_hz: tuple[float, float]

    # provenance
    description: str


def build_noise_bank_export_config(doc: dict[str, Any]) -> NoiseBankExportConfig:
    """Translate a parsed YAML dict into a typed noise-bank-export config."""
    cfg_dir: Path = doc["_config_dir"]

    data_dir = _resolve_path(_required(doc, "data", "data_dir"), cfg_dir)
    output = _resolve_path(_required(doc, "data", "output"), cfg_dir)
    if data_dir is None or output is None:
        raise ConfigError("data.data_dir and data.output must both be set.")
    run_record_output = _resolve_path(
        _optional(doc, "data", "run_record_output", default=None), cfg_dir
    )

    # Optional explicit stable id; None -> the producer derives a default.
    bank_id_raw = _optional(doc, "data", "bank_id", default=None)
    bank_id = str(bank_id_raw) if bank_id_raw is not None else None

    threshold_mode_raw = _optional(doc, "threshold", "mode", default="percentile")
    if threshold_mode_raw not in ("absolute", "percentile"):
        raise ConfigError(
            f"threshold.mode must be 'absolute' or 'percentile' for noise extraction; "
            f"got {threshold_mode_raw!r}"
        )
    threshold_value = float(_required(doc, "threshold", "value"))

    window_ms = float(_optional(doc, "windowing", "window_ms", default=200.0))
    hop_ms = float(_optional(doc, "windowing", "hop_ms", default=100.0))
    band_hz_raw = _optional(doc, "windowing", "band_hz", default=list(DEFAULT_BIPOLAR_BAND_HZ))
    if not (isinstance(band_hz_raw, list) and len(band_hz_raw) == 2):
        raise ConfigError("windowing.band_hz must be a two-element list [low, high].")
    band_hz = (float(band_hz_raw[0]), float(band_hz_raw[1]))

    description = str(_optional(doc, "description", default=""))

    return NoiseBankExportConfig(
        data_dir=data_dir,
        output=output,
        run_record_output=run_record_output,
        bank_id=bank_id,
        threshold_mode=threshold_mode_raw,
        threshold_value=threshold_value,
        window_ms=window_ms,
        hop_ms=hop_ms,
        band_hz=band_hz,
        description=description,
    )
