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

The two `export` commands take a positional YAML config plus `--overwrite` and `--no-progress`; `iafdb-export-bank` also takes `--report PATH`. The `download` and `inspect` commands keep their argparse flags — they're small enough that a config file would add friction.

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
iafdb-export-bank CONFIG.yaml [--overwrite] [--no-progress] [--report PATH]
```

The shipped examples cover one scenario each — start with the first, which is the annotated reference:

- `examples/iafdb_healthy_default.yaml` — Kosiuk-adjusted 0.2 mV absolute threshold, sliding windows. The annotated reference config; also shows `data.bank_id`, the Sánchez 0.5 mV alternative, and one of the two remaining `calibration.method: r_wave_anchoring` examples (an absolute mV cut is where anchoring is load-bearing). The other five bank examples use `none`.
- `examples/iafdb_healthy_percentile.yaml` — per-record percentile selection, the scale-invariant alternative to an absolute mV cut. Paired with `calibration.method: none`, this is the recommended combination end to end.
- `examples/iafdb_pretrain.yaml` — `threshold.mode: none`; every windowed segment kept.
- `examples/iafdb_classifier_unlabeled.yaml` — emits a paired ClassifierBank with **no ground truth**. The honest shape for IAFDB and the default label policy.
- `examples/iafdb_classifier.yaml` — the same, but labeled `all-healthy`. **Opt-in and unproven** — read the warning at the top of that file before using it.
- `examples/iafdb_activation_windows.yaml` — activation-anchored windowing (`windowing.mode: activation`): one window per detected activation rather than a fixed stride.
- `examples/iafdb_activation_botteron.yaml` — the same mode with the noise-robust detection chain (Botteron envelope, percentile threshold, two-stage refiner).

Run one with:

```bash
iafdb-export-bank examples/iafdb_healthy_default.yaml
```

The CLI prints a summary on exit: output paths, number of segments, records processed, records contributing, threshold used, and per-patient / per-channel counts.

#### `--report` — the audit sidecar

`--report PATH` additionally writes a JSON record of *how each record was treated*. The bank's columns already say what each trace is; what they cannot say is which surface lead calibrated a record, how large its QRS reference actually was, or how many candidate windows were discarded and why.

```bash
iafdb-export-bank examples/iafdb_activation_windows.yaml --report banks/iafdb_activation_report.json
```

Two things it is uniquely able to answer:

- **A record that contributed nothing leaves no trace in the bank** — not even its name in `source_records`. The report lists every record processed, so "did it fail, or was it never reached?" has an answer.
- **A run that yields no bank at all still writes a report.** That is the run whose diagnostics matter most, and the report is the only artifact of it. It carries `bank_written: false` so `bank_file` is never misread as a promise that the file exists.

The bank stores a pointer back, in its `run_record_path` root attr, **relative to the bank's own directory** — so the pair survives being moved or copied together. Without `--report` the attr is absent, which the schema defines as "no run record".

The shape (activation mode; sliding mode omits `activation`, `channels` and the activation totals, and carries `window_ms` / `hop_ms` instead):

```json
{
  "report_version": "1",
  "created_utc": "2026-08-09T18:36:17.045043+00:00",
  "bank_id": "ptbank_iafdb_2026-08-09",
  "bank_file": "iafdb_activation.h5",
  "bank_written": true,
  "source": "iafdb v1.0.0",
  "windowing_mode": "activation",
  "run": {
    "threshold_mode": "none",
    "threshold_value": null,
    "calibration_method": "none",
    "calibration_target_qrs_pp_mv": null,
    "band_hz": [30.0, 300.0],
    "trace_duration_ms": 192.0,
    "activation": {
      "detection_curve": "rectified_derivative",
      "threshold_rule": "median_mad",
      "threshold_c": 1.0,
      "threshold_lam": 10.0,
      "threshold_q": null,
      "min_prominence": null,
      "refractory_ms": 50.0,
      "refine_curve": null,
      "refine_radius_ms": null,
      "position_low": 0.4,
      "position_high": 0.6,
      "position_seed": 20260808,
      "keep_multi_activation": false
    }
  },
  "totals": {
    "records_processed": 4,
    "records_contributing": 4,
    "segments_kept": 4447,
    "activations": 5254,
    "dropped_boundary": 12,
    "dropped_multi_activation": 795,
    "kept_multi_activation": 0,
    "degenerate_channels": 0
  },
  "records": [
    {
      "record_name": "iaf1_svc",
      "patient_id": "iaf1",
      "placement": "svc",
      "calibration": {
        "scalar": null,
        "lead": null,
        "measured_qrs_pp_mv": null,
        "n_beats": null
      },
      "surface_leads": ["II", "V1", "aVF"],
      "segments_kept": 698,
      "channels": [
        {
          "channel": "CS12",
          "activations": 70,
          "kept": 66,
          "dropped_boundary": 1,
          "dropped_multi_activation": 3,
          "kept_multi_activation": 0,
          "degenerate": false
        }
      ]
    }
  ]
}
```

Values above are a real four-record run of `examples/iafdb_activation_windows.yaml`, trimmed to one record and one channel — a full run has an entry per record and five per record's `channels`. That example uses `calibration.method: none`, hence the null calibration block; an `r_wave_anchoring` run fills those four fields and sets `calibration_target_qrs_pp_mv` to the configured target. `surface_leads` is reported either way, since which leads a record carries is a fact about the record rather than about the calibration.

Field notes:

- `run.calibration_method` is the value the config stated, not a constant. It was briefly a hardcoded literal here — a field that looked like a recorded decision next to genuine settings like `threshold_mode`, while no decision existed. It now comes from the required `calibration.method` key.
- **Under `method: none` every calibration field is `null`, not `1.0`.** A reported scalar of `1.0` would be indistinguishable from an anchoring run that happened to measure 1.0. The per-record *yield* accounting is unchanged — it is just as useful uncalibrated.
- `calibration.lead` is the lead the priority walk actually chose, and `surface_leads` is what it had to choose from — IAFDB records carry three of four possible leads, and which three decides the outcome, so the two only make sense together.
- **`activations` is the detector's *output*, not what it found in the signal.** It counts what survived the full chain `preprocess → threshold → select → suppress (refractory) → refine`. Peaks merged or discarded *inside* that chain are counted nowhere in this file — see the note below.
- `channels[].activations` partitions exactly into `kept + dropped_boundary + dropped_multi_activation`, since one window is evaluated per activation. The two drop reasons have different remedies (window length vs detection settings), which is why they are never summed.
- `kept_multi_activation` counts windows kept *because* `activation.keep_multi_activation: true` — a subset of `kept`, not a fourth bucket.
- `degenerate: true` marks a channel egm-signal refused as constant or otherwise unmeasurable. The record continues; the channel contributes nothing.

**What this report cannot tell you: where the detection chain lost peaks.**

The yield funnel begins at the detector's output. `detect_activation_train` computes its candidate peaks internally and returns only the final index array, so the count *before* refractory suppression is not recoverable through the current egm-signal API — it is not merely unreported.

The practical consequence: **`refractory_ms` is the only detection knob with no feedback column.** Every stage after detection has one — a window lost to the record boundary or to a neighbouring activation is counted and attributed. But if the refractory window is set too wide and is merging genuine activations, the only symptom here is a low `activations` count with nothing to compare it against. On IAFDB, where every patient is arrhythmic and activations can be closely spaced, that is a live failure mode rather than a theoretical one.

Until egm-signal exposes the candidate count, the way to probe it is to vary `activation.detection.refractory_ms` across runs and watch `activations` move. Raised with egm-signal; see `project/architecture.md`.

**This JSON is documented but not schema-validated.** The other sidecar in this repo, `noise_bank_run_record.json`, has an egm-contracts schema; this one deliberately does not yet. The methods paper is the first real consumer and may reshape these fields, and freezing a contract around a shape nobody has read is a cost with no buyer. `report_version` exists so the eventual schema has something to migrate from — until then, treat unknown keys as ignorable and do not depend on key order.

### `iafdb-export-noise-bank`

Produces the noise-side artifact: a `noise_bank.h5` plus a paired `noise_bank_run_record.json`. The noise side has different defaults (200 ms windows vs 512 ms, no calibration, the keep-below threshold direction) and a slightly different schema — see [Architecture > Healthy vs noise asymmetry](../project/architecture.md).

```bash
iafdb-export-noise-bank CONFIG.yaml [--overwrite] [--no-progress]
```

Shipped examples:

- `examples/iafdb_noise_percentile.yaml` — 20th-percentile (scale-invariant) keep-below. **Recommended default** for uncalibrated IAFDB input; also shows `data.bank_id` and `data.run_record_output`.
- `examples/iafdb_noise_absolute.yaml` — 0.05 mV absolute (Sanders 2003 "electrically silent" tier). Requires calibrated input — not the IAFDB defaults.

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
  label_policy: unlabeled                      # default; 'all-healthy' opts in to labels (see warning)
  # classifier_output: ../banks/iafdb_healthy_v1.classifier.h5  # default: <output>.classifier.h5

threshold:
  mode:  absolute                              # 'absolute' / 'percentile' / 'none'
  value: 0.2                                   # mV for absolute, 0-100 for percentile, ignored for none

windowing:
  window_ms: 512.0                             # default
  hop_ms:   256.0                              # default
  # band_hz: [30.0, 300.0]                     # default — clinical bipolar EGM band

calibration:
  method: r_wave_anchoring                     # REQUIRED — no default
  target_qrs_pp_mv: 1.0                        # default — passed to R-wave anchoring
```

Per-field reference:

| Key | Type | Default | Meaning |
|---|---|---|---|
| `data.data_dir` | path | (required) | Root of the IAFDB download. |
| `data.output` | path | (required) | Output `.h5` path for the iafdb_bank. |
| `data.bank_id` | str (ArtifactId) | auto: `tbank_`/`ptbank_` + `_iafdb_<date>` | Optional explicit stable id. Derived from the threshold role when omitted (`none` → `ptbank_`, else `tbank_`). See [Stable bank IDs](#stable-bank-ids). |
| `format.type` | `iafdb` / `classifier` | `iafdb` | When `classifier`, also writes a labeled ClassifierBank.h5 sibling. |
| `format.label_policy` | `all-healthy` / `unlabeled` | **`unlabeled`** | Label policy for the ClassifierBank conversion. `unlabeled` (the default) leaves every `label_truth` as `None` with an empty labels dict — the honest policy for IAFDB, which has no per-segment fibrosis truth, and what an eval run turns into an unlabeled (`upred_`) predictions bank. `all-healthy` labels every trace 0 (`{0: "healthy"}`); it is **opt-in and unproven** — the assertion restates the amplitude threshold rather than evidencing it, so prefer `unlabeled` unless you specifically need the labeled shape. |
| `format.classifier_output` | path | `<output>.classifier.h5` | Override the classifier output location. |
| `threshold.mode` | `absolute` / `percentile` / `none` | `absolute` | Healthy-side selection strategy. |
| `threshold.value` | float | `0.2` | mV cutoff for `absolute`, 0-100 percentile for `percentile`, ignored for `none`. |
| `windowing.window_ms` | float | `512.0` | Sliding-window length. |
| `windowing.hop_ms` | float | `256.0` | Stride between adjacent windows. |
| `windowing.band_hz` | `[low, high]` | `[30, 300]` | Bandpass for peak-to-peak measurement. |
| `calibration.method` | str | **required** | `r_wave_anchoring`. **No default, and the block cannot be omitted** — see the note below. `none` is accepted by the config type but rejected at parse time, because `iafdb_bank`'s schema pins `calibration_method` to a single-value enum (CL-153). |
| `calibration.target_qrs_pp_mv` | float | `1.0` | Target QRS peak-to-peak the R-wave anchoring normalizes to. Still defaulted, unlike `method`: it only chooses the units a transform you already opted into lands in, and is inert without one. **This is the only place the value is defaulted** — the programmatic `export_bank()` requires it explicitly, and egm-signal ships no default of its own, so a corpus can't be calibrated to two different scales depending on the entry point. |

#### Why `calibration.method` has no default

Until 2026-08-09 the `calibration:` block held only `target_qrs_pp_mv`, and `export_bank` called R-wave anchoring unconditionally. Deleting the block therefore did **not** disable calibration — it silently accepted 1.0 mV and anchored anyway. Every `iafdb_bank` ever written has had every trace multiplied by a per-record scalar that no config expressed a choice about.

That default is not defensible, because the step is neither small nor settled:

- **Not small.** Measured across all 32 IAFDB records, the per-record scalar spans **0.2696–1.4495 (5.38×)**. Everything amplitude-derived downstream inherits it.
- **Not settled — now ruled.** R-wave anchoring is **non-standard for EGM** and has no prior art for this purpose. It is defensible *only* under a shared-amplifier-gain premise, never as a physiological normalizer, and that premise is unverified on IAFDB. Its free consistency check (four catheter placements of one patient share a surface ECG, so should give one scalar) passes for 6 of 8 patients and **fails for 2 by ~2×** — including `iaf4`, the one patient whose ADC gains are real and identical across all four of its records, which is where the premise is checkable and where it does not hold.

So the method is stated, never inferred. This is a **breaking config change**: a config written before this date fails to load with a message saying what to add. Nothing about the produced banks changed — `r_wave_anchoring` with a 1.0 mV target is what every existing config was already getting.

**`none` is available, and is what most shipped examples now use.** Research recommends raw values with scale-invariant processing; the repo goes one step stricter and requires the choice to be *written down* rather than defaulted, on the view that a step this consequential should not be inherited. `method: none` emits traces in the recorded nominal mV, sets every `calibration_scalar` to `1.0`, and records `calibration_method: "none"` on the bank — which needed `iafdb_bank` **1.4** (egm-contracts v0.6.1, CL-154), since the single-value enum before it left an uncalibrated bank no legal value to record.

Two consequences worth knowing:

- **Nominal mV is comparable *within* a record, not *across* records.** So amplitude selection under `none` should be scale-invariant — `threshold.mode: percentile`. Pairing `none` with `threshold.mode: absolute` is allowed but emits a `ConfigWarning`, because a fixed mV cut then lands at a different tier in every record. Note this pairing is reachable by default, since `threshold.mode` itself defaults to `absolute`.
- **`calibration_target_qrs_pp_mv` carries `+inf` on the bank** — the schema requires a positive number and there is no target. It reads as "not applicable", matching the `peak_to_peak_mv` and `hop_ms` sentinels. Setting a target in the config under `method: none` is rejected, not ignored. The audit report writes `null` instead, because `Infinity` is not valid JSON.

Background: `project/architecture.md` → "Calibration", and `intracardiac-platform/project/investigations/rwave_anchoring_review.md`.

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
- For the end-to-end math of both producer paths (calibration, band-pass, thresholding) and a map of where IAFDB and synthetic data diverge: [`theory.md`](theory.md).
- For known limitations and planned work: `project/roadmap.md`.
- For the egm-contracts schemas the outputs validate against: `myocard-egm-contracts/docs/schemas/`.
- For the egm-signal primitives the producer pulls in: `myocard-egm-signal/docs/usage.md`.
