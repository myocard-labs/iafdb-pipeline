"""Tests for the CLI YAML config loader.

The CLIs themselves are thin wrappers around argparse + the
orchestrators; the heavy lifting (parsing, validating, resolving
relative paths) lives in ``cli/_config.py`` and is what these tests
exercise.
"""

from __future__ import annotations

import types
import warnings
from pathlib import Path

import pytest

from myocard_iafdb_pipeline.cli._config import (
    ConfigError,
    ConfigWarning,
    build_bank_export_config,
    build_noise_bank_export_config,
    load_yaml,
)
from myocard_iafdb_pipeline.cli.export_bank_cmd import _label_policy

# ---------------------------------------------------------------------------
# Bank-export config
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, body: str, name: str = "config.yaml") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_bank_export_minimum_required_fields(tmp_path: Path) -> None:
    """The minimum a config can specify is data.data_dir + data.output;
    every other field has a default that reproduces the prior CLI's
    behavior."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./out.h5
        """,
    )
    cfg = build_bank_export_config(load_yaml(path))
    # Paths get resolved against the YAML's directory.
    assert cfg.data_dir == (tmp_path / "data").resolve()
    assert cfg.output == (tmp_path / "out.h5").resolve()
    # Defaults match the previous argparse defaults.
    assert cfg.output_format == "iafdb"
    assert cfg.label_policy == "all-healthy"
    assert cfg.classifier_output is None
    assert cfg.threshold_mode == "absolute"
    assert cfg.threshold_value == 0.2
    assert cfg.window_ms == 512.0
    assert cfg.hop_ms == 256.0
    assert cfg.target_qrs_pp_mv == 1.0


def test_bank_export_rejects_malformed_bank_id(tmp_path: Path) -> None:
    """A malformed data.bank_id override fails at config-load (fail-fast),
    not after the segment-extraction run (S8-3)."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./out.h5
          bank_id: NOT-a-valid-id
        """,
    )
    with pytest.raises(ConfigError, match=r"data.bank_id"):
        build_bank_export_config(load_yaml(path))


def test_bank_export_accepts_valid_bank_id(tmp_path: Path) -> None:
    """A well-formed stable id passes config-load and reaches the typed config."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./out.h5
          bank_id: tbank_iafdb_healthy_2026-06-25
        """,
    )
    cfg = build_bank_export_config(load_yaml(path))
    assert cfg.bank_id == "tbank_iafdb_healthy_2026-06-25"


def test_bank_export_no_filter_mode_nullable_value(tmp_path: Path) -> None:
    """When threshold.mode is 'none', threshold_value is None; the CLI
    builds a NoThreshold strategy regardless of any value field."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./out.h5
        threshold:
          mode: none
        """,
    )
    cfg = build_bank_export_config(load_yaml(path))
    assert cfg.threshold_mode == "none"
    assert cfg.threshold_value is None


def test_bank_export_classifier_format(tmp_path: Path) -> None:
    """format.type='classifier' is required when the producer should
    also write a ClassifierBank.h5 next to the iafdb bank. The CLI
    side translates label_policy to a label_fn."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./out.h5
        format:
          type: classifier
          label_policy: all-healthy
        """,
    )
    cfg = build_bank_export_config(load_yaml(path))
    assert cfg.output_format == "classifier"
    assert cfg.label_policy == "all-healthy"


def test_bank_export_rejects_unknown_format(tmp_path: Path) -> None:
    """A typo in format.type must fail loudly rather than silently
    falling back to the default."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./out.h5
        format:
          type: pickle-of-cats
        """,
    )
    with pytest.raises(ConfigError, match=r"format\.type"):
        build_bank_export_config(load_yaml(path))


def test_bank_export_rejects_unknown_threshold_mode(tmp_path: Path) -> None:
    """Same defensive validation on threshold.mode."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./out.h5
        threshold:
          mode: vibes-based
        """,
    )
    with pytest.raises(ConfigError, match=r"threshold\.mode"):
        build_bank_export_config(load_yaml(path))


def test_bank_export_missing_required_path_raises(tmp_path: Path) -> None:
    """data.data_dir and data.output are both required; missing one
    is a config error, not a silently-empty-result run."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          output: ./out.h5
        """,
    )
    with pytest.raises(ConfigError, match="data_dir"):
        build_bank_export_config(load_yaml(path))


def test_bank_export_absolute_paths_pass_through(tmp_path: Path) -> None:
    """Absolute paths in the YAML must NOT be re-resolved against the
    config file's directory."""
    abs_out = tmp_path / "absolute" / "out.h5"
    abs_data = tmp_path / "absolute" / "data"
    path = _write_yaml(
        tmp_path,
        f"""
        data:
          data_dir: {abs_data}
          output: {abs_out}
        """,
    )
    cfg = build_bank_export_config(load_yaml(path))
    assert cfg.data_dir == abs_data
    assert cfg.output == abs_out


# ---------------------------------------------------------------------------
# Noise-bank-export config
# ---------------------------------------------------------------------------


def test_noise_export_minimum_required_fields(tmp_path: Path) -> None:
    """threshold.value is REQUIRED for noise extraction (unlike the
    healthy side; there's no 'none' analog on the noise side). data
    paths required as usual."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./noise.h5
        threshold:
          mode: percentile
          value: 20.0
        """,
    )
    cfg = build_noise_bank_export_config(load_yaml(path))
    assert cfg.data_dir == (tmp_path / "data").resolve()
    assert cfg.output == (tmp_path / "noise.h5").resolve()
    assert cfg.threshold_mode == "percentile"
    assert cfg.threshold_value == 20.0
    # Defaults match the previous argparse defaults.
    assert cfg.window_ms == 200.0
    assert cfg.hop_ms == 100.0
    assert cfg.description == ""


def test_noise_export_run_record_output_optional(tmp_path: Path) -> None:
    """run_record_output is optional; when omitted, the producer
    derives it from the bank path (`<stem>_run_record.json`)."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./noise.h5
        threshold:
          mode: percentile
          value: 20.0
        """,
    )
    cfg = build_noise_bank_export_config(load_yaml(path))
    assert cfg.run_record_output is None


def test_noise_export_rejects_none_threshold_mode(tmp_path: Path) -> None:
    """Unlike the healthy side, noise extraction has no 'none' mode —
    a pass-through noise bank is not a meaningful operation. The
    config layer rejects it explicitly so the CLI doesn't crash on
    the egm-signal strategy builder."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./noise.h5
        threshold:
          mode: none
          value: 0.0
        """,
    )
    with pytest.raises(ConfigError, match=r"threshold\.mode"):
        build_noise_bank_export_config(load_yaml(path))


def test_noise_export_requires_threshold_value(tmp_path: Path) -> None:
    """threshold.value is required for noise extraction — every
    selection strategy needs a cutoff."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./noise.h5
        threshold:
          mode: percentile
        """,
    )
    with pytest.raises(ConfigError, match=r"threshold\.value"):
        build_noise_bank_export_config(load_yaml(path))


# ---------------------------------------------------------------------------
# Generic loader behavior
# ---------------------------------------------------------------------------


def test_load_yaml_missing_file_raises(tmp_path: Path) -> None:
    """A bad path should fail with a clear error message rather than
    a stack trace from the YAML library."""
    with pytest.raises(ConfigError, match="not found"):
        load_yaml(tmp_path / "nope.yaml")


def test_load_yaml_non_mapping_top_level_raises(tmp_path: Path) -> None:
    """A YAML file whose top level is a list (or scalar) isn't a
    config — raise rather than walk into a non-dict node."""
    path = _write_yaml(tmp_path, "- one\n- two\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_yaml(path)


# ---------------------------------------------------------------------------
# Label policy (CLI name -> label_fn)
# ---------------------------------------------------------------------------


def _fake_bank(n: int) -> object:
    """A stand-in IafdbBank exposing just the ``traces.signal`` a label_fn reads."""
    return types.SimpleNamespace(traces=types.SimpleNamespace(signal=[0.0] * n))


def test_label_policy_all_healthy_labels_every_trace_zero() -> None:
    """The 'all-healthy' policy labels all N traces 0 with a {0: 'healthy'} map."""
    label_fn = _label_policy("all-healthy")
    labels, labels_dict = label_fn(_fake_bank(3))
    assert labels.tolist() == [0, 0, 0]
    assert labels_dict == {0: "healthy"}


def test_label_policy_unlabeled_returns_none() -> None:
    """The 'unlabeled' policy's label_fn returns None — egm-data's converter
    maps that to every label_truth=None (IAFDB has no per-segment truth)."""
    label_fn = _label_policy("unlabeled")
    assert label_fn(_fake_bank(3)) is None


def test_label_policy_unknown_raises() -> None:
    """An unrecognised policy name is a config error, not a silent default."""
    with pytest.raises(ConfigError, match="label_policy"):
        _label_policy("vibes-based")


# ---------------------------------------------------------------------------
# Activation windowing mode (IAF1 / S2)
# ---------------------------------------------------------------------------


def test_windowing_mode_defaults_to_sliding_with_no_activation_block(tmp_path: Path) -> None:
    """Adding the mode must not change what an existing config means.

    Every config written before IAF1 omits `windowing.mode`, and there are
    banks on disk produced from them — so the default has to keep meaning
    exactly what it meant, with no activation config attached."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./out.h5
        """,
    )
    cfg = build_bank_export_config(load_yaml(path))
    assert cfg.windowing_mode == "sliding"
    assert cfg.activation is None
    assert cfg.window_ms == 512.0
    assert cfg.hop_ms == 256.0


def test_activation_mode_parses_the_full_block(tmp_path: Path) -> None:
    """Every activation key round-trips into the typed config."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./out.h5
        windowing:
          mode: activation
        activation:
          trace_duration_ms: 192.0
          detection:
            curve: botteron_envelope
            botteron_band_hz: [40.0, 250.0]
            botteron_lowpass_hz: 20.0
            threshold:
              rule: percentile
              q: 99.0
            min_prominence: 0.05
            refractory_ms: 60.0
            refine:
              curve: rectified_derivative
              radius_ms: 8.0
          position:
            low: 0.35
            high: 0.65
            seed: 7
        """,
    )
    cfg = build_bank_export_config(load_yaml(path))
    assert cfg.windowing_mode == "activation"
    act = cfg.activation
    assert act is not None
    assert act.trace_duration_ms == 192.0
    assert act.detection.curve == "botteron_envelope"
    assert act.detection.botteron_band_hz == (40.0, 250.0)
    assert act.detection.botteron_lowpass_hz == 20.0
    assert act.detection.threshold_rule == "percentile"
    assert act.detection.threshold_q == 99.0
    assert act.detection.threshold_c is None
    assert act.detection.min_prominence == 0.05
    assert act.detection.refractory_ms == 60.0
    assert act.detection.refine_curve == "rectified_derivative"
    assert act.detection.refine_radius_ms == 8.0
    assert act.position.low == 0.35
    assert act.position.high == 0.65
    assert act.position.seed == 7


def test_activation_mode_defaults_are_usable(tmp_path: Path) -> None:
    """`mode: activation` alone must produce a runnable config — the
    defaults are this producer's policy values (egm-signal ships none),
    which study §8.1 later replaces."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./out.h5
        windowing:
          mode: activation
        """,
    )
    act = build_bank_export_config(load_yaml(path)).activation
    assert act is not None
    assert act.trace_duration_ms == 192.0
    assert act.detection.curve == "rectified_derivative"
    assert act.detection.threshold_rule == "median_mad"
    assert (act.detection.threshold_c, act.detection.threshold_lam) == (1.0, 10.0)
    assert act.detection.refine_curve is None
    # Central band, per the flutter position-bias ruling.
    assert (act.position.low, act.position.high) == (0.4, 0.6)


def test_off_grid_trace_duration_warns_but_is_honoured(tmp_path: Path) -> None:
    """An off-grid trace length is a warning, not an error.

    The 64-sample multiple is *egm-classifier's* constraint — its current
    MobileViT-1D stem — not this producer's, and it would evaporate if that
    model changed. Someone using this tool without that classifier is
    entitled to the length they asked for, so the config is honoured
    exactly and nothing is padded or clipped here."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./out.h5
        windowing:
          mode: activation
        activation:
          trace_duration_ms: 200.0
        """,
    )
    with pytest.warns(ConfigWarning, match="not a multiple of 64"):
        cfg = build_bank_export_config(load_yaml(path))
    act = cfg.activation
    assert act is not None
    # Honoured verbatim — no rounding to the nearest grid value.
    assert act.trace_duration_ms == 200.0


def test_on_grid_trace_duration_is_silent(tmp_path: Path) -> None:
    """The common case must not warn, or the warning becomes noise."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./out.h5
        windowing:
          mode: activation
        activation:
          trace_duration_ms: 192.0
        """,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConfigWarning)
        cfg = build_bank_export_config(load_yaml(path))
    assert cfg.activation is not None


def test_sliding_only_keys_are_rejected_under_activation_mode(tmp_path: Path) -> None:
    """A key belonging to the other mode is a mistake, not a no-op.

    Silently ignoring `window_ms` would leave the config reading "windows
    are 512 ms" to whoever wrote it, while the bank used 192."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./out.h5
        windowing:
          mode: activation
          window_ms: 512.0
        """,
    )
    with pytest.raises(ConfigError, match="no meaning under mode='activation'"):
        build_bank_export_config(load_yaml(path))


def test_activation_block_without_activation_mode_is_rejected(tmp_path: Path) -> None:
    """The mirror case: a fully-specified activation block that the run
    would ignore, because the mode was never switched over."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./out.h5
        activation:
          trace_duration_ms: 192.0
        """,
    )
    with pytest.raises(ConfigError, match=r"windowing\.mode is 'sliding'"):
        build_bank_export_config(load_yaml(path))


def test_botteron_keys_rejected_for_other_curves(tmp_path: Path) -> None:
    """Band/low-pass describe the Botteron envelope only."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./out.h5
        windowing:
          mode: activation
        activation:
          detection:
            curve: teager_kaiser
            botteron_lowpass_hz: 20.0
        """,
    )
    with pytest.raises(ConfigError, match="botteron_"):
        build_bank_export_config(load_yaml(path))


# Each case is a full document: the earlier parametrize built YAML by
# indenting fragments, which silently nested `activation:` under
# `windowing:` and tested the wrong error. Explicit beats clever here.
_INVALID_ACTIVATION_CONFIGS = [
    pytest.param(
        """
        windowing:
          mode: nonsense
        """,
        "windowing.mode must be",
        id="unknown-mode",
    ),
    pytest.param(
        """
        windowing:
          mode: activation
        activation:
          detection:
            curve: sobel
        """,
        "curve must be one of",
        id="unknown-detection-curve",
    ),
    pytest.param(
        """
        windowing:
          mode: activation
        activation:
          detection:
            threshold:
              rule: otsu
        """,
        "threshold.rule must be",
        id="unknown-threshold-rule",
    ),
    pytest.param(
        """
        windowing:
          mode: activation
        activation:
          detection:
            refractory_ms: 0
        """,
        "refractory_ms must be positive",
        id="non-positive-refractory",
    ),
    pytest.param(
        """
        windowing:
          mode: activation
        activation:
          position:
            low: 0.7
            high: 0.3
        """,
        "0 <= low <= high <= 1",
        id="inverted-position-band",
    ),
    pytest.param(
        """
        windowing:
          mode: activation
        activation:
          position:
            high: 1.4
        """,
        "0 <= low <= high <= 1",
        id="position-out-of-range",
    ),
    pytest.param(
        """
        windowing:
          mode: activation
        activation:
          detection:
            threshold:
              rule: percentile
              q: 140
        """,
        "percentile in \\[0, 100\\]",
        id="percentile-out-of-range",
    ),
    pytest.param(
        """
        windowing:
          mode: activation
        activation:
          detection:
            refine:
              curve: rectified_derivative
              radius_ms: -1
        """,
        "radius_ms must be positive",
        id="non-positive-refine-radius",
    ),
]


@pytest.mark.parametrize(("block", "match"), _INVALID_ACTIVATION_CONFIGS)
def test_activation_config_rejects_invalid_values(tmp_path: Path, block: str, match: str) -> None:
    """Each invalid value fails at config-load, before any record is read —
    the same fail-fast boundary the bank_id override already uses."""
    path = _write_yaml(
        tmp_path,
        """
        data:
          data_dir: ./data
          output: ./out.h5
        """
        + block,
    )
    with pytest.raises(ConfigError, match=match):
        build_bank_export_config(load_yaml(path))


def test_shipped_activation_example_config_loads(tmp_path: Path) -> None:
    """The annotated example must actually parse — examples that drift out
    of sync with the loader are worse than no examples."""
    example = Path(__file__).resolve().parents[1] / "examples" / "iafdb_activation_windows.yaml"
    cfg = build_bank_export_config(load_yaml(example))
    assert cfg.windowing_mode == "activation"
    assert cfg.activation is not None
    assert cfg.activation.trace_duration_ms == 192.0


# ---------------------------------------------------------------------------
# The shipped examples
# ---------------------------------------------------------------------------

_EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"

# Keys a config can set, and which builder reads them. The examples exist to
# demonstrate the surface, so every key here must appear in at least one of
# them — otherwise an option ships with no worked example and the only way to
# discover it is reading the loader.
_BANK_KEYS = [
    "data.bank_id",
    "format.type",
    "format.label_policy",
    "format.classifier_output",
    "threshold.mode",
    "threshold.value",
    "windowing.mode",
    "windowing.window_ms",
    "windowing.hop_ms",
    "windowing.band_hz",
    "calibration.target_qrs_pp_mv",
    "activation.trace_duration_ms",
    "activation.keep_multi_activation",
    "activation.detection.curve",
    "activation.detection.botteron_band_hz",
    "activation.detection.botteron_lowpass_hz",
    "activation.detection.threshold.rule",
    "activation.detection.min_prominence",
    "activation.detection.refractory_ms",
    "activation.detection.refine.curve",
    "activation.detection.refine.radius_ms",
    "activation.position.low",
    "activation.position.high",
]
_NOISE_KEYS = [
    "data.bank_id",
    "data.run_record_output",
    "threshold.mode",
    "threshold.value",
    "windowing.window_ms",
    "windowing.hop_ms",
    "description",
]
# Enum values that must each be demonstrated somewhere, not just the key.
_ENUM_COVERAGE = {
    "format.type": {"iafdb", "classifier"},
    "format.label_policy": {"all-healthy", "unlabeled"},
    "threshold.mode": {"absolute", "percentile", "none"},
    "windowing.mode": {"sliding", "activation"},
    "activation.detection.curve": {"rectified_derivative", "botteron_envelope"},
    "activation.detection.threshold.rule": {"median_mad", "percentile"},
}


def _dig(doc: object, dotted: str) -> object:
    node = doc
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _example_docs() -> dict[str, dict[str, object]]:
    return {p.name: load_yaml(p) for p in sorted(_EXAMPLES_DIR.glob("*.yaml"))}


def test_every_shipped_example_loads() -> None:
    """Each example must parse through the builder it is written for.

    An example that no longer loads is worse than no example: it is a
    documented recipe that fails the moment someone follows it."""
    for name, doc in _example_docs().items():
        builder = build_noise_bank_export_config if "noise" in name else build_bank_export_config
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConfigWarning)
            builder(doc)  # raises ConfigError on any drift


@pytest.mark.parametrize("key", _BANK_KEYS)
def test_every_bank_config_key_has_a_worked_example(key: str) -> None:
    docs = {n: d for n, d in _example_docs().items() if "noise" not in n}
    assert any(_dig(d, key) is not None for d in docs.values()), (
        f"no example sets {key!r} — every documented option needs one worked example"
    )


@pytest.mark.parametrize("key", _NOISE_KEYS)
def test_every_noise_config_key_has_a_worked_example(key: str) -> None:
    docs = {n: d for n, d in _example_docs().items() if "noise" in n}
    assert any(_dig(d, key) is not None for d in docs.values()), f"no noise example sets {key!r}"


@pytest.mark.parametrize(("key", "expected"), sorted(_ENUM_COVERAGE.items()))
def test_every_enum_value_is_demonstrated(key: str, expected: set[str]) -> None:
    """Covering the key is not enough — each alternative needs showing,
    since the whole point of an enum is that the alternatives differ."""
    seen = {_dig(d, key) for d in _example_docs().values()}
    missing = expected - seen
    assert not missing, f"no example demonstrates {key}={sorted(missing)}"
