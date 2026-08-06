# Theory — myocard-iafdb-pipeline

The end-to-end math of the two producer paths — the **trace bank**
(`iafdb_bank` / ClassifierBank) and the **noise bank** — written as a
single reference for exactly *what happens to a sample* between the raw
WFDB file and an on-disk bank row. Its motivating purpose is the
**IAFDB-vs-synthetic divergence research**: to reason about why real and
simulated EGMs look so different, we first need every transform the real
side applies, in one place, with its distributional consequence called
out (§4).

> **Rendering note.** Equations are written in LaTeX — `$$…$$` for
> display, `$…$` for inline. GitHub and VS Code render these as typeset
> math; in a plain-text viewer they show as LaTeX source. Backticked
> names (`fs_hz`, `window_ms`, `peak_to_peak_mv`) are code identifiers,
> not math symbols.

> **Ownership boundary.** iafdb-pipeline is a *producer* — it owns
> **composition** and IAFDB-specific quantities, not the DSP primitives.
> The band-pass, sliding peak-to-peak, threshold strategies and R-wave
> anchoring are implemented in **`myocard-egm-signal`**, and their
> derivations live in **`egm-signal/docs/theory.md`** (§1 filtering, §6
> segment extraction and calibration). This doc says what this pipeline
> *does with* them — the order, the parameters, the channel set, and what
> each choice means for IAFDB — and links down for the mathematics. The
> rule is that the repo owning a primitive owns its math, so nothing is
> derived twice; see [Caveats §7.3](#7-caveats--open-inconsistencies) for
> the history. The HDF5 serialization is owned by **`myocard-egm-data`**;
> the schemas by **`myocard-egm-contracts`**.

> **Honest-evaluation framing.** IAFDB has **no fibrosis ground truth**,
> so none of the math here supports a scored ML result on IAFDB (no
> AUROC / ROC / confusion / calibration). It supports *label-free,
> distributional* comparison only — feature-distribution and
> morphology/spectra checks against the synthetic side. See
> `[[project-iafdb-eval-catch22]]` and
> `[[feedback-iafdb-unlabeled-no-ml-validation]]`. A "sim-to-real"
> statement built on this pipeline is a **directional baseline**, never
> validation.

## Table of contents

- [Notation](#notation)
- [0. Input: from ADC counts to nominal mV](#0-input-from-adc-counts-to-nominal-mv)
- [1. Shared primitives (both paths)](#1-shared-primitives-both-paths)
- [2. Trace-bank path (`iafdb_bank`)](#2-trace-bank-path-iafdb_bank)
  - [2.1 Per-record calibration (R-wave anchoring)](#21-per-record-calibration-r-wave-anchoring)
  - [2.2 Per-channel transform](#22-per-channel-transform)
  - [2.3 Selection and emission](#23-selection-and-emission)
  - [2.4 Worked example](#24-worked-example)
- [3. Noise-bank path (`noise_bank`)](#3-noise-bank-path-noise_bank)
- [4. Where IAFDB and synthetic diverge — a map from the math](#4-where-iafdb-and-synthetic-diverge--a-map-from-the-math)
- [5. Parameter reference](#5-parameter-reference)
- [6. Provenance: what is written where](#6-provenance-what-is-written-where)
- [7. Caveats & open inconsistencies](#7-caveats--open-inconsistencies)
- [References](#references)

---

## Notation

Indexed notation with explicit shapes throughout (per
`[[feedback-notation-style]]`).

| Symbol | Meaning | Shape / units |
|---|---|---|
| $r$ | a record, e.g. `iaf1_afw` | one of 32 |
| $x^{(r)}[n, c]$ | raw physical signal, sample $n$, channel $c$ | $(N_r, C_r)$, nominal mV |
| $n$ | sample index along time | $0 \le n < N_r$ |
| $c$ | channel index | $0 \le c < C_r$ |
| $f_s$ | sampling rate | $1000$ Hz (all records) |
| $\mathcal{B}$ | bipolar channel set `{CS12,CS34,CS56,CS78,CS90}` | up to 5 present |
| $\ell_r$ | surface ECG lead chosen for calibration | 1 lead |
| $\{q_k\}$ | QRS annotation sample indices (from `.qrs`) | $k = 1 \ldots K_r$ |
| $a_r$ | per-record calibration scalar | dimensionless |
| $\tau$ | calibration target QRS peak-to-peak | mV |
| $W, H$ | window length, hop | samples |
| $\mathrm{p2p}$ | peak-to-peak $= \max - \min$ over a window | mV |
| $T_r, U_r$ | keep-above / keep-below threshold for record $r$ | mV |
| $y^{(r)}[\cdot, c]$ | band-passed (and, healthy path, calibrated) channel | $(N_r,)$ |

Sample/millisecond conversion used everywhere:
$$ W = \operatorname{round}\!\left( \text{window\_ms} \cdot 10^{-3} \cdot f_s \right), \qquad H = \max\!\left(1,\ \operatorname{round}\!\left( \text{hop\_ms} \cdot 10^{-3} \cdot f_s \right)\right). $$
At $f_s = 1000$: healthy $(W,H) = (512, 256)$; noise $(W,H) = (200, 100)$; calibration half-window $= \operatorname{round}(100 \cdot 10^{-3} \cdot f_s / 2) = 50$.

---

## 0. Input: from ADC counts to nominal mV

Records load via `wfdb.rdrecord` (`records.py`), which returns the
**physical** signal already converted from stored ADC counts $d[n,c]$:
$$ x^{(r)}[n, c] = \frac{d[n, c] - b_c}{g_c}, $$
where $b_c$ is the ADC baseline and $g_c$ the ADC gain in counts/mV. This
conversion is WFDB's, not ours, but it sets the **units of everything
downstream**, so it matters for the research.

The catch, from `[[dataset-decision-iafdb]]`: for **7 of 8 patients**
$g_c = 3277$ counts/mV on every channel — the nominal $\approx 16384/5$
fallback (±5 mV at 14-bit), i.e. *no real per-channel calibration was
stored*. Patient `iaf4` alone has plausibly-real per-channel gains. So
$x^{(r)}$ is in **nominal mV**: internally consistent within a record,
but not comparable in absolute terms across patients. This is the entire
reason the trace path calibrates (§2.1).

Additional structural facts (`[[iafdb-no-session-timestamps]]`): each
`.dat` starts at its own sample 0 (no cross-record clock), and the
channel set $C_r$ varies per record — every record carries the five
bipolar pairs, but the surface-lead complement differs. Channels absent
from a record are silently skipped, never assumed.

---

## 1. Shared primitives (both paths)

Both producer paths compose the same three primitives in the same order:
**band-pass → sliding peak-to-peak → threshold**. Only the calibration
(§2.1, healthy only), window sizes, and comparison direction differ.

> **The math for all three now lives in egm-signal.** They are egm-signal
> primitives, and the repo that owns a primitive owns its derivation, so
> this section is a pointer rather than a restatement — two copies of one
> derivation drift, and the copy that drifts is the one nobody runs.
>
> | Primitive | Where the math is |
> |---|---|
> | Zero-phase band-pass (Butterworth, `sosfiltfilt`, the linearity that lets $a_r$ factor through) | `egm-signal/docs/theory.md` §1.1–1.2 |
> | Sliding-window peak-to-peak (window starts, count, NaN-robust amplitude) | §6.1 |
> | Threshold strategies, the pooled amplitude distribution they consume, and the empty-pool sentinels | §6.2 |
> | R-wave-anchored calibration (§2.1 below) | §6.3 |

**What stays this repo's to state**, because it is composition rather than
mathematics:

- **The order, and that both paths share it.** Calibrate (healthy only) →
  band-pass → sliding peak-to-peak → threshold. The noise path skips
  calibration and reverses the comparison.
- **Which channel set is pooled.** The threshold is computed over *every
  window of every present bipolar channel of one record*, pooled —
  `constants.BIPOLAR_CHANNELS`, the five CS pairs in distal-to-proximal
  order. Pooling is per **record**, so a percentile threshold is a
  different mV cut in every record; that is the point on uncalibrated
  input. egm-signal's skip-absent guard never fires on this path, since
  all five pairs are present in all 32 records (see
  `project/architecture.md` → "Channel layouts").
- **The parameters this producer chooses** — the $[30,300]$ Hz band, the
  512/256 ms healthy windowing and 200/100 ms noise windowing, and which
  threshold strategy each path defaults to. Those are in §5.

---

## 2. Trace-bank path (`iafdb_bank`)

Orchestrated by `export.bank_export.export_bank`. Per record: calibrate
(§2.1) → extract (§2.2–2.3) → accumulate → build one
`egm-contracts.IafdbBank` Pydantic model → hand to
`egm-data.write_iafdb_bank`. The optional ClassifierBank sibling applies
a consumer-supplied `label_fn` (`all-healthy` or, honestly for IAFDB,
`unlabeled`) via the egm-data converter.

### 2.1 Per-record calibration (R-wave anchoring)

`egm_signal.compute_calibration` → `RWaveAnchoring`. Recovers a per-record
scalar $a_r$ mapping the nominal-mV signal (§0) onto a common scale, using
the surface-ECG QRS as the reference: measure peak-to-peak per annotated
beat on a chosen lead, take the median $M_r$ across beats, and set
$a_r = \tau / M_r$ for a target $\tau$.

**The derivation is `egm-signal/docs/theory.md` §6.3** — the beat window,
the median's role, and why the reference is measured on the raw rather
than band-passed lead. What follows is what that math means *on IAFDB*.

**The target $\tau$ is this repo's policy, and is set in exactly one
place:** the CLI/YAML default, $\tau = 1.0$ mV (`cli/_config.py`).
`export_bank(...)` **requires** the argument and egm-signal ships no
default at all — it deleted `DEFAULT_TARGET_QRS_PP_MV` in v0.3.0 (B22).
Before that the library said $1.5$ and this repo's CLI said $1.0$, so the
same code calibrated a corpus to a different scale depending on whether it
was entered through the CLI or called directly. Requiring the argument
makes that class of drift impossible rather than merely fixed, and is why
egm-signal's §6.3 deliberately names no default: the value belongs to the
consumer that knows its corpus. Example: $M_r = 2.0$ mV, $\tau = 1.0
\Rightarrow a_r = 0.5$.

**Lead selection is load-bearing here, not a fallback.** IAFDB records
carry exactly three surface leads each, drawn from only four that occur in
the dataset at all, so the priority walk is the mechanism that absorbs the
variation rather than a guard for a rare case — it resolves to lead II for
28 of 32 records and lead I for the other 4, and the tail of the default
priority list is inert because those leads appear in no record. The
measured breakdown lives in `project/architecture.md` → "Channel layouts",
which is the layout story's home; egm-signal §6.3 cites it as the concrete
argument for `preferred_leads` being a caller-supplied argument.

**Failure modes.** If a record carries no QRS annotations, or none of the
preferred leads is present, or $M_r \le 0$, `RWaveAnchoring` raises
`ValueError`. All 32 IAFDB records ship `.qrs` files and at least one
preferred lead, so this does not fire in practice — but the orchestrator
does **not** catch it (see §7).

### 2.2 Per-channel transform

For each present bipolar channel $c \in \mathcal{B} \cap \text{channels}(r)$:
$$ \tilde{x}[n, c] = a_r \cdot x^{(r)}[n, c] \quad\xrightarrow{\ \text{band-pass}\ }\quad y[n, c] = \mathrm{BP}_{30,300}\bigl(\tilde{x}[\cdot, c]\bigr)[n]. $$
By linearity of the band-pass (egm-signal §1.1), $y[\cdot, c] = a_r \cdot \mathrm{BP}(x^{(r)}[\cdot, c])$,
so every peak-to-peak is exactly the uncalibrated value scaled by $a_r$:
$$ \mathrm{p2p}_{c,i} = a_r \cdot \mathrm{p2p}^{\text{uncal}}_{c,i}. $$
**Consequence:** an `AbsoluteThreshold(0.2)` cut is a true 0.2 mV cut only
through $a_r$; the per-record scalar is what makes "0.2 mV" mean the same
thing across records. Percentile thresholds are unaffected by $a_r$
(scale-invariant).

### 2.3 Selection and emission

Compute $T_r$ from the pooled distribution (§1; math in egm-signal §6.2), then keep windows with
$\mathrm{p2p}_{c,i} \ge T_r$. Each kept window emits a `HealthySegment`
whose **`signal` is the calibrated, band-passed window**
$$ y[s_i : s_i + W,\ c] \in \mathbb{R}^{W}, \qquad W = 512, $$
cast to `float32`, with `peak_to_peak_mv` $= \mathrm{p2p}_{c,i}$ and
`calibration_scalar` $= a_r$ (identical for every trace from record $r$).
The producer asserts every emitted trace has length exactly $W$.

The default thresholds encode the clinical voltage conventions:
`AbsoluteThreshold(0.2)` (Kosiuk AF-adjusted, project default) and
`AbsoluteThreshold(0.5)` (Sánchez sinus-rhythm anchor). `NoThreshold`
($T_r = -\infty$) keeps every window for pretraining banks
(`ptbank_`).

### 2.4 Worked example

Record with $M_r = 2.0$ mV, $\tau = 1.0 \Rightarrow a_r = 0.5$. A window
on `CS34` with uncalibrated band-passed $\mathrm{p2p}^{\text{uncal}} =
1.2$ mV becomes $\mathrm{p2p} = 0.5 \times 1.2 = 0.6$ mV. Against
`AbsoluteThreshold(0.2)`: $0.6 \ge 0.2$ → **kept**; the stored trace is
the 512-sample calibrated+filtered window, `peak_to_peak_mv = 0.6`,
`calibration_scalar = 0.5`. On a 60 s record ($N_r = 60{,}000$) each
channel yields $\lfloor (60000-512)/256 \rfloor + 1 = 233$ candidate
windows, $\approx 1165$ across 5 bipolar channels *before* thresholding.

---

## 3. Noise-bank path (`noise_bank`)

Orchestrated by `export.noise_export.export_noise_bank`. Structurally a
mirror of §2 with three deliberate differences (see
`project/architecture.md`, "healthy vs noise asymmetry"):

1. **No calibration.** $a = 1$; the transform is
   $y[n,c] = \mathrm{BP}_{30,300}(x^{(r)}[\cdot,c])[n]$ on the **raw,
   nominal-mV** signal. The percentile strategy is scale-invariant, and
   an absolute-mV cutoff would require the *caller* to pre-calibrate. The
   run record stamps `calibration_method="none"`.
2. **Keep-below.** Keep windows with $\mathrm{p2p}_{c,i} \le U_r$;
   default $U_r = \operatorname{percentile}(P_r, 20)$
   (`PercentileQuietThreshold(20)` — quietest 20% per record).
   `AbsoluteQuietThreshold(0.05)` is Sanders 2003's "electrically silent"
   tier but needs calibrated input.
3. **Shorter windows.** $(W, H) = (200, 100)$. The synthetic mixer tiles
   a noise window to the clean trace's length, so shorter sources give
   more variety per record. On $N_r = 60{,}000$: $599$ windows/channel.

Each kept window emits a `NoiseSegment` whose `signal` is the
band-passed, **uncalibrated** window $y[s_i:s_i+W,\,c] \in \mathbb{R}^{200}$
(`float32`). The on-disk `noise_bank` HDF5 is intentionally slim (signal,
`source_record`, `source_channel`); all extraction provenance rides on
the paired `noise_bank_run_record.json` sidecar, including a per-trace
block where `calibration_scalar` is written as `1.0` (no scaling applied)
so consumers never accidentally rescale.

**Units caveat (research-critical).** Noise traces are in each record's
own **nominal-mV** scale — *not* the ~1 mV-R-wave calibrated scale of the
trace bank. The two banks the same repo emits therefore live on
**different amplitude scales**. Anything that combines them (the mixer)
must account for this.

---

## 4. Where IAFDB and synthetic diverge — a map from the math

The point of the doc. Each transform above is also a place the real
distribution can pull away from the simulated one. Ordered roughly by how
much it likely matters. These are **distributional / feature** differences
to quantify with `egm-features` against the synthetic bank — *not* ML
metrics (§honest-evaluation framing above).

1. **Selection operator mismatch (likely the big one).** The IAFDB
   "healthy" class is *defined by a voltage cut*: windows with
   $\mathrm{p2p} \ge T_r$. That is a hard **truncation of the amplitude
   distribution** — the real bank is a biased high-voltage subset of real
   tissue, not a representative sample. The synthetic "healthy" label
   comes from **substrate density around the bipolar pair**
   (`LocalDensityLabel`), which has no voltage-truncation built in. Two
   populations defined by *different operators* will differ before any
   physics does — and this connects directly to the Phase 1 saturation
   result (the model, trained on substrate-labelled synthetic, sees real
   voltage-selected windows it was never shown).

2. **Amplitude scale / units.** Trace bank: per-record scalar $a_r$ to a
   ~1 mV R-wave (§2.1). Noise bank: uncalibrated nominal mV (§3).
   Synthetic: pseudo-EGM forward-calculation units from the
   Aliev-Panfilov / Courtemanche solver. Three different amplitude
   conventions. **Mitigant:** the classifier z-scores each trace before
   the model (`project_plan.md`, per-trace normalization), so $a_r$
   *cancels at model input* — the model sees shape, not absolute mV.
   Implication worth internalizing: **calibration governs *selection*
   (which windows pass $T_r$), not the model's input.** What the model
   actually sees diverge on is **morphology + spectrum**, not amplitude.

3. **SNR of the noise-mixed synthetic bank.** The mixer overlays these
   *uncalibrated* IAFDB noise windows onto synthetic traces at a target
   SNR. If SNR is set in absolute mV, the arbitrary per-record noise
   scale (§3) leaks into the mixed distribution. *This is synthetic-side
   territory* — flag for the synthetic-egm-pipeline / project-lead chat;
   the iafdb side's contribution is simply that the noise it ships is
   uncalibrated, which the mixer must know.

4. **Band-pass parity.** Real traces are zero-phase 30–300 Hz Butterworth
   (egm-signal §1.2). If the synthetic pipeline doesn't apply an *identical* band —
   same edges, same zero-phase realization — the spectra differ for a
   purely procedural reason. Confirm the synthetic side filters to the
   same band before attributing spectral gaps to physics.

5. **Window length & activation content.** Real healthy windows are 512
   ms (may span multiple atrial activations in AF); synthetic traces are
   single-activation at the classifier's $T$. Different temporal support
   changes every window-level feature (dominant frequency, fractionation,
   entropy).

6. **Rhythm & anatomy context (not in the signal math, but upstream of
   it).** IAFDB is AF-rhythm, right atrium, decapolar 2-5-2 mm; the
   synthetic substrate is a 2D patch with a 5×5 grid. These bias the raw
   distribution before any transform (`[[dataset-decision-iafdb]]`
   deviations 1–2, 6).

For the comparison itself, compute `egm-features` bundles on both sides
and compare per-feature distributions; the map above says which
divergences are *procedural* (fixable by matching the pipeline: 4, 5) vs
*definitional* (inherent to the data/labels: 1, 2, 6).

---

## 5. Parameter reference

| Parameter | Trace path | Noise path | Set in |
|---|---|---|---|
| `window_ms` / $W$ | 512 ms / 512 | 200 ms / 200 | `_config` default / `constants` |
| `hop_ms` / $H$ | 256 ms / 256 | 100 ms / 100 | `_config` default / `constants` |
| `band_hz` | [30, 300] | [30, 300] | `egm_signal.DEFAULT_BIPOLAR_BAND_HZ` |
| Butterworth order $M$ | 2 (→ eff. 4, zero-phase) | 2 | `filters.bandpass` |
| calibration | R-wave anchoring | none | §2.1 / §3 |
| target $\tau$ | 1.0 mV (CLI) / 1.5 mV (lib) | — | `_config` / `r_wave_anchoring` |
| cal. lead priority | II, I, V1, aVF, aVL, III, aVR, V5 | — | `qrs_estimation` |
| cal. half-window | 50 samples (100 ms) | — | `RWaveAnchoring(window_ms=100)` |
| threshold default | `Absolute(0.2)` (Kosiuk) | `PercentileQuiet(20)` | examples / `_config` |
| threshold direction | keep $\ge T_r$ | keep $\le U_r$ | `extraction` |
| emitted signal | calibrated + filtered | filtered (uncalibrated) | `segments` |
| dtype | float32 | float32 | model builders |

---

## 6. Provenance: what is written where

Two provenance layers, matching the schema asymmetry:

- **Trace bank** — provenance is **inline** in the `iafdb_bank` HDF5:
  root attrs (`bank_id`, `calibration_method="r_wave_anchoring"`,
  `calibration_target_qrs_pp_mv`, `threshold_mode`, `threshold_value`,
  `band_hz`, `window_ms`, `hop_ms`, `window_samples`, `source_records`)
  and per-trace columns (`patient_id`, `source_record`, `source_channel`,
  `start_sample`, `peak_to_peak_mv`, `calibration_scalar`). The
  ClassifierBank sibling inherits `bank_id`.
- **Noise bank** — the slim HDF5 carries only the signal + source ids;
  everything else lives on the `noise_bank_run_record.json` sidecar
  (calibration scheme, threshold, band, windowing, source records,
  optional per-trace audit). The stable `bank_id` (an egm-contracts
  `ArtifactId`) rides on the sidecar, not the slim HDF5.

Every bank is stamped with a stable `ArtifactId` at write time —
`tbank_`/`ptbank_`/`nbank_` + `iafdb` + UTC date, or an explicit
`data.bank_id`. A contracts schema change means a coordinated re-pin +
version bump; the producer never touches the phase manifest (egm-studio
curates it).

---

## 7. Caveats & open inconsistencies

Found while writing this doc against the current source — research-relevant
because they touch the actual calibration scale, and worth reconciling:

1. **Calibration-target default split — RESOLVED 2026-08-06.** This doc
   originally flagged that `_config.build_bank_export_config` defaulted
   `calibration.target_qrs_pp_mv` to **1.0 mV** while
   `egm_signal.r_wave_anchoring.DEFAULT_TARGET_QRS_PP_MV` was **1.5 mV**,
   so CLI runs and direct `export_bank(...)` calls calibrated to scales a
   factor of 1.5 apart. Fixed **structurally rather than by aligning the
   numbers**: egm-signal v0.3.0 deleted its constant and made the target a
   required argument, and `export_bank` did the same, so the CLI config's
   `1.0` is now the single place the value is decided (B22). Two copies of
   a policy value cannot drift if there is only one copy. Kept here rather
   than deleted so the finding-to-fix trail survives; see §2.1.
2. **Record-skip claim vs behavior.** `project/architecture.md` says a
   record with no usable surface lead is skipped with a warning; the
   orchestrator loop actually lets `RWaveAnchoring`'s `ValueError`
   propagate (whole export fails). Inert on IAFDB (all records qualify),
   but the doc and code disagree.
3. **Primitive derivations lived here — DONE 2026-08-06, they are now
   egm-signal's.** This doc originally carried the band-pass, sliding
   peak-to-peak, threshold-strategy and R-wave-anchoring math, only
   because egm-signal had no theory doc. The project-lead ruled that the
   repo owning a primitive owns its derivation — mirroring the
   eval-metrics split, where egm-classifier owns the math and the viewer
   cross-links — and the migration has now completed in both directions:

   - **egm-signal's `docs/theory.md`** landed with SIG1 (v0.4.0) and
     absorbed them as §1 (filtering) and §6 (segment extraction and
     calibration), together with the activation-detection math that
     graduated out of the platform investigation
     `activation_splitting_method.md`.
   - **This doc keeps composition** — what the producer applies, in what
     order, with which parameters, and what each choice means for IAFDB —
     plus the parts that are genuinely this repo's: the §0 ADC/gain input
     scaling, the pooled channel set, the calibration *target* (a policy
     value this repo owns), the §4 divergence map, and §5–§6. §1 and §2.1
     are now links.

   Two boundary calls worth remembering, since they were argued rather
   than assumed: **pool assembly went to egm-signal** (the skip-absent
   filter and the empty-pool sentinels are meaningless apart from the
   pooling that produces an empty pool), and **the calibration target's
   value stayed here** (egm-signal's §6.3 deliberately names no default,
   so the two-sources-of-truth problem B22 removed from code is not
   rebuilt in prose). The measured channel-layout facts live in
   `project/architecture.md`, which both docs cite.

---

## References

Source (this repo): `export/bank_export.py`, `export/noise_export.py`,
`records.py`, `constants.py`, `cli/_config.py`, `ids.py`.

Primitives (egm-signal, read-only from here) — **the derivations are in
`egm-signal/docs/theory.md`**: §1.1–1.2 zero-phase filtering and the
band-pass, §6.1 sliding-window peak-to-peak, §6.2 the pooled-amplitude
threshold strategies and empty-pool sentinels, §6.3 R-wave-anchored
calibration, §6.4 the two senses of "anchoring". Implementations:
`filters/bandpass.py`, `windowing.py`, `thresholds/{healthy,noise}.py`,
`calibration/{r_wave_anchoring,qrs_estimation}.py`,
`extraction/extractors.py`.

Companion docs: `docs/usage.md` (CLIs, YAML, walkthroughs),
`project/architecture.md` (why the healthy/noise split, provenance
layers), `project/roadmap.md` (planned per-record audit reports +
label policies). Sibling theory docs for the consumer side:
`egm-classifier/docs/theory.md` (preprocessing, normalization, metrics),
`egm-features/docs/theory.md` (the feature math used for the §4
comparison).

Clinical / methodological: Sánchez et al. 2021 (Front. Physiol.,
30–300 Hz band, 0.5 mV sinus threshold); Marchlinski 2000 / Sanders 2003
(voltage tiers); Kosiuk (AF-adjusted 0.2 mV). Dataset provenance +
Sánchez-deviation limitations: `[[dataset-decision-iafdb]]`. No-ground-truth
constraint: `[[project-iafdb-eval-catch22]]`,
`[[feedback-iafdb-unlabeled-no-ml-validation]]`.
