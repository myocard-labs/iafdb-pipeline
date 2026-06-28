# iafdb-pipeline — roadmap

What's planned for future releases. Internal doc — public users see the
README and `docs/usage.md`.

## v0.2.0 — current release (shipped)

Scope (recap, see `architecture.md` for the design rationale):

- IAFDB download from PhysioNet (`iafdb-download`), 32 records, 8
  patients × 4 placements.
- Typed `IAFDBRecord` dataclass with the WFDB loader and the
  `iter_records` walker.
- `iafdb-export-bank` producer: R-wave-anchored calibration, three
  threshold modes (absolute, percentile, none), 512 ms / 256 ms hop
  default windowing, ClassifierBank-format secondary output with
  consumer-supplied label policy.
- `iafdb-export-noise-bank` producer: scale-invariant percentile
  default and absolute-mV alternate, 200 ms / 100 ms hop default
  windowing, JSON sidecar with full provenance + optional per-trace
  audit columns.
- `iafdb-inspect` interactive metadata + per-channel stats printer.
- YAML-config-driven CLIs for the two export commands; six annotated
  example configs under `examples/`.
- All HDF5 writing delegated to `myocard-egm-data`; all schemas
  pinned to `myocard-egm-contracts==v0.2.0`; all DSP primitives
  pulled from `myocard-egm-signal==v0.1.0`.

Pinned dependencies (drop the direct git references once they
publish to PyPI):

```
myocard-egm-contracts @ git+...@v0.2.0
myocard-egm-data       @ git+...@v0.2.0
myocard-egm-signal     @ git+...@v0.1.0
```

## v0.3.0 — stable cross-artifact IDs (current release)

Adds stable cross-artifact ID stamping for the cross-artifact-linkage
system (egm-contracts v0.5.0 / egm-data v0.4.0):

- Every bank carries an egm-contracts `ArtifactId`. Auto-derived from the
  bank's role at write time (`tbank_` / `ptbank_` / `nbank_` + `iafdb` +
  UTC date), or overridden via the `data.bank_id` config key / `bank_id=`
  orchestrator kwarg. Derivation + validation live in `ids.py`.
- iafdb_bank stamps the ID on the HDF5 root attr (`iafdb_bank` 1.2) and
  propagates it onto the paired ClassifierBank. The noise bank's ID rides
  on the `noise_bank_run_record.json` sidecar
  (`noise_bank_run_record` 1.1); the slim noise HDF5 is unchanged.
- See `architecture.md` > "Stable cross-artifact IDs" for the design and
  the same-day-uniqueness limitation.

Re-pinned dependencies:

```
myocard-egm-contracts @ git+...@v0.5.1
myocard-egm-data       @ git+...@v0.4.0
myocard-egm-signal     @ git+...@v0.1.0
```

Follow-on (other producers, tracked in the meta repo): synthetic-egm-pipeline
and egm-classifier stamp their own IDs and re-pin egm-data v0.4.0 next.

## v0.4.0+ — concrete next steps

These are sized for "could land in one focused PR each." Items
scheduled into cross-cutting Phase work in the meta repo's
`project_plan.md` carry a `→ tracked at intracardiac-platform Phase X`
annotation; the rest are component-internal — add when a consumer
needs them.

### Per-record audit reports — Phase 1.5

The producer currently prints a one-line summary on exit ("N segments,
M records contributing, etc."). For the white paper's methods section
and for rerunning calibration after a header fix, a richer
per-record report would be useful: median QRS p-p, calibration scalar,
threshold actually applied (post per-record-percentile resolution),
number of windows kept vs rejected, and the surface lead that won
the priority-order selection. Likely lands as an optional
`--report PATH` flag emitting a JSON sidecar next to the bank.

> → Tracked at `intracardiac-platform/project/project_plan.md` Phase 1.5. Diagnostic data is useful during the synthetic-vs-IAFDB feature comparison work (per-record outlier hunts) as well as later for paper methods sections.

### Additional label policies

`format.label_policy` is wired but only `all-healthy` is implemented.
Plausible additions in priority order:

- **`per-patient-af-status`** — read AF / sinus-rhythm assignment
  from a side file (the IAFDB description doesn't ship one, so this
  needs a curated mapping). Useful for the supervised eval if a
  curator's AF assignment per recording becomes available.
- **`drug-state-aware`** — IAFDB's `_afw` records include drug
  delivery + washout phases in their comments. Parsing those to
  emit time-varying labels would let the classifier study the
  drug-induced transition.
- **`patient-id-as-label`** — for patient-discrimination probes that
  test whether the classifier is using a patient-identity shortcut
  vs a generalizable fibrosis signal.
  > → Tracked at `intracardiac-platform/project/project_plan.md` Phase 1.5 (shortcut-hunt diagnostic).

The signature is fixed (a callable taking a Pydantic `IafdbBank` and
returning `(labels, labels_dict)`), so adding a policy is a
config-key + a closure.

### Multi-record dedup

A bipolar pair recorded in `_afw` (atrial free wall, longest
recording) overlaps spatially with the same pair recorded in `_tva`
on the same patient. The producer currently treats them as
independent; some downstream analyses want to dedup at the
patient × placement level. Likely an optional filter wired into the
config (`dedup.scheme: per-patient-placement-pair`), defaulting to
off to preserve the v0.2.0 behavior.

### Calibration on the noise side (opt-in)

The noise producer doesn't calibrate today (see
`architecture.md` for why). Adding an opt-in calibration step would
make the absolute-threshold strategy meaningful on uncalibrated
inputs. The shape: an optional `calibration:` block in the
noise-config YAML, the orchestrator runs `compute_calibration` per
record and applies the scalar before windowing if present, and the
run record's `calibration_method` flips from `none` to
`r_wave_anchoring`.

### Multi-beat extraction — Phase 5 (CLOCS pretraining)

The current extractor emits fixed-window slices. The multi-beat
extraction primitive (`extraction.multi_beat`) lives in egm-signal;
this repo wires it in.

> → Tracked at `intracardiac-platform/project/project_plan.md` Phase 5 (CLOCS-style self-supervised pretraining). Note: previous wording said "classifier's Phase 2" or "Phase 4 multi-beat classification" — both wrong destinations for iafdb-pipeline specifically. IAFDB has no fibrosis ground truth (per [[project-iafdb-eval-catch22]]) so it'll never be a labeled train/test set; the only consumer of multi-beat IAFDB extraction is the CLOCS pretraining pipeline that needs adjacent-segment training pairs from unlabeled recordings. Phase 4's multi-beat work is purely synthetic-side (PacingTrain in synthetic-egm-pipeline). See [[reference-multi-beat-consensus]] for the broader literature framing.

### A second dataset producer (open question, no concrete dataset today)

The repo name (`iafdb-pipeline`) implies IAFDB-only. If a second
publicly-available labeled atrial intracardiac EGM dataset ever
becomes available, the architectural question is whether to:

- Spin up a sister repo (e.g. `<dataset-name>-pipeline`) using
  egm-signal + egm-data the same way this repo does, or
- Generalize this repo to a multi-dataset producer.

The IAFDB-specific pieces (constants, WFDB loader, the calibration
target choice) make a clean repo split easy.

> **Reality check (added 2026-06-23):** as of today, no such second
> dataset exists that's both publicly available and labeled with
> fibrosis ground truth — see [[dataset-decision-iafdb]] for why
> IAFDB was the only viable choice. An earlier version of this
> section name-dropped CFAE / Bordeaux as a candidate, but research
> confirmed there's no publicly downloadable EGM dataset from the
> Haïssaguerre group's CFAE work. Re-evaluate if the field ships
> something new (e.g. a future CinC dataset challenge release).

## Schema bumps to coordinate — see egm-contracts roadmap

The producer writes `iafdb_bank`, `noise_bank`, and
`noise_bank_run_record`. Each is pinned to a specific egm-contracts
version. The full cascade order (egm-contracts ships first → egm-data
updates → iafdb-pipeline bumps pins + verifies round-trip → tag and
release) plus the list of upcoming schema bumps now lives at
`egm-contracts/project/roadmap.md` (the "Schema bumps to coordinate"
section there is the source of truth).

The iafdb-pipeline-side work for each upcoming bump is the same recipe
each time:

1. Bump the `myocard-egm-contracts` and `myocard-egm-data` pins in
   `pyproject.toml`.
2. Update any producer-side code that has to satisfy the new schema
   shape (usually just stamping new attrs or new per-trace columns;
   the Pydantic model + validator handle the shape correctness).
3. Round-trip test via `egm-contracts.validators` (already in tests).
4. Tag + release.

Upcoming bumps that affect this repo specifically. **Note:** the
cross-artifact-linkage wave (egm-contracts v0.5.0) already consumed
`iafdb_bank` 1.2 and `noise_bank_run_record` 1.1 for the `bank_id`
field, so the planned features below shift up a version:

- **`iafdb_bank` 1.3** (audit-report sidecar pointer) — pairs with the
  per-record audit reports work above; ships in the same Phase 1.5
  release as the producer-side `--report` flag. (1.2 was taken by
  `bank_id`.)
- **`noise_bank` 1.1** (`calibration_scalar` per-trace column) — pairs
  with the noise-side opt-in calibration work above. The noise HDF5
  schema was *not* bumped by the linkage wave, so 1.1 is still free.
- **`noise_bank_run_record` 1.2** (`per_trace_provenance.lead`) — pairs
  with `noise_bank` 1.1. (1.1 was taken by `bank_id`.)

## Won't-do (out of scope, but documented to save the question)

- **No HDF5 I/O inside this repo.** Every writer lives in egm-data; if
  the iafdb-pipeline source tree grows an `h5py` import, that's a
  smell. The contract is "build a Pydantic model, hand it to a
  writer."
- **No DSP primitives inside this repo.** Filters, thresholds,
  calibration strategies, and segment extractors all live in
  egm-signal. The producer composes them; it doesn't reimplement
  them. The previous (pre-egm-signal) flat-layout repo did
  reimplement; that's specifically what the v0.2.0 refactor undid.
- **No feature engineering.** Spectral entropy, fractionation
  indices, etc. belong in egm-features (planned). This producer
  ships raw bipolar signal + per-trace calibration metadata; the
  features are computed downstream.
- **No CLI for the bank reader.** Reading a bank is a Python call,
  not a command. `egm-viewer`'s Inspection tab provides the GUI
  surface for bank inspection.
- **No model code.** No torch dependency. Stays at the
  numpy + scipy + wfdb level.

## Open architectural questions for later

These don't need decisions for v0.2.0 but are worth thinking about
when the second consumer or producer arrives:

- **Should the healthy-side producer grow a sidecar?** Currently
  inline per-trace provenance is enough. The boundary is "when the
  producer's run-time config has more knobs than the schema can
  carry."

- **Should there be a shared `BaseRecord` between IAFDB and future
  datasets?** Today `IAFDBRecord` is concrete and satisfies the
  egm-signal Record Protocol structurally. If a second producer
  shares 80% of the dataclass fields, factoring out a base might
  be worthwhile — but the Protocol already handles the polymorphism,
  so the case for a concrete base is weaker than it looks.

- **Should the YAML config schema be a JSON Schema like the on-disk
  formats?** The config is currently validated by the typed
  dataclass builder (`build_bank_export_config`, etc.). A formal
  JSON Schema would give IDEs autocomplete and would version-stamp
  the config format. The cost is a third schema-codegen surface
  in this repo. Worth revisiting if the config grows beyond what a
  single hand-maintained dataclass can document.

- **Should `iafdb-inspect` grow plot output?** It currently prints
  per-channel stats only. Plots (`--plot signal`, `--plot histogram`,
  `--plot qrs`) are a natural extension but cross into the
  egm-viewer's territory. Likely defer — point users at egm-viewer
  for visual inspection, keep this CLI textual.
