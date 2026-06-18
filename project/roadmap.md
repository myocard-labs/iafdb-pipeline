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

## v0.3.0+ — concrete next steps

These are sized for "could land in one focused PR each." Order is
suggestive; pick by what the classifier or the synthetic mixer needs
next.

### Per-record audit reports

The producer currently prints a one-line summary on exit ("N segments,
M records contributing, etc."). For the white paper's methods section
and for rerunning calibration after a header fix, a richer
per-record report would be useful: median QRS p-p, calibration scalar,
threshold actually applied (post per-record-percentile resolution),
number of windows kept vs rejected, and the surface lead that won
the priority-order selection. Likely lands as an optional
`--report PATH` flag emitting a JSON sidecar next to the bank.

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

### Multi-beat extraction

The current extractor emits fixed-window slices. The classifier's
Phase 2 multi-beat work needs N-consecutive-beat extraction units
where each unit aligns on a QRS annotation. That's a new egm-signal
extractor (`extraction.multi_beat`); this repo just calls it once
it's there.

### A second dataset producer (CFAE / Bordeaux)

The repo name (`iafdb-pipeline`) implies IAFDB-only. The plausible
medium-term extensions (CFAE-annotated datasets from the Bordeaux
group, or larger contemporary atrial banks) might warrant a sister
repo (`bordeaux-pipeline`) using egm-signal + egm-data the same way
this repo does, or might warrant generalizing this repo to a
multi-dataset producer. The decision point is when the second
producer lands. The IAFDB-specific pieces (constants, WFDB loader,
the calibration target choice) make a clean repo split easy.

## Schema bumps to coordinate

The producer writes `iafdb_bank`, `noise_bank`, and
`noise_bank_run_record`. Each is pinned to a specific egm-contracts
version. Schema changes that ship in egm-contracts have to land here
within the same release cycle, in this order:

1. egm-contracts ships the new schema version.
2. egm-data updates its writer to satisfy it.
3. iafdb-pipeline bumps the egm-contracts + egm-data pins and verifies
   round-trip via the validator.
4. Tag and release.

Changes that are likely to land before v1.0:

- **`iafdb_bank` 1.2** — add an audit-report sidecar pointer field
  (`run_record_path`) so the healthy bank also gets a paired JSON,
  symmetric with the noise side.
- **`noise_bank` 1.1** — add an optional `calibration_scalar` per-trace
  column so the opt-in calibration item above can land without a
  schema breakage.
- **`noise_bank_run_record` 1.1** — add `per_trace_provenance.lead`
  for the calibration's chosen surface lead when calibration is on.

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
