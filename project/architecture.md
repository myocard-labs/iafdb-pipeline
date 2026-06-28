# iafdb-pipeline — architecture and design rationale

Internal design doc for people building / maintaining the producer. The
public surface is documented in `docs/usage.md`; this doc explains the
why.

## Where the package sits

```
┌────────────────────────┐  ┌───────────────────────┐  ┌────────────────────────┐
│ myocard-egm-contracts  │  │  myocard-egm-signal   │  │   myocard-egm-data     │
│   (format schemas)     │  │  (numpy + scipy DSP)  │  │  (HDF5 I/O, datasets)  │
└──────────┬─────────────┘  └──────────┬────────────┘  └──────────┬─────────────┘
           │                           │                          │
           │                           │                          │
           │ schema-version contract   │ extract_*_segments       │ write_iafdb_bank
           │ via egm-data writers      │ AbsoluteThreshold        │ write_noise_bank
           │                           │ PercentileThreshold      │ write_noise_bank_run_record
           │                           │ NoThreshold              │ iafdb_bank_to_classifier
           │                           │ AbsoluteQuietThreshold   │ write_classifier_bank
           │                           │ PercentileQuietThreshold │
           │                           │ RWaveAnchoring           │
           │                           │ compute_calibration      │
           │                           │ bandpass                 │
           │                           │                          │
           └─────────────┬─────────────┴────────────┬─────────────┘
                         │                          │
                         ▼                          ▼
                ┌────────────────────────────────────────┐
                │         iafdb-pipeline                 │
                │           (this repo)                  │
                │                                        │
                │  IAFDBRecord            — WFDB loader  │
                │  constants              — channels/IDs │
                │  export.bank_export     — healthy bank │
                │  export.noise_export    — noise bank   │
                │  cli/*                  — 4 CLIs       │
                │  cli/_config            — YAML loader  │
                └────────────────┬───────────────────────┘
                                 │
                                 ▼
                         on-disk artifacts:
                           iafdb_bank.h5
                           classifier_bank.h5 (optional)
                           noise_bank.h5
                           noise_bank_run_record.json
                                 │
                                 ▼
                 ┌─────────────────────────────────────┐
                 │   downstream consumers              │
                 │     - egm-classifier (training)     │
                 │     - synthetic-egm-pipeline        │
                 │       (mixer, noise side)           │
                 │     - egm-viewer (Inspection tab)   │
                 └─────────────────────────────────────┘
```

iafdb-pipeline is a **producer**, not a library. It pulls every shared
primitive — filters, thresholds, calibration, the segment extractors —
from `egm-signal`. It owns one dataset-specific record type
(`IAFDBRecord`), the WFDB loader, the IAFDB constants (record names,
channel layout, sampling rate), the producer orchestrators
(`bank_export`, `noise_export`), and the four CLIs that drive them.

The package has zero h5py imports — every HDF5 writer is owned by
egm-data. Every Pydantic model the producer assembles is owned by
egm-contracts. The producer's job is to walk records, slice segments,
build Pydantic objects, and hand them to a writer.

## Folder layout

```
src/myocard_iafdb_pipeline/
├── constants.py            ← IAFDB-specific: patients, placements,
│                              channels, PhysioNet URL, fs
├── download.py             ← PhysioNet mirror
├── ids.py                  ← stable cross-artifact id derive + validate
├── records.py              ← IAFDBRecord dataclass + WFDB loader
├── export/
│   ├── bank_export.py      ← export_bank(...) orchestrator
│   └── noise_export.py     ← export_noise_bank(...) orchestrator
└── cli/
    ├── _config.py          ← YAML loader + typed config dataclasses
    ├── download_cmd.py     ← iafdb-download
    ├── inspect_cmd.py      ← iafdb-inspect
    ├── export_bank_cmd.py  ← iafdb-export-bank
    └── export_noise_bank_cmd.py  ← iafdb-export-noise-bank
```

Convention: orchestrators live under `export/` and are importable from
Python; CLIs under `cli/` are thin argparse + YAML wrappers around the
orchestrators. The CLIs hold no business logic; they parse the config,
build the strategy objects, and call the orchestrator.

## Why four CLIs instead of one

We considered a single `iafdb` entry point with subcommands
(`iafdb download`, `iafdb inspect`, `iafdb export-bank`, `iafdb
export-noise-bank`). Rejected for two reasons:

1. **Independent install surfaces.** The four commands have different
   audiences. `download` and `inspect` are interactive (a researcher
   pokes at a record). `export-bank` and `export-noise-bank` are
   batch / pipeline (called from a Makefile or a meta-runner). Four
   separate console scripts make it easy to put just the two
   producers on a CI image without dragging the interactive commands
   along.
2. **Argparse is simpler than a subcommand dispatcher.** The
   subcommand approach adds a layer of argparse plumbing for no
   functional benefit. The four scripts share `_config.py`; that's
   the right shared surface.

## Why YAML configs (not argparse-only)

The two producer commands had eight to twelve substantive parameters
each before the refactor. Long argparse invocations are hard to commit
to a repo, hard to diff, and easy to misorder. The other repos in the
stack (egm-classifier already, soon egm-features) also use YAML
configs, so the project-wide convention is one config format.

The CLI surface for the two export commands is therefore:

```
iafdb-export-bank CONFIG.yaml [--overwrite] [--no-progress]
iafdb-export-noise-bank CONFIG.yaml [--overwrite] [--no-progress]
```

The two flags that survived argparse are run-time decisions, not
algorithmic parameters: `--overwrite` answers "should we replace the
existing output?" and `--no-progress` answers "should the progress bar
print?". Neither is something you'd want to bake into a config.

`download` and `inspect` keep their argparse surface — they have one
or two arguments each, a YAML config would be more friction than
help. The cutoff was "more than ~5 substantive parameters → YAML."

## Why the path-resolution convention

Paths in YAML are resolved against the **config file's directory**, not
against the current working directory. So:

```yaml
data:
  data_dir: ../data/iafdb        # resolved against config's dir
  output:   ../banks/iafdb.h5    # resolved against config's dir
```

works the same whether you run `iafdb-export-bank examples/foo.yaml`
from the repo root or from `examples/`. Absolute paths pass through
untouched. Empty string / `null` falls back to a CLI-side default.

The alternative — resolving against CWD — was rejected: the CWD when
the script runs is rarely the directory the config was written in, so
relative paths in the YAML would be brittle. Resolving against the
config dir matches egm-classifier and is the pattern Python's
`yaml-config-driven CLIs` tend to land on.

## Why two parallel export paths (healthy vs noise)

The two producer paths look superficially similar (walk records,
extract segments, write HDF5) but have important asymmetries that
make a unified extractor a bad fit:

1. **Schema differs.** `iafdb_bank` carries per-trace calibration
   provenance (R-wave anchored scalar, target QRS p-p, lead used);
   `noise_bank` is intentionally minimal (signal, source_record,
   source_channel) because the mixer only needs the signal and the
   per-trace source identifiers. Carrying calibration columns in
   the noise bank would imply the noise traces are in mV, which
   they're not.

2. **Calibration differs.** The healthy producer calibrates each
   record before windowing (so the absolute mV threshold means the
   same thing in every record). The noise producer does NOT
   calibrate — the default percentile strategy is scale-invariant,
   and absolute mV strategies on the noise side require the
   *caller* to feed a calibrated source. The run record records
   `calibration_method="none"` to be honest about this.

3. **Threshold direction differs.** Healthy keeps p-p above a
   threshold; noise keeps p-p below. egm-signal's two parallel
   strategy hierarchies enforce this at the type level — see
   `egm-signal/project/architecture.md` for the rationale.

4. **Window length differs.** Healthy uses 512 ms windows (matches
   the classifier's T=512 input). Noise uses 200 ms windows because
   the mixer tiles noise samples to match the clean trace's length;
   shorter source windows give more variety per record without
   inflating the bank's row count.

5. **Sidecar differs.** Noise extraction has a paired
   `noise_bank_run_record.json` provenance sidecar (calibration
   scheme, threshold strategy, filter band, windowing, optional
   per-trace audit). Healthy extraction doesn't — the iafdb_bank
   schema carries the per-trace provenance directly in HDF5 columns.

Combining these into one orchestrator with a stack of `if noise else
healthy` branches would be net negative on readability. Two
focused orchestrators sharing a small set of helpers
(`_emit_one_record`, `_threshold_provenance`) is the right shape.

## Why the producer doesn't pre-calibrate noise input

IAFDB v1.0.0 is uncalibrated. The healthy producer applies R-wave
anchoring before windowing because absolute mV thresholds (Sánchez 0.5
mV, Kosiuk-adjusted 0.2 mV) need calibrated input. The noise
producer doesn't, for two reasons:

1. **The recommended noise strategy is percentile-based**, which is
   scale-invariant. Calibration would be wasted work and would
   introduce R-wave-detection failure modes on records that don't
   have surface ECG annotations.

2. **A calibrated noise bank implies the threshold is in mV.** If we
   calibrated and then applied `PercentileQuietThreshold(20)`, the
   threshold semantics are still percentile (not mV). Calibrating
   without changing the user-facing semantics is misleading.

A future revision may add an *opt-in* calibration step for users who
want the absolute-mV threshold to mean the same thing across records.
Until then, the run record records `calibration_method="none"`, and
the absolute-threshold example explicitly notes that the input
should be calibrated upstream.

## Why R-wave anchoring (and not fixed gain)

IAFDB's WFDB headers carry ADC gains, but those gains differ across
records and reflect the acquisition system, not the physiology. R-wave
anchoring normalizes by the per-record median QRS peak-to-peak on a
chosen surface ECG lead, so the bipolar EGM amplitudes become
comparable across records.

Three options were on the table:

- **Fixed gain from the WFDB header.** Rejected: the header gain is
  acquisition-system-specific, and using it as the calibration scalar
  produces banks where 0.5 mV in record A and 0.5 mV in record B
  represent different physiological amplitudes.
- **Per-record peak normalization** (divide by per-record max p-p).
  Rejected: noisy outliers dominate the max; the resulting calibration
  is unstable record-to-record.
- **R-wave anchoring on the surface ECG.** The chosen design. The
  surface ECG R wave is a physiological reference common across
  records; anchoring its median peak-to-peak to a chosen target
  (default 1.0 mV) gives per-record scalars that bring the bipolar
  EGM amplitudes into comparable units. The lead selection logic tries
  a priority list (II → I → V1 → aVF → aVL → III → aVR → V5) and
  records the actually-used lead in the per-trace metadata.

The implementation lives in egm-signal (`RWaveAnchoring`,
`compute_calibration`). The producer just calls it.

## Channel layouts

IAFDB records do NOT share a uniform channel set. Every record has the
five bipolar pairs (CS12, CS34, CS56, CS78, CS90), but the surface
ECG complement varies — some records have II + V1 + aVF, others have
I + III + aVL, etc. The producer handles this by:

- Hard-coding the bipolar set in `constants.BIPOLAR_CHANNELS` (always
  present, in distal-to-proximal order).
- Picking the surface lead at calibration time from a priority list,
  via `RWaveAnchoring`'s `preferred_leads` kwarg.
- Skipping a record (with a logged warning) only if zero usable
  surface leads are present.

This shows up as a memory note: "IAFDB has no session timestamps —
each .dat starts at sample 0; no cross-record clock; channel set
varies per record." All three of those constraints inform the design
above.

## Why a frozen `IAFDBRecord` dataclass

`records.IAFDBRecord` is a `@dataclass(frozen=True)` exposing `name`,
`patient`, `placement`, `fs`, `signal`, `channel_names`, `units`,
`comments`, and `qrs_samples`. It satisfies the egm-signal `Record`
Protocol structurally — no inheritance, no isinstance registration.

Frozen-ness matters: the producer iterates records, mutates state
(segment lists, threshold accumulators) outside the record, and never
needs to write back into a record's signal array. Making the dataclass
frozen prevents accidental in-place edits during calibration and
keeps the record cacheable.

The `channel_index(name)` lookup is implemented as a method on the
dataclass to satisfy the Protocol. It does a linear scan over
`channel_names` — fine for the ~5-9 channels IAFDB records expose;
the next dataset's record type (which may carry 64-128 channels) can
override with a dict lookup without changing the Protocol.

## Provenance: what gets written where

Two layers of provenance per bank:

1. **Per-trace columns in the HDF5.** Bank-resident. The
   iafdb_bank schema carries `patient_id`, `record_name`,
   `channel_name`, `start_sample`, `calibration_scalar`,
   `calibration_target_mv`, `peak_to_peak_mv`. The noise_bank schema
   carries `source_record`, `source_channel` (intentionally less —
   no calibration columns).

2. **Run-record JSON for the noise bank.** Sidecar. Carries the full
   producer config (calibration scheme, threshold strategy, filter
   band, windowing, source records) plus optional per-trace audit
   arrays (`patient_id`, `start_sample`, `peak_to_peak_mv`,
   `calibration_scalar` — even when calibration is "none", these
   columns are present and filled with sentinel values).

The healthy bank doesn't have a sidecar today — the iafdb_bank
schema carries everything inline. If the producer grows enough
configuration knobs that the schema can't keep up, a healthy-side
sidecar joins the noise-side one. The boundary will be re-examined
when that happens.

## Stable cross-artifact IDs

Since v0.3.0 (egm-contracts v0.5.0 / egm-data v0.4.0) every bank the
producer writes carries a stable cross-artifact ID — an egm-contracts
`ArtifactId` that the intracardiac-platform phase manifests and the
future provenance graph key on. The producer is the natural place to
stamp it: the ID has to be assigned exactly once, at the moment the
artifact is created, and never reused.

`ids.py` owns the small amount of logic:

- **Role-based derivation.** The default ID encodes the bank's role in
  its prefix: `tbank_` for a thresholded training bank, `ptbank_` for an
  unfiltered (`threshold=none`) pretraining bank, `nbank_` for a noise
  bank. The descriptor segment is the dataset tag (`iafdb`) and the date
  is the write-time UTC date — e.g. `tbank_iafdb_2026-06-27`. The role
  follows the threshold because that's the only thing at the producer
  level that distinguishes a training bank from a pretraining bank;
  nothing else about the run changes it.
- **Override.** A caller (or the `data.bank_id` config key) can supply
  an explicit ID, which is validated and used verbatim. This is the
  curation path — when a human wants a descriptive, hand-chosen ID.
- **Single-sourced pattern.** The ID *pattern* lives once in
  egm-contracts (`common.ArtifactId`); `ids.py` only composes candidate
  strings and validates them. The validation idiom (construct
  `common.ArtifactId`, catch `ValidationError`, re-raise as `ValueError`)
  mirrors how egm-data validates ClassifierBank IDs, so an explicit
  override fails fast at the producer boundary rather than deep inside a
  writer.

Where the ID is written differs by bank, matching the schema asymmetry
above:

- **iafdb_bank:** stamped on the HDF5 root attr `bank_id` (egm-contracts
  `iafdb_bank` 1.2). When the producer also emits a ClassifierBank, the
  egm-data converter keys the source-bank entry and every trace by this
  same ID (it *requires* the source bank to carry one).
- **noise_bank:** the slim `noise_bank` HDF5 schema was deliberately
  *not* given an ID field (it carries only what the mixer needs). The ID
  rides on the `noise_bank_run_record.json` sidecar instead
  (egm-contracts `noise_bank_run_record` 1.1).

**Known limitation — same-day uniqueness.** The auto-derived default is
`{role}_iafdb_<date>` with no within-day disambiguator, so two banks of
the same role exported on the same date derive the same ID. The override
is the escape hatch for multiple-banks-per-day workflows, and
`intracardiac-platform/scripts/validate_manifest.py` (Check A.2) catches
a real duplicate across the project at curation time. If the producer
ever routinely emits several same-role banks per day, fold a descriptor
(e.g. the threshold) or a uniqueness suffix into the default — deferred
until that's a real workflow.

## How a downstream consumer reads the output

```python
from myocard_egm_data.banks import load_iafdb_bank_as_classifier

# A consumer-side label_fn — the producer doesn't ship one because
# label semantics are downstream policy.
def all_healthy(bank):
    n = len(bank.traces.signal)
    return np.zeros(n, dtype=np.int64), {0: "healthy"}

cb = load_iafdb_bank_as_classifier("banks/iafdb_healthy_v1.h5", label_fn=all_healthy)
print(cb.n_traces, cb.labels)
```

The classifier-format export shortcuts this — when the producer is
invoked with `format.type: classifier`, it does the conversion at
write time and emits the ClassifierBank.h5 as a sibling file. The
label policy is still consumer-side; the producer just runs it
internally with the `format.label_policy` from the config.

## Testing strategy

Two layers:

1. **Unit tests** against synthetic in-memory records exercise the
   producer modules without touching disk. Calibration, threshold
   strategies, and segment extraction are tested in egm-signal;
   here we test the IAFDB-specific glue (WFDB loader, constants,
   the orchestrator's per-record loop, the CLI's YAML config
   loader). The CLI config tests live in `tests/test_cli_config.py`
   and exercise every required-field / default / nullable case.
2. **End-to-end smoke** is via the integration tests that round-trip
   a fixture record through the producer and then back through
   egm-contracts' file-level validator. The bank that comes out must
   pass the schema check.

A real PhysioNet download is **not** part of the test suite — too
slow and network-dependent. The download module is tested via the
CLI's dry-run path.

## Version coordination

The producer pins exact egm-contracts, egm-data, and egm-signal git
tags. Bumping any of those means bumping the iafdb-pipeline pin in
`pyproject.toml`, re-running the test suite, and tagging a new
iafdb-pipeline release. The schema-version stamp in the on-disk bank
is read from `myocard_egm_contracts.schema_info.current_version`, so
the bank always declares the version the producer was built against —
not a version the producer chose at write time.

This is what makes coordinated rolls possible: every output's
`schema_version` field is provably the version the producer was
linked against, so a consumer at the same egm-contracts pin is
guaranteed to validate.
