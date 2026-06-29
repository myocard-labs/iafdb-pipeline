"""Tests for the CLI YAML config loader.

The CLIs themselves are thin wrappers around argparse + the
orchestrators; the heavy lifting (parsing, validating, resolving
relative paths) lives in ``cli/_config.py`` and is what these tests
exercise.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from myocard_iafdb_pipeline.cli._config import (
    ConfigError,
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
