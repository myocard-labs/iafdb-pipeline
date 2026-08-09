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

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from myocard_egm_signal import DEFAULT_BIPOLAR_BAND_HZ

from myocard_iafdb_pipeline.constants import SAMPLING_RATE_HZ
from myocard_iafdb_pipeline.ids import validate_artifact_id


class ConfigError(ValueError):
    """Raised when a config file is malformed or missing required keys."""


class ConfigWarning(UserWarning):
    """A config is valid here but likely to disappoint a known consumer.

    Its own category so a caller can silence it (or promote it to an
    error) without touching unrelated warnings — the distinction being
    that this producer has no opinion, but something downstream might.
    """


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


def _present(doc: dict[str, Any], *path: str) -> bool:
    """True if ``path`` is explicitly set in the document.

    Distinct from ``_optional(...) is not None``: it tells apart "the user
    wrote this key" from "the key defaulted". Needed to reject keys that
    belong to the *other* windowing mode — silently ignoring a key the
    user deliberately set is how a config comes to mean something other
    than it says.
    """
    node: Any = doc
    for k in path:
        if not isinstance(node, dict) or k not in node:
            return False
        node = node[k]
    return True


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

# How windows are cut from a record.
#
# ``sliding`` is the shipped behavior: a fixed-length window every ``hop_ms``,
# kept or dropped on its peak-to-peak amplitude. ``activation`` is IAF1: detect
# the activation train, then cut one window per activation anchored at a drawn
# fractional position. The two are different sampling schemes over the same
# records, not two settings of one scheme, which is why the mode selects a
# whole config block rather than a flag.
WindowingMode = Literal["sliding", "activation"]

# The detection curve `g` that peaks at activations (egm-signal's
# DetectionPreprocessor family). Sharpness vs noise-robustness: the first two
# spike at every steep deflection, the envelope smooths a fractionated complex
# into one bump before anything downstream sees it.
DetectionCurve = Literal["rectified_derivative", "teager_kaiser", "botteron_envelope"]

# The rule that turns the detection curve into an amplitude threshold.
DetectionThresholdRule = Literal["median_mad", "percentile"]

# A trace length that is a multiple of this suits egm-classifier's current
# MobileViT-1D, whose stem downsamples 32x and whose stage-5 block takes patch
# size 2. It is **that consumer's** constraint, not this producer's, and it
# would evaporate if the model changed — so an off-grid length is a *warning*
# here, never an error. Anyone using this tool without that classifier is
# entitled to whatever length they asked for.
#
# What is worth warning about: egm-classifier will zero-pad an off-grid trace,
# and padding shifts the activation off the fractional position this producer
# placed it at — re-introducing the positional regularity the activation
# anchoring exists to remove. We emit exactly what was asked for and say so;
# padding or clipping to fit is deliberately NOT done here (a pad/clip policy
# would be a project-level decision, not a quiet side effect of loading a
# config).
CLASSIFIER_LENGTH_MULTIPLE_SAMPLES = 64


@dataclass(frozen=True)
class DetectionConfig:
    """Detection-chain settings: curve -> threshold -> select -> suppress."""

    curve: DetectionCurve
    # Botteron only; ignored (and rejected) for the other two curves.
    botteron_band_hz: tuple[float, float] | None
    botteron_lowpass_hz: float | None

    threshold_rule: DetectionThresholdRule
    # median_mad: tau = c * median(g) + lam * MAD(g)
    threshold_c: float | None
    threshold_lam: float | None
    # percentile: tau = percentile(g, q)
    threshold_q: float | None

    # LocalMaximaSelector; None = keep every local maximum above tau.
    min_prominence: float | None
    # GreedyHeightSuppressor, in ms so it is rate-independent. This is the
    # physiological floor on activation spacing, NOT an amplitude rule.
    refractory_ms: float

    # Optional TwoStageRefiner: re-locate each anchor on a sharper curve
    # within a small radius, after a smoothed curve found it.
    refine_curve: DetectionCurve | None
    refine_radius_ms: float | None


@dataclass(frozen=True)
class PositionBandConfig:
    """The band `P` that each window's activation position is drawn from.

    A fraction of the trace: 0.0 puts the activation on the first sample,
    1.0 on the last. Collapsing the band to a point (``low == high``) is
    the fixed-position baseline arm of the A/B, not a special code path.
    """

    low: float
    high: float
    seed: int | None


@dataclass(frozen=True)
class ActivationConfig:
    """The ``activation:`` block — required iff ``windowing.mode`` is it."""

    trace_duration_ms: float
    detection: DetectionConfig
    position: PositionBandConfig
    # Let windows containing more than one detected activation through.
    #
    # Default False: the corpus contract is one activation per trace, and a
    # multi-activation window is a different *kind* of trace in a bank the
    # classifier reads as uniform.
    #
    # Why it is an option at all, and not just a drop. Every IAFDB patient is
    # in AF or another arrhythmia, so the records are dense with activations
    # and the fibrillatory side-peaks are exactly what the detection
    # parameters have to suppress. Mistune them and almost every window holds
    # a neighbour — which, with multi-activation windows dropped, looks
    # identical to "the detector found nothing": an empty bank with no
    # indication of why. Allowing them through turns a silent empty result
    # into a visible one you can inspect, which is what makes the parameters
    # tunable at all. Longer term these windows are also the ones likely to
    # carry information about where fibrotic tissue sits, so they are worth
    # being able to keep on purpose rather than only by accident.
    #
    # Out-of-bounds windows are dropped either way — those have no signal.
    keep_multi_activation: bool = False


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
    windowing_mode: WindowingMode
    window_ms: float
    hop_ms: float
    band_hz: tuple[float, float]

    # activation windowing — set iff windowing_mode == "activation"
    activation: ActivationConfig | None

    # calibration
    target_qrs_pp_mv: float


def _build_activation_config(doc: dict[str, Any], *, fs_hz: float) -> ActivationConfig:
    """Parse + validate the ``activation:`` block.

    Every value here is a **policy** choice: egm-signal deliberately ships
    no defaults for threshold multipliers, prominence floors, refractory
    intervals, position ranges or window lengths, because they decide what
    the science is. The defaults below are this producer's, and study §8.1
    sets the values that actually get used.
    """
    trace_duration_ms = float(_optional(doc, "activation", "trace_duration_ms", default=192.0))
    if trace_duration_ms <= 0:
        raise ConfigError(f"activation.trace_duration_ms must be positive; got {trace_duration_ms}")
    samples = round(trace_duration_ms * 1e-3 * fs_hz)
    if samples % CLASSIFIER_LENGTH_MULTIPLE_SAMPLES != 0:
        lower = samples // CLASSIFIER_LENGTH_MULTIPLE_SAMPLES * CLASSIFIER_LENGTH_MULTIPLE_SAMPLES
        upper = lower + CLASSIFIER_LENGTH_MULTIPLE_SAMPLES
        warnings.warn(
            f"activation.trace_duration_ms={trace_duration_ms:g} is {samples} samples at "
            f"{fs_hz:g} Hz, which is not a multiple of "
            f"{CLASSIFIER_LENGTH_MULTIPLE_SAMPLES}. Traces of this length are produced as "
            f"asked — nothing is padded or clipped here — but myocard-egm-classifier's "
            f"current MobileViT-1D needs a multiple of "
            f"{CLASSIFIER_LENGTH_MULTIPLE_SAMPLES} and will zero-pad them, which shifts each "
            f"activation off the position this producer placed it at. If the bank is headed "
            f"for that classifier, {lower} or {upper} samples "
            f"({lower * 1000 / fs_hz:g} or {upper * 1000 / fs_hz:g} ms) avoid the padding.",
            ConfigWarning,
            stacklevel=2,
        )

    curve = _optional(doc, "activation", "detection", "curve", default="rectified_derivative")
    valid_curves = ("rectified_derivative", "teager_kaiser", "botteron_envelope")
    if curve not in valid_curves:
        raise ConfigError(
            f"activation.detection.curve must be one of {valid_curves}; got {curve!r}"
        )

    # Botteron's band/low-pass describe that curve only. Accepting them for
    # the others would silently ignore a deliberate setting.
    botteron_keys_set = _present(doc, "activation", "detection", "botteron_band_hz") or _present(
        doc, "activation", "detection", "botteron_lowpass_hz"
    )
    if curve != "botteron_envelope" and botteron_keys_set:
        raise ConfigError(
            "activation.detection.botteron_* apply only to "
            f"curve='botteron_envelope'; got curve={curve!r}"
        )
    botteron_band_hz: tuple[float, float] | None = None
    botteron_lowpass_hz: float | None = None
    if curve == "botteron_envelope":
        raw_band = _optional(
            doc, "activation", "detection", "botteron_band_hz", default=[40.0, 250.0]
        )
        if not (isinstance(raw_band, list) and len(raw_band) == 2):
            raise ConfigError(
                "activation.detection.botteron_band_hz must be a two-element list [low, high]."
            )
        botteron_band_hz = (float(raw_band[0]), float(raw_band[1]))
        botteron_lowpass_hz = float(
            _optional(doc, "activation", "detection", "botteron_lowpass_hz", default=20.0)
        )

    rule = _optional(doc, "activation", "detection", "threshold", "rule", default="median_mad")
    if rule not in ("median_mad", "percentile"):
        raise ConfigError(
            f"activation.detection.threshold.rule must be 'median_mad' or "
            f"'percentile'; got {rule!r}"
        )
    threshold_c: float | None = None
    threshold_lam: float | None = None
    threshold_q: float | None = None
    if rule == "median_mad":
        threshold_c = float(
            _optional(doc, "activation", "detection", "threshold", "c", default=1.0)
        )
        # 10.0, not the 5.0 first written here. The threshold is
        # c*median(g) + lam*MAD(g), so on a channel that is mostly baseline
        # a small multiplier sits only a couple of noise-sigma up and
        # ordinary noise peaks register as activations. Measured on the
        # test fixture, lam=5 finds 16 activations where 3 were planted;
        # lam=10 finds exactly 3. Still a placeholder — study §8.1 sets the
        # value against real records — but a placeholder that is not
        # obviously wrong.
        threshold_lam = float(
            _optional(doc, "activation", "detection", "threshold", "lam", default=10.0)
        )
    else:
        threshold_q = float(
            _optional(doc, "activation", "detection", "threshold", "q", default=99.0)
        )
        if not 0.0 <= threshold_q <= 100.0:
            raise ConfigError(
                f"activation.detection.threshold.q must be a percentile in [0, 100]; "
                f"got {threshold_q}"
            )

    min_prominence_raw = _optional(doc, "activation", "detection", "min_prominence", default=None)
    min_prominence = None if min_prominence_raw is None else float(min_prominence_raw)

    refractory_ms = float(_optional(doc, "activation", "detection", "refractory_ms", default=50.0))
    if refractory_ms <= 0:
        raise ConfigError(
            f"activation.detection.refractory_ms must be positive; got {refractory_ms}"
        )

    refine_curve_raw = _optional(doc, "activation", "detection", "refine", "curve", default=None)
    refine_curve: DetectionCurve | None = None
    refine_radius_ms: float | None = None
    if refine_curve_raw is not None:
        if refine_curve_raw not in valid_curves:
            raise ConfigError(
                f"activation.detection.refine.curve must be one of {valid_curves}; "
                f"got {refine_curve_raw!r}"
            )
        refine_curve = refine_curve_raw
        refine_radius_ms = float(
            _optional(doc, "activation", "detection", "refine", "radius_ms", default=10.0)
        )
        if refine_radius_ms <= 0:
            raise ConfigError(
                f"activation.detection.refine.radius_ms must be positive; got {refine_radius_ms}"
            )

    low = float(_optional(doc, "activation", "position", "low", default=0.4))
    high = float(_optional(doc, "activation", "position", "high", default=0.6))
    if not 0.0 <= low <= high <= 1.0:
        raise ConfigError(
            f"activation.position must satisfy 0 <= low <= high <= 1; got low={low}, high={high}"
        )
    seed_raw = _optional(doc, "activation", "position", "seed", default=None)
    seed = None if seed_raw is None else int(seed_raw)

    keep_multi_raw = _optional(doc, "activation", "keep_multi_activation", default=False)
    if not isinstance(keep_multi_raw, bool):
        raise ConfigError(
            f"activation.keep_multi_activation must be true or false; got {keep_multi_raw!r}"
        )

    return ActivationConfig(
        trace_duration_ms=trace_duration_ms,
        detection=DetectionConfig(
            curve=curve,
            botteron_band_hz=botteron_band_hz,
            botteron_lowpass_hz=botteron_lowpass_hz,
            threshold_rule=rule,
            threshold_c=threshold_c,
            threshold_lam=threshold_lam,
            threshold_q=threshold_q,
            min_prominence=min_prominence,
            refractory_ms=refractory_ms,
            refine_curve=refine_curve,
            refine_radius_ms=refine_radius_ms,
        ),
        position=PositionBandConfig(low=low, high=high, seed=seed),
        keep_multi_activation=keep_multi_raw,
    )


def _validated_id(value: Any, *, field_path: str) -> str | None:
    """Validate an optional config-supplied ArtifactId override at load time.

    Returns the id string unchanged (``None`` passes through). Raises
    :class:`ConfigError` with the field path on a malformed id, so a bad
    override fails fast at config-load — before the (expensive) export run
    rather than at the bank write at the very end.
    """
    if value is None:
        return None
    try:
        return validate_artifact_id(str(value))
    except ValueError as exc:
        raise ConfigError(f"{field_path}: {exc}") from exc


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
    # Validated at load so a malformed override fails before the export run.
    bank_id = _validated_id(
        _optional(doc, "data", "bank_id", default=None), field_path="data.bank_id"
    )

    output_format_raw = _optional(doc, "format", "type", default="iafdb")
    if output_format_raw not in ("iafdb", "classifier"):
        raise ConfigError(f"format.type must be 'iafdb' or 'classifier'; got {output_format_raw!r}")

    # Default `unlabeled`, deliberately. IAFDB carries no per-segment
    # fibrosis truth, so a bank that omits this key should not quietly
    # acquire labels — and for a while it did: the old `all-healthy`
    # default meant a config that said nothing about labels produced a
    # fully-labeled bank, which is a claim about the data nobody made.
    #
    # The labeling idea came from a paper using a simple amplitude rule to
    # separate healthy from unhealthy tissue. On closer reading of IAFDB
    # that approach does not transfer to the classification this project is
    # doing, so `all-healthy` is now opt-in and carries a health warning
    # where it is demonstrated.
    label_policy = str(_optional(doc, "format", "label_policy", default="unlabeled"))
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

    windowing_mode = _optional(doc, "windowing", "mode", default="sliding")
    if windowing_mode not in ("sliding", "activation"):
        raise ConfigError(
            f"windowing.mode must be 'sliding' or 'activation'; got {windowing_mode!r}"
        )

    window_ms = float(_optional(doc, "windowing", "window_ms", default=512.0))
    hop_ms = float(_optional(doc, "windowing", "hop_ms", default=256.0))
    band_hz_raw = _optional(doc, "windowing", "band_hz", default=list(DEFAULT_BIPOLAR_BAND_HZ))
    if not (isinstance(band_hz_raw, list) and len(band_hz_raw) == 2):
        raise ConfigError("windowing.band_hz must be a two-element list [low, high].")
    band_hz = (float(band_hz_raw[0]), float(band_hz_raw[1]))

    # The two modes take disjoint settings, so a key belonging to the mode
    # you are not in is always a mistake — usually a half-finished edit.
    # Rejecting beats ignoring: a silently-dropped window_ms reads as
    # "windows are 512 ms" to whoever wrote it.
    activation: ActivationConfig | None = None
    if windowing_mode == "activation":
        for unused in ("window_ms", "hop_ms"):
            if _present(doc, "windowing", unused):
                raise ConfigError(
                    f"windowing.{unused} has no meaning under mode='activation' — windows are "
                    f"anchored on detected activations, not stepped at a fixed stride. "
                    f"Trace length is activation.trace_duration_ms."
                )
        # Amplitude selection is a sliding-mode concept. Activation mode picks
        # windows by *where an activation is*, and computes no peak-to-peak
        # statistic to threshold against — so a threshold block here would
        # either be ignored (a config that lies) or would need a second,
        # independent selection mechanism layered on top of the first.
        if _present(doc, "threshold") and threshold_mode_raw != "none":
            raise ConfigError(
                "threshold.mode has no meaning under windowing.mode='activation' — windows "
                "are selected by where an activation is, not by amplitude, and no "
                "peak-to-peak statistic is computed to threshold against. Remove the "
                "threshold block (the bank records threshold_mode='none')."
            )
        activation = _build_activation_config(doc, fs_hz=SAMPLING_RATE_HZ)
        # No amplitude selection happened, and the bank must say so rather
        # than inherit the sliding default of 'absolute 0.2'.
        threshold_mode_raw = "none"
        threshold_value = None
    elif _present(doc, "activation"):
        raise ConfigError(
            "an 'activation:' block is set but windowing.mode is 'sliding' — "
            "set mode: activation to use it, or remove the block."
        )

    # The constellation's ONE target-amplitude default. egm-signal removed
    # its own (v0.3.0, B22) on the library-defaults rule, and
    # export_bank() requires the argument — so this line is the single
    # place the value is decided, and the only place to change it.
    #
    # Why 1.0 mV: it is the value every bank on disk was produced with,
    # and every example config carries it, so keeping it means no corpus
    # is re-scaled. The number itself is a normalization convention, not a
    # clinical threshold — R-wave anchoring divides by the per-record
    # median QRS peak-to-peak, so the target only sets the units the
    # calibrated corpus lands in. It is deliberately not the 1.5 mV
    # egm-signal used to default to; that copy drifted from this one, and
    # this is the one the data followed.
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
        windowing_mode=windowing_mode,
        window_ms=window_ms,
        hop_ms=hop_ms,
        band_hz=band_hz,
        activation=activation,
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
    # Validated at load so a malformed override fails before the export run.
    bank_id = _validated_id(
        _optional(doc, "data", "bank_id", default=None), field_path="data.bank_id"
    )

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
