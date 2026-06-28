# Using myocard-iafdb-pipeline

`myocard-iafdb-pipeline` is the PhysioNet IAFDB producer for the myocard-labs intracardiac-EGM stack. It downloads the dataset, reads the raw WFDB files into typed Python records, calibrates and band-passes the signal, slices out segments, and writes one of three on-disk artifacts:

- `iafdb_bank.h5` — high-voltage / pretraining / classifier-format bank of bipolar EGM segments
- `noise_bank.h5` — low-amplitude bipolar windows used by the synthetic-EGM mixer
- `noise_bank_run_record.json` — JSON sidecar paired with every noise bank; captures extraction provenance for audits + paper methods

Every output validates against a schema in [`myocard-egm-contracts`](https://github.com/myocard-labs/egm-contracts); this package is the IAFDB-specific producer that writes them.

The CLIs are the primary surface. Programmatic use is supported (the orchestrators are importable from `myocard_iafdb_pipeline.export`), but you'll typically drive the pipeline from YAML configs.

## Install

During pre-1.0 iteration:

```bash
pip install "myocard-iafdb-pipeline @ git+https://github.com/myocard-labs/iafdb-pipeline.git"
```

Editable for development:

```bash
git clone https://github.com/myocard-labs/iafdb-pipeline.git
cd iafdb-pipeline
pip install -e ".[dev]"
pre-commit install
```

The runtime deps are `myocard-egm-contracts`, `myocard-egm-data`, `myocard-egm-signal`, `numpy`, `wfdb`, `tqdm`, and `pyyaml`. The egm-* siblings are pinned to git tags during pre-1.0 — drop the direct references once they publish to PyPI.

## CLI reference

The package ships four console scripts. All four are wired in `[project.scripts]`; `pip install` puts them on the PATH.

| Command | Purpose |
|---|---|
| `iafdb-download` | Fetch the IAFDB dataset from PhysioNet. |
| `iafdb-inspect` | Print per-record metadata + channel stats. |
| `iafdb-export-bank` | Build an `iafdb_bank.h5` (or a paired ClassifierBank). |
| `iafdb-export-noise-bank` | Build a `noise_bank.h5` + `noise_bank_run_record.json`. |

The two `export` commands take a positional YAML config plus `--overwrite` and `--no-progress`. The `download` and `inspect` commands keep their argparse flags — they're small enough that a config file would add friction.

### `iafdb-download`

Mirrors the 32 IAFDB records (8 patients × 4 catheter placements) from PhysioNet into a local directory. Default destination is `./data/iafdb/` under the current working directory.

```bash
iafdb-download                                    # all 32 records to ./data/iafdb/
iafdb-download --dest /path/to/iafdb              # custom destination
iafdb-download --records iaf1_afw iaf2_svc        # subset (must be valid record names)
```

The PhysioNet base URL and DOI are pinned in `myocard_iafdb_pipeline.constants`; bump them once when the dataset releases a new version.

### `iafdb-inspect`

Prints metadata (record name, patient, placement, sample rate, duration, channels, units, QRS annotations, comments) and per-channel stats (min, max, mean, std, peak-to-peak in mV) for one record or every downloaded record.

```bash
iafdb-inspect iaf1_afw                            # one record
iafdb-inspect iaf1_afw --data-dir /custom/path    # alternate data dir
iafdb-inspect --all                               # every downloaded record
```

Useful for sanity-checking a fresh download or for confirming that a record has the expected channel set before calibration (channel sets vary per record — see [Architecture > Channel layouts](../project/architecture.md)).

### `iafdb-export-bank`

The headline producer. Builds an `iafdb_bank.h5` of high-voltage (or unfiltered) bipolar EGM segments, with the schema, calibration, threshold, and windowing all driven by a YAML config.

```bash
iafdb-export-bank CONFIG.yaml [--overwrite] [--no-progress]
```

The shipped examples cover the four common scenarios:

- `examples/iafdb_healthy_default.yaml` — Kosiuk-adjusted 0.2 mV absolute threshold (the project default).
- `examples/iafdb_healthy_sanchez.yaml` — Sánchez sinus-rhythm 0.5 mV threshold (the literature anchor).
- `examples/iafdb_pretrain.yaml` — `threshold.mode: none` for unsupervised pretraining banks (every windowed segment kept).
- `examples/iafdb_classifier.yaml` — Same selection as the default, but additionally emits a paired ClassifierBank.h5 with every trace labeled 0 (healthy).
- `examples/iafdb_healthy_with_bank_id.yaml` — Same as the default, but sets an explicit `data.bank_id` instead of auto-deriving it (see [Stable bank IDs](#stable-bank-ids)).

Run one with:

```bash
iafdb-export-bank examples/iafdb_healthy_default.yaml
```

The CLI prints a summary on exit: output paths, number of segments, records processed, records contributing, threshold used, and per-patient / per-channel counts.

### `iafdb-export-noise-bank`

Produces the noise-side artifact: a `noise_bank.h5` plus a paired `noise_bank_run_record.json`. The noise side has different defaults (200 ms windows vs 512 ms, no calibration, the keep-below threshold direction) and a slightly different schema — see [Architecture > Healthy vs noise asymmetry](../project/architecture.md).

```bash
iafdb-export-noise-bank CONFIG.yaml [--overwrite] [--no-progress]
```

Shipped examples:

- `examples/iafdb_noise_percentile.yaml` — 20th-percentile (scale-invariant) keep-below. **Recommended default** for uncalibrated IAFDB input.
- `examples/iafdb_noise_absolute.yaml` — 0.05 mV absolute (Sanders 2003 "electrically silent" tier). Requires calibrated input — not the IAFDB defaults.
- `examples/iafdb_noise_with_bank_id.yaml` — Percentile noise bank with an explicit `data.bank_id` override (recorded on the sidecar).

## Config schema

Both export CLIs share the same YAML conventions:

- The top level is a mapping. Lists or scalars at the top level are rejected.
- Paths can be relative (resolved against the config file's directory) or absolute (pass-through). Empty string or `null` falls back to a CLI-side default.
- Every section has defaults that reproduce the prior argparse defaults; the minimum config is two paths.
- Unknown keys at any nesting level are silently ignored. A typo in an enum value (e.g. `threshold.mode: vibes-based`) fails loudly.

### `iafdb-export-bank` config

```yaml
data:
  data_dir: ../data/iafdb                      # required — IAFDB download root
  output:   ../banks/iafdb_healthy_v1.h5       # required — output .h5 path
  # bank_id: tbank_iafdb_healthy_2026-06-27    # optional — auto-derived (tbank_iafdb_<date>) when omitted

format:
  type: iafdb                                  # 'iafdb' (default) or 'classifier'
  # The two below only matter when type=classifier:
  label_policy: all-healthy                    # required for 'classifier'
  # classifier_output: ../banks/iafdb_healthy_v1.classifier.h5  # default: <output>.classifier.h5

threshold:
  mode:  absolute                              # 'absolute' / 'percentile' / 'none'
  value: 0.2                                   # mV for absolute, 0-100 for percentile, ignored for none

windowing:
  window_ms: 512.0                             # default
  hop_ms:   256.0                              # default
  # band_hz: [30.0, 300.0]                     # default — clinical bipolar EGM band

calibration:
  target_qrs_pp_mv: 1.0                        # default — passed to R-wave anchoring
```

Per-field reference:

| Key | Type | Default | Meaning |
|---|---|---|---|
| `data.data_dir` | path | (required) | Root of the IAFDB download. |
| `data.output` | path | (required) | Output `.h5` path for the iafdb_bank. |
| `data.bank_id` | str (ArtifactId) | auto: `tbank_`/`ptbank_` + `_iafdb_<date>` | Optional explicit stable id. Derived from the threshold role when omitted (`none` → `ptbank_`, else `tbank_`). See [Stable bank IDs](#stable-bank-ids). |
| `format.type` | `iafdb` / `classifier` | `iafdb` | When `classifier`, also writes a labeled ClassifierBank.h5 sibling. |
| `format.label_policy` | str | `all-healthy` | Label policy for the ClassifierBank conversion. Only `all-healthy` is implemented today. |
| `format.classifier_output` | path | `<output>.classifier.h5` | Override the classifier output location. |
| `threshold.mode` | `absolute` / `percentile` / `none` | `absolute` | Healthy-side selection strategy. |
| `threshold.value` | float | `0.2` | mV cutoff for `absolute`, 0-100 percentile for `percentile`, ignored for `none`. |
| `windowing.window_ms` | float | `512.0` | Sliding-window length. |
| `windowing.hop_ms` | float | `256.0` | Stride between adjacent windows. |
| `windowing.band_hz` | `[low, high]` | `[30, 300]` | Bandpass for peak-to-peak measurement. |
| `calibration.target_qrs_pp_mv` | float | `1.0` | Target QRS peak-to-peak the R-wave anchoring normalizes to. |

### `iafdb-export-noise-bank` config

```yaml
data:
  data_dir: ../data/iafdb                      # required
  output:   ../banks/iafdb_noise_v1.h5         # required — output .h5 path
  # bank_id: nbank_iafdb_quiet_2026-06-27      # optional — auto-derived (nbank_iafdb_<date>); recorded on the sidecar
  # run_record_output: ../banks/iafdb_noise_v1_run_record.json
  # ^^^ optional; default is <output>.stem + "_run_record.json" next to the bank.

threshold:
  mode:  percentile                            # 'absolute' or 'percentile' (no 'none' on noise side)
  value: 20.0                                  # required — 0..100 for percentile, mV for absolute

windowing:
  window_ms: 200.0                             # default — shorter than healthy
  hop_ms:   100.0                              # default
  # band_hz: [30.0, 300.0]                     # default

description: >
  Free-form provenance string stamped into the run_record JSON.
```

Per-field reference:

| Key | Type | Default | Meaning |
|---|---|---|---|
| `data.data_dir` | path | (required) | Root of the IAFDB download. |
| `data.output` | path | (required) | Output `.h5` path for the noise_bank. |
| `data.bank_id` | str (ArtifactId) | auto: `nbank_iafdb_<date>` | Optional explicit stable id. Recorded on the run-record sidecar (the slim noise_bank HDF5 carries no id). See [Stable bank IDs](#stable-bank-ids). |
| `data.run_record_output` | path | `<output>_run_record.json` | Override the sidecar location. |
| `threshold.mode` | `absolute` / `percentile` | `percentile` | Noise-side selection strategy. `none` is **rejected** (no pass-through noise bank). |
| `threshold.value` | float | (required) | mV for `absolute`, 0-100 for `percentile`. |
| `windowing.window_ms` | float | `200.0` | Shorter than healthy to give the mixer more variety per record. |
| `windowing.hop_ms` | float | `100.0` | Stride between adjacent windows. |
| `windowing.band_hz` | `[low, high]` | `[30, 300]` | Bandpass for peak-to-peak measurement. |
| `description` | str | `""` | Free-form note stamped into the JSON sidecar. |

## Stable bank IDs

Every bank the producer writes carries a stable cross-artifact ID — an egm-contracts `ArtifactId` (added in egm-contracts v0.5.0 for the cross-artifact-linkage system). The intracardiac-platform phase manifests and the provenance graph key on it.

By default the ID is **derived at write time** from the bank's role:

| Output | Default ID | When |
|---|---|---|
| iafdb_bank (thresholded) | `tbank_iafdb_<date>` | `threshold.mode` is `absolute` or `percentile` |
| iafdb_bank (unfiltered) | `ptbank_iafdb_<date>` | `threshold.mode: none` (pretraining bank) |
| noise_bank | `nbank_iafdb_<date>` | always |

`<date>` is the write-time UTC date (`YYYY-MM-DD`). To override with a hand-curated ID, set `data.bank_id` in the config (or pass `bank_id=` to the orchestrator). An explicit ID is validated against the ArtifactId pattern (`^[a-z]+_[a-z0-9_]+_\d{4}-\d{2}-\d{2}(_v\d+)?$`) and rejected up front if malformed.

For the iafdb_bank the ID is stamped on the HDF5 root attr `bank_id` and propagates onto the paired ClassifierBank's source-bank entry plus every trace. For the noise bank it rides on the `noise_bank_run_record.json` sidecar — the slim `noise_bank` HDF5 schema intentionally carries no ID.

**Same-day collisions.** The auto-derived default has no within-day uniqueness — two thresholded banks exported on the same date both derive `tbank_iafdb_<date>`. Set an explicit `data.bank_id` when you produce more than one bank of the same role per day; the platform's `validate_manifest` (Check A.2) catches a real duplicate across the project at curation time.

## End-to-end walkthroughs

### Producing the project-default healthy bank

```bash
iafdb-download
iafdb-export-bank examples/iafdb_healthy_default.yaml
```

The bank lands at `../banks/iafdb_healthy_v1.h5` (relative to the example config's directory — adjust the YAML if your layout differs). egm-classifier can open it directly via `load_iafdb_bank_as_classifier` with the project's label policy.

### Producing a paired ClassifierBank for the IAFDB diagnostic

```bash
iafdb-export-bank examples/iafdb_classifier.yaml
```

Two files come out: the iafdb_bank.h5 and the labeled ClassifierBank.h5. The ClassifierBank is what egm-classifier loads when running the trained model over IAFDB segments; the iafdb_bank is the producer's primary artifact and stays around as the canonical record.

### Producing a pretraining bank (no threshold)

```bash
iafdb-export-bank examples/iafdb_pretrain.yaml
```

Every windowed segment is kept regardless of amplitude. The on-disk schema records `threshold_mode="none"` so consumers can distinguish a pretraining bank from a thresholded one.

### Producing the synthetic-mixer noise bank

```bash
iafdb-export-noise-bank examples/iafdb_noise_percentile.yaml
```

Two files: `iafdb_noise_v1.h5` (the bank) and `iafdb_noise_v1_run_record.json` (the provenance sidecar). The synthetic-EGM mixer opens the `.h5`; auditing tools and the white paper's methods section open the `.json`.

## Programmatic use

If you'd rather drive the producers from Python (e.g. from a notebook or a meta-runner), the orchestrators are importable:

```python
from pathlib import Path
from myocard_egm_signal import AbsoluteThreshold
from myocard_iafdb_pipeline.export import export_bank
from myocard_iafdb_pipeline.records import iter_records

records = iter_records(Path("data/iafdb"), skip_missing=True)
result = export_bank(
    output_path=Path("out/iafdb_healthy_v1.h5"),
    records=records,
    threshold=AbsoluteThreshold(0.2),
    window_ms=512.0,
    hop_ms=256.0,
    target_qrs_pp_mv=1.0,
    overwrite=True,
)
print(result.n_segments, result.source_records)
```

The threshold strategies, calibration, and segment extractors live in [`myocard-egm-signal`](https://github.com/myocard-labs/egm-signal) — see its `docs/usage.md` for the full primitive reference. The bank readers and writers live in [`myocard-egm-data`](https://github.com/myocard-labs/egm-data); the schemas in [`myocard-egm-contracts`](https://github.com/myocard-labs/egm-contracts).

## Where to read more

- For the design rationale (why CLIs over a single one, why the healthy/noise asymmetry, why the producer doesn't pre-calibrate noise input): `project/architecture.md`.
- For known limitations and planned work: `project/roadmap.md`.
- For the egm-contracts schemas the outputs validate against: `myocard-egm-contracts/docs/schemas/`.
- For the egm-signal primitives the producer pulls in: `myocard-egm-signal/docs/usage.md`.
