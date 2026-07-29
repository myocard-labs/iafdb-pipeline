# iafdb-pipeline — roadmap

Future work only — shipped history lives in [`CHANGELOG.md`](../CHANGELOG.md). Internal
doc; public users read the README + `docs/usage.md`.

Work lands here as it's identified, sits in the **Backlog** until a phase-planning session
promotes it into a **Phase** cluster, then moves to the CHANGELOG once shipped. Phase
clusters mirror the science Project Phases in
`intracardiac-platform/project/project_plan.md`. Items scheduled into cross-cutting Phase
work carry a `→ tracked at intracardiac-platform Phase X` annotation; the rest are
component-internal.

## Phase 1.5 — sim-realism

### Per-record audit reports

The producer prints a one-line summary on exit; a richer per-record report (median QRS p-p,
calibration scalar, threshold actually applied, windows kept vs rejected, the surface lead
that won the priority-order selection) is useful for the white-paper methods section and for
rerunning calibration after a header fix. An optional `--report PATH` flag emitting a JSON
sidecar next to the bank; pairs with the `iafdb_bank` 1.3 schema bump below.

**Split across two waves** (B11a / B11b): Wave 1 adopts the 1.3 schema and ships
`run_record_path` **unset**, since there is no report to point at yet; Wave 2 builds the
generator and populates the pointer. The sidecar is documented-but-unvalidated JSON for
Phase 1.5 — see the schema-bump note below for when it earns a contracts schema.

Two fields are now cheaper than when this was written: the **calibrating lead** and its
**surface-set group** fall out of the channel-layout sweep recorded in
[`architecture.md`](architecture.md) ("Channel layouts"), so the report gets per-record
calibration provenance essentially for free.

> → Tracked at `intracardiac-platform/project/project_plan.md` Phase 1.5 (per-record outlier
> hunts during the synthetic-vs-IAFDB feature comparison).

### Additional label policies

`format.label_policy` already ships `all-healthy` and `unlabeled`. Plausible further
additions, in priority order: **`per-patient-af-status`** (AF / sinus assignment from a
curated side file — IAFDB ships none) and **`drug-state-aware`** (parse the `_afw` drug
delivery / washout phases into time-varying labels). The signature is fixed (a callable
taking a Pydantic `IafdbBank`, returning `(labels, labels_dict)` or `None`), so each is a
config key + a closure.

**Retired — `patient-id-as-label`.** A third policy was tracked here: labelling each trace
with its patient id, to train a patient-discrimination probe and see whether the classifier
leans on a patient-identity shortcut. **Dropped 2026-07-28** — patient identity is never an
ML classification target on this project, so neither the policy nor the study that consumed
it will be built. Recorded rather than deleted so the idea isn't re-proposed. The legitimate
concern underneath it — a noise-donor patient's traces spanning the train and val splits of
a noise-mixed bank — is real but lives elsewhere: it needs patient provenance on the
`noise_bank`, not a label policy here. Tracked as **FB-12**; deliberately not in Phase 1.5,
since only the noise is patient-derived and the `noise_bank` field is additive whenever it's
wanted.

## Phase 5 — CLOCS pretraining

### Multi-beat extraction

The current extractor emits fixed-window slices. The multi-beat extraction primitive
(`extraction.multi_beat`) lives in egm-signal; this repo wires it in. IAFDB has no fibrosis
ground truth (per [[project-iafdb-eval-catch22]]), so its only multi-beat consumer is the
CLOCS pretraining pipeline that needs adjacent-segment training pairs from unlabeled
recordings — not the synthetic-side Phase 4 multi-beat classification work.

> → Tracked at `intracardiac-platform/project/project_plan.md` Phase 5 (CLOCS-style
> self-supervised pretraining). See [[reference-multi-beat-consensus]].

## Backlog (unscheduled — promoted into a phase at a planning session)

### Multi-record dedup

A bipolar pair recorded in `_afw` (atrial free wall) overlaps spatially with the same pair
in `_tva` on the same patient; the producer treats them as independent. Some downstream
analyses want to dedup at the patient × placement level — likely an optional
`dedup.scheme: per-patient-placement-pair` config filter, default off to preserve current
behavior.

### Noise-side opt-in calibration

The noise producer doesn't calibrate today. An opt-in calibration step would make the
absolute-threshold strategy meaningful on uncalibrated inputs: an optional `calibration:`
block in the noise-config YAML, `compute_calibration` per record applied before windowing,
and the run record's `calibration_method` flipping `none → r_wave_anchoring`. Pairs with the
`noise_bank` 1.1 schema bump below.

### A second dataset producer (open question)

The repo name implies IAFDB-only. If a second publicly-available *labeled* atrial
intracardiac-EGM dataset ever appears, the question is whether to spin up a sister
`<dataset>-pipeline` (reusing egm-signal + egm-data the same way) or generalize this repo to
a multi-dataset producer. The IAFDB-specific pieces (constants, WFDB loader, calibration
target) make a clean split easy. **Reality check:** as of now no such dataset exists that's
both public and fibrosis-labeled — see [[dataset-decision-iafdb]]; re-evaluate if the field
ships one (e.g. a future CinC challenge release).

## Schema bumps to coordinate — see egm-contracts roadmap

The producer writes `iafdb_bank`, `noise_bank`, and `noise_bank_run_record`, each pinned to a
specific egm-contracts version. The cascade order (egm-contracts ships → egm-data updates →
iafdb-pipeline bumps pins + round-trip-tests → tag) and the master bump list live at
`egm-contracts/project/roadmap.md`. The iafdb-side recipe each time: bump the contracts/data
pins, satisfy the new schema shape (stamp new attrs / columns), round-trip-test via the
contracts validators, tag. Upcoming bumps that affect this repo (the cross-artifact-linkage
wave already consumed `iafdb_bank` 1.2 + `noise_bank_run_record` 1.1 for `bank_id`, so the
below shift up a version):

- **`iafdb_bank` 1.3** (audit-report sidecar pointer) — pairs with the per-record audit
  reports (Phase 1.5). Ships **unset** in the Wave-1 re-pin (B11a); the report generator
  that populates it is Wave-2 work (B11b). The sidecar itself stays **unschema'd** for 1.5
  — an `iafdb_bank_run_record` schema, mirroring `noise_bank_run_record`, waits for a later
  contracts wave once the sidecar's shape has stabilized after first use.
- **`noise_bank` 1.1** (`bank_id` HDF5 root attr) — **B20, Phase 1.5, Wave 1.** Brings the
  noise bank up to the cross-artifact-linkage baseline the other banks already carry: the id
  moves onto the bank itself instead of riding only on the `noise_bank_run_record` sidecar.
  Additive; the sidecar keeps carrying it too, so consumers can migrate at their own pace.
  Ships in the same re-pin as `iafdb_bank` 1.3 above.
- **`noise_bank` 1.2** (`calibration_scalar` per-trace column) — pairs with noise-side
  opt-in calibration, below. Unscheduled; shifted up a version by B20.
- **`noise_bank_run_record` 1.2** (`per_trace_provenance.lead`) — pairs with the
  `calibration_scalar` bump, not with B20.

## Known issues

None open.

## Won't-do (out of scope, but documented to save the question)

- **No HDF5 I/O inside this repo.** Every writer lives in egm-data; an `h5py` import here is a
  smell. The contract is "build a Pydantic model, hand it to a writer."
- **No DSP primitives.** Filters, thresholds, calibration strategies, and segment extractors
  all live in egm-signal; the producer composes them (the v0.2.0 refactor specifically undid
  the old flat-layout reimplementation).
- **No feature engineering.** Spectral entropy, fractionation indices, etc. belong in
  egm-features; this producer ships raw bipolar signal + per-trace calibration metadata.
- **No CLI for the bank reader.** Reading a bank is a Python call; egm-studio's Inspection
  surface provides the GUI.
- **No model code.** No torch dependency — stays at numpy + scipy + wfdb.

## Open architectural questions for later

- **Should the healthy-side producer grow a sidecar?** Inline per-trace provenance is enough
  today; the boundary is "when the run-time config has more knobs than the schema can carry."
- **A shared `BaseRecord` between IAFDB and future datasets?** `IAFDBRecord` satisfies the
  egm-signal `Record` Protocol structurally; a concrete base is weaker than it looks since
  the Protocol already handles the polymorphism.
- **Should the YAML config schema be a JSON Schema** like the on-disk formats? Would give IDE
  autocomplete + version-stamp the config, at the cost of a third codegen surface. Revisit if
  the config outgrows a single hand-maintained dataclass.
- **Should `iafdb-inspect` grow plot output?** It prints per-channel stats only; plots cross
  into egm-studio's territory — likely defer and point users there.
