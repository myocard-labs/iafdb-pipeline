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

## Calibration: what R-wave anchoring is for, and what it is not

**Ruled non-standard for EGM by research, 2026-08-09** (CL-152/CL-153,
`intracardiac-platform/project/investigations/rwave_anchoring_review.md`).
It is retained as a *selectable, demoted* option under one specific
premise, and `none` is the intended default. This section records the
ruling rather than the case that preceded it, because the case that
preceded it was partly wrong and is worth naming as such.

### The two premises, only one of which survives

R-wave anchoring divides every bipolar EGM channel by the per-record
median QRS peak-to-peak measured on a surface ECG lead. Two different
arguments can be made for why that ratio means anything, and they are
not equally good:

- **Physiological covariation — a category error.** "The surface R wave
  is a physiological reference common across records, so anchoring it
  brings EGM amplitudes into comparable units." This was the
  justification written here, and it does not hold. Bipolar atrial EGM
  amplitude is a *near-field local* quantity, set by wavefront-to-bipole
  angle, electrode size and spacing, contact force, and fibrosis. Surface
  QRS amplitude is *ventricular far-field*, set by ventricular mass, body
  habitus, and lead placement. There is no shared physiological driver,
  so there is no reason for the two to covary — and the covariation test
  the premise implies would be expected to fail.
- **Shared amplifier gain — defensible, unverified here.** If the surface
  and intracardiac channels pass through one amplifier/digitizer chain
  carrying a single miscalibrated per-record gain, then anchoring the
  surface QRS to its assumed-true amplitude recovers *that gain*, and the
  recovered scalar applies to the intracardiac channels for electronic
  reasons rather than physiological ones. This is the premise egm-signal's
  own docstring states, and it is the only one worth keeping. **It has not
  been verified for IAFDB.**

The distinction is not academic: under the first premise the scalar is a
physiological normalizer and could be trusted for absolute voltage; under
the second it is a gain correction and is only as good as the assumption
that one gain is shared. Everything below is about testing the second.

### Prior art: essentially none for this purpose

Standard electroanatomic mapping applies voltage thresholds to
recording-system-calibrated mV directly — there is no calibration step of
this kind to cite. The surface-to-intracardiac R-wave *ratio* does appear
in the literature, but for **catheter localization** (the ventricular
far-field grows as the catheter approaches the heart) — a different
signal used for a different purpose. egm-signal's `RWaveAnchoring`
docstring already says plainly that this is "an engineering response to
the calibration gap", not a literature method. Treat any writeup
accordingly.

### The IAFDB evidence

Calibration has a free internal consistency check: each patient
contributes four records differing only in catheter placement, sharing
one surface ECG, so their four scalars should agree. Measured across all
32 records at a 1.0 mV target:

```
whole corpus:      0.2696 - 1.4495  (5.38x)   median 0.4605   CV 0.541
between patients:  0.2707 - 1.1873  (4.39x)   (per-patient medians)
within patient:    median 1.05x  —  but iaf4 1.91x, iaf7 2.26x
```

Three things follow:

1. **The step is not near-identity.** A 5.38x spread means it does
   substantial work, so if the premise is wrong the corpus carries a
   systematic between-patient amplitude distortion.
2. **6 of 8 patients pass** at <= 1.10x — genuine support, since that is
   agreement across four independent catheter placements.
3. **2 of 8 fail by ~2x** (iaf4 1.91x, iaf7 2.26x).

**The iaf4 failure is the informative one, and it is bad news.**
egm-signal's docstring records that 7 of 8 IAFDB patients carry a nominal
fallback ADC gain rather than a real calibrated one. The exception is
**iaf4** — and iaf4 is one of the two failures. Reading the headers
directly: 28 of 32 records carry an identical uniform fallback of
`3277.0` on all eight channels, while iaf4's four records carry one real
per-channel vector `(980, 990, 392, 2066, 2062, 2072, 2064, 2056)` —
**the same vector on all four**.

That matters because it closes the escape hatch. The 2-of-8 failures were
previously unresolvable: the four placements might be one session (making
a 2x spread a real error) or separate acquisitions with genuinely
different gains (making the spread correct), and IAFDB has no session
timestamps to decide. **For iaf4 the headers decide it.** Identical gains
across all four records means the shared-gain premise predicts identical
scalars; the measured spread is 1.91x, driven by `iaf4_tva` measuring a
0.6899 mV QRS against ~1.31 mV for its three siblings at a comparable
beat count (31 vs 29/32).

So on the one patient where the surviving premise is checkable, it does
not hold. A plausible reading is that surface electrodes were repositioned
between placements — which is precisely the point: surface QRS amplitude
moves for reasons that have nothing to do with the intracardiac gain, and
anchoring imports that noise into the EGM scale.

This is a yellow flag rather than a formal disqualification — n=1 patient,
and beat-count or artifact contamination of the median remains a live
alternative. It is enough to say anchoring must not be trusted as a
physiological calibration, and must never run as a silent default.

### The alternatives, and what is actually used

- **`none`** — raw recorded values, with scale-invariant or relative
  processing downstream. Research's recommended default, and what the
  noise path has always done. The honest option when no gain correction
  can be justified.
- **Per-record self-normalization on the *intracardiac* signal** — the
  clinical relative-voltage approach. If absolute voltage is ever wanted,
  this beats a cross-domain surface anchor, because at least the reference
  and the signal are the same measurement.
- **Fixed gain from the WFDB header** — unusable for 7 of 8 patients,
  since the header carries a nominal fallback. Recovering that missing
  gain is the entire motivation for anchoring in the first place.
- **Per-record peak normalization** (divide by max p-p) — noisy outliers
  dominate the max.

Lead selection, when anchoring is used, walks a priority list
(II -> I -> V1 -> aVF -> aVL -> III -> aVR -> V5) and records the
actually-used lead, which the `--report` sidecar surfaces per record. It
matters: 28 records anchor on II and 4 (iaf8, which has no II) anchor on
I, and the two leads do not have the same QRS amplitude, so those four
are not comparable like-for-like with the rest.

### Why the stakes are lower than the above suggests

Research's assessment, and the strongest reason `none` is safe: **the
calibration choice feeds no scored metric.** Synthetic traces are in
relative units (`synthetic_au`, FB-17), so absolute amplitude cannot be
compared across corpora regardless of what IAFDB does. Amplitude features
in the STU5 realism distance should therefore be scale-normalized on both
sides or dropped, and the Sanchez 0.5 mV healthy-segment selection on
IAFDB should be read as approximate — a per-record *relative* voltage
threshold being the scale-invariant alternative. IAFDB's own use is
qualitative-only. So a method change is not phase-blocking and does not
force bank regeneration.

### What follows in code

`calibration.method` is a **required** config key with no default
(`cli/_config.py`), so no bank is produced without the choice being
stated. This diverges from research's recommendation of `none` as the
*default*, on Daniel's call that a step this consequential should be
written down rather than inherited — a stricter position than research's,
and compatible with its stated concern, which was silent defaults rather
than defaults as such.

`method: none` **is implemented** (egm-contracts v0.6.1 / `iafdb_bank`
1.4, CL-154), and five of the seven bank examples now use it. It skips
the step rather than calibrating by 1.0 — which matters, because
`compute_calibration` raises on a record with no QRS annotations or no
preferred lead, and that must not happen on a path that was never going
to use the result. Per-trace `calibration_scalar` is `1.0` and the bank
records `calibration_method: "none"`.

The one sentinel that remains is `calibration_target_qrs_pp_mv = +inf`,
because the schema requires a positive number and an uncalibrated run has
no target. That is the line this episode established, now recorded in
egm-contracts' `schema_evolution.md`: **sentinel a field that would merely
be _unused_; fix a field that would be _untrue_.** `peak_to_peak_mv`
(CL-149) and `hop_ms` (CL-151) took sentinels because they are only
unused on runs that do not compute them; `calibration_method` could not,
because the file would have asserted a signal-processing step that never
ran. Retiring all three in favour of per-run-type applicability is FB-30.

**Interaction to watch:** under `none` the recorded amplitudes are nominal
mV — internally consistent within a record, not comparable across them —
so amplitude selection should be scale-invariant. `none` +
`threshold.mode: absolute` emits a `ConfigWarning` rather than an error,
since thresholding the recorded scale is a legitimate thing to want. It is
reachable by default, though, because `threshold.mode` itself defaults to
`absolute`; worth revisiting when threshold defaults are next examined.

The implementation lives in egm-signal (`RWaveAnchoring`,
`compute_calibration`). The producer selects and records it.

## Channel layouts

IAFDB records do NOT share a uniform channel set — but the variation is
narrower and more structured than "it varies." Swept across all 32
headers (2026-07-29):

- **Bipolar: perfectly uniform.** All five pairs (CS12, CS34, CS56,
  CS78, CS90) are present in **32/32** records, no exceptions.
- **Surface: exactly three leads per record, in one of four
  combinations** — `{I, II, V1}` ×12, `{II, V1, aVF}` ×8,
  `{I, II, aVF}` ×8, `{I, V1, aVF}` ×4. Only **four distinct** surface
  leads appear anywhere in the dataset (I, II, V1, aVF); **III, V5, aVL
  and aVR never appear at all**, and no record carries more than three.

The producer handles this by:

- Hard-coding the bipolar set in `constants.BIPOLAR_CHANNELS` (present
  in every record, in distal-to-proximal order).
- Picking the surface lead at calibration time from a priority list,
  via `RWaveAnchoring`'s `preferred_leads` kwarg.
- Skipping a record (with a logged warning) only if zero usable
  surface leads are present.

**Consequence — the priority walk is load-bearing, not defensive.**
Because no record carries the full complement and the three-lead set
differs between records, the `preferred_leads` walk is the mechanism
that absorbs the variation, not a fallback for a rare case. Under the
shipped `DEFAULT_PREFERRED_LEADS`, **lead II calibrates 28/32 records
and lead I calibrates the remaining 4** (the `{I, V1, aVF}` group, which
has no II). The tail of the default list — aVL, III, aVR, V5 — is inert
on IAFDB, since none of those leads exists here.

Two corollaries worth holding onto: the per-record calibrating lead is a
provenance fact the methods section will want (and a candidate field for
B11b's `--report` sidecar), and `extract_healthy_segments`' skip-absent
filter never actually fires on the bipolar path, because the bipolar set
is complete in every record.

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

3. **Audit-report JSON for the iafdb bank** (optional, `--report`).
   Sidecar. Added in Phase 1.5 (B11b), when the prediction in the
   paragraph this replaced came true: activation mode added a dozen
   detection knobs and three per-window drop reasons, and the schema
   could not keep up. It carries the effective run settings, per-record
   calibration provenance (the lead the priority walk chose, what it
   measured, the beat count behind it), and the per-channel
   kept/dropped-by-reason breakdown. The bank points back at it via
   `run_record_path`, relative to the bank's own directory.

### What the yield funnel does not cover

The per-channel tally reports `activations → kept | dropped_boundary |
dropped_multi_activation`, and those four numbers partition exactly. It is
worth being precise about where that funnel *starts*, because the name
`detected` originally implied it started earlier than it does (renamed to
`activations` for that reason).

`detect_activation_train` runs `preprocess → threshold → select →
suppress (refractory) → refine` and returns only the final index array.
The candidate list produced by the selector, and the subset the refractory
suppressor merged, are locals inside that function. So the pre-suppression
count is **not recoverable through the current egm-signal API** — this is
an absence of capability, not an omission in the report.

The consequence is a real asymmetry in the tuning feedback:

| Knob | Failure mode | Feedback column |
|---|---|---|
| `trace_duration_ms` | window overruns the record edge | `dropped_boundary` |
| detection settings / `𝒫` | neighbouring activation inside the window | `dropped_multi_activation` |
| **`refractory_ms`** | **genuine activations merged into one** | **none** |

`refractory_ms` is a knob users are expected to tune, and on IAFDB — every
patient arrhythmic, activations closely spaced — merging is the plausible
failure. Its only symptom today is a low `activations` count with no
denominator. The workaround is to sweep it and watch the count move.

Raised with egm-signal as a backlog item: have the detector return, or
optionally report, the candidate count alongside the accepted train. The
argument is the same one that justified splitting boundary from
multi-activation drops rather than summing them — a drop reason with no
column attached cannot be acted on.

Terminology note, since it caused the original confusion: egm-signal uses
**candidate** for a pre-suppression peak out of `CandidateSelector` and
**activation** for what survives suppression. This repo's tally now follows
that split, and its `windows_evaluated` property is named to avoid
colliding with the upstream sense of "candidate" — those are candidate
*windows*, one per activation, a strictly later stage.

### Why the audit report is not schema'd, when the noise run-record is

The two sidecars sit at different points in their lives and are
governed accordingly. `noise_bank_run_record` has a real cross-repo
consumer — the synthetic mixer reads it to reproduce a noise bank —
so its shape is a contract and lives in egm-contracts. The audit
report's first consumer is the methods paper, which has not been
written; freezing a schema around a shape nobody has read yet buys
nothing and costs a migration every time the paper's needs shift. So
for Phase 1.5 it is **documented but unvalidated** (CL-026): one
described shape in `docs/usage.md`, a `report_version` string so a
future schema has something to migrate from, and readers told to
ignore unknown keys.

The asymmetry is deliberate and temporary, not an oversight. The
trigger for schema'ing it is a *second* consumer — the moment anything
other than a human reads the file, the shape is load-bearing and
belongs in egm-contracts like everything else at a repo boundary.

Two design choices worth recording, because both look like bugs from
the outside:

- **Every record appears, including ones that contributed nothing.**
  A record whose windows were all dropped is invisible in the bank —
  it is not even in `source_records` — so without this the difference
  between "processed and yielded nothing" and "never reached" is
  unrecoverable. That distinction is the report's single highest-value
  output on this dataset, where every patient is arrhythmic and
  multi-activation drops are the expected failure.
- **The report is written even when no bank is.** An empty run has no
  bank to attach diagnostics to, and is exactly the run an operator
  needs to debug. Writing the report unconditionally, flagged with
  `bank_written: false`, means the failing case is the one case that
  is never silent. It is also written *before* the bank, so the
  pointer the bank stores can never name a file that does not exist.

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

Two policies ship today: `all-healthy` (every trace labeled 0) and
`unlabeled` (the label_fn returns `None`, leaving every `label_truth`
as `None` with an empty labels dict). `unlabeled` is the honest choice
for IAFDB — it has no per-segment fibrosis truth — and is what feeds an
eval run that emits an unlabeled (`upred_`) predictions bank. egm-data's
converter accepts the `None` return directly (`IAFDBLabelFn`), so the
policy is just a closure that declines to label.

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
