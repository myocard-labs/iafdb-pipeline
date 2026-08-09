# iafdb-pipeline — Phase 1.5 implementation plan

**Repo:** iafdb-pipeline · **Phase:** 1.5
**Phase design doc:** `intracardiac-platform/phases/phase_1_5/design.md`
**Status:** in progress · **Progress:** 5/9 steps done — Wave 1 complete for this repo (S0 B22, S1 IAF3, S5+S7 the `theory.md` graduation). Remaining: S2–S4 (IAF1) and S6 (B11b), both **Wave 2**, plus S8 phase-exit.
**Repo estimate:** **12–23.75 h active · 11 points** across B22 · IAF1 · B20 · B11a · B11b and the
`theory.md` trim. Cold-start estimates are by **analogy**, not arithmetic —
`estimation_ledger.csv` is empty, so there is no time-per-point rate to multiply by yet. Ranges are
deliberately wide, widest on IAF1 (see its driver notes).

**Wave placement:** S0 touches no schema and was gated only on egm-signal's B22 tag — which shipped as
**v0.4.0**, not the v0.3.0 it was written up as (that tag was never created), so this repo pins v0.4.0. B20 and B11's schema half are
**Wave 1** (they need egm-contracts v0.6.0 + egm-data). IAF1 is **Wave 2** — it needs SIG1
(egm-signal v0.4.0) *and* the activation-position range `𝒫` that study §8.1 produces. `T` is no
longer one of those unknowns — see the design note below.

---

## Scope — what this plan covers

| Phase item | What it needs from this repo | Steps |
|---|---|---|
| **B22** | **egm-signal v0.3.0 adoption** — `DEFAULT_TARGET_QRS_PP_MV` is deleted upstream and `RWaveAnchoring`'s target becomes required. Drop the import, make `export_bank`'s `target_qrs_pp_mv` required, re-pin, and refresh the doc passages that describe the old two-layer default. Originated here (the `theory.md` §7.1 finding); value ruled by research, structural fix ruled by Daniel 2026-07-28; id assigned by CL-024 §5a. Requested from this side by egm-signal in **CL-017**. | S0 |
| **IAF1** | Activation-train splitting — detect the train per channel, emit length-**192 ms** windows at fractional position `p ∼ 𝒫`, drop boundary + multi-beat windows, filter per record first. **Response only**: the detector, the anchor-window helper, and the boundary / multi-beat predicates are SIG1's; this repo does not re-implement the crop math. | S2–S4 |
| ~~**IAF2**~~ | ~~`patient-id-as-label` policy~~ — **dissolved 2026-07-28 (CL-027).** Patient identity is never an ML classification target; §3 IAF2 and §8 study 10 are both removed and the id is retired, not reused. The legitimate need it was gesturing at — noise-donor patient awareness at split time — is **FB-12**, not in 1.5. | *n/a* |
| **IAF3** | **Migrate to egm-contracts + egm-data v0.6.0** (Wave 1) — re-pin both, then adopt the three additive fields at **current behavior**: `noise_bank` `bank_id` (B20), `iafdb_bank` `run_record_path` (B11a), and `iafdb_bank` `traces.activation_position` (CL-052), the last two shipped **absent** until their producers exist. | S1 |
| **B11a** | **Schema adoption (Wave 1)** — stamp `iafdb_bank` 1.3's `run_record_path`, shipped **unset**, since no report exists yet to point at. Current behavior otherwise; rides IAF3's re-pin. | S1 |
| **B11b** | **Report generator (Wave 2)** — optional `--report PATH` JSON sidecar (median QRS p-p, calibration scalar, threshold applied, windows kept vs rejected, winning surface lead), and populating the pointer B11a added. Written as **documented-but-unvalidated JSON**: the `iafdb_bank_run_record` schema stays deferred (P6) until its shape stabilizes after first use. | S6 |
| **B20** | `noise_bank` `bank_id` HDF5 root attr — moves the id off the run-record sidecar onto the bank itself. Wave 1, additive. | S1 |
| *(doc)* | **`theory.md` trim to consumption-only** — §1.1–1.3 and §2.1 graduate to egm-signal's new `docs/theory.md`; this doc keeps composition + the IAFDB-specific parts and cross-links down. Timing agreed in **CL-018**: leave the sections in place under a "moving to egm-signal" note until egm-signal **v0.4.0** tags, then delete and link — its doc records *as-built* behavior so it can't land before SIG1's code, and trimming first would orphan the math. | S5, S7 |

**Not in this plan:** study §8.1 (the IAFDB activation-interval + position-range analysis). Its code
lands in this repo, but per Daniel (2026-07-28) research / the project-lead score and estimate it.
It is tracked here only as the **external dependency that sets IAF1's activation-position range `𝒫`**
— `T` is already fixed (design note below).

## Design notes

- **`export_bank`'s target becomes required, not defaulted to 1.0.** Flipping this repo's signature
  default from the egm-signal constant to a literal `1.0` would make the two copies agree today while
  keeping the structure that let them diverge in the first place — exactly what egm-signal's S0
  rejected. Making it required leaves the constellation with **one** target-amplitude default,
  `cli/_config.py:189`, which is the executable-level consumer per the library-defaults rule. The
  research rationale for the value 1.0 gets recorded there and in `docs/theory.md`, since this repo
  now owns it.

- **The argument stays keyword-only and moves nowhere.** `export_bank`'s signature is already
  `*`-separated, and Python permits a required keyword-only parameter to sit among defaulted ones, so
  dropping the default is a one-line change with no reordering and no positional-call breakage.

- **Test churn: a module constant, not a fixture.** All **14** `export_bank(...)` call sites
  (`tests/test_bank_export.py` ×9, `tests/test_bank_ids.py` ×5) currently rely on the signature
  default; none passes the argument. A `TARGET_QRS_PP_MV = 1.0` module constant per test file is two
  lines and reads at the call site; a fixture would need threading through 14 test signatures for the
  same effect. If a third test module appears, promote it to `conftest.py`.

- **`theory.md` §7 items are resolved in place, never deleted.** The ownership blockquote and §7.3
  both link to `#7-caveats--open-inconsistencies` and refer to items by number. Marking item 1
  resolved (rather than removing it) keeps that numbering stable and preserves the trail from finding
  → ruling → fix.

- **`T` is settled at 192 ms — IAF1 plans against a number, not a pending study.** §3 originally
  treated trace length as a coupled config value that study §8.1 would choose from a ~150–250 ms
  range. egm-classifier then found a hard arithmetic constraint (CL-021): MobileViT's patch sizes
  make the stem require `T ≡ 0 (mod 64 samples)`, and at IAFDB's 1 kHz that leaves **exactly one**
  admissible value in the window — **192 ms = 192 samples**. CL-024 §4 adopted it (round to the grid;
  don't relax the architecture and don't pad). Consequences for IAF1: the window length is an input,
  not an output, so only the **position range `𝒫`** still comes from §8.1; and the padding path in
  egm-classifier's `TraceTransform` must never engage, because padding would shift the activation off
  the fractional position this repo just placed it at — reintroducing the positional regularity T1
  exists to remove.

- **Any resampling decision is both-sides-or-neither.** If §8.1 concludes the rate should change,
  egm-features' catch22 lag features are indexed in **samples**, so synthetic and IAFDB must move
  together or the STU5 / STU4 distances go quietly wrong (CL-015). Both corpora are 1 kHz today.
  This constrains a decision that would otherwise look local to this repo.

- **IAF1 is a second extraction *mode*, not a replacement.** The shipped sliding-window path
  (`extract_healthy_segments`: band-pass → sliding peak-to-peak → threshold) still produces the noise
  bank and every bank generated to date, and §8.1 needs the two comparable. So activation splitting
  arrives as `windowing.mode: sliding | activation` in the YAML, with the activation branch in its own
  module, rather than as a rewrite of `bank_export`'s per-record loop. The two modes share
  calibration, per-record filtering and the emit/provenance tail.

- **Filter once per record, before detection.** The method spec is explicit that band-pass ringing at
  a window's edges corrupts the very margins the position range exists to protect, so the activation
  path filters the whole record first and detects on the filtered signal — unlike the sliding path,
  where filtering happens per channel inside egm-signal's extractor. This is the one place the two
  modes genuinely diverge in signal handling; worth a comment at the call site.

- **Drop accounting belongs to IAF1, not to the §8.1 study.** The splitter must count, per record and
  channel, how many candidate windows were dropped as **boundary** versus **multi-beat** versus kept.
  Those counts are the yield half of §8.1's trade-off and the methods section's "how much real data
  survived" number. The study consumes them; producing them is this repo's job, and it is cheap only
  if built in from the start rather than reconstructed later.

- **Open — where the realized activation position gets persisted.** `AnchoredWindow` carries
  `realized_position` precisely so consumers can report the position statistic they actually produced
  (egm-signal's design note: *"that statistic is what STU5/STU4 compare"*). But `iafdb_bank`'s
  `traces/` has **no field for it** — the columns are `signal`, `patient_id`, `source_record`,
  `source_channel`, `start_sample`, `calibration_scalar`, `peak_to_peak_mv`. Synthetic's equivalent
  rides its per-sim config; IAFDB has no such carrier, so as things stand the position distribution
  T1's hypothesis rests on is computed and then discarded. Raised in the coordination log.
  **Planning assumption:** an additive per-trace `activation_position` float rides the same
  `iafdb_bank` 1.3 bump as B11a. S4's estimate assumes stamping one extra column; if it's refused,
  S4 shrinks and the statistic has to live in B11b's sidecar instead.

## Steps

Each step is one focused commit, ends green (`ruff format src tests` + `ruff check` + `mypy src` +
`pytest`), and states its verification. ☐ todo · 🔨 wip · ✅ done

### S0 — Adopt egm-signal's required QRS-calibration target (B22) ✅ (2026-08-06)

> **Pinned to v0.4.0, not v0.3.0.** The B22 change was written up as egm-signal 0.3.0 — its
> CHANGELOG has a `[0.3.0]` section and design §7 step 7 says "re-pin v0.3.0" — but **that tag was
> never created**, on the remote or locally; the commit shipped inside **v0.4.0**. Pinning the
> documented version would have made `pip install` fail outright. Checked before pinning that
> v0.4.0 is otherwise additive for this repo: every threshold / extraction / calibration name the
> producer imports still resolves, and `DEFAULT_TARGET_QRS_PP_MV` is the only casualty — so the
> "adopt one small breaking change in isolation" intent survives even though the isolating tag
> does not. Design §7 step 7 and egm-signal's dangling `[0.3.0]` CHANGELOG link both want a
> correction from their owners.

- **Change — source:**
  - `pyproject.toml:65` — re-pin `myocard-egm-signal` v0.2.0 → **v0.3.0**.
  - `export/bank_export.py:50` — drop `DEFAULT_TARGET_QRS_PP_MV` from the `myocard_egm_signal`
    import block (it no longer exists upstream → `ImportError`, not a silent behavior change).
  - `export/bank_export.py:116` — `target_qrs_pp_mv: float` loses its default and becomes
    **required**. The `compute_calibration(record, target_qrs_pp_mv=...)` call at `:232` already
    always supplies the value, so egm-signal's new either/or `TypeError` never fires from here.
  - `export/bank_export.py:152` — docstring: state the argument is required and that
    `cli/_config.py` holds the single default.
  - `cli/_config.py:189` — keeps `default=1.0`; add a comment recording that this is now the
    constellation's only target-amplitude default and why the value is 1.0 (research chat).
- **Change — tests:** add `TARGET_QRS_PP_MV = 1.0` to `tests/test_bank_export.py` and
  `tests/test_bank_ids.py` and pass it at all **14** `export_bank(...)` call sites
  (`test_bank_export.py:51, 78, 100, 130, 160, 184, 198, 200, 204`; `test_bank_ids.py:32, 47, 57, 72,
  92`). Add one test asserting `export_bank(...)` **without** the argument raises `TypeError`.
- **Change — docs:**
  - `docs/theory.md` §2.1 (~216–219) — rewrite the "two-layer default split" passage: the library
    constant is gone, the orchestrator argument is required, and the CLI/YAML `1.0` is the single
    source. Record the research rationale for 1.0 here.
  - `docs/theory.md` §7 item 1 — mark **resolved 2026-07-28** in place (see design note), naming
    egm-signal v0.3.0 as the fix.
  - `docs/usage.md:158` — the `calibration.target_qrs_pp_mv` config default `1.0` stays true; note
    that the programmatic `export_bank()` has **no** default. The example at `:268` already passes
    the argument explicitly, so it needs no change — it just stops being optional.
  - `CHANGELOG.md` `[Unreleased]` → **Changed**: the re-pin + the required-argument break.
- **Verify:** full suite green against an editable egm-signal at v0.3.0; the new `TypeError` test
  passes; `mypy src` clean. **Confirm no produced artifact changes** — every bank came through the
  CLI path, which already passed 1.0 (matches egm-signal's blast-radius check), so this is an API
  break with zero data impact and **no bank regeneration**.
- **Depends on:** egm-signal **v0.3.0** tagged. Nothing else in this plan depends on S0.

### S1 — Wave-1 migration to v0.6.0 (IAF3 = B20 + B11a + `activation_position`) ✅ (2026-08-01)

- **Change:** re-pinned egm-contracts **v0.5.3 → v0.6.0** and egm-data **v0.5.0 → v0.6.0**.
  `export/noise_export.py` now stamps the resolved id onto the `noise_bank` **HDF5 root attr** (B20):
  one resolution feeds both the attr and the sidecar, since the schema requires them to agree and two
  independent derivations either side of a UTC midnight would not.
  `_build_noise_bank_model` takes `bank_id` as a **required keyword** rather than defaulting to
  `None` — egm-data's writer raises without it, so failing at construction beats failing at the
  writer. `export/bank_export.py` is unchanged in behavior: `iafdb_bank` 1.3's `run_record_path`
  (B11a) and `traces.activation_position` (IAF3) are both **omitted**, with the reasoning recorded at
  the construction site.
- **Verify:** ✅ **Wave-1 gate passed.** A bank regenerated with today's config against pre- and
  post-migration code is **bit-identical** — signal plus all six provenance columns — with no root
  attr differing except `created_utc`/`bank_id`, and no trace column added or removed. Noise-bank
  signal matches a direct `extract_noise_segments` call trace-for-trace; bank attr and sidecar id
  agree. 39 tests pass (3 new), bare `mypy` clean over `src`+`tests`, ruff clean.
- **Depends on:** egm-contracts + egm-data v0.6.0 — both tagged 2026-07-31 (CL-089, CL-102).
- **Note:** B20, B11a and the `activation_position` field share one re-pin and one test pass, so they
  ship as one commit under IAF3; the effort table splits them only so each id has a row.
- **Not defaulted, deliberately.** `activation_position` ships absent rather than `0.0`, because
  `0.0` is a legitimate value (activation on the first sample) and a default would fabricate a spike
  at the low edge of the very distribution T1 exists to compare. A test asserts absence.

### S2 — Activation-mode config surface (IAF1) ✅ (2026-08-08)

- **Change:** `cli/_config.py` gains `windowing.mode: sliding | activation` (default `sliding`,
  preserving current behavior) plus the activation block — detection function, detection-threshold
  strategy and its parameters, `refractory_ms`, `trace_duration_ms` (**192**), and the position range
  as a `(lo, hi)` fraction pair that collapses to a point for the fixed-position baseline arm.
  Validation rejects activation-only keys under `sliding` and vice versa, mirroring how the existing
  threshold block validates. One new annotated `examples/*.yaml`.
- **Verify:** ✅ `tests/test_cli_config.py` covers every new key (defaults, parse, each invalid
  value, both cross-mode rejections) plus example coverage; 93 tests green, ruff + mypy clean.
- **Depends on:** none — the config shape was fixed by the method spec, so this landed without
  needing SIG1 at runtime.
- **Two revisions during review (Daniel):** the 64-sample trace-length rule is a **warning**, not an
  error — it is egm-classifier's constraint, not this producer's, and would evaporate if that model
  changed; a general-purpose IAFDB tool has no business refusing a length someone asked for. No
  automatic pad/clip either (a pad/clip policy would be a project-level backlog item, not a silent
  side effect of loading a config). Cross-mode key rejection kept as-is.
- **Also folded in:** the `examples/` prune. The folder had accumulated one-off run configs (that
  role now belongs to the gitignored `configs/`). Coverage was **measured** rather than eyeballed —
  five keys and three enum values had no example — and is now enforced by tests.

### S3 — Activation-based extraction path (IAF1) ☐ (3–6 h)

- **Change:** new `export/activation_extract.py`. Per record: calibrate (unchanged), band-pass the
  **whole record** once, then per bipolar channel — detect the train via SIG1's
  `detect_activation_train`, draw `p ∼ 𝒫` per activation, build the window with `window_from_anchor`,
  and drop it if `window_is_within_bounds` fails (boundary) or `window_is_single_beat` fails
  (multi-beat). Emit the survivors as the same segment shape the sliding path produces, plus each
  window's `realized_position` and a per-record/per-channel **drop tally** (kept / boundary /
  multi-beat). No crop arithmetic here — every geometric decision comes from SIG1.
- **Verify:** unit tests on synthetic records with a **known** activation train — a clean train at
  known spacing yields one window per activation with the activation at the requested fraction within
  half a sample; a train tight enough to overlap yields the expected multi-beat drops and nothing
  mis-centred; activations near the record ends are dropped as boundary; a flat channel yields no
  windows and no exception; the tallies sum to the candidate count.
- **Depends on:** S2, and **egm-signal v0.4.0** (SIG1).

### S4 — Wire the mode into the export + provenance (IAF1) ☐ (1.5–4 h)

- **Change:** `export/bank_export.py` dispatches on `windowing.mode` and threads the activation
  path's results into the existing Pydantic-build / write tail, so both modes emit an `iafdb_bank`
  that validates. Stamp per-trace `activation_position` (pending the open question in the design
  notes) and surface the drop tallies on `BankExportResult` so the CLI can print them and B11b can
  serialize them. `cli/export_bank_cmd.py` prints a mode-aware summary line.
- **Verify:** an end-to-end export in each mode round-trips through the contracts validator; the
  sliding-mode output is **byte-identical to before** (the mode is additive, not a behavior change);
  activation-mode traces all have length 192 and a position inside the configured range; the printed
  yield matches the tallies.
- **Depends on:** S3. The upper end of the range assumes the extra column lands; see the design note.

### S5 — `theory.md` graduation notice ✅ *(superseded — egm-signal's doc landed before this was needed, so the trim (S7) happened directly)*

- **Change:** annotate §1.1–1.3 and §2.1 with a "moving to egm-signal — see its `docs/theory.md`"
  pointer, per the timing agreed in CL-029/CL-031. No content moves yet.
- **Verify:** links resolve; §7.3's description of the pending lift still matches what the sections say.
- **Depends on:** none. Can ride any earlier commit.

### S6 — `--report` audit sidecar (B11b) ☐ (2–4 h)

- **Change:** optional `--report PATH` on `iafdb-export-bank`, emitting a JSON sidecar per run:
  per-record median QRS p-p, calibration scalar, the **calibrating lead actually used** and its
  surface-set group, threshold applied, and windows kept vs rejected (with S3's boundary/multi-beat
  split when in activation mode). `bank_export` then populates the `run_record_path` attr B11a
  shipped unset. Documented-but-unvalidated JSON per CL-026 — no egm-contracts schema this phase;
  the shape gets one documented description in `docs/usage.md` and the asymmetry against the schema'd
  `noise_bank_run_record` noted in `architecture.md`.
- **Verify:** the sidecar parses, its per-record entries match the bank's own provenance columns, and
  `run_record_path` resolves relative to the bank; a run without `--report` still leaves the attr unset.
- **Depends on:** S1 (the attr must exist). S4 if the activation tallies are to appear.

### S7 — `theory.md` trim to consumption-only ✅ (2026-08-06)

- **Change:** delete §1.1–1.3 and §2.1, replacing them with cross-links into egm-signal's
  `docs/theory.md`; rewire the table of contents, notation table and every internal reference; keep
  the IAFDB-specific remnants — the §0 ADC/gain input scaling, which channel set is passed and why
  the surface complement varies, per-record filtering, IAF1's drop/stride response, §4's divergence
  map, §5 parameters, §6 provenance. Close out §7.3.
- **Verify:** no orphaned section references or dead anchors; every formula that left has a working
  link to its new home; the doc still reads as a continuous argument rather than a set of stubs.
- **Depends on:** **egm-signal v0.4.0** tagged with its `docs/theory.md` present (CL-031).

### S8 — Docs + phase-exit ☐ (1–2 h)

- **Change:** `roadmap.md` drops the now-shipped Phase 1.5 items; `CHANGELOG.md` gets the
  `[Unreleased]` summary; `docs/usage.md` documents the activation mode end to end;
  `project/architecture.md` gains the second extraction path and the sidecar asymmetry note.
- **Verify:** the full `intracardiac-platform/project/pr_checklist.md` run passes.
- **Depends on:** all prior steps.

## Complexity + estimate

Scored with the rubric in
`intracardiac-platform/project/investigations/estimate_vs_actual_tracking.md` §8.

| Item | Cx | Size | Estimate | Driver notes |
|---|---|---|---|---|
| B22 — egm-signal v0.3.0 adoption | **2** | S | 1–2 h | *Change size:* three source lines + a re-pin. *Novelty:* none — mechanical. *Surface:* small in `src/`, but **14 test call sites** and three doc passages, plus a coordinated adoption of an upstream breaking change. *Verification:* full suite + a confirmation that no artifact changed. Sits at the S anchor (`noise_bank` `bank_id` add); the test churn is what keeps it off XS. Slightly wider than egm-signal's own 0.5–1 h for S0 because the call-site count is on this side. |
| **IAF1** | **3** | M | 6–13 h | *Change size:* a new config block, a new extraction module, an orchestrator branch and provenance plumbing — but **no new algorithm**: every geometric decision is SIG1's, which is what keeps it off L. *Novelty:* moderate — composing unfamiliar primitives against real AF data, and §9 warns the splitter may simply underperform. That risk lands on §8.1's evaluation, not on this code. *Surface:* three modules + config + examples, one repo, one cross-repo dependency (egm-signal v0.4.0). *Verification:* unit tests on synthetic records with known activation trains — cheap and decisive. Scored **below** SIG1 (L 5) deliberately: this is the consumer half of that work. Range is wide at the top because S4 assumes a schema column that isn't agreed yet. |
| **B20** | **1** | XS | 0.5–1 h | Plumb an already-derived id onto a root attr. `ids.py` does the derivation today; the sidecar keeps carrying it, so there's no consumer migration. The S anchor (P5) is the *contracts* half — this is the producer's adoption of it, which is smaller. |
| **B11a** | **1** | XS | 0.5–1 h | Write one optional attr, deliberately unset. Genuinely the smallest item in the plan. |
| **B11b** | **2** | S | 2–4 h | *Change size:* a new CLI flag + a serializer over values the producer already computes. *Novelty:* none. *Verification:* light — parse the JSON, cross-check against the bank's own columns. Kept off XS by the number of distinct provenance fields and by having to define a shape that a schema will later have to match. |
| **`theory.md` trim** (S5 + S7) | **2** | S | 1.75–2.75 h | Not a content rewrite — a deletion plus re-linking, but across a 470-line doc whose table of contents, notation table and internal cross-references all have to stay coherent. The annotate-now half (S5) is minutes; the trim (S7) is the work, and it is blocked until egm-signal v0.4.0 exists. |
| **Repo total** | **11** | | **12–23.75 h** | Sum of the rows; no coordination surcharge — that is the project-lead's phase-level line, not a repo's. |

## Effort tracking

Mechanism: `intracardiac-platform/project/investigations/estimate_vs_actual_tracking.md` §7. Daniel
speaks the markers; this chat stamps the time from `date`. **Active = marked span − breaks.**
**Backstop:** unmarked silence > **2 h** = away.

> **Flow-down effort is NOT tracked this phase** (Daniel, 2026-07-29). The planning/flow-down session
> ran without enough methodology in place to measure it accurately, so rather than record a
> reconstructed number that would quietly become calibration data, Phase 1.5's flow-down effort is
> **deliberately skipped**. Daniel + the project-lead will settle the mechanism for future phases.
> This supersedes CL-024 §5b *for this phase only* — that rule (a repo chat's planning session counts
> toward its issues' `Actual`) stands from Phase 2 onward.
>
> **Consequence at cleanup:** the `Actual` columns below stay empty for planning work, and the
> `estimation_ledger.csv` rows this repo contributes are **estimate-only** — they must be marked as
> such, not read as "came in at zero." Implementation effort (S0–S8) is still tracked normally once
> the markers start.

### Session log

| Timestamp (local) | Event | Focus (issue) | Note |
|---|---|---|---|
| — | *(planning not tracked — see the note above)* | — | Clock starts at the first `start` marker on implementation. |

### Effort by issue

| Issue | Task-type | Estimate | Active | Elapsed | Sessions |
|---|---|---|---|---|---|
| B22 — egm-signal v0.3.0 adoption | API change / re-pin | 1–2 h | — | — | 0 |
| IAF1 | pipeline (extraction) | 6–13 h | — | — | 0 |
| B20 | schema adoption | 0.5–1 h | — | — | 0 |
| B11a | schema adoption | 0.5–1 h | — | — | 0 |
| B11b | pipeline (provenance) | 2–4 h | — | — | 0 |
| `theory.md` trim | docs | 1.75–2.75 h | — | — | 0 |
| **Repo total** | | **12–23.75 h** | | | |

## Notes / decisions log

- **2026-07-28** — Escalated the theory-doc ownership question (egm-signal primitive derivations
  parked in this repo's `docs/theory.md` because egm-signal had no theory doc). Project-lead ruled:
  the owner owns the math theory. egm-signal gains `docs/theory.md` alongside SIG1; the
  activation-splitting derivations graduate there out of the platform investigation, which stays as
  the design/research record. Scope then confirmed by Daniel to cover the **already-shipped**
  primitives too (§1.1–1.3, §2.1), not just SIG1's math. Recorded in `theory.md` §7.3; the trim is
  blocked on egm-signal's doc existing and must land with its S7a.
- **2026-07-28** — The `theory.md` §7.1 finding (CLI default 1.0 vs
  `egm_signal.r_wave_anchoring.DEFAULT_TARGET_QRS_PP_MV` 1.5) went upstream and came back as a
  structural fix rather than a value flip: **the constant is deleted and the argument made
  required** (Daniel), per the library-defaults rule. Ships as egm-signal **v0.3.0**, alone and
  before SIG1, so this repo adopts one isolated breaking change. **S0** is this repo's response.
- **2026-07-28** — Daniel ruled that study **§8.1 is scored by research / the project-lead**, not
  this chat, even though its code lands here. Tracked as an external dependency on IAF1's parameters
  rather than an estimated item.
- **2026-07-28** — `theory.md` §7 item 2 (the `architecture.md` record-skip claim vs the
  orchestrator's propagating `ValueError`) is still open and in-repo. Not yet assigned to a step.
- **2026-07-28** — **CL-024 batch adjudication** folded in. (a) The QRS-default adoption is now
  **B22** (design §4), so its effort has a §6 row to land in — the "no id" loose end raised here and
  in egm-signal's CL-016 is closed. (b) **`T` = 192 ms**, per the mod-64 MobileViT constraint —
  recorded as a design note; IAF1 no longer waits on §8.1 for it. (c) Fleet ruff decision: **pin CI
  to `ruff==0.15.17`**, matching this repo's pre-commit rev. Applied as a `[Chore]` on
  `.github/workflows/ci.yml`, verified green. **Deviation worth recording:** CL-024 says *not* to run
  the 0.16 reformat first-aid, but this repo had already applied and pushed it before the decision
  landed. Left in place rather than reverted — `project/architecture.md` verifies clean under both
  0.15.17 and 0.16, so the reformat is inert under the pin and a revert would be pure churn.
- **2026-07-28** — **CL-026 resolved.** B11 splits in two: **B11a** ships `iafdb_bank` 1.3's
  `run_record_path` **unset** in Wave 1 (schema adoption only, ≈ XS) and **B11b** builds the
  `--report` generator in Wave 2 (≈ S/M). The `iafdb_bank_run_record` schema **stays deferred** (P6)
  — the sidecar is documented-but-unvalidated JSON for 1.5, with the asymmetry against the schema'd
  `noise_bank_run_record` noted in `architecture.md` when B11b lands. Trigger for revisiting: the
  sidecar's shape stabilizes after first use. Additive iafdb/noise schema changes are cheap in any
  later bump, so nothing forces it into v0.6.0.
- **2026-07-28** — **CL-027 resolved: IAF2 is dissolved.** Patient identity is never an ML
  classification target; design §3 IAF2 and §8 study 10 are both removed and the id is retired
  (not reused). **My action:** retire the `patient-id-as-label` entry from `roadmap.md`'s
  "Additional label policies" — done. The real gap it was gesturing at (a noise-donor patient's
  traces spanning train and val) is logged as **FB-12** and deliberately **not** pulled into 1.5:
  the stakes are empirical, since only the noise is patient-derived, and a `noise_bank` patient
  field is additive so it costs nothing to add later.
- **2026-07-28** — Replied to the two open items addressed here: **CL-028** (→ CL-017: B22 confirmed
  as S0, with the correction that adoption is 1–2 h not trivial, because 14 test call sites rely on
  the default) and **CL-029** (→ CL-018: agreed to annotate-now / delete-at-v0.4.0, plus two boundary
  details — the peak-to-peak pooling definition stays here, and egm-signal's graduated §2.1 should
  not name a default calibration target).
- **2026-07-29** — **Theory boundary settled (CL-031 → CL-034); I was wrong on pooling.** egm-signal
  takes **pool assembly** — pooling across the supplied channel list, the skip-absent filter, and the
  empty-pool ±inf sentinels — because all of it is `extraction/extractors.py` L105–122 and the
  fail-closed sentinel is incomprehensible apart from the pooling that empties the pool. My CL-029
  argument (that present-ness encodes IAFDB's varying channel layout) **doesn't survive checking**:
  `BIPOLAR_CHANNELS` is the five CS pairs and every IAFDB record has all five, so `skip-absent` never
  fires on the bipolar path. What varies is the **surface ECG** complement, which drives calibration
  lead selection, not pooling. **Consequence for the trim:** the $P_r$ pooled-set formula in §1.3 goes
  to egm-signal; this doc keeps which set is passed + a one-line note that the intersection is inert
  on IAFDB, and the channel-layout story moves next to the calibration section where it belongs.
  Boundary detail 2 agreed as proposed — egm-signal's graduated anchoring math names no default target.
- **2026-07-29** — **v0.3.0 tag timing: "tag whenever"** (Daniel). egm-signal does not hold the tag;
  this repo adopts B22/S0 reactively when the suite goes red. Chosen because nothing else here depends
  on S0, while SIG1 — which sits behind the tag — is on the phase's early critical path.
- **2026-07-29** — **Flow-down complete** (nine steps, 11 points, 12–23.75 h) and posted to the
  project-lead as **CL-052** for design §6. Two things worth remembering about the scoring: IAF1 is
  **M(3)** rather than L, deliberately below SIG1's L(5), because this repo composes those primitives
  and writes no crop arithmetic of its own; and B20/B11a are separate rows but **one commit**, since
  they share a re-pin and a test pass.
- **2026-07-29** — **Open (CL-052): the realized activation position has nowhere to live.** IAF1
  computes `realized_position` per window via SIG1 and, as `iafdb_bank` stands, discards it at write
  time — which would make T1's position-matching hypothesis unfalsifiable from the artifacts. Asked
  for an additive per-trace `activation_position` on the `iafdb_bank` **1.3** bump that B11a is
  already causing. S4 is planned assuming it lands; if it doesn't, S4 shrinks by ~1 h and the
  statistic moves into B11b's sidecar or becomes a §9 limitation.
- **2026-07-29** — **Channel-layout facts corrected in `architecture.md` + `theory.md` §2.1**
  (prompted by CL-035; swept all 32 `.hea` headers independently to confirm before editing). The docs
  said the surface complement "varies — some records have II + V1 + aVF, others have I + III + aVL":
  **III and aVL appear in no IAFDB record.** Actual: bipolar is uniform (5/5 pairs in 32/32); every
  record carries exactly **three** surface leads from only four that occur at all — `{I,II,V1}` ×12,
  `{II,V1,aVF}` ×8, `{I,II,aVF}` ×8, `{I,V1,aVF}` ×4. **Consequence:** `RWaveAnchoring`'s
  `preferred_leads` walk is load-bearing rather than defensive — II calibrates 28/32 records, I the
  other 4, and the list's tail (aVL/III/aVR/V5) is inert here. **Feeds B11b:** the per-record
  calibrating lead + surface-set group are free provenance fields for the `--report` sidecar.
