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

Per-record audit reports (B11a/B11b), activation-anchored windowing (IAF1), the `noise_bank`
`bank_id` attr (B20) and the explicit-calibration work all shipped — see
[`CHANGELOG.md`](../CHANGELOG.md). What remains:

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

### Noise-side opt-in calibration — **re-scope before building**

The noise producer doesn't calibrate today, and as originally written this item proposed
adding R-wave anchoring to it so the absolute-threshold strategy would mean something on
uncalibrated input.

**That framing is now wrong.** R-wave anchoring was ruled non-standard for EGM in Phase 1.5
(CL-152/CL-153) and demoted on the *trace* side; adding it to the noise side would be
importing a method we just stepped back from, in the one place that never needed it — the
noise path's percentile default is already scale-invariant.

If this is picked up, the question to answer first is what problem it solves that a
per-record *relative* threshold does not. If the answer is "absolute mV tiers such as the
Sanders 0.05 mV electrically-silent cut", note research's finding that those literature
thresholds assume recording-system-calibrated mV, which IAFDB does not provide for 7 of 8
patients. Keep the `calibration_scalar` schema bump below; drop the assumption that the
method behind it is anchoring.

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

- **`iafdb_bank_run_record` (new schema)** — the `--report` sidecar ships as
  **documented-but-unvalidated JSON** carrying a `report_version` string, per CL-026. The
  trigger for schema'ing it is a **second consumer**: the first is the methods paper, and
  freezing a shape nobody has read buys nothing. The moment anything other than a human
  reads the file, it is load-bearing and belongs in egm-contracts like every other
  cross-repo artifact.
- **`iafdb_bank` — per-run-type field applicability (FB-30).** The schema requires three
  fields that do not apply to every run type, each currently satisfied with a `+inf`
  sentinel: `peak_to_peak_mv` (not computed for single-activation windows, CL-149),
  `hop_ms` (no stride in activation mode, CL-151), and `calibration_target_qrs_pp_mv` (no
  target under `method: none`, CL-154). The general fix is conditional requirement by run
  type rather than three sentinels. Deferred out of 1.5 deliberately — the sentinels are
  honest and readable, and a schema restructure is its own wave.
- **`noise_bank` 1.2** (`calibration_scalar` per-trace column) — pairs with noise-side
  opt-in calibration, below. Unscheduled; shifted up a version by B20.
- **`noise_bank_run_record` 1.2** (`per_trace_provenance.lead`) — pairs with the
  `calibration_scalar` bump, not with B20.

## Known issues

- **The ClassifierBank's two ids are easy to misread as contradicting each other.** The root `id`
  describes the bank (labels → `tbank_`, none → `ptbank_`); `banks[0].bank_id` names the *source*
  iafdb bank verbatim. They can legitimately differ, and "fixing" the provenance entry to match
  would name a nonexistent artifact and break trace→source resolution and `concat` dedup. Misread
  twice already (CL-145, and again in review 2026-08-11), which is evidence about the vocabulary
  rather than the readers — folded into **FB-19**. Explained in
  [`architecture.md`](architecture.md) → "The paired ClassifierBank carries two ids".
- **`ptbank_` is the wrong name for what the paired ClassifierBank is.** These banks are pulled into
  egm-studio for feature comparison against a synthetic bank, or handed to egm-classifier as
  unlabeled inference input. Neither is "pretraining". `ptbank_` is what the id-content rule yields
  (no labels, no predictions) and is the least wrong of the existing vocabulary, so it is what the
  producer stamps — but the vocabulary is the thing that needs fixing, not this producer's use of
  it. Tracked at **FB-19** (the role-vocabulary study), which already carries three other artifacts
  that fall back to an over-claiming prefix.
- **`refractory_ms` has no feedback column.** The `--report` yield funnel starts at the
  detector's *output*: `detect_activation_train` returns only its final index array, so the
  candidate count before refractory suppression is not recoverable through the current
  egm-signal API. Every other stage's drops are counted and attributed; merged activations
  are not. Raised as **CL-159** with three possible shapes, one of which is "leave it, and
  iafdb documents the gap as permanent". Workaround: sweep `refractory_ms` and watch the
  activation count move.
- **The minimal `method: none` config lands on a warned pairing.** `threshold.mode` defaults
  to `absolute`, so a config that sets only `calibration.method: none` gets a fixed mV cut on
  uncalibrated input — which selects a different amplitude tier in every record. It warns
  (`ConfigWarning`) rather than raising, since thresholding the recorded scale is legitimate,
  but the *default* combination being the warned one is backwards. Changing a threshold
  default changes what banks get produced, so it is a policy call above this repo; flagged in
  CL-158 for whenever selection defaults are revisited.

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

- ~~**Should the healthy-side producer grow a sidecar?**~~ **Answered in Phase 1.5: yes.**
  The stated trigger — "when the run-time config has more knobs than the schema can carry" —
  arrived with activation mode's dozen detection settings and three per-window drop reasons.
  `--report` (B11b) is that sidecar. Kept here rather than deleted because the *criterion*
  turned out to be the right one and is worth reusing.
- **A shared `BaseRecord` between IAFDB and future datasets?** `IAFDBRecord` satisfies the
  egm-signal `Record` Protocol structurally; a concrete base is weaker than it looks since
  the Protocol already handles the polymorphism.
- **Should the YAML config schema be a JSON Schema** like the on-disk formats? Would give IDE
  autocomplete + version-stamp the config, at the cost of a third codegen surface. Revisit if
  the config outgrows a single hand-maintained dataclass.
- **Should `iafdb-inspect` grow plot output?** It prints per-channel stats only; plots cross
  into egm-studio's territory — likely defer and point users there.
